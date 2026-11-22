"""Generation-wide reads through the existing Statement API fake, never cloud."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from workbench import delta_state as ds
from workbench.delta_runtime import DeltaRuntime, RequestStateStore
from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
from workbench.tests.test_delta_runtime import estate, rows, sources


@pytest.fixture
def setup(tmp_path):
    client = RuntimeStatementClient()
    identity = SimpleNamespace(client=client, host=client.config.host, user_id=client.user.id)
    remote = "/Volumes/user_catalog/workbench/artifacts/users/test-user"

    def runtime(name, store=None):
        return DeltaRuntime(lambda: identity, remote, tmp_path / name, warehouse="warehouse", store=store)

    return client, identity, runtime


def seed(client, store, generation, count=117):
    store.ensure_tables()
    records = {table: [] for table in ds.TABLES if table != "migration_receipts"}
    for i in range(count):
        records["jobs"].append(store.make_record(
            "jobs", project_id=f"project-{i % 40:02d}", package_hash="package",
            context_id="context-' OR 1=1 --", source_path="state/jobs/item.json",
            source_table="json", source_key=str(i), payload={"value": i}))
    store.upsert_records("jobs", records["jobs"], migration_id=generation)
    client.calls.clear()
    return ds._inventory(records)


def queries(client):
    return [call for call in client.calls if call["statement"].startswith("SELECT")]


def test_real_fake_paging_matches_legacy_and_reduces_scope_queries(setup):
    client, identity, runtime = setup
    new = runtime("new")
    generation = "generation-' OR 1=1 --"
    inventory = seed(client, new.store, generation)
    saved = deepcopy(inventory)
    actual = new._records(inventory, generation)
    calls = queries(client)
    pages = [c for c in calls if "count(*)" not in c["statement"]]
    assert len(calls) == 4  # One count plus three pages, not forty scope reads.
    assert len(pages) == 3
    assert all("LIMIT 50" in c["statement"] for c in pages)
    assert all(generation not in c["statement"] and "OR 1=1" not in c["statement"] for c in calls)
    assert all(any(p.name == "migration_id" and p.value == generation for p in c["parameters"]) for c in calls)
    assert inventory == saved
    legacy = ds.DeltaStateStore(client, "warehouse", "user_catalog", "workbench", identity.user_id)
    client.calls.clear()
    expected = runtime("legacy", legacy)._records(inventory, generation)
    assert actual == expected
    assert len(queries(client)) > 40


@pytest.mark.parametrize("count,pages", [(0, 0), (1, 1), (50, 1), (51, 2), (100, 2)])
def test_count_bound_avoids_extra_empty_page(setup, count, pages):
    client, _, runtime = setup
    current = runtime("read")
    inventory = seed(client, current.store, "generation", count)
    assert len(current._records(inventory, "generation")["jobs"]) == count
    assert len([c for c in queries(client) if "count(*)" not in c["statement"]]) == pages


@pytest.mark.parametrize("damage", ["extra_scope", "empty_table", "missing", "payload", "inventory_scope", "duplicate", "replacement"])
def test_corrupt_or_unexpected_generation_fails_closed(setup, damage):
    client, _, runtime = setup
    current = runtime("read")
    inventory = seed(client, current.store, "generation", 2)
    jobs = client.rows["user_catalog.workbench.jobs"]
    if damage == "extra_scope":
        row = current.store.make_record("jobs", project_id="unlisted", package_hash="package",
                                        context_id="context", source_path="state/jobs/item.json",
                                        source_table="json", source_key="extra", payload={})
        current.store.upsert_records("jobs", [row], migration_id="generation")
    elif damage == "empty_table":
        row = current.store.make_record("projects", project_id="unlisted", package_hash="package",
                                        context_id="context", source_path="state/item.json",
                                        source_table="json", source_key="extra", payload={})
        current.store.upsert_records("projects", [row], migration_id="generation")
    elif damage == "missing":
        jobs.pop()
    elif damage == "payload":
        jobs[0]["payload"] = '{"tampered":true}'
    elif damage == "inventory_scope":
        inventory["jobs"][0]["project_id"] = "wrong-scope"
    elif damage == "replacement":
        jobs[0]["project_id"] = "unlisted"
        jobs[0]["content_hash"] = ds._content_hash(jobs[0])
    else:
        jobs[1] = deepcopy(jobs[0])
    with pytest.raises(ValueError):
        current._records(inventory, "generation")


@pytest.mark.parametrize("change", ["principal", "catalog", "schema", "table_owner", "table_schema", "binding"])
def test_fresh_request_cannot_reuse_previous_authorization(setup, change):
    client, identity, runtime = setup
    first = runtime("first")
    inventory = seed(client, first.store, "generation", 1)
    assert first._records(inventory, "generation")["jobs"]
    fresh = runtime("fresh")
    if change == "principal":
        client.user.id = "another-user"
    elif change == "catalog":
        client.catalog_owner = "other"
    elif change == "schema":
        client.schema_owner = "other"
    elif change == "table_owner":
        client.infos["user_catalog.workbench.jobs"].owner = "other"
    elif change == "table_schema":
        client.infos["user_catalog.workbench.jobs"].columns = []
    else:
        fresh.store.user_id = "other"
    client.calls.clear()
    with pytest.raises((PermissionError, ValueError)):
        fresh._records(inventory, "generation")
    assert not queries(client)


def test_current_data_is_read_again_and_changed_generation_does_not_leak(setup):
    client, _, runtime = setup
    first = runtime("first")
    inventory = seed(client, first.store, "generation", 1)
    assert len(first._records(inventory, "generation")["jobs"]) == 1
    client.rows["user_catalog.workbench.jobs"][0]["payload"] = "tampered"
    with pytest.raises(ValueError, match="checksum"):
        runtime("second")._records(inventory, "generation")
    assert runtime("third")._records({table: [] for table in inventory}, "other-generation") == {
        table: [] for table in inventory}


def test_other_users_and_generations_are_excluded(setup):
    client, _, runtime = setup
    current = runtime("read")
    inventory = seed(client, current.store, "generation", 1)
    unrelated = deepcopy(client.rows["user_catalog.workbench.jobs"][0])
    unrelated["user_id"] = "other"
    unrelated["content_hash"] = ds._content_hash(unrelated)
    old = deepcopy(client.rows["user_catalog.workbench.jobs"][0])
    old["migration_id"] = "other-generation"
    client.rows["user_catalog.workbench.jobs"].extend([unrelated, old])
    result = current._records(inventory, "generation")
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["user_id"] == current.store.user_id
    assert result["jobs"][0]["migration_id"] == "generation"


def test_invalid_scope_is_rejected_even_with_matching_hash_and_inventory(setup):
    client, _, runtime = setup
    current = runtime("read")
    inventory = seed(client, current.store, "generation", 1)
    row = client.rows["user_catalog.workbench.jobs"][0]
    row["context_id"] = "bad\ncontext"
    row["content_hash"] = ds._content_hash(row)
    inventory["jobs"][0].update(context_id=row["context_id"], content_hash=row["content_hash"])
    with pytest.raises(ValueError, match="Invalid namespace"):
        current._records(inventory, "generation")


def test_actual_restore_and_flush_with_existing_statement_fake(setup, tmp_path):
    client, _, runtime = setup
    writer = runtime("writer")
    assert writer.restore() is False
    estate(writer.local)
    sources(writer)
    before = {path.relative_to(writer.local): rows(path) for path in writer.local.rglob("*")
              if path.suffix in {".sqlite", ".sqlite3"}}
    generation = writer.flush()
    client.calls.clear()
    restored = runtime("restored")
    assert restored.restore() is True
    assert restored.generation == generation
    for relative, expected in before.items():
        assert rows(restored.local / relative) == expected
    assert len([c for c in queries(client) if "count(*)" in c["statement"]]) == 1
    assert any("LIMIT 50" in c["statement"] and "migration_id" in c["statement"]
               and "count(*)" not in c["statement"] for c in queries(client))


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "migration_id"])
def test_page_rejects_bad_envelope_and_nonadvancing_response(setup, monkeypatch, field):
    client, _, runtime = setup
    current = runtime("read")
    inventory = seed(client, current.store, "generation", 51)
    page = current.store.generation_page

    def wrong_user(*args, **kwargs):
        result = page(*args, **kwargs)
        result[0][field] = "other"
        result[0]["content_hash"] = ds._content_hash(result[0])
        return result

    monkeypatch.setattr(current.store, "generation_page", wrong_user)
    with pytest.raises(ValueError, match="envelope"):
        current._records(inventory, "generation")
    monkeypatch.setattr(current.store, "generation_page", lambda table, generation, after=None: page(table, generation))
    with pytest.raises(ValueError, match="cursor"):
        current._records(inventory, "generation")