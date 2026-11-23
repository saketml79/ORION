"""Authored offline unit/contract tests. Fake SDK receipts are NOT real E2E proof.

No model, Job execution, deployment, SQL or network client is constructed.
The saved canonical model is used only for planning and fail-closed reports.
"""
from copy import deepcopy
from dataclasses import replace
import gzip
from hashlib import sha256
import json
from pathlib import Path

import pytest

from workbench import conversion_acceptance as gate


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
OWNER = gate.Owner("workspace-unit-only", "unit@example.invalid", "project-unit-only", PACKAGE)
WID = gate.LEDGER_PRIORITY_IDS[0]
SUCCESS = {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}


class FakeJobs:
    def __init__(self):
        self.calls = []
        self.spec = {"task_key": "unit", "spark_python_task": {
            "python_file": "/Workspace/unit/main.py", "source": "WORKSPACE"}}
        self.job = {"job_id": 41, "creator_user_name": OWNER.user_id,
                    "settings": {"name": "UNIT_NOT_REAL", "tasks": [self.spec]}}
        self.run = {"job_id": 41, "run_id": 42, "creator_user_name": OWNER.user_id,
                    "run_as_user_name": OWNER.user_id, "state": deepcopy(SUCCESS),
                    "tasks": [{**deepcopy(self.spec), "state": deepcopy(SUCCESS)}]}

    def get_run(self, *, run_id):
        self.calls.append(("get_run", run_id))
        return deepcopy(self.run)

    def get(self, *, job_id):
        self.calls.append(("get", job_id))
        return deepcopy(self.job)

    def __getattr__(self, name):
        raise AssertionError("Non-read-only SDK method requested: " + name)


class FakeProvider:
    """Trusted-boundary simulation only, never installed in the application."""
    def __init__(self):
        self.jobs = FakeJobs()
        self.values = {}
        self.assets = {"/Workspace/unit/main.py": b"# unit fixture, never executed\n"}
        self.identity = OWNER
        self.pins = {}
        self.policy = gate.ValidationPolicy(("unit_catalog",), "acceptance_")
        self.exports = []

    def current_identity(self):
        return self.identity

    def current_source_pins(self, owner):
        assert owner == OWNER
        return deepcopy(self.pins)

    def current_validation_policy(self, owner):
        assert owner == OWNER
        return self.policy

    def read_committed(self, owner):
        assert owner == OWNER
        refs = {}
        for key, value in self.values.items():
            raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
            refs[key] = gate.ArtifactRef(json.dumps(key), sha256(raw).hexdigest(), len(raw))
        return gate.CommittedInventory(OWNER, "unit-generation", gate._hash(sorted(
            (list(key), ref.sha256) for key, ref in refs.items())), refs)

    def load_artifact(self, owner, reference):
        assert owner == OWNER
        value = self.values[tuple(json.loads(reference.key))]
        return value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()

    def export_deployed_source(self, owner, workspace_path):
        assert owner == OWNER
        self.exports.append(workspace_path)
        return self.assets[workspace_path]


