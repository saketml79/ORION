"""Real Delta generation publications, scoped observational history GETs only."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3

import pytest
from databricks.sdk.errors import PermissionDenied

from workbench import delta_state as ds
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_agent_reads import history_response
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B, create
from workbench.tests.test_hosted_registry import uploaded, fresh, committed


def selected(project, package, token=TOKEN_A2):
    return headers(token, **{"X-Workbench-Project": project, "X-Workbench-Package": package})


@pytest.fixture
def history(backend, uploaded, tmp_path):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    runtime = fresh(backend, tmp_path / "publish")
    runtime.restore(initialize=False)
    root = runtime.local / f"projects/{project}/package-contexts/{package}/state"
    directory = root / "agent-runs"
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(56):
        record = {"id": f"{number:032x}", "package_hash": package, "workflow_id": "w" * 64,
                  "scenario": "original", "status": "queued" if number == 0 else "completed",
                  "created_at": f"2026-11-09T00:00:{number:02d}Z", "input_version": "old",
                  "output": {"summary": "saved advisory output"}}
        if number == 55:
            record["output"] = {"summary": "saved advisory output" * 6000}
        (directory / (record["id"] + ".json")).write_text(ds._json(record), encoding="utf-8")
    with closing(sqlite3.connect(root / "loops.sqlite")) as db, db:
        for table in ds.SQLITE_MAP["loops.sqlite"]:
            db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, package_hash TEXT NOT NULL, workflow_id TEXT NOT NULL, payload TEXT NOT NULL)")
        record = {"id": "loop", "package_hash": package, "workflow_id": "w" * 64,
                  "scenario": "original", "status": "completed", "input_version": "old"}
        db.execute("INSERT INTO runs VALUES (?,?,?,?)", (record["id"], package, record["workflow_id"], ds._json(record)))
    runtime.flush()
    return project, package


def forbid_restore(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("History must not restore/package-load/publish/construct runtime models")
    for method in ("restore", "restore_audit", "flush", "flush_audit", "_records", "_database", "_verify_source"):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    monkeypatch.setattr("workbench.app.PackageIndex.load", forbidden)
    monkeypatch.setattr("workbench.databricks_models.build_chat_models", forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)


@pytest.mark.parametrize("path", ["/api/agents/runs", "/api/loops", "/api/tracing/status"])
def test_real_history_projection_no_restore_and_no_writes(backend, history, monkeypatch, path):
    project, package = history
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        for _ in range(2):
            result = client.get(path, headers=selected(project, package))
            assert result.status_code == 200, result.text
            data = result.json()
            if path == "/api/agents/runs":
                assert data["total"] == 56 and len(data["items"]) == 50 and data["truncated"]
                assert data["active_run_present"] is True and data["run_status_verified"] is True
                assert not any("output" in item for item in data["items"])
                assert all(item["stale"] is None and item["current_input_version"] is None for item in data["items"])
            elif path == "/api/loops":
                assert data["totals"] == {"runs": 1, "events": 0, "lessons": 0}
                assert data["runs"][0]["stale"] is None
                assert data["current_auth_status"]["inference_ready"] is False
            else:
                assert data["verified"] is False
        if path == "/api/agents/runs":
            page = client.get(path + "?offset=50&limit=5", headers=selected(project, package)).json()
            assert page["total"] == 56 and len(page["items"]) == 5 and page["truncated"]
            assert page["active_run_present"] is True
    assert before == (backend.rows, backend.objects)
    statements = [sql for _, action, sql in backend.calls if action == "sql"]
    assert not any(sql.startswith(("CREATE", "MERGE", "INSERT", "UPDATE", "DELETE")) for sql in statements)
    assert not any(".`audit_events`" in sql or ".`agent_checkpoints`" in sql for sql in statements)
    downloads = [backend.objects[name] for _, action, name in backend.calls if action == "download"]
    assert not any(raw.startswith((b"\x1f\x8b", b"<?xml")) for raw in downloads)
    assert not any(b"sqlite-ddl-v1" in raw or b"saved advisory output" in raw for raw in downloads)


@pytest.mark.parametrize("case,expected", [("no_auth", 401), ("other_user", 404),
    ("missing_project", 404), ("wrong_package", 403), ("denied", 503), ("hash", 503), ("missing_row", 503)])
def test_history_failures_never_fallback_or_become_empty(backend, history, tmp_path, monkeypatch, case, expected):
    project, package = history
    token = TOKEN_A2
    if case == "other_user":
        with browser(backend) as client:
            create(client, "Bob", TOKEN_B)
        token = TOKEN_B
    elif case == "missing_project":
        project = "0" * 32
    elif case == "wrong_package":
        package = "0" * 64
    elif case in {"hash", "missing_row"}:
        generation = committed(fresh(backend, tmp_path / "corruption"))["migration_id"]
        family = next(rows for name, rows in backend.rows.items() if name.endswith(".agent_runs"))
        row = next(row for row in family if row["migration_id"] == generation)
        if case == "hash":
            row["payload"] = "{}"
        else:
            family.remove(row)
    elif case == "denied":
        original = backend.client
        def denied(token):
            client = original(token)
            def reject(**kwargs):
                raise PermissionDenied("private denied message")
            client.statement_execution.execute_statement = reject
            return client
        monkeypatch.setattr(backend, "client", denied)
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        result = client.get("/api/agents/runs", headers={} if case == "no_auth" else selected(project, package, token))
        assert result.status_code == expected, result.text
        assert "private denied message" not in result.text


@pytest.mark.parametrize("path", ["/api/loops", "/api/tracing/status"])
def test_observer_metadata_endpoints_recheck_membership_without_fallback(backend, uploaded, monkeypatch, path):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=selected("0" * 32, package)).status_code == 404
        assert client.get(path, headers=selected(project, "0" * 64)).status_code == 403
        assert client.get(path, headers=selected(project, package)).status_code == 200


def test_missing_receipt_is_legitimate_empty_unselected_history(backend, tmp_path, monkeypatch):
    runtime = fresh(backend, tmp_path)
    runtime.store.ensure_tables()
    forbid_restore(monkeypatch)
    data = history_response(runtime, runtime.identity_factory(), "/api/agents/runs", None, None, {})
    assert data["items"] == [] and data["total"] == 0
    assert data["active_run_present"] is False and data["run_status_verified"] is True


@pytest.mark.parametrize("query", ["?limit=101", "?offset=-1", "?limit=invalid"])
def test_history_rejects_invalid_paging_before_restore(backend, uploaded, monkeypatch, query):
    project, _, detail = uploaded
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        assert client.get("/api/agents/runs" + query, headers=selected(project, detail["packages"][0]["sha256"])).status_code == 422


def publish_writers(backend, history, tmp_path, jobs, *, parent_status="completed"):
    project, package = history
    runtime = fresh(backend, tmp_path / "writers")
    runtime.restore(initialize=False)
    root = runtime.local / f"projects/{project}/package-contexts/{package}"
    parent = root / "state/agent-runs" / ("0" * 32 + ".json")
    record = json.loads(parent.read_bytes())
    record["status"] = parent_status
    parent.write_text(ds._json(record), encoding="utf-8")
    for subdir, job in jobs:
        directory = root / subdir / "state/jobs" if subdir else root / "state/jobs"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (job["id"] + ".json")).write_text(ds._json(job), encoding="utf-8")
    runtime.flush()
    return root


def writer(package, identifier, status, **changes):
    return {"id": f"{identifier:032x}", "kind": "spark_draft", "agent_run_id": "0" * 32,
            "package_hash": package, "status": status, "created_at": f"2026-11-10T00:00:{identifier:02d}Z",
            "phase": "candidate_code_generation", **changes}


@pytest.mark.parametrize("status", ["queued", "running", "cancelling"])
def test_candidate_writer_outside_page_blocks_and_matches_local_metadata(backend, history, tmp_path, monkeypatch, status):
    project, package = history
    job = writer(package, 100, status, output={"manifest_status": "draft", "task_statuses": [{"task_id": "t"}]})
    root = publish_writers(backend, history, tmp_path, [("", job)])
    from workbench.app import _candidate_writer_status
    local_writer = _candidate_writer_status(root, "0" * 32, package)
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        first = client.get("/api/agents/runs?status_only=true&limit=5", headers=selected(project, package))
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["total"] == 56 and data["truncated"] and data["active_run_present"] is True
        assert data["run_status_verified"] is True
        assert all(item["id"] != "0" * 32 and "output" not in item for item in data["items"])
        last = client.get("/api/agents/runs?offset=55&limit=1", headers=selected(project, package)).json()
        assert last["items"][0]["candidate_writer"] == {
            "id": job["id"], "status": status, "phase": job["phase"], "created_at": job["created_at"],
            "started_at": None, "finished_at": None, "manifest_status": "draft", "task_statuses": [{"task_id": "t"}]}
        assert last["items"][0]["candidate_writer"] == local_writer
        assert last["active_run_present"] is True
        assert client.get("/api/agents/runs", headers=selected(project, package, TOKEN_B)).status_code == 503
    assert before == (backend.rows, backend.objects)


def test_all_writers_checked_even_when_latest_is_terminal(backend, history, tmp_path, monkeypatch):
    project, package = history
    jobs = [("", writer(package, 100, "running")),
            ("package-contexts/" + package, writer(package, 101, "completed"))]
    publish_writers(backend, history, tmp_path, jobs)
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/agents/runs?offset=55&limit=1", headers=selected(project, package))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["items"][0]["candidate_writer"]["id"] == jobs[1][1]["id"]
        assert data["active_run_present"] is True and data["run_status_verified"] is True


def test_older_unknown_writer_is_not_hidden_by_latest_terminal_writer(backend, history, tmp_path, monkeypatch):
    project, package = history
    jobs = [("", writer(package, 100, "unknown")), ("", writer(package, 101, "completed"))]
    publish_writers(backend, history, tmp_path, jobs)
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/agents/runs?limit=1", headers=selected(project, package))
        assert response.status_code == 200, response.text
        assert response.json()["active_run_present"] is False
        assert response.json()["run_status_verified"] is False


def test_unrelated_and_standalone_jobs_do_not_attach_to_parent(backend, history, tmp_path, monkeypatch):
    project, package = history
    independent = writer(package, 100, "running")
    del independent["agent_run_id"]
    other = writer(package, 101, "running", kind="other_job")
    publish_writers(backend, history, tmp_path, [("", independent), ("", other)])
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/agents/runs?offset=55&limit=1", headers=selected(project, package))
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["candidate_writer"] is None
        assert response.json()["active_run_present"] is False
        assert response.json()["run_status_verified"] is True


@pytest.mark.parametrize("which,status", [("parent", "cancelling"), ("parent", "unknown"),
    ("parent", None), ("writer", "unknown"), ("writer", None), ("writer", ["running"])])
def test_unknown_status_fails_closed_across_full_history(backend, history, tmp_path, monkeypatch, which, status):
    project, package = history
    job = writer(package, 100, status if which == "writer" else "completed")
    publish_writers(backend, history, tmp_path, [("", job)],
                    parent_status=status if which == "parent" else "completed")
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/agents/runs?limit=1", headers=selected(project, package))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["active_run_present"] is (status == "cancelling")
        assert data["run_status_verified"] is (status == "cancelling")


@pytest.mark.parametrize("case", ["missing_row", "duplicate", "missing_parent", "bad_parent", "null_parent",
                                  "wrong_workflow", "wrong_scenario", "missing_kind", "wrong_id"])
def test_candidate_writer_incomplete_or_ambiguous_never_silent_idle(backend, history, tmp_path, monkeypatch, case):
    project, package = history
    job = writer(package, 100, "completed")
    if case == "missing_parent":
        job["agent_run_id"] = "f" * 32
    elif case == "bad_parent":
        job["agent_run_id"] = ["0" * 32]
    elif case == "null_parent":
        job["agent_run_id"] = None
    elif case == "wrong_workflow":
        job["workflow_id"] = "other"
    elif case == "wrong_scenario":
        job["scenario"] = "other"
    elif case == "missing_kind":
        del job["kind"]
    jobs = [("", job)]
    if case == "duplicate":
        jobs.append(("package-contexts/" + package, job))
    publish_writers(backend, history, tmp_path, jobs)
    generation = committed(fresh(backend, tmp_path / "latest-writers"))["migration_id"]
    family = next(rows for name, rows in backend.rows.items() if name.endswith(".jobs"))
    row = next(row for row in family if row["migration_id"] == generation and row["source_path"].endswith(job["id"] + ".json"))
    if case == "missing_row":
        family.remove(row)
    elif case == "wrong_id":
        row["source_key"] = '"wrong"'
    forbid_restore(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/agents/runs", headers=selected(project, package))
        assert response.status_code == 503, response.text