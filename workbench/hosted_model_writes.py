"""Small model settings journal within the existing single-user writer.

No XML, full-generation copies, checkpoints or model clients on the active path.
The unused former writer is retained below solely as a rollback reference.
"""
import json
from uuid import uuid4

from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.databricks_models import ROLE_PURPOSES, WorkspaceModels
from workbench.delta_runtime import RUNTIME_SCOPE
from workbench.hosted_model_reads import model_status
from workbench.hosted_registry import committed_generation
from workbench.hosted_write_lock import serialize_delta_write


# OLD: retained only for source rollback, never called by hosted dispatch.
def _legacy_full_generation_select_model(runtime, identity, body, project=None, package=None, progress=None):
    if (not isinstance(body, dict) or set(body) - {
            "endpoint", "tool_support_confirmed", "expected_route_version"}
            or body.get("tool_support_confirmed") is not True
            or not isinstance(body.get("endpoint"), str)):
        raise HTTPException(422, "Explicit endpoint and tool compatibility confirmation required")
    def report(stage):
        if progress:
            progress(stage)
    report("metadata_restore")
    committed = committed_generation(runtime)
    previous = model_status(runtime, identity, project, package, committed=committed, validate_ready=False)
    expected = body.get("expected_route_version")
    if expected is not None and expected != previous["route_version"]:
        raise HTTPException(409, "Saved model route changed. Reload settings before a new Apply.")
    receipt, manifest = committed
    parent = receipt["migration_id"] if receipt else None
    sequence = receipt["sequence"] if receipt else 0
    prefix = f"projects/{project}/package-contexts/{package}/" if package else ""
    name = prefix + "models/model-routing.json"
    router = WorkspaceModels(runtime.local / prefix / "models")
    inventory = router.discover(identity)
    routes = router.configure(identity, {
        role: {"endpoint": body["endpoint"],
               "tool_support_evidence": "User declared chat and tool-call compatibility in model picker."}
        for role in ROLE_PURPOSES}, inventory)
    report("route_commit")
    # Validate all inherited records against the pinned complete inventory.
    # Do not download or reconstruct any unrelated file or execution state.
    records = runtime._records(manifest["inventory"], parent) if receipt else {
        table: [] for table in ds.TABLES if table != "migration_receipts"}
    raw = router.path.read_bytes()
    reference = runtime.artifacts.put(raw)
    replacement = ds.build_records(runtime.store,
        ds.Snapshot(runtime.local, [{"path": name, "sha256": reference["sha256"],
                                   "byte_size": len(raw)}]), runtime.artifacts)
    for table in ("model_routes", "artifact_manifest"):
        records[table] = [row for row in records[table] if row["source_path"] != name]
        records[table].extend(replacement[table])
    generation = uuid4().hex
    new_manifest = {"format": 1, "workspace_id": runtime.store.workspace_id,
        "user_id": runtime.store.user_id, "generation": generation,
        "sequence": sequence + 1, "parent": parent, "inventory": ds._inventory(records)}
    encoded = runtime._checked(ds._json(new_manifest).encode())
    if len(encoded) > ds.MAX_JSON:
        raise ValueError("Generation inventory exceeds bound")
    reference = runtime.artifacts.put(encoded)
    for table, rows in records.items():
        if rows:
            if table == "audit_events":
                serialize_delta_write(runtime.store.upsert_records, table, rows, migration_id=generation)
            else:
                runtime.store.upsert_records(table, rows, migration_id=generation)
    runtime._records(new_manifest["inventory"], generation)
    latest = runtime._latest()
    if (latest["migration_id"] if latest else None) != parent:
        raise HTTPException(409, "Committed parent changed; no route publication confirmed")
    row = runtime.store.make_record("migration_receipts", **RUNTIME_SCOPE,
        source_path="hosted-runtime", source_table="generation", source_key=generation,
        payload={"manifest": reference, "parent": parent}, sequence=sequence + 1,
        status="committed", artifact_path=reference["volume_path"],
        artifact_sha256=reference["sha256"], byte_size=reference["byte_size"])
    runtime.store.upsert_records("migration_receipts", [row], migration_id=generation)
    report("route_readback")
    final = runtime._latest()
    if final is None or final["migration_id"] != generation:
        raise ValueError("Model route publication is unconfirmed")
    verified = model_status(runtime, identity, project, package)
    if verified["route_version"] != ds._hash(routes):
        raise ValueError("Model route readback differs from requested selection")
    return {"selected_endpoint": body["endpoint"], "roles": list(ROLE_PURPOSES),
        "project_id": project, "package_hash": package,
        "route_version": verified["route_version"], "previous_route_version": previous["route_version"],
        "committed_generation": generation, "inference_ready": verified["inference_ready"],
        "runtime_verified": False, "tool_calling_verified": False,
        "notice": "Route metadata committed and read back. No inference was invoked."}


