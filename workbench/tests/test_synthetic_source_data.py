"""Offline source-grounded planner, runtime and real-SDK contract regressions."""
import ast
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import sqlglot
from sqlglot import exp

from workbench import synthetic_source_data as planner
from workbench import synthetic_source_runtime as runtime
from workbench.source_fixtures import fixture_namespace, plan_sources


def fixture_xml(tmp_path):
    path = tmp_path / "sources.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <SOURCE NAME="A" DATABASETYPE="Oracle" DBDNAME="DB" OWNERNAME="O">
        <SOURCEFIELD NAME="CUSTOMER_ID" DATATYPE="number" PRECISION="3" SCALE="0" NULLABLE="NOTNULL" KEYTYPE="PRIMARY KEY"/>
        <SOURCEFIELD NAME="STATUS" DATATYPE="varchar2" PRECISION="1" SCALE="0"/>
        <SOURCEFIELD NAME="FRACTION" DATATYPE="number" PRECISION="2" SCALE="2"/>
        <SOURCEFIELD NAME="PASSWORD" DATATYPE="varchar2" PRECISION="2" SCALE="0"/>
      </SOURCE>
      <SOURCE NAME="B" DATABASETYPE="Oracle" DBDNAME="DB" OWNERNAME="O">
        <SOURCEFIELD NAME="ACCOUNT_KEY" DATATYPE="varchar2" PRECISION="2" SCALE="0"/>
      </SOURCE>
      <MAPPING NAME="M">
        <TRANSFORMATION NAME="QA" TYPE="Source Qualifier"><TRANSFORMFIELD NAME="CUSTOMER_ID"/><TABLEATTRIBUTE NAME="Sql Query" VALUE=""/></TRANSFORMATION>
        <TRANSFORMATION NAME="QB" TYPE="Source Qualifier"><TRANSFORMFIELD NAME="ACCOUNT_KEY"/></TRANSFORMATION>
        <TRANSFORMATION NAME="J" TYPE="Joiner">
          <TRANSFORMFIELD NAME="LEFT_KEY"/><TRANSFORMFIELD NAME="RIGHT_KEY"/>
          <TABLEATTRIBUTE NAME="Join Condition" VALUE="LEFT_KEY = RIGHT_KEY"/>
        </TRANSFORMATION>
        <TRANSFORMATION NAME="STATUS_QUERY" TYPE="Source Qualifier">
          <TABLEATTRIBUTE NAME="Sql Query" VALUE="SELECT * FROM O.A WHERE STATUS IN ('Y', 'N', 'TOO_WIDE')"/>
        </TRANSFORMATION>
        <INSTANCE NAME="A" TYPE="SOURCE" TRANSFORMATION_NAME="A" DBDNAME="DB"/>
        <INSTANCE NAME="B" TYPE="SOURCE" TRANSFORMATION_NAME="B" DBDNAME="DB"/>
        <INSTANCE NAME="QA" TYPE="TRANSFORMATION" TRANSFORMATION_NAME="QA" TRANSFORMATION_TYPE="Source Qualifier"/>
        <INSTANCE NAME="QB" TYPE="TRANSFORMATION" TRANSFORMATION_NAME="QB" TRANSFORMATION_TYPE="Source Qualifier"/>
        <INSTANCE NAME="J" TYPE="TRANSFORMATION" TRANSFORMATION_NAME="J" TRANSFORMATION_TYPE="Joiner"/>
        <INSTANCE NAME="STATUS_QUERY" TYPE="TRANSFORMATION" TRANSFORMATION_NAME="STATUS_QUERY" TRANSFORMATION_TYPE="Source Qualifier"/>
        <CONNECTOR FROMINSTANCE="A" FROMFIELD="CUSTOMER_ID" TOINSTANCE="QA" TOFIELD="CUSTOMER_ID"/>
        <CONNECTOR FROMINSTANCE="B" FROMFIELD="ACCOUNT_KEY" TOINSTANCE="QB" TOFIELD="ACCOUNT_KEY"/>
        <CONNECTOR FROMINSTANCE="QA" FROMFIELD="CUSTOMER_ID" TOINSTANCE="J" TOFIELD="LEFT_KEY"/>
        <CONNECTOR FROMINSTANCE="QB" FROMFIELD="ACCOUNT_KEY" TOINSTANCE="J" TOFIELD="RIGHT_KEY"/>
      </MAPPING>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return path


