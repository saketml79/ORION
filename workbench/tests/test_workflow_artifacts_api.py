"""Offline HTTP and Chromium integration. No inference, servers or deployment."""
from copy import deepcopy
from contextvars import ContextVar
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from playwright.sync_api import expect
import pytest

from workbench import app as app_module, workflow_artifacts as compiler
from workbench.app import _create_package_app
from workbench.projects import ProjectDispatch
from workbench.tests.test_redesign_ui import browser, ui, open_proposals
from workbench.tests.test_workflow_artifacts import canonical


ENDPOINT = "/api/proposals/workflow-artifacts"
ARCHIVE = ENDPOINT + "/archive"
HEADERS = {"X-Workbench-Request": "1"}
WORKFLOW = "a" * 64
BODY = {"workflow_id": WORKFLOW, "scenario": "bronze", "consent": True}


class NoExecutor:
    def __init__(self, **kwargs):
        pass

    def submit(self, *args, **kwargs):
        pytest.fail("Pure artifact generation must not queue work")

    def shutdown(self, **kwargs):
        pass


def index_for(workflow=WORKFLOW, package_hash="b" * 64):
    nodes = [{"id": workflow, "name": "Reviewed workflow", "kind": "workflow"},
             {"id": "c" * 64, "kind": "mapping"}]
    model = {"package": {"sha256": package_hash}, "nodes": nodes, "edges": []}
    return SimpleNamespace(model=model, nodes={node["id"]: node for node in nodes})


def response_for(workflow=WORKFLOW):
    _, tasks, parameters = compiler._bundle()
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {"language": "markdown", "id": "title"}, "source": ["# Review only"]},
        {"cell_type": "code", "metadata": {"language": "python", "id": "code"}, "source": ["password = dbutils.secrets.get(scope='approved', key='smtp')\n"], "execution_count": None, "outputs": []}]}
    inventory = [{"kind": "workflowvariable", "name": "$$VALUE", "scope": [{"kind": "workflow", "name": "W"}],
                  "attributes": {"DEFAULTVALUE": "captured-default"}}]
    inventory += [{"kind": "reference", "name": f"$$REF_{i}", "scope": [{"kind": "session", "name": "S"}],
                   "attributes": {"expression": f"$$REF_{i}"}, "resolution": "evidence_only_not_an_execution_binding"} for i in range(120)]
    return {"workflow_id": workflow, "scenario": "bronze", "package_hash": "b" * 64,
            "artifacts": {"databricks.yml": "bundle:\n  name: portable\n", "notebooks/wait.ipynb": json.dumps(notebook),
                          "notebooks/wait.py": "password = dbutils.secrets.get(scope='approved', key='smtp')\n",
                          "parameter_inventory.json": json.dumps(inventory), "README.md": "# Candidate, not deployed\n",
                          "tests/test_contract.py": "# Portable tests, not run by compiler\n"},
            "tasks": tasks, "parameters": parameters, "parameter_inventory": inventory,
            "deployment_requirements": [{"id": "bindings", "status": "unbound", "detail": "Runtime bindings required"}],
            "validation": {"remote_execution": "not_run", "deployment": "not_performed"},
            "ready_for_execution": False, "deployment_allowed": False, "execution_allowed": False, "approved": False,
            "notice": "Deterministic in-memory boilerplate only."}


@pytest.fixture
def api(tmp_path, monkeypatch):
    app = _create_package_app(tmp_path, executor_factory=NoExecutor, model_installer=lambda app: None)
    service = app.state.service
    calls = []

    def compile_stub(model, workflow_id, scenario):
        calls.append((model, workflow_id, scenario))
        return {key: value for key, value in response_for(workflow_id).items() if key not in {
            "workflow_id", "scenario", "package_hash", "deployment_allowed", "execution_allowed", "approved", "notice"}}

    monkeypatch.setattr(compiler, "compile_workflow_artifacts", compile_stub)
    monkeypatch.setattr(app_module, "analyze_package", lambda *a, **k: pytest.fail("Request reparsed XML"))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        service.active = index_for()
        yield SimpleNamespace(app=app, service=service, client=client, calls=calls, root=tmp_path)


