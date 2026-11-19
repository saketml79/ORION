"""Navigation prerequisites in Chromium with the real markup and all current scripts.

All services are mocked offline. Business mutations and unexpected reads fail.
Delayed responses ignore cancellation to verify context identity, not abort luck.
"""
import json
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import (
    STATIC, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, PACKAGE_C,
    browser, member, settle,
)


PROTECTED = ("readiness", "explorer", "agents", "proposals", "evidence", "validate", "release")
GATED = PROTECTED
PUBLIC = ("projects", "logs", "evolution")


@pytest.fixture
def navigation(browser):
    contexts = []

    def start(*, fragment="agents", legacy=False, saved=None, hold=False,
              members=None, overview_hash=None, agent_available=True, hold_models=False):
        context = browser.new_context(offline=True, service_workers="block")
        context.add_init_script("window.AbortController = class extends AbortController { abort() {} };")
        if saved:
            context.add_init_script(
                "localStorage.setItem('lakeflow-studio-selection', JSON.stringify("
                + json.dumps(saved) + "));")
        page = context.new_page()
        state = {"requests": [], "unexpected": [], "errors": [], "pending": [],
                 "pending_models": [],
                 "hold": hold, "overview_hash": overview_hash, "ready": True,
                 "agent_available": agent_available, "objects": [], "proposals": [],
                 "projects": {PROJECT_A: [member()] if members is None else members,
                              PROJECT_B: []}}
        contexts.append((context, state))
        page.on("pageerror", lambda error: state["errors"].append(str(error)))

        def fulfill(route, data):
            route.fulfill(content_type="application/json", body=json.dumps(data))

        def handler(route):
            req = route.request
            path = urlsplit(req.url).path
            read_operation = None
            if urlsplit(req.url).hostname != "navigation.invalid":
                route.fulfill(body="")  # Font stylesheets, never a network request.
                return
            if path == "/":
                route.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
                return
            if path.startswith("/static/"):
                asset = STATIC / path.removeprefix("/static/")
                route.fulfill(path=str(asset))
                return
            if path == '/api/hosted/operations' and req.method == 'POST':
                envelope = req.post_data_json
                if envelope['method'] == 'GET':
                    read_operation = envelope['id']
                    path = envelope['path']
            entry = {"path": path, "method": 'GET' if read_operation else req.method, "headers": req.all_headers()}
            state["requests"].append(entry)
            # The real activity adapter reports trusted browser interaction only.
            if path == "/api/hosted/heartbeat" and req.method == "POST":
                fulfill(route, {"status": "ok"})
                return
            if entry['method'] != "GET":
                state["unexpected"].append(entry)
                route.abort()
                return
            if path == "/api/health":
                data = {"status": "ok", "local_only": legacy,
                    "authentication": 'not_implemented' if legacy else 'databricks_obo'}
            elif path == "/api/projects":
                data = {"items": [{"id": pid, "name": pid[0],
                                  "ready_package_count": len(rows)}
                                 for pid, rows in state["projects"].items()]}
            elif path.removeprefix("/api/projects/") in state["projects"]:
                pid = path.rsplit("/", 1)[-1]
                rows = state["projects"][pid]
                data = {"id": pid, "name": pid[0], "packages": rows, "jobs": [],
                        "ready_package_count": len(rows)}
            elif path == "/api/input-packages":
                data = {"items": []}
            elif path == "/api/overview":
                pid = entry["headers"].get("x-workbench-project", "")
                digest = entry["headers"].get("x-workbench-package", "")
                selected = next((m for m in state["projects"].get(pid, [])
                                 if m["sha256"] == digest and m["status"] == "completed"), None)
                is_ready = state["ready"] and bool(selected or (legacy and not pid))
                data = {"ready": is_ready, "status": "package_selection_required",
                        "package": {"sha256": state["overview_hash"] or digest or PACKAGE_C,
                                    "name": selected["name"] if selected else "legacy.xml"},
                        "counts": {"workflow": 1} if is_ready else {},
                        "workflows": state.get("workflows", [{"id": "wf/selected", "name": "Selected workflow"}])
                        if is_ready else [], "agent": {"available": state["agent_available"]}}
                if state["hold"] and pid:
                    result = {"operation_id": read_operation, "status": "completed", "result": data} if read_operation else data
                    state["pending"].append((route, result))
                    return
            elif path == "/api/logs":
                data = {"items": [], "total": 0}
            elif path == "/api/hosted/logs":
                data = {"items": [], "storage": "bounded test diagnostics"}
            elif path == "/api/objects":
                data = {"items": state["objects"], "total": len(state["objects"])}
            elif path == "/api/workflow-invocation-summary":
                data = {"package_hash": entry["headers"].get("x-workbench-package"), "workflows": {},
                        "basis": "literal_pmcmd_calls_only_other_dependencies_not_assessed"}
            elif path == "/api/proposals":
                data = {"items": state["proposals"], "total": len(state["proposals"])}
            elif any(path == f'/api/proposals/{row["id"]}' for row in state["proposals"]):
                data = next(row for row in state["proposals"] if path == f'/api/proposals/{row["id"]}')
            elif path == '/api/object/wf%2Fselected':
                data = {"node": {"id": "wf/selected", "name": "Selected workflow", "kind": "workflow"},
                        "graph": {"nodes": [], "edges": []}}
            elif path == '/api/reviews':
                data = {"items": [], "total": 0}
            elif path == "/api/readiness":
                from test_readiness_ui import payload
                data = payload(entry['headers'].get('x-workbench-package', PACKAGE_C))
                data['scope']['project_id'] = entry['headers'].get('x-workbench-project') or '__legacy__'
            elif re.fullmatch(r"/api/agents/runs/[a-f0-9]{32}", path):
                run_id = path.rsplit("/", 1)[-1]
                data = state.get("agent_run_details", {}).get(run_id, {"id": run_id, "status": "completed"})
            elif path in ("/api/documents",
                          "/api/agents/runs", "/api/redesigns", "/api/connections"):
                data = {"items": [], "total": 0}
            elif path == "/api/loops":
                data = {"runs": [], "lessons": [], "events": []}
            elif path == "/api/settings":
                data = {}
            elif path == "/api/models/status":
                data = {"inference_ready": state["agent_available"], "roles": [],
                        "active_endpoint": "explicit-chat" if state["agent_available"] else None}
                if hold_models:
                    state['pending_models'].append((route, data))
                    return
            elif path == "/api/models/external/status":
                data = {"mode": "databricks", "configured": False,
                        "inference_ready": False, "status": "default"}
            elif path == "/api/models/workspace":
                data = {"workspace": "https://workspace.example.com", "mode": "obo"}
            elif path == "/api/tracing/status":
                data = {"state": "disabled", "reason": "not_opted_in"}
            else:
                state["unexpected"].append(entry)
                route.abort()
                return
            fulfill(route, {"operation_id": read_operation, "status": "completed", "result": data} if read_operation else data)

        context.route("**/*", handler)
        page.goto(f"http://navigation.invalid/#{fragment}")
        expect(page.locator("#redesign-review")).to_have_count(1)
        # Mounted scripts are not startup completion. In particular Estate is
        # initially visible while a restored #agents selection is still loading.
        expect(page.locator("#main")).to_have_attribute("data-current-page", re.compile(r".+"))
        return page, state, fulfill

    yield start
    for context, state in contexts:
        context.close()
        assert not state["errors"], state["errors"]
        assert not state["unexpected"], state["unexpected"]


