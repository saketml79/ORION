import json

import pytest
from playwright.sync_api import expect
from workbench.tests.test_notebook_workspace_ui import notebook_workspace, prose_assets, ui, browser


@pytest.mark.parametrize("save_fails", [False, True])
def test_app_manages_preview_save_then_cleanup(notebook_workspace, save_fails):
    page, _, _, path, _, _ = notebook_workspace
    job = "e" * 32; session = "1" * 32; sha = "2" * 64; calls = []
    def operation(route):
        action = route.request.url.rsplit("/", 1)[-1]; calls.append(action)
        body = route.request.post_data_json
        assert body["consent"] is True
        assert body["expected_workspace"] == "https://workspace.example"
        common = {"job_id": job, "session_id": session, "artifact_path": path}
        if action == "attach":
            response = {**common, "status": "attached", "execution_started": False,
                "workspace": "https://workspace.example", "notebook_url": "https://workspace.example/editor/notebooks/123",
                "message": "Temporary notebook ready"}
        elif action == "edit-preview":
            response = {**common, "remote_sha256": sha, "diff": "Cell 2: changed business calculation", "message": "Review before saving"}
        elif action == "edit-return":
            assert body["expected_remote_sha256"] == sha
            if save_fails:
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Durable save unconfirmed"}))
                return
            response = {**common, "saved": True, "remote_sha256": sha}
        else:
            assert action == "edit-cleanup" and calls[-2] == "edit-return"
            response = {**common, "saved": True, "remote_deleted": True,
                        "message": "Returned edits saved and temporary notebook deleted"}
        route.fulfill(content_type="application/json", body=json.dumps(response))
    page.route(f"**/api/jobs/{job}/spark-drafts/*", operation)
    page.locator('[data-task-key="t_wait"]').click()
    inspector = page.get_by_role("region", name="Selected task details", exact=True)
    inspector.get_by_role("button", name="Open in Databricks", exact=True).click()
    inspector.get_by_role("button", name="Preview returned edits", exact=True).click()
    expect(inspector.locator(".notebook-edit-diff")).to_contain_text("changed business calculation")
    assert calls == ["attach", "edit-preview"]
    inspector.get_by_role("button", name="Save returned edits and delete temporary notebook", exact=True).click()
    if save_fails:
        expect(inspector.locator(".notebook-attachment-note")).to_contain_text("Save not confirmed")
        assert calls == ["attach", "edit-preview", "edit-return"]
        expect(inspector.get_by_role("link", name="Open attached notebook")).to_be_visible()
    else:
        expect(page.locator("#spark-draft-status")).to_have_text("Saved notebooks loaded.")
        assert calls == ["attach", "edit-preview", "edit-return", "edit-cleanup"]