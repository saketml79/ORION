"""Offline Volume contracts, real XML staging/parsing, no workspace calls."""
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

from databricks.sdk.core import ApiClient
from databricks.sdk.errors import NotFound, PermissionDenied, Unauthenticated
from fastapi.testclient import TestClient
import pytest

from workbench.app import create_app, _create_package_app
from workbench import databricks_models as dm
from workbench import volume_intake as vi
from workbench.projects import install_projects

BASE = "/api/databricks/volumes"
HOST = "https://workspace.example.com"
ROOT = "/Volumes/C/S/V"
MUTATION = {"X-Workbench-Request": "1"}
XML = (b'<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">'
       b'<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>'
       b'<WORKFLOW NAME="VolumeWorkflow"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/>'
       b'</WORKFLOW></FOLDER></REPOSITORY></POWERMART>')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Do not break Windows' asyncio loopback socketpair initialization.
    def blocked(*args, **kwargs):
        raise AssertionError("Live SDK calls forbidden")
    monkeypatch.setattr(ApiClient, "do", blocked)
    monkeypatch.setattr(dm, "databricks_cli", blocked)
    monkeypatch.setattr(dm.subprocess, "run", blocked)


class FakeSDK:
    def __init__(self):
        self.config = SimpleNamespace(host=HOST)
        self.user_id = "user-one"
        self.calls = []
        self.error = None
        self.entries = []
        self.payloads = {ROOT + "/exports/source.XML": XML}
        self.streams = []
        self.current_user = SimpleNamespace(me=self.me)
        self.catalogs = SimpleNamespace(list=lambda **kwargs: self.listing("catalogs", kwargs))
        self.schemas = SimpleNamespace(list=lambda **kwargs: self.listing("schemas", kwargs))
        self.volumes = SimpleNamespace(list=lambda **kwargs: self.listing("volumes", kwargs))
        self.files = SimpleNamespace(list_directory_contents=self.directory, download=self.download)

    def check(self):
        if self.error:
            raise self.error

    def me(self):
        self.calls.append(("me",))
        self.check()
        return SimpleNamespace(id=self.user_id)

    def listing(self, kind, kwargs):
        self.calls.append((kind, kwargs))
        self.check()
        return iter([SimpleNamespace(name={"catalogs": "C", "schemas": "S", "volumes": "V"}[kind])])

    def directory(self, path, **kwargs):
        self.calls.append(("directory", path, kwargs))
        self.check()
        return iter(self.entries)

    def download(self, path):
        self.calls.append(("download", path))
        self.check()
        payload = self.payloads[path]
        stream = payload if hasattr(payload, "read") else BytesIO(payload)
        self.streams.append(stream)
        return SimpleNamespace(contents=stream, content_length=None)


def test_client(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


# This helper is not a test function.
test_client.__test__ = False


def post(client, route, **kwargs):
    return client.post(route, headers={**MUTATION, **kwargs.pop("headers", {})}, **kwargs)


@pytest.fixture
def workspace(tmp_path):
    sdk = FakeSDK()
    app = _create_package_app(tmp_path / "data", tmp_path / "unused.xml")
    install_projects(app, _create_package_app, tmp_path / "inputs")
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return dm.WorkspaceIdentity("profile", HOST, sdk.user_id, sdk)

    service = vi.install_volume_intake(app, identity_factory=factory)
    with test_client(app) as client:
        project = post(client, "/api/projects", json={"name": "Volume import"}).json()["id"]
        yield client, app, sdk, service, project, factory_calls


def connect(workspace):
    client, *_ = workspace
    result = post(client, BASE + "/connect")
    assert result.status_code == 200, result.text
    return result.json()


def body(project, **kwargs):
    return {"project_id": project, "catalog": "C", "schema": "S", "volume": "V",
            "paths": ["exports/source.XML"], "consent": True, **kwargs}


def assert_clean(app):
    assert not list(app.state.projects.uploads.iterdir())
    assert app.state.projects.slot.acquire(blocking=False)
    app.state.projects.slot.release()


def test_root_hook_offline_status_and_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "profile_identity", lambda: pytest.fail("Status must not authenticate"))
    app = create_app(tmp_path / "data", tmp_path / "unused.xml")
    with test_client(app) as client:
        project = post(client, "/api/projects", json={"name": "P"}).json()["id"]
        expected = {"mode": "profile", "profile": "labs", "configured": False, "connected": False,
                    "auth_check": "not_checked", "host": None, "user_id": None,
                    "local_only": True, "production_remote_enabled": False}
        assert client.get(BASE + "/status").json() == expected
        assert client.get(BASE + "/status", headers={"X-Workbench-Project": project,
                          "X-Workbench-Package": "a" * 64}).json() == expected
        assert client.get(BASE + "/catalogs").status_code == 401
        assert client.get(BASE + "/status").headers["x-content-type-options"] == "nosniff"
        assert app.state.volume_intake.identity is None