@pytest.mark.parametrize("workflow,member", [
    ("00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38", "wil_net_dis"),
    ("3507aed6f85c7c2dbc5dca041f6662f506e8ef0a623fa9642febf55c0ad3b203", "wil_net_ins"),
    ("87856a03806ee5c53746761a542b409f588d1768223106ca32486daf1bfa6fa8", "wil_cus_inst"),
    ("4d30896b15af879a2011782a66e1446f8de8fbd4bc1c681d14c15c5eb58d10a0", "wil_cus_dis"),
])
def test_archive_download_name_matches_verified_member(api, workflow, member):
    api.service.active = index_for(workflow)
    response = api.client.post(ARCHIVE, headers=HEADERS, json={**BODY, "workflow_id": workflow})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{member}_bronze.zip"'


@pytest.mark.parametrize("endpoint", [ENDPOINT, ARCHIVE])
@pytest.mark.parametrize("body,status", [
    ({"scenario": "bronze", "consent": True}, 422),
    ({"workflow_id": WORKFLOW, "consent": True}, 422),
    ({"workflow_id": WORKFLOW, "scenario": "bronze"}, 422),
    ({**BODY, "consent": False}, 403), ({**BODY, "consent": "true"}, 422),
    ({**BODY, "consent": 1}, 422), ({**BODY, "scenario": "original"}, 422),
    ({**BODY, "scenario": "other"}, 422), ({**BODY, "workflow_id": []}, 422),
    ({**BODY, "workflow_id": "../source.xml"}, 422),
    ({**BODY, "approved": True}, 422), ({**BODY, "workflow_id": "d" * 64}, 404),
    ({**BODY, "workflow_id": "c" * 64}, 404), ([], 422), (None, 422),
])
def test_strict_scope_and_consent_rejected_before_compilation(api, endpoint, body, status):
    response = api.client.post(endpoint, headers=HEADERS, json=body)
    assert response.status_code == status, response.text
    assert not api.calls


@pytest.mark.parametrize("endpoint", [ENDPOINT, ARCHIVE])
def test_mutation_boundary_and_missing_active_package(api, endpoint):
    assert api.client.post(endpoint, json=BODY).status_code == 403
    api.service.active = None
    assert api.client.post(endpoint, headers=HEADERS, json=BODY).status_code == 409
    assert not api.calls


def test_active_model_identity_deterministic_text_and_no_artifact_storage(api):
    index = api.service.active
    before = deepcopy(index.model)
    responses = [api.client.post(ENDPOINT, headers=HEADERS, json=BODY) for _ in range(2)]
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json() == responses[1].json()
    result = responses[0].json()
    assert all(call[0] is index.model and call[1:] == (WORKFLOW, "bronze") for call in api.calls)
    assert index.model == before
    assert result["package_hash"] == index.model["package"]["sha256"]
    assert result["ready_for_execution"] is result["execution_allowed"] is result["deployment_allowed"] is result["approved"] is False
    assert result["artifacts"] == response_for()["artifacts"]
    assert not (api.root / "generated-drafts").exists()
    assert not list(api.root.rglob("*.ipynb"))
    assert not list((api.service.state / "jobs").glob("*.json"))


@pytest.mark.parametrize("endpoint", [ENDPOINT, ARCHIVE])
@pytest.mark.parametrize("message", ["Unsupported workflow; only reviewed WIL_NET_DIS is supported",
                                        "Unsupported or changed scoped semantics; review required"])
def test_compiler_unsupported_scope_errors_are_explicit(api, monkeypatch, endpoint, message):
    def unsupported(*args, **kwargs):
        raise ValueError(message)
    monkeypatch.setattr(compiler, "compile_workflow_artifacts", unsupported)
    result = api.client.post(endpoint, headers=HEADERS, json=BODY)
    assert result.status_code == 422 and result.json()["detail"] == message


