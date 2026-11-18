"""Fresh observational history projections. No execution-cache/package restore."""
import json
import re

from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.hosted_registry import committed_generation, _registry
from workbench.hosted_object_reads import scope_records

PATHS = {"/api/agents/runs", "/api/loops", "/api/tracing/status"}
MAX_RECORDS = 10000


def history_response(runtime, identity, path, project, package, query, progress=None):
    if package and not project:
        raise HTTPException(400, "X-Workbench-Package requires X-Workbench-Project")
    if project and not re.fullmatch(r"[a-f0-9]{32}", project):
        raise HTTPException(404, "Unknown project ID")
    if package and not re.fullmatch(r"[a-f0-9]{64}", package):
        raise HTTPException(403, "Package is not a ready member of this project")
    try:
        offset = int(query.get("offset", ["0"])[-1])
        limit = int(query.get("limit", ["50"])[-1])
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid history pagination") from None
    if progress:
        progress("reading_history")
    committed = committed_generation(runtime)
    receipt, manifest = committed
    if project:
        _, registry = _registry(runtime, tables=("projects", "packages"), committed=committed)
        if not any(owner == project for owner, _ in registry["projects"]):
            raise HTTPException(404, "Unknown project ID")
        if not package or not any(owner == project and row["sha256"] == package
                                  and row["status"] == "completed" for owner, row in registry["packages"]):
            raise HTTPException(403, "Package is not a ready member of this project")
    if path == "/api/tracing/status":
        from workbench.tracing import tracing_status
        return tracing_status()
    prefix = f"projects/{project}/package-contexts/{package}/" if project else ""
    scope = ds._scope(prefix + "state/loops.sqlite")
    inventory = manifest["inventory"] if manifest else None
    generation = receipt["migration_id"] if receipt else None

    def rows(table):
        if inventory is None:
            return []
        return scope_records(runtime, table, inventory, generation, scope, maximum=MAX_RECORDS)

    file_rows = rows("artifact_manifest")
    files = {row["source_path"]: row for row in file_rows}
    if len(files) != len(file_rows):
        raise ValueError("Duplicate history artifact identity")

    def verify_file(name, *, download=True):
        file = files.get(name)
        if file is None or file["source_table"] != "file" or json.loads(file["source_key"]) != name:
            raise ValueError("Missing history artifact identity")
        reference = runtime._decode(json.loads(file["payload"]))
        if (set(reference) != {"volume_path", "sha256", "byte_size"}
                or reference["volume_path"] != file["artifact_path"]
                or reference["sha256"] != file["artifact_sha256"]
                or reference["byte_size"] != file["byte_size"]):
            raise ValueError("History artifact reference mismatch")
        # Delta source columns, not file snapshots, are content authority.
        # Verify the committed reference but do not download unused full run
        # output or SQLite DDL merely to list its already-verified source rows.
        if download:
            runtime._object(reference)

    if package:
        for table, name in (("settings", prefix + "state/active.json"),
                            ("packages", prefix + f"packages/{package}/manifest.json")):
            verify_file(name)
            documents = [row for row in rows(table) if row["source_path"] == name]
            if any(json.loads(row["source_key"]) != name for row in documents):
                raise ValueError("Invalid selected package metadata identity")
            document = runtime._json_file(name, documents)
            if document.get("sha256") != package:
                raise HTTPException(409, "Persisted project package does not match selection")

    def unverified(record):
        return {**record, "current_input_version": None, "stale": True if record.get("stale") is True else None,
                "freshness_status": "unverified", "verification_status": "unverified"}

    if path == "/api/agents/runs":
        records, seen = [], set()
        for row in rows("agent_runs"):
            name = runtime._local_path(row["source_path"])
            # agents.sqlite3 contains runtime ledger columns, not the public run
            # document. Never confuse it with state/agent-runs/<id>.json.
            if not name.startswith(prefix + "state/agent-runs/") or row["source_table"] != "json":
                continue
            if (not re.fullmatch(re.escape(prefix) + r"state/agent-runs/[a-f0-9]{32}\.json", name)
                    or json.loads(row["source_key"]) != name or name in seen):
                raise ValueError("Invalid history source identity")
            seen.add(name)
            verify_file(name, download=False)
            payload = json.loads(row["payload"])
            if isinstance(payload, dict) and "$artifact" in payload:
                payload = runtime._decode(payload)
            if not isinstance(payload, dict):
                raise ValueError("Invalid run document")
            record = {key: runtime._decode(value) for key, value in payload.items() if key != "output"}
            if (record.get("id") != name.rsplit("/", 1)[-1][:-5]
                    or (package and record.get("package_hash") != package)):
                raise ValueError("History package or run identity mismatch")
            records.append(unverified({k: v for k, v in record.items() if k != "output"}))
        # A committed file without its Delta JSON row must fail, not disappear.
        expected = {name for name in files if name.startswith(prefix + "state/agent-runs/")}
        if seen != expected:
            raise ValueError("Incomplete history documents")
        records.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        active = any(r.get("status") in {"queued", "running"} for r in records)
        known = {"queued", "running", "completed", "partial", "complete", "succeeded", "success",
             "failed", "error", "cancelled", "canceled", "rejected"}
        return {"items": records[offset:offset + limit], "total": len(records),
                "offset": offset, "limit": limit, "truncated": offset + limit < len(records),
            "active_run_present": active, "run_status_verified": all(r.get("status") in known for r in records),
                "committed_generation": generation}
    if path != "/api/loops":
        raise HTTPException(404, "Unknown history read")
    from workbench.loops import DEFINITIONS, GATES
    from workbench.hosted_model_reads import model_status
    result = {"definitions": DEFINITIONS, **GATES, "totals": {}, "limit": limit, "offset": offset}
    name = prefix + "state/loops.sqlite"
    verified_file = False
    for table in ("runs", "events", "lessons"):
        records, seen = [], set()
        for row in rows(ds.SQLITE_MAP["loops.sqlite"][table]):
            if row["source_path"] != name or row["source_table"] != table:
                raise ValueError("Invalid loop history source")
            if not verified_file:
                verify_file(name, download=False)
                verified_file = True
            columns = runtime._decode(json.loads(row["payload"]))
            record = json.loads(columns["payload"])
            if (set(columns) - {"payload_decoded"} != {"id", "package_hash", "workflow_id", "payload"}
                    or json.loads(row["source_key"]) != [columns["id"]]
                    or columns["id"] in seen
                    or any(record[k] != columns[k] for k in ("id", "package_hash", "workflow_id"))
                    or (package and record["package_hash"] != package)):
                raise ValueError("Invalid loop history columns")
            seen.add(columns["id"])
            records.append(unverified(record) if table == "runs" else record)
        records.sort(key=lambda r: (r.get("created_at", ""), r["id"]), reverse=True)
        if table == "runs":
            result["active_loop_present"] = any(r.get("status") in {"queued", "running"} for r in records)
            result["loop_status_verified"] = all(r.get("status") in {
                "queued", "running", "completed", "failed", "cancelled", "canceled",
                "interrupted", "rejected", "unavailable"} for r in records)
        result[table] = records[offset:offset + limit]
        result["totals"][table] = len(records)
    models = model_status(runtime, identity, project, package, committed=committed)
    result["current_auth_status"] = {"authentication_verified": True,
        "inference_ready": models["inference_ready"], "consent_required": True,
        "runtime_verified": False}
    result["truncated"] = any(offset + limit < n for n in result["totals"].values())
    return result