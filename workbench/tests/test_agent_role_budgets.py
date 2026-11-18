"""Offline root regressions for public run 476545560, no live models or service."""
from copy import deepcopy
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from workbench import agents
from workbench.tests.test_redesign import ToolModel, chat, model, scripted_runtime, tool_call


def expand(value):
    """Independent RFC6901 decoder for proving exact response round trips."""
    root = value

    def visit(item):
        if isinstance(item, dict) and set(item) == {"$evidence_ref"}:
            target = root
            for key in item["$evidence_ref"][2:].split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                target = target[int(key)] if isinstance(target, list) else target[key]
            return visit(target)
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    return visit(value)


def test_compaction_preserves_exact_sql_java_citations_and_pointer_escaping():
    evidence = {"id": "a" * 64, "sql": "SELECT '\u03bb'\r\n" + "x" * 4000,
                "java": "if (counter >= 54) throw timeout;" * 20,
                "wait_mode": "nowait", "evidence_ids": ["b" * 64]}
    value = {"owner/~": evidence, "other": deepcopy(evidence), "rows": [evidence] * 12,
             "truncated": False, "total": 12, "approved": False}
    before = deepcopy(value)
    compact = agents._compact_evidence(value)
    assert expand(compact) == before == value
    assert compact["other"]["$evidence_ref"] == "#/owner~1~0"
    assert len(agents._json(compact)) < len(agents._json(value)) / 4


def test_request_only_compaction_keeps_checkpoint_messages_and_status():
    payload = json.dumps({"records": [{"sql": "x" * 3000}] * 8, "evidence_ids": ["a" * 64]})
    first = ToolMessage(content=payload, name="workflow_planning_contract", id="first",
                        tool_call_id="call1", status="success")
    second = ToolMessage(content=payload, name="workflow_planning_contract", id="second",
                         tool_call_id="call2", status="success")
    malformed = ToolMessage(content="not JSON", name="read_file", id="third", tool_call_id="call3", status="error")
    result = agents._compact_evidence_messages([first, second, malformed])
    assert first.content == second.content == payload
    assert expand(json.loads(result[0].content)) == json.loads(payload)
    assert json.loads(result[1].content) == {"$evidence_message_ref": "first", "sha256": sha256(payload.encode()).hexdigest()}
    assert result[1].tool_call_id == "call2" and result[2].status == "error"
    assert result[2].content == "not JSON"


def test_live_child_cross_message_subtrees_round_trip_without_losing_unique_evidence():
    shared = {"sql": "SELECT '\u03bb'\r\n" + "x" * 4000, "evidence_ids": ["a" * 64],
              "java": "poll_dependency_count();" * 80, "wait_mode": "nowait"}
    first = ToolMessage(content=json.dumps({"owner/~": shared}), name="source_contracts",
                        id="source", tool_call_id="source-call")
    second = ToolMessage(content=json.dumps({"mapping": shared, "unique": "KEEP_UNIQUE_SQL"}),
                         name="workflow_planning_contract", id="planning", tool_call_id="planning-call")
    result = agents._compact_evidence_messages([first, second])
    reference = json.loads(result[1].content)["mapping"]
    assert reference == {"$evidence_message_ref": "source", "pointer": "#/owner~1~0",
                         "sha256": sha256(agents._json(shared).encode()).hexdigest()}
    original = json.loads(first.content)["owner/~"]
    decoded = {"mapping": original, "unique": json.loads(result[1].content)["unique"]}
    assert decoded == json.loads(second.content)
    assert json.loads(first.content)["owner/~"] == shared
    assert len(result[1].content) < len(second.content) / 4


def test_role_time_boundary_reserves_completion_and_all_later_roles(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agents, "monotonic", lambda: clock[0])
    original = ToolModel._generate
    calls = []

    def generate(self, messages, **kwargs):
        envelope = next(json.loads(m.content) for m in messages if m.type == "human"
                        and '"contract_version"' in str(m.content))
        role = envelope["role"]
        completion = any('"structured_completion_pass"' in str(m.content) for m in messages)
        calls.append((role, completion, kwargs.get("timeout"), kwargs.get("max_tokens")))
        if not completion:
            # A slow synchronous call crosses investigation admission, but not its role end.
            seconds = agents._ROLE_SECONDS[role]
            clock[0] += seconds - min(90, seconds * 0.4) + 0.1
        else:
            clock[0] += 1
        return original(self, messages, **kwargs)

    monkeypatch.setattr(ToolModel, "_generate", generate)
    monkeypatch.setattr(ToolModel, "bind_tools", lambda self, tools, **kwargs: self.bind(**kwargs))
    routes = {role: chat([AIMessage(content="Investigation stopped"),
        tool_call("submit_finding", {"finding": {"role": role, "summary": "Limited captured analysis",
                  "status": "needs_information"}}, role + "-finding"), AIMessage(content="Recorded")]) for role in agents.ROLES}
    result = agents.AgentRuntime(tmp_path, role_models=routes).run(model(), "wf", "original", "Propose design", [], [])
    assert len(result["findings"]) == 6
    assert [r["role"] for r in result["role_completion"]] == [*agents.ROLES[1:], "supervisor"]
    assert all(r["completion_pass"] and r["normal_call_limit_reached"] for r in result["role_completion"])
    assert all(r["budget_seconds"] == agents._ROLE_SECONDS[r["role"]] for r in result["role_completion"])
    assert all(0 < timeout <= 60 and cap in {4096, 8192} for _, _, timeout, cap in calls)
    assert all(r["completion_model_calls"] == 2 for r in result["role_completion"])
    assert clock[0] < 900 and not result["approved"] and not result["generation_allowed"]