@pytest.mark.parametrize("endpoint", [ENDPOINT, ARCHIVE])
def test_existing_dispatcher_selects_only_requested_package_and_keeps_context(api, endpoint):
    selected = index_for("e" * 64, "f" * 64)
    child = _create_package_app(api.root / "child", executor_factory=NoExecutor, model_installer=lambda app: None)
    child.state.service.active = selected
    released = []
    identity = ContextVar("workflow_test_identity", default="request-user")

    def project(project_id):
        if project_id != "selected":
            raise HTTPException(404, "Unknown project")
        return {"id": project_id}

    def acquire(project_id, digest):
        assert identity.get() == "request-user"
        if digest != "f" * 64:
            raise HTTPException(403, "Package is not a member of this project")
        return child

    manager = SimpleNamespace(registry=SimpleNamespace(project=project), acquire=acquire,
                              release=lambda *scope: released.append(scope))
    try:
        with TestClient(ProjectDispatch(api.app, manager), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
            headers = {**HEADERS, "X-Workbench-Project": "selected", "X-Workbench-Package": "f" * 64}
            result = client.post(endpoint, headers=headers, json={**BODY, "workflow_id": "e" * 64})
            assert result.status_code == 200, result.text
            assert api.calls[-1][0] is selected.model
            assert api.calls[-1][1:] == ("e" * 64, "bronze")
            if endpoint == ENDPOINT:
                assert result.json()["package_hash"] == "f" * 64
            else:
                with ZipFile(io.BytesIO(result.content)) as archive:
                    assert archive.namelist() == sorted(response_for()["artifacts"])
            assert client.post(endpoint, headers=headers, json=BODY).status_code == 404
            assert client.post(endpoint, headers={**headers, "X-Workbench-Package": "b" * 64}, json=BODY).status_code == 403
            assert client.post(endpoint, headers={**HEADERS, "X-Workbench-Project": "selected"}, json=BODY).status_code == 409
            assert client.post(endpoint, headers={**HEADERS, "X-Workbench-Package": "f" * 64}, json=BODY).status_code == 400
        assert released == [("selected", "f" * 64)] * 2
    finally:
        child.state.service.close()


def test_archive_is_deterministic_exact_utf8_tree_and_never_persists(api, monkeypatch):
    payload = response_for()
    payload["artifacts"]["tests/nested/unicode.txt"] = "\ufeffExact café\r\n未変更\n\n"
    payload["artifacts"]["tests/empty.txt"] = ""
    monkeypatch.setattr(compiler, "compile_workflow_artifacts", lambda model, workflow_id, scenario: payload)
    index = api.service.active
    before_model = deepcopy(index.model)
    before_files = {p.relative_to(api.root) for p in api.root.rglob("*") if p.is_file()}
    text = api.client.post(ENDPOINT, headers=HEADERS, json=BODY).json()["artifacts"]
    responses = [api.client.post(ARCHIVE, headers=HEADERS, json=BODY) for _ in range(2)]
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].content == responses[1].content
    assert responses[0].headers["content-type"] == "application/zip"
    assert responses[0].headers["content-disposition"] == 'attachment; filename="workflow_artifacts_bronze.zip"'
    assert responses[0].headers["cache-control"] == "no-store"
    with ZipFile(io.BytesIO(responses[0].content)) as archive:
        assert archive.namelist() == sorted(text)
        assert {name: archive.read(name) for name in archive.namelist()} == {
            name: content.encode("utf-8") for name, content in text.items()}
        assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) and not entry.is_dir()
                   for entry in archive.infolist())
        assert archive.testzip() is None
    assert index.model == before_model
    assert {p.relative_to(api.root) for p in api.root.rglob("*") if p.is_file()} == before_files
    assert not list(api.root.rglob("*.zip"))
    assert not list(api.root.rglob("*.ipynb"))
    assert not list((api.service.state / "jobs").glob("*.json"))


@pytest.mark.parametrize("path", ["", "/absolute.py", "../escape.py", "a/../escape.py", "./a.py",
                                   "a/./b.py", "a//b.py", "a/", "C:/escape.py", "C:escape.py",
                                   r"a\escape.py", "a\x00.py", "a\r\n.py", "//host/share.py"])
def test_archive_rejects_unsafe_compiler_paths(api, monkeypatch, path):
    monkeypatch.setattr(compiler, "compile_workflow_artifacts", lambda *a, **k: {"artifacts": {path: "text"}})
    result = api.client.post(ARCHIVE, headers=HEADERS, json=BODY)
    assert result.status_code == 422
    assert result.json()["detail"] == "Invalid compiler artifact relative path or text"
    assert not list(api.root.rglob("*.zip"))


