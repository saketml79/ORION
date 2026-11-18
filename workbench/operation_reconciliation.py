"""Append-only observations of already committed analysis, never status repair.

No execution, source parse, runtime flush, canonical/review rewrite or replay.
Only an explicit same-user request can publish a separate receipt after exact
input, parent/current inventory, semantic configuration and overlay verification.
"""
from datetime import datetime, timezone
import gzip
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from workbench import delta_state as ds
from workbench.delta_runtime import RUNTIME_SCOPE
from workbench.hosted_registry import _envelope, _registry, committed_generation
from workbench.source_analysis import FORMAT, _version


class ReconciliationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent: bool
    request: dict
    project_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    parent_generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    committed_generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    analysis_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    analyzer_hashes: dict[str, str]


def _scope(operation):
    return dict(project_id="__operations__", package_hash="__none__",
                context_id="hosted-reconciliation:" + operation)


def _record(store, operation, observation):
    scope = _scope(operation)
    return dict(store.make_record("jobs", **scope, source_path="hosted-operations",
        source_table="reconciliations", source_key="1", payload=observation,
        sequence=1, status="committed_with_ack_failure", kind="source-analysis-java-v1"),
        migration_id=scope["context_id"])


def read_observation(service, store, operation):
    rows = store.read_table("jobs", **_scope(operation), limit=2)
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Ambiguous reconciliation receipt")
    from workbench.hosted_operations import _checked, _json
    value = _json(rows[0]["payload"])
    expected = {"observation", "operation_id", "request_fingerprint", "request", "workspace_id", "user_id",
                "project_id", "package_hash", "parent_generation", "committed_generation", "sequence",
                "analysis_version", "artifact_sha256", "base_understanding_sha256", "analyzer_hashes",
                "manifest_sha256", "preserved_inventory_sha256", "canonical_file_refs_sha256",
                "terminal_history_sha256", "observed_at", "proof_sha256"}
    if (not isinstance(value, dict) or set(value) != expected
            or value["observation"] != "committed_with_ack_failure" or value["operation_id"] != operation
            or value["workspace_id"] != store.workspace_id or value["user_id"] != store.user_id
            or type(value["sequence"]) is not int or value["sequence"] < 1
            or value["proof_sha256"] != ds._hash({k: v for k, v in value.items() if k != "proof_sha256"})
            or rows[0] != _record(store, operation, value)):
        raise ValueError("Invalid reconciliation receipt")
    _checked(value)
    history = service._read(store, operation)
    if (not history or history[-1]["status"] != "failed"
            or ds._hash(history) != value["terminal_history_sha256"]
            or history[-1]["fingerprint"] != value["request_fingerprint"]):
        raise ValueError("Reconciliation terminal history mismatch")
    return value


def _parent(runtime, generation):
    rows = runtime.store.read_table("migration_receipts", **RUNTIME_SCOPE,
                                    migration_id=generation, limit=2)
    if len(rows) != 1:
        raise ValueError("Parent generation receipt unavailable or ambiguous")
    receipt = rows[0]
    _envelope(runtime, "migration_receipts", receipt, RUNTIME_SCOPE, generation)
    manifest = json.loads(runtime._object(json.loads(receipt["payload"])["manifest"]))
    if (receipt["status"] != "committed" or manifest.get("format") != 1
            or manifest.get("generation") != generation or manifest.get("sequence") != receipt["sequence"]
            or manifest.get("workspace_id") != runtime.store.workspace_id
            or manifest.get("user_id") != runtime.store.user_id):
        raise ValueError("Invalid parent generation")
    return receipt, manifest


def _verified_records(runtime, receipt, manifest):
    records = runtime._records(manifest["inventory"], receipt["migration_id"])
    for table, rows in records.items():
        for row in rows:
            scope = {k: row[k] for k in ("project_id", "package_hash", "context_id")}
            _envelope(runtime, table, row, scope, receipt["migration_id"])
    return records


