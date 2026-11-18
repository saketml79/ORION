"""Offline presentation checks on real markup, CSS and review script.

No server, model, credentials or remote assets. Screenshots are pytest temp files.
"""
import json
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui
from workbench.tests.test_workflow_artifacts_api import (
    workflow_ui, select_bronze, generate_workflow,
)


STATIC = Path(__file__).resolve().parents[1] / "static"
PAGES = ("estate", "explorer", "proposals", "evidence", "agents", "release", "logs", "evolution")


@pytest.fixture
def presentation(browser):
    context = browser.new_context(offline=True, service_workers="block",
                                  viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    traffic, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # This fixture never triggers a review action. Any request is a regression.
    page.route("**/*", lambda route: (traffic.append(route.request.url), route.abort()))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    page.set_content(markup)
    page.add_style_tag(content=(STATIC / "app.css").read_text(encoding="utf-8"))
    # Match the real later-loaded arena stylesheet to verify cascade specificity.
    page.add_style_tag(content=(STATIC / "agent-arena.css").read_text(encoding="utf-8"))
    page.add_script_tag(content=(STATIC / "agent-arena.js").read_text(encoding="utf-8"))
    page.evaluate("""() => {
      window.fetch = async path => {
        if (!String(path).startsWith('/api/redesigns?')) throw new Error('Unexpected offline fixture request');
        return new Response(JSON.stringify({items:[], total:0}), {status:200, headers:{'Content-Type':'application/json'}});
      };
    }""")
    page.add_script_tag(content=(STATIC / "redesign-review.js").read_text(encoding="utf-8"))
    yield page
    context.close()
    assert not errors, errors
    assert not traffic, traffic


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("section", PAGES)
def test_page_typography_controls_wrap_and_keyboard_focus(presentation, section, width, theme):
    page = presentation
    page.set_viewport_size({"width": width, "height": 1000})
    page.evaluate("""({section, theme}) => {
      document.documentElement.dataset.theme = theme;
      document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== `page-${section}`; });
    }""", {"section": section, "theme": theme})
    host = page.locator(f"#page-{section}")
    expect(host).to_be_visible()
    assert page.evaluate("getComputedStyle(document.body).fontSize") == "13px"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    styles = host.locator("p, label, summary, td, .small, .detail-value, .arena-kicker, .arena-role strong, .arena-role-signal, .arena-telemetry dt, .arena-telemetry dd, .arena-core strong, .arena-core > span:last-child, .arena-legend ol").evaluate_all("""nodes => nodes
      .filter(n => n.getClientRects().length && n.textContent.trim())
      .map(n => ({text: n.textContent.slice(0, 60), size: parseFloat(getComputedStyle(n).fontSize)}))""")
    assert all(12 <= item["size"] <= 14 for item in styles), styles
    controls = host.locator("button, input:not([type=checkbox]), select, textarea").evaluate_all("""nodes => nodes
      .filter(n => n.getClientRects().length && !n.classList.contains('context-native-select'))
      .map(n => ({id:n.id, height:n.getBoundingClientRect().height}))""")
    assert all(control["height"] >= 32 for control in controls), controls
    # A real navigation control supports keyboard focus in both themes.
    navigation = page.locator(f'[data-page="{section}"]')
    navigation.focus()
    navigation.press("Tab")
    page.keyboard.press("Shift+Tab")
    expect(navigation).to_be_focused()
    focus = navigation.evaluate("n => ({style:getComputedStyle(n).outlineStyle, width:parseFloat(getComputedStyle(n).outlineWidth)})")
    assert focus["style"] != "none" and focus["width"] >= 2


def test_generic_normal_objects_are_readable_and_updates_are_observed(presentation):
    page = presentation
    payload = {"scope": {"repository": "Legacy & <safe>", "folder": "Daily"},
               "enabled": False, "count": 0, "evidence_ids": ["xml/task", {"locator": "line:42"}]}
    page.evaluate("""text => {
      const host = document.getElementById('inspector-content');
      const row = document.createElement('div'); row.className = 'detail-row';
      const key = document.createElement('span'); key.className = 'detail-key'; key.textContent = 'Assessment';
      const value = document.createElement('pre'); value.className = 'detail-value'; value.textContent = text;
      row.append(key, value); host.replaceChildren(row);
      const scalar = document.createElement('pre'); scalar.className = 'detail-value'; scalar.textContent = 'wf/one';
      host.append(scalar);
    }""", json.dumps(payload))
    host = page.locator("#inspector-content")
    expect(host.locator("div.detail-value > dl")).to_have_count(1)
    expect(host).to_contain_text("Legacy & <safe>")
    expect(host).to_contain_text("No")
    expect(host).to_contain_text("0")
    expect(host).to_contain_text("line:42")
    expect(host.locator("pre.detail-value")).to_have_text("wf/one")
    assert host.locator("script, safe").count() == 0
    page.evaluate("""() => {
      const value = document.createElement('pre'); value.className = 'detail-value';
      document.getElementById('inspector-content').append(value);
      value.textContent = '{"status":"updated after mount"}';
      const list = document.createElement('ul'); const item = document.createElement('li');
      item.textContent = '[{"reason":"Owner review","required":true}]'; list.append(item);
      document.getElementById('release-blockers').append(list);
    }""")
    expect(host.locator("div.detail-value > dl")).to_have_count(2)
    expect(page.locator("#release-blockers li dl").first).to_contain_text("Owner review")
    expect(page.locator("#release-blockers li dl").first).to_contain_text("Yes")


def test_technical_json_source_and_invalid_json_remain_untouched(presentation):
    page = presentation
    page.evaluate("""() => {
      const host = document.getElementById('inspector-content');
      const details = document.createElement('details');
      const summary = document.createElement('summary'); summary.textContent = 'Technical source';
      const raw = document.createElement('pre'); raw.className = 'detail-value'; raw.textContent = '{"exact":"technical"}';
      details.append(summary, raw); host.append(details);
      const source = document.createElement('pre'); source.className = 'json-block'; source.textContent = '{"code":"unchanged"}';
      details.append(source);
      const invalid = document.createElement('pre'); invalid.className = 'detail-value'; invalid.textContent = '{not JSON}';
      host.append(invalid);
    }""")
    expect(page.locator("#inspector-content > pre.detail-value")).to_have_text("{not JSON}")
    raw = page.locator("#inspector-content details")
    assert raw.get_attribute("open") is None
    expect(raw.locator("pre.detail-value")).to_have_text('{"exact":"technical"}')
    expect(raw.locator("pre.json-block")).to_have_text('{"code":"unchanged"}')
    expect(raw.locator("dl")).to_have_count(0)


def test_native_tasks_and_inventory_show_fields_not_json(workflow_ui):
    page, state, _ = workflow_ui
    state["workflow_response"]["parameter_inventory"].append({
        "kind": "property", "name": "Runtime policy", "scope": [],
        "attributes": {"owner": "Operations", "enabled": False, "limit": 0}})
    select_bronze(page)
    generate_workflow(page)
    table = page.locator("#workflow-artifact-output table").first
    expect(table).to_contain_text("Notebook path")
    expect(table).to_contain_text("Predicate")
    assert '"notebook_path":' not in table.inner_text()
    assert '"op":' not in table.inner_text()
    expect(table.locator("tbody tr")).to_have_count(len(state["workflow_response"]["tasks"]))
    disclosure = page.locator("#workflow-artifact-output details").filter(
        has=page.get_by_text("Full scoped inventory (122 records, no truncation)", exact=True))
    disclosure.locator("summary").first.click()
    inventory = disclosure.locator("tbody tr").last
    expect(inventory).to_contain_text("Owner")
    expect(inventory).to_contain_text("Operations")
    expect(inventory).to_contain_text("No")
    expect(inventory).to_contain_text("0")
    assert '"owner":' not in inventory.inner_text()
    # Raw technical evidence remains available but collapsed by default.
    assert inventory.locator("details").get_attribute("open") is None
    assert '"owner": "Operations"' in inventory.locator("pre").text_content()
    assert state["workflow_requests"][0]["body"] == {"workflow_id": "wf/two", "scenario": "bronze", "consent": True}


def test_code_download_preserves_exact_unicode_crlf_and_symbolic_secrets(workflow_ui):
    page, state, _ = workflow_ui
    original = "# café 未変更\r\npassword = dbutils.secrets.get(scope='approved', key='smtp')\r\n\n"
    state["workflow_response"]["artifacts"]["notebooks/wait.py"] = original
    select_bronze(page)
    generate_workflow(page)
    output = page.locator("#workflow-artifact-output")
    with page.expect_download() as downloaded:
        output.get_by_role("button", name="Download notebooks/wait.py", exact=True).click()
    assert Path(downloaded.value.path()).read_bytes() == original.encode("utf-8")
    assert all(value is None for value in output.locator("details").evaluate_all(
        "nodes => nodes.map(n => n.getAttribute('open'))"))


def test_offline_review_screenshots(presentation, tmp_path):
    page = presentation
    page.evaluate("""() => {
      document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== 'page-proposals'; });
      // Generic presentation slots qualify, technical disclosures do not.
      // The retained legacy proposal-detail is now inside such a disclosure.
      const host = document.createElement('section'); host.id = 'presentation-proposal-fixture';
      document.getElementById('page-proposals').append(host);
      const heading = document.createElement('h3'); heading.textContent = 'Presentation fixture, not an actual submitted design';
      const row = document.createElement('div'); row.className = 'detail-row';
      const label = document.createElement('span'); label.className = 'detail-key'; label.textContent = 'Recovery boundary';
      const value = document.createElement('pre'); value.className = 'detail-value';
      value.textContent = JSON.stringify({purpose:'Retain captured workflow semantics', required:true, owner:'Data platform', evidence:['XML task', 'Control link']});
      row.append(label, value); host.append(heading, row);
    }""")
    expect(page.locator("#presentation-proposal-fixture dl")).to_have_count(1)
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        path = tmp_path / f"review-{width}.png"
        page.screenshot(path=str(path), full_page=True, animations="disabled")
        assert path.stat().st_size > 0
        print(f"Offline screenshot: {path}")