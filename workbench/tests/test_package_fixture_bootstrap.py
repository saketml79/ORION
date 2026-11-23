"""Operator contracts, fake clients only. No network, browser or inference."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

from databricks.sdk.errors import NotFound
import pytest

from deployment.stage_hosted import stage
from workbench import package_fixture_bootstrap as bootstrap

USER = "tester@example.test"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
FRESH_IDENTITY = bootstrap.fresh_profile_identity


@pytest.fixture(autouse=True)
def no_external_activity(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("No remote calls or CLI allowed in bootstrap unit tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(bootstrap, "fresh_profile_identity", forbidden)


@pytest.fixture
def xml(tmp_path):
    path = tmp_path / "package.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <SOURCE NAME="LOG" DATABASETYPE="Oracle" DBDNAME="A" OWNERNAME="$$OWNER">
        <SOURCEFIELD NAME="WHEN" DATATYPE="date" PRECISION="19" SCALE="0" NULLABLE="NOTNULL"/>
        <SOURCEFIELD NAME="VALUE" DATATYPE="number" PRECISION="10" SCALE="2" NULLABLE="NULL"/>
      </SOURCE>
      <SOURCE NAME="LOG" DATABASETYPE="Oracle" DBDNAME="B" OWNERNAME="$$OWNER">
        <SOURCEFIELD NAME="WHEN" DATATYPE="date" PRECISION="19" SCALE="0" NULLABLE="NOTNULL"/>
      </SOURCE>
      <TARGET NAME="LOG" DATABASETYPE="Oracle" DBDNAME="A" OWNERNAME="$$OWNER">
        <TARGETFIELD NAME="WHEN" DATATYPE="date" PRECISION="19" SCALE="0" NULLABLE="NOTNULL"/>
      </TARGET>
      <TARGET NAME="AE2E_JOB_DEP_CHK_COUNT" DATABASETYPE="Oracle" OWNERNAME="$$AE2E_SCHEMA">
        <TARGETFIELD NAME="COUNT_VALUE" DATATYPE="number" PRECISION="38" SCALE="5" NULLABLE="NULL"/>
      </TARGET>
      <TARGET NAME="ORPHAN_LOG" DATABASETYPE="Flat File">
        <TARGETFIELD NAME="RESULT" DATATYPE="double" PRECISION="15" SCALE="0" NULLABLE="NULL"/>
      </TARGET>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return path


def planned(xml, tmp_path, assignments=None):
    plan = bootstrap.build_plan(xml, USER, assignments=assignments)
    output = tmp_path / "review"
    bootstrap.export_plan(plan, output)
    return plan, output


def table_info(plan, table):
    return NS(full_name=table["physical_fqn"], owner=USER, table_type="MANAGED", data_source_format="DELTA",
              properties=bootstrap.properties(plan, table),
              columns=[NS(name=c["name"], type_text=c["type"], nullable=c["nullable"])
                       for c in table["columns"]])


class Objects:
    def __init__(self, kind, plan, calls):
        self.kind, self.plan, self.calls = kind, plan, calls
        self.items = {}
        self.denied = set()

    def get(self, name):
        self.calls.append(("get", self.kind, name))
        if name in self.denied:
            raise PermissionError("do not persist credential-shaped error text")
        if name not in self.items:
            raise NotFound("absent")
        return self.items[name]

    def create(self, name, comment, catalog_name=None):
        full = name if catalog_name is None else catalog_name + "." + name
        self.calls.append(("create", self.kind, full))
        assert full not in self.items
        self.items[full] = NS(name=name, full_name=full, owner=USER, comment=comment)
        return self.items[full]

    def list(self, catalog_name, schema_name=None):
        prefix = catalog_name + "." + (schema_name + "." if schema_name else "")
        return [v for k, v in self.items.items() if k.startswith(prefix)]


class Statements:
    def __init__(self, plan, tables, calls):
        self.plan, self.tables, self.calls = plan, tables, calls
        self.fail = False
        self.pending = False
        self.populated = False
        self.corrupt_readback = False
        self.submissions = 0

    def execute_statement(self, statement, warehouse, wait_timeout, row_limit):
        self.calls.append(("sql", statement))
        assert warehouse == "approved-warehouse" and wait_timeout == "50s" and row_limit == 1
        if statement.startswith("SELECT EXISTS("):
            return NS(statement_id="empty-read", status=NS(state="SUCCEEDED"),
                      result=NS(data_array=[["true" if self.populated else "false"]], next_chunk_index=None))
        assert statement.startswith("CREATE TABLE ") and "IF NOT EXISTS" not in statement
        self.submissions += 1
        if self.fail:
            raise ConnectionError("credential-shaped exception must not be printed or journaled")
        table = next(t for t in self.plan["tables"] if bootstrap.table_sql(self.plan, t) == statement)
        info = table_info(self.plan, table)
        if self.corrupt_readback:
            info.columns[0].nullable = not info.columns[0].nullable
        self.tables.items[table["physical_fqn"]] = info
        return NS(statement_id="create-" + str(self.submissions),
                  status=NS(state="PENDING" if self.pending else "SUCCEEDED"))

    def get_statement(self, statement_id):
        self.calls.append(("observe", statement_id))
        return NS(statement_id=statement_id, status=NS(state="PENDING"))


def fake_identity(plan, existing=False):
    calls = []
    catalogs, schemas, tables = [Objects(kind, plan, calls) for kind in ("catalog", "schema", "table")]
    if existing:
        catalogs.items[plan["catalog"]] = NS(name=plan["catalog"], owner=USER, comment=bootstrap.marker(plan))
        for layer in bootstrap.LAYERS:
            full = plan["catalog"] + "." + layer
            schemas.items[full] = NS(name=layer, full_name=full, owner=USER, comment=bootstrap.marker(plan))
        tables.items = {t["physical_fqn"]: table_info(plan, t) for t in plan["tables"]}
    me = NS(id="user-id", user_name=USER, active=True, schemas=[USER_SCHEMA])
    statements = Statements(plan, tables, calls)
    client = NS(catalogs=catalogs, schemas=schemas, tables=tables, statement_execution=statements,
                current_user=NS(me=lambda: me))
    identity = NS(mode="profile", host="https://workspace.example.test", user_id=me.id, client=client)
    return identity, calls, me


def apply(plan, xml, output, identity, **overrides):
    args = dict(warehouse="approved-warehouse", expected_plan_sha=plan["plan_sha256"],
                consent="CREATE EMPTY FIXTURES " + plan["plan_sha256"], identity_provider=lambda p: identity)
    args.update(overrides)
    return bootstrap.apply_plan(plan, xml, output, **args)


def receipts(output):
    import json
    return [json.loads(line) for line in (output / "creation-receipts.jsonl").read_text().splitlines()]


def test_all_definitions_no_workflow_filter_control_orphans_and_layers(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    assert (plan["source_count"], plan["target_count"]) == (2, 3)
    assert {t["original_xml_name"] for t in plan["tables"]} >= {"AE2E_JOB_DEP_CHK_COUNT", "ORPHAN_LOG"}
    assert plan == bootstrap.build_plan(xml, USER)
    assert plan["schemas"] == ["bronze", "silver", "gold"]
    assert not any(t["layer"] == "gold" for t in plan["tables"])
    for table in plan["tables"]:
        assert table["xml_identity_sha256"] == bootstrap.fingerprint(table["xml_identity"])
        assert table["table"].endswith(table["xml_identity_sha256"])
        assert table["layer"] == ("bronze" if table["kind"] == "SOURCE" else "silver")
        assert "$$" not in table["physical_fqn"]
    assert len({t["physical_fqn"] for t in plan["tables"]}) == 5
    assert "$$" in (output / "creation-manifest.json").read_text()
    for name in ("source-tables.sql", "target-tables.sql"):
        sql = (output / name).read_text()
        assert "$$" not in sql and "CREATE TABLE " in sql and "IF NOT EXISTS" not in sql
        assert not any(word in sql for word in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ", "OVERWRITE"))


def test_exact_assignments_preserved_unknown_and_source_move_rejected(xml):
    original = bootstrap.build_plan(xml, USER)
    target = next(t for t in original["tables"] if t["kind"] == "TARGET")
    assignments = {target["assignment_key"]: "gold"}
    plan = bootstrap.build_plan(xml, USER, assignments=assignments)
    assert plan["assignments"] == assignments
    assigned = next(t for t in plan["tables"] if t["assignment_key"] == target["assignment_key"])
    assert assigned["layer"] == "gold" and assigned["basis"] == "explicit_full_xml_identity_assignment"
    for invalid in ({"unknown": "silver"}, {target["assignment_key"]: "curated"},
                    {next(t["assignment_key"] for t in original["tables"] if t["kind"] == "SOURCE"): "silver"}):
        with pytest.raises(ValueError):
            bootstrap.build_plan(xml, USER, assignments=invalid)


def test_user_package_namespace_and_explicit_catalog(xml):
    plan = bootstrap.build_plan(xml, USER)
    assert bootstrap.build_plan(xml, "other@example.test")["catalog"] != plan["catalog"]
    assert bootstrap.build_plan(xml, USER, plan["catalog"] + "_review")["catalog"].endswith("_review")
    for catalog in ("production", "orion_empty_shared", plan["catalog"] + "`;bad"):
        with pytest.raises(ValueError):
            bootstrap.build_plan(xml, USER, catalog)
    with pytest.raises(ValueError):
        bootstrap.build_plan(xml, USER, profile="DEFAULT")


@pytest.mark.parametrize("old,new", [
    ('DATATYPE="date"', 'DATATYPE="mystery"'),
    ('NULLABLE="NOTNULL"', 'NULLABLE="UNKNOWN"'),
    ('NAME="VALUE"', 'NAME="WHEN"'),
    ('NAME="VALUE"', 'NAME="$$COLUMN"'),
    ('PRECISION="38"', 'PRECISION="39"'),
])
def test_unsafe_unknown_or_ambiguous_columns_fail_before_export(xml, tmp_path, old, new):
    xml.write_text(xml.read_text().replace(old, new))
    with pytest.raises((ValueError, KeyError)):
        bootstrap.build_plan(xml, USER)
    assert not (tmp_path / "review").exists()


def test_new_only_exports(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        bootstrap.export_plan(plan, output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


def test_create_only_complete_preflight_and_full_readback(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan)
    apply(plan, xml, output, identity)
    first_create = next(i for i, call in enumerate(calls) if call[0] == "create")
    assert {call[2] for call in calls[:first_create] if call[0] == "get"} == {
        plan["catalog"], *(plan["catalog"] + "." + layer for layer in bootstrap.LAYERS),
        *(t["physical_fqn"] for t in plan["tables"])}
    assert identity.client.statement_execution.submissions == 5
    rows = receipts(output)
    created = [r for r in rows if r["state"] == "created"]
    assert len(created) == 9 and rows[-1]["state"] == "complete"
    for row in created:
        assert row["readback"]["owner"] == USER
        if row["kind"] == "table":
            assert row["readback"]["empty_verified"] and row["readback"]["columns"]
            assert row["readback"]["properties"]["orion.plan_sha256"] == plan["plan_sha256"]
    assert not any(call[0] in {"delete", "update"} for call in calls)


def test_matching_owned_existing_objects_reused_no_creates(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan, existing=True)
    apply(plan, xml, output, identity)
    assert not any(call[0] == "create" for call in calls)
    assert identity.client.statement_execution.submissions == 0
    assert len([r for r in receipts(output) if r["state"] == "reused-verified"]) == 9


@pytest.mark.parametrize("corruption", ["owner", "provenance", "type", "nullability", "format", "external", "property", "populated"])
def test_existing_table_mismatch_rejects_before_any_ddl(xml, tmp_path, corruption):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan, existing=True)
    info = identity.client.tables.items[plan["tables"][-1]["physical_fqn"]]
    if corruption == "owner":
        info.owner = "other"
    elif corruption == "provenance":
        info.properties["orion.package_sha256"] = "0" * 64
    elif corruption == "type":
        info.columns[0].type_text = "STRING"
    elif corruption == "nullability":
        info.columns[0].nullable = not info.columns[0].nullable
    elif corruption == "format":
        info.data_source_format = "PARQUET"
    elif corruption == "external":
        info.table_type = "EXTERNAL"
    elif corruption == "property":
        info.properties["unexpected"] = "must be rejected"
    else:
        identity.client.statement_execution.populated = True
    with pytest.raises(RuntimeError, match="Preflight rejected"):
        apply(plan, xml, output, identity)
    assert not any(call[0] == "create" for call in calls)
    assert identity.client.statement_execution.submissions == 0
    assert receipts(output)[-1]["state"] == "failure"


@pytest.mark.parametrize("kind", ["catalog", "schema"])
def test_existing_unowned_or_unmarked_containers_rejected(xml, tmp_path, kind):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan, existing=True)
    api = identity.client.catalogs if kind == "catalog" else identity.client.schemas
    next(iter(api.items.values())).comment = "unowned ordinary catalog"
    with pytest.raises(RuntimeError):
        apply(plan, xml, output, identity)
    assert not any(call[0] == "create" for call in calls)


def test_unplanned_objects_and_permission_errors_are_not_absence(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan, existing=True)
    unknown = plan["catalog"] + ".silver.not_in_xml"
    identity.client.tables.items[unknown] = NS(full_name=unknown)
    with pytest.raises(RuntimeError):
        apply(plan, xml, output, identity)
    assert not any(call[0] == "create" for call in calls)


def test_permission_failure_no_create_no_sensitive_receipt(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan)
    identity.client.catalogs.denied.add(plan["catalog"])
    with pytest.raises(RuntimeError):
        apply(plan, xml, output, identity)
    assert not any(call[0] == "create" for call in calls)
    assert "credential-shaped" not in (output / "creation-receipts.jsonl").read_text()


@pytest.mark.parametrize("failure", ["submit", "pending", "readback"])
def test_partial_failure_or_uncertainty_no_retry_delete_and_persist_statement_id(xml, tmp_path, failure):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan)
    statements = identity.client.statement_execution
    statements.fail = failure == "submit"
    statements.pending = failure == "pending"
    statements.corrupt_readback = failure == "readback"
    with pytest.raises(RuntimeError, match="Apply stopped"):
        apply(plan, xml, output, identity)
    assert statements.submissions == 1
    last = receipts(output)[-1]
    assert last["state"] == "uncertain" and last["no_retry"] and last["no_delete"]
    if failure != "submit":
        assert last["statement_id"] == "create-1"
    assert "credential-shaped" not in (output / "creation-receipts.jsonl").read_text()
    before = list(calls)
    with pytest.raises(FileExistsError):
        apply(plan, xml, output, identity)
    assert identity.client.statement_execution.submissions == 1 and calls == before


@pytest.mark.parametrize("bad", ["consent", "sha", "plan", "xml", "sql", "identity", "sp"])
def test_exact_consent_package_plan_identity_and_review_files_are_required(xml, tmp_path, bad):
    plan, output = planned(xml, tmp_path)
    identity, calls, me = fake_identity(plan)
    overrides = {}
    if bad == "consent":
        overrides["consent"] = "yes"
    elif bad == "sha":
        overrides["expected_plan_sha"] = "0" * 64
        overrides["consent"] = "CREATE EMPTY FIXTURES " + "0" * 64
    elif bad == "plan":
        plan = deepcopy(plan)
        plan["tables"][0]["layer"] = "gold"
    elif bad == "xml":
        xml.write_text(xml.read_text() + "\n")
    elif bad == "sql":
        (output / "source-tables.sql").write_text("CREATE TABLE wrong (x INT) USING DELTA")
    elif bad == "identity":
        me.user_name = "other@example.test"
    else:
        me.schemas = ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"]
    with pytest.raises((ValueError, PermissionError)):
        apply(plan, xml, output, identity, **overrides)
    assert not any(call[0] == "create" for call in calls)
    assert identity.client.statement_execution.submissions == 0


def test_staging_packages_explicit_operator_not_startup_or_inputs(tmp_path):
    import json
    destination = tmp_path / "stage"
    manifest = stage(destination, "https://workspace.example.test", "https://app.example.test")
    names = {entry["path"] for entry in manifest}
    assert {"workbench/package_fixture_bootstrap.py", "empty-fixture-operator.json"} <= names
    instructions = json.loads((destination / "empty-fixture-operator.json").read_bytes())
    assert instructions["automatic"] is False and instructions["deployment_orchestrator_integrated"] is False
    for name in ("app.yaml", "databricks.yml"):
        assert "package_fixture_bootstrap" not in (destination / name).read_text()
    assert not any(Path(name).suffix.lower() == ".xml" for name in names)
    assert not any(part in {"input", "output", "tests"} for name in names for part in Path(name).parts)
    assert all(entry["size"] <= 10 * 1024 * 1024 for entry in manifest)


def test_sdk_user_schema_enums_are_accepted_without_service_principal_fallback(xml):
    from databricks.sdk.service.iam import UserSchema
    plan = bootstrap.build_plan(xml, USER)
    identity, _, me = fake_identity(plan)
    me.schemas = [UserSchema.URN_IETF_PARAMS_SCIM_SCHEMAS_CORE_2_0_USER]
    assert bootstrap.confirm_user(identity, plan).id == "user-id"
    me.id = "changed-id"
    with pytest.raises(PermissionError):
        bootstrap.confirm_user(identity, plan)


def test_single_attempt_transport_is_client_local_and_installed_before_identity(monkeypatch):
    from databricks.sdk import errors
    from workbench import databricks_models
    import databricks.sdk
    calls = []
    transports = []

    class Throttled(Exception):
        retry_after_secs = 30

    def workspace_factory(**kwargs):
        def perform(*args, **kw):
            calls.append(args)
            if args[0] == "missing":
                raise errors.NotFound("absent")
            raise Throttled("do not print sensitive transport details")
        transport = NS(_perform=perform)
        transports.append(transport)
        return NS(api_client=NS(_api_client=transport))

    def profile_factory(profile, client_factory):
        assert profile == "labs"
        client = client_factory(config="explicit-profile-only")
        with pytest.raises(RuntimeError, match="Single-attempt") as captured:
            client.api_client._api_client._perform("CREATE", "url")
        assert not hasattr(captured.value, "retry_after_secs")
        with pytest.raises(errors.NotFound):
            client.api_client._api_client._perform("missing")
        return client

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", workspace_factory)
    monkeypatch.setattr(databricks_models, "profile_identity", profile_factory)
    result = FRESH_IDENTITY("labs")
    assert result.api_client._api_client is transports[0] and len(calls) == 2


def test_duplicate_identity_is_rejected_not_silently_flattened(xml):
    text = xml.read_text()
    start = text.index('<SOURCE NAME="LOG"')
    end = text.index("</SOURCE>", start) + len("</SOURCE>")
    xml.write_text(text.replace("</FOLDER>", text[start:end] + "</FOLDER>"))
    with pytest.raises(ValueError, match="Duplicate"):
        bootstrap.build_plan(xml, USER)


def test_source_target_fqn_tampering_is_rejected_before_identity_or_ddl(xml, tmp_path):
    plan, output = planned(xml, tmp_path)
    identity, calls, _ = fake_identity(plan)
    source = next(t for t in plan["tables"] if t["kind"] == "SOURCE")
    target = next(t for t in plan["tables"] if t["kind"] == "TARGET")
    target["physical_fqn"] = source["physical_fqn"]
    # Even a recomputed checksum cannot substitute a different deterministic plan.
    plan["plan_sha256"] = bootstrap.fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        apply(plan, xml, output, identity)
    assert not calls


def test_duplicate_assignment_json_keys_rejected(tmp_path):
    path = tmp_path / "assignments.json"
    path.write_text('{"same-full-identity":"silver","same-full-identity":"gold"}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        bootstrap.read_json(path)


def test_local_cli_plan_is_offline_and_apply_flags_fail_before_auth(xml, tmp_path, capsys):
    output = tmp_path / "cli-review"
    bootstrap.main(["--xml", str(xml), "--output", str(output), "--user-label", USER])
    assert "review_required" in capsys.readouterr().out
    assert (output / "creation-manifest.json").exists()
    with pytest.raises(SystemExit):
        bootstrap.main(["--xml", str(xml), "--output", str(output), "--apply"])
    assert not (output / "creation-receipts.jsonl").exists()