def reconcile_analysis(service, store, runtime, operation, raw, headers):
    from workbench.app import PackageIndex
    from workbench.hosted_operations import MAX_BODY, _checked, _json
    from workbench.source_analysis import RebuildBody, analysis_pointer
    if isinstance(raw, (bytes, str)):
        if len(raw if isinstance(raw, bytes) else raw.encode()) > MAX_BODY:
            raise ValueError("Reconciliation request exceeds bound")
        raw = _json(raw)
    forbidden = []
    for value in store.client.config.authenticate().values():
        if isinstance(value, str) and value:
            forbidden.extend((value, value.removeprefix("Bearer ")))
    if len(_checked(raw, forbidden)) > MAX_BODY:
        raise ValueError("Reconciliation request exceeds bound")
    body = ReconciliationBody.model_validate(raw)
    if body.consent is not True:
        raise PermissionError("Explicit reconciliation consent required")
    operation_id, request, fingerprint = service._request(body.request, forbidden)
    if (operation_id != operation or request["method"] != "POST"
            or request["path"] != "/api/source-analysis/rebuild" or request["query"]):
        raise ValueError("Only the exact original analysis request can be reconciled")
    inputs = RebuildBody.model_validate(request["body"])
    if inputs.consent is not True:
        raise PermissionError("Original analysis consent required")
    selected = {k.lower(): v for k, v in headers.items()}
    if (selected.get("x-workbench-project") != body.project_id
            or selected.get("x-workbench-package") != inputs.package_hash):
        raise PermissionError("Reconciliation selection mismatch")
    if (set(body.analyzer_hashes) != {"understanding.py", "source_evidence.py"}
            or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in body.analyzer_hashes.values())):
        raise ValueError("Exact analyzer fingerprints required")
    history = service._read(store, operation)
    if not history or history[-1]["status"] != "failed" or history[-1]["fingerprint"] != fingerprint:
        raise ValueError("Original failed request not verified")
    previous = read_observation(service, store, operation)
    if previous is not None:
        if previous["request"] != raw:
            raise ValueError("Conflicting immutable reconciliation")
        return previous

    current, manifest = committed_generation(runtime)
    if (current is None or current["migration_id"] != body.committed_generation
            or manifest["parent"] != body.parent_generation):
        raise ValueError("Requested commit is not the current direct child")
    parent, baseline = _parent(runtime, body.parent_generation)
    if current["sequence"] != parent["sequence"] + 1:
        raise ValueError("Reconciliation requires exactly one generation")
    before = _verified_records(runtime, parent, baseline)
    after = _verified_records(runtime, current, manifest)
    _, registry = _registry(runtime, tables=("projects", "packages"), committed=(current, manifest))
    if (not any(owner == body.project_id for owner, _ in registry["projects"])
            or not any(owner == body.project_id and item["sha256"] == inputs.package_hash
                       and item["status"] == "completed" for owner, item in registry["packages"])):
        raise PermissionError("Reconciliation package membership unavailable")

    prefix = f"projects/{body.project_id}/package-contexts/{inputs.package_hash}/packages/{inputs.package_hash}/"
    pointer_path = prefix + "source-analysis.json"
    archive_path = prefix + "analysis/" + body.analysis_version + ".json.gz"
    scope = ds._scope(pointer_path)
    # No semantic config, canonical, registry, reviews or other namespace change
    # may be hidden inside this observation. Audit permits additions only.
    for table in before:
        old = {(r["record_id"], r["content_hash"]): r for r in before[table]}
        new = {(r["record_id"], r["content_hash"]): r for r in after[table]}
        for key in old.keys() - new.keys():
            row = old[key]
            if not (table in {"artifact_manifest", "settings"} and row["source_path"] == pointer_path
                    and all(row[k] == v for k, v in scope.items())):
                raise ValueError(f"Previously committed history or canonical state changed: {table}, {row['source_path']}")
        for key in new.keys() - old.keys():
            row = new[key]
            if table == "audit_events":
                continue
            if not (table in {"artifact_manifest", "settings"}
                    and row["source_path"] in ({pointer_path, archive_path} if table == "artifact_manifest" else {pointer_path})
                    and all(row[k] == v for k, v in scope.items())):
                raise ValueError("Commit contains changes outside authorized analysis scope")

    def files(records):
        result = {}
        for row in records["artifact_manifest"]:
            name = row["source_path"]
            if name in result or row["source_table"] != "file" or json.loads(row["source_key"]) != name:
                raise ValueError("Invalid committed file identity")
            ref = runtime._decode(json.loads(row["payload"]))
            if (ref["volume_path"] != row["artifact_path"] or ref["sha256"] != row["artifact_sha256"]
                    or ref["byte_size"] != row["byte_size"]):
                raise ValueError("Committed file reference mismatch")
            result[name] = ref
        return result

    old_files, new_files = files(before), files(after)
    def pointer_at(records, references):
        if pointer_path not in references:
            return None
        actual = json.loads(runtime._object(references[pointer_path]))
        settings = [r for r in records["settings"] if r["source_path"] == pointer_path]
        if runtime._json_file("source-analysis.json", settings) != actual:
            raise ValueError("Analysis pointer metadata disagrees with artifact")
        return actual
    old_pointer = pointer_at(before, old_files)
    if inputs.expected_analysis_version != (old_pointer["analysis_version"] if old_pointer else None):
        raise ValueError("Original expected analysis version mismatch")
    pointer = pointer_at(after, new_files)
    expected = {"format": FORMAT, "package_hash": inputs.package_hash,
                "base_understanding_sha256": inputs.expected_understanding_sha256,
                "analysis_version": body.analysis_version, "artifact_sha256": body.artifact_sha256}
    if pointer != expected or new_files[archive_path]["sha256"] != body.artifact_sha256:
        raise ValueError("Committed overlay does not match requested pins")
    compressed = runtime._object(new_files[archive_path])
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        document_raw = stream.read(ds.MAX_JSON + 1)
    if len(document_raw) > ds.MAX_JSON:
        raise ValueError("Analysis document exceeds bound")
    document = _json(document_raw)
    if (_version(document) != body.analysis_version or document.get("analyzer_hashes") != body.analyzer_hashes):
        raise ValueError("Analysis semantic configuration or version mismatch")
    for name in ("manifest.json", "understanding.json.gz", "source-analysis.json", "analysis/" + body.analysis_version + ".json.gz"):
        target = runtime.local / runtime._local_path(prefix + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(runtime._object(new_files[prefix + name]))
    directory = runtime.local / prefix
    saved_manifest = _json((directory / "manifest.json").read_bytes())
    manifest_rows = [r for r in after["packages"] if r["source_path"] == prefix + "manifest.json"]
    if runtime._json_file("manifest.json", manifest_rows) != saved_manifest:
        raise ValueError("Canonical manifest metadata mismatch")
    if (saved_manifest["sha256"] != inputs.package_hash
            or saved_manifest["understanding_sha256"] != inputs.expected_understanding_sha256
            or analysis_pointer(directory) != expected):
        raise ValueError("Canonical input fingerprints mismatch")
    # Actual joined-model verification checks every owning ID/property/provenance.
    index = PackageIndex.load(directory, cache_scope=ds._hash([store.workspace_id, store.user_id]))
    if index.model.get("source_analysis_version") != body.analysis_version:
        raise ValueError("Joined analysis version mismatch")
    canonical = {k: v for k, v in old_files.items() if k != pointer_path}
    if any(new_files.get(k) != v for k, v in canonical.items()):
        raise ValueError("Immutable canonical/source reference changed")
    if runtime._latest() != current or service._read(store, operation) != history:
        raise ValueError("Committed authority or terminal history changed during verification")
    observation = {"observation": "committed_with_ack_failure", "operation_id": operation,
        "request_fingerprint": fingerprint, "request": raw,
        "workspace_id": store.workspace_id, "user_id": store.user_id,
        "project_id": body.project_id, "package_hash": inputs.package_hash,
        "parent_generation": body.parent_generation, "committed_generation": body.committed_generation,
        "sequence": current["sequence"], "analysis_version": body.analysis_version,
        "artifact_sha256": body.artifact_sha256, "base_understanding_sha256": inputs.expected_understanding_sha256,
        "analyzer_hashes": body.analyzer_hashes, "manifest_sha256": current["artifact_sha256"],
        "preserved_inventory_sha256": ds._hash(baseline["inventory"]),
        "canonical_file_refs_sha256": ds._hash(canonical), "terminal_history_sha256": ds._hash(history),
        "observed_at": datetime.now(timezone.utc).isoformat()}
    observation["proof_sha256"] = ds._hash(observation)
    _checked(observation)
    row = _record(store, operation, observation)
    store.upsert_records("jobs", [row], migration_id=row["migration_id"])
    if read_observation(service, store, operation) != observation:
        raise RuntimeError("Reconciliation receipt publication is unconfirmed")
    if runtime._latest() != current:
        raise ValueError("Authority changed after observation publication; inspect pinned receipt")
    return observation