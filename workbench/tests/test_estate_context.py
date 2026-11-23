"""Offline exact-graph estate retrieval and role authorization regressions."""
from copy import deepcopy
from pathlib import Path
import json

from langchain_core.messages import AIMessage
import pytest

from workbench.agents import _Evidence, _POLICY, _result, build_agents
from workbench.loops import grade
from workbench.understanding import analyze_package
from workbench.tests.test_agents import fake, finding
from workbench.tests.test_loops import supported


XML = b'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
<SOURCE NAME="Input"><SOURCEFIELD NAME="ID" DATATYPE="integer"/></SOURCE>
<TARGET NAME="Output"><TARGETFIELD NAME="ID" DATATYPE="integer"/></TARGET>
<MAPPING NAME="M"><INSTANCE NAME="src" TRANSFORMATION_NAME="Input" TRANSFORMATION_TYPE="Source Definition"/>
<INSTANCE NAME="tgt" TRANSFORMATION_NAME="Output" TRANSFORMATION_TYPE="Target Definition"/></MAPPING>
<MAPPING NAME="Clone"><INSTANCE NAME="src" TRANSFORMATION_NAME="Input" TRANSFORMATION_TYPE="Source Definition"/>
<INSTANCE NAME="tgt" TRANSFORMATION_NAME="Output" TRANSFORMATION_TYPE="Target Definition"/></MAPPING>
<SESSION NAME="S" MAPPINGNAME="M"/><SESSION NAME="SC" MAPPINGNAME="Clone"/>
<WORKFLOW NAME="Selected"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
<WORKFLOW NAME="Shared"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
<WORKFLOW NAME="Similar"><TASKINSTANCE NAME="Run" TASKNAME="SC" TASKTYPE="Session"/></WORKFLOW>
<WORKFLOW NAME="Unrelated"/></FOLDER></REPOSITORY></POWERMART>'''


@pytest.fixture
def estate(tmp_path):
    source = tmp_path / "estate.xml"
    source.write_bytes(XML)
    model = analyze_package(source)
    ids = {n["name"]: n["id"] for n in model["nodes"] if n["kind"] == "workflow"}
    return model, ids


def test_exact_shared_definitions_fingerprints_and_bounded_coverage(estate):
    model, ids = estate
    evidence = _Evidence(model, ids["Selected"], [], [])
    assert ids["Shared"] not in evidence.records
    first = evidence.package_neighbors(limit=1)
    assert first["candidate_count"] == 2 and first["estate_workflow_count"] == 4
    assert first["truncated"] and first["retrieved_candidate_count"] == 1
    assert not first["safe_merge"] and first["semantic_equivalence"] == "unproven"
    unreturned = ({ids["Shared"], ids["Similar"]} - {first["neighbors"][0]["workflow_id"]}).pop()
    assert unreturned not in evidence.records
    second = evidence.package_neighbors(offset=1, limit=1)
    assert second["retrieved_candidate_count"] == 2 and not second["truncated"]
    rows = {r["workflow_id"]: r for r in first["neighbors"] + second["neighbors"]}
    shared = {r["relation"] for r in rows[ids["Shared"]]["relations"]}
    similar = {r["relation"] for r in rows[ids["Similar"]]["relations"]}
    assert {"shared_mapping_definition", "shared_source_definition", "shared_target_definition"} <= shared
    assert "metadata_fingerprint" in similar and "shared_mapping_definition" not in similar
    assert {"shared_source_definition", "shared_target_definition"} <= similar
    assert ids["Unrelated"] not in evidence.records
    assert all(not row["safe_merge"] and len(row["questions"]) == 4 for row in rows.values())
    assert evidence.scope == _Evidence(model, ids["Selected"], [], []).scope
    assert evidence.package_neighbors(offset=99)["neighbors"] == []
    assert evidence.package_neighbors(limit=999)["limit"] == 10


def test_fingerprint_is_metadata_not_names_or_invented_equivalence(estate):
    model, ids = estate
    model = deepcopy(model)
    clone = next(n for n in model["nodes"] if n["kind"] == "mapping" and n["name"] == "Clone")
    clone["attributes"]["SQL_OVERRIDE"] = "select ID + 1 from Input"
    report = _Evidence(model, ids["Selected"], [], []).package_neighbors()
    similar = next(r for r in report["neighbors"] if r["workflow_id"] == ids["Similar"])
    assert "metadata_fingerprint" not in {r["relation"] for r in similar["relations"]}
    assert "shared_source_definition" in {r["relation"] for r in similar["relations"]}


def test_role_tools_explicit_retrieval_citations_and_no_document_leak(estate, tmp_path):
    model, ids = estate
    with build_agents(tmp_path, model, ids["Selected"], "original", chat_model=fake()) as bundle:
        for role in ("architecture", "semantics", "validation", "supervisor"):
            assert "package_neighbors" in bundle.tools[role]
        for role in ("discovery", "artifact"):
            assert "package_neighbors" not in bundle.tools[role]
        tools = bundle.tools["architecture"]
        before = finding("architecture", evidence_ids=[ids["Shared"]])
        rejected = tools["submit_finding"].invoke({"finding": before})
        assert rejected["recorded"] is False and rejected["error"] == "finding_evidence_not_retrieved"
        page = tools["package_neighbors"].invoke({"limit": 1})
        row = page["neighbors"][0]
        citation = finding("architecture", evidence_ids=row["evidence_ids"])
        assert tools["submit_finding"].invoke({"finding": citation})["recorded"]
        assert all(not i.startswith(("document:", "feedback:")) for i in row["evidence_ids"])
        # A different role cannot cite a globally registered ID before retrieval.
        rejected = bundle.tools["validation"]["submit_finding"].invoke(
            {"finding": finding("validation", evidence_ids=[row["workflow_id"]])})
        assert rejected["recorded"] is False and rejected["error"] == "finding_evidence_not_retrieved"
        result = _result(bundle, "advisory", "Bounded review")
        assert result["estate_context"]["retrieved_candidate_count"] == 1
        assert result["neighbor_pages"] == [{"offset": 0, "limit": 1}]
        assert "not_all_workflows_analyzed" in result["estate_context"]["analysis_coverage"]
        output = supported(ids["Selected"])
        output["findings"][0]["evidence_ids"] = [row["workflow_id"]]
        assert grade(output, _Evidence(model, ids["Selected"], [], []))["status"] == "invalid_output"
        output["neighbor_pages"] = result["neighbor_pages"]
        assert grade(output, _Evidence(model, ids["Selected"], [], []))["status"] == "valid_advisory"


def test_dynamic_allowlist_and_safe_tool_audit_via_compiled_graph(estate, tmp_path):
    model, ids = estate
    chat = fake([AIMessage(content="", tool_calls=[{"id": "neighbors", "name": "package_neighbors",
                 "args": {"offset": 0, "limit": 1}}]), AIMessage(content="Bounded metadata only.")])
    with build_agents(tmp_path, model, ids["Selected"], "original", chat_model=chat) as bundle:
        bundle.specialists["architecture"].invoke(
            {"messages": [{"role": "user", "content": "Compare neighboring workflow alternatives."}]},
            {"configurable": {"thread_id": "neighbors"}, "recursion_limit": 12})
        with bundle.ledger.connect() as db:
            events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM agent_usage WHERE event='tool_completed'")]
        assert any(e["tool_name"] == "package_neighbors" and e["success"] for e in events)
        assert "Architecture must retrieve package_neighbors" in _POLICY


def test_pagination_covers_every_candidate_without_field_walk_or_closure_cache(estate):
    model, ids = estate
    model = deepcopy(model)
    template = next(n for n in model["nodes"] if n["id"] == ids["Shared"])
    mapping = next(n["id"] for n in model["nodes"] if n["kind"] == "mapping" and n["name"] == "M")
    for number in range(23):
        wid = "extra-" + str(number)
        model["nodes"].append({**template, "id": wid, "name": wid})
        model["edges"].append({"id": "edge-" + wid, "source": wid, "target": mapping, "kind": "session_mapping"})
    evidence = _Evidence(model, ids["Selected"], [], [])
    rows = []
    for offset in (0, 10, 20):
        page = evidence.package_neighbors(offset=offset)
        assert page["candidate_count"] == 25
        assert len(page["neighbors"]) <= 10
        rows.extend(page["neighbors"])
    assert len({r["workflow_id"] for r in rows}) == 25
    assert page["retrieved_candidate_count"] == 25 and not page["truncated"]
    assert all(not key.endswith("closures") for key in vars(evidence))


@pytest.mark.skipif(not Path("input/LumenExample.XML").is_file(), reason="Full export not present")
def test_sample_estate_neighbors_real_export():
    # One full-export parse, not a suite-wide fixture or model invocation.
    model = analyze_package(Path("input/LumenExample.XML"))
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow"
                    and n["name"] == "wkf_CDW_ASL_DAILY_REFRESH_MASTER")
    evidence = _Evidence(model, workflow["id"], [], [])
    report = evidence.package_neighbors(limit=2)
    assert report["estate_workflow_count"] > 100
    assert report["candidate_count"] >= len(report["neighbors"])
    assert len(report["neighbors"]) <= 2 and not report["safe_merge"]
    for row in report["neighbors"]:
        assert row["workflow_id"] in evidence.nodes
        assert all(ref in evidence.records for ref in row["evidence_ids"])