def test_archive_rejects_non_text_compiler_artifacts(api, monkeypatch):
    monkeypatch.setattr(compiler, "compile_workflow_artifacts", lambda *a, **k: {"artifacts": {"a.py": {}}})
    assert api.client.post(ARCHIVE, headers=HEADERS, json=BODY).status_code == 422


def test_actual_xml_api_matches_unmodified_compiler_including_fail_closed_scope(api, canonical, monkeypatch):
    model, workflow_id = canonical
    # Parsing is fixture-only. The request must work without raw source access.
    api.service.active = SimpleNamespace(model=model, nodes={n["id"]: n for n in model["nodes"]})
    from workbench.tests.test_workflow_artifacts import compile_workflow_artifacts
    monkeypatch.setattr(compiler, "compile_workflow_artifacts", compile_workflow_artifacts)
    result = api.client.post(ENDPOINT, headers=HEADERS, json={**BODY, "workflow_id": workflow_id})
    archived = api.client.post(ARCHIVE, headers=HEADERS, json={**BODY, "workflow_id": workflow_id})
    # Integration must preserve the compiler's semantic guard, not repin or
    # repair the current parser's output. Its own suite verifies the pinned
    # model independently. Report that separate suite's failures explicitly.
    from workbench.demo_conversion import _digest, _without_annotations
    actual_hash = _digest(_without_annotations({key: model[key] for key in ("nodes", "edges")}))
    if actual_hash != compiler.REVIEWED_SCOPE_SHA256:
        assert result.status_code == 422
        assert result.json() == {"detail": "Unsupported or changed scoped semantics; review required"}
        assert result.headers['x-workbench-diagnostic'] == 'saved_analysis_incompatible'
        assert archived.status_code == 422 and archived.json() == result.json()
        assert not list(api.root.rglob("*.ipynb"))
        return
    assert result.status_code == 200, result.text
    assert archived.status_code == 200, archived.text
    data = result.json()
    with ZipFile(io.BytesIO(archived.content)) as archive:
        assert archive.namelist() == sorted(data["artifacts"])
        assert all(archive.read(path) == text.encode("utf-8") for path, text in data["artifacts"].items())
    assert len(data["artifacts"]) == 14 and len(data["tasks"]) == 5
    assert data["validation"]["reviewed_scope_match"] is True
    assert data["validation"]["deployment"] == "not_performed"
    assert json.loads(data["artifacts"]["parameter_inventory.json"]) == data["parameter_inventory"]
    for path, text in data["artifacts"].items():
        if path.endswith(".ipynb"):
            notebook = json.loads(text)
            assert [c["metadata"]["language"] for c in notebook["cells"]] == ["markdown", "python"]
            assert all(c["metadata"]["id"] for c in notebook["cells"])


def test_unsupported_workflow_classified_before_scoped_reference_traversal(api, monkeypatch):
    from workbench.workflow_artifact_responses import compile_response
    from workbench.app import WorkflowArtifactsBody
    from workbench.tests.test_workflow_artifacts import compile_workflow_artifacts
    monkeypatch.setattr(compiler, 'compile_workflow_artifacts', compile_workflow_artifacts)
    index = index_for()
    with pytest.raises(HTTPException) as error:
        compile_response(index, WorkflowArtifactsBody(**BODY))
    assert error.value.status_code == 422
    assert error.value.headers['X-Workbench-Diagnostic'] == 'unsupported_workflow'
    assert error.value.detail == 'Unsupported workflow; only four explicitly pinned WIL counter wrappers are supported'


@pytest.fixture
def workflow_ui(ui):
    page, state, fulfill = ui
    page.evaluate('''() => document.dispatchEvent(new CustomEvent('orion:health', {detail: {
        authentication: 'not_implemented', heartbeat_enabled: false,
        capabilities: {generation: {available: true, scenario: 'bronze', supported_workflows: [{workflow_id: 'wf/two'}]}}
    }}))''')
    state.update(workflow_response=response_for("wf/two"), workflow_status=200, workflow_requests=[], workflow_pending=[], workflow_hold=False)
    state.update(archive_requests=[], archive_pending=[], archive_hold=False, archive_status=200,
                 archive_type="application/zip")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, text in sorted(state["workflow_response"]["artifacts"].items()):
            archive.writestr(path, text.encode("utf-8"))
    state["archive_bytes"] = buffer.getvalue()

    def route_handler(route):
        request = route.request
        state["workflow_requests"].append({"body": request.post_data_json, "headers": request.all_headers()})
        if state["workflow_hold"]:
            state["workflow_pending"].append(route)
        else:
            fulfill(route, state["workflow_response"], state["workflow_status"])

    page.route("**/api/proposals/workflow-artifacts", route_handler)

    def archive_handler(route):
        request = route.request
        state["archive_requests"].append({"body": request.post_data_json, "headers": request.all_headers()})
        if state["archive_hold"]:
            state["archive_pending"].append(route)
        elif state["archive_status"] != 200:
            fulfill(route, {"detail": "Unsupported or changed scoped semantics; review required"}, state["archive_status"])
        else:
            route.fulfill(content_type=state["archive_type"], body=state["archive_bytes"])

    page.route("**/api/proposals/workflow-artifacts/archive", archive_handler)
    open_proposals(page)
    yield page, state, fulfill


