"""Offline hosted boundary, real parser/SQLite/Files API contract tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace

from databricks.sdk.core import ApiClient
from databricks.sdk.errors import AlreadyExists, NotFound, PermissionDenied
from fastapi.testclient import TestClient
import pytest

from workbench import databricks_models as dm
from workbench.hosted import ActionExecutor, HostedConfig, _CURRENT, create_hosted_app, current_identity
from workbench.hosted_storage import VolumeSnapshots, provision, volume_path

HOST = "https://workspace.example.com"
ORIGIN = "https://workbench.example.com"
TOKEN_A = "forwarded-oauth-credential-alice-1234567890"
TOKEN_B = "forwarded-oauth-credential-bob-1234567890"
TOKEN_A2 = "refreshed-oauth-credential-alice-9876543210"
XML = (b'<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">'
       b'<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>'
       b'<WORKFLOW NAME="W"><TASKINSTANCE NAME="T" TASKNAME="S" TASKTYPE="Session"/>'
       b'</WORKFLOW></FOLDER></REPOSITORY></POWERMART>')


class Backend:
    def __init__(self):
        self.objects, self.catalog_data, self.schema_data, self.volume_data = {}, {}, {}, {}
        self.infos, self.rows = {}, {}
        self.calls = []
        self.fail_head = False
        self.block_upload, self.release_upload = None, None

    def client(self, token):
        username, user_id = (("alice@example.com", "alice-id") if token in {TOKEN_A, TOKEN_A2}
                             else ("bob@example.com", "bob-id"))

        def get(table, key):
            if key not in table:
                raise NotFound("absent")
            return table[key]

        def create(table, key):
            if key in table:
                raise AlreadyExists("exists")
            table[key] = SimpleNamespace(owner=username)
            self.calls.append((token, "create", key))
            return table[key]

        def upload(path, stream, overwrite=False):
            self.calls.append((token, "upload", path))
            if self.block_upload is not None:
                self.block_upload.set()
                assert self.release_upload.wait(10)
            if self.fail_head and path.endswith("/HEAD.json"):
                raise PermissionDenied("sensitive upstream message " + token)
            if path in self.objects and not overwrite:
                raise AlreadyExists("exists")
            self.objects[path] = stream.read()

        def download(path):
            self.calls.append((token, "download", path))
            return SimpleNamespace(contents=BytesIO(get(self.objects, path)), content_length=len(get(self.objects, path)))

        def metadata(path):
            self.calls.append((token, "metadata", path))
            return SimpleNamespace(content_length=len(get(self.objects, path)))

        def listing(**kwargs):
            return iter([SimpleNamespace(name="external")])

        from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
        sql_client = RuntimeStatementClient()
        sql_client.user = SimpleNamespace(id=user_id, user_name=username)
        sql_client.infos, sql_client.rows = self.infos, self.rows

        def execute(**kw):
            self.calls.append((token, "sql", kw["statement"]))
            if self.fail_head and kw["statement"].startswith("MERGE INTO") and ".`migration_receipts`" in kw["statement"]:
                raise PermissionDenied("sensitive upstream message " + token)
            return sql_client.execute(**kw)

        return SimpleNamespace(
            config=SimpleNamespace(host=HOST, authenticate=lambda: {"Authorization": "Bearer " + token}),
            current_user=SimpleNamespace(me=lambda: SimpleNamespace(id=user_id, user_name=username)),
            catalogs=SimpleNamespace(get=lambda name: get(self.catalog_data, name),
                                     create=lambda name, **kwargs: create(self.catalog_data, name), list=listing),
            schemas=SimpleNamespace(get=lambda name: get(self.schema_data, name),
                                    create=lambda name, catalog_name: create(self.schema_data, catalog_name + "." + name), list=listing),
            volumes=SimpleNamespace(read=lambda name: get(self.volume_data, name),
                                    create=lambda catalog, schema, name, kind: create(self.volume_data, catalog + "." + schema + "." + name), list=listing),
            files=SimpleNamespace(upload=upload, download=download, create_directory=lambda path: None,
                                  get_metadata=metadata,
                                  list_directory_contents=lambda path, **kwargs: iter([])),
            tables=sql_client.tables,
            statement_execution=SimpleNamespace(execute_statement=execute, get_statement=sql_client.get_statement),
            serving_endpoints=SimpleNamespace(list=lambda: iter([SimpleNamespace(
                name="chat", task="llm/v1/chat", state=SimpleNamespace(ready="READY"))])))

    def resolve(self, host, token, **kwargs):
        assert host == HOST and kwargs == {"trusted_proxy": True, "deployment_host": HOST}
        return dm.obo_identity(host, token, **kwargs, client_factory=lambda **kw: self.client(token))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    monkeypatch.setattr(ApiClient, "do", lambda *a, **k: pytest.fail("Live SDK call forbidden"))
    monkeypatch.setattr(dm, "databricks_cli", lambda *a, **k: pytest.fail("No CLI fallback in hosted mode"))


@pytest.fixture
def backend():
    return Backend()


@contextmanager
def browser(backend):
    app = create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="warehouse"), identity_resolver=backend.resolve)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        yield client


def headers(token=TOKEN_A, **extra):
    return {"x-forwarded-access-token": token, "X-Workbench-Request": "1", **extra}


def create(client, name="Project", token=TOKEN_A):
    response = client.post("/api/projects", headers=headers(token), json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def save_model(client, body, scope=None):
    """Exercise real acceptance and terminal journal receipt, not old HTTP save."""
    from time import monotonic
    from uuid import uuid4
    import httpx
    scope = scope or headers()
    status = client.get('/api/models/status', headers=scope)
    assert status.status_code == 200, status.text
    operation = uuid4().hex
    response = client.post('/api/models/select', headers=scope, json={**body,
        'operation_id': operation, 'expected_route_version': status.json()['route_version']})
    assert response.status_code == 202, response.text
    deadline = monotonic() + 30
    while monotonic() < deadline:
        response = client.get('/api/hosted/operations/' + operation, headers=scope)
        assert response.status_code == 200, response.text
        data = response.json()
        if data['status'] == 'completed':
            return httpx.Response(data['status_code'], json=data['result'])
        assert data['status'] in {'queued', 'running'}, data
    raise AssertionError('Model save did not produce a terminal journal receipt')


def test_missing_token_and_no_ambient_fallback(backend, monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "ambient-secret-not-allowed")
    with browser(backend) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/api/projects")
        assert response.status_code == 401
        assert client.get("/", headers=headers()).status_code == 200
        assert client.get("/static/app.js", headers=headers()).status_code == 200
    assert not backend.calls


def test_hosted_capabilities_separate_obo_app_inference_and_jobs(backend, monkeypatch):
    from workbench.agents import AgentRuntime
    from workbench.workflow_artifacts import supported_workflows
    monkeypatch.setattr(AgentRuntime, "run", lambda *a, **k: pytest.fail("No inference allowed"))
    with browser(backend) as client:
        create(client)
        assert client.get("/api/overview").status_code == 401
        for token in (TOKEN_A, TOKEN_A2):
            health = client.get("/api/health", headers=headers(token))
            assert health.status_code == 200
            live_caps = health.json()["capabilities"]
            inventory = health.json()["capability_inventory"]
            assert inventory["version"] == "capability-inventory-v1"
            assert len(inventory["items"]) == 15
            auth = next(r for r in inventory["items"] if r["key"] == "authentication")
            assert auth["runtime_validation"] == "request_verified"
            assert all(r["runtime_validation"] == "not_verified" for r in inventory["items"] if r["key"] != "authentication")
            assert live_caps["authentication"]["status"] == "request_obo_authenticated"
            assert live_caps["generation"]["supported_workflows"] == supported_workflows()
            assert live_caps["deployment"] is False
            assert token not in health.text
            response = client.get("/api/overview", headers=headers(token))
            assert response.status_code == 200, response.text
            result = response.json()
            caps = result["capabilities"]
            assert caps["generation"]["available"] is True
            assert caps["generation"]["supported_workflows"] == supported_workflows()
            assert caps["general_code_generation"] is False
            assert caps["hosted_application"]["status"] == "app_hosted"
            assert caps["hosted_application"]["available"] is True
            assert caps["authentication"]["status"] == "request_obo_authenticated"
            assert caps["authentication"]["available"] is True
            assert caps["authentication"]["inference_verified"] is False
            assert caps["model_inference_authentication"]["available"] is None
            assert caps["agents"]["inference_verified"] is False
            assert caps["semantic_translation"]["available"] is False
            assert caps["deployment"] is False and caps["github_publication"] is False
            assert result["generation_allowed"] is False and result["deployment_allowed"] is False
            assert token not in response.text
            assert all(caps[key] == value for key, value in live_caps.items())


def test_wrong_host_cross_origin_and_sp_identity_rejected(backend):
    with browser(backend) as client:
        assert client.get("/api/projects", headers=headers(Host="evil.example.com")).status_code == 400
        assert client.post("/api/projects", headers=headers(Origin="https://evil.example.com"), json={"name": "x"}).status_code == 403
        assert client.post("/api/projects", headers={"x-forwarded-access-token": TOKEN_A}, json={"name": "x"}).status_code == 403
    wrong = dm.WorkspaceIdentity("obo", "https://different.example.com", "user", None)
    app = create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="warehouse"), identity_resolver=lambda *a, **k: wrong)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/projects", headers=headers()).status_code == 401
    with pytest.raises(dm.AuthenticationUnavailable):
        dm.obo_identity(HOST, TOKEN_A, trusted_proxy=True, deployment_host="https://wrong.example.com")
    assert not backend.calls


def test_user_projects_isolated_restored_and_token_refreshed(backend):
    with browser(backend) as client:
        alice = create(client, "Alice")
        bob = create(client, "Bob", TOKEN_B)
        assert alice != bob
        result = client.get("/api/projects", headers=headers(TOKEN_A2))
        assert [p["name"] for p in result.json()["items"]] == ["Alice"]
        assert client.get("/api/projects/" + alice, headers=headers(TOKEN_B)).status_code == 404
        assert client.get("/api/projects/" + bob, headers=headers()).status_code == 404
        logs = client.get("/api/logs", headers=headers(TOKEN_A2)).json()["items"]
        assert any(row.get("actor") == "alice-id" and row.get("actor_authenticated") is True for row in logs)
        assert all(row.get("actor") != "bob-id" for row in logs)
    with browser(backend) as restarted:
        assert restarted.get("/api/projects/" + alice, headers=headers(TOKEN_A2)).status_code == 200
    assert any(token == TOKEN_A2 for token, *_ in backend.calls)
    for data in backend.objects.values():
        assert all(token.encode() not in data for token in (TOKEN_A, TOKEN_A2, TOKEN_B))
    with pytest.raises(dm.AuthenticationUnavailable):
        current_identity()


def test_observational_reads_preserve_generation_and_emit_safe_stdout_timing(backend, caplog):
    from copy import deepcopy
    caplog.set_level("INFO", logger="workbench.hosted")
    with browser(backend) as client:
        create(client, "Committed")
        content = deepcopy({name: rows for name, rows in backend.rows.items() if not name.endswith(".audit_events")})
        objects = deepcopy(backend.objects)
        backend.calls.clear()
        with ThreadPoolExecutor(max_workers=2) as executor:
            overview = executor.submit(client.get, "/api/overview", headers=headers(TOKEN_A2))
            logs = executor.submit(client.get, "/api/logs", headers=headers(TOKEN_A2))
            assert overview.result(timeout=30).status_code == 200
            assert logs.result(timeout=30).status_code == 200
        assert {name: rows for name, rows in backend.rows.items() if not name.endswith(".audit_events")} == content
        assert backend.objects == objects
        assert not any(action == "upload" for _, action, _ in backend.calls)
        assert not any(action == "sql" and sql.startswith("MERGE")
                   for _, action, sql in backend.calls)
        logs = [record.getMessage() for record in caplog.records
            if "Hosted request finished " in record.getMessage()]
        overview_events = [json.loads(row.split("Hosted request finished ", 1)[1])
                   for row in logs if '"path": "/api/overview"' in row]
        assert overview_events and all(event["status"] == 200 for event in overview_events)
        assert all("token" not in row.lower() and TOKEN_A2 not in row for row in logs)
    with browser(backend) as restarted:
        result = restarted.get("/api/logs", headers=headers(TOKEN_A2))
        assert result.status_code == 200, result.text
        assert result.json()["chain_valid"] is True
        assert restarted.get("/api/projects", headers=headers(TOKEN_A2)).json()["items"][0]["name"] == "Committed"
def test_readonly_overview_does_not_depend_on_audit_delta_merge(backend):
    with browser(backend) as client:
        create(client)
        original = backend.client

        def denied(token):
            resolved = original(token)
            execute = resolved.statement_execution.execute_statement

            def unavailable(**kwargs):
                if kwargs["statement"].startswith("MERGE") and ".`audit_events`" in kwargs["statement"]:
                    raise PermissionDenied("sensitive audit failure " + token)
                return execute(**kwargs)

            resolved.statement_execution.execute_statement = unavailable
            return resolved

        backend.client = denied
        response = client.get("/api/overview", headers=headers(TOKEN_A2))
        assert response.status_code == 200
        assert TOKEN_A2 not in response.text


def test_empty_user_reads_do_not_bootstrap_content_generations(backend):
    from workbench.delta_runtime import RequestStateStore
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    RequestStateStore(identity.client, "warehouse", remote.split("/")[2], "workbench", identity.user_id).ensure_tables()
    backend.calls.clear()
    with browser(backend) as client:
        assert client.get("/api/overview", headers=headers()).status_code == 200
        assert client.get("/api/logs", headers=headers(TOKEN_A2)).status_code == 200
    assert not any(rows for name, rows in backend.rows.items() if not name.endswith(".audit_events"))
    assert not backend.objects
    assert not any(action == "sql" and sql.startswith("SELECT count(*)") for _, action, sql in backend.calls)
    assert not any(action == "sql" and sql.startswith("CREATE") for _, action, sql in backend.calls)


def test_uninitialized_get_fails_closed_then_mutation_initializes_idempotently(backend):
    with browser(backend) as client:
        response = client.get("/api/projects", headers=headers())
        assert response.status_code == 503
        assert not backend.infos and not backend.rows and not backend.objects
        assert not any(action == "sql" and sql.startswith("CREATE") for _, action, sql in backend.calls)
        create(client)
        backend.calls.clear()
        create(client, "Second")
        assert not any(action == "sql" and sql.startswith("CREATE") for _, action, sql in backend.calls)


def test_empty_page_bootstrap_has_bounded_queries_fresh_auth_and_durable_audit(backend):
    from workbench import delta_state as ds
    from workbench.delta_runtime import RequestStateStore, RUNTIME_SCOPE
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    RequestStateStore(identity.client, "warehouse", remote.split("/")[2], "workbench", identity.user_id).ensure_tables()
    original = backend.client
    table_calls, principals = [], []

    def tracked(token):
        resolved = original(token)
        table, me = resolved.tables.get, resolved.current_user.me

        def verify(name):
            table_calls.append((token, name.rsplit(".", 1)[-1]))
            return table(name)

        def principal():
            principals.append(token)
            return me()

        resolved.tables = SimpleNamespace(get=verify)
        resolved.current_user = SimpleNamespace(me=principal)
        return resolved

    backend.client = tracked
    backend.calls.clear()
    paths = ("/api/projects", "/api/overview", "/api/models/status", "/api/databricks/volumes/status")
    with browser(backend) as client, ThreadPoolExecutor(max_workers=len(paths)) as executor:
        pending = [executor.submit(client.get, path, headers=headers(TOKEN_A2)) for path in paths]
        results = [future.result(timeout=30) for future in pending]
        assert all(result.status_code == 200 for result in results), [r.text for r in results]
        assert results[0].json()["items"] == []
        assert results[1].json()["ready"] is False
        assert results[3].json()["authentication"] == "databricks_obo"
        sql = [statement for _, action, statement in backend.calls if action == "sql"]
        # Direct volume-status and project-registry reads avoid the execution
        # cache. Overview/models each read the committed generation, but neither
        # write an audit chain.
        # No per-request SQLite audit merge, content counts, DDL or full commit.
        # One additional exact-scope SELECT checks separate model settings.
        assert len(sql) == 4 + (len(paths) - 2)
        assert sum(statement.startswith("MERGE") for statement in sql) == 1
        assert len(table_calls) == 3 + (len(paths) - 2)
        assert {name for _, name in table_calls} == {"migration_receipts", "audit_events", "model_routes"}
        assert {token for token, _ in table_calls} == {TOKEN_A2}
        assert len(principals) >= len(paths) - 2
        assert set(principals) == {TOKEN_A2}
        assert not backend.objects
        direct = [row for name, rows in backend.rows.items() if name.endswith(".audit_events")
              for row in rows if row["context_id"].startswith("hosted-registry-read-v1:")]
        assert len(direct) == 1
        event = direct[0]
        payload = json.loads(event["payload"])
        assert payload == {"event_id": event["source_key"], "method": "GET", "request_path": "/api/projects",
                   "owner_id": "alice-id", "committed_generation": None, "status_code": 200}
        assert event["context_id"] == "hosted-registry-read-v1:" + payload["event_id"]
        assert event["content_hash"] == ds._content_hash(event)
        identity = backend.resolve(HOST, TOKEN_A2, trusted_proxy=True, deployment_host=HOST)
        reader = RequestStateStore(identity.client, "warehouse", remote.split("/")[2], "workbench", identity.user_id)
        assert reader.read_table("audit_events", **{**RUNTIME_SCOPE, "context_id": event["context_id"]},
                     migration_id=event["migration_id"], limit=2) == [event]
        logs = client.get("/api/logs", headers=headers(TOKEN_A2)).json()
        assert logs["chain_valid"] is True


def test_catalog_slug_reuse_owner_conflict_and_schema_collision(backend):
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    backend.catalog_data["orion_dev_alice"] = SimpleNamespace(owner="alice@example.com")
    first = provision(identity, "orion_dev_")
    assert first.startswith("/Volumes/orion_dev_alice/workbench/artifacts/users/")
    assert not any(action == "create" and key == "orion_dev_alice" for _, action, key in backend.calls)
    backend.catalog_data["orion_dev_alice"] = SimpleNamespace(owner="other@example.com")
    second = provision(identity, "orion_dev_")
    assert second != first and second.startswith("/Volumes/orion_dev_alice_")
    hashed = second.split("/")[2]
    backend.schema_data[hashed + ".workbench"] = SimpleNamespace(owner="other@example.com")
    with pytest.raises(PermissionError):
        provision(identity, "orion_dev_")
    backend.catalog_data[hashed] = SimpleNamespace(owner="other@example.com")
    with pytest.raises(PermissionError):
        provision(identity, "orion_dev_")


@pytest.mark.parametrize("path", ["/Volumes/c/s/v/../x", "/Volumes/c/s/v/%2e%2e/x", "/Volumes/c/s/v/a\\x",
                                "/Volumes/c/s/v//x", "/Volumes/c/s/v/./x", "/Volumes/c/s/v/a:bad",
                                "/tmp/a", "/Volumes/c/s/v"])
def test_volume_traversal_rejected(path):
    from fastapi import HTTPException
    with pytest.raises((ValueError, HTTPException)):
        volume_path(path)


def test_upload_durable_before_processing_and_restore(backend, monkeypatch):
    import workbench.app as local
    original = local.analyze_package
    parsed = []

    def inspect_source(path):
        assert any(p.endswith("/uploads/" + sha256(XML).hexdigest() + ".xml") for p in backend.objects)
        parsed.append(True)
        return original(path)

    monkeypatch.setattr(local, "analyze_package", inspect_source)
    with browser(backend) as client:
        project = create(client)
        response = client.post(f"/api/projects/{project}/packages/upload", params={"stage_only": "true"},
                               headers=headers(), files={"files": ("source.xml", XML, "application/xml")})
        assert response.status_code == 202, response.text
        assert not parsed
        response = client.post(f"/api/projects/{project}/analyze", headers=headers())
        assert response.status_code == 202, response.text
        assert parsed == [True]
        detail = client.get(f"/api/projects/{project}", headers=headers()).json()
        assert detail["packages"][0]["status"] == "completed"
        selection = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": sha256(XML).hexdigest()})
        overview = client.get("/api/overview", headers=selection)
        assert overview.status_code == 200 and overview.json()["ready"] is True
        exported = client.get("/api/export/understanding", headers=selection)
        assert exported.status_code == 200 and exported.content[:2] == b"\x1f\x8b"
    assert not any(name.endswith("/HEAD.json") for name in backend.objects)
    manifests = [r for name, rows in backend.rows.items() if name.endswith(".artifact_manifest") for r in rows]
    assert any(json.loads(record["payload"]).get("source") for record in manifests)
    assert not any(data.startswith(b"SQLite format 3") for data in backend.objects.values())
    assert not any(name.endswith("/blobs/" + sha256(XML).hexdigest()) for name in backend.objects)


@pytest.mark.parametrize("source_failure", ["missing", "changed"])
def test_existing_volume_reference_not_persistently_copied(backend, source_failure):
    original = "/Volumes/source/exports/xml/estate.xml"
    backend.objects[original] = XML
    with browser(backend) as client:
        project = create(client)
        response = client.post("/api/databricks/volumes/import", headers=headers(), json={
            "project_id": project, "catalog": "source", "schema": "exports", "volume": "xml",
            "paths": ["estate.xml"], "consent": True})
        assert response.status_code == 200, response.text
        response = client.post(f"/api/projects/{project}/analyze", headers=headers())
        assert response.status_code == 202, response.text
        assert client.get(f"/api/projects/{project}", headers=headers()).json()["ready_package_count"] == 1
    assert not any("/uploads/" in p or p.endswith("/blobs/" + sha256(XML).hexdigest()) for p in backend.objects)
    if source_failure == "missing":
        del backend.objects[original]
    else:
        backend.objects[original] = b"unique-mutated-raw-source-content"
    backend.calls.clear()
    with browser(backend) as client:
        result = client.get("/api/projects", headers=headers())
        assert result.status_code == 200, result.text
        assert [(p["id"], p["package_count"], p["ready_package_count"]) for p in result.json()["items"]] == [
            (project, 1, 1)]
        detail = client.get(f"/api/projects/{project}", headers=headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["packages"][0]["sha256"] == sha256(XML).hexdigest()
        assert not any(action == "download" and path == original for _, action, path in backend.calls)
        selection = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": sha256(XML).hexdigest()})
        result = client.get("/api/overview", headers=selection)
        assert result.status_code == 503
        assert any(action == "download" and path == original for _, action, path in backend.calls)
        assert "unique-mutated-raw-source-content" not in result.text
        assert TOKEN_A not in result.text


def test_failed_flush_not_acknowledged_and_last_snapshot_restored(backend):
    with browser(backend) as client:
        create(client, "Committed")
        backend.fail_head = True
        result = client.post("/api/projects", headers=headers(), json={"name": "Not committed"})
        assert result.status_code == 503 and TOKEN_A not in result.text
        backend.fail_head = False
        assert [p["name"] for p in client.get("/api/projects", headers=headers()).json()["items"]] == ["Committed"]


def test_same_user_overlap_reads_committed_while_mutation_blocked_and_other_user_isolated(backend):
    with browser(backend) as client, ThreadPoolExecutor(max_workers=2) as executor:
        committed = create(client, "Committed")
        backend.block_upload, backend.release_upload = Event(), Event()
        future = executor.submit(client.post, "/api/projects", headers=headers(), json={"name": "First"})
        try:
            assert backend.block_upload.wait(10)
            pending = executor.submit(client.get, "/api/projects", headers=headers(TOKEN_A2))
            response = pending.result(timeout=5)
            assert response.status_code == 200, response.text
            assert [(p["id"], p["name"]) for p in response.json()["items"]] == [(committed, "Committed")]
            assert not future.done() and not backend.release_upload.is_set()
            assert client.get("/api/health", headers=headers(TOKEN_B)).json()["authentication"] == "databricks_obo"
        finally:
            backend.release_upload.set()
        mutation = future.result(timeout=30)
        assert mutation.status_code == 201, mutation.text
        response = client.get("/api/projects", headers=headers(TOKEN_A2))
        assert response.status_code == 200, response.text
        assert {(p["id"], p["name"]) for p in response.json()["items"]} == {
            (committed, "Committed"), (mutation.json()["id"], "First")}


def test_executor_binds_initiating_context_only(backend):
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    key = _CURRENT.set(identity)
    try:
        with ActionExecutor(max_workers=1) as executor:
            assert executor.submit(current_identity).result() is identity
    finally:
        _CURRENT.reset(key)
    with ActionExecutor(max_workers=1) as executor:
        with pytest.raises(dm.AuthenticationUnavailable):
            executor.submit(current_identity).result()


def test_snapshot_rejects_credentials_and_manifest_traversal(backend, tmp_path):
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    storage = VolumeSnapshots(lambda: identity, remote, tmp_path, forbidden_token=TOKEN_A)
    state = tmp_path / "state"
    state.mkdir()
    secret = state / "unsafe.txt"
    secret.write_text(TOKEN_A)
    with pytest.raises(ValueError, match="Credential"):
        storage.flush()
    assert not any(TOKEN_A.encode() in data for data in backend.objects.values())
    manifest = json.dumps({"version": 1, "root": remote, "sources": {}, "files": [
        {"path": "state/../../escape", "size": 1, "sha256": "a" * 64, "source": False}]}).encode()
    digest = sha256(manifest).hexdigest()
    backend.objects[remote + "/snapshots/" + digest + ".json"] = manifest
    backend.objects[remote + "/HEAD.json"] = json.dumps({"manifest": digest}).encode()
    with pytest.raises(Exception):
        storage.restore()
    assert not (tmp_path.parent / "escape").exists()


def test_environment_explicit_trust_host_and_model_guard(monkeypatch):
    monkeypatch.delenv("WORKBENCH_TRUST_DATABRICKS_PROXY", raising=False)
    with pytest.raises(ValueError, match="proxy trust"):
        HostedConfig.environment()
    monkeypatch.setenv("WORKBENCH_TRUST_DATABRICKS_PROXY", "1")
    monkeypatch.setenv("WORKBENCH_DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_HOST", "https://wrong.example.com")
    with pytest.raises(ValueError, match="differs"):
        HostedConfig.environment()
    monkeypatch.setenv("WORKBENCH_MODEL", "openai:ambient")
    with pytest.raises(ValueError, match="user-scoped"):
        create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="warehouse"))


def test_model_selection_survives_requests_and_uses_fresh_user_token(backend, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    seen, metadata = [], []
    original_discover = dm.WorkspaceModels.discover

    def discover(router, identity):
        metadata.append((identity.user_id, identity.client.config.authenticate()["Authorization"]))
        return original_discover(router, identity)

    def build(identity, routes):
        seen.append((identity.user_id, identity.client.config.authenticate()["Authorization"]))
        return {role: FakeListChatModel(responses=["offline"]) for role in routes}

    monkeypatch.setattr(dm, "build_chat_models", build)
    monkeypatch.setattr(dm.WorkspaceModels, "discover", discover)
    with browser(backend) as client:
        assert client.post("/api/models/connect", headers=headers()).status_code == 200
        create(client)
        selected = save_model(client, {"endpoint": "chat", "tool_support_confirmed": True})
        assert selected.status_code == 200, selected.text
        built = len(seen)
        assert client.get("/api/models/status", headers=headers(TOKEN_A2)).json()["inference_ready"] is True
        assert metadata[-1] == ("alice-id", "Bearer " + TOKEN_A2)
        assert len(seen) == built  # Fresh metadata validation, no status client construction.
        # Discovery never initializes storage. An explicit project save does.
        create(client, "Bob", TOKEN_B)
        assert client.post("/api/models/connect", headers=headers(TOKEN_B)).status_code == 200
        assert client.get("/api/models/status", headers=headers(TOKEN_B)).json()["inference_ready"] is False
    assert all(TOKEN_A.encode() not in data and TOKEN_A2.encode() not in data for data in backend.objects.values())


def test_hosted_connect_never_configures_default_model_or_initializes_storage(backend):
    original_client = backend.client

    def client_with_default(token):
        client = original_client(token)
        client.serving_endpoints = SimpleNamespace(list=lambda: [SimpleNamespace(
            name="OpenAI-gpt-4o-mini", task="llm/v1/chat",
            state=SimpleNamespace(ready="READY"))])
        return client

    backend.client = client_with_default
    with browser(backend) as client:
        response = client.post("/api/models/connect", headers=headers())
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["endpoints"][0]["name"] == "OpenAI-gpt-4o-mini"
        assert not data.get("selected_endpoint")
        assert data["default_route_configured"] is False
        assert data["data_egress_consent_required_for_each_run"] is True
        assert data["runtime_verified"] is False
        assert not backend.rows and not backend.objects and not backend.calls
        create(client)
        status = client.get("/api/models/status", headers=headers(TOKEN_A2))
        assert status.status_code == 200 and status.json()["inference_ready"] is False
        rows = [row for table, records in backend.rows.items() if table.endswith(".model_routes") for row in records]
        assert rows == []


def test_hosted_connect_preserves_existing_user_selected_model_route(backend, monkeypatch):
    original_client = backend.client

    def client_with_endpoints(token):
        client = original_client(token)
        client.serving_endpoints = SimpleNamespace(list=lambda: [
            SimpleNamespace(name="OpenAI-gpt-4o-mini", task="llm/v1/chat", state=SimpleNamespace(ready="READY")),
            SimpleNamespace(name="chat", task="llm/v1/chat", state=SimpleNamespace(ready="READY")),
        ])
        return client

    backend.client = client_with_endpoints
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    monkeypatch.setattr(dm, "build_chat_models", lambda identity, routes: {
        role: FakeListChatModel(responses=["offline"]) for role in routes})
    with browser(backend) as client:
        assert client.post("/api/models/connect", headers=headers()).status_code == 200
        create(client)
        selected = save_model(client, {"endpoint": "chat", "tool_support_confirmed": True})
        assert selected.status_code == 200, selected.text
        response = client.post("/api/models/connect", headers=headers(TOKEN_A2))
        assert response.status_code == 200, response.text
        data = response.json()
        assert not data.get("selected_endpoint")  # Discovery is independent of saved settings.
        assert data["default_route_configured"] is False
        status = client.get('/api/models/status', headers=headers(TOKEN_A2))
        assert status.status_code == 200 and status.json()['active_endpoint'] == 'chat'


def test_hosted_config_requires_explicit_sql_warehouse(monkeypatch):
    with pytest.raises(ValueError, match="warehouse"):
        HostedConfig(HOST, ORIGIN)
    monkeypatch.setenv("WORKBENCH_TRUST_DATABRICKS_PROXY", "1")
    monkeypatch.setenv("WORKBENCH_DATABRICKS_HOST", HOST)
    monkeypatch.setenv("DATABRICKS_HOST", HOST)
    monkeypatch.setenv("WORKBENCH_APP_ORIGIN", ORIGIN)
    monkeypatch.delenv("WORKBENCH_SQL_WAREHOUSE_ID", raising=False)
    with pytest.raises(ValueError, match="warehouse"):
        HostedConfig.environment()
    monkeypatch.setenv("WORKBENCH_SQL_WAREHOUSE_ID", "explicit-existing-warehouse")
    assert HostedConfig.environment().warehouse == "explicit-existing-warehouse"


def test_sql_failure_never_uses_local_or_volume_snapshot_fallback(backend):
    with browser(backend) as client:
        create(client, "Committed")
        old_client = backend.client

        def denied(token):
            resolved = old_client(token)

            def unavailable(**kwargs):
                raise PermissionDenied("sensitive upstream message " + token)

            resolved.statement_execution.execute_statement = unavailable
            return resolved

        backend.client = denied
        result = client.get("/api/projects", headers=headers(TOKEN_A2))
        assert result.status_code == 503
        assert "No local fallback" in result.text
        assert TOKEN_A2 not in result.text and "Committed" not in result.text
        backend.client = old_client
        assert [p["name"] for p in client.get("/api/projects", headers=headers()).json()["items"]] == ["Committed"]


def test_upload_failure_never_starts_parser(backend, monkeypatch):
    import workbench.app as local
    monkeypatch.setattr(local, "analyze_package", lambda *a: pytest.fail("Must persist source first"))
    old_client = backend.client

    def denied(token):
        client = old_client(token)
        original = client.files.upload

        def upload(path, stream, **kwargs):
            if "/uploads/" in path:
                raise PermissionDenied("sensitive upstream message " + token)
            return original(path, stream, **kwargs)
        client.files.upload = upload
        return client

    backend.client = denied
    with browser(backend) as client:
        project = create(client)
        result = client.post(f"/api/projects/{project}/packages/upload", params={"stage_only": "true"},
                             headers=headers(), files={"files": ("source.xml", XML)})
        assert result.status_code == 503 and TOKEN_A not in result.text
        assert client.get(f"/api/projects/{project}", headers=headers()).json()["package_count"] == 0