@pytest.fixture(scope="module")
def canonical():
    # Existing immutable canonical artifact, not a new parse or hosted authority.
    with gzip.open(ROOT / "output/workbench/packages" / PACKAGE / "understanding.json.gz",
                   "rt", encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture(scope="module")
def real_plan(canonical):
    request = json.loads((ROOT / "output/workbench/conversion-acceptance-20261110-v1/request.json").read_bytes())
    return gate.materialize_request(canonical, request)


@pytest.fixture
def unit():
    """Small complete single-member evidence graph, NOT ten real migrations."""
    provider = FakeProvider()
    put = lambda family, identifier, value: provider.values.__setitem__((family, identifier), value)
    plan = {"plan_sha256": "unit-plan", "units": {WID: {
        "source_contract_sha256": "unit-contract", "business_target_ids": ["T"],
        "obligations": ["node:" + WID, "target:T"]}}}
    current = {"design_id": "design", "design_version": 1, "scenario": "original",
               "input_version": "unit-input", "input_hashes": {"source": "unit-source"},
               "input_versions": {"source": 1}}
    approval = {"action": "approve_for_drafting", "consent": True, "version": 1,
                "input_version": "unit-input"}
    design = {"id": "design", "version": 1, "workflow_id": WID, "scenario": "original",
              "package_hash": PACKAGE, "input_version": "unit-input", "design": {"blockers": [], "tasks": []},
              "reviews": [approval], "review_version": 1, "source": {
                  "kind": "agent", "record_id": "agent", "runtime_run_id": "design-runtime",
                  "submission_index": 0, "input_hashes": current["input_hashes"],
                  "input_versions": current["input_versions"]}}
    join = {"design_id": "design", "design_version": 1, "design_sha256": gate._hash(design["design"]),
            "input_version": "unit-input", "package_hash": PACKAGE}
    targets = {"workflow_id": WID, "input_version": "unit-input", "source_contract_sha256": "unit-contract",
               "design_sha256": join["design_sha256"], "status": "approved_for_controlled_validation",
               "approved_by": OWNER.user_id, "targets": {"T": {"catalog": "unit_catalog",
                   "schema": "acceptance_unit", "table": "result", "disposition": "table"}}}
    current["target_selection_sha256"] = gate._hash(targets)
    provider.pins = {"package_hash": PACKAGE, "understanding_sha256": "a" * 64, "analysis_version": None,
                     "input_hashes": {"canonical": "unit"}, "input_versions": {"canonical": 1},
                     "workflow_inputs": {WID: current}}
    binding = {"plan_sha256": "unit-plan", "workflow_id": WID,
               "source_contract_sha256": "unit-contract", "pins_sha256": gate._hashpins(provider.pins)}
    raw = provider.assets["/Workspace/unit/main.py"]
    writer = {**join, "run_id": "writer", "approval_review_version": 1,
              "approval_sha256": gate._hash(approval), "input_hashes": current["input_hashes"],
              "input_versions": current["input_versions"], "status": "generation_complete",
              "producer": "spark_codegen", "behavior_basis": "source_preserving",
              "translation_assessment": {"status": "static_inventory_accounted", "missing_items": [],
                  "blocked_items": [], "behavior_basis": "source_preserving"},
              "artifact_hashes": {"modules/main.py": sha256(raw).hexdigest()}}
    deployment = {**binding, **join, "writer_run_id": "writer", "writer_manifest_sha256": gate._hash(writer),
                  "target_selection_sha256": gate._hash(targets), "job_id": 41, "run_id": 42,
                  "execution_principal": OWNER.user_id,
                  "job_settings_sha256": gate._hash(provider.jobs.job["settings"]),
                  "assets": {"/Workspace/unit/main.py": sha256(raw).hexdigest()},
                  "artifact_paths": {"modules/main.py": "/Workspace/unit/main.py"}}
    put("design", "design", design)
    put("target_selection", "targets", targets)
    put("writer_manifest", "writer", writer)
    put("writer_artifact", "writer:modules/main.py", raw)
    put("job_manifest", "deployment", deployment)
    put("behavior_review", "behavior", {"source_contract_sha256": "unit-contract",
        "pins_sha256": binding["pins_sha256"], "writer_manifest_sha256": gate._hash(writer),
        "assets_sha256": gate._hash(deployment["assets"]),
        "obligations_sha256": gate._hash(plan["units"][WID]["obligations"]),
        "decision": "source_preserving_without_substitutions", "reviewer_id": "independent@example.invalid",
        "dependency_asset_paths": sorted(deployment["assets"])})
    put("agent_run", "agent", {"id": "agent", "workflow_id": WID, "package_hash": PACKAGE,
        "consent": True, "input_version": "unit-input", "output": {"run_id": "design-runtime",
            "redesigns": [{"design": deepcopy(design["design"])}]}})
    put("generation_provenance", "provenance", {"designOrigin": "agent_submitted",
        "agent_runtime_run_id": "design-runtime", "writer_run_id": "writer",
        "design_sha256": join["design_sha256"], "writer_manifest_sha256": gate._hash(writer),
        "design_model_call_ids": ["design-call"], "writer_model_call_ids": ["writer-call"]})
    for role, run_id in (("design", "design-runtime"), ("writer", "writer")):
        put("model_call", role + "-call", {"runtime_run_id": run_id, "status": "completed",
            "provider_request_id": "UNIT_NOT_REAL", "model_route_sha256": "unit-route"})
    columns = [{"name": "id", "type": "INT", "nullable": False},
               {"name": "label", "type": "STRING", "nullable": True},
               {"name": "day", "type": "DATE", "nullable": False},
               {"name": "event", "type": "STRING", "nullable": False}]
    # Literal fixed expectations. Never call candidate code to create expectations.
    rows = [{"id": 1, "label": None, "day": "2026-11-10", "event": "failed"},
            {"id": 1, "label": None, "day": "2026-11-10", "event": "failed"},
            {"id": 2, "label": "ok", "day": "2026-11-10", "event": "recovered"}]
    observations = {key: {"columns": columns, "keys": ["id"], "rows": deepcopy(rows)}
                    for key in plan["units"][WID]["obligations"]}
    cases = []
    for kind in sorted(gate.CASE_KINDS):
        identifier = "unit-" + kind
        cases.append({"id": identifier})
        inputs = {"workflow_id": WID, "pins_sha256": binding["pins_sha256"],
                  "snapshot_identity": "independent-unit-input-" + kind}
        expected = {"basis": "reviewed_fixed_case", "producer_run_id": "independent-author",
                    "independence_record_id": identifier, "observations": deepcopy(observations),
                    "case_id": identifier, "input_snapshot_sha256": gate._hash(inputs),
                    "target_selection_sha256": gate._hash(targets)}
        actual = {"job_run_id": 42, "case_id": identifier, "assets_sha256": gate._hash(deployment["assets"]),
                  "input_snapshot_sha256": gate._hash(inputs), "target_selection_sha256": gate._hash(targets),
                  "observations": deepcopy(observations)}
        put("source_snapshot", identifier, inputs)
        put("expected_snapshot", identifier, expected)
        put("actual_snapshot", identifier, actual)
        put("reference_review", identifier, {"expected_sha256": gate._hash(expected["observations"]),
            "source_contract_sha256": "unit-contract", "pins_sha256": binding["pins_sha256"],
            "method": "independently_authored_literals", "reviewer_id": "independent@example.invalid",
            "candidate_used": False})
        put("comparison_case", identifier, {**binding, "job_run_id": 42,
            "writer_manifest_sha256": gate._hash(writer), "kind": kind,
            "stages": {s: "completed" for s in gate.STAGES}, "source_snapshot_id": identifier,
            "expected_id": identifier, "actual_id": identifier,
            "expected_sha256": gate._hash(expected), "actual_sha256": gate._hash(actual)})
    put("acceptance_member", "link", {**binding, "design_id": "design", "writer_id": "writer",
        "provenance_id": "provenance", "deployment_id": "deployment", "cases": cases,
        "behavior_review_id": "behavior",
        "target_selection_id": "targets", "target_selection_sha256": gate._hash(targets)})
    return provider, plan


def evaluate_unit(unit):
    provider, plan = unit
    reader = gate._Reader(provider, OWNER, provider.read_committed(OWNER))
    return gate._member(reader, plan, WID, "link", provider.pins)


def test_complete_fake_single_member_is_unit_evidence_only(unit):
    result = evaluate_unit(unit)
    assert result["status"] == "evidence_complete" and result["comparison_count"] == 10
    assert unit[0].jobs.calls == [("get_run", 42), ("get", 41)]
    assert unit[0].exports == ["/Workspace/unit/main.py"]
    assert "production_certified" not in result  # no certificate API at member level


def test_comparator_is_existing_exact_implementation():
    from workbench.validation_data import compare_rows
    assert gate.compare_rows is compare_rows


def test_actual_plan_ten_definitions_two_full_distinct_components(real_plan):
    assert set(real_plan["workflow_ids"]) == set(gate.LEDGER_PRIORITY_IDS)
    cohorts = [i for i in real_plan["items"] if i["key"].startswith("cohort:")]
    assert sorted(len(c["members"]) for c in cohorts) == [4, 32]
    assert len(real_plan["units"]) == 36
    assert not set(cohorts[0]["members"]) & set(cohorts[1]["members"])
    assert all(c["invocation_count"] == len(c["invocation_sites"]) > 0 for c in cohorts)


def test_second_seed_same_component_is_not_second_cohort(canonical, real_plan):
    seeds = [real_plan["workflow_ids"][0], real_plan["workflow_ids"][1]]
    with pytest.raises(gate.EvidenceError, match="duplicate_connected_component"):
        gate.build_plan(canonical, real_plan["workflow_ids"], seeds)


@pytest.mark.parametrize("ids", [["invented-" + str(i) for i in range(10)], [WID] * 10,
                                 list(gate.LEDGER_PRIORITY_IDS[:-1])])
def test_fake_names_duplicates_or_nine_cannot_be_requested_ten(canonical, ids):
    with pytest.raises(gate.EvidenceError, match="exact_requested_ten_required"):
        gate.build_plan(canonical, ids, [WID, WID])


def test_without_provider_nothing_runs_or_counts(canonical, real_plan):
    report = gate.assess(real_plan, canonical, owner=OWNER)
    assert report["status"] == "not_provided"
    assert report["verified_workflows"] == report["verified_cohorts"] == 0
    assert report["blockers"] == ["trusted_provider_not_provided"]
    assert not any(report[k] for k in ("execution_allowed", "deployment_allowed", "production_certified", "output_parity"))


def test_empty_committed_inventory_cannot_be_proof(canonical, real_plan):
    provider = FakeProvider()
    provider.pins = {"package_hash": PACKAGE, "understanding_sha256": "a" * 64,
        "analysis_version": None, "input_hashes": {"source": "unit"}, "input_versions": {"source": 1},
        "model_sha256": real_plan["model_sha256"]}
    report = gate.assess(real_plan, canonical, owner=OWNER, provider=provider)
    assert report["status"] == "blocked" and len(report["items"]) == 12
    assert report["verified_workflows"] == report["verified_cohorts"] == 0
    assert provider.jobs.calls == provider.exports == []
    assert all(r["status"] == "not_run" for r in report["items"])


def test_plan_mutation_is_not_authoritative(canonical, real_plan):
    altered = deepcopy(real_plan)
    altered["units"][WID]["name"] = "fake-success"
    report = gate.assess(altered, canonical, owner=OWNER)
    assert report["blockers"] == ["plan_source_changed"]


def test_full_hashpins_detects_extra_identity_change(unit):
    pins = deepcopy(unit[0].pins)
    old = gate._hashpins(pins)
    pins["additional_observation_identity"] = {"count": 10}
    assert gate._hashpins(pins) != old


def test_owner_inventory_and_artifact_bytes_are_checked(unit):
    provider = unit[0]
    inventory = provider.read_committed(OWNER)
    with pytest.raises(gate.EvidenceError, match="committed_owner_manifest_required"):
        gate._Reader(provider, OWNER, replace(inventory, owner=replace(OWNER, user_id="other")))
    reader = gate._Reader(provider, OWNER, inventory)
    provider.values[("design", "design")]["version"] = 99
    with pytest.raises(gate.EvidenceError, match="artifact_bytes_mismatch"):
        reader.record("design", "design")


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p.values[("design", "design")]["source"].update(kind="deterministic"), "current_agent_design_required"),
    (lambda p: p.values[("design", "design")].update(version=2), "current_agent_design_required"),
    (lambda p: p.values[("design", "design")]["reviews"][-1].update(action="reject"), "exact_current_approval_required"),
    (lambda p: p.values[("writer_manifest", "writer")].update(design_version=99), "writer_design_join_mismatch"),
    (lambda p: p.values[("writer_manifest", "writer")]["translation_assessment"].update(status="unverified"), "translation_inventory_incomplete"),
    (lambda p: p.values[("generation_provenance", "provenance")].update(designOrigin="deterministic"), "generation_provenance_mismatch"),
    (lambda p: p.values[("model_call", "writer-call")].update(status="not_run"), "model_transport_receipt_mismatch"),
    (lambda p: p.values[("agent_run", "agent")]["output"].update(run_id="invented"), "persisted_agent_submission_required"),
    (lambda p: p.values[("acceptance_member", "link")].update(target_selection_sha256="old"), "current_target_selection_required"),
    (lambda p: setattr(p, "policy", gate.ValidationPolicy(("different_catalog",), "acceptance_")), "target_outside_controlled_namespace"),
    (lambda p: p.values[("job_manifest", "deployment")].update(writer_run_id="old"), "deployment_lineage_mismatch"),
    (lambda p: p.values.__setitem__(("writer_artifact", "writer:modules/main.py"), b"wrong code"), "writer_deployed_artifact_mismatch"),
    (lambda p: p.assets.update({"/Workspace/unit/main.py": b"different deployed code"}), "deployed_source_bytes_mismatch"),
    (lambda p: p.jobs.run["state"].update(result_state="FAILED"), "remote_run_not_successful"),
    (lambda p: p.jobs.run.update(creator_user_name="other@example.invalid"), "remote_run_owner_mismatch"),
    (lambda p: p.jobs.run.update(run_as_user_name="service-principal"), "remote_run_owner_mismatch"),
    (lambda p: p.jobs.run.update(job_id=999), "remote_run_join_mismatch"),
    (lambda p: p.jobs.job["settings"].update(name="changed after deployment"), "deployed_job_changed"),
    (lambda p: p.jobs.run.update(tasks=[]), "remote_task_coverage_incomplete"),
    (lambda p: p.jobs.run["tasks"][0]["spark_python_task"].update(python_file="/Workspace/not-writer.py"), "remote_task_payload_mismatch_or_unpinned_library"),
    (lambda p: p.jobs.run["tasks"][0]["state"].update(result_state="SKIPPED"), "remote_task_not_successful"),
    (lambda p: p.values[("reference_review", "unit-normal")].update(candidate_used=True), "independent_reference_review_required"),
    (lambda p: p.values[("reference_review", "unit-normal")].update(reviewer_id=OWNER.user_id), "independent_reference_review_required"),
    (lambda p: p.values[("comparison_case", "unit-normal")]["stages"].update(comparison="not_run"), "case_execution_join_mismatch"),
    (lambda p: p.values[("comparison_case", "unit-normal")].update(expected_sha256="wrong"), "snapshot_case_hash_mismatch"),
    (lambda p: p.values[("actual_snapshot", "unit-normal")].update(input_snapshot_sha256="wrong"), "input_snapshot_or_target_mismatch"),
])
def test_evidence_tampering_fails_closed(unit, mutate, code):
    mutate(unit[0])
    with pytest.raises(gate.EvidenceError, match=code):
        evaluate_unit(unit)


