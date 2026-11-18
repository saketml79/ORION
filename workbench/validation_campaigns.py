"""Evidence-only six-pillar campaigns, pinned to exact selected inputs.

Hosted receipts use the existing per-user Delta jobs envelope directly, never
SQLite authority or a local fallback. Local development uses exclusive JSON.
No reference SQL, caller code, model calls, Jobs or business writes are accepted.
"""
from datetime import datetime, timezone
from hashlib import sha256
import base64
import gzip
import io
import json
from pathlib import Path
import re
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from workbench import delta_state as ds
from workbench.package_context import PackageContext
from workbench.package_parity import PARENT_ID, build_parity_candidate
from workbench.source_fixtures import identifier
from workbench.target_fixtures import _checked, catalog_names, target_table
from workbench.validation_data import controlled_fixture_suite
from workbench.validation_readiness import workflow_readiness, blocked_release_gate
from workbench.validation_contracts import SourceContracts, target_assignment_contract, VERSION as CONTRACT_VERSION
from workbench.validation_contracts import pack_source_contract, unpack_source_contract, LEDGER_PRIORITY_IDS
from workbench.validation_contracts import campaign_source_contract, verify_materialized_contract
from workbench.campaign_artifacts import (MAX_REPORT_BYTES, LocalArtifacts,
    validate_reference, read_artifact, artifact_available)


FORMAT = "six-pillar-validation-v1"
PILLARS = ("optimization", "accuracy", "integrity", "security", "databricks_architecture", "data_validation")
MANIFEST_FORMAT = "campaign-artifact-manifest-v1"


class CampaignPublicationError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = dict(receipt)
        super().__init__("Campaign publication unconfirmed; inspect receipt before exact retry")


