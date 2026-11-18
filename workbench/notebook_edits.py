"""App-managed notebook edits. Originals and generation receipts stay immutable.

Hosted files are published/restored by the existing committed Delta/Volume boundary.
Cleanup is a separate request AFTER that save acknowledgement, never before flush.
"""
from hashlib import sha256
import difflib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from typing import Literal

_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_NOTEBOOK = 2 * 1024 * 1024


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]
    session_id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")
    expected_workspace: StrictStr = Field(min_length=8, max_length=256)
    expected_remote_sha256: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def digest(content):
    return sha256(content.encode("utf-8")).hexdigest()


def _read(path, limit=MAX_NOTEBOOK):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise HTTPException(409, "Notebook edit storage contains a symlink")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, "Notebook edit exceeds the bounded review size")
    return raw.decode("utf-8")


def _write_new(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic exclusive publication. Existing historical files cannot be replaced.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def revision_state(root, job_id, originals):
    """Verify a contiguous per-notebook revision chain, not a mutable latest pointer."""
    if not _ID.fullmatch(job_id):
        raise HTTPException(404, "Unknown notebook draft")
    state = {}
    directory = root / "state" / "notebook-edits" / job_id / "revisions"
    files = sorted(directory.glob("*.json"))
    if len(files) > 500:
        raise HTTPException(409, "Notebook revision history exceeds the bounded reader")
    for path in files:
        receipt = json.loads(_read(path))
        artifact = receipt["artifact_path"]
        prior = state.get(artifact)
        expected = prior["content_sha256"] if prior else digest(originals[artifact])
        if (receipt["job_id"] != job_id or receipt["parent_sha256"] != expected
                or receipt["sequence"] != (prior["sequence"] + 1 if prior else 1)
                or not _ID.fullmatch(receipt["session_id"])
                or not _HASH.fullmatch(receipt["content_sha256"])):
            raise HTTPException(409, "Notebook revision chain failed integrity validation")
        content = _read(directory / (receipt["session_id"] + ".ipynb"))
        if digest(content) != receipt["content_sha256"]:
            raise HTTPException(409, "Returned notebook checksum mismatch")
        state[artifact] = {**receipt, "content": content}
    return state


def normalize_export(raw, namespace):
    """Keep source/metadata, remove execution output, restore required cell language."""
    from workbench.spark_codegen import _notebook
    try:
        book = json.loads(raw)
        for cell in book["cells"]:
            metadata = cell.setdefault("metadata", {})
            metadata.setdefault("language", "markdown" if cell["cell_type"] == "markdown" else "python")
            if cell["cell_type"] == "code":
                cell["execution_count"] = None
                cell["outputs"] = []
            else:
                cell.pop("execution_count", None)
                cell.pop("outputs", None)
        content = _notebook(json.dumps(book, ensure_ascii=False), namespace)
        # Do not assimilate credential literals. References to secret scope/key remain valid.
        from workbench.redaction import redact_text
        if redact_text(content) != content:
            raise ValueError("Sensitive content")
        return content
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Edited notebook must contain valid supported Python/SQL cells and no credential literals. Use secret references. Execution outputs are not imported.") from None


class NotebookEditor:
    def __init__(self, app, load_candidate, lock):
        self.app, self.load_candidate, self.lock = app, load_candidate, lock
        self.root = app.state.service.root

    def identity(self, expected):
        if getattr(self.app.state, "hosted_request_authenticated", False):
            from workbench.hosted import current_identity
            identity = current_identity()
            if identity.mode != "obo":
                raise HTTPException(403, "Notebook edits require the current hosted user")
        else:
            from workbench.databricks_models import profile_identity, _profile_host
            if expected.rstrip("/") != _profile_host().rstrip("/"):
                raise HTTPException(409, "Selected workspace changed. Reopen notebook attachment.")
            identity = profile_identity("labs")
        user = identity.client.current_user.me()
        if (expected.rstrip("/") != identity.host.rstrip("/") or user.id != identity.user_id
                or not isinstance(user.user_name, str)
                or not re.fullmatch(r"[A-Za-z0-9._@+-]{1,256}", user.user_name)):
            raise HTTPException(403, "Notebook editing user or workspace changed")
        return identity, user

    def source(self, job_id, artifact):
        _, job, manifest, exact, content, _ = self.load_candidate(job_id, artifact, for_execution=False)
        from workbench.draft_artifact_reads import read_result
        saved = read_result(self.root, job, job_id)
        originals = {**saved["staged_artifacts"], **saved["artifacts"]}
        revisions = revision_state(self.root, job_id, originals)
        latest = revisions.get(artifact)
        return job, manifest, exact, latest["content"] if latest else content, latest

    def session(self, job_id, body):
        if not _ID.fullmatch(job_id):
            raise HTTPException(404, "Unknown notebook draft")
        identity, user = self.identity(body.expected_workspace)
        directory = self.root / "state" / "notebook-edits" / job_id
        try:
            session = json.loads(_read(directory / "sessions" / (body.session_id + ".json")))
        except FileNotFoundError:
            raise HTTPException(404, "Notebook edit session not found in this package") from None
        expected_path = (f"/Workspace/Users/{user.user_name}/ORION-notebooks/edits/"
                         f"{body.session_id}/{Path(session['artifact_path']).stem}")
        if (session["job_id"] != job_id or session["session_id"] != body.session_id
                or session["workspace"] != identity.host or session["user_id"] != identity.user_id
                or session["notebook_path"] != expected_path):
            raise HTTPException(403, "Notebook edit session scope mismatch")
        return identity, session, directory

    def remote(self, identity, session):
        from databricks.sdk.service.workspace import ExportFormat
        status = identity.client.workspace.get_status(session["notebook_path"])
        if status.object_id != session["object_id"]:
            raise HTTPException(409, "Temporary notebook object changed; no import or deletion performed")
        with identity.client.workspace.download(session["notebook_path"], format=ExportFormat.JUPYTER) as stream:
            raw = stream.read(MAX_NOTEBOOK + 1)
        if len(raw) > MAX_NOTEBOOK:
            raise HTTPException(413, "Edited notebook exceeds review bound")
        return raw.decode("utf-8")

    def attach(self, job_id, body):
        from databricks.sdk.service.workspace import ImportFormat, Language
        with self.lock:
            job, manifest, exact, content, latest = self.source(job_id, body.artifact_path)
            identity, user = self.identity(body.expected_workspace)
            directory = self.root / "state" / "notebook-edits" / job_id
            for file in sorted((directory / "sessions").glob("*.json")):
                old = json.loads(_read(file))
                if (old["artifact_path"] == body.artifact_path and old["user_id"] == identity.user_id
                        and old["workspace"] == identity.host
                        and not (directory / "cleanup" / (old["session_id"] + ".json")).exists()):
                    # Reopening never overwrites edits, including after a lost acknowledgement.
                        revision = directory / "revisions" / f"{old['sequence']:08d}-{old['session_id']}.json"
                        completed = json.loads(_read(revision)) if revision.exists() else {}
                        return {**old, "saved": bool(completed), "remote_sha256": completed.get("remote_sha256"),
                            "status": "attached", "execution_started": False,
                            "message": "Reopened the existing temporary notebook without overwriting edits. Return edits here when finished."}
            session_id = uuid4().hex
            folder = f"/Workspace/Users/{user.user_name}/ORION-notebooks/edits/{session_id}"
            path = folder + "/" + Path(body.artifact_path).stem
            identity.client.workspace.mkdirs(folder)
            identity.client.workspace.upload(path, content.encode("utf-8"), format=ImportFormat.JUPYTER,
                                             language=Language.PYTHON, overwrite=False)
            remote = identity.client.workspace.get_status(path)
            if not isinstance(remote.object_id, int) or remote.object_id <= 0:
                raise HTTPException(502, "Temporary notebook object identity was not confirmed")
            session = {"session_id": session_id, "job_id": job_id, "artifact_path": body.artifact_path,
                       "artifact_sha256": digest(content), "workspace": identity.host, "user_id": identity.user_id,
                       "notebook_path": path, "object_id": remote.object_id,
                       "notebook_url": identity.host.rstrip("/") + "/editor/notebooks/" + quote(str(remote.object_id)),
                       "manifest_sha256": job["manifest_sha256"], "package_hash": manifest["package_hash"],
                       "sequence": latest["sequence"] + 1 if latest else 1}
            _write_new(directory / "sessions" / (session_id + ".json"), json.dumps(session))
            return {**session, "status": "attached", "execution_started": False,
                    "message": "Temporary notebook opened. The app can preview and save returned edits, then delete this temporary Databricks copy. No code was run."}

    def preview(self, job_id, body):
        identity, session, _ = self.session(job_id, body)
        _, _, _, content, _ = self.source(job_id, session["artifact_path"])
        if digest(content) != session["artifact_sha256"]:
            raise HTTPException(409, "The app notebook revision changed. Reopen it before returning edits.")
        raw = self.remote(identity, session)
        normalized = normalize_export(raw, session["session_id"])
        def source_lines(text):
            return [f"Cell {i + 1} ({c['cell_type']}):\n" + (''.join(c['source']) if isinstance(c['source'], list) else c['source']) + '\n'
                    for i, c in enumerate(json.loads(text)["cells"])]
        diff = ''.join(difflib.unified_diff(source_lines(content), source_lines(normalized),
                                          fromfile="Saved app notebook", tofile="Databricks edits"))
        return {"job_id": job_id, "session_id": session["session_id"], "artifact_path": session["artifact_path"],
                "remote_sha256": digest(raw), "parent_sha256": digest(content), "content_sha256": digest(normalized),
                "diff": diff, "outputs_imported": False, "saved": False,
                "message": "Review the cell-source diff. Saving creates an unapproved revision, then the app removes only its temporary notebook after verifying durable save."}

    def save(self, job_id, body):
        if not body.expected_remote_sha256:
            raise HTTPException(422, "Preview the edits before saving")
        with self.lock:
            identity, session, directory = self.session(job_id, body)
            destination = directory / "revisions" / f"{session['sequence']:08d}-{body.session_id}.json"
            job, _, _, content, latest = self.source(job_id, session["artifact_path"])
            if destination.exists():
                receipt = json.loads(_read(destination))
                if receipt["remote_sha256"] != body.expected_remote_sha256:
                    raise HTTPException(409, "This edit session was already saved with different content")
                return {**receipt, "saved": True, "cleanup_required": True}
            if digest(content) != session["artifact_sha256"] or job["manifest_sha256"] != session["manifest_sha256"]:
                raise HTTPException(409, "App notebook or generation receipt changed; edits were not saved")
            raw = self.remote(identity, session)
            if digest(raw) != body.expected_remote_sha256:
                raise HTTPException(409, "Databricks notebook changed after preview. Preview again before saving.")
            normalized = normalize_export(raw, body.session_id)
            receipt = {**session, "parent_sha256": digest(content), "remote_sha256": digest(raw),
                       "content_sha256": digest(normalized), "approved": False, "review_required": True,
                       "execution_allowed": False, "deployment_allowed": False}
            notebook_path = directory / "revisions" / (body.session_id + ".ipynb")
            if notebook_path.exists():
                if _read(notebook_path) != normalized:
                    raise HTTPException(409, "An incomplete prior save contains different edits; temporary notebook retained")
            else:
                _write_new(notebook_path, normalized)
            _write_new(destination, json.dumps(receipt))
            # Re-read saved bytes before acknowledging. Hosted dispatcher must also flush.
            self.source(job_id, session["artifact_path"])
            return {**receipt, "saved": True, "cleanup_required": True}

    def cleanup(self, job_id, body):
        from databricks.sdk.errors import NotFound
        with self.lock:
            identity, session, directory = self.session(job_id, body)
            marker = directory / "cleanup" / (body.session_id + ".json")
            _, _, _, _, latest = self.source(job_id, session["artifact_path"])
            receipt_path = directory / "revisions" / f"{session['sequence']:08d}-{body.session_id}.json"
            if not receipt_path.exists():
                raise HTTPException(409, "No verified saved revision exists. Temporary notebook retained.")
            receipt = json.loads(_read(receipt_path))
            saved = _read(directory / "revisions" / (body.session_id + ".ipynb"))
            if digest(saved) != receipt["content_sha256"] or body.expected_remote_sha256 != receipt["remote_sha256"]:
                raise HTTPException(409, "Saved revision does not match the requested cleanup")
            try:
                raw = self.remote(identity, session)
                if digest(raw) != receipt["remote_sha256"]:
                    raise HTTPException(409, "New edits appeared after save. Temporary notebook retained; save those edits before cleanup.")
                identity.client.workspace.delete(session["notebook_path"], recursive=False)
                try:
                    identity.client.workspace.get_status(session["notebook_path"])
                except NotFound:
                    pass
                else:
                    raise HTTPException(502, "Temporary notebook deletion is unconfirmed")
            except NotFound:
                # Recover a lost deletion acknowledgement, but only after saved-byte verification.
                pass
            result = {"job_id": job_id, "session_id": body.session_id, "saved": True,
                      "content_sha256": receipt["content_sha256"], "remote_deleted": True,
                      "message": "Returned edits saved as an unapproved app revision. Temporary Databricks notebook deleted. Original generated files remain unchanged."}
            if not marker.exists():
                _write_new(marker, json.dumps(result))
            return result


def install(app, load_candidate, lock):
    editor = NotebookEditor(app, load_candidate, lock)
    app.state.notebook_editor = editor

    @app.post("/api/jobs/{job_id}/spark-drafts/edit-preview")
    def preview(job_id: str, body: EditRequest):
        return editor.preview(job_id, body)

    @app.post("/api/jobs/{job_id}/spark-drafts/edit-return")
    def save(job_id: str, body: EditRequest):
        return editor.save(job_id, body)

    @app.post("/api/jobs/{job_id}/spark-drafts/edit-cleanup")
    def cleanup(job_id: str, body: EditRequest):
        return editor.cleanup(job_id, body)