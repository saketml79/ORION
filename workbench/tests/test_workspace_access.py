"""Static UI regression with explicitly fake routes. No live service or auth calls."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"
SCRIPT = STATIC / "workspace-access.js"


def test_script_hook_and_javascript_syntax():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count('src="/static/workspace-access.js" defer') == 1
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("localStorage", "sessionStorage", "Authorization", "innerHTML", "/api/agents/runs"):
        assert forbidden not in source
    node = shutil.which("node")
    assert node, "Node is required for the standalone JavaScript syntax check"
    subprocess.run([node, "--check", str(SCRIPT)], check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def ui(chromium):
    context = chromium.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    calls, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    fake = {"fail": None, "default_route_configured": False, "endpoints": [
        {"name": "chat-looking-unknown", "type": "unknown", "state": "READY"},
        {"name": "not-ready", "type": "chat", "state": "NOT_READY"},
        {"name": "explicit-chat", "type": "chat", "state": "READY"},
        {"name": "second-chat", "type": "chat", "state": "READY"},
    ]}

    def route(request_route):
        request = request_route.request
        calls.append({"url": request.url, "method": request.method,
                      "headers": request.all_headers(), "body": request.post_data_json if request.post_data else None})
        path = request.url.split("test.invalid", 1)[1]
        if fake["fail"] and path.startswith(fake["fail"][0]):
            request_route.fulfill(status=fake["fail"][1], json={"detail": fake["fail"][2]})
            return
        if path == "/api/models/workspace":
            data = {"workspace": "https://workspace.example.com", "mode": "local_user_profile", "profile": "labs"}
        elif path == "/api/models/status":
            data = {"inference_ready": fake["default_route_configured"], "connected": True,
                    "active_endpoint": "explicit-chat" if fake["default_route_configured"] else None}
        elif path == "/api/models/connect":
            data = {"connected": True, "workspace": "https://workspace.example.com", "endpoints": fake["endpoints"], "default_endpoint": "explicit-chat",
                    "selected_endpoint": "explicit-chat" if fake["default_route_configured"] else None,
                    "default_route_configured": fake["default_route_configured"],
                    "route_source": "hosted_default" if fake["default_route_configured"] else None}
        elif path == "/api/models/select":
            data = {"selected_endpoint": request.post_data_json["endpoint"], "inference_ready": True,
                    "notice": "Model clients configured. No inference has been executed or verified."}
        elif path == "/api/databricks/volumes/connect":
            data = {"connected": True, "profile": "labs", "mode": "profile"}
        elif path.startswith("/api/databricks/volumes/catalogs?"):
            data = {"items": [{"name": "cat"}], "truncated": True, "limit": 200}
        elif path.startswith("/api/databricks/volumes/schemas?"):
            data = {"items": [{"name": "sch"}], "truncated": False}
        elif path.startswith("/api/databricks/volumes/volumes?"):
            data = {"items": [{"name": "vol"}], "truncated": False}
        elif path.startswith("/api/databricks/volumes/files?"):
            folder = "directory=nested" in path
            data = {"items": [{"name": "one.xml", "path": "nested/one.xml" if folder else "one.xml",
                               "is_directory": False, "size_bytes": 42}],
                    "directory": "nested" if folder else "", "volume_path": "/Volumes/cat/sch/vol", "truncated": False}
            if not folder:
                data["items"].insert(0, {"name": "nested", "path": "nested", "is_directory": True, "size_bytes": None})
        elif path == "/api/databricks/volumes/import":
            data = {"status": "staged", "project_id": "project-a", "total": 1,
                    "analysis_started": False, "job_id": None,
                    "items": [{"name": "one.xml", "status": "staged", "reused": True}]}
        else:
            pytest.fail(f"Unexpected fake request: {path}")
        request_route.fulfill(json=data)

    context.route("**/*", route)
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    page.set_content('<base href="https://test.invalid/">' + markup)
    page.add_style_tag(content=(STATIC / "app.css").read_text(encoding="utf-8"))
    page.evaluate("""() => {
      document.querySelectorAll('.page').forEach(section => { section.hidden = false; });
      const project = document.getElementById('project-select');
      project.append(new Option('Fake project A', 'project-a'), new Option('Fake project B', 'project-b'));
      project.value = 'project-a';
      const member = document.getElementById('project-package'); member.disabled = false;
      member.append(new Option('Fake ready member', 'hash-a')); member.value = 'hash-a';
      window.refreshCount = 0; window.projectRefreshCount = 0;
      document.getElementById('refresh').addEventListener('click', () => window.refreshCount++);
      document.getElementById('refresh-projects').addEventListener('click', () => window.projectRefreshCount++);
    }""")
    page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
    expect(page.locator("#workspace-model-status")).to_contain_text("Connect workspace models")
    yield page, calls, fake
    context.close()
    assert not errors, errors


def browse(page):
    page.locator("#connect-workspace-volume").click()
    expect(page.locator("#workspace-volume-status")).to_contain_text("capped at 200")
    page.locator("#workspace-volume-catalog").select_option("cat")
    expect(page.locator("#workspace-volume-schema")).to_be_enabled()
    page.locator("#workspace-volume-schema").select_option("sch")
    expect(page.locator("#workspace-volume-volume")).to_be_enabled()
    page.locator("#workspace-volume-volume").select_option("vol")
    expect(page.locator("#workspace-volume-path")).to_have_text("/Volumes/cat/sch/vol")


def test_explicit_model_selection_metadata_headers_and_refresh_only(ui):
    page, calls, _ = ui
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#agent-endpoint")).to_be_enabled()
    expect(page.locator("#agent-endpoint")).to_have_value("")
    assert page.locator('#agent-endpoint option[value="chat-looking-unknown"]').is_disabled()
    assert page.locator('#agent-endpoint option[value="not-ready"]').is_disabled()
    assert not any(call["url"].endswith("/select") for call in calls)
    page.locator("#agent-endpoint").select_option("second-chat")
    expect(page.locator("#workspace-model-status")).to_contain_text("Selected endpoint: second-chat. Not applied.")
    expect(page.locator("#workspace-model-selection")).to_contain_text("Pending changes")
    expect(page.locator("#workspace-model-selection strong").first).to_have_text("second-chat")
    expect(page.locator("#apply-workspace-model")).to_be_disabled()
    page.locator("#endpoint-tool-support").check()
    page.locator("#apply-workspace-model").click()
    expect(page.locator("#workspace-model-status")).to_contain_text("Saved endpoint: second-chat")
    expect(page.locator("#workspace-model-status")).to_contain_text("inference has not been run or verified")
    expect(page.locator("#workspace-model-selection strong").last).to_have_text("second-chat")
    expect(page.locator("#workspace-model-selection")).not_to_contain_text("Pending changes")
    assert page.evaluate("refreshCount") == 0
    assert not any(call["url"].endswith("/api/overview") for call in calls)
    select = next(call for call in calls if call["url"].endswith("/select"))
    assert select["body"] == {"endpoint": "second-chat", "tool_support_confirmed": True}
    for call in calls:
        assert call["headers"]["x-workbench-request"] == "1"
        assert call["headers"]["x-workbench-project"] == "project-a"
        assert call["headers"]["x-workbench-package"] == "hash-a"
    expect(page.locator("#agent-submit")).to_be_disabled()


def test_project_without_package_allows_model_discovery_and_volume(ui):
    page, calls, _ = ui
    page.locator("#project-package").select_option("")
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#workspace-model-status")).to_contain_text("authenticated endpoint metadata is ready")
    assert any(call["url"].endswith("/api/models/connect") for call in calls)
    expect(page.locator("#agent-endpoint")).to_have_value("")
    expect(page.locator("#apply-workspace-model")).to_be_disabled()
    page.locator("#connect-workspace-volume").click()
    expect(page.locator("#workspace-volume-catalog")).to_be_enabled()
    assert "x-workbench-package" not in calls[-1]["headers"]


def test_saved_route_is_displayed_but_never_automatically_selected(ui):
    page, calls, fake = ui
    fake["default_route_configured"] = True
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-saved-status')).to_contain_text('explicit-chat')
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#agent-endpoint")).to_be_enabled()
    expect(page.locator("#agent-endpoint")).to_have_value("")
    expect(page.locator("#workspace-model-selection strong").last).to_have_text("explicit-chat")
    expect(page.locator("#workspace-model-status")).to_contain_text("No selection or inference runs automatically")
    expect(page.locator("#endpoint-tool-support")).not_to_be_checked()
    expect(page.locator("#agent-consent")).to_have_count(0)
    assert not any(call["url"].endswith("/select") for call in calls)
    assert not any(call["url"].endswith("/agents/run") for call in calls)


@pytest.mark.parametrize("prefix,control,status,detail", [
    ("/api/models/connect", "connect-workspace-models", "workspace-model-status", "The labs sign-in has expired. Reauthenticate the labs profile, then reconnect."),
    ("/api/databricks/volumes/connect", "connect-workspace-volume", "workspace-volume-status", "Reconnect Databricks Volume authentication (local profile: labs)"),
])
def test_precise_auth_failures_no_success(ui, prefix, control, status, detail):
    page, _, fake = ui
    fake["fail"] = (prefix, 401, detail)
    page.locator("#" + control).click()
    expect(page.locator("#" + status)).to_have_text("HTTP 401: " + detail)
    expect(page.locator("#apply-workspace-model")).to_be_disabled()
    expect(page.locator("#workspace-volume-import")).to_be_disabled()


def test_volume_metadata_navigation_button_staging_and_real_refresh(ui):
    page, calls, _ = ui
    browse(page)
    assert page.locator("#workspace-volume-panel select").count() == 3
    assert page.locator("#agent-endpoint").evaluate("select => !select.multiple && select.size <= 1")
    assert page.locator("#workspace-volume-schema option").last.inner_text() == "cat.sch"
    page.get_by_role("button", name="Open folder: nested", exact=True).click()
    expect(page.locator("#workspace-volume-path")).to_have_text("/Volumes/cat/sch/vol/nested")
    page.locator("#workspace-volume-files input").check()
    expect(page.locator("#workspace-volume-import")).to_be_enabled()
    page.locator("#workspace-volume-back").click()
    expect(page.locator("#workspace-volume-path")).to_have_text("/Volumes/cat/sch/vol")
    expect(page.locator("#workspace-volume-files input")).not_to_be_checked()
    page.locator("#workspace-volume-files input").check()
    page.locator("#workspace-volume-import").click()
    expect(page.locator("#workspace-volume-status")).to_contain_text("Analysis started: no")
    expect(page.locator("#workspace-volume-status")).to_contain_text("one.xml: staged (reused)")
    assert page.evaluate("projectRefreshCount") == 1
    imported = next(call for call in calls if call["url"].endswith("/import"))
    assert imported["method"] == "POST"
    assert imported["body"] == {"project_id": "project-a", "catalog": "cat", "schema": "sch",
                                "volume": "vol", "paths": ["one.xml"], "consent": True}
    assert imported["headers"]["x-workbench-project"] == "project-a"
    assert imported["headers"]["x-workbench-package"] == "hash-a"
    assert not any("jobs" in call["url"] or "agents/runs" in call["url"] for call in calls)
    expect(page.locator("#workspace-volume-panel")).to_contain_text("active Databricks authorization to browse workspace metadata")


def test_unavailable_hint_observer_preserves_real_run_errors_and_stops(ui):
    page, _, _ = ui
    page.evaluate("""() => {
      window.hintMutations = 0;
      new MutationObserver(() => window.hintMutations++).observe(document.getElementById('agent-availability'), {childList:true});
      document.getElementById('agent-availability').textContent = 'Agent execution unavailable. WORKBENCH_MODEL=provider:model';
      document.querySelector('#agent-form .form-status').textContent = 'Actual run failed: HTTP 500';
    }""")
    expect(page.locator("#agent-availability")).to_contain_text("Choose a ready chat endpoint above")
    assert page.evaluate("hintMutations") <= 2
    expect(page.locator("#agent-form .form-status")).to_have_text("Actual run failed: HTTP 500")
    page.evaluate("document.getElementById('agent-availability').textContent = 'Actual run failed: HTTP 502'")
    expect(page.locator("#agent-availability")).to_have_text("Actual run failed: HTTP 502")


def test_project_switch_clears_selection_and_narrow_card_layout(ui):
    page, _, _ = ui
    browse(page)
    page.locator("#workspace-volume-files input").check()
    page.locator("#project-select").select_option("project-b")
    expect(page.locator("#workspace-volume-import")).to_be_disabled()
    expect(page.locator("#workspace-volume-catalog")).to_have_value("")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#workspace-volume-panel").scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_no_eligible_endpoint_does_not_guess_from_name(ui):
    page, calls, fake = ui
    fake["endpoints"] = [{"name": "chat-gpt-tools", "type": "unknown", "state": "READY"}]
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#workspace-model-status")).to_contain_text("no endpoint explicitly reports both READY and chat")
    expect(page.locator("#agent-endpoint")).to_be_disabled()
    page.locator("#endpoint-tool-support").check()
    expect(page.locator("#apply-workspace-model")).to_be_disabled()
    assert not any(call["url"].endswith("/select") for call in calls)


def test_no_project_volume_import_and_connect_are_blocked(ui):
    page, calls, _ = ui
    page.locator("#project-select").select_option("")
    expect(page.locator("#connect-workspace-volume")).to_be_disabled()
    expect(page.locator("#workspace-volume-import")).to_be_disabled()
    assert not any("databricks/volumes" in call["url"] for call in calls)


def test_volume_controls_have_vertical_spacing(ui):
        page, _, _ = ui
        card = page.locator('#workspace-volume-panel')
        assert card.evaluate("e => getComputedStyle(e).rowGap") == '16px'
        gaps = card.evaluate("""card => {
            const gap=(a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().bottom;
            const label=card.querySelector('label[for="workspace-volume-catalog"]');
            return {
                buttonToLabel:gap(card.querySelector('#connect-workspace-volume'),label),
                labelToSelect:gap(label,card.querySelector('#workspace-volume-catalog')),
                dropdownToLabel:gap(card.querySelector('#workspace-volume-catalog'),card.querySelector('label[for="workspace-volume-schema"]')),
                filesToButton:gap(card.querySelector('#workspace-volume-files'),card.querySelector('#workspace-volume-import')),
                buttonToStatus:gap(card.querySelector('#workspace-volume-import'),card.querySelector('#workspace-volume-status'))
            };
        }""")
        assert gaps['labelToSelect'] >= 8
        assert all(value >= 16 for key, value in gaps.items() if key != 'labelToSelect')


def test_pending_volume_response_cannot_populate_another_project(ui):
    page, _, _ = ui
    pending = []
    page.route("**/api/databricks/volumes/catalogs?*", lambda route: pending.append(route))
    page.locator("#connect-workspace-volume").click()
    expect(page.locator("#workspace-volume-panel")).to_contain_text("No analysis is started")
    page.wait_for_function("document.getElementById('workspace-volume-catalog').disabled")
    # A browser-side context switch is allowed while the metadata request is pending.
    page.locator("#project-select").select_option("project-b")
    assert pending
    pending[0].fulfill(json={"items": [{"name": "stale-catalog"}], "truncated": False})
    expect(page.locator("#connect-workspace-volume")).to_be_enabled()
    expect(page.locator("#workspace-volume-catalog option")).to_have_count(1)
    expect(page.locator("#workspace-volume-status")).to_have_text("Select a saved project, then connect to browse Databricks.")


def test_pending_status_cannot_overwrite_connect_auth_error(ui):
    page, _, fake = ui
    pending = []
    page.route("**/api/models/status", lambda route: pending.append(route))
    page.locator("#project-package").select_option("")
    page.locator("#project-package").select_option("hash-a")
    fake["fail"] = ("/api/models/connect", 401, "The labs sign-in has expired.")
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#workspace-model-status")).to_have_text("HTTP 401: The labs sign-in has expired.")
    assert pending
    pending[0].fulfill(json={"inference_ready": False, "connected": False})
    # Round-trip through browser task/microtask queues to settle the pending status.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#workspace-model-status")).to_have_text("HTTP 401: The labs sign-in has expired.")


def test_pending_saved_settings_never_blocks_discovery_or_selection(ui):
    page, calls, _ = ui
    pending = []
    page.route("**/api/models/status", lambda route: pending.append(route))
    page.locator("#project-package").select_option("")
    expect(page.locator("#workspace-model-saved-status")).to_contain_text("Loading saved model settings")
    expect(page.locator("#connect-workspace-models")).to_be_enabled()
    page.locator("#connect-workspace-models").click()
    expect(page.locator("#agent-endpoint")).to_be_enabled()
    expect(page.locator("#agent-endpoint")).to_have_value("")
    page.locator("#agent-endpoint").select_option("second-chat")
    page.locator("#endpoint-tool-support").check()
    expect(page.locator("#apply-workspace-model")).to_be_enabled()
    expect(page.locator("#agent-submit")).to_be_disabled()
    assert pending
    assert not any(call["url"].endswith("/select") for call in calls)
    for route in pending:
        route.fulfill(json={"active_endpoint": "explicit-chat", "inference_ready": True})
    expect(page.locator("#agent-endpoint")).to_have_value("second-chat")


def test_four_step_layout_preserves_controls_and_mobile_geometry(ui):
    page, _, _ = ui
    page.locator('#page-agents').scroll_into_view_if_needed()
    expect(page.locator('#workspace-model-heading')).to_have_text('Select agentic model endpoint')
    expect(page.locator('#agents-heading')).to_have_text('Agent activity')
    assert len(page.locator('#page-agents .page-heading p').inner_text().split()) <= 12
    assert len(page.locator('.workspace-model-copy p').inner_text().split()) <= 24
    expect(page.locator('#page-agents .eyebrow')).to_have_text([
        'EXPLICIT INVOCATION ONLY', 'STEP 1 · MODEL', 'STEP 2 · SOURCE & SCOPE',
        'STEP 3 · ASSESSMENT', 'STEP 4 · RESULTS & HUMAN REVIEW'])
    positions = [page.locator(selector).bounding_box()['y'] for selector in (
        '#workspace-model-panel', '#agent-workflow', '#agent-assessment-heading', '#agent-results-heading')]
    assert positions == sorted(positions)
    for theme in ('light', 'dark'):
        page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_workspace_url_before_connect_independent_of_delayed_settings(ui):
    page, calls, _ = ui
    contexts, settings = [], []
    page.route('**/api/models/workspace', lambda route: contexts.append(route))
    page.route('**/api/models/status', lambda route: settings.append(route))
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-target')).to_have_text('Resolving configured workspace…')
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    assert contexts and settings
    contexts.pop().fulfill(json={'workspace': 'https://verified.example.com', 'mode': 'obo'})
    expect(page.locator('#workspace-model-target')).to_have_text(
        'Workspace https://verified.example.com · Hosted signed-in user (OBO)')
    expect(page.locator('#connect-workspace-models')).to_contain_text('https://verified.example.com')
    assert 'test.invalid' not in page.locator('#workspace-model-target').inner_text()
    assert all(call['method'] == 'GET' for call in calls)
    settings.pop().fulfill(json={'inference_ready': False})


@pytest.mark.parametrize('bad_url', [None, 'http://workspace.example.com',
    'https://user:secret@workspace.example.com', 'https://workspace.example.com/path?secret=1'])
def test_unknown_or_unsafe_workspace_is_never_guessed_or_exposed(ui, bad_url):
    page, _, _ = ui
    page.route('**/api/models/workspace', lambda route: route.fulfill(json={'workspace': bad_url, 'mode': 'obo'}))
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-target')).to_have_text('Workspace URL unavailable · Hosted signed-in user (OBO)')
    expect(page.locator('#connect-workspace-models')).to_have_text('Connect workspace models')


def test_discovery_and_apply_have_real_indeterminate_progress_and_cleanup(ui):
    page, _, fake = ui
    pending = []
    page.route('**/api/models/connect', lambda route: pending.append(route))
    expect(page.locator('#workspace-model-target')).to_contain_text('https://workspace.example.com')
    page.locator('#connect-workspace-models').click()
    progress = page.locator('#workspace-model-progress')
    expect(progress).to_be_visible()
    expect(progress).to_contain_text('Discovering endpoints · Workspace https://workspace.example.com')
    assert progress.locator('progress').get_attribute('value') is None
    expect(page.locator('#connect-workspace-models')).to_be_disabled()
    expect(page.locator('#endpoint-tool-support')).to_be_disabled()
    screenshot = STATIC.parents[1] / 'output' / 'workbench' / 'workspace-model-picker-local.png'
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    page.locator('#workspace-model-panel').screenshot(path=str(screenshot), animations='disabled')
    pending.pop().fulfill(json={'connected': True, 'workspace': 'https://workspace.example.com', 'endpoints': fake['endpoints']})
    expect(progress).to_be_hidden()
    expect(page.locator('#agent-endpoint')).to_have_value('')
    page.locator('#agent-endpoint').select_option('second-chat')
    page.locator('#endpoint-tool-support').check()
    page.route('**/api/models/select', lambda route: pending.append(route))
    page.locator('#apply-workspace-model').click()
    expect(progress).to_be_visible()
    expect(progress).to_contain_text('Applying endpoint selection')
    expect(page.locator('#agent-endpoint')).to_be_disabled()
    expect(page.locator('#apply-workspace-model')).to_be_disabled()
    pending.pop().fulfill(status=503, json={'detail': 'Save unavailable'})
    expect(progress).to_be_hidden()
    expect(page.locator('#workspace-model-status')).to_have_text('HTTP 503: Save unavailable')
    expect(page.locator('#agent-endpoint')).to_be_enabled()
    expect(page.locator('#endpoint-tool-support')).to_be_enabled()
    expect(page.locator('#apply-workspace-model')).to_be_enabled()
    expect(page.locator('#workspace-model-panel')).to_have_attribute('aria-busy', 'false')


def test_old_scope_cannot_clear_new_discovery_progress_or_workspace(ui):
    page, _, fake = ui
    pending = []
    page.route('**/api/models/connect', lambda route: pending.append(route))
    page.locator('#connect-workspace-models').click()
    expect(page.locator('#workspace-model-progress')).to_be_visible()
    page.locator('#project-select').select_option('project-b')
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    page.locator('#connect-workspace-models').click()
    expect(page.locator('#workspace-model-progress')).to_be_visible()
    assert len(pending) == 2
    pending[0].fulfill(json={'connected': True, 'workspace': 'https://stale.example.com', 'endpoints': fake['endpoints']})
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('#workspace-model-progress')).to_be_visible()
    expect(page.locator('#connect-workspace-models')).to_be_disabled()
    expect(page.locator('#workspace-model-target')).not_to_contain_text('stale.example.com')
    pending[1].fulfill(status=401, json={'detail': 'Reconnect required'})
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    expect(page.locator('#workspace-model-status')).to_have_text('HTTP 401: Reconnect required')
    expect(page.locator('#agent-endpoint')).to_be_disabled()


def test_stale_workspace_response_cannot_replace_new_scope_target(ui):
    page, _, _ = ui
    pending = []
    page.route('**/api/models/workspace', lambda route: pending.append(route))
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-target')).to_have_text('Resolving configured workspace…')
    page.locator('#project-package').select_option('hash-a')
    expect(page.locator('#workspace-model-target')).to_have_text('Resolving configured workspace…')
    assert len(pending) == 2
    pending[1].fulfill(json={'workspace': 'https://current.example.com', 'mode': 'obo'})
    expect(page.locator('#workspace-model-target')).to_contain_text('https://current.example.com')
    pending[0].fulfill(json={'workspace': 'https://stale.example.com', 'mode': 'local_user_profile'})
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('#workspace-model-target')).to_have_text('Workspace https://current.example.com · Hosted signed-in user (OBO)')


def test_scope_switch_during_apply_discards_late_confirmation(ui):
    page, _, _ = ui
    page.locator('#connect-workspace-models').click()
    expect(page.locator('#agent-endpoint')).to_be_enabled()
    page.locator('#agent-endpoint').select_option('second-chat')
    page.locator('#endpoint-tool-support').check()
    pending = []
    page.route('**/api/models/select', lambda route: pending.append(route))
    page.locator('#apply-workspace-model').click()
    expect(page.locator('#workspace-model-progress')).to_contain_text('Applying endpoint selection')
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    expect(page.locator('#endpoint-tool-support')).not_to_be_checked()
    pending.pop().fulfill(json={'selected_endpoint': 'second-chat'})
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('#workspace-model-selection strong').last).to_have_text('Not confirmed by the service')
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    expect(page.locator('#apply-workspace-model')).to_be_disabled()
    assert page.evaluate('refreshCount') == 0


def test_elapsed_wait_and_actual_server_timings_are_accessible_and_not_percentages(ui):
    page, _, fake = ui
    pending = []
    page.clock.install()
    page.route('**/api/models/connect', lambda route: pending.append(route))
    page.locator('#connect-workspace-models').click()
    expect(page.locator('#workspace-model-progress')).to_contain_text('Discovering endpoints')
    page.clock.fast_forward(65000)
    expect(page.locator('#workspace-model-elapsed')).to_contain_text('1m 5s elapsed')
    expect(page.locator('#workspace-model-elapsed')).to_contain_text('Still waiting for the server response')
    expect(page.locator('#workspace-model-elapsed')).to_have_attribute('aria-live', 'off')
    assert page.locator('#workspace-model-progress progress').get_attribute('value') is None
    pending.pop().fulfill(json={'connected': True, 'endpoints': fake['endpoints'],
        'timings': {'authentication_seconds': 0.75, 'endpoint_list_seconds': 0.4}})
    expect(page.locator('#workspace-model-progress')).to_be_hidden()
    expect(page.locator('#workspace-model-timing')).to_contain_text('Request finished after 1m 5s')
    expect(page.locator('#workspace-model-timing')).to_contain_text('Authentication 0.8s · Endpoint list 0.4s')
    expect(page.locator('#agent-endpoint')).to_have_value('')
    expect(page.locator('#endpoint-tool-support')).not_to_be_checked()


def test_hosted_settings_progress_uses_real_inline_stages_without_modal_or_agent_activity(ui):
    page, calls, _ = ui
    page.route('**/api/models/workspace', lambda route: route.fulfill(json={
        'workspace': 'https://workspace.example.com', 'mode': 'obo'}))
    page.route('**/api/models/status', lambda route: route.fulfill(json={
        'route_version': 'a' * 64, 'inference_ready': False}))
    page.evaluate('''() => {
      window.orionModelRouteSave = {pending: () => null, save: options => {
        window.saveOptions = options;
        return new Promise(resolve => { window.completeSave = resolve; });
      }};
    }''')
    page.locator('#project-package').select_option('')
    expect(page.locator('#workspace-model-target')).to_contain_text('Hosted signed-in user')
    page.locator('#connect-workspace-models').click()
    page.locator('#agent-endpoint').select_option('second-chat')
    page.locator('#endpoint-tool-support').check()
    page.locator('#apply-workspace-model').click()
    progress = page.locator('#workspace-model-progress')
    expect(progress).to_be_visible()
    expect(page.locator('#apply-workspace-model')).to_be_disabled()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    expect(page.locator('#orion-hosted-intake-activity')).to_have_count(0)
    for index, phase in enumerate(['route_validate', 'route_commit', 'route_readback']):
        page.evaluate("phase => saveOptions.onStatus({operation_id: 'b'.repeat(32), status: 'running', progress: {stage: phase}})", phase)
        expect(progress.locator('[aria-current=step]')).to_have_text(
            ['Validate endpoint', 'Save setting', 'Confirm saved'][index])
        expect(progress.locator('li[data-state=complete]')).to_have_count(index)
        assert progress.locator('progress').get_attribute('value') is None
        expect(progress).not_to_contain_text('Preparing package')
        expect(progress).not_to_contain_text('agent working')
    page.evaluate("completeSave({selected_endpoint: 'second-chat', inference_ready: true, route_version: 'c'.repeat(64)})")
    expect(progress).to_be_hidden()
    expect(page.locator('#workspace-model-saved-status')).to_have_text('Saved route: second-chat.')
    assert not [call for call in calls if '/api/agents/' in call['url'] or '/api/source-analysis/' in call['url']]


def test_optional_status_failure_is_separate_and_connect_does_not_duplicate_status(ui):
    page, calls, _ = ui
    pending = []
    page.route('**/api/models/status', lambda route: pending.append(route))
    page.locator('#project-package').select_option('')
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    page.locator('#connect-workspace-models').click()
    expect(page.locator('#agent-endpoint')).to_be_enabled()
    assert len(pending) == 1
    before = len([call for call in calls if call['url'].endswith('/models/status')])
    pending.pop().fulfill(status=503, json={'detail': 'Saved state unavailable'})
    expect(page.locator('#workspace-model-saved-status')).to_contain_text('Discovery is still available')
    expect(page.locator('#workspace-model-status')).to_contain_text('Connected: authenticated endpoint metadata is ready')
    expect(page.locator('#connect-workspace-models')).to_be_enabled()
    expect(page.locator('#workspace-model-panel')).to_have_attribute('data-model-ready', 'false')
    page.locator('#agent-endpoint').select_option('second-chat')
    page.locator('#endpoint-tool-support').check()
    expect(page.locator('#apply-workspace-model')).to_be_enabled()
    assert len([call for call in calls if call['url'].endswith('/models/status')]) == before