"""Runtime planning contracts exercised through the real XML graph parser."""
from copy import deepcopy
import json

import pytest

from workbench.migration_planning import PlanningIndex
from workbench.understanding import analyze_package


def parse(tmp_path, body):
    path = tmp_path / "planning.xml"
    path.write_text('<POWERMART><REPOSITORY NAME="R">' + body +
                    '</REPOSITORY></POWERMART>', encoding="utf-8")
    return analyze_package(path)


def identity(model, kind, name):
    return next(n["id"] for n in model["nodes"] if n["kind"] == kind and n["name"] == name)


def mapping(name="M", target="T"):
    return f'''<MAPPING NAME="{name}"><MAPPINGVARIABLE NAME="$$day" DEFAULTVALUE="2026-01-01"/>
      <INSTANCE NAME="src" TRANSFORMATION_NAME="S" TRANSFORMATION_TYPE="Source Definition"/>
      <INSTANCE NAME="dst" TRANSFORMATION_NAME="{target}" TRANSFORMATION_TYPE="Target Definition"/>
      <CONNECTOR FROMINSTANCE="src" FROMFIELD="id" TOINSTANCE="dst" TOFIELD="id"/>
      </MAPPING>'''


def definitions():
    return '''<SOURCE NAME="S" DBDNAME="DB" OWNERNAME="dbo"><SOURCEFIELD NAME="id" DATATYPE="integer"/></SOURCE>
      <TARGET NAME="T" DBDNAME="DB" OWNERNAME="dbo"><TARGETFIELD NAME="id" DATATYPE="integer"/></TARGET>'''


def workflow(name, session="session", extra="", scheduler=""):
    return f'''<WORKFLOW NAME="{name}" {scheduler}>
      <TASK NAME="start" TYPE="Start"/><TASK NAME="command" TYPE="Command">
      <ATTRIBUTE NAME="Command" VALUE="echo reviewed"/></TASK>
      <TASKINSTANCE NAME="start" TASKNAME="start" TASKTYPE="Start"/>
      <TASKINSTANCE NAME="command" TASKNAME="command" TASKTYPE="Command" ISENABLED="NO"
        FAIL_PARENT_IF_INSTANCE_FAILS="YES" TREAT_INPUTLINK_AS_AND="YES" SKIP_IF_DISABLED="YES"/>
      <TASKINSTANCE NAME="run" TASKNAME="{session}" TASKTYPE="Session"/>
      <WORKFLOWLINK FROMTASK="start" TOTASK="command" CONDITION="TRUE"/>
      <WORKFLOWLINK FROMTASK="command" TOTASK="run" CONDITION="$command.Status = SUCCEEDED"/>
      {extra}</WORKFLOW>'''


@pytest.fixture
def estate(tmp_path):
    return parse(tmp_path, '<FOLDER NAME="F">' + definitions() + mapping() + mapping("M2") + '''
      <SESSION NAME="session" MAPPINGNAME="M" COMMITINTERVAL="1000">
        <ATTRIBUTE NAME="Recovery Strategy" VALUE="Resume from last checkpoint"/>
        <SESSIONEXTENSION NAME="writer" SINSTANCENAME="dst" TRANSFORMATIONTYPE="Target Definition">
          <ATTRIBUTE NAME="Truncate target table option" VALUE="YES"/>
          <CONNECTIONREFERENCE CONNECTIONNAME="hidden-prod" CONNECTIONTYPE="Relational"/>
        </SESSIONEXTENSION></SESSION>
      <SESSION NAME="session2" MAPPINGNAME="M2" COMMITINTERVAL="2000"/>
      <SCHEDULER NAME="daily"><SCHEDULEINFO SCHEDULETYPE="DAILY"/></SCHEDULER>
      <SCHEDULER NAME="weekly"><SCHEDULEINFO SCHEDULETYPE="WEEKLY"/></SCHEDULER>
      ''' + workflow("W1", scheduler='SCHEDULERNAME="daily"') +
      workflow("W2", scheduler='SCHEDULERNAME="weekly"') + workflow("W3", "session2") +
      '<WORKFLOW NAME="empty"/></FOLDER>')


