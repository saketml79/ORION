"""Offline contract regressions for failed live run 1d76b866. No activation."""
from copy import deepcopy
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from workbench.agents import build_agents
from workbench.tests.test_agents import fake, finding, sample_model
from workbench.tests.test_run_review_fixes import diagnostic_model


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("Network forbidden"))


def test_summary_length_feedback_is_numeric_payload_free_and_remember_matches(tmp_path):
    with build_agents(tmp_path, sample_model(), "wf", "original", chat_model=fake()) as bundle:
        tools = bundle.tools["discovery"]
        tools["lookup_evidence"].invoke({"evidence_id": "wf"})
        text = "PRIVATE_REJECTED_TEXT " + "x" * 1700
        for tool, flag in (("submit_finding", "recorded"), ("remember_finding", "remembered")):
            result = tools[tool].invoke({"finding": finding(summary=text)})
            assert result[flag] is False
            issue = result["validation_issues"][0]
            assert issue["field"] == "summary"
            assert issue["code"] == "string_too_long"
            assert issue["max_length"] == 1600 and issue["actual_length"] == len(text)
            assert "max_length=1600" in issue["issue"]
            assert f"actual_length={len(text)}" in issue["issue"]
            assert "PRIVATE_REJECTED_TEXT" not in json.dumps(result)
        assert not bundle.findings
        assert tools["submit_finding"].invoke({"finding": finding(summary="x" * 1600)})["recorded"]


def test_sql_inventory_occurrence_is_not_canonical_and_owner_is_citable(tmp_path):
    source = sample_model()
    source["nodes"][1]["properties"] = [{"attributes": {"NAME": "Sql Query", "VALUE": "SELECT 1"}}]
    original = deepcopy(source)
    with build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        tools = bundle.tools["discovery"]
        result = tools["sql_role_inventory"].invoke({})
        item = result["sql_inventory"][0]
        assert item["id"] not in bundle.evidence.records
        assert item["evidence_ids"] == [item["node_id"]]
        assert item["node_id"] in result["evidence_ids"]
        assert item["id"] not in result["evidence_ids"]
        assert item["id"] not in bundle.role_retrieved["discovery"]
        assert "item.id" in result["citation_contract"] and "node_id" in result["citation_contract"]
        assert tools["lookup_evidence"].invoke({"evidence_id": item["id"]})["status"] == "not_found_or_out_of_scope"
        rejected = tools["submit_finding"].invoke({"finding": finding(evidence_ids=[item["id"]])})
        assert rejected["recorded"] is False and "node_id" in rejected["instruction"]
        assert item["id"] not in rejected["retrieved_evidence_ids"]
        assert tools["submit_finding"].invoke({"finding": finding(evidence_ids=item["evidence_ids"])})["recorded"]
        assert not bundle.tools["semantics"]["submit_finding"].invoke(
            {"finding": finding(role="semantics", evidence_ids=item["evidence_ids"])})["recorded"]
    assert source == original


def test_sql_inventory_registers_only_existing_records_never_outside_nodes(tmp_path):
    # A returned occurrence ID can only be cited if it already is canonical evidence.
    source = sample_model()
    def sql_item(identifier, owner):
        return dict(id=identifier, node_id=owner, task_ids=[], role="source", phase="read", ordinal=0,
                    property_name="Sql Query", origin="property", original_sha256="hash", evidence={},
                    source_dialect="oracle", status="review", blocker_codes=[], original_sql="SELECT 1")
    manifest = dict(sql_inventory=[sql_item("mapping", "source"), sql_item("synthetic-id", "outside")],
                    tasks=[], ordering_contract={}, blockers=[])
    with patch("workbench.sql_artifacts.compile_sql_artifacts", return_value=SimpleNamespace(manifest=manifest)), \
            build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        result = bundle.tools["discovery"]["sql_role_inventory"].invoke({})
        assert result["sql_inventory"][0]["evidence_ids"] == ["mapping", "source"]
        assert result["sql_inventory"][1]["evidence_ids"] == []
        assert result["evidence_ids"] == ["mapping", "source"]
        assert "outside" not in bundle.evidence.records
        assert "synthetic-id" not in bundle.role_retrieved["discovery"]


