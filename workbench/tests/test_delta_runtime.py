"""Offline authority tests using real SQLite, Statement API and Volume contracts."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from databricks.sdk.errors import NotFound

from workbench import delta_state as ds
from workbench.delta_runtime import (DeltaRuntime, RUNTIME_SCOPE, RequestArtifacts,
                                     RequestStateStore, _artifact_cache_clear_user,
                                     _VERIFIED_ARTIFACT_CACHE_LIMIT,
                                     _VERIFIED_ARTIFACT_MEMORY_RESERVE)
from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
from workbench.tests.test_delta_state import estate, write_json


def test_container_memory_state_discovers_nested_cgroup_limits(tmp_path):
    import workbench.delta_runtime as runtime

    v2 = tmp_path / "cgroup-v2"
    nested = v2 / "system.slice" / "app.scope"
    nested.mkdir(parents=True)
    (v2 / "memory.current").write_text("1200", encoding="ascii")
    (v2 / "memory.max").write_text("max", encoding="ascii")
    (v2 / "system.slice" / "memory.current").write_text("900", encoding="ascii")
    (v2 / "system.slice" / "memory.max").write_text("2000", encoding="ascii")
    (nested / "memory.current").write_text("700", encoding="ascii")
    (nested / "memory.max").write_text("max", encoding="ascii")
    cgroup_v2 = tmp_path / "proc-v2"
    cgroup_v2.write_text("0::/system.slice/app.scope\n", encoding="ascii")
    assert runtime._container_memory_state(cgroup_file=cgroup_v2, cgroup_v2_root=v2,
                                          cgroup_v1_root=tmp_path / "missing-v1") == (900, 2000)

    v1 = tmp_path / "cgroup-v1"
    nested_v1 = v1 / "batch.slice" / "app.scope"
    nested_v1.mkdir(parents=True)
    (v1 / "memory.usage_in_bytes").write_text("1800", encoding="ascii")
    (v1 / "memory.limit_in_bytes").write_text("4000", encoding="ascii")
    (v1 / "batch.slice" / "memory.usage_in_bytes").write_text("1500", encoding="ascii")
    (v1 / "batch.slice" / "memory.limit_in_bytes").write_text("3000", encoding="ascii")
    (nested_v1 / "memory.usage_in_bytes").write_text("900", encoding="ascii")
    (nested_v1 / "memory.limit_in_bytes").write_text(str(1 << 62), encoding="ascii")
    cgroup_v1 = tmp_path / "proc-v1"
    cgroup_v1.write_text("5:memory:/batch.slice/app.scope\n", encoding="ascii")
    assert runtime._container_memory_state(cgroup_file=cgroup_v1, cgroup_v2_root=tmp_path / "missing-v2",
                                          cgroup_v1_root=v1) == (1500, 3000)

    mounted = tmp_path / "mounted-cgroup"
    mounted_child = mounted / "app"
    mounted_child.mkdir(parents=True)
    (mounted / "memory.current").write_text("1400", encoding="ascii")
    (mounted / "memory.max").write_text("2500", encoding="ascii")
    (mounted_child / "memory.current").write_text("1200", encoding="ascii")
    (mounted_child / "memory.max").write_text("max", encoding="ascii")
    mounted_cgroup = tmp_path / "proc-mounted-cgroup"
    mounted_cgroup.write_text("0::/tenant/container/app\n", encoding="ascii")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"29 23 0:26 /tenant/container {mounted} rw - cgroup2 cgroup rw\n", encoding="ascii")
    assert runtime._container_memory_state(cgroup_file=mounted_cgroup, mountinfo_file=mountinfo,
                                          cgroup_v2_root=tmp_path / "missing-v2",
                                          cgroup_v1_root=tmp_path / "missing-v1") == (1400, 2500)


@pytest.fixture
def authority():
    client = RuntimeStatementClient()
    store = ds.DeltaStateStore(client, "warehouse", "user_catalog", "workbench", "user-123")
    artifacts = ds.VolumeArtifacts(store)
    identity = NS(client=client, host=store.workspace_id, user_id=store.user_id)
    remote = "/Volumes/user_catalog/workbench/artifacts/users/test-user"
    return client, store, artifacts, identity, remote


def adapter(authority, root):
    _, store, artifacts, identity, remote = authority
    return DeltaRuntime(lambda: identity, remote, root, warehouse="warehouse", store=store, artifacts=artifacts)


def test_fresh_request_artifacts_reuse_verified_immutable_blob_after_fresh_volume_authorization(authority):
    client, store, _, _, _ = authority
    raw = b"verified immutable package artifact"
    digest = sha256(raw).hexdigest()
    writer = ds.VolumeArtifacts(store)
    writer.put(raw)
    _artifact_cache_clear_user(store.workspace_id, store.user_id)

    downloads, owner_checks = [], []
    original_download = client.files.download
    original_owner = client.volumes.read
    def download(path):
        downloads.append(path)
        return original_download(path)
    def owner(name):
        owner_checks.append(name)
        return original_owner(name)
    client.files.download = download
    client.volumes.read = owner

    first = RequestArtifacts(store, "artifacts")
    assert first.get(digest, max_bytes=len(raw)) == raw
    second = RequestArtifacts(store, "artifacts")
    assert second.get(digest, max_bytes=len(raw)) == raw
    assert len(downloads) == 1
    assert len(owner_checks) == 2
    assert second.memory_cache_hits == 1

    client.user = NS(id="user-456", user_name="second@example.com")
    client.catalog_owner = client.schema_owner = client.volume_owner = client.user.user_name
    other_store = ds.DeltaStateStore(client, "warehouse", "user_catalog", "workbench", "user-456")
    other = RequestArtifacts(other_store, "artifacts")
    client.objects[other._path(digest)] = raw
    assert other.get(digest, max_bytes=len(raw)) == raw
    assert len(downloads) == 2
    _artifact_cache_clear_user(store.workspace_id, store.user_id)
    _artifact_cache_clear_user(other_store.workspace_id, other_store.user_id)


def test_immutable_artifact_cache_respects_memory_headroom_and_hard_cap(monkeypatch):
    import workbench.delta_runtime as runtime
    gib = 1024 ** 3
    with runtime._VERIFIED_ARTIFACT_CACHE_LOCK:
        runtime._VERIFIED_ARTIFACT_CACHE.clear()
        runtime._VERIFIED_ARTIFACT_CACHE_BYTES = 0
    raw = b"x" * (2 * 1024 * 1024)
    key = ("workspace", "user", "/owned/root", "a" * 64, len(raw))
    monkeypatch.setattr(runtime, "_container_memory_state", lambda: (5 * gib, 6 * gib))
    runtime._artifact_cache_put(key, raw)
    assert runtime._artifact_cache_get(key) is None
    monkeypatch.setattr(runtime, "_container_memory_state", lambda: (2 * gib, 6 * gib))
    runtime._artifact_cache_put(key, raw)
    assert runtime._artifact_cache_get(key) == raw
    assert runtime._VERIFIED_ARTIFACT_CACHE_BYTES <= _VERIFIED_ARTIFACT_CACHE_LIMIT
    assert _VERIFIED_ARTIFACT_MEMORY_RESERVE == 1536 * 1024 * 1024
    with runtime._VERIFIED_ARTIFACT_CACHE_LOCK:
        runtime._VERIFIED_ARTIFACT_CACHE.clear()
        runtime._VERIFIED_ARTIFACT_CACHE_BYTES = 0


def sources(runtime):
    for path in runtime.local.rglob("source.xml"):
        runtime.publish_source(path, path.name, sha256(path.read_bytes()).hexdigest())


def test_generated_draft_json_bytes_and_scoped_artifacts_survive_restart(authority, tmp_path):
    runtime = adapter(authority, tmp_path / 'draft-first')
    runtime.restore()
    package, design, run, bundle = 'a' * 64, 'b' * 64, 'c' * 32, 'd' * 64
    base = runtime.local / 'projects' / ('e' * 32) / 'package-contexts' / package / 'generated-drafts' / package / design / '1' / run
    receipt = base / 'native-bundles' / bundle / 'bundle-receipt.json'
    receipt.parent.mkdir(parents=True)
    original = b'{"version":1, "files":{}}\n'
    receipt.write_bytes(original)
    (base / 'manifest.json').write_bytes(b'{ "x": 1 }\n')
    write_json(runtime.local / 'state/active.json', {'owned': True})
    runtime.flush()
    restarted = adapter(authority, tmp_path / 'draft-restart')
    restarted.restore()
    assert (restarted.local / receipt.relative_to(runtime.local)).read_bytes() == original
    assert (restarted.local / (base / 'manifest.json').relative_to(runtime.local)).read_bytes() == b'{ "x": 1 }\n'
    assert json.loads((restarted.local / 'state/active.json').read_bytes()) == {'owned': True}


@pytest.mark.parametrize('bad', [
    'generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/1/' + 'c' * 32 + '/native-bundles/not-a-hash/bundle-receipt.json',
    'generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/1/' + 'c' * 32 + '/native-bundles/' + 'd' * 64 + '/../../state/private.json',
    'generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/01/' + 'c' * 32 + '/manifest.json',
    'generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/1/' + 'c' * 32 + '/notebooks/CON.ipynb',
    'projects/' + 'e' * 32 + '/package-contexts/' + 'f' * 64 + '/generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/1/' + 'c' * 32 + '/manifest.json',
    'generated-drafts/' + 'a' * 64 + '/' + 'b' * 64 + '/1/' + 'c' * 32 + '/arbitrary.json',
])
def test_generated_draft_snapshot_paths_reject_malicious_or_mismatched_scope(bad):
    from fastapi import HTTPException
    from workbench.hosted_storage import VolumeSnapshots
    with pytest.raises((ValueError, HTTPException)):
        VolumeSnapshots._local_path(bad)


def rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY 1').fetchall()
                for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_complete_sqlite_and_json_roundtrip_including_binary_checkpoints(authority, tmp_path):
    runtime = adapter(authority, tmp_path / "first")
    assert runtime.restore() is False
    estate(runtime.local)
    sources(runtime)
    before = {p.relative_to(runtime.local).as_posix(): rows(p)
              for p in runtime.local.rglob("*") if p.suffix in {".sqlite", ".sqlite3"}}
    generation = runtime.flush()
    # Published Volume objects contain DDL, binary cells or ordinary files, but
    # never an SQLite image that could override live Delta edits.
    client, store, _, _, _ = authority
    assert not any(raw.startswith(b"SQLite format 3") for raw in client.objects.values())
    assert not any(name.endswith("/HEAD.json") for name in client.objects)
    assert store.migration_count("agent_checkpoints", generation) == 6
    restored = adapter(authority, tmp_path / "restart")
    assert restored.restore() is True
    for relative, expected in before.items():
        assert rows(restored.local / relative) == expected, relative
    for path in runtime.local.rglob("*.json"):
        relative = path.relative_to(runtime.local)
        assert json.loads((restored.local / relative).read_bytes()) == json.loads(path.read_bytes())
    # A second full commit preserves exact source columns and binary types.
    restored.flush()
    restarted = adapter(authority, tmp_path / "restart-again")
    restarted.restore()
    for relative, expected in before.items():
        assert rows(restarted.local / relative) == expected


def external_generation(runtime, change):
    """An authorized Delta writer publishes a new, complete active generation."""
    latest = runtime._latest()
    manifest = json.loads(runtime._object(json.loads(latest["payload"])["manifest"]))
    records = runtime._records(manifest["inventory"], latest["migration_id"])
    change(records)
    for items in records.values():
        for item in items:
            item["content_hash"] = ds._content_hash(item)
    generation = uuid4().hex
    manifest.update(generation=generation, sequence=latest["sequence"] + 1,
                    parent=latest["migration_id"], inventory=ds._inventory(records))
    reference = runtime.artifacts.put(ds._json(manifest).encode())
    for table, items in records.items():
        if items:
            runtime.store.upsert_records(table, items, migration_id=generation)
    receipt = runtime.store.make_record(
        "migration_receipts", **RUNTIME_SCOPE, source_path="hosted-runtime", source_table="generation",
        source_key=generation, payload={"manifest": reference, "parent": latest["migration_id"]},
        status="committed", sequence=manifest["sequence"])
    runtime.store.upsert_records("migration_receipts", [receipt], migration_id=generation)
    return generation


def test_committed_delta_edits_not_original_artifacts_drive_restart(authority, tmp_path):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    estate(runtime.local)
    sources(runtime)
    runtime.flush()

    def edit(records):
        for row in records["projects"]:
            if row["source_key"] == '["p1"]':
                payload = json.loads(row["payload"])
                payload["record"] = '{"id":"p1","name":"EDITED IN DELTA"}'
                # Deliberately leave record_decoded stale. Raw source columns win.
                row["payload"] = ds._json(payload)
        for row in records["settings"]:
            if row["source_path"] == "state/active.json":
                row["payload"] = '{"edited_in_delta":true}'
        for row in records["agent_checkpoints"]:
            if row["source_table"] == "writes":
                payload = json.loads(row["payload"])
                payload["value"] = ds._externalize(b"new checkpoint write\x00\xff", runtime.artifacts)
                row["payload"] = ds._json(payload)

    generation = external_generation(runtime, edit)
    # Poison the legacy snapshot pointer. It must not be read at all.
    client, _, _, _, remote = authority
    client.objects[remote + "/HEAD.json"] = b"not a snapshot"
    restart = adapter(authority, tmp_path / "restart")
    restart.restore()
    assert restart.generation == generation
    with closing(sqlite3.connect(restart.local / "projects/projects.sqlite")) as db:
        assert json.loads(db.execute("SELECT record FROM projects WHERE id='p1'").fetchone()[0])["name"] == "EDITED IN DELTA"
    assert json.loads((restart.local / "state/active.json").read_bytes()) == {"edited_in_delta": True}
    for path in restart.local.rglob("checkpoint_*.sqlite3"):
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT value FROM writes").fetchone()[0] == b"new checkpoint write\x00\xff"


def test_active_manifest_deletions_do_not_resurrect_stale_rows_or_sources(authority, tmp_path):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    state = runtime.local / "state"
    state.mkdir()
    write_json(state / "active.json", {"value": 1})
    write_json(state / "removed.json", {"value": "remove"})
    package = runtime.local / "packages" / ("a" * 64)
    package.mkdir(parents=True)
    source = package / "source.xml"
    source.write_bytes(b"<source/>")
    sources(runtime)
    runtime.flush()
    old_source = runtime.sources[sha256(source.read_bytes()).hexdigest()]
    (state / "removed.json").unlink()
    source.unlink()
    write_json(state / "active.json", {"value": 2})
    runtime.flush()
    # Removing a no-longer-active source remotely must not break the next read.
    authority[0].objects.pop(old_source)
    restart = adapter(authority, tmp_path / "restart")
    restart.restore()
    assert json.loads((restart.local / "state/active.json").read_bytes()) == {"value": 2}
    assert not (restart.local / "state/removed.json").exists()
    assert not (restart.local / source.relative_to(runtime.local)).exists()
    assert restart.sources == {}


@pytest.mark.parametrize("failure", ["records", "receipt", "artifact"])
def test_failures_never_publish_partial_generation_or_use_local_fallback(authority, tmp_path, monkeypatch, failure):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    write_json(runtime.local / "state/active.json", {"value": "committed"})
    committed = runtime.flush()
    write_json(runtime.local / "state/active.json", {"value": "uncommitted"})
    store, artifacts = authority[1:3]
    with monkeypatch.context() as patch:
        original = store.upsert_records

        def append(table, records, **kwargs):
            if (failure == "records" and table == "artifact_manifest") or (failure == "receipt" and table == "migration_receipts"):
                raise RuntimeError("write denied")
            return original(table, records, **kwargs)

        if failure == "artifact":
            patch.setattr(artifacts, "put", lambda *a: (_ for _ in ()).throw(RuntimeError("artifact denied")))
        else:
            patch.setattr(store, "upsert_records", append)
        with pytest.raises(RuntimeError):
            runtime.flush()
    restart = adapter(authority, tmp_path / "restart")
    restart.restore()
    assert restart.generation == committed
    assert json.loads((restart.local / "state/active.json").read_bytes()) == {"value": "committed"}


def test_commit_receipt_is_last_append_after_live_validation(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    write_json(runtime.local / "state/active.json", {"value": 1})
    events = []
    store, artifacts = authority[1:3]
    append, put, read = store.upsert_records, artifacts.put, store.read_table
    monkeypatch.setattr(artifacts, "put", lambda raw: (events.append("artifact"), put(raw))[1])
    monkeypatch.setattr(store, "upsert_records", lambda table, rows, **kw: (events.append("append:" + table), append(table, rows, **kw))[1])
    monkeypatch.setattr(store, "read_table", lambda table, **kw: (events.append("read:" + table), read(table, **kw))[1])
    runtime.flush()
    assert events[-1] == "append:migration_receipts"
    assert max(i for i, e in enumerate(events) if e == "artifact") < min(i for i, e in enumerate(events) if e.startswith("append:"))
    assert events.index("read:artifact_manifest") < events.index("append:migration_receipts")


def test_hash_failure_and_delta_unavailable_fail_closed_with_empty_cache(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    write_json(runtime.local / "state/active.json", {"value": 1})
    runtime.flush()
    latest = runtime._latest()
    manifest_path = json.loads(latest["payload"])["manifest"]["volume_path"]
    authority[0].objects[manifest_path] = b"corrupt artifact"
    restart = adapter(authority, tmp_path / "bad-hash")
    with pytest.raises(ValueError, match="checksum"):
        restart.restore()
    assert list(restart.local.iterdir()) == []
    monkeypatch.setattr(authority[1], "ensure_tables", lambda: (_ for _ in ()).throw(RuntimeError("warehouse denied")))
    unavailable = adapter(authority, tmp_path / "unavailable")
    with pytest.raises(RuntimeError, match="warehouse denied"):
        unavailable.restore()
    assert list(unavailable.local.iterdir()) == []
    with pytest.raises(ValueError, match="restore"):
        unavailable.flush()


def test_existing_cache_and_legacy_head_are_not_authority(authority, tmp_path):
    local = tmp_path / "local"
    write_json(local / "state/active.json", {"poison": True})
    with pytest.raises(ValueError, match="fallback"):
        adapter(authority, local).restore()
    authority[0].objects[authority[4] + "/HEAD.json"] = b'{"manifest":"legacy"}'
    with pytest.raises(ValueError, match="explicit Delta import"):
        adapter(authority, tmp_path / "legacy").restore()


def test_secrets_preflight_does_not_upload_any_raw_database(authority, tmp_path):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    path = runtime.local / "state/reviews.sqlite"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE settings(package_hash TEXT PRIMARY KEY,payload TEXT)")
        db.execute("INSERT INTO settings VALUES ('p',?)", ('{"api_key":"sensitive"}',))
    before = deepcopy(authority[0].objects)
    with pytest.raises(ValueError, match="Credential"):
        runtime.flush()
    assert authority[0].objects == before


def test_request_store_checks_once_but_fresh_store_reauthorizes(authority, monkeypatch):
    client, _, _, identity, _ = authority
    calls = {"me": 0, "table": 0}
    me, table = client.current_user.me, client.tables.get

    def checked_me():
        calls["me"] += 1
        return me()

    def checked_table(name):
        calls["table"] += 1
        return table(name)

    monkeypatch.setattr(client.current_user, "me", checked_me)
    monkeypatch.setattr(client.tables, "get", checked_table)
    store = RequestStateStore(client, "warehouse", "user_catalog", "workbench", identity.user_id)
    store.ensure_tables()
    first = calls.copy()
    store.ensure_tables()
    store.read_table("projects", **RUNTIME_SCOPE)
    assert calls == first
    assert calls["me"] == 1
    fresh = RequestStateStore(client, "warehouse", "user_catalog", "workbench", identity.user_id)
    client.catalog_owner = "other"
    with pytest.raises(PermissionError):
        fresh.ensure_tables()
    assert calls["me"] == 2
    store.user_id = "other"
    with pytest.raises(PermissionError, match="identity changed"):
        store._authorize()


def test_request_runtime_batches_counts_and_detects_unexpected_empty_table_rows(authority, tmp_path):
    client, _, _, identity, remote = authority

    def runtime(path):
        return DeltaRuntime(lambda: identity, remote, path, warehouse="warehouse")

    first = runtime(tmp_path / "first")
    first.restore()
    write_json(first.local / "state/active.json", {"value": 1})
    generation = first.flush()
    client.calls.clear()
    restart = runtime(tmp_path / "restart")
    restart.restore()
    counts = [call for call in client.calls if "count(*)" in call["statement"]]
    assert len(counts) == 1 and counts[0]["statement"].count(" UNION ALL ") == 19
    row = first.store.make_record("projects", **RUNTIME_SCOPE, source_path="unexpected", source_table="json",
                                  source_key="unexpected", payload={})
    first.store.upsert_records("projects", [row], migration_id=generation)
    with pytest.raises(ValueError, match="unexpected generation"):
        runtime(tmp_path / "unexpected").restore()


def test_observational_empty_restore_uses_only_required_existing_tables(authority, tmp_path, monkeypatch):
    client, store, _, identity, remote = authority
    store.ensure_tables()
    # Unrelated families need not even exist when there is no committed estate.
    client.infos = {name: info for name, info in client.infos.items()
                    if name.rsplit(".", 1)[-1] in {"migration_receipts", "audit_events"}}
    tables = []
    table = client.tables.get

    def verified(name):
        tables.append(name.rsplit(".", 1)[-1])
        return table(name)

    monkeypatch.setattr(client.tables, "get", verified)
    monkeypatch.setattr(RequestStateStore, "ensure_tables",
                        lambda self: pytest.fail("GET must not initialize metadata tables"))
    client.calls.clear()
    runtime = DeltaRuntime(lambda: identity, remote, tmp_path / "read", warehouse="warehouse")
    assert runtime.restore(initialize=False) is False
    runtime.restore_audit()
    runtime.flush_audit()
    assert tables == ["migration_receipts", "audit_events"]
    assert len(client.calls) == 2
    assert all(call["statement"].startswith("SELECT") for call in client.calls)
    assert list(runtime.local.iterdir()) == [] and runtime.ready


@pytest.mark.parametrize("failure", ["missing", "owner", "schema", "sql"])
def test_observational_restore_rechecks_fresh_permissions_and_schema(authority, tmp_path, monkeypatch, failure):
    client, store, _, identity, remote = authority
    store.ensure_tables()

    def runtime(name):
        return DeltaRuntime(lambda: identity, remote, tmp_path / name, warehouse="warehouse")

    first = runtime("first")
    first.restore(initialize=False)
    first.restore_audit()
    name = "user_catalog.workbench.audit_events"
    if failure == "missing":
        del client.infos[name]
    elif failure == "owner":
        client.infos[name].owner = "someone-else@example.com"
    elif failure == "schema":
        client.infos[name].columns = []
    else:
        from databricks.sdk.errors import PermissionDenied

        def denied(**kwargs):
            raise PermissionDenied("Warehouse SELECT privilege revoked")

        monkeypatch.setattr(client.statement_execution, "execute_statement", denied)
    fresh = runtime("fresh")
    with pytest.raises((NotFound, PermissionError, ValueError, ds.DeltaStatementError)):
        fresh.restore(initialize=False)
        fresh.restore_audit()
    assert list(fresh.local.iterdir()) == []


def test_latest_rejects_valid_hash_receipt_outside_requested_user(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    receipt = runtime.store.make_record(
        "migration_receipts", **RUNTIME_SCOPE, source_path="hosted-runtime", source_table="generation",
        source_key="generation", payload={}, status="committed", sequence=1)
    receipt["user_id"] = "other-user"
    receipt["content_hash"] = ds._content_hash(receipt)
    monkeypatch.setattr(runtime.store, "_execute", lambda *a, **kw: [[str(receipt[k]) for k in ds.COLUMNS]])
    with pytest.raises(ValueError, match="Invalid committed generation receipt"):
        runtime._latest()


def test_audit_tail_paging_checkpoint_and_mutation_roundtrip(authority, tmp_path):
    from workbench.audit import AuditLog
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    runtime.restore_audit()
    path = runtime.local / "state/audit.sqlite"
    path.parent.mkdir(exist_ok=True)
    audit = AuditLog(path)
    for number in range(55):
        audit.append({"event": "http_request_completed", "message": str(number)})
    runtime.flush_audit()
    assert runtime.generation is None
    restart = adapter(authority, tmp_path / "restart")
    restart.restore()
    restart.restore_audit()
    assert AuditLog(restart.local / "state/audit.sqlite").records() == audit.records()
    # A mutation checkpoints the journal prefix into a normal content generation.
    restart.flush()
    third = adapter(authority, tmp_path / "third")
    third.restore()
    third.restore_audit()
    AuditLog(third.local / "state/audit.sqlite").append({"message": "after checkpoint"})
    third.flush_audit()
    fourth = adapter(authority, tmp_path / "fourth")
    fourth.restore()
    fourth.restore_audit()
    result, valid = AuditLog(fourth.local / "state/audit.sqlite").records()
    assert valid and len(result) == 56 and result[-1]["message"] == "after checkpoint"