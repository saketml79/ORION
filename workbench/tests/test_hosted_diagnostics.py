"""Hosted diagnostics and Delta-writer coordination; offline only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from fastapi.testclient import TestClient

from workbench.hosted_diagnostics import HostedDiagnostics, owner_key
from workbench.hosted_registry import DELTA_WRITE_LOCK
from workbench.hosted_write_lock import serialize_delta_write
from workbench.delta_state import DeltaStatementError
from workbench.tests.test_hosted import HOST, TOKEN_A, TOKEN_B, backend, browser, headers, offline


class DatabricksWriteError(RuntimeError):
    error_code = "DELTA_CONCURRENT_WRITE"
    sql_state = "40001"
    statement_id = "stmt-safe-123"
    diagnostic_message = "Concurrent Delta write; token=secret-value SQL=MERGE INTO private_table"


def test_diagnostic_ring_is_user_scoped_bounded_and_redacted():
    diagnostics = HostedDiagnostics(per_user_limit=2)
    alice, bob = owner_key(HOST, "alice"), owner_key(HOST, "bob")
    for number in range(3):
        diagnostics.record(alice, method="GET", path="/api/projects?secret=x", status=503,
                           error=DatabricksWriteError(), frames=[("hosted.py", "handler", number)])
    assert len(diagnostics.list(alice, limit=10)) == 2
    assert diagnostics.list(bob) == []
    event = diagnostics.list(alice)[0]
    assert event["error"]["error_code"] == "DELTA_CONCURRENT_WRITE"
    assert event["error"]["sql_state"] == "40001"
    assert event["error"]["statement_id"] == "stmt-safe-123"
    assert "secret-value" not in str(event)
    assert "private_table" not in str(event)
    assert "?secret" not in event["path"]
    assert event["frames"][0]["line"] == 2


def test_delta_statement_error_keeps_safe_provider_diagnostics():
        error = DeltaStatementError({"error_code": "DELTA_CONCURRENT_WRITE", "sql_state": "40001",
                                                                 "message": "Concurrent write; token=private SQL=MERGE INTO secret"},
                                                             statement_id="statement-123")
        diagnostics = HostedDiagnostics()
        event = diagnostics.record("owner", method="GET", path="/api/projects", status=503, error=error)
        assert event["error"]["error_code"] == "DELTA_CONCURRENT_WRITE"
        assert event["error"]["sql_state"] == "40001"
        assert event["error"]["statement_id"] == "statement-123"
        assert "private" not in str(event) and "secret" not in str(event)


def test_shared_delta_commit_lock_prevents_same_process_merge_overlap():
    entered, release = Event(), Event()
    state_lock = Lock()
    active = maximum = 0

    def write():
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            if active == 1:
                entered.set()
        if not release.wait(5):
            raise AssertionError("writer test synchronization timed out")
        with state_lock:
            active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(serialize_delta_write, write)
        assert entered.wait(5)
        second = pool.submit(serialize_delta_write, write)
        release.set()
        first.result(); second.result()
    assert maximum == 1


def test_hosted_logs_endpoint_is_fast_scoped_and_requires_no_delta_calls(backend, monkeypatch):
    from workbench.hosted import HostedDiagnostics, owner_key
    with browser(backend) as client:
        dispatcher = next(route.app for route in client.app.routes if getattr(route, "path", None) == "")
        assert isinstance(dispatcher.diagnostics, HostedDiagnostics)
        dispatcher.diagnostics.record(owner_key(HOST, "alice-id"), method="GET", path="/api/projects",
                                      status=503, error=DatabricksWriteError(), frames=[])
        backend.calls.clear()
        alice = client.get("/api/hosted/logs", headers=headers(TOKEN_A))
        bob = client.get("/api/hosted/logs", headers=headers(TOKEN_B))
        assert alice.status_code == bob.status_code == 200
        assert alice.json()["items"][0]["error"]["error_code"] == "DELTA_CONCURRENT_WRITE"
        assert bob.json()["items"] == []
        assert alice.json()["storage"].startswith("bounded per-user in-memory")
        assert not any(action == "sql" for _, action, *_ in backend.calls)
