"""Fake UC/SQL tests only. No browser, credentials, inference or remote DDL."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

from databricks.sdk.errors import NotFound, PermissionDenied
import pytest

from workbench import complete_target_fixtures as c

ROOT = Path(__file__).resolve().parents[2]
XML = ROOT / "input" / "LumenExample.XML"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in fake-client tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(c, "fresh_profile_identity", forbidden)


@pytest.fixture(scope="module")
def full_plan():
    return c.build_plan(XML)


@pytest.fixture
def plan(full_plan):
    result = deepcopy(full_plan)
    # Two distinct schema namespaces per catalog, keep only a tiny internal
    # operator test inventory. Public apply still rebuilds all 307 definitions.
    chosen = []
    for obj in result["objects"]:
        if obj["catalog"] == c.CATALOGS[0] and obj["target"]["schema"] not in {
                t["target"]["schema"] for t in chosen}:
            chosen.append(obj)
        if len(chosen) == 2:
            break
    keys = {o["target"]["schema"] + "." + o["target"]["table"] for o in chosen}
    result["objects"] = [o for o in result["objects"] if
                          o["target"]["schema"] + "." + o["target"]["table"] in keys]
    return result


class Fake:
    def __init__(self, plan):
        self.plan = plan
        self.calls, self.creates, self.sql = [], [], []
        self.hook = None
        self.catalog_items = {name: NS(name=name, owner=c.USER) for name in plan["catalogs"]}
        self.schema_items, self.table_items = {}, {}
        self.by_sql = {o["sql"]: o for o in plan["objects"]}
        self.me = NS(id=c.USER_ID, user_name=c.USER, active=True,
                     schemas=["urn:ietf:params:scim:schemas:core:2.0:User"])
        self.config = NS(host=c.HOST)
        self.current_user = NS(me=lambda: self.call("me", c.USER, lambda: self.me))
        self.catalogs = NS(get=lambda n: self.get("catalog", n))  # deliberately no create API
        self.schemas = NS(get=lambda n: self.get("schema", n), create=self.create_schema,
                          list=lambda catalog_name: self.list_schemas(catalog_name))
        self.tables = NS(get=lambda n: self.get("table", n),
                         list=lambda catalog_name, schema_name: self.list_tables(catalog_name, schema_name))
        self.statement_execution = NS(execute_statement=self.execute, get_statement=self.get_statement)
        for catalog in plan["catalogs"]:
            # Unrelated namespaces and variable inventory sizes must be retained.
            schema = catalog + ".vyvx"
            self.schema_items[schema] = NS(full_name=schema, name="vyvx", owner="unrelated-owner")
            for index in range(3):
                name = schema + ".unrelated_" + str(index)
                self.table_items[name] = NS(full_name=name, owner="unrelated-owner", table_type="MANAGED",
                                           table_id="unrelated-" + name)

    def identity(self, profile="labs"):
        assert profile == "labs"
        return NS(mode="profile", host=c.HOST, user_id=c.USER_ID, client=self)

    def call(self, operation, name, fn):
        self.calls.append((operation, name))
        if self.hook:
            self.hook(operation, name)
        return fn()

    def get(self, kind, name):
        def result():
            items = getattr(self, kind + "_items")
            if name not in items:
                raise NotFound("absent")
            return items[name]

        return self.call(kind + ".get", name, result)

    def list_schemas(self, catalog):
        return self.call("schemas.list", catalog, lambda: [v for k, v in self.schema_items.items()
                                                          if k.startswith(catalog + ".")])

    def list_tables(self, catalog, schema):
        return self.call("tables.list", catalog + "." + schema,
                         lambda: [v for k, v in self.table_items.items()
                                  if k.rsplit(".", 1)[0] == catalog + "." + schema])

    def create_schema(self, name, catalog_name, comment):
        full = catalog_name + "." + name
        self.creates.append(("schema", full))
        assert full not in self.schema_items and comment == c.DISCLOSURE
        self.schema_items[full] = NS(full_name=full, name=name, owner=c.USER)
        self.call("schema.create", full, lambda: None)

    def add_table(self, obj):
        schema = obj["full_name"].rsplit(".", 1)[0]
        self.schema_items.setdefault(schema, NS(full_name=schema, name=schema.split(".")[1], owner=c.USER))
        self.table_items[obj["full_name"]] = NS(full_name=obj["full_name"], owner=c.USER,
            table_type="MANAGED", data_source_format="DELTA", table_id="fixture-" + obj["full_name"],
            properties={"orion.fixture": "xml_target_schema_only", "orion.package_sha256": c.PACKAGE},
            columns=[NS(name=col["name"], type_text=col["type"], nullable=col["nullable"])
                     for col in obj["target"]["columns"]])

    def execute(self, statement, warehouse, **kwargs):
        self.sql.append(statement)
        assert warehouse == c.WAREHOUSE
        assert kwargs == {"wait_timeout": "50s", "row_limit": 1}
        obj = self.by_sql[statement]
        assert obj["full_name"] not in self.table_items
        self.creates.append(("table", obj["full_name"]))
        self.add_table(obj)
        self.call("statement.execute", obj["full_name"], lambda: None)
        return NS(statement_id="statement-" + str(len(self.sql)), status=NS(state="SUCCEEDED"))

    def get_statement(self, statement_id):
        raise AssertionError("Unexpected SQL poll")


def run(plan, tmp_path, monkeypatch, fake=None):
    fake = fake or Fake(plan)
    output = tmp_path / "receipts"
    output.mkdir()
    monkeypatch.setattr(c, "file_hash", lambda path: c.PACKAGE)
    journal = c.Journal(output / "journal.jsonl", plan)
    # Exercise durable per-statement request/readback instrumentation too.
    fake.statement_execution = c.ObservedStatements(fake.statement_execution, journal)
    try:
        summary = c.run_completion(plan, XML, fake.identity(), journal, output)
    finally:
        journal.close()
    events = [json.loads(line) for line in (output / "journal.jsonl").read_text().splitlines()]
    assert json.loads((output / "summary.json").read_bytes()) == summary
    return fake, summary, events


def test_full_pinned_xml_and_exact_sql_all_307_three_catalogs(full_plan):
    assert full_plan["package_sha256"] == c.PACKAGE
    assert full_plan["targets_per_catalog"] == 307
    assert full_plan["schemas_per_catalog"] == 14
    assert len(full_plan["objects"]) == 921
    assert len({o["full_name"] for o in full_plan["objects"]}) == 921
    assert not full_plan["semantic_medallion"] and not full_plan["data_loaded"]
    for catalog in c.CATALOGS:
        objects = [o for o in full_plan["objects"] if o["catalog"] == catalog]
        assert len(objects) == 307
        assert any("JOB_DEP_CHK_COUNT" in o["target"]["xml_identity"][-1] for o in objects)
        for obj in objects:
            assert obj["sql"] == c.target_sql(catalog, obj["target"], c.PACKAGE)
            assert obj["sql"].startswith("CREATE TABLE ") and "IF NOT EXISTS" not in obj["sql"]


def test_full_scope_fake_apply_verifies_all_921_without_other_operations(full_plan, tmp_path, monkeypatch):
    fake = Fake(full_plan)
    # Reuse 20 real planned shapes per catalog, reproducing the live expected split.
    for catalog in c.CATALOGS:
        for obj in [o for o in full_plan["objects"] if o["catalog"] == catalog][:20]:
            fake.add_table(obj)
    fake, summary, events = run(full_plan, tmp_path, monkeypatch, fake)
    assert summary["status"] == "complete" and summary["metadata_verified"]
    assert len(fake.sql) == 861
    for counts in summary["catalogs"].values():
        assert counts["tables_created"] == 287 and counts["tables_reused_verified"] == 20
        assert counts["unrelated_inventory_preserved"]
    assert len([e for e in events if e["state"] in {"created_verified", "reused_verified"}
                and e["kind"] == "table"]) == 921
    assert all(operation in {"me", "catalog.get", "schema.get", "table.get", "schemas.list",
                             "tables.list", "schema.create", "statement.execute"} for operation, _ in fake.calls)


def test_additive_partial_existing_and_journal_before_every_create(plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    fake.add_table(plan["objects"][0])
    unrelated = deepcopy({n: vars(t) for n, t in fake.table_items.items() if ".vyvx." in n})
    actual_fsync = c.os.fsync
    fsyncs = []

    def synced(fd):
        fsyncs.append(fd)
        return actual_fsync(fd)

    monkeypatch.setattr(c.os, "fsync", synced)
    fake, summary, events = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] == "complete" and len(fake.sql) == 5
    assert unrelated == {n: vars(t) for n, t in fake.table_items.items() if ".vyvx." in n}
    for kind, name in fake.creates:
        requested = next(i for i, e in enumerate(events) if e["state"] == "create_requested" and e["name"] == name)
        observed = next(i for i, e in enumerate(events) if e["state"] == "created_verified" and e["name"] == name)
        assert requested < observed and events[observed]["metadata_verified"]
    preflight = next(i for i, e in enumerate(events) if e["state"] == "preflight_complete")
    assert all(i > preflight for i, e in enumerate(events) if e["state"] == "create_requested")
    assert len(fsyncs) >= len(events)


@pytest.mark.parametrize("field,bad", [("owner", "other"), ("table_type", "EXTERNAL"),
    ("data_source_format", "PARQUET"), ("full_name", "other.schema.table")])
def test_existing_target_metadata_guards(field, bad, plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    fake.add_table(plan["objects"][-1])
    setattr(fake.table_items[plan["objects"][-1]["full_name"]], field, bad)
    fake, summary, _ = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] == "partial_or_rejected" and summary["phase"] == "preflight"
    assert not fake.creates


@pytest.mark.parametrize("mutation", ["marker", "package", "column_name", "column_type", "nullable",
                                      "column_order", "extra_column", "missing_column"])
def test_every_ordered_column_and_provenance_guard(mutation, plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    obj = plan["objects"][-1]
    fake.add_table(obj)
    info = fake.table_items[obj["full_name"]]
    if mutation == "marker":
        info.properties["orion.fixture"] = "xml_source_schema_only"
    elif mutation == "package":
        info.properties["orion.package_sha256"] = "0" * 64
    elif mutation == "column_name":
        info.columns[0].name += "changed"
    elif mutation == "column_type":
        info.columns[0].type_text = "BOOLEAN"
    elif mutation == "nullable":
        info.columns[0].nullable = not info.columns[0].nullable
    elif mutation == "column_order":
        info.columns = list(reversed(info.columns))
    elif mutation == "extra_column":
        info.columns.append(NS(name="extra", type_text="INT", nullable=True))
    else:
        info.columns.pop()
    fake, summary, _ = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] != "complete" and not fake.creates


@pytest.mark.parametrize("failure", ["missing_catalog", "catalog_owner", "schema_owner", "permission_unknown"])
def test_container_authority_fail_closed_before_any_ddl(failure, plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    if failure == "missing_catalog":
        del fake.catalog_items[c.CATALOGS[-1]]
    elif failure == "catalog_owner":
        fake.catalog_items[c.CATALOGS[-1]].owner = "someone_else"
    elif failure == "schema_owner":
        fake.add_table(plan["objects"][-1])
        fake.schema_items[plan["objects"][-1]["full_name"].rsplit(".", 1)[0]].owner = "someone_else"
    else:
        def hook(operation, name):
            if operation == "table.get":
                raise PermissionDenied("sensitive raw error must never be logged")
        fake.hook = hook
    fake, summary, events = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] != "complete" and not fake.creates
    assert "sensitive raw error" not in json.dumps(events)


@pytest.mark.parametrize("failure", ["host", "client_host", "user_id", "mode", "username", "inactive", "sp", "no_schema"])
def test_exact_fresh_human_identity_guards(failure, plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    identity = fake.identity()
    if failure == "host":
        identity.host += "/other"
    elif failure == "client_host":
        fake.config.host += "/other"
    elif failure == "user_id":
        identity.user_id = "other"
    elif failure == "mode":
        identity.mode = "obo"
    elif failure == "username":
        fake.me.user_name = "other@example.test"
    elif failure == "inactive":
        fake.me.active = False
    elif failure == "sp":
        fake.me.schemas.append("ServicePrincipal")
    else:
        fake.me.schemas = []
    monkeypatch.setattr(fake, "identity", lambda: identity)
    fake, summary, _ = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] != "complete" and not fake.creates


def test_race_appeared_table_stops_not_if_not_exists(plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    obj = plan["objects"][0]
    gets = []

    def hook(operation, name):
        if operation == "table.get" and name == obj["full_name"]:
            gets.append(name)
            if len(gets) == 2:
                fake.add_table(obj)

    fake.hook = hook
    fake, summary, events = run(plan, tmp_path, monkeypatch, fake)
    assert summary["phase"] == "apply" and not fake.sql
    assert not any(e["state"] == "reused_verified" and e["name"] == obj["full_name"] for e in events)


@pytest.mark.parametrize("kind", ["schema", "table"])
def test_unknown_create_single_attempt_reconciles_get_and_stops(kind, plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    if kind == "table":
        for name in c.schema_names(plan):
            fake.schema_items[name] = NS(full_name=name, name=name.split(".")[1], owner=c.USER)

    def hook(operation, name):
        if operation == ("schema.create" if kind == "schema" else "statement.execute"):
            raise RuntimeError("credential-shaped raw details forbidden")

    fake.hook = hook
    fake, summary, events = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] == "partial_or_rejected"
    assert summary["uncertain_request_reconciled"] == "present_metadata_verified"
    assert len(fake.creates) == 1
    assert len([e for e in events if e["state"] == "reconciled_present_no_retry"]) == 1
    assert "credential-shaped" not in json.dumps(events)


def test_new_table_readback_mismatch_stops_without_mutation_or_repair(plan, tmp_path, monkeypatch):
    fake = Fake(plan)

    def hook(operation, name):
        if operation == "statement.execute":
            fake.table_items[name].properties["orion.fixture"] = "wrong"

    fake.hook = hook
    fake, summary, _ = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] != "complete" and len(fake.sql) == 1
    assert summary["uncertain_request_reconciled"] == "unresolved"


def test_disappeared_existing_table_no_replacement(plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    obj = plan["objects"][0]
    fake.add_table(obj)
    gets = []

    def hook(operation, name):
        if operation == "table.get" and name == obj["full_name"]:
            gets.append(name)
            if len(gets) == 2:
                del fake.table_items[name]

    fake.hook = hook
    fake, summary, _ = run(plan, tmp_path, monkeypatch, fake)
    assert summary["status"] != "complete" and not fake.sql


def test_package_drift_before_ddl_rejected(plan, tmp_path, monkeypatch):
    fake = Fake(plan)
    output = tmp_path / "receipts"
    output.mkdir()
    journal = c.Journal(output / "journal.jsonl", plan)
    monkeypatch.setattr(c, "file_hash", lambda _: "0" * 64)
    try:
        summary = c.run_completion(plan, XML, fake.identity(), journal, output)
    finally:
        journal.close()
    assert summary["status"] != "complete" and not fake.creates


def test_export_offline_exclusive_and_checksum(full_plan, tmp_path):
    output = tmp_path / "new-plan"
    c.export_plan(full_plan, output)
    assert c.read_json(output / "plan.json") == full_plan
    with pytest.raises(FileExistsError):
        c.export_plan(full_plan, output)
    bad = deepcopy(full_plan)
    bad["objects"].pop()
    with pytest.raises(ValueError):
        c.export_plan(bad, tmp_path / "tampered")


@pytest.mark.parametrize("failure", ["consent", "checksum", "sql", "scope", "catalog", "host", "user"])
def test_public_apply_entire_plan_gates_before_identity(failure, full_plan, tmp_path, monkeypatch):
    plan = deepcopy(full_plan)
    sha = plan["plan_sha256"]
    consent = "CREATE MISSING TARGET FIXTURES " + sha
    if failure == "consent":
        consent = "yes"
    elif failure == "checksum":
        sha = "0" * 64
    elif failure == "sql":
        plan["objects"][0]["sql"] += " IF NOT EXISTS"
    elif failure == "scope":
        plan["objects"].pop()
    elif failure == "catalog":
        plan["catalogs"].append("source_forbidden")
    elif failure == "host":
        plan["host"] = "https://elsewhere.test"
    else:
        plan["user"] = "other"
    path = tmp_path / "plan"
    path.mkdir()
    c.write_new(path / "plan.json", plan)
    monkeypatch.setattr(c, "build_plan", lambda _: full_plan)
    with pytest.raises((ValueError, PermissionError)):
        c.apply_plan(path, tmp_path / "apply", XML, sha, consent)
    assert not (tmp_path / "apply").exists()


def test_public_apply_claim_prevents_generic_rerun_even_new_output(full_plan, tmp_path, monkeypatch):
    output = tmp_path / "plan"
    c.export_plan(full_plan, output)
    monkeypatch.setattr(c, "build_plan", lambda _: full_plan)
    c.write_new(output / "apply-claim.json", {"already_attempted": True})
    with pytest.raises(FileExistsError):
        c.apply_plan(output, tmp_path / "different-receipts", XML, full_plan["plan_sha256"],
                     "CREATE MISSING TARGET FIXTURES " + full_plan["plan_sha256"])


def test_observed_statement_polls_are_journaled_and_bounded(plan, tmp_path):
    calls = []
    original = NS(execute_statement=lambda *a, **k: NS(statement_id="id", status=NS(state="RUNNING")),
                  get_statement=lambda n: calls.append(n) or NS(statement_id=n, status=NS(state="RUNNING")))
    journal = c.Journal(tmp_path / "events", plan)
    try:
        with pytest.raises(RuntimeError, match="manual reconciliation"):
            c.execute_create(NS(statement_execution=c.ObservedStatements(original, journal)),
                             c.WAREHOUSE, plan["objects"][0]["sql"], lambda _: None)
    finally:
        journal.close()
    assert len(calls) == 20
    events = [json.loads(line) for line in (tmp_path / "events").read_text().splitlines()]
    assert len([e for e in events if e["state"] == "request"]) == 21


def test_main_plan_has_no_identity_or_remote_access(full_plan, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(c, "build_plan", lambda _: full_plan)
    c.main(["--plan", "--xml", str(XML), "--output", str(tmp_path / "offline")])
    assert json.loads(capsys.readouterr().out)["state"] == "planned_offline"