"""Selected hosted overview projection without execution-state restoration."""
from copy import deepcopy
from pathlib import Path

from starlette.testclient import TestClient

from workbench.agents import AgentRuntime
from workbench.app import create_app
from workbench.delta_runtime import DeltaRuntime, _artifact_cache_clear_user
from workbench.hosted_object_reads import ROUTE, _REVIEW_INDEX_CACHE, _REVIEW_INDEX_LOCK
from workbench.tests.test_hosted import TOKEN_A2, TOKEN_B, backend, browser, create, headers, offline
from workbench.tests.test_hosted_registry import committed, fresh, uploaded


_OVERVIEW_KEYS = {
    "ready", "status", "job_id", "agent", "capabilities", "startup_error",
    "capability_inventory", "package", "counts", "summary", "coverage",
    "diagnostics_count", "diagnostics", "diagnostics_truncated", "workflows",
    "generation_allowed", "deployment_allowed",
}


def _clear_read_caches():
    import workbench.delta_runtime as runtime

    with runtime._VERIFIED_ARTIFACT_CACHE_LOCK:
        runtime._VERIFIED_ARTIFACT_CACHE.clear()
        runtime._VERIFIED_ARTIFACT_CACHE_BYTES = 0
    with _REVIEW_INDEX_LOCK:
        _REVIEW_INDEX_CACHE.clear()
    runtime._WORKING_SNAPSHOT_CACHE.clear()
    _artifact_cache_clear_user("https://workspace.example.com", "alice-id")


def _selected(project, package, token=TOKEN_A2):
    return headers(token, **{
        "X-Workbench-Project": project,
        "X-Workbench-Package": package,
    })


def _loaded_package(backend, uploaded, tmp_path):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    runtime = fresh(backend, tmp_path / "seed")
    runtime.restore(initialize=False)
    directory = runtime.local / f"projects/{project}/package-contexts/{package}/packages/{package}"
    from workbench.app import PackageIndex

    return project, package, PackageIndex.load(directory)


def _without_assessment_time(value):
    result = deepcopy(value)
    result["capability_inventory"].pop("assessed_at")
    return result