def test_connect_rechecks_identity_without_inventory_or_models(workspace):
    client, app, sdk, service, _, factory_calls = workspace
    assert not client.get(BASE + "/status").json()["configured"]
    assert sdk.calls == [] and factory_calls == []
    state = connect(workspace)
    assert state == {"mode": "profile", "profile": "labs", "configured": True, "connected": True,
                     "auth_check": "verified", "host": HOST, "user_id": "user-one",
                     "local_only": True, "production_remote_enabled": False}
    assert sdk.calls == [("me",)] and len(factory_calls) == 1
    assert client.get(BASE + "/status").json() == state
    assert sdk.calls == [("me",)]
    assert not list(app.state.projects.root.glob("**/*identity*"))
    assert service.identity.client is sdk


def test_catalog_schema_volume_metadata_contract(workspace):
    client, _, sdk, _, project, _ = workspace
    connect(workspace)
    headers = {"X-Workbench-Project": project}
    for path, name in (("/catalogs", "C"), ("/schemas?catalog=C", "S"),
                       ("/volumes?catalog=C&schema=S", "V")):
        result = client.get(BASE + path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json() == {"items": [{"name": name}], "limit": 200, "truncated": False}
    assert sdk.calls[1:] == [("catalogs", {"max_results": 200}),
                            ("schemas", {"catalog_name": "C", "max_results": 200}),
                            ("volumes", {"catalog_name": "C", "schema_name": "S", "max_results": 200})]


def test_files_filter_paths_and_exact_metadata(workspace):
    client, _, sdk, _, _, _ = workspace
    connect(workspace)
    sdk.entries = [SimpleNamespace(path=ROOT + "/exports/" + name, is_directory=isdir, file_size=size)
                   for name, isdir, size in [("nested", True, None), ("source.XML", False, 42),
                                            ("notes.txt", False, 9), ("../evil.xml", False, 1),
                                            ("nested/child.xml", False, 1), ("x%2fother.xml", False, 1)]]
    sdk.entries += [SimpleNamespace(path="/Volumes/OTHER/S/V/leak.xml", is_directory=False),
                    SimpleNamespace(path=ROOT + "/exports2/leak.xml", is_directory=False),
                    SimpleNamespace(path=ROOT + "/exports/trailing/", is_directory=True),
                    SimpleNamespace(path=ROOT + "/exports/deep/child/", is_directory=True)]
    result = client.get(BASE + "/files", params={"catalog": "C", "schema": "S", "volume": "V", "directory": "exports"})
    assert result.status_code == 200
    assert result.json() == {"items": [
        {"path": "exports/nested", "name": "nested", "size_bytes": None, "is_directory": True},
        {"path": "exports/source.XML", "name": "source.XML", "size_bytes": 42, "is_directory": False},
        {"path": "exports/trailing", "name": "trailing", "size_bytes": None, "is_directory": True}],
        "directory": "exports", "volume_path": ROOT, "limit": 200, "truncated": False}
    assert sdk.calls[-1] == ("directory", ROOT + "/exports", {"page_size": 200})


@pytest.mark.parametrize("kind", ["catalogs", "files"])
def test_iterator_bounded_to_200_including_filtered_entries(workspace, kind):
    client, _, sdk, _, _, _ = workspace
    connect(workspace)
    seen = []

    def entries():
        for i in range(1000):
            seen.append(i)
            if kind == "catalogs":
                yield {"name": "C" + str(i)}
            else:
                yield {"path": ROOT + f"/ignored{i}.txt", "is_directory": False}

    if kind == "catalogs":
        sdk.catalogs.list = lambda **kwargs: entries()
        result = client.get(BASE + "/catalogs")
    else:
        sdk.files.list_directory_contents = lambda *args, **kwargs: entries()
        result = client.get(BASE + "/files?catalog=C&schema=S&volume=V")
    assert result.status_code == 200
    assert result.json()["truncated"] is True and len(seen) == 200
    assert len(result.json()["items"]) == (200 if kind == "catalogs" else 0)


@pytest.mark.parametrize("invalid", ["../C", "C.S", "/Volumes/C", "C/S", "C\\S", "C%2fS", " C", "C:", "C\x00"])
def test_strict_identifiers_never_reach_sdk(workspace, invalid):
    client, _, sdk, _, project, _ = workspace
    connect(workspace)
    before = list(sdk.calls)
    assert client.get(BASE + "/schemas", params={"catalog": invalid}).status_code == 400
    assert post(client, BASE + "/import", json=body(project, catalog=invalid)).status_code == 400
    assert sdk.calls == before


@pytest.mark.parametrize("invalid", ["..", "../source.xml", "dir/../source.xml", "/source.xml", "dir//source.xml",
                                     "dir/./source.xml", "dir\\source.xml", "x%2fsource.xml", "x%252fsource.xml",
                                     "https://evil/source.xml", "C:/source.xml", "dir/", "source.xml\x00"])
def test_escaping_directories_and_import_paths_rejected(workspace, invalid):
    client, app, sdk, _, project, _ = workspace
    connect(workspace)
    before = list(sdk.calls)
    assert client.get(BASE + "/files", params={"catalog": "C", "schema": "S", "volume": "V", "directory": invalid}).status_code == 400
    assert post(client, BASE + "/import", json=body(project, paths=[invalid])).status_code == 400
    assert sdk.calls == before
    assert_clean(app)


def test_actual_xml_download_stage_only_then_explicit_real_parser(workspace):
    client, app, sdk, _, project, _ = workspace
    connect(workspace)
    response = post(client, BASE + "/import", headers={"X-Workbench-Project": project,
                    "X-Workbench-Package": "f" * 64}, json=body(project))
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"status", "project_id", "items", "total", "analysis_started", "job_id"}
    assert result["status"] == "staged" and result["project_id"] == project
    assert result["analysis_started"] is False and result["job_id"] is None and result["total"] == 1
    item = result["items"][0]
    assert item == {"name": "source.XML", "sha256": sha256(XML).hexdigest(), "size_bytes": len(XML),
                    "status": "staged", "created_at": item["created_at"]}
    snapshot = app.state.projects.root / project / "staged" / (item["sha256"] + ".xml")
    assert snapshot.read_bytes() == XML
    assert sdk.streams[0].closed
    assert app.state.projects.registry.records("jobs", project) == []
    assert app.state.projects.contexts == {} and not app.state.service.active
    assert_clean(app)
    analysis = post(client, f"/api/projects/{project}/analyze")
    assert analysis.status_code == 202, analysis.text
    job = analysis.json()["id"]
    app.state.projects.futures[job].result(timeout=30)
    assert client.get(f"/api/projects/{project}/jobs/{job}").json()["status"] == "completed"
    overview = client.get("/api/overview", headers={"X-Workbench-Project": project,
                          "X-Workbench-Package": item["sha256"]}).json()
    assert overview["workflows"][0]["name"] == "VolumeWorkflow"