@pytest.mark.parametrize("change", ["value", "duplicate", "date", "null", "schema"])
def test_same_count_candidate_mutation_never_becomes_expected(unit, change):
    provider = unit[0]
    actual = provider.values[("actual_snapshot", "unit-normal")]
    observation = actual["observations"]["target:T"]
    if change == "value":
        observation["rows"][0]["id"] = 9
    elif change == "duplicate":
        observation["rows"][0] = deepcopy(observation["rows"][2])
    elif change == "date":
        observation["rows"][0]["day"] = "2026-11-11"
    elif change == "null":
        observation["rows"][0]["label"] = "not-null"
    else:
        observation["columns"][0]["type"] = "BIGINT"
    # Even re-hashing a freshly persisted actual snapshot cannot mint parity.
    provider.values[("comparison_case", "unit-normal")]["actual_sha256"] = gate._hash(actual)
    with pytest.raises(gate.EvidenceError, match="snapshot_schema_mismatch|typed_multiset_mismatch"):
        evaluate_unit(unit)


def test_missing_expected_and_unmanifested_json_are_not_accepted(unit):
    provider = unit[0]
    del provider.values[("expected_snapshot", "unit-normal")]
    with pytest.raises(gate.EvidenceError, match="artifact_not_in_manifest"):
        evaluate_unit(unit)
    with pytest.raises(gate.EvidenceError, match="committed_owner_manifest_required"):
        gate._Reader(provider, OWNER, {"verified": True, "signature": "client-signed"})


