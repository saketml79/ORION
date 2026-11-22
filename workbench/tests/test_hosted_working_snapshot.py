"""Local fake authority exercises actual restore/commit paths, never hosted services."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from workbench import delta_runtime as dr
from workbench import delta_state as ds
from workbench.audit import AuditLog
from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
from workbench.tests.test_delta_state import database, write_json


TOKEN = "local-test-forwarded-credential"
REMOTE = "/Volumes/user_catalog/workbench/artifacts/users/test-user"


def scan_file(stream, limit, output=None):
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("File exceeds limit")
    if output is not None:
        output.write(raw)
    return sha256(raw).hexdigest(), len(raw)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cache = dr.VerifiedWorkingSnapshot(max_bytes=8 * 1024 * 1024, reserve=1024)
    monkeypatch.setattr(dr, "_WORKING_SNAPSHOT_CACHE", cache)
    monkeypatch.setattr(dr, "_container_memory_state", lambda: (1024, 1024 ** 3))
    client = RuntimeStatementClient()
    identity = SimpleNamespace(client=client, host=client.config.host, user_id=client.user.id)

    def runtime(name, token=TOKEN, remote=REMOTE):
        return dr.DeltaRuntime(lambda: identity, remote, tmp_path / name,
                               warehouse="warehouse", forbidden_token=token)

    writer = runtime("writer")
    assert writer.restore() is False
    database(writer.local / "projects/projects.sqlite", [
        ("CREATE TABLE projects(id TEXT PRIMARY KEY,record TEXT NOT NULL)", ()),
        ("INSERT INTO projects VALUES (?,?)", ("p1", '{"name":"committed"}')),
    ])
    write_json(writer.local / "state/active.json", {"value": "committed"})
    source = writer.local / "packages" / ("a" * 64) / "source.xml"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"<source>verified</source>")
    # The same immutable source can appear in more than one package scope.
    duplicate = writer.local / "projects/p1/package-contexts" / ("a" * 64) / "packages" / ("a" * 64) / "source.xml"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(source.read_bytes())
    writer.publish_source(source, source.name, sha256(source.read_bytes()).hexdigest())
    generation = writer.flush()
    # Most tests below deliberately start with a cold cache.
    cache.clear()
    yield client, identity, runtime, writer, generation, cache
    cache.clear()


def warm(setup):
    _, _, runtime, _, _, cache = setup
    cold = runtime("cold")
    assert cold.restore()
    assert cache._entry is not None
    return cold


def test_cache_only_restore_skips_disposable_tree_copy_and_cannot_commit(setup, monkeypatch):
    _, _, runtime, _, generation, cache = setup
    copies = []
    copytree = dr.shutil.copytree

    def tracked_copy(*args, **kwargs):
        copies.append((args, kwargs))
        return copytree(*args, **kwargs)

    monkeypatch.setattr(dr.shutil, "copytree", tracked_copy)
    cold = runtime("cache-only-cold")
    assert cold.restore(initialize=False, materialize=False)
    assert cold.ready and cold.generation == generation
    assert cold.working_snapshot_cache_ready and cache._entry is not None
    assert not copies and not list(cold.local.iterdir())
    with pytest.raises(ValueError, match="cache-only restore"):
        cold.restore_audit()
    with pytest.raises(ValueError, match="Materialized authoritative restore"):
        cold.flush()
    with pytest.raises(ValueError, match="Materialized authoritative restore"):
        cold.flush_audit()

    current = runtime("cache-only-warm")
    monkeypatch.setattr(current, "_database", lambda *a: pytest.fail("warm snapshot reconstructed database"))
    assert current.restore(initialize=False, materialize=False)
    assert current.ready and current.working_snapshot_cache_hit
    assert current.generation == generation and not copies and not list(current.local.iterdir())
    normal = runtime("materialized-after-warm")
    assert normal.restore(initialize=False)
    assert normal.working_snapshot_cache_hit and len(copies) == 1
    assert (normal.local / "projects/projects.sqlite").is_file()


def test_cache_only_restore_still_requires_fresh_authority(setup):
    client, _, runtime, _, _, _ = setup
    first = runtime("cache-only-authorized")
    assert first.restore(initialize=False, materialize=False)
    client.volume_owner = "revoked"
    denied = runtime("cache-only-denied")
    with pytest.raises(PermissionError):
        denied.restore(initialize=False, materialize=False)
    assert not denied.ready
    with pytest.raises(ValueError):
        denied.flush()


def test_cache_only_restore_cannot_initialize_tables(setup):
    _, _, runtime, _, _, _ = setup
    current = runtime("cache-only-invalid")
    with pytest.raises(ValueError, match="read-only"):
        current.restore(materialize=False)
    assert not current.ready


def test_warm_reads_full_generation_and_current_source_without_reconstruction(setup, monkeypatch):
    client, _, runtime, writer, generation, _ = setup
    cold = warm(setup)
    downloads = []
    download = client.files.download
    monkeypatch.setattr(client.files, "download", lambda path: (downloads.append(path), download(path))[1])
    current = runtime("warm")
    monkeypatch.setattr(current, "_database", lambda *a: pytest.fail("warm snapshot reconstructed database"))
    objects = []
    original_object = current._object
    monkeypatch.setattr(current, "_object", lambda ref: (objects.append(ref), original_object(ref))[1])
    client.calls.clear()
    assert current.restore(initialize=False)
    assert current.ready and current.generation == generation and current.sequence == 1
    assert current.sources == cold.sources == writer.sources
    assert objects  # Every original artifact is rechecked, using cached bytes.
    source_downloads = [path for path in downloads if path in writer.sources.values()]
    assert len(source_downloads) == 1
    assert all(path in writer.sources.values() or path.startswith("/Volumes/user_catalog/workbench/artifacts/delta-state/users/")
               for path in downloads)
    assert any("count(*)" in call["statement"] for call in client.calls)
    assert any("LIMIT 50" in call["statement"] and "migration_id" in call["statement"]
               for call in client.calls)
    with closing(sqlite3.connect(current.local / "projects/projects.sqlite")) as db:
        assert db.execute("SELECT record FROM projects").fetchone()[0] == '{"name":"committed"}'
    # A request works on its own files, not a shared mutable object.
    write_json(current.local / "state/active.json", {"value": "uncommitted"})
    another = runtime("another")
    assert another.restore()
    assert json.loads((another.local / "state/active.json").read_bytes()) == {"value": "committed"}


@pytest.mark.parametrize("damage", ["missing", "corrupt", "denied"])
def test_source_is_freshly_authorized_and_checked_on_every_warm_action(setup, monkeypatch, damage):
    client, _, runtime, writer, _, _ = setup
    warm(setup)
    path = next(iter(writer.sources.values()))
    if damage == "missing":
        del client.objects[path]
    elif damage == "corrupt":
        client.objects[path] = b"<source>tampered</source>"
    else:
        original = client.files.download
        def download(name):
            if name == path:
                raise PermissionDenied("source denied")
            return original(name)
        monkeypatch.setattr(client.files, "download", download)
    current = runtime("source-failure")
    with pytest.raises((NotFound, PermissionDenied, ValueError)):
        current.restore()
    assert not current.ready and not list(current.local.iterdir())


@pytest.mark.parametrize("damage", ["extra", "missing", "payload", "scope", "table_owner", "principal", "volume_owner", "warehouse"])
def test_warm_never_bypasses_current_scope_integrity_or_permissions(setup, monkeypatch, damage):
    client, _, runtime, writer, generation, _ = setup
    warm(setup)
    if damage == "extra":
        row = writer.store.make_record("jobs", project_id="unlisted", package_hash="package", context_id="context",
                                       source_path="state/unlisted.json", source_table="json", source_key="x", payload={})
        writer.store.upsert_records("jobs", [row], migration_id=generation)
    elif damage == "missing":
        client.rows["user_catalog.workbench.projects"].pop()
    elif damage in ("payload", "scope"):
        row = client.rows["user_catalog.workbench.projects"][0]
        if damage == "payload":
            row["payload"] = '{"tampered":true}'
        else:
            row["project_id"] = "wrong-scope"
            row["content_hash"] = ds._content_hash(row)
    elif damage == "table_owner":
        client.infos["user_catalog.workbench.projects"].owner = "someone-else"
    elif damage == "principal":
        client.user.id = "another-user"
    elif damage == "volume_owner":
        client.volume_owner = "someone-else"
    else:
        monkeypatch.setattr(client.statement_execution, "execute_statement", lambda **kw: (_ for _ in ()).throw(PermissionDenied("denied")))
    current = runtime("integrity-failure")
    with pytest.raises((PermissionError, ValueError, ds.DeltaStatementError, PermissionDenied)):
        current.restore()
    assert not current.ready and not list(current.local.iterdir())


def test_commit_primes_cache_and_failed_write_never_publishes_local_changes(setup, monkeypatch):
    _, _, runtime, _, old_generation, cache = setup
    first = warm(setup)
    old_entry = cache._entry
    old_path = old_entry[1][0][1]
    write_json(first.local / "state/active.json", {"value": "new"})
    new_generation = first.flush()
    assert new_generation != old_generation
    assert cache._entry is not old_entry and cache._entry[0][2] == new_generation
    assert not old_path.exists()
    second = runtime("new-generation")
    monkeypatch.setattr(second, "_database", lambda *a: pytest.fail("first restore reconstructed database"))
    assert second.restore() and second.generation == new_generation
    assert json.loads((second.local / "state/active.json").read_bytes()) == {"value": "new"}
    committed_entry = cache._entry
    write_json(second.local / "state/active.json", {"value": "never committed"})
    monkeypatch.setattr(second.artifacts, "put", lambda *a: (_ for _ in ()).throw(RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        second.flush()
    assert cache._entry is committed_entry
    third = runtime("after-failed-write")
    assert third.restore() and third.generation == new_generation
    assert json.loads((third.local / "state/active.json").read_bytes()) == {"value": "new"}


def test_first_restore_after_commit_is_warm_and_reauthorizes(setup, monkeypatch):
    client, _, runtime, writer, _, cache = setup
    write_json(writer.local / "state/active.json", {"value": "newly committed"})
    generation = writer.flush()
    assert cache._entry is not None and cache._entry[0][2] == generation
    me = client.current_user.me
    identities = []
    monkeypatch.setattr(client.current_user, "me", lambda: (identities.append(True), me())[1])
    current = runtime("immediate-warm")
    monkeypatch.setattr(current, "_database", lambda *args: pytest.fail("unexpected reconstruction"))
    assert current.restore(initialize=False)
    assert identities and current.generation == generation
    assert json.loads((current.local / "state/active.json").read_bytes()) == {"value": "newly committed"}
    client.volume_owner = "revoked"
    denied = runtime("immediate-warm-revoked")
    with pytest.raises(PermissionError):
        denied.restore(initialize=False)
    assert not denied.ready


def test_post_commit_candidate_is_reconstructed_not_a_copy_of_local_files(setup, monkeypatch):
    _, _, runtime, writer, _, cache = setup
    path = writer.local / "projects/projects.sqlite"
    deleted = "discarded-private-row-" * 8
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA secure_delete=OFF")
        db.execute("INSERT INTO projects VALUES (?,?)", ("deleted", deleted))
        db.execute("DELETE FROM projects WHERE id=?", ("deleted",))
    assert deleted.encode() in path.read_bytes()
    (writer.local / "state/active.json").write_text('{ "value" : "new" }', encoding="utf-8")
    generation = writer.flush()
    assert cache._entry is not None
    files = {name: path.read_bytes() for name, path, _, _ in cache._entry[1]}
    assert deleted.encode() not in files["projects/projects.sqlite"]
    assert files["state/active.json"] == b'{"value":"new"}'
    current = runtime("canonical-immediate-warm")
    monkeypatch.setattr(current, "_database", lambda *args: pytest.fail("unexpected reconstruction"))
    assert current.restore() and current.generation == generation
    with closing(sqlite3.connect(current.local / "projects/projects.sqlite")) as db:
        assert db.execute("SELECT id FROM projects").fetchall() == [("p1",)]


@pytest.mark.parametrize("failure", ["append", "readback", "different-receipt", "different-rows"])
def test_ambiguous_or_inexact_commit_never_primes(setup, monkeypatch, failure):
    _, _, _, writer, _, cache = setup
    write_json(writer.local / "state/active.json", {"value": "changed"})
    if failure == "append":
        upsert = writer.store.upsert_records
        def append_then_fail(table, rows, **kwargs):
            result = upsert(table, rows, **kwargs)
            if table == "migration_receipts":
                raise RuntimeError("lost receipt response")
            return result
        monkeypatch.setattr(writer.store, "upsert_records", append_then_fail)
    elif failure in ("readback", "different-receipt"):
        latest = writer._latest
        calls = []
        def changed_latest():
            row = latest()
            calls.append(True)
            if len(calls) == 2:
                if failure == "readback":
                    raise RuntimeError("lost receipt readback")
                return dict(row, source_key="not-the-submitted-receipt")
            return row
        monkeypatch.setattr(writer, "_latest", changed_latest)
    else:
        records = writer._records
        def changed_records(*args):
            result = records(*args)
            result["settings"][0] = dict(result["settings"][0], payload='{"different":true}')
            return result
        monkeypatch.setattr(writer, "_records", changed_records)
    if failure in ("append", "readback"):
        with pytest.raises(RuntimeError):
            writer.flush()
    else:
        assert writer.flush()
    assert cache._entry is None


def test_post_commit_admission_exception_cannot_fail_successful_commit(setup, monkeypatch):
    _, _, runtime, writer, _, cache = setup
    hydrate = dr.DeltaRuntime._hydrate_generation
    def fail_prime(self, *args, **kwargs):
        if kwargs.get("prime_only"):
            raise RuntimeError("optional admission failed")
        return hydrate(self, *args, **kwargs)
    monkeypatch.setattr(dr.DeltaRuntime, "_hydrate_generation", fail_prime)
    generation = writer.flush()
    assert writer.ready and writer.generation == generation and cache._entry is None
    assert runtime("after-admission-failure").restore()


def test_post_commit_cleanup_exception_never_publishes(setup, monkeypatch):
    _, _, _, writer, _, cache = setup
    temporary_directory = dr.tempfile.TemporaryDirectory

    class FailedCleanup:
        def __init__(self, **kwargs):
            self.inner = temporary_directory(**kwargs)

        def __enter__(self):
            return self.inner.__enter__()

        def cleanup(self):
            self.inner.cleanup()
            raise OSError("private staging cleanup failed")

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

    def temporary(**kwargs):
        return (FailedCleanup(**kwargs) if kwargs.get("prefix") == "workbench-hydrate-"
                else temporary_directory(**kwargs))

    monkeypatch.setattr(dr.tempfile, "TemporaryDirectory", temporary)
    generation = writer.flush()
    assert writer.ready and writer.generation == generation
    assert cache._entry is None and not cache._capturing


@pytest.mark.parametrize("mode", ["limit", "no-token"])
def test_post_commit_admission_requires_budget_and_current_token(setup, mode):
    _, _, _, writer, _, cache = setup
    if mode == "limit":
        cache.max_bytes = 1
    else:
        writer.forbidden = b""
    generation = writer.flush()
    assert writer.ready and writer.generation == generation and cache._entry is None


def test_post_commit_warm_snapshot_rechecks_sources_and_current_token(setup):
    client, _, runtime, writer, _, cache = setup
    marker = "new-forwarded-token-marker"
    write_json(writer.local / "state/active.json", {"value": marker})
    generation = writer.flush()
    assert cache._entry is not None and cache._entry[0][2] == generation
    with pytest.raises(ValueError, match="Credential material"):
        runtime("primed-new-token", token=marker).restore(initialize=False)
    assert cache._entry is None

    generation = writer.flush()
    assert cache._entry is not None and cache._entry[0][2] == generation
    del client.objects[next(iter(writer.sources.values()))]
    current = runtime("primed-source-missing")
    with pytest.raises(NotFound):
        current.restore(initialize=False)
    assert not current.ready and not list(current.local.iterdir())


def test_commit_does_not_wait_for_concurrent_capture_or_publish_unverified_state(setup):
    _, _, runtime, writer, _, cache = setup
    entered, release = Event(), Event()
    def hold_capture():
        with cache.capture_slot() as acquired:
            assert acquired
            entered.set()
            assert release.wait(10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(hold_capture)
        try:
            assert entered.wait(10)
            generation = writer.flush()
            assert writer.ready and cache._entry is None
        finally:
            release.set()
        holder.result(timeout=10)
    assert not cache._capturing
    assert runtime("after-contended-commit").restore()
    assert cache._entry[0][2] == generation


def test_mutation_still_restores_audit_tail_and_commits_it(setup):
    _, _, runtime, _, _, _ = setup
    first = warm(setup)
    first.restore_audit()
    path = first.local / "state/audit.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    AuditLog(path).append({"message": "tail"})
    first.flush_audit()
    second = runtime("mutation")
    assert second.restore()
    second.restore_audit()
    records, valid = AuditLog(second.local / "state/audit.sqlite").records()
    assert valid and records[-1]["message"] == "tail"
    new_generation = second.flush()
    third = runtime("audit-after-commit")
    assert third.restore() and third.generation == new_generation
    third.restore_audit()
    records, valid = AuditLog(third.local / "state/audit.sqlite").records()
    assert valid and records[-1]["message"] == "tail"


def test_cache_requires_current_token_and_fails_closed_on_corruption(setup):
    _, _, runtime, _, _, cache = setup
    no_token = runtime("without-token", token="")
    assert no_token.restore() and cache._entry is None
    warm(setup)
    _, files, _, _ = cache._entry
    marker = next(path.read_text() for name, path, _, _ in files if name == "state/active.json")
    with pytest.raises(ValueError, match="Credential material"):
        runtime("new-token", token=marker).restore()
    assert cache._entry is None
    cold = runtime("cold-again")
    assert cold.restore() and cache._entry is not None
    _, files, _, _ = cache._entry
    path = files[0][1]
    path.write_bytes(path.read_bytes() + b"corrupt")
    current = runtime("corrupt-cache")
    with pytest.raises(ValueError, match="checksum"):
        current.restore()
    assert not current.ready and cache._entry is None


def test_admission_fails_open_to_normal_restore(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    cache.max_bytes = 1
    first = runtime("limited")
    assert first.restore() and first.ready and cache._entry is None
    second = runtime("limited-again")
    calls = []
    original = second._database
    monkeypatch.setattr(second, "_database", lambda *a: (calls.append(True), original(*a))[1])
    assert second.restore() and calls and cache._entry is None


def test_namespace_generation_and_receipt_keys_are_isolated(setup, tmp_path):
    _, _, _, _, _, cache = setup
    warm(setup)
    key = cache._entry[0]
    file = tmp_path / "key-test"
    file.write_bytes(b"data")
    for index in range(len(key)):
        cache.clear()
        with cache.capture_slot() as acquired:
            assert acquired and cache.capture(key, tmp_path, {file.name}, {}, scan_file)
        changed = list(key)
        changed[index] += "other"
        with cache.borrow(tuple(changed), scan_file) as value:
            assert value is None
        assert cache._entry is None
    with cache.capture_slot() as acquired:
        assert acquired and cache.capture(key, tmp_path, {file.name}, {}, scan_file)
    with cache.borrow(key, scan_file) as value:
        assert value is not None


def test_different_root_and_principal_cannot_reuse_snapshot(setup, monkeypatch):
    client, identity, runtime, _, _, _ = setup
    warm(setup)
    other_root = runtime("other-root", remote=REMOTE + "-other")
    rebuilt = []
    original = other_root._database
    monkeypatch.setattr(other_root, "_database", lambda *a: (rebuilt.append(True), original(*a))[1])
    assert other_root.restore() and rebuilt
    client.user = SimpleNamespace(id="user-456", user_name="second@example.com")
    client.catalog_owner = client.schema_owner = client.volume_owner = client.user.user_name
    for info in client.infos.values():
        info.owner = client.user.user_name
    identity.user_id = client.user.id
    other_user = runtime("other-user")
    assert other_user.restore() is False
    assert other_user.ready and not list(other_user.local.iterdir())


def test_failed_cold_restore_never_populates_snapshot(setup):
    client, _, runtime, writer, _, cache = setup
    del client.objects[next(iter(writer.sources.values()))]
    current = runtime("cold-failure")
    with pytest.raises(NotFound):
        current.restore()
    assert not current.ready and cache._entry is None
    assert not list(current.local.iterdir())


def test_failed_capture_releases_evidence_before_lease(setup, monkeypatch):
    client, _, runtime, writer, _, cache = setup
    del client.objects[next(iter(writer.sources.values()))]
    current = runtime("capture-cleanup")
    slot = cache.capture_slot
    observed = []

    @contextmanager
    def observe_slot():
        with slot() as acquired:
            try:
                yield acquired
            finally:
                observed.append((acquired, cache._capturing, current._snapshot_capture))

    monkeypatch.setattr(cache, "capture_slot", observe_slot)
    with pytest.raises(NotFound):
        current.restore()
    assert observed == [(True, True, None)]
    assert not cache._capturing and cache._entry is None


def test_failed_local_publication_does_not_populate_snapshot(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    monkeypatch.setattr(dr.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError("copy failed")))
    current = runtime("copy-failure")
    with pytest.raises(OSError, match="copy failed"):
        current.restore()
    assert not current.ready and cache._entry is None


def test_new_token_in_original_artifact_is_not_hidden_by_reconstructed_state(setup):
    from workbench.tests.test_delta_runtime import external_generation

    _, _, runtime, writer, _, cache = setup
    marker = "credential-only-in-original-artifact"
    write_json(writer.local / "state/active.json", {"value": marker})
    writer.flush()

    def edit(records):
        for row in records["settings"]:
            if row["source_path"] == "state/active.json":
                row["payload"] = '{"value":"delta replacement"}'

    external_generation(writer, edit)
    cold = runtime("original-evidence-cold")
    assert cold.restore() and cache._entry is not None
    assert marker.encode() not in (cold.local / "state/active.json").read_bytes()
    current = runtime("original-evidence-new-token", token=marker)
    with pytest.raises(ValueError, match="Credential material"):
        current.restore()
    assert not current.ready and cache._entry is None


def test_fresh_identity_and_mutation_initialization_are_never_cached(setup, monkeypatch):
    client, _, runtime, _, _, _ = setup
    warm(setup)
    events = []
    me = client.current_user.me
    ensure = dr.RequestStateStore.ensure_tables
    monkeypatch.setattr(client.current_user, "me", lambda: (events.append("identity"), me())[1])
    monkeypatch.setattr(dr.RequestStateStore, "ensure_tables",
                        lambda self: (events.append("ensure"), ensure(self))[1])
    assert runtime("fresh-read").restore(initialize=False)
    assert events == ["identity"]
    events.clear()
    assert runtime("fresh-mutation").restore(initialize=True)
    assert events == ["ensure", "identity"]


def test_other_workspace_cannot_see_previous_workspace_snapshot(setup):
    client, identity, runtime, _, _, _ = setup
    warm(setup)
    client.config.host = "https://different.cloud.databricks.com"
    identity.host = client.config.host
    other = runtime("other-workspace")
    assert other.restore() is False
    assert other.ready and not list(other.local.iterdir())


def test_corrupt_cached_working_file_is_never_used(setup):
    _, _, runtime, _, _, cache = setup
    warm(setup)
    _, files, _, _ = cache._entry
    path = files[0][1]
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    current = runtime("corrupt-evidence")
    with pytest.raises(ValueError, match="checksum"):
        current.restore()
    assert not current.ready and cache._entry is None


def test_warm_source_download_does_not_lock_other_verified_restore(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    warm(setup)
    first, second = runtime("blocked-source"), runtime("parallel-warm")
    entered, release = Event(), Event()
    verify = first._verify_source

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return verify(*args, **kwargs)

    monkeypatch.setattr(first, "_verify_source", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(first.restore, initialize=False)
        try:
            assert entered.wait(10)
            parallel = executor.submit(second.restore, initialize=False)
            assert parallel.result(timeout=10)
            assert cache._borrowers == 1
        finally:
            release.set()
        assert pending.result(timeout=10)
    assert first.ready and second.ready and cache._borrowers == 0


def test_cold_source_download_does_not_block_another_cold_restore(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    first, second = runtime("blocked-cold"), runtime("parallel-cold")
    entered, release = Event(), Event()
    verify = first._verify_source

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return verify(*args, **kwargs)

    monkeypatch.setattr(first, "_verify_source", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(first.restore, initialize=False)
        try:
            assert entered.wait(10)
            parallel = executor.submit(second.restore, initialize=False)
            assert parallel.result(timeout=10)
            assert cache._entry is None and cache._capturing
        finally:
            release.set()
        assert pending.result(timeout=10)
    assert first.ready and second.ready and cache._entry is not None
    assert not cache._capturing


def test_pin_and_capture_lease_bound_replacement_without_blocking(tmp_path):
    cache = dr.VerifiedWorkingSnapshot(max_bytes=4096, reserve=10)
    file = tmp_path / "file.json"
    file.write_bytes(b"first")
    with cache.capture_slot() as acquired:
        assert acquired and cache.capture(("user-a",), tmp_path, {file.name}, {}, scan_file)
        with cache.capture_slot() as other:
            assert not other
    with cache.borrow(("user-a",), scan_file) as old:
        cached_path = old[0][file.name][0]
        assert cached_path.read_bytes() == b"first"
        with cache.borrow(("user-b",), scan_file) as wrong:
            assert wrong is None
        assert cache._entry is None
        with cache.capture_slot() as acquired:
            assert not acquired
        assert cached_path.read_bytes() == b"first"
    assert not cached_path.exists()
    file.write_bytes(b"second")
    with cache.capture_slot() as acquired:
        assert acquired and cache.capture(("user-b",), tmp_path, {file.name}, {}, scan_file)
    with cache.borrow(("user-b",), scan_file) as new:
        assert new[0][file.name][0].read_bytes() == b"second"
    assert cache._borrowers == 0 and not cache._capturing
    cache.clear()


def test_capture_checks_snapshot_size_without_memory_detection(tmp_path):
    cache = dr.VerifiedWorkingSnapshot(max_bytes=350, reserve=100)
    (tmp_path / "x").write_bytes(b"x" * 100)
    with cache.capture_slot() as acquired:
        assert acquired
        # 100 bytes plus the bounded name and per-file estimate exceeds budget.
        assert not cache.capture("key", tmp_path, {"x"}, {}, scan_file)
    cache.max_bytes = 500
    with cache.capture_slot() as acquired:
        assert acquired and cache.capture("key", tmp_path, {"x"}, {}, scan_file,
                                          lambda: pytest.fail("memory detection was called"))
    assert cache._entry is not None
    cache.clear()


def test_unavailable_cgroup_still_admits_and_reuses_real_snapshot(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    monkeypatch.setattr(dr, "_container_memory_state", lambda: None)
    first = runtime("no-cgroup-first")
    assert first.restore() and first.working_snapshot_cache_ready
    assert cache._entry is not None
    second = runtime("no-cgroup-second")
    monkeypatch.setattr(second, "_database", lambda *a: pytest.fail("database reconstructed"))
    assert second.restore(initialize=False) and second.working_snapshot_cache_hit


def test_snapshot_contains_no_token_or_external_sources(setup):
    _, _, _, writer, _, cache = setup
    warm(setup)
    _, files, _, _ = cache._entry
    assert files and all("source.xml" not in name for name, _, _, _ in files)
    assert all(TOKEN.encode() not in path.read_bytes() for _, path, _, _ in files)
    assert all(path.is_file() and writer.local not in path.parents for _, path, _, _ in files)


def test_corruption_after_borrow_is_detected_before_private_copy_is_used(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    warm(setup)
    path = cache._entry[1][0][1]
    current = runtime("corruption-during-copy")
    original_object = current._object
    changed = []

    def corrupt_after_borrow(reference):
        if cache._borrowers and not changed:
            raw = path.read_bytes()
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            changed.append(True)
        return original_object(reference)

    monkeypatch.setattr(current, "_object", corrupt_after_borrow)
    with pytest.raises(ValueError, match="checksum"):
        current.restore(initialize=False)
    assert changed and not current.ready and cache._entry is None
    assert not list(current.local.iterdir())


def test_snapshot_disk_unavailable_preserves_normal_restore(setup, monkeypatch):
    _, _, runtime, _, _, cache = setup
    temporary_directory = dr.tempfile.TemporaryDirectory

    def temporary(**kwargs):
        if kwargs.get("prefix") == "workbench-verified-snapshot-":
            raise OSError("temporary storage unavailable")
        return temporary_directory(**kwargs)

    monkeypatch.setattr(dr.tempfile, "TemporaryDirectory", temporary)
    current = runtime("no-snapshot-disk")
    assert current.restore(initialize=False) and current.ready
    assert not current.working_snapshot_cache_ready and cache._entry is None


def test_clear_invalidates_prepared_candidate_and_cleans_directory(tmp_path):
    cache = dr.VerifiedWorkingSnapshot(max_bytes=4096)
    (tmp_path / "file").write_bytes(b"data")
    with cache.capture_slot() as acquired:
        assert acquired
        candidate = cache.prepare("key", tmp_path, {"file"}, {}, scan_file)
        assert candidate is not None
        path = candidate[1][0][1]
        cache.clear()
        assert not cache.publish(candidate)
        assert path.exists()
    assert not path.exists() and cache._entry is None


def test_source_same_size_corruption_is_detected_on_warm_request(setup):
    client, _, runtime, writer, _, _ = setup
    warm(setup)
    path = next(iter(writer.sources.values()))
    original = client.objects[path]
    client.objects[path] = bytes([original[0] ^ 1]) + original[1:]
    current = runtime("same-size-source-corruption")
    with pytest.raises(ValueError, match="checksum"):
        current.restore(initialize=False)
    assert not current.ready and not list(current.local.iterdir())