@pytest.fixture
def small_plan(tmp_path):
    return planner.build_plan(fixture_xml(tmp_path), rows_per_table=3)


def test_planner_preserves_exact_namespace_and_sources(tmp_path):
    path = fixture_xml(tmp_path)
    before = path.read_bytes()
    original = plan_sources(path, runtime.CATALOG)
    plan = planner.build_plan(path, rows_per_table=3)
    assert plan == planner.build_plan(path, rows_per_table=3)
    for table, source in zip(plan["tables"], original["tables"]):
        assert {k: table[k] for k in source} == source
        assert (table["schema"], table["table"]) == fixture_namespace(table["xml_identity"])
    assert path.read_bytes() == before
    assert plan["metadata"]["keys"][0]["xml_keytype"] == "PRIMARY KEY"
    assert plan["metadata"]["relationships"][0]["key_aliases"] == ["LEFT_KEY", "RIGHT_KEY"]
    assert plan["id_domains"][0]["confidence"] == 0.8
    a, b = plan["tables"]
    assert [str(r[0]) for r in a["synthetic_rows"]] == [r[0] for r in b["synthetic_rows"]]
    assert {r[1] for r in a["synthetic_rows"]} == {"N", "Y"}
    assert all(r[3] is None for r in a["synthetic_rows"])
    assert plan["metadata"]["withheld"]["literal_outside_type_or_width"] == 1
    assert plan["relationship_parity"] == plan["business_parity"] == "not_established"


@pytest.mark.parametrize("kind,value,valid", [
    ("DECIMAL(38,0)", "9" * 38, True), ("DECIMAL(38,0)", "1" + "0" * 38, False),
    ("DECIMAL(2,2)", "0.09", True), ("DECIMAL(2,2)", "1", False),
    ("DECIMAL(2,2)", "0.001", False), ("DECIMAL(2,2)", "NaN", False),
    ("INT", 2147483648, False), ("SMALLINT", 32767, True),
    ("STRING", "AB", True), ("STRING", "ABC", False),
    ("TIMESTAMP_NTZ", "2026-01-01 00:00:00", True),
    ("TIMESTAMP_NTZ", "2026-01-01T00:00:00+01:00", False),
    ("BINARY", "0123", True), ("BINARY", "xyz", False), ("DOUBLE", float("inf"), False),
])
def test_value_bounds(kind, value, valid):
    column = {"type": kind, "precision": "2", "nullable": False}
    if valid:
        runtime.validate_value(column, value)
    else:
        with pytest.raises(runtime.FixtureError):
            runtime.validate_value(column, value)


@pytest.mark.parametrize("mutation", ["catalog", "hash", "namespace", "type", "nullability", "rows"])
def test_plan_tampering_fails_closed(small_plan, mutation):
    plan = deepcopy(small_plan)
    if mutation == "catalog":
        plan["catalog"] = "production"
    elif mutation == "hash":
        plan["package_sha256"] = "bad"
    elif mutation == "namespace":
        plan["tables"][0]["schema"] += "_bad"
    elif mutation == "type":
        plan["tables"][0]["columns"][0]["type"] = "STRING); DELETE"
    elif mutation == "nullability":
        plan["tables"][0]["synthetic_rows"][0][0] = None
    else:
        plan["tables"][0]["synthetic_rows"] = []
    plan["plan_sha256"] = runtime.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(runtime.FixtureError):
        runtime.validate_plan(plan)


class Row(dict):
    def asDict(self, recursive=False):
        return dict(self)


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def collect(self):
        return self.rows