def test_normal_tools_cannot_spend_completion_or_later_role_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "monotonic", lambda: 0.0)
    with agents.build_agents(tmp_path, model(), "wf", "original", "Propose design", chat_model=chat()) as bundle:
        budget = bundle.contexts["discovery"]["budget"]
        budget.begin(["semantics", "architecture", "validation", "artifact", "supervisor"])
        for _ in range(12):
            budget.check_tool("package_summary")
        with pytest.raises(agents.RoleBudgetExceeded):
            budget.check_tool("package_summary")
        bundle.completion_roles.add("discovery")
        for _ in range(4):
            budget.check_tool("submit_finding")
        with pytest.raises(TimeoutError):
            budget.check_tool("submit_finding")
        bundle.contexts["supervisor"]["budget"].check_tool("package_summary")
        assert budget.allocation.tool_calls == 16


def test_slow_tool_is_charged_before_next_model_and_does_not_run_in_orphan_thread(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agents, "monotonic", lambda: clock[0])
    with agents.build_agents(tmp_path, model(), "wf", "original", "Propose design", chat_model=chat()) as bundle:
        budget = bundle.contexts["discovery"]["budget"]
        allocation = budget.begin()
        budget.check_tool("package_summary")
        clock[0] = allocation.normal_deadline + 1
        with pytest.raises(agents.RoleBudgetExceeded):
            allocation.check()
        assert 0 < allocation.request_timeout(True, 2) < allocation.deadline - clock[0]
        clock[0] = allocation.deadline + 1
        with pytest.raises(TimeoutError):
            allocation.request_timeout(True)


def test_live_cohort_completed_tool_receipt_survives_investigation_overrun(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agents, "monotonic", lambda: clock[0])
    with agents.build_agents(tmp_path, model(), "wf", "original", "Propose design", chat_model=chat()) as bundle:
        budget = bundle.contexts["discovery"]["budget"]
        allocation = budget.begin()
        request = SimpleNamespace(tool_call={"name": "package_summary", "args": {}, "id": "completed"})
        receipt = ToolMessage(content='{"captured":true}', name="package_summary", tool_call_id="completed")
        def slow_handler(request):
            clock[0] = allocation.normal_deadline + 1
            return receipt
        assert budget.wrap_tool_call(request, slow_handler) is receipt
        assert allocation.tool_calls == 1
        with pytest.raises(agents.RoleBudgetExceeded):
            budget.before_model({}, None)
        bundle.completion_roles.add("discovery")
        budget.before_model({}, None)
        assert allocation.request_timeout(True) > 0


def test_live_cohort_denied_tool_has_receipt_without_running_or_spending_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "monotonic", lambda: 0.0)
    with agents.build_agents(tmp_path, model(), "wf", "original", "Propose design", chat_model=chat()) as bundle:
        budget = bundle.contexts["discovery"]["budget"]
        allocation = budget.begin()
        for _ in range(12):
            budget.check_tool("package_summary")
        request = SimpleNamespace(tool_call={"name": "package_summary", "args": {}, "id": "denied"})
        def forbidden_handler(request):
            pytest.fail("An investigation-denied tool must never execute")
        receipt = budget.wrap_tool_call(request, forbidden_handler)
        assert receipt.status == "error" and receipt.tool_call_id == "denied"
        assert json.loads(receipt.content)["error"] == "role_investigation_budget_exhausted"
        assert allocation.tool_calls == 12
        bundle.completion_roles.add("discovery")
        for _ in range(4):
            budget.check_tool("submit_finding")
        with pytest.raises(TimeoutError):
            budget.check_tool("submit_finding")


def test_actual_sequential_design_handoff_preserves_citation_ownership(tmp_path):
    runtime, routes = scripted_runtime(tmp_path, model(), "wf")
    result = runtime.run(model(), "wf", "original", "Propose redesign", [], [])
    assert result["status"] == "draft_available" and len(result["redesigns"]) == 1
    assert result["redesigns"][0]["role"] == "architecture"
    assert result["redesigns"][0]["retrieved_evidence_ids"] == ["a", "b"]
    for role in ("validation", "artifact", "supervisor"):
        envelope = next(json.loads(m.content) for m in routes[role]._seen[0]
                        if m.type == "human" and '"contract_version"' in str(m.content))
        assert envelope["existing_design_count"] == 1
        assert any(f["role"] == "architecture" for f in envelope["handoffs"])
    assert not result["approved"] and not result["deployment_allowed"]


def test_no_submission_timeout_summary_never_claims_validator_rejection(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("private timeout")
    monkeypatch.setattr(ToolModel, "_generate", fail)
    result = agents.AgentRuntime(tmp_path, role_models={r: chat() for r in agents.ROLES}).run(
        model(), "wf", "original", "Propose design", [], [])
    assert result["design_submission"]["rejections"] == []
    assert "not submitted due to role timeouts" in result["summary"]
    assert "No validator rejection was recorded" in result["summary"]
    assert "private timeout" not in json.dumps(result)
    assert all(r["model_calls"] == 1 and not r["completion_pass"] for r in result["role_completion"])


def test_review_only_keeps_unmodified_model_request_accounting(tmp_path, monkeypatch):
    original = ToolModel._generate
    requests = []
    def generate(self, messages, **kwargs):
        requests.append(kwargs)
        return original(self, messages, **kwargs)
    monkeypatch.setattr(ToolModel, "_generate", generate)
    result = agents.AgentRuntime(tmp_path, role_models={r: chat() for r in agents.ROLES}).run(
        model(), "wf", "original", "Review captured behavior", [], [])
    assert len(requests) == 6
    assert all("timeout" not in request and "max_tokens" not in request for request in requests)
    assert all("budget_seconds" not in r and not r["completion_pass"] for r in result["role_completion"])
    assert result["design_submission"]["status"] == "not_requested"