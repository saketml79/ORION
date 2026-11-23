"""Offline provider transport and installer timeout contracts, no live calls."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx2
import openai
import pytest
from langchain_core.exceptions import ModelInvalidRequestError
from langchain_openai.chat_models.base import _handle_openai_api_error, _handle_openai_bad_request

from workbench import databricks_models as dm
from workbench.tests.test_databricks_models import identity, selections


SONNET = "databricks-claude-sonnet-4-6"


def configured(tmp_path, endpoint=SONNET):
    who = identity(endpoints=[{"name": endpoint, "task": "llm/v1/chat",
                               "state": {"ready": "READY"}}])
    router = dm.WorkspaceModels(tmp_path)
    routes = router.configure(who, selections(endpoint), router.discover(who))
    return who, router, routes


def assert_clients(models, expected):
    assert set(models) == set(dm.ROLE_PURPOSES)
    for role, model in models.items():
        assert model.request_timeout == expected[role]
        assert model.root_client.timeout == expected[role]
        assert model.root_async_client.timeout == expected[role]
        assert model.max_retries == 0
        assert model.root_client.max_retries == 0
        assert model.root_async_client.max_retries == 0


def test_default_factory_and_router_remain_60_even_for_sonnet(tmp_path):
    who, router, routes = configured(tmp_path)
    assert_clients(dm.build_chat_models(who, routes), dict.fromkeys(routes, 60))
    assert_clients(router.chat_models(who), dict.fromkeys(routes, 60))
    assert who.client.config.calls == 0


@pytest.mark.parametrize("timeout", [1, 60, 90.5, 180, {}, {"artifact": 180}])
def test_explicit_timeout_is_bounded_role_local_and_not_persisted(tmp_path, timeout):
    who, router, routes = configured(tmp_path)
    before = router.path.read_bytes()
    original_routes = deepcopy(routes)
    expected = ({role: timeout.get(role, 60) for role in routes}
                if isinstance(timeout, dict) else dict.fromkeys(routes, timeout))
    assert_clients(router.chat_models(who, request_timeout=timeout), expected)
    assert routes == original_routes and router.path.read_bytes() == before
    assert who.client.config.calls == 0


@pytest.mark.parametrize("timeout", [None, True, False, "180", 0, -1, 181, 900,
    float("inf"), float("nan"), [], {"writer": 180}, {"artifact": None},
    {"artifact": True}, {"artifact": "180"}, {"artifact": float("nan")}])
def test_invalid_timeout_rejected_before_any_model_or_auth(tmp_path, monkeypatch, timeout):
    who, _, routes = configured(tmp_path)
    monkeypatch.setattr(dm, "_DatabricksChatOpenAI", lambda **kwargs: pytest.fail("No client construction"))
    with pytest.raises(dm.RoutingError, match="timeout"):
        dm.build_chat_models(who, routes, request_timeout=timeout)
    assert who.client.config.calls == 0


@pytest.mark.parametrize("endpoint,seconds", [(SONNET, 180), ("chat", 60),
    ("databricks-claude-sonnet-4-5", 60), ("custom-sonnet-4-6", 60)])
def test_app_policy_matches_exact_sonnet_endpoint_only(tmp_path, endpoint, seconds):
    who, router, routes = configured(tmp_path, endpoint)
    before = router.path.read_bytes()
    listed = who.client.list_calls
    assert_clients(router.app_chat_models(who), dict.fromkeys(routes, seconds))
    assert who.client.list_calls == listed + 1
    assert who.client.config.calls == 0 and router.path.read_bytes() == before


def test_mixed_app_routes_extend_only_selected_sonnet_roles(tmp_path):
    who, router, _ = configured(tmp_path)
    who.client.endpoints.append({"name": "chat", "task": "llm/v1/chat", "state": {"ready": "READY"}})
    selected = selections("chat")
    for role in ("architecture", "artifact"):
        selected[role]["endpoint"] = SONNET
    router.configure(who, selected, router.discover(who))
    models = router.app_chat_models(who)
    assert_clients(models, {role: 180 if role in ("architecture", "artifact") else 60
                            for role in selected})
    assert models["artifact"].model_name == SONNET  # Existing writer route, no new provider.


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
def test_actual_openai_transport_receives_180_and_never_retries(tmp_path, monkeypatch, failure):
    who, router, _ = configured(tmp_path)
    attempts = []
    def respond(request):
        attempts.append(request.extensions["timeout"])
        if failure == "timeout":
            raise httpx2.ReadTimeout("offline simulated provider timeout", request=request)
        return httpx2.Response(503, json={"error": {"message": "offline unavailable"}})
    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    real_chat = dm._DatabricksChatOpenAI
    monkeypatch.setattr(dm, "_DatabricksChatOpenAI", lambda **kwargs: real_chat(**kwargs, http_client=http))
    try:
        model = router.app_chat_models(who)["artifact"]
        error = openai.APITimeoutError if failure == "timeout" else openai.InternalServerError
        with pytest.raises(error):
            model.invoke("Offline coding request")
    finally:
        http.close()
    assert attempts == [{"connect": 180, "read": 180, "write": 180, "pool": 180}]
    assert who.client.config.calls == 1


def test_tool_bound_databricks_model_retries_langchain_wrapped_503():
    model = dm._DatabricksChatOpenAI(model=SONNET, api_key="offline-placeholder",
        base_url="https://adb.example/serving-endpoints", max_retries=0)
    bound = model.bind_tools([{"type": "function", "function": {
        "name": "read_file", "parameters": {"type": "object", "properties": {}}}}])
    attempts, progress = [], []

    def invoke():
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            request = httpx2.Request("POST", "https://adb.example/serving-endpoints")
            response = httpx2.Response(503, headers={"retry-after": "0"}, request=request)
            failure = openai.InternalServerError(message="offline service unavailable", response=response, body={})
            _handle_openai_api_error(failure)
        return "recovered"

    assert dm.retry_databricks_429(invoke, bound, Event(), progress.append) == "recovered"
    assert len(attempts) == 3
    assert [entry["event"] for entry in progress] == [
        "model_retry_wait", "model_retry_resuming", "model_retry_wait",
        "model_retry_resuming", "model_retry_succeeded"]
    assert all(entry["status_code"] == 503 for entry in progress)


def test_tool_bound_databricks_model_does_not_retry_invalid_400():
    model = dm._DatabricksChatOpenAI(model=SONNET, api_key="offline-placeholder",
        base_url="https://adb.example/serving-endpoints", max_retries=0)
    bound = model.bind_tools([{"type": "function", "function": {
        "name": "read_file", "parameters": {"type": "object", "properties": {}}}}])
    request = httpx2.Request("POST", "https://adb.example/serving-endpoints")
    response = httpx2.Response(400, headers={"retry-after": "0"}, request=request)
    failure = openai.BadRequestError(message="offline invalid request", response=response, body={})
    attempts = []

    def invoke():
        attempts.append(1)
        _handle_openai_bad_request(failure)

    with pytest.raises(ModelInvalidRequestError) as caught:
        dm.retry_databricks_429(invoke, bound, Event())
    assert getattr(caught.value, "status_code", None) == 400
    assert len(attempts) == 1


@pytest.mark.parametrize("pre_cancelled", [True, False])
def test_180_second_client_preserves_cooperative_runtime_cancellation(tmp_path, monkeypatch, pre_cancelled):
    from workbench.agents import AgentRuntime
    from workbench.tests.test_runtime_planning import model as source_model
    who, router, _ = configured(tmp_path / "models")
    entered, release, cancellation = Event(), Event(), Event()
    attempts = []
    def respond(request):
        attempts.append(request.extensions["timeout"]["read"])
        entered.set()
        assert release.wait(10), "Test must release the in-flight offline request"
        return httpx2.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0, "model": SONNET,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done"},
                         "finish_reason": "stop"}]})
    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    real_chat = dm._DatabricksChatOpenAI
    monkeypatch.setattr(dm, "_DatabricksChatOpenAI", lambda **kwargs: real_chat(**kwargs, http_client=http))
    runtime = AgentRuntime(tmp_path / "agents", role_models=router.app_chat_models(who))
    assert runtime.budget_seconds is None  # No provider-policy escalation of the run budget.
    if pre_cancelled:
        cancellation.set()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runtime.run, source_model(), "wf", "original", "Review source", [], [],
                                 cancellation_event=cancellation)
            if not pre_cancelled:
                try:
                    assert entered.wait(10)
                    cancellation.set()
                    assert not future.done()  # Cooperative stop does not kill an in-flight provider call.
                finally:
                    release.set()
            result = future.result(timeout=30)
    finally:
        release.set()
        http.close()
    assert result["status"] == "cancelled"
    assert attempts == ([] if pre_cancelled else [180])
    assert who.client.config.calls == len(attempts)


@pytest.mark.parametrize("mode", ["local", "hosted"])
def test_real_installers_supply_sonnet_policy_without_inference(tmp_path, monkeypatch, mode):
    from workbench import hosted, model_access
    who, _, _ = configured(tmp_path / "models")
    service = SimpleNamespace(root=tmp_path, runtime=None, agent_slot=Lock())
    captured = []
    def runtime(root, *, role_models, identity_scope, **kwargs):
        captured.append(role_models)
        return SimpleNamespace(role_models=role_models)
    app = FastAPI()
    app.state.service = service
    if mode == "local":
        monkeypatch.setattr(model_access, "AgentRuntime", runtime)
        monkeypatch.setattr(model_access, "workspace_ready", lambda value: True)
        model_access.install_model_access(app, identity_factory=lambda: who)
        with TestClient(app) as client:
            assert client.post("/api/models/connect").status_code == 200
            assert captured == []  # Metadata-only discovery.
            response = client.post("/api/models/select", json={
                "endpoint": SONNET, "tool_support_confirmed": True})
            assert response.status_code == 200, response.text
    else:
        who = dm.WorkspaceIdentity("obo", who.host, who.user_id, who.client)
        monkeypatch.setattr(hosted, "AgentRuntime", runtime)
        token = hosted._CURRENT.set(who)
        try:
            app.state.hosted_execution_models = False
            hosted.install_hosted_models(app)
            assert captured == []  # Metadata-only installation.
            app.state.hosted_execution_models = True
            hosted.install_hosted_models(app)
        finally:
            hosted._CURRENT.reset(token)
    assert len(captured) == 1
    assert_clients(captured[0], dict.fromkeys(dm.ROLE_PURPOSES, 180))
    assert service.runtime.role_models["artifact"] is captured[0]["artifact"]
    assert who.client.config.calls == 0