class Spark:
    """Offline protocol double, not a Spark/Delta transaction simulator."""
    def __init__(self, plan, populated=None, failure=None, isolation="Serializable", race=None, no_metrics=False):
        self.plan = plan
        self.tables = {runtime.full_name(plan, t): t for t in plan["tables"]}
        self.counts = {name: 0 for name in self.tables}
        self.counts.update(populated or {})
        self.queries = []
        self.failure = failure
        self.race = race
        self.isolation = isolation
        self.no_metrics = no_metrics
        self.corrupt: str | None = None
        self.bad_schema: str | None = None
        self.swap_metadata = False
        self.observations = {}

    def table(self, name):
        columns = self.tables[name]["columns"]
        fields = [NS(name=c["name"], nullable=c["nullable"], dataType=NS(simpleString=lambda c=c: c["type"].lower())) for c in columns]
        if name == self.bad_schema:
            fields[0].nullable = not fields[0].nullable
        return NS(schema=NS(fields=fields))

    def sql(self, sql):
        self.queries.append(sql)
        if sql.startswith("SELECT current_user"):
            return Frame([Row(principal="owner@example.com")])
        name = next((n for n in self.tables if n in sql), None)
        assert name is not None, sql
        if sql.startswith("DESCRIBE DETAIL"):
            self.observations[name] = self.observations.get(name, 0) + 1
            return Frame([Row(format="delta", id=name + ("changed" if self.swap_metadata and self.observations[name] > 1 else ""),
                properties={"orion.fixture": "wrong" if name == self.corrupt else "xml_source_schema_only",
                            "orion.package_sha256": self.plan["package_sha256"], "delta.isolationLevel": self.isolation})])
        if sql.startswith("SELECT COUNT"):
            return Frame([Row(n=self.counts[name])])
        assert sql.startswith("INSERT INTO")
        if self.race == name:
            self.counts[name] = 7
        if self.failure == name:
            # Model acknowledgement failure AFTER a possible commit.
            self.counts[name] += len(self.tables[name]["synthetic_rows"])
            raise RuntimeError("password=NEVER_LOG_THIS")
        inserted = 0 if self.counts[name] else len(self.tables[name]["synthetic_rows"])
        self.counts[name] += inserted
        return Frame([] if self.no_metrics else [Row(num_affected_rows=inserted)])


def test_runtime_skips_populated_and_conditional_insert_only(small_plan, tmp_path):
    names = [runtime.full_name(small_plan, t) for t in small_plan["tables"]]
    spark = Spark(small_plan, populated={names[0]: 22})
    receipt = runtime.run(spark, small_plan, tmp_path, expected_user="owner@example.com")
    assert receipt["status"] == "completed"
    assert spark.counts[names[0]] == 22
    assert receipt["tables"][0]["count_before"] == receipt["tables"][0]["count_after"] == 22
    assert receipt["tables"][0]["inserted"] == 0 and receipt["tables"][0]["skipped"]
    statements = [q for q in spark.queries if q.startswith("INSERT")]
    assert len(statements) == 1 and names[0] not in statements[0]
    tree = sqlglot.parse_one(statements[0], read="databricks")
    assert isinstance(tree, exp.Insert) and list(tree.find_all(exp.Exists))
    assert receipt["tables"][1]["inserted"] == 3
    assert json.loads((tmp_path / "receipt-final.json").read_bytes())["status"] == "completed"
    assert all(q.startswith(("SELECT", "DESCRIBE", "INSERT")) for q in spark.queries)


@pytest.mark.parametrize("problem", ["marker", "schema", "isolation", "identity", "swap"])
def test_runtime_preflight_fails_without_writes(small_plan, tmp_path, problem):
    spark = Spark(small_plan)
    names = list(spark.tables)
    if problem == "marker": spark.corrupt = names[-1]
    if problem == "schema": spark.bad_schema = names[-1]
    if problem == "isolation": spark.isolation = "WriteSerializable"
    if problem == "swap": spark.swap_metadata = True
    with pytest.raises(runtime.FixtureError):
        runtime.run(spark, small_plan, tmp_path, expected_user="wrong" if problem == "identity" else "owner@example.com",
                    require_serializable=problem == "isolation")
    assert not any(q.startswith("INSERT") for q in spark.queries)
    assert json.loads((tmp_path / "receipt-final.json").read_bytes())["status"] == "failed"


