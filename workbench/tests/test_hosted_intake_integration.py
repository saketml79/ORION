"""Offline HTTP integration of hosted chunks, operation workers and Delta commits.

Only the remote services are faked. The dispatcher, worker executor, event loop,
project staging, XML parser and generation commit boundary are production code.
All synchronization uses bounded events/futures, never sleep-based polling.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Event, get_ident
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from starlette.concurrency import run_in_threadpool

from workbench.hosted import HostedConfig, HostedDispatcher, create_hosted_app
from workbench.hosted_operations import API as OPERATIONS
from workbench.hosted_uploads import API as UPLOADS, CHUNK_BYTES
from workbench.tests.test_hosted import (
    Backend, HOST, ORIGIN, TOKEN_A, TOKEN_A2, TOKEN_B, XML, create, headers,
    offline,  # noqa: F401: forbid real SDK traffic and CLI fallback
)


@pytest.fixture
def intake(monkeypatch):
    backend = Backend()
    app = create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="warehouse"),
                            identity_resolver=backend.resolve)
    dispatcher = next(route.app for route in app.routes
                      if isinstance(getattr(route, "app", None), HostedDispatcher))
    terminal = {}
    append = dispatcher.operations._append

    def observed_append(store, operation, history, payload):
        append(store, operation, history, payload)
        # Signal only after the actual durable append and verified readback.
        if payload["status"] in {"completed", "failed", "authentication_required"}:
            terminal[operation].set()

    monkeypatch.setattr(dispatcher.operations, "_append", observed_append)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        estate = SimpleNamespace(backend=backend, client=client, dispatcher=dispatcher,
                                 terminal=terminal, releases=[])
        try:
            yield estate
        finally:
            for release in estate.releases:
                release.set()
            if backend.release_upload is not None:
                backend.release_upload.set()
            # The TestClient portal must remain alive while workers re-enter it.
            # Closing the independent executor on this thread cannot block its loop.
            dispatcher.operations.close(wait=True)


def manifest(intake, project, raw):
    upload_id = uuid4().hex
    response = intake.client.post(UPLOADS, headers=headers(), json={
        "id": upload_id, "project_id": project, "name": "source.xml",
        "size_bytes": len(raw),
    })
    assert response.status_code == 201, response.text
    assert response.json()["chunk_bytes"] == CHUNK_BYTES
    return upload_id


def chunks(intake, upload_id, raw):
    for index, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
        chunk = raw[offset:offset + CHUNK_BYTES]
        digest = sha256(chunk).hexdigest()
        response = intake.client.put(f"{UPLOADS}/{upload_id}/chunks/{index}",
            headers=headers(**{"Content-Type": "application/octet-stream",
                               "X-Chunk-SHA256": digest}), content=chunk)
        assert response.status_code == 200, response.text
        assert response.json() == {"index": index, "size_bytes": len(chunk), "sha256": digest}
        paths = [path for path in intake.backend.objects
                 if path.endswith(f"/upload-chunks/{upload_id}/{index}.bin")]
        assert len(paths) == 1
        assert intake.backend.objects[paths[0]] == chunk


def submit(intake, path, *, context=None):
    operation = uuid4().hex
    intake.terminal[operation] = Event()
    response = intake.client.post(OPERATIONS, headers=headers(**(context or {})), json={
        "id": operation, "method": "POST", "path": path, "body": {},
    })
    assert response.status_code == 202, response.text
    assert response.json()["operation_id"] == operation
    assert response.json()["status"] == "queued"
    return operation


def completed(intake, operation):
    assert intake.terminal[operation].wait(10), "Worker did not durably reach a terminal state"
    response = intake.client.get(f"{OPERATIONS}/{operation}", headers=headers(TOKEN_A2))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert result["progress"]["stage"] == "committed"
    assert result["status_code"] == 202
    return result["result"]


def assert_no_credentials(intake):
    # Fake call records intentionally retain the token to check OBO identity;
    # actual persisted objects and Delta envelopes must never contain it.
    durable = json.dumps(intake.backend.rows).encode()
    for token in (TOKEN_A, TOKEN_A2, TOKEN_B):
        assert token.encode() not in durable
        assert all(token.encode() not in data for data in intake.backend.objects.values())
    assert not any(path.endswith("/HEAD.json") for path in intake.backend.objects)


def test_http_chunk_finalize_analyze_commits_and_preserves_context(intake, monkeypatch):
    project = create(intake.client)
    # A parser-ignored comment crosses the real 4MiB boundary without a huge graph.
    raw = b"<!--" + b"x" * (CHUNK_BYTES + 17) + b"-->\n" + XML
    digest = sha256(raw).hexdigest()
    upload_id = manifest(intake, project, raw)
    assert intake.client.post(f"{UPLOADS}/{upload_id}/complete", headers=headers()).status_code == 409
    chunks(intake, upload_id, raw)
    ready = intake.client.post(f"{UPLOADS}/{upload_id}/complete", headers=headers())
    assert ready.status_code == 200, ready.text
    assert ready.json()["ready_for_finalize"] is True
    assert ready.json()["durable_bytes"] == len(raw)

    entered, release = Event(), Event()
    intake.releases.append(release)
    execute = intake.dispatcher.execute_operation
    forwarded, inner_context, loop_threads = [], [], []
    dispatch = HostedDispatcher.__call__

    async def observe_dispatch(self, scope, receive, send):
        if scope.get("workbench.operation_execution"):
            inner_context.append(dict(scope["headers"]))
            loop_threads.append(get_ident())
        return await dispatch(self, scope, receive, send)

    async def blocked_execute(scope, method, path, query, action_headers, body, progress_reporter=None):
        forwarded.append(dict(action_headers))
        entered.set()
        # Waiting in a thread leaves the server loop free to service GET status.
        assert await run_in_threadpool(release.wait, 10)
        return await execute(scope, method, path, query, action_headers, body, progress_reporter)

    monkeypatch.setattr(HostedDispatcher, "__call__", observe_dispatch)
    monkeypatch.setattr(intake.dispatcher, "execute_operation", blocked_execute)
    context = {"X-Workbench-Project": project, "X-Workbench-Package": digest}
    operation = submit(intake, f"{UPLOADS}/{upload_id}/finalize", context=context)
    try:
        assert entered.wait(10)
        assert not intake.terminal[operation].is_set()
        # HTTP acceptance and status must not wait for the blocked worker.
        with ThreadPoolExecutor(max_workers=1) as pool:
            status = pool.submit(intake.client.get, f"{OPERATIONS}/{operation}",
                                 headers=headers(TOKEN_A2)).result(timeout=3)
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "running"
    finally:
        release.set()
    staged = completed(intake, operation)
    assert staged["status"] == "staged" and staged["project_id"] == project
    assert staged["items"][0]["sha256"] == digest
    upload = intake.client.get(f"{UPLOADS}/{upload_id}", headers=headers(TOKEN_A2))
    assert upload.status_code == 200, upload.text
    assert upload.json()["status"] == "committed"
    detail = intake.client.get(f"/api/projects/{project}", headers=headers()).json()
    assert detail["packages"][0]["status"] == "staged"

    operation = submit(intake, f"/api/projects/{project}/analyze", context=context)
    completed(intake, operation)
    detail = intake.client.get(f"/api/projects/{project}", headers=headers(TOKEN_A2))
    assert detail.status_code == 200, detail.text
    assert detail.json()["ready_package_count"] == 1
    package, = detail.json()["packages"]
    assert package["status"] == "completed" and package["sha256"] == digest
    overview = intake.client.get("/api/overview", headers=headers(TOKEN_A2, **context))
    assert overview.status_code == 200, overview.text
    assert overview.json()["ready"] is True
    assert len(forwarded) == len(inner_context) == 2
    for action_headers, reentered in zip(forwarded, inner_context):
        lowered = {k.lower(): v for k, v in action_headers.items()}
        assert lowered["x-workbench-project"] == project
        assert lowered["x-workbench-package"] == digest
        assert "x-forwarded-access-token" not in lowered and "authorization" not in lowered
        assert reentered[b"x-workbench-project"] == project.encode()
        assert reentered[b"x-workbench-package"] == digest.encode()
    assert all(thread != get_ident() for thread in loop_threads)
    source_paths = [path for path in intake.backend.objects
                    if path.endswith("/uploads/" + digest + ".xml")]
    assert len(source_paths) == 1
    assert sha256(intake.backend.objects[source_paths[0]]).hexdigest() == digest
    assert not any("hosted-upload-" in row["source_path"]
                   for rows in intake.backend.rows.values() for row in rows)
    assert_no_credentials(intake)


@pytest.mark.parametrize("size", [65537, CHUNK_BYTES, CHUNK_BYTES + 1])
def test_http_chunk_body_limits_and_exact_remainder(intake, size):
    project = create(intake.client)
    raw = b"x" * size
    upload_id = manifest(intake, project, raw)
    response = intake.client.put(f"{UPLOADS}/{upload_id}/chunks/0", headers=headers(),
                                 content=b"x" * (CHUNK_BYTES + 1))
    assert response.status_code == 413, response.text
    assert not any(f"/upload-chunks/{upload_id}/" in path for path in intake.backend.objects)
    if size > CHUNK_BYTES:
        response = intake.client.put(f"{UPLOADS}/{upload_id}/chunks/1", headers=headers(), content=b"xx")
        assert response.status_code == 400, response.text
    chunks(intake, upload_id, raw)
    response = intake.client.post(f"{UPLOADS}/{upload_id}/complete", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["ready_for_finalize"] is True
    assert response.json()["durable_bytes"] == size
    assert len(response.json()["chunks"]) == (size + CHUNK_BYTES - 1) // CHUNK_BYTES
    assert_no_credentials(intake)


def test_same_user_direct_write_returns_conflict_and_intake_status_bypasses_lock(intake, monkeypatch):
    project = create(intake.client)
    upload_id = manifest(intake, project, XML)
    chunks(intake, upload_id, XML)
    response = intake.client.post(f"{UPLOADS}/{upload_id}/complete", headers=headers())
    assert response.status_code == 200, response.text
    backend = intake.backend
    backend.block_upload, backend.release_upload = Event(), Event()
    operation = submit(intake, f"{UPLOADS}/{upload_id}/finalize")
    try:
        assert backend.block_upload.wait(10), "Finalize did not enter the real commit path"
        entry, = intake.dispatcher.user_queues.values()
        assert entry["lock"].locked()
        with ThreadPoolExecutor(max_workers=3) as pool:
            queued = pool.submit(intake.client.post, "/api/projects", headers=headers(TOKEN_A2),
                                 json={"name": "Queued same-user project"})
            try:
                result = queued.result(timeout=3)
                assert result.status_code == 409, result.text
                assert result.json()["change_applied"] is False
                assert result.json()["active_write"]["operation_id"] == operation
                for path in (f"{OPERATIONS}/{operation}", f"{UPLOADS}/{upload_id}"):
                    status = pool.submit(intake.client.get, path,
                                         headers=headers(TOKEN_A2)).result(timeout=3)
                    assert status.status_code == 200, status.text
                assert not intake.terminal[operation].is_set()
            finally:
                backend.release_upload.set()
        completed(intake, operation)
        assert not intake.dispatcher.user_queues
        create(intake.client, "Explicit next write after completion", TOKEN_A2)
    finally:
        backend.release_upload.set()
    detail = intake.client.get(f"/api/projects/{project}", headers=headers())
    assert detail.status_code == 200, detail.text
    assert detail.json()["packages"][0]["status"] == "staged"
    assert_no_credentials(intake)