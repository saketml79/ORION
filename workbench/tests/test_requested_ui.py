"""Scoped UI contracts and isolated static DOM regression, no backend/model calls."""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
PICKER = (STATIC / "context-picker.js").read_text(encoding="utf-8")
GUIDE = (STATIC / "workflow-guide.js").read_text(encoding="utf-8")
REVIEW = (STATIC / "redesign-review.js").read_text(encoding="utf-8")
PICKERS = (
    "project-select", "project-package", "estate-package-select",
    "document-workflow", "attestation-workflow", "agent-workflow",
)


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags():
    parser = Elements()
    parser.feed(HTML)
    return parser.tags


def test_native_select_ids_and_workflow_contracts_are_preserved():
    controls = {attrs["id"]: (tag, attrs) for tag, attrs in tags() if "id" in attrs}
    for identifier in PICKERS:
        assert repr(identifier) in PICKER
        if identifier == "estate-package-select":
            continue  # Existing application creates this native select dynamically.
        tag, attrs = controls[identifier]
        assert tag == "select" and "multiple" not in attrs
    assert controls["document-workflow"][1]["data-optional"] == "true"
    for identifier in ("attestation-workflow", "agent-workflow"):
        assert "required" in controls[identifier][1]
    assert "select.selectedIndex = index" in PICKER
    assert "new Event('change', { bubbles: true })" in PICKER
    assert "option.title ||" not in PICKER
    assert "innerHTML" not in PICKER


def test_default_prompt_is_textarea_content_not_a_placeholder():
    match = re.search(r'<textarea\b([^>]*\bid="agent-prompt"[^>]*)>(.*?)</textarea>', HTML, re.S)
    assert match and "placeholder=" not in match[1]
    prompt = match[2]
    for phrase in (
        "understand and convert the selected Informatica workflow", "Unity Catalog",
        "source is not the bronze destination layer", "bronze, silver and gold",
        "inspect every SQL phase, expression, transformation, embedded Java",
        "historical connection aliases do not block UC-bound inputs",
        "Lakeflow Job", "actual PySpark notebook files", "embedded Databricks SQL",
        "Recommend the necessary cohort and explain each inclusion",
        "use verified source-table bindings and population receipts when available",
        "without assuming empty fixtures contain data",
        "using captured calls and shared-data evidence, not names alone",
        "Respect the selected review scope; request explicit scope expansion",
        "rather than silently treating outside workflows as converted",
        "A nonblocking child launch is not child completion",
        "Translate supported SQL into Databricks SQL",
        "Distinguish static checks from runtime or business parity",
        "no SQL equivalent is required for a timer, email or child-launch task",
        "Explicitly report any unsupported task and its impact on native Job completeness",
        "Do not deploy or run the converted Job automatically",
    ):
        assert phrase.lower() in prompt.lower()
    assert re.findall(r"^([^:\n]+):", prompt, re.M) == [
        "Supervisor", "Discovery", "Semantics", "Architecture", "Validation",
        "Artifact specialist and separate code writer",
    ]
    assert len(prompt) < 12000


def test_endpoint_hooks_precede_workflow_and_do_not_authorize_runs():
    controls = {attrs["id"]: (tag, attrs) for tag, attrs in tags() if "id" in attrs}
    for identifier in (
        "workspace-model-panel", "connect-workspace-models", "agent-endpoint",
        "workspace-model-status", "endpoint-tool-support", "workspace-model-controls",
    ):
        assert identifier in controls
    assert HTML.index('id="workspace-model-panel"') < HTML.index('id="agent-arena"')
    assert HTML.index('id="workspace-model-panel"') < HTML.index('id="agent-form"') < HTML.index('id="agent-workflow"')
    assert controls["connect-workspace-models"][1]["type"] == "button"
    assert controls["agent-endpoint"][0] == "select"
    assert "disabled" in controls["agent-endpoint"][1]
    assert controls["workspace-model-status"][1]["role"] == "status"
    assert controls["endpoint-tool-support"][1]["type"] == "checkbox"
    assert "checked" not in controls["endpoint-tool-support"][1]
    assert "I checked chat and tool-call support. This declaration is not a live test." in HTML
    assert '<h2 id="workspace-model-heading">Select agentic model endpoint</h2>' in HTML
    assert "Metadata only, no package loading or inference." in HTML
    assert '<h1 id="agents-heading">Agent activity</h1>' in HTML
    assert "Invoke runs the specialists, saves the design, and drafts candidate PySpark/SQL notebooks for supported tasks." in HTML
    assert "Deployment is a separate action." in HTML
    assert '<h2>Choose workflow and scope</h2>' in HTML
    assert '<h2 id="agent-assessment-heading">Run agents and generate code</h2>' in HTML
    assert '<h2 id="agent-results-heading">Agent run and generated draft</h2>' in HTML
    assert '<h2>Assessment history</h2>' in HTML
    assert 'data-page="agents"><span aria-hidden="true">✳</span>Agent activity</button>' in HTML
    assert all(f'STEP {number} ·' in HTML for number in range(1, 5))
    assert "disabled" in controls["agent-submit"][1]
    assert 'id="agent-consent"' not in HTML
    assert 'value="bronze" selected' in HTML