def test_manifest_covers_every_workflow_without_payload_and_pages(estate):
    index = PlanningIndex(estate)
    first = index.estate_manifest(limit=1)
    assert first["workflow_count"] == 4
    assert first["workflows"]["total"] == 4
    assert first["workflows"]["truncated"]
    rows = [index.estate_manifest(i, 1)["workflows"]["items"][0] for i in range(4)]
    assert {r["workflow_id"] for r in rows} == {
        n["id"] for n in estate["nodes"] if n["kind"] == "workflow"}
    assert all("tasks" not in r and "graph" not in r for r in rows)
    assert first["coverage_totals"]["mappings"] == 3
    assert first["estate_object_counts"]["mapping"] == 2
    assert first["coverage_totals"]["taskinstances"] == 9
    assert index.estate_manifest(100, 1)["workflows"]["returned"] == 0
    assert first["provenance"]["package_sha256"] == estate["package"]["sha256"]
    assert first["provenance"]["model_schema_version"] == estate["schema_version"]


def test_exact_control_edges_definitions_failure_skip_and_order(estate):
    contract = PlanningIndex(estate).workflow_contract(identity(estate, "workflow", "W1"))
    expected = [e for e in estate["edges"] if e["kind"] == "workflow_control" and
                estate_node(estate, e["source"])["parent_id"] == contract["workflow_id"]]
    actual = contract["control_links"]["items"]
    assert {e["id"] for e in actual} == {e["id"] for e in expected}
    assert [e["attributes"]["value"] for e in actual] == [e["attributes"] for e in expected]
    assert all(not e["attributes"]["truncated"] for e in actual)
    command = next(t for t in contract["tasks"]["items"] if t["name"]["value"] == "command")
    assert command["attributes"]["value"]["ISENABLED"] == "NO"
    assert command["attributes"]["value"]["SKIP_IF_DISABLED"] == "YES"
    assert command["attributes"]["value"]["FAIL_PARENT_IF_INSTANCE_FAILS"] == "YES"
    definition = command["definitions"]["items"][0]["definition"]
    assert definition["kind"] == "task"
    assert definition["properties"]["items"][0]["value"]["attributes"]["VALUE"] == "echo reviewed"
    assert contract["sequencing"]["inferred_edges"] == []
    assert not contract["sequencing"]["parallel_execution_authorized"]
    assert not contract["sequencing"]["topological_flattening"]


def estate_node(model, node_id):
    return next(n for n in model["nodes"] if n["id"] == node_id)


def test_session_mapping_closures_and_connection_is_not_schema(estate):
    contract = PlanningIndex(estate).workflow_contract(identity(estate, "workflow", "W1"))
    session = contract["sessions"]["items"][0]
    assert session["closure"]["mapping_ids"]["items"] == [identity(estate, "mapping", "M")]
    assert session["closure"]["source_ids"]["items"] == [identity(estate, "source", "S")]
    assert session["closure"]["target_ids"]["items"] == [identity(estate, "target", "T")]
    assert contract["mappings"]["items"][0]["closure"]["target_ids"]["total"] == 1
    assert session["write_semantics"]["status"] == "unknown"
    # The parser retains a safe alias as external metadata, not a fabricated
    # unresolved graph node. The gap still blocks physical execution.
    assert contract["coverage"]["unresolved_references"] == 0
    assert contract["unresolved_references"]["total"] == 0
    external = contract["external_connection_metadata"]
    assert external["total"] == 1
    attrs = external["items"][0]["attributes"]["value"]
    assert attrs["CONNECTIONNAME"] == "hidden-prod"
    assert attrs["status"] == "external"
    assert attrs["status_reason"] == "external_connection_definition_not_exported"
    assert "external_connection_metadata_required" in contract["readiness"]["blockers"]
    assert contract["coverage"]["unresolved_fields"] == 0
    writer = next(n for n in contract["runtime_evidence"]["items"] if n["kind"] == "sessionextension")
    assert writer["properties"]["items"][0]["value"]["attributes"] == {
        "NAME": "Truncate target table option", "VALUE": "YES"}
    assert session["attributes"]["value"]["COMMITINTERVAL"] == "1000"
    assert session["properties"]["items"][0]["value"]["attributes"]["VALUE"] == "Resume from last checkpoint"
    assert {e["kind"] for e in contract["definition_links"]["items"]} >= {
        "task_definition", "session_mapping", "session_instance", "instance_of", "schedule_reference"}
    for endpoint in contract["sources"]["items"] + contract["targets"]["items"]:
        assert endpoint["schema"]["status"] == "captured_unverified"
        assert endpoint["schema"]["fields"]["total"] == 1
        assert endpoint["logical_identity"]["qualifiers"]["value"]["OWNERNAME"] == "dbo"
        assert endpoint["connection_status"] == "unverified"
        assert endpoint["physical_binding"] is None
        assert endpoint["required_physical_bindings"] == ["environment", "connection", "physical_object"]
        assert endpoint["write_semantics"]["status"] == "unknown"
    assert not contract["readiness"]["missing_connection_implies_missing_schema"]
    assert not contract["readiness"]["execution_ready"]
    assert "connection_reference" not in {e["kind"] for e in contract["definition_links"]["items"]}


