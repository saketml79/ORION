"""Offline compiler regressions using the actual understanding parser contract."""
from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch
from xml.sax.saxutils import escape

import pytest
import sqlglot

from workbench.sql_artifacts import (
    SQLRole, compile_sql_artifacts, export_sql_artifacts,
)
from workbench.understanding import analyze_package


def prop(name, sql):
    return '<TABLEATTRIBUTE NAME="' + name + '" VALUE="' + escape(sql, {'"': '&quot;'}) + '"/>'


def package(tmp_path, *, sql="SELECT src.x, NVL(x, 0) FROM src", extra="", vendor="Oracle"):
    xml = f'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <SOURCE NAME="Src" DATABASETYPE="{vendor}"><SOURCEFIELD NAME="x"/></SOURCE>
    <TARGET NAME="Dst" DATABASETYPE="Oracle"><TARGETFIELD NAME="x"/></TARGET>
    <MAPPING NAME="M"><TRANSFORMATION NAME="SQ" TYPE="Source Qualifier">
    {prop('Pre SQL', 'DELETE FROM stage WHERE x=0')}
    {prop('Sql Query', sql)}
    {prop('Lookup Sql Override', 'SELECT id FROM reference_table')}
    {prop('Post SQL', 'INSERT INTO audit_table VALUES (1)')}
    <TRANSFORMFIELD NAME="x" EXPRESSION="x+1"/></TRANSFORMATION>
    <INSTANCE NAME="src" TRANSFORMATION_NAME="Src" TRANSFORMATION_TYPE="Source Definition"/>
    <INSTANCE NAME="sq" TRANSFORMATION_NAME="SQ" TRANSFORMATION_TYPE="Source Qualifier"/>
    <INSTANCE NAME="dst" TRANSFORMATION_NAME="Dst" TRANSFORMATION_TYPE="Target Definition"/>
    <CONNECTOR FROMINSTANCE="src" FROMFIELD="x" TOINSTANCE="sq" TOFIELD="x"/>
    <CONNECTOR FROMINSTANCE="sq" FROMFIELD="x" TOINSTANCE="dst" TOFIELD="x"/>
    </MAPPING>
    <SESSION NAME="S" MAPPINGNAME="M">{prop('Pre SQL', 'DELETE FROM stage WHERE x=2')}
    <SESSTRANSFORMATIONINST SINSTANCENAME="sq" TRANSFORMATIONTYPE="Source Qualifier">
    {prop('Sql Query', 'SELECT x FROM session_source')}</SESSTRANSFORMATIONINST>
    <SESSIONEXTENSION NAME="Relational Writer" TYPE="WRITER" SUBTYPE="Relational Writer"
      TRANSFORMATIONTYPE="Target Definition" SINSTANCENAME="dst">
      {prop('SQL Override', 'INSERT INTO dst SELECT x FROM stage')}
      {prop('Post SQL', 'UPDATE dst SET x=3')}
    </SESSIONEXTENSION>{prop('Post SQL', 'INSERT INTO audit_table VALUES (9)')}</SESSION>
    <WORKFLOW NAME="W"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="S" ISENABLED="NO"/>
    {extra}</WORKFLOW>
    <WORKFLOW NAME="Other"><TASKINSTANCE NAME="Again" TASKTYPE="Session" TASKNAME="S"/></WORKFLOW>
    </FOLDER></REPOSITORY></POWERMART>'''
    source = tmp_path / "arbitrary.xml"
    source.write_text(xml, encoding="utf-8")
    model = analyze_package(source)
    workflow = next(n["id"] for n in model["nodes"] if n["kind"] == "workflow" and n["name"] == "W")
    return model, workflow


def compile_fixture(tmp_path, **kwargs):
    model, workflow = package(tmp_path, **kwargs)
    return compile_sql_artifacts(model, workflow, source_dialect="oracle")


def load_program(tmp_path, result, item):
    """Only tests load generated definitions, against a fake Spark, never real SQL."""
    name = "draft_" + item.id
    file = tmp_path / (name + ".py")
    file.write_text(result.artifacts[item.emitted_artifacts[0]], encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class Spark:
    def __init__(self):
        self.calls = []
        self.frame = object()

    def sql(self, sql):
        self.calls.append(sql)
        sqlglot.parse_one(sql, read="spark")
        return self.frame


def bindings(item):
    return {key: "draft_catalog.isolated_test.t" + str(i) for i, key in enumerate(item.bindings)}


def test_all_roles_order_evidence_override_and_complete_graph(tmp_path):
    model, workflow = package(tmp_path)
    before = deepcopy(model)
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert model == before
    assert len(result.sql) == 9
    sq = next(n for n in model["nodes"] if n["kind"] == "transformation")
    items = [i for i in result.sql if i.node_id == sq["id"]]
    assert [i.role for i in items] == [SQLRole.PRE, SQLRole.SOURCE, SQLRole.LOOKUP, SQLRole.POST]
    assert [i.ordinal for i in items] == [0, 1, 2, 3]
    for item in result.sql:
        assert item.original_sha256 == sha256(item.original_sql.encode()).hexdigest()
        assert item.evidence["locator"]
        assert item.planned_artifacts == item.emitted_artifacts
    assert {i.context["disposition"] for i in result.sql if i.role == SQLRole.SOURCE} == {
        "mapping_or_definition", "session_override"}
    target = next(i for i in result.sql if i.role == SQLRole.TARGET)
    assert target.context["references"][0]["kind"] == "session_instance"
    task = next(t for t in result.tasks if t.kind == "taskinstance")
    assert task.enabled == "NO" and len(task.sql_ids) == 9
    assert [p["phase"] for p in task.phase_plan] == [
        "pre_sql", "source_query", "lookup_query", "transformation_sql", "target_override", "post_sql", "unknown_sql"]
    graph = json.loads(result.artifacts[result.manifest["graph_artifact"]])
    assert any(e["kind"] == "field_flow" for e in graph["edges"])
    assert any(n["fields"] for n in graph["nodes"])
    assert not any(n["name"] == "Other" for n in graph["nodes"])
    assert not result.manifest["release_ready"] and not result.manifest["execution_permitted"]
    assert "UNSUPPORTED_MAPPING_IMPLEMENTATION" in task.blocker_codes


def test_notebook_json_and_python_are_definitions_only(tmp_path):
    result = compile_fixture(tmp_path)
    for item in result.sql:
        notebook = json.loads(result.artifacts[item.emitted_artifacts[1]])
        assert notebook["metadata"]["workflow_id"] == result.workflow_id
        for cell in notebook["cells"]:
            assert cell["metadata"]["id"] == cell["id"]
            assert cell["metadata"]["language"] in {"python", "markdown"}
            assert cell["metadata"]["sql_role_id"] == item.id
        tree = ast.parse(result.artifacts[item.emitted_artifacts[0]])
        assert not any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) for n in tree.body)
        assert notebook["cells"][1]["outputs"] == []


@pytest.mark.parametrize("role,function", [(SQLRole.SOURCE, "source_query"), (SQLRole.LOOKUP, "lookup_query")])
def test_queries_are_callable_dataframes_with_explicit_identifiers(tmp_path, role, function):
    result = compile_fixture(tmp_path)
    item = next(i for i in result.sql if i.role == role)
    module = load_program(tmp_path, result, item)
    spark = Spark()
    assert not spark.calls
    assert getattr(module, function)(spark, bindings=bindings(item)) is spark.frame
    assert len(spark.calls) == 1 and not spark.calls[0].startswith("INSERT")
    assert "`draft_catalog`.`isolated_test`." in spark.calls[0]
    if role == SQLRole.SOURCE:
        assert "src.x" in spark.calls[0] and " AS src" in spark.calls[0]
    for bad in ({}, {k: "prod.t; DROP TABLE other" for k in item.bindings},
                {k: "only_two.parts" for k in item.bindings}):
        with pytest.raises(ValueError):
            getattr(module, function)(spark, bindings=bad)
    assert len(spark.calls) == 1


def test_commands_require_context_and_validate_whole_batch(tmp_path):
    model, workflow = package(tmp_path)
    node = next(n for n in model["nodes"] if n["kind"] == "session")
    node["properties"][0]["attributes"]["VALUE"] = "DELETE FROM a; INSERT INTO b VALUES (1)"
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    item = next(i for i in result.sql if i.node_id == node["id"] and i.role == SQLRole.PRE)
    assert len(item.statements) == 2
    module = load_program(tmp_path, result, item)
    spark = Spark()
    for context in (None, module.ExecutionContext(spark, False, frozenset()),
                    module.ExecutionContext(spark, True, frozenset())):
        with pytest.raises(ValueError):
            module.execute_commands(context, bindings=bindings(item))
    assert spark.calls == []
    context = module.ExecutionContext(spark, True, frozenset({("draft_catalog", "isolated_test")}))
    assert module.execute_commands(context, bindings=bindings(item)) == [spark.frame, spark.frame]
    assert spark.calls[0].startswith("DELETE") and spark.calls[1].startswith("INSERT")


@pytest.mark.parametrize("sql", ["SELECT (", "DELETE FROM src", "SELECT * FROM src; SELECT 2",
                                  "SELECT evil_udf(x) FROM src", "SELECT $$unbound FROM src",
                                  "SELECT * FROM read_csv('secret.csv')", "SELECT * INTO dst FROM src"])
def test_bad_sql_retained_without_runnable_fallback(tmp_path, sql):
    result = compile_fixture(tmp_path, sql=sql)
    item = next(i for i in result.sql if i.original_sql == sql)
    assert item.status == "blocked" and item.original_sql == sql
    assert item.planned_artifacts and item.emitted_artifacts == []
    assert item.statements == []
    assert not any(path in result.artifacts for path in item.planned_artifacts)
    assert all(t.status == "blocked" for t in result.tasks if t.node_id in item.task_ids)
    assert any(i.status == "draft_syntax_only" for i in result.sql if i.id != item.id)
    assert sql in [i["original_sql"] for i in json.loads(result.artifacts["manifest.json"])["sql_inventory"]]


def test_missing_refs_are_scoped_and_other_sql_is_generated(tmp_path):
    model, workflow = package(tmp_path, extra='<TASKINSTANCE NAME="Missing" TASKNAME="absent" TASKTYPE="Session"/>')
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    missing = next(t for t in result.tasks if t.name == "Missing")
    good = next(t for t in result.tasks if t.name == "Run")
    assert "UNRESOLVED_REFERENCE" in missing.blocker_codes
    assert "UNRESOLVED_REFERENCE" not in good.blocker_codes
    assert good.sql_ids and all(i.emitted_artifacts for i in result.sql)
    edge = next(e for e in model["edges"] if e["source"] == missing.node_id and e["kind"] == "task_definition")
    model["nodes"] = [n for n in model["nodes"] if n["id"] != edge["target"]]
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert any(b["code"] == "MISSING_CANONICAL_REFERENCE" and missing.node_id in b["task_ids"]
               for b in result.manifest["blockers"])


def test_mapping_without_sql_keeps_graph_without_fake_notebooks(tmp_path):
    model, workflow = package(tmp_path)
    for node in model["nodes"]:
        node["properties"] = []
    result = compile_sql_artifacts(model, workflow)
    assert result.sql == []
    assert not any(p.endswith((".py", ".ipynb")) for p in result.artifacts)
    assert any(b["code"] == "UNSUPPORTED_MAPPING_IMPLEMENTATION" for b in result.manifest["blockers"])
    graph = json.loads(result.artifacts[result.manifest["graph_artifact"]])
    assert any(n["kind"] == "transformation" and n["fields"] for n in graph["nodes"])


def test_inference_unknown_vendor_and_explicit_override(tmp_path):
    model, workflow = package(tmp_path)
    result = compile_sql_artifacts(model, workflow)
    assert all(i.source_dialect == "oracle" for i in result.sql)
    model, workflow = package(tmp_path, vendor="UnverifiedVendor")
    result = compile_sql_artifacts(model, workflow)
    source = next(i for i in result.sql if i.role == SQLRole.SOURCE)
    assert source.status == "blocked" and source.source_dialect is None
    result = compile_sql_artifacts(model, workflow, source_dialects={source.node_id: "oracle"})
    assert next(i for i in result.sql if i.id == source.id).status == "draft_syntax_only"


def test_oracle_dates_are_syntax_only(tmp_path):
    result = compile_fixture(tmp_path, sql="SELECT TO_DATE('2024-01-02', 'YYYY-MM-DD'), NVL(x, 0) FROM src")
    item = next(i for i in result.sql if i.role == SQLRole.SOURCE)
    assert "COALESCE" in item.statements[0]["spark_sql_template"]
    assert "TO_DATE" in item.statements[0]["spark_sql_template"]
    assert result.manifest["translation_claim"] == "syntax_only_not_semantic_or_data_parity"


def test_ctes_do_not_require_physical_bindings(tmp_path):
    result = compile_fixture(tmp_path, sql="WITH c AS (SELECT x FROM src) SELECT c.x FROM c")
    item = next(i for i in result.sql if i.role == SQLRole.SOURCE)
    assert item.status == "draft_syntax_only" and len(item.bindings) == 1
    assert next(iter(item.bindings.values()))["original_identifier"] == "src"
    module = load_program(tmp_path, result, item)
    assert module.source_query(Spark(), bindings=bindings(item)) is not None


def test_unknown_sql_empty_sql_and_transformation_role_not_dropped(tmp_path):
    model, workflow = package(tmp_path)
    node = next(n for n in model["nodes"] if n["kind"] == "transformation")
    node["properties"] += [
        {"attributes": {"NAME": "Future SQL Behavior", "VALUE": "SELECT 1"}},
        {"attributes": {"NAME": "Post SQL", "VALUE": ""}},
        {"attributes": {"NAME": "SQL Script", "VALUE": "SELECT x+1 FROM src"}},
    ]
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert len(result.sql) == 12
    assert next(i for i in result.sql if i.role == SQLRole.UNKNOWN).status == "blocked"
    assert next(i for i in result.sql if i.original_sql == "").status == "empty"
    assert next(i for i in result.sql if i.role == SQLRole.TRANSFORM).emitted_artifacts


def test_target_update_override_without_sql_in_name_is_inventoried(tmp_path):
    model, workflow = package(tmp_path)
    writer = next(n for n in model["nodes"] if n["kind"] == "sessionextension")
    writer["properties"][0]["attributes"] = {"NAME": "Update Override", "VALUE": "UPDATE dst SET x=8"}
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    item = next(i for i in result.sql if i.property_name == "Update Override")
    assert item.role == SQLRole.TARGET and item.emitted_artifacts


def test_general_workflow_identity_determinism_and_new_directory_only(tmp_path):
    model, workflow = package(tmp_path)
    first = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    second = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert first.artifacts == second.artifacts
    other = next(n["id"] for n in model["nodes"] if n["kind"] == "workflow" and n["name"] == "Other")
    third = compile_sql_artifacts(model, other, source_dialect="oracle")
    assert first.sql[0].id != third.sql[0].id and third.sql
    with pytest.raises(ValueError):
        compile_sql_artifacts(model, "W")
    output = export_sql_artifacts(first, tmp_path / "isolated")
    assert sorted(str(p.relative_to(output)).replace("\\", "/") for p in output.rglob("*") if p.is_file()) == sorted(first.artifacts)
    with pytest.raises(FileExistsError):
        export_sql_artifacts(first, output)
    first.artifacts["../escape.py"] = ""
    with pytest.raises(ValueError):
        export_sql_artifacts(first, tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_missing_parser_is_reported_not_original_sql_fallback(tmp_path):
    model, workflow = package(tmp_path)
    with patch.dict(sys.modules, {"sqlglot": None}):
        result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert all(i.status == "blocked" for i in result.sql)
    assert not any(p.endswith(".ipynb") for p in result.artifacts)
    assert "SQLGLOT_DEPENDENCY_MISSING" in result.sql[0].blocker_codes
    assert any("missing from workbench/requirements.txt" in n for n in result.manifest["dependency_notes"])


def test_numeric_locator_order_not_content_hash_or_lexical_order(tmp_path):
    model, workflow = package(tmp_path)
    node = next(n for n in model["nodes"] if n["kind"] == "transformation")
    twin = deepcopy(node)
    twin["id"] = "000000"
    twin["evidence"]["locator"] = node["evidence"]["locator"].replace("TRANSFORMATION[1]", "TRANSFORMATION[10]")
    node["evidence"]["locator"] = node["evidence"]["locator"].replace("TRANSFORMATION[1]", "TRANSFORMATION[2]")
    model["nodes"].insert(0, twin)
    model["edges"].append({"id": "extra", "source": node["parent_id"], "target": twin["id"],
                           "kind": "contains", "attributes": {}, "evidence": {}})
    result = compile_sql_artifacts(model, workflow, source_dialect="oracle")
    assert next(i for i in result.sql if i.node_id in {node["id"], twin["id"]}).node_id == node["id"]