def test_terminology_branding_and_future_stages_are_honest():
    assert "Legacy Data Integration Artifacts" in HTML
    for old in ("Local XML files", "XML exports", "Upload package XML"):
        assert old not in HTML
    assert "Informatica packages (.xml)" in HTML
    assert 'class="databricks-badge" aria-label="Databricks modernization target">' in HTML
    assert 'src="/static/databricks-official.svg"' in HTML
    assert "Human review & deployment" in GUIDE
    assert "Approve & implement using Databricks Lakeflow" in GUIDE
    assert "not implemented in the current local app" in GUIDE
    assert "Future stage:" in GUIDE


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
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    # Actual static UI, without app initialization, external assets, or polling.
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", HTML, flags=re.S)
    page.set_content(markup)
    page.add_style_tag(content=CSS)
    page.evaluate("""ids => {
      document.querySelectorAll('.page').forEach(section => { section.hidden = false; });
      const estate = document.createElement('select'); estate.id = 'estate-package-select';
      const label = document.createElement('label'); label.htmlFor = estate.id;
      label.textContent = 'Current Informatica package';
      document.querySelector('.hero-aside').append(label, estate);
      for (const id of ids) {
        const select = document.getElementById(id); select.disabled = false;
        select.replaceChildren();
        for (const [index, name] of ['Choose context', 'Alpha workflow', 'Beta workflow', 'Gamma workflow'].entries()) {
          const option = new Option(name, index ? `object-${index}` : '');
          option.title = JSON.stringify({scope: 'UNBOUNDED_SCOPE'.repeat(10000)});
          select.append(option);
        }
        select.selectedIndex = 2;
      }
    }""", list(PICKERS))
    page.add_script_tag(content=PICKER)
    yield page
    context.close()
    assert not errors, errors
    assert not requests, requests


@pytest.mark.parametrize("identifier", PICKERS)
def test_roller_geometry_accessible_labels_and_bounded_tooltips(dom, identifier):
    roller = dom.locator(f"#{identifier}-roller")
    roller.scroll_into_view_if_needed()
    expect(roller).to_have_attribute("role", "listbox")
    expect(roller).to_have_attribute("aria-labelledby", f"{identifier}-label")
    assert roller.evaluate("node => node.getBoundingClientRect().height") == 140
    selected = roller.locator('[aria-selected="true"]')
    expect(selected).to_have_text("Beta workflow✓")
    geometry = selected.evaluate("""row => {
      const bounds = row.getBoundingClientRect(), parent = row.parentElement.getBoundingClientRect();
      const style = getComputedStyle(row);
      return {delta: Math.abs(bounds.y + bounds.height / 2 - parent.y - parent.height / 2),
        font: style.fontSize, weight: style.fontWeight, opacity: style.opacity};
    }""")
    assert geometry["delta"] <= 2
    assert geometry["font"] == "14px" and geometry["weight"] == "700"
    assert geometry["opacity"] == "1"
    for position in ("above", "below"):
        row = roller.locator(f'[data-position="{position}"]').first
        assert row.evaluate("node => getComputedStyle(node).fontSize") == "12px"
        assert float(row.evaluate("node => getComputedStyle(node).opacity")) < 1
    for title in roller.locator('[role="option"]').evaluate_all("rows => rows.map(row => row.title)"):
        assert len(title) <= 203 and "UNBOUNDED_SCOPE" not in title
    assert selected.get_attribute("title") == "Beta workflow | object-2"
    assert dom.locator(f'#{identifier}').evaluate("select => select.labels[0].htmlFor") == identifier