def test_complete_cases_must_cover_every_source_obligation(unit):
    unit[1]["units"][WID]["obligations"].append("property:unobserved-source-behavior")
    unit[0].values[("behavior_review", "behavior")]["obligations_sha256"] = gate._hash(unit[1]["units"][WID]["obligations"])
    with pytest.raises(gate.EvidenceError, match="full_behavior_coverage_required"):
        evaluate_unit(unit)


def test_no_provider_or_row_error_details_leak(canonical, real_plan):
    class Broken(FakeProvider):
        def current_identity(self):
            raise RuntimeError("sensitive row or token must not escape")
    report = gate.assess(real_plan, canonical, owner=OWNER, provider=Broken())
    assert report["blockers"] == ["authority_unavailable_or_invalid"]
    assert "sensitive" not in json.dumps(report)


def _aggregation_fixture(real_plan, monkeypatch):
    """Aggregator seams ONLY. Member verification has its own fake-unit tests.

    These fabricated callbacks cannot establish actual conversion or remote E2E.
    """
    provider = FakeProvider()
    provider.pins = {"package_hash": PACKAGE, "understanding_sha256": "a" * 64,
        "analysis_version": None, "input_hashes": {"source": "unit"}, "input_versions": {"source": 1},
        "model_sha256": real_plan["model_sha256"]}
    pins_hash = gate._hashpins(provider.pins)
    run_ids = {wid: i + 1 for i, wid in enumerate(real_plan["units"])}

    def stub_member(reader, plan, wid, link_id, pins):
        assert link_id == wid
        return {"workflow_id": wid, "job_run_id": run_ids[wid], "status": "evidence_complete"}

    monkeypatch.setattr(gate, "_member", stub_member)
    for item in real_plan["items"]:
        key = item["key"]
        receipt = {"plan_sha256": real_plan["plan_sha256"], "pins_sha256": pins_hash,
                   "members": {wid: wid for wid in item["members"]}}
        provider.values[("acceptance_item", key)] = receipt
        if not key.startswith("cohort:"):
            continue
        receipt.update(membership_sha256=item["membership_sha256"],
                       invocations_sha256=item["invocations_sha256"], cohort_execution_id=key)
        observations = {site: {"launch_and_completion": "unit_observation_not_real"}
                        for site in item["invocation_sites"]}
        digest = gate._hash(observations)
        provider.values[("cohort_execution", key)] = {
            "plan_sha256": real_plan["plan_sha256"], "pins_sha256": pins_hash,
            "invocations_sha256": item["invocations_sha256"],
            "member_run_ids": {wid: run_ids[wid] for wid in item["members"]},
            "observations_id": key, "expected_sha256": digest, "actual_sha256": digest,
            "independent_review_ids": [key]}
        provider.values[("invocation_observations", key)] = {
            "expected_sha256": digest, "actual_sha256": digest,
            "invocations_sha256": item["invocations_sha256"], "observed_site_count": item["invocation_count"],
            "source_preserving_review_id": key, "actual": deepcopy(observations)}
        provider.values[("cohort_reference", key)] = {
            "candidate_used": False, "pins_sha256": pins_hash, "reviewer_id": "independent@example.invalid",
            "invocations_sha256": item["invocations_sha256"], "expected": observations}
    return provider


