"""Offline-only contract tests. No real credentials, endpoints or inference.

These tests deliberately use fake SDK services and HTTP mock transports. They
are supplied for the parent regression run, not automatically executed here.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
import openai
import pytest
from databricks.sdk.config import Config
from databricks.sdk.service.serving import EndpointState, ServingEndpoint

from workbench import databricks_models as dm


HOST = "https://workspace.example.com"
OTHER = "https://other.example.com"
DECLARATION = "Operator reviewed provider tool documentation, reference DOC-42"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in offline model routing tests")
    import socket
    original_socket_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    # Asyncio's Windows ProactorEventLoop uses socket.socketpair() at loop
    # construction, whose fallback internally calls socket.socket.connect.
    # Return the original only so this one test can construct a local loop,
    # then restore the network guard before any async model invocation.
    return original_socket_connect


class FakeConfig:
    def __init__(self, host=HOST, token="fake-user-one"):
        self.host = host
        self.token = token
        self.calls = 0
        self.fail = False

    def authenticate(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Bearer private-upstream-token")
        return {"Authorization": "Bearer " + self.token}


class FakeClient:
    def __init__(self, user="user-one", host=HOST, endpoints=None):
        self.config = FakeConfig(host)
        self.me_calls = 0
        self.list_calls = 0
        self.fail_me = False
        self.fail_list = False
        self.user = user
        self.endpoints = endpoints if endpoints is not None else endpoint_list()
        self.current_user = SimpleNamespace(me=self.me)
        self.serving_endpoints = SimpleNamespace(list=self.list)

    def me(self):
        self.me_calls += 1
        if self.fail_me:
            raise RuntimeError("Bearer private-upstream-token")
        return SimpleNamespace(id=self.user)

    def list(self):
        self.list_calls += 1
        if self.fail_list:
            raise RuntimeError("Authorization=private-upstream-token")
        return iter(self.endpoints)


def endpoint_list():
    return [{"name": "chat-" + role, "task": "llm/v1/chat", "state": {"ready": "READY"}}
            for role in dm.ROLE_PURPOSES]


def identity(user="user-one", host=HOST, mode="profile", endpoints=None):
    client = FakeClient(user, host, endpoints)
    return dm.WorkspaceIdentity(mode, host, user, client)


def selections(endpoint=None):
    return {role: {"endpoint": endpoint or "chat-" + role, "tool_support_evidence": DECLARATION}
            for role in dm.ROLE_PURPOSES}


@pytest.fixture
def labs(monkeypatch, tmp_path):
    config = tmp_path / "profiles.cfg"
    config.write_text("[DEFAULT]\nhost = https://wrong.example.com\n"
                      "[labs]\nhost = https://workspace.example.com/\n"
                      "[other]\nhost = https://other.example.com\n", encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(config))
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "other")
    monkeypatch.setenv("DATABRICKS_HOST", OTHER)
    monkeypatch.setenv("DATABRICKS_TOKEN", "ambient-token")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "ambient-service-principal")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ambient-secret")
    return config


def test_labs_profile_is_explicit_and_environment_unchanged(labs):
    seen = []
    client = FakeClient()
    before = dict(os.environ)

    def factory(**kwargs):
        seen.append(kwargs)
        return client

    result = dm.profile_identity(client_factory=factory)
    assert result.host == HOST and result.user_id == "user-one" and result.mode == "profile"
    assert client.me_calls == 1 and client.list_calls == 0
    assert seen[0]["profile"] == "labs"
    cfg = seen[0]["config"]
    assert cfg.host == HOST and cfg.profile == "labs" and cfg.token is None
    assert cfg.client_id is None and cfg.client_secret is None
    assert cfg.auth_type == "databricks-cli"
    assert dict(os.environ) == before
    with pytest.raises(FrozenInstanceError):
        result.user_id = "changed"
    assert "FakeClient" not in repr(result) and "token" not in repr(result)


def test_profile_config_is_lazy_and_cli_failure_does_not_fallback(labs, monkeypatch):
    calls = []

    def cli(command, **kwargs):
        calls.append(command)
        raise ValueError("refresh failed, Bearer private-upstream-token")

    monkeypatch.setattr(dm.shutil, "which", lambda name: "databricks.exe")
    monkeypatch.setattr(dm.subprocess, "run", cli)
    configs = []

    def factory(**kwargs):
        cfg = kwargs["config"]
        assert calls == []
        configs.append(cfg)
        client = FakeClient()
        client.config = cfg
        client.current_user = SimpleNamespace(me=lambda: cfg.authenticate())
        return client

    with pytest.raises(dm.AuthenticationUnavailable, match="^Reauthenticate labs$") as error:
        dm.profile_identity(client_factory=factory)
    assert calls == [["databricks.exe", "auth", "token", "--profile", "labs", "--output", "json"]]
    assert configs[0].auth_type == "databricks-cli"
    assert "private-upstream-token" not in str(error.value)
    assert error.value.__suppress_context__


def test_profile_missing_explicit_host_never_inherits_default(labs):
    labs.write_text("[DEFAULT]\nhost = https://wrong.example.com\n[labs]\n", encoding="utf-8")
    with pytest.raises(dm.AuthenticationUnavailable, match="Reauthenticate labs"):
        dm.profile_identity(client_factory=lambda **kw: pytest.fail("must not construct client"))


def test_other_profile_is_rejected():
    with pytest.raises(dm.RoutingError, match="labs"):
        dm.profile_identity("other")


def test_obo_has_explicit_credentials_strategy_and_current_user(labs):
    captured = []

    def factory(**kwargs):
        captured.append(kwargs)
        client = FakeClient("forwarded-user")
        client.config = kwargs["config"]
        return client

    result = dm.obo_identity(HOST.upper() + "/", "forwarded-user-token", True, HOST,
                             client_factory=factory)
    assert result.mode == "obo" and result.user_id == "forwarded-user" and result.host == HOST
    args = captured[0]
    assert args["host"] == HOST and args["auth_type"] == "pat"
    assert args["token"] == "forwarded-user-token"
    cfg = args["config"]
    assert cfg.profile is None and cfg.client_secret is None and cfg.client_id is None
    assert isinstance(cfg._credentials_strategy, dm._ForwardedCredentials)
    assert cfg.authenticate() == {"Authorization": "Bearer forwarded-user-token"}
    assert "forwarded-user-token" not in repr(result)


@pytest.mark.parametrize("token,trusted,deployment,host", [
    (None, True, HOST, HOST), ("", True, HOST, HOST), ("Bearer value", True, HOST, HOST),
    ("a\r\nb", True, HOST, HOST), ("x", False, HOST, HOST), ("x", 1, HOST, HOST),
    ("x", True, None, HOST), ("x", True, HOST, OTHER),
])
def test_obo_rejects_missing_or_untrusted_credentials_without_factory(token, trusted, deployment, host):
    with pytest.raises(dm.AuthenticationUnavailable):
        dm.obo_identity(host, token, trusted, deployment,
                        client_factory=lambda **kw: pytest.fail("no fallback permitted"))


@pytest.mark.parametrize("host", ["http://workspace.example.com", "https://user:pass@workspace.example.com",
    HOST + "/serving-endpoints", HOST + "?token=x", HOST + "#x", HOST + ":444",
    "https://workspace.example.com\\evil", "https://workspace.example.com\n"])
def test_invalid_origins_are_rejected(host):
    with pytest.raises(dm.RoutingError):
        dm.obo_identity(host, "x", True, HOST)


@pytest.mark.parametrize("mode", ["profile", "obo"])
def test_failed_user_resolution_is_sanitized_without_fallback(labs, mode):
    client = FakeClient()
    client.fail_me = True
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return client

    with pytest.raises(dm.AuthenticationUnavailable) as error:
        if mode == "profile":
            dm.profile_identity(client_factory=factory)
        else:
            dm.obo_identity(HOST, "fake-forwarded", True, HOST, factory)
    assert len(calls) == 1 and client.me_calls == 1
    assert "private-upstream-token" not in str(error.value)


def test_inventory_uses_only_explicit_task_and_safe_metadata(tmp_path):
    endpoints = [
        ServingEndpoint(name="native-chat", task="llm/v1/chat", state=EndpointState.from_dict({"ready": "READY"})),
        {"name": "looks-like-chat-with-tools", "description": "Bearer private-upstream-token",
         "ai_gateway": {"token": "private-upstream-token"}, "state": {"ready": "READY"}},
        {"name": "vectors", "task": "llm/v1/embeddings"},
        {"name": "completion", "task": "llm/v1/completions"},
        {"name": "custom", "task": "custom"},
        {"name": "metadata-chat", "metadata": {"task": "llm/v1/chat"}},
        {"name": "unknown-task", "task": "Bearer private-upstream-token"},
    ]
    who = identity(endpoints=endpoints)
    router = dm.WorkspaceModels(tmp_path)
    inventory = router.discover(who)
    by_name = {e["name"]: e for e in inventory["endpoints"]}
    assert len(by_name) == len(endpoints) and who.client.list_calls == 1
    assert by_name["native-chat"]["type"] == "chat"
    assert by_name["metadata-chat"]["type"] == "chat"
    assert by_name["looks-like-chat-with-tools"]["type"] == "unknown"
    assert by_name["vectors"]["type"] == "embedding"
    assert by_name["completion"]["type"] == "completions"
    assert by_name["custom"]["type"] == "custom"
    assert all(e["tool_capability"] == "unknown" for e in by_name.values())
    assert "private-upstream-token" not in json.dumps(inventory)
    assert not router.path.exists()


def test_saved_route_validation_lists_once_without_detail_gets_or_model_clients(tmp_path, monkeypatch):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    original = router.configure(who, selections(), router.discover(who))
    before = who.client.list_calls
    monkeypatch.setattr(dm, 'build_chat_models', lambda *a, **k: pytest.fail('Metadata only'))
    # FakeClient deliberately has no per-endpoint get or inference API.
    assert router.validated_routes(who) == original
    assert who.client.list_calls == before + 1
    who.client.endpoints[0]['state']['ready'] = 'NOT_READY'
    with pytest.raises(dm.RoutingError, match='READY'):
        router.validated_routes(who)


@pytest.mark.parametrize("name", ["../chat", "a/b", "a\\b", "https://example.com", "catalog.schema.model",
                                 "a?b", "a%2fb", "", "..", "a\n"])
def test_endpoint_names_are_single_segments(tmp_path, name):
    router = dm.WorkspaceModels(tmp_path)
    who = identity(endpoints=[{"name": name}])
    with pytest.raises(dm.DiscoveryUnavailable):
        router.discover(who)


def test_all_six_roles_are_independently_selected_persisted_and_nonsecret(tmp_path):
    who = identity()
    who.client.config.token = "private-user-token"
    router = dm.WorkspaceModels(tmp_path)
    routes = router.configure(who, selections(), router.discover(who))
    assert len({r["endpoint"] for r in routes.values()}) == 6
    assert {k: v["purpose"] for k, v in routes.items()} == dm.ROLE_PURPOSES
    assert all(not r["tool_calling_verified"] for r in routes.values())
    assert all(r["tool_support_status"] == "declared_not_runtime_verified" for r in routes.values())
    assert dm.WorkspaceModels(tmp_path).load(who) == routes
    text = router.path.read_text(encoding="utf-8")
    assert "private-user-token" not in text and "Authorization" not in text
    assert "client_secret" not in text and "FakeClient" not in text
    assert not list(tmp_path.glob(".model-routing-*"))


@pytest.mark.parametrize("task,state", [("llm/v1/embeddings", "READY"),
    ("llm/v1/completions", "READY"), ("llm/v1/chat", "NOT_READY"), ("llm/v1/chat", None)])
def test_rejects_nonchat_or_unready_routes(tmp_path, task, state):
    who = identity(endpoints=[{"name": "selected", "task": task, "state": {"ready": state}}])
    router = dm.WorkspaceModels(tmp_path)
    with pytest.raises(dm.RoutingError):
        router.configure(who, selections("selected"), router.discover(who))
    assert not router.path.exists()


@pytest.mark.parametrize("task", ["custom", "unknown"])
def test_unknown_chat_requires_explicit_attestation_and_never_claims_verified(tmp_path, task):
    who = identity(endpoints=[{"name": "selected", "task": task, "state": {"ready": "READY"}}])
    router = dm.WorkspaceModels(tmp_path)
    inventory = router.discover(who)
    selected = selections("selected")
    with pytest.raises(dm.RoutingError, match="evidence"):
        router.configure(who, selected, inventory)
    for route in selected.values():
        route["chat_compatible_evidence"] = "Operator declaration: custom wrapper implements chat API"
    result = router.configure(who, selected, inventory)
    assert not result["supervisor"]["tool_calling_verified"]


@pytest.mark.parametrize("change", [
    {"endpoint": "absent"}, {"api_key": "private-token"}, {"token": "private-token"},
    {"purpose": "override fixed purpose"}, {"tool_calling_verified": True},
    {"tool_calling_verified": "false"}, {"tool_support_evidence": ""},
    {"tool_support_evidence": "Bearer private-token"},
    {"tool_support_evidence": "api_key=private-token"},
])
def test_invalid_route_fields_and_unprobed_claims_rejected(tmp_path, change):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    selected = selections()
    selected["supervisor"].update(change)
    with pytest.raises(dm.RoutingError):
        router.configure(who, selected, router.discover(who))
    assert not router.path.exists()


def test_inventory_provenance_user_workspace_and_mutation_checked(tmp_path):
    router = dm.WorkspaceModels(tmp_path)
    who = identity()
    inventory = router.discover(who)
    for other in (identity("different-user"), identity(host=OTHER), identity()):
        with pytest.raises(dm.RoutingError, match="inventory"):
            router.configure(other, selections(), inventory)
    modified = deepcopy(inventory)
    modified["endpoints"][0]["state"] = "NOT_READY"
    with pytest.raises(dm.RoutingError, match="inventory"):
        router.configure(who, selections(), modified)
    router.configure(who, selections(), inventory)
    assert router.load(identity("different-user")) == {}
    assert router.load(identity(host=OTHER)) == {}


def test_discovery_failure_invalidates_prior_inventory(tmp_path):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    inventory = router.discover(who)
    who.client.fail_list = True
    with pytest.raises(dm.DiscoveryUnavailable) as error:
        router.discover(who)
    assert "private-upstream-token" not in str(error.value)
    with pytest.raises(dm.RoutingError, match="inventory"):
        router.configure(who, selections(), inventory)


def test_status_is_lazy_without_identity_or_network(tmp_path):
    router = dm.WorkspaceModels(tmp_path)
    assert not tmp_path.joinpath("model-routing.json").exists()
    assert router.status()["authentication"] == "not_resolved"
    who = identity()
    assert not router.status(who)["configured"]
    assert who.client.me_calls == who.client.list_calls == who.client.config.calls == 0
    assert not list(tmp_path.iterdir())


def test_models_are_callback_based_refresh_per_call_and_identity_isolated(tmp_path):
    router = dm.WorkspaceModels(tmp_path)
    first, second = identity(), identity("user-two", mode="obo")
    first_routes = router.configure(first, selections(), router.discover(first))
    second_routes = router.configure(second, selections(), router.discover(second))
    models = dm.build_chat_models(first, first_routes)
    other = dm.build_chat_models(second, second_routes)
    assert first.client.config.calls == second.client.config.calls == 0
    assert set(models) == set(dm.ROLE_PURPOSES)
    model = models["supervisor"]
    assert isinstance(model, dm.ChatOpenAI)
    assert model.openai_api_base == HOST + "/serving-endpoints"
    assert model.request_timeout == 60 and model.max_retries == 0 and model.temperature is None
    assert model.use_responses_api is False
    assert model.openai_api_key() == "fake-user-one"
    first.client.config.token = "refreshed-first-user"
    second.client.config.token = "separate-second-user"
    assert model.openai_api_key() == "refreshed-first-user"
    assert other["artifact"].openai_api_key() == "separate-second-user"
    assert first.client.config.calls == 2 and second.client.config.calls == 1
    for secret in ("fake-user-one", "refreshed-first-user", "separate-second-user"):
        assert secret not in repr(model) and secret not in repr(first)
        assert secret not in str(model.to_json()) and secret not in router.path.read_text(encoding="utf-8")
    first.client.config.fail = True
    with pytest.raises(dm.AuthenticationUnavailable, match="^Reauthenticate labs$"):
        model.openai_api_key()
    assert other["artifact"].openai_api_key() == "separate-second-user"


def test_chat_models_rechecks_selected_endpoint_without_invoking(tmp_path):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    router.configure(who, selections(), router.discover(who))
    who.client.endpoints = []
    with pytest.raises(dm.RoutingError, match="absent"):
        router.chat_models(who)
    assert who.client.config.calls == 0


def test_concurrent_instances_preserve_each_identity_and_whole_json(tmp_path):
    identities = [identity("user-" + str(i)) for i in range(12)]

    def save(who):
        router = dm.WorkspaceModels(tmp_path)
        router.configure(who, selections(), router.discover(who))
        return router.load(who)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(save, identities))
    document = json.loads((tmp_path / "model-routing.json").read_text(encoding="utf-8"))
    assert len(document["identities"]) == len(identities)
    assert all(len(routes) == 6 for routes in results)


def test_corrupt_or_secret_bearing_store_is_not_rewritten(tmp_path):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    inventory = router.discover(who)
    router.path.write_text('{"version":1,"identities":{},"token":"private-token"}', encoding="utf-8")
    before = router.path.read_bytes()
    with pytest.raises(dm.RoutingError, match="routing document"):
        router.configure(who, selections(), inventory)
    assert router.path.read_bytes() == before


def test_explicit_config_uses_sdk_descriptors_and_no_ambient_auth(labs):
    cfg = dm._ExplicitConfig(host=HOST, token="forwarded", auth_type="pat",
                             credentials_strategy=dm._ForwardedCredentials("forwarded"))
    assert list(cfg.attributes()) == list(Config.attributes())
    copied = cfg.copy()
    assert copied.host == HOST and copied.token == "forwarded"
    assert copied.authenticate() == {"Authorization": "Bearer forwarded"}
    assert copied.client_id is None and copied.client_secret is None


def test_real_sdk_client_construction_has_no_metadata_probe_or_auth(labs, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Construction must not probe metadata or invoke CLI authentication")

    monkeypatch.setattr(dm, "databricks_cli", forbidden)
    from databricks.sdk import WorkspaceClient
    cfg = dm._ExplicitConfig(host=HOST, profile="labs", auth_type="databricks-cli",
                             credentials_strategy=dm._ProfileCredentials())
    client = WorkspaceClient(profile="labs", config=cfg)
    assert client.config.host == HOST and client.config.profile == "labs"


def test_mock_http_sync_and_async_use_fresh_user_authorization(tmp_path, monkeypatch, no_network):
    """Exercise installed LangChain/OpenAI callback wiring with no network."""
    requests = []

    def respond(request):
        requests.append((str(request.url), request.headers["authorization"],
                         json.loads(request.content)))
        return httpx2.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0,
            "model": "chat-supervisor",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "offline"},
                         "finish_reason": "stop"}],
        })

    sync_http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    async_http = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    chat_class = dm._DatabricksChatOpenAI

    def chat(**kwargs):
        return chat_class(**kwargs, http_client=sync_http, http_async_client=async_http)

    monkeypatch.setattr(dm, "_DatabricksChatOpenAI", chat)
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    routes = router.configure(who, selections(), router.discover(who))
    model = dm.build_chat_models(who, routes)["supervisor"]
    assert who.client.config.calls == 0
    try:
        assert model.invoke("offline").content == "offline"
        who.client.config.token = "refreshed-user-token"
        assert model.invoke("offline").content == "offline"

        async def invoke_and_close():
            try:
                who.client.config.token = "async-user-token"
                assert (await model.ainvoke("offline")).content == "offline"
            finally:
                await async_http.aclose()

        # Build the local Windows Proactor socketpair before restoring the
        # network-deny guard. The async OpenAI request still uses MockTransport.
        import socket
        with patch.object(socket.socket, "connect", no_network):
            loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(invoke_and_close())
        finally:
            loop.close()
    finally:
        sync_http.close()
    assert [r[1] for r in requests] == ["Bearer fake-user-one", "Bearer refreshed-user-token",
                                       "Bearer async-user-token"]
    assert all(r[0] == HOST + "/serving-endpoints/chat/completions" for r in requests)
    assert all(r[2]["model"] == "chat-supervisor" and "temperature" not in r[2] for r in requests)
    assert who.client.config.calls == 3


def test_atomic_replace_failure_preserves_previous_routes_and_cleans_temp(tmp_path, monkeypatch):
    who = identity()
    router = dm.WorkspaceModels(tmp_path)
    inventory = router.discover(who)
    router.configure(who, selections(), inventory)
    original = router.path.read_bytes()

    def fail_replace(*args):
        raise OSError("offline simulated replacement failure")

    monkeypatch.setattr(dm.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        router.configure(who, selections(), inventory)
    assert router.path.read_bytes() == original
    assert not list(tmp_path.glob(".model-routing-*"))