def test_consent_wrong_project_unknown_project_and_no_auth(workspace):
    client, app, sdk, _, project, _ = workspace
    assert post(client, BASE + "/import", json=body(project)).status_code == 401
    connect(workspace)
    other = post(client, "/api/projects", json={"name": "Other"}).json()["id"]
    payload = body(project)
    del payload["consent"]
    assert post(client, BASE + "/import", json=payload).status_code == 403
    assert post(client, BASE + "/import", json=body(project, consent=False)).status_code == 403
    assert post(client, BASE + "/import", headers={"X-Workbench-Project": other}, json=body(project)).status_code == 403
    assert post(client, BASE + "/import", json=body("f" * 32)).status_code == 404
    assert all(call[0] != "download" for call in sdk.calls)
    assert_clean(app)


@pytest.mark.parametrize("change,code", [({"paths": ["notes.txt"]}, 415), ({"paths": []}, 422),
                                        ({"paths": ["x.xml"] * 11}, 422), ({"paths": ["x.xml"] * 2}, 400),
                                        ({"consent": "true"}, 422), ({"host": HOST}, 422),
                                        ({"token": "sensitive"}, 422), ({"url": "https://evil/"}, 422)])
def test_invalid_import_body(workspace, change, code):
    client, _, sdk, _, project, _ = workspace
    connect(workspace)
    assert post(client, BASE + "/import", json=body(project, **change)).status_code == code
    assert not sdk.streams