def test_write_failure_retains_partial_receipt_and_no_retry(small_plan, tmp_path):
    spark = Spark(small_plan)
    spark.failure = list(spark.tables)[1]
    with pytest.raises(runtime.FixtureError):
        runtime.run(spark, small_plan, tmp_path)
    receipt = json.loads((tmp_path / "receipt-final.json").read_bytes())
    assert receipt["status"] == "failed" and receipt["tables"][0]["inserted"] == 3
    assert receipt["tables"][1]["commit_uncertain"] is True
    assert receipt["tables"][1]["inserted"] is None
    assert len([q for q in spark.queries if q.startswith("INSERT")]) == 2
    assert "NEVER_LOG_THIS" not in json.dumps(receipt)
    assert list(tmp_path.glob("receipt-0*.json"))


def test_statement_snapshot_race_skips_and_unknown_metrics_not_fabricated(small_plan, tmp_path):
    race_dir, metric_dir = tmp_path / "race", tmp_path / "metrics"
    race_dir.mkdir(); metric_dir.mkdir()
    spark = Spark(small_plan)
    spark.race = list(spark.tables)[0]
    receipt = runtime.run(spark, small_plan, race_dir)
    assert receipt["tables"][0]["inserted"] == 0
    assert receipt["tables"][0]["count_after"] == 7
    assert receipt["tables"][0]["skipped"]
    receipt = runtime.run(Spark(small_plan, no_metrics=True), small_plan, metric_dir)
    assert all(t["inserted"] is None for t in receipt["tables"])
    assert "No proven atomic" in receipt["concurrency_boundary"]


