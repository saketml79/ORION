"""Pinned, user-owned model settings only. Never restore XML or agent history."""
import json
import re

from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.databricks_models import ROLE_PURPOSES, WorkspaceModels
from workbench.hosted_registry import committed_generation, _registry
from workbench.hosted_object_reads import scope_records
from workbench.hosted_registry import _envelope

SETTINGS_JOURNAL = "hosted-model-settings-v1"


def settings_scope(project=None, package=None):
    return dict(project_id=project or "__runtime__", package_hash=package or "__none__",
                context_id=SETTINGS_JOURNAL)


def latest_settings(runtime, project=None, package=None):
    """One bounded, exact-owner read. Invalid authority never falls back."""
    store = runtime.store
    store._verify_table("model_routes", store._authorize())
    scope = settings_scope(project, package)
    values = {"workspace_id": store.workspace_id, "user_id": store.user_id,
              **scope, "migration_id": SETTINGS_JOURNAL}
    where = " AND ".join(ds._identifier(k) + "=:" + k for k in values)
    names = ",".join(ds._identifier(k) for k in ds.COLUMNS)
    raw = store._execute(f"SELECT {names} FROM {store._name('model_routes')} WHERE {where} "
                         "ORDER BY sequence DESC LIMIT 2", values)
    rows = [{k: int(v) if ds.COLUMNS[k] == "BIGINT" else v
             for k, v in zip(ds.COLUMNS, row, strict=True)} for row in raw]
    for row in rows:
        _envelope(runtime, "model_routes", row, scope, SETTINGS_JOURNAL)
        payload = json.loads(row["payload"])
        if (row["sequence"] < 1 or row["status"] != "committed"
                or row["source_table"] != SETTINGS_JOURNAL
                or not re.fullmatch(r"[a-f0-9]{32}", row["source_key"])
                or set(payload) != {"document", "route_version", "previous_route_version", "parent_generation"}):
            raise ValueError("Invalid model settings revision")
    if len(rows) > 1 and rows[0]["sequence"] == rows[1]["sequence"]:
        raise ValueError("Ambiguous model settings writers")
    return rows[0] if rows else None


def model_status(runtime, identity, project=None, package=None, *, committed=None, validate_ready=True):
    if package and not project:
        raise HTTPException(400, "X-Workbench-Package requires X-Workbench-Project")
    if project and not re.fullmatch(r"[a-f0-9]{32}", project):
        raise HTTPException(404, "Unknown project ID")
    if package and not re.fullmatch(r"[a-f0-9]{64}", package):
        raise HTTPException(403, "Package is not a ready member of this project")
    committed = committed if committed is not None else committed_generation(runtime)
    receipt, manifest = committed
    if project:
        _, registry = _registry(runtime, tables=("projects", "packages"), committed=committed)
        if not any(owner == project for owner, _ in registry["projects"]):
            raise HTTPException(404, "Unknown project ID")
        if package and not any(owner == project and row["sha256"] == package
                               and row["status"] == "completed" for owner, row in registry["packages"]):
            raise HTTPException(403, "Package is not a ready member of this project")
    prefix = f"projects/{project}/package-contexts/{package}/" if package else ""
    name = prefix + "models/model-routing.json"
    scope = ds._scope(name)
    revision = latest_settings(runtime, project, package)
    if revision is not None:
        if revision["source_path"] != name:
            raise ValueError("Model settings source mismatch")
        document = json.loads(revision["payload"])["document"]
        if (not isinstance(document, dict) or set(document) != {"version", "identities"}
                or document["version"] != 1 or not isinstance(document["identities"], dict)
                or len(document["identities"]) != 1
                or any(not isinstance(entry, dict) or entry.get("workspace") != identity.host
                       or entry.get("user_id") != identity.user_id
                       for entry in document["identities"].values())):
            raise PermissionError("Model settings owner mismatch")
        target = runtime.local / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ds._json(document), encoding="utf-8")
    elif receipt is not None:
        generation, inventory = receipt["migration_id"], manifest["inventory"]
        files = scope_records(runtime, "artifact_manifest", inventory, generation, scope)
        rows = scope_records(runtime, "model_routes", inventory, generation, scope)
        files = [row for row in files if row["source_path"] == name]
        rows = [row for row in rows if row["source_path"] == name]
        if len(files) > 1 or (rows and not files):
            raise ValueError("Invalid model routing artifact inventory")
        if files:
            file = files[0]
            if file["source_table"] != "file" or json.loads(file["source_key"]) != name:
                raise ValueError("Invalid model routing artifact identity")
            reference = runtime._decode(json.loads(file["payload"]))
            if (set(reference) != {"volume_path", "sha256", "byte_size"}
                    or reference["volume_path"] != file["artifact_path"]
                    or reference["sha256"] != file["artifact_sha256"]
                    or reference["byte_size"] != file["byte_size"]):
                raise ValueError("Invalid model routing artifact reference")
            runtime._object(reference)
            document = runtime._json_file("model-routing.json", rows)
            if len(document["identities"]) != len(rows):
                raise ValueError("Duplicate model routing identity")
            if any(entry.get("workspace") != identity.host or entry.get("user_id") != identity.user_id
                   for entry in document["identities"].values()):
                raise PermissionError("Model routing owner mismatch")
            target = runtime.local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ds._json(document), encoding="utf-8")
    router = WorkspaceModels(runtime.local / prefix / "models")
    routes = router.load(identity)
    route_version = ds._hash(routes)
    if revision is not None and route_version != json.loads(revision["payload"])["route_version"]:
        raise ValueError("Model settings route checksum mismatch")
    # Revalidate READY chat metadata under this request's fresh OBO identity.
    # Status needs metadata validation, not six serial ChatOpenAI constructors.
    ready = bool(router.validated_routes(identity)) if routes and validate_ready else False
    endpoints = {route["endpoint"] for route in routes.values()}
    return {"connected": False, "profile": None, "mode": "obo", "roles": ROLE_PURPOSES,
            "workspace": identity.host,
            "active_endpoint": next(iter(endpoints)) if len(endpoints) == 1 else None,
            "route_version": route_version, "project_id": project, "package_hash": package,
            "settings_revision": revision["source_key"] if revision else None,
            "configured_roles": sorted(routes), "inference_ready": ready,
            "tool_calling_verified": False, "runtime_verified": False,
            "reason": "Saved user-scoped routes; endpoint discovery requires an explicit click."}