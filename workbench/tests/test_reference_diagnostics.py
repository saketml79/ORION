"""Actual XML ownership and honest dependency evidence, no remote access."""
from collections import Counter
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from workbench.app import PackageIndex
from workbench.fixture_metadata import observe
from workbench.package_context import PackageContext
from workbench.reference_diagnostics import reference_report, sql_dependencies
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[2]


def override_model(tmp_path, *, task_name="S", duplicate=False):
    path = tmp_path / "override.xml"
    session = '<SESSION NAME="S" MAPPINGNAME="M"/>'
    path.write_text(f'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <TARGET NAME="Data"><TARGETFIELD NAME="ID" DATATYPE="number"/></TARGET>
      <MAPPING NAME="M"><INSTANCE NAME="Data" TRANSFORMATION_NAME="Data" TYPE="TARGET" TRANSFORMATION_TYPE="Target Definition"/></MAPPING>
      <WORKFLOW NAME="W"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="{task_name}" ISENABLED="NO">
        <SESSIONEXTENSION NAME="Writer" SINSTANCENAME="Data" TRANSFORMATIONTYPE="Target Definition"/>
      </TASKINSTANCE></WORKFLOW>{session}{session if duplicate else ''}
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return analyze_package(path)


def test_task_instance_override_resolves_forward_session_without_enabling_disabled_task(tmp_path):
    model = override_model(tmp_path)
    nodes = {n["id"]: n for n in model["nodes"]}
    edges = [e for e in model["edges"] if e["kind"] == "session_instance"]
    assert len(edges) == 1
    assert nodes[edges[0]["target"]]["kind"] == "instance"
    assert not [n for n in model["nodes"] if n["kind"] == "unresolved_reference"]
    assert next(n for n in model["nodes"] if n["kind"] == "taskinstance")["attributes"]["ISENABLED"] == "NO"


def test_task_override_follows_exact_cross_folder_session_shortcut(tmp_path):
    model = override_model(tmp_path)
    path = tmp_path / "override.xml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('TASKNAME="S"', 'TASKNAME="SharedSession"')
    text = text.replace('<SESSION NAME="S" MAPPINGNAME="M"/>',
        '<SHORTCUT NAME="SharedSession" REFOBJECTNAME="S" OBJECTSUBTYPE="Session" REFFOLDERNAME="Shared" REFREPOSITORYNAME="R"/>')
    text = text.replace('</FOLDER></REPOSITORY>', '<SESSION NAME="Unrelated" MAPPINGNAME="M"/></FOLDER><FOLDER NAME="Shared"><MAPPING NAME="OtherMap"><INSTANCE NAME="Data" TRANSFORMATION_NAME="Data" TRANSFORMATION_TYPE="Target Definition" TYPE="TARGET"/></MAPPING><TARGET NAME="Data"/><SESSION NAME="S" MAPPINGNAME="OtherMap"/></FOLDER></REPOSITORY>')
    path.write_text(text, encoding="utf-8")
    model = analyze_package(path)
    nodes = {n["id"]: n for n in model["nodes"]}
    edge = next(e for e in model["edges"] if e["kind"] == "session_instance")
    assert nodes[edge["target"]]["kind"] == "instance"
    assert nodes[edge["target"]]["scope"]["folder"] == "Shared"
    assert not [n for n in model["nodes"] if n["kind"] == "unresolved_reference"]


@pytest.mark.parametrize("name,duplicate,status", [("s", False, "unresolved"), ("S", True, "ambiguous")])
def test_missing_case_or_ambiguous_session_never_uses_another_mapping(tmp_path, name, duplicate, status):
    model = override_model(tmp_path, task_name=name, duplicate=duplicate)
    nodes = {n["id"]: n for n in model["nodes"]}
    task = next(n for n in nodes.values() if n["kind"] == "taskinstance")
    definition = next(e for e in model["edges"] if e["kind"] == "task_definition" and e["source"] == task["id"])
    assert nodes[definition["target"]]["attributes"]["status"] == status
    instance = next(e for e in model["edges"] if e["kind"] == "session_instance")
    assert nodes[instance["target"]]["kind"] == "unresolved_reference"


def test_sqlglot_cte_schema_parameters_and_dynamic_java_remain_separate():
    def prop(name, sql):
        return {"attributes": {"NAME": name, "VALUE": sql}, "evidence": {"locator": "SQL"}}
    owner = {"id": "m", "kind": "mapping", "properties": [
        prop("Sql Query", 'WITH c AS (SELECT * FROM "Owner"."Orders") SELECT * FROM c JOIN Customer ON 1=1'),
        prop("Post SQL", "DELETE FROM Owner.Output; INSERT INTO Owner.Output SELECT * FROM Owner.Input"),
        prop("Lookup Sql Override", "SELECT * FROM $$SCHEMA.Items"),
        prop("Pre SQL", "SELECT ( FROM"),
        {"embedded_code": {"source_sha256": "a" * 64, "sql_expressions": [{"expression": '"select * from " + table'}]}}]}
    before = deepcopy(owner)
    rows = sql_dependencies({"m": owner}, {"m"})
    assert {tuple(t["parts"]) for t in rows[0]["tables"]} == {("Owner", "Orders"), ("Customer",)}
    assert {tuple(t["parts"]) for t in rows[1]["tables"]} == {("Owner", "Output"), ("Owner", "Input")}
    assert rows[2]["status"] == "parameterized_or_redacted_sql" and not rows[2]["tables"]
    assert rows[3]["status"] == "sql_parse_unverified" and not rows[3]["tables"]
    assert rows[4]["status"] == "dynamic_java_expression_not_standalone_sql"
    assert all(r["binding_status"] == "unverified" and r["physical_existence"] == "not_observed" for r in rows)
    assert owner == before


def test_unrelated_connection_gap_does_not_claim_missing_source_or_query_uc(tmp_path):
    model = override_model(tmp_path)
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
    ref = {"id": "old", "name": "[REDACTED]", "kind": "unresolved_reference", "parent_id": workflow["id"],
           "attributes": {"expected_kinds": ["connection"], "status_reason": "connection_value_redacted"}}
    model["nodes"].append(ref)
    model["edges"].append({"id": "old-edge", "source": workflow["id"], "target": "old", "kind": "contains"})
    context = PackageContext(model).contract(workflow["id"])
    assert context["selected"]["source_status"] == "no_direct_sources"
    assert context["reference_diagnostics"]["summary"]["source_definition_blockers"] == 0
    row = context["reference_diagnostics"]["references"]["items"][0]
    assert row["status"] == "historical_connection_redaction" and "UC tables" in row["action"]
    identity = SimpleNamespace(client=SimpleNamespace(schemas=Mock(), tables=Mock()), mode="profile", host="offline", user_id="u")
    result = observe(PackageIndex(model, tmp_path), workflow["id"], "fixture_catalog", identity)
    assert result["binding_status"] == "no_direct_sources"
    identity.client.schemas.get.assert_not_called(); identity.client.tables.get.assert_not_called()


def test_actual_lumen_fresh_reference_resolution_and_immutable_saved_history():
    source = ROOT / "input/LumenExample.XML"
    saved = ROOT / "output/workbench/packages/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba/understanding.json.gz"
    if not saved.exists():
        pytest.skip("Saved Lumen analysis unavailable")
    before = sha256(saved.read_bytes()).hexdigest()
    with gzip.open(saved, "rt", encoding="utf-8") as stream:
        historical = json.load(stream)
    fresh = analyze_package(source)
    assert fresh["package"]["sha256"] == historical["package"]["sha256"]
    refs = [n for n in fresh["nodes"] if n["kind"] == "unresolved_reference"]
    assert refs == []
    assert Counter(n["kind"] for n in fresh["nodes"])["workflow"] == 243
    old_refs = [n for n in historical["nodes"] if n["kind"] == "unresolved_reference"]
    assert len(old_refs) == 5081
    context = PackageContext(fresh)
    rows = [context.summary(n["id"]) for n in fresh["nodes"] if n["kind"] == "workflow"]
    assert Counter(r["source_status"] for r in rows) == {"direct_sources_captured_not_bound": 199, "no_direct_sources": 44}
    master = next(n for n in fresh["nodes"] if n["kind"] == "workflow" and n["name"] == "wkf_LOAD_AE2E_MASTER_PREP")
    contract = context.contract(master["id"])
    assert "Orchestration workflow" in contract["selected"]["source_explanation"]
    assert len([r for r in contract["invocations"]["items"] if r["direction"] == "outward"]) == 19
    assert not contract["production_ready"] and not contract["generation_allowed"]
    assert sha256(saved.read_bytes()).hexdigest() == before