from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from databricks.sdk.errors import NotFound
import pytest

from workbench import delta_state as ds
from workbench import ten_workflow_fixtures as fixtures


ROOT = Path(__file__).resolve().parents[2]
XML = ROOT / "input" / "LumenExample.XML"


@pytest.fixture(scope="module")
def real_plan():
    return fixtures.build_plan(XML, SimpleNamespace(host="https://example.test", user_id="123"))


def test_real_ten_workflows_include_all_target_definitions_and_full_fields(real_plan):
    assert len(real_plan["workflows"]) == 10
    assert len(real_plan["targets"]) == 20
    assert real_plan["schema_count_per_catalog"] == 2
    assert real_plan["column_count_per_catalog"] == 696
    assert all(w["task_sessions"] and w["mappings"] and w["target_ids"] for w in real_plan["workflows"])
    assert all(s["fields"] for s in real_plan["sources"])
    assert all(len(t["xml_fields"]) == len(t["columns"]) for t in real_plan["targets"])
    assert len({tuple(t["xml_identity"]) for t in real_plan["targets"]}) == 20
    assert any(t["xml_identity"][-1] == "AE2E_AMT_PREP" for t in real_plan["targets"])
    assert not real_plan["destination_binding_approved"] and not real_plan["data_loaded"]
    fixtures.verify_plan(real_plan, real_plan["plan_sha256"],
                         SimpleNamespace(host="https://example.test", user_id="123"), XML)


def test_plan_tampering_and_user_change_fail_closed(real_plan):
    bad = deepcopy(real_plan)
    bad["targets"][0]["columns"][0]["type"] = "INT"
    with pytest.raises(ValueError):
        fixtures.verify_plan(bad, real_plan["plan_sha256"],
                             SimpleNamespace(host="https://example.test", user_id="123"), XML)
    with pytest.raises(ValueError):
        fixtures.verify_plan(real_plan, real_plan["plan_sha256"],
                             SimpleNamespace(host="https://example.test", user_id="other"), XML)


class Catalogs:
    def __init__(self):
        self.items = {}

    def get(self, name):
        if name not in self.items:
            raise NotFound("absent")
        return self.items[name]

    def create(self, name, comment):
        assert name not in self.items and comment == fixtures.COMMENT
        self.items[name] = SimpleNamespace(name=name, owner="user@example.test")


class Schemas:
    def __init__(self):
        self.items = {}

    def create(self, name, catalog_name, comment):
        full = catalog_name + "." + name
        assert full not in self.items and comment == fixtures.COMMENT
        self.items[full] = SimpleNamespace(full_name=full, owner="user@example.test")

    def get(self, name):
        return self.items[name]


def fake_identity(monkeypatch, plan, *, fail_sql=False):
    catalogs, schemas, sql = Catalogs(), Schemas(), []
    tables = {}
    for catalog in fixtures.NAMES.values():
        for table in plan["targets"]:
            name = ".".join((catalog, table["schema"], table["table"]))
            tables[name] = SimpleNamespace(full_name=name, owner="user@example.test", table_type="MANAGED",
                data_source_format="DELTA", properties={"orion.fixture": "xml_target_schema_only",
                "orion.package_sha256": fixtures.PACKAGE_HASH},
                columns=[SimpleNamespace(name=c["name"], type_text=c["type"], nullable=c["nullable"])
                         for c in table["columns"]])
    client = SimpleNamespace(catalogs=catalogs, schemas=schemas,
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(id="123", user_name="user@example.test")),
        tables=SimpleNamespace(get=lambda name: tables[name]))
    class Store:
        def __init__(self, client, warehouse, catalog, schema, user):
            assert warehouse == fixtures.WAREHOUSE

        def _execute(self, statement):
            sql.append(statement)
            if fail_sql:
                raise PermissionError("credential-shaped error must not be logged")
    monkeypatch.setattr(fixtures.ds, "DeltaStateStore", Store)
    return SimpleNamespace(client=client, user_id="123"), catalogs, schemas, sql


def test_create_only_all_three_layers_and_verify_readback(real_plan, tmp_path, monkeypatch):
    plan = deepcopy(real_plan)
    plan["targets"] = plan["targets"][:2]
    plan["target_count_per_catalog"] = 2
    plan["schema_count_per_catalog"] = len({t["schema"] for t in plan["targets"]})
    identity, catalogs, schemas, sql = fake_identity(monkeypatch, plan)
    with (tmp_path / "events").open("x", encoding="utf-8") as stream:
        assert fixtures.provision(plan, identity, stream)
    assert set(catalogs.items) == set(fixtures.NAMES.values())
    assert len(schemas.items) == 3 * plan["schema_count_per_catalog"]
    assert len(sql) == 6 and all(s.startswith("CREATE TABLE ") for s in sql)
    assert all("IF NOT EXISTS" not in s and fixtures.COMMENT in s for s in sql)
    assert not any(word in s for s in sql for word in ("INSERT", "TRUNCATE", "DROP", "GRANT"))
    assert '"status": "verified"' in (tmp_path / "events").read_text()


def test_failure_journal_stops_without_retry_or_cleanup(real_plan, tmp_path, monkeypatch):
    plan = deepcopy(real_plan)
    plan["targets"] = plan["targets"][:1]
    identity, catalogs, schemas, sql = fake_identity(monkeypatch, plan, fail_sql=True)
    with (tmp_path / "events").open("x", encoding="utf-8") as stream:
        assert not fixtures.provision(plan, identity, stream)
    text = (tmp_path / "events").read_text()
    assert len(catalogs.items) == 3 and len(sql) == 1
    assert '"phase": "failed"' in text and '"no_retry": true' in text
    assert "credential-shaped" not in text


def test_existing_catalog_prevents_all_creation(real_plan, tmp_path, monkeypatch):
    identity, catalogs, schemas, sql = fake_identity(monkeypatch, real_plan)
    existing = fixtures.NAMES["silver"]
    catalogs.items[existing] = SimpleNamespace(name=existing, owner="other")
    with (tmp_path / "events").open("x", encoding="utf-8") as stream:
        with pytest.raises(ValueError):
            fixtures.provision(real_plan, identity, stream)
    assert list(catalogs.items) == [existing] and not schemas.items and not sql


def test_readback_rejects_external_or_wrong_format(real_plan):
    table = real_plan["targets"][0]
    info = SimpleNamespace(full_name="c.s.t", owner="u", table_type="EXTERNAL", data_source_format="DELTA",
        properties={"orion.fixture": "xml_target_schema_only", "orion.package_sha256": fixtures.PACKAGE_HASH},
        columns=[SimpleNamespace(name=c["name"], type_text=c["type"], nullable=c["nullable"])
                 for c in table["columns"]])
    with pytest.raises(ValueError):
        fixtures.verify_table(info, "c.s.t", table, "u")