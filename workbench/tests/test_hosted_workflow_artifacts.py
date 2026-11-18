"""Pure POSTs exercise the real hosted OBO/Delta canonical projection, offline."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from zipfile import ZipFile

from databricks.sdk.errors import PermissionDenied
import pytest

from workbench.delta_runtime import DeltaRuntime
from workbench.tests.test_hosted import (
    backend, browser, headers, offline, create, HOST, TOKEN_A2, TOKEN_B,
)
from workbench.tests.test_hosted_registry import uploaded, fresh, committed
from workbench.tests.test_hosted_object_reads import full_index, selected

ENDPOINT = "/api/proposals/workflow-artifacts"
PATHS = [ENDPOINT, ENDPOINT + "/archive"]


@pytest.fixture
def artifact_scope(backend, uploaded, tmp_path, monkeypatch):
    project, package, index = full_index(backend, uploaded, tmp_path / "canonical")
    workflow = index.kinds["workflow"][0]["id"]
    calls = []
    texts = {"nested/cafe.txt": "\ufeffcafé\r\n未変更\n", "empty.txt": ""}

    def compiler(model, workflow_id, scenario):
        assert model == index.model
        calls.append((workflow_id, scenario))
        return {"artifacts": texts, "ready_for_execution": False}

    monkeypatch.setattr("workbench.workflow_artifacts.compile_workflow_artifacts", compiler)
    return project, package, index, {"workflow_id": workflow, "scenario": "bronze", "consent": True}, calls, texts


def forbid_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure artifact POST must not restore, parse XML, install models or publish")
    for method in ("restore", "restore_audit", "flush", "flush_audit", "_records", "_database", "_verify_source"):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)
    monkeypatch.setattr("workbench.app.analyze_package", forbidden)
    monkeypatch.setattr("workbench.understanding.analyze_package", forbidden)
    monkeypatch.setattr("workbench.agents.AgentRuntime.run", forbidden)


@pytest.mark.parametrize("endpoint", PATHS)
def test_pure_posts_overlap_active_write_without_restore_or_any_publication(
        backend, artifact_scope, monkeypatch, endpoint):
    project, package, index, body, calls, texts = artifact_scope
    rows, objects = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        dispatcher = client.app.routes[-1].app
        owner = sha256((HOST + "\n" + "alice-id").encode()).hexdigest()
        queue = {"lock": object(), "requests": 1, "active": {"operation_id": "a" * 32}}
        dispatcher.user_queues[owner] = queue
        # All active-user slots are occupied, including this actor. A read for
        # this same actor must share its slot, never acquire another or a lock.
        while dispatcher.capacity.acquire(blocking=False):
            pass
        dispatcher.users[owner] = 1
        try:
            responses = [client.post(endpoint, headers=selected(project, package), json=body) for _ in range(2)]
            assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
            assert responses[0].content == responses[1].content
            assert responses[0].headers["cache-control"] == "no-store"
            if endpoint.endswith("/archive"):
                assert responses[0].headers["content-disposition"] == 'attachment; filename="workflow_artifacts_bronze.zip"'
                with ZipFile(io.BytesIO(responses[0].content)) as archive:
                    assert archive.namelist() == sorted(texts)
                    assert {name: archive.read(name) for name in archive.namelist()} == {
                        name: text.encode("utf-8") for name, text in texts.items()}
            else:
                result = responses[0].json()
                assert result["package_hash"] == package and result["artifacts"] == texts
                assert result["execution_allowed"] is result["deployment_allowed"] is result["approved"] is False
            assert dispatcher.users[owner] == 1 and queue["requests"] == 1
            other_actor = client.post(endpoint, headers=selected(project, package, TOKEN_B), json=body)
            assert other_actor.status_code == 429
            # No broad read-only POST exception, even for adjacent pure-looking routes.
            for path in ("/api/projects", "/api/proposals/sql-drafts", ENDPOINT + "/other"):
                conflict = client.post(path, headers=selected(project, package), json=body)
                assert conflict.status_code == 409 and conflict.json()["active_write"] == queue["active"]
            assert queue["requests"] == 1
        finally:
            dispatcher.users.pop(owner)
            dispatcher.user_queues.pop(owner)
    assert calls == [(body["workflow_id"], "bronze")] * 2
    assert backend.rows == rows and backend.objects == objects
    statements = [sql for _, action, sql in backend.calls if action == "sql"]
    assert not any(sql.startswith(("CREATE", "MERGE", "INSERT", "UPDATE", "DELETE")) for sql in statements)
    assert not any(f".`{table}`" in sql for sql in statements for table in (
        "audit_events", "jobs", "agent_runs", "model_routes", "designs", "loop_attempts"))
    downloads = [path for _, action, path in backend.calls if action == "download"]
    # Fresh authority/committed manifest per POST, one verified immutable graph
    # hydration shared across the two compilations. Do not merely relax a budget:
    # prove which body was reused and retain all publication/actor assertions.
    canonical = (index.directory / 'understanding.json.gz').read_bytes()
    assert sum(backend.objects[path] == canonical for path in downloads) == 1
    assert len(downloads) == 3  # Two fresh generation manifests plus one gzip.
    assert all(token == TOKEN_A2 for token, *_ in backend.calls)


@pytest.mark.parametrize("endpoint", PATHS)
def test_strict_body_csrf_and_auth_are_checked_before_compile_even_while_writer_active(
        backend, artifact_scope, monkeypatch, endpoint):
    project, package, index, body, calls, _ = artifact_scope
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        dispatcher = client.app.routes[-1].app
        owner = sha256((HOST + "\n" + "alice-id").encode()).hexdigest()
        queue = {"lock": object(), "requests": 1, "active": None}
        dispatcher.user_queues[owner] = queue
        try:
            for changes, status in [({"consent": False}, 403), ({"consent": "true"}, 422),
                    ({"consent": 1}, 422), ({"scenario": "original"}, 422),
                    ({"workflow_id": "../source.xml"}, 422), ({"approved": True}, 422)]:
                assert client.post(endpoint, headers=selected(project, package), json={**body, **changes}).status_code == status
            for raw in (b"{", b"null", b"[]", b"{}", b""):
                assert client.post(endpoint, headers=selected(project, package), content=raw).status_code == 422
            assert client.post(endpoint, headers=selected(project, package), content=b"x" * 65537).status_code == 413
            assert client.post(endpoint, headers={**selected(project, package), "content-type": "text/plain"},
                               content=json.dumps(body)).status_code == 422
            for extra in ({"X-Workbench-Request": "0"}, {"Origin": "https://evil.example.com"},
                          {"Sec-Fetch-Site": "cross-site"}):
                assert client.post(endpoint, headers={**selected(project, package), **extra}, json=body).status_code == 403
            no_csrf = {k: v for k, v in selected(project, package).items() if k.lower() != "x-workbench-request"}
            assert client.post(endpoint, headers=no_csrf, json=body).status_code == 403
            assert client.post(endpoint, headers={"X-Workbench-Request": "1"}, json=body).status_code == 401
            assert not calls and queue["requests"] == 1
        finally:
            dispatcher.user_queues.pop(owner)


@pytest.mark.parametrize("endpoint", PATHS)
def test_canonical_workflow_kind_and_compiler_errors_match_local_api(backend, artifact_scope, monkeypatch, endpoint):
    project, package, index, body, calls, _ = artifact_scope
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        for identifier in ("0" * 64, index.kinds["mapping"][0]["id"]):
            response = client.post(endpoint, headers=selected(project, package), json={**body, "workflow_id": identifier})
            assert response.status_code == 404 and "Unknown canonical workflow ID" in response.json()["detail"]
        assert not calls
        def unsupported(*args, **kwargs):
            raise ValueError("Unsupported or changed scoped semantics; review required")
        monkeypatch.setattr("workbench.workflow_artifacts.compile_workflow_artifacts", unsupported)
        response = client.post(endpoint, headers=selected(project, package), json=body)
        assert response.status_code == 422 and "Unsupported or changed scoped semantics" in response.json()["detail"]


@pytest.mark.parametrize("case", ["user", "project", "package", "missing_project", "missing_package", "denied", "corrupt"])
def test_membership_authorization_and_canonical_integrity_are_fresh(backend, artifact_scope, tmp_path, monkeypatch, case):
    project, package, index, body, calls, _ = artifact_scope
    with browser(backend) as client:
        assert client.post(ENDPOINT, headers=selected(project, package), json=body).status_code == 200
        if case == "user":
            create(client, "Bob", TOKEN_B)
        if case == "corrupt":
            runtime = fresh(backend, tmp_path / "corrupt")
            generation = committed(runtime)["migration_id"]
            row = next(r for name, rows in backend.rows.items() if name.endswith(".artifact_manifest") for r in rows
                       if r["migration_id"] == generation and r["source_path"].endswith("understanding.json.gz"))
            backend.objects[row["artifact_path"]] = b"corrupt"
            # Test cold materialization integrity. A verified warm immutable
            # graph no longer downloads these bytes again; authority remains
            # fresh regardless of graph reuse, as the denied/user cases prove.
            from workbench.hosted_object_reads import _REVIEW_INDEX_CACHE
            _REVIEW_INDEX_CACHE.clear()
        if case == "denied":
            original = backend.client
            def revoked(token):
                result = original(token)
                def denied(**kwargs):
                    raise PermissionDenied("sensitive revoked privilege " + token)
                result.statement_execution.execute_statement = denied
                return result
            monkeypatch.setattr(backend, "client", revoked)
        scope = selected("0" * 32 if case == "project" else project,
                         "0" * 64 if case == "package" else package, TOKEN_B if case == "user" else TOKEN_A2)
        if case.startswith("missing_"):
            scope.pop("X-Workbench-" + ("Project" if case == "missing_project" else "Package"))
        calls.clear()
        forbid_runtime(monkeypatch)
        before = deepcopy(backend.rows), deepcopy(backend.objects)
        for endpoint in PATHS:
            response = client.post(endpoint, headers=scope, json=body)
            expected = (404 if case in {"user", "project"} else 403 if case == "package"
                        else 400 if case == "missing_project" else 409 if case == "missing_package" else 503)
            assert response.status_code == expected, response.text
            assert TOKEN_A2 not in response.text and "sensitive revoked" not in response.text
        assert not calls
        assert (backend.rows, backend.objects) == before


def test_no_http_get_or_query_can_enable_internal_artifact_projection(backend, artifact_scope, monkeypatch):
    project, package, index, body, calls, _ = artifact_scope
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get("/api/object/" + body["workflow_id"],
                              params={"_projector": "workflow-artifacts", "workflow_id": "0" * 64},
                              headers=selected(project, package))
        assert response.status_code == 200 and "artifacts" not in response.json()
        assert not calls
    # Artifact paths were deliberately not added to the public GET matcher.
    from workbench.hosted_object_reads import ROUTE
    assert all(ROUTE.fullmatch(path) is None for path in PATHS)


@pytest.mark.parametrize("case,status", [("streamed_overflow", 413), ("disconnected", 400)])
def test_streamed_body_limit_and_disconnect_before_any_remote_access(backend, monkeypatch, case, status):
    import asyncio
    from workbench.hosted import HostedConfig, HostedDispatcher
    from workbench.tests.test_hosted import ORIGIN
    forbid_runtime(monkeypatch)

    async def exercise():
        dispatcher = HostedDispatcher(HostedConfig(HOST, ORIGIN, warehouse="warehouse"), backend.resolve)
        messages = ([{"type": "http.request", "body": b"x" * 40000, "more_body": True},
                     {"type": "http.request", "body": b"x" * 30000, "more_body": False}]
                    if case == "streamed_overflow" else [{"type": "http.disconnect"}])
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": ENDPOINT, "query_string": b"",
                 "headers": [(key.lower().encode(), value.encode()) for key, value in
                             {**headers(TOKEN_A2), "host": "workbench.example.com"}.items()]}
        try:
            await dispatcher(scope, receive, send)
            assert next(m["status"] for m in sent if m["type"] == "http.response.start") == status
            assert not dispatcher.users and not dispatcher.user_queues
        finally:
            dispatcher.operations.close()

    asyncio.run(exercise())
    assert not backend.calls