def tab(page, name):
    return page.locator(f'.navigation [data-page="{name}"]')


def shown(page, name):
    expect(page.locator(f"#page-{name}")).to_be_visible()
    expect(tab(page, name)).to_have_attribute("aria-current", "page")
    assert page.evaluate("location.hash") == f"#{name}"


def blocked(page, names=GATED, *, estate_enabled=False):
    for name in names:
        expect(tab(page, name)).to_be_disabled()
        expect(tab(page, name)).to_have_attribute("aria-disabled", "true")
        assert tab(page, name).get_attribute("title")
    # Estate is a progress view only for a selected completed package, not a
    # public route for an empty project or a queued upload.
    if estate_enabled:
        expect(tab(page, "estate")).to_be_enabled()
        expect(tab(page, "estate")).to_have_attribute("aria-disabled", "false")
    else:
        expect(tab(page, "estate")).to_be_disabled()
        expect(tab(page, "estate")).to_have_attribute("aria-disabled", "true")
    for name in PUBLIC:
        expect(tab(page, name)).to_be_enabled()


def protected_reads(state):
    prefixes = ("/api/agents/", "/api/loops", "/api/redesigns", "/api/tracing/",
                "/api/objects", "/api/proposals", "/api/documents",
                "/api/settings", "/api/connections")
    return [r for r in state["requests"] if r["path"].startswith(prefixes)]


