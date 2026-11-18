"""Pure bounded reader for committed artifacts and separately labeled saved drafts."""
from hashlib import sha256
import json
import re

from fastapi import HTTPException


def matching_jobs(record, jobs):
    """Exact immutable design/input receipts, never inferred artifact paths."""
    keys = ('package_hash', 'workflow_id', 'scenario', 'input_version')
    result = []
    for job in jobs:
        if (job.get('kind') == 'spark_draft' and job.get('design_id') == record['id']
                and job.get('design_version') == record['version']
                and all(job.get(key) == record[key] for key in keys)):
            identifier = job.get('id', '')
            if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
                raise ValueError('Invalid saved draft job identity')
            result.append({
                **{key: job.get(key) for key in ('id', 'status', 'created_at', 'design_id', 'design_version')},
                **{key: job[key] for key in ('kind', *keys) if key in job},
            })
    return sorted(result, key=lambda job: (job.get('created_at') or '', job['id']), reverse=True)

def read_result(root, record, job_id):
    """Read only checksum-bound committed or staged paths, never HTTP paths."""
    from workbench.spark_codegen import MAX_CONTENT, MAX_TOTAL, _path
    if record.get("kind") != "spark_draft":
        raise HTTPException(404, "Unknown draft job in this package")
    if record["status"] != "completed":
        raise HTTPException(409, "Draft result is not committed; read the job status")
    try:
        output = record["output"]
        package, design, run = record["package_hash"], record["design_id"], output["run_id"]
        if not (re.fullmatch(r"[a-f0-9]{64}", package) and re.fullmatch(r"[a-f0-9]{64}", design)
                and re.fullmatch(r"[a-f0-9]{32}", run)):
            raise ValueError("Invalid draft identity")
        root = root.resolve()
        directory = root / "generated-drafts" / package / design / str(record["design_version"]) / run

        def read(relative, limit):
            target = directory / relative
            for part in (target, *target.parents):
                if part == root:
                    break
                if part.is_symlink():
                    raise ValueError("Symlink in draft storage")
            if not target.resolve().is_relative_to(root):
                raise ValueError("Draft path escaped storage")
            with target.open("rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Draft response bound exceeded")
            return raw

        raw = read("manifest.json", 8 * 1024 * 1024)
        if sha256(raw).hexdigest() != record["manifest_sha256"]:
            raise ValueError("Manifest checksum mismatch")
        manifest = json.loads(raw)
        for name, expected in {"run_id": run, "package_hash": package, "design_id": design,
                               "design_version": record["design_version"],
                               "input_version": record["input_version"]}.items():
            if manifest.get(name) != expected:
                raise ValueError("Manifest scope mismatch")
        hashes = manifest["artifact_hashes"]
        if not isinstance(hashes, dict) or len(hashes) > 1000:
            raise ValueError("Invalid artifact inventory")
        artifacts, staged_artifacts, total = {}, {}, 0
        kinds = {"notebooks": "notebook", "modules": "module", "sql": "sql", "tests": "test"}
        for path, digest in hashes.items():
            _path(path, kinds[path.split("/")[0]])
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid artifact checksum")
            body = read(path, min(MAX_CONTENT * 2, MAX_TOTAL * 2 - total))
            total += len(body)
            if sha256(body).hexdigest() != digest:
                raise ValueError("Artifact checksum mismatch")
            artifacts[path] = body.decode("utf-8")
        # A blocked run still has useful model-authored code in its immutable
        # staged receipts. Expose it separately from committed artifacts so
        # the UI can preview/download it without implying completeness.
        if manifest.get("status") in {"blocked", "drafts_saved"}:
            for item in manifest.get("staged_drafts", []):
                if (not isinstance(item, dict) or item.get("staged") is not True
                        or item.get("committed") is not False or item.get("artifact_approved") is not False
                        or item.get("execution_allowed") is not False or item.get("deployment_allowed") is not False):
                    continue
                relative = item.get("relative_path")
                kind = item.get("kind")
                digest = item.get("content_sha256")
                audit_path = item.get("audit_relative_path")
                if kind not in {"notebook", "module", "sql", "test"}:
                    continue
                _path(relative, kind)
                if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                        or audit_path != f"staged-artifacts/{digest}/{relative}"):
                    raise ValueError("Invalid staged draft receipt")
                body = read(audit_path, min(MAX_CONTENT * 2, MAX_TOTAL * 2 - total))
                total += len(body)
                if sha256(body).hexdigest() != digest or item.get("bytes") != len(body):
                    raise ValueError("Staged artifact checksum mismatch")
                content = body.decode("utf-8")
                if kind == "notebook":
                    notebook = json.loads(content)
                    cells = notebook.get("cells") if isinstance(notebook, dict) else None
                    if not isinstance(cells, list):
                        raise ValueError("Invalid staged notebook")
                    for cell in cells:
                        metadata = cell.get("metadata")
                        if not isinstance(metadata, dict) or not isinstance(metadata.get("language"), str):
                            raise ValueError("Staged notebook cell language metadata missing")
                staged_artifacts[relative] = content
        from workbench.notebook_edits import revision_state
        revisions = revision_state(root, job_id, {**staged_artifacts, **artifacts})
        return {"job_id": job_id, "manifest": manifest, "artifacts": artifacts,
            "staged_artifacts": staged_artifacts, "notebook_revisions": revisions}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, "Draft manifest or artifacts failed bounded integrity validation") from None