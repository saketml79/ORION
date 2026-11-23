"""Offline source evidence and independent control/set-semantics differentials.

SQLite checks the set query algebra only, not Oracle/Spark runtime compatibility.
The reference below independently interprets the inspected Java do/while, not
compiler constants. No real sleep, model, credentials or remote query is used.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import ast
import json
from pathlib import Path
import random
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import socket

import pytest
import sqlglot

from workbench.dependency_compiler import (
    JAVA_SHA256, MAPPING_ID, compile_dependency_check, definition_scope,
)
from workbench.demo_conversion import scoped_model
from workbench.package_parity import (
    PARENT_ID, build_parity_candidate, completion_gate, export_parity_candidate,
)
from workbench.understanding import analyze_package
from workbench.workflow_artifacts import compile_workflow_artifacts


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def model():
    return analyze_package(ROOT / "input" / "LumenExample.XML")


@pytest.fixture(scope="module")
def candidate(model):
    return build_parity_candidate(model)


@pytest.fixture(scope="module")
def runtime(model):
    source = compile_dependency_check(model)["artifacts"]["modules/dependency_check.py"]
    ast.parse(source)
    module = ModuleType("generated_dependency_candidate")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, "generated_dependency_candidate", "exec"), module.__dict__)
        yield module
    finally:
        del sys.modules[module.__name__]


def java_reference(values, *, query_error=None, sleep_error=None):
    """Independent literal Java trace including catch(out_VALUE=1) and ERROR."""
    events, timeoutcnt, index, total = [], 0, 0, 0
    try:
        while True:  # Java do block, at least one query.
            events.append(("query", index + 1))
            if query_error == index + 1:
                raise OSError("private driver error")
            total = values[min(index, len(values) - 1)]
            index += 1
            if total > 0:
                events.append(("sleep", 300))  # Thread.sleep(300000).
                if sleep_error == timeoutcnt + 1:
                    raise OSError("private sleep error")
                timeoutcnt += 1
                if timeoutcnt == 54:
                    break
            if not total > 0:
                break
        status = "timeout" if total > 0 else "satisfied"
    except Exception:
        total, status = 1, "exception"
    return total, status, events


def candidate_trace(runtime, values, *, query_error=None, sleep_error=None):
    events, polls, sleeps = [], 0, 0
    def query():
        nonlocal polls
        polls += 1
        events.append(("query", polls))
        if query_error == polls:
            raise OSError("password=DO_NOT_LEAK")
        return values[min(polls - 1, len(values) - 1)]
    def sleep(seconds):
        nonlocal sleeps
        sleeps += 1
        events.append(("sleep", seconds))
        if sleep_error == sleeps:
            raise OSError("password=DO_NOT_LEAK")
    try:
        result = runtime.poll_dependency_count(query, sleep)
    except runtime.DependencyFailure as error:
        assert str(error) == "Max Wait Time Exceeded"
        assert error.__context__ is None
        result = error.result
        assert isinstance(error, runtime.DependencyTimeout if result.status == "timeout" else runtime.DependencyQueryError)
    return result.remaining, result.status, events


@pytest.mark.parametrize("success_poll", range(1, 55))
def test_differential_every_success_poll(runtime, success_poll):
    values = [7] * (success_poll - 1) + [0]
    assert candidate_trace(runtime, values) == java_reference(values)


@pytest.mark.parametrize("remaining", [1, 7, 1000])
def test_differential_timeout_no_final_query(runtime, remaining):
    # A hypothetical 55th response is zero: neither implementation observes it.
    values = [remaining] * 54 + [0]
    actual = candidate_trace(runtime, values)
    assert actual == java_reference(values)
    assert actual[0] == remaining
    assert sum(e[0] == "query" for e in actual[2]) == 54
    assert sum(e[1] for e in actual[2] if e[0] == "sleep") == 16200


@pytest.mark.parametrize("failure_poll", range(1, 55))
@pytest.mark.parametrize("kind", ["query_error", "sleep_error"])
def test_differential_all_exception_positions(runtime, failure_poll, kind):
    kwargs = {kind: failure_poll}
    assert candidate_trace(runtime, [9], **kwargs) == java_reference([9], **kwargs)


def bindings(**overrides):
    return dict(dependency_table="cat.sch.deps", template_table="cat.sch.templates",
                job_log_table="cat.sch.logs", template_name="WIL", job_run_date="11042026", **overrides)


def oracle_minus_reference(templates, dependencies, logs, template_name="WIL", run_date="2026-11-04"):
    matches = [tid for tid, name in templates if name == template_name]
    if len(matches) > 1:
        raise ValueError("Oracle scalar subquery returns more than one row")
    tid = matches[0] if matches else None
    # SQL = does not match NULLs; MINUS DISTINCT considers NULL equal to NULL.
    required = {dep for owner, dep in dependencies if tid is not None and owner == tid}
    done = {owner for owner, day, end in logs if day == run_date and end is not None}
    return len(required - done)


def sqlite_candidate_algebra(runtime, templates, dependencies, logs):
    sql, args = runtime.dependency_statement(**bindings())
    sqlglot.parse_one(sql, read="databricks")
    # Explicit dialect shim only for SQLite's CAST-to-DATE behavior and lack of
    # optional DISTINCT keyword after EXCEPT. Keep the candidate query otherwise.
    for name in ("deps", "templates", "logs"):
        sql = sql.replace(f"`cat`.`sch`.`{name}`", name)
    sql = sql.replace("EXCEPT DISTINCT", "EXCEPT").replace("CAST(:job_run_date AS DATE)", "date(:job_run_date)")
    sql = sql.replace("current_date()", "CURRENT_DATE")
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("CREATE TABLE templates(template_id INTEGER,template_name TEXT);"
            "CREATE TABLE deps(template_id INTEGER,depends_on_template_id INTEGER);"
            "CREATE TABLE logs(template_id INTEGER,run_date TEXT,end_time TEXT);")
        connection.executemany("INSERT INTO templates VALUES (?,?)", templates)
        connection.executemany("INSERT INTO deps VALUES (?,?)", dependencies)
        connection.executemany("INSERT INTO logs VALUES (?,?,?)", logs)
        return connection.execute(sql, args).fetchone()[0]


def test_independent_minus_except_duplicates_null_dates_end_time(runtime):
    templates = [(1, "WIL")]
    deps = [(1, None), (1, None), (1, 2), (1, 2), (1, 3), (None, 4), (7, 8)]
    logs = [(None, "2026-11-04", "done"), (2, "2026-11-04", "done"),
            (2, "2026-11-04", "duplicate"), (3, "2026-11-04", None),
            (3, "2026-11-03", "done"), (3, "2026-11-04 12:00:00", "not midnight")]
    assert oracle_minus_reference(templates, deps, logs) == 1
    assert sqlite_candidate_algebra(runtime, templates, deps, logs) == 1
    assert sqlite_candidate_algebra(runtime, [], deps, logs) == 0
    rng = random.Random(18300)
    for _ in range(120):
        deps = [(rng.choice([None, 1, 9]), rng.choice([None, 2, 3, 4])) for _ in range(20)]
        logs = [(rng.choice([None, 2, 3, 4]), rng.choice(["2026-11-04", "2026-11-03", None]),
                 rng.choice([None, "done"])) for _ in range(20)]
        assert sqlite_candidate_algebra(runtime, templates, deps, logs) == oracle_minus_reference(templates, deps, logs)


def test_sql_bound_parameters_clock_and_scalar_subquery(runtime):
    values = bindings()
    values.update(template_name="WIL' OR 1=1 --", job_run_date="")
    sql, args = runtime.dependency_statement(**values)
    tree = sqlglot.parse_one(sql, read="databricks")
    assert args == {"template_name": "WIL' OR 1=1 --", "job_run_date": None}
    assert "OR 1=1" not in sql
    assert "current_date()" in sql and "WHERE run_date =" in sql
    assert "CAST(run_date" not in sql
    assert "EXCEPT DISTINCT" in sql and "end_time IS NOT NULL" in sql
    assert "WHERE template_id = (SELECT template_id" in sql  # Not a multiplying join.
    assert len(list(tree.find_all(sqlglot.exp.Except))) == 1
    with pytest.raises(ValueError):
        oracle_minus_reference([(1, "WIL"), (1, "WIL")], [], [])
    # sqlite differs for scalar cardinality errors, so it is NOT used to claim
    # Spark runtime coverage of that case. The real adapter must propagate errors.
    class Spark:
        def sql(self, sql, args):
            raise RuntimeError("SCALAR_SUBQUERY_TOO_MANY_ROWS")
    with pytest.raises(runtime.DependencyQueryError) as error:
        runtime.run_spark_dependency_check(Spark(), **bindings(), sleep=lambda _: pytest.fail("must not sleep"))
    assert error.value.result.remaining == 1


@pytest.mark.parametrize("value", ["11312026", "02302026", "null", "11/04/2026", "$$JOB_RUN_DATE", None])
def test_bad_dates_are_errors_not_fallback_current_date(runtime, value):
    values = bindings()
    values["job_run_date"] = value
    with pytest.raises(runtime.DependencyQueryError):
        runtime.run_spark_dependency_check(None, **values, sleep=lambda _: pytest.fail("must not sleep"))


def test_spark_adapter_binds_and_requeries_with_virtual_clock(runtime):
    class Spark:
        def __init__(self):
            self.calls = []
        def sql(self, statement, *, args):
            self.calls.append((statement, args))
            return SimpleNamespace(first=lambda: {"remaining": 5 if len(self.calls) < 3 else 0})
    spark, sleeps = Spark(), []
    result = runtime.run_spark_dependency_check(spark, **bindings(), sleep=sleeps.append)
    assert result == runtime.PollResult(0, 3, 2, "satisfied")
    assert sleeps == [300, 300]
    assert all(c[1] == {"template_name": "WIL", "job_run_date": date(2026, 11, 4).isoformat()} for c in spark.calls)
    values = bindings()
    values["dependency_table"] = "cat.sch.deps; DROP TABLE logs"
    with pytest.raises(runtime.DependencyQueryError):
        runtime.run_spark_dependency_check(spark, **values, sleep=sleeps.append)
    assert len(spark.calls) == 3


@pytest.mark.parametrize("drift", ["java", "expression", "edge", "old_saved_artifact"])
def test_dependency_compiler_fails_closed(model, drift):
    scope = deepcopy(definition_scope(model, MAPPING_ID))
    scope["package"] = model["package"]
    if drift in {"java", "old_saved_artifact"}:
        prop = next(p for n in scope["nodes"] for p in n["properties"]
                    if p["attributes"].get("NAME") == "OnInputRow_Method_Snippet")
        assert prop["embedded_code"]["source_sha256"] == JAVA_SHA256
        if drift == "java":
            prop["embedded_code"]["source_sha256"] = "0" * 64
        else:
            del prop["embedded_code"]
    elif drift == "expression":
        field = next(f for n in scope["nodes"] if n["kind"] == "transformation" and n["name"] == "exp_Define_Param"
                     for f in n["fields"] if f["attributes"]["NAME"] == "MAX_WAIT_COUNT")
        field["attributes"]["EXPRESSION"] = "30"
    else:
        scope["edges"].pop()
    with pytest.raises(ValueError, match="fingerprint"):
        compile_dependency_check(scope)


def test_exact_parent_child_scope_and_unflattened_coverage(model, candidate):
    report = candidate["report"]
    assert {w["name"] for w in report["migration_unit"]} == {
        "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS", "wkf_Load_AE2E_WILTEL_NETWORK_DISCO"}
    assert len(report["session_uses"]) == 10
    assert len(report["mapping_definitions"]) == 7
    child = next(w for w in report["migration_unit"] if w["workflow_id"] != PARENT_ID)
    # Fresh canonical parsing resolves nine definitions. The historical saved
    # artifact's four-source count is not silently reused as current evidence.
    assert (child["direct_sources"], child["direct_targets"], child["mappings"]) == (9, 8, 6)
    child_sources = {n["name"] for n in report["evidence_snapshot"]["nodes"]
                     if n["kind"] == "source" and n["id"] in {
                         sid for d in report["mapping_definitions"] for sid in d["source_ids"]}}
    assert {"AE2E_AM_OFFNET_MGR_PREP", "AE2E_ODW_PREP", "WILTEL_OFFNET_MGR_INVENTORY",
            "DUAL", "AE2E_UNITY_ODW_STG", "WILTEL_OPENCI_STG", "AE2E_JOB_LOG",
            "AE2E_UNIX_JOB_LOG", "AE2E_BTP_STG3"} <= child_sources
    log_uses = [u for u in report["session_uses"] if "LOG_CHECK" in u["instance_name"]]
    assert len(log_uses) == 4
    assert len({tuple(u["mapping_definition_ids"]) for u in log_uses}) == 1
    assert len({tuple(u["session_definition_ids"]) for u in log_uses}) == 4
    assert all(u["session_override_owner_ids"] and u["session_scope_node_ids"] for u in log_uses)
    assert {p["qualified_name"] for p in report["stored_procedures"]} == {
        "AE2E_COMMON.TRUNCATE_TABLE", "AE2E_COMMON.GET_PRODUCT_KEY_ID"}
    assert len([p for p in report["stored_procedures"] if p["phase"] == "Source Pre Load"]) == 4
    assert all(not p["replacement_generated"] for p in report["stored_procedures"])
    assert len(report["inward_callers_review_only"]) == 1
    master_call = report["inward_callers_review_only"][0]
    assert master_call["wait_mode"] == "nowait"
    assert master_call["caller_workflow_id"] not in {w["workflow_id"] for w in report["migration_unit"]}
    assert report["outward_invocations"][0]["wait_mode"] == "nowait"
    assert report["sql_phase_contracts"]
    assert len(report["parameter_file_contracts"]) >= 9
    assert any(p["role"] == "Post SQL" for p in report["sql_phase_contracts"])
    assert report["gate"]["dependency_check"] == "pinned_core_candidate_not_full_session"
    assert all(d["conversion_status"] == "not_converted" for d in report["mapping_definitions"])
    assert candidate == build_parity_candidate(model)


def test_wrapper_artifacts_cannot_satisfy_package_gate(model):
    # Existing tested wrapper flow remains generated, not disabled by the gate.
    result = compile_workflow_artifacts(model, PARENT_ID)
    gate = result["validation"]["package_completion_gate"]
    assert gate["session_uses"] == 10 and gate["unique_mappings"] == 7
    assert gate["full_package_conversion"] is False
    assert gate["converted_sessions"] == 0
    assert {b["code"] for b in gate["blockers"]} >= {
        "MAPPING_NOT_CONVERTED", "EXTERNAL_PROCEDURE_BODY_REQUIRED", "SCOPED_PARAMETER_VALUES_REQUIRED",
        "REAL_OUTPUT_DIFFERENTIAL_NOT_RUN", "CHILD_CONTROL_AND_SESSION_ERROR_CONTRACT_NOT_CONVERTED"}
    assert result["tasks"] and result["artifacts"]["databricks.yml"]
    assert result["ready_for_execution"] is False
    wrapper_only = completion_gate(scoped_model(model, PARENT_ID), PARENT_ID)
    assert wrapper_only["session_uses"] == 1
    assert any(b["code"] == "CHILD_IMPLEMENTATION_NOT_IN_MODEL" for b in wrapper_only["blockers"])
    assert wrapper_only["full_package_conversion"] is False


def test_gate_rejects_missing_java_even_when_wrapper_present(model, candidate):
    scope = deepcopy(candidate["report"]["evidence_snapshot"])
    scope["package"] = model["package"]
    for n in scope["nodes"]:
        for prop in n.get("properties", []):
            prop.pop("embedded_code", None)
    gate = completion_gate(scope, PARENT_ID)
    assert gate["dependency_check"] == "unsupported_fingerprint_or_missing_Java_sidecar"
    assert any(b["code"] == "DEPENDENCY_JAVA_NOT_SUPPORTED" for b in gate["blockers"])
    assert gate["full_package_conversion"] is False


def test_persistent_report_hashes_exclusive_export(candidate, tmp_path):
    destination = tmp_path / "candidate"
    manifest = export_parity_candidate(candidate, destination)
    assert json.loads((destination / "manifest.json").read_text()) == manifest
    for name, receipt in manifest["artifacts"].items():
        data = (destination / name).read_bytes()
        assert receipt == {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
    persisted = json.loads((destination / "parity_readiness.json").read_text())
    assert persisted["gate"]["status"] == "blocked"
    assert persisted["gate"]["output_parity"] == "not_established"
    with pytest.raises(FileExistsError):
        export_parity_candidate(candidate, destination)
    assert json.loads((destination / "manifest.json").read_text()) == manifest
    invalid = {**candidate, "artifacts": {"../outside": "no"}}
    with pytest.raises(ValueError):
        export_parity_candidate(invalid, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_agent_tool_exposes_authoritative_gate_without_model_or_network(model, tmp_path):
    from workbench.agents import build_agents, _result
    from workbench.tests.test_agents import fake
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
        with build_agents(tmp_path, model, PARENT_ID, "original", chat_model=fake()) as bundle:
            tools = bundle.supervisor.nodes["tools"].bound.tools_by_name
            response = tools["package_migration_contract"].invoke({"offset": 0, "limit": 1})
            gate = response["selected_unit_output_parity_gate"]
            assert gate["full_package_conversion"] is False
            assert gate["session_uses"] == 10
            assert gate["unique_mappings"] == 7
            assert response["cohort"]["total"] > len(response["cohort"]["items"])
            with patch("workbench.package_parity.completion_gate", side_effect=AssertionError("Must reuse cached gate")):
                page = tools["package_migration_contract"].invoke({"offset": 1, "limit": 1})
            assert page["selected_unit_output_parity_gate"] == gate
            result = _result(bundle, "completed", "Model prose cannot grant conversion authority")
            assert result["package_candidate_complete"] is False
            assert result["output_parity"] == "not_established"
            assert result["generation_allowed"] is False
            assert result["deployment_allowed"] is False