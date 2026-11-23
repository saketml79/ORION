"""Navigation prerequisites in Chromium with the real markup and all current scripts.

All services are mocked offline. Business mutations and unexpected reads fail.
Delayed responses ignore cancellation to verify context identity, not abort luck.
"""
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import (
    STATIC, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, PACKAGE_C,
    browser, member, settle,
)


PROTECTED = ("folder-migration", "readiness", "explorer", "agents", "proposals", "evidence", "validate", "release")
GATED = PROTECTED
PUBLIC = ("projects", "logs", "evolution")


@pytest.fixture
def navigation(browser):
    contexts = []

    def start(*, fragment="agents", legacy=False, saved=None, hold=False,
              members=None, overview_hash=None, agent_available=True, hold_models=False,
              hold_inventory=False, fail_inventory=False, hold_warm=False, wait_startup=True,
              hold_readiness=False, fail_readiness=False, missing_readiness=False, parity=False):
        context = browser.new_context(offline=True, service_workers="block")
        context.add_init_script("window.AbortController = class extends AbortController { abort() {} };")
        if saved:
            context.add_init_script(
                "localStorage.setItem('lakeflow-studio-selection', JSON.stringify("
                + json.dumps(saved) + "));")
        page = context.new_page()
        state = {"requests": [], "unexpected": [], "errors": [], "pending": [],
                 "pending_models": [],
                 "pending_inventory": [], "hold_inventory": hold_inventory, "fail_inventory": fail_inventory,
                 "pending_warm": [], "hold_warm": hold_warm, "warm_operations": {},
                 "pending_readiness": [], "hold_readiness": hold_readiness, "fail_readiness": fail_readiness,
                 "warm_result": {"cache_ready": True},
                 "external_ready": False,
                 "operation_id": None, "operation_pending": None, "hold_operation": False,
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
                if path == "/static/readiness.js" and missing_readiness:
                    route.fulfill(content_type="application/javascript", body="")
                    return
                asset = STATIC / path.removeprefix("/static/")
                route.fulfill(path=str(asset))
                return
            if path == '/api/hosted/operations' and req.method == 'POST':
                envelope = req.post_data_json
                if envelope['method'] == 'GET':
                    read_operation = envelope['id']
                    path = envelope['path']
                else:
                    state['operation_id'] = envelope['id']
                    route.fulfill(status=202, content_type='application/json',
                                  body=json.dumps({'operation_id': envelope['id'], 'status': 'queued'}))
                    return
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
                if parity and is_ready and not legacy:
                    data["retained_service_ready"] = True
                if state["hold"] and pid:
                    result = {"operation_id": read_operation, "status": "completed", "result": data} if read_operation else data
                    state["pending"].append((route, result))
                    return
            elif path == "/api/package/warm":
                data = {"ready": True, "cache_hit": False, "generation": "generation-test",
                    "generation_allowed": False, "deployment_allowed": False, **state["warm_result"]}
                if read_operation and state["hold_warm"]:
                    state["warm_operations"][read_operation] = data
                    fulfill(route, {"operation_id": read_operation, "status": "running",
                            "progress": {"stage": "artifact_files", "completed": 2,
                                 "total": 5, "unit": "files"}})
                    return
            elif path == "/api/logs":
                data = {"items": [], "total": 0}
            elif path == "/api/hosted/logs":
                data = {"items": [], "storage": "bounded test diagnostics"}
            elif re.fullmatch(r"/api/hosted/operations/[a-f0-9]{32}", path):
                operation_id = path.rsplit('/', 1)[-1]
                if operation_id in state['warm_operations']:
                    result = {"operation_id": operation_id, "status": "completed",
                              "result": state['warm_operations'][operation_id]}
                    if state['hold_warm']:
                        state['pending_warm'].append((route, result))
                        return
                    data = result
                    fulfill(route, data)
                    return
                if state['hold_operation']:
                    state['operation_pending'] = route
                    return
                data = {"operation_id": operation_id, "status": "completed",
                        "result": {"status": "completed"}}
            elif path == "/api/objects":
                digest = entry["headers"].get("x-workbench-package", "")
                items = state.get("objects_by_package", {}).get(digest, state["objects"])
                data = {"items": items, "total": len(items)}
                if state["hold_inventory"]:
                    result = {"operation_id": read_operation, "status": "completed", "result": data} if read_operation else data
                    state["pending_inventory"].append((route, result))
                    return
                if state["fail_inventory"]:
                    route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "inventory unavailable"}))
                    return
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
                data['scope']['scenario'] = parse_qs(urlsplit(req.url).query).get('scenario', ['original'])[0]
                if state['hold_readiness']:
                    state['pending_readiness'].append((route, data))
                    return
                if state['fail_readiness']:
                    route.fulfill(status=503, content_type='application/json', body='{}')
                    return
            elif re.fullmatch(r"/api/agents/runs/[a-f0-9]{32}", path):
                run_id = path.rsplit("/", 1)[-1]
                data = state.get("agent_run_details", {}).get(run_id, {"id": run_id, "status": "completed"})
            elif path in ("/api/documents",
                          "/api/agents/runs", "/api/redesigns", "/api/connections"):
                data = {"items": [], "total": 0}
                if parity and path == "/api/agents/runs":
                    data.update(run_status_verified=True, active_run_present=False)
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
                data = ({"mode": "external", "configured": True, "inference_ready": True,
                         "status": "configured", "provider": "openai", "model": "test-model",
                         "expires_in_seconds": 3600} if state["external_ready"] else
                        {"mode": "databricks", "configured": False,
                         "inference_ready": False, "status": "default"})
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
        if wait_startup:
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


