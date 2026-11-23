"""Real Chromium, actual index markup, isolated arena, no provider or API traffic."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors, unexpected = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css">'
                            '<link rel="stylesheet" href="/static/agent-arena.css">'
                            '<script src="/static/agent-arena.js" defer></script></head>')
    markup = markup.replace('aria-labelledby="agents-heading" hidden', 'aria-labelledby="agents-heading"')

    def route_handler(route):
        path = route.request.url.split(".invalid", 1)[-1]
        if path == "/":
            route.fulfill(content_type="text/html", body=markup, headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'none'; connect-src 'none'"
            })
        elif path in {"/static/app.css", "/static/agent-arena.css", "/static/agent-arena.js"}:
            route.fulfill(path=str(STATIC / path.rsplit("/", 1)[-1]),
                          content_type="text/javascript" if path.endswith(".js") else "text/css")
        else:
            unexpected.append(path)
            route.abort()

    context.route("**/*", route_handler)
    page.goto("http://arena.invalid/")
    expect(page.locator(".agent-arena")).to_be_visible()
    yield page
    assert not errors
    assert not unexpected, f"Arena made unexpected requests: {unexpected}"
    context.close()


def emit(page, record, kind="agent", target="document"):
    page.evaluate("""({record, kind, target}) => {
      (target === 'window' ? window : document).dispatchEvent(
        new CustomEvent(`orion:${kind}-run`, {detail: record}));
    }""", {"record": record, "kind": kind, "target": target})


def fact(page, key):
    return page.locator(f'[data-fact="{key}"]')


def test_model_selection_is_first_and_agent_copy_is_compact(ui):
    model = ui.locator('#workspace-model-panel')
    arena = ui.locator('#agent-arena')
    assert model.evaluate("e => e.compareDocumentPosition(document.querySelector('#agent-arena')) & Node.DOCUMENT_POSITION_FOLLOWING")
    assert model.locator('#agent-endpoint').count() == 1
    assert ui.locator('#agent-form #workspace-model-panel').count() == 0
    assert model.evaluate("e => getComputedStyle(e).display") == 'grid'
    assert float(model.locator('h2').evaluate("e => getComputedStyle(e).fontSize.replace('px','')")) <= 20
    emit(ui, {"id": "r", "status": "completed", "output": {"findings": [
        {"role": "discovery", "status": "supported", "summary": "Captured control flow and threshold evidence."}]}})
    ui.locator('.arena-role-discovery').click()
    paragraph = ui.locator('.arena-finding > p').first
    assert float(paragraph.evaluate("e => getComputedStyle(e).fontSize.replace('px','')")) <= 13
    assert arena.evaluate("e => getComputedStyle(e).fontSize")


def test_historical_repeated_findings_collapse_within_role_not_across_roles(ui):
    finding = {'role': 'artifact', 'status': 'needs_information', 'summary': 'Review binding and format',
               'evidence_ids': ['captured/task'], 'blockers': [], 'proposed_changes': []}
    emit(ui, {'id': '16276644ad254af8b76992cc91ce9edc', 'status': 'completed',
        'output': {'findings': [finding] * 4 + [{**finding, 'role': 'discovery'}],
                   'architecture_knowledge': {'retrievals': [{'role': 'architecture'}], 'citations': []}}})
    ui.locator('.arena-role-artifact').click()
    expect(ui.locator('.arena-finding')).to_have_count(1)
    expect(ui.locator('.arena-findings')).to_contain_text('Identical finding recorded 4 times')
    expect(ui.locator('.arena-findings')).to_contain_text('4 recorded submissions, 1 distinct finding')
    expect(ui.locator('.arena-run-outcome')).to_contain_text('0 applied citations')
    ui.locator('.arena-role-discovery').click()
    expect(ui.locator('.arena-finding')).to_have_count(1)
    expect(ui.locator('.arena-findings')).to_contain_text('1 recorded submission, 1 distinct finding')


def test_role_signal_is_latest_record_not_worst_historical_submission(ui):
    emit(ui, {'id': 'r', 'status': 'completed', 'output': {'findings': [
        {'role': 'semantics', 'status': 'needs_information', 'summary': 'Earlier review'},
        {'role': 'semantics', 'status': 'supported', 'summary': 'Later scoped evidence'}]}})
    expect(ui.locator('.arena-role-semantics')).to_have_attribute('data-state', 'supported')
    ui.locator('.arena-role-semantics').click()
    expect(ui.locator('.arena-findings')).to_contain_text('Latest recorded status: Agent-reported observation')
    expect(ui.locator('.arena-finding')).to_have_count(2)


def test_idle_is_static_and_does_not_invoke_or_persist(ui):
    expect(ui.locator("#arena-status")).to_have_text("No run selected.")
    expect(ui.locator('.arena-role[data-state="unknown"]')).to_have_count(6)
    expect(fact(ui, "tokens")).to_have_text("Unknown")
    expect(fact(ui, "attempts")).to_have_text("Unknown")
    assert ui.locator(".agent-arena").evaluate("e => e.getAnimations({subtree:true}).length") == 0
    assert ui.evaluate("localStorage.length") == 0
    assert ui.evaluate("sessionStorage.length") == 0


@pytest.mark.parametrize("width", [1440, 1100, 800, 390])
def test_role_cards_do_not_overlap_side_panels_or_core(ui, width):
    ui.set_viewport_size({"width": width, "height": 1100})
    assert ui.locator('.arena-role').evaluate_all("""cards => {
        const obstacles = [...document.querySelectorAll('.arena-telemetry,.arena-inspector,.arena-core')];
        return cards.every(card => {
            const a=card.getBoundingClientRect();
            return obstacles.every(other => {
                const b=other.getBoundingClientRect();
                return a.right<=b.left || a.left>=b.right || a.bottom<=b.top || a.top>=b.bottom;
            });
        });
    }""")
    expect(ui.locator('.arena-stack')).to_contain_text('LangSmith · Tracing not enabled')
    expect(ui.locator('.arena-improvement')).to_have_count(0)
    expect(ui.locator('.arena-legend')).to_have_count(0)


def test_separate_loop_events_are_not_rendered_as_agent_activity(ui):
    emit(ui, {"id":"loop-1", "status":"completed", "attempts":[]}, kind="loop")
    expect(ui.locator('#arena-status')).to_contain_text('No run selected')
    expect(ui.locator('.arena-improvement')).to_have_count(0)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_only_animates_outer_run_without_role_progress(ui, status):
    emit(ui, {"id": "run-actual-12345678901234567890", "status": status, "workflow_id": "wf/actual"})
    expect(ui.locator("#arena-status")).to_have_text("Run active. Specialist-level progress is not reported by the runtime.")
    expect(ui.locator('.arena-role[data-state="unknown"]')).to_have_count(6)
    expect(ui.locator('.agent-arena[data-active="true"]')).to_be_visible()
    expect(fact(ui, "id")).to_have_attribute("title", "run-actual-12345678901234567890")
    expect(fact(ui, "workflow")).to_have_text("wf/actual")
    expect(fact(ui, "citations")).to_have_text("Unknown")
    assert ui.locator(".arena-orbit").evaluate("e => getComputedStyle(e).animationName") == "arena-orbit"
    assert all(value == "none" for value in ui.locator(".arena-role").evaluate_all(
        "elements => elements.map(e => getComputedStyle(e).animationName)"))


def test_terminal_findings_are_not_blanket_completed(ui):
    emit(ui, {"id": "r1", "status": "completed", "workflow_name": "wf/actual-name",
        "evidence_references": [
            {"id": "xml:1", "kind": "taskinstance", "label": "s_m_JOB_CHECK_COUNTER", "locator": "/WORKFLOW/TASKINSTANCE[1]"},
            {"id": "xml:2", "kind": "workflow_control", "label": "Start → tmr_WAIT_FOR_DEPENDENCY", "locator": "/WORKFLOW/WORKFLOWLINK[1]"}],
        "output": {"status": "proposal_failed", "design_submission": {"requested": True,
            "status": "not_produced", "accepted_count": 0, "rejections": [{"role": "supervisor",
                "error": "design_schema_validation_failed", "validation_issues": [
                    {"field": "tasks.0.implementation_kind", "issue": "Unsupported task kind."},
                    {"field": "tasks.1.implementation_kind", "issue": "Unsupported task kind."},
                    {"field": "edges.0.evidence_ids", "issue": "At least one retrieved canonical XML evidence ID is required."}]}]},
            "redesigns": [], "usage": {"total_tokens": 58}, "findings": [
        {"role": "supervisor", "status": "needs_information", "summary": "Failed to submit redesign due to schema validation errors.", "evidence_ids": ["xml:1"]},
        {"role": "discovery", "status": "supported", "summary": "Captured structure only", "evidence_ids": ["xml:1", "xml:2"]},
        {"role": "validation", "status": "needs_information", "summary": "Parity unknown", "evidence_ids": ["xml:1"]}
    ]}})
    expect(fact(ui, "tokens")).to_have_text("58")
    expect(fact(ui, "citations")).to_have_text("2")
    expect(fact(ui, "workflow")).to_have_text("wf/actual-name")
    expect(ui.locator(".arena-run-outcome")).to_contain_text("Conversion proposal not produced")
    expect(ui.locator(".arena-run-outcome")).to_contain_text("workflow-proposal format error, not a Unity Catalog table/schema error")
    expect(ui.locator(".arena-run-outcome")).to_contain_text("Task type must be supported by the Lakeflow proposal format. (2 fields)")
    expect(ui.locator(".arena-run-outcome")).to_contain_text("Each dependency must cite at least one retrieved XML control link.")
    expect(ui.locator(".arena-run-outcome details")).not_to_have_attribute("open", "")
    ui.locator(".arena-run-outcome summary").click()
    expect(ui.locator(".arena-run-outcome")).to_contain_text("tasks.0.implementation_kind")
    expect(ui.locator('.arena-role[data-state="unknown"]')).to_have_count(3)
    expect(ui.locator('.arena-role-validation')).to_have_attribute("data-state", "needs_information")
    expect(ui.locator('.arena-role-validation .arena-role-signal')).to_have_text("Incomplete analysis")
    ui.get_by_role("button", name=re.compile("03 Transformation semantics analyst")).focus()
    ui.keyboard.press("Enter")
    expect(ui.locator("#arena-role-heading")).to_have_text("Transformation semantics analyst")
    expect(ui.locator(".arena-findings")).to_contain_text("did not submit a finding")
    ui.locator(".arena-role-discovery").click()
    expect(ui.locator(".arena-findings")).to_contain_text("Captured structure only")
    expect(ui.locator(".arena-findings")).to_contain_text("s_m_JOB_CHECK_COUNTER")
    expect(ui.locator(".arena-findings")).to_contain_text("/WORKFLOW/TASKINSTANCE[1]")
    assert "xml:1" not in ui.locator(".arena-findings").inner_text()
    expect(ui.locator(".arena-findings code")).to_have_count(2)
    assert ui.locator(".agent-arena").evaluate("e => e.getAnimations({subtree:true}).length") == 0


@pytest.mark.parametrize("status", ["failed", "error", "cancelled", "interrupted", "unavailable"])
def test_error_and_other_terminal_records_stop_animation(ui, status):
    emit(ui, {"id": "r", "status": "running"})
    emit(ui, {"id": "r", "status": status})
    expect(ui.locator(".arena-badge")).to_have_text(f"Run / {status}")
    expect(ui.locator('.agent-arena[data-active="false"]')).to_be_visible()
    expect(fact(ui, "tokens")).to_have_text("Unknown")
    assert ui.locator(".agent-arena").evaluate("e => e.getAnimations({subtree:true}).length") == 0


def test_actual_agent_role_completion_receipts_are_visible(ui):
    emit(ui, {"id": "run1", "status": "completed", "output": {"role_completion": [
        {"role": "discovery", "status": "failed", "error_type": "TimeoutError"},
        {"role": "semantics", "status": "incomplete"},
        {"role": "architecture", "status": "completed"}
    ]}})
    expect(fact(ui, "attempts")).to_have_text("1/3 completed")
    expect(ui.locator(".arena-role-discovery .arena-role-signal")).to_have_text("Failed · timed out")
    expect(ui.locator(".arena-role-semantics .arena-role-signal")).to_have_text("Incomplete output")
    expect(ui.locator(".arena-role-architecture .arena-role-signal")).to_have_text("Completed, no finding")


def test_missing_accounting_zero_and_partial_usage_remain_truthful(ui):
    for usage in [{}, {"known_total_tokens": 50, "total_tokens": None}, {"total_tokens": "42"}, {"input_tokens": 4, "output_tokens": 7}]:
        emit(ui, {"id": "r", "status": "completed", "output": {"usage": usage}})
        expect(fact(ui, "tokens")).to_have_text("Unknown")
    emit(ui, {"id": "r", "status": "completed", "output": {"usage_metadata": {"total_tokens": 0}}})
    expect(fact(ui, "tokens")).to_have_text("0")


def test_reduced_motion_and_mobile_layout_both_themes(ui):
    ui.emulate_media(reduced_motion="reduce")
    ui.set_viewport_size({"width": 375, "height": 900})
    emit(ui, {"id": "r", "status": "running", "workflow_id": "w" * 400})
    for theme in ["light", "dark"]:
        ui.locator("html").evaluate("(e, theme) => e.dataset.theme = theme", theme)
        assert ui.locator(".agent-arena").evaluate("e => e.getAnimations({subtree:true}).length") == 0
        assert ui.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(ui.locator(".arena-role-artifact")).to_be_visible()
        assert ui.locator(".arena-role-artifact").bounding_box()["width"] >= 44


def test_text_only_secret_redaction_clear_and_no_repeat_rebuild(ui):
    record = {"id": "r", "status": "completed", "output": {"findings": [{
        "role": "supervisor", "status": "failed", "evidence_ids": [],
        "summary": '<img src=x onerror=alert(1)> token=never-display Bearer private-value'
    }]}}
    emit(ui, record)
    expect(ui.locator(".arena-findings")).to_contain_text("<img src=x")
    expect(ui.locator(".arena-findings")).not_to_contain_text("never-display")
    expect(ui.locator(".arena-findings img")).to_have_count(0)
    ui.locator(".arena-findings").evaluate("e => { window.arenaChanges = 0; new MutationObserver(() => window.arenaChanges++).observe(e, {childList:true, subtree:true}); }")
    emit(ui, record)
    assert ui.evaluate("window.arenaChanges") == 0
    emit(ui, None)
    expect(ui.locator("#arena-status")).to_contain_text("No run selected")
    emit(ui, {"id": "r2", "status": "running"})
    ui.locator("#agent-workflow").dispatch_event("change")
    expect(ui.locator("#arena-status")).to_contain_text("No run selected")


def test_index_inclusion_and_observer_only_source():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count('src="/static/agent-arena.js"') == 1
    assert html.count('href="/static/agent-arena.css"') == 1
    assert re.search(r'id="page-agents"[\s\S]*?id="agent-availability"[\s\S]*?id="agent-results-heading"[\s\S]*?id="agent-arena"', html)
    script = (STATIC / "agent-arena.js").read_text(encoding="utf-8")
    for forbidden in ["innerHTML", "localStorage", "sessionStorage", "fetch(", "setInterval(", ".style."]:
        assert forbidden not in script