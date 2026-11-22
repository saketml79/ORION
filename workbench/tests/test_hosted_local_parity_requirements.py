"""Hosted behavior required by the workbench, exercised without cloud resources."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench.agents import AgentRuntime
from workbench.app import PackageIndex
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted import HostedSessionManager, _finish_action_before_cancellation
from workbench.tests.test_hosted import TOKEN_A2, TOKEN_B, backend, browser, create, headers, offline
from workbench.tests.test_hosted_registry import uploaded


def test_selected_xml_is_loaded_once_and_reused_for_later_page_reads(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    selected = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": package})
    calls = {"restore": 0, "index": 0}
    original_restore = DeltaRuntime.restore
    original_load = PackageIndex.load.__func__

    def restore(runtime, *args, **kwargs):
        calls["restore"] += 1
        return original_restore(runtime, *args, **kwargs)

    def load(cls, *args, **kwargs):
        calls["index"] += 1
        return original_load(cls, *args, **kwargs)

    def no_model(*args, **kwargs):
        raise AssertionError("Page reads must not invoke a model")

    monkeypatch.setattr(DeltaRuntime, "restore", restore)
    monkeypatch.setattr(PackageIndex, "load", classmethod(load))
    monkeypatch.setattr(AgentRuntime, "run", no_model)
    with browser(backend) as client:
        overview = client.get("/api/overview", headers=selected)
        assert overview.status_code == 200, overview.text
        assert overview.json()["ready"] is True
        assert overview.json()["retained_service_ready"] is True
        first = dict(calls)
        assert first == {"restore": 1, "index": 1}

        for path in ("/api/objects?kind=workflow&offset=0&limit=50", "/api/overview",
                     "/api/connections?offset=0&limit=100", "/api/documents", "/api/settings"):
            response = client.get(path, headers=selected)
            assert response.status_code == 200, (path, response.text)
            assert calls == first, (path, first, calls)
        workflow = overview.json()["workflows"][0]["id"]
        response = client.get(f"/api/object/{workflow}", headers=selected)
        assert response.status_code == 200, response.text
        assert calls == first


def test_invoke_uses_already_loaded_xml_without_a_second_restore(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    selected = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": package})
    calls = {"restore": 0, "index": 0, "runtime": 0}
    original_restore = DeltaRuntime.restore
    original_load = PackageIndex.load.__func__

    def restore(runtime, *args, **kwargs):
        calls["restore"] += 1
        return original_restore(runtime, *args, **kwargs)

    def load(cls, *args, **kwargs):
        calls["index"] += 1
        return original_load(cls, *args, **kwargs)

    def fake_run(*args, **kwargs):
        calls["runtime"] += 1
        return {}

    monkeypatch.setattr(DeltaRuntime, "restore", restore)
    monkeypatch.setattr(PackageIndex, "load", classmethod(load))
    monkeypatch.setattr(AgentRuntime, "run", fake_run)
    monkeypatch.setattr("workbench.model_access.workspace_ready", lambda runtime: True)
    with browser(backend) as client:
        overview = client.get("/api/overview", headers=selected)
        assert overview.status_code == 200, overview.text
        workflow = overview.json()["workflows"][0]["id"]
        initial = {"restore": calls["restore"], "index": calls["index"]}
        response = client.post("/api/agents/run", headers=selected, json={
            "workflow_id": workflow, "scenario": "bronze", "prompt": "Review selected workflow",
            "consent": True, "generate_code": False,
        })
        assert response.status_code == 202, response.text
        assert calls["runtime"] == 1
        assert {"restore": calls["restore"], "index": calls["index"]} == initial
        repeat = client.get("/api/overview", headers=selected)
        assert repeat.status_code == 200, repeat.text
        assert {"restore": calls["restore"], "index": calls["index"]} == initial


def test_loaded_page_reads_remain_responsive_while_agent_is_running(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    selected = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": package})
    started, release = Event(), Event()
    loads = []
    original_load = PackageIndex.load.__func__
    with browser(backend) as client:
        create(client, "Other user", TOKEN_B)

    def load(cls, *args, **kwargs):
        loads.append(1)
        return original_load(cls, *args, **kwargs)

    def fake_run(*args, **kwargs):
        started.set()
        if not release.wait(15):
            raise AssertionError("Simulated agent was not released")
        return {}

    monkeypatch.setattr(AgentRuntime, "run", fake_run)
    monkeypatch.setattr("workbench.model_access.workspace_ready", lambda runtime: True)
    monkeypatch.setattr(PackageIndex, "load", classmethod(load))
    with browser(backend) as client, ThreadPoolExecutor(max_workers=2) as pool:
        overview = client.get("/api/overview", headers=selected)
        assert overview.status_code == 200, overview.text
        workflow = overview.json()["workflows"][0]["id"]
        action = pool.submit(client.post, "/api/agents/run", headers=selected, json={
            "workflow_id": workflow, "scenario": "bronze", "prompt": "Review selected workflow",
            "consent": True, "generate_code": False,
        })
        try:
            assert started.wait(10), "Simulated agent never started"
            refreshed = headers(TOKEN_A2, **{"X-Workbench-Project": project, "X-Workbench-Package": package})
            page_read = pool.submit(client.get, "/api/objects?kind=workflow&offset=0&limit=50", headers=refreshed)
            try:
                result = page_read.result(timeout=3)
                assert result.status_code == 200, result.text
                assert len(loads) == 1
                assert any(token == TOKEN_A2 for token, _, _ in backend.calls)
                history = client.get("/api/agents/runs?status_only=true&offset=0&limit=50", headers=refreshed)
                assert history.status_code == 200, history.text
                assert history.json()["run_status_verified"] is True
                assert history.json()["active_run_present"] is True
                other = headers(TOKEN_B, **{"X-Workbench-Project": project, "X-Workbench-Package": package})
                denied = client.get("/api/objects?kind=workflow&offset=0&limit=50", headers=other)
                assert denied.status_code in {403, 404}, denied.text
                assert len(loads) == 1
            finally:
                release.set()
            assert action.result(timeout=10).status_code == 202
        finally:
            release.set()


def test_hosted_background_invoke_keeps_pages_and_active_status_available(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    selected = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": package})
    started, release = Event(), Event()
    loads = []
    original_load = PackageIndex.load.__func__

    def load(cls, *args, **kwargs):
        loads.append(1)
        return original_load(cls, *args, **kwargs)

    def fake_run(*args, **kwargs):
        started.set()
        if not release.wait(20):
            raise AssertionError("Simulated agent was not released")
        return {}

    monkeypatch.setattr(AgentRuntime, "run", fake_run)
    monkeypatch.setattr("workbench.model_access.workspace_ready", lambda runtime: True)
    monkeypatch.setattr(PackageIndex, "load", classmethod(load))
    with browser(backend) as client:
        overview = client.get("/api/overview", headers=selected)
        assert overview.status_code == 200, overview.text
        workflow = overview.json()["workflows"][0]["id"]
        operation = uuid4().hex
        response = client.post("/api/hosted/operations", headers=selected, json={
            "id": operation, "method": "POST", "path": "/api/agents/run", "body": {
                "workflow_id": workflow, "scenario": "bronze", "prompt": "Review selected workflow",
                "consent": True, "generate_code": False,
            },
        })
        assert response.status_code == 202, response.text
        try:
            assert started.wait(10), "Hosted operation did not start the simulated agent"
            with ThreadPoolExecutor(max_workers=1) as pool:
                read = pool.submit(client.get, "/api/objects?kind=workflow&offset=0&limit=50", headers=selected)
                assert read.result(timeout=3).status_code == 200
            history = client.get("/api/agents/runs?status_only=true&offset=0&limit=50", headers=selected)
            assert history.status_code == 200, history.text
            assert history.json()["active_run_present"] is True
            assert len(loads) == 1
        finally:
            release.set()
        deadline = monotonic() + 15
        while monotonic() < deadline:
            receipt = client.get(f"/api/hosted/operations/{operation}", headers=selected)
            assert receipt.status_code == 200, receipt.text
            if receipt.json()["status"] not in {"queued", "running"}:
                break
            Event().wait(0.05)
        assert receipt.json()["status"] == "completed", receipt.text
        assert receipt.json()["status_code"] == 202
        saved = client.get("/api/agents/runs?status_only=true&offset=0&limit=50", headers=selected)
        assert saved.status_code == 200, saved.text
        assert saved.json()["run_status_verified"] is True
        assert saved.json()["active_run_present"] is False


def test_cancelled_action_keeps_workers_alive_until_cleanup_finishes():
    async def check():
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def action():
            started.set()
            await release.wait()
            finished.set()

        task = asyncio.create_task(_finish_action_before_cancellation(action()))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(check())


def test_waiting_read_is_admitted_when_agent_window_opens_and_shutdown_waits():
    async def check():
        manager = HostedSessionManager(1)
        session, borrowed = await manager.enter("owner")
        assert not borrowed
        root = session.root
        session.app = SimpleNamespace()
        scope = ("a" * 32, "b" * 64)
        waiting = asyncio.create_task(manager.enter("owner", scope))
        await asyncio.sleep(0)
        assert not waiting.done()
        session.live_readers_done.set()
        session.live_scope = scope
        manager.notify(session)
        read_session, borrowed = await asyncio.wait_for(waiting, 1)
        assert borrowed and read_session is session and session.live_readers == 1
        session.app = None
        manager.close_all()
        assert root.exists()
        session.live_scope = None
        session.live_readers -= 1
        session.live_readers_done.set()
        await asyncio.to_thread(manager.release, session)
        assert root.exists()
        session.gate.release()
        manager.notify(session)
        await asyncio.to_thread(manager.release, session)
        assert not root.exists()

    asyncio.run(check())


def test_different_package_cannot_borrow_the_active_package_session():
    async def check():
        manager = HostedSessionManager(1)
        session, borrowed = await manager.enter("owner")
        assert not borrowed
        session.app = SimpleNamespace()
        session.live_scope = ("a" * 32, "b" * 64)
        session.live_readers_done.set()
        async def other_package():
            next_session, borrowed = await manager.enter("owner", ("a" * 32, "c" * 64))
            assert next_session is session and not borrowed
            next_session.gate.release()
            manager.notify(next_session)
            await asyncio.to_thread(manager.release, next_session)

        waiting = asyncio.create_task(other_package())
        await asyncio.sleep(0)
        assert not waiting.done() and session.live_readers == 0
        session.live_scope = None
        session.app = None
        session.gate.release()
        manager.notify(session)
        await asyncio.wait_for(waiting, 1)
        await asyncio.to_thread(manager.release, session)
        manager.close_all()

    asyncio.run(check())