def test_aggregation_complete_seams_never_grants_execution_or_certification(canonical, real_plan, monkeypatch):
    provider = _aggregation_fixture(real_plan, monkeypatch)
    report = gate.assess(real_plan, canonical, owner=OWNER, provider=provider)
    assert report["evidence_complete"] and report["verified_workflows"] == 10 and report["verified_cohorts"] == 2
    assert not any(report[k] for k in ("execution_allowed", "deployment_allowed", "production_certified", "output_parity"))
    assert provider.jobs.calls == []  # Explicitly stubbed, NOT remote run evidence.


@pytest.mark.parametrize("mutation", ["member", "edge", "observation", "run_join"])
def test_cohort_requires_all_members_and_independent_exact_edges(canonical, real_plan, monkeypatch, mutation):
    provider = _aggregation_fixture(real_plan, monkeypatch)
    cohort = next(i for i in real_plan["items"] if i["key"].startswith("cohort:"))
    key = cohort["key"]
    if mutation == "member":
        provider.values[("acceptance_item", key)]["members"].pop(cohort["members"][0])
    elif mutation == "edge":
        provider.values[("acceptance_item", key)]["invocations_sha256"] = "wrong"
    elif mutation == "observation":
        provider.values[("invocation_observations", key)]["observed_site_count"] = 0
    else:
        provider.values[("cohort_execution", key)]["member_run_ids"][cohort["members"][0]] = 9999
    report = gate.assess(real_plan, canonical, owner=OWNER, provider=provider)
    assert not report["evidence_complete"] and report["verified_cohorts"] == 1


def test_late_identity_change_invalidates_all_earlier_observations(canonical, real_plan, monkeypatch):
    provider = _aggregation_fixture(real_plan, monkeypatch)
    observations = iter([OWNER, replace(OWNER, user_id="changed")])
    monkeypatch.setattr(provider, "current_identity", lambda: next(observations))
    report = gate.assess(real_plan, canonical, owner=OWNER, provider=provider)
    assert report["blockers"] == ["authority_changed_during_read"]
    assert report["verified_workflows"] == report["verified_cohorts"] == 0
    assert all(r["status"] == "authority_invalidated" and not r["members"] for r in report["items"])