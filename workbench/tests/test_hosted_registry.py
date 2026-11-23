"""Direct registry reads against the real hosted mutation/Delta/Files contracts."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
from typing import Any

from databricks.sdk.errors import PermissionDenied
from fastapi import HTTPException
import pytest

from workbench import delta_state as ds
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_registry import registry_response
from workbench.hosted_storage import provision
from workbench.projects import ProjectRegistry
from workbench.tests.test_hosted import (
    HOST, TOKEN_A2, TOKEN_B, XML, backend, browser, create, headers, offline,
)


def fresh(backend, tmp_path, token=TOKEN_A2):
    identity = backend.resolve(HOST, token, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    return DeltaRuntime(lambda: identity, remote, tmp_path, warehouse="warehouse", forbidden_token=token)


def committed(runtime: DeltaRuntime) -> dict[str, Any]:
    receipt = runtime._latest()
    assert receipt is not None, "Test setup must publish a committed generation"
    return receipt


@pytest.fixture
def created(backend):
    with browser(backend) as client:
        project = create(client, "Committed project")
    return project


@pytest.fixture
def uploaded(backend):
    with browser(backend) as client:
        project = create(client, "With XML")
        response = client.post(f"/api/projects/{project}/packages/upload", params={"stage_only": "true"},
                               headers=headers(), files={"files": ("source.xml", XML, "application/xml")})
        assert response.status_code == 202, response.text
        assert any(path.endswith("/uploads/" + sha256(XML).hexdigest() + ".xml")
                   and data == XML for path, data in backend.objects.items())
        response = client.post(f"/api/projects/{project}/analyze", headers=headers())
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        detail = client.get(f"/api/projects/{project}", headers=headers()).json()
        assert detail["ready_package_count"] == 1
    return project, job_id, detail


def latest_row(backend, table, generation):
    return next(row for name, rows in backend.rows.items() if name.endswith("." + table)
                for row in rows if row["migration_id"] == generation
                and row["source_path"] == "projects/projects.sqlite")


def rewrite_manifest(runtime, backend, change):
    """Publish altered evidence for a corruption test, preserving receipt hashes."""
    receipt = committed(runtime)
    payload = json.loads(receipt["payload"])
    manifest = json.loads(runtime._object(payload["manifest"]))
    change(manifest)
    reference = runtime.artifacts.put(ds._json(manifest).encode())
    payload["manifest"] = reference
    for name, rows in backend.rows.items():
        if name.endswith(".migration_receipts"):
            for row in rows:
                if row["record_id"] == receipt["record_id"]:
                    row.update(payload=ds._json(payload), artifact_path=reference["volume_path"],
                               artifact_sha256=reference["sha256"], byte_size=reference["byte_size"])
                    row["content_hash"] = ds._content_hash(row)


def rewrite_registry_row(runtime, backend, table, change):
    generation = committed(runtime)["migration_id"]
    row = latest_row(backend, table, generation)
    old_pair = row["record_id"], row["content_hash"]
    change(row)
    row["content_hash"] = ds._content_hash(row)

    def update(manifest):
        item = next(item for item in manifest["inventory"][table]
                    if (item["record_id"], item["content_hash"]) == old_pair)
        item.update({k: row[k] for k in item})

    rewrite_manifest(runtime, backend, update)


def journal_rows(backend):
    return [row for name, rows in backend.rows.items() if name.endswith(".audit_events")
            for row in rows if row["context_id"].startswith("hosted-registry-read-v1:")]


def test_uploaded_estate_projects_details_jobs_without_hydration(backend, uploaded, tmp_path, monkeypatch):
    project, job, expected = uploaded
    runtime = fresh(backend, tmp_path / "unused")
    receipt = committed(runtime)
    manifest_path = json.loads(receipt["payload"])["manifest"]["volume_path"]
    before = deepcopy(runtime.__dict__)
    objects = deepcopy(backend.objects)
    content = deepcopy({name: rows for name, rows in backend.rows.items() if not name.endswith(".audit_events")})
    prior_audits = deepcopy(journal_rows(backend))
    backend.calls.clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Registry reads must not restore SQLite, parse XML, replay audit or load a model")

    import workbench.app as local
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(local, "analyze_package", forbidden)
    monkeypatch.setattr(local.PackageIndex, "load", forbidden)
    for method in ("restore", "restore_audit", "flush", "flush_audit", "_records", "_database"):
        monkeypatch.setattr(runtime, method, forbidden)
    monkeypatch.setattr(runtime.store, "ensure_tables", forbidden)
    download = runtime.store.client.files.download

    def only_manifest(path):
        assert path == manifest_path, "Unexpected source/model/DDL/checkpoint/metadata download"
        return download(path)

    monkeypatch.setattr(runtime.store.client.files, "download", only_manifest)
    listing = registry_response(runtime, "/api/projects")
    detail = registry_response(runtime, f"/api/projects/{project}")
    actual_job = registry_response(runtime, f"/api/projects/{project}/jobs/{job}")
    expected.update(local_only=False, authentication="databricks_obo")
    assert detail == expected
    assert listing == {"items": [{k: v for k, v in expected.items() if k not in {"packages", "jobs"}}]}
    assert actual_job == expected["jobs"][0]
    assert not runtime.local.exists()
    for key in ("ready", "generation", "sequence", "sources", "_audit_sequences"):
        assert runtime.__dict__[key] == before[key]
    assert backend.objects == objects
    assert {name: rows for name, rows in backend.rows.items() if not name.endswith(".audit_events")} == content
    statements = [sql for _, action, sql in backend.calls if action == "sql"]
    assert all(not sql.startswith("CREATE") and "artifact_manifest" not in sql for sql in statements)
    assert all(".`audit_events`" in sql for sql in statements if sql.startswith("MERGE"))
    assert all(any(".`" + table + "`" in sql for table in ("migration_receipts", "projects", "packages", "jobs", "audit_events"))
               for sql in statements)
    all_audits = journal_rows(backend)
    prior_contexts = {row["context_id"] for row in prior_audits}
    assert [row for row in all_audits if row["context_id"] in prior_contexts] == prior_audits
    audits = [row for row in all_audits if row["context_id"] not in prior_contexts]
    assert len(all_audits) == len(prior_audits) + 3
    assert len(audits) == 3 and len({row["context_id"] for row in audits}) == 3
    assert {json.loads(row["payload"])["request_path"] for row in audits} == {
        "/api/projects", f"/api/projects/{project}", f"/api/projects/{project}/jobs/{job}"}
    for row in audits:
        payload = json.loads(row["payload"])
        assert set(payload) == {"event_id", "method", "request_path", "owner_id", "committed_generation", "status_code"}
        assert payload["owner_id"] == "alice-id" and payload["committed_generation"] == receipt["migration_id"]
        assert payload["status_code"] == 200 and row["source_key"] == payload["event_id"]
        assert row["content_hash"] == ds._content_hash(row)
        assert "With XML" not in row["payload"] and TOKEN_A2 not in ds._json(row)


@pytest.mark.parametrize("table", ["projects", "packages", "jobs"])
def test_missing_any_registry_row_fails_closed(backend, uploaded, tmp_path, table):
    project, _, _ = uploaded
    runtime = fresh(backend, tmp_path)
    generation = committed(runtime)["migration_id"]
    row = latest_row(backend, table, generation)
    prior_audits = deepcopy(journal_rows(backend))
    for name, rows in backend.rows.items():
        if name.endswith("." + table):
            rows.remove(row)
            break
    with pytest.raises(ValueError, match="Incomplete or unexpected registry scope"):
        registry_response(runtime, f"/api/projects/{project}")
    assert journal_rows(backend) == prior_audits


@pytest.mark.parametrize("failure", ["missing", "owner", "schema", "sql", "principal"])
def test_hosted_registry_routes_never_restore_and_recheck_fresh_authorization(
        backend, uploaded, monkeypatch, failure):
    project, job, expected = uploaded
    original = backend.client
    clients, principals = [], []
    revoked = False

    def forbidden(*args, **kwargs):
        pytest.fail("Hosted project list/detail/job must not restore an execution cache")

    def tracked(token):
        resolved = original(token)
        clients.append((token, resolved))
        me = resolved.current_user.me

        def principal():
            principals.append(token)
            if revoked and failure == "principal":
                raise PermissionDenied("sensitive revoked principal " + token)
            return me()

        resolved.current_user = SimpleNamespace(me=principal)
        if revoked and failure == "sql":
            def denied(**kwargs):
                raise PermissionDenied("sensitive revoked SQL privilege " + token)
            resolved.statement_execution.execute_statement = denied
        return resolved

    from types import SimpleNamespace
    monkeypatch.setattr(DeltaRuntime, "restore", forbidden)
    monkeypatch.setattr(backend, "client", tracked)
    paths = ("/api/projects", f"/api/projects/{project}", f"/api/projects/{project}/jobs/{job}")
    expected = {**expected, "local_only": False, "authentication": "databricks_obo"}
    responses = ({"items": [{k: v for k, v in expected.items() if k not in {"packages", "jobs"}}]},
                 expected, expected["jobs"][0])
    with browser(backend) as client:
        for path, body in zip(paths, responses, strict=True):
            response = client.get(path, headers=headers(TOKEN_A2))
            assert response.status_code == 200, response.text
            assert response.json() == body
        prior_audits = deepcopy(journal_rows(backend))
        revoked = True
        name = next(name for name in backend.infos if name.endswith(".migration_receipts"))
        if failure == "missing":
            del backend.infos[name]
        elif failure == "owner":
            backend.infos[name].owner = "someone-else@example.com"
        elif failure == "schema":
            backend.infos[name].columns = []
        for path in paths:
            response = client.get(path, headers=headers(TOKEN_A2))
            assert response.status_code == (401 if failure == "principal" else 503), response.text
            assert TOKEN_A2 not in response.text and "With XML" not in response.text
        assert journal_rows(backend) == prior_audits
    assert len(clients) == 2 * len(paths)
    assert len({id(resolved) for _, resolved in clients}) == len(clients)
    assert {token for token, _ in clients} == {TOKEN_A2}
    assert len(principals) >= len(clients) and set(principals) == {TOKEN_A2}


@pytest.mark.parametrize("field,value", [("payload", "{}"), ("content_hash", "0" * 64)])
def test_row_tamper_fails_before_ack(backend, created, tmp_path, field, value):
    runtime = fresh(backend, tmp_path)
    latest_row(backend, "projects", committed(runtime)["migration_id"])[field] = value
    with pytest.raises(ValueError, match="checksum"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


@pytest.mark.parametrize("field,value", [("format", 2), ("user_id", "bob-id"),
                                         ("workspace_id", "https://elsewhere.example.com"),
                                         ("sequence", 123456), ("generation", "0" * 32)])
def test_manifest_identity_and_generation_validation(backend, created, tmp_path, field, value):
    runtime = fresh(backend, tmp_path)
    rewrite_manifest(runtime, backend, lambda manifest: manifest.update({field: value}))
    with pytest.raises(PermissionError, match="generation namespace"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_receipt_checksum_and_manifest_bytes_are_verified(backend, created, tmp_path):
    runtime = fresh(backend, tmp_path)
    receipt = committed(runtime)
    reference = json.loads(receipt["payload"])["manifest"]
    backend.objects[reference["volume_path"]] = b"x" * reference["byte_size"]
    with pytest.raises(ValueError, match="checksum"):
        registry_response(runtime, "/api/projects")
    for name, rows in backend.rows.items():
        if name.endswith(".migration_receipts"):
            rows[-1]["payload"] = "{}"
    with pytest.raises(ValueError, match="receipt"):
        registry_response(runtime, "/api/projects")


def test_empty_unknown_project_and_user_isolation(backend, created, tmp_path):
    with browser(backend) as client:
        bob = create(client, "Bob only", TOKEN_B)
    alice = fresh(backend, tmp_path / "alice")
    other = fresh(backend, tmp_path / "bob", TOKEN_B)
    assert [item["id"] for item in registry_response(alice, "/api/projects")["items"]] == [created]
    assert [item["id"] for item in registry_response(other, "/api/projects")["items"]] == [bob]
    for runtime, project in ((alice, bob), (other, created)):
        with pytest.raises(HTTPException) as error:
            registry_response(runtime, f"/api/projects/{project}")
        assert error.value.status_code == 404 and error.value.detail == "Unknown project ID"
    # A provisioned schema with no committed receipt is a genuinely empty user.
    for name, rows in backend.rows.items():
        if name.endswith(".migration_receipts"):
            rows[:] = [row for row in rows if row["user_id"] != "bob-id"]
    empty = fresh(backend, tmp_path / "empty", TOKEN_B)
    assert registry_response(empty, "/api/projects") == {"items": []}
    assert not empty.ready and empty.generation is None and empty.sequence == 0
    with pytest.raises(HTTPException) as error:
        registry_response(empty, f"/api/projects/{bob}")
    assert error.value.status_code == 404
    assert any(json.loads(row["payload"])["committed_generation"] is None for row in journal_rows(backend))


def test_empty_legacy_head_and_uninitialized_tables_fail_closed(backend, tmp_path):
    runtime = fresh(backend, tmp_path)
    with pytest.raises(Exception):
        registry_response(runtime, "/api/projects")
    assert not any(action == "sql" and sql.startswith("CREATE") for _, action, sql in backend.calls)
    runtime.store.ensure_tables()  # Explicit provisioning is setup, never a read side effect.
    backend.objects[runtime.remote + "/HEAD.json"] = b"{}"
    with pytest.raises(ValueError, match="Legacy snapshot exists"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


@pytest.mark.parametrize("suffix,detail", [("/invalid", "Unknown project ID"),
                                          ("/" + "A" * 32, "Unknown project ID"),
                                          ("/{project}/jobs/invalid", "Unknown project job ID"),
                                          ("/{project}/jobs/" + "f" * 32, "Unknown project job ID"),
                                          ("/{project}/packages", "Unknown registry path")])
def test_invalid_and_unknown_paths_are_404(backend, created, tmp_path, suffix, detail):
    runtime = fresh(backend, tmp_path)
    with pytest.raises(HTTPException) as error:
        registry_response(runtime, "/api/projects" + suffix.format(project=created))
    assert error.value.status_code == 404
    assert error.value.detail == detail


@pytest.mark.parametrize("failure", ["denied", "lost", "tampered", "duplicate", "wrong_owner", "rewritten"])
def test_journal_failure_cannot_acknowledge_result(backend, created, tmp_path, monkeypatch, failure):
    runtime = fresh(backend, tmp_path)
    write, read = runtime.store.upsert_records, runtime.store.read_table

    def append(table, records, **kwargs):
        assert table == "audit_events"
        if failure == "denied":
            raise PermissionDenied("sensitive upstream message " + TOKEN_A2)
        if failure != "lost":
            return write(table, records, **kwargs)

    def readback(table, **kwargs):
        rows = read(table, **kwargs)
        if table == "audit_events" and rows:
            rows = deepcopy(rows)
            if failure == "tampered":
                rows[0]["payload"] = "{}"
            elif failure == "duplicate":
                rows.append(dict(rows[0]))
            elif failure == "wrong_owner":
                rows[0]["user_id"] = "bob-id"
                rows[0]["content_hash"] = ds._content_hash(rows[0])
            elif failure == "rewritten":
                # Even a self-consistent checksum cannot confirm a DIFFERENT event.
                rows[0]["payload"] = ds._json({"unrelated": True})
                rows[0]["content_hash"] = ds._content_hash(rows[0])
        return rows

    monkeypatch.setattr(runtime.store, "upsert_records", append)
    monkeypatch.setattr(runtime.store, "read_table", readback)
    with pytest.raises((ValueError, PermissionError, PermissionDenied)):
        registry_response(runtime, "/api/projects")
    assert not runtime.ready and runtime.generation is None


def test_scoped_pagination_and_created_at_ordering(backend, created, tmp_path):
    setup = fresh(backend, tmp_path / "setup")
    setup.restore()
    registry = ProjectRegistry(setup.local / "projects")
    for number in range(53):
        # All jobs have the same legacy project/package scope, forcing keyset paging.
        registry.save("jobs", created, {"id": f"{number:032x}", "project_id": created,
                                      "status": "completed", "created_at": f"2026-10-{number + 1:02d}"})
    setup.flush()
    runtime = fresh(backend, tmp_path / "read")
    backend.calls.clear()
    detail = registry_response(runtime, f"/api/projects/{created}")
    assert len(detail["jobs"]) == 53
    assert [item["id"] for item in detail["jobs"]] == [f"{n:032x}" for n in reversed(range(53))]
    reads = [sql for _, action, sql in backend.calls if action == "sql" and ".`jobs`" in sql]
    assert len(reads) == 2 and ":cursor_id" in reads[-1]


@pytest.mark.parametrize("external_document", [False, True])
def test_only_genuinely_externalized_exact_registry_columns_are_downloaded(
        backend, created, tmp_path, monkeypatch, external_document):
    runtime = fresh(backend, tmp_path)
    allowed = set()

    def externalize(row):
        payload = json.loads(row["payload"])
        record = json.loads(payload["record"])
        record["description"] = "metadata " * 2000
        reference = runtime.artifacts.put(ds._json(record).encode())
        allowed.add(reference["volume_path"])
        payload["record"] = {"$artifact": reference, "encoding": "utf-8"}
        # This unused convenience value must not be recursively decoded.
        payload["record_decoded"] = {"$artifact": {"volume_path": "/Volumes/evil/source/xml/file.xml",
                                                   "sha256": "0" * 64, "byte_size": 1, "source": True},
                                     "encoding": "json"}
        if external_document:
            reference = runtime.artifacts.put(ds._json(payload).encode())
            allowed.add(reference["volume_path"])
            payload = {"$artifact": reference, "encoding": "json"}
        row["payload"] = ds._json(payload)

    rewrite_registry_row(runtime, backend, "projects", externalize)
    allowed.add(json.loads(committed(runtime)["payload"])["manifest"]["volume_path"])
    download = runtime.store.client.files.download

    def checked_download(path):
        assert path in allowed
        return download(path)

    monkeypatch.setattr(runtime.store.client.files, "download", checked_download)
    result = registry_response(runtime, f"/api/projects/{created}")
    assert result["description"] == "metadata " * 2000


def test_registry_source_references_and_column_identity_tamper_rejected(backend, created, tmp_path):
    runtime = fresh(backend, tmp_path)

    def corrupt(row):
        payload = json.loads(row["payload"])
        payload["id"] = "b" * 32
        row["payload"] = ds._json(payload)

    rewrite_registry_row(runtime, backend, "projects", corrupt)
    with pytest.raises(ValueError, match="source identity"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_inventory_duplicate_entries_rejected(backend, created, tmp_path):
    runtime = fresh(backend, tmp_path)
    rewrite_manifest(runtime, backend, lambda manifest: manifest["inventory"]["projects"].append(
        dict(manifest["inventory"]["projects"][0])))
    with pytest.raises(ValueError, match="Incomplete or unexpected registry scope"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


@pytest.mark.parametrize("field,value", [("user_id", "bob-id"), ("workspace_id", "https://elsewhere.example.com"),
                                         ("migration_id", "wrong-generation"), ("context_id", "project:other"),
                                         ("record_id", "0" * 64)])
def test_read_envelopes_do_not_trust_store_predicates(backend, created, tmp_path, monkeypatch, field, value):
    runtime = fresh(backend, tmp_path)
    read = runtime.store.read_table

    def untrusted(table, **kwargs):
        rows = read(table, **kwargs)
        if table == "projects" and rows:
            rows[0][field] = value
            rows[0]["content_hash"] = ds._content_hash(rows[0])
        return rows

    monkeypatch.setattr(runtime.store, "read_table", untrusted)
    with pytest.raises((ValueError, PermissionError)):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_external_source_reference_rejected_before_download(backend, created, tmp_path, monkeypatch):
    runtime = fresh(backend, tmp_path)

    def corrupt(row):
        payload = json.loads(row["payload"])
        payload["record"] = {"$artifact": {"volume_path": "/Volumes/source/exports/xml/secret.xml",
                                           "sha256": "0" * 64, "byte_size": 1, "source": True},
                             "encoding": "utf-8"}
        row["payload"] = ds._json(payload)

    rewrite_registry_row(runtime, backend, "projects", corrupt)
    manifest_path = json.loads(committed(runtime)["payload"])["manifest"]["volume_path"]
    download = runtime.store.client.files.download

    def no_source(path):
        assert path == manifest_path
        return download(path)

    monkeypatch.setattr(runtime.store.client.files, "download", no_source)
    with pytest.raises(ValueError, match="external registry metadata reference"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_unknown_project_cannot_acknowledge_when_audit_is_unavailable(backend, created, tmp_path, monkeypatch):
    runtime = fresh(backend, tmp_path)

    def denied(*args, **kwargs):
        raise PermissionDenied("audit unavailable")

    monkeypatch.setattr(runtime.store, "upsert_records", denied)
    with pytest.raises(PermissionDenied):
        registry_response(runtime, "/api/projects/" + "f" * 32)


def test_nonadvancing_page_and_unexpected_scope_records_fail(backend, created, tmp_path, monkeypatch):
    runtime = fresh(backend, tmp_path)
    read = runtime.store.read_table

    def repeats(table, **kwargs):
        rows = read(table, **kwargs)
        return rows * 50 if table == "projects" else rows

    monkeypatch.setattr(runtime.store, "read_table", repeats)
    with pytest.raises(ValueError, match="Non-advancing registry cursor"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_full_scope_evidence_verified_before_selecting_registry_sources(backend, created, tmp_path):
    runtime = fresh(backend, tmp_path)
    generation = committed(runtime)["migration_id"]
    original = latest_row(backend, "projects", generation)
    # A non-registry row in the same scope is evidence too. It must not be
    # silently dropped before the full expected scope comparison.
    extra = runtime.store.make_record(
        "projects", project_id=created, package_hash=original["package_hash"], context_id="legacy",
        source_path="unrelated/metadata.json", source_table="json", source_key="unrelated", payload={})
    runtime.store.upsert_records("projects", [extra], migration_id=generation)
    with pytest.raises(ValueError, match="Incomplete or unexpected registry scope"):
        registry_response(runtime, "/api/projects")
    assert not journal_rows(backend)


def test_project_package_job_created_at_ordering_and_counts(backend, created, tmp_path):
    setup = fresh(backend, tmp_path / "setup")
    setup.restore()
    registry = ProjectRegistry(setup.local / "projects")
    # Intentionally write opposite to creation time, so record_id/row order cannot
    # accidentally supply the requested ordering.
    for number in (2, 1, 3):
        project = f"{number:032x}"
        value = {"id": project, "name": f"Project {number}", "created_at": f"2026-01-0{number}"}
        with closing(registry.connect()) as db, db:
            db.execute("INSERT INTO projects VALUES (?,?)", (project, ds._json(value)))
        for item in (2, 1, 3):
            registry.save("packages", project, {"sha256": f"{item:064x}", "created_at": f"2026-02-0{item}",
                                                "status": "completed" if item == 1 else "staged"})
    setup.flush()
    runtime = fresh(backend, tmp_path / "read")
    listing = registry_response(runtime, "/api/projects")["items"]
    assert [item["id"] for item in listing] == [created, f"{3:032x}", f"{2:032x}", f"{1:032x}"]
    detail = registry_response(runtime, f"/api/projects/{1:032x}")
    assert detail["package_count"] == 3 and detail["ready_package_count"] == 1
    assert [item["sha256"] for item in detail["packages"]] == [f"{item:064x}" for item in (3, 2, 1)]