@pytest.mark.parametrize("error,code", [(Unauthenticated("Bearer private-secret"), 401),
                                      (PermissionDenied("Authorization=private-secret"), 403),
                                      (NotFound("private-secret"), 404), (RuntimeError("private-secret"), 502)])
def test_sdk_failures_are_sanitized_and_expiry_requires_explicit_reconnect(workspace, error, code):
    client, app, sdk, _, project, factory_calls = workspace
    connect(workspace)
    sdk.error = error
    response = post(client, BASE + "/import", json=body(project))
    assert response.status_code == code
    assert "private-secret" not in response.text and "Bearer" not in response.text
    assert_clean(app)
    sdk.error = None
    if code == 401:
        assert client.get(BASE + "/status").json()["auth_check"] == "reconnect_required"
        assert client.get(BASE + "/catalogs").status_code == 401
        assert len(factory_calls) == 1  # No automatic fallback or reauthentication.
        assert connect(workspace)["host"] == HOST
        assert len(factory_calls) == 2
        assert post(client, BASE + "/import", json=body(project)).status_code == 200


def test_connect_failure_clears_previous_identity_and_sanitizes(workspace):
    client, _, sdk, _, _, _ = workspace
    connect(workspace)
    sdk.error = Unauthenticated("refresh invalid_grant: Bearer private-secret")
    result = post(client, BASE + "/connect")
    assert result.status_code == 401 and "private-secret" not in result.text
    state = client.get(BASE + "/status").json()
    assert not state["configured"] and state["host"] is None and state["user_id"] is None
    sdk.error = None
    assert connect(workspace)["host"] == HOST


def test_injected_stale_identity_and_workspace_change_rejected(workspace):
    client, _, sdk, service, _, _ = workspace
    stale = dm.WorkspaceIdentity("profile", HOST, "old-user", sdk)
    service.identity_factory = lambda: stale
    assert post(client, BASE + "/connect").status_code == 401
    sdk.user_id = "old-user"
    sdk.config.host = "https://other.example.com"
    assert post(client, BASE + "/connect").status_code == 401


def test_stream_limits_chunking_aggregate_cleanup_and_stage_failure(workspace, monkeypatch):
    client, app, sdk, _, project, _ = workspace
    connect(workspace)
    monkeypatch.setattr(vi, "CHUNK_BYTES", 4)
    monkeypatch.setattr(vi, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(vi, "MAX_BATCH_BYTES", 12)
    sdk.payloads[ROOT + "/exports/source.XML"] = b"123456789"
    assert post(client, BASE + "/import", json=body(project)).status_code == 413
    assert sdk.streams[-1].closed
    assert_clean(app)
    sdk.payloads[ROOT + "/exports/source.XML"] = b"12345678"
    sdk.payloads[ROOT + "/two.xml"] = b"12345"
    assert post(client, BASE + "/import", json=body(project, paths=["exports/source.XML", "two.xml"])).status_code == 413
    assert all(s.closed for s in sdk.streams)
    assert_clean(app)
    assert app.state.projects.registry.records("packages", project) == []

    def fail(*args):
        raise OSError("private-secret")
    monkeypatch.setattr(app.state.projects, "stage", fail)
    result = post(client, BASE + "/import", json=body(project))
    assert result.status_code == 502 and "private-secret" not in result.text
    assert_clean(app)


@pytest.mark.parametrize("failure", ["read", "close", "second", "empty", "declared", "incomplete"])
def test_download_failures_close_streams_and_release_reservation(workspace, monkeypatch, failure):
    client, app, sdk, _, project, _ = workspace
    connect(workspace)
    class Broken(BytesIO):
        def read(self, n=-1):
            if failure == "read":
                raise Unauthenticated("Bearer private-secret")
            assert n == vi.CHUNK_BYTES  # Never read the entire stream at once.
            return super().read(n)

        def close(self):
            super().close()
            if failure == "close":
                raise RuntimeError("private-secret")

    stream = Broken(b"" if failure == "empty" else XML)
    sdk.payloads[ROOT + "/exports/source.XML"] = stream
    paths = ["exports/source.XML"]
    if failure == "second":
        paths.append("missing.xml")
    if failure in {"declared", "incomplete"}:
        download = sdk.files.download
        def declared(path):
            response = download(path)
            response.content_length = vi.MAX_FILE_BYTES + 1 if failure == "declared" else len(XML) + 1
            return response
        monkeypatch.setattr(sdk.files, "download", declared)
    result = post(client, BASE + "/import", json=body(project, paths=paths))
    assert result.status_code == {"read": 401, "empty": 400, "declared": 413}.get(failure, 502)
    assert "private-secret" not in result.text and stream.closed
    assert_clean(app)
    assert app.state.projects.registry.records("packages", project) == []


def test_busy_reservation_no_download(workspace):
    client, app, sdk, _, project, _ = workspace
    connect(workspace)
    app.state.projects.reserve(project)
    try:
        assert post(client, BASE + "/import", json=body(project)).status_code == 409
        assert not sdk.streams
    finally:
        app.state.projects.slot.release()


def test_default_labs_invalid_refresh_no_ambient_fallback(workspace, tmp_path, monkeypatch):
    client, _, sdk, service, _, _ = workspace
    config = tmp_path / "profiles.cfg"
    config.write_text("[labs]\nhost = https://workspace.example.com\n", encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(config))
    monkeypatch.setenv("DATABRICKS_TOKEN", "ambient-secret")
    monkeypatch.setenv("DATABRICKS_HOST", "https://other.example.com")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "other")
    calls = []
    def expired(command, **kwargs):
        calls.append(command[command.index("--profile") + 1])
        raise RuntimeError("invalid_grant refresh Bearer private-secret")
    monkeypatch.setattr(dm.shutil, "which", lambda name: "databricks.exe")
    monkeypatch.setattr(dm.subprocess, "run", expired)
    def factory(**kwargs):
        sdk.config = kwargs["config"]
        sdk.current_user.me = lambda: sdk.config.authenticate()
        return sdk
    monkeypatch.setattr(dm, "WorkspaceClient", factory)
    service.identity_factory = None
    result = post(client, BASE + "/connect")
    assert result.status_code == 401 and calls == ["labs"]
    assert "private-secret" not in result.text and "ambient-secret" not in result.text
    assert sdk.config.token is None and sdk.config.profile == "labs"
    assert not client.get(BASE + "/status").json()["configured"]
    assert client.get(BASE + "/catalogs").status_code == 401 and calls == ["labs"]


