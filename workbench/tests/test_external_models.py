"""Backend-only regressions. All credentials are fabricated, all wire I/O mocked.

Parent runs this module in the consolidated regression batch. No live discovery,
provider verification, paid inference, real credential, browser or installation.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from threading import BoundedSemaphore
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from workbench import external_models as em
from workbench.databricks_models import ROLE_PURPOSES

PROJECT, PACKAGE = "1" * 32, "2" * 64
KEY = ("https://workspace.example.com\nalice", PROJECT, PACKAGE)
FAKE = "sk-orion-fabricated-test-credential-never-live-1234567890"


def configured(store=None, provider="openai", key=KEY):
    store = store or em.Sessions()
    body = {"provider": provider, "model": "gpt-test" if provider == "openai" else "claude-test", "api_key": FAKE}
    result, token = store.connect(key, "", body)
    return store, token, result


@pytest.fixture(autouse=True)
def clean_sessions(monkeypatch):
    # Keep test timers inert; expiry is exercised with the injected clock.
    monkeypatch.setattr(em.Timer, "start", lambda self: None)
    monkeypatch.setattr(em, "LOCAL", em.Sessions())


def test_default_configure_ttl_disconnect_and_no_key_metadata():
    now = [100.0]
    store = em.Sessions(clock=lambda: now[0], ttl=10)
    assert store.status(KEY, "")["mode"] == "databricks"
    store, token, result = configured(store)
    secret = store.entries[KEY].secret
    assert result["status"] == "configured" and result["verified"] is False
    assert result["databricks_controls_disabled"] is True
    assert result["expires_in_seconds"] == 10
    assert FAKE not in json.dumps(result) and FAKE[:16] not in json.dumps(result)
    assert not any(word in result for word in ("api_key", "fingerprint", "prefix"))
    now[0] = 111
    expired = store.status(KEY, token)
    assert expired["mode"] == "external" and expired["status"] == "expired"
    assert not expired["inference_ready"] and not secret
    with pytest.raises(HTTPException) as failure:
        store.credential(KEY, token)
    assert failure.value.status_code == 409
    assert store.disconnect(KEY, token)["mode"] == "databricks"


def test_lost_process_session_is_not_silent_databricks_fallback():
    _, token, _ = configured()
    restarted = em.Sessions()
    assert restarted.active(KEY, token)
    assert restarted.status(KEY, token)["status"] == "expired"
    assert restarted.disconnect(KEY, token)["mode"] == "databricks"
    assert not restarted.active(KEY, token)


def test_cross_user_package_cookie_isolation_and_server_minted_cookie():
    store, token, _ = configured()
    other_user = ("https://workspace.example.com\nbob", PROJECT, PACKAGE)
    other_package = (KEY[0], PROJECT, "3" * 64)
    assert store.status(other_user, token)["configured"] is False
    assert store.status(other_package, token)["configured"] is False
    with pytest.raises(HTTPException) as failure:
        store.status(KEY, "x" * 43)
    assert failure.value.status_code == 401
    _, other_token = store.connect(other_package, token,
        {"provider": "anthropic", "model": "claude-test", "api_key": FAKE + "-other"})
    assert other_token == token
    store.disconnect(other_package, token)
    assert store.status(KEY, token)["configured"] is True
    fresh = em.Sessions()
    _, minted = fresh.connect(KEY, "x" * 43,
        {"provider": "openai", "model": "gpt-test", "api_key": FAKE})
    assert minted != "x" * 43


def test_active_jobs_block_replace_disconnect_and_duplicate_reservations():
    store, token, _ = configured()
    assert store.begin(KEY, "job") is True
    assert store.begin(KEY, "job") is False
    before = bytes(store.entries[KEY].secret)
    with pytest.raises(HTTPException) as replace:
        store.connect(KEY, token, {"provider": "anthropic", "model": "claude-test", "api_key": FAKE + "new"})
    with pytest.raises(HTTPException) as disconnect:
        store.disconnect(KEY, token)
    assert replace.value.status_code == disconnect.value.status_code == 409
    assert bytes(store.entries[KEY].secret) == before
    store.end(KEY, "job")
    assert store.disconnect(KEY, token)["mode"] == "databricks"


def test_capacity_and_rate_fail_closed_without_evicting_credentials():
    store, token, _ = configured(em.Sessions(capacity=1))
    with pytest.raises(HTTPException) as full:
        store.connect((KEY[0], PROJECT, "4" * 64), "",
            {"provider": "openai", "model": "gpt-test", "api_key": FAKE})
    assert full.value.status_code == 429
    for _ in range(30):
        store.rate(KEY[0])
    with pytest.raises(HTTPException) as limited:
        store.rate(KEY[0])
    assert limited.value.status_code == 429
    assert store.status(KEY, token)["configured"]


@pytest.mark.parametrize("raw", [
    b"{", b"[]", b"null", b'{"provider":[]}',
    json.dumps({"provider": "codex", "model": "gpt-test", "api_key": FAKE}).encode(),
    json.dumps({"provider": "openai", "model": "https://evil.example/", "api_key": FAKE}).encode(),
    json.dumps({"provider": "openai", "model": "gpt-test", "api_key": FAKE, "base_url": "https://evil.example"}).encode(),
    ('{"provider":"openai","provider":"anthropic","model":"gpt-test","api_key":"' + FAKE + '"}').encode(),
    json.dumps({"provider": "anthropic", "model": FAKE, "api_key": FAKE}).encode(),
])
def test_malformed_input_never_echoes_submitted_key(raw):
    with pytest.raises(HTTPException) as failure:
        em.selection(raw)
    assert failure.value.status_code == 422
    assert FAKE not in str(failure.value) and "evil.example" not in str(failure.value)


async def call(handler, path, method="POST", body=b"{}", headers=None, query=b""):
    sent = []
    messages = iter([{"type": "http.request", "body": body, "more_body": False}])
    async def receive():
        return next(messages, {"type": "http.disconnect"})
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "path": path, "method": method, "query_string": query,
             "scheme": "http", "client": ("127.0.0.1", 1234), "headers": [
                 (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    await handler(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    data = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], json.loads(data), start.get("headers", [])


def test_configuration_http_contract_no_sdk_construction_and_secure_cookie(monkeypatch):
    store = em.Sessions()
    monkeypatch.setattr(em, "build_runtime", lambda *a, **k: pytest.fail("Connect must not build/invoke SDK clients"))
    async def handler(scope, receive, send):
        scope["scheme"] = "https"
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        return await em.handle(store, KEY, headers, scope, receive, send)
    body = json.dumps({"provider": "openai", "model": "gpt-test", "api_key": FAKE}).encode()
    status, result, headers = asyncio.run(call(handler, em.PREFIX + "connect", body=body,
        headers={"content-type": "application/json"}))
    assert status == 200 and result["verified"] is False and result["status"] == "configured"
    cookie = next(value.decode() for name, value in headers if name == b"set-cookie")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Max-Age" not in cookie and FAKE not in repr((headers, result))
    status, result, _ = asyncio.run(call(handler, em.PREFIX + "connect", body=b"{",
        headers={"content-type": "application/json"}))
    assert status == 422 and FAKE not in repr(result)
    status, _, _ = asyncio.run(call(handler, em.PREFIX + "connect", body=b"x" * (em.MAX_BODY + 1),
        headers={"content-type": "application/json"}))
    assert status == 413


def test_local_runtime_preserves_default_and_busy_switch_lock(tmp_path, monkeypatch):
    from workbench.agents import AgentRuntime
    baseline = AgentRuntime(tmp_path / "agents")
    service = SimpleNamespace(root=tmp_path, runtime=baseline, agent_slot=BoundedSemaphore(1))
    async def app(scope, receive, send):
        from starlette.responses import JSONResponse
        return await JSONResponse(service.runtime.status())(scope, receive, send)
    middleware = em.LocalAccess(app, service=service)
    scope_headers = {"host": "127.0.0.1", "x-workbench-request": "1", "content-type": "application/json",
                     "x-workbench-project": PROJECT, "x-workbench-package": PACKAGE}
    body = json.dumps({"provider": "anthropic", "model": "claude-test", "api_key": FAKE}).encode()
    status, _, headers = asyncio.run(call(middleware, em.PREFIX + "connect", body=body, headers=scope_headers))
    assert status == 200
    cookie = next(value.decode().split(";", 1)[0] for name, value in headers if name == b"set-cookie")
    scope_headers["cookie"] = cookie
    status, result, _ = asyncio.run(call(middleware, "/api/models/status", method="GET", headers=scope_headers))
    assert status == 200 and result["mode"] == "external" and isinstance(service.runtime, em.Runtime)
    assert service.agent_slot.acquire(False)
    try:
        status, _, _ = asyncio.run(call(middleware, em.PREFIX + "disconnect", headers=scope_headers))
        assert status == 409
    finally:
        service.agent_slot.release()
    status, result, _ = asyncio.run(call(middleware, em.PREFIX + "disconnect", headers=scope_headers))
    assert status == 200 and result["mode"] == "databricks" and service.runtime is baseline


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_six_fresh_models_no_paid_calls_and_secret_not_in_sdk_state(tmp_path, monkeypatch, provider):
    import httpx2
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("No network on construction"))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-must-not-be-used")
    store, token, _ = configured(provider=provider)
    runtime = store.runtime(KEY, token, tmp_path)
    assert set(runtime.role_models) == set(ROLE_PURPOSES) and len(runtime.role_models) == 6
    assert len({id(model) for model in runtime.role_models.values()}) == 6
    assert runtime.role_models["artifact"] is not runtime.role_models["validation"]
    for model in runtime.role_models.values():
        assert model.max_retries == 0
        assert FAKE not in repr(model) and FAKE not in repr(model.model_dump())
        if provider == "openai":
            assert str(model.root_client.base_url) == "https://api.openai.com/v1/"
            assert model.request_timeout == 60 and model.root_client.max_retries == 0
        else:
            assert str(model._client.base_url).rstrip("/") == "https://api.anthropic.com"
            assert model.default_request_timeout == 60 and model._client.max_retries == 0


@pytest.mark.parametrize("provider,url", [("openai", "https://api.openai.com/v1/chat/completions"),
                                         ("anthropic", "https://api.anthropic.com/v1/messages")])
def test_wire_auth_error_redaction_and_expiry_before_network(monkeypatch, provider, url):
    import httpx2
    from workbench.external_model_transport import Transport
    store, token, _ = configured(provider=provider)
    calls = []
    def respond(self, request):
        calls.append(request.url)
        header = "authorization" if provider == "openai" else "x-api-key"
        assert FAKE in request.headers[header]
        return httpx2.Response(401, content=FAKE.encode(), headers={"x-secret": FAKE})
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", respond)
    transport = Transport(store, KEY, token)
    request = httpx2.Request("POST", url, json={"model": "test"})
    result = transport.handle_request(request)
    assert result.status_code == 401 and len(calls) == 1
    assert FAKE not in repr((result.content, result.headers, request.headers))
    for destination in ("http://api.openai.com/v1/chat/completions", "https://evil.example/v1/messages",
                        url + "?secret=value", url + "/models"):
        with pytest.raises(RuntimeError):
            transport.handle_request(httpx2.Request("POST", destination))
    assert len(calls) == 1
    store.entries[KEY].expires = 0
    with pytest.raises(RuntimeError) as failure:
        transport.handle_request(httpx2.Request("POST", url))
    assert FAKE not in str(failure.value) and len(calls) == 1
    transport.close()


def test_secret_routes_cannot_be_submitted_as_durable_operations():
    from workbench.hosted_operations import HostedOperations
    for path in em.PATHS:
        with pytest.raises(ValueError):
            HostedOperations._request({"id": "a" * 32, "method": "POST", "path": path,
                "body": {"provider": "openai", "model": "gpt-test", "api_key": FAKE}}, [])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_existing_six_role_graph_and_read_only_tools_are_preserved(tmp_path, monkeypatch, provider):
    import httpx2
    from workbench.agents import build_agents, ROLES
    from workbench.tests.test_agents import sample_model
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("Compile must not invoke provider"))
    store, token, _ = configured(provider=provider)
    runtime = store.runtime(KEY, token, tmp_path)
    with build_agents(tmp_path, sample_model(), "wf", "original",
                      role_models=runtime.role_models, identity_scope=runtime.identity_scope) as bundle:
        assert set(bundle.role_models) == set(ROLES) == set(ROLE_PURPOSES)
        assert bundle.role_models == runtime.role_models
        graphs = {"supervisor": bundle.supervisor, **bundle.specialists}
        assert set(graphs) == set(ROLES)
        for role, graph in graphs.items():
            tools = graph.nodes["tools"].bound.tools_by_name
            assert "source_contracts" in tools and "sql_role_inventory" in tools
            assert not ({"execute", "write_file", "edit_file", "delete"} & set(tools))
            assert ("task" in tools) is (role == "supervisor")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert FAKE.encode() not in path.read_bytes()


@pytest.mark.parametrize("provider,url", [("openai", "https://api.openai.com/v1/chat/completions"),
                                         ("anthropic", "https://api.anthropic.com/v1/messages")])
def test_async_transport_redacts_and_makes_exactly_one_allowed_request(monkeypatch, provider, url):
    import httpx2
    from workbench.external_model_transport import AsyncTransport
    store, token, _ = configured(provider=provider)
    calls = []
    async def respond(self, request):
        calls.append(request.url)
        header = 'authorization' if provider == 'openai' else 'x-api-key'
        assert request.headers[header] == ('Bearer ' if provider == 'openai' else '') + FAKE
        return httpx2.Response(403, content=FAKE.encode(), headers={"x-secret": FAKE})
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", respond)
    async def exercise():
        transport = AsyncTransport(store, KEY, token)
        try:
            request = httpx2.Request("POST", url, json={"model": "test"})
            response = await transport.handle_async_request(request)
            assert response.status_code == 403 and FAKE not in repr((response.content, response.headers, request.headers))
            store.entries[KEY].expires = 0
            with pytest.raises(RuntimeError):
                await transport.handle_async_request(httpx2.Request("POST", url))
        finally:
            await transport.aclose()
    asyncio.run(exercise())
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_model_error_callbacks_cannot_observe_provider_echoed_key(tmp_path, monkeypatch, provider, caplog):
    import httpx2
    from langchain_core.callbacks import BaseCallbackHandler
    seen = []
    class Capture(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, **kwargs):
            seen.append(repr(serialized))
        def on_llm_error(self, error, **kwargs):
            seen.append(str(error))
    def reject(self, request):
        return httpx2.Response(403, content=json.dumps({"error": {"message": FAKE}}).encode())
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject)
    store, token, _ = configured(provider=provider)
    runtime = store.runtime(KEY, token, tmp_path)
    with pytest.raises(Exception):
        runtime.role_models["artifact"].invoke("Synthetic test only", config={"callbacks": [Capture()]})
    assert seen and FAKE not in repr(seen) and FAKE not in caplog.text