def wait_pending(page, state, key):
    for _ in range(100):
        if state[key]:
            return
        page.wait_for_timeout(50)
    pytest.fail(f"Deferred request was not observed: {key}")


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
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    assert before == 1
    page.locator('[data-go="explorer"]').click()
    shown(page, "explorer")
    expect(page.locator("#inventory-count")).not_to_have_text("Not loaded.")
    expect(page.locator("#objects-prev")).to_be_disabled()
    expect(page.locator("#objects-next")).to_be_disabled()
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before


def test_inventory_preload_pending_failure_and_retry_gate_navigation(navigation):
    page, state, fulfill = navigation(fragment="projects", hold=True)
    state["hold_inventory"] = True
    choose_project(page)
    wait_pending(page, state, "pending")
    route, data = state["pending"].pop()
    state["hold"] = False
    fulfill(route, data)
    expect(page.locator("#package-ready")).to_have_text("Loading Explorer inventory")
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, "estate").click()
    expect(page.locator("#package-understanding-progress")).to_contain_text("Explorer inventory")
    expect(page.locator("#package-understanding-progress")).to_have_attribute("aria-busy", "true")
    tab(page, "logs").click()
    shown(page, "logs")
    tab(page, "projects").click()
    shown(page, "projects")
    wait_pending(page, state, "pending_inventory")
    route, _ = state["pending_inventory"].pop()
    state["hold_inventory"] = False
    route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "inventory unavailable"}))
    expect(page.locator("#package-ready")).to_have_text("Explorer inventory unavailable")
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, "estate").click()
    expect(page.locator("#package-understanding-progress")).to_contain_text("inventory unavailable")
    page.get_by_role("button", name="Retry Explorer inventory").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    assert before == 2
    tab(page, "explorer").click()
    shown(page, "explorer")
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before


def test_preload_old_selection_cannot_publish_after_new_package_ready(navigation):
    page, state, fulfill = navigation(fragment="projects", members=[member(), member(PACKAGE_B)])
    state["objects_by_package"] = {
        PACKAGE_A: [{"id": "old", "name": "Old object", "kind": "workflow"}],
        PACKAGE_B: [{"id": "new", "name": "New object", "kind": "workflow"}],
    }
    choose_project(page)
    state["hold_inventory"] = True
    page.locator("#project-package").select_option(PACKAGE_A)
    expect(page.locator("#package-ready")).to_have_text("Loading Explorer inventory")
    wait_pending(page, state, "pending_inventory")
    old_route, old_data = state["pending_inventory"].pop()
    state["hold_inventory"] = False
    page.locator("#project-package").select_option(PACKAGE_B, force=True)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    fulfill(old_route, old_data)
    settle(page)
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    tab(page, "explorer").click()
    shown(page, "explorer")
    expect(page.locator("#object-list")).to_contain_text("New object")
    expect(page.locator("#object-list")).not_to_contain_text("Old object")
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before == 2


