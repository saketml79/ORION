"""Real Chromium tests, no HTTP fixtures or mocked package/model results.

Run against the loopback workbench after loading the supplied XML by setting
WORKBENCH_TEST_URL=http://127.0.0.1:8000. Tests append explicitly labelled local
review/evidence records, never approve, generate, publish, or invoke a provider.
"""
import os
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def browser():
    url = os.environ.get("WORKBENCH_TEST_URL")
    if not url:
        pytest.skip("Set WORKBENCH_TEST_URL for real-service browser regression")
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        yield browser, url
        browser.close()


@pytest.fixture
def page(browser):
    browser_instance, url = browser
    context = browser_instance.new_context(base_url=url, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator("#package-ready")).to_have_text("Package ready", timeout=30000)
    yield page
    context.close()
    assert not errors, errors


def test_real_supplied_xml_and_light_dark_persistence(page):
    overview = page.request.get("/api/overview").json()
    assert overview["package"]["sha256"] == "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
    assert overview["counts"]["session"] == 592
    assert overview["counts"]["workflow"] == 243
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.get_by_role("button", name="Toggle light and dark theme").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_graph_english_json_fields_are_live_and_bounded(page):
    page.get_by_role("button", name="Pipeline explorer", exact=True).click()
    page.locator("#object-list button").first.click()
    expect(page.locator("#graph-area svg")).to_be_visible(timeout=30000)
    assert 0 < page.locator("#graph-area g.node").count() <= 100
    name = page.locator("#object-title").inner_text()
    page.get_by_role("tab", name="English", exact=True).click()
    expect(page.locator("#english-content")).to_contain_text(name)
    page.get_by_role("tab", name="JSON", exact=True).click()
    expect(page.locator("#object-json")).to_contain_text(name)
    page.get_by_role("tab", name="Fields & evidence", exact=True).click()
    expect(page.locator("#fields-content")).to_contain_text("Showing")
    page.get_by_role("tab", name="Graph", exact=True).click()
    page.locator("#graph-area g.node").first.press("Enter")
    expect(page.locator("#inspector-content")).not_to_be_empty()


def test_real_review_reject_rework_persists_and_gates_remain(page):
    page.get_by_role("button", name="Proposals", exact=True).click()
    page.locator("#proposal-list button").first.click(timeout=30000)
    expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    initial = page.locator("#proposal-detail>.status-pill").inner_text()
    marker = "Browser regression " + uuid4().hex[:8]
    page.locator("#review-comment").fill(marker + ": preserve recovery isolation")
    page.get_by_role("button", name="Reject", exact=True).click()
    expect(page.locator("#proposal-detail>.status-pill")).to_contain_text("rejected", timeout=30000)
    assert initial != page.locator("#proposal-detail>.status-pill").inner_text()
    page.locator("#review-comment").fill(marker + ": request reviewed revision, no automatic merge")
    page.get_by_role("button", name="Request rework", exact=True).click()
    expect(page.locator("#proposal-detail>.status-pill")).to_contain_text("draft", timeout=30000)
    expected = page.locator("#proposal-detail>.status-pill").inner_text()
    page.reload()
    page.locator("#proposal-list button").first.click(timeout=30000)
    expect(page.locator("#proposal-detail>.status-pill")).to_have_text(expected)
    expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(0)


def test_actual_evidence_upload_and_settings(page):
    page.get_by_role("button", name="Legacy Data Integration Artifacts", exact=True).click()
    marker = "browser-evidence-" + uuid4().hex[:8]
    page.locator("#document-file").set_input_files({"name": marker + ".md", "mimeType": "text/markdown", "buffer": b"# Regression evidence\nUnverified recovery requirement; no lifecycle inference.\n"})
    page.get_by_role("button", name="Store supporting evidence", exact=True).click()
    expect(page.locator("#document-form .form-status")).to_contain_text("stored", timeout=30000)
    expect(page.locator("#documents-list")).to_contain_text(marker)
    page.get_by_role("button", name="Release & GitHub", exact=True).click()
    expect(page.locator("#settings-submit")).to_be_enabled(timeout=30000)
    page.locator("#github-owner").fill("regression-owner")
    page.locator("#github-repo").fill("regression-repo")
    page.locator("#default-branch").fill("main")
    page.locator("#dev-host").fill("https://dev.example.com")
    page.locator("#settings-submit").click()
    expect(page.locator("#settings-form .form-status")).to_contain_text("saved", timeout=30000)
    saved = page.request.get("/api/settings").json()["settings"]
    assert saved["default_branch"] == "main"
    assert saved["dev_host"] == "https://dev.example.com"


