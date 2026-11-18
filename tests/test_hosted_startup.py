"""Actual app.js startup contracts, using the autoload suite's offline fixtures.

Only fixture GETs are allowed. No server, credentials, mutations or model calls.
"""
import json
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import (
    STATIC, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, PACKAGE_C,
    browser, member, ready, settle,
)


@pytest.fixture
def startup(browser):
    contexts = []

    def start(*, health=None, projects=None, saved=None, failure=None, hold_overview=False,
              hold_project_detail=False, loading_diagnostics=None):
        context = browser.new_context(offline=True, service_workers="block")
        if saved is not None:
            context.add_init_script(
                f"localStorage.setItem('lakeflow-studio-selection', JSON.stringify({json.dumps(saved)}));")
        page = context.new_page()
        state = {"requests": [], "unexpected": [], "errors": [], "pending": [], "held_project_details": [],
                 "projects": projects if projects is not None else {PROJECT_A: [member()]}}
        contexts.append((context, state))
        page.on("pageerror", lambda error: state["errors"].append(str(error)))
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>",
                        "", markup, flags=re.S)
        markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css">'
                                '<script src="/static/app.js" defer></script></head>')

        def fulfill(route, data, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

        def handler(route):
            request = route.request
            path = urlsplit(request.url).path
            if path == "/":
                route.fulfill(content_type="text/html", body=markup)
                return
            if path in ("/static/app.js", "/static/app.css"):
                route.fulfill(path=str(STATIC / path.rsplit("/", 1)[1]),
                              content_type="text/css" if path.endswith(".css") else "text/javascript")
                return
            entry = {"path": path, "method": request.method, "headers": request.all_headers()}
            state["requests"].append(entry)
            if request.method != "GET":
                state["unexpected"].append(entry)
                route.abort()
                return
            if path.startswith("/api/hosted/loading/"):
                if loading_diagnostics is None:
                    fulfill(route, {"detail": "No diagnostic fixture configured"}, 404)
                    return
                loading_diagnostics["calls"].append(request)
                if loading_diagnostics["hold"]:
                    loading_diagnostics["held"].append(route)
                    return
                else:
                    fulfill(route, loading_diagnostics["data"], loading_diagnostics["status"])
                return
            if path == failure:
                fulfill(route, {"detail": "State read unavailable"}, 503)
                return
            if path == "/api/health":
                data = health if health is not None else {
                    "status": "ok", "local_only": False, "authentication": "databricks_obo"}
            elif path == "/api/projects":
                data = {"items": [{"id": identifier, "name": identifier[:1],
                                  "ready_package_count": sum(m["status"] == "completed" for m in rows)}
                                 for identifier, rows in state["projects"].items()]}
            elif path.startswith("/api/projects/") and path.rsplit("/", 1)[1] in state["projects"]:
                identifier = path.rsplit("/", 1)[1]
                rows = state["projects"][identifier]
                data = {"id": identifier, "name": identifier[:1], "packages": rows, "jobs": [],
                        "ready_package_count": sum(m["status"] == "completed" for m in rows)}
                if hold_project_detail:
                    state["held_project_details"].append(route)
                    return
            elif path == "/api/input-packages":
                data = {"items": []}
            elif path == "/api/agents/runs":
                data = {"items": [], "total": 0}
            elif path == "/api/overview":
                project = entry["headers"].get("x-workbench-project", "")
                package = entry["headers"].get("x-workbench-package", "")
                selected = next((m for m in state["projects"].get(project, [])
                                 if m["sha256"] == package and m["status"] == "completed"), None)
                # The local legacy singleton remains available without project headers.
                legacy = not project
                data = {"ready": selected is not None or legacy,
                        "package": {"sha256": package or PACKAGE_C,
                                    "name": selected["name"] if selected else "legacy.xml"},
                        "summary": f"Understanding for {project}/{package}",
                        "counts": {"workflow": 1}, "agent": {"available": True},
                        "workflows": [{"id": f"wf/{package}", "name": "Actual workflow"}]}
                if hold_overview:
                    state["pending"].append((route, data))
                    return
            else:
                state["unexpected"].append(entry)
                route.abort()
                return
            fulfill(route, data)

        context.route("**/*", handler)
        page.goto("http://hosted-startup.invalid/")
        return page, state, fulfill

    yield start
    for context, state in contexts:
        context.close()
        assert not state["errors"], state["errors"]
        assert not state["unexpected"], state["unexpected"]
        assert all(r["method"] == "GET" for r in state["requests"])


def selection_page(page, state, count=1):
    expect(page.locator("#page-name")).to_have_text("New or existing project")
    expect(page.locator("#page-projects")).to_be_visible()
    expect(page.locator("#project-summary")).to_contain_text(f"{count} saved project")
    expect(page.locator("#project-select")).to_have_value("")
    expect(page.locator('#project-select option[value=""]')).to_have_text("Choose an existing project")
    settle(page)
    assert not any(r["path"] == "/api/overview" for r in state["requests"])
    assert not any(r["path"].startswith("/api/projects/") for r in state["requests"])
    assert page.evaluate("localStorage.getItem('lakeflow-studio-selection')") is None


@pytest.mark.parametrize("health", [
    {"status": "ok", "local_only": False},
    {"status": "ok", "authentication": "databricks_obo"},
])
def test_fresh_hosted_existing_project_waits_for_explicit_selection(startup, health):
    page, state, _ = startup(health=health)
    selection_page(page, state)
    page.locator("#refresh").click()
    selection_page(page, state)
    page.locator("#project-select").select_option(PROJECT_A)
    ready(page, state)
    assert all(r["headers"].get("x-workbench-project") == PROJECT_A
               and r["headers"].get("x-workbench-package") == PACKAGE_A
               for r in state["requests"] if r["path"] == "/api/overview")


def test_multiple_hosted_projects_never_choose_first_or_latest(startup):
    page, state, _ = startup(projects={PROJECT_B: [member(PACKAGE_C)], PROJECT_A: [member()]})
    selection_page(page, state, 2)
    page.locator("#project-select").select_option(PROJECT_B)
    ready(page, state, PACKAGE_C, PROJECT_B)


@pytest.mark.parametrize("failure", [None, "/api/agents/runs"])
def test_agent_submission_requires_scoped_persisted_run_confirmation(startup, failure):
    page, state, _ = startup(failure=failure)
    selection_page(page, state)
    page.locator("#project-select").select_option(PROJECT_A)
    ready(page, state)
    page.locator('[data-page="agents"]').click()
    # Confirm all user-input prerequisites for both history outcomes, so a
    # failed history read cannot pass merely because no workflow was chosen.
    expect(page.locator("#agent-submit")).to_be_disabled()
    page.locator("#agent-workflow").select_option(f"wf/{PACKAGE_A}")
    page.locator("#agent-prompt").fill("Review this selected workflow without executing or deploying it.")
    page.locator("#agent-consent").uncheck()
    expect(page.locator("#agent-submit")).to_be_disabled()
    page.locator("#agent-consent").check()
    if failure:
        expect(page.locator("#runs-list")).to_contain_text("Recorded agent runs unavailable")
        expect(page.locator("#agent-submit")).to_be_disabled()
    else:
        expect(page.locator("#agent-submit")).to_be_enabled()
    runs = [r for r in state["requests"] if r["path"] == "/api/agents/runs"]
    assert runs
    assert all(r["headers"].get("x-workbench-project") == PROJECT_A
               and r["headers"].get("x-workbench-package") == PACKAGE_A for r in runs)


@pytest.mark.parametrize("health", [{"status": "ok", "local_only": True}, {"status": "ok"}])
def test_local_legacy_singleton_startup_remains_unscoped(startup, health):
    page, state, _ = startup(health=health)
    expect(page.locator("#page-name")).to_have_text("Estate")
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#package-name")).to_have_text("legacy.xml")
    settle(page)
    overview = [r for r in state["requests"] if r["path"] == "/api/overview"]
    assert len(overview) == 1
    assert "x-workbench-project" not in overview[0]["headers"]
    assert "x-workbench-package" not in overview[0]["headers"]
    assert not any(r["path"] == "/api/projects" for r in state["requests"])


@pytest.mark.parametrize("members, remembered, expected", [
    ([member()], "", PACKAGE_A),
    ([member(), member(PACKAGE_B)], PACKAGE_B, PACKAGE_B),
])
def test_saved_hosted_selection_restores_exact_package_or_sole_completed(startup, members, remembered, expected):
    page, state, _ = startup(projects={PROJECT_A: members},
                             saved={"project": PROJECT_A, "package": remembered})
    ready(page, state, expected)
    assert all(r["headers"].get("x-workbench-project") == PROJECT_A
               for r in state["requests"] if r["path"] == "/api/overview")


def test_scoped_overview_activity_describes_selected_understanding(startup):
    page, state, fulfill = startup(hold_overview=True)
    selection_page(page, state)
    page.locator("#project-select").select_option(PROJECT_A)
    expect(page.locator("#activity-status")).to_have_text("Loading selected package understanding")
    expect(page.locator("#operation-banner")).not_to_contain_text("Refreshing package inventory")
    route, data = state["pending"].pop()
    fulfill(route, data)
    ready(page, state)


def test_project_detail_activity_uses_friendly_name_not_api_path(startup):
    page, state, _ = startup(hold_project_detail=True)
    selection_page(page, state)
    page.locator("#project-select").select_option(PROJECT_A)
    expect(page.locator("#activity-status")).to_contain_text("Loading selected project and its XML packages")
    expect(page.locator("#activity-status")).not_to_contain_text("/api/projects/")
    assert len(state["held_project_details"]) == 1
    route = state["held_project_details"].pop()
    route.fulfill(content_type="application/json", body=json.dumps({
        "id": PROJECT_A, "name": "A", "packages": [member()], "jobs": [], "ready_package_count": 1}))
    expect(page.locator("#project-package")).to_have_value(PACKAGE_A)


@pytest.mark.parametrize("failure", ["/api/health", "/api/projects"])
def test_startup_read_error_never_means_missing_xml_or_triggers_estate_hydration(startup, failure):
    page, state, _ = startup(failure=failure)
    expect(page.locator("#page-name")).to_have_text("New or existing project")
    if failure == "/api/health":
        expect(page.locator("#notice")).to_contain_text("Existing projects and XML may still be available")
    else:
        expect(page.locator("#project-summary")).to_contain_text("This does not mean they are missing")
    settle(page)
    assert not any(r["path"] == "/api/overview" for r in state["requests"])