def report_payload(report):
    """Legacy inline encoder retained for historical compatibility only.

    New publication always uses an immutable artifact, never this Delta body.
    """
    ds._safe(report)
    raw = ds._json(report).encode()
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Campaign report exceeds bounded decoded size")
    payload = report if len(raw) <= ds.MAX_PAYLOAD else {
        "encoding": "gzip-base64-campaign-v1", "decoded_bytes": len(raw),
        "decoded_sha256": sha256(raw).hexdigest(),
        "data": base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")}
    return payload


def decode_report(payload):
    if not isinstance(payload, dict) or len(ds._json(payload).encode()) > ds.MAX_PAYLOAD:
        raise ValueError("Legacy campaign exceeds Delta read bound")
    if payload.get("encoding") != "gzip-base64-campaign-v1":
        ds._safe(payload)
        return payload
    size = payload.get("decoded_bytes")
    if type(size) is not int or not 0 < size <= MAX_REPORT_BYTES:
        raise ValueError("Invalid campaign decoded size")
    packed = base64.b64decode(payload["data"], validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or sha256(raw).hexdigest() != payload["decoded_sha256"]:
        raise ValueError("Campaign compressed evidence mismatch")
    report = json.loads(raw)
    ds._safe(report)
    return report


class CampaignBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent: bool
    workflow_id: str = Field(min_length=1, max_length=512)
    scenario: str = Field(pattern=r"^(original|bronze)$")
    expected_package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_understanding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_analysis_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_fixture_catalog: str = Field(min_length=1, max_length=255)
    target_layers: dict[str, str] = Field(default_factory=dict)


class ProvisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent: bool
    expected_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_catalog_names: dict[str, str]


def input_pins(index):
    manifest = json.loads((index.directory / "manifest.json").read_bytes())
    return {"package_hash": index.model["package"]["sha256"],
            "understanding_sha256": manifest["understanding_sha256"],
            "analysis_version": index.model.get("source_analysis_version")}


def build_campaign(index, body, *, project_id, workspace_id, user_id, user_label, campaign_id=None):
    pins = input_pins(index)
    if pins != {"package_hash": body.expected_package_hash,
                "understanding_sha256": body.expected_understanding_sha256,
                "analysis_version": body.expected_analysis_version}:
        raise ValueError("Selected analysis changed; refresh exact fingerprints")
    if body.consent is not True:
        raise PermissionError("Explicit controlled validation consent required")
    identifier(body.source_fixture_catalog)
    context = PackageContext(index.model)
    if context.nodes.get(body.workflow_id, {}).get("kind") != "workflow":
        raise ValueError("Canonical workflow definition required")
    campaign_id = campaign_id or uuid4().hex
    names = catalog_names(user_label, campaign_id)
    if body.source_fixture_catalog.casefold() in {v.casefold() for v in names.values()}:
        raise ValueError("Existing source fixtures must remain external and unchanged")
    units = {body.workflow_id}
    if body.workflow_id == PARENT_ID:
        parity = build_parity_candidate(index.model, include_evidence=False, context=context)["report"]
        units.update(w["workflow_id"] for w in parity["migration_unit"])
        blockers = [{"code": b["code"], "evidence_ids": b["evidence_ids"]} for b in parity["gate"]["blockers"]]
    else:
        blockers = [{"code": "GENERAL_WORKFLOW_RUNTIME_NOT_IMPLEMENTED", "evidence_ids": [body.workflow_id]}]
    selected_ids = set().union(*(context.local(w) for w in units))
    tasks = [{"id": n["id"], "kind": n["kind"],
              "type": n.get("attributes", {}).get("TASKTYPE", n.get("attributes", {}).get("TYPE", "unknown")),
              "runtime_status": "not_run", "coverage_status": "unimplemented_or_not_verified"}
             for n in (context.nodes[i] for i in sorted(selected_ids))
             if n["kind"] in {"taskinstance", "transformation"}]
    if len(tasks) > 250:
        raise ValueError("Selected campaign exceeds 250 task/transform obligations")
    targets, target_errors = [], []
    for node in (context.nodes[i] for i in sorted(selected_ids)):
        if node["kind"] != "target":
            continue
        try:
            fields = [f["attributes"] for f in node.get("fields", []) if f["kind"] == "targetfield"]
            targets.append({**target_table(node["attributes"], fields, node["scope"]), "target_id": node["id"]})
        except (ValueError, KeyError, TypeError):
            target_errors.append(node["id"])
    _checked(targets)
    if (not set(body.target_layers) <= {t["target_id"] for t in targets}
            or any(layer not in names for layer in body.target_layers.values())):
        raise ValueError("Target layers require exact scoped target IDs and bronze/silver/gold")
    for target in targets:
        target["destination_layer"] = body.target_layers.get(target["target_id"])
        target["destination_catalog"] = names.get(target["destination_layer"])
        target["layer_assignment_status"] = "explicit_test_destination" if target["destination_layer"] else "BLOCKED"
    unassigned = [t["target_id"] for t in targets if not t["destination_layer"]]
    if unassigned:
        blockers.append({"code": "EXPLICIT_TARGET_LAYER_ASSIGNMENT_REQUIRED", "evidence_ids": unassigned})
    if target_errors:
        blockers.append({"code": "TARGET_TYPE_OR_IDENTITY_PLANNING_BLOCKED", "evidence_ids": target_errors})
    blockers.extend([
        {"code": "APPROVED_PHYSICAL_BINDINGS_REQUIRED", "evidence_ids": sorted(units)},
        {"code": "PAIRED_REFERENCE_AND_CANDIDATE_SNAPSHOTS_REQUIRED", "evidence_ids": sorted(units)},
        {"code": "LIVE_OPTIMIZATION_SECURITY_AND_RECOVERY_NOT_RUN", "evidence_ids": sorted(units)}])
    suite = controlled_fixture_suite()
    notebook = suite.pop("notebook")
    candidates = workflow_readiness(context, body.workflow_id)
    for candidate in candidates:
        candidate["source_execution_contract"] = campaign_source_contract(candidate["source_execution_contract"], pins)
    source_contracts = SourceContracts(context)
    candidate_contracts = {c["workflow_id"]: c["source_execution_contract"] for c in candidates}
    selected_contracts = [{"workflow_id": w, "candidate_contract_ref": w,
        "contract_sha256": candidate_contracts[w]["contract_sha256"], "version": CONTRACT_VERSION}
        if w in candidate_contracts else campaign_source_contract(source_contracts.workflow(w), pins) for w in sorted(units)]
    from workbench.workflow_artifacts import supported_workflows
    checks = [
        {"pillar": "optimization", "id": "paired_runtime_profile", "status": "blocked",
         "basis": "Need equivalent outputs plus observed duration, scan/shuffle, cost and source baseline."},
        {"pillar": "accuracy", "id": "controlled_notebook_reference", "status": suite["output_comparison"]["status"],
         "basis": suite["basis"]},
        {"pillar": "accuracy", "id": "business_output_parity", "status": "blocked",
         "basis": "Incomplete load/session implementation and no paired independent business outputs."},
        {"pillar": "integrity", "id": "typed_fixture_inputs", "status": suite["input_validation"]["status"],
         "basis": "Controlled fixture null, duplicate, exact decimal and microsecond timestamp checks only."},
        {"pillar": "integrity", "id": "transaction_replay_rejects", "status": "not_run",
         "basis": "Need source/candidate reject, rollback, restart, concurrency and sequence receipts."},
        {"pillar": "security", "id": "live_least_privilege", "status": "not_run",
         "basis": "Need fresh OBO, warehouse access, catalog ownership, cross-user denial and classification review."},
        {"pillar": "databricks_architecture", "id": "three_catalog_plan", "status": "blocked" if unassigned else "passing",
         "basis": "Exactly three CREATE-only catalogs. Target layer assignments are explicit or blocked, never inferred."},
        {"pillar": "databricks_architecture", "id": "runtime_capabilities", "status": "not_run",
         "basis": "Serverless compatibility, UC lineage, task DAG and approved side effects need runtime evidence."},
        {"pillar": "data_validation", "id": "same_count_wrong_value_control", "status": suite["same_count_negative_control"],
         "basis": "Exact typed multiset differential rejects a wrong value despite equal row counts."},
        {"pillar": "data_validation", "id": "all_business_targets", "status": "blocked",
         "basis": "Every target needs approved keys/types, same input snapshots/date/parameters and independent outputs."}]
    report = {"format": FORMAT, "id": campaign_id, "created_at": datetime.now(timezone.utc).isoformat(),
              "workspace_id": workspace_id, "user_id": user_id, "project_id": project_id,
              "workflow_id": body.workflow_id, "scenario": body.scenario, "pins": pins,
              "bindings": {"source_fixture_catalog": body.source_fixture_catalog, "approved": False},
              "binding_fingerprint": ds._hash({"source_fixture_catalog": body.source_fixture_catalog, "approved": False}),
              "catalog_plan": {"names": names, "provisioned": False, "source_catalog_preserved": True,
                               "destination_layers": ["bronze", "silver", "gold"],
                               "unassigned_target_ids": unassigned,
                               "targets": targets, "target_errors": target_errors,
                               "data_loaded": False, "approval_required_per_action": True},
              "source_coverage": {"workflow_ids": sorted(units), "obligations": tasks,
                                  "unresolved_reference_ids": sorted(i for i in selected_ids if context.nodes[i]["kind"] == "unresolved_reference"),
                                  "scope_sha256": ds._hash(sorted(selected_ids)), "coverage_is_runtime_parity": False},
              "checks": checks, "pillars": {p: "blocked" if any(c["status"] == "blocked" for c in checks if c["pillar"] == p)
                  else "not_run" if any(c["status"] == "not_run" for c in checks if c["pillar"] == p)
                  else "failed" if any(c["status"] == "failed" for c in checks if c["pillar"] == p)
                  else "passing" for p in PILLARS},
              "controlled_fixture": suite, "controlled_notebook_sha256": ds._hash(notebook),
              "workflow_candidates": candidates,
              "selection_contract": {"version": CONTRACT_VERSION,
                  "criteria": "canonical_session_mapping_wired_business_target_with_field_schema",
                  "ordering": "selected_then_outward_then_ledger_canonical_id_priority_then_exact_name_then_id",
                  "ledger_priority_ids": list(LEDGER_PRIORITY_IDS), "priority_is_approval": False,
                  "limit": 10, "not_execution_order": True,
                  "cohort_classifications": source_contracts.cohort_inventory(body.workflow_id),
                  "selected_definition_contracts": selected_contracts,
                  "wrappers_qualify": False, "business_intent_approved": False},
              "pinned_wrapper_support": {"members": supported_workflows(),
                  "basis": "existing_four_scope_pins_unchanged_separate_from_business_shortlist",
                  "business_workflows_verified": 0, "qualifies_for_release_gate": False},
              "target_assignment_contract": target_assignment_contract(targets, context),
              "release_gate": blocked_release_gate(),
              "semantic_fingerprints": {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                  for name in ("validation_data.py", "validation_campaigns.py", "target_fixtures.py", "validation_provisioning.py", "validation_readiness.py", "validation_contracts.py")},
              "status": "blocked", "blockers": blockers, "business_parity": "not_established",
              "execution_allowed": False, "deployment_allowed": False, "production_ready": False,
              "remote_jobs_run": False, "model_calls": 0, "pii_rows_logged": False,
              "source_to_layer_mapping_status": "BLOCKED",
              "limitations": ["No source SQL execution or caller-provided code. Reference datasets must be acquired separately read-only.",
                              "XML widths, Oracle empty-string/collation, constraints, clock and transactions require explicit contracts.",
                              "Passing controlled fixtures is not business proof. No all-six guarantee."]}
    report["report_sha256"] = ds._hash(report)
    ds._safe(report)
    if len(ds._json(report).encode()) > MAX_REPORT_BYTES:
        raise ValueError("Campaign report exceeds bounded artifact size")
    return report


class CampaignStore:
    def __init__(self, *, local=None, delta=None, artifacts=None):
        if (local is None) == (delta is None):
            raise ValueError("Exactly one campaign authority required")
        self.local, self.delta = Path(local) if local is not None else None, delta
        self.artifacts = artifacts or (ds.VolumeArtifacts(delta) if delta is not None
                                      else LocalArtifacts(self.local / "artifacts"))
        if delta is not None and (not isinstance(self.artifacts, ds.VolumeArtifacts)
                                  or self.artifacts.store is not delta):
            raise PermissionError("Hosted campaigns require the same user Delta/Volume authority")
        self.last_receipt = None

    @staticmethod
    def _scope(project, package, campaign):
        if not re.fullmatch(r"[a-f0-9]{32}", campaign):
            raise ValueError("Invalid campaign ID")
        return {"project_id": project, "package_hash": package, "context_id": "validation:" + campaign}

    def _record(self, report, payload=None):
        artifact_keys = {}
        if payload is not None and payload.get("format") == MANIFEST_FORMAT:
            reference = payload["artifact"]
            artifact_keys = {"artifact_path": reference["volume_path"], "artifact_sha256": reference["sha256"],
                             "byte_size": reference["byte_size"], "version": 1}
        return self.delta.make_record("jobs", **self._scope(report["project_id"], report["pins"]["package_hash"], report["id"]),
            source_path="validation-campaigns", source_table=FORMAT, source_key=report["id"],
            payload=report_payload(report) if payload is None else payload, status="blocked", kind=FORMAT, workflow_id=report["workflow_id"],
            created_at=report["created_at"], sequence=1, **artifact_keys)

    @staticmethod
    def _validate_report(report, project, package, campaign):
        if (report["id"] != campaign or report["project_id"] != project or report["pins"]["package_hash"] != package
                or report["format"] != FORMAT or report["report_sha256"] != ds._hash({k: v for k, v in report.items() if k != "report_sha256"})):
            raise ValueError("Campaign fingerprint/scope mismatch")
        ds._safe(report)

    def _manifest(self, report, reference):
        from workbench.validation_metrics import campaign_metrics
        ids = {c["workflow_id"] for c in report.get("workflow_candidates", [])}
        summary = {k: report[k] for k in ("format", "id", "created_at", "workspace_id", "user_id",
            "project_id", "workflow_id", "pins", "report_sha256")}
        for key in ("scenario", "status", "checks", "pillars", "controlled_fixture", "semantic_fingerprints"):
            if key in report:
                summary[key] = report[key]
        summary["selection_contract"] = {"version": report.get("selection_contract", {}).get("version")}
        summary["blockers"] = [{"code": b["code"], "evidence_count": len(b.get("evidence_ids", []))}
                               for b in report.get("blockers", [])]
        summary["verified_manifest_summary"] = True
        summary["campaign_metrics"] = campaign_metrics([report], ids, complete=True)
        summary["summary_sha256"] = ds._hash(summary)
        manifest = {"format": MANIFEST_FORMAT, "revision": report["report_sha256"],
            "input_version": ds._hash(report["pins"]),
            "artifact": reference, "summary": summary, "upload_verified": True,
            "visibility": "manifest_published_after_artifact_readback"}
        manifest["manifest_sha256"] = ds._hash(manifest)
        return manifest

    def _validate_manifest(self, payload, project, package, campaign, row=None):
        if (payload.get("format") != MANIFEST_FORMAT or payload.get("upload_verified") is not True
                or payload.get("visibility") != "manifest_published_after_artifact_readback"
                or payload.get("manifest_sha256") != ds._hash({k: v for k, v in payload.items() if k != "manifest_sha256"})):
            raise ValueError("Campaign manifest envelope mismatch")
        summary = payload["summary"]
        if (summary.get("summary_sha256") != ds._hash({k: v for k, v in summary.items() if k != "summary_sha256"})
                or summary.get("verified_manifest_summary") is not True
                or summary["id"] != campaign or summary["project_id"] != project
                or summary["pins"]["package_hash"] != package or summary["format"] != FORMAT
                or payload.get("input_version") != ds._hash(summary["pins"])
                or payload["revision"] != summary["report_sha256"]):
            raise ValueError("Campaign summary fingerprint/scope mismatch")
        if self.delta is not None:
            if summary["workspace_id"] != self.delta.workspace_id or summary["user_id"] != self.delta.user_id:
                raise PermissionError("Campaign manifest owner mismatch")
            if row != dict(self._record(summary, payload), migration_id="validation:" + campaign):
                raise ValueError("Campaign manifest envelope mismatch")
        ds._safe(payload)
        validate_reference(self.artifacts, payload["artifact"])
        return summary

    def _payload(self, project, package, campaign):
        scope = self._scope(project, package, campaign)
        if not re.fullmatch(r"[a-f0-9]{64}", package):
            raise ValueError("Invalid campaign package")
        if self.delta is not None:
            rows = self.delta.read_table("jobs", **scope, migration_id="validation:" + campaign, limit=2)
            if not rows:
                return None, None
            if len(rows) != 1:
                raise ValueError("Ambiguous immutable campaign")
            row = rows[0]
            if len(row["payload"].encode()) > ds.MAX_PAYLOAD:
                raise ValueError("Campaign manifest exceeds Delta bound")
            return json.loads(row["payload"]), row
        path = self.local / package / (campaign + ".json")
        if not path.exists():
            return None, None
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > ds.MAX_PAYLOAD:
            # Historical local inline reports have the separate decoded bound.
            if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > MAX_REPORT_BYTES:
                raise ValueError("Campaign file outside read bound")
        return json.loads(path.read_bytes()), None

    def save(self, report):
        self._scope(report["project_id"], report["pins"]["package_hash"], report["id"])
        if self.delta is not None:
            if report["workspace_id"] != self.delta.workspace_id or report["user_id"] != self.delta.user_id:
                raise PermissionError("Campaign owner mismatch")
        self._validate_report(report, report["project_id"], report["pins"]["package_hash"], report["id"])
        existing = self.read(report["project_id"], report["pins"]["package_hash"], report["id"])
        if existing is not None:
            if existing != report:
                raise ValueError("Conflicting immutable campaign revision; use a new campaign ID")
            self.last_receipt = {"status": "published_exact_retry", "campaign_id": report["id"],
                                 "report_sha256": report["report_sha256"]}
            payload, row = self._payload(report["project_id"], report["pins"]["package_hash"], report["id"])
            if payload.get("format") == MANIFEST_FORMAT:
                self.last_receipt.update(artifact=payload["artifact"], manifest_sha256=payload["manifest_sha256"],
                    orphan_possible=False, automatic_delete_allowed=False,
                    serialized_record_bytes=len(ds._json(row if row is not None else payload).encode()))
            return self.last_receipt
        raw = ds._json(report).encode()
        if not 0 < len(raw) <= MAX_REPORT_BYTES:
            raise ValueError("Campaign report exceeds bounded artifact size")
        digest = sha256(raw).hexdigest()
        reference = {"volume_path": self.artifacts._path(digest), "sha256": digest, "byte_size": len(raw)}
        # Verify owner path and actual serialized envelope BEFORE any upload.
        validate_reference(self.artifacts, reference)
        manifest = self._manifest(report, reference)
        record = self._record(report, manifest) if self.delta is not None else None
        envelope_size = len(ds._json(dict(record, migration_id="validation:" + report["id"]) if record else manifest).encode())
        if envelope_size >= ds.MAX_PAYLOAD:
            raise ValueError("Campaign manifest including envelope exceeds 48 KiB")
        self.last_receipt = {"status": "artifact_upload_unconfirmed", "campaign_id": report["id"],
            "artifact": reference, "orphan_possible": True, "automatic_delete_allowed": False}
        try:
            if self.artifacts.put(raw) != reference or read_artifact(self.artifacts, reference) != raw:
                raise ValueError("Campaign upload readback failed")
        except Exception:
            raise CampaignPublicationError(self.last_receipt) from None
        self.last_receipt["status"] = "artifact_verified_manifest_unpublished"
        try:
            if self.delta is not None:
                self.delta.upsert_records("jobs", [record], migration_id="validation:" + report["id"])
            else:
                directory = self.local / report["pins"]["package_hash"]
                directory.mkdir(parents=True, exist_ok=True)
                import os
                import tempfile
                # Publish only a complete file, without replacing an existing ID.
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
                    staged = Path(stream.name)
                    try:
                        stream.write(ds._json(manifest))
                        stream.flush()
                        os.fsync(stream.fileno())
                    except BaseException:
                        staged.unlink(missing_ok=True)
                        raise
                try:
                    os.link(staged, directory / (report["id"] + ".json"))
                finally:
                    staged.unlink(missing_ok=True)
            observed, row = self._payload(report["project_id"], report["pins"]["package_hash"], report["id"])
            if observed != manifest:
                raise RuntimeError("Campaign manifest durable readback failed")
            self._validate_manifest(observed, report["project_id"], report["pins"]["package_hash"], report["id"], row)
        except Exception:
            self.last_receipt["status"] = "manifest_publish_unconfirmed"
            raise CampaignPublicationError(self.last_receipt) from None
        self.last_receipt = {**self.last_receipt, "status": "published", "orphan_possible": False,
            "manifest_sha256": manifest["manifest_sha256"], "serialized_record_bytes": envelope_size}
        return self.last_receipt

    def read(self, project, package, campaign):
        payload, row = self._payload(project, package, campaign)
        if payload is None:
            return None
        if payload.get("format") == MANIFEST_FORMAT:
            summary = self._validate_manifest(payload, project, package, campaign, row)
            report = json.loads(read_artifact(self.artifacts, payload["artifact"]))
            # Historical summaries are not recomputed with a newer classifier.
            if any(report.get(k) != summary[k] for k in ("report_sha256", "pins", "workspace_id", "user_id")):
                raise ValueError("Campaign artifact/manifest mismatch")
        else:
            report = decode_report(payload) if self.delta is not None else payload
            if row is not None and row != dict(self._record(report, payload), migration_id="validation:" + campaign):
                raise ValueError("Campaign envelope mismatch")
        self._validate_report(report, project, package, campaign)
        return report

    def summary(self, project, package, campaign, *, payload=None, row=None):
        if payload is None:
            payload, row = self._payload(project, package, campaign)
        if payload is None:
            return None
        if payload.get("format") != MANIFEST_FORMAT:
            return self.read(project, package, campaign)
        summary = self._validate_manifest(payload, project, package, campaign, row)
        available = artifact_available(self.artifacts, payload["artifact"])
        result = {**summary, "artifact_availability": "available_metadata_only" if available else "unavailable",
            "artifact_content_verified_this_read": False}
        result["summary_sha256"] = ds._hash({k: v for k, v in result.items() if k != "summary_sha256"})
        return result

    def page(self, project, package, after=None):
        """Discover immutable campaign receipts with bounded, owner-scoped reads."""
        if self.delta is None:
            raise ValueError("Campaign discovery requires hosted Delta authority")
        store = self.delta
        username = store._authorize()
        store._verify_table("jobs", username)
        values = {"workspace_id": store.workspace_id, "user_id": store.user_id,
                  "project_id": project, "package_hash": package, "source_table": FORMAT}
        where = " AND ".join(k + "=:" + k for k in values)
        if after is not None:
            values.update(cursor_id=after[0], cursor_hash=after[1], cursor_migration=after[2])
            where += " AND (record_id,content_hash,migration_id) > (:cursor_id,:cursor_hash,:cursor_migration)"
        rows = store._execute("SELECT " + ",".join(ds.COLUMNS) + " FROM " + store._name("jobs")
            + " WHERE " + where + " ORDER BY record_id,content_hash,migration_id LIMIT 50", values)
        reports, cursor = [], after
        for cells in rows:
            row = {k: int(v) if ds.COLUMNS[k] == "BIGINT" else v for k, v in zip(ds.COLUMNS, cells, strict=True)}
            if len(row["payload"].encode()) > ds.MAX_PAYLOAD:
                raise ValueError("Campaign discovery exceeds Delta bound")
            payload = json.loads(row["payload"])
            if payload.get("format") == MANIFEST_FORMAT:
                report = self.summary(project, package, row["source_key"], payload=payload, row=row)
            else:
                report = decode_report(payload)
            position = row["record_id"], row["content_hash"], row["migration_id"]
            if (report.get("workspace_id") != store.workspace_id or report.get("user_id") != store.user_id
                    or report.get("project_id") != project or report.get("pins", {}).get("package_hash") != package
                    or row != dict(self._record(report, payload), migration_id="validation:" + report["id"])
                    or cursor is not None and position <= cursor):
                raise ValueError("Campaign discovery envelope mismatch")
            if payload.get("format") != MANIFEST_FORMAT:
                self._validate_report(report, project, package, report["id"])
            cursor = position
            reports.append(report)
        return reports, cursor, len(rows) == 50

    def provisioning_events(self, report):
        if self.delta is None:
            raise PermissionError("Provisioning requires hosted Delta authority")
        scope = self._scope(report["project_id"], report["pins"]["package_hash"], report["id"])
        scope["context_id"] += ":provision"
        rows = self.delta.read_table("jobs", **scope, migration_id=scope["context_id"], limit=3)
        events = []
        for row in rows:
            event = json.loads(row["payload"])
            expected = self.delta.make_record("jobs", **scope, source_path="validation-provisioning",
                source_table=FORMAT, source_key=event["phase"], payload=event,
                created_at=event["created_at"], status=event.get("status", "started"), kind=FORMAT)
            if (row != dict(expected, migration_id=scope["context_id"])
                    or event["campaign_id"] != report["id"] or event["report_sha256"] != report["report_sha256"]):
                raise ValueError("Invalid provisioning evidence envelope")
            events.append(event)
        if len(events) > 2 or len({e["phase"] for e in events}) != len(events):
            raise ValueError("Ambiguous provisioning attempt")
        return sorted(events, key=lambda e: e["created_at"])

    def provisioning_journal(self, report, event):
        if event["phase"] in {e["phase"] for e in self.provisioning_events(report)}:
            raise ValueError("Immutable provisioning phase already exists")
        scope = self._scope(report["project_id"], report["pins"]["package_hash"], report["id"])
        scope["context_id"] += ":provision"
        record = self.delta.make_record("jobs", **scope, source_path="validation-provisioning",
            source_table=FORMAT, source_key=event["phase"], payload=event,
            created_at=event["created_at"], status=event.get("status", "started"), kind=FORMAT)
        self.delta.upsert_records("jobs", [record], migration_id=scope["context_id"])
        if event not in self.provisioning_events(report):
            raise RuntimeError("Provisioning phase durable readback failed")


def install_validation(app):
    service = app.state.service
    # Hosted factory injects a fresh request-scoped Delta authority instead.
    app.state.validation_store_provider = lambda: CampaignStore(local=service.state / "validation-campaigns")

    def context(request):
        index = service.require()
        project = request.headers.get("X-Workbench-Project") or "__legacy__"
        if getattr(app.state, "hosted_request_authenticated", False):
            from workbench.hosted import current_identity
            identity = current_identity()
            if identity.mode != "obo" or project == "__legacy__":
                raise PermissionError("Hosted validation requires selected project and fresh OBO")
            store = app.state.validation_store_provider()
            if store.delta is None:
                raise PermissionError("Hosted validation has no Delta authority; no local fallback")
            return index, project, store, store.delta.workspace_id, identity.user_id, identity.user_id
        return index, project, app.state.validation_store_provider(), "local", "local-reviewer", "local-reviewer"

    @app.get("/api/validation/context")
    def current_context(request: Request):
        try:
            index, project, _, _, _, _ = context(request)
            return {"project_id": project, "pins": input_pins(index), "source_fixture_catalog": None,
                    "hosted_obo": getattr(app.state, "hosted_request_authenticated", False),
                    "source_catalog_requires_explicit_selection": True, "execution_allowed": False}
        except (ValueError, PermissionError):
            raise HTTPException(409, "Validation context unavailable; select a verified project/package") from None

    @app.post("/api/validation/campaigns", status_code=201)
    def create_campaign(body: CampaignBody, request: Request, response: Response):
        try:
            index, project, store, workspace, user, label = context(request)
            report = build_campaign(index, body, project_id=project, workspace_id=workspace, user_id=user, user_label=label)
            receipt = store.save(report)
            response.headers["X-Workbench-Campaign-Publication"] = receipt["status"]
            response.headers["X-Workbench-Campaign-Artifact-SHA256"] = receipt["artifact"]["sha256"]
            response.headers["X-Workbench-Campaign-Artifact-Bytes"] = str(receipt["artifact"]["byte_size"])
            response.headers["X-Workbench-Campaign-Manifest-SHA256"] = receipt["manifest_sha256"]
            return report
        except CampaignPublicationError as error:
            raise HTTPException(409, {"reason": "Campaign manifest publication unconfirmed; no automatic retry or deletion",
                                      "receipt": error.receipt}) from None
        except PermissionError:
            raise HTTPException(403, "Validation consent or user authority unavailable") from None
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            raise HTTPException(409, "Validation pins, target types or scoped evidence invalid; refresh before retry") from None

    @app.get("/api/validation/campaigns/{campaign_id}")
    @app.get("/api/validation/campaigns/{campaign_id}/export")
    def read_campaign(campaign_id: str, request: Request):
        try:
            index, project, store, _, _, _ = context(request)
            report = store.read(project, index.model["package"]["sha256"], campaign_id)
            if report is None:
                raise HTTPException(404, "Campaign not found in this user/project/package")
            if request.url.path.endswith("/export"):
                return Response(ds._json(report), media_type="application/json",
                                headers={"Content-Disposition": 'attachment; filename="validation-report.json"',
                                    "X-Workbench-Campaign-Artifact-SHA256": ds._hash(report),
                                    "X-Workbench-Campaign-Artifact-Bytes": str(len(ds._json(report).encode())),
                                    "X-Workbench-Campaign-Current-Input-Pins-Match": str(report["pins"] == input_pins(index)).lower()})
            return {"report": report, "current_input_pins_match": report["pins"] == input_pins(index),
                    "provisioning_events": store.provisioning_events(report) if store.delta is not None else [],
                    "historical_receipt_immutable": True, "execution_allowed": False}
        except (ValueError, KeyError, PermissionError, OSError, RuntimeError):
            raise HTTPException(409, "Campaign authority, immutable artifact or fingerprints unavailable; no fallback used") from None

    @app.post("/api/validation/campaigns/{campaign_id}/provision")
    def provision_campaign(campaign_id: str, body: ProvisionBody, request: Request):
        try:
            index, project, store, _, _, _ = context(request)
            if store.delta is None:
                raise PermissionError("Remote provisioning is hosted OBO only")
            report = store.read(project, index.model["package"]["sha256"], campaign_id)
            if (report is None or report["pins"] != input_pins(index)
                    or store.provisioning_events(report)):
                raise ValueError("Missing, stale or previously attempted immutable campaign")
            from workbench.validation_provisioning import provision_test_catalogs
            return provision_test_catalogs(store.delta, report, consent=body.consent,
                confirmed_names=body.confirmed_catalog_names, expected_report_hash=body.expected_report_sha256,
                journal=lambda event: store.provisioning_journal(report, event))
        except PermissionError:
            raise HTTPException(403, "Separate provisioning consent and fresh hosted user authority required") from None
        except (ValueError, KeyError):
            raise HTTPException(409, "Provisioning plan stale, incomplete, existing or previously attempted; no automatic retry") from None


def export_local_evidence(xml, output, *, workflow_id, source_fixture_catalog, compare_campaign=None):
    """Exclusive offline proof export, not an active hosted campaign or deploy.

    Fingerprint the fresh canonical model, never placeholder manifest pins.
    Catalog names are provisional local names, not live user-owned resources.
    """
    import tempfile
    from workbench.app import PackageIndex
    from workbench.understanding import analyze_package
    from workbench.wil_parent_child import discover_external_evidence

    output = Path(output)
    if output.exists():
        raise FileExistsError("Proof directory already exists; no overwrites")
    model = analyze_package(Path(xml))
    understanding_hash = sha256(ds._json(model).encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="orion-validation-pins-") as temporary:
        directory = Path(temporary)
        (directory / "manifest.json").write_text(ds._json({"understanding_sha256": understanding_hash}), encoding="utf-8")
        index = PackageIndex(model, directory)
        pins = input_pins(index)
        body = CampaignBody(consent=True, workflow_id=workflow_id, scenario="bronze",
            expected_package_hash=pins["package_hash"], expected_understanding_sha256=understanding_hash,
            source_fixture_catalog=source_fixture_catalog)
        report = build_campaign(index, body, project_id="__offline_validation__", workspace_id="local",
            user_id="local-reviewer", user_label="local-reviewer")
    external = discover_external_evidence(Path(xml).resolve().parents[1])
    observations = {"format": "offline-validation-proof-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED", "source_package_sha256": pins["package_hash"],
        "understanding_sha256": understanding_hash, "pin_basis": "fresh_local_canonical_json_not_hosted_activation",
        "external_evidence": external, "release_gate": report["release_gate"],
        "catalog_names_live": None, "catalogs_created": 0, "schemas_created": 0, "jobs_run": [],
        "source_catalog_modified": False, "deployment_performed": False,
        "catalog_plan_basis": "provisional_local_identity_only_rebuild_under_fresh_hosted_OBO_before_creation",
        "requirements": ["Fresh labs OAuth and hosted OBO authorization",
            "Exact external procedure bodies and dependent key allocation contracts",
            "Scoped parameter sections and approved source/target column/layer/type/clock bindings",
            "Complete business session writes, control and recovery implementation",
            "Independent nonempty paired original/candidate output snapshots and bounded runtime receipts"],
        "existing_v2_candidate": "output/workbench/wil-net-dis-parent-child-source-v2/implementation_report.json"}
    comparison = None
    if compare_campaign is not None:
        previous_bytes = Path(compare_campaign).read_bytes()
        previous = json.loads(previous_bytes)
        if (previous.get("pins", {}).get("package_hash") != pins["package_hash"]
                or previous.get("workflow_id") != workflow_id
                or previous.get("report_sha256") != ds._hash({k: v for k, v in previous.items() if k != "report_sha256"})):
            raise ValueError("Previous campaign integrity or selected package/workflow mismatch")
        inventory = {r["workflow_id"]: r for r in report["selection_contract"]["cohort_classifications"]}
        classified = [{"workflow_id": r["workflow_id"], "name": r["name"],
            "current_classification": inventory.get(r["workflow_id"], {}).get("classification", "outside_current_cohort")}
            for r in previous["workflow_candidates"]]
        comparison = {"previous_campaign_sha256": sha256(previous_bytes).hexdigest(),
            "previous_rows": classified,
            "previous_business_definitions": sum(r["current_classification"] == "business_definition_candidate" for r in classified),
            "previous_nonbusiness_definitions": sum(r["current_classification"] != "business_definition_candidate" for r in classified),
            "basis": "Canonical source role reclassification of old shortlist, NOT original/candidate business output comparison",
            "previous_export_preserved": True, "output_parity": False}
    shortlist = {"version": CONTRACT_VERSION, "pins": pins, "report_sha256": report["report_sha256"],
        "workflow_id": workflow_id, "selection_contract": report["selection_contract"],
        "workflows": [{k: r[k] for k in ("workflow_id", "name", "status", "runtime_status", "business_outputs_verified")}
                      for r in report["workflow_candidates"]], "comparison": comparison,
        "pinned_wrapper_support": report["pinned_wrapper_support"], "output_parity": False,
        "deployment_allowed": False}
    materializer = SourceContracts(PackageContext(model))
    materialized = [verify_materialized_contract(r["source_execution_contract"], materializer.workflow(r["workflow_id"]), pins)
                    for r in report["workflow_candidates"]]
    contents = {"campaign.json": ds._json(report), "proof.json": ds._json(observations),
        "shortlist.json": ds._json(shortlist),
        "source-execution-contracts.json": ds._json({"pins": pins, "report_sha256": report["report_sha256"],
            "contracts": materialized, "output_parity": False}),
        "target-assignments.json": ds._json({"pins": pins, "report_sha256": report["report_sha256"],
            "contract": report["target_assignment_contract"]})}
    output.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for name, text in contents.items():
        data = text.encode()
        with (output / name).open("xb") as stream:
            stream.write(data)
        manifest[name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
    with (output / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(ds._json(manifest))
    for name, expected in manifest.items():
        data = (output / name).read_bytes()
        if len(data) != expected["bytes"] or sha256(data).hexdigest() != expected["sha256"]:
            raise RuntimeError("Offline proof readback failed")
    return {"directory": str(output), "manifest_verified": True,
            "workflows": [{"name": c["name"], "status": c["status"]} for c in report["workflow_candidates"]],
            "targets": len(report["catalog_plan"]["targets"]), "deployment_allowed": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Exclusive offline validation proof; no remote actions")
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--source-fixture-catalog", required=True)
    parser.add_argument("--compare-campaign", type=Path)
    args = parser.parse_args()
    print(ds._json(export_local_evidence(args.xml, args.output,
        workflow_id=args.workflow_id, source_fixture_catalog=args.source_fixture_catalog, compare_campaign=args.compare_campaign)))