def select_bronze(page):
    page.locator("#scenario").select_option("bronze", force=True)
    page.locator("#redesign-workflow").select_option("wf/two")
    expect(page.locator("#redesign-status")).to_contain_text("No saved designs on this page")


def generate_workflow(page):
    page.locator("#workflow-artifact-submit").click()


def test_ui_explicit_generation_button_original_error_and_no_model_call(workflow_ui):
    page, state, _ = workflow_ui
    page.locator("#redesign-workflow").select_option("wf/two")
    expect(page.locator("#workflow-artifact-submit")).to_be_disabled()
    expect(page.locator("#workflow-artifact-status")).to_contain_text("Original mode unsupported")
    page.locator('#workflow-artifact-submit').evaluate('button => button.click()')
    expect(page.locator("#workflow-artifact-status")).to_contain_text("Original mode unsupported")
    assert not state["workflow_requests"]
    select_bronze(page)
    expect(page.locator("#agent-consent")).to_have_count(0)
    generate_workflow(page)
    expect(page.locator("#workflow-artifact-status")).to_contain_text("Generated 6 portable artifact texts")
    assert state["workflow_requests"][0]["body"] == {"workflow_id": "wf/two", "scenario": "bronze", "consent": True}
    assert state["workflow_requests"][0]["headers"]["x-workbench-request"] == "1"
    expect(page.locator("#workflow-artifact-consent")).to_have_count(0)
    expect(page.locator("#workflow-artifact-submit")).to_be_enabled()
    assert not [r for r in state["requests"] if r["method"] == "POST"]
    assert not state["archive_requests"]


def test_ui_unsupported_selection_disables_static_compiler_without_post(workflow_ui):
    page, state, _ = workflow_ui
    select_bronze(page)
    page.locator('#redesign-workflow').select_option('wf/one')
    expect(page.locator('#workflow-artifact-status')).to_contain_text('Use agent design & code for this workflow')
    expect(page.locator('#workflow-artifact-consent')).to_have_count(0)
    expect(page.locator('#workflow-artifact-submit')).to_be_disabled()
    page.locator('#workflow-artifact-submit').evaluate('button => button.dispatchEvent(new Event("click"))')
    assert not state['workflow_requests']


def test_ui_saved_analysis_incompatible_is_explicit_and_blocks_repeat(workflow_ui):
    page, state, _ = workflow_ui
    select_bronze(page)
    page.route('**/api/proposals/workflow-artifacts', lambda route: route.fulfill(
        status=422, content_type='application/json', headers={'X-Workbench-Diagnostic': 'saved_analysis_incompatible'},
        body=json.dumps({'detail': 'Unsupported or changed scoped semantics; review required'})))
    generate_workflow(page)
    expect(page.locator('#workflow-artifact-status')).to_contain_text('Saved analysis incompatible')
    expect(page.locator('#workflow-artifact-status')).to_contain_text('no automatic rebuild or pin replacement')
    expect(page.locator('#workflow-artifact-submit')).to_be_disabled()
    expect(page.locator('#workflow-artifact-output')).to_be_empty()


