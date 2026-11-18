import io
import json
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from databricks.sdk.errors import NotFound
from fastapi import FastAPI, HTTPException
import pytest

from workbench.notebook_edits import EditRequest, NotebookEditor, digest, revision_state


def book(source="value = 1\n"):
    return json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {"id": "overview", "language": "markdown"}, "source": ["# Business overview\n"]},
        {"cell_type": "code", "metadata": {"id": "business", "language": "python"},
         "source": [source], "outputs": [], "execution_count": None}]})


class Workspace:
    def __init__(self):
        self.files = {}; self.uploads = []; self.deleted = []; self.fail_delete = False; self.object_id = 42

    def mkdirs(self, path):
        pass

    def upload(self, path, content, **kwargs):
        assert path not in self.files
        self.uploads.append(kwargs)
        self.files[path] = content

    def get_status(self, path):
        if path not in self.files:
            raise NotFound("missing")
        return SimpleNamespace(object_id=self.object_id)

    def download(self, path, **kwargs):
        return io.BytesIO(self.files[path])

    def delete(self, path, recursive=False):
        assert recursive is False
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(path)
        del self.files[path]


@pytest.fixture
def setup_editor(tmp_path, monkeypatch):
    root = tmp_path.resolve(); job_id = "a" * 32; run_id = "b" * 32
    package = "c" * 64; design = "d" * 64; artifact = "notebooks/task.ipynb"
    content = book()
    manifest = {"run_id": run_id, "package_hash": package, "design_id": design,
                "design_version": 1, "input_version": "e" * 64,
                "artifact_hashes": {artifact: digest(content)}}
    manifest_content = json.dumps(manifest)
    directory = root / "generated-drafts" / package / design / "1" / run_id
    (directory / "notebooks").mkdir(parents=True)
    (directory / "manifest.json").write_text(manifest_content, encoding="utf-8")
    (directory / artifact).write_text(content, encoding="utf-8")
    job = {"id": job_id, "kind": "spark_draft", "status": "completed", "package_hash": package,
           "design_id": design, "design_version": 1, "input_version": "e" * 64,
           "output": {"run_id": run_id}, "manifest_sha256": digest(manifest_content)}
    def loader(identifier, path, *, for_execution=True):
        assert identifier == job_id and path == artifact and for_execution is False
        return None, job, manifest, {"id": design, "version": 1}, content, json.loads(content)
    app = FastAPI(); app.state.service = SimpleNamespace(root=root)
    workspace = Workspace()
    user = SimpleNamespace(id="user-1", user_name="tester@example.com")
    identity = SimpleNamespace(host="https://example.cloud.databricks.com", user_id=user.id,
                               mode="user", client=SimpleNamespace(workspace=workspace, current_user=SimpleNamespace(me=lambda: user)))
    monkeypatch.setattr("workbench.databricks_models.profile_identity", lambda profile: identity)
    monkeypatch.setattr("workbench.databricks_models._profile_host", lambda: "https://example.cloud.databricks.com")
    editor = NotebookEditor(app, loader, Lock())
    attach_body = SimpleNamespace(consent=True, artifact_path=artifact, expected_workspace=identity.host)
    return SimpleNamespace(editor=editor, app=app, workspace=workspace, identity=identity, user=user,
                           body=attach_body, job=job, job_id=job_id, artifact=artifact, original=content,
                           root=root, directory=directory)


def edit(s):
    session = s.editor.attach(s.job_id, s.body)
    edited = json.loads(book("value = 2\n"))
    edited["cells"][1]["outputs"] = [{"output_type": "stream", "text": "private runtime output"}]
    edited["cells"][1]["execution_count"] = 3
    s.workspace.files[session["notebook_path"]] = json.dumps(edited).encode()
    request = EditRequest(consent=True, session_id=session["session_id"], expected_workspace=s.identity.host)
    preview = s.editor.preview(s.job_id, request)
    body = request.model_copy(update={"expected_remote_sha256": preview["remote_sha256"]})
    return session, preview, body