def choose_project(page):
    tab(page, 'projects').click()
    expect(page.locator(f'#project-select option[value="{PROJECT_A}"]')).to_have_count(1)
    page.locator("#project-select").select_option(PROJECT_A)


@pytest.mark.parametrize("fragment", GATED)
def test_initial_deep_links_never_expose_protected_sections_or_observer_reads(navigation, fragment):
    page, state, _ = navigation(fragment=fragment)
    shown(page, "projects")
    blocked(page)
    settle(page)
    assert not protected_reads(state)
    assert not any(r["path"] == "/api/overview" for r in state["requests"])


def test_project_without_selected_xml_remains_on_projects(navigation):
    page, state, _ = navigation(members=[member(), member(PACKAGE_B)])
    choose_project(page)
    expect(page.locator("#project-package option")).to_have_count(3)
    expect(page.locator("#project-package")).to_have_value("")
    shown(page, "projects")
    blocked(page)
    page.evaluate("location.hash = 'agents'")
    shown(page, "projects")
    expect(page.locator('#workspace-model-panel')).to_be_hidden()
    expect(page.locator('#agent-submit')).to_be_disabled()
    assert not protected_reads(state)


def test_selected_completed_xml_loading_allows_estate_only_and_guards_data_go(navigation):
    page, state, fulfill = navigation(hold=True)
    choose_project(page)
    expect(tab(page, "estate")).to_be_enabled()
    blocked(page, PROTECTED, estate_enabled=True)
    page.evaluate("location.hash = 'agents'")
    shown(page, "estate")
    expect(page.locator('#workspace-model-panel')).to_be_hidden()
    expect(page.locator('#agent-submit')).to_be_disabled()
    expect(page.locator("#package-ready")).to_have_text("Loading package understanding")
    expect(page.locator("#package-understanding-progress")).to_have_attribute('aria-busy', 'true')
    # Estate remains open as the progress view, but package navigation/actions stay disabled.
    tab(page, 'estate').click()
    shown(page, 'estate')
    expect(page.locator('#package-understanding-progress')).to_be_visible()
    expect(page.locator('[data-go="explorer"]')).to_be_disabled()
    page.locator('[data-go="explorer"]').evaluate('(button) => button.click()')
    shown(page, "estate")
    assert not protected_reads(state)
    route, data = state["pending"].pop()
    state["hold"] = False
    fulfill(route, data)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    for name in GATED:
        expect(tab(page, name)).to_be_enabled()
        expect(tab(page, name)).to_have_attribute("aria-disabled", "false")
    page.locator('[data-go="explorer"]').click()
    shown(page, "explorer")
    expect(page.locator("#inventory-count")).not_to_have_text("Not loaded.")
    assert any(r["path"] == "/api/objects" for r in state["requests"])


@pytest.mark.parametrize("fragment", PROTECTED)
def test_direct_hash_changes_are_guarded_without_protected_reads(navigation, fragment):
    page, state, _ = navigation(fragment="projects")
    shown(page, "projects")
    page.evaluate("value => { location.hash = value; }", fragment)
    shown(page, "projects")
    settle(page)
    assert not protected_reads(state)


