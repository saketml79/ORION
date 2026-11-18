"""Package-contract regressions: no model, business Job, source query or deployment."""
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from workbench.agents import build_agents
from workbench.app import PackageIndex, _create_package_app
from workbench.fixture_metadata import observe
from workbench.package_context import CONTEXT_VERSION, PackageContext, workflow_invocation_summary
from workbench.tests.test_redesign import chat
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[2]


def parsed(tmp_path, extra="", command="pmcmd startworkflow -f F -nowait Child"):
    path = tmp_path / "context.xml"
    path.write_text(f'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <SOURCE NAME="Data" DATABASETYPE="Oracle" DBDNAME="DB" OWNERNAME="Owner">
      <SOURCEFIELD NAME="ID" DATATYPE="number" PRECISION="5" SCALE="0"/>
    </SOURCE><TARGET NAME="Data" DATABASETYPE="Oracle" DBDNAME="DB" OWNERNAME="Owner"/>
    <MAPPING NAME="Read"><INSTANCE NAME="src" TYPE="SOURCE" TRANSFORMATION_TYPE="Source Definition" TRANSFORMATION_NAME="Data"/></MAPPING>
    <MAPPING NAME="Write"><INSTANCE NAME="dst" TYPE="TARGET" TRANSFORMATION_TYPE="Target Definition" TRANSFORMATION_NAME="Data"/></MAPPING>
    <SESSION NAME="ReadSession" MAPPINGNAME="Read"/><SESSION NAME="WriteSession" MAPPINGNAME="Write"/>
    <WORKFLOW NAME="Parent"><TASK NAME="call" TYPE="Command"><VALUEPAIR NAME="command" VALUE="{command}"/></TASK>
      <TASKINSTANCE NAME="launch" TASKTYPE="Command" TASKNAME="call"/></WORKFLOW>
    <WORKFLOW NAME="Child"><TASKINSTANCE NAME="read" TASKTYPE="Session" TASKNAME="ReadSession"/></WORKFLOW>
    <WORKFLOW NAME="Sibling"><TASKINSTANCE NAME="same_mapping" TASKTYPE="Session" TASKNAME="ReadSession"/></WORKFLOW>
    {extra}</FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    model = analyze_package(path)
    ids = {n["name"]: n["id"] for n in model["nodes"] if n["kind"] == "workflow"}
    return model, ids, path


def test_command_only_caller_child_sources_are_separate_and_no_shared_mapping_expansion(tmp_path):
    model, ids, _ = parsed(tmp_path)
    before = deepcopy(model)
    context = PackageContext(model)
    result = context.contract(ids["Parent"])
    assert result["version"] == CONTEXT_VERSION
    assert result["selected"]["source_status"] == "no_direct_sources"
    assert result["selected"]["direct_sources"] == result["selected"]["direct_targets"] == 0
    assert result["referenced_children"]["items"][0]["workflow_id"] == ids["Child"]
    assert result["referenced_children"]["source_count_sum"] == 1
    assert result["cohort"]["total"] == 2
    assert ids["Sibling"] not in {w["workflow_id"] for w in result["cohort"]["items"]}
    assert result["invocations"]["items"][0]["completion"] == "asynchronous_no_completion_dependency"
    assert not result["invocations"]["items"][0]["enforced_completion_dependency"]
    inward = context.contract(ids["Child"])["invocations"]["items"][0]
    assert inward["direction"] == "inward" and inward["caller_name"] == "Parent"
    assert result["risk_status"] == "not_assessed" and not result["candidate_complete"]
    assert not result["production_ready"] and not result["child_metadata_reads"]
    assert not result["generation_allowed"] and not result["deployment_allowed"]
    assert model == before


@pytest.mark.parametrize("command,reason", [
    ("pmcmd startworkflow -f F -nowait Absent", "literal_child_missing_from_export"),
    ("pmcmd startworkflow -f $$FOLDER -nowait Child", "dynamic_parameter_reference"),
    ("pmcmd startworkflow -f F -nowait Child; echo hidden", "unsupported_command_syntax"),
])
def test_unresolved_child_does_not_invent_cohort_or_leak_command(tmp_path, command, reason):
    model, ids, _ = parsed(tmp_path, command=command)
    result = PackageContext(model).contract(ids["Parent"])
    assert result["cohort"]["total"] == 1 and result["referenced_children"]["total"] == 0
    assert result["outside_references"]["total"] == 1
    assert result["outside_references"]["items"][0]["resolution_reason"] == reason
    assert "echo hidden" not in json.dumps(result) and "$$FOLDER" not in json.dumps(result)
    assert any("child invocation" in b for b in result["blockers"])


def test_ambiguous_inward_boundary_never_connects_candidates(tmp_path):
    model, ids, _ = parsed(tmp_path)
    duplicate = deepcopy(next(n for n in model["nodes"] if n["id"] == ids["Child"]))
    duplicate["id"] = "duplicate-child"
    model["nodes"].append(duplicate)
    context = PackageContext(model)
    result = context.contract(ids["Child"])
    assert result["cohort"]["total"] == 1
    assert result["outside_references"]["items"][0]["resolution"] == "ambiguous"
    assert result["invocations"]["items"][0]["direction"] == "inward"


def test_cycle_and_wait_are_not_a_completion_dag(tmp_path):
    extra = '''<WORKFLOW NAME="Back"><TASK NAME="again" TYPE="Command">
      <VALUEPAIR NAME="command" VALUE="pmcmd startworkflow -f F -wait Parent"/></TASK></WORKFLOW>'''
    model, ids, _ = parsed(tmp_path, extra, command="pmcmd startworkflow -f F -nowait Back")
    result = PackageContext(model).contract(ids["Parent"])
    assert result["cycles"]["total"] == 1
    assert set(result["cycles"]["items"][0]) == {ids["Parent"], ids["Back"]}
    assert all(not r["enforced_completion_dependency"] for r in result["invocations"]["items"])
    assert {r["completion"] for r in result["invocations"]["items"]} == {
        "asynchronous_no_completion_dependency", "command_wait_requested_not_verified_completion"}


def test_exact_data_identity_is_potential_not_enforced_and_names_alone_do_not_match(tmp_path):
    extra = '''<WORKFLOW NAME="Writer"><TASKINSTANCE NAME="write" TASKTYPE="Session" TASKNAME="WriteSession"/>
      <TASK NAME="launch" TYPE="Command"><VALUEPAIR NAME="command" VALUE="pmcmd startworkflow -f F -nowait Child"/></TASK></WORKFLOW>
      <WORKFLOW NAME="SecondWriter"><TASKINSTANCE NAME="write" TASKTYPE="Session" TASKNAME="WriteSession"/>
      <TASK NAME="launch2" TYPE="Command"><VALUEPAIR NAME="command" VALUE="pmcmd startworkflow -f F -nowait Child"/></TASK></WORKFLOW>'''
    model, ids, _ = parsed(tmp_path, extra)
    result = PackageContext(model).contract(ids["Parent"])
    match = result["potential_data_dependencies"]["items"][0]
    assert match["shared_write"] and not match["enforced_dependency"]
    assert match["reader_workflow_ids"] == [ids["Child"]]
    assert set(match["writer_workflow_ids"]) == {ids["Writer"], ids["SecondWriter"]}
    target = next(n for n in model["nodes"] if n["kind"] == "target")
    target["attributes"]["OWNERNAME"] = "Different"
    changed = PackageContext(model).contract(ids["Parent"])
    # Shared writer identity remains, but there is no cross-identity reader match.
    assert changed["potential_data_dependencies"]["items"][0]["reader_workflow_ids"] == []
    target["attributes"].pop("DBDNAME")
    assert PackageContext(model).contract(ids["Parent"])["potential_data_dependencies"]["total"] == 0


def test_cohort_larger_than_graph_page_is_fully_counted_and_explicitly_paged(tmp_path):
    model, ids, _ = parsed(tmp_path)
    # Build exact canonical command evidence for a 121-workflow star, no parser mutations on disk.
    parent_task = next(n for n in model["nodes"] if n["kind"] == "task" and n["name"] == "call")
    child = next(n for n in model["nodes"] if n["id"] == ids["Child"])
    for i in range(119):
        copy = deepcopy(child); copy.update(id=f"child-{i}", name=f"Child{i}")
        model["nodes"].append(copy)
        prop = deepcopy(parent_task["properties"][0])
        prop["attributes"]["VALUE"] = f"pmcmd startworkflow -f F -nowait Child{i}"
        parent_task["properties"].append(prop)
    context = PackageContext(model)
    first = context.contract(ids["Parent"], limit=40)
    assert first["cohort"]["total"] == 121 and first["cohort"]["closure_complete"]
    assert first["cohort"]["truncated"] and first["cohort"]["next_offset"] == 40
    seen = []
    for offset in range(0, 121, 40):
        page = context.contract(ids["Parent"], offset=offset, limit=40)
        seen.extend(w["workflow_id"] for w in page["cohort"]["items"])
    assert len(set(seen)) == 121
    with pytest.raises(ValueError): context.contract(ids["Parent"], limit=201)


def test_metadata_no_direct_vs_no_resolved_sources_does_not_read_children(tmp_path):
    model, ids, _ = parsed(tmp_path)
    identity = SimpleNamespace(client=SimpleNamespace(schemas=Mock(), tables=Mock()),
                               mode="profile", host="https://example.com", user_id="u")
    index = PackageIndex(model, tmp_path)
    result = observe(index, ids["Parent"], "fixture_catalog", identity)
    assert result["sources"] == [] and result["binding_status"] == "no_direct_sources"
    assert result["direct_source_count"] == 0 and not result["child_metadata_reads"]
    identity.client.schemas.get.assert_not_called(); identity.client.tables.get.assert_not_called()
    source = next(n for n in model["nodes"] if n["kind"] == "source")
    source["kind"] = "unresolved_reference"
    source["attributes"] = {"expected_kinds": ["source"], "status": "unresolved"}
    result = observe(PackageIndex(model, tmp_path), ids["Child"], "fixture_catalog", identity)
    assert result["sources"] == [] and result["binding_status"] == "no_resolved_sources"
    identity.client.tables.get.assert_not_called()


def test_api_is_read_only_scoped_and_versioned(tmp_path):
    model, ids, path = parsed(tmp_path)
    app = _create_package_app(tmp_path / "runtime", path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        app.state.service.active = PackageIndex(model, tmp_path)
        response = client.get(f"/api/workflows/{ids['Parent']}/migration-context?limit=1")
        assert response.status_code == 200
        assert response.json()["version"] == CONTEXT_VERSION
        assert response.json()["cohort"]["total"] == 2
        summary = client.get("/api/workflow-invocation-summary")
        assert summary.status_code == 200
        assert summary.json() == workflow_invocation_summary(app.state.service.active)
        assert set(summary.json()["workflows"]) == set(ids.values())
        assert client.get("/api/workflows/missing/migration-context").status_code == 404
        assert client.get(f"/api/workflows/{ids['Parent']}/migration-context?limit=201").status_code == 422


def test_agent_package_contract_metadata_does_not_unlock_unrelated_child_evidence(tmp_path):
    model, ids, _ = parsed(tmp_path)
    with build_agents(tmp_path / "agents", model, ids["Parent"], "original", chat_model=chat()) as bundle:
        tool = bundle.tools["semantics"]["package_migration_contract"]
        report = tool.invoke({})
        assert report["cohort"]["total"] == 2 and not report["candidate_complete"]
        child = bundle.tools["semantics"]["workflow_planning_contract"]
        assert child.invoke({"related_workflow_id": ids["Child"]})["status"] == "related_workflow_not_retrieved"
        child.invoke({"section": "invocations"})
        assert child.invoke({"related_workflow_id": ids["Child"]})["workflow_id"] == ids["Child"]
        assert child.invoke({"related_workflow_id": ids["Sibling"]})["status"] == "related_workflow_not_retrieved"


def test_actual_saved_lumen_master_child_contract_without_reparse_or_mutation():
    path = ROOT / "output/workbench/packages/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba/understanding.json.gz"
    if not path.exists():
        pytest.skip("Immutable saved local Lumen evidence not available")
    before = sha256(path.read_bytes()).hexdigest()
    with gzip.open(path, "rt", encoding="utf-8") as source:
        model = json.load(source)
    context = PackageContext(model)
    ids = {n["name"]: n["id"] for n in model["nodes"] if n["kind"] == "workflow"}
    master = context.contract(ids["wkf_LOAD_AE2E_MASTER_PREP"], limit=200)
    assert master["selected"]["direct_sources"] == 0
    assert master["cohort"]["total"] == 32 and master["invocations"]["total"] == 31
    assert master["cohort"]["direct_source_count_sum"] == 175
    assert master["cohort"]["direct_target_count_sum"] == 198
    outward = [r for r in master["invocations"]["items"] if r["direction"] == "outward"]
    assert len(outward) == 19 and all(r["wait_mode"] == "nowait" for r in outward)
    child = context.contract(ids["wkf_Load_AE2E_WILTEL_NETWORK_DISCO"], limit=200)
    assert child["selected"]["direct_sources"] == 4 and child["selected"]["direct_targets"] == 8
    assert {r["caller_name"] for r in child["invocations"]["items"] if r["direction"] == "inward"} == {
        "wkf_LOAD_AE2E_MASTER_PREP", "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS"}
    assert sha256(path.read_bytes()).hexdigest() == before