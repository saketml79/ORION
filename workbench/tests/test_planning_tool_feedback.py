"""Offline regressions for recoverable planning pagination in real Deep Agents."""
from collections import Counter
from copy import deepcopy
import json
import socket

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
import pytest

from workbench.agents import RECURSION_LIMIT, ROLES, build_agents
from workbench.migration_planning import PlanningIndex
from workbench.tests.test_agents import OfflineModel, fake
from workbench.tests.test_redesign import model


SECTIONS = {"overview", "tasks", "control_links", "definitions", "mapping_fields",
            "mapping_links", "behavior", "properties", "connections", "parameters",
            "embedded_code", "invocations"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("WORKBENCH_MODEL", "")
    monkeypatch.setenv("WORKBENCH_LANGSMITH_ENABLED", "0")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def source():
    value = model()
    value["nodes"][1]["fields"] = [
        {"kind": "transformfield", "attributes": {"NAME": f"port_{i}", "EXPRESSION": str(i)}}
        for i in range(25)]
    return value


@pytest.mark.parametrize("role", ROLES)
def test_all_roles_invalid_pages_before_index_or_receipt_then_valid_20(tmp_path, source, role, monkeypatch):
    with build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        planning_tool = bundle.tools[role]["workflow_planning_contract"]
        original_init = PlanningIndex.__init__
        constructed = []

        def tracked_init(self, value):
            constructed.append(True)
            original_init(self, value)

        monkeypatch.setattr(PlanningIndex, "__init__", tracked_init)
        requests = [{"section": "mapping_fields", "limit": limit} for limit in (0, 21, 50, 100)]
        requests += [{"section": "unknown", "limit": 20},
                     {"section": "tasks", "offset": -1, "limit": 20},
                     {"section": "invocations", "limit": 50, "related_workflow_id": "outside"}]
        for request in requests:
            original_request = deepcopy(request)
            feedback = planning_tool.invoke(request)
            assert request == original_request
            assert feedback["status"] == "invalid_page_request" and feedback["recoverable"]
            assert feedback["evidence_ids"] == []
            assert not feedback["generation_allowed"] and not feedback["deployment_allowed"]
            assert set(feedback["supported_sections"]) == SECTIONS
            assert feedback["bounds"] == {"limit": {"minimum": 1, "maximum": 20},
                                          "offset": {"minimum": 0, "maximum": None}}
            retry = feedback["retry_args"]
            assert retry["section"] in SECTIONS and 1 <= retry["limit"] <= 20 and retry["offset"] >= 0
            assert retry["related_workflow_id"] == request.get("related_workflow_id", "")
            for field in ("section", "offset", "limit"):
                if field not in {issue["field"] for issue in feedback["validation_issues"]}:
                    assert retry[field] == request.get(field, {"section": "overview", "offset": 0, "limit": 12}[field])
            assert constructed == []
            assert bundle.planning_retrievals == []
            assert all(not seen for seen in bundle.role_retrieved.values())
            assert bundle.findings == []

        # A rejected invocation page cannot grant related-workflow access.
        assert planning_tool.invoke({"section": "tasks", "related_workflow_id": "outside"}) == {
            "status": "related_workflow_not_retrieved"}
        assert constructed == [True]
        assert bundle.planning_retrievals == [] and not bundle.role_retrieved[role]
        page = planning_tool.invoke({"section": "mapping_fields", "limit": 20})
        assert page == PlanningIndex(source).agent_contract("wf", "mapping_fields", limit=20)
        assert page["returned"] == 20 and page["total"] == 25 and page["next_offset"] == 20
        assert bundle.role_retrieved[role] == set(page["evidence_ids"]) == {"a"}
        assert all(not seen for other, seen in bundle.role_retrieved.items() if other != role)
        assert len(bundle.planning_retrievals) == 1
        receipt = bundle.planning_retrievals[0]
        assert receipt["role"] == role and receipt["limit"] == 20 and receipt["offset"] == 0
        assert receipt["evidence_ids"] == page["evidence_ids"]
        assert "1..20" in planning_tool.description and "0..unbounded" in planning_tool.description
        assert all(section in planning_tool.description for section in SECTIONS)
        assert "1..200" in bundle.tools[role]["package_migration_contract"].description


def test_actual_offset_has_no_invented_upper_cap_and_sections_match_index(tmp_path, source):
    with build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        planning_tool = bundle.tools["discovery"]["workflow_planning_contract"]
        feedback = planning_tool.invoke({"section": "not_supported"})
        overview = planning_tool.invoke(feedback["retry_args"])
        assert set(overview["sections"]) == set(feedback["supported_sections"]) == SECTIONS
        index = PlanningIndex(source)
        for section in SECTIONS:
            request = {"section": section, "offset": 100001, "limit": 20}
            assert planning_tool.invoke(request) == index.agent_contract(
                "wf", section, offset=100001, limit=20)


@pytest.mark.parametrize("invalid_request", [{"limit": True}, {"limit": "50"}, {"limit": 1.5},
                                            {"offset": False}, {"offset": "0"}, {"offset": 1.5},
                                            {"section": []}])
def test_manual_validation_without_schema_coercion(tmp_path, source, invalid_request, monkeypatch):
    # Wrong-type tool.invoke requests remain subject to the existing Pydantic schema.
    # Direct wrapper calls verify the internal strict type checks without changing it.
    with build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        def forbidden_init(*args, **kwargs):
            raise AssertionError("Invalid request constructed a planning index")

        monkeypatch.setattr(PlanningIndex, "__init__", forbidden_init)
        result = bundle.tools["discovery"]["workflow_planning_contract"].func(**invalid_request)
        assert result["status"] == "invalid_page_request"
        assert result["evidence_ids"] == [] and bundle.planning_retrievals == []
        assert not bundle.role_retrieved["discovery"]


@pytest.mark.parametrize("failure", [ValueError("unexpected retrieval failure"),
                                   RuntimeError("operational retrieval failure")])
def test_unexpected_retrieval_errors_are_not_swallowed(tmp_path, source, monkeypatch, failure):
    with build_agents(tmp_path, source, "wf", "original", chat_model=fake()) as bundle:
        def fail(*args, **kwargs):
            raise failure

        monkeypatch.setattr(PlanningIndex, "agent_contract", fail)
        with pytest.raises(type(failure), match=str(failure)):
            bundle.tools["discovery"]["workflow_planning_contract"].invoke({"limit": 20})
        assert bundle.planning_retrievals == [] and not bundle.role_retrieved["discovery"]


def test_internal_planning_index_remains_strict(source):
    index = PlanningIndex(source)
    for limit in (0, 21, 50, 100, True, "20", 1.5):
        with pytest.raises(ValueError, match="limit must be an integer"):
            index.agent_contract("wf", "tasks", limit=limit)
    with pytest.raises(ValueError, match="offset must be an integer"):
        index.agent_contract("wf", "tasks", offset=-1)
    with pytest.raises(ValueError, match="Unknown planning section"):
        index.agent_contract("wf", "unknown")


class PlanningFeedbackModel(OfflineModel):
    """Choose the retry and finding from actual returned tool feedback/evidence."""
    role: str = "discovery"
    _step: int = PrivateAttr(default=0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(messages)
        step = self._step
        self._step += 1
        if step == 0:
            name, args = "workflow_planning_contract", {"section": "mapping_fields", "limit": 50}
        elif step == 1:
            feedback = json.loads(next(m.content for m in reversed(messages)
                                       if isinstance(m, ToolMessage)))
            assert feedback["status"] == "invalid_page_request" and feedback["evidence_ids"] == []
            assert feedback["retry_args"]["limit"] == 20
            name, args = "workflow_planning_contract", feedback["retry_args"]
        elif step == 2:
            page = json.loads(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))
            assert page["returned"] == 20 and page["has_more"]
            name, args = "submit_finding", {"finding": {
                "role": self.role, "summary": "Captured field page inspected; remaining fields unverified.",
                "status": "supported", "evidence_ids": page["evidence_ids"]}}
        else:
            assert step == 3, "Unexpected extra model loop"
            receipt = json.loads(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))
            assert receipt["recorded"] is True
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Advisory finding submitted."))])
        response = AIMessage(content="", tool_calls=[{
            "id": self.role + str(step), "name": name, "args": args}])
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.mark.parametrize("role", ROLES)
def test_real_compiled_role_recovers_50_to_20_then_submits_with_exact_counts(tmp_path, source, role):
    route = PlanningFeedbackModel(role=role, responses=[AIMessage(content="Unused scripted response")],
                                 custom_get_token_ids=lambda text: list(range(len(text) // 4)))
    with build_agents(tmp_path, source, "wf", "original", chat_model=route) as bundle:
        graph = bundle.supervisor if role == "supervisor" else bundle.specialists[role]
        result = graph.invoke({"messages": [{"role": "user", "content": "Inspect the field page and submit a finding."}]},
                              {"configurable": {"thread_id": "planning-feedback-" + role}, "recursion_limit": RECURSION_LIMIT})
        tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        assert [message.name for message in tools] == [
            "workflow_planning_contract", "workflow_planning_contract", "submit_finding"]
        assert all(message.status == "success" for message in tools)
        assert json.loads(tools[0].content)["status"] == "invalid_page_request"
        assert json.loads(tools[1].content)["returned"] == 20
        assert json.loads(tools[2].content)["recorded"] is True
        assert len(route._seen) == 4 and not bundle.completion_roles
        assert len(bundle.findings) == 1 and bundle.findings[0]["status"] == "supported"
        assert bundle.findings[0]["evidence_ids"] == ["a"]
        assert len(bundle.planning_retrievals) == 1 and bundle.planning_retrievals[0]["limit"] == 20
        assert bundle.role_retrieved[role] == {"a"}
        assert all(not seen for other, seen in bundle.role_retrieved.items() if other != role)
        usage = bundle.ledger.usage(bundle.run_id)
        assert usage["failed_calls"] == 0 and usage["unreported_calls"] == 4
        with bundle.ledger.connect() as db:
            counts = Counter(event for (event,) in db.execute(
                "SELECT event FROM agent_usage WHERE run_id=? AND role=?", (bundle.run_id, role)))
        assert counts["model_started"] == counts["model_completed"] == 4
        assert counts["tool_started"] == counts["tool_completed"] == 3
        assert counts["model_failed"] == counts["tool_failed"] == 0