def test_saved_redesign_502_does_not_replace_successful_workflow_artifact_status(workflow_ui):
    page, state, _ = workflow_ui
    select_bronze(page)
    page.route("**/api/redesigns?*", lambda route: route.fulfill(
        status=502, content_type="text/html", body="<html>Gateway failure, not JSON</html>"))
    page.locator("#redesign-refresh").click()
    expect(page.locator("#redesign-status")).to_contain_text("GET /api/redesigns: HTTP 502")
    generate_workflow(page)
    expect(page.locator("#workflow-artifact-status")).to_contain_text("Generated 6 portable artifact texts")
    expect(page.locator("#redesign-status")).to_contain_text("GET /api/redesigns: HTTP 502")
    expect(page.locator("#workflow-artifact-output")).to_contain_text("databricks.yml")
    assert len(state["workflow_requests"]) == 1
    assert not state["archive_requests"]


def test_ui_native_task_graph_full_inventory_and_exact_all_downloads(workflow_ui):
    page, state, _ = workflow_ui
    select_bronze(page)
    generate_workflow(page)
    output = page.locator("#workflow-artifact-output")
    expect(output.get_by_role("img", name=re.compile("Databricks Jobs task grid"))).to_have_count(1)
    expect(output.locator("svg .workflow-native-card rect")).to_have_count(5)
    expect(output.locator(".workflow-native-canvas")).to_have_count(1)
    expect(output.locator("svg pattern")).to_have_count(1)
    expect(output.locator("svg > circle")).to_have_count(2)
    expect(output).to_contain_text("Serverless")
    expect(output).to_contain_text("./notebooks/counter.py")
    cards = output.locator("svg .workflow-native-card rect").evaluate_all(
        "cards => cards.map(card => ({x: Number(card.getAttribute('x')), y: Number(card.getAttribute('y'))}))")
    assert cards[0]["x"] < cards[1]["x"] < cards[2]["x"]
    assert cards[0]["y"] == cards[1]["y"] == cards[2]["y"]
    assert cards[3]["x"] == cards[4]["x"] and cards[3]["y"] != cards[4]["y"]
    expect(output.locator("svg > path")).to_have_count(4)
    expect(output.locator("table").first.locator("tbody tr")).to_have_count(5)
    expect(output).to_contain_text("condition_task")
    expect(output).to_contain_text("threshold: true")
    expect(output).to_contain_text("threshold: false")
    expect(output).to_contain_text("Complete variable inventory: 121 records")
    output.get_by_text("Full scoped inventory (121 records, no truncation)", exact=True).click()
    expect(output.locator("table").nth(2).locator("tbody tr")).to_have_count(121)
    expect(output).to_contain_text("$$REF_119")
    expect(output).to_contain_text("captured-default")
    expect(output).to_contain_text("evidence_only_not_an_execution_binding")
    expect(output).to_contain_text("Bronze needs no original Oracle connections")
    expect(output).to_contain_text("Runtime bindings remain required")
    for path, text in state["workflow_response"]["artifacts"].items():
        with page.expect_download() as downloaded:
            output.get_by_role("button", name=f"Download {path}", exact=True).click()
        assert downloaded.value.suggested_filename == Path(path).name
        assert Path(downloaded.value.path()).read_bytes() == text.encode("utf-8")
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert not state["archive_requests"]


def test_ui_zoom_and_clickable_notebook_and_condition_details(workflow_ui):
    page, state, _ = workflow_ui
    select_bronze(page)
    generate_workflow(page)
    output = page.locator("#workflow-artifact-output")
    svg = output.locator(".workflow-native-canvas svg")
    expect(svg).to_have_attribute("width", "1100")
    output.get_by_role("button", name="+ Zoom in", exact=True).click()
    expect(svg).to_have_attribute("width", "1375")
    expect(output.locator(".workflow-native-toolbar output")).to_have_text("125%")
    output.get_by_role("button", name="− Zoom out", exact=True).click()
    expect(svg).to_have_attribute("width", "1100")
    output.get_by_role("button", name="Fit to view", exact=True).click()
    assert float(svg.get_attribute("width")) > 0
    output.get_by_role("button", name="Reset zoom", exact=True).click()
    expect(svg).to_have_attribute("width", "1100")
    wait = output.locator('.workflow-native-card[data-task-key="wait"]')
    wait.click()
    inspector = output.locator(".workflow-task-inspector")
    expect(inspector).to_be_visible()
    expect(inspector).to_contain_text("Task: wait")
    expect(inspector).to_contain_text("Cell 2 · python")
    expect(inspector).to_contain_text("dbutils.secrets.get")
    expect(inspector).not_to_contain_text("0b4e03d1")
    expect(wait).to_have_attribute("aria-pressed", "true")
    inspector.get_by_role("button", name="Close task details").click()
    expect(inspector).to_be_hidden()
    condition = output.locator('.workflow-native-card[data-task-key="threshold"]')
    condition.focus()
    condition.press("Enter")
    expect(inspector).to_contain_text("Task: threshold")
    expect(inspector).to_contain_text("LESS_THAN")
    expect(inspector).to_contain_text("launch_child")
    expect(inspector).to_contain_text("email")
    assert len(state["workflow_requests"]) == 1
    assert not [r for r in state["requests"] if r["method"] == "POST"]
    assert not state["archive_requests"]
    page.locator("#redesign-workflow").select_option("wf/one")
    expect(output.locator(".workflow-task-inspector")).to_have_count(0)


