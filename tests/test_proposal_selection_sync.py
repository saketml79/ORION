"""Offline Chromium: real app + redesign scripts, mocked GETs, no server or data writes."""
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "workbench" / "static"
PROJECT = "1" * 32
PACKAGE_A, PACKAGE_B = "a" * 64, "b" * 64


def candidates(scenario="original"):
    return [{"id": f"{scenario}-proposal-{i}", "workflow_id": f"wf/{i}",
             "name": f"Candidate {i}", "scenario": scenario, "version": 1,
             "status": "draft", "blockers": [], "generation_allowed": False,
             "deployment_allowed": False, "graph": {"nodes": [], "edges": []}}
            for i in range(65)]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context(offline=True, service_workers="block",
                                  viewport={"width": 1440, "height": 1100})
    # Response identity checks must work even when cancellation is ineffective.
    context.add_init_script("window.AbortController = class extends AbortController { abort() {} };")
    page = context.new_page()
    state = {"requests": [], "unexpected": [], "errors": [], "pending": [],
             "hold": None, "rows": None, "proposal_status": 200}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css">'
                            '<script src="/static/scoped-reads.js" defer></script>'
                            '<script src="/static/app.js" defer></script>'
                            '<script src="/static/redesign-review.js" defer></script></head>')

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def route_handler(route):
        request = route.request
        path = urlsplit(request.url).path
        query = parse_qs(urlsplit(request.url).query)
        if path == "/":
            route.fulfill(content_type="text/html", body=markup)
            return
        if path in ("/static/app.js", "/static/redesign-review.js", "/static/scoped-reads.js", "/static/app.css"):
            route.fulfill(path=str(STATIC / path.rsplit("/", 1)[1]),
                          content_type="text/css" if path.endswith(".css") else "text/javascript")
            return
        entry = {"path": path, "query": query, "method": request.method,
                 "headers": request.all_headers()}
        state["requests"].append(entry)
        if not path.startswith("/api/") or request.method != "GET":
            state["unexpected"].append(entry)
            route.abort()
            return
        scenario = query.get("scenario", ["original"])[0]
        status = 200
        if path == "/api/health":
            data = {"status": "ok"}
        elif path == "/api/overview":
            package = entry["headers"].get("x-workbench-package", "legacy")
            ready = not entry["headers"].get("x-workbench-project") or package != "legacy"
            data = {"ready": ready, "package": {"sha256": package, "name": "Mock package"},
                    "workflows": [{"id": f"wf/{i}", "name": f"Workflow {i}"} for i in range(66)] if ready else []}
        elif path == "/api/proposals":
            rows = state["rows"] if state["rows"] is not None else candidates(scenario)
            workflow_id = query.get("workflow_id", [None])[0]
            rows = [row for row in rows if row["scenario"] == scenario
                and (workflow_id is None or row["workflow_id"] == workflow_id)]
            offset, limit = int(query["offset"][0]), int(query["limit"][0])
            assert 0 <= offset and 1 <= limit <= 50
            data = {"items": rows[offset:offset + limit], "total": len(rows),
                "offset": offset, "limit": limit, "scenario": scenario,
                "summary_only": True, "truncated": offset + limit < len(rows)}
            status = state["proposal_status"]
        elif path.startswith("/api/proposals/"):
            # List responses are summaries; selected details use their own GET.
            proposal_id = path.rsplit("/", 1)[-1]
            data = next((p for p in candidates(scenario) if p["id"] == proposal_id), None)
            if data is None:
                data, status = {"detail": "Unknown proposal"}, 404
        elif path in ("/api/redesigns", "/api/reviews", "/api/input-packages"):
            data = {"items": [], "total": 0}
        elif path.startswith("/api/object/"):
            data = {"graph": {"nodes": [], "edges": []}}
        elif path.startswith("/api/workflows/") and path.endswith("/migration-context"):
            # This selection-sync fixture does not model package implementation.
            # Explicitly test the unavailable-contract state, not unexpected traffic.
            data, status = {"detail": "No canonical package context in this fixture"}, 404
        elif path == "/api/projects":
            data = {"items": [{"id": PROJECT, "name": "Mock project", "ready_package_count": 2}]}
        elif path == f"/api/projects/{PROJECT}":
            data = {"id": PROJECT, "name": "Mock project", "ready_package_count": 2, "jobs": [],
                    "packages": [{"sha256": sha, "name": name, "status": "completed"}
                                 for sha, name in [(PACKAGE_A, "A"), (PACKAGE_B, "B")]]}
        else:
            state["unexpected"].append(entry)
            route.abort()
            return
        if state["hold"] and state["hold"](entry):
            state["pending"].append((route, data, status))
        else:
            fulfill(route, data, status)

    context.route("**/*", route_handler)
    page.goto("http://selection.invalid/")
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#redesign-workflow option")).to_have_count(67)
    yield page, state, fulfill
    assert not state["errors"], state["errors"]
    assert not state["unexpected"], state["unexpected"]
    assert all(r["method"] == "GET" for r in state["requests"])
    context.close()


