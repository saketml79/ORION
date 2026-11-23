"""Authored local HTTP pause contracts. No live models/cloud/server activation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest

from workbench.agents import AgentRunCancelled
from workbench.tests.test_app import ready, small_xml, post
from workbench.tests.test_projects import (
    workspace, create, upload, selection, xml,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setenv("WORKBENCH_LANGSMITH_ENABLED", "0")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    connect = socket.socket.connect

    def local_only(sock, address):
        # Windows asyncio's self-pipe uses a loopback socket pair.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return connect(sock, address)
        raise AssertionError("External network forbidden")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr("langchain.chat_models.init_chat_model",
                        lambda *a, **k: pytest.fail("Provider initialization forbidden"))


class BoundaryRuntime:
    def status(self):
        return {"available": True, "authentication": "model_provided", "roles": []}

    def run(self, model, workflow_id, scenario, prompt, documents, feedback, *,
            cancellation_event=None, progress_callback=None, pause_control=None):
        assert pause_control is not None
        if not pause_control.wait("analysis:model"):
            raise AgentRunCancelled("Cooperative cancellation")
        return {"status": "needs_information", "summary": "Nonnetwork stub", "findings": []}


def queue(client, service, monkeypatch, *, headers=None, runtime=None):
    headers = headers or {}
    service.runtime = runtime or BoundaryRuntime()
    pending = []
    monkeypatch.setattr(service.agent_executor, "submit", lambda fn, *args: pending.append((fn, args)))
    workflow = next(n["id"] for n in service.active.model["nodes"] if n["kind"] == "workflow")
    result = post(client, "/api/agents/run", headers=headers,
                  json={"workflow_id": workflow, "scenario": "original", "consent": True,
                        "generate_code": False})
    assert result.status_code == 202, result.text
    return result.json(), pending


def test_pause_request_resume_duplicates_and_mutation_boundary(ready, monkeypatch):
    client, app = ready
    record, _ = queue(client, app.state.service, monkeypatch)
    path = "/api/agents/runs/" + record["id"]
    assert record["pause_supported"] is True
    assert client.post(path + "/pause").status_code == 403
    assert post(client, path + "/pause", headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    first = post(client, path + "/pause")
    assert first.status_code == 202
    requested = first.json()
    assert requested["status"] == "queued" and requested["pause_requested"]
    assert not requested["paused"] and requested["pause_boundary"] is None
    assert post(client, path + "/pause").json()["pause_requested_at"] == requested["pause_requested_at"]
    saved = client.get(path + "?status_only=true").json()
    assert saved["pause_requested_at"] == requested["pause_requested_at"]
    assert saved["consent"] is True and not saved["generate_code"]
    assert "generation_allowed" not in requested and "deployment_allowed" not in requested
    for _ in range(2):
        resumed = post(client, path + "/resume")
        assert resumed.status_code == 202
        assert resumed.json()["pause_requested_at"] is None
        assert not resumed.json()["paused"]
    saved = client.get(path + "?status_only=true").json()
    assert not saved["pause_requested"] and saved["pause_requested_at"] is None


@pytest.mark.parametrize("cancel", [False, True])
def test_waiting_worker_does_not_hold_service_lock_and_gets_terminal_cleanup(ready, monkeypatch, cancel):
    client, app = ready
    service = app.state.service
    initial, pending = queue(client, service, monkeypatch)
    path = "/api/agents/runs/" + initial["id"]
    control = service.agent_pause_controls[initial["id"]]
    observed = Event()
    persist = control._on_change

    def changed():
        persist()
        if control.snapshot()["paused"]:
            observed.set()

    control._on_change = changed
    assert post(client, path + "/pause").status_code == 202
    fn, args = pending[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        try:
            assert observed.wait(5)
            assert service.lock.acquire(timeout=1)
            service.lock.release()
            saved = client.get(path + "?status_only=true").json()
            assert saved["status"] == "running" and saved["paused"]
            assert saved["pause_boundary"] == "analysis:model" and saved["paused_at"]
            # Simulate the worker's pre-request record copy. Authoritative gate
            # state must survive later model/retry/progress record writes.
            stale_copy = deepcopy(initial)
            stale_copy["status"] = "running"
            service.update_record(stale_copy, agent=True)
            assert service.record(initial["id"], agent=True)["paused"]
            action = "/cancel" if cancel else "/resume"
            assert post(client, path + action).status_code == 202
            future.result(timeout=5)
        finally:
            control.cancel()
    terminal = service.record(initial["id"], agent=True)
    assert terminal["status"] == ("cancelled" if cancel else "completed")
    assert not terminal["paused"] and not terminal["pause_requested"]
    assert terminal["pause_requested_at"] is terminal["paused_at"] is None
    assert initial["id"] not in service.agent_pause_controls
    assert initial["id"] not in service.agent_cancel_events
    assert post(client, path + "/resume").status_code == 409


def test_cancel_before_boundary_clears_pause_and_blocks_later_resume(ready, monkeypatch):
    client, app = ready
    record, _ = queue(client, app.state.service, monkeypatch)
    path = "/api/agents/runs/" + record["id"]
    assert post(client, path + "/pause").status_code == 202
    cancelled = post(client, path + "/cancel")
    assert cancelled.status_code == 202
    assert post(client, path + "/cancel").json()["cancellation_requested_at"] == cancelled.json()["cancellation_requested_at"]
    for action in ("pause", "resume"):
        assert post(client, path + "/" + action).status_code == 409
    saved = client.get(path + "?status_only=true").json()
    assert not saved["paused"] and not saved["pause_requested"]


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "cancelled", "proposal_failed", "unavailable"])
def test_terminal_runs_never_accept_control(ready, monkeypatch, status):
    client, app = ready
    service = app.state.service
    initial, _ = queue(client, service, monkeypatch)
    record = service.record(initial["id"], agent=True)
    record["status"] = status
    service.update_record(record, agent=True)
    for action in ("pause", "resume"):
        assert post(client, f"/api/agents/runs/{record['id']}/{action}").status_code == 409
    assert service.record(record["id"], agent=True)["status"] == status


def test_unknown_wrong_package_and_stale_worker_are_not_controlled(ready, monkeypatch):
    client, app = ready
    service = app.state.service
    initial, _ = queue(client, service, monkeypatch)
    path = "/api/agents/runs/" + initial["id"]
    for action in ("pause", "resume"):
        assert post(client, f"/api/agents/runs/{'f' * 32}/{action}").status_code == 404
        assert post(client, f"/api/agents/runs/not-an-id/{action}").status_code == 404
    original = service.record(initial["id"], agent=True)
    service.update_record({**original, "package_hash": "f" * 64}, agent=True)
    assert post(client, path + "/pause").status_code == 404
    service.update_record(original, agent=True)
    service.agent_pause_controls.pop(initial["id"])
    for action in ("pause", "resume"):
        assert post(client, path + "/" + action).status_code == 409


def test_legacy_runtime_gets_no_new_keyword_and_explicitly_cannot_pause(ready, monkeypatch):
    client, app = ready
    called = Event()

    class LegacyRuntime:
        status = BoundaryRuntime.status

        def run(self, model, workflow_id, scenario, prompt, documents, feedback, *,
                cancellation_event=None, progress_callback=None):
            called.set()
            return {"status": "needs_information", "findings": []}

    initial, pending = queue(client, app.state.service, monkeypatch, runtime=LegacyRuntime())
    assert initial["pause_supported"] is False
    assert post(client, f"/api/agents/runs/{initial['id']}/pause").status_code == 409
    fn, args = pending[0]
    fn(*args)
    assert called.is_set()
    assert app.state.service.record(initial["id"], agent=True)["status"] == "completed"


def test_hosted_rejection_is_explicit_and_does_not_install_gate(ready, monkeypatch):
    client, app = ready
    app.state.hosted_request_authenticated = True
    initial, _ = queue(client, app.state.service, monkeypatch)
    assert not initial["pause_supported"]
    assert initial["id"] not in app.state.service.agent_pause_controls
    for action in ("pause", "resume"):
        response = post(client, f"/api/agents/runs/{initial['id']}/{action}")
        assert response.status_code == 501
        assert "durable actions" in response.json()["detail"]


def test_project_membership_and_registry_lease_do_not_block_resume_or_stored_status(workspace, monkeypatch):
    client, app, _, _ = workspace
    first, second = create(client, "First"), create(client, "Second")
    data = xml()
    for project in (first, second):
        assert upload(client, app, project, [("same.xml", data)])["status"] == "completed"
    headers = selection(first, data)
    manager = app.state.projects
    child = manager.acquire(first, headers["X-Workbench-Package"])
    manager.release(first, headers["X-Workbench-Package"])
    service = child.state.service
    initial, _ = queue(client, service, monkeypatch, headers=headers)
    path = "/api/agents/runs/" + initial["id"]
    control = service.agent_pause_controls[initial["id"]]
    assert post(client, path + "/pause", headers=headers).status_code == 202
    with ThreadPoolExecutor(max_workers=1) as pool:
        observed = Event()
        persist = control._on_change

        def changed():
            persist()
            if control.snapshot()["paused"]:
                observed.set()

        control._on_change = changed
        future = pool.submit(control.wait, "candidate_writer:tool")
        try:
            assert observed.wait(5)
            assert manager.lock.acquire(timeout=1)
            try:
                assert manager.contexts[(first, headers["X-Workbench-Package"])]["leases"] == 0
            finally:
                manager.lock.release()
            status = client.get(path + "?status_only=true", headers=headers)
            assert status.status_code == 200 and status.json()["paused"]
            assert post(client, path + "/resume", headers=selection(second, data)).status_code == 404
            assert control.snapshot()["pause_requested"]
            assert post(client, path + "/resume", headers=headers).status_code == 202
            assert future.result(timeout=5)
        finally:
            control.cancel()