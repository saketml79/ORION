"""Real current app.js, offline network fixtures and deterministic browser time."""
import re

import pytest
from playwright.sync_api import expect

from test_hosted_startup import startup, selection_page
from test_project_package_autoload import (
    browser, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, member, settle,
)


@pytest.fixture
def diagnostics(startup):
    def start(**kwargs):
        observed = {"calls": [], "held": [], "data": {}, "status": 200, "hold": False}
        page, state, fulfill = startup(hold_overview=True, loading_diagnostics=observed, **kwargs)
        selection_page(page, state, len(state["projects"]))
        page.clock.install()
        page.locator("#project-select").select_option(PROJECT_A)
        expect(page.locator("#package-ready")).to_have_text("Loading package understanding")
        settle(page)
        return page, state, fulfill, observed

    return start


def tick(page, observed, count):
    page.clock.run_for(3000)
    settle(page)
    # Intercepted requests are not consistently represented in Resource Timing.
    # Assert the route callback directly and fail immediately if polling did not run.
    assert len(observed["calls"]) >= count, "Expected the hosted loading diagnostic poll to reach its route handler"


@pytest.mark.parametrize("phase,label", [
    ("queued", "Waiting in the app request queue"), ("provisioning", "Provisioning"),
    ("metadata_restore", "Restoring metadata"), ("artifact_files", "Restoring artifact files"),
    ("audit_restore", "Restoring audit records"), ("preparing_package", "Preparing package"),
    ("audit_persist", "Persisting audit records"), ("responding", "Responding"),
])
def test_backend_phase_warehouse_elapsed_and_real_file_counts(diagnostics, phase, label):
    page, state, fulfill, observed = diagnostics()
    overview = next(r for r in state["requests"] if r["path"] == "/api/overview")
    read_id = overview["headers"]["xworkbenchreadid"]
    assert re.fullmatch(r"[a-f0-9]{32}", read_id)
    observed["data"] = {"phase": phase, "status": "queued" if phase == "queued" else "running", "elapsed_seconds": 12.75,
                        "verified_files": 2, "total_files": 7,
                        "warehouse": {"availability": "available", "state": "STARTING"}}
    tick(page, observed, 1)
    assert observed["calls"][0].url.endswith("/" + read_id)
    assert observed["calls"][0].all_headers()["x-workbench-package"] == PACKAGE_A
    expect(page.locator("#activity-status")).to_contain_text(label)
    expect(page.locator("#activity-status")).to_contain_text("Warehouse: STARTING")
    pane = page.locator("#package-understanding-progress")
    # Projects can remain the current page; the existing progress pane is revealed by its action.
    page.get_by_role("button", name="View package loading progress").click()
    expect(pane).to_be_visible()
    expect(pane).to_contain_text("Elapsed: 12.75s")
    expect(pane).to_contain_text("2 of 7 artifact files verified")
    expect(pane.locator("progress")).to_have_attribute("value", "2")
    expect(pane.locator("progress")).to_have_attribute("max", "7")
    # Unrelated activity refresh must not overwrite the observed stage.
    page.locator("#agent-consent").dispatch_event("change")
    expect(page.locator("#activity-status")).to_contain_text(label)
    route, data = state["pending"].pop()
    fulfill(route, data)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    page.clock.run_for(9000)
    assert len(observed["calls"]) == 1
    expect(pane).to_be_hidden()


def test_queued_overview_explains_work_has_not_started(diagnostics):
    page, state, fulfill, observed = diagnostics()
    observed["data"] = {"phase": "queued", "status": "queued", "elapsed_seconds": 20.9,
                        "warehouse": {"availability": "available", "state": "RUNNING"}}
    tick(page, observed, 1)
    label = page.locator("#activity-status")
    expect(label).to_contain_text("Waiting in the app request queue")
    expect(label).to_contain_text("package processing has not started")
    expect(label).to_contain_text("Warehouse: RUNNING")
    expect(label).to_contain_text("Elapsed: 20.9s")
    expect(label).not_to_contain_text("Queued · queued")
    route, _ = state["pending"].pop()
    fulfill(route, {"detail": "queue test complete"}, 503)
    expect(page.locator("#notice")).to_contain_text("queue test complete")