def test_context_switch_clears_stale_state_and_does_not_storm_project_reads(navigation):
    page, state, _ = navigation()
    choose_project(page)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "agents").click()
    shown(page, "agents")
    page.locator("#agent-consent").check()
    # Native selection dispatches the same context change while its page is hidden.
    before = len([r for r in state["requests"] if r["path"] == "/api/projects"])
    page.locator("#project-select").select_option(PROJECT_B, force=True)
    shown(page, "projects")
    expect(page.locator('#workspace-model-panel')).to_be_hidden()
    expect(page.locator('#agent-submit')).to_be_disabled()
    blocked(page)
    expect(page.locator("#agent-consent")).to_be_checked()
    expect(page.locator("#runs-list")).to_contain_text("Package context changed")
    expect(page.locator("#agent-workflow option")).to_have_count(1)
    expect(page.locator("#project-package")).to_have_attribute("aria-busy", "false")
    settle(page)
    after = len([r for r in state["requests"] if r["path"] == "/api/projects"])
    assert after == before
    assert not [r for r in protected_reads(state)
                if r["headers"].get("x-workbench-project") == PROJECT_B]


def test_refresh_keeps_cached_page_locked_then_readiness_loss_redirects(navigation):
    page, state, fulfill = navigation(saved={"project": PROJECT_A, "package": PACKAGE_A})
    shown(page, "agents")
    page.locator("#agent-consent").check()
    settle(page)
    before = len(protected_reads(state))
    state["hold"] = True
    page.locator("#refresh").click()
    shown(page, "agents")
    blocked(page, PROTECTED, estate_enabled=True)
    expect(page.locator("#agent-submit")).to_be_disabled()
    settle(page)
    assert len(protected_reads(state)) == before
    route, data = state["pending"].pop()
    state["hold"] = False
    data.get('result', data)["ready"] = False
    fulfill(route, data)
    expect(page.locator("#package-ready")).to_have_text("Selected package understanding not loaded")
    shown(page, 'estate')
    blocked(page, PROTECTED, estate_enabled=True)
    assert len(protected_reads(state)) == before


def test_model_setup_navigation_does_not_bypass_assessment_prerequisite(navigation):
    page, state, _ = navigation(fragment="projects")
    shown(page, "projects")
    tab(page, "agents").dispatch_event("click")
    shown(page, "projects")
    expect(page.locator('#agent-submit')).to_be_disabled()
    settle(page)
    assert not protected_reads(state)


@pytest.mark.parametrize("fragment", ["logs", "evolution"])
def test_logs_and_roadmap_always_accessible_including_startup(navigation, fragment):
    page, state, _ = navigation(fragment=fragment)
    shown(page, fragment)
    blocked(page)
    tab(page, "projects").click()
    choose_project(page)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, fragment).click()
    shown(page, fragment)


def test_restored_scoped_ready_deep_link_needs_no_prior_page_visits(navigation):
    page, state, _ = navigation(saved={"project": PROJECT_A, "package": PACKAGE_A})
    shown(page, "agents")
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    for name in GATED:
        expect(tab(page, name)).to_be_enabled()
    assert any(r["path"] == "/api/agents/runs" for r in state["requests"])


def test_mismatched_ready_package_does_not_unlock_project_navigation(navigation):
    page, state, _ = navigation(overview_hash=PACKAGE_B,
                              saved={"project": PROJECT_A, "package": PACKAGE_A})
    shown(page, "estate")
    blocked(page, PROTECTED, estate_enabled=True)
    expect(tab(page, "explorer")).to_have_attribute(
        "title", "Load understanding for the selected project XML package first.")
    assert not protected_reads(state)


def test_legacy_actual_ready_retains_navigation_without_project(navigation):
    page, state, _ = navigation(legacy=True)
    shown(page, "agents")
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    for name in GATED:
        expect(tab(page, name)).to_be_enabled()
    assert any(r["path"] == "/api/agents/runs" for r in state["requests"])
    assert all("x-workbench-project" not in r["headers"] for r in protected_reads(state))


def test_agent_setup_without_package_is_navigation_locked_without_reads(navigation):
    page, state, _ = navigation(fragment='agents')
    shown(page, 'projects')
    expect(tab(page, 'agents')).to_be_disabled()
    expect(page.locator('#workspace-model-panel')).to_be_hidden()
    expect(page.locator('#agent-submit')).to_be_disabled()
    settle(page)
    assert not protected_reads(state)
    assert not any(row['path'] == '/api/overview' for row in state['requests'])