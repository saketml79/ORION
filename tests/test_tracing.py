"""Real SDK serialization and compiled graph callbacks, fake HTTP only.

No remote LangSmith credentials, models, projects or datasets are used.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import requests
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from workbench import tracing


PRIVATE = "PRIVATE_SQL_XML_PROMPT_HEADERS_MODELS_CHECKPOINT_CREDENTIALS"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("LANGSMITH_", "LANGCHAIN_", "WORKBENCH_LANGSMITH_")):
            monkeypatch.delenv(key)
    # Accidental requests must fail, even in disabled/status-only tests.
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected HTTP request")
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("WORKBENCH_LANGSMITH_ENABLED", "1")
    monkeypatch.setenv("WORKBENCH_LANGSMITH_PROJECT", "workbench-test")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-server-secret-not-a-real-key")


@pytest.fixture
def wire(monkeypatch):
    """Keep the real Client, transformations, retries and guarded Transport."""
    calls = []

    def request(session, method, url, **kwargs):
        assert session.trust_env is False
        assert url.startswith(tracing.DEFAULT_ENDPOINT + "/")
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"].total == 3
        assert kwargs["timeout"].connect_timeout == 1
        assert session.get_adapter(url).max_retries.total == 0
        data = kwargs.get("data", b"{}")
        payload = json.loads(data)
        calls.append({"method": method, "url": url, "payload": payload})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.request = requests.Request(method, url).prepare()
        # Feedback schema uses the same JSON record sent to the server.
        body = {**payload, "trace_id": payload.get("trace_id"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "modified_at": datetime.now(timezone.utc).isoformat()}
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    return calls


def rubric():
    return {"checks": [{"criterion": "authority_gates_closed", "passed": True,
                         "details": PRIVATE},
                        {"criterion": "known_scoped_citations", "passed": False,
                         "details": [PRIVATE]}]}


def submitted(scope="user-a"):
    run = uuid4().hex
    with tracing.trace_session(run, scope) as session:
        receipt = session.finish()
    return run, receipt


def test_disabled_and_status_never_initialize_or_read_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("WORKBENCH_LANGSMITH_ENABLED=1\nLANGSMITH_API_KEY=" + PRIVATE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracing, "_new_client", lambda *a: pytest.fail("must not initialize"))
    before = dict(os.environ)
    assert tracing.tracing_status()["state"] == "disabled"
    with tracing.trace_session(uuid4().hex, PRIVATE) as session:
        assert session.callbacks() == []
        assert session.finish()["root_id"] is None
    assert tracing.export_evaluation({}, rubric(), identity_scope="user", run_id=None)["state"] == "disabled"
    assert dict(os.environ) == before


@pytest.mark.parametrize("value", ["true", "yes", "0", "", " 1"])
def test_exact_opt_in_only(monkeypatch, value):
    monkeypatch.setenv("WORKBENCH_LANGSMITH_ENABLED", value)
    assert tracing.tracing_status()["state"] == "disabled"


@pytest.mark.parametrize("endpoint", [
    "https://api.smith.langchain.com/", "https://api.smith.langchain.com.evil.example",
    "https://api.smith.langchain.com@evil.example", "http://api.smith.langchain.com",
    "https://api.smith.langchain.com:443", "https://127.0.0.1", "http://169.254.169.254",
    "https://api.smith.langchain.com?key=" + PRIVATE, "https://api.smith.langchain.com/#x",
    "https://eu.api.smith.langchain.com",
])
def test_exact_endpoint_allowlist(enabled, monkeypatch, endpoint):
    monkeypatch.setenv("LANGSMITH_ENDPOINT", endpoint)
    monkeypatch.setattr(tracing, "_new_client", lambda *a: pytest.fail("must not initialize"))
    status = tracing.tracing_status()
    assert status["state"] == "failed" and status["enabled"]
    assert status["reason"] == "endpoint_not_approved"
    assert PRIVATE not in json.dumps(status)
    with tracing.trace_session(uuid4().hex, "user") as session:
        assert not session.callbacks()


@pytest.mark.parametrize("field,value,reason", [
    ("WORKBENCH_LANGSMITH_PROJECT", "", "explicit_project_required"),
    ("WORKBENCH_LANGSMITH_PROJECT", "project\nAuthorization: secret", "explicit_project_required"),
    ("WORKBENCH_LANGSMITH_PROJECT", "lsv2-secret-value", "explicit_project_required"),
    ("LANGSMITH_API_KEY", "", "server_key_required"),
])
def test_configuration_errors_are_failed_not_disabled(enabled, monkeypatch, field, value, reason):
    monkeypatch.setenv(field, value)
    status = tracing.tracing_status()
    assert status["state"] == "failed" and status["reason"] == reason
    assert not status["verified"]


def test_status_endpoint_is_safe_no_client_and_no_model(tmp_path, enabled, monkeypatch):
    from workbench.app import _create_package_app
    from workbench.tests.test_loops import Runtime, client_for
    monkeypatch.setattr(tracing, "_new_client", lambda *a: pytest.fail("status must not probe"))
    runtime = Runtime()
    app = _create_package_app(tmp_path / "app", tmp_path / "absent.xml", runtime)
    with client_for(app) as client:
        response = client.get("/api/tracing/status")
        assert response.status_code == 200
        status = response.json()
        assert status["state"] == "configured" and status["project"] == "workbench-test"
        assert not status["verified"] and runtime.calls == []
        assert "key" not in response.text.lower() and "test-server-secret" not in response.text


def test_actual_sdk_callback_tree_no_payload_serialization(enabled, wire, monkeypatch):
    # Ambient LangSmith tracing must never enable a second full-content tracer.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", PRIVATE)
    monkeypatch.setenv("LANGSMITH_METADATA", json.dumps({PRIVATE: PRIVATE}))
    before = dict(os.environ)
    root, chain, model, tool = uuid4(), uuid4(), uuid4(), uuid4()
    with tracing.trace_session(root.hex, PRIVATE) as session:
        callback = session.callbacks()[0]
        dirty = {"name": PRIVATE, "kwargs": {"api_key": PRIVATE}, "metadata": {PRIVATE: PRIVATE}}
        callback.on_chain_start(dirty, dirty, run_id=chain, metadata=dirty)
        callback.on_chat_model_start(dirty, [[PRIVATE]], run_id=model, parent_run_id=chain, **dirty)
        message = AIMessage(content=PRIVATE, usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})
        callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=model)
        callback.on_tool_start(dirty, PRIVATE, run_id=tool, parent_run_id=chain, **dirty)
        callback.on_tool_error(RuntimeError(PRIVATE), run_id=tool)
        callback.on_chain_end(dirty, run_id=chain)
        receipt = session.finish(usage={"input_tokens": 12, "output_tokens": True, PRIVATE: PRIVATE})
        assert session.finish() == receipt
    assert dict(os.environ) == before
    assert receipt["state"] == "submitted" and receipt["root_submitted"] and receipt["root_completed"]
    assert receipt["http_acknowledged_writes"] == len(wire) == 8
    assert receipt["submitted_spans"] == 4 and not receipt["verified"]
    assert PRIVATE not in json.dumps(wire)
    creates = {c["payload"]["id"]: c["payload"] for c in wire if c["method"] == "POST"}
    assert creates[str(root)]["parent_run_id"] is None
    assert creates[str(chain)]["parent_run_id"] == str(root)
    assert creates[str(model)]["parent_run_id"] == str(chain)
    assert creates[str(tool)]["parent_run_id"] == str(chain)
    for item in creates.values():
        assert item["trace_id"] == str(root)
        assert item["inputs"] == item["outputs"] == {}
        assert not item.get("serialized")
        assert "runtime" not in item.get("extra", {})
        assert not item.get("extra", {}).get("metadata")
    patches = {c["url"].rsplit("/", 1)[-1]: c["payload"] for c in wire if c["method"] == "PATCH"}
    assert patches[str(model)]["extra"]["usage"] == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert patches[str(tool)]["error"] == "execution_failed"
    assert patches[str(root)]["extra"]["usage"] == {"input_tokens": 12}
    assert all("end_time" in p for p in patches.values())


def test_actual_compiled_runtime_callbacks_completion_and_no_invocation_changes(tmp_path, enabled, wire, monkeypatch):
    from workbench.agents import AgentRuntime, ROLES
    from workbench.tests.test_redesign import chat, model, tool_call
    from langchain_core.tracers import LangChainTracer
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(LangChainTracer, "__init__", lambda *a, **k: pytest.fail("full-content tracer forbidden"))

    def routes():
        return {r: chat([AIMessage(content="Initial " + PRIVATE),
                        tool_call("submit_finding", {"finding": {"role": r, "summary": PRIVATE,
                            "evidence_ids": [], "status": "needs_information"}}, r),
                        AIMessage(content="Done " + PRIVATE)]) for r in ROLES}

    traced_routes = routes()
    runtime = AgentRuntime(tmp_path / "on", role_models=traced_routes, identity_scope="owner")
    result = runtime.run(model(), "wf", "original", "Propose design " + PRIVATE, [], [])
    assert len(result["findings"]) == 6 and all(r["completion_pass"] for r in result["role_completion"])
    assert result["tracing"]["state"] == "submitted" and result["tracing"]["root_completed"]
    assert result["tracing"]["root_id"] == str(UUID(result["run_id"]))
    assert not result["tracing"]["truncated"]
    assert PRIVATE not in json.dumps(wire)
    assert {c["payload"]["run_type"] for c in wire if c["method"] == "POST"} >= {"chain", "llm", "tool"}
    creates = {c["payload"]["id"]: c["payload"] for c in wire if c["method"] == "POST"}
    root = result["tracing"]["root_id"]
    assert all(p["parent_run_id"] in creates for id_, p in creates.items() if id_ != root)
    assert len([p for p in creates.values() if p["parent_run_id"] == root]) == 12
    count = len(wire)
    monkeypatch.delenv("WORKBENCH_LANGSMITH_ENABLED")
    plain_routes = routes()
    plain = AgentRuntime(tmp_path / "off", role_models=plain_routes).run(
        model(), "wf", "original", "Propose design " + PRIVATE, [], [])
    assert len(wire) == count
    assert {r: len(m._seen) for r, m in traced_routes.items()} == {r: len(m._seen) for r, m in plain_routes.items()}
    assert plain["findings"] == result["findings"] and plain["status"] == result["status"]
    assert plain["role_completion"] == result["role_completion"]
    assert not result["approved"] and not result["generation_allowed"] and not result["deployment_allowed"]
    from contextlib import closing
    ledger = next((tmp_path / "on").rglob("agents.sqlite3"))
    with closing(sqlite3.connect(ledger)) as db:
        saved = json.loads(db.execute("SELECT result FROM agent_runs").fetchone()[0])
    assert saved["tracing"] == result["tracing"]


@pytest.mark.parametrize("stage", ["initialize", "root", "child", "end"])
def test_failures_isolated_and_never_report_success(enabled, monkeypatch, wire, stage):
    if stage == "initialize":
        monkeypatch.setattr(tracing, "_new_client", lambda *a: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    root = uuid4()
    with tracing.trace_session(root.hex, "user") as session:
        if stage != "initialize":
            session.queue.join()  # fake HTTP only: establish root delivery before changing transport
            def fail(*a, **k):
                raise requests.Timeout(PRIVATE)
            if stage == "root":
                # The root already succeeded. Exercise a separate root failure.
                monkeypatch.setattr(requests.Session, "request", fail)
                with tracing.trace_session(uuid4().hex, "user") as failed:
                    receipt = failed.finish()
                    assert not receipt["root_submitted"] and receipt["state"] == "failed"
                return
            monkeypatch.setattr(requests.Session, "request", fail)
            if stage == "child":
                session.start_span("tool", uuid4(), None)
        receipt = session.finish()
        assert not session.callbacks()
    assert receipt["state"] == "failed" and not receipt["verified"]
    assert PRIVATE not in json.dumps(receipt)


def test_slow_delivery_does_not_block_callbacks_and_finish_is_bounded(enabled, monkeypatch):
    from threading import Event
    from time import monotonic
    started, release = Event(), Event()
    writes = []
    def create(**kwargs):
        started.set()
        assert release.wait(timeout=10)
        writes.append(kwargs)
    client = SimpleNamespace(session=SimpleNamespace(close=lambda: None),
        create_run=create, update_run=lambda *a, **k: writes.append(k))
    monkeypatch.setattr(tracing, "_new_client", lambda *a: client)
    try:
        with tracing.trace_session(uuid4().hex, "user") as session:
            assert started.wait(timeout=2)
            before = monotonic()
            callback = session.callbacks()[0]
            child = uuid4()
            callback.on_tool_start({"name": PRIVATE}, PRIVATE, run_id=child)
            callback.on_tool_end(PRIVATE, run_id=child)
            assert monotonic() - before < 0.5
            before = monotonic()
            result = session.finish()
            assert monotonic() - before < 4
            assert result["state"] == "pending" and not result["verified"]
            assert not result["root_completed"] and "receipt" not in result
            release.set()
            session.worker.join(timeout=2)
            assert not session.worker.is_alive()
            assert session.finish() == result  # no retroactive success claim in persisted output
            assert len(writes) == 1  # queued writes cancelled after finalization deadline
    finally:
        release.set()


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429, 500])
def test_http_errors_never_mint_trace_or_feedback_receipts(enabled, monkeypatch, status):
    calls = []
    def error(session, method, url, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = status
        response.url = url
        response.request = requests.Request(method, url).prepare()
        response._content = PRIVATE.encode()
        return response
    monkeypatch.setattr(requests.Session, "request", error)
    _, receipt = submitted()
    assert len(calls) == 1 and receipt["state"] == "failed"
    assert not receipt["root_submitted"] and not receipt["root_acknowledged"]
    assert receipt["http_acknowledged_writes"] == 0 and "receipt" not in receipt
    assert PRIVATE not in json.dumps(receipt)


def test_provider_failure_preserves_result_and_trace_error(tmp_path, enabled, wire, monkeypatch):
    from workbench.agents import AgentRuntime, ROLES
    from workbench.tests.test_redesign import ToolModel, chat, model
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(ToolModel, "_generate", fail)
    result = AgentRuntime(tmp_path, role_models={r: chat() for r in ROLES}).run(
        model(), "wf", "original", "Review", [], [])
    assert len(calls) == 1 and result["status"] == "failed"
    assert result["tracing"]["root_completed"]
    assert wire[-1]["payload"]["error"] == "execution_failed"
    assert PRIVATE not in json.dumps(wire) and PRIVATE not in json.dumps(result)


def test_trace_delivery_failure_does_not_change_runtime(tmp_path, enabled, monkeypatch):
    from workbench.agents import AgentRuntime, ROLES
    from workbench.tests.test_redesign import chat, model
    def fail(*args, **kwargs):
        raise requests.Timeout(PRIVATE)
    monkeypatch.setattr(requests.Session, "request", fail)
    routes = {r: chat() for r in ROLES}
    result = AgentRuntime(tmp_path, role_models=routes).run(model(), "wf", "original", "Review", [], [])
    assert all(len(m._seen) == 1 for m in routes.values())
    assert result["status"] == "needs_information" and result["tracing"]["state"] == "failed"


def test_sdk_return_is_not_verified_remote_ui_or_ack(enabled, monkeypatch):
    client = SimpleNamespace(session=SimpleNamespace(close=lambda: None),
        create_run=lambda **k: None, update_run=lambda *a, **k: None)
    monkeypatch.setattr(tracing, "_new_client", lambda *a: client)
    _, receipt = submitted()
    assert receipt["state"] == "submitted" and not receipt["verified"]
    assert receipt["http_acknowledged_writes"] == 0 and "url" not in receipt
    result = tracing.export_evaluation(receipt, rubric(), identity_scope="user-a",
                                      run_id=receipt["runtime_run_id"])
    assert result["state"] == "not_submitted" and result["submitted_scores"] == 0


def test_span_limit_missing_parent_and_duplicate_events(enabled, wire, monkeypatch):
    monkeypatch.setattr(tracing, "MAX_SPANS", 3)
    with tracing.trace_session(uuid4().hex, "user") as session:
        child = uuid4()
        session.start_span("tool", child, uuid4())  # unknown parent: do not invent ancestry
        session.start_span("tool", child, None)
        session.start_span("tool", child, None)
        session.end_span(child)
        session.end_span(child)
        session.start_span("chain", uuid4(), None)  # intentionally missing end event
        session.start_span("llm", uuid4(), None)
        receipt = session.finish()
    assert receipt["submitted_spans"] == 3 and receipt["truncated"]
    assert len(wire) == 5


def test_multi_destination_refused_without_network_or_env_mutation(enabled, monkeypatch):
    monkeypatch.setenv("LANGSMITH_RUNS_ENDPOINTS", json.dumps({"http://127.0.0.1": PRIVATE}))
    before = dict(os.environ)
    _, receipt = submitted()
    assert receipt["state"] == "failed" and not receipt["root_submitted"]
    assert dict(os.environ) == before and PRIVATE not in json.dumps(receipt)


def test_transport_rejects_redirects_and_arbitrary_operations(enabled, monkeypatch):
    attempts = []
    def redirect(*args, **kwargs):
        attempts.append(1)
        response = requests.Response()
        response.status_code = 307
        response.headers["Location"] = "http://169.254.169.254/"
        return response
    monkeypatch.setattr(requests.Session, "request", redirect)
    client = tracing._new_client(tracing.tracing_status())
    try:
        for url in ("http://127.0.0.1/runs", tracing.DEFAULT_ENDPOINT + "/sessions",
                    tracing.DEFAULT_ENDPOINT + "/runs?token=x"):
            with pytest.raises(ValueError):
                client.session.request("POST", url)
        with pytest.raises(ValueError, match="redirect"):
            client.session.request("POST", tracing.DEFAULT_ENDPOINT + "/runs")
        assert len(attempts) == 1
    finally:
        client.session.close()


def test_feedback_only_boolean_allowlist_no_details_and_actual_same_root(enabled, wire):
    run, trace = submitted()
    receipt = tracing.export_evaluation(trace, rubric(), identity_scope="user-a", run_id=run)
    assert receipt["state"] == "submitted" and receipt["submitted_scores"] == 2
    assert receipt["http_acknowledged_writes"] == 2 and not receipt["verified"]
    assert receipt["semantic_parity"] == "unproven"
    payloads = [c["payload"] for c in wire if c["url"].endswith("/feedback")]
    assert {p["key"] for p in payloads} == {"advisory.authority_gates_closed", "advisory.known_scoped_citations"}
    assert [p["score"] for p in payloads] == [True, False]
    assert all(p["run_id"] == str(UUID(run)) for p in payloads)
    assert PRIVATE not in json.dumps(wire)


@pytest.mark.parametrize("tamper", ["owner", "run", "project", "endpoint", "fake", "failed", "unsigned", "unfinished", "restart"])
def test_feedback_rejects_foreign_fabricated_failed_or_stale_receipts(enabled, wire, monkeypatch, tamper):
    run, trace = submitted()
    scope = "user-a"
    if tamper == "owner":
        scope = "user-b"
    elif tamper == "run":
        run = uuid4().hex
    elif tamper == "project":
        trace["project"] = "different"
    elif tamper == "endpoint":
        trace["endpoint"] = "https://evil.example"
    elif tamper == "fake":
        trace["root_id"] = str(uuid4())
    elif tamper == "failed":
        trace["state"] = "failed"
    elif tamper == "unsigned":
        trace.pop("receipt")
    elif tamper == "unfinished":
        trace["root_completed"] = False
    else:
        monkeypatch.setattr(tracing, "_RECEIPT_KEY", b"different-process")
    count = len(wire)
    result = tracing.export_evaluation(trace, rubric(), identity_scope=scope, run_id=run)
    assert result["state"] == "not_submitted" and result["submitted_scores"] == 0
    assert len(wire) == count


@pytest.mark.parametrize("checks", [[], [{"criterion": PRIVATE, "passed": True}],
    [{"criterion": "runtime_completed", "passed": 1}],
    [{"criterion": "runtime_completed", "passed": True}] * 2])
def test_feedback_rejects_non_rubric_payloads(enabled, wire, checks):
    run, trace = submitted()
    count = len(wire)
    result = tracing.export_evaluation(trace, {"checks": checks}, identity_scope="user-a", run_id=run)
    assert result["state"] == "not_submitted" and len(wire) == count


def test_feedback_failure_single_attempt_no_authority(enabled, wire, monkeypatch):
    run, trace = submitted()
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout(PRIVATE)
    monkeypatch.setattr(requests.Session, "request", fail)
    result = tracing.export_evaluation(trace, rubric(), identity_scope="user-a", run_id=run)
    assert len(calls) == 1 and result["state"] == "failed" and result["submitted_scores"] == 0
    assert not result["verified"] and PRIVATE not in json.dumps(result)


def test_owner_scope_only_hash_and_sessions_are_isolated(enabled, wire):
    one, two = uuid4().hex, uuid4().hex
    before = dict(os.environ)
    with tracing.trace_session(one, "owner-a") as a, tracing.trace_session(two, "owner-b") as b:
        first, second = a.finish(), b.finish()
    assert first["owner_scope"] != second["owner_scope"]
    assert first["root_id"] != second["root_id"]
    assert "owner-a" not in json.dumps(wire) and "owner-b" not in json.dumps(wire)
    assert dict(os.environ) == before


def test_loop_persists_trace_and_exports_its_actual_rubric(tmp_path, enabled, wire):
    from workbench.app import _create_package_app
    from workbench.tests.test_loops import Runtime, XML, client_for, completed, post, submit, supported
    runtime = Runtime()
    runtime.identity_scope = "loop-owner"
    run_id, trace = submitted("loop-owner")
    runtime.respond = lambda call, number: {**supported(call[1]), "run_id": run_id, "tracing": deepcopy(trace)}
    source = tmp_path / "sample.xml"
    source.write_bytes(XML)
    app = _create_package_app(tmp_path / "app", source, runtime)
    with client_for(app) as client:
        response = post(client, "/api/packages/sample")
        assert response.status_code == 202
        app.state.service.package_executor.submit(lambda: None).result(timeout=10)
        workflow = client.get("/api/overview").json()["workflows"][0]["id"]
        result = completed(client, app, submit(client, workflow))
        attempt = result["attempts"][0]
        assert attempt["tracing"] == trace
        assert attempt["tracing_evaluation"]["state"] == "submitted"
        assert attempt["tracing_evaluation"]["submitted_scores"] == len(attempt["rubric"]["checks"])
        assert len(runtime.calls) == 1
        assert not result["approved"] and result["semantic_parity"] == "unproven"