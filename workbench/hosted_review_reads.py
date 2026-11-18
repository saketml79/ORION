"""Selected committed review reads, without an application or execution cache.

This module does not register proposals, approve designs, reconcile history, or
construct model clients. Invalid authority is an error, never a restore fallback.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

from fastapi import HTTPException

from workbench import delta_state as ds

MAX_RECORDS = 2000
MAX_BYTES = 16 * 1024 * 1024


class Evidence:
    def __init__(self, runtime, inventory, generation, scope):
        self.runtime, self.inventory, self.generation, self.scope = runtime, inventory, generation, scope
        self.used = 0
        self.tables = {}

    def rows(self, table):
        from workbench.hosted_object_reads import scope_records
        if table not in self.tables:
            self.tables[table] = scope_records(self.runtime, table, self.inventory,
                self.generation, self.scope, maximum=MAX_RECORDS)
            if len({row["record_id"] for row in self.tables[table]}) != len(self.tables[table]):
                raise ValueError("Ambiguous review source identities")
        return self.tables[table]

    def decode(self, value):
        # Count actual downloads, including nested externalized TEXT columns,
        # before fetching them. Never decode supplementary payload_decoded copies.
        if isinstance(value, dict):
            if "$artifact" in value:
                if set(value) != {"$artifact", "encoding"} or value["encoding"] not in {"json", "utf-8"}:
                    raise ValueError("Invalid review evidence reference")
                reference = value["$artifact"]
                size = reference.get("byte_size")
                if type(size) is not int or size < 0 or self.used + size > MAX_BYTES:
                    raise ValueError("Review evidence byte bound exceeded")
                self.used += size
                raw = self.runtime._object(reference)
                return self.decode(json.loads(raw)) if value["encoding"] == "json" else raw.decode("utf-8")
            return {key: self.decode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        return value

    def payload(self, row, columns=None, *, exclude=()):
        packed = json.loads(row["payload"])
        if isinstance(packed, dict) and "$artifact" in packed:
            # Unwrap only the outer source document before choosing TEXT columns.
            reference = packed["$artifact"]
            size = reference.get("byte_size")
            if packed.get("encoding") != "json" or type(size) is not int or size < 0 or self.used + size > MAX_BYTES:
                raise ValueError("Invalid review evidence document")
            self.used += size
            packed = json.loads(self.runtime._object(reference))
        if columns is not None:
            if not isinstance(packed, dict) or not set(columns) <= set(packed):
                raise ValueError("Missing review source columns")
            packed = {key: packed[key] for key in columns}
        if exclude:
            if not isinstance(packed, dict):
                raise ValueError("Invalid review source columns")
            packed = {key: value for key, value in packed.items() if key not in exclude}
        value = self.decode(packed)
        self.used += len(ds._json(value).encode())
        if self.used > MAX_BYTES:
            raise ValueError("Review evidence byte bound exceeded")
        return value

    def sql(self, row, filename, table, primary, extra=()):
        expected = f'projects/{self.scope["project_id"]}/package-contexts/{self.scope["package_hash"]}/state/{filename}'
        if row["source_path"] != expected or row["source_table"] != table:
            raise ValueError("Review source identity mismatch")
        raw = self.payload(row, (*primary, *extra, "payload"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid review source columns")
        if json.loads(row["source_key"]) != [raw[key] for key in primary]:
            raise ValueError("Review source key mismatch")
        value = json.loads(raw["payload"])
        if raw.get("package_hash", self.scope["package_hash"]) != self.scope["package_hash"] or value.get("package_hash", self.scope["package_hash"]) != self.scope["package_hash"]:
            raise ValueError("Review package mismatch")
        return raw, value


def _workflow(index, workflow):
    if workflow not in index.nodes or index.nodes[workflow]["kind"] != "workflow":
        raise HTTPException(404, "Unknown workflow in this package")


def proposals(index, evidence, query, identifier, by_path, materialize, offset, limit):
    from workbench.app import _hash, MAX_GRAPH_NODES, MAX_EVIDENCE
    scenario = query.get("scenario", ["original"])[-1]
    if scenario not in {"original", "bronze"}:
        raise HTTPException(422, "Invalid proposal scenario")
    workflow = query.get("workflow_id", [None])[-1] if identifier is None else None
    if workflow is not None:
        if not re.fullmatch(r"[a-f0-9]{64}", workflow):
            raise HTTPException(422, "Invalid canonical workflow ID")
        _workflow(index, workflow)
    name = f"proposals-{scenario}.json"
    full = (index.directory / name).relative_to(evidence.runtime.local).as_posix()
    if full in by_path:
        if by_path[full]["byte_size"] > ds.MAX_JSON:
            raise ValueError("Proposal baseline byte bound exceeded")
        materialize(name)
    summaries = index.proposal_summaries(scenario)
    selected = {row["id"]: row for row in summaries
                if (workflow is None or row["workflow_id"] == workflow)
                and (identifier is None or row["id"] == identifier)}
    current, documents = {}, []
    for row in evidence.rows("proposal_versions"):
        if row["source_table"] != "proposals":
            continue
        key = json.loads(row["source_key"])
        if not isinstance(key, list) or len(key) != 3:
            raise ValueError("Invalid proposal source key")
        if key[1] not in selected:
            continue
        raw, value = evidence.sql(row, "reviews.sqlite", "proposals", ("package_hash", "proposal_id", "version"))
        baseline = selected[key[1]]
        if (value.get("id") != raw["proposal_id"] or value.get("version") != raw["version"]
                or value.get("workflow_id") != baseline["workflow_id"] or value.get("scenario") != scenario
                or row["workflow_id"] != baseline["workflow_id"]):
            raise ValueError("Proposal source identity mismatch")
        if value["version"] > current.get(value["id"], {}).get("version", 0):
            current[value["id"]] = value
    for row in evidence.rows("documents"):
        if row["source_table"] != "documents":
            continue
        raw, value = evidence.sql(row, "reviews.sqlite", "documents", ("package_hash", "document_id"))
        if value.get("id") != raw["document_id"]:
            raise ValueError("Document source identity mismatch")
        documents.append(value)
    items = []
    for baseline in selected.values():
        ids = [d["id"] for d in documents if d.get("metadata", {}).get("workflow_id") in {None, "", baseline["workflow_id"]}]
        items.append({**baseline, **current.get(baseline["id"], {}),
            "evidence_version": _hash(sorted(ids)), "supporting_evidence_count": len(ids),
            "stale": bool(ids), "verification_status": "unverified",
            "stale_reason": "Supporting evidence changed since structural proposal construction; independent revalidation required." if ids else None})
    if identifier is not None:
        if not items:
            raise HTTPException(404, "Unknown proposal in this package/scenario")
        return items[0]
    page = items[offset:offset + limit]
    keys = ("id", "workflow_id", "name", "scenario", "version", "status", "blockers",
            "generation_allowed", "deployment_allowed", "summary")
    coverage = ("scoped_objects", "mapping_count", "unresolved_count", "redacted_connection_count",
                "semantic_status", "lifecycle", "scope_status", "connection_status", "structural_only")
    return {"items": [{**{key: item[key] for key in keys}, "coverage": {
                key: item["coverage"].get(key) for key in coverage}} for item in page],
        "total": len(items), "offset": offset, "limit": limit, "truncated": offset + len(page) < len(items),
        "scenario": scenario, "summary_only": True, "graph_node_limit": MAX_GRAPH_NODES,
        "evidence_limit": MAX_EVIDENCE, "build_seconds": index.proposal_build_seconds}


def cohort(index, evidence, identifier):
    from workbench.cohort_review import _inventory, operation_id, reviewed_members
    saved, members = None, []
    package = evidence.scope["package_hash"]
    for row in evidence.rows("jobs"):
        table = row["source_table"]
        if table not in {"cohort_runs", "cohort_workflows"}:
            continue
        key = json.loads(row["source_key"])
        if not isinstance(key, list) or len(key) != (2 if table == "cohort_runs" else 3):
            raise ValueError("Invalid cohort source key")
        if key[:2] != [package, identifier]:
            continue
        primary = ("package_hash", "id") if table == "cohort_runs" else ("package_hash", "run_id", "workflow_id")
        raw, value = evidence.sql(row, "cohort.sqlite", table, primary)
        if table == "cohort_runs":
            if saved is not None or value.get("id") != identifier or value.get("request", {}).get("request_id") != identifier:
                raise ValueError("Invalid cohort run identity")
            saved = deepcopy(value)
        else:
            if value.get("workflow_id") != raw["workflow_id"] or row["workflow_id"] != raw["workflow_id"]:
                raise ValueError("Invalid cohort workflow identity")
            value.setdefault("operation_id", operation_id(package, identifier, raw["workflow_id"]))
            members.append(value)
    if saved is None:
        raise HTTPException(404, "Unknown cohort in this package")
    saved["workflows"] = sorted(members, key=lambda value: value["workflow_id"])
    if len(members) > 200 or len({value["workflow_id"] for value in members}) != len(members):
        raise ValueError("Invalid cohort membership")
    request = saved["request"]
    try:
        inventory = _inventory(index, request["workflow_id"], request["scope"], request["input_version"])
        if [value["workflow_id"] for value in saved["workflows"]] != reviewed_members(index, inventory, request):
            raise HTTPException(409, "Saved cohort membership is incomplete or inconsistent; inspect the committed review")
        saved["stale"] = False
    except HTTPException as error:
        if error.status_code != 409 or error.detail != "Package evidence changed; refresh target review":
            raise
        saved["stale"] = True
    return saved


def selection(index, evidence, workflow, target):
    from workbench.cohort_review import _target
    _workflow(index, workflow)
    saved = None
    for row in evidence.rows("settings"):
        if row["source_table"] != "target_selections":
            continue
        key = json.loads(row["source_key"])
        if key != [evidence.scope["package_hash"], workflow, target]:
            continue
        raw, value = evidence.sql(row, "cohort.sqlite", "target_selections",
                                  ("package_hash", "workflow_id", "target_id"), ("version",))
        if (saved is not None or value.get("workflow_id") != workflow or row["workflow_id"] != workflow
                or value.get("target_id") != target or value.get("version") != raw["version"]):
            raise ValueError("Invalid target selection identity")
        saved = value
    if saved is None:
        return {"version": 0, "selection": None}
    try:
        _target(index, workflow, saved["scope"], saved["input_version"], target)
        saved["stale"] = False
    except HTTPException as error:
        if error.status_code not in {404, 409}:
            raise
        saved["stale"] = True
    return {"version": saved["version"], "selection": saved}


def review_response(runtime, index, path, query, inventory, generation, scope,
                    by_path, materialize, offset, limit):
    evidence = Evidence(runtime, inventory, generation, scope)
    if path.startswith("/api/proposals"):
        identifier = path.rsplit("/", 1)[-1] if path != "/api/proposals" else None
        return proposals(index, evidence, query, identifier, by_path, materialize, offset, limit)
    if path.startswith("/api/cohorts/"):
        return cohort(index, evidence, path.rsplit("/", 1)[-1])
    if path.startswith("/api/target-selections/"):
        workflow, target = path.split("/")[-2:]
        return selection(index, evidence, workflow, target)
    return draft_response(runtime, evidence, path, by_path, materialize)


def draft_response(runtime, evidence, path, by_path, materialize, *, metadata_only=False):
    from workbench.draft_artifact_reads import read_result
    parts = path.split("/")
    identifier = parts[3]
    prefix = f'projects/{evidence.scope["project_id"]}/package-contexts/{evidence.scope["package_hash"]}/'
    source = prefix + "state/jobs/" + identifier + ".json"
    rows = [row for row in evidence.rows("jobs") if row["source_path"] == source]
    if not rows:
        raise HTTPException(404, "Unknown job ID")
    if len(rows) != 1 or rows[0]["source_table"] != "json" or json.loads(rows[0]["source_key"]) != source:
        raise ValueError("Invalid draft job source identity")
    # Saved-design discovery needs exact identity/status metadata, not nested
    # writer output artifacts. Explicit job/result GETs retain their full read.
    record = evidence.payload(rows[0], exclude=("output",) if metadata_only else ())
    if not isinstance(record, dict):
        raise ValueError("Invalid draft job document")
    identity_matches = (
        record.get("id") == identifier
        and record.get("package_hash", evidence.scope["package_hash"]) == evidence.scope["package_hash"]
        and record.get("workflow_id", "") == rows[0]["workflow_id"]
    )
    if not identity_matches:
        raise ValueError("Draft job package mismatch")
    if len(parts) == 4:
        return record
    if record.get("kind") != "spark_draft":
        raise HTTPException(404, "Unknown draft job in this package")
    if record.get("status") != "completed":
        raise HTTPException(409, "Draft result is not committed; read the job status")
    package, design, run = record["package_hash"], record["design_id"], record["output"]["run_id"]
    if (not re.fullmatch(r"[a-f0-9]{64}", design) or not re.fullmatch(r"[a-f0-9]{32}", run)
            or type(record["design_version"]) is not int or record["design_version"] < 1):
        raise ValueError("Invalid draft output identity")
    directory = prefix + f'generated-drafts/{package}/{design}/{record["design_version"]}/{run}/'
    manifest_path = directory + "manifest.json"
    if manifest_path not in by_path:
        raise HTTPException(409, "Draft manifest or artifacts failed bounded integrity validation")
    if by_path[manifest_path]["byte_size"] > 8 * 1024 * 1024:
        raise ValueError("Draft manifest byte bound exceeded")
    raw_manifest = materialize("manifest.json", full=manifest_path).read_bytes()
    if sha256(raw_manifest).hexdigest() != record["manifest_sha256"]:
        raise HTTPException(409, "Draft manifest or artifacts failed bounded integrity validation")
    manifest = json.loads(raw_manifest)
    if any(manifest.get(key) != value for key, value in {
            "run_id": run, "package_hash": package, "design_id": design,
            "design_version": record["design_version"], "input_version": record["input_version"]}.items()):
        raise HTTPException(409, "Draft manifest or artifacts failed bounded integrity validation")
    hashes = manifest.get("artifact_hashes")
    if not isinstance(hashes, dict) or len(hashes) > 1000:
        raise ValueError("Invalid draft artifact inventory")
    from workbench.spark_codegen import MAX_CONTENT, MAX_TOTAL, _path
    kinds = {"notebooks": "notebook", "modules": "module", "sql": "sql", "tests": "test"}
    total = 0
    for name, digest in hashes.items():
        _path(name, kinds[name.split("/")[0]])
        full = directory + name
        row = by_path.get(full)
        if row is None or row["artifact_sha256"] != digest or row["byte_size"] > MAX_CONTENT * 2:
            raise ValueError("Draft artifact manifest mismatch")
        total += row["byte_size"]
        if total > MAX_TOTAL * 2:
            raise ValueError("Draft artifact byte bound exceeded")
        materialize(name, full=full)
    edit_directory = prefix + f"state/notebook-edits/{identifier}/revisions/"
    edit_files = [name for name in by_path if name.startswith(edit_directory)]
    if len(edit_files) > 1000:
        raise ValueError("Notebook revision inventory exceeds bound")
    for full in sorted(edit_files):
        if by_path[full]["byte_size"] > 2 * 1024 * 1024:
            raise ValueError("Notebook revision byte bound exceeded")
        materialize(full[len(prefix):], full=full)
    result = read_result(runtime.local / prefix, record, identifier)
    if len(parts) == 5 and parts[4] == "completeness":
        from workbench.draft_completeness import assess
        versions = []
        for row in evidence.rows("proposal_versions"):
            if row["source_table"] != "redesign_versions":
                continue
            key = json.loads(row["source_key"])
            if not isinstance(key, list) or len(key) != 2 or key[0] != design:
                continue
            raw, value = evidence.sql(row, "redesigns.sqlite", "redesign_versions", ("id", "version"),
                                      ("package_hash", "workflow_id", "scenario"))
            if (value.get("id") != design or value.get("version") != raw["version"]
                    or value.get("workflow_id") != raw["workflow_id"] or row["workflow_id"] != raw["workflow_id"]
                    or raw["workflow_id"] != record["workflow_id"] or raw["scenario"] != value.get("scenario")):
                raise ValueError("Draft design source mismatch")
            versions.append(value)
        exact = next((value for value in versions if value["version"] == record["design_version"]), None)
        if exact is None:
            raise HTTPException(409, "Draft design is not available in this package")
        stale = True if max(value["version"] for value in versions) != exact["version"] else None
        return {"job_id": identifier, **assess(exact["design"], result["manifest"], result["artifacts"], stale=stale)}
    return result