# NEW: the sole hosted endpoint-settings write path.
def select_model(runtime, identity, body, project=None, package=None, progress=None):
    from workbench.hosted_model_reads import SETTINGS_JOURNAL, latest_settings, settings_scope
    if (not isinstance(body, dict) or set(body) != {
            "endpoint", "tool_support_confirmed", "expected_route_version"}
            or body.get("tool_support_confirmed") is not True
            or not isinstance(body.get("endpoint"), str)):
        raise HTTPException(422, "Explicit endpoint and tool compatibility confirmation required")

    def report(stage):
        if progress:
            progress(stage)

    report("route_validate")
    committed = committed_generation(runtime)
    previous = model_status(runtime, identity, project, package, committed=committed, validate_ready=False)
    if body["expected_route_version"] != previous["route_version"]:
        raise HTTPException(409, "Saved model route changed. Reload settings before a new Apply.")
    old = latest_settings(runtime, project, package)
    prefix = f"projects/{project}/package-contexts/{package}/" if package else ""
    name = prefix + "models/model-routing.json"
    router = WorkspaceModels(runtime.local / prefix / "models")
    inventory = router.discover(identity)
    routes = router.configure(identity, {
        role: {"endpoint": body["endpoint"],
               "tool_support_evidence": "User declared chat and tool-call compatibility in model picker."}
        for role in ROLE_PURPOSES}, inventory)
    revision = uuid4().hex
    parent = committed[0]["migration_id"] if committed[0] else None
    row = runtime.store.make_record("model_routes", **settings_scope(project, package),
        source_path=name, source_table=SETTINGS_JOURNAL, source_key=revision,
        status="committed", sequence=old["sequence"] + 1 if old else 1,
        payload={"document": json.loads(router.path.read_text(encoding="utf-8")),
                 "route_version": ds._hash(routes), "previous_route_version": previous["route_version"],
                 "parent_generation": parent})
    runtime._checked(ds._json(row).encode())
    if len(row["payload"].encode()) > ds.MAX_PAYLOAD:
        raise ValueError("Model settings exceed inline metadata bound")
    # Existing per-user mutation lock covers this append and readback. No DDL,
    # artifacts, copied rows or full-generation receipts are published here.
    report("route_commit")
    current = runtime._latest()
    if (current["migration_id"] if current else None) != parent:
        raise HTTPException(409, "Committed parent changed; no settings write submitted")
    runtime.store.upsert_records("model_routes", [row], migration_id=SETTINGS_JOURNAL)
    report("route_readback")
    final = latest_settings(runtime, project, package)
    if final != dict(row, migration_id=SETTINGS_JOURNAL):
        raise ValueError("Model settings publication is unconfirmed")
    return {"selected_endpoint": body["endpoint"], "roles": list(ROLE_PURPOSES),
        "project_id": project, "package_hash": package,
        "route_version": ds._hash(routes), "previous_route_version": previous["route_version"],
        "committed_generation": parent, "settings_revision": revision,
        "settings_authority": SETTINGS_JOURNAL, "parent_generation": parent,
        "inference_ready": True, "runtime_verified": False, "tool_calling_verified": False,
        "notice": "Scoped setting committed and independently read back. No inference was invoked."}