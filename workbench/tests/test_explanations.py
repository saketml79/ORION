"""Shared runtime/UI behavior explanation regressions, from actual XML parsing."""
import json

import pytest
from fastapi.testclient import TestClient

from workbench.app import _create_package_app
from workbench.explanations import explain_workflow, model_indexes
from workbench.understanding import analyze_package
from workbench.tests.test_agents import fake
from workbench.agents import build_agents, ROLES


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "sample.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <WORKFLOW NAME="Wrapper">
        <TASK NAME="Start" TYPE="Start"/>
        <TASK NAME="Launch" TYPE="Command"><VALUEPAIR NAME="invoke" VALUE="pmcmd startworkflow -nowait child"/></TASK>
        <TASK NAME="Success" TYPE="Email"><ATTRIBUTE NAME="Email Subject" VALUE="Dependency failed"/></TASK>
        <TASKINSTANCE NAME="Start" TASKNAME="Start" TASKTYPE="Start"/>
        <TASKINSTANCE NAME="Launch" TASKNAME="Launch" TASKTYPE="Command"/>
        <TASKINSTANCE NAME="Success" TASKNAME="Success" TASKTYPE="Email"/>
        <WORKFLOWLINK FROMTASK="Start" TOTASK="Launch" CONDITION="$check.TgtSuccessRows &lt;35"/>
        <WORKFLOWLINK FROMTASK="Start" TOTASK="Success" CONDITION="$check.TgtSuccessRows &gt;=35"/>
      </WORKFLOW>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return path


def test_explanation_uses_predicates_and_contents_not_names(source):
    model = analyze_package(source)
    nodes, outgoing = model_indexes(model)
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
    report = explain_workflow(nodes, outgoing, workflow["id"])
    text = json.dumps(report)
    assert "$check.TgtSuccessRows <35" in text
    assert "$check.TgtSuccessRows >=35" in text
    assert "Dependency failed" in text and "-nowait child" in text
    assert report["coverage"]["task_count"] == 3
    assert report["coverage"]["control_link_count"] == 2
    assert report["semantic_parity"] == "unverified"
    assert not report["generation_allowed"]
    assert not report["coverage"]["truncated"]
    bounded = explain_workflow(nodes, outgoing, workflow["id"], limit=1)
    assert bounded["coverage"]["truncated"]
    with pytest.raises(ValueError):
        explain_workflow(nodes, outgoing, "unknown")