def test_same_canonical_target_conflicts_have_unknown_sequence(estate):
    contract = PlanningIndex(estate).workflow_contract(identity(estate, "workflow", "W1"))
    conflicts = contract["potential_shared_target_conflicts"]
    assert conflicts["total"] == 1
    conflict = conflicts["items"][0]
    assert conflict["target_definition_id"] == identity(estate, "target", "T")
    assert conflict["workflow_ids"]["total"] == 3
    assert conflict["sequence"] == "unknown"
    assert not conflict["inferred_ordering"]
    assert contract["sequencing"]["inferred_edges"] == []
    assert "shared_target_sequence_unknown" in contract["readiness"]["blockers"]


def test_reuse_identity_and_equivalent_metadata_never_authorize_merge(estate):
    contract = PlanningIndex(estate).workflow_contract(identity(estate, "workflow", "W1"))
    candidates = contract["reuse_candidates"]["items"]
    assert {c["kind"] for c in candidates} == {"shared_mapping_identity", "metadata_fingerprint"}
    assert all(not c["safe_merge"] and not c["parameterization_authorized"] for c in candidates)
    assert all(c["status"] == "candidate_only_not_semantic_equivalence" for c in candidates)
    comparison = contract["combine_parameterized_jobs"]
    assert not comparison["authorized"]
    assert {r["dimension"] for r in comparison["mandatory_reviews"]} >= {"schedule", "checkpoint", "transaction"}
    assert all(r["status"] == "unverified" for r in comparison["mandatory_reviews"])
    assert contract["parameter_evidence"]["total"] == 1
    assert contract["schedule_evidence"]["total"] == 2
    assert not contract["generation_allowed"]
    assert not contract["notebook_generation_allowed"]
    assert not contract["deployment_allowed"]
    assert not contract["readiness"]["execution_ready"]


def test_same_names_in_different_folders_are_not_target_conflicts(tmp_path):
    body = ''.join('<FOLDER NAME="' + f + '">' + definitions() + mapping() +
                   '<SESSION NAME="session" MAPPINGNAME="M"/>' + workflow("W" + f) +
                   '</FOLDER>' for f in ("A", "B"))
    model = parse(tmp_path, body)
    for name in ("WA", "WB"):
        c = PlanningIndex(model).workflow_contract(identity(model, "workflow", name))
        assert c["potential_shared_target_conflicts"]["total"] == 0
        assert c["reuse_candidates"]["total"] == 0