def test_ui_archive_click_only_scoped_exact_blob_and_repeat_generation(workflow_ui):
    page, state, _ = workflow_ui
    page.locator("#project-select").select_option("project-one", force=True)
    page.locator("#project-package").select_option("package-one", force=True)
    select_bronze(page)
    expect(page.locator("#agent-consent")).to_have_count(0)
    expect(page.locator("#workflow-artifact-archive")).to_have_count(0)
    generate_workflow(page)
    archive = page.get_by_role("button", name="Download complete bundle (ZIP)", exact=True)
    expect(archive).to_be_enabled()
    expect(page.locator("#workflow-artifact-consent")).to_have_count(0)
    assert not state["archive_requests"]
    state["archive_hold"] = True
    archive.click()
    expect(archive).to_be_disabled()
    expect(page.locator("#workflow-artifact-submit")).to_be_disabled()
    request = state["archive_requests"][0]
    assert request["body"] == {"workflow_id": "wf/two", "scenario": "bronze", "consent": True}
    assert request["headers"]["x-workbench-request"] == "1"
    assert request["headers"]["x-workbench-project"] == "project-one"
    assert request["headers"]["x-workbench-package"] == "package-one"
    assert request["headers"]["accept"] == "application/zip"
    assert request["headers"]["content-type"] == "application/json"
    with page.expect_download() as downloaded:
        state["archive_pending"].pop().fulfill(content_type="application/zip", body=state["archive_bytes"])
    assert downloaded.value.suggested_filename == "wil_net_dis_bronze.zip"
    downloaded_bytes = Path(downloaded.value.path()).read_bytes()
    assert downloaded_bytes == state["archive_bytes"]
    with ZipFile(io.BytesIO(downloaded_bytes)) as bundle:
        assert bundle.namelist() == sorted(state["workflow_response"]["artifacts"])
        assert all(bundle.read(path) == text.encode("utf-8")
                   for path, text in state["workflow_response"]["artifacts"].items())
    expect(archive).to_be_enabled()
    expect(page.locator("#workflow-artifact-status")).to_contain_text("No execution, approval or deployment")
    generate_workflow(page)
    expect(archive).to_be_enabled()
    assert len(state["workflow_requests"]) == 2
    assert all(r["body"]["consent"] is True for r in state["workflow_requests"])
    assert len(state["archive_requests"]) == 1
    assert not [r for r in state["requests"] if r["method"] == "POST"]
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("failure", ["scope", "content_type"])
def test_ui_archive_errors_do_not_download_and_allow_retry(workflow_ui, failure):
    page, state, _ = workflow_ui
    select_bronze(page)
    generate_workflow(page)
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    if failure == "scope":
        state["archive_status"] = 422
    else:
        state["archive_type"] = "application/json"
    archive = page.locator("#workflow-artifact-archive")
    archive.click()
    expect(page.locator("#workflow-artifact-status")).to_contain_text(
        "HTTP 422" if failure == "scope" else "contract mismatch")
    expect(archive).to_be_enabled()
    assert not downloads
    state["archive_status"] = 200
    state["archive_type"] = "application/zip"
    with page.expect_download():
        archive.click()
    assert len(state["archive_requests"]) == 2


