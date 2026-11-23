"""Offline loading observations, isolation and actual restore counters."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
from uuid import uuid4

from databricks.sdk.errors import PermissionDenied
from fastapi import HTTPException
import pytest
from fastapi.testclient import TestClient

from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_loading import HostedLoading, read_id, warehouse_status
from workbench.hosted import HostedConfig, create_hosted_app
from workbench.tests.test_hosted import (
    HOST, TOKEN_A, TOKEN_A2, TOKEN_B, backend, browser, create, headers, offline,
)
from workbench.tests.test_delta_runtime import authority, adapter, estate, sources


def test_uuid_validation_isolation_and_bounded_records():
    loading = HostedLoading(maximum=3, per_user=2)
    first, second, third = (uuid4().hex for _ in range(3))
    for invalid in ("", "../secret", str(uuid4()), "G" * 32, "A" * 32):
        with pytest.raises(HTTPException) as error:
            read_id(invalid)
        assert error.value.status_code == 400
    loading.begin("alice", first)
    loading.begin("alice", second)
    with pytest.raises(HTTPException) as error:
        loading.begin("alice", third)
    assert error.value.status_code == 429
    with pytest.raises(HTTPException) as error:
        loading.begin("alice", first)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        loading.snapshot("bob", first)
    assert error.value.status_code == 404
    loading.begin("bob", first)
    loading.update("alice", first, "artifact_files", verified_files=1, total_files=3)
    assert loading.snapshot("bob", first)["verified_files"] is None
    assert loading.snapshot("alice", second)["status"] == "queued"
    loading.finish("alice", first)
    loading.begin("alice", third)
    assert len(loading._items) == 3
    with pytest.raises(HTTPException):
        loading.snapshot("alice", first)


def test_actual_warehouse_state_and_permission_unavailable():
    identity = SimpleNamespace(client=SimpleNamespace(warehouses=SimpleNamespace(
        get=lambda warehouse: SimpleNamespace(state="STARTING"))))
    assert warehouse_status(identity, "warehouse") == {
        "availability": "available", "state": "STARTING", "exception_class": None}

    def denied(warehouse):
        raise PermissionDenied("secret token and raw upstream URL")

    identity.client.warehouses.get = denied
    assert warehouse_status(identity, "warehouse") == {
        "availability": "unavailable", "state": None, "exception_class": "PermissionDenied"}


def test_restore_reports_real_verified_counts_including_sources(authority, tmp_path):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    estate(runtime.local)
    sources(runtime)
    runtime.flush()
    expected = len([p for p in runtime.local.rglob("*") if p.is_file()])
    observed = []
    restarted = adapter(authority, tmp_path / "restored")
    assert restarted.restore(initialize=False, progress=lambda phase, **kw: observed.append((phase, kw)))
    assert observed[0] == ("metadata_restore", {})
    counts = [kw for phase, kw in observed if phase == "artifact_files"]
    assert counts == [dict(verified_files=i, total_files=expected) for i in range(expected + 1)]
    assert all("percent" not in kw for _, kw in observed)


def test_failed_artifact_is_not_counted(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    (runtime.local / "state").mkdir()
    (runtime.local / "state" / "payload.bin").write_bytes(b"valid")
    runtime.flush()
    restarted = adapter(authority, tmp_path / "restored")
    original = restarted._object

    def corrupt(reference):
        if reference["byte_size"] == 5:
            raise ValueError("sensitive upstream message")
        return original(reference)

    monkeypatch.setattr(restarted, "_object", corrupt)
    observed = []
    with pytest.raises(ValueError):
        restarted.restore(progress=lambda phase, **kw: observed.append((phase, kw)))
    assert observed[-1] == ("artifact_files", {"verified_files": 0, "total_files": 1})


def test_same_user_overview_reads_run_concurrently_and_progress_stays_scoped(backend, monkeypatch):
    entered, all_entered, release = Event(), Event(), Event()
    active_reads, active_lock = [0], Lock()
    original_restore = DeltaRuntime.restore
    original_client = backend.client
    warehouse_calls, identities = [], []
    original_resolve = backend.resolve

    def resolve(host, token, **kw):
        identities.append(token)
        return original_resolve(host, token, **kw)

    backend.resolve = resolve

    def client(token):
        result = original_client(token)

        def warehouse_get(warehouse):
            warehouse_calls.append((token, warehouse))
            return SimpleNamespace(state="RUNNING")

        result.warehouses = SimpleNamespace(get=warehouse_get)
        return result

    backend.client = client

    def restore(self, *, initialize=True, progress=None):
        if progress is not None:
            progress("metadata_restore")
            with active_lock:
                active_reads[0] += 1
                if active_reads[0] == 1:
                    entered.set()
                if active_reads[0] >= 3:
                    all_entered.set()
            try:
                assert release.wait(20)
            finally:
                with active_lock:
                    active_reads[0] -= 1
        return original_restore(self, initialize=initialize, progress=progress)

    monkeypatch.setattr(DeltaRuntime, "restore", restore)
    first, second = uuid4().hex, uuid4().hex
    with browser(backend) as http:
        create(http)
        with ThreadPoolExecutor(max_workers=4) as pool:
            active = pool.submit(http.get, "/api/overview", headers=headers(XWorkbenchReadId=first))
            try:
                assert entered.wait(10)
                concurrent = pool.submit(http.get, "/api/overview", headers=headers(XWorkbenchReadId=second))
                third = pool.submit(http.get, "/api/overview", headers=headers(XWorkbenchReadId=uuid4().hex))
                assert all_entered.wait(10), "Same-user overview reads remained queued or consumed per-user request capacity"
                second_status = http.get("/api/hosted/loading/" + second, headers=headers()).json()
                assert second_status["phase"] == "metadata_restore" and second_status["status"] == "running"
                warehouse_calls.clear()
                status = http.get("/api/hosted/loading/" + first, headers=headers(TOKEN_A2))
                assert status.status_code == 200, status.text
                assert status.json()["phase"] == "metadata_restore"
                assert status.json()["status"] == "running"
                assert status.json()["warehouse"]["state"] == "RUNNING"
                assert status.json()["elapsed_seconds"] >= 0
                assert status.headers["cache-control"] == "no-store"
                assert http.get("/api/hosted/loading/" + first, headers=headers(TOKEN_B)).status_code == 404
                assert http.get("/api/hosted/loading/" + first).status_code == 401
                assert warehouse_calls == [(TOKEN_A2, "warehouse")]
                assert TOKEN_A2 in identities
            finally:
                release.set()
            assert active.result(timeout=30).status_code == 200
            assert concurrent.result(timeout=30).status_code == 200
            assert third.result(timeout=30).status_code == 200
        for diagnostic_id in (first, second):
            status = http.get("/api/hosted/loading/" + diagnostic_id, headers=headers()).json()
            assert status["phase"] == "responding" and status["status"] == "succeeded"
            assert status["exception_class"] is None


def test_distinct_user_capacity_is_still_enforced(backend, monkeypatch):
    entered, release = Event(), Event()
    original = DeltaRuntime.restore

    def blocked(self, *, initialize=True, progress=None):
        if progress is not None:
            progress("metadata_restore")
            entered.set()
            assert release.wait(10)
        return original(self, initialize=initialize, progress=progress)

    monkeypatch.setattr(DeltaRuntime, "restore", blocked)
    app = create_hosted_app(HostedConfig(HOST, "https://app.example.com", warehouse="warehouse",
                                          max_active_users=1), identity_resolver=backend.resolve)
    with TestClient(app, base_url="https://app.example.com", raise_server_exceptions=False) as client:
        create(client, "Active user has committed state")
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(client.get, "/api/overview",
                                 headers=headers(TOKEN_A, XWorkbenchReadId=uuid4().hex))
            try:
                assert entered.wait(10)
                rejected = client.get("/api/overview",
                                      headers=headers(TOKEN_B, XWorkbenchReadId=uuid4().hex))
                assert rejected.status_code == 429
                assert "active-user capacity" in rejected.json()["detail"]
            finally:
                release.set()
            assert active.result(timeout=30).status_code == 200


def test_reused_diagnostic_id_never_rejects_overview_request(backend):
    diagnostic_id = "1234567890abcdef1234567890abcdef"
    with browser(backend) as client:
        create(client, "Committed before overview")
        response = client.get("/api/overview", headers=headers(XWorkbenchReadId=diagnostic_id))
        assert response.status_code == 200, response.text
        repeated = client.get("/api/overview", headers=headers(XWorkbenchReadId=diagnostic_id))
        assert repeated.status_code == 200, repeated.text
        # A collision disables only progress for the duplicate request. The
        # package response remains the authoritative outcome.
        assert repeated.json()["ready"] is False


def test_restored_stages_permission_failure_does_not_block_load(backend, monkeypatch):
    original_client = backend.client
    phases = []
    original_update = HostedLoading.update

    def update(self, owner, diagnostic_id, phase, **kw):
        phases.append(phase)
        return original_update(self, owner, diagnostic_id, phase, **kw)

    def client(token):
        result = original_client(token)

        def denied(warehouse):
            raise PermissionDenied("upstream secret " + token)

        result.warehouses = SimpleNamespace(get=denied)
        return result

    backend.client = client
    monkeypatch.setattr(HostedLoading, "update", update)
    diagnostic_id = uuid4().hex
    with browser(backend) as http:
        create(http)
        assert http.get("/api/overview", headers=headers(XWorkbenchReadId=diagnostic_id)).status_code == 200
        status = http.get("/api/hosted/loading/" + diagnostic_id, headers=headers()).json()
        assert status["status"] == "succeeded"
        assert status["warehouse"] == {
            "availability": "unavailable", "state": None, "exception_class": "PermissionDenied"}
        assert {"provisioning", "metadata_restore", "artifact_files",
                "preparing_package", "responding"} <= set(phases)
        assert status["verified_files"] == status["total_files"]
        assert "upstream" not in str(status) and TOKEN_A not in str(status)
        assert http.get("/api/overview", headers=headers(XWorkbenchReadId="bad")).status_code == 400


def test_terminal_failure_safe_class_and_diagnostic_id(backend, monkeypatch):
    def failed(self, *, initialize=True, progress=None):
        if progress:
            progress("metadata_restore")
        raise PermissionDenied("raw secret " + TOKEN_A)

    diagnostic_id = uuid4().hex
    monkeypatch.setattr(DeltaRuntime, "restore", failed)
    with browser(backend) as http:
        response = http.get("/api/overview", headers=headers(XWorkbenchReadId=diagnostic_id))
        assert response.status_code == 503
        assert response.json()["diagnostic_id"] == diagnostic_id
        status = http.get("/api/hosted/loading/" + diagnostic_id, headers=headers()).json()
        assert status["phase"] == "metadata_restore"
        assert status["status"] == "failed"
        assert status["exception_class"] == "PermissionDenied"
        assert TOKEN_A not in str(status) and "raw secret" not in response.text


def test_readonly_overview_does_not_flush_mutable_audit_chain(backend, monkeypatch):
    def failed(self):
        raise PermissionDenied("sensitive audit failure " + TOKEN_A)

    diagnostic_id = uuid4().hex
    with browser(backend) as http:
        create(http)
        monkeypatch.setattr(DeltaRuntime, "flush_audit", failed)
        response = http.get("/api/overview", headers=headers(XWorkbenchReadId=diagnostic_id))
        assert response.status_code == 200
        status = http.get("/api/hosted/loading/" + diagnostic_id, headers=headers()).json()
        assert status["phase"] == "responding"
        assert status["status"] == "succeeded" and status["exception_class"] is None