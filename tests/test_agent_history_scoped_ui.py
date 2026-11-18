"""Full real JS request traces from a loaded Explorer to Agent activity."""
import json

import pytest
from playwright.sync_api import expect

from test_explorer_graph_ui import explorer
from test_navigation_prerequisites import navigation, tab, choose_project
from test_project_package_autoload import browser, settle, PROJECT_A, PROJECT_B, PACKAGE_A


def hold_history(page):
    held, calls = [], []
    def handler(route):
        request = route.request
        calls.append((request.method, request.url, request.all_headers()))
        held.append(route)
    page.route("**/api/agents/runs**", handler)
    return held, calls


def answer(route, data=None, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(data or {"items": [], "total": 0}))


def test_loaded_explorer_to_agents_exact_metadata_reads_single_pending(explorer):
    page, state, _, wire = explorer(hosted=True)
    expect(tab(page, "agents")).to_be_enabled()
    expect(page.locator('#page-agents')).to_have_attribute('data-package-hash', PACKAGE_A)
    page.locator('#agent-workflow').select_option('wf/selected', force=True)
    held, calls = hold_history(page)
    before = len(state["requests"])
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    expect(page.locator("#agent-submit")).to_be_disabled()
    # Multiple observer notifications and user refresh while pending coalesce.
    page.locator("#refresh-runs").click()
    page.evaluate("document.getElementById('page-agents').hidden=false")
    settle(page)
    assert len(held) == 1 and len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][2]["x-workbench-project"] == PROJECT_A
    assert calls[0][2]["x-workbench-package"] == PACKAGE_A
    paths = [entry["path"] for entry in state["requests"][before:]]
    assert paths.count("/api/loops") == 1
    assert paths.count("/api/tracing/status") == 1
    assert "/api/overview" not in paths
    assert not any(path.startswith(("/api/object", "/api/package")) for path in paths)
    assert "Reading saved package" not in page.locator("#operation-banner").inner_text()
    answer(held.pop(), {"items": [], "total": 0, "active_run_present": False, "run_status_verified": True})
    expect(page.locator("#agent-submit")).to_be_enabled()
    expect(page.locator("#operation-banner")).to_be_hidden()


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_history_errors_block_invoke_without_fallback_or_overview(explorer, status):
    page, state, _, _ = explorer(hosted=True)
    held, calls = hold_history(page)
    before = len(state["requests"])
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    answer(held.pop(), {"detail": "denied"}, status)
    expect(page.locator("#runs-list")).to_contain_text("Agent invocation stays disabled")
    expect(page.locator("#agent-submit")).to_be_disabled()
    page.locator("#agent-form").dispatch_event("submit")
    settle(page)
    assert len(calls) == 1
    assert not any(entry["path"] in {"/api/overview", "/api/agents/run"} for entry in state["requests"][before:])


def test_queued_outside_visible_page_blocks_and_page_entry_rechecks_without_clearing_list(explorer):
    page, state, _, _ = explorer(hosted=True)
    page.locator('#agent-workflow').select_option('wf/selected', force=True)
    held, calls = hold_history(page)
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    answer(held.pop(), {"items": [{"id": "1" * 32, "status": "completed", "workflow_name": "Saved workflow"}],
                       "total": 56, "truncated": True, "active_run_present": True, "run_status_verified": True})
    expect(page.locator("#runs-list")).to_contain_text("Saved workflow")
    expect(page.locator("#agent-submit")).to_be_disabled()
    page.locator("#loops-consent").check()
    expect(page.locator("#loops-start")).to_be_disabled()
    tab(page, "explorer").click()
    before = len(state["requests"])
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    expect(page.locator("#runs-list")).to_contain_text("Saved workflow")
    expect(page.locator("#agent-submit")).to_be_disabled()
    assert len(calls) == 2
    answer(held.pop(), {"items": [], "total": 0, "active_run_present": False, "run_status_verified": True})
    expect(page.locator("#agent-submit")).to_be_enabled()
    assert not any(entry["path"] == "/api/overview" for entry in state["requests"][before:])


def test_running_candidate_writer_keeps_parent_visible_as_active(explorer):
    page, state, _, _ = explorer(hosted=True)
    run_id = "3" * 32
    detail = {"id": run_id, "status": "partial", "phase": "complete",
        "workflow_id": "wf/selected", "workflow_name": "Saved workflow",
        "candidate_writer": {"id": "4" * 32, "status": "running",
            "phase": "candidate_code_generation", "started_at": "2026-11-13T10:00:00Z"}}
    state["agent_run_details"] = {run_id: detail}
    held, _ = hold_history(page)
    page.locator('#agent-workflow').select_option('wf/selected', force=True)
    tab(page, "agents").click()
    answer(held.pop(), {"items": [{
        **detail,
    }], "total": 1, "truncated": False, "active_run_present": True, "run_status_verified": True})
    expect(page.locator('#runs-list')).to_contain_text('Candidate writer: running')
    expect(page.locator('#operation-banner')).to_contain_text('Candidate writer: running')
    expect(page.locator('#agent-submit')).to_be_disabled()
    expect(page.locator('#run-detail')).to_contain_text('candidate_writer')
    expect(page.locator('#run-detail')).to_contain_text('running')
    expect(page.locator('#run-detail')).to_contain_text('candidate_code_generation')


def test_package_initial_loading_locks_agent_navigation_and_step_two(navigation):
    page, state, fulfill = navigation(fragment="projects", hold=True)
    choose_project(page)
    expect(tab(page, 'agents')).to_be_disabled()
    tab(page, "agents").dispatch_event('click')
    expect(page.locator('#page-agents')).to_be_hidden()
    expect(page.locator("#agent-form")).to_have_attribute("inert", "")
    for identifier in ("agent-workflow", "agent-scenario", "agent-prompt", "agent-consent", "agent-submit"):
        expect(page.locator("#" + identifier)).to_be_disabled()
    expect(page.locator("#workspace-model-panel")).to_be_hidden()
    assert not any(entry["path"] in {"/api/agents/runs", "/api/loops", "/api/tracing/status"} for entry in state["requests"])
    for route, data in state["pending"]:
        fulfill(route, data)


def test_stale_history_response_cannot_unlock_new_project(explorer):
    page, state, _, _ = explorer(hosted=True)
    held, calls = hold_history(page)
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    assert len(held) == 1
    old = held.pop()
    tab(page, "projects").click()
    page.locator("#project-select").select_option(PROJECT_B)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "agents").click()
    expect(page.locator("#operation-banner")).to_contain_text("Loading saved agent runs")
    assert len(held) == 1
    answer(old, {"items": [], "total": 0, "active_run_present": False, "run_status_verified": True})
    settle(page)
    expect(page.locator("#agent-submit")).to_be_disabled()
    answer(held.pop(), {"items": [{"id": "2" * 32, "status": "queued"}], "total": 1,
                       "active_run_present": True, "run_status_verified": True})
    expect(page.locator("#runs-list")).to_contain_text("queued")
    expect(page.locator("#agent-submit")).to_be_disabled()
    assert [entry[2]["x-workbench-project"] for entry in calls] == [PROJECT_A, PROJECT_B]