def test_exports_are_real_artifacts_and_agent_never_auto_runs(page):
    english = page.request.get("/api/export/english")
    assert english.ok and "structural understanding" in english.text().lower()
    before = page.request.get("/api/agents/runs").json()["items"]
    page.get_by_role("button", name="Agent activity", exact=True).click()
    expect(page.locator("#agent-submit")).to_be_disabled()
    assert page.request.get("/api/agents/runs").json()["items"] == before
    denied = page.request.post("/api/packages/sample", data={})
    assert denied.status == 403


def test_narrow_layout_all_views_without_page_overflow(page):
    page.set_viewport_size({"width": 390, "height": 844})
    for label in ["Estate", "Pipeline explorer", "Proposals", "Legacy Data Integration Artifacts", "Agent activity", "Release & GitHub"]:
        page.get_by_role("button", name=label, exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), label


def test_estate_package_picker_switches_real_project_members(page):
    import time
    from threading import Event
    headers = {"X-Workbench-Request": "1"}
    project = page.request.post("/api/projects", headers=headers, data={
        "name": "Package picker regression " + uuid4().hex[:8], "description": "Two-package selection regression"
    }).json()
    files = []
    for name in ("Alpha", "Beta"):
        content = f'<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F"><WORKFLOW NAME="{name}"/></FOLDER></REPOSITORY></POWERMART>'.encode()
        files.append((name, content))
    # Separate uploads avoid duplicate multipart keys in the API client dictionary.
    for name, content in files:
        response = page.request.post(f"/api/projects/{project['id']}/packages/upload", headers=headers,
                                     multipart={"files": {"name": name + ".xml", "mimeType": "application/xml", "buffer": content}})
        assert response.status == 202
        job = response.json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            record = page.request.get(f"/api/projects/{project['id']}/jobs/{job['id']}").json()
            if record["status"] in {"completed", "partial", "failed"}:
                break
            Event().wait(.05)
        assert record["status"] == "completed", record
    page.get_by_role("button", name="New Project", exact=True).click()
    page.get_by_role("button", name="Refresh projects", exact=True).click()
    expect(page.locator(f"#project-select option[value='{project['id']}']")).to_have_count(1)
    page.locator("#project-select-roller [role='option']").filter(has_text=project["name"]).click()
    expect(page.locator("#project-package option")).to_have_count(3)
    members = page.request.get(f"/api/projects/{project['id']}").json()["packages"]
    page.get_by_role("button", name="Estate", exact=True).click()
    expect(page.locator("#estate-package-select option")).to_have_count(2)
    for member in members:
        page.locator("#estate-package-select-roller [role='option']").filter(has_text=member["name"]).click()
        expect(page.locator("#package-name")).to_have_text(member["name"], timeout=30000)
        expect(page.locator("#project-package")).to_have_value(member["sha256"])
        page.get_by_role("button", name="Pipeline explorer", exact=True).click()
        expect(page.locator("#object-list")).to_contain_text(member["name"].removesuffix(".xml"))
        page.get_by_role("button", name="Estate", exact=True).click()
    page.locator("#estate-package-select-roller").focus()
    page.locator("#estate-package-select-roller").press("Home")
    page.locator("#estate-package-select-roller").press("ArrowDown")
    assert page.locator("#estate-package-select").get_attribute("multiple") is None