"""Configured workspace metadata is offline and independent of package restoration."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from workbench import databricks_models as dm
from workbench.model_access import install_model_access
from workbench.projects import ProjectDispatch


@pytest.mark.parametrize("host,expected", [
    ("https://Workspace.example.com:443/", "https://workspace.example.com"),
    ("https://user:secret@workspace.example.com", None),
    ("https://workspace.example.com/path?token=secret", None),
    ("http://workspace.example.com", None),
    ("https://workspace.example.com:8443", None),
    ("https://workspace.example.com/#secret", None),
    (None, None),
])
def test_local_context_never_authenticates_or_restores(tmp_path, monkeypatch, host, expected):
    path = tmp_path / "profiles.cfg"
    path.write_text("[DEFAULT]\nhost = https://not-labs.example.com\n[labs]\n" +
                    (f"host = {host}\n" if host else ""), encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(path))
    monkeypatch.setenv("DATABRICKS_HOST", "https://ambient.example.com")

    def forbidden(*args, **kwargs):
        pytest.fail("Workspace metadata must not authenticate, restore, or discover models")

    monkeypatch.setattr(dm, "WorkspaceClient", forbidden)
    monkeypatch.setattr(dm.subprocess, "run", forbidden)
    monkeypatch.setattr(dm.WorkspaceModels, "discover", forbidden)
    app = FastAPI()
    app.state.service = SimpleNamespace(root=tmp_path)
    install_model_access(app, identity_factory=forbidden)
    app.add_middleware(ProjectDispatch, manager=SimpleNamespace(
        registry=SimpleNamespace(project=forbidden), acquire=forbidden))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        response = client.get("/api/models/workspace", headers={
            "X-Workbench-Project": "unrestored-project", "X-Workbench-Package": "unrestored-package"})
        assert response.status_code == 200, response.text
        assert response.json() == {"workspace": expected, "mode": "local_user_profile", "profile": "labs",
                                   "auth_check": "not_checked", "connected": False}
        assert "secret" not in response.text and "not-labs" not in response.text


def test_missing_local_config_is_unknown_not_ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "missing.cfg"))
    monkeypatch.setenv("DATABRICKS_HOST", "https://ambient.example.com")
    with pytest.raises(dm.AuthenticationUnavailable, match="Configured labs workspace is unavailable"):
        dm._profile_host()


def test_local_connect_reports_measured_metadata_stages_without_clients_or_saves(tmp_path, monkeypatch):
    calls = []
    client = SimpleNamespace(config=SimpleNamespace(host='https://workspace.example.com'),
        serving_endpoints=SimpleNamespace(list=lambda: calls.append('list') or [
            {'name': 'chat', 'task': 'llm/v1/chat', 'state': {'ready': 'READY'}}]))
    identity = dm.WorkspaceIdentity('profile', client.config.host, 'user', client)
    def resolve():
        calls.append('identity')
        return identity
    def forbidden(*args, **kwargs):
        pytest.fail('Connect must not build clients, apply routes, or invoke inference')
    monkeypatch.setattr(dm, 'build_chat_models', forbidden)
    monkeypatch.setattr(dm.WorkspaceModels, 'configure', forbidden)
    clock = iter([10.0, 10.75, 11.25])
    monkeypatch.setattr('workbench.model_access.monotonic', lambda: next(clock))
    app = FastAPI()
    app.state.service = SimpleNamespace(root=tmp_path)
    install_model_access(app, identity_factory=resolve)
    with TestClient(app) as http:
        response = http.post('/api/models/connect')
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['timings'] == {'authentication_seconds': 0.75, 'endpoint_list_seconds': 0.5}
        assert data['connected'] is True and data['runtime_verified'] is False
        assert len(data['endpoints']) == 1
    assert calls == ['identity', 'list']
    assert not (tmp_path / 'models' / 'model-routing.json').exists()