def test_cycles_preserve_parallel_edges_self_loops_and_conditions(tmp_path):
    model = parse(tmp_path, '<FOLDER NAME="F">' + definitions() + mapping() +
                  '<SESSION NAME="session" MAPPINGNAME="M"/>' + workflow("W", extra='''
        <WORKFLOWLINK FROMTASK="run" TOTASK="command" CONDITION="retry"/>
        <WORKFLOWLINK FROMTASK="run" TOTASK="command" CONDITION="failure"/>
        <WORKFLOWLINK FROMTASK="start" TOTASK="start" CONDITION="loop"/>
        ''') + '</FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"))
    assert c["task_cycles"]["total"] == 2
    assert sorted(x["members"]["total"] for x in c["task_cycles"]["items"]) == [1, 2]
    assert sorted(x["edges"]["total"] for x in c["task_cycles"]["items"]) == [1, 3]
    assert c["control_links"]["total"] == 5
    assert "task_cycles_require_review" in c["readiness"]["blockers"]
    assert not c["sequencing"]["topological_flattening"]


def test_unmapped_tasks_and_unresolved_control_endpoints_not_omitted(tmp_path):
    model = parse(tmp_path, '<FOLDER NAME="F">' + workflow("W", "missing", extra='''
        <WORKFLOWLINK FROMTASK="run" TOTASK="absent" CONDITION="failed"/>''') + '</FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"))
    assert c["tasks"]["total"] == 3
    assert c["tasks_without_mapping"]["total"] == 3
    assert c["unresolved_references"]["total"] == 2
    assert c["control_links"]["total"] == 3
    assert c["mappings"]["total"] == 0
    assert "tasks_without_mapping_require_disposition_not_silent_omission" in c["readiness"]["blockers"]


def test_nested_worklet_boundary_and_controls_not_flattened(tmp_path):
    model = parse(tmp_path, '''<FOLDER NAME="F"><WORKLET NAME="Child">
      <TASK NAME="a" TYPE="Command"/><TASKINSTANCE NAME="a" TASKNAME="a" TASKTYPE="Command"/>
      <WORKFLOWLINK FROMTASK="a" TOTASK="a" CONDITION="retry"/></WORKLET>
      <WORKFLOW NAME="W"><TASKINSTANCE NAME="invoke" TASKNAME="Child" TASKTYPE="Worklet"/></WORKFLOW>
      </FOLDER>''')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"))
    assert c["nested_boundaries"]["total"] == 1
    assert c["coverage"]["direct_taskinstances"] == 1
    assert c["coverage"]["taskinstances"] == 2
    assert {t["boundary_status"] for t in c["tasks"]["items"]} == {
        "direct", "nested_definition_not_flattened"}
    assert c["control_links"]["total"] == 1
    assert "nested_workflow_or_worklet_boundary_blocked" in c["readiness"]["blockers"]


def test_bounded_outputs_honest_totals_and_exact_truncation_paths(tmp_path):
    fields = ''.join(f'<TARGETFIELD NAME="f{i}"/>' for i in range(7))
    properties = ''.join(f'<ATTRIBUTE NAME="p{i}" VALUE="long value"/>' for i in range(5))
    model = parse(tmp_path, '<FOLDER NAME="F"><SOURCE NAME="S"/><TARGET NAME="T">' + fields +
                  '</TARGET>' + mapping() + '<SESSION NAME="session" MAPPINGNAME="M">' +
                  properties + '</SESSION>' + workflow("W") + '</FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"),
                                              limit=1, field_limit=2, attribute_limit=2, text_limit=3)
    assert c["tasks"]["total"] == 3 and c["tasks"]["returned"] == 1
    assert c["control_links"]["total"] == 2 and c["control_links"]["truncated"]
    fields_page = c["targets"]["items"][0]["schema"]["fields"]
    assert fields_page["total"] == 7 and fields_page["returned"] == 2 and fields_page["truncated"]
    props = c["sessions"]["items"][0]["properties"]
    assert props["total"] == 5 and props["returned"] == 1 and props["truncated"]
    attrs = c["control_links"]["items"][0]["attributes"]
    assert attrs["truncated"] and attrs["truncations"]
    assert PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"),
        offset=1, limit=1)["tasks"]["items"][0]["id"] != c["tasks"]["items"][0]["id"]


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 201}, {"limit": True}, {"offset": -1},
    {"field_limit": 0}, {"attribute_limit": 201}, {"text_limit": 0}, {"text_limit": 16385},
])
def test_invalid_bounds_rejected(estate, kwargs):
    with pytest.raises(ValueError):
        PlanningIndex(estate).workflow_contract(identity(estate, "workflow", "W1"), **kwargs)


def test_invalid_workflow_and_invalid_graph_rejected(estate):
    with pytest.raises(ValueError):
        PlanningIndex(estate).workflow_contract(identity(estate, "mapping", "M"))
    bad = deepcopy(estate)
    bad["edges"][0]["target"] = "missing"
    with pytest.raises(ValueError):
        PlanningIndex(bad)