def test_malformed_json_persists_receipt(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("not json")
    with pytest.raises(runtime.FixtureError, match="plan_read_failed"):
        runtime.run_file(None, path, tmp_path, expected_hash="a" * 64, expected_user="owner")
    assert json.loads((tmp_path / "receipt-final.json").read_bytes())["errors"] == ["plan_read_failed"]


@pytest.fixture(scope="module")
def original_plan():
    return planner.build_plan(planner.ROOT / "input" / "LumenExample.XML")


def test_full_original_583_table_plan_and_wil_evidence(original_plan):
    plan = original_plan
    assert plan["table_count"] == 583 and plan["column_count"] == 27908 and plan["schema_count"] == 136
    assert plan["package_sha256"] == planner.PACKAGE_SHA256
    runtime.validate_plan(plan)
    counters = [t for t in plan["tables"] if t["generation"] == "captured_counter_select_literals"]
    assert counters, "Actual counter SQL must resolve to existing original SOURCE definitions"
    values = {int(r[0]) for t in counters for r in t["synthetic_rows"]}
    assert {18299, 18300, 18301, 18302} <= values
    assert all(int(r[1]) == 1 for t in counters for r in t["synthetic_rows"])
    settings = plan["metadata"]["dependency_settings"]
    assert settings and any(54 in s["max_wait_count_literals"] for s in settings)
    assert any(300000 in s["sleep_milliseconds_literals"] for s in settings)
    assert all(s["resolved_fixture_indices"]["ae2e_template"] is None for s in settings)
    assert all(s["resolved_fixture_indices"]["ae2e_template_job_dependency"] is None for s in settings)
    assert all(s["parity"] == "not_established" for s in settings)
    for table in plan["tables"]:
        for row in table["synthetic_rows"]:
            for column, value in zip(table["columns"], row):
                runtime.validate_value(column, value)


class Workspace:
    def __init__(self):
        self.files = {}; self.calls = []
        self.root: str | None = None

    def mkdirs(self, path):
        self.root = path
        self.calls.append(("mkdirs", path))

    def upload(self, path, content, **kwargs):
        assert kwargs["overwrite"] is False
        self.files[path] = content
        self.calls.append(("upload", path, kwargs["format"].value))

    def download(self, path, **kwargs):
        assert kwargs["format"].value == "AUTO"
        return io.BytesIO(self.files[path])

    def list(self, root):
        return [NS(path=p) for p in self.files if p.startswith(root)]


@pytest.mark.parametrize("cloud_failure", [False, True])
def test_sdk_deploy_run_is_unique_serverless_and_explicit_labs(original_plan, tmp_path, monkeypatch, cloud_failure):
    from databricks.sdk.service import jobs
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(original_plan))
    monkeypatch.setattr(planner, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(planner, "build_plan", lambda *a, **kw: original_plan)
    workspace = Workspace()
    calls = []
    def create(**kwargs):
        calls.append(("create", kwargs))
        return NS(job_id=123)
    def run_now(**kwargs):
        calls.append(("run_now", kwargs))
        def result(**kwargs):
            receipt = {"status": "failed" if cloud_failure else "completed", "catalog": runtime.CATALOG,
                       "plan_sha256": original_plan["plan_sha256"], "tables": [], "errors": []}
            assert workspace.root is not None
            workspace.files[workspace.root + "/receipt-final.json"] = json.dumps(receipt).encode()
            if cloud_failure:
                raise RuntimeError("secret=not-in-receipts")
            return NS(state=NS(as_dict=lambda: {"result_state": "SUCCESS"}))
        return NS(response=NS(run_id=456), result=result)
    client = NS(workspace=workspace, jobs=NS(create=create, run_now=run_now),
                current_user=NS(me=lambda: NS(id="12345", user_name="owner@example.com")))
    profiles = []
    def identity(profile):
        profiles.append(profile)
        return NS(mode="profile", host="https://example.com", user_id="12345", client=client)
    if cloud_failure:
        with pytest.raises(runtime.FixtureError):
            planner.deploy_run(plan_path, "not-used", identity_factory=identity)
    else:
        planner.deploy_run(plan_path, "not-used", identity_factory=identity)
    assert profiles == ["labs"]
    kwargs = calls[0][1]
    assert kwargs["performance_target"] is jobs.PerformanceTarget.STANDARD
    assert kwargs["run_as"].user_name == "owner@example.com"
    assert kwargs["max_concurrent_runs"] == 1
    task = kwargs["tasks"][0].as_dict()
    assert not any("cluster" in key for key in task)
    assert task["max_retries"] == 0 and task["notebook_task"]["source"] == "WORKSPACE"
    assert workspace.root is not None
    assert workspace.root.startswith("/Workspace/Users/owner@example.com/orion-synthetic-source-data/")
    assert len(workspace.root.rsplit("/", 1)[-1]) == 32
    assert len([c for c in workspace.calls if c[0] == "upload"]) == 3
    assert [c for c in workspace.calls if c[0] == "upload"][-1][-1] == "SOURCE"
    receipt = json.loads(next((tmp_path / "output").glob("*/deployment-receipt.json")).read_bytes())
    assert receipt["status"] == ("failed_or_partial" if cloud_failure else "completed")
    assert "not-in-receipts" not in json.dumps(receipt)


def test_actual_sdk_workspace_extension_uses_multipart_not_base64():
    from databricks.sdk.mixins.workspace import WorkspaceExt
    from databricks.sdk.service.workspace import ImportFormat, ExportFormat
    requests = []
    def do(method, path, **kwargs):
        requests.append((method, path, kwargs))
        return {"contents": io.BytesIO(b"plan-content")} if method == "GET" else None
    workspace = WorkspaceExt(NS(_cfg=NS(workspace_id=None), do=do))
    workspace.upload("/Workspace/Users/owner/plan.json", b"plan-content", format=ImportFormat.AUTO, overwrite=False)
    with workspace.download("/Workspace/Users/owner/plan.json", format=ExportFormat.AUTO) as content:
        assert content.read() == b"plan-content"
    assert requests[0][2]["files"] == {"content": b"plan-content"}
    assert requests[0][2]["data"]["format"] == "AUTO"
    assert "overwrite" not in requests[0][2]["data"]
    assert requests[1][2]["query"]["direct_download"] == "true"


def test_exported_source_and_runtime_are_serverless_safe():
    source = planner.notebook_source("/Workspace/Users/owner/unique")
    assert source.startswith("# Databricks notebook source\n")
    ast.parse(source)
    text = Path(runtime.__file__).read_text(encoding="utf-8")
    ast.parse(text)
    for prohibited in ("sparkContext", ".rdd", "parallelize", ".cache(", ".persist(", "dbfs:/", "spark.conf.set", "ALTER TABLE", "TRUNCATE", "DELETE FROM", "CREATE TABLE", "OVERWRITE"):
        assert prohibited not in source + text
    assert "run_file" in source and "expected_user" in source