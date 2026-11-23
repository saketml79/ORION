"""Independent immutable graph manifests in existing user-owned jobs/Volume authority.

No DDL, local backend, token persistence, service identity or cross-request cache.
Only explicit curation publishes. A failed/missing read never restores an old graph.
"""
import json
import re

from fastapi import HTTPException, Request
from pydantic import Field

from workbench import delta_state as ds
from workbench.architecture_knowledge import (
    FORMAT, MAX_BYTES, FeatureRequest, Snapshot, Strict, curate, unavailable, validate,
)
from workbench.campaign_artifacts import read_artifact

PACKAGE = ds._hash(FORMAT)
CONTEXT = "architecture-knowledge:v1"


class KnowledgeStore:
    def __init__(self, delta, artifacts):
        if not isinstance(artifacts, ds.VolumeArtifacts) or artifacts.store is not delta:
            raise PermissionError("Knowledge requires the same scoped Delta and Volume authority")
        self.delta, self.artifacts = delta, artifacts

    def scope(self, project):
        if not re.fullmatch(r"[a-f0-9]{32}", project):
            raise ValueError("An explicit project namespace is required")
        return dict(project_id=project, package_hash=PACKAGE, context_id=CONTEXT)

    def _record(self, manifest):
        ref = manifest["artifact"]
        return self.delta.make_record("jobs", **self.scope(manifest["project_id"]),
            source_path="architecture-knowledge", source_table=FORMAT, source_key=manifest["version"],
            payload=manifest, status="curated", kind=FORMAT, version=1,
            created_at=manifest["created_at"], artifact_path=ref["volume_path"],
            artifact_sha256=ref["sha256"], byte_size=ref["byte_size"])

    def manifests(self, project):
        rows, after = [], None
        while True:
            page = self.delta.read_table("jobs", **self.scope(project), after=after)
            if len(page) > 50 or len(rows) + len(page) > 128:
                raise ValueError("Knowledge manifest bound exceeded")
            for row in page:
                pos = (row["record_id"], row["content_hash"], row["migration_id"])
                if after is not None and pos <= after:
                    raise ValueError("Knowledge manifest cursor did not advance")
                after = pos
                if len(row["payload"].encode()) > ds.MAX_PAYLOAD:
                    raise ValueError("Knowledge manifest bound exceeded")
                m = json.loads(row["payload"])
                expected_keys = {"format", "version", "created_at", "workspace_id", "user_id", "project_id", "graph_sha256", "artifact", "manifest_sha256"}
                if (set(m) != expected_keys or m["format"] != FORMAT
                        or m["workspace_id"] != self.delta.workspace_id or m["user_id"] != self.delta.user_id
                        or m["project_id"] != project or m["manifest_sha256"] != ds._hash({k: v for k, v in m.items() if k != "manifest_sha256"})
                        or row != dict(self._record(m), migration_id="knowledge:" + m["version"])):
                    raise ValueError("Knowledge manifest scope/hash/envelope mismatch")
                rows.append(m)
            if len(page) < 50:
                break
        return rows

    def load(self, project):
        manifests = self.manifests(project)
        if not manifests:
            return None
        # Select before fetching. Never skip corrupt/unavailable latest for an older revision.
        m = max(manifests, key=lambda x: (x["created_at"], x["version"]))
        raw = read_artifact(self.artifacts, m["artifact"])
        if len(raw) > MAX_BYTES:
            raise ValueError("Knowledge graph bound exceeded")
        value = validate(json.loads(raw), self.delta.workspace_id, self.delta.user_id, project)
        if any(value[k] != m[k] for k in ("version", "created_at", "graph_sha256")):
            raise ValueError("Knowledge manifest/graph mismatch")
        return Snapshot(value, self.delta.workspace_id, self.delta.user_id, project)

    def save(self, value, *, consent):
        if consent is not True:
            raise PermissionError("Explicit consent is required to store curated knowledge")
        project = value["project_id"]
        validate(value, self.delta.workspace_id, self.delta.user_id, project)
        self.scope(project)
        raw = ds._json(value).encode()
        digest = ds.sha256(raw).hexdigest()
        reference = {"volume_path": self.artifacts._path(digest), "sha256": digest, "byte_size": len(raw)}
        m = {k: value[k] for k in ("format", "version", "created_at", "workspace_id", "user_id", "project_id", "graph_sha256")}
        m["artifact"] = reference
        m["manifest_sha256"] = ds._hash(m)
        record = self._record(m)
        if len(ds._json(record).encode()) >= ds.MAX_PAYLOAD:
            raise ValueError("Knowledge manifest envelope exceeds Delta bound")
        if self.artifacts.put(raw) != reference or read_artifact(self.artifacts, reference) != raw:
            raise ValueError("Knowledge artifact upload readback failed; manifest not published")
        self.delta.upsert_records("jobs", [record], migration_id="knowledge:" + value["version"])
        if m not in self.manifests(project):
            raise ValueError("Knowledge manifest durable readback failed")
        loaded = self.load(project)
        if loaded is None or loaded.value != value:
            raise ValueError("Knowledge published snapshot readback mismatch")
        return {"status": "published", "version": value["version"], "graph_sha256": value["graph_sha256"],
            "artifact": reference, "manifest_sha256": m["manifest_sha256"], "readback_verified": True,
            "execution_allowed": False, "deployment_allowed": False}


class CurateBody(Strict):
    consent: bool = False
    features: list[FeatureRequest] = Field(min_length=1, max_length=12)


def project_for_root(root):
    """The existing project package service path, not a caller-controlled namespace."""
    parts = root.parts
    if len(parts) >= 3 and parts[-2] == "package-contexts" and re.fullmatch(r"[a-f0-9]{32}", parts[-3]):
        return parts[-3]
    return None


def install_knowledge(app):
    def scoped(request):
        provider = getattr(app.state, "knowledge_store_provider", None)
        if provider is None or not getattr(app.state, "hosted_request_authenticated", False):
            raise HTTPException(409, "Knowledge requires authenticated UC authority; no local fallback")
        project = project_for_root(app.state.service.root)
        if not project or request.headers.get("X-Workbench-Project") != project:
            raise HTTPException(403, "Knowledge requires the selected project service")
        return provider(), project

    @app.get("/api/architecture-knowledge")
    def status(request: Request):
        store, project = scoped(request)
        snapshot = store.load(project)
        return snapshot.status() if snapshot else unavailable()

    @app.post("/api/architecture-knowledge/curate", status_code=201)
    def curate_knowledge(body: CurateBody, request: Request):
        store, project = scoped(request)
        if body.consent is not True:
            raise HTTPException(403, "Explicit storage consent:true required; no inference is invoked")
        try:
            value = curate([f.model_dump() for f in body.features], store.delta.workspace_id, store.delta.user_id, project)
            receipt = store.save(value, consent=True)
            return {"receipt": receipt, "knowledge": store.load(project).status()}
        except (ValueError, PermissionError):
            raise HTTPException(422, "Knowledge curation failed verification; no release state is inferred") from None