@pytest.mark.parametrize("phase", ["headers", "blob"])
@pytest.mark.parametrize("change", ["workflow", "scenario", "project", "package", "close"])
def test_ui_archive_stale_context_never_downloads_even_without_abort(workflow_ui, phase, change):
    page, state, _ = workflow_ui
    select_bronze(page)
    generate_workflow(page)
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    if phase == "headers":
        state["archive_hold"] = True
    else:
        page.evaluate("""() => {
          const original = Response.prototype.blob;
          Response.prototype.blob = async function() {
            const blob = await original.call(this);
                        await new Promise(resolve => {
                            window.releaseArchiveBlob = resolve;
                            document.body.dataset.archiveBlobPending = 'true';
                        });
            return blob;
          };
        }""")
    page.locator("#workflow-artifact-archive").click()
    expect(page.locator("#workflow-artifact-archive")).to_be_disabled()
    if phase == "blob":
        expect(page.locator("body")).to_have_attribute("data-archive-blob-pending", "true")
    if change == "workflow":
        expect(page.locator("#redesign-workflow")).to_be_disabled()
        page.locator("#agent-workflow").select_option("wf/one", force=True)
    elif change == "scenario":
        page.locator("#scenario").select_option("original", force=True)
    elif change == "project":
        page.locator("#project-select").select_option("project-two", force=True)
    elif change == "package":
        page.locator("#project-package").select_option("package-two", force=True)
    else:
        page.locator('[data-page="agents"]').click()
    if phase == "headers":
        state["archive_pending"].pop().fulfill(content_type="application/zip", body=state["archive_bytes"])
    else:
        page.evaluate("window.releaseArchiveBlob()")
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#workflow-artifact-output")).to_be_empty()
    expect(page.locator("#workflow-artifact-consent")).to_have_count(0)
    assert not downloads


@pytest.mark.parametrize("change", ["workflow", "scenario", "project", "package", "close"])
def test_ui_stale_response_guards_even_when_abort_is_ineffective(workflow_ui, change):
    page, state, fulfill = workflow_ui
    select_bronze(page)
    state["workflow_hold"] = True
    generate_workflow(page)
    expect(page.locator("#workflow-artifact-status")).to_contain_text("Generating deterministic")
    assert len(state["workflow_pending"]) == 1
    if change == "workflow":
        expect(page.locator("#redesign-workflow")).to_be_disabled()
        page.locator("#agent-workflow").select_option("wf/one", force=True)
    elif change == "scenario":
        page.locator("#scenario").select_option("original", force=True)
    elif change == "project":
        page.locator("#project-select").select_option("project-two", force=True)
    elif change == "package":
        page.locator("#project-package").select_option("package-two", force=True)
    else:
        page.locator('[data-page="agents"]').click()
    fulfill(state["workflow_pending"].pop(), state["workflow_response"])
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#workflow-artifact-output")).to_be_empty()
    expect(page.locator("#workflow-artifact-consent")).to_have_count(0)


@pytest.mark.parametrize("mismatch", ["workflow_id", "scenario", "ready_for_execution", "artifact_type"])
def test_ui_rejects_mismatched_response_contract(workflow_ui, mismatch):
    page, state, _ = workflow_ui
    select_bronze(page)
    if mismatch == "artifact_type":
        state["workflow_response"]["artifacts"]["bad.json"] = {}
    else:
        state["workflow_response"][mismatch] = True if mismatch == "ready_for_execution" else "other"
    generate_workflow(page)
    expect(page.locator("#workflow-artifact-status")).to_contain_text("contract mismatch")
    expect(page.locator("#workflow-artifact-output")).to_be_empty()


def test_ui_project_headers_and_explicit_unsupported_workflow(workflow_ui):
    page, state, _ = workflow_ui
    page.locator("#project-select").select_option("project-one", force=True)
    page.locator("#project-package").select_option("package-one", force=True)
    select_bronze(page)
    state["workflow_status"] = 422
    state["workflow_response"] = {"detail": "Unsupported workflow; only reviewed WIL_NET_DIS is supported"}
    generate_workflow(page)
    expect(page.locator("#workflow-artifact-status")).to_contain_text("only reviewed WIL_NET_DIS is supported")
    headers = state["workflow_requests"][0]["headers"]
    assert headers["x-workbench-project"] == "project-one"
    assert headers["x-workbench-package"] == "package-one"
    expect(page.locator("#workflow-artifact-output")).to_be_empty()