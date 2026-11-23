"""Offline campaigns, real generated notebook, immutable scope and DDL guards."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from workbench.app import _create_package_app, PackageIndex
from workbench import delta_state as ds
from workbench.validation_campaigns import CampaignBody, CampaignStore, build_campaign, input_pins, PILLARS
from workbench.validation_data import compare_rows, controlled_fixture_suite
from workbench.target_fixtures import plan_targets, target_sql, catalog_names
from workbench.validation_provisioning import provision_test_catalogs
from workbench.validation_readiness import STATUSES
from workbench.validation_contracts import unpack_source_contract
from workbench.validation_campaigns import report_payload, decode_report
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[2]
XML = '''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
<TARGET NAME="RESULT" DATABASETYPE="Oracle" DBDNAME="A" OWNERNAME="OWNER">
<TARGETFIELD NAME="ID" DATATYPE="number" PRECISION="10" SCALE="0" NULLABLE="NOTNULL" KEYTYPE="PRIMARY KEY"/>
<TARGETFIELD NAME="LABEL" DATATYPE="varchar2" PRECISION="20" SCALE="0" NULLABLE="NULL"/>
</TARGET>
<MAPPING NAME="M"><INSTANCE NAME="RESULT" TRANSFORMATION_NAME="RESULT" TRANSFORMATION_TYPE="Target Definition" TYPE="TARGET"/></MAPPING>
<WORKFLOW NAME="W"><SESSION NAME="S" MAPPINGNAME="M"/><TASKINSTANCE NAME="S" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
</FOLDER></REPOSITORY></POWERMART>'''


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "input.xml"
    path.write_text(XML, encoding="utf-8")
    model = analyze_package(path)
    directory = tmp_path / "canonical"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"understanding_sha256": "a" * 64}), encoding="utf-8")
    return PackageIndex(model, directory)


def body_for(index, **changes):
    pins = input_pins(index)
    return CampaignBody(consent=True, workflow_id=next(n["id"] for n in index.model["nodes"] if n["kind"] == "workflow"),
        scenario="bronze", expected_package_hash=pins["package_hash"],
        expected_understanding_sha256=pins["understanding_sha256"], expected_analysis_version=pins["analysis_version"],
        source_fixture_catalog="external_source_fixtures",
        target_layers={n["id"]: "silver" for n in index.model["nodes"] if n["kind"] == "target"}).model_copy(update=changes)


def report_for(index, **changes):
    return build_campaign(index, body_for(index, **changes), project_id="p" * 32,
                          workspace_id="https://example.test", user_id="123", user_label="123", campaign_id="b" * 32)


def column(kind="DECIMAL(6,2)", nullable=False, **extra):
    return [{"name": "v", "type": kind, "nullable": nullable, **extra}]


def test_controlled_notebook_actual_reference_and_equal_count_negative():
    suite = controlled_fixture_suite()
    assert suite["output_comparison"]["status"] == "passing"
    assert suite["input_validation"]["status"] == "passing"
    assert suite["same_count_negative_control"] == "passing"
    assert suite["output_comparison"]["actual_duplicate_keys"] == 1
    assert suite["output_comparison"]["actual_null_cells"] == 2
    assert suite["business_parity"] == "not_established"
    assert suite["notebook"]["cells"][0]["metadata"]["language"] == "python"


@pytest.mark.parametrize("value", ["1.001", "10000.00", "NaN", "Infinity", 1.0, True, None])
def test_decimal_fails_precision_coercion_and_required_null(value):
    result = compare_rows([{"v": "1.00"}], [{"v": value}], column())
    assert result["status"] == "failed" and result["actual_invalid_rows"] == 1


@pytest.mark.parametrize("kind,expected,actual", [
    ("INT", 1, True), ("INT", 1, 2 ** 31), ("SMALLINT", 1, 32768),
    ("DATE", "2026-11-05", "2026-11-05T00:00:00"),
    ("TIMESTAMP_NTZ", "2026-11-05T00:00:00", "2026-11-05T00:00:00Z"),
    ("TIMESTAMP_NTZ", "2026-11-05T00:00:00.123456", "2026-11-05T00:00:00.1234567"),
    ("STRING", "A", "a"), ("STRING", "A", "A "), ("STRING", "", None),
])
def test_type_date_collation_and_empty_string_not_silently_coerced(kind, expected, actual):
    assert compare_rows([{"v": expected}], [{"v": actual}], column(kind, True))["status"] == "failed"


def test_bag_multiplicity_and_key_nulls_no_value_leakage():
    rows = [{"v": "private-value"}, {"v": "private-value"}, {"v": None}]
    result = compare_rows(rows, list(reversed(rows)), column("STRING", True), keys=("v",))
    assert result["status"] == "passing" and result["actual_duplicate_keys"] == 1 and result["actual_null_keys"] == 1
    assert "private-value" not in json.dumps(result)
    wrong = compare_rows(rows, [{"v": "private-value"}, {"v": None}, {"v": None}], column("STRING", True))
    assert wrong["status"] == "failed" and wrong["missing_rows"] == wrong["unexpected_rows"] == 1


def test_width_byte_semantics_and_snapshot_bounds():
    assert compare_rows([{"v": "é"}], [{"v": "é"}], column("STRING", width=1, width_unit="utf8_bytes"))["status"] == "failed"
    assert compare_rows([{"v": "é"}], [{"v": "é"}], column("STRING", width=1, width_unit="characters"))["status"] == "passing"
    with pytest.raises(ValueError):
        compare_rows([{}] * 10001, [], column())
    with pytest.raises(ValueError):
        compare_rows([], [], column(), keys=("absent",))


def test_target_planner_deterministic_identity_width_and_create_only(tmp_path):
    path = tmp_path / "targets.xml"
    path.write_text(XML, encoding="utf-8")
    plan = plan_targets(path, "test_output")
    assert plan == plan_targets(path, "test_output")
    assert len(plan["tables"]) == 1
    table = plan["tables"][0]
    assert table["columns"][1]["character_width"] == "20"
    assert table["columns"][1]["character_width_unit"] == "unverified"
    assert table["columns"][0]["key_type"] == "PRIMARY KEY"
    sql = target_sql(plan["catalog"], table, plan["package_sha256"])
    assert "CREATE TABLE " in sql and "IF NOT EXISTS" not in sql
    assert not any(word in sql for word in ("TRUNCATE", "INSERT", "DROP", "GRANT"))


@pytest.mark.parametrize("change", [
    lambda s: s.replace('DATATYPE="number"', 'DATATYPE="unknown"'),
    lambda s: s.replace('PRECISION="10"', 'PRECISION="39"'),
    lambda s: s.replace('NAME="LABEL"', 'NAME="id"'),
    lambda s: s.replace('NULLABLE="NOTNULL"', 'NULLABLE="UNKNOWN"'),
    lambda s: s.replace('</TARGET>', '</TARGET>' + s[s.index('<TARGET '):s.index('</TARGET>') + 9]),
])
def test_target_types_columns_and_identity_fail_closed(tmp_path, change):
    path = tmp_path / "targets.xml"
    path.write_text(change(XML), encoding="utf-8")
    with pytest.raises(ValueError):
        plan_targets(path, "test_output")


def test_campaign_all_pillars_gates_and_local_immutable_scopes(index, tmp_path):
    report = report_for(index)
    assert set(report["pillars"]) == set(PILLARS)
    assert report["status"] == "blocked" and not report["execution_allowed"] and not report["deployment_allowed"]
    assert report["catalog_plan"]["targets"] and report["source_coverage"]["obligations"]
    assert not report["bindings"]["approved"] and not report["catalog_plan"]["provisioned"]
    assert report["controlled_fixture"]["basis"] == "controlled_synthetic_fixture_not_business_parity"
    store = CampaignStore(local=tmp_path / "reports")
    store.save(report)
    assert store.read(report["project_id"], report["pins"]["package_hash"], report["id"]) == report
    assert store.save(report)["status"] == "published_exact_retry"
    with pytest.raises(ValueError):
        store.read("another-project", report["pins"]["package_hash"], report["id"])
    with pytest.raises(ValueError):
        store.read(report["project_id"], report["pins"]["package_hash"], "../other")


@pytest.mark.parametrize("changes", [{"consent": False}, {"expected_understanding_sha256": "c" * 64},
                                     {"expected_analysis_version": "d" * 64}, {"source_fixture_catalog": "bad;DROP"}])
def test_campaign_pins_consent_identifier_fail_before_run(index, changes):
    with pytest.raises((PermissionError, ValueError)):
        report_for(index, **changes)


class FakeDelta(ds.DeltaStateStore):
    def __init__(self):
        self.catalogs, self.schemas, self.table_info, self.records, self.sql = {}, {}, {}, [], []
        from databricks.sdk.errors import NotFound, AlreadyExists
        self.objects, self.uploads, self.downloads = {}, [], []
        def download(path):
            self.downloads.append(path)
            if path not in self.objects:
                raise NotFound("absent")
            return SimpleNamespace(contents=BytesIO(self.objects[path]))
        def upload(path, stream, overwrite=False):
            assert overwrite is False
            self.uploads.append(path)
            if path in self.objects:
                raise AlreadyExists("immutable")
            self.objects[path] = stream.read()
        def metadata(path):
            if path not in self.objects:
                raise NotFound("absent")
            return SimpleNamespace(content_length=len(self.objects[path]))
        client = SimpleNamespace(config=SimpleNamespace(host="https://example.test"),
            catalogs=SimpleNamespace(get=lambda n: self.catalogs[n]), schemas=SimpleNamespace(get=lambda n: self.schemas[n]),
            volumes=SimpleNamespace(read=lambda n: SimpleNamespace(owner="test@example.test")),
            files=SimpleNamespace(download=download, upload=upload, get_metadata=metadata, create_directory=lambda p: None),
            tables=SimpleNamespace(get=lambda n: self.table_info[n]))
        super().__init__(client, "warehouse", "state", "metadata", "123")
        self.fail_at = None

    def _authorize(self):
        return "test@example.test"

    def read_table(self, table, **scope):
        return [deepcopy(r) for r in self.records if all(r.get(k) == v for k, v in scope.items() if k != "limit")][:scope["limit"]]

    def upsert_records(self, table, records, *, migration_id):
        for record in records:
            row = dict(record, migration_id=migration_id)
            assert len(row["payload"].encode()) <= ds.MAX_PAYLOAD
            assert len(ds._json(row).encode()) < ds.MAX_PAYLOAD
            assert row["content_hash"] == ds._content_hash(row)
            if row not in self.records:
                self.records.append(row)

    def _execute(self, sql, values=None):
        self.sql.append(sql)
        if self.fail_at == len(self.sql):
            raise RuntimeError("password=private-value")
        if sql == "SHOW CATALOGS":
            return [[n] for n in self.catalogs]
        if sql.startswith("CREATE CATALOG "):
            n = sql.split('`')[1]
            self.catalogs[n] = SimpleNamespace(name=n, owner="test@example.test")
        if sql.startswith("CREATE SCHEMA "):
            n = '.'.join(sql.split('`')[i] for i in (1, 3))
            self.schemas[n] = SimpleNamespace(full_name=n, owner="test@example.test")
        return []


def prepare(delta, report):
    for table in report["catalog_plan"]["targets"]:
        n = '.'.join((table["destination_catalog"], table["schema"], table["table"]))
        delta.table_info[n] = SimpleNamespace(full_name=n, owner="test@example.test", data_source_format="DELTA",
            columns=[SimpleNamespace(name=c["name"], type_text=c["type"], nullable=c["nullable"]) for c in table["columns"]],
            properties={"orion.fixture": "xml_target_schema_only", "orion.package_sha256": report["pins"]["package_hash"]})


def provision(delta, report, journal):
    return provision_test_catalogs(delta, report, consent=True, confirmed_names=report["catalog_plan"]["names"],
                                  expected_report_hash=report["report_sha256"], journal=journal)


def test_delta_campaign_readback_immutability_and_cross_user(index):
    delta = FakeDelta()
    store, report = CampaignStore(delta=delta), report_for(index)
    store.save(report)
    assert store.read(report["project_id"], report["pins"]["package_hash"], report["id"]) == report
    assert store.save(report)["status"] == "published_exact_retry"
    other = deepcopy(report); other["user_id"] = "another-user"
    with pytest.raises(PermissionError):
        store.save(other)
    delta.records[0]["payload"] = json.dumps({**report, "production_ready": True})
    with pytest.raises(ValueError):
        store.read(report["project_id"], report["pins"]["package_hash"], report["id"])


def test_create_only_provision_durable_intent_metadata_and_no_business_gate(index):
    delta, report = FakeDelta(), report_for(index)
    prepare(delta, report)
    store = CampaignStore(delta=delta)
    result = provision(delta, report, lambda event: store.provisioning_journal(report, event))
    assert result["status"] == "passing" and len(result["observed_catalogs"]) == 3
    assert len(result["verified_tables"]) == 1 and not result["data_loaded"]
    assert [e["phase"] for e in store.provisioning_events(report)] == ["started", "finished"]
    assert not report["production_ready"] and not report["catalog_plan"]["provisioned"]
    assert all(s == "SHOW CATALOGS" or s.startswith("CREATE ") for s in delta.sql)
    assert all("external_source_fixtures" not in s for s in delta.sql)
    with pytest.raises(ValueError):
        provision(delta, report, lambda event: store.provisioning_journal(report, event))


def test_provision_intent_failure_prevents_ddl_and_partial_failure_safe(index):
    delta, report = FakeDelta(), report_for(index)
    def fail(event):
        raise RuntimeError("No durable authority")
    with pytest.raises(RuntimeError):
        provision(delta, report, fail)
    assert delta.sql == ["SHOW CATALOGS"]
    delta = FakeDelta(); delta.fail_at = 3
    events = []
    result = provision(delta, report, events.append)
    assert result["status"] == "failed" and len(result["observed_catalogs"]) == 1
    assert "private-value" not in json.dumps(events)
    assert not result["automatic_retry_allowed"] and len(events) == 2


@pytest.mark.parametrize("change", ["consent", "owner", "hash", "names"])
def test_provision_scope_guard_before_remote_reads(index, change):
    delta, report = FakeDelta(), report_for(index)
    kwargs = dict(consent=True, confirmed_names=report["catalog_plan"]["names"], expected_report_hash=report["report_sha256"])
    if change == "consent": kwargs["consent"] = False
    if change == "owner": delta.user_id = "other"
    if change == "hash": kwargs["expected_report_hash"] = "f" * 64
    if change == "names": kwargs["confirmed_names"] = {"bronze": "production"}
    with pytest.raises((PermissionError, ValueError)):
        provision_test_catalogs(delta, report, journal=lambda e: pytest.fail("Unexpected journal"), **kwargs)
    assert delta.sql == []


def test_real_xml_target_inventory_and_selected_wil_blockers(tmp_path):
    plan = plan_targets(ROOT / "input" / "LumenExample.XML", "dedicated_test_output")
    assert len(plan["tables"]) == 307 and sum(len(t["columns"]) for t in plan["tables"]) > 1000
    model = analyze_package(ROOT / "input" / "LumenExample.XML")
    directory = tmp_path / "canonical"; directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"understanding_sha256": "a" * 64}), encoding="utf-8")
    index = PackageIndex(model, directory)
    from workbench.package_parity import PARENT_ID
    report = report_for(index, workflow_id=PARENT_ID, target_layers={})
    codes = {b["code"] for b in report["blockers"]}
    assert {"MAPPING_NOT_CONVERTED", "EXTERNAL_PROCEDURE_BODY_REQUIRED", "SCOPED_PARAMETER_VALUES_REQUIRED"} <= codes
    assert len(report["source_coverage"]["workflow_ids"]) == 2
    # Fresh selected parent+child definition closure includes the parent counter
    # and the child's flat-file target. Do not reuse the child-only count of 8.
    assert len(report["catalog_plan"]["targets"]) == 9
    assert any(t["xml_identity"][-1] == "AE2E_JOB_DEP_CHK_COUNT" for t in report["catalog_plan"]["targets"])
    assert not report["catalog_plan"]["target_errors"]
    assert report["business_parity"] == "not_established" and len(report["source_coverage"]["obligations"]) > 15
    candidates = report["workflow_candidates"]
    assert len(candidates) == len({c["workflow_id"] for c in candidates}) == 10
    assert [c["name"] for c in candidates] == [
        "wkf_Load_AE2E_WILTEL_NETWORK_DISCO", "wkf_LOAD_AE2E_AOB_PREP_PULL",
        "wkf_LOAD_AE2E_BW_DISC_PREP_PULL", "wkf_LOAD_AE2E_BW_HIST_PREP_PULL",
        "wkf_LOAD_AE2E_BW_INST_PREP_PULL", "wkf_LOAD_AE2E_FUSION_PREP_PULL",
        "wkf_LOAD_AE2E_IOB_PREP_PULL", "wkf_Load_AE2E_VYVX_HIST_PREP_PULL",
        "wkf_Load_AE2E_WILTEL_CUSTOMER_DISCO", "wkf_Load_AE2E_WILTEL_NETWORK_INSTALL"]
    inventory = report["selection_contract"]["cohort_classifications"]
    excluded_counters = [r for r in inventory if r["classification"] == "launch_wrapper" and r["mapping_ids"]]
    assert len(excluded_counters) == 6
    assert all(not r["business_target_ids"] and r["invocation_owner_ids"] for r in excluded_counters)
    assert all(unpack_source_contract(c["source_execution_contract"])["tasks"] and unpack_source_contract(c["source_execution_contract"])["business_target_ids"] for c in candidates)
    assert len(report["pinned_wrapper_support"]["members"]) == 4
    assert len(report["target_assignment_contract"]["unassigned_target_ids"]) == 9
    assert all(c["direct_targets"] and c["mappings"] and c["status"] == "BLOCKED" for c in candidates)
    assert all(c["runtime_status"] == "NOT_RUN" and c["run_id"] is None for c in candidates)
    assert report["release_gate"]["verified_business_workflows"] == 0
    assert report["release_gate"]["minimum_business_workflows"] == 4
    assert not report["release_gate"]["deployment_allowed"]
    delta = FakeDelta()
    store = CampaignStore(delta=delta)
    receipt = store.save(report)
    assert receipt["status"] == "published"
    assert receipt["artifact"]["byte_size"] == len(ds._json(report).encode()) > ds.MAX_PAYLOAD
    assert receipt["artifact"]["sha256"] == ds._hash(report)
    assert receipt["serialized_record_bytes"] == len(ds._json(delta.records[0]).encode()) < ds.MAX_PAYLOAD
    assert CampaignStore(delta=delta).read(report["project_id"], report["pins"]["package_hash"], report["id"]) == report
    summary = store.summary(report["project_id"], report["pins"]["package_hash"], report["id"])
    assert summary["campaign_metrics"]["counts"]["campaign_business_candidates"] == 10
    assert summary["campaign_metrics"]["counts"]["campaign_unassigned_targets"] == 9
    assert "workflow_candidates" not in summary
    print("ACTUAL_LUMEN_STORAGE_PROOF=" + ds._json(receipt))
    local = CampaignStore(local=tmp_path / "owned-service" / "validation-campaigns")
    local.save(report)
    assert CampaignStore(local=local.local).read(report["project_id"], report["pins"]["package_hash"], report["id"]) == report
    # Actual selected Lumen contracts reach the real API/readiness projection,
    # not just a storage adapter or a separately exported shortlist.
    app = _create_package_app(tmp_path / "lumen-api", model_installer=lambda app: None)
    api_report = deepcopy(report)
    api_report["project_id"] = "__legacy__"
    api_report["scenario"] = "original"
    api_report["report_sha256"] = ds._hash({k: v for k, v in api_report.items() if k != "report_sha256"})
    app.state.validation_store_provider().save(api_report)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        app.state.service.active = index
        exported = client.get("/api/validation/campaigns/" + api_report["id"] + "/export")
        assert exported.status_code == 200, exported.text
        assert exported.content == ds._json(api_report).encode()
        result = client.get("/api/readiness")
        assert result.status_code == 200, result.text
        metrics = result.json()
        assert metrics["counts"]["campaign_business_candidates"] == 10
        assert metrics["counts"]["campaign_runtime_not_run"] == 10
        assert metrics["counts"]["campaign_unassigned_targets"] == 9
        assert metrics["counts"]["output_parity_approved"] is None
        assert metrics["release_gate"]["verified_business_workflows"] == 0


def test_api_context_campaign_export_and_local_remote_denial(index, tmp_path):
    app = _create_package_app(tmp_path / "service", model_installer=lambda app: None)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        app.state.service.active = index
        body = body_for(index).model_dump()
        assert client.post('/api/validation/campaigns', json=body).status_code == 403
        assert client.get('/api/validation/context').json()["pins"] == input_pins(index)
        response = client.post('/api/validation/campaigns', json=body, headers={"X-Workbench-Request": "1"})
        assert response.status_code == 201, response.text
        report = response.json()
        assert client.get('/api/validation/campaigns/' + report["id"]).json()["current_input_pins_match"]
        export = client.get('/api/validation/campaigns/' + report["id"] + '/export')
        assert export.json() == report and "attachment" in export.headers["content-disposition"]
        denied = client.post('/api/validation/campaigns/' + report["id"] + '/provision',
            json={"consent": True, "confirmed_catalog_names": report["catalog_plan"]["names"],
                  "expected_report_sha256": report["report_sha256"]}, headers={"X-Workbench-Request": "1"})
        assert denied.status_code == 403
        index.model["source_analysis_version"] = "f" * 64
        assert not client.get('/api/validation/campaigns/' + report["id"]).json()["current_input_pins_match"]
        assert client.post('/api/validation/campaigns', json=body, headers={"X-Workbench-Request": "1"}).status_code == 409


def test_unique_user_and_campaign_names():
    assert catalog_names("user@example.test", "b" * 32) != catalog_names("user@other.test", "b" * 32)
    assert catalog_names("user@example.test", "b" * 32) != catalog_names("user@example.test", "c" * 32)
    assert set(catalog_names("user@example.test", "b" * 32)) == {"bronze", "silver", "gold"}


def test_flat_file_double_target_preserves_type_and_source_behavior():
    from workbench.source_fixtures import mock_type
    from workbench.target_fixtures import target_table
    attrs = dict(NAME="RESULT", DATABASETYPE="Flat File", DBDNAME="A", OWNERNAME="OWNER")
    field = dict(NAME="VALUE", DATATYPE="double", PRECISION="38", SCALE="0", NULLABLE="NULL")
    table = target_table(attrs, [field], dict(repository="R", folder="F"))
    assert table["columns"][0]["type"] == "DOUBLE" and table["columns"][0]["xml_type"] == "double"
    assert "`VALUE` DOUBLE" in target_sql("test_catalog", table, "a" * 64)
    with pytest.raises(ValueError):
        mock_type(field, "Flat File")
    with pytest.raises(ValueError):
        target_table({**attrs, "DATABASETYPE": "Oracle"}, [field], dict(repository="R", folder="F"))


@pytest.mark.parametrize("layers", [{"unknown": "gold"}, {"unknown": "output"}])
def test_invalid_target_layer_assignment_fails_before_actions(index, layers):
    with pytest.raises(ValueError):
        report_for(index, target_layers=layers)


def test_unassigned_targets_create_only_catalogs_no_invented_schema(index):
    delta, report = FakeDelta(), report_for(index, target_layers={})
    result = provision(delta, report, lambda event: None)
    assert len(result["observed_catalogs"]) == 3
    assert not result["observed_schemas"] and not result["verified_tables"]
    assert result["target_schema_status"] == "BLOCKED"
    assert "EXPLICIT_TARGET_LAYER_ASSIGNMENT_REQUIRED" in {b["code"] for b in report["blockers"]}
    assert report["catalog_plan"]["targets"][0]["destination_catalog"] is None


def test_release_status_vocabulary_and_small_cohort_not_padded(index):
    report = report_for(index)
    assert set(STATUSES) == {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
    # Target-only metadata without any wired source is not a business definition.
    assert report["workflow_candidates"] == []
    assert unpack_source_contract(report["selection_contract"]["selected_definition_contracts"][0])["classification"] == "unclassified"
    assert all(s in STATUSES for s in report["release_gate"]["criteria"].values())
    assert not report["release_gate"]["synthetic_contract_tests_qualify"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "1.0", 1, True])
def test_double_rejects_nonfinite_and_implicit_casts(value):
    assert compare_rows([{"v": 1.0}], [{"v": value}], column("DOUBLE"))["status"] == "failed"


def test_column_multisets_and_numeric_aggregates_are_not_whole_row_parity():
    columns = [{"name": "id", "type": "INT", "nullable": False},
               {"name": "amount", "type": "DECIMAL(6,2)", "nullable": False}]
    left = [{"id": 1, "amount": "2.00"}, {"id": 2, "amount": "3.00"}]
    swapped = [{"id": 1, "amount": "3.00"}, {"id": 2, "amount": "2.00"}]
    result = compare_rows(left, swapped, columns, keys=("id",))
    assert result["status"] == "failed"
    assert all(c["multiset_match"] and c["numeric_sum_match"] for c in result["column_checks"])
    assert not result["aggregate_values_exported"] and not result["rows_logged"]
    changed = compare_rows(left, [{"id": 1, "amount": "2.01"}, left[1]], columns)
    assert changed["column_checks"][1]["numeric_sum_match"] is False


def test_double_exact_comparison_no_tolerance():
    assert compare_rows([{"v": 1.0}], [{"v": 1.0}], column("DOUBLE"))["status"] == "passing"
    assert compare_rows([{"v": 1.0}], [{"v": 1.0000000000000002}], column("DOUBLE"))["status"] == "failed"


def test_large_evidence_kept_in_bounded_delta_row_and_tamper_rejected(index):
    report = report_for(index)
    report["additional_evidence"] = ["canonical-evidence-" + str(i % 100) for i in range(5000)]
    report["report_sha256"] = ds._hash({k: v for k, v in report.items() if k != "report_sha256"})
    payload = report_payload(report)
    assert payload["encoding"] == "gzip-base64-campaign-v1"
    assert len(ds._json(payload).encode()) <= ds.MAX_PAYLOAD
    assert decode_report(payload) == report
    store = CampaignStore(delta=FakeDelta()); store.save(report)
    assert store.read(report["project_id"], report["pins"]["package_hash"], report["id"]) == report
    assert json.loads(store.delta.records[0]["payload"])["format"] == "campaign-artifact-manifest-v1"
    with pytest.raises(ValueError):
        decode_report({**payload, "decoded_sha256": "f" * 64})
    with pytest.raises(ValueError):
        decode_report({**payload, "decoded_bytes": 2 * 1024 * 1024 + 1})


def test_exclusive_export_real_pins_and_no_remote(tmp_path):
    from hashlib import sha256
    from workbench.validation_campaigns import export_local_evidence
    xml = tmp_path / "input.xml"; xml.write_text(XML, encoding="utf-8")
    workflow = next(n["id"] for n in analyze_package(xml)["nodes"] if n["kind"] == "workflow")
    output = tmp_path / "proof"
    result = export_local_evidence(xml, output, workflow_id=workflow, source_fixture_catalog="source_fixture")
    report = json.loads((output / "campaign.json").read_bytes())
    assert result["manifest_verified"] and not result["deployment_allowed"]
    assert report["pins"]["understanding_sha256"] != "a" * 64
    proof = json.loads((output / "proof.json").read_bytes())
    assert proof["catalogs_created"] == 0 and not proof["deployment_performed"]
    for name, entry in json.loads((output / "manifest.json").read_bytes()).items():
        assert sha256((output / name).read_bytes()).hexdigest() == entry["sha256"]
    with pytest.raises(FileExistsError):
        export_local_evidence(xml, output, workflow_id=workflow, source_fixture_catalog="source_fixture")


def test_actual_validation_ui_explicit_layers_and_blocked_gate(index):
    from playwright.sync_api import sync_playwright
    report = report_for(index)
    requests = []
    html = (ROOT / "workbench/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "workbench/static/validation-campaigns.js").read_text(encoding="utf-8")
    def serve(route):
        if not route.request.url.startswith("http://validation.test/"):
            return route.abort()
        path = route.request.url.removeprefix("http://validation.test")
        if path == "/":
            return route.fulfill(content_type="text/html", body=html)
        if path == "/static/validation-campaigns.js":
            return route.fulfill(content_type="application/javascript", body=script)
        if path.startswith("/static/"):
            return route.fulfill(body="")
        requests.append((path, route.request.method,
                 route.request.post_data_json if route.request.method == "POST" else None))
        if path == "/api/databricks/volumes/status":
            return route.fulfill(json={"connected": True})
        if path == "/api/databricks/volumes/catalogs":
            return route.fulfill(json={"items": [{"name": "external_source_fixtures"}], "truncated": False})
        if path == "/api/validation/context":
            return route.fulfill(json={"pins": input_pins(index), "hosted_obo": False})
        if path == "/api/validation/campaigns":
            return route.fulfill(status=201, json=report)
        pytest.fail("Unexpected validation browser request: " + path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(); page.route("**/*", serve)
        page.goto("http://validation.test/")
        page.locator("#page-validate").evaluate("element => element.hidden = false")
        page.locator("#validation-workflow").evaluate("(element, id) => element.add(new Option('Workflow', id))", report["workflow_id"])
        page.locator("#validation-workflow").select_option(report["workflow_id"])
        page.locator("#validation-load-catalogs").click()
        page.wait_for_function("document.getElementById('validation-source-catalog').options.length === 2")
        page.locator("#validation-source-catalog").select_option("external_source_fixtures")
        target = report["catalog_plan"]["targets"][0]["target_id"]
        page.get_by_text("Advanced: assign destinations for test output tables", exact=True).click()
        page.locator("#validation-target-layers").fill(json.dumps({target: "silver"}))
        page.locator("#validation-context").click()
        page.locator("#validation-create").wait_for(state="visible")
        page.wait_for_function("!document.getElementById('validation-create').disabled")
        assert page.locator("#validation-consent").count() == 0
        page.locator("#validation-create").click()
        page.locator("#validation-results").get_by_text("Release gate: BLOCKED.", exact=False).wait_for()
        assert page.locator("#validation-provision").is_disabled()
        assert requests[-1][2]["target_layers"] == {target: "silver"}
        assert len(requests) == 4
        assert "output:" not in page.locator("#validation-results").inner_text()
        browser.close()


def test_validation_ui_reads_saved_export_and_reports_failures(index, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    report = report_for(index)
    raw = json.dumps(report, sort_keys=True).encode("utf-8")
    html = (ROOT / "workbench/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "workbench/static/validation-campaigns.js").read_text(encoding="utf-8")
    state = {"export": "fail", "context": "same"}
    requests = []

    def serve(route):
        path = route.request.url.removeprefix("http://validation.test")
        if not route.request.url.startswith("http://validation.test/"):
            return route.abort()
        if path == "/":
            return route.fulfill(content_type="text/html", body=html)
        if path == "/static/validation-campaigns.js":
            return route.fulfill(content_type="application/javascript", body=script)
        if path.startswith("/static/"):
            return route.fulfill(body="")
        requests.append((path, route.request.method))
        if path == f"/api/validation/campaigns/{report['id']}":
            return route.fulfill(json={"report": report, "current_input_pins_match": False,
                                       "provisioning_events": []})
        if path == f"/api/validation/campaigns/{'c' * 32}":
            return route.fulfill(status=404, body="not found")
        if path == "/api/validation/context":
            pins = dict(reversed(list(report["pins"].items())))
            if state["context"] == "changed":
                pins["understanding_sha256"] = "f" * 64
            return route.fulfill(json={"pins": pins, "hosted_obo": False})
        if path == f"/api/validation/campaigns/{report['id']}/export":
            if state["export"] == "fail":
                return route.fulfill(status=409, body="unavailable")
            length = len(raw) + (1 if state["export"] == "wrong_length" else 0)
            return route.fulfill(content_type="application/json", body=raw, headers={
                "X-Workbench-Campaign-Artifact-Bytes": str(length),
                "X-Workbench-Campaign-Current-Input-Pins-Match": "false"})
        pytest.fail("Unexpected validation browser request: " + path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(); page.route("**/*", serve)
        downloads = []; page.on("download", lambda download: downloads.append(download))
        page.goto("http://validation.test/")
        page.locator("#page-validate").evaluate("element => element.hidden = false")
        page.locator("#validation-campaign-id").fill(report["id"])
        page.locator("#validation-read").click()
        expect(page.locator("#validation-export")).to_be_enabled()
        expect(page.locator("#validation-report-freshness")).to_contain_text("stale or not verified")
        page.locator("#validation-context").click()
        expect(page.locator("#validation-report-freshness")).to_contain_text("fingerprints: match")
        state["context"] = "changed"
        page.locator("#validation-context").click()
        expect(page.locator("#validation-report-freshness")).to_contain_text("stale or not verified")
        page.locator("#validation-export").click()
        expect(page.locator("#validation-status")).to_contain_text("HTTP 409")
        assert not downloads
        state["export"] = "wrong_length"
        page.locator("#validation-export").click()
        expect(page.locator("#validation-status")).to_contain_text("length could not be verified")
        assert not downloads
        state["export"] = "ok"
        with page.expect_download() as downloaded:
            page.locator("#validation-export").click()
        downloaded.value.save_as(tmp_path / "saved-report.json")
        assert (tmp_path / "saved-report.json").read_bytes() == raw
        expect(page.locator("#validation-status")).to_contain_text("Current fingerprints: stale")
        page.locator("#validation-campaign-id").fill("c" * 32)
        expect(page.locator("#validation-export")).to_be_disabled()
        expect(page.locator("#validation-results")).to_be_empty()
        page.locator("#validation-read").click()
        expect(page.locator("#validation-status")).to_contain_text("HTTP 404")
        expect(page.locator("#validation-export")).to_be_disabled()
        assert all(method == "GET" for _, method in requests)
        browser.close()