"""Offline logical redesign tool, HTTP integration, immutable storage and review regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Event
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr, ValidationError
import pytest

from workbench.agents import AgentRuntime, ROLES, _Evidence, _hash, build_agents
from workbench.app import _create_package_app
from workbench.redesign import (
    Conflict, Design, DesignQualityError, RedesignStore, Review, capture_run, store_for,
    validate_submission,
)


class ToolModel(FakeMessagesListChatModel):
    _seen: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def chat(responses=None):
    return ToolModel(responses=responses or [AIMessage(content="Advisory only.")],
                     custom_get_token_ids=lambda text: list(range(len(text) // 4)))


def model():
    def node(identifier, kind, parent=None, **attributes):
        return {"id": identifier, "kind": kind, "name": identifier, "parent_id": parent,
                "scope": {}, "attributes": attributes, "evidence": {"locator": "/" + identifier},
                "fields": [], "properties": []}
    nodes = [node("wf", "workflow"), node("a", "taskinstance", "wf"),
             node("b", "taskinstance", "wf", ISENABLED="NO"), node("outside", "workflow"),
             node("nested", "taskinstance", "outside")]
    edges = [{"id": "edge-" + n["id"], "source": "wf", "target": n["id"],
              "kind": "contains", "attributes": {}, "evidence": n["evidence"]}
             for n in nodes if n["parent_id"] == "wf"]
    return {"package": {"sha256": "package"}, "nodes": nodes, "edges": edges,
            "diagnostics": [], "coverage": {}}


def design_for(source=None, workflow_id="wf", blockers=None):
    source = source or model()
    originals = [n for n in source["nodes"] if n["kind"] == "taskinstance" and n.get("parent_id") == workflow_id]
    refs = [n["id"] for n in originals]
    removed_start = {n["id"] for n in originals
                     if (n.get("attributes", {}).get("TASKTYPE", "").casefold() == "start"
                         and n.get("attributes", {}).get("ISENABLED") != "NO")}
    start_ids = {n["id"] for n in originals
                 if n.get("attributes", {}).get("TASKTYPE", "").casefold() == "start"}
    return {"title": "Evidence-backed logical consolidation", "tasks": [
        {"id": "logical-load", "label": "Load", "implementation_kind": "pipeline_task",
         "purpose": "Preserve active loading behavior; keep disabled tasks disabled", "evidence_ids": refs}],
        "edges": [], "rationale": "A reusable outline preserves captured behavior without physical bindings.",
        "alternatives": [{"label": "Keep separate tasks", "rationale": "Minimize orchestration change",
                          "tradeoffs": ["More repeated configuration"], "evidence_ids": refs}],
        "coverage": [{"original_task_id": n["id"],
                      "proposed_task_ids": [] if n["id"] in start_ids else ["logical-load"],
                      "disposition": "removed" if n["id"] in removed_start else
                                     "disabled" if n["attributes"].get("ISENABLED") == "NO" else "mapped",
                      "rationale": "Disabled behavior retained" if n["attributes"].get("ISENABLED") == "NO" else "Loading behavior retained",
                      "evidence_ids": [n["id"]]} for n in originals],
                "artifact_plan": [{"relative_path": "notebooks/load.ipynb", "artifact_kind": "notebook",
                           "proposed_task_ids": ["logical-load"], "mapping_ids": [],
                           "description": "Implement the supported task behavior with explicit input parameters.",
                           "evidence_ids": refs}],
        "evidence_ids": refs,
        "reuse_groups": [{"id": "reuse-load", "proposed_task_ids": ["logical-load"],
                          "rationale": "Parameterize the shared outline", "evidence_ids": refs}],
        "parameters": [{"name": "logical_source", "purpose": "Source identity to be reviewed",
                        "required": True, "value_type": "string"}],
        "proposed_constraints": ["Preserve disabled behavior and require independent parity review"],
        "blockers": blockers or []}


def validate(value):
    return validate_submission(value, model(), "wf", {"a", "b"}, _Evidence(model(), "wf", [], []).records)


def tool_call(name, args, identifier):
    return AIMessage(content="", tool_calls=[{"id": identifier, "name": name, "args": args}])


def scripted_runtime(path, source, workflow_id, value=None):
    value = value or design_for(source, workflow_id)
    refs = value["evidence_ids"]
    finding = lambda role: {"role": role, "summary": "Captured behavior reviewed", "evidence_ids": refs,
                            "assumptions": [], "blockers": [], "proposed_changes": [],
                            "alternatives": [], "status": "supported"}
    routes = {}
    for role in ROLES:
        responses = [tool_call("lookup_evidence", {"evidence_id": ref}, role + str(i)) for i, ref in enumerate(refs)]
        if role == "architecture":
            responses.append(tool_call("submit_redesign", {"design": value}, "submit-design"))
        responses += [tool_call("submit_finding", {"finding": finding(role)}, role + "finding"), AIMessage(content="Done.")]
        routes[role] = chat(responses)
    return AgentRuntime(path, role_models=routes), routes


def test_strict_design_and_complete_direct_disabled_coverage():
    value = validate(design_for())
    assert {item.original_task_id for item in value.coverage} == {"a", "b"}
    assert value.coverage[1].disposition == "disabled"
    assert "nested" not in {item.original_task_id for item in value.coverage}
    with pytest.raises(ValidationError):
        Design.model_validate({**design_for(), "approved": True})
    value = design_for()
    value["tasks"][0]["code"] = "arbitrary body"
    with pytest.raises(ValidationError):
        validate(value)


@pytest.mark.parametrize("purpose", [
    "Target catalog derived from source variable $$AE2E_SCHEMA. Required, no default.",
    "Target catalog derived from schema variable $$AE2E_SCHEMA.",
])
def test_target_catalog_cannot_be_derived_from_schema_variable(purpose):
    value = design_for()
    value["parameters"].append({"name": "target_catalog", "purpose": purpose,
                                 "required": True, "value_type": "string"})
    with pytest.raises(DesignQualityError) as error:
        validate(value)
    assert any(issue["field"] == "parameters.target_catalog.purpose"
               and "cannot be derived from a schema variable" in issue["issue"]
               for issue in error.value.issues)


def test_target_schema_may_be_described_by_schema_variable():
    value = design_for()
    value["parameters"].append({"name": "target_schema", "purpose": "Target schema from $$AE2E_SCHEMA.",
                                 "required": True, "value_type": "string"})
    assert validate(value).parameters[-1].name == "target_schema"


@pytest.mark.parametrize("mutation", ["missing", "disabled", "invented", "unretrieved", "out_of_scope",
                                     "endpoint", "cycle", "duplicate", "sql", "script", "reuse"])
def test_invalid_submissions_are_rejected(mutation):
    value = design_for()
    if mutation == "missing":
        value["coverage"].pop()
    elif mutation == "disabled":
        value["coverage"][1]["disposition"] = "mapped"
    elif mutation == "invented":
        value["evidence_ids"].append("forged")
    elif mutation == "unretrieved":
        value["evidence_ids"].append("wf")
    elif mutation == "out_of_scope":
        value["evidence_ids"].append("outside")
    elif mutation in {"endpoint", "cycle"}:
        value["edges"] = [{"source": "logical-load", "target": "missing" if mutation == "endpoint" else "logical-load",
                           "evidence_ids": ["a"]}]
    elif mutation == "duplicate":
        value["tasks"].append(deepcopy(value["tasks"][0]))
    elif mutation == "sql":
        value["rationale"] = "SELECT id FROM production"
    elif mutation == "script":
        value["tasks"][0]["purpose"] = "```python\nprint('not allowed')\n```"
    elif mutation == "reuse":
        value["reuse_groups"][0]["proposed_task_ids"] = ["missing"]
    with pytest.raises(ValueError):
        validate(value)


def test_conditional_dag_attributes_and_supported_kinds():
    value = design_for()
    value["tasks"][0]["implementation_kind"] = "condition_task"
    value["tasks"].append({**value["tasks"][0], "id": "branch", "implementation_kind": "sql_task"})
    value["coverage"][0]["proposed_task_ids"].append("branch")
    value["edges"] = [{"source": "logical-load", "target": "branch", "condition": "Original success predicate",
                       "outcome": "true", "run_if": "ALL_SUCCESS", "evidence_ids": ["a"]}]
    assert validate(value).edges[0].outcome == "true"
    value["tasks"][0]["implementation_kind"] = "unsupported"
    with pytest.raises(ValidationError):
        validate(value)


def test_actual_compiled_harness_tool_and_runtime_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda *a, **k: pytest.fail("No provider call"))
    runtime, routes = scripted_runtime(tmp_path, model(), "wf")
    result = runtime.run(model(), "wf", "original", "Propose a redesign", [], [])
    assert result["status"] == "draft_available", result
    assert len(result["redesigns"]) == 1
    assert result["redesigns"][0]["role"] == "architecture"
    assert result["redesigns"][0]["retrieved_evidence_ids"] == ["a", "b"]
    assert len(result["input_hashes"]["model"]) == 64
    assert not result["approved"] and not result["generation_allowed"] and not result["deployment_allowed"]
    messages = routes["architecture"]._seen[-1]
    assert "submit_redesign" in str(messages[0].content)
    assert any('"redesign_contract"' in str(m.content) for m in messages)
    assert any(m.type == "tool" and json.loads(m.content).get("status") == "draft" for m in messages)


def test_only_architecture_supervisor_receive_tool_and_bad_tool_does_not_append(tmp_path):
    with build_agents(tmp_path, model(), "wf", "original", chat_model=chat()) as bundle:
        for role in ROLES:
            assert ("submit_redesign" in bundle.tools[role]) == (role in {"architecture", "supervisor"})
        tool = bundle.tools["architecture"]["submit_redesign"]
        rejected = tool.invoke({"design": design_for()})
        assert rejected["recorded"] is False and rejected["error"] == "design_evidence_not_retrieved"
        assert bundle.redesigns == []
        for ref in ("a", "b"):
            bundle.tools["architecture"]["lookup_evidence"].invoke({"evidence_id": ref})
        accepted = tool.invoke({"design": design_for()})
        assert accepted["recorded"] is True
        assert len(bundle.redesigns) == 1
        # Another role cannot reuse architecture's retrieval set.
        rejected = bundle.tools["supervisor"]["submit_redesign"].invoke({"design": design_for()})
        assert rejected["recorded"] is False and rejected["error"] == "design_evidence_not_retrieved"


def source_record():
    return {"record_id": "record", "runtime_run_id": "a" * 32, "submission_index": 0,
            "kind": "agent", "input_version": "b" * 64, "base_input_version": "b" * 64,
            "input_hashes": {"policy": "c" * 64}, "input_versions": {}, "runtime_input_hashes": {}}


def submission(blockers=None):
    return {"role": "architecture", "design": Design.model_validate(design_for(blockers=blockers)).model_dump(),
            "retrieved_evidence_ids": ["a", "b"]}


def test_immutable_versions_restart_idempotency_scope_and_expected_version(tmp_path):
    path = tmp_path / "state" / "redesigns.sqlite"
    store = RedesignStore(path)
    first = store.append("p", "wf", "original", submission(), source_record(), 0)
    assert first["version"] == 1
    assert store.append("p", "wf", "original", submission(), source_record(), 0) == first
    second_source = {**source_record(), "runtime_run_id": "d" * 32}
    with pytest.raises(Conflict):
        store.append("p", "wf", "original", submission(), second_source, 0)
    second = store.append("p", "wf", "original", submission(), second_source, 1)
    assert second["version"] == 2
    reopened = RedesignStore(path)
    assert reopened.get(first["id"], "p", 1)["design"] == first["design"]
    assert reopened.list("other") == []
    assert reopened.list("p", scenario="bronze") == []
    with pytest.raises(KeyError):
        reopened.get(first["id"], "other")
    with sqlite3.connect(path) as db:
        for statement in ("DELETE FROM redesign_versions", "UPDATE redesign_versions SET version=4"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)


def review_body(**kwargs):
    return Review(action="approve_for_drafting", comment="Explicit outline-only review",
                  expected_version=1, expected_input_version="b" * 64, consent=True, **kwargs)


def test_review_consent_blockers_stale_cas_append_only_and_outline_only(tmp_path):
    store = RedesignStore(tmp_path / "redesigns.sqlite")
    row = store.append("p", "wf", "original", submission(), source_record(), 0)
    actor = {"actor": "local-reviewer", "actor_authenticated": False}
    body = review_body()
    with pytest.raises(PermissionError):
        store.review(row["id"], "p", body.model_copy(update={"consent": False}), "b" * 64, actor)
    with pytest.raises(Conflict, match="Current input"):
        store.review(row["id"], "p", body, "c" * 64, actor)
    with pytest.raises(Conflict, match="Stale"):
        store.review(row["id"], "p", body.model_copy(update={"expected_input_version": "c" * 64}), "c" * 64, actor)
    event = store.review(row["id"], "p", body, "b" * 64, actor)
    assert event["action"] == "approve_for_drafting" and event["review_version"] == 1
    current = store.get(row["id"], "p")
    assert current["outline_drafting_allowed"]
    assert not current["approved"] and not current["generation_allowed"] and not current["deployment_allowed"]
    with pytest.raises(Conflict, match="review version"):
        store.review(row["id"], "p", body, "b" * 64, actor)
    for number, action in enumerate(("comment", "request_changes", "reject"), 1):
        next_body = body.model_copy(update={"action": action, "expected_review_version": number})
        store.review(row["id"], "p", next_body, "b" * 64, actor)
    assert not store.get(row["id"], "p")["outline_drafting_allowed"]
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM redesign_reviews")
    blocked = store.append("p", "wf", "bronze", submission(["Parity is unverified"]), source_record() | {"record_id": "blocked"}, 0)
    with pytest.raises(Conflict, match="blockers"):
        store.review(blocked["id"], "p", body, "b" * 64, actor)


@pytest.mark.parametrize('origin', [None, 'agent_submitted', 'deterministic_workflow_compiler'])
def test_capture_never_converts_free_text_and_rejects_bad_receipts(tmp_path, origin):
    service = SimpleNamespace(state=tmp_path)
    index = SimpleNamespace(model=model())
    record = {"id": "record", "workflow_id": "wf", "scenario": "original", "package_hash": "package",
              "consent": True, "input_version": "b" * 64, "input_hashes": {}, "input_versions": {},
              "redesign_snapshot": {"documents": [], "reviews": []}}
    assert capture_run(service, index, record, {"summary": "Please redesign", "findings": [{"proposed_changes": ["new task"]}]}) == []
    output = {"status": "advisory", "run_id": "a" * 32, "redesigns": [submission()], "input_hashes": {"model": _hash(model())}}
    if origin is not None:
        output['redesigns'][0]['design_origin'] = origin
    assert capture_run(service, index, record, {**output, "status": "failed"}) == []
    for extra in ({'design_origin': 'invented'}, {'approved': True}):
        invalid = deepcopy(output)
        invalid['redesigns'][0].update(extra)
        with pytest.raises(ValueError, match='Invalid redesign tool submission receipt'):
            capture_run(service, index, record, invalid)
    bad = deepcopy(output)
    bad["redesigns"][0]["design"]["evidence_ids"].append("forged")
    with pytest.raises(ValueError):
        capture_run(service, index, record, bad)
    saved = capture_run(service, index, record, output)
    assert len(saved) == 1 and saved[0]["source"]["runtime_run_id"] == "a" * 32
    assert saved[0]["source"]["runtime_input_hashes"]["model"] == _hash(model())
    assert saved[0]['source'].get('design_origin') == origin


HEADERS = {"X-Workbench-Request": "1"}


def wait(client, path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = client.get(path).json()
        if result["status"] in {"completed", "partial", "failed", "cancelled", "canceled"}:
            return result
        Event().wait(.01)
    pytest.fail("Worker did not finish")


@pytest.fixture
def api(tmp_path, monkeypatch):
    source = tmp_path / "sample.xml"
    source.write_text('<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F"><WORKFLOW NAME="W">'
                      '<TASKINSTANCE NAME="A" TASKTYPE="Start"/>'
                      '<TASKINSTANCE NAME="B" TASKTYPE="Start" ISENABLED="NO"/>'
                      '<TASKINSTANCE NAME="C" TASKTYPE="Session"/>'
                      '</WORKFLOW></FOLDER></REPOSITORY></POWERMART>', encoding="utf-8")
    app = _create_package_app(tmp_path / "data", source)
    monkeypatch.setattr("workbench.model_access.workspace_ready", lambda runtime: True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        response = client.post("/api/packages/sample", headers=HEADERS)
        assert wait(client, "/api/jobs/" + response.json()["id"])["status"] == "completed"
        index = app.state.service.active
        workflow_id = next(n["id"] for n in index.model["nodes"] if n["kind"] == "workflow")
        runtime, _ = scripted_runtime(tmp_path / "runtime", index.model, workflow_id)
        app.state.service.runtime = runtime
        yield client, app, workflow_id


def launch(client, workflow_id):
    response = client.post("/api/agents/run", headers=HEADERS,
                           json={"workflow_id": workflow_id, "scenario": "original", "prompt": "Redesign", "consent": True})
    assert response.status_code == 202, response.text
    result = wait(client, "/api/agents/runs/" + response.json()["id"])
    assert result["status"] == "completed", json.dumps(result, indent=2)
    return result


def test_http_real_runtime_capture_versions_review_and_staleness(api):
    client, app, workflow_id = api
    assert client.get("/api/redesigns").json()["items"] == []
    denied = client.post("/api/agents/run", headers=HEADERS, json={"workflow_id": workflow_id})
    assert denied.status_code == 403
    run = launch(client, workflow_id)
    assert len(run["redesign_refs"]) == 1
    identifier = run["redesign_refs"][0]["id"]
    listing = client.get("/api/redesigns", params={"scenario": "original", "workflow_id": workflow_id}).json()
    assert listing["total"] == 1
    assert client.get("/api/redesigns?scenario=bronze").json()["total"] == 0
    row = client.get("/api/redesigns/" + identifier).json()
    assert row["status"] == "draft" and not row["stale"] and row["reviews"] == []
    assert row["source"]["record_id"] == run["id"]
    assert row['source']['design_origin'] == 'agent_submitted'
    assert row["input_version"] == app.state.service.agent_inputs(app.state.service.active, workflow_id, "original")["input_version"]
    body = {"action": "approve_for_drafting", "comment": "Outline only", "expected_version": 1,
            "expected_input_version": row["current_input_version"], "consent": True}
    assert client.post(f"/api/redesigns/{identifier}/reviews", json=body).status_code == 403
    assert client.post(f"/api/redesigns/{identifier}/reviews", headers=HEADERS, json={**body, "actor_authenticated": True}).status_code == 422
    assert client.post(f"/api/redesigns/{identifier}/reviews", headers=HEADERS, json={**body, "consent": False}).status_code == 403
    response = client.post(f"/api/redesigns/{identifier}/reviews", headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    reviewed = response.json()["redesign"]
    assert reviewed["outline_drafting_allowed"] and not reviewed["approved"]
    assert not reviewed["reviews"][0]["actor_authenticated"]
    assert client.post(f"/api/redesigns/{identifier}/reviews", headers=HEADERS, json=body).status_code == 409
    # A scoped supporting document changes the same service-defined freshness hash.
    response = client.post("/api/documents", headers=HEADERS,
                           files={"file": ("review.md", b"Additional behavior evidence", "text/markdown")},
                           data={"workflow_id": workflow_id})
    assert response.status_code == 201, response.text
    stale = client.get("/api/redesigns/" + identifier).json()
    assert stale["stale"] and not stale["outline_drafting_allowed"]
    stale_body = {**body, "expected_review_version": 1, "expected_input_version": stale["current_input_version"]}
    assert client.post(f"/api/redesigns/{identifier}/reviews", headers=HEADERS, json=stale_body).status_code == 409
    # Re-run with newly captured inputs creates an immutable second version.
    runtime, _ = scripted_runtime(app.state.service.root / "runtime2", app.state.service.active.model, workflow_id)
    app.state.service.runtime = runtime
    second = launch(client, workflow_id)
    assert second["redesign_refs"] == [{"id": identifier, "version": 2}]
    assert client.get("/api/redesigns").json()["total"] == 2
    assert client.get(f"/api/redesigns/{identifier}?version=1").json()["version"] == 1
    assert client.get("/api/redesigns/" + "0" * 64).status_code == 404


def test_delete_exact_design_version_and_generated_writer_files(api):
    client, app, workflow_id = api
    run = launch(client, workflow_id)
    identifier = run["redesign_refs"][0]["id"]
    record = client.get(f"/api/redesigns/{identifier}?version=1").json()
    package_hash = record["package_hash"]
    design_version = record["version"]
    source_path = app.state.service.sample
    source_hash = __import__("hashlib").sha256(source_path.read_bytes()).hexdigest()

    run_id = "d" * 32
    writer_id = "e" * 32
    draft_root = (app.state.service.root / "generated-drafts" / package_hash / identifier
                  / str(design_version) / run_id)
    draft_root.mkdir(parents=True)
    (draft_root / "manifest.json").write_text("{}\n", encoding="utf-8")
    (draft_root / "notebooks").mkdir()
    (draft_root / "notebooks" / "saved.ipynb").write_text("{}\n", encoding="utf-8")
    writer_path = app.state.service.state / "jobs" / f"{writer_id}.json"
    writer_path.write_text(json.dumps({"id": writer_id, "kind": "spark_draft", "status": "completed",
        "package_hash": package_hash, "design_id": identifier, "design_version": design_version,
        "created_at": "2026-11-13T00:00:00+00:00"}), encoding="utf-8")
    parent_run_path = app.state.service.state / "agent-runs" / f"{run['id']}.json"
    parent_bytes = parent_run_path.read_bytes()

    body = {"version": design_version, "expected_review_version": 0, "consent": True}
    assert client.request("DELETE", f"/api/redesigns/{identifier}", headers=HEADERS,
                          json={**body, "consent": False}).status_code == 403
    assert client.request("DELETE", f"/api/redesigns/{identifier}", headers=HEADERS,
                          json={**body, "expected_review_version": 1}).status_code == 409
    response = client.request("DELETE", f"/api/redesigns/{identifier}", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["deleted"] is True
    assert result["writer_jobs_removed"] == 1
    assert result["artifact_runs_removed"] == 1
    assert result["parent_agent_history_preserved"] is True
    assert result["source_package_preserved"] is True
    assert not draft_root.exists() and not writer_path.exists()
    assert parent_run_path.read_bytes() == parent_bytes
    assert __import__("hashlib").sha256(source_path.read_bytes()).hexdigest() == source_hash
    assert client.get(f"/api/redesigns/{identifier}?version=1").status_code == 404
    assert client.get("/api/redesigns").json()["total"] == 0
    with pytest.raises(Conflict, match="was deleted"):
        store_for(app.state.service).append(package_hash, workflow_id, "original", {
            "role": "architecture", "design": design_for(), "retrieved_evidence_ids": ["a", "b"]},
            record["source"], expected_version=1)