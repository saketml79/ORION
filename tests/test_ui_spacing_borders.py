"""Real Chromium layout contracts, intercepted services only, no live backend."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "workbench" / "static"
PAGES = ("projects", "estate", "explorer", "proposals", "evidence", "agents",
         "release", "logs", "evolution")
DECORATIVE = (
    ".panel", ".hero", ".hero-aside", ".metric", ".notice", ".disclosure",
    ".record", ".job-card", ".event-card", ".document-card", ".review-card",
    ".capability", ".inventory-fact", ".context-picker", ".context-roller",
    ".context-option", ".endpoint-selection-summary", ".loop-journey", ".loop-stage",
    ".agent-arena", ".arena-badge", ".arena-telemetry", ".arena-inspector",
    ".arena-role", ".arena-core", ".arena-stack", ".arena-stack span",
    ".arena-improvement", ".arena-verdicts li", ".arena-finding", ".arena-legend",
)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context(offline=True, service_workers="block")
    page = context.new_page()
    state = {"errors": [], "unexpected": [], "requests": [], "trace_events": [],
             "tracing_status": 404, "tracing_body": "<html>Not Found</html>"}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>",
                    "", markup, flags=re.S)

    def route_handler(route):
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html", body=markup)
        elif path.startswith("/static/") and (STATIC / path.rsplit("/", 1)[-1]).is_file():
            asset = STATIC / path.rsplit("/", 1)[-1]
            route.fulfill(content_type="text/css" if asset.suffix == ".css" else "text/javascript",
                          path=str(asset))
        elif path in ("/api/overview", "/api/loops", "/api/redesigns", "/api/tracing/status"):
            state["requests"].append((path, route.request.method))
            if path == "/api/tracing/status":
                route.fulfill(status=state["tracing_status"], body=state["tracing_body"],
                              content_type="text/html" if state["tracing_status"] == 404 else "application/json")
                return
            data = {"ready": True, "agent": {"available": False}} if path == "/api/overview" else {
                "definitions": [], "runs": [], "events": [], "lessons": [], "items": [],
                "total": 0, "current_auth_status": {"inference_ready": False}}
            route.fulfill(content_type="application/json", body=json.dumps(data))
        else:
            state["unexpected"].append(route.request.url)
            route.abort()

    context.route("**/*", route_handler)
    page.goto("http://spacing.invalid/")
    page.add_style_tag(url="/static/app.css")
    page.add_style_tag(url="/static/agent-arena.css")
    page.evaluate("""() => {
      for (const nav of document.querySelectorAll('[data-page]')) nav.addEventListener('click', () => {
        for (const section of document.querySelectorAll('main > .page'))
          section.hidden = section.id !== `page-${nav.dataset.page}`;
      });
      const workflow = document.getElementById('agent-workflow');
      workflow.append(new Option('Captured workflow', 'wf/one')); workflow.value = 'wf/one';
      document.getElementById('metrics').innerHTML = '<div class="metric">Captured count</div>';
      document.getElementById('runs-list').innerHTML = '<article class="record job-card">Recorded run</article>';
      document.getElementById('logs-list').innerHTML = '<article class="record event-card" data-severity="error">Recorded event</article>';
      document.getElementById('documents-list').innerHTML = '<article class="record document-card">Document</article><article class="record review-card">Review</article>';
      document.getElementById('capabilities').innerHTML = '<div class="capability capability-available">Available</div><div class="capability capability-unavailable">Unavailable</div>';
      document.getElementById('overview-summary').innerHTML = '<div class="inventory-fact">Count</div>';
      document.getElementById('workspace-model-panel').insertAdjacentHTML('beforeend', '<div class="endpoint-selection-summary">Endpoint</div>');
      document.getElementById('run-detail').innerHTML = '<div class="loop-journey"><div class="loop-stage">Recorded stage</div></div>';
      document.getElementById('graph-area').innerHTML = '<div class="graph-scroll"><svg class="graph-svg"><path class="edge" d="M0 0L40 40"/><g class="node status-green" tabindex="0"><rect width="20" height="20"/></g></svg></div>';
      window.traceEvents = [];
      document.addEventListener('orion:tracing-status', e => window.traceEvents.push(e.detail));
    }""")
    for script in ("context-picker.js", "agent-arena.js", "tracing-status.js",
                   "agent-loops.js", "redesign-review.js"):
        page.add_script_tag(url=f"/static/{script}")
        page.evaluate("""() => document.dispatchEvent(new CustomEvent('orion:loop-run', {detail: {
            id: 'recorded-loop', status: 'completed', workflow_id: 'wf/one', attempts: [{number: 1,
                status: 'completed', rubric: {status: 'advisory', checks: [{passed: true}]},
                findings: [{role: 'supervisor', status: 'needs_information', summary: 'Recorded gap', evidence_ids: ['wf/one']}]
            }]
        }}))""")
    yield page, state
    assert not state["errors"], state["errors"]
    assert not state["unexpected"], state["unexpected"]
    assert all(method == "GET" for _, method in state["requests"])
    context.close()


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("name", PAGES)
def test_adjacent_top_sections_have_consistent_gaps(ui, name, theme, width):
    page, _ = ui
    page.set_viewport_size({"width": width, "height": 1100})
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    page.locator(f'[data-page="{name}"]').click()
    if name == "agents":
        expect(page.locator("#langsmith-panel [role=status]")).to_contain_text("endpoint unavailable")
        expect(page.locator("#agent-loops")).to_be_visible()
        expect(page.locator("#agent-arena .agent-arena")).to_be_visible()
    if name == "proposals":
        expect(page.locator("#redesign-review")).to_be_visible()
    pairs = page.locator(f"#page-{name}").evaluate("""section => {
      const children = [...section.children].filter(e => e.getClientRects().length && !e.hidden);
      return children.slice(1).map((e, i) => ({before: children[i].id || children[i].className,
        after: e.id || e.className, gap: e.getBoundingClientRect().top - children[i].getBoundingClientRect().bottom}));
    }""")
    assert pairs
    assert all(24 <= pair["gap"] <= 24.5 for pair in pairs), pairs
    if name == "agents":
        assert page.locator("#langsmith-panel").evaluate("e => getComputedStyle(e).marginBottom") == "24px"
    # Also reveal the optional graph between existing siblings.
    if name == "estate":
        page.locator("#package-graph-panel").evaluate("e => e.hidden = false")
        assert page.locator("#package-graph-panel").evaluate("""e =>
          e.getBoundingClientRect().top - e.previousElementSibling.getBoundingClientRect().bottom""") >= 24


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_decorative_boxes_borderless_controls_and_semantic_edges_retained(ui, theme):
    page, _ = ui
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    for selector in DECORATIVE:
        locator = page.locator(selector)
        assert locator.count(), selector
        borders = locator.evaluate_all("""nodes => nodes.map(e => {
          const s = getComputedStyle(e);
          return [s.borderTopWidth, s.borderRightWidth, s.borderBottomWidth, s.borderLeftWidth];
        })""")
        assert all(width == "0px" for border in borders for width in border), (selector, borders)
    for selector in ("#project-name", "#agent-prompt", "#scenario", "#refresh-runs"):
        assert page.locator(selector).evaluate("e => getComputedStyle(e).borderTopWidth") == "1px"
    assert page.locator(".graph-svg .edge").evaluate("e => getComputedStyle(e).strokeWidth") == "1.25px"
    assert page.locator(".graph-svg .node rect").evaluate("e => getComputedStyle(e).stroke") != "none"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_keyboard_focus_visible_survives_on_arena_roller_and_input(ui, theme):
    page, _ = ui
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    for name, selector in (("agents", ".arena-role"), ("projects", "#project-select-roller"),
                           ("projects", "#project-name")):
        page.locator(f'[data-page="{name}"]').click()
        page.keyboard.press("Tab")
        target = page.locator(selector).first
        target.focus()
        assert target.evaluate("e => e.matches(':focus-visible')")
        assert target.evaluate("e => getComputedStyle(e).outlineStyle") == "solid"
        assert target.evaluate("e => getComputedStyle(e).outlineWidth") == "3px"


def test_tracing_html_404_unknown_and_refresh_recovers_real_configuration(ui):
    page, state = ui
    page.locator('[data-page="agents"]').click()
    status = page.locator("#langsmith-panel [role=status]")
    expect(status).to_contain_text("configuration endpoint unavailable (HTTP 404)")
    expect(status).to_contain_text("Tracing state is unknown, not confirmed off")
    expect(status).not_to_contain_text("Server opt-in is off")
    assert page.evaluate("window.traceEvents") == [{"state": "unknown", "reason": "configuration_endpoint_unavailable"}]
    expect(page.locator("#arena-langsmith-status")).to_contain_text("unknown")
    expect(page.locator("#arena-langsmith-status")).not_to_contain_text("not enabled")
    button = page.locator("#langsmith-panel button")
    expect(button).to_be_enabled()
    state.update(tracing_status=200, tracing_body=json.dumps({"state": "configured", "reason": "not_probed"}))
    button.click()
    expect(status).to_contain_text("Tracing: configured")
    expect(status).to_contain_text("no live delivery has been verified")
    assert page.evaluate("window.traceEvents.at(-1)") == {"state": "configured", "reason": "not_probed"}
    state["tracing_body"] = json.dumps({"state": "off", "reason": "not_opted_in"})
    button.click()
    expect(status).to_contain_text("Server opt-in is off")
    expect(button).to_be_enabled()