def test_restored_startup_keeps_public_navigation_usable_while_inventory_pending(navigation):
    page, state, fulfill = navigation(fragment="agents", saved={"project": PROJECT_A, "package": PACKAGE_A},
                                     hold_inventory=True, wait_startup=False)
    expect(page.locator("#package-ready")).to_have_text("Loading Explorer inventory")
    wait_pending(page, state, "pending_inventory")
    expect(tab(page, "explorer")).to_be_disabled()
    tab(page, "logs").click()
    expect(page.locator("#page-logs")).to_be_visible()
    expect(page.locator("#logs-summary")).not_to_have_text("No log request for this selection yet.")
    tab(page, "projects").click()
    expect(page.locator("#page-projects")).to_be_visible()
    tab(page, "logs").click()
    route, data = state["pending_inventory"].pop()
    state["hold_inventory"] = False
    fulfill(route, data)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    shown(page, "logs")
    expect(tab(page, "explorer")).to_be_enabled()


def test_hosted_warm_progress_blocks_actual_navigation_until_cache_ready(navigation):
    page, state, fulfill = navigation(fragment="agents", saved={"project": PROJECT_A, "package": PACKAGE_A},
                                     hold_warm=True, wait_startup=False)
    expect(page.locator("#package-ready")).to_have_text("Preparing package workspace")
    shown(page, "estate")
    blocked(page, PROTECTED, estate_enabled=True)
    progress = page.locator("#package-understanding-progress")
    expect(progress).to_be_visible()
    expect(progress).to_contain_text("Verifying committed package files")
    expect(progress).to_contain_text("2 of 5 files")
    expect(progress.locator("progress")).to_have_attribute("value", "2")
    expect(progress.locator("progress")).to_have_attribute("max", "5")
    assert not [r for r in protected_reads(state) if r["path"] != "/api/objects"]
    page.evaluate("location.hash = 'agents'")
    shown(page, "estate")
    tab(page, "logs").click()
    shown(page, "logs")
    tab(page, "projects").click()
    shown(page, "projects")
    tab(page, "estate").click()
    expect(progress).to_contain_text("2 of 5 files")
    wait_pending(page, state, "pending_warm")
    route, result = state["pending_warm"].pop()
    state["hold_warm"] = False
    fulfill(route, result)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(tab(page, "agents")).to_be_enabled()
    warm_count = sum(r["path"] == "/api/package/warm" for r in state["requests"])
    tab(page, "agents").click()
    shown(page, "agents")
    tab(page, "logs").click()
    tab(page, "agents").click()
    shown(page, "agents")
    settle(page)
    assert sum(r["path"] == "/api/package/warm" for r in state["requests"]) == warm_count == 1


def test_hosted_readiness_delays_gate_and_navigation_reuses_preload(navigation):
    page, state, fulfill = navigation(fragment='readiness', saved={'project': PROJECT_A, 'package': PACKAGE_A},
                                      hold_readiness=True, wait_startup=False)
    wait_pending(page, state, 'pending_readiness')
    expect(page.locator('#package-ready')).to_have_text('Loading readiness evidence')
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, 'estate').click()
    expect(page.locator('#package-understanding-progress')).to_contain_text('Readiness evidence')
    expect(page.locator('#package-understanding-progress')).to_have_attribute('aria-busy', 'true')
    tab(page, 'logs').click(); shown(page, 'logs')
    tab(page, 'projects').click(); shown(page, 'projects')
    tab(page, 'estate').click(); shown(page, 'estate')
    route, result = state['pending_readiness'].pop()
    state['hold_readiness'] = False
    fulfill(route, result)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(tab(page, 'readiness')).to_be_enabled()
    before = [r for r in state['requests'] if r['path'] == '/api/readiness']
    assert len(before) == 1
    assert before[0]['headers']['x-workbench-project'] == PROJECT_A
    assert before[0]['headers']['x-workbench-package'] == PACKAGE_A
    tab(page, 'readiness').click(); shown(page, 'readiness')
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    tab(page, 'estate').click(); tab(page, 'readiness').click()
    settle(page)
    assert len([r for r in state['requests'] if r['path'] == '/api/readiness']) == 1
    # Later history invalidation is not a startup prerequisite or navigation lock.
    page.evaluate("document.dispatchEvent(new CustomEvent('orion:agent-run', {detail: {status: 'completed'}}))")
    tab(page, 'estate').click()
    expect(tab(page, 'readiness')).to_be_enabled()
    tab(page, 'readiness').click(); shown(page, 'readiness')
    settle(page)
    assert len([r for r in state['requests'] if r['path'] == '/api/readiness']) == 1


