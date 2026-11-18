"""Agent activity has one selected-run workflow, not a second manual loop UI."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "agent-loops.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        chromium = runtime.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", HTML, flags=re.S)
    page.set_content(markup)
    page.evaluate("""() => {
      document.querySelectorAll('.page').forEach(section => { section.hidden = section.id !== 'page-agents'; });
      const selector = document.getElementById('agent-workflow');
      selector.replaceChildren(new Option('Selected workflow', 'workflow-1'));
      document.getElementById('object-json').textContent = JSON.stringify({node: {kind: 'workflow', id: 'workflow-1'}});
      document.getElementById('scenario').value = 'bronze';
      document.querySelector('[data-page="agents"]').addEventListener('click', () => {
        document.querySelectorAll('.page').forEach(section => { section.hidden = section.id !== 'page-agents'; });
      });
    }""")
    page.add_script_tag(content=SCRIPT)
    yield page, errors
    context.close()
    assert errors == []


def test_no_duplicate_manual_loop_or_evaluation_panel(ui):
    page, _ = ui
    expect(page.locator('#agent-loops')).to_have_count(0)
    expect(page.get_by_role('heading', name='Agent loops & evaluations')).to_have_count(0)
    expect(page.get_by_role('button', name=re.compile('verification loop|hill climb|compare recorded runs', re.I))).to_have_count(0)
    expect(page.locator('#agent-consent')).to_have_count(0)
    expect(page.locator('#agent-submit')).to_be_visible()
    expect(page.locator('#agent-arena')).to_have_count(1)


def test_explorer_handoff_selects_workflow_without_starting_a_run(ui):
    page, _ = ui
    page.locator('#page-explorer').evaluate("element => element.hidden = false")
    page.locator('#object-json').evaluate("element => { element.textContent = JSON.stringify({node:{kind:'workflow', id:'workflow-1'}}); element.dispatchEvent(new Event('input',{bubbles:true})); }")
    handoff = page.get_by_role('button', name='Review this workflow with agents')
    expect(handoff).to_be_enabled()
    handoff.click()
    expect(page.locator('#agent-workflow')).to_have_value('workflow-1')
    expect(page.locator('#agent-scenario')).to_have_value('bronze')
    expect(page.locator('#agent-loops')).to_have_count(0)