def open_proposals(page):
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#redesign-status")).to_contain_text("No agent-submitted design")
    page.locator('#proposal-baseline > summary').click()
    expect(page.locator("#proposal-count")).to_contain_text("65 actual candidates")


def selected(page, index, scenario="original"):
    expect(page.locator("#proposal-detail h2")).to_have_text(f"Candidate {index}")
    expect(page.locator("#proposal-list .selected")).to_have_count(1)
    expect(page.locator("#proposal-list .selected .truncate")).to_have_text(f"Candidate {index}")
    expect(page.locator("#proposal-detail")).to_contain_text(f"{scenario}-proposal-{index}")
    expect(page.locator("#redesign-workflow")).to_have_value(f"wf/{index}")


def reads(state, path):
    return [r for r in state["requests"] if r["path"] == path]


def test_collapsed_baseline_performs_no_candidate_graph_or_review_reads(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-status')).to_contain_text('No agent-submitted design')
    assert not page.locator('#proposal-baseline').get_attribute('open')
    assert not reads(state, '/api/proposals')
    assert not reads(state, '/api/reviews')
    assert not any(r['path'].startswith('/api/object/') for r in state['requests'])
    page.locator('#redesign-workflow').select_option('wf/3')
    expect(page.locator('#redesign-status')).to_contain_text('No agent-submitted design')
    assert not reads(state, '/api/proposals')
    page.locator('#proposal-baseline > summary').click()
    selected(page, 3)
    before = len(state['requests'])
    page.locator('#proposal-list button.selected').click()
    settle(page)
    assert len(state['requests']) == before


def settle(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


@pytest.mark.parametrize("width", [1440, 390])
def test_scoped_panel_gap_above_unchanged_two_column(ui, width):
    page, _, _ = ui
    open_proposals(page)
    page.set_viewport_size({"width": width, "height": 1100})
    geometry = page.locator("#redesign-review").evaluate("""panel => ({
      margin: getComputedStyle(panel).marginBottom,
    nextIsGrid: Boolean(panel.nextElementSibling.querySelector('.proposal-layout')),
      gap: panel.nextElementSibling.getBoundingClientRect().top - panel.getBoundingClientRect().bottom
    })""")
    assert geometry == {"margin": "24px", "nextIsGrid": True, "gap": 24}


@pytest.mark.parametrize("index", [3, 62])
def test_top_selects_exact_server_filtered_candidate_without_feedback_reads(ui, index):
    page, state, _ = ui
    open_proposals(page)
    before = len(reads(state, "/api/redesigns"))
    page.locator("#redesign-workflow").select_option(f"wf/{index}")
    selected(page, index)
    expect(page.locator("#proposals-prev")).to_be_disabled()
    expect(page.locator("#proposals-next")).to_be_disabled()
    expect(page.locator("#proposal-list button")).to_have_count(1)
    expect(page.locator("#proposal-count")).to_contain_text("1 of 1 actual candidates")
    settle(page)
    assert len(reads(state, "/api/proposals")) == 2
    assert reads(state, "/api/proposals")[-1]["query"]["workflow_id"] == [f"wf/{index}"]
    assert len(reads(state, "/api/redesigns")) == before + 1
    # Optional source evidence remains collapsed. Only the baseline graph reads.
    assert len(reads(state, f"/api/object/wf%2F{index}")) == 1
    assert len(reads(state, "/api/reviews")) == 1


def test_unfiltered_candidates_follow_server_pagination(ui):
    page, state, _ = ui
    open_proposals(page)
    expect(page.locator("#proposal-list button")).to_have_count(20)
    expect(page.locator("#proposals-prev")).to_be_disabled()
    page.locator("#proposals-next").click()
    expect(page.locator("#proposal-list button .truncate").first).to_have_text("Candidate 20")
    expect(page.locator("#proposal-list button")).to_have_count(20)
    expect(page.locator("#proposals-prev")).to_be_enabled()
    assert reads(state, "/api/proposals")[-1]["query"]["offset"] == ["20"]


def test_lower_selection_still_updates_top_once_and_only_known_workflows(ui):
    page, state, _ = ui
    open_proposals(page)
    before = len(reads(state, "/api/redesigns"))
    page.locator("#proposal-list button").nth(7).click()
    selected(page, 7)
    settle(page)
    assert len(reads(state, "/api/redesigns")) == before + 1
    page.locator("#proposal-detail").evaluate("""host => {
      host.querySelectorAll('.detail-row').forEach(row => {
        if (row.querySelector('.detail-key').textContent === 'workflow id')
          row.querySelector('.detail-value').textContent = 'not-in-overview';
      });
    }""")
    settle(page)
    expect(page.locator("#redesign-workflow")).to_have_value("wf/7")
    assert len(reads(state, "/api/redesigns")) == before + 1


def test_blank_top_clears_lower_selection_and_detail_without_fabrication(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/62")
    selected(page, 62)
    page.locator("#redesign-workflow").select_option("")
    expect(page.locator("#proposal-list .selected")).to_have_count(0)
    expect(page.locator("#proposal-detail")).to_have_text("Select a proposal to review rationale, evidence, and gates.")
    expect(page.locator("#proposal-detail form")).to_have_count(0)
    expect(page.locator("#proposals-prev")).to_be_disabled()
    settle(page)
    expect(page.locator("#redesign-workflow")).to_have_value("")
    assert len(reads(state, "/api/proposals")) == 3


def test_absent_workflow_clears_old_detail_with_explicit_message(ui):
    page, _, _ = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/3")
    selected(page, 3)
    page.locator("#redesign-workflow").select_option("wf/65")
    expect(page.locator("#proposal-list .selected")).to_have_count(0)
    expect(page.locator("#proposal-detail")).to_have_text("No structural candidate is available for workflow wf/65 in the selected scenario.")
    expect(page.locator("#proposal-detail form")).to_have_count(0)
    settle(page)
    expect(page.locator("#redesign-workflow")).to_have_value("wf/65")


@pytest.mark.parametrize("result", ["empty", "wrong-scenario", "failed"])
def test_missing_or_failed_candidates_never_retain_old_selection(ui, result):
    page, state, _ = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/3")
    selected(page, 3)
    if result == "failed":
        state["proposal_status"] = 503
    else:
        state["rows"] = [] if result == "empty" else candidates("original")
    page.locator("#scenario").select_option("bronze")
    if result == "failed":
        expect(page.locator("#proposal-detail")).to_contain_text("Current proposal data is unavailable")
        expect(page.locator("#proposal-count")).to_have_text("Could not retrieve proposal counts.")
    else:
        expect(page.locator("#proposal-detail")).to_contain_text("No structural candidate is available for workflow wf/3")
    expect(page.locator("#proposal-list .selected")).to_have_count(0)
    expect(page.locator("#proposal-detail form")).to_have_count(0)
    expect(page.locator("#redesign-workflow")).to_have_value("wf/3")
    page.locator("#redesign-workflow").select_option("wf/4")
    expect(page.locator("#proposal-detail h2")).to_have_count(0)
    expect(page.locator("#redesign-workflow")).to_have_value("wf/4")


@pytest.mark.parametrize("final", ["wf/62", ""])
@pytest.mark.parametrize("initial", [True, False])
@pytest.mark.parametrize("late_status", [200, 503])
def test_current_top_selection_is_applied_when_candidate_fetch_completes(ui, final, initial, late_status):
    page, state, fulfill = ui
    if not initial:
        open_proposals(page)
        page.locator("#redesign-workflow").select_option("wf/7")
        selected(page, 7)
    before = len(reads(state, "/api/proposals"))
    state["hold"] = lambda r: r["path"] == "/api/proposals"
    if initial:
        page.locator('[data-page="proposals"]').click()
        # Candidates are intentionally lazy. Exercise the same pending-list race
        # after explicit disclosure, never require hidden baseline traffic.
        page.locator('#proposal-baseline > summary').click()
    else:
        page.locator("#refresh").click()
    expect(page.locator("#proposal-list")).to_contain_text("Loading proposals")
    page.locator("#redesign-workflow").select_option("wf/3")
    page.locator("#redesign-workflow").select_option(final)
    expect(page.locator("#proposal-detail h2")).to_have_count(0)
    settle(page)
    assert len(state["pending"]) == 1
    route, data, status = state["pending"].pop()
    state["hold"] = None
    fulfill(route, data, late_status)
    if initial and not final and late_status == 503:
        # No selection change, so this is a current failure, not a stale one.
        expect(page.locator("#proposal-detail")).to_contain_text("Current proposal data is unavailable")
        expect(page.locator("#proposal-list .selected")).to_have_count(0)
        return
    expect(page.locator("#proposal-count")).to_contain_text("1 of 1 actual candidates" if final else "65 actual candidates")
    if final:
        selected(page, 62)
    else:
        expect(page.locator("#proposal-list .selected")).to_have_count(0)
        expect(page.locator("#proposal-detail h2")).to_have_count(0)
    settle(page)
    expect(page.locator("#redesign-workflow")).to_have_value(final)
    # A changed filter needs one fresh server read, never an old selected ID.
    changed_filter = final != ("" if initial else "wf/7")
    assert len(reads(state, "/api/proposals")) == before + 1 + int(changed_filter)


@pytest.mark.parametrize("late_status", [200, 503])
def test_old_scenario_response_cannot_override_current_top_or_lower(ui, late_status):
    page, state, fulfill = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/3")
    selected(page, 3)
    state["hold"] = lambda r: r["path"] == "/api/proposals" and r["query"].get("scenario") == ["bronze"]
    page.locator("#scenario").select_option("bronze")
    expect(page.locator("#proposal-list")).to_contain_text("Loading proposals")
    page.locator("#redesign-workflow").select_option("wf/62")
    settle(page)
    assert len(state["pending"]) == 1
    page.locator("#scenario").select_option("original")
    selected(page, 62)
    route, data, _ = state["pending"].pop()
    state["hold"] = None
    fulfill(route, data, late_status)
    settle(page)
    selected(page, 62)


def test_explicit_refresh_retires_pending_same_context_list(ui):
    page, state, fulfill = ui
    open_proposals(page)
    page.locator('#redesign-workflow').select_option('wf/3')
    selected(page, 3)
    state['hold'] = lambda r: r['path'] == '/api/proposals'
    for _ in range(2):
        with page.expect_request('**/api/proposals?*'):
            page.locator('#refresh').click()
        settle(page)
    assert len(state['pending']) == 2
    old, fresh = state['pending']
    state['pending'].clear()
    state['hold'] = None
    fulfill(*fresh)
    selected(page, 3)
    fulfill(old[0], old[1], 503)
    settle(page)
    selected(page, 3)
    expect(page.locator('#proposal-detail')).not_to_contain_text('Current proposal data is unavailable')


@pytest.mark.parametrize("scope_change", ["package", "project"])
def test_old_package_response_is_ignored_even_while_next_context_is_not_ready(ui, scope_change):
    page, state, fulfill = ui
    page.locator('[data-page="projects"]').click()
    expect(page.locator(f'#project-select option[value="{PROJECT}"]')).to_have_count(1)
    page.locator("#project-select").select_option(PROJECT)
    expect(page.locator("#project-package")).to_be_enabled()
    page.locator("#project-package").select_option(PACKAGE_A)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/3")
    selected(page, 3)
    state["hold"] = lambda r: r["path"] == "/api/proposals" or (r["path"] == "/api/overview" and r["headers"].get("x-workbench-package") != PACKAGE_A)
    page.locator("#scenario").select_option("bronze")
    expect(page.locator("#proposal-list")).to_contain_text("Loading proposals")
    settle(page)
    assert len(state["pending"]) == 1
    old_route, old_data, old_status = state["pending"].pop()
    if scope_change == "package":
        page.locator("#estate-package-select").select_option(PACKAGE_B, force=True)
    else:
        page.locator("#project-select").select_option("", force=True)
    expect(page.locator("#package-ready")).not_to_have_text("Package ready")
    fulfill(old_route, old_data, old_status)
    settle(page)
    expect(page.locator("#proposal-detail h2")).to_have_count(0)
    expect(page.locator("#proposal-list .selected")).to_have_count(0)
    expect(page.locator("#redesign-workflow")).to_have_value("")
    # Complete held GETs against the new scope, then verify selection works there.
    pending = list(state["pending"])
    state["pending"].clear()
    state["hold"] = None
    for route, data, status in pending:
        fulfill(route, data, status)
    # A proposal list must remain empty until the next package is explicitly ready.
    expect(page.locator("#proposal-count")).to_contain_text("Not loaded")
    if scope_change == "project":
        page.locator("#project-select").select_option(PROJECT)
        expect(page.locator("#project-package")).to_be_enabled()
        page.locator("#project-package").select_option(PACKAGE_B)
        page.locator('[data-page="proposals"]').click()
    else:
        expect(page.locator("#package-ready")).to_have_text("Package ready")
        page.locator('[data-page="proposals"]').click()
    expect(page.locator("#proposal-count")).to_contain_text("65 actual candidates")
    page.locator("#redesign-workflow").select_option("wf/62")
    selected(page, 62, "bronze")