def test_404_is_unknown_then_unavailable_warehouse_is_not_stopped(diagnostics):
    page, state, fulfill, observed = diagnostics()
    observed["status"] = 404
    tick(page, observed, 1)
    expect(page.locator("#activity-status")).to_contain_text("Backend stage not yet available")
    expect(page.locator("#package-ready")).to_have_text("Loading package understanding")
    observed["status"] = 200
    observed["data"] = {"phase": "metadata_restore", "status": "failed", "elapsed_seconds": 18,
                        "warehouse": {"availability": "unavailable", "state": None,
                                      "exception_class": "PermissionDenied"}}
    tick(page, observed, 2)
    expect(page.locator("#activity-status")).to_contain_text("Warehouse state unknown (unavailable)")
    expect(page.locator("#activity-status")).not_to_contain_text("STOPPED")
    expect(page.locator("#activity-status")).to_contain_text("PermissionDenied")
    expect(page.locator("#package-understanding-progress progress")).not_to_have_attribute("value")
    expect(page.locator("#package-ready")).to_have_text("Loading package understanding")
    route, _ = state["pending"].pop()
    fulfill(route, {"detail": "Actual overview failure"}, 503)
    expect(page.locator("#notice")).to_contain_text("Actual overview failure")
    expect(page.locator("#project-package")).to_have_value(PACKAGE_A)
    page.clock.run_for(9000)
    settle(page)
    assert len(observed["calls"]) == 2
    assert len([r for r in state["requests"] if r["path"] == "/api/overview"]) == 1


def test_serial_polling_selection_cancellation_and_new_read_identity(diagnostics):
    page, state, fulfill, observed = diagnostics(projects={PROJECT_A: [member()], PROJECT_B: [member(PACKAGE_B)]})
    observed["hold"] = True
    page.clock.run_for(3000)
    settle(page)
    assert len(observed["held"]) == 1
    page.clock.run_for(15000)
    settle(page)
    assert len(observed["calls"]) == 1  # No next poll until the prior response finishes.
    first_id = observed["calls"][0].url.rsplit("/", 1)[1]
    page.locator("#project-select").select_option(PROJECT_B)
    expect(page.locator("#project-package")).to_have_value(PACKAGE_B)
    settle(page)
    observed["hold"] = False
    observed["data"] = {"phase": "audit_restore", "status": "running", "elapsed_seconds": 3,
                        "warehouse": {"availability": "available", "state": "RUNNING"}}
    fulfill(observed["held"].pop(), {"phase": "artifact_files", "status": "running"})
    page.clock.run_for(3000)
    expect(page.locator("#activity-status")).to_contain_text("Restoring audit records")
    assert len(observed["calls"]) == 2
    assert observed["calls"][-1].url.rsplit("/", 1)[1] != first_id
    assert observed["calls"][-1].all_headers()["x-workbench-project"] == PROJECT_B
    for route, data in state["pending"]:
        fulfill(route, data)
    state["pending"].clear()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#package-name")).to_have_text("b.xml")
    page.clock.run_for(9000)
    settle(page)
    assert len(observed["calls"]) == 2


def test_backend_text_is_never_interpreted_as_markup(diagnostics):
    page, _, _, observed = diagnostics()
    observed["data"] = {"phase": "responding", "status": "running", "elapsed_seconds": 1,
                        "warehouse": {"availability": "available", "state": '<img src=x onerror="window.injected=1">'}}
    tick(page, observed, 1)
    expect(page.locator("#activity-status")).to_contain_text("<img")
    expect(page.locator("#activity-status img")).to_have_count(0)
    assert page.evaluate("window.injected") is None