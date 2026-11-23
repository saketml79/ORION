"""Direct selected Job CI/CD controls using production script and offline API."""
from pathlib import Path

from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui


def test_selected_job_preview_confirmation_deploy_and_link(ui):
    page, state, fulfill = ui
    script = Path(__file__).resolve().parents[1] / "static" / "selected-job-deploy.js"
    page.route("**/static/selected-job-deploy.js", lambda route: route.fulfill(
        content_type="text/javascript", path=str(script)))
    page.add_script_tag(url="/static/selected-job-deploy.js")
    candidate = {"job_id": "e" * 32, "name": "Selected workflow", "project_id": "d" * 32,
        "package_hash": "a" * 64, "workflow_id": "wf/one", "scenario": "bronze",
        "design_id": "b" * 64, "design_version": 9, "design_sha256": "c" * 64,
        "input_version": "f" * 64, "manifest_sha256": "1" * 64, "profile": "labs"}
    requests = []

    def selected(route):
        requests.append((route.request.url, route.request.method, route.request.post_data_json))
        if route.request.url.endswith("/preview"):
            assert route.request.post_data_json == {k: v for k, v in candidate.items() if k not in {"job_id", "name"}}
            fulfill(route, {"preview_sha256": "2" * 64, "bundle": {"resources": {"jobs": {"only_job": {}}}}})
        else:
            assert route.request.post_data_json["consent"] is True
            assert route.request.post_data_json["expected_workspace"] == "https://workspace.example"
            fulfill(route, {"id": "3" * 32, "status": "queued"}, 202)

    page.route("**/api/jobs/" + candidate["job_id"] + "/selected-job/*", selected)
    page.route("**/api/models/workspace", lambda route: fulfill(route, {"workspace": "https://workspace.example"}))
    page.route("**/api/selected-job-deployments/*", lambda route: fulfill(route, {
        "id": "3" * 32, "status": "succeeded", "workspace": "https://workspace.example", "workspace_job_id": 789}))
    page.evaluate("record => document.dispatchEvent(new CustomEvent('orion:selected-job-candidate', {detail:record}))", candidate)
    page.locator('[data-page="release"]').click()
    panel = page.locator("#selected-job-deployment")
    expect(panel.get_by_role("button", name="Deploy this Job to development workspace")).to_be_disabled()
    panel.get_by_role("button", name="Preview selected Job deployment").click()
    expect(panel).to_contain_text("Preview ready")
    expect(panel.get_by_role("button", name="Deploy this Job to development workspace")).to_be_disabled()
    panel.locator('input[type="checkbox"]').check()
    panel.get_by_role("button", name="Deploy this Job to development workspace").click()
    expect(panel).to_contain_text("Deployment: succeeded")
    expect(panel.get_by_role("link", name="Open deployed Job")).to_have_attribute("href", "https://workspace.example/jobs/789")
    assert len(requests) == 2
    assert not any("/spark-drafts/run" in request["path"] for request in state["requests"])
    page.locator("#project-select").select_option("project-two", force=True)
    expect(panel.get_by_role("button", name="Preview selected Job deployment")).to_be_disabled()