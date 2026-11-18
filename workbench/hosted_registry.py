"""Committed registry projections, without restoring an execution cache.

The caller supplies a fresh, OBO-bound DeltaRuntime after owned-root provisioning.
Only generation evidence and externalized registry columns are downloaded. Reads
do not initialize tables or advance runtime state. A unique, independently read
back audit append must succeed before either a result or a registry 404 escapes.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from databricks.sdk.errors import NotFound
from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.delta_runtime import DeltaRuntime, RUNTIME_SCOPE
from workbench.hosted_write_lock import DELTA_WRITE_LOCK

_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SOURCE = "projects/projects.sqlite"
_JOURNAL = "hosted-registry-read-v1"
_COLUMNS = {"projects": ("id", "record"), "packages": ("project_id", "hash", "record"),
            "jobs": ("project_id", "id", "record")}


def _envelope(runtime, table, row, scope, generation):
    if (set(row) != set(ds.COLUMNS)
            or any(type(row[k]) is not (int if typ == "BIGINT" else str)
                   for k, typ in ds.COLUMNS.items())):
        raise ValueError("Invalid registry metadata envelope")
    if any(row[k] != value for k, value in {
            "workspace_id": runtime.store.workspace_id, "user_id": runtime.store.user_id,
            "migration_id": generation, **scope}.items()):
        raise PermissionError("Registry metadata namespace mismatch")
    identity = [table, row["workspace_id"], row["user_id"], row["project_id"],
                row["package_hash"], row["context_id"], row["source_path"],
                row["source_table"], row["source_key"]]
    if row["record_id"] != ds._hash(identity) or row["content_hash"] != ds._content_hash(row):
        raise ValueError("Registry metadata checksum mismatch")
    runtime._checked(ds._json(row).encode())


def _reference(reference):
    # Registry metadata never needs a source/XML reference, even if a damaged
    # payload tries to disguise one as an externalized source column.
    if not isinstance(reference, dict) or set(reference) != {"volume_path", "sha256", "byte_size"}:
        raise ValueError("Invalid external registry metadata reference")


def _references(value):
    if isinstance(value, dict):
        if "$artifact" in value:
            if set(value) != {"$artifact", "encoding"} or value["encoding"] not in {"json", "utf-8"}:
                raise ValueError("Invalid external registry metadata encoding")
            _reference(value["$artifact"])
        else:
            for item in value.values():
                _references(item)
    elif isinstance(value, list):
        for item in value:
            _references(item)


def _columns(runtime, table, row):
    payload = json.loads(row["payload"])
    # Unwrap only the whole source-column document here. Do not recursively
    # decode record_decoded, build_records' supplementary convenience copy.
    if isinstance(payload, dict) and "$artifact" in payload:
        _references(payload)
        if payload["encoding"] != "json":
            raise ValueError("Invalid external registry column document")
        payload = json.loads(runtime._object(payload["$artifact"]))
    columns = _COLUMNS[table]
    if (not isinstance(payload, dict) or not set(columns) <= set(payload)
            or set(payload) - set(columns) - {"record_decoded"}):
        raise ValueError("Invalid registry source columns")
    exact = {name: payload[name] for name in columns}
    _references(exact)
    # These SQLite source columns are TEXT. Publication externalizes a TEXT
    # column only as utf-8, never as a recursively decoded JSON document.
    if any(not isinstance(value, str) and not (
            isinstance(value, dict) and set(value) == {"$artifact", "encoding"}
            and value["encoding"] == "utf-8") for value in exact.values()):
        raise ValueError("Invalid external registry TEXT column")
    exact = runtime._decode(exact)
    if any(not isinstance(value, str) for value in exact.values()):
        raise ValueError("Invalid registry source column value")
    record = json.loads(exact["record"])
    if not isinstance(record, dict):
        raise ValueError("Invalid registry record JSON")
    project = exact["id"] if table == "projects" else exact["project_id"]
    key = [exact[name] for name in columns if name != "record"]
    if (not _ID.fullmatch(project) or row["project_id"] != project
            or json.loads(row["source_key"]) != key
            or ("project_id" in record and record["project_id"] != project)):
        raise ValueError("Registry source identity mismatch")
    if table == "packages":
        if not _HASH.fullmatch(exact["hash"]) or record.get("sha256") != exact["hash"]:
            raise ValueError("Registry package identity mismatch")
    elif not _ID.fullmatch(exact["id"]) or record.get("id") != exact["id"]:
        raise ValueError("Registry record identity mismatch")
    # Re-derive the scope exactly as the publication path does, rather than
    # assuming projects or package memberships were stored in project contexts.
    scope = ds._scope(_SOURCE, {**record, **exact})
    if table == "projects":
        scope["project_id"] = project
    if any(row[k] != value for k, value in scope.items()):
        raise ValueError("Registry source scope mismatch")
    return project, tuple(key), record


def committed_generation(runtime):
    """Fresh receipt and verified immutable inventory, shared by read projections."""
    receipt = runtime._latest()
    if receipt is None:
        try:
            runtime._read(runtime.remote + "/HEAD.json", 4096)
        except NotFound:
            return None, None
        raise ValueError("Legacy snapshot exists; explicit Delta import required")
    generation = receipt["migration_id"]
    _envelope(runtime, "migration_receipts", receipt, RUNTIME_SCOPE, generation)
    payload = json.loads(receipt["payload"])
    _reference(payload["manifest"])
    manifest = json.loads(runtime._object(payload["manifest"]))
    if (manifest.get("format") != 1 or manifest.get("generation") != generation
            or manifest.get("workspace_id") != runtime.store.workspace_id
            or manifest.get("user_id") != runtime.store.user_id
            or manifest.get("sequence") != receipt["sequence"]):
        raise PermissionError("Invalid generation namespace")
    inventory = manifest["inventory"]
    if not isinstance(inventory, dict) or set(inventory) != set(ds.TABLES) - {"migration_receipts"}:
        raise ValueError("Incomplete generation table inventory")
    return receipt, manifest


def _registry(runtime, *, tables=None, committed=None):
    receipt, manifest = committed if committed is not None else committed_generation(runtime)
    result = {table: [] for table in _COLUMNS}
    if receipt is None:
        return None, result
    generation, inventory = receipt["migration_id"], manifest["inventory"]
    for table in (_COLUMNS if tables is None else tables):
        expected = inventory[table]
        if not isinstance(expected, list):
            raise ValueError("Invalid registry inventory")
        scopes = {}
        for item in expected:
            if (not isinstance(item, dict) or set(item) != {
                    "record_id", "content_hash", "project_id", "package_hash", "context_id"}
                    or any(not isinstance(v, str) for v in item.values())
                    or not _HASH.fullmatch(item["record_id"]) or not _HASH.fullmatch(item["content_hash"])):
                raise ValueError("Invalid registry inventory entry")
            if item["context_id"] == "legacy":
                scope = tuple(item[k] for k in ("project_id", "package_hash", "context_id"))
                scopes.setdefault(scope, []).append((item["record_id"], item["content_hash"]))
        seen_keys = set()
        for scope_values, wanted in sorted(scopes.items()):
            scope = dict(zip(("project_id", "package_hash", "context_id"), scope_values, strict=True))
            actual, rows, cursor = [], [], None
            while True:
                page = runtime.store.read_table(table, **scope, migration_id=generation, after=cursor)
                if len(page) > 50:
                    raise ValueError("Oversize registry page")
                for row in page:
                    _envelope(runtime, table, row, scope, generation)
                    position = (row["record_id"], row["content_hash"], row["migration_id"])
                    if cursor is not None and position <= cursor:
                        raise ValueError("Non-advancing registry cursor")
                    cursor = position
                    actual.append(position[:2])
                rows.extend(page)
                if len(page) < 50:
                    break
            # Compare the ENTIRE queried scope before selecting registry sources.
            # Missing memberships/jobs must never become plausible partial lists.
            if len(set(wanted)) != len(wanted) or sorted(actual) != sorted(wanted):
                raise ValueError("Incomplete or unexpected registry scope records")
            for row in rows:
                if row["source_path"] == _SOURCE and row["source_table"] == table:
                    project, key, record = _columns(runtime, table, row)
                    if key in seen_keys:
                        raise ValueError("Duplicate registry source identity")
                    seen_keys.add(key)
                    result[table].append((project, record))
    return generation, result


def _audit(runtime, path, generation, status):
    event = uuid4().hex
    context = _JOURNAL + ":" + event
    scope = {**RUNTIME_SCOPE, "context_id": context}
    record = runtime.store.make_record(
        "audit_events", **scope, source_path="hosted-registry-read", source_table="http",
        source_key=event, kind=_JOURNAL, sequence=1, actor=runtime.store.user_id,
        payload={"event_id": event, "method": "GET", "request_path": path,
                 "owner_id": runtime.store.user_id, "committed_generation": generation,
                 "status_code": status})
    runtime._checked(ds._json(record).encode())
    # Delta optimistic concurrency can reject even single-user writers when two
    # browser/API GETs overlap. Serialize only the short audit append/readback;
    # the potentially expensive project reads remain concurrent.
    with DELTA_WRITE_LOCK:
        runtime.store.upsert_records("audit_events", [record], migration_id=context)
        # Unique event scope makes this bounded exact readback independent of
        # journal length, simultaneous reads, and the old HTTP audit chain.
        rows = runtime.store.read_table("audit_events", **scope, migration_id=context, limit=2)
        for row in rows:
            _envelope(runtime, "audit_events", row, scope, context)
        if rows != [dict(record, migration_id=context)]:
            raise ValueError("Hosted registry audit publication is unconfirmed")


def registry_response(runtime: DeltaRuntime, path: str) -> dict:
    """Project list/detail or project-scoped job from one committed generation.

    Integrity/auth/storage errors deliberately propagate to the hosted boundary's
    fixed 503/authorization handling. No partial result is acknowledged on error.
    """
    if not isinstance(path, str):
        raise HTTPException(404, "Unknown registry path")
    parts = path.split("/")
    if path == "/api/projects":
        project_id = job_id = None
    elif len(parts) in (4, 6) and parts[:3] == ["", "api", "projects"]:
        project_id = parts[3]
        if not _ID.fullmatch(project_id):
            raise HTTPException(404, "Unknown project ID")
        job_id = parts[5] if len(parts) == 6 and parts[4] == "jobs" else None
        if len(parts) == 6 and (parts[4] != "jobs" or not _ID.fullmatch(job_id or "")):
            raise HTTPException(404, "Unknown project job ID")
    else:
        raise HTTPException(404, "Unknown registry path")
    generation, registry = _registry(runtime)
    projects = {project: {**record, "local_only": False, "authentication": "databricks_obo"}
                for project, record in registry["projects"]}

    def ordered(table, project):
        return sorted((record for owner, record in registry[table] if owner == project),
                      key=lambda record: record.get("created_at", ""), reverse=True)

    def summary(project):
        packages = ordered("packages", project)
        return {**projects[project], "package_count": len(packages),
                "ready_package_count": sum(item["status"] == "completed" for item in packages)}

    error = None
    if project_id is None:
        response = {"items": [summary(project) for project in sorted(
            projects, key=lambda project: projects[project].get("created_at", ""), reverse=True)]}
    elif project_id not in projects:
        response, error = {}, "Unknown project ID"
    elif job_id is not None:
        response = next((record for record in ordered("jobs", project_id) if record["id"] == job_id), None)
        if response is None:
            response, error = {}, "Unknown project job ID"
    else:
        response = {**summary(project_id), "packages": ordered("packages", project_id),
                    "jobs": ordered("jobs", project_id)}
    _audit(runtime, path, generation, 404 if error else 200)
    if error:
        raise HTTPException(404, error)
    return response