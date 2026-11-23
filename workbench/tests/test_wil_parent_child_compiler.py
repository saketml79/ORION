"""Real XML pins plus constructed operator/routing differentials, no parity claim."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import ast
import json
from pathlib import Path
import random
import socket
import sqlite3
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import expressions as exp
import yaml

from workbench.package_parity import build_parity_candidate
from workbench.understanding import analyze_package
from workbench.wil_control import eligibility, link_matches, preview_trace
from workbench.wil_mapping_compiler import MappingCompiler, Unsupported, sql_candidate
from workbench.wil_parent_child import (
    UNIT_SHA256, compile_parent_child, compile_report,
    discover_external_evidence, export_candidate,
)
from workbench.wil_runtime_contract import compare_reference_outputs, prepare_statement


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No remote execution is authorized")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture(scope="module")
def fresh_model():
    return analyze_package(ROOT / "input" / "LumenExample.XML")


@pytest.fixture(scope="module")
def candidate(fresh_model):
    return compile_report(build_parity_candidate(fresh_model)["report"])


def test_real_xml_scope_and_candidate_counts(candidate):
    r = candidate["report"]
    assert r["unit_sha256"] == UNIT_SHA256
    assert (r["child_tasks"], r["child_sessions"], r["session_uses"], r["unique_mappings"]) == (15, 9, 10, 7)
    assert r["gate"] == {"ready": False, "converted_sessions": 0, "full_package_conversion": False,
                         "output_parity": "not_established", "execution_allowed": False, "deployment_allowed": False}
    assert r["model_calls"] == r["canonical_mutations"] == 0
    uses = {s["name"]: s for s in candidate["sessions"]}
    for name in ("s_m_LOAD_AE2E_BILLTRACK_PRO_PREP_WND", "s_m_LOAD_AE2E_ODW_PREP_WND", "s_m_LOAD_AE2E_OPENCI_PREP_WND"):
        s = uses[name]
        assert all(o["status"] != "blocked" or o["type"] == "Stored Procedure" for o in s["operators"])
        assert any(t.get("insert_sql") for t in s["target_candidates"])
    onm = uses["s_m_LOAD_AE2E_ONM_PREP_WND"]
    assert any("NEXTVAL" in o["source_properties"].get("Sql Query", "").upper() and o["status"] == "blocked"
               for o in onm["operators"])
    assert any("GET_PRODUCT_KEY_ID" in b for o in onm["operators"] for b in o["blockers"])


def test_exact_source_port_projection_and_scoped_override(candidate):
    s = next(s for s in candidate["sessions"] if s["name"] == "s_m_LOAD_AE2E_OPENCI_PREP_WND")
    for o in s["operators"]:
        if o["name"] in {"SQ_WILTEL_OPENCI_STG", "SQ_WILTEL_OPENCI_STG1"}:
            assert len(o["ports"]) == 26 and len(o["query_port_order"]) == 24
            assert o["query_port_order"][-1] == "TRUNK_DIRECTION"
            assert "TRAIL_TYPE_NAME" not in o["query_port_order"]
    onm = next(s for s in candidate["sessions"] if s["name"] == "s_m_LOAD_AE2E_ONM_PREP_WND")
    log = next(o for o in onm["operators"] if o["name"] == "SQ_AE2E_JOB_LOG")
    assert log["property_owners"]["Sql Query"] == "5ee0490c83026ec6307d8e08975c3dfd07e60db46db59fe52ceb6862cde7102a"
    assert log["source_properties"]["Sql Query"].rstrip().endswith(")")
    bill = next(s for s in candidate["sessions"] if "LOAD_AE2E_BILLTRACK" in s["name"])
    exp = next(o for o in bill["operators"] if o["type"] == "Expression")
    assert "onm_LEVEL3_CIRCUIT_ID" not in exp["sql"]
    assert any(p["NAME"] == "onm_LEVEL3_CIRCUIT_ID" for p in exp["ports"])


def test_real_sql_and_notebooks_and_serverless_shape(candidate):
    for s in candidate["sessions"]:
        for target in s["target_candidates"]:
            sqlglot.parse_one(target.get("insert_sql", target["select_sql"]), read="databricks")
            assert target["ready"] is False
            if target.get("insert_sql"):
                assert target["source_row_mode"] == "Insert"
                assert target["connected_columns"]  # Explicit target order.
        for o in s["operators"]:
            if o.get("port_candidates"):
                assert all(p["ready"] is False for p in o["port_candidates"])
    for name, text in candidate["artifacts"].items():
        if name.endswith(".py"):
            ast.parse(text)
        if name.endswith(".ipynb"):
            notebook = json.loads(text)
            assert all(c["metadata"]["language"] and c["metadata"]["id"] for c in notebook["cells"])
            assert len({c["metadata"]["id"] for c in notebook["cells"]}) == len(notebook["cells"])
    job = json.loads(candidate["artifacts"]["jobs/child.review.json"])
    assert job["performance_target"] == "STANDARD" and len(job["tasks"]) == 15
    assert not any("cluster" in k for t in job["tasks"] for k in t)
    assert all(t["run_if"] == "ALL_DONE" and t["max_retries"] == 0 for t in job["tasks"])
    assert yaml.safe_load(candidate["artifacts"]["jobs/child.review.yaml"])["resources"]["jobs"]["wil_child_review"] == job


@pytest.mark.parametrize("bits", range(16))
def test_every_child_load_skip_combination(candidate, bits):
    control = candidate["child_control"]
    checks = ["s_m_AE2E_ONM_PREP_WND_LOG_CHECK", "s_m_AE2E_OPENCI_PREP_WND_LOG_CHECK",
              "s_m_AE2E_ODW_PREP_LOG_CHECK_WND", "s_m_AE2E_BILL_TRAK_PREP_WND_LOG_CHECK"]
    loads = ["s_m_LOAD_AE2E_ONM_PREP_WND", "s_m_LOAD_AE2E_OPENCI_PREP_WND",
             "s_m_LOAD_AE2E_ODW_PREP_WND", "s_m_LOAD_AE2E_BILLTRACK_PRO_PREP_WND"]
    provided: dict[str, dict[str, str | int]] = {"s_m_Check_Dependency_Status_WIL_ND": {"status": "SUCCEEDED"}}
    for i, (check, load) in enumerate(zip(checks, loads)):
        provided[check] = {"status": "SUCCEEDED", "target_success_rows": (bits >> i) & 1}
        provided[load] = {"status": "SUCCEEDED", "target_success_rows": 3}
    trace = preview_trace(control["tasks"], control["links"], provided)
    for i, load in enumerate(loads):
        assert trace[load]["status"] == ("SKIPPED" if (bits >> i) & 1 else "SUCCEEDED")
    assert trace["dec_WND_BILL_TRAK_PREP"]["status"] == "SUCCEEDED"
    assert trace["eml_on_SUCCESS"]["status"] == "NOT_EXECUTED"  # Email not fabricated.


@pytest.mark.parametrize("bad", [{"status": "FAILED", "target_success_rows": 0}, {"status": "SKIPPED"},
                                 {"status": "SUCCEEDED"}, {"status": "SUCCEEDED", "target_success_rows": -1}])
def test_failed_or_unknown_count_never_routes_as_empty(bad):
    assert not link_matches("$session.TgtSuccessRows=0", {"session": bad})


def test_and_or_are_not_interchangeable():
    links = [{"FROMTASK": "a", "TOTASK": "merge", "CONDITION": "$a.Status=SUCCEEDED"},
             {"FROMTASK": "b", "TOTASK": "merge", "CONDITION": "$b.Status=SUCCEEDED"}]
    task = {"name": "merge", "attributes": {"ISENABLED": "YES", "TREAT_INPUTLINK_AS_AND": "NO"}}
    outcomes = {"a": {"status": "SUCCEEDED"}, "b": {"status": "SKIPPED"}}
    assert eligibility(task, links, outcomes)
    task["attributes"]["TREAT_INPUTLINK_AS_AND"] = "YES"
    assert not eligibility(task, links, outcomes)
    with pytest.raises(ValueError):
        link_matches("$a.TgtSuccessRows=0 OR TRUE", {})


@pytest.mark.parametrize("count", [0, 2])
def test_generated_notebook_routes_then_blocks_business(candidate, count):
    name = "s_m_LOAD_AE2E_ONM_PREP_WND"
    published = {}
    def get_value(*, taskKey, key, default):
        if taskKey != "s_m_AE2E_ONM_PREP_WND_LOG_CHECK":
            return default
        return "SUCCEEDED" if key == "source_status" else count
    values = SimpleNamespace(get=get_value, set=lambda *, key, value: published.update({key: value}))
    scope = {"dbutils": SimpleNamespace(jobs=SimpleNamespace(taskValues=values))}
    source = candidate["artifacts"]["notebooks/" + name + ".py"]
    if count == 0:
        with pytest.raises(RuntimeError, match="business candidate blocked"):
            exec(compile(source, name, "exec"), scope)
        assert published["source_status"] == "FAILED"
    else:
        exec(compile(source, name, "exec"), scope)
        assert published["source_status"] == "SKIPPED"
    assert "target_success_rows" not in published


def test_actual_logcheck_query_multiset_against_independent_reference(candidate):
    plan = next(s for s in candidate["sessions"] if s["name"] == "s_m_AE2E_ONM_PREP_WND_LOG_CHECK")
    sql = next(o["sql"] for o in plan["operators"] if o["type"] == "Source Qualifier")
    tree = sqlglot.parse_one(sql, read="databricks")
    # Explicit date dialect shim, not production query changes or proof of
    # SQLite's differing multirow scalar-subquery behavior.
    for cast in list(tree.find_all(exp.Cast)):
        if cast.to.this == exp.DataType.Type.DATE:
            cast.replace(exp.Anonymous(this="date", expressions=[cast.this]))
    rendered = tree.sql(dialect="sqlite")
    tokens = {identity.upper(): token for token, identity in plan["table_tokens"].items()}
    job = next(token for identity, token in tokens.items() if identity.endswith(".AE2E_JOB_LOG"))
    unix = next(token for identity, token in tokens.items() if identity.endswith(".AE2E_UNIX_JOB_LOG"))
    template = next(token for identity, token in tokens.items() if identity.endswith(".AE2E_TEMPLATE"))
    rng = random.Random(18257)
    params = {"p_JOB_RUN_DATE": "2026-11-05", "p_TEMPLATE_NAME": "WIL", "p_JOB_NAME": "ONM"}
    for _ in range(60):
        logs = [(rng.choice([None, 100, 101]), rng.choice(["ONM", "ODW"]), "start",
                 rng.choice([None, "done"])) for _ in range(20)]
        expected = Counter(row for row in logs if row[0] == 100 and row[1] == "ONM" and row[3] is not None)
        with sqlite3.connect(":memory:") as conn:
            conn.execute(f'CREATE TABLE "{job}" (RUN_ID INTEGER, JOB_NAME TEXT, START_TIME TEXT, END_TIME TEXT)')
            conn.execute(f'CREATE TABLE "{unix}" (RUN_ID INTEGER, TEMPLATE_ID INTEGER, RUN_DATE TEXT)')
            conn.execute(f'CREATE TABLE "{template}" (TEMPLATE_ID INTEGER, TEMPLATE_NAME TEXT)')
            conn.execute(f'INSERT INTO "{template}" VALUES (1,\'WIL\')')
            conn.executemany(f'INSERT INTO "{unix}" VALUES (?,?,?)',
                             [(100, 1, "2026-11-05"), (101, 1, "2026-11-04"), (101, 2, "2026-11-05")])
            conn.executemany(f'INSERT INTO "{job}" VALUES (?,?,?,?)', logs)
            assert Counter(conn.execute(rendered, params).fetchall()) == expected


def synthetic_mapping():
    nodes, edges = [], []
    def port(name, **attrs):
        return {"attributes": {"NAME": name, "DATATYPE": "decimal", "PORTTYPE": "INPUT/OUTPUT", **attrs}}
    def instance(name, typ, fs, props=None):
        iid, did = name, name + "_definition"
        nodes.extend([{"id": iid, "kind": "instance", "name": name, "parent_id": "mapping",
                       "attributes": {"TRANSFORMATION_TYPE": typ}},
                      {"id": did, "kind": "target" if typ == "Target Definition" else "transformation", "name": name,
                       "attributes": {"NAME": name, "TYPE": typ}, "fields": fs,
                       "properties": [{"attributes": {"NAME": k, "VALUE": v}} for k, v in (props or {}).items()]}])
        edges.append({"source": iid, "target": did, "kind": "instance_of"})
    def flow(src, sf, dst, df):
        edges.append({"source": src, "target": dst, "kind": "data_flow",
                      "attributes": {"FROMFIELD": sf, "TOFIELD": df}})
    nodes.append({"id": "session", "kind": "session", "properties": []})
    instance("source", "Source Qualifier", [port("SOURCE_KEY"), port("PAYLOAD")], {"Sql Query": "SELECT key, payload FROM raw"})
    instance("lookup", "Lookup Procedure", [port("KEY", PORTTYPE="LOOKUP/OUTPUT"), port("IN_KEY")],
             {"Lookup condition": "KEY = IN_KEY", "Lookup table name": "lookup_raw", "Lookup cache persistent": "NO",
              "Dynamic Lookup Cache": "NO", "Lookup policy on multiple match": "Use Any Value"})
    instance("expression", "Expression", [port("PAYLOAD"), port("LOOKUP_KEY"),
             port("MISSING", PORTTYPE="LOCAL VARIABLE", EXPRESSION="IIF(ISNULL(LOOKUP_KEY),1,0)"),
             port("RESULT", PORTTYPE="OUTPUT", EXPRESSION="MISSING")])
    instance("target", "Target Definition", [port("PAYLOAD"), port("RESULT")])
    flow("source", "SOURCE_KEY", "lookup", "IN_KEY")
    flow("source", "PAYLOAD", "expression", "PAYLOAD")
    flow("lookup", "KEY", "expression", "LOOKUP_KEY")
    flow("expression", "PAYLOAD", "target", "PAYLOAD")
    flow("expression", "RESULT", "target", "RESULT")
    return {"nodes": nodes, "edges": edges}


def test_constructed_lookup_lineage_duplicates_nulls_reference():
    plan = MappingCompiler(synthetic_mapping(), "mapping", "session").compile()
    assert all(o["status"] == "compiled_sql_candidate" for o in plan["operators"])
    sql = sqlglot.transpile(plan["target_candidates"][0]["select_sql"], read="databricks", write="sqlite")[0]
    tokens = {v: k for k, v in plan["table_tokens"].items()}
    rng = random.Random(18300)
    for _ in range(60):
        inputs = [(rng.choice([None, 1, 2, 3]), rng.randrange(5)) for _ in range(12)]
        lookup = [(rng.choice([None, 1, 2, 3]),) for _ in range(10)]
        expected = Counter((payload, 0 if key is not None and (key,) in lookup else 1) for key, payload in inputs)
        with sqlite3.connect(":memory:") as conn:
            conn.execute(f'CREATE TABLE "{tokens["raw"]}" (key INTEGER, payload INTEGER)')
            conn.execute(f'CREATE TABLE "{tokens["lookup_raw"]}" (key INTEGER)')
            conn.executemany(f'INSERT INTO "{tokens["raw"]}" VALUES (?,?)', inputs)
            conn.executemany(f'INSERT INTO "{tokens["lookup_raw"]}" VALUES (?)', lookup)
            assert Counter(conn.execute(sql).fetchall()) == expected


def test_scoped_override_ambiguity_and_arity_fail_closed():
    snapshot = synthetic_mapping()
    definition = next(n for n in snapshot["nodes"] if n["id"] == "source_definition")
    definition["properties"][0]["attributes"]["VALUE"] = "SELECT key FROM raw"
    plan = MappingCompiler(snapshot, "mapping", "session").compile()
    source = next(o for o in plan["operators"] if o["name"] == "source")
    assert source["status"] == "blocked" and "arity mismatch" in source["blockers"][0]
    with pytest.raises(Unsupported):
        sql_candidate("fancy_unknown_function(key)")
    with pytest.raises(Unsupported):
        sql_candidate("COMMIT")


def test_real_odw_router_independent_true_branches_and_default(candidate):
    plan = next(s for s in candidate["sessions"] if s["name"] == "s_m_LOAD_AE2E_ODW_PREP_WND")
    router = next(o for o in plan["operators"] if o["type"] == "Router")
    assert [g["attributes"].get("EXPRESSION") for g in router["router_groups"]
            if "DEFAULT" not in g["attributes"]["TYPE"]] == ["TRUE", "TRUE", "TRUE"]
    tree = sqlglot.parse_one(plan["target_candidates"][0]["select_sql"], read="databricks")
    group_ctes = [cte for cte in tree.find_all(exp.CTE)
                  if any(e.alias in {"ACNA1", "ACNA2", "ACNA3", "ACNA4"} for e in cte.this.expressions)]
    assert len(group_ctes) == 4
    for cte in group_ctes:
        select = cte.this
        columns = sorted({c.name for c in select.find_all(exp.Column)})
        source = next(select.find_all(exp.Table)).name
        with sqlite3.connect(":memory:") as conn:
            conn.execute('CREATE TABLE "' + source + '" (' + ','.join('"' + c + '" INTEGER' for c in columns) + ')')
            placeholders = ','.join('?' for _ in columns)
            rows = [tuple(None for _ in columns), tuple(7 for _ in columns), tuple(7 for _ in columns)]
            conn.executemany('INSERT INTO "' + source + '" VALUES (' + placeholders + ')', rows)
            output = conn.execute(select.sql(dialect="sqlite")).fetchall()
            default = any(e.alias == "ACNA2" for e in select.expressions)
            assert len(output) == (0 if default else 3)
            if not default:
                assert Counter(output) == Counter(rows)  # Duplicates and NULL rows retained.


def test_parameterized_log_queries_and_no_fixture_approval(candidate):
    s = next(s for s in candidate["sessions"] if s["name"] == "s_m_AE2E_ONM_PREP_WND_LOG_CHECK")
    target = s["target_candidates"][0]
    assert target["target"]["DATABASETYPE"] == "Flat File"
    assert len(target["unconnected_target_ports"]) == 2
    assert target["target_format_children"][0]["attributes"]["CODEPAGE"] == "MS1252"
    assert target["target_format_children"][0]["attributes"]["NULL_CHARACTER"] == "*"
    query = next(o["sql"] for o in s["operators"] if o["type"] == "Source Qualifier")
    bindings = {token: {"approved": True, "owner": "reviewer", "review_sha256": "f" * 64,
                       "source_identity": identity, "table": "cat.sch.table" + str(i)}
                for i, (token, identity) in enumerate(s["table_tokens"].items())}
    params = {"p_JOB_RUN_DATE": "11052026", "p_TEMPLATE_NAME": "WIL' OR 1=1 --", "p_JOB_NAME": "job"}
    rendered, args = prepare_statement(query, s["table_tokens"], params, approved_bindings=bindings)
    assert "OR 1=1" not in rendered and args["p_JOB_RUN_DATE"] == "2026-11-05"
    assert args["p_TEMPLATE_NAME"] == params["p_TEMPLATE_NAME"]
    params["p_JOB_RUN_DATE"] = ""
    assert prepare_statement(query, s["table_tokens"], params, approved_bindings=bindings)[1]["p_JOB_RUN_DATE"] is None
    with pytest.raises(ValueError):
        prepare_statement(query, s["table_tokens"], params, approved_bindings={})
    bindings[next(iter(bindings))]["approved"] = False
    with pytest.raises(ValueError):
        prepare_statement(query, s["table_tokens"], params, approved_bindings=bindings)


def test_typed_multiset_not_counts_and_no_parity_certificate():
    rows = [{"a": None, "b": Decimal("1.0")}, {"a": None, "b": Decimal("1.0")}, {"a": "", "b": 1}]
    assert compare_reference_outputs(rows, list(reversed(rows)))["rows_equal"]
    wrong = [rows[0], rows[2], rows[2]]
    result = compare_reference_outputs(rows, wrong)
    assert not result["rows_equal"] and result["reference_count"] == result["candidate_count"]
    assert result["missing_multiset_rows"] == result["extra_multiset_rows"] == 1
    assert result["output_parity"] == "not_established"
    assert not compare_reference_outputs([{"x": 1}], [{"x": True}])["rows_equal"]


def test_multiline_repository_search_never_approves_or_displays_values(tmp_path):
    (tmp_path / "body.sql").write_text("CREATE OR REPLACE\nPACKAGE BODY\nAE2E_COMMON AS\nEND;", encoding="utf-8")
    (tmp_path / "p_ae2e.txt").write_text("$$PASSWORD=DO_NOT_PRINT_THIS", encoding="utf-8")
    result = discover_external_evidence(tmp_path)
    assert result["local_search_complete"] and len(result["procedure_ddl_candidates"]) == 1
    assert len(result["parameter_files"]) == 1
    assert "DO_NOT_PRINT_THIS" not in json.dumps(result)
    assert result["remote_volume_metadata"]["status"] == "not_checked_by_local_search"


def test_export_exclusive_hashes_and_source_immutability(candidate, tmp_path):
    before = (ROOT / "output/workbench/wil-net-dis-parent-child-parity-v1/manifest.json").read_bytes()
    destination = tmp_path / "candidate"
    manifest = export_candidate(candidate, destination)
    for name, info in manifest["artifacts"].items():
        data = (destination / name).read_bytes()
        assert sha256(data).hexdigest() == info["sha256"] and len(data) == info["bytes"]
    with pytest.raises(FileExistsError):
        export_candidate(candidate, destination)
    assert (ROOT / "output/workbench/wil-net-dis-parent-child-parity-v1/manifest.json").read_bytes() == before


def test_changed_canonical_scope_rejected():
    report = json.loads((ROOT / "output/workbench/wil-net-dis-parent-child-parity-v1/parity_readiness.json").read_bytes())
    report["evidence_snapshot"]["edges"][0]["attributes"]["FROMFIELD"] = "tampered"
    with pytest.raises(ValueError, match="Canonical WIL evidence changed"):
        compile_report(report)


def test_additive_api_keeps_parent_subtree_and_original_keys(fresh_model):
    from workbench.workflow_artifacts import compile_workflow_artifacts
    before = compile_workflow_artifacts(fresh_model, "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38")
    c = compile_parent_child(fresh_model)
    for key, value in before["artifacts"].items():
        assert c["artifacts"]["parent_wrapper/" + key] == value
    assert set(before) == {"artifacts", "tasks", "parameters", "parameter_inventory", "deployment_requirements",
                           "validation", "ready_for_execution"}