def test_http_cannot_select_credentials_and_local_boundary_preserved(workspace):
    client, _, sdk, _, _, _ = workspace
    for payload in ({"host": HOST}, {"profile": "other"}, {"token": "secret"}):
        assert post(client, BASE + "/connect", json=payload).status_code == 422
    assert client.post(BASE + "/connect").status_code == 403
    assert post(client, BASE + "/connect", headers={"Origin": "http://evil.example"}).status_code == 403
    assert post(client, BASE + "/connect", headers={"Content-Length": "65537"}, content=b"x").status_code == 413
    assert sdk.calls == []
    result = post(client, BASE + "/connect", headers={"X-Forwarded-Access-Token": "untrusted",
                  "Authorization": "Bearer untrusted", "X-Databricks-Host": "https://evil.example"})
    assert result.status_code == 200 and result.json()["host"] == HOST


def test_optional_obo_is_explicit_server_only_and_unsupported_until_ready(workspace, monkeypatch):
    client, _, sdk, service, _, _ = workspace
    monkeypatch.delenv("WORKBENCH_VOLUME_HOSTED_AUTH", raising=False)
    service.identity_factory = vi.build_obo_identity_factory()
    assert post(client, BASE + "/connect").status_code == 501
    monkeypatch.setenv("WORKBENCH_VOLUME_HOSTED_AUTH", "1")
    assert post(client, BASE + "/connect").status_code == 501
    seen = []
    def obo(host, token, **kwargs):
        seen.append((host, token, kwargs))
        return dm.WorkspaceIdentity("obo", HOST, sdk.user_id, sdk)
    monkeypatch.setattr(dm, "obo_identity", obo)
    service.identity_factory = vi.build_obo_identity_factory(deployment_host=HOST, token_provider=lambda: "server-token",
                                                           trusted_proxy=True)
    result = post(client, BASE + "/connect", headers={"X-Forwarded-Access-Token": "untrusted"})
    assert result.status_code == 200 and result.json()["mode"] == "obo" and result.json()["profile"] is None
    assert result.json()["production_remote_enabled"] is False
    assert "server-token" not in result.text and "untrusted" not in result.text
    assert seen == [(HOST, "server-token", {"trusted_proxy": True, "deployment_host": HOST})]