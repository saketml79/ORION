"""Internal, read-only acceptance instrument. Not a route or certificate issuer.

Only a server-installed Provider may supply authority. Never construct one from
HTTP JSON, model output, signed client attestations or an offline export folder.
No SDK constructor, SQL, execution, deployment or default network access exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol, Sequence

import networkx as nx

from workbench.package_context import PackageContext
from workbench.validation_contracts import LEDGER_PRIORITY_IDS, SourceContracts
from workbench.validation_data import compare_rows


VERSION = "conversion-acceptance-v1"
MAX_BYTES = 16 * 1024 * 1024
CASE_KINDS = frozenset({"normal", "nulls", "duplicates", "dates", "failure_recovery"})
STAGES = frozenset({"source", "design", "generation", "deployment", "execution", "comparison"})


class EvidenceError(ValueError):
    """Safe, bounded failure code, never a provider exception or business row."""


def _require(condition: Any, code: str) -> None:
    if not condition:
        raise EvidenceError(code)


def _hash(value: Any) -> str:
    digest = sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False,
                                  separators=(",", ":")).iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _hashpins(pins: Mapping[str, Any]) -> str:
    """Hash the ENTIRE input identity, not a package-only/selected-field alias."""
    _require(set(pins) >= {"package_hash", "understanding_sha256", "analysis_version",
                          "input_hashes", "input_versions"}, "source_pins_incomplete")
    for key in ("package_hash", "understanding_sha256"):
        _require(isinstance(pins[key], str) and re.fullmatch(r"[a-f0-9]{64}", pins[key]),
                 "source_pin_invalid")
    _require(pins["analysis_version"] is None or
             isinstance(pins["analysis_version"], str) and
             re.fullmatch(r"[a-f0-9]{64}", pins["analysis_version"]), "analysis_pin_invalid")
    _require(bool(pins["input_hashes"]) and bool(pins["input_versions"]), "source_pins_incomplete")
    return _hash(dict(pins))


def _obligations(context: PackageContext, contracts: SourceContracts, wid: str) -> list[str]:
    contract = contracts.workflow(wid)
    ids = {"task:" + t["task_instance_id"] for t in contract["tasks"]}
    ids.update("control:" + e["id"] for e in contract["control_links"])
    ids.update("target:" + i for i in context.local(wid) if context.nodes[i]["kind"] == "target")
    for i in context.local(wid):
        node = context.nodes[i]
        ids.add("node:" + i)
        ids.update("property:" + _hash([i, n, p]) for n, p in enumerate(node.get("properties", [])))
    return sorted(ids)


def build_plan(model: dict, workflow_ids: Sequence[str], cohort_seeds: Sequence[str]) -> dict:
    """Materialize exactly the requested ten and two real connected components.

    Cohorts include ALL members, not just the ten selected business definitions.
    This function is observational, has no file writes and does not parse XML.
    """
    _require(len(workflow_ids) == 10 and set(workflow_ids) == set(LEDGER_PRIORITY_IDS),
             "exact_requested_ten_required")
    _require(len(cohort_seeds) == 2, "exactly_two_cohorts_required")
    context = PackageContext(model)
    contracts = SourceContracts(context)
    for wid in [*workflow_ids, *cohort_seeds]:
        _require(context.nodes.get(wid, {}).get("kind") == "workflow", "canonical_workflow_required")
    for wid in workflow_ids:
        _require(contracts.workflow(wid)["classification"] == "business_definition_candidate",
                 "business_definition_required")
    groups = []
    for seed in cohort_seeds:
        members = sorted(nx.node_connected_component(context.graph.to_undirected(as_view=True), seed))
        _require(len(members) > 1, "connected_cohort_required")
        calls = sorted((c for c in context.invocations if c["caller_workflow_id"] in members or
                set(c["candidate_workflow_ids"]) & set(members)), key=_hash)
        _require(bool(calls) and all(c["caller_workflow_id"] in members and len(c["candidate_workflow_ids"]) == 1 and
                 c["candidate_workflow_ids"][0] in members and
                 c["resolution"] == "scoped_literal_candidate" for c in calls),
                 "cohort_invocations_incomplete")
        membership_hash = _hash(members)
        groups.append({"key": "cohort:" + membership_hash, "seed": seed, "members": members,
                       "membership_sha256": membership_hash, "invocations_sha256": _hash(calls),
                       "invocation_count": len(calls),
                       "invocation_sites": sorted(c["command_owner_id"] for c in calls)})
    _require(groups[0]["members"] != groups[1]["members"], "duplicate_connected_component")
    _require(not set(groups[0]["members"]) & set(groups[1]["members"]), "overlapping_components")
    all_ids = sorted(set(workflow_ids).union(*(set(g["members"]) for g in groups)))
    units = {wid: {"workflow_id": wid, "name": context.nodes[wid]["name"],
                   "scope": context.nodes[wid].get("scope", {}),
                   "source_contract_sha256": contracts.workflow(wid)["contract_sha256"],
                   "business_target_ids": contracts.workflow(wid)["business_target_ids"],
                   "obligations": _obligations(context, contracts, wid)} for wid in all_ids}
    items = [{"key": "workflow:" + wid, "members": [wid]} for wid in workflow_ids] + groups
    plan = {"version": VERSION, "package_hash": model["package"]["sha256"],
            "model_sha256": _hash(model), "workflow_ids": list(workflow_ids),
            "cohort_seeds": list(cohort_seeds), "units": units, "items": items}
    plan["plan_sha256"] = _hash(plan)
    return plan


@dataclass(frozen=True)
class Owner:
    workspace_id: str
    user_id: str
    project_id: str
    package_hash: str


@dataclass(frozen=True)
class ValidationPolicy:
    """Host policy, not model-selected catalogs. Does not authorize writes."""
    allowed_catalogs: tuple[str, ...]
    schema_prefix: str


@dataclass(frozen=True)
class ArtifactRef:
    key: str
    sha256: str
    size: int


@dataclass(frozen=True)
class CommittedInventory:
    """Produced ONLY after the host verifies owner, commit receipt and manifest.

    `records` maps (family, id) to immutable artifact references. The artifact
    loader must resolve only these owner-bound references, with no path fallback.
    The gate checks bytes again, but cannot authenticate an adapter's honesty.
    """
    owner: Owner
    generation: str
    manifest_sha256: str
    records: Mapping[tuple[str, str], ArtifactRef]


class JobsReadOnly(Protocol):
    def get_run(self, *, run_id: int) -> Any: ...
    def get(self, *, job_id: int) -> Any: ...


class Provider(Protocol):
    """Trusted server dependency, NOT a public request-body type.

    Methods must freshly authorize the caller's supplied remote SDK identity.
    No cached identity, SP fallback, SQL, run_now, repair, submit, deploy or write.
    """
    jobs: JobsReadOnly

    def read_committed(self, owner: Owner) -> CommittedInventory: ...
    def load_artifact(self, owner: Owner, reference: ArtifactRef) -> bytes: ...
    def current_identity(self) -> Owner: ...
    def current_source_pins(self, owner: Owner) -> Mapping[str, Any]: ...
    def current_validation_policy(self, owner: Owner) -> ValidationPolicy: ...
    def export_deployed_source(self, owner: Owner, workspace_path: str) -> bytes: ...


def _dictionary(value: Any) -> dict:
    value = value.as_dict() if hasattr(value, "as_dict") else value
    _require(isinstance(value, dict), "remote_shape_invalid")
    return value


class _Reader:
    def __init__(self, provider: Provider, owner: Owner, inventory: CommittedInventory):
        _require(isinstance(inventory, CommittedInventory) and inventory.owner == owner and
                 bool(inventory.generation) and re.fullmatch(r"[a-f0-9]{64}", inventory.manifest_sha256),
                 "committed_owner_manifest_required")
        self.provider, self.owner, self.inventory = provider, owner, inventory

    def raw(self, family: str, identifier: str) -> bytes:
        ref = self.inventory.records.get((family, identifier))
        _require(isinstance(ref, ArtifactRef) and 0 < ref.size <= MAX_BYTES, "artifact_not_in_manifest")
        raw = self.provider.load_artifact(self.owner, ref)
        _require(isinstance(raw, bytes) and len(raw) == ref.size and
                 sha256(raw).hexdigest() == ref.sha256, "artifact_bytes_mismatch")
        return raw

    def record(self, family: str, identifier: str) -> dict:
        value = json.loads(self.raw(family, identifier))
        _require(isinstance(value, dict), "stored_record_shape_invalid")
        return value


def _run(provider: Provider, owner: Owner, deployment: dict) -> None:
    """Fresh SDK reads AND exact workspace export, never tags as code proof."""
    job_id, run_id = deployment["job_id"], deployment["run_id"]
    _require(type(job_id) is int and job_id > 0 and type(run_id) is int and run_id > 0,
             "job_identity_invalid")
    run = _dictionary(provider.jobs.get_run(run_id=run_id))
    job = _dictionary(provider.jobs.get(job_id=job_id))
    _require(run.get("run_id") == run_id and run.get("job_id") == job_id and job.get("job_id") == job_id,
             "remote_run_join_mismatch")
    # Host stores exact SDK user_name as execution_principal after fresh identity
    # resolution. It must not infer creator/run-as from a model-supplied label.
    principal = deployment["execution_principal"]
    _require(principal == owner.user_id and run.get("creator_user_name") == principal and
             job.get("creator_user_name") == principal and run.get("run_as_user_name") == principal,
             "remote_run_owner_mismatch")
    _require(run.get("state", {}).get("life_cycle_state") == "TERMINATED" and
             run.get("state", {}).get("result_state") == "SUCCESS", "remote_run_not_successful")
    _require(_hash(job["settings"]) == deployment["job_settings_sha256"], "deployed_job_changed")
    specs = job["settings"]["tasks"]
    tasks = run.get("tasks", [])
    by_key = {t["task_key"]: t for t in tasks}
    _require(len(by_key) == len(tasks) == len(specs) > 0 and
             set(by_key) == {s["task_key"] for s in specs}, "remote_task_coverage_incomplete")
    supported = {"notebook_task", "spark_python_task", "condition_task"}
    used_paths = set()
    for spec in specs:
        task = by_key[spec["task_key"]]
        kinds = set(spec) & supported
        _require(len(kinds) == 1 and not any(k.endswith("_task") and k not in supported for k in spec),
                 "unsupported_remote_task")
        kind = next(iter(kinds))
        _require(task.get(kind) == spec[kind] and task.get("libraries", []) == spec.get("libraries", []) and
                 not spec.get("libraries"), "remote_task_payload_mismatch_or_unpinned_library")
        if kind != "condition_task":
            config = spec[kind]
            _require(config.get("source", "WORKSPACE") == "WORKSPACE", "workspace_source_required")
            used_paths.add(config["notebook_path" if kind == "notebook_task" else "python_file"])
        _require(task.get("state", {}).get("life_cycle_state") == "TERMINATED" and
                 task.get("state", {}).get("result_state") == "SUCCESS", "remote_task_not_successful")
    assets = deployment["assets"]
    _require(bool(assets) and used_paths <= set(assets), "remote_source_assets_incomplete")
    for path, digest in assets.items():
        _require(isinstance(path, str) and path.startswith("/Workspace/") and
                 not any(p in {"..", ".", ""} for p in path.split("/")[1:]), "unsafe_workspace_asset")
        raw = provider.export_deployed_source(owner, path)
        _require(isinstance(raw, bytes) and sha256(raw).hexdigest() == digest,
                 "deployed_source_bytes_mismatch")


def _member(reader: _Reader, plan: dict, wid: str, link_id: str, pins: Mapping[str, Any]) -> dict:
    """Join persisted lineage, approval, candidate, independent cases and run."""
    unit = plan["units"][wid]
    current = pins["workflow_inputs"][wid]
    link = reader.record("acceptance_member", link_id)
    binding = {"plan_sha256": plan["plan_sha256"], "workflow_id": wid,
               "source_contract_sha256": unit["source_contract_sha256"], "pins_sha256": _hashpins(pins)}
    _require(all(link.get(k) == v for k, v in binding.items()), "member_source_scope_mismatch")
    design = reader.record("design", link["design_id"])
    source = design["source"]
    _require(source["kind"] == "agent" and design["workflow_id"] == wid and
             design["id"] == current["design_id"] and design["version"] == current["design_version"] and
             design["scenario"] == current["scenario"] and design["input_version"] == current["input_version"] and
             design["package_hash"] == pins["package_hash"] and not design["design"]["blockers"] and
             source["input_hashes"] == current["input_hashes"] and
             source["input_versions"] == current["input_versions"], "current_agent_design_required")
    generation = reader.record("writer_manifest", link["writer_id"])
    decisions = [r for r in design["reviews"] if r["action"] != "comment"]
    _require(bool(decisions), "approval_missing")
    approval = decisions[-1]
    _require(approval["action"] == "approve_for_drafting" and approval["consent"] is True and
             approval["version"] == design["version"] and
             approval["input_version"] == design["input_version"] and
             generation["approval_review_version"] == design["review_version"] and
             generation["approval_sha256"] == _hash(approval), "exact_current_approval_required")
    expected_join = {"design_id": design["id"], "design_version": design["version"],
                     "design_sha256": _hash(design["design"]), "input_version": design["input_version"],
                     "package_hash": pins["package_hash"]}
    _require(all(generation.get(k) == v for k, v in expected_join.items()) and
             generation["input_hashes"] == current["input_hashes"] and
             generation["input_versions"] == current["input_versions"] and
             generation["status"] == "generation_complete" and
             generation["producer"] == "spark_codegen" and generation["behavior_basis"] == "source_preserving",
             "writer_design_join_mismatch")
    assessment = generation["translation_assessment"]
    _require(assessment["status"] == "static_inventory_accounted" and
             assessment["missing_items"] == [] and assessment["blocked_items"] == [] and
             assessment["behavior_basis"] == "source_preserving", "translation_inventory_incomplete")
    targets = reader.record("target_selection", link["target_selection_id"])
    policy = reader.provider.current_validation_policy(reader.owner)
    _require(isinstance(policy, ValidationPolicy) and bool(policy.allowed_catalogs) and
             bool(policy.schema_prefix) and
             targets["workflow_id"] == wid and targets["input_version"] == current["input_version"] and
             targets["source_contract_sha256"] == unit["source_contract_sha256"] and
             targets["design_sha256"] == expected_join["design_sha256"] and
             targets["status"] == "approved_for_controlled_validation" and
             targets["approved_by"] == reader.owner.user_id and
             _hash(targets) == link["target_selection_sha256"] == current["target_selection_sha256"],
             "current_target_selection_required")
    target_ids = {o.removeprefix("target:") for o in unit["obligations"] if o.startswith("target:")}
    _require(set(targets["targets"]) == target_ids, "target_selection_coverage_incomplete")
    for destination in targets["targets"].values():
        _require(set(destination) == {"catalog", "schema", "table", "disposition"} and
                 destination["catalog"] in policy.allowed_catalogs and
                 destination["schema"].startswith(policy.schema_prefix) and
                 all(isinstance(destination[k], str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", destination[k])
                     for k in ("catalog", "schema", "table")) and
                 destination["disposition"] in {"table", "controlled_file_capture", "control_observation"},
                 "target_outside_controlled_namespace")
    # Persisted agent/model tool history must contain the EXACT submission.
    agent = reader.record("agent_run", source["record_id"])
    output = agent["output"]
    _require(agent["id"] == source["record_id"] and agent["workflow_id"] == wid and
             agent["package_hash"] == pins["package_hash"] and agent["consent"] is True and
             agent["input_version"] == design["input_version"] and
             output["run_id"] == source["runtime_run_id"] and
             output["redesigns"][source["submission_index"]]["design"] == design["design"],
             "persisted_agent_submission_required")
    proof = reader.record("generation_provenance", link["provenance_id"])
    _require(proof["designOrigin"] == "agent_submitted" and
             proof["agent_runtime_run_id"] == source["runtime_run_id"] and
             proof["writer_run_id"] == generation["run_id"] and
             proof["design_sha256"] == expected_join["design_sha256"] and
             proof["writer_manifest_sha256"] == _hash(generation), "generation_provenance_mismatch")
    # Each call is separately persisted by the host transport, not LLM usage text.
    for role, run_id in (("design", source["runtime_run_id"]), ("writer", generation["run_id"])):
        calls = proof[role + "_model_call_ids"]
        _require(bool(calls) and len(set(calls)) == len(calls), "model_call_evidence_missing")
        for identifier in calls:
            call = reader.record("model_call", identifier)
            _require(call["runtime_run_id"] == run_id and call["status"] == "completed" and
                     bool(call["provider_request_id"]) and bool(call["model_route_sha256"]),
                     "model_transport_receipt_mismatch")
    deployment = reader.record("job_manifest", link["deployment_id"])
    _require(all(deployment.get(k) == v for k, v in {**binding, **expected_join}.items()) and
             deployment["writer_run_id"] == generation["run_id"] and
             deployment["writer_manifest_sha256"] == _hash(generation) and
             deployment["target_selection_sha256"] == _hash(targets), "deployment_lineage_mismatch")
    artifacts = generation["artifact_hashes"]
    _require(bool(artifacts) and set(deployment["artifact_paths"]) == set(artifacts) and
             len(set(deployment["artifact_paths"].values())) == len(artifacts) and
             set(deployment["assets"]) == set(deployment["artifact_paths"].values()),
             "deployment_artifacts_incomplete")
    for path, digest in artifacts.items():
        _require(sha256(reader.raw("writer_artifact", generation["run_id"] + ":" + path)).hexdigest() == digest and
                 deployment["assets"].get(deployment["artifact_paths"][path]) == digest,
                 "writer_deployed_artifact_mismatch")
    review = reader.record("behavior_review", link["behavior_review_id"])
    _require(review["source_contract_sha256"] == unit["source_contract_sha256"] and
             review["pins_sha256"] == binding["pins_sha256"] and
             review["writer_manifest_sha256"] == _hash(generation) and
             review["assets_sha256"] == _hash(deployment["assets"]) and
             review["obligations_sha256"] == _hash(unit["obligations"]) and
             review["decision"] == "source_preserving_without_substitutions" and
             review["reviewer_id"] != deployment["execution_principal"] and
             review["dependency_asset_paths"] == sorted(deployment["assets"]),
             "independent_source_behavior_review_required")
    _run(reader.provider, reader.owner, deployment)
    cases = link["cases"]
    _require(len(cases) >= len(CASE_KINDS) and len({c["id"] for c in cases}) == len(cases),
             "independent_case_coverage_incomplete")
    kinds, obligations, results = set(), set(), []
    for case_ref in cases:
        case = reader.record("comparison_case", case_ref["id"])
        _require(all(case.get(k) == v for k, v in binding.items()) and
                 case["job_run_id"] == deployment["run_id"] and
                 case["writer_manifest_sha256"] == _hash(generation) and
                 case["kind"] in CASE_KINDS and case["stages"] == {s: "completed" for s in STAGES},
                 "case_execution_join_mismatch")
        kinds.add(case["kind"])
        expected = reader.record("expected_snapshot", case["expected_id"])
        actual = reader.record("actual_snapshot", case["actual_id"])
        inputs = reader.record("source_snapshot", case["source_snapshot_id"])
        _require(inputs["pins_sha256"] == binding["pins_sha256"] and inputs["workflow_id"] == wid and
             bool(inputs["snapshot_identity"]) and
             expected["input_snapshot_sha256"] == actual["input_snapshot_sha256"] == _hash(inputs) and
             expected["case_id"] == case_ref["id"] and
             expected["target_selection_sha256"] == actual["target_selection_sha256"] == _hash(targets),
             "input_snapshot_or_target_mismatch")
        _require(expected["basis"] in {"original_source_snapshot", "reviewed_fixed_case"} and
                 expected["producer_run_id"] != generation["run_id"] and
                 expected["producer_run_id"] != str(deployment["run_id"]) and
                 expected["independence_record_id"] != "", "independent_expected_required")
        independence = reader.record("reference_review", expected["independence_record_id"])
        _require(independence["expected_sha256"] == _hash(expected["observations"]) and
                 independence["source_contract_sha256"] == unit["source_contract_sha256"] and
                 independence["pins_sha256"] == binding["pins_sha256"] and
                 independence["method"] in {"original_engine_capture", "independently_authored_literals"} and
                 independence["reviewer_id"] != deployment["execution_principal"] and
                 independence["candidate_used"] is False, "independent_reference_review_required")
        _require(actual["job_run_id"] == deployment["run_id"] and
                 actual["case_id"] == case_ref["id"] and
                 actual["assets_sha256"] == _hash(deployment["assets"]) and
                 case["expected_sha256"] == _hash(expected) and
                 case["actual_sha256"] == _hash(actual), "snapshot_case_hash_mismatch")
        left, right = expected["observations"], actual["observations"]
        _require(bool(left) and set(left) == set(right), "observation_coverage_mismatch")
        _require(set(left) <= set(unit["obligations"]), "noncanonical_observation")
        if case["kind"] == "normal":
            _require(all("target:" + tid in left and bool(left["target:" + tid]["rows"])
                         for tid in unit["business_target_ids"]), "nonempty_business_outputs_required")
        characteristics = {"nulls": False, "duplicates": False, "dates": False,
                           "failure_recovery": False}
        for key in sorted(left):
            a, b = left[key], right[key]
            _require(a["columns"] == b["columns"] and a.get("keys", []) == b.get("keys", []),
                     "snapshot_schema_mismatch")
            result = compare_rows(a["rows"], b["rows"], a["columns"], keys=a.get("keys", []))
            _require(result["status"] == "passing", "typed_multiset_mismatch")
            characteristics["nulls"] |= result["expected_null_cells"] > 0
            characteristics["duplicates"] |= len({_hash(r) for r in a["rows"]}) < len(a["rows"])
            characteristics["dates"] |= any(c["type"] in {"DATE", "TIMESTAMP_NTZ"} for c in a["columns"]) and bool(a["rows"])
            if any(c["name"] == "event" and c["type"] == "STRING" for c in a["columns"]):
                characteristics["failure_recovery"] |= {"failed", "recovered"} <= {r["event"] for r in a["rows"]}
        _require(case["kind"] == "normal" or characteristics[case["kind"]], "case_label_without_observations")
        results.append({"case_id": case_ref["id"], "kind": case["kind"], "observation_count": len(left),
                        "comparison": "exact_typed_multiset", "status": "passing"})
        obligations.update(left)
    _require(kinds == CASE_KINDS and obligations == set(unit["obligations"]), "full_behavior_coverage_required")
    return {"workflow_id": wid, "status": "evidence_complete", "design_id": design["id"],
            "design_version": design["version"], "writer_run_id": generation["run_id"],
            "agent_runtime_run_id": source["runtime_run_id"], "job_run_id": deployment["run_id"],
            "comparison_count": sum(r["observation_count"] for r in results), "comparisons": results}


def assess(plan: dict, model: dict, *, owner: Owner, provider: Provider | None = None) -> dict:
    """Fail-closed internal aggregator. Passing means evidence complete ONLY.

    The host must enforce independent recorder/fixture-review permissions before
    installing Provider. No public raw-attestation entry point is supplied.
    """
    report = {"version": VERSION, "status": "not_provided", "evidence_complete": False,
              "verified_workflows": 0, "verified_cohorts": 0, "items": [], "blockers": [],
              "production_certified": False, "output_parity": False,
              "execution_allowed": False, "deployment_allowed": False,
              "certificate_issuer_implemented": False}
    try:
        rebuilt = build_plan(model, plan["workflow_ids"], plan["cohort_seeds"])
        _require(rebuilt == plan and plan["package_hash"] == owner.package_hash, "plan_source_changed")
        if provider is None:
            report["blockers"] = ["trusted_provider_not_provided"]
            return report
        _require(provider.current_identity() == owner, "fresh_remote_identity_mismatch")
        inventory = provider.read_committed(owner)
        reader = _Reader(provider, owner, inventory)
        pins = dict(provider.current_source_pins(owner))
        pins_hash = _hashpins(pins)
        _require(pins["package_hash"] == owner.package_hash and pins.get("model_sha256") == plan["model_sha256"],
                 "current_source_model_mismatch")
        for item in plan["items"]:
            row = {"key": item["key"], "status": "not_run", "members": []}
            try:
                receipt = reader.record("acceptance_item", item["key"])
                _require(receipt["plan_sha256"] == plan["plan_sha256"] and
                         receipt["pins_sha256"] == pins_hash and
                         set(receipt["members"]) == set(item["members"]), "item_exact_scope_required")
                if item["key"].startswith("cohort:"):
                    _require(receipt["membership_sha256"] == item["membership_sha256"] and
                             receipt["invocations_sha256"] == item["invocations_sha256"],
                             "cohort_edges_mismatch")
                for wid in item["members"]:
                    row["members"].append(_member(reader, plan, wid, receipt["members"][wid], pins))
                if item["key"].startswith("cohort:"):
                    # Individual runs alone do not prove invocation/nowait behavior.
                    flow = reader.record("cohort_execution", receipt["cohort_execution_id"])
                    _require(flow["plan_sha256"] == plan["plan_sha256"] and
                             flow["pins_sha256"] == pins_hash and
                             flow["invocations_sha256"] == item["invocations_sha256"] and
                             flow["member_run_ids"] == {r["workflow_id"]: r["job_run_id"] for r in row["members"]},
                             "cohort_execution_join_mismatch")
                    # This record must be independently captured by the host,
                    # covering each exact source invocation, including nowait.
                    events = reader.record("invocation_observations", flow["observations_id"])
                    _require(events["expected_sha256"] == flow["expected_sha256"] and
                             events["actual_sha256"] == flow["actual_sha256"] and
                             events["invocations_sha256"] == item["invocations_sha256"] and
                             events["observed_site_count"] == item["invocation_count"] and
                             events["source_preserving_review_id"] in flow["independent_review_ids"] and
                             bool(flow["independent_review_ids"]), "cohort_observations_incomplete")
                    reference = reader.record("cohort_reference", events["source_preserving_review_id"])
                    _require(reference["candidate_used"] is False and
                             reference["pins_sha256"] == pins_hash and
                             reference["reviewer_id"] != owner.user_id and
                             reference["invocations_sha256"] == item["invocations_sha256"] and
                             sorted(reference["expected"]) == item["invocation_sites"] and
                             _hash(reference["expected"]) == events["expected_sha256"] and
                             _hash(events["actual"]) == events["actual_sha256"] and
                             reference["expected"] == events["actual"], "cohort_behavior_mismatch")
                row["status"] = "evidence_complete"
            except EvidenceError as exc:
                row["blocker"] = str(exc)
            except Exception:
                row["blocker"] = "evidence_unavailable_or_invalid"
            report["items"].append(row)
        # Authorization, owner manifest AND full pins must still be current.
        _require(provider.current_identity() == owner and provider.read_committed(owner) == inventory and
                 _hashpins(provider.current_source_pins(owner)) == pins_hash, "authority_changed_during_read")
        report["verified_workflows"] = sum(r["status"] == "evidence_complete" and
                                            r["key"].startswith("workflow:") for r in report["items"])
        report["verified_cohorts"] = sum(r["status"] == "evidence_complete" and
                                         r["key"].startswith("cohort:") for r in report["items"])
        complete = report["verified_workflows"] == 10 and report["verified_cohorts"] == 2
        report.update(status="evidence_complete" if complete else "blocked", evidence_complete=complete)
    except EvidenceError as exc:
        report["blockers"] = [str(exc)]
        report["status"] = "blocked"
    except Exception:
        report["blockers"] = ["authority_unavailable_or_invalid"]
        report["status"] = "blocked"
    if report["blockers"]:
        # A late owner/pin/commit change invalidates every earlier observation.
        report["verified_workflows"] = report["verified_cohorts"] = 0
        report["evidence_complete"] = False
        for item in report["items"]:
            item["status"] = "authority_invalidated"
            item["members"] = []
    return report


def materialize_request(model: dict, request: dict) -> dict:
    """Resolve persisted seed selectors against canonical definitions, not labels alone."""
    _require(request["version"] == VERSION and request["package_hash"] == model["package"]["sha256"],
             "request_package_mismatch")
    seeds = []
    for selector in request["cohort_seed_selectors"]:
        matches = [n["id"] for n in model["nodes"] if n["kind"] == "workflow" and
                   n["name"] == selector["name"] and
                   all(n.get("scope", {}).get(k) == selector[k] for k in ("repository", "folder"))]
        _require(len(matches) == 1, "canonical_seed_selector_not_unique")
        seeds.append(matches[0])
    return build_plan(model, request["workflow_ids"], seeds)


def _main() -> None:
    """Explicit offline PLAN export only. Never acceptance evidence or remote action."""
    import argparse
    import gzip
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Export canonical acceptance plan only, no execution")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with gzip.open(args.model, "rt", encoding="utf-8") as stream:
        model = json.load(stream)
    request = json.loads(args.request.read_bytes())
    plan = materialize_request(model, request)
    # Exclusive publication. Never overwrite another agent's result or history.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump({"basis": "offline_plan_not_execution_proof", "plan": plan,
                   "acceptance": assess(plan, model, owner=Owner("offline", "offline", "offline",
                                                               plan["package_hash"]))}, stream, indent=2)


if __name__ == "__main__":
    _main()