def test_keyboard_click_native_change_and_programmatic_selection(dom):
    roller = dom.locator("#agent-workflow-roller")
    roller.focus()
    for key, expected in (("Home", "object-1"), ("ArrowDown", "object-2"), ("End", "object-3"), ("ArrowUp", "object-2")):
        roller.press(key)
        expect(dom.locator("#agent-workflow")).to_have_value(expected)
    roller.press("a")
    expect(dom.locator("#agent-workflow")).to_have_value("object-1")
    roller.locator('[role="option"]').filter(has_text="Beta workflow").click()
    expect(dom.locator("#agent-workflow")).to_have_value("object-2")
    # The hidden select API remains available. Do not selectOption a clipped UI.
    dom.evaluate("""() => {
      const select = document.getElementById('agent-workflow');
      select.value = 'object-3'; select.dispatchEvent(new Event('change', {bubbles:true}));
    }""")
    expect(roller.locator('[aria-selected="true"]')).to_contain_text("Gamma workflow")


def test_disabled_options_dynamic_options_and_optional_empty_choice(dom):
    roller = dom.locator("#document-workflow-roller")
    dom.evaluate("""() => {
      const select = document.getElementById('document-workflow');
      select.options[1].disabled = true;
      select.append(new Option('Delta workflow', 'object-4'));
    }""")
    expect(roller.locator('[role="option"]')).to_have_count(5)
    roller.focus()
    roller.press("Home")
    expect(dom.locator("#document-workflow")).to_have_value("")
    roller.press("ArrowDown")
    expect(dom.locator("#document-workflow")).to_have_value("object-2")
    dom.evaluate("document.getElementById('document-workflow').disabled = true")
    expect(roller).to_have_attribute("aria-disabled", "true")
    expect(roller).to_have_attribute("tabindex", "-1")
    expect(roller.locator('[role="option"]').last).to_be_disabled()
    # A synthetic event also cannot bypass the component's disabled guard.
    roller.locator('[role="option"]').last.dispatch_event("click")
    expect(dom.locator("#document-workflow")).to_have_value("object-2")


def test_label_focus_visibility_resize_and_narrow_layout(dom):
    dom.locator('label[for="attestation-workflow"]').click()
    expect(dom.locator("#attestation-workflow-roller")).to_be_focused()
    dom.evaluate("document.getElementById('page-agents').hidden = true")
    dom.evaluate("document.getElementById('page-agents').hidden = false")
    dom.set_viewport_size({"width": 390, "height": 844})
    roller = dom.locator("#agent-workflow-roller")
    roller.scroll_into_view_if_needed()
    expect(roller).to_be_visible()
    assert dom.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert roller.evaluate("node => getComputedStyle(node).scrollSnapType") == "y mandatory"


def test_default_prompt_is_editable_and_endpoint_controls_do_not_submit(dom):
    prompt = dom.locator("#agent-prompt")
    assert "Respect the selected review scope; request explicit scope expansion" in prompt.input_value()
    prompt.fill("Owner-specific review instruction")
    expect(prompt).to_have_value("Owner-specific review instruction")
    dom.locator("#connect-workspace-models").click()
    expect(dom.locator("#workspace-model-status")).to_contain_text("Not connected")
    expect(dom.locator("#agent-submit")).to_be_disabled()
    dom.locator("#endpoint-tool-support").check()
    expect(dom.locator("#agent-submit")).to_be_disabled()


def test_workflow_guide_distinguishes_future_implementation_from_actual_progress(dom):
    dom.add_script_tag(content=GUIDE)
    expect(dom.locator(".approach-stages")).to_contain_text("Human review & deployment")
    expect(dom.locator(".intake-steps li").last).to_contain_text("Approve & implement using Databricks Lakeflow")
    expect(dom.locator(".intake-steps li").last).to_contain_text("Future stage:")
    expect(dom.locator(".intake-steps li").last).not_to_have_class(re.compile(r"is-recorded|is-running|is-current"))
    expect(dom.locator(".approach-boundary")).to_contain_text("not implemented in the current local app")


def test_source_binding_human_review_is_visible_but_execution_stays_gated():
    assert "Human review before execution" in REVIEW
    assert "drafted_pending_human_review" in REVIEW
    assert "Resolve the listed human review items before any run or deployment." in REVIEW
    assert "Only fully drafted notebook-only DAGs without conditional edges are currently supported." in REVIEW
    assert "Semantic parity is unverified; execution and deployment remain unauthorized." in REVIEW