def test_hosted_readiness_failure_requires_explicit_estate_retry(navigation):
    page, state, _ = navigation(fragment='agents', saved={'project': PROJECT_A, 'package': PACKAGE_A},
                                fail_readiness=True, wait_startup=False)
    expect(page.locator('#package-ready')).to_have_text('Readiness unavailable')
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, 'estate').click()
    progress = page.locator('#package-understanding-progress')
    expect(progress).to_contain_text('HTTP 503')
    expect(progress).to_have_attribute('aria-busy', 'false')
    tab(page, 'logs').click(); tab(page, 'projects').click(); tab(page, 'estate').click()
    page.locator('#refresh').click()
    expect(page.locator('#package-ready')).to_have_text('Readiness unavailable')
    assert sum(r['path'] == '/api/readiness' for r in state['requests']) == 1
    state['fail_readiness'] = False
    page.get_by_role('button', name='Retry readiness').click()
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    assert sum(r['path'] == '/api/readiness' for r in state['requests']) == 2
    tab(page, 'readiness').click(); shown(page, 'readiness')
    settle(page)
    assert sum(r['path'] == '/api/readiness' for r in state['requests']) == 2


def test_missing_readiness_module_is_reported_not_assumed_ready(navigation):
    page, state, _ = navigation(fragment='agents', saved={'project': PROJECT_A, 'package': PACKAGE_A},
                                missing_readiness=True, wait_startup=False)
    expect(page.locator('#package-ready')).to_have_text('Readiness unavailable')
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, 'estate').click()
    expect(page.locator('#package-understanding-progress')).to_contain_text('Readiness module is unavailable')
    page.get_by_role('button', name='Retry readiness').click()
    expect(page.locator('#package-ready')).to_have_text('Readiness unavailable')
    assert not [r for r in state['requests'] if r['path'] == '/api/readiness']


def test_stale_readiness_completion_does_not_unlock_new_selection(navigation):
    page, state, fulfill = navigation(fragment='projects', members=[member(), member(PACKAGE_B)])
    choose_project(page)
    state['hold_readiness'] = True
    page.locator('#project-package').select_option(PACKAGE_A)
    wait_pending(page, state, 'pending_readiness')
    old_route, old_result = state['pending_readiness'].pop()
    page.locator('#project-package').select_option(PACKAGE_B, force=True)
    wait_pending(page, state, 'pending_readiness')
    new_route, new_result = state['pending_readiness'].pop()
    fulfill(old_route, old_result)
    settle(page)
    expect(page.locator('#package-ready')).to_have_text('Loading readiness evidence')
    blocked(page, PROTECTED, estate_enabled=True)
    state['hold_readiness'] = False
    fulfill(new_route, new_result)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    tab(page, 'readiness').click(); shown(page, 'readiness')
    assert page.evaluate('orionReadiness.getStatus().scope.packageHash') == PACKAGE_B
    assert [r['headers'].get('x-workbench-package') for r in state['requests']
            if r['path'] == '/api/readiness'] == [PACKAGE_A, PACKAGE_B]