def test_runtime_and_translation_review_phrases_do_not_require_xml_diagnostics(tmp_path):
    phrases = [
        "runtime child_job_id unresolved",
        "Runtime child_job_id is unresolved; bind the child job before execution.",
        "The runtime parameter 'child_job_id' is unresolved.",
        "Oracle SQL translation review remains unresolved.",
        "Oracle SQL translation has ambiguous runtime semantics and requires review.",
        "Review SQL translation; the child_job_id binding is not unique.",
    ]
    with build_agents(tmp_path, sample_model(), "wf", "original", chat_model=fake()) as bundle:
        tools = bundle.tools["validation"]
        tools["lookup_evidence"].invoke({"evidence_id": "wf"})
        for phrase in phrases:
            for field in ("summary", "blockers", "assumptions", "proposed_changes", "alternatives"):
                value = finding(role="validation", **{field: phrase if field == "summary" else [phrase]})
                result = tools["submit_finding"].invoke({"finding": value})
                assert result["recorded"], (phrase, field, result)


def test_genuine_xml_reference_gap_phrases_still_require_exact_diagnostics(tmp_path):
    phrases = [
        "Fields 'TEMPLATE_ID' and 'COUNTER' are ambiguous.",
        "Reference actual-missing is unresolved.",
        "XML mapping definition is missing.",
        "Session 'missing_session' is unresolved.",
        "Mapping missing_mapping is unresolved.",
        "Field `COUNTER` is not unique.",
        "Missing XML source reference 'missing_source'.",
        "Field 'COUNTER' is not found. Runtime child_job_id is unresolved.",
    ]
    with build_agents(tmp_path, diagnostic_model(), "wf", "bronze", chat_model=fake()) as bundle:
        tools = bundle.tools["validation"]
        tools["lookup_evidence"].invoke({"evidence_id": "wf"})
        for phrase in phrases:
            result = tools["submit_finding"].invoke({"finding": finding(role="validation", summary=phrase)})
            assert not result["recorded"] and result["error"] == "finding_reference_claim_unverified", (phrase, result)
        diagnostic = tools["graph_slice"].invoke({})["diagnostics"][1]
        scopes = [{"diagnostic_id": diagnostic["id"], "owner_id": "b",
                   "impact": "affected_task_blocker", "reason": "Exact missing mapping reference."}]
        value = finding(role="validation", summary="Reference actual-missing is unresolved. Runtime 'child_job_id' is unresolved.",
                        evidence_ids=[diagnostic["id"]], diagnostic_scopes=scopes)
        assert tools["submit_finding"].invoke({"finding": value})["recorded"]
        for phrase in ("Fields 'TEMPLATE_ID' and 'COUNTER' are ambiguous.", "Reference made-up is unresolved.",
                   "Mapping made_up_mapping is unresolved."):
            rejected = tools["submit_finding"].invoke({"finding": {**value, "summary": phrase}})
            assert not rejected["recorded"] and rejected["error"] == "finding_reference_claim_unverified"


def test_diagnostic_code_feedback_lists_only_role_retrieved_canonical_ids(tmp_path):
    with build_agents(tmp_path, diagnostic_model(), "wf", "bronze", chat_model=fake()) as bundle:
        validation = bundle.tools["validation"]
        rows = validation["graph_slice"].invoke({})["diagnostics"]
        value = finding(role="validation", evidence_ids=[rows[1]["id"]], diagnostic_scopes=[{
            "diagnostic_id": "UNRESOLVED_REFERENCE", "owner_id": "b", "impact": "affected_task_blocker",
            "reason": "PRIVATE_DIAGNOSTIC_REASON"}])
        for tool, flag in (("submit_finding", "recorded"), ("remember_finding", "remembered")):
            result = validation[tool].invoke({"finding": value})
            assert not result[flag]
            assert result["allowed_diagnostic_ids"] == sorted(row["id"] for row in rows)
            assert "PRIVATE_DIAGNOSTIC_REASON" not in json.dumps(result)
            assert "UNRESOLVED_REFERENCE" not in bundle.role_retrieved["validation"]
        value["diagnostic_scopes"][0]["diagnostic_id"] = rows[1]["id"]
        assert validation["submit_finding"].invoke({"finding": value})["recorded"]
        other = bundle.tools["discovery"]["submit_finding"].invoke({"finding": {**value, "role": "discovery"}})
        assert not other["recorded"] and other["allowed_diagnostic_ids"] == []
        assert other["retrieved_evidence_ids"] == []
        for change in ({"owner_id": "a"}, {"impact": "human_review"}):
            value["diagnostic_scopes"][0].update(owner_id="b", impact="affected_task_blocker")
            value["diagnostic_scopes"][0].update(change)
            assert not validation["submit_finding"].invoke({"finding": value})["recorded"]