def test_selected_overview_matches_local_contract_without_restore_or_unrelated_reads(
        backend, uploaded, tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_TRUST_DATABRICKS_PROXY", raising=False)
    _clear_read_caches()
    project, package, index = _loaded_package(backend, uploaded, tmp_path)
    _clear_read_caches()
    original_rows, original_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("Selected overview must not restore execution state or invoke inference")

    for name in ("restore", "restore_audit", "flush", "flush_audit", "_records", "_database"):
        monkeypatch.setattr(DeltaRuntime, name, forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)
    monkeypatch.setattr(AgentRuntime, "run", forbidden)

    local = create_app(tmp_path / "local", tmp_path / "not-used.xml",
                       AgentRuntime(tmp_path / "local" / "agents"))
    with TestClient(local, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as local_client:
        local.state.service.active = index
        local.state.hosted_request_authenticated = True
        expected = local_client.get("/api/overview").json()

    with browser(backend) as client:
        cold = client.get("/api/overview", headers=_selected(project, package))
        warm = client.get("/api/overview", headers=_selected(project, package))
    assert cold.status_code == warm.status_code == 200, cold.text or warm.text
    actual = cold.json()
    assert set(actual) == _OVERVIEW_KEYS
    assert actual["ready"] is True and actual["status"] == "ready"
    assert actual["job_id"] is None and actual["startup_error"] is None
    assert _without_assessment_time(actual) == _without_assessment_time(expected)
    assert actual["agent"]["model_configured"] is False
    assert actual["agent"]["available"] is False
    assert actual["capabilities"]["agents"]["available"] is False
    assert actual["generation_allowed"] is False and actual["deployment_allowed"] is False

    downloads = [path for _, action, path in backend.calls if action == "download"]
    downloaded = [backend.objects[path] for path in downloads]
    canonical = (index.directory / "understanding.json.gz").read_bytes()
    assert downloaded.count(canonical) == 1
    assert downloaded.count((index.directory / "source.xml").read_bytes()) == 1
    assert downloaded.count((index.directory / "english.md").read_bytes()) == 1
    assert backend.rows == original_rows and backend.objects == original_objects
    sql = [statement for _, action, statement in backend.calls if action == "sql"]
    assert not any(any(f".{table}`" in statement for table in (
        "agent_runs", "jobs", "reviews", "checkpoints", "audit_events",
        "model_routes", "loop_attempts")) for statement in sql)


def test_selected_overview_rechecks_membership_on_warm_cache(backend, uploaded, tmp_path):
    _clear_read_caches()
    project, package, _ = _loaded_package(backend, uploaded, tmp_path)
    _clear_read_caches()
    with browser(backend) as client:
        first = client.get("/api/overview", headers=_selected(project, package))
        assert first.status_code == 200, first.text
        create(client, "Overview other owner", TOKEN_B)
        cross_user = client.get("/api/overview", headers=_selected(project, package, TOKEN_B))
        wrong_package = client.get("/api/overview", headers=_selected(project, "0" * 64))
    assert cross_user.status_code == 404
    assert wrong_package.status_code == 403


def test_selected_overview_rejects_corrupt_canonical_artifact_on_cold_cache(backend, uploaded, tmp_path):
    _clear_read_caches()
    project, package, _ = _loaded_package(backend, uploaded, tmp_path)
    runtime = fresh(backend, tmp_path / "artifact-row")
    generation = committed(runtime)["migration_id"]
    row = next(record for name, records in backend.rows.items() if name.endswith(".artifact_manifest")
               for record in records if record["migration_id"] == generation
               and record["source_path"].endswith("understanding.json.gz"))
    backend.objects[row["artifact_path"]] = b"corrupt canonical package bytes"
    _clear_read_caches()
    with browser(backend) as client:
        response = client.get("/api/overview", headers=_selected(project, package))
    assert response.status_code == 503
    assert "corrupt canonical package bytes" not in response.text


def test_selected_overview_rejects_corrupt_source_on_cold_cache(backend, uploaded, tmp_path):
    _clear_read_caches()
    project, package, _ = _loaded_package(backend, uploaded, tmp_path)
    runtime = fresh(backend, tmp_path / "source-row")
    generation = committed(runtime)["migration_id"]
    row = next(record for name, records in backend.rows.items() if name.endswith(".artifact_manifest")
               for record in records if record["migration_id"] == generation
               and record["source_path"].endswith("/source.xml"))
    backend.objects[row["artifact_path"]] = b"corrupt source bytes"
    _clear_read_caches()
    with browser(backend) as client:
        response = client.get("/api/overview", headers=_selected(project, package))
    assert response.status_code == 503
    assert "corrupt source bytes" not in response.text
    assert not _REVIEW_INDEX_CACHE


def test_overview_fast_route_requires_both_explicit_scope_headers():
    assert ROUTE.fullmatch("/api/overview")
    assert not ROUTE.fullmatch("/api/overview/")
    assert not ROUTE.fullmatch("/api/overview?project_id=example")


def test_unselected_overview_keeps_legacy_restore_route(backend, monkeypatch):
    calls = []
    restore = DeltaRuntime.restore

    def tracked(runtime, *args, **kwargs):
        calls.append(True)
        return restore(runtime, *args, **kwargs)

    with browser(backend) as client:
        create(client, "Legacy overview project", TOKEN_A2)
        monkeypatch.setattr(DeltaRuntime, "restore", tracked)
        response = client.get("/api/overview", headers=headers(TOKEN_A2))
    assert response.status_code == 200, response.text
    assert calls


def test_overview_is_not_wrapped_in_durable_operation_envelope():
    from workbench.hosted_operations import HostedOperations

    try:
        HostedOperations._request({
            "id": "a" * 32, "method": "GET", "path": "/api/overview", "body": {},
        }, ())
    except ValueError:
        pass
    else:
        raise AssertionError("Overview must remain a direct authenticated GET")


def test_package_warm_operation_is_scoped_read_only_and_reports_cache_admission(
        backend, uploaded, tmp_path, monkeypatch):
    import workbench.delta_runtime as runtime_module
    import time
    from uuid import uuid4

    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    _clear_read_caches()
    monkeypatch.setattr(runtime_module, "_container_memory_state", lambda: (1024, 6 * 1024 ** 3))
    restores = []
    original_restore = DeltaRuntime.restore

    def tracked_restore(instance, *args, **kwargs):
        restores.append(kwargs.copy())
        return original_restore(instance, *args, **kwargs)

    monkeypatch.setattr(DeltaRuntime, "restore", tracked_restore)
    with browser(backend) as client:
        operation = uuid4().hex
        accepted = client.post("/api/hosted/operations", headers=_selected(project, package), json={
            "id": operation, "method": "GET", "path": "/api/package/warm", "query": "", "body": {},
        })
        assert accepted.status_code == 202, accepted.text
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(f"/api/hosted/operations/{operation}",
                                  headers=_selected(project, package))
            assert response.status_code == 200, response.text
            result = response.json()
            if result["status"] == "completed":
                assert result["result"]["ready"] is True
                assert result["result"]["cache_ready"] is True
                assert result["result"]["cache_reason"] == "ready"
                assert result["result"]["generation_allowed"] is False
                assert result["result"]["deployment_allowed"] is False
                break
            assert result["status"] in {"queued", "running"}, result
        else:
            raise AssertionError("Selected package warm operation did not finish")
        wrong_scope = client.post("/api/hosted/operations", headers=_selected(project, "0" * 64), json={
            "id": uuid4().hex, "method": "GET", "path": "/api/package/warm", "query": "", "body": {},
        })
        assert wrong_scope.status_code == 202, wrong_scope.text
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rejected = client.get(f"/api/hosted/operations/{wrong_scope.json()['operation_id']}",
                                  headers=_selected(project, "0" * 64))
            assert rejected.status_code == 200, rejected.text
            data = rejected.json()
            if data["status"] in {"failed", "completed"}:
                break
        else:
            raise AssertionError("Invalid package warm request did not reach a terminal receipt")
        assert data["status"] == "failed"
    assert len(restores) == 1
    assert restores[0]["initialize"] is False and restores[0]["materialize"] is False


def test_frontend_overview_uses_direct_authenticated_no_store_fetch():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    hosted_reads = source.split("function hostedBackgroundReadPath(path) {", 1)[1].split(
        "async function request(path, options = {}) {", 1)[0]
    direct_request = source.split("async function request(path, options = {}) {", 1)[1].split(
        "const api =", 1)[0]
    assert "'/api/overview'" not in hosted_reads
    assert "credentials: 'same-origin', cache: 'no-store'" in direct_request
    assert "diagnostic = overviewRead && state.hosted ? startLoadingDiagnostic(controller) : null" in direct_request