def test_unresolved_fields_schema_absence_and_unsupported_tags_are_distinct(tmp_path):
    model = parse(tmp_path, '<FOLDER NAME="F"><SOURCE NAME="S"/><TARGET NAME="T"/>' + mapping() +
                  '<SESSION NAME="session" MAPPINGNAME="M"/>' + workflow("W", extra='<FUTURE MODE="x"/>') +
                  '</FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"))
    assert c["coverage"]["unresolved_fields"] == 2
    assert c["coverage"]["unresolved_references"] == 0
    assert c["unresolved_fields"]["total"] == 2
    assert c["targets"]["items"][0]["schema"]["status"] == "not_captured"
    assert c["unsupported_tags"]["value"] == {"FUTURE": 1}
    assert "package_unsupported_tags_require_review" in c["readiness"]["blockers"]


def test_shortcuts_use_canonical_identity_not_alias_name(tmp_path):
    model = parse(tmp_path, '<FOLDER NAME="Shared">' + definitions() + '''</FOLDER>
      <FOLDER NAME="F"><SHORTCUT NAME="Alias" REFERENCETYPE="TARGET" REFOBJECTNAME="T"
      REFFOLDERNAME="Shared" REFREPOSITORYNAME="R"/><SOURCE NAME="S"><SOURCEFIELD NAME="id"/></SOURCE>'''
      + mapping(target="Alias") + '<SESSION NAME="session" MAPPINGNAME="M"/>' + workflow("W1") +
      workflow("W2") + '</FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W1"))
    assert c["targets"]["total"] == 1
    assert c["potential_shared_target_conflicts"]["items"][0]["target_definition_id"] == identity(model, "target", "T")
    assert any(e["kind"] == "shortcut_reference" for e in c["definition_links"]["items"])


def test_empty_estate_and_unmapped_workflow_have_honest_coverage(tmp_path):
    empty = PlanningIndex(parse(tmp_path, '<FOLDER NAME="F"/>')).estate_manifest()
    assert empty["workflow_count"] == 0
    assert empty["workflows"]["total"] == 0 and not empty["workflows"]["truncated"]
    model = parse(tmp_path, '<FOLDER NAME="F"><WORKFLOW NAME="W"/></FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"))
    assert c["tasks"]["total"] == c["control_links"]["total"] == c["mappings"]["total"] == 0
    assert {"no_resolved_mapping", "source_target_coverage_incomplete"} <= set(c["readiness"]["blockers"])


def test_deep_task_chain_scc_is_iterative_and_page_counts_remain_full(tmp_path):
    size = 1100
    body = '<FOLDER NAME="F"><WORKFLOW NAME="W"><TASK NAME="cmd" TYPE="Command"/>'
    body += ''.join(f'<TASKINSTANCE NAME="t{i}" TASKNAME="cmd" TASKTYPE="Command"/>' for i in range(size))
    body += ''.join(f'<WORKFLOWLINK FROMTASK="t{i}" TOTASK="t{i + 1}" CONDITION="TRUE"/>'
                    for i in range(size - 1))
    model = parse(tmp_path, body + '</WORKFLOW></FOLDER>')
    c = PlanningIndex(model).workflow_contract(identity(model, "workflow", "W"), limit=2)
    assert c["coverage"]["taskinstances"] == size
    assert c["control_links"]["total"] == size - 1
    assert c["task_cycles"]["total"] == 0
    assert c["tasks"]["returned"] == 2


def test_pure_deterministic_no_model_mutation_or_legacy_io(estate, monkeypatch):
    from pathlib import Path
    import socket

    before = deepcopy(estate)
    def forbidden(*args, **kwargs):
        raise AssertionError("Planning must not access filesystem or network")
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    index = PlanningIndex(estate)
    wid = identity(estate, "workflow", "W1")
    first = index.workflow_contract(wid)
    assert first == index.workflow_contract(wid) == PlanningIndex(deepcopy(estate)).workflow_contract(wid)
    first["workflow"]["attributes"]["value"]["NAME"] = "caller mutation"
    assert index.workflow_contract(wid)["workflow"]["attributes"]["value"]["NAME"] == "W1"
    assert estate == before
    json.dumps(index.estate_manifest(), allow_nan=False)
    json.dumps(index.workflow_contract(wid), allow_nan=False)