def test_plain_language_overview_and_sql_preserve_evidence_boundaries(tmp_path):
        path = tmp_path / 'business.xml'
        path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
            <WORKFLOW NAME="Wrapper">
                <TASK NAME="Launch" TYPE="Command"><VALUEPAIR NAME="invoke" VALUE="pmcmd startworkflow -f F -nowait Child"/></TASK>
                <TASK NAME="Alert" TYPE="Email"><ATTRIBUTE NAME="Email Subject" VALUE="Dependency failed"/></TASK>
                <TASKINSTANCE NAME="Count" TASKNAME="Count" TASKTYPE="Session"/>
                <TASKINSTANCE NAME="Launch" TASKNAME="Launch" TASKTYPE="Command"/>
                <TASKINSTANCE NAME="Alert" TASKNAME="Alert" TASKTYPE="Email"/>
                <WORKFLOWLINK FROMTASK="Count" TOTASK="Launch" CONDITION="$Count.TgtSuccessRows &lt;35"/>
                <WORKFLOWLINK FROMTASK="Count" TOTASK="Alert" CONDITION="$Count.TgtSuccessRows &gt;=35"/>
            </WORKFLOW><WORKFLOW NAME="Child"/>
        </FOLDER></REPOSITORY></POWERMART>''', encoding='utf-8')
        model = analyze_package(path)
        nodes, outgoing = model_indexes(model)
        workflow = next(n for n in model['nodes'] if n['kind'] == 'workflow' and n['name'] == 'Wrapper')
        sql_id = 'sql-evidence'
        nodes[sql_id] = {'id': sql_id, 'kind': 'sesstransformationinst', 'name': 'Target', 'properties': [
                {'attributes': {'NAME': 'Post SQL', 'VALUE': "INSERT INTO t VALUES ('DELETE;'); DELETE FROM t; COMMIT;"}}]}
        outgoing[workflow['id']].append({'id': 'sql-edge', 'source': workflow['id'], 'target': sql_id, 'kind': 'contains'})
        report = explain_workflow(nodes, outgoing, workflow['id'])
        assert report['sections'][0]['title'] == 'What this workflow does'
        overview = ' '.join(report['sections'][0]['items'])
        assert 'Child in folder F, without waiting for it to finish' in overview
        assert 'fewer than 35 successful target rows' in overview
        assert 'at least 35 successful target rows' in overview
        assert 'Dependency failed' in overview
        assert 'not automatically a source-readiness check' in overview
        code = ' '.join(report['sections'][1]['items'])
        assert 'inserts rows, then deletes rows, then commits changes' in code
        assert 'lexical summary, not execution' in code
        assert sql_id in report['evidence_ids']


def test_redacted_connection_gap_is_not_reported_as_missing_unity_catalog_schema(source):
    model = analyze_package(source)
    nodes, outgoing = model_indexes(model)
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
    connection_id, unresolved_id = "a" * 64, "b" * 64
    nodes[connection_id] = {"id": connection_id, "kind": "connectionreference",
                            "name": "CONNECTIONREFERENCE", "attributes": {}}
    nodes[unresolved_id] = {"id": unresolved_id, "kind": "unresolved_reference",
                            "name": "[REDACTED]", "attributes": {
                                "status": "unresolved", "expected_kinds": ["connection"],
                                "status_reason": "connection_value_redacted"}}
    outgoing[workflow["id"]].append({"id": "c" * 64, "source": workflow["id"],
                                     "target": connection_id, "kind": "contains"})
    outgoing[connection_id].append({"id": "d" * 64, "source": connection_id,
                                    "target": unresolved_id, "kind": "connection_reference"})

    report = explain_workflow(nodes, outgoing, workflow["id"])
    text = json.dumps(report)
    assert "1 Informatica connection reference(s) are withheld in the selected" in text
    assert "cannot establish which, or what the original XML contained" in text
    assert "This is not evidence that a Unity Catalog schema or table is missing" in text
    assert "did not query Unity Catalog" in text
    assert "captured source/target field metadata" in text
    assert "captured source/target schemas" not in text


def test_current_connection_evidence_distinguishes_alias_from_withheld_endpoint(tmp_path):
        path = tmp_path / "connections.xml"
        path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
            <WORKFLOW NAME="W"><TASK NAME="T" TYPE="Command">
                <CONNECTIONREFERENCE CONNECTIONNAME="Oracle_Alias"/>
                <CONNECTIONREFERENCE CONNECTIONNAME="jdbc:oracle:thin:@private-host/db"/>
            </TASK><TASKINSTANCE NAME="T" TASKNAME="T" TASKTYPE="Command"/></WORKFLOW>
        </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
        model = analyze_package(path)
        nodes, outgoing = model_indexes(model)
        workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
        report = json.dumps(explain_workflow(nodes, outgoing, workflow["id"]))
        assert "1 Informatica connection reference(s) are withheld" in report
        assert "1 Informatica connection reference(s) have an external definition" in report
        assert "external alias is not a redaction" in report
        assert "private-host" not in report


def test_stale_redaction_reason_with_current_alias_is_not_counted(source):
        model = analyze_package(source)
        nodes, outgoing = model_indexes(model)
        workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
        nodes["connection"] = {"id": "connection", "kind": "connectionreference", "name": "ref",
                                                     "attributes": {"CONNECTIONNAME": "Oracle_Alias"}}
        nodes["stale"] = {"id": "stale", "kind": "unresolved_reference", "name": "[REDACTED]",
                                            "parent_id": "connection", "attributes": {"status_reason": "connection_value_redacted"}}
        outgoing[workflow["id"]].append({"source": workflow["id"], "target": "connection", "kind": "contains"})
        outgoing["connection"].append({"source": "connection", "target": "stale", "kind": "connection_reference"})
        report = json.dumps(explain_workflow(nodes, outgoing, workflow["id"]))
        assert "connection reference(s) are withheld" not in report
        assert "1 other unresolved package reference(s)" in report


def test_all_runtime_agents_have_same_service_and_citable_evidence(source, tmp_path):
    model = analyze_package(source)
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
    with build_agents(tmp_path / "agents", model, workflow["id"], "original", chat_model=fake()) as bundle:
        for role in ROLES:
            tools = bundle.tools[role]
            report = tools["workflow_explanation"].invoke({})
            assert "Dependency failed" in json.dumps(report)
            assert all(i in bundle.evidence.records for i in report["evidence_ids"])
            tools["submit_finding"].invoke({"finding": {
                "role": role, "summary": "Captured control behavior, not verified parity.",
                "evidence_ids": [workflow["id"]], "status": "needs_information"}})


def test_object_api_delivers_explanation(source, tmp_path):
    app = _create_package_app(tmp_path / "data", source)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        service = app.state.service
        service.package_slot.acquire()
        service._ingest({"id": "a" * 32, "status": "queued"}, source, "sample.xml", False)
        workflow = service.active.kinds["workflow"][0]
        response = client.get("/api/object/" + workflow["id"])
        assert response.status_code == 200
        assert response.json()["behavior_explanation"]["coverage"]["control_link_count"] == 2


def test_launch_schedule_does_not_confuse_task_timer_with_cadence(tmp_path):
        path = tmp_path / "cadence.xml"
        path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
            <WORKFLOW NAME="W" SCHEDULERNAME="Scheduler" ISENABLED="YES">
                <SCHEDULER NAME="Scheduler"><SCHEDULEINFO SCHEDULETYPE="ONDEMAND"/></SCHEDULER>
                <TASK NAME="Wait" TYPE="Timer"><TIMER TIMERTYPE="START_RELATIVE_TO_PREVIOUSTASK">
                    <RECURRING DAYS="0" HOURS="0" MINUTES="5"/>
                </TIMER></TASK>
                <TASKINSTANCE NAME="Wait" TASKNAME="Wait" TASKTYPE="Timer"/>
            </WORKFLOW>
        </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
        model = analyze_package(path)
        nodes, outgoing = model_indexes(model)
        workflow = next(n for n in model["nodes"] if n["kind"] == "workflow")
        report = explain_workflow(nodes, outgoing, workflow["id"])
        assert report["cadence"]["schedule_types"] == ["ONDEMAND"]
        assert not any(r["kind"] in {"timer", "recurring"} for r in report["cadence"]["records"])
        assert report["cadence"]["timezone"] == "unverified"
        assert not report["cadence"]["deployment_ready"]
        assert "On-demand launch" in json.dumps(report)