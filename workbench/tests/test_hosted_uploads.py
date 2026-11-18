"""Offline chunk intake contracts using the real Delta envelopes and registry."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import uuid4

from databricks.sdk.errors import Unauthenticated
import pytest

from workbench import delta_state as ds
from workbench import hosted_uploads as hu
from workbench.delta_runtime import DeltaRuntime, RequestStateStore
from workbench.hosted_storage import provision
from workbench.tests.test_hosted import (
    Backend, HOST, TOKEN_A, TOKEN_A2, TOKEN_B, browser, create,
    offline,  # noqa: F401: forbid live network and CLI fallback
)


@pytest.fixture
def estate(monkeypatch):
    backend = Backend()
    with browser(backend) as client:
        project = create(client)
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    # Every intake/status/helper path must avoid full execution-cache hydration.
    monkeypatch.setattr(DeltaRuntime, "restore", lambda *a, **k: pytest.fail("No state hydration"))
    return SimpleNamespace(backend=backend, project=project, identity=identity, remote=remote,
                           service=hu.HostedUploads("warehouse"))


def small(estate, *, chunk_bytes=7, max_file_bytes=2800, max_batch_bytes=5600):
    estate.service = hu.HostedUploads("warehouse", chunk_bytes=chunk_bytes,
                                    max_file_bytes=max_file_bytes, max_batch_bytes=max_batch_bytes)


def call(estate, method, suffix="", body=b"", *, service=None, identity=None, remote=None,
         headers=None, query=""):
    return (service or estate.service).handle(
        identity or estate.identity, remote or estate.remote, method, hu.API + suffix, query,
        headers or {"x-forwarded-access-token": TOKEN_A}, body,
        lambda *a: pytest.fail("The upload handler must never execute work"))


def start(estate, size, upload_id=None, **changes):
    body = {"id": upload_id or uuid4().hex, "project_id": estate.project,
            "name": "source.xml", "size_bytes": size, **changes}
    return body, call(estate, "POST", body=body)


def put(estate, upload_id, index, data, **kwargs):
    return call(estate, "PUT", f"/{upload_id}/chunks/{index}", data, **kwargs)


def journal(estate, upload_id):
    return [r for name, rows in estate.backend.rows.items() if name.endswith(".jobs")
            for r in rows if r["context_id"] == "hosted-upload:" + upload_id]


def upload(estate, raw):
    body, (status, _) = start(estate, len(raw))
    assert status == 201
    for index, offset in enumerate(range(0, len(raw), estate.service.chunk_bytes)):
        data = raw[offset:offset + estate.service.chunk_bytes]
        assert put(estate, body["id"], index, data)[0] == 200
    return body["id"]


def test_large_declared_file_uses_metadata_only_and_exact_scope(estate):
    # >80MiB declaration, no source buffer or giant allocation required.
    size = 83 * 1024**2 + 19
    estate.backend.calls.clear()
    before = deepcopy(estate.backend.objects)
    body, (status, public) = start(estate, size)
    assert status == 201
    assert public["size_bytes"] == public["total_bytes"] == size
    assert public["chunk_bytes"] == 4 * 1024**2 and public["total_chunks"] == 21
    assert public["status"] == "uploading" and public["durable_bytes"] == 0
    assert public["chunks"] == [] and not public["ready_for_finalize"]
    assert estate.backend.objects == before
    rows = journal(estate, body["id"])
    assert len(rows) == 1
    row = rows[0]
    assert {k: row[k] for k in ("project_id", "package_hash", "context_id", "migration_id",
                                "source_path", "source_table", "source_key")} == {
        "project_id": "__uploads__", "package_hash": "__none__",
        "context_id": "hosted-upload:" + body["id"], "migration_id": "hosted-upload:" + body["id"],
        "source_path": "hosted-uploads", "source_table": "uploads", "source_key": "manifest"}
    assert len(row["payload"].encode()) < 1024
    assert not any(action == "upload" for _, action, _ in estate.backend.calls)
    assert not any(action == "sql" and ("CREATE TABLE" in sql or
                   (sql.startswith("MERGE") and ".`jobs`" not in sql))
                   for _, action, sql in estate.backend.calls)


def test_membership_missing_project_and_other_user_are_rejected(estate):
    body, response = start(estate, 10, project_id="0" * 32)
    assert response[0] == 404 and not journal(estate, body["id"])
    body, response = start(estate, 10)
    assert response[0] == 201
    bob = estate.backend.resolve(HOST, TOKEN_B, trusted_proxy=True, deployment_host=HOST)
    remote = provision(bob, "orion_dev_")
    RequestStateStore(bob.client, "warehouse", remote.split("/")[2], "workbench", bob.user_id).ensure_tables()
    assert call(estate, "POST", body=body, identity=bob, remote=remote)[0] == 404
    assert call(estate, "GET", "/" + body["id"], identity=bob, remote=remote)[0] == 404
    assert put(estate, body["id"], 0, b"a" * 10, identity=bob, remote=remote)[0] == 404
    assert call(estate, "GET", "/" + body["id"], identity=bob)[0] == 503
    impostor = SimpleNamespace(mode="obo", host=HOST, user_id=estate.identity.user_id, client=bob.client)
    assert call(estate, "GET", "/" + body["id"], identity=impostor)[0] == 503


@pytest.mark.parametrize("name", ["../safe.xml", "a/b.xml", "C:\\bad.xml", "bad\x00.xml",
                                  "bad\n.xml", "", "x" * 201 + ".xml", "archive.zip", "source.txt"])
def test_refuse_unsafe_filename_without_writes(estate, name):
    before = deepcopy(estate.backend.rows)
    _, (status, _) = start(estate, 10, name=name)
    assert status in {400, 415}
    assert estate.backend.rows == before


@pytest.mark.parametrize("size", [True, False, 0, -1, 1.0, "12", None, 8 * 1024**3 + 1])
def test_strict_file_size(estate, size):
    _, response = start(estate, size)
    assert response[0] == (413 if type(size) is int and size > 8 * 1024**3 else 400)


@pytest.mark.parametrize("change", [{"id": "../x"}, {"project_id": "X" * 32},
                                    {"extra": 1}, {"name": TOKEN_A + ".xml"}])
def test_invalid_or_secret_manifest_is_not_persisted(estate, change):
    before = deepcopy(estate.backend.rows)
    _, response = start(estate, 1, **change)
    assert response[0] == 400 and estate.backend.rows == before


def test_start_json_is_bounded_strict_and_idempotent(estate):
    body, response = start(estate, 10)
    assert response[0] == 201
    before = deepcopy(estate.backend.rows)
    assert call(estate, "POST", body=json.dumps(body).encode())[0] == 200
    assert estate.backend.rows == before
    assert call(estate, "POST", body={**body, "size_bytes": 11})[0] == 409
    assert call(estate, "POST", body=b'{"id":"x","id":"y"}')[0] == 400
    assert call(estate, "POST", body=b" " * (hu.MAX_METADATA + 1))[0] == 400
    assert call(estate, "POST", body=b'{"size_bytes":NaN}')[0] == 400


def test_chunks_integrity_idempotency_exact_last_remainder_and_conflict(estate):
    small(estate)
    body, _ = start(estate, 10)
    uid = body["id"]
    data = b"<source"
    assert put(estate, uid, 0, data, headers={"X-Chunk-SHA256": "0" * 64})[0] == 400
    assert put(estate, uid, 0, data, headers={"X-Chunk-SHA256": sha256(data).hexdigest()}) == (
        200, {"index": 0, "size_bytes": 7, "sha256": sha256(data).hexdigest()})
    rows = deepcopy(journal(estate, uid))
    assert put(estate, uid, 0, data)[0] == 200
    assert journal(estate, uid) == rows
    assert put(estate, uid, 0, b"changed")[0] == 409
    assert put(estate, uid, 1, b"badsize")[0] == 400
    assert put(estate, uid, 1, b"x" * 8)[0] == 413
    assert put(estate, uid, 2, b"x")[0] == 400
    assert put(estate, uid, 1, b"/>")[0] == 400
    assert put(estate, uid, 1, b"/ >")[0] == 200
    status, public = call(estate, "GET", "/" + uid)
    assert status == 200 and public["status"] == "ready_for_finalize"
    assert public["durable_bytes"] == public["total_bytes"] == 10
    assert [c["index"] for c in public["chunks"]] == [0, 1]
    assert estate.backend.objects[estate.remote + "/upload-chunks/" + uid + "/0.bin"] == data


def test_default_chunk_bound_is_enforced(estate):
    body, _ = start(estate, hu.CHUNK_BYTES + 1)
    assert put(estate, body["id"], 0, b"x" * (hu.CHUNK_BYTES + 1))[0] == 413
    assert put(estate, body["id"], 0, b"x" * hu.CHUNK_BYTES)[0] == 200
    assert put(estate, body["id"], 1, b"x")[0] == 200


def test_orphan_volume_chunk_can_resume_only_when_exactly_matching(estate):
    small(estate)
    body, _ = start(estate, 3)
    path = estate.remote + "/upload-chunks/" + body["id"] + "/0.bin"
    estate.backend.objects[path] = b"abc"
    assert put(estate, body["id"], 0, b"xyz")[0] == 409
    assert len(journal(estate, body["id"])) == 1
    assert put(estate, body["id"], 0, b"abc")[0] == 200
    assert len(journal(estate, body["id"])) == 2


@pytest.mark.parametrize("fault", ["corrupt", "short", "long", "none"])
def test_readback_precedes_journal_acceptance(estate, monkeypatch, fault):
    small(estate)
    body, _ = start(estate, 3)
    client = estate.identity.client
    original = client.files.download

    def download(path):
        if "/upload-chunks/" in path:
            return SimpleNamespace(contents=None if fault == "none" else BytesIO(
                {"corrupt": b"xyz", "short": b"ab", "long": b"abcd"}.get(fault, b"")))
        return original(path)

    monkeypatch.setattr(client.files, "download", download)
    assert put(estate, body["id"], 0, b"abc")[0] == 409
    assert len(journal(estate, body["id"])) == 1
    status, public = call(estate, "GET", "/" + body["id"])
    assert status == 200 and public["durable_bytes"] == 0


def test_journal_write_must_be_read_back_and_can_resume_after_uncertain_write(estate, monkeypatch):
    small(estate)
    body, _ = start(estate, 3)
    original = RequestStateStore.upsert_records

    def dropped(store, table, records, **kwargs):
        if records[0]["source_key"].startswith("chunk:"):
            return 1
        return original(store, table, records, **kwargs)

    monkeypatch.setattr(RequestStateStore, "upsert_records", dropped)
    assert put(estate, body["id"], 0, b"abc")[0] == 503
    assert call(estate, "POST", "/" + body["id"] + "/complete")[0] == 409
    monkeypatch.setattr(RequestStateStore, "upsert_records", original)
    assert put(estate, body["id"], 0, b"abc")[0] == 200


def test_restart_refreshed_obo_and_missing_chunk_blocking(estate, tmp_path):
    small(estate)
    body, _ = start(estate, 10)
    uid = body["id"]
    assert put(estate, uid, 1, b"xyz")[0] == 200
    restarted = hu.HostedUploads("warehouse", max_file_bytes=2800, max_batch_bytes=5600, chunk_bytes=7)
    refreshed = estate.backend.resolve(HOST, TOKEN_A2, trusted_proxy=True, deployment_host=HOST)
    status, public = call(estate, "GET", "/" + uid, service=restarted, identity=refreshed)
    assert status == 200 and public["durable_bytes"] == 3 and not public["ready_for_finalize"]
    assert call(estate, "POST", "/" + uid + "/complete", service=restarted, identity=refreshed)[0] == 409
    with pytest.raises(ValueError, match="missing chunks"):
        restarted.materialize(refreshed, estate.remote, uid, tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        restarted.mark_committed(refreshed, estate.remote, uid, {})
    assert not list(tmp_path.iterdir())
    assert put(estate, uid, 0, b"abcdefg", service=restarted, identity=refreshed)[0] == 200
    assert any(token == TOKEN_A2 and action == "upload" for token, action, _ in estate.backend.calls)


def test_complete_is_observational_and_materialize_streams_source_exactly(estate, tmp_path, monkeypatch):
    small(estate, chunk_bytes=13)
    raw = b'<POWERMART><REPOSITORY NAME="R"/></POWERMART>'
    uid = upload(estate, raw)
    before = deepcopy(estate.backend.rows)
    objects = deepcopy(estate.backend.objects)
    code, public = call(estate, "POST", "/" + uid + "/complete")
    assert code == 200 and public["status"] == "ready_for_finalize"
    assert call(estate, "POST", "/" + uid + "/complete", body={})[0] == 400
    assert estate.backend.rows == before and estate.backend.objects == objects
    reads, streams = [], []
    original = estate.identity.client.files.download

    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= hu.READ_BYTES
            reads.append(size)
            return super().read(size)

    def download(path):
        if "/upload-chunks/" not in path:
            return original(path)
        stream = BoundedStream(estate.backend.objects[path])
        streams.append(stream)
        return SimpleNamespace(contents=stream)

    monkeypatch.setattr(estate.identity.client.files, "download", download)
    path, name, project, digest, size = estate.service.materialize(estate.identity, estate.remote, uid, tmp_path)
    try:
        assert path.read_bytes() == raw
        assert path.suffix == ".xml" and name == "source.xml" and project == estate.project
        assert digest == sha256(raw).hexdigest() and size == len(raw)
        assert reads and all(s.closed for s in streams)
        assert estate.backend.rows == before
    finally:
        path.unlink()


@pytest.mark.parametrize("fault", ["corrupt", "short", "long", "missing"])
def test_materialize_detects_corruption_and_cleans_partial_file(estate, tmp_path, fault):
    small(estate)
    uid = upload(estate, b"1234567abcdefg<xml/>")
    path = estate.remote + "/upload-chunks/" + uid + "/1.bin"
    if fault == "missing":
        del estate.backend.objects[path]
    else:
        estate.backend.objects[path] = {"corrupt": b"xxxxxxx", "short": b"x", "long": b"x" * 8}[fault]
    with pytest.raises(Exception):
        estate.service.materialize(estate.identity, estate.remote, uid, tmp_path)
    assert not list(tmp_path.iterdir())
    assert call(estate, "GET", "/" + uid)[1]["status"] != "committed"
    # Retrying an accepted chunk still revalidates the immutable Volume bytes.
    assert put(estate, uid, 1, b"abcdefg")[0] in {200, 409}


def test_status_paginates_beyond_50_and_is_metadata_only(estate, monkeypatch):
    small(estate, chunk_bytes=1, max_file_bytes=200, max_batch_bytes=400)
    uid = upload(estate, b"x" * 123)
    calls = []
    original = RequestStateStore.read_table

    def read(store, table, **kwargs):
        if kwargs.get("context_id") == "hosted-upload:" + uid:
            calls.append(kwargs)
            assert kwargs["limit"] == 50
        return original(store, table, **kwargs)

    monkeypatch.setattr(RequestStateStore, "read_table", read)
    monkeypatch.setattr(estate.identity.client.files, "download", lambda *a: pytest.fail("Status is metadata only"))
    code, public = call(estate, "GET", "/" + uid)
    assert code == 200 and public["durable_bytes"] == 123
    assert len(public["chunks"]) == 123 and len(calls) == 3
    assert calls[0]["after"] is None and all(c["after"] is not None for c in calls[1:])


def test_status_supports_8192_chunks_with_bounded_pages_and_no_source_allocations(estate, monkeypatch):
    # Simulate the full chunk count using receipts, not 32GiB of source buffers.
    small(estate, chunk_bytes=hu.CHUNK_BYTES,
          max_file_bytes=hu.CHUNK_BYTES * hu.MAX_CHUNKS,
          max_batch_bytes=hu.CHUNK_BYTES * hu.MAX_CHUNKS)
    body, response = start(estate, hu.CHUNK_BYTES * hu.MAX_CHUNKS)
    assert response[0] == 201
    uid = body["id"]
    store = estate.service._store(estate.identity, estate.remote)
    records = [estate.service._record(store, uid, "chunk:" + str(index), {
        "index": index, "size_bytes": hu.CHUNK_BYTES, "sha256": "a" * 64})
        for index in range(hu.MAX_CHUNKS)]
    store.upsert_records("jobs", records, migration_id="hosted-upload:" + uid)
    calls = []
    original = RequestStateStore.read_table

    def read(store, table, **kwargs):
        calls.append(kwargs)
        return original(store, table, **kwargs)

    monkeypatch.setattr(RequestStateStore, "read_table", read)
    monkeypatch.setattr(estate.identity.client.files, "download", lambda *a: pytest.fail("No chunk downloads"))
    code, public = call(estate, "GET", "/" + uid)
    assert code == 200 and public["status"] == "ready_for_finalize"
    assert public["durable_bytes"] == hu.CHUNK_BYTES * hu.MAX_CHUNKS
    assert len(public["chunks"]) == hu.MAX_CHUNKS
    assert len(calls) == 164 and all(c["limit"] == 50 for c in calls)
    # A full journal is only ready, not a claim that source generation committed.
    assert "result" not in public


def test_reservations_paginate_and_concurrent_reservations_cannot_exceed_budget(estate, monkeypatch):
    small(estate, chunk_bytes=1, max_file_bytes=2, max_batch_bytes=56)
    store = estate.service._store(estate.identity, estate.remote)
    records = []
    for _ in range(55):
        uid = uuid4().hex
        manifest = {"id": uid, "project_id": estate.project, "name": "x.xml", "size_bytes": 1,
                    "chunk_bytes": 1, "total_chunks": 1}
        record = estate.service._record(store, uid, "manifest", manifest)
        # Each immutable reservation has its own migration identity.
        store.upsert_records("jobs", [record], migration_id=record["migration_id"])
        records.append(record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: start(estate, 1)[1][0], range(2)))
    assert sorted(codes) == [201, 413]
    assert start(estate, 1)[1][0] == 413
    # Explicit bounded history cap fails closed rather than ignoring reservations.
    monkeypatch.setattr(hu, "MAX_UPLOADS", 50)
    assert start(estate, 1)[1][0] == 503


def test_materialize_uses_multiple_bounded_reads_and_closes_stream(estate, tmp_path, monkeypatch):
    raw = b"x" * (2 * hu.READ_BYTES + 13)
    uid = upload(estate, raw)
    reads, streams = [], []

    class BoundedStream(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= hu.READ_BYTES
            reads.append(size)
            return super().read(size)

    def download(path):
        stream = BoundedStream(estate.backend.objects[path])
        streams.append(stream)
        return SimpleNamespace(contents=stream)

    monkeypatch.setattr(estate.identity.client.files, "download", download)
    path, _, _, digest, size = estate.service.materialize(estate.identity, estate.remote, uid, tmp_path)
    try:
        assert path.read_bytes() == raw and size == len(raw) and digest == sha256(raw).hexdigest()
        assert reads == [hu.READ_BYTES, hu.READ_BYTES, 14, 1]
        assert all(stream.closed for stream in streams)
    finally:
        path.unlink()


def test_materialize_rejects_credentials_split_across_chunks(estate, tmp_path):
    small(estate)
    # Individual immutable blobs contain only fragments, never the full token.
    uid = upload(estate, TOKEN_A.encode())
    with pytest.raises(ValueError, match="Credential"):
        estate.service.materialize(estate.identity, estate.remote, uid, tmp_path)
    assert not list(tmp_path.iterdir())


def test_batch_reservations_survive_restart_and_release_only_after_commit(estate):
    small(estate, chunk_bytes=4, max_file_bytes=8, max_batch_bytes=12)
    uid = upload(estate, b"abcdefgh")
    assert start(estate, 5)[1][0] == 413
    estate.service = hu.HostedUploads("warehouse", chunk_bytes=4, max_file_bytes=8, max_batch_bytes=12)
    assert start(estate, 5)[1][0] == 413
    assert call(estate, "POST", "/" + uid + "/complete")[0] == 200
    assert start(estate, 5)[1][0] == 413
    result = {"generation": "committed-generation", "package_sha256": sha256(b"abcdefgh").hexdigest()}
    public = estate.service.mark_committed(estate.identity, estate.remote, uid, result)
    assert public["status"] == "committed" and public["result"] == result
    before = deepcopy(journal(estate, uid))
    assert estate.service.mark_committed(estate.identity, estate.remote, uid, result) == public
    assert journal(estate, uid) == before
    with pytest.raises(ValueError, match="different commit"):
        estate.service.mark_committed(estate.identity, estate.remote, uid, {"different": True})
    assert start(estate, 5)[1][0] == 201


def test_concurrent_start_and_chunk_retry_are_immutable(estate):
    small(estate)
    uid = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: start(estate, 3, uid)[1][0], range(4)))
        assert sorted(responses) == [200, 200, 200, 201]
        responses = list(pool.map(lambda _: put(estate, uid, 0, b"abc")[0], range(4)))
    assert responses == [200] * 4 and len(journal(estate, uid)) == 2


@pytest.mark.parametrize("fault", ["checksum", "record_id", "envelope", "duplicate", "revision", "missing_manifest"])
def test_corrupt_or_ambiguous_journal_fails_closed(estate, fault):
    small(estate)
    uid = upload(estate, b"abc")
    rows = journal(estate, uid)
    row = next(r for r in rows if r["source_key"] == "chunk:0")
    table = next(items for name, items in estate.backend.rows.items() if name.endswith(".jobs") and row in items)
    if fault == "checksum":
        row["content_hash"] = "0" * 64
    elif fault == "record_id":
        row["record_id"] = "0" * 64
        row["content_hash"] = ds._content_hash(row)
    elif fault == "envelope":
        row["source_path"] = "other"
        row["content_hash"] = ds._content_hash(row)
    elif fault == "duplicate":
        table.append(deepcopy(row))
    elif fault == "revision":
        changed = deepcopy(row)
        changed["payload"] = ds._json({"index": 0, "size_bytes": 3, "sha256": "0" * 64})
        changed["content_hash"] = ds._content_hash(changed)
        table.append(changed)
    else:
        table.remove(next(r for r in rows if r["source_key"] == "manifest"))
    assert call(estate, "GET", "/" + uid)[0] == 503


def test_nonadvancing_and_oversize_pages_fail_closed(estate, monkeypatch):
    body, _ = start(estate, 1)
    rows = journal(estate, body["id"])
    monkeypatch.setattr(RequestStateStore, "read_table", lambda *a, **k: rows * 51)
    assert call(estate, "GET", "/" + body["id"])[0] == 503
    monkeypatch.setattr(RequestStateStore, "read_table", lambda *a, **k: rows * 50)
    assert call(estate, "GET", "/" + body["id"])[0] == 503


def test_authentication_freshness_pinning_no_fallback_or_token_persistence(estate, monkeypatch):
    small(estate, chunk_bytes=128, max_file_bytes=256, max_batch_bytes=512)
    body, _ = start(estate, len(TOKEN_A))
    assert put(estate, body["id"], 0, TOKEN_A.encode())[0] == 400
    uid = upload(estate, b"<xml/>")
    for result in ({"access_token": "secret"}, {"detail": TOKEN_A}, {"large": "x" * hu.MAX_METADATA}):
        with pytest.raises(ValueError):
            estate.service.mark_committed(estate.identity, estate.remote, uid, result)
    original = estate.identity.client.current_user.me
    calls = []
    monkeypatch.setattr(estate.identity.client.current_user, "me", lambda: (calls.append(1) or original()))
    assert call(estate, "GET", "/" + uid)[0] == 200
    assert call(estate, "GET", "/" + uid)[0] == 200
    assert len(calls) == 2
    store = estate.service._store(estate.identity, estate.remote)
    store.client = estate.backend.client(TOKEN_B)
    with pytest.raises(PermissionError, match="identity changed"):
        store._authorize()
    monkeypatch.setattr(estate.identity.client.current_user, "me", lambda: (_ for _ in ()).throw(Unauthenticated(TOKEN_A)))
    code, public = call(estate, "GET", "/" + uid)
    assert code == 401 and TOKEN_A not in json.dumps(public)
    local = SimpleNamespace(mode="profile")
    assert call(estate, "GET", "/" + uid, identity=local)[0] == 401
    for row in journal(estate, uid):
        assert TOKEN_A not in ds._json(row)
    assert all(TOKEN_A.encode() not in blob for blob in estate.backend.objects.values())
    assert set(vars(estate.service)) == {"warehouse", "max_file_bytes", "max_batch_bytes", "chunk_bytes"}


def test_upstream_errors_are_fixed_and_do_not_leak_tokens(estate, monkeypatch):
    body, _ = start(estate, 3)
    monkeypatch.setattr(estate.identity.client.files, "upload", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(TOKEN_A)))
    response = put(estate, body["id"], 0, b"abc")
    assert response == (503, {"detail": "Hosted upload is unavailable."})


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("DELETE", "/" + "a" * 32),
                                          ("POST", "/" + "a" * 32 + "/finalize"),
                                          ("PUT", "/" + "a" * 32 + "/chunks/-1"),
                                          ("PUT", "/" + "a" * 32 + "/chunks/01")])
def test_unknown_routes_fall_through_without_authentication(estate, method, suffix):
    assert call(estate, method, suffix, identity=object()) is None


def test_queries_are_rejected(estate):
    assert call(estate, "POST", body={}, query="token=unsafe")[0] == 400


@pytest.mark.parametrize("kwargs", [{"chunk_bytes": 0}, {"chunk_bytes": hu.CHUNK_BYTES + 1},
                                    {"max_file_bytes": True}, {"max_file_bytes": 0},
                                    {"max_batch_bytes": 1},
                                    {"max_file_bytes": hu.CHUNK_BYTES * (hu.MAX_CHUNKS + 1)}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        hu.HostedUploads("warehouse", **kwargs)