def test_warm_completes_during_inventory_refresh_without_unlocking_early(navigation):
    page, state, fulfill = navigation(fragment="agents", saved={"project": PROJECT_A, "package": PACKAGE_A},
                                     hold_warm=True, wait_startup=False)
    shown(page, "estate")
    wait_pending(page, state, "pending_warm")
    warm_route, warm_result = state["pending_warm"].pop()
    state["hold_inventory"] = True
    page.locator("#refresh").click()
    wait_pending(page, state, "pending_inventory")
    fulfill(warm_route, warm_result)
    blocked(page, PROTECTED, estate_enabled=True)
    expect(page.locator("#package-ready")).to_have_text("Loading Explorer inventory")
    inventory_route, inventory_result = state["pending_inventory"].pop()
    state["hold_inventory"] = False
    state["hold_warm"] = False
    fulfill(inventory_route, inventory_result)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "agents").click()
    shown(page, "agents")
    assert sum(r["path"] == "/api/package/warm" for r in state["requests"]) == 1


@pytest.mark.parametrize("failure", ["cache_not_retained", "operation_failed"])
def test_hosted_warm_failure_stays_locked_until_explicit_retry(navigation, failure):
    page, state, fulfill = navigation(fragment="agents", saved={"project": PROJECT_A, "package": PACKAGE_A},
                                     hold_warm=True, wait_startup=False)
    shown(page, "estate")
    wait_pending(page, state, "pending_warm")
    route, result = state["pending_warm"].pop()
    state["hold_warm"] = False
    if failure == "cache_not_retained":
        result["result"] = {"cache_ready": False, "cache_reason": "capacity unavailable"}
    else:
        result = {"operation_id": result["operation_id"], "status": "failed", "recovery": "storage unavailable"}
    fulfill(route, result)
    expect(page.locator("#package-ready")).to_have_text("Package preparation unavailable")
    progress = page.locator("#package-understanding-progress")
    expect(progress).to_contain_text("capacity unavailable" if failure == "cache_not_retained" else "storage unavailable")
    expect(progress).to_have_attribute("aria-busy", "false")
    blocked(page, PROTECTED, estate_enabled=True)
    tab(page, "logs").click()
    tab(page, "projects").click()
    tab(page, "estate").click()
    page.locator("#refresh").click()
    expect(page.locator("#package-ready")).to_have_text("Package preparation unavailable")
    assert sum(r["path"] == "/api/package/warm" for r in state["requests"]) == 1
    page.get_by_role("button", name="Retry package preparation").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    assert sum(r["path"] == "/api/package/warm" for r in state["requests"]) == 2
    tab(page, "agents").click()
    shown(page, "agents")


def test_stale_warm_completion_cannot_unlock_or_replace_new_selection(navigation):
    page, state, fulfill = navigation(fragment="projects", members=[member(), member(PACKAGE_B)])
    choose_project(page)
    state["hold_warm"] = True
    page.locator("#project-package").select_option(PACKAGE_A)
    wait_pending(page, state, "pending_warm")
    old_route, old_result = state["pending_warm"].pop()
    page.locator("#project-package").select_option(PACKAGE_B, force=True)
    wait_pending(page, state, "pending_warm")
    new_route, new_result = state["pending_warm"].pop()
    tab(page, "estate").click()
    fulfill(old_route, old_result)
    settle(page)
    expect(page.locator("#package-ready")).to_have_text("Preparing package workspace")
    blocked(page, PROTECTED, estate_enabled=True)
    state["hold_warm"] = False
    fulfill(new_route, new_result)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "explorer").click()
    shown(page, "explorer")
    assert [r["headers"].get("x-workbench-package") for r in state["requests"]
            if r["path"] == "/api/package/warm"] == [PACKAGE_A, PACKAGE_B]


def test_legacy_inventory_failure_can_retry_from_estate(navigation):
    page, state, _ = navigation(legacy=True, fail_inventory=True)
    shown(page, "estate")
    expect(tab(page, "explorer")).to_be_disabled()
    expect(page.locator("#package-ready")).to_have_text("Explorer inventory unavailable")
    state["fail_inventory"] = False
    page.get_by_role("button", name="Retry Explorer inventory").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    tab(page, "explorer").click()
    shown(page, "explorer")
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before == 2