def test_roundtrip_save_before_delete_and_original_integrity(setup_editor):
    s = setup_editor; original_files = {p: p.read_bytes() for p in s.directory.rglob("*") if p.is_file()}
    session, preview, body = edit(s)
    assert "value = 2" in preview["diff"] and preview["saved"] is False
    saved = s.editor.save(s.job_id, body)
    assert saved["saved"] and not s.workspace.deleted
    assert saved["approved"] is False and saved["deployment_allowed"] is False
    revisions = revision_state(s.root, s.job_id, {s.artifact: s.original})
    cells = json.loads(revisions[s.artifact]["content"])["cells"]
    assert cells[1]["metadata"]["id"] == "business"
    assert cells[1]["outputs"] == [] and cells[1]["execution_count"] is None
    assert "private runtime output" not in revisions[s.artifact]["content"]
    cleanup = s.editor.cleanup(s.job_id, body)
    assert cleanup["remote_deleted"] and s.workspace.deleted == [session["notebook_path"]]
    assert all(path.read_bytes() == raw for path, raw in original_files.items())


def test_reopen_never_overwrites_remote_edits(setup_editor):
    s = setup_editor; session, _, _ = edit(s)
    remote = s.workspace.files[session["notebook_path"]]
    reopened = s.editor.attach(s.job_id, s.body)
    assert reopened["session_id"] == session["session_id"]
    assert s.workspace.files[session["notebook_path"]] == remote
    assert len(s.workspace.uploads) == 1 and s.workspace.uploads[0]["overwrite"] is False


def test_cleanup_requires_save(setup_editor):
    s = setup_editor; session, _, body = edit(s)
    with pytest.raises(HTTPException) as error:
        s.editor.cleanup(s.job_id, body)
    assert error.value.status_code == 409 and session["notebook_path"] in s.workspace.files


def test_remote_conflict_after_preview_retained(setup_editor):
    s = setup_editor; session, _, body = edit(s)
    s.workspace.files[session["notebook_path"]] = book("value = 3\n").encode()
    with pytest.raises(HTTPException, match="409"):
        s.editor.save(s.job_id, body)
    assert not s.workspace.deleted
    assert not revision_state(s.root, s.job_id, {s.artifact: s.original})


def test_cleanup_failure_recoverable_after_reload(setup_editor):
    s = setup_editor; session, _, body = edit(s); s.editor.save(s.job_id, body)
    s.workspace.fail_delete = True
    with pytest.raises(RuntimeError):
        s.editor.cleanup(s.job_id, body)
    reopened = s.editor.attach(s.job_id, s.body)
    assert reopened["saved"] and reopened["remote_sha256"] == body.expected_remote_sha256
    s.workspace.fail_delete = False
    assert s.editor.cleanup(s.job_id, body)["remote_deleted"]
    assert s.editor.cleanup(s.job_id, body)["remote_deleted"]


def test_new_remote_edits_after_save_not_deleted(setup_editor):
    s = setup_editor; session, _, body = edit(s); s.editor.save(s.job_id, body)
    s.workspace.files[session["notebook_path"]] = book("value = 4\n").encode()
    with pytest.raises(HTTPException, match="409"):
        s.editor.cleanup(s.job_id, body)
    assert not s.workspace.deleted


def test_changed_remote_object_not_deleted(setup_editor):
    s = setup_editor; _, _, body = edit(s); s.editor.save(s.job_id, body)
    s.workspace.object_id = 99
    with pytest.raises(HTTPException, match="409"):
        s.editor.cleanup(s.job_id, body)
    assert not s.workspace.deleted


def test_saved_revision_checksum_required_for_cleanup(setup_editor):
    s = setup_editor; _, _, body = edit(s); s.editor.save(s.job_id, body)
    path = s.root / "state" / "notebook-edits" / s.job_id / "revisions" / (body.session_id + ".ipynb")
    path.write_text(book("value = 9\n"), encoding="utf-8")
    with pytest.raises(HTTPException, match="409"):
        s.editor.cleanup(s.job_id, body)
    assert not s.workspace.deleted


