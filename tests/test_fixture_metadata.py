"""Offline canonical/API/identity/UI regressions for fixture-only observations."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from databricks.sdk.errors import NotFound, PermissionDenied
from fastapi.testclient import TestClient
import pytest

from workbench.app import PackageIndex, _create_package_app, create_app
from workbench.databricks_models import AuthenticationUnavailable, WorkspaceIdentity, _ProfileCredentials
from workbench.fixture_metadata import observe
from workbench.source_fixtures import plan_sources
from workbench.understanding import analyze_package

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "infotodbxmig_src_dev_ssuman"
HOST = "https://workspace.example.com"
XML = b'''<POWERMART><REPOSITORY NAME="Repo"><FOLDER NAME="Folder">
<SOURCE NAME="AE2E_JOB_DEP_CHK_COUNT" DATABASETYPE="Oracle" DBDNAME="ATLDEV" OWNERNAME="AE2E">
<SOURCEFIELD NAME="COUNTER" DATATYPE="number" PRECISION="5" SCALE="0" NULLABLE="NULL"/>
</SOURCE><TARGET NAME="Target"/>
<MAPPING NAME="Mapping"><INSTANCE NAME="Source" TRANSFORMATION_NAME="AE2E_JOB_DEP_CHK_COUNT"
TRANSFORMATION_TYPE="Source Definition" TYPE="SOURCE"/>
<INSTANCE NAME="Target" TRANSFORMATION_NAME="Target" TRANSFORMATION_TYPE="Target Definition" TYPE="TARGET"/>
</MAPPING><SESSION NAME="Session" MAPPINGNAME="Mapping"/>
<WORKFLOW NAME="Workflow"><TASKINSTANCE NAME="Run" TASKNAME="Session" TASKTYPE="Session"/></WORKFLOW>
<WORKFLOW NAME="Other"/></FOLDER></REPOSITORY></POWERMART>'''


@pytest.fixture
def parsed(tmp_path):
    path = tmp_path / "source.xml"
    path.write_bytes(XML)
    return PackageIndex(analyze_package(path), tmp_path), path


def setup(parsed, mode="profile", user="one"):
    index, path = parsed
    workflow = next(n["id"] for n in index.kinds["workflow"] if n["name"] == "Workflow")
    plan = plan_sources(path, CATALOG)["tables"][0]
    name = ".".join((CATALOG, plan["schema"], plan["table"]))
    client = NS(schemas=Mock(), tables=Mock())
    client.schemas.get.return_value = NS(full_name=name.rsplit(".", 1)[0])
    client.tables.get.return_value = NS(full_name=name, columns=[NS(name="COUNTER", type_text="decimal(5,0)", nullable=True)],
        data_source_format="DELTA", properties={"orion.fixture": "xml_source_schema_only",
        "orion.package_sha256": index.model["package"]["sha256"], "secret": "private-secret"})
    return index, workflow, name, WorkspaceIdentity(mode, HOST, user, client)


def test_exact_canonical_namespace_provenance_and_no_mutation(parsed):
    index, workflow, name, identity = setup(parsed)
    before = deepcopy(index.model)
    result = observe(index, workflow, CATALOG, identity)
    source = result["sources"][0]
    assert source["expected_full_name"] == source["actual_full_name"] == name
    assert source["status"] == "metadata_verified_fixture_only"
    assert source["exists"] and source["schema_exists"] and source["column_schema_matches"]
    assert source["package_hash_matches"] and source["fixture_provenance_matches"]
    assert source["observed_at"] and not source["approved"]
    assert result["binding_status"] == "fixture_only_unapproved"
    assert result["targets"][0]["binding_status"] == "unresolved"
    assert not result["production_ready"] and not result["generation_allowed"]
    assert "private-secret" not in json.dumps(result)
    assert index.model == before
    identity.client.tables.get.assert_called_once_with(name)
    other = next(n["id"] for n in index.kinds["workflow"] if n["name"] == "Other")
    assert observe(index, other, CATALOG, identity)["sources"] == []
    assert identity.client.tables.get.call_count == 1


@pytest.mark.parametrize("change", ["package", "fixture", "columns", "format"])
def test_mismatch_never_approves(parsed, change):
    index, workflow, _, identity = setup(parsed)
    table = identity.client.tables.get.return_value
    if change == "package": table.properties["orion.package_sha256"] = "b" * 64
    if change == "fixture": table.properties.pop("orion.fixture")
    if change == "columns": table.columns[0].type_text = "STRING"
    if change == "format": table.data_source_format = "PARQUET"
    source = observe(index, workflow, CATALOG, identity)["sources"][0]
    assert source["status"] == "metadata_mismatch" and source["exists"]
    assert not source["approved"]


@pytest.mark.parametrize("where,error,status,exists", [
    ("tables", NotFound("missing"), "table_not_found", False),
    ("schemas", NotFound("missing"), "schema_not_found", False),
    ("tables", PermissionDenied("Bearer private-token"), "metadata_error", None),
    ("schemas", PermissionDenied("Bearer private-token"), "metadata_error", None),
    ("tables", TimeoutError("Bearer private-token"), "metadata_error", None)])
def test_errors_are_not_missing(parsed, where, error, status, exists):
    index, workflow, _, identity = setup(parsed)
    getattr(identity.client, where).get.side_effect = error
    result = observe(index, workflow, CATALOG, identity)
    assert result["sources"][0]["status"] == status
    assert result["sources"][0]["exists"] is exists
    assert "private-token" not in json.dumps(result)


def test_mapping_failures_and_duplicate_identity_do_not_read(parsed):
    index, workflow, _, identity = setup(parsed)
    source = index.kinds["source"][0]
    source["attributes"]["OWNERNAME"] = "[REDACTED]"
    assert observe(index, workflow, CATALOG, identity)["sources"][0]["status"] == "mapping_error"
    identity.client.tables.get.assert_not_called()
    source["attributes"]["OWNERNAME"] = "AE2E"
    duplicate = deepcopy(source); duplicate["id"] = "duplicate"
    index.nodes[duplicate["id"]] = duplicate
    index.out[workflow].append({"kind": "instance_of", "target": "duplicate"})
    with pytest.raises(ValueError, match="collision"):
        observe(index, workflow, CATALOG, identity)
    identity.client.tables.get.assert_not_called()


def test_api_explicit_fresh_identity_and_unknown_workflow(parsed, tmp_path):
    index, workflow, _, identity = setup(parsed)
    app = _create_package_app(tmp_path / "runtime", parsed[1])
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        app.state.service.active = index
        provider = Mock(return_value=identity)
        app.state.fixture_identity_provider = provider
        body = {"workflow_id": workflow, "catalog": CATALOG, "profile": "labs"}
        assert client.post("/api/workflows/fixture-metadata", json=body).status_code == 403
        headers = {"X-Workbench-Request": "1"}
        for user, mode in [("one", "profile"), ("two", "obo")]:
            provider.return_value = WorkspaceIdentity(mode, HOST, user, identity.client)
            response = client.post("/api/workflows/fixture-metadata", json=body, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["identity"]["user_id"] == user
            assert response.json()["profile"] == ("labs" if mode == "profile" else None)
        assert provider.call_count == 2
        for invalid in [{**body, "workflow_id": "unknown"}, {**body, "catalog": "bad.name"},
                        {**body, "profile": "other"}, {**body, "approved": True}]:
            assert client.post("/api/workflows/fixture-metadata", json=invalid, headers=headers).status_code in (404, 422)
        assert provider.call_count == 2
        provider.side_effect = AuthenticationUnavailable("private-token")
        response = client.post("/api/workflows/fixture-metadata", json=body, headers=headers)
        assert response.status_code == 401 and "private-token" not in response.text


def test_exact_profile_headers_no_host_fallback(monkeypatch):
    from workbench import databricks_models as dm
    monkeypatch.setenv("DATABRICKS_HOST", "https://wrong.example.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "ambient-private")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", "explicit.cfg")
    monkeypatch.setattr(dm.shutil, "which", lambda _: "cli.exe")
    run = Mock(return_value=NS(stdout=json.dumps({"access_token": "fake-one"})))
    monkeypatch.setattr(dm.subprocess, "run", run)
    headers = _ProfileCredentials()(NS(profile="labs", host=HOST))
    assert headers() == {"Authorization": "Bearer fake-one"}
    run.return_value.stdout = json.dumps({"access_token": "fake-two"})
    assert headers() == {"Authorization": "Bearer fake-two"}
    assert run.call_args.args[0] == ["cli.exe", "auth", "token", "--profile", "labs", "--output", "json"]
    assert run.call_args.kwargs["capture_output"]
    assert "--host" not in run.call_args.args[0]
    assert "DATABRICKS_HOST" not in run.call_args.kwargs["env"]
    assert "DATABRICKS_TOKEN" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"]["DATABRICKS_CONFIG_FILE"] == "explicit.cfg"


def test_api_project_package_dispatch_isolation(tmp_path):
    app = create_app(tmp_path / "runtime", tmp_path / "unused.xml")
    manager = app.state.projects
    original_factory = manager.factory
    clients = []
    def factory(*args):
        child = original_factory(*args)
        def provider(profile):
            index = child.state.service.require()
            client = NS(schemas=Mock(), tables=Mock())
            client.schemas.get.side_effect = lambda name: NS(full_name=name)
            client.tables.get.side_effect = NotFound("missing")
            clients.append(index.model["package"]["sha256"])
            return WorkspaceIdentity("profile", HOST, "one", client)
        child.state.fixture_identity_provider = provider
        return child
    manager.factory = factory
    headers = {"X-Workbench-Request": "1"}
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        project = client.post("/api/projects", headers=headers, json={"name": "Fixtures"}).json()["id"]
        packages = []
        for name in ("Workflow", "Second"):
            data = XML.replace(b'NAME="Workflow"', ('NAME="' + name + '"').encode())
            response = client.post(f"/api/projects/{project}/packages/upload", headers=headers,
                files=[("files", (name + ".xml", data, "application/xml"))])
            assert response.status_code == 202
            manager.futures[response.json()["id"]].result(timeout=30)
            digest = sha256(data).hexdigest()
            scoped = {**headers, "X-Workbench-Project": project, "X-Workbench-Package": digest}
            overview = client.get("/api/overview", headers=scoped).json()
            workflow = next(w["id"] for w in overview["workflows"] if w["name"] == name)
            body = {"workflow_id": workflow, "catalog": CATALOG, "profile": "labs"}
            result = client.post("/api/workflows/fixture-metadata", headers=scoped, json=body)
            assert result.status_code == 200, result.text
            assert result.json()["project_id"] == project and result.json()["package_hash"] == digest
            context_result = client.get(f"/api/workflows/{workflow}/migration-context", headers=scoped)
            assert context_result.status_code == 200 and context_result.json()["package_hash"] == digest
            packages.append((scoped, body))
        assert len(clients) == 2 and len(set(clients)) == 2
        assert client.post("/api/workflows/fixture-metadata", headers=packages[1][0],
                           json=packages[0][1]).status_code == 404
        assert client.get(f"/api/workflows/{packages[0][1]['workflow_id']}/migration-context",
                  headers=packages[1][0]).status_code == 404
        assert len(clients) == 2
        assert client.post("/api/workflows/fixture-metadata", headers={**headers, "X-Workbench-Project": project},
                           json=packages[0][1]).status_code == 409


def test_read_only_hosted_root_never_creates():
    from workbench.hosted_storage import provision
    client = NS(current_user=NS(me=lambda: NS(id="one", user_name="user@example.com")),
                catalogs=Mock(), schemas=Mock(), volumes=Mock())
    client.catalogs.get.side_effect = NotFound("missing")
    with pytest.raises(PermissionError):
        provision(WorkspaceIdentity("obo", HOST, "one", client), "orion_dev_", initialize=False)
    client.catalogs.create.assert_not_called()
    client.schemas.create.assert_not_called()
    client.volumes.create.assert_not_called()


def test_hosted_wiring_without_importing_known_syntax_blocker():
    import ast
    source = (ROOT / "workbench/hosted.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    install = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "install_hosted_models")
    assignment = next(n for n in install.body if isinstance(n, ast.Assign)
                      and "fixture_identity_provider" in ast.unparse(n))
    current = Mock(side_effect=["first-user", "second-user"])
    app = NS(state=NS())
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), "hosted-provider", "exec"),
         {"app": app, "current_identity": current})
    assert app.state.fixture_identity_provider("labs") == "first-user"
    assert app.state.fixture_identity_provider(None) == "second-user"
    # Verify the read classification, not the spelling of an older predicate.
    dispatcher = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HostedDispatcher')
    mutation = next(n for n in ast.walk(dispatcher) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'mutation' for t in n.targets))
    expression = compile(ast.Expression(mutation.value), 'hosted-read-classification', 'eval')
    for method, fixture_read, artifact_read, expected in (
            ('POST', True, False, False), ('POST', False, True, False),
            ('POST', False, False, True), ('GET', False, False, False)):
        assert eval(expression, {'scope': {'method': method}, 'fixture_read': fixture_read,
                                 'artifact_read': artifact_read}) is expected


def test_workflow_ui_explicit_body_and_stale_guard(parsed):
    from playwright.sync_api import sync_playwright, expect
    index, workflow, _, identity = setup(parsed)
    result = observe(index, workflow, CATALOG, identity)
    source = (ROOT / "workbench/static/app.js").read_text(encoding="utf-8")
    function = source[source.index("  function renderFixtureMetadata("):source.index("  function renderObject()")]
    # Execute the actual renderer with a minimal offline DOM harness.
    harness = '''window.state={epoch:1,objectTicket:1,hosted:false};
const array=v=>Array.isArray(v)?v:[];
const el=(tag,cls='',text='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=text;return e;};
const paragraph=(p,t,c)=>p.append(el('p',c,t));
const human=v=>String(v).replaceAll('_',' ');
const heading=(p,t,tag)=>p.append(el(tag,'',t));
const message=(p,t)=>{p.textContent=t;}; const clear=p=>p.replaceChildren();
const jsonDetails=(p,t,v)=>{const d=el('details');d.append(el('summary','',t),el('pre','',JSON.stringify(v)));p.append(d);};
const action=(text,fn)=>{const b=el('button','',text);b.onclick=fn;return b;};
const post=(path,body)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json',
'X-Workbench-Project':'project-one','X-Workbench-Package':'package-one'},body:JSON.stringify(body)}).then(r=>{if(!r.ok)throw Error('denied');return r.json();});
'''
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        requests, pending = [], []
        def route(r):
            if r.request.url.endswith('/api/workflows/fixture-metadata'):
                requests.append(r.request)
                if len(requests) == 2: pending.append(r)
                elif len(requests) == 4: r.fulfill(status=403, json={"detail": "denied"})
                else: r.fulfill(json=result)
            else: r.fulfill(content_type="text/html", body="<main id='panel'></main>")
        page.route("**/*", route)
        page.goto("http://fixture.test/")
        page.evaluate(harness + function + f"renderFixtureMetadata(document.getElementById('panel'),{json.dumps(workflow)});")
        assert not requests
        page.get_by_label("Fixture catalog").fill("explicit_catalog")
        page.get_by_role("button", name="Read metadata for selected workflow").click()
        expect(page.locator("main")).to_contain_text("metadata_verified_fixture_only")
        assert requests[0].post_data_json == {"workflow_id": workflow, "catalog": "explicit_catalog", "profile": "labs"}
        assert requests[0].headers['x-workbench-project'] == 'project-one'
        expect(page.locator("main")).to_contain_text("Target bindings unapproved")
        expect(page.locator("main")).to_contain_text("0 source-definition blockers; 0 total canonical reference gaps")
        expect(page.locator("main")).to_contain_text("Empty fixtures are not successful source-data validation")
        page.get_by_role("button", name="Read metadata for selected workflow").click()
        page.wait_for_function("document.querySelector('button').disabled")
        page.evaluate("state.objectTicket++;state.hosted=true;")
        pending[0].fulfill(json=result)
        expect(page.locator("main")).not_to_contain_text("metadata_verified_fixture_only")
        expect(page.get_by_role("button")).to_be_enabled()
        page.get_by_role("button").click()
        expect(page.locator("main")).to_contain_text("metadata_verified_fixture_only")
        assert requests[2].post_data_json['profile'] is None
        page.get_by_role("button").click()
        expect(page.locator("main")).to_contain_text("Existence remains unknown, no approval inferred")
        expect(page.locator("main")).not_to_contain_text("metadata_verified_fixture_only")
        browser.close()


def test_actual_hosted_dispatch_read_only_fresh_obo_no_cli(parsed, monkeypatch):
    """Exercise real hosted construction and request-scoped observational OBO."""
    from fastapi import FastAPI
    from workbench import hosted, databricks_models as dm
    index, workflow, _, first = setup(parsed, mode="obo", user="one")
    second = WorkspaceIdentity("obo", HOST, "two", first.client)
    identities = iter([first, second])
    dispatcher = hosted.HostedDispatcher(
        hosted.HostedConfig(HOST, "https://app.example.com", warehouse="warehouse"),
        identity_resolver=lambda *args, **kwargs: next(identities))
    dispatcher.heartbeats = NS(enabled=False)
    provision = Mock(return_value="/Volumes/user/workbench/artifacts/users/scope")
    monkeypatch.setattr(hosted, "provision", provision)
    runtimes = []
    def runtime(*args, **kwargs):
        result = NS(restore=Mock(), restore_audit=Mock(), flush=Mock())
        runtimes.append(result)
        return result
    monkeypatch.setattr(hosted, "DeltaRuntime", runtime)
    def user_app(root, snapshots, *, read_only, execution_models):
        assert read_only and execution_models is False
        app = _create_package_app(root, root / "unused.xml", model_installer=hosted.install_hosted_models,
                                  record_audit_initialization=False)
        app.state.service.active = index
        app.state.service.start = lambda *args: None
        app.state.projects = NS(start=lambda: None, close=lambda: None)
        return app
    monkeypatch.setattr(hosted, "user_app", user_app)
    def forbidden(*args, **kwargs):
        pytest.fail("Hosted metadata must never invoke local CLI/profile fallback")
    monkeypatch.setattr(dm, "profile_identity", forbidden)
    monkeypatch.setattr(dm.subprocess, "run", forbidden)
    outer = FastAPI()
    outer.mount("/", dispatcher)
    body = {"workflow_id": workflow, "catalog": CATALOG, "profile": "labs"}
    headers = {"X-Forwarded-Access-Token": "fake-forwarded", "X-Workbench-Request": "1"}
    with TestClient(outer, base_url="https://app.example.com") as client:
        assert client.post("/api/workflows/fixture-metadata", json=body,
            headers={**headers, "Origin": "https://evil.example.com"}).status_code == 403
        for user in ("one", "two"):
            response = client.post("/api/workflows/fixture-metadata", json=body, headers=headers)
            from workbench.hosted_diagnostics import owner_key
            diagnostics = dispatcher.diagnostics.list(owner_key(HOST, user))
            assert response.status_code == 200, (response.text, diagnostics)
            assert not diagnostics
            assert response.json()["identity"] == {"mode": "obo", "host": HOST, "user_id": user}
            assert response.json()["profile"] is None
    assert len(runtimes) == 2
    assert all(call.kwargs == {"initialize": False} for call in provision.call_args_list)
    for snapshot in runtimes:
        snapshot.restore.assert_called_once_with(initialize=False)
        snapshot.flush.assert_not_called()
        snapshot.restore_audit.assert_not_called()