def test_refresh_preloads_once_and_preserves_explorer_filters(navigation):
    page, state, _ = navigation(fragment="projects")
    choose_project(page)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "explorer").click()
    page.locator("#object-search").fill("needle")
    expect(page.locator("#inventory-count")).to_contain_text("matching objects")
    page.wait_for_timeout(350)
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    page.locator("#refresh").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#object-search")).to_have_value("needle")
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before + 1
    tab(page, "estate").click()
    tab(page, "explorer").click()
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before + 1


def test_overlapping_same_package_refresh_ignores_older_inventory(navigation):
    page, state, fulfill = navigation(fragment="projects")
    choose_project(page)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    state["objects"] = [{"id": "old", "name": "Old refresh", "kind": "workflow"}]
    state["hold_inventory"] = True
    page.locator("#refresh").click()
    wait_pending(page, state, "pending_inventory")
    old_route, old_data = state["pending_inventory"].pop()
    state["objects"] = [{"id": "new", "name": "Current refresh", "kind": "workflow"}]
    state["hold_inventory"] = False
    page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    fulfill(old_route, old_data)
    settle(page)
    before = len([r for r in state["requests"] if r["path"] == "/api/objects"])
    tab(page, "explorer").click()
    expect(page.locator("#object-list")).to_contain_text("Current refresh")
    expect(page.locator("#object-list")).not_to_contain_text("Old refresh")
    settle(page)
    assert len([r for r in state["requests"] if r["path"] == "/api/objects"]) == before == 3


def test_invoke_remains_locked_across_logs_navigation_until_hosted_operation_finishes(navigation):
    page, state, _ = navigation(fragment='projects', parity=True)
    state['external_ready'] = True
    choose_project(page)
    expect(tab(page, 'agents')).to_be_enabled(timeout=20000)
    tab(page, 'agents').click()
    shown(page, 'agents')
    invoke = page.locator('#agent-submit')
    page.locator('#agent-workflow').select_option('wf/selected')
    page.locator('#agent-review-scope').dispatch_event('change')
    expect(invoke).to_be_enabled()
    state['hold_operation'] = True
    page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}); }")
    expect(invoke).to_be_disabled()
    expect(invoke).to_have_attribute('title', re.compile('hosted operation is still active'))
    expect(page.locator('#orion-hosted-intake-activity')).to_contain_text('DEBUG ONLY · TO BE REMOVED IN PROD')
    page.locator('.navigation [data-page="logs"]').click()
    shown(page, 'logs')
    page.locator('.navigation [data-page="agents"]').click()
    shown(page, 'agents')
    expect(invoke).to_be_disabled()
    wait_pending(page, state, 'operation_pending')
    pending = state['operation_pending']
    assert pending is not None
    state['hold_operation'] = False
    pending.fulfill(json={"operation_id": state['operation_id'], "status": "completed",
                          "result": {"status": "completed"}})
    expect(invoke).to_be_enabled(timeout=5000)
    assert page.evaluate('orionHostedIntake.hasActiveOperation()') is False


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
    prompt = page.locator("#agent-prompt")
    default_prompt = prompt.input_value()
    prompt.fill("Unsaved prompt for the old package")
    # Native selection dispatches the same context change while its page is hidden.
    before = len([r for r in state["requests"] if r["path"] == "/api/projects"])
    page.locator("#project-select").select_option(PROJECT_B, force=True)
    shown(page, "projects")
    expect(page.locator('#workspace-model-panel')).to_be_hidden()
    expect(page.locator('#agent-submit')).to_be_disabled()
    blocked(page)
    expect(prompt).to_have_value(default_prompt)
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
    page.locator("#agent-prompt").fill("Keep this prompt during refresh")
    settle(page)
    before = len(protected_reads(state))
    state["hold"] = True
    page.locator("#refresh").click()
    shown(page, "agents")
    blocked(page, PROTECTED, estate_enabled=True)
    expect(page.locator("#agent-prompt")).to_have_value("Keep this prompt during refresh")
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