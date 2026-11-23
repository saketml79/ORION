"""Presentation contracts and isolated Chromium DOM tests, not live API tests.

No HTTP response interception, server mutations, model calls, or parent browser
fixtures. Renderers execute their actual source against explicitly supplied unit
inputs. These inputs are test cases, not service verification evidence.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])


def test_existing_targets_safe_dom_and_operation_accessibility():
    parsed = Ids()
    parsed.feed(HTML)
    assert len(parsed.ids) == len(set(parsed.ids))
    assert set(re.findall(r"\$\('([^']+)'\)", JS)) <= set(parsed.ids)
    assert 'id="operation-banner"' in HTML
    assert 'aria-atomic="true" hidden' in HTML
    for unsafe in ["innerHTML", "outerHTML", "insertAdjacentHTML", "eval("]:
        assert unsafe not in JS
    assert "Object.freeze({" in JS and "Object.hasOwn(iconPaths, name)" in JS
    assert "'stroke-width': '1.8'" in JS
    assert "state.requests += 1; updateActivity()" in JS
    assert "state.requests -= 1; updateActivity()" in JS
    assert "requestActivity.delete(controller)" in JS
    assert "prefers-reduced-motion: reduce" in CSS
    assert "animation: none !important" in CSS
    for text in ["Download package analysis report (Markdown)", "Save nonsecret settings"]:
        assert text in HTML


def test_agent_invocation_waits_for_confirmed_nonrunning_status():
    assert "state.agentRunning || !state.agentRunsLoaded" in JS
    assert "state.agentRunsLoaded = false;" in JS
    assert "state.agentRunning = array(data.items).some((run) => ['queued', 'running'].includes(run.status))" in JS
    assert "Agent invocation stays disabled until active-run status is confirmed." in JS
    assert "Waiting for persisted run status to be confirmed." in JS


def source_between(start, end):
    return JS[JS.index(start):JS.index(end, JS.index(start))]


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def dom(chromium):
    context = chromium.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # No app initialization, polling, external fonts, or requests in unit tests.
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>", "", HTML, flags=re.S)
    markup = re.sub(r'<img\b[^>]*src="/static/[^>]*>', "", markup)
    page.set_content(markup)
    page.add_style_tag(content=CSS)
    # Keep the source utilities/renderers, but not app-level const state that the
    # isolated harness supplies explicitly below. Duplicating that binding makes
    # the injected script fail before window.ui is installed.
    helpers = "\n".join([
      source_between("  const array =", "  const state = {"),
      source_between("  const iconPaths = Object.freeze({", "  function icon(name)"),
      source_between("  function icon(name)", "  function contextHeaders()"),
    ])
    renderers = "\n".join([
        source_between("  function viewLogs(", "  function requestDescription("),
        source_between("  function selectedCompletedPackage()", "  function syncPackagePicker()"),
        source_between("  function updateActivity()", "  async function request("),
        source_between("  function updateControls()", "  function invalidatePackage()"),
        source_between("  function renderProjectJobs()", "  async function loadProject("),
        source_between("  function renderOverview(data)", "  async function ingest("),
    ])
    page.add_script_tag(content="""(() => {
      const $ = (id) => document.getElementById(id);
      const all = (selector, root = document) => [...root.querySelectorAll(selector)];
      const state = { page: 'estate', ready: false, projectId: '', memberHash: '',
        project: null, projectJobs: [], projectSubmitting: false, projectTimer: null,
        jobBusy: false, jobId: null, requests: 0, epoch: 0, overview: null,
        overviewLoading: false, overviewError: '', settingsLoaded: false,
        loadingDiagnostic: null, logsOffset: 0, logsTicket: 0, logsTimer: null };
      const requestActivity = new Map(), recordedRunStates = new Map();
      let legacyJobStatus = '';
      let initializingNavigation = true;
      const pageNames = Object.fromEntries([...document.querySelectorAll('[data-page]')]
        .map(button => [button.dataset.page, button.dataset.page]));
      const navigationPrerequisite = () => '';
      const packagePicker = document.createElement('select');
      document.body.append(packagePicker);
      const packagePickerHint = document.createElement('p');
      const packageCard = document.querySelector('.hero-aside');
    """ + source_between("  const overviewProgress =", "  document.querySelectorAll('a[href=") + """
      const selectedObjectAssessment = () => null;
      const renderGraph = () => {};
    """ + helpers + renderers + """
      window.ui = { icon, highlightedText, paragraph, rows, list, semanticStatus,
        message, renderOverview, renderProjectJobs, updateActivity, requestActivity,
        recordedRunStates, state };
    })();""")
    assert not errors, errors
    yield page
    context.close()
    assert not errors, errors


@pytest.mark.parametrize("content, expected", [
    ("Contains 12345 fields, 1 workflow and 2,345 objects.", ["12,345", "1", "2,345"]),
    ("0 diagnostics, 900 nodes and 42 tokens.", ["0", "900", "42"]),
    ("id_123 fields; 2026-10-28 workflows; v1.2 workflows; 1.2 mappings", []),
    ("0123456789012345 fields; abcdef1234567890 fields", []),
    ('`123 fields` and "456 nodes" and \'789 mappings\'', []),
    ("https://example.test/123 fields; /data/456 nodes; wf:789 mappings", []),
    ('{"fields": "123 fields"}', []),
    ("SELECT 123 fields FROM source", []),
    ("9007199254740992 fields", []),
])
def test_narrative_emphasis_is_controlled_and_preserves_identifiers(dom, content, expected):
    actual = dom.evaluate("""content => {
      const p = document.createElement('p');
      ui.highlightedText(p, content);
      return { text: p.textContent, stats: [...p.querySelectorAll('strong')].map(n => n.textContent) };
    }""", content)
    assert actual["stats"] == expected
    if not expected:
        assert actual["text"] == content


def test_untrusted_markup_and_code_rows_remain_literal(dom):
    value = '<img src=x onerror="window.compromised=true"> 1200 fields'
    result = dom.evaluate("""value => {
      const host = document.createElement('div');
      ui.paragraph(host, value);
      ui.rows(host, { sql: 'SELECT 1200 fields', id: 'abc_1200', json: '{"n":1200}' });
      ui.list(host, ['1200 fields', value]);
      document.body.append(host);
      return { images: host.querySelectorAll('img').length,
        codeEmphasis: host.querySelectorAll('pre strong, li strong').length,
        code: [...host.querySelectorAll('pre')].map(n => n.textContent),
        prose: host.querySelector('p').textContent,
        stats: [...host.querySelectorAll('p strong')].map(n => n.textContent),
        compromised: Boolean(window.compromised) };
    }""", value)
    assert result["images"] == result["codeEmphasis"] == 0
    assert result["code"] == ['SELECT 1200 fields', 'abc_1200', '{"n":1200}']
    assert result["prose"] == value.replace("1200", "1,200")
    assert result["stats"] == ["1,200"]
    assert not result["compromised"]


@pytest.mark.parametrize("status, tone, ongoing", [
    ("queued", "pending", True), ("running", "busy", True),
    ("completed", "good", False), ("failed", "failed", False),
    ("partial", "caution", False), ("skipped", "neutral", False),
    ("rejected", "failed", False), ("draft", "neutral", False),
    ("unavailable", "neutral", False), ("<img src=x>", "neutral", False),
])
def test_status_icons_labels_and_motion_are_truthful(dom, status, tone, ongoing):
    result = dom.evaluate("""status => {
      const badge = ui.semanticStatus(status);
      return { label: badge.textContent, tone: badge.classList.contains('""" + tone + """'),
        ongoing: badge.classList.contains('is-ongoing'),
        svg: badge.querySelectorAll('svg[aria-hidden="true"] path').length,
        images: badge.querySelectorAll('img').length };
    }""", status)
    assert result == {"label": status, "tone": True, "ongoing": ongoing, "svg": 1, "images": 0}


def test_icon_names_cannot_inject_svg_paths(dom):
    result = dom.evaluate("""() => {
      const unknown = ui.icon('<script>alert(1)</script>');
      return { path: unknown.querySelector('path').getAttribute('d'),
        fallback: ui.icon('info').querySelector('path').getAttribute('d'),
        scripts: unknown.querySelectorAll('script').length,
        width: unknown.getAttribute('stroke-width') };
    }""")
    assert result["path"] == result["fallback"]
    assert result["scripts"] == 0 and result["width"] == "1.8"


def test_inventory_uses_structured_counts_full_narrative_and_unknown_dimensions(dom):
    narrative = ("The complete export contains 12345 XML elements. "
                 "Definitions, instances, field flows, SQL/property roles, schedules and original conditional control links are retained, including disabled instances. "
                 "This is structural understanding, not verified transformation semantic translation.")
    dom.evaluate("""summary => ui.renderOverview({ready: true,
      package: {name: 'unit-input.xml'}, summary,
      counts: {repository: 1, folder: 2, workflow: 1, mapping: 1234, connectionreference: 2},
      coverage: {xml_elements: 12345, lifecycle_dimensions: {lifecycle: 'unknown', activity: 'unknown'}},
      diagnostics_count: 7, capabilities: {read: true, deploy: false}, agent: {available: false}
    })""", narrative)
    expect(dom.locator("#overview-summary")).to_contain_text("Package inventory")
    assert dom.locator(".inventory-total strong").inner_text() == "12,345"
    assert dom.locator("#overview-summary .service-narrative p").text_content() == narrative
    assert dom.locator(".inventory-fact").filter(has_text="Repository").count() == 1
    expect(dom.locator("#metrics")).to_contain_text("Connection references")
    expect(dom.locator("#metrics")).to_contain_text("Exported definitions")
    assert dom.locator(".feature-bullets li").count() == 3
    expect(dom.locator(".coverage-boundary")).to_contain_text("Structural inspection only")
    expect(dom.locator(".diagnostic-summary strong")).to_have_text("7")
    assert dom.locator(".lifecycle-row dd").all_text_contents() == ["Not provided in export", "Not provided in export"]
    expect(dom.locator(".lifecycle-details")).not_to_have_attribute("open", "")
    expect(dom.locator(".lifecycle-row").first).to_be_hidden()
    expect(dom.locator(".lifecycle-card")).to_contain_text("does not mean the workflows are broken or inactive")
    dom.locator(".lifecycle-details summary").click()
    expect(dom.locator(".lifecycle-row").first).to_be_visible()
    assert dom.locator(".lifecycle-unknown dd").first.evaluate("node => getComputedStyle(node).fontWeight") == "400"
    evidence = dom.locator("#overview-details").text_content()
    assert '"lifecycle": "unknown"' in evidence and '"activity": "unknown"' in evidence
    expect(dom.locator(".capability-unavailable")).to_contain_text("Unavailable")
    expect(dom.locator("#agent-submit")).to_be_disabled()
    expect(dom.locator("#agent-availability")).to_contain_text("unavailable")


def test_not_ready_does_not_reuse_or_invent_inventory_counts(dom):
    dom.evaluate("ui.renderOverview({ready:false, status:'loading'})")
    assert dom.locator(".inventory-fact").count() == 0
    expect(dom.locator("#coverage button")).to_be_disabled()
    expect(dom.locator("#overview-summary")).to_contain_text("Package is not ready")
    expect(dom.locator("#coverage")).to_contain_text("Not supplied")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_operational_values_visible_unknowns_neutral_and_raw_evidence_preserved(dom, theme):
    dom.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    dom.evaluate("""() => ui.renderOverview({ready: true, package: {name: 'operational.xml'},
      coverage: {lifecycle_dimensions: {lifecycle: 'active', activity: 'unknown',
        ownership: 'Operations team', execution: null, deprecation: ''}},
      capabilities: {}, agent: {available: false}})""")
    expect(dom.locator(".lifecycle-provided").first).to_be_visible()
    assert dom.locator(".lifecycle-provided dd").all_text_contents() == ["active", "Operations team"]
    expect(dom.locator(".lifecycle-unknown").first).to_be_hidden()
    dom.locator(".lifecycle-details summary").click()
    assert dom.locator(".lifecycle-unknown dd").first.evaluate("""node => {
      const comparison = document.createElement('span'); comparison.style.color = 'var(--muted)';
      node.append(comparison); const same = getComputedStyle(node).color === getComputedStyle(comparison).color;
      comparison.remove(); return same;
    }""")
    import json
    dimensions = json.loads(dom.locator("#overview-details").text_content())["coverage"]["lifecycle_dimensions"]
    assert dimensions == {"lifecycle": "active", "activity": "unknown", "ownership": "Operations team",
                          "execution": None, "deprecation": ""}


def test_job_progress_uses_recorded_counts_and_per_file_statuses(dom):
    dom.evaluate("""() => {
  document.getElementById('page-estate').hidden=true;
  document.getElementById('page-projects').hidden=false;
      ui.state.projectJobs = [{id:'123456789012abcdefghijkl', status:'partial',
        created_at:'2026-10-28T10:00:00Z', processed:2, total:3,
        items:[{name:'a.xml',status:'completed'}, {name:'b.xml',status:'failed',error:'Parse rejected'}]}];
      ui.renderProjectJobs();
    }""")
    progress = dom.locator("#project-jobs progress")
    expect(progress).to_have_attribute("max", "3")
    expect(progress).to_have_attribute("value", "2")
    expect(progress).to_have_attribute("aria-label", "2 of 3 files processed")
    assert dom.locator(".file-task .semantic-status").all_text_contents() == ["completed", "failed"]
    expect(dom.locator("#project-jobs .record-title")).to_have_attribute("title", "123456789012abcdefghijkl")
    expect(dom.locator("#project-jobs button", has_text="View logs")).to_be_visible()
    assert "%" not in dom.locator("#project-jobs").inner_text()
    dom.evaluate("ui.state.projectJobs = [{id:'unknown',status:'running'}]; ui.renderProjectJobs()")
    assert dom.locator("#project-jobs progress").count() == 0
    assert dom.locator("#project-jobs .job-progress-label").count() == 0


def test_operations_strip_ignores_background_log_polling_and_unknown_run_state(dom):
    dom.evaluate("""() => {
      ui.state.requests = 1;
      ui.requestActivity.set('logs', {epoch:0, label:'Refreshing logs', background:true});
      ui.updateActivity();
    }""")
    expect(dom.locator("#operation-banner")).to_be_hidden()
    expect(dom.locator("#activity-status")).to_have_text("Refreshing logs")
    assert "is-active" not in dom.locator("#activity-status").get_attribute("class")
    dom.evaluate("""() => {
      ui.requestActivity.set('inventory', {epoch:0, label:'Refreshing package inventory', background:false});
      ui.state.requests = 2; ui.updateActivity();
    }""")
    expect(dom.locator("#operation-banner")).to_be_visible()
    expect(dom.locator("#operation-banner")).to_contain_text("Refreshing package inventory")
    dom.evaluate("""() => {
      ui.requestActivity.clear(); ui.state.requests=0;
      ui.recordedRunStates.set('unknown', {epoch:0, status:'unavailable'});
      ui.updateActivity();
    }""")
    expect(dom.locator("#operation-banner")).to_be_hidden()
    expect(dom.locator("#activity-status")).to_have_text("Idle")
    dom.evaluate("ui.recordedRunStates.set('actual', {epoch:0,status:'running'}); ui.updateActivity()")
    expect(dom.locator("#operation-banner")).to_be_visible()
    expect(dom.locator("#activity-status")).to_have_text("1 active job")
    dom.evaluate("ui.state.epoch=1; ui.updateActivity()")
    expect(dom.locator("#operation-banner")).to_be_hidden()


def test_reduced_motion_and_mobile_layout(dom):
    dom.evaluate("""() => {
      ui.state.projectJobs=[{id:'actual-job',status:'running',processed:1,total:10,
        items:[{name:'a-long-package-name-for-a-small-screen.xml',status:'running'}]}];
      ui.renderProjectJobs(); ui.updateActivity();
      document.querySelectorAll('.page').forEach(n => n.hidden=true);
      document.getElementById('page-projects').hidden=false;
    }""")
    dom.emulate_media(reduced_motion="reduce")
    animations = dom.locator('.semantic-status[data-status="running"] > svg').evaluate_all(
        "nodes => nodes.map(n => getComputedStyle(n).animationName)"
    )
    assert animations and all(name == "none" for name in animations)
    dom.set_viewport_size({"width": 390, "height": 844})
    assert dom.evaluate("document.documentElement.scrollWidth <= innerWidth")