def test_save_idempotent_and_app_revision_conflicts(setup_editor):
    s = setup_editor; _, _, body = edit(s)
    first = s.editor.save(s.job_id, body); second = s.editor.save(s.job_id, body)
    assert first["content_sha256"] == second["content_sha256"]
    with pytest.raises(HTTPException, match="409"):
        s.editor.preview(s.job_id, body)


def test_identity_change_blocks_return(setup_editor):
    s = setup_editor; _, _, body = edit(s)
    s.identity.user_id = "other"
    with pytest.raises(HTTPException, match="403"):
        s.editor.save(s.job_id, body)
    assert not s.workspace.deleted


def test_workspace_change_blocks_attachment(setup_editor):
    s = setup_editor; s.body.expected_workspace = "https://other.cloud.databricks.com"
    with pytest.raises(HTTPException, match="409"):
        s.editor.attach(s.job_id, s.body)
    assert not s.workspace.uploads


def test_hosted_uses_obo_never_local_profile(setup_editor, monkeypatch):
    s = setup_editor; s.app.state.hosted_request_authenticated = True; s.identity.mode = "obo"
    monkeypatch.setattr("workbench.hosted.current_identity", lambda: s.identity)
    monkeypatch.setattr("workbench.databricks_models.profile_identity", lambda *_: pytest.fail("hosted fallback"))
    session, _, body = edit(s)
    s.editor.save(s.job_id, body)
    assert s.editor.cleanup(s.job_id, body)["remote_deleted"]


@pytest.mark.parametrize("change", ["invalid_python", "credential"])
def test_invalid_or_sensitive_edits_not_saved(setup_editor, change):
    s = setup_editor; session = s.editor.attach(s.job_id, s.body)
    source = "this is invalid python !" if change == "invalid_python" else "password = 'unsafe-plaintext-password'\n"
    s.workspace.files[session["notebook_path"]] = book(source).encode()
    body = EditRequest(consent=True, session_id=session["session_id"], expected_workspace=s.identity.host)
    with pytest.raises(HTTPException, match="422"):
        s.editor.preview(s.job_id, body)
    assert not s.workspace.deleted


def test_delta_committed_revision_survives_restart_before_cleanup(setup_editor, tmp_path):
    from workbench import delta_state as ds
    from workbench.delta_runtime import DeltaRuntime
    from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
    s = setup_editor
    client = RuntimeStatementClient()
    store = ds.DeltaStateStore(client, "warehouse", "user_catalog", "workbench", "user-123")
    artifacts = ds.VolumeArtifacts(store)
    identity = SimpleNamespace(client=client, host=store.workspace_id, user_id=store.user_id)
    remote = "/Volumes/user_catalog/workbench/artifacts/users/test-user"
    runtime = DeltaRuntime(lambda: identity, remote, tmp_path / "delta-first",
                           warehouse="warehouse", store=store, artifacts=artifacts)
    runtime.restore()
    _, _, body = edit(s); s.editor.save(s.job_id, body)
    # Publish through the REAL runtime mapper, not a SQLite snapshot or local fallback.
    import shutil
    shutil.copytree(s.root / "state", runtime.local / "state", dirs_exist_ok=True)
    generation = runtime.flush()
    assert generation and not s.workspace.deleted
    assert not any(raw.startswith(b"SQLite format 3") for raw in client.objects.values())
    restored = DeltaRuntime(lambda: identity, remote, tmp_path / "delta-restart",
                            warehouse="warehouse", store=store, artifacts=artifacts)
    assert restored.restore()
    revisions = revision_state(restored.local, s.job_id, {s.artifact: s.original})
    assert "value = 2" in revisions[s.artifact]["content"]
    assert revisions[s.artifact]["session_id"] == body.session_id