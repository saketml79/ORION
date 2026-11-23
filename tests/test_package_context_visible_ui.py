"""Production renderer plus observer against the immutable saved package, offline only."""
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from workbench.app import PackageIndex, _create_package_app
from workbench.explanations import explain_workflow, model_indexes
from workbench.tests.test_redesign_ui import browser


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "workbench/static"
PACKAGE = ROOT / "output/workbench/packages/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba/understanding.json.gz"


@pytest.fixture(scope="module")
def saved_model():
    before = sha256(PACKAGE.read_bytes()).hexdigest()
    with gzip.open(PACKAGE, "rt", encoding="utf-8") as source:
        model = json.load(source)
    yield model
    assert sha256(PACKAGE.read_bytes()).hexdigest() == before


@pytest.fixture
def context_pages(saved_model, tmp_path):
    app = _create_package_app(tmp_path / "runtime", ROOT / "input/LumenExample.XML")
    workflows = {n["name"]: n["id"] for n in saved_model["nodes"] if n["kind"] == "workflow"}
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        app.state.service.active = PackageIndex(saved_model, tmp_path)
        def get(name, offset=0, limit=40):
            response = client.get(f"/api/workflows/{workflows[name]}/migration-context",
                                  params={"offset": offset, "limit": limit})
            assert response.status_code == 200
            return response.json()
        yield get


@pytest.fixture
def production_renderer(browser):
    context = browser.new_context(offline=True, service_workers="block")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    page.set_content(markup)
    page.add_style_tag(content=(STATIC / "app.css").read_text(encoding="utf-8"))
    page.evaluate("window.fetch = async () => new Response('{}', {status: 404})")
    page.add_script_tag(content=(STATIC / "app.js").read_text(encoding="utf-8"))
    page.add_script_tag(content=(STATIC / "redesign-review.js").read_text(encoding="utf-8"))
    page.evaluate("""() => {
      document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== 'page-explorer'; });
      document.getElementById('model-english').hidden = false;
      window.pageRequests = [];
      window.renderTestContext = contract => {
        const host = document.getElementById('english-content');
        window.renderPackageMigrationContext(host, contract, offset => window.pageRequests.push(offset));
        host.querySelectorAll('details').forEach(d => { d.open = true; });
      };
    }""")
    yield page
    context.close()
    assert not errors, errors


def test_real_32_members_and_31_invocations_are_visible_with_observer(context_pages, production_renderer):
    contract = context_pages("wkf_LOAD_AE2E_MASTER_PREP")
    assert contract["cohort"]["total"] == 32 and contract["invocations"]["total"] == 31
    page = production_renderer
    page.evaluate("contract => window.renderTestContext(contract)", contract)
    members = page.locator("#english-content > details").filter(has=page.get_by_text(re.compile(r"^Review cohort members"))).locator("li")
    calls = page.locator("#english-content > details").filter(has=page.get_by_text(re.compile(r"^Review inward, outward and cohort invocation evidence"))).locator("li")
    expect(members).to_have_count(32)
    expect(calls).to_have_count(31)
    assert all(value.strip() for value in members.all_inner_texts() + calls.all_inner_texts())
    expect(members.first).to_contain_text("wkf_LOAD_AE2E_MASTER_PREP")
    expect(calls.filter(has_text="wkf_Load_AE2E_WILTEL_NETWORK_DISCO").first).to_contain_text("nowait")
    expect(calls.filter(has_text="wkf_Load_AE2E_WILTEL_NETWORK_DISCO").first).to_contain_text("Command definition:")
    expect(page.locator("#english-content")).to_contain_text("selected saved canonical package")
    assert members.first.is_visible() and calls.first.is_visible()


def test_api_pages_direction_unknown_and_safe_text(context_pages, production_renderer):
    page = production_renderer
    name = "wkf_Load_AE2E_WILTEL_NETWORK_DISCO"
    first = context_pages(name, limit=10)
    page.evaluate("contract => window.renderTestContext(contract)", first)
    expect(page.locator("#english-content")).to_contain_text("inward:")
    page.get_by_role("button", name="Read next package-context page").click()
    assert page.evaluate("window.pageRequests") == [10]
    second = context_pages(name, offset=10, limit=10)
    page.evaluate("contract => window.renderTestContext(contract)", second)
    expect(page.locator("#english-content")).to_contain_text("offset 10")
    assert all(text.strip() for text in page.locator("#english-content > details:nth-of-type(1) li").all_inner_texts())
    second["cohort"]["items"][0]["name"] = '<img src=x onerror="alert(1)">'
    second["invocations"]["items"][0].update(direction=None, caller_name=None, candidate_names=[], workflow_name=None, wait_mode=None, command_name=None)
    page.evaluate("contract => window.renderTestContext(contract)", second)
    expect(page.locator("#english-content")).to_contain_text("direction unknown: Caller name unknown")
    expect(page.locator("#english-content")).to_contain_text("Launch mode: unknown or not specified")
    assert page.locator("#english-content img").count() == 0


def test_actual_orchestration_and_historical_gaps_have_actionable_ui(context_pages, production_renderer):
    page = production_renderer
    contract = context_pages("wkf_LOAD_AE2E_MASTER_PREP")
    page.evaluate("contract => window.renderTestContext(contract)", contract)
    host = page.locator("#english-content")
    expect(host).to_contain_text("Orchestration workflow")
    expect(host).to_contain_text("Creating or populating mock tables does not repair XML references")
    expect(host.get_by_role("button", name=re.compile(r"^Review child:"))).to_have_count(len(contract["referenced_children"]["items"]))
    wrapper = context_pages("wkf_AE2E_DUMMY_CALL_WIL_NET_DIS")
    page.evaluate("contract => window.renderTestContext(contract)", wrapper)
    expect(host).to_contain_text("historical connection redaction")
    expect(host).to_contain_text("do not infer missing UC tables")
    assert not wrapper["reference_diagnostics"]["history_changed"]


def test_saved_wrapper_explanation_preserves_historical_two_withheld_records(saved_model):
    nodes, outgoing = model_indexes(saved_model)
    workflow = next(n for n in saved_model["nodes"] if n["kind"] == "workflow"
                    and n["name"] == "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS")
    report = explain_workflow(nodes, outgoing, workflow["id"])
    gaps = next(section["items"] for section in report["sections"] if section["title"] == "Gaps and required evidence")
    assert "2 Informatica connection reference(s) are withheld in the selected saved canonical analysis" in gaps[0]
    assert "cannot establish which, or what the original XML contained" in gaps[0]
    assert "not evidence that a Unity Catalog schema or table is missing" in gaps[0]