"""Parent-run hosted integration checks against existing fake Delta/OBO backend."""
from copy import deepcopy
import json

import pytest

from workbench import external_models as em
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted import HostedDispatcher
from workbench.tests.test_external_models import FAKE, clean_sessions
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B
from workbench.tests.test_hosted_registry import uploaded


def selected(project, package, token=TOKEN_A2):
    return headers(token, **{"X-Workbench-Project": project, "X-Workbench-Package": package,
                            "Content-Type": "application/json"})


def test_hosted_configuration_uses_fresh_obo_no_durable_secret_or_sdk(backend, uploaded, monkeypatch, caplog):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    expected_rows, expected_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    verified = []
    original = backend.resolve
    def resolve(host, token, **kwargs):
        verified.append(token)
        return original(host, token, **kwargs)
    monkeypatch.setattr(backend, "resolve", resolve)
    def forbidden(*args, **kwargs):
        pytest.fail("External credential configuration must not hydrate, persist, construct clients or invoke models")
    monkeypatch.setattr(DeltaRuntime, "restore", forbidden)
    monkeypatch.setattr(DeltaRuntime, "restore_audit", forbidden)
    monkeypatch.setattr(DeltaRuntime, "flush", forbidden)
    monkeypatch.setattr(em, "build_runtime", forbidden)
    backend.calls.clear()
    scope = selected(project, package)
    with browser(backend) as client:
        default = client.get(em.PREFIX + "status", headers=scope)
        assert default.status_code == 200 and default.json()["mode"] == "databricks"
        connected = client.post(em.PREFIX + "connect", headers=scope,
            json={"provider": "anthropic", "model": "claude-test", "api_key": FAKE})
        assert connected.status_code == 200, connected.text
        assert connected.json()["status"] == "configured" and connected.json()["verified"] is False
        cookie = connected.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        status = client.get("/api/models/status", headers=scope)
        assert status.status_code == 200 and status.json()["mode"] == "external"
        assert status.json()["databricks_controls_disabled"] is True
        blocked = client.post("/api/models/connect", headers=scope)
        assert blocked.status_code == 409
        malformed = client.post(em.PREFIX + "connect", headers=scope,
            content=json.dumps({"provider": "openai", "model": [], "api_key": FAKE}))
        assert malformed.status_code == 422 and FAKE not in malformed.text
        # A fresh OBO user never inherits Alice's external credentials even when
        # this client carries the same cookie and project/package headers.
        other = client.get(em.PREFIX + "status", headers=selected(project, package, TOKEN_B))
        assert other.status_code in {404, 503}
        disconnected = client.post(em.PREFIX + "disconnect", headers=scope, json={})
        assert disconnected.status_code == 200 and disconnected.json()["mode"] == "databricks"
    assert verified.count(TOKEN_A2) >= 6 and TOKEN_B in verified
    assert backend.rows == expected_rows and backend.objects == expected_objects
    assert FAKE not in repr(backend.calls) and FAKE not in caplog.text
    sql = [statement for _, action, statement in backend.calls if action == "sql"]
    assert not any(statement.startswith(("CREATE", "MERGE", "INSERT", "UPDATE", "DELETE")) for statement in sql)
    assert not any(".`audit_events`" in statement for statement in sql)


@pytest.mark.parametrize("case,expected", [("no_auth", 401), ("no_header", 403),
    ("cross_origin", 403), ("too_large", 413), ("bad_scope", 409)])
def test_hosted_secret_route_rejections_never_read_or_record_body(backend, monkeypatch, case, expected):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected external request must not reach Delta or durable operation handling")
    monkeypatch.setattr(HostedDispatcher, "authorize_external_scope", forbidden)
    scope = selected("1" * 32, "2" * 64)
    if case == "no_auth":
        scope.pop("x-forwarded-access-token", None)
    elif case == "no_header":
        scope.pop("X-Workbench-Request", None)
    elif case == "cross_origin":
        scope["origin"] = "https://evil.example"
    elif case == "too_large":
        scope["content-length"] = str(em.MAX_BODY + 1)
    else:
        scope["X-Workbench-Package"] = "invalid"
    with browser(backend) as client:
        result = client.post(em.PREFIX + "connect", headers=scope,
            content=json.dumps({"provider": "openai", "model": "gpt-test", "api_key": FAKE}))
        assert result.status_code == expected, result.text
        assert FAKE not in result.text
    assert not backend.rows and not backend.objects


def test_hosted_expired_external_fails_before_databricks_route_or_model_job(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    stores = []
    original = em.Sessions.connect
    def connect(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        stores.append(self)
        return result
    monkeypatch.setattr(em.Sessions, "connect", connect)
    scope = selected(project, package)
    with browser(backend) as client:
        result = client.post(em.PREFIX + "connect", headers=scope,
            json={"provider": "openai", "model": "gpt-test", "api_key": FAKE})
        assert result.status_code == 200
        store = stores[-1]
        key = next(iter(store.entries))
        entry = store.entries[key]
        secret = entry.secret
        store.begin(key, "queued-model-operation")
        blocked = client.post(em.PREFIX + "disconnect", headers=scope, json={})
        assert blocked.status_code == 409
        entry.expires = 0
        status = client.get("/api/models/status", headers=scope)
        assert status.status_code == 200 and status.json()["status"] == "expired"
        assert status.json()["inference_ready"] is False and not secret
        result = client.post("/api/agents/run", headers=scope, json={"consent": True})
        assert result.status_code == 409 and "expired" in result.text
        store.end(key, "queued-model-operation")
        result = client.post(em.PREFIX + "disconnect", headers=scope, json={})
        assert result.status_code == 200 and result.json()["mode"] == "databricks"