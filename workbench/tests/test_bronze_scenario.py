"""Scenario-specific WIL_NET_DIS proposal regressions, entirely offline."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.agents import AgentRuntime, ROLES, _Evidence, build_agents
from workbench.redesign import (
    Design, DesignQualityError, build_wil_net_dis_candidate, capture_run, cited_ids,
    validate_submission,
)
from workbench.tests.test_redesign import chat
from workbench.understanding import analyze_package


@pytest.fixture(scope="module")
def source():
    model = analyze_package(Path(__file__).resolve().parents[2] / "input" / "LumenExample.XML")
    workflow_id = next(node["id"] for node in model["nodes"] if node["kind"] == "workflow"
                       and node["name"] == "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS")
    return model, workflow_id


def validate(candidate, source, scenario="original"):
    model, workflow_id = source
    evidence = _Evidence(model, workflow_id, [], [])
    return validate_submission(candidate, model, workflow_id,
                               cited_ids(Design.model_validate(candidate)), evidence.records,
                               scenario=scenario)


def test_real_connection_records_and_single_candidate_scope_traversal(source, monkeypatch):
    import workbench.redesign as redesign
    model, workflow_id = source
    evidence = _Evidence(model, workflow_id, [], [])
    assert (len(evidence.scope), len(evidence.scoped_edges)) == (82, 124)
    assert not any(node["kind"] == "unresolved_reference"
                   and node.get("attributes", {}).get("status_reason") == "connection_value_redacted"
                   for node in evidence.records.values())
    calls = []
    closure = redesign._workflow_scope

    def counted(*args):
        calls.append(True)
        return closure(*args)

    monkeypatch.setattr(redesign, "_workflow_scope", counted)
    candidate = build_wil_net_dis_candidate(model, workflow_id)
    assert len(calls) == 1
    refs = {node["id"] for node in evidence.records.values() if node["kind"] == "connectionreference"}
    review = next(item for item in candidate["scoped_unresolved"] if item["category"] == "connection")
    assert refs and refs <= set(review["evidence_ids"])
    assert "verified" in review["description"].lower()
    assert "redacted" not in review["description"].lower()
    assert validate(candidate, source).parameters == []


@pytest.mark.parametrize("scenario", ["original", "bronze"])
def test_connection_count_is_not_a_candidate_precondition(source, scenario):
    model, workflow_id = source
    model = deepcopy(model)
    removed = {node["id"] for node in model["nodes"] if node["kind"] == "connectionreference"}
    model["nodes"] = [node for node in model["nodes"] if node["id"] not in removed]
    model["edges"] = [edge for edge in model["edges"]
                      if edge["source"] not in removed and edge["target"] not in removed]
    candidate = build_wil_net_dis_candidate(model, workflow_id, scenario=scenario)
    assert validate(candidate, (model, workflow_id), scenario).blockers == []


def test_bronze_parameters_and_uc_only_review_without_historical_bindings(source):
    model, workflow_id = source
    candidate = build_wil_net_dis_candidate(model, workflow_id, scenario="bronze")
    design = validate(candidate, source, "bronze")
    assert {parameter.name: (parameter.required, parameter.value_type) for parameter in design.parameters} == {
        "bronze_source_table": (True, "string"), "target_table": (True, "string")}
    refs = {node["id"] for node in model["nodes"] if node["kind"] == "connectionreference"}
    review = next(item for item in design.scoped_unresolved if item.category == "connection")
    assert review.impact == "human_review" and not refs & set(review.evidence_ids)
    assert "Unity Catalog" in review.description
    assert "not required" in review.description
    assert design.blockers == []
    assert "separately" in design.artifact_plan[0].description
    assert "no notebook code is included" not in design.artifact_plan[0].description
    assert {(item.category, item.impact) for item in design.scoped_unresolved} == {
        ("connection", "human_review"), ("control", "execution_blocker"),
        ("notification", "peripheral_note")}


@pytest.mark.parametrize("name", ["bronze_source_table", "target_table"])
@pytest.mark.parametrize("mutation", ["missing", "optional", "object"])
def test_bronze_quality_requires_native_required_string_parameters(source, name, mutation):
    candidate = build_wil_net_dis_candidate(*source, scenario="bronze")
    parameter = next(item for item in candidate["parameters"] if item["name"] == name)
    if mutation == "missing":
        candidate["parameters"].remove(parameter)
    elif mutation == "optional":
        parameter["required"] = False
    else:
        parameter["value_type"] = "object"
    with pytest.raises(DesignQualityError) as caught:
        validate(candidate, source, "bronze")
    assert any(issue["field"] == f"parameters.{name}" for issue in caught.value.issues)


@pytest.mark.parametrize("scenario", ["original", "bronze"])
def test_both_scenarios_require_task_scoped_binding_review(source, scenario):
    candidate = build_wil_net_dis_candidate(*source, scenario=scenario)
    candidate["scoped_unresolved"] = [item for item in candidate["scoped_unresolved"]
                                      if item["category"] != "connection"]
    with pytest.raises(DesignQualityError) as caught:
        validate(candidate, source, scenario)
    assert any(issue["field"] == "scoped_unresolved" for issue in caught.value.issues)


def test_original_requires_historical_connection_evidence_but_bronze_does_not(source):
    candidate = build_wil_net_dis_candidate(*source, scenario="bronze")
    assert validate(candidate, source, "bronze")
    with pytest.raises(DesignQualityError):
        validate(candidate, source, "original")


@pytest.mark.parametrize("scenario", ["original", "bronze"])
def test_outline_tool_passes_scenario_through_redacted_validation(source, tmp_path, scenario):
    with build_agents(tmp_path, *source, scenario, chat_model=chat()) as bundle:
        receipt = bundle.tools["architecture"]["submit_wil_net_dis_outline"].invoke({})
        assert receipt["recorded"] is True, receipt
        assert not receipt["generation_allowed"] and not receipt["deployment_allowed"]
        assert len(bundle.redesigns) == 1
        validate(bundle.redesigns[0]["design"], source, scenario)
        assert bool(bundle.redesigns[0]["design"]["parameters"]) == (scenario == "bronze")


def test_runtime_without_agent_design_does_not_promote_bronze_reference_candidate(source, tmp_path):
    result = AgentRuntime(tmp_path, role_models={role: chat() for role in ROLES}).run(
        *source, "bronze", "Propose a Lakeflow design", [], [])
    assert result["status"] == "proposal_failed", result
    assert not result["redesigns"]
    assert result["design_origin"] is None
    assert result["design_submission"]["accepted_count"] == 0
    assert not result["approved"] and not result["generation_allowed"] and not result["deployment_allowed"]


def test_capture_uses_record_scenario_not_original_default(source, tmp_path):
    model, workflow_id = source
    candidate = build_wil_net_dis_candidate(model, workflow_id, scenario="bronze")
    record = {"id": "bronze-run", "workflow_id": workflow_id, "scenario": "bronze",
              "package_hash": model["package"]["sha256"], "consent": True,
              "input_version": "b" * 64, "input_hashes": {}, "input_versions": {},
              "redesign_snapshot": {"documents": [], "reviews": []}}
    output = {"status": "draft_available", "run_id": "a" * 32, "redesigns": [{
        "role": "architecture", "design": candidate,
        "retrieved_evidence_ids": sorted(cited_ids(Design.model_validate(candidate)))}]}
    saved = capture_run(SimpleNamespace(state=tmp_path), SimpleNamespace(model=model), record, output)
    assert len(saved) == 1 and saved[0]["scenario"] == "bronze"
    assert saved[0]["design"]["parameters"] == candidate["parameters"]
    assert not saved[0]["generation_allowed"] and not saved[0]["deployment_allowed"]


def test_old_builder_and_validator_signatures_remain_compatible(source):
    model, workflow_id = source
    candidate = build_wil_net_dis_candidate(model, workflow_id)
    evidence = _Evidence(model, workflow_id, [], [])
    assert validate_submission(candidate, model, workflow_id,
                               cited_ids(Design.model_validate(candidate)), evidence.records)