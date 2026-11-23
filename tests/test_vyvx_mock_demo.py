"""Consolidated local contracts. Live Spark correctness is checked by the remote Job."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest
import yaml
from databricks.sdk.service import jobs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deployment/vyvx_mock_demo"))
import compiler
import fixtures
import runtime
import remote
from source_contract import read_contract

@pytest.fixture(scope="module")
def contract():
    return compiler.build_contract()

@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    path = tmp_path_factory.mktemp("vyvx") / "release"
    compiler.export(path)
    return path

def test_exact_xml_dbd_and_complete_source_fields(contract):
    btp, vbus = (contract["tables"][name] for name in ("AE2E_BTP_STG3", "AE2E_VBUS_STG"))
    assert btp["xml_identity"]["DBDNAME"] == "ATLDEV"
    assert vbus["xml_identity"]["DBDNAME"] == "atldev"
    assert len(btp["columns"]) == 53
    assert len(vbus["columns"]) == 39
    assert len(btp["rows"]) == 9 and len(vbus["rows"]) == 10
    for data in contract["tables"].values():
        if data["role"] == "source":
            assert 0 < len(data["rows"]) <= 50
            assert all(set(row) == {c[0] for c in data["columns"]} for row in data["rows"])

def test_mock_namespace_and_roles(contract):
    assert set(contract["catalogs"].values()) == runtime.CATALOGS
    assert {name for name, t in contract["tables"].items() if t["role"] == "destination"} == runtime.DESTINATIONS
    assert all(".vyvx_mock_demo." in t["name"] for t in contract["tables"].values())
    assert all("infotodbxmig" not in t["name"] and "orion_dev_" not in t["name"] for t in contract["tables"].values())

def test_source_widths_match_xml(contract):
    xml = read_contract()
    for name, dbd in (("AE2E_BTP_STG3", "ATLDEV"), ("AE2E_VBUS_STG", "atldev")):
        definition = compiler.definition(xml, name, "SOURCE", dbd)
        widths = {c["attrs"]["NAME"]: int(c["attrs"]["PRECISION"]) for c in definition["children"]
                  if c["tag"] == "SOURCEFIELD" and c["attrs"]["DATATYPE"] in {"varchar2", "nvarchar2", "char"}}
        for row in contract["tables"][name]["rows"]:
            assert all(value is None or len(value) <= widths[col] for col, value in row.items() if col in widths)

def test_positive_negative_filter_contract(contract):
    rows = contract["tables"]["AE2E_BTP_STG3"]["rows"]
    passed = [r for r in rows if r["BILL_DATE"] >= "2006-05-28 00:00:00" and
              (r["GL_CODE"].startswith("761") or r["COMPANY"] == "133")]
    assert len(passed) == 7
    retained = [r for r in passed if r["GL_CODE"] not in contract["btp_gl_delete_codes"] and not r["GL_CODE"].startswith("8")]
    assert len(retained) == 5
    assert "76104" in contract["btp_gl_delete_codes"]
    assert len(contract["projections"]["btp"]) == 25
    assert set(contract["projections"]["ecckt"]) == set(contract["projections"]["l3"])

def test_independent_expected_values_duplicates_nulls(contract):
    btp = contract["tables"]["AE2E_BILL_TAMER_PREP"]
    vbus = contract["tables"]["AE2E_VBUS_PREP"]
    assert len(btp["expected"]) == 5 and len(vbus["expected"]) == 3
    assert len(set(runtime.canonical_rows(vbus["expected"], vbus["columns"]))) == 2
    assert Counter(r["CUSTOMER_NAME"] for r in vbus["expected"]) == {"ECCKT_MATCH": 2, "L3_ONLY_MATCH": 1}
    assert vbus["expected"][2]["MRC"] == "0.0100" and vbus["expected"][2]["NRC"] is None
    assert btp["expected"][-1]["ECCKT_STRIPPED"] is None
    # A same-count different value and duplicate multiplicity must fail comparison.
    wrong = [dict(row) for row in vbus["expected"]]
    wrong[0]["CUSTOMER_NAME"] = "UNMATCHED_SENTINEL"
    assert Counter(runtime.canonical_rows(wrong, vbus["columns"])) != Counter(runtime.canonical_rows(vbus["expected"], vbus["columns"]))

def test_canonical_schema_numeric_and_timestamp_values():
    cols = [("A", "DECIMAL(38,0)"), ("B", "DECIMAL(19,4)"), ("T", "TIMESTAMP"), ("N", "STRING")]
    row = {"A": "99999999999999999999999999999999999999", "B": "0.01", "T": "2026-11-07T00:00:00.000Z", "N": None}
    canonical = json.loads(runtime.canonical_rows([row], cols)[0])
    assert canonical == [row["A"], "0.0100", "2026-11-07 00:00:00.000000", None]

@pytest.mark.parametrize("name", ["orion_dev_ssuman.test.x", "orion_bronze_test_ssuman.shared.x", "a.b.c;DROP TABLE x", "x", "orion_gold_test_ssuman.vyvx_mock_demo.x`"])
def test_unsafe_identifier_rejected(name):
    with pytest.raises(ValueError):
        runtime.identifier(name)

def test_source_truncate_rejected_before_spark(contract):
    class NeverSpark:
        def sql(self, *a, **kw):
            raise AssertionError("Source safety checked before SQL")
    with pytest.raises(ValueError, match="Source or unknown"):
        runtime.mock_truncate_table(NeverSpark(), contract, "AE2E_BTP_STG3")

@pytest.mark.parametrize("ack,run_id", [("", "12"), ("MOCK_ONLY", "interactive_not_allowed"), ("PROD", "12")])
def test_execution_ack_and_native_run_guard(ack, run_id):
    with pytest.raises(ValueError):
        runtime.execute(None, None, "btp_load", ack, run_id, "absent")

def test_native_yaml_sdk_roundtrip_and_control_tasks(exported):
    raw = json.loads((exported / "job.json").read_bytes())
    converted = jobs.JobSettings.from_dict(raw).as_dict()
    assert converted == raw
    bundle = yaml.safe_load((exported / "databricks.yml").read_text())
    assert bundle["resources"]["jobs"]["vyvx_mock"] == raw
    assert "**/*.ipynb" in bundle["sync"]["exclude"]
    manifest = json.loads((exported / "manifest.json").read_bytes())
    assert not bundle["workspace"]["root_path"].startswith(manifest["workspace_path"])
    assert manifest["workspace_path"].endswith("/execution")
    assert raw["performance_target"] == "STANDARD" and raw["max_concurrent_runs"] == 1
    assert raw["queue"] == {"enabled": False}
    tasks = {t["task_key"]: t for t in raw["tasks"]}
    assert len(tasks) == 11
    for key in ("btp", "vbus"):
        assert tasks[key + "_needed"]["condition_task"]["right"] == "true"
        assert tasks[key + "_load"]["depends_on"] == [{"task_key": key + "_needed", "outcome": "true"}]
        assert tasks[key + "_merge"]["run_if"] == "ALL_DONE"
    for task in raw["tasks"]:
        assert task["max_retries"] == 0
        assert not any(key in task for key in ("new_cluster", "existing_cluster_id", "job_cluster_key", "libraries"))

def test_json_notebooks_stable_metadata_ast_and_source_twins(exported):
    for path in (exported / "notebooks").glob("*.ipynb"):
        doc = json.loads(path.read_bytes())
        assert len(doc["cells"]) == 2
        for cell in doc["cells"]:
            assert cell["metadata"]["language"] in {"markdown", "python"}
            assert cell["metadata"]["id"]
            if cell["cell_type"] == "code":
                ast.parse("".join(cell["source"]))
        ast.parse(path.with_suffix(".py").read_text())
    for path in (ROOT / "deployment/vyvx_mock_demo").glob("*.py"):
        ast.parse(path.read_text())
    source = (exported / "runtime.py").read_text()
    assert all(term not in source for term in (".rdd", ".cache(", ".persist(", "spark.conf.set", "sparkContext", "smtplib"))

def test_export_is_immutable_and_all_hashes_match(exported):
    manifest = json.loads((exported / "manifest.json").read_bytes())
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((exported / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        compiler.export(exported)
    # Workspace FILE assets support AUTO export, not RAW. RAW remains correct
    # for upload. One deterministic SDK path, no selector/format fallbacks.
    tree = ast.parse((ROOT / "deployment/vyvx_mock_demo/remote.py").read_text())
    downloads = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "download"]
    assert len(downloads) == 1
    format_arg = next(k.value for k in downloads[0].keywords if k.arg == "format")
    assert isinstance(format_arg, ast.Attribute) and format_arg.attr == "AUTO"

def test_java_ported_dependency_ready_missing_and_exception(exported):
    namespace = {}
    exec(compile((exported / "dependency_check.py").read_text(), "dependency_check.py", "exec"), namespace)
    poll = namespace["poll_dependency_count"]
    ready = poll(lambda: 0, lambda _: pytest.fail("Ready fixture must not sleep"))
    assert (ready.remaining, ready.queries, ready.sleeps) == (0, 1, 0)
    virtual_clock = []
    with pytest.raises(namespace["DependencyTimeout"]) as failure:
        poll(lambda: 1, virtual_clock.append)
    assert failure.value.result.queries == 54 and failure.value.result.sleeps == 54
    assert virtual_clock == [300] * 54
    with pytest.raises(namespace["DependencyQueryError"]):
        poll(lambda: 1 / 0, virtual_clock.append)

def test_existing_job_owner_marker_guard():
    good = NS(creator_user_name=runtime.OWNER, settings=NS(run_as=NS(user_name=runtime.OWNER),
              tags={"orion_mock": runtime.MARKER, "orion_owner": runtime.OWNER}))
    remote.Remote.job_guard(good)
    good.creator_user_name = "someone_else"
    with pytest.raises(RuntimeError):
        remote.Remote.job_guard(good)

def test_observed_server_defaults_without_relaxing_job_policy():
    from copy import deepcopy
    wanted = compiler.job_settings("/Workspace/Users/ssuman@bpcs.com/mock/execution")
    actual = deepcopy(wanted)
    actual.update(format="MULTI_TASK", email_notifications={}, webhook_notifications={})
    for task in actual["tasks"]:
        del task["max_retries"]
        task.update(disabled=False, email_notifications={})
        if "condition_task" in task:
            task.update(run_if="ALL_SUCCESS", timeout_seconds=0)
    assert remote.settings_match(actual, wanted)
    actual["tasks"][0]["max_retries"] = 1
    assert not remote.settings_match(actual, wanted)
    actual["tasks"][0]["max_retries"] = 0
    actual["tasks"][0]["disabled"] = True
    assert not remote.settings_match(actual, wanted)
    actual["tasks"][0]["disabled"] = False
    actual["email_notifications"] = {"on_success": ["real@example.com"]}
    assert not remote.settings_match(actual, wanted)

def test_active_job_guard():
    obj = remote.Remote.__new__(remote.Remote)
    obj.job_guard = lambda _: None
    obj.w = NS(jobs=NS(list=lambda **_: [NS(job_id=1)], get=lambda _: NS(job_id=1), list_runs=lambda **_: iter([NS(run_id=2)])))
    with pytest.raises(RuntimeError, match="Active demo"):
        obj.active_guard()

@pytest.mark.parametrize("status", ["FAIL", "not_verified", "PASS_COUNTS_ONLY"])
def test_cleanup_requires_successful_exact_proof(status):
    obj = remote.Remote.__new__(remote.Remote)
    with pytest.raises(RuntimeError, match="No successful independent proof"):
        obj.cleanup({"proof": {"status": status}, "run": {"state": {"result_state": "SUCCESS"}}})

def test_cleanup_destinations_only_and_source_checksums(contract, tmp_path):
    obj = remote.Remote.__new__(remote.Remote)
    obj.c = contract
    obj.reports = tmp_path
    calls = []
    data = {"source": "hash"}
    obj.active_guard = lambda: None
    obj.source_snapshot = lambda: data
    obj.table_guard = lambda key, **kw: calls.append(("guard", key, kw))
    obj.rows = lambda key: []
    obj.statement = lambda sql: calls.append(("sql", sql))
    path = obj.cleanup({"run_id": 7, "proof": {"status": "PASS_MOCK_EXPECTATIONS_ONLY", "sources_after": data},
                        "run": {"state": {"result_state": "SUCCESS"}}})
    report = json.loads(path.read_bytes())
    assert report["sources_before"] == report["sources_after"]
    statements = [call[1] for call in calls if call[0] == "sql"]
    assert len(statements) == 7 and all(sql.startswith("TRUNCATE TABLE") for sql in statements)
    assert not any("orion_bronze" in sql for sql in statements)
    assert all(value == 0 for value in report["destination_counts_after"].values())

def test_source_change_aborts_cleanup_before_any_sql(contract):
    obj = remote.Remote.__new__(remote.Remote)
    obj.c = contract
    obj.active_guard = lambda: None
    obj.source_snapshot = lambda: {"different": True}
    obj.statement = lambda _: pytest.fail("Cannot truncate after source drift")
    with pytest.raises(RuntimeError, match="Sources changed"):
        obj.cleanup({"proof": {"status": "PASS_MOCK_EXPECTATIONS_ONLY", "sources_after": {}},
                     "run": {"state": {"result_state": "SUCCESS"}}})