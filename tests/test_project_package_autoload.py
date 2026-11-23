"""Offline Chromium contracts for the current index and unmodified app.js runtime.

Only GET fixtures are served. Unexpected traffic, reviews and all mutations fail.
Cancellation is deliberately ineffective to exercise response identity guards.
"""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "workbench" / "static"
PROJECT_A, PROJECT_B = "1" * 32, "2" * 32
PACKAGE_A, PACKAGE_B, PACKAGE_C = "a" * 64, "b" * 64, "c" * 64


def member(digest=PACKAGE_A, status="completed"):
    return {"sha256": digest, "name": f"{digest[:1]}.xml", "status": status}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    contexts = []

    def start(members, saved=None):
        context = browser.new_context(offline=True, service_workers="block")
        context.add_init_script("window.AbortController = class extends AbortController { abort() {} };")
        if saved is not None:
            context.add_init_script(
                f"localStorage.setItem('lakeflow-studio-selection', JSON.stringify({json.dumps(saved)}));")
        page = context.new_page()
        state = {"requests": [], "unexpected": [], "errors": [], "pending": [],
                 "hold": None, "projects": {PROJECT_A: members, PROJECT_B: [member(PACKAGE_C)]}}
        contexts.append((context, state))
        page.on("pageerror", lambda error: state["errors"].append(str(error)))
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>",
                        "", markup, flags=re.S)
        markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css">'
                                '<script src="/static/app.js" defer></script></head>')

        def fulfill(route, data):
            route.fulfill(content_type="application/json", body=json.dumps(data))

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
            if path == "/api/health":
                data = {"status": "ok"}
            elif path == "/api/projects":
                data = {"items": [{"id": identifier, "name": identifier[:1],
                                  "ready_package_count": sum(m["status"] == "completed" for m in rows)}
                                 for identifier, rows in state["projects"].items()]}
            elif path.startswith("/api/projects/") and path.rsplit("/", 1)[1] in state["projects"]:
                identifier = path.rsplit("/", 1)[1]
                rows = state["projects"][identifier]
                data = {"id": identifier, "name": identifier[:1], "packages": rows, "jobs": [],
                        "ready_package_count": sum(m["status"] == "completed" for m in rows)}
            elif path == "/api/input-packages":
                data = {"items": []}
            elif path == "/api/overview":
                project = entry["headers"].get("x-workbench-project", "")
                package = entry["headers"].get("x-workbench-package", "")
                selected = next((m for m in state["projects"].get(project, [])
                                 if m["sha256"] == package and m["status"] == "completed"), None)
                data = {"ready": selected is not None, "status": "package_selection_required",
                        "package": {"sha256": package, "name": selected["name"]} if selected else None,
                        "summary": f"Understanding for {project}/{package}",
                        "counts": {"workflow": 1} if selected else {},
                        "workflows": [{"id": f"wf/{package}", "name": "Actual workflow"}] if selected else [],
                        "agent": {"available": True}}
            else:
                state["unexpected"].append(entry)
                route.abort()
                return
            if state["hold"] and state["hold"](entry):
                state["pending"].append((route, data))
            else:
                fulfill(route, data)

        context.route("**/*", handler)
        page.goto("http://project-autoload.invalid/")
        if saved is None:
            expect(page.locator("#connection-status")).to_have_text("API connected · not ready")
            page.locator('[data-page="projects"]').click()
        expect(page.locator(f'#project-select option[value="{PROJECT_A}"]')).to_have_count(1)
        return page, state, fulfill

    yield start
    for context, state in contexts:
        context.close()
        assert not state["errors"], state["errors"]
        assert not state["unexpected"], state["unexpected"]
        assert all(r["method"] == "GET" for r in state["requests"])
        assert not any("/reviews" in r["path"] or "/agents/" in r["path"]
                       or "/loops/" in r["path"] for r in state["requests"])


def settle(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def overviews(state, project=PROJECT_A):
    return [r for r in state["requests"] if r["path"] == "/api/overview"
            and r["headers"].get("x-workbench-project") == project]


def ready(page, state, package=PACKAGE_A, project=PROJECT_A):
    expect(page.locator("#project-select")).to_have_value(project)
    expect(page.locator("#project-package")).to_have_value(package)
    expect(page.locator("#estate-package-select")).to_have_value(package)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#package-name")).to_have_text(f"{package[:1]}.xml")
    expect(page.locator("#overview-summary")).to_contain_text(f"Understanding for {project}/{package}")
    expect(page.locator(f'#agent-workflow option[value="wf/{package}"]')).to_have_count(1)
    expect(page.locator("#show-package-graph")).to_be_enabled()
    expect(page.locator("#agent-consent")).to_be_checked()
    expect(page.locator("#agent-submit")).to_be_disabled()
    expect(page.locator("#agent-submit")).to_have_attribute(
        "title", "Waiting for persisted run status to be confirmed.")
    settle(page)
    assert overviews(state, project)
    assert overviews(state, project)[-1]["headers"].get("x-workbench-package") == package
    assert all(r["headers"].get("x-workbench-package", package) == package
               for r in overviews(state, project))
    assert page.evaluate("JSON.parse(localStorage.getItem('lakeflow-studio-selection'))") == {
        "project": project, "package": package}


def test_selecting_project_autoloads_sole_completed_xml_and_understanding(ui):
    page, state, _ = ui([member()])
    page.locator("#project-select").select_option(PROJECT_A)
    ready(page, state)
    expect(page.locator("#project-package")).to_have_attribute(
        "data-empty-message", "Select a completed XML package to load its analysis.")


@pytest.mark.parametrize("remembered", ["", "d" * 64])
def test_single_completed_autoloads_with_absent_or_invalid_preference(ui, remembered):
    page, state, _ = ui([member()], {"project": PROJECT_A, "package": remembered})
    ready(page, state)


@pytest.mark.parametrize("members", [[member(), member(PACKAGE_B)], [member(PACKAGE_B), member()]])
def test_multiple_completed_require_explicit_selection_without_newest_or_merge(ui, members):
    page, state, _ = ui(members)
    page.locator("#project-select").select_option(PROJECT_A)
    expect(page.locator("#project-package option")).to_have_count(3)
    expect(page.locator("#project-package")).to_have_value("")
    expect(page.locator("#estate-package-select")).to_have_value("")
    expect(page.locator("#package-ready")).not_to_have_text("Package ready")
    expect(page.locator("#show-package-graph")).to_be_disabled()
    settle(page)
    assert all("x-workbench-package" not in r["headers"] for r in overviews(state))
    page.locator("#project-package").select_option(PACKAGE_B)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#estate-package-select")).to_have_value(PACKAGE_B)
    assert overviews(state)[-1]["headers"]["x-workbench-package"] == PACKAGE_B


def test_valid_remembered_preference_survives_multiple_ready_members(ui):
    page, state, _ = ui([member(), member(PACKAGE_B)], {"project": PROJECT_A, "package": PACKAGE_B})
    ready(page, state, PACKAGE_B)


def test_valid_current_selection_survives_refresh_and_membership_reordering(ui):
    page, state, _ = ui([member(), member(PACKAGE_B)])
    page.locator("#project-select").select_option(PROJECT_A)
    expect(page.locator("#project-package option")).to_have_count(3)
    page.locator("#project-package").select_option(PACKAGE_B)
    ready(page, state, PACKAGE_B)
    state["projects"][PROJECT_A] = [member(PACKAGE_C), member(PACKAGE_B), member()]
    page.locator("#refresh-projects").click()
    expect(page.locator("#project-package option")).to_have_count(4)
    ready(page, state, PACKAGE_B)


@pytest.mark.parametrize("status", ["staged", "failed"])
def test_noncompleted_member_never_competes_with_sole_completed_xml(ui, status):
    page, state, _ = ui([member(PACKAGE_B, status), member()],
                        {"project": PROJECT_A, "package": PACKAGE_B})
    ready(page, state)
    expect(page.locator(f'#estate-package-select option[value="{PACKAGE_B}"]')).to_be_disabled()
    expect(page.locator("#project-package option")).to_have_count(2)


@pytest.mark.parametrize("members", [[], [member(status="staged")], [member(status="failed")],
                                    [member(status="staged"), member(PACKAGE_B, "failed")],
                                    [member("malformed", "completed")]])
def test_no_valid_completed_member_never_loads_ready(ui, members):
    page, state, _ = ui(members)
    page.locator("#project-select").select_option(PROJECT_A)
    expect(page.locator("#project-package")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#project-package")).to_be_disabled()
    expect(page.locator("#project-package")).to_have_value("")
    expect(page.locator("#estate-package-select")).to_have_value("")
    expect(page.locator("#package-ready")).not_to_have_text("Package ready")
    expect(page.locator("#show-package-graph")).to_be_disabled()
    settle(page)
    assert overviews(state)
    assert all("x-workbench-package" not in r["headers"] for r in overviews(state))
    expect(page.locator("#project-package")).to_have_attribute("data-empty-message",
        "Packages found. Analysis must complete before they can be selected." if members
        else "No XML packages added to this project yet.")


@pytest.mark.parametrize("delayed", ["detail", "overview"])
def test_delayed_old_project_cannot_overwrite_newer_selection(ui, delayed):
    page, state, fulfill = ui([member()])
    state["hold"] = lambda r: (r["path"] == f"/api/projects/{PROJECT_A}" if delayed == "detail"
                              else r["path"] == "/api/overview"
                              and r["headers"].get("x-workbench-project") == PROJECT_A)
    page.locator("#project-select").select_option(PROJECT_A)
    page.wait_for_function("document.querySelector('#project-package').getAttribute('aria-busy') === 'true'")
    settle(page)
    assert len(state["pending"]) == 1
    page.locator("#project-select").select_option(PROJECT_B)
    ready(page, state, PACKAGE_C, PROJECT_B)
    route, data = state["pending"].pop()
    state["hold"] = None
    fulfill(route, data)
    settle(page)
    ready(page, state, PACKAGE_C, PROJECT_B)
    expect(page.locator("#project-summary")).to_contain_text("2 · 1 ready packages")
    expect(page.locator("#project-members")).to_contain_text("c.xml")
    expect(page.locator("#project-members")).not_to_contain_text("a.xml")