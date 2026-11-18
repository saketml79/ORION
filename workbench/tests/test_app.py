"""Offline HTTP/service regressions, including the complete 61MB source export.

No package installation, provider calls, legacy CLI execution, or deployment.
Only the agent transport uses a fake runtime; package tests use the real parser.
"""
from collections import Counter
import gc
import gzip
from hashlib import sha256
import inspect
import io
import json
from pathlib import Path
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from workbench.app import (
    MAX_DOCUMENT_BYTES, MAX_PACKAGE_BYTES, PackageIndex, _CACHE, _CACHE_LOCK, _PACKAGE_INDEX_CACHE,
    _read_model, create_app,
)
from workbench.agents import AgentRuntime
from workbench.domain import Store, build_proposals, impact_analysis


ROOT = Path(__file__).resolve().parents[2]
REAL_XML = ROOT / "input" / "LumenExample.XML"
HEADERS = {"X-Workbench-Request": "1"}


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50123))


def post(client, path, **kwargs):
    return client.post(path, headers={**HEADERS, **kwargs.pop("headers", {})}, **kwargs)


def wait_record(client, path, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in {"completed", "partial", "failed", "cancelled", "canceled"}:
            return record
        Event().wait(.02)
    pytest.fail(f"Job did not reach a terminal state within {timeout}s: {path}")


def load_sample(client):
    response = post(client, "/api/packages/sample")
    assert response.status_code == 202, response.text
    result = wait_record(client, "/api/jobs/" + response.json()["id"])
    assert result["status"] == "completed", result
    return result


@pytest.fixture
def small_xml(tmp_path):
    fields = "".join(f'<SOURCEFIELD NAME="c{i}" DATATYPE="integer"/>' for i in range(130))
    instances = "".join(f'<TASKINSTANCE NAME="r{i}" TASKNAME="S" TASKTYPE="Session" ISENABLED="NO"/>' for i in range(130))
    path = tmp_path / "sample.xml"
    path.write_text(f'''<!DOCTYPE POWERMART SYSTEM "http://127.0.0.1:9/no.dtd">
    <POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <SOURCE NAME="Src">{fields}</SOURCE><TARGET NAME="Tgt"><TARGETFIELD NAME="z" DATATYPE="integer"/></TARGET>
    <MAPPING NAME="M"><INSTANCE NAME="a" TRANSFORMATION_NAME="Src" TRANSFORMATION_TYPE="Source Definition"/>
    <INSTANCE NAME="b" TRANSFORMATION_NAME="Tgt" TRANSFORMATION_TYPE="Target Definition"/>
    <CONNECTOR FROMINSTANCE="a" FROMFIELD="c0" TOINSTANCE="b" TOFIELD="z"/></MAPPING>
    <SESSION NAME="S" MAPPINGNAME="M"/>
    <WORKFLOW NAME="W">{instances}<WORKFLOWLINK FROMTASK="r0" TOTASK="r1" CONDITION="TRUE"/></WORKFLOW>
    <WORKFLOW NAME="Shared"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return path


@pytest.fixture
def ready(tmp_path, small_xml, monkeypatch):
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    app = create_app(tmp_path / "data", small_xml)
    with client_for(app) as client:
        assert not client.get("/api/overview").json()["ready"]
        load_sample(client)
        yield client, app


def test_factory_health_static_no_startup_ingestion(tmp_path, small_xml, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No startup ingestion or model execution is allowed")
    monkeypatch.setattr("workbench.app.analyze_package", forbidden)
    monkeypatch.setattr(AgentRuntime, "run", forbidden)
    app = create_app(tmp_path / "data", small_xml)
    with client_for(app) as client:
        health = client.get("/api/health").json()
        assert health["local_only"]
        assert health["capabilities"]["authentication"]["status"] == "local_only"
        assert health["capabilities"]["authentication"]["available"] is None
        assert len(health["capabilities"]["generation"]["supported_workflows"]) == 4
        assert health["generation_allowed"] is False and health["deployment_allowed"] is False
        overview = client.get("/api/overview").json()
        assert health["capability_inventory"]["version"] == overview["capability_inventory"]["version"] == "capability-inventory-v1"
        assert health["capability_inventory"]["items"] == overview["capability_inventory"]["items"]
        assert len(overview["capability_inventory"]["items"]) == 17
        assert {item["key"] for item in overview["capability_inventory"]["items"]} >= {
            "oracle_source_exploration", "model_cost_estimates"
        }
        assert overview["ready"] is False and overview["package"] is None
        assert overview["capabilities"]["deployment"] is False
        assert client.get("/api/objects").status_code == 409
        assert client.get("/api/export/understanding").status_code == 409
        assert client.get("/").status_code == 200
        for path in ("/static/app.js", "/static/app.css"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
        for path in ("/api/generate", "/api/deploy", "/api/publish"):
            assert post(client, path).status_code == 404
        assert post(client, "/api/jobs/" + "a" * 32 + "/spark-drafts/run", json={}).status_code == 422
        assert client.get("/api/jobs/" + "a" * 32 + "/spark-drafts/download?path=notebooks/t_wait.ipynb").status_code == 404
        assert list((tmp_path / "data" / "packages").iterdir()) == []


def test_capabilities_report_bounded_compiler_without_spoofing_hosted_auth(tmp_path, small_xml, monkeypatch):
    from workbench.workflow_artifacts import supported_workflows
    monkeypatch.setenv("WORKBENCH_TRUST_DATABRICKS_PROXY", "1")
    monkeypatch.setattr(AgentRuntime, "run", lambda *a, **k: pytest.fail("No model calls"))
    app = create_app(tmp_path / "data", small_xml)
    with client_for(app) as client:
        result = client.get("/api/overview").json()
        caps = result["capabilities"]
        generation = caps["generation"]
        assert generation["available"] is True
        assert generation["scenario"] == "bronze"
        assert generation["supported_workflows"] == supported_workflows()
        assert len(generation["supported_workflows"]) == 4
        assert generation["general_codegen_available"] is False
        assert caps["general_code_generation"] is False
        assert caps["authentication"]["available"] is None
        assert caps["authentication"]["status"] == "local_only"
        assert caps["authentication"]["scope"] == "local_loopback_boundary"
        assert caps["hosted_application"]["available"] is False
        assert caps["model_inference_authentication"]["available"] is None
        assert caps["agents"]["inference_verified"] is False
        assert caps["semantic_translation"]["available"] is False
        assert caps["deployment"] is False and caps["github_publication"] is False
        for record in (result, generation):
            assert record["generation_allowed"] is False
            assert record["deployment_allowed"] is False


def test_verified_package_index_is_reused_across_disposable_request_directories(ready, tmp_path, monkeypatch):
    import shutil
    import workbench.app as app_module

    _, app = ready
    original = app.state.service.active.directory
    package_hash = app.state.service.active.model["package"]["sha256"]
    first = tmp_path / "request-a" / package_hash
    second = tmp_path / "request-b" / package_hash
    first.parent.mkdir()
    second.parent.mkdir()
    shutil.copytree(original, first)
    shutil.copytree(original, second)
    with app_module._CACHE_LOCK:
        app_module._CACHE.clear()
        app_module._PACKAGE_INDEX_CACHE.clear()
    reads = []
    original_reader = app_module._read_model

    def counted(path, *args, **kwargs):
        reads.append(Path(path).resolve())
        return original_reader(path, *args, **kwargs)

    monkeypatch.setattr(app_module, "_read_model", counted)
    index_a = app_module.PackageIndex.load(first)
    index_b = app_module.PackageIndex.load(second)
    assert len(reads) == 1
    assert index_a.model is index_b.model
    assert index_a.nodes is index_b.nodes and index_a.out is index_b.out
    assert index_a.directory == first.resolve()
    assert index_b.directory == second.resolve()


def test_security_loopback_host_origin_and_mutation_header(tmp_path, small_xml):
    app = create_app(tmp_path / "data", small_xml)
    with client_for(app) as client:
        assert client.post("/api/packages/sample").status_code == 403
        assert post(client, "/api/packages/sample", headers={"Origin": "http://evil.example"}).status_code == 403
        assert post(client, "/api/packages/sample", headers={"Origin": "null"}).status_code == 403
        assert post(client, "/api/packages/sample", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
        assert post(client, "/api/packages/sample", headers={"Origin": "http://127.0.0.1"}).status_code == 202
    with TestClient(create_app(tmp_path / "remote", small_xml), base_url="http://127.0.0.1", client=("203.0.113.8", 1)) as remote:
        assert remote.get("/api/health").status_code == 403


def test_objects_fields_children_graphs_are_bounded_and_original(ready):
    client, app = ready
    inventory = client.get("/api/objects?kind=workflow&q=W&offset=0&limit=1").json()
    assert inventory["total"] == 1 and len(inventory["items"]) == 1
    workflow = inventory["items"][0]
    detail = client.get("/api/object/" + workflow["id"]).json()
    assert detail["children_total"] == 130
    assert len(detail["children"]) == 100 and detail["children_truncated"]
    assert len(detail["graph"]["nodes"]) <= 100 and detail["graph_truncated"]
    assert detail["graph"]["field_nodes_traversed"] is False
    assert all(n["kind"] != "instance_field" for n in detail["graph"]["nodes"])
    page = client.get("/api/object/" + workflow["id"] + "?offset=100&limit=100").json()
    assert len(page["children"]) == 30
    source = client.get("/api/objects?kind=source").json()["items"][0]
    source_detail = client.get("/api/object/" + source["id"]).json()
    assert len(source_detail["node"]["fields"]) == 100
    assert source_detail["node"]["fields_total"] == 130 and source_detail["node"]["fields_truncated"]
    assert len(app.state.service.active.nodes[source["id"]]["fields"]) == 130
    mapping = client.get("/api/objects?kind=mapping").json()["items"][0]
    mapping_detail = client.get("/api/object/" + mapping["id"]).json()
    assert any(e["kind"] == "data_flow" for e in mapping_detail["graph"]["edges"])
    assert all(n["kind"] != "instance_field" for n in mapping_detail["graph"]["nodes"])
    for query in ("limit=101", "limit=0", "offset=-1"):
        assert client.get("/api/objects?" + query).status_code == 422
        assert client.get("/api/object/" + source["id"] + "?" + query).status_code == 422
    assert client.get("/api/object/not-an-id").status_code == 404


def test_lite_proposals_domain_parity_review_versions_and_impact(ready):
    client, app = ready
    index = app.state.service.active
    assert index.proposals == {}
    expected = build_proposals(index.model)
    response = client.get("/api/proposals")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for actual, full in zip(items, expected):
        for key in ("id", "workflow_id", "scenario", "blockers", "generation_allowed", "deployment_allowed"):
            assert actual[key] == full[key]
        assert actual["coverage"]["scoped_objects"] == full["coverage"]["scoped_objects"]
    proposal = items[0]
    detail = client.get("/api/proposals/" + proposal["id"]).json()
    assert len(detail["graph"]["nodes"]) <= 100
    assert len(detail["evidence"]) <= 50
    cached = index.proposals["original"]
    assert client.get("/api/proposals").status_code == 200 and index.proposals["original"] is cached
    state = app.state.service.store.load_state(index.model["package"]["sha256"])
    assert all("graph" not in p and "evidence" not in p for p in state["proposals"])
    response = post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1, "action": "comment", "comment": "password=not-for-storage"})
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["review"]["comment"] == "[REDACTED]"
    assert response.json()["actor_authenticated"] is False
    assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1, "action": "comment", "comment": "conflict"}).status_code == 409
    assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 2, "action": "approve", "comment": "approve"}).status_code == 409
    effective = client.get("/api/proposals/" + proposal["id"]).json()
    assert effective["version"] == 2 and not effective["generation_allowed"]
    reviews = client.get("/api/reviews").json()
    assert reviews["total"] == 1
    assert client.get("/api/proposals?scenario=other").status_code == 422
    page = client.get("/api/proposals", params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 2 and page["offset"] == 1 and len(page["items"]) == 1
    selected = client.get("/api/proposals", params={"workflow_id": proposal["workflow_id"]}).json()
    assert selected["total"] == 1 and selected["items"][0]["workflow_id"] == proposal["workflow_id"]
    actual_impact = client.get("/api/impact/" + proposal["workflow_id"]).json()
    assert actual_impact == impact_analysis(index.model, proposal["workflow_id"])
    assert client.get("/api/impact/not-a-workflow").status_code == 404


def test_redacted_informatica_connection_is_not_a_unity_catalog_schema_failure(ready):
    client, app = ready
    index = app.state.service.active
    workflow_id = index.kinds["workflow"][0]["id"]
    connection_id, unresolved_id = "a" * 64, "b" * 64
    index.nodes[connection_id] = {"id": connection_id, "kind": "connectionreference",
                                  "name": "CONNECTIONREFERENCE", "attributes": {}, "evidence": {}}
    index.nodes[unresolved_id] = {"id": unresolved_id, "kind": "unresolved_reference",
                                  "name": "[REDACTED]", "attributes": {
                                      "status": "unresolved", "expected_kinds": ["connection"],
                                      "status_reason": "connection_value_redacted"}, "evidence": {}}
    index.out[workflow_id].append({"id": "c" * 64, "source": workflow_id,
                                   "target": connection_id, "kind": "contains"})
    index.out[connection_id].append({"id": "d" * 64, "source": connection_id,
                                     "target": unresolved_id, "kind": "connection_reference"})

    proposal = next(p for p in app.state.service.proposals(index, "original")
                    if p["workflow_id"] == workflow_id)
    assert proposal["coverage"]["redacted_connection_count"] == 1
    assert any("connection value(s) are redacted in the XML" in blocker
               and "no Unity Catalog lookup was performed" in blocker
               for blocker in proposal["blockers"])
    assert not any("schema failure" in blocker.lower() for blocker in proposal["blockers"])
    assert client.get("/api/proposals?limit=1").status_code == 200


def test_real_wil_net_dis_quality_gate_rejects_misleading_task_translation(ready):
    from workbench.agents import _Evidence
    from workbench.redesign import DesignQualityError, validate_submission

    def node(identifier, kind, name, parent=None, **attributes):
        return {"id": identifier, "kind": kind, "name": name, "parent_id": parent,
                "scope": {}, "attributes": attributes, "evidence": {"locator": "/" + name},
                "fields": [], "properties": []}

    workflow = node("wf", "workflow", "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS")
    originals = [node("start", "taskinstance", "Start", "wf", TASKTYPE="Start"),
                 node("timer", "taskinstance", "tmr_WAIT_FOR_DEPENDENCY", "wf", TASKTYPE="Timer"),
                 node("session", "taskinstance", "s_m_AE2E_JOB_CHECK_COUNTER", "wf", TASKTYPE="Session"),
                 node("command", "taskinstance", "cmd_AE2E_CALL_WILTEL_NET_DIS_PULL", "wf", TASKTYPE="Command"),
                 node("email", "taskinstance", "eml_on_SUCCESS", "wf", TASKTYPE="Email")]
    command_definition = node("command_def", "task", "cmd_AE2E_CALL_WILTEL_NET_DIS_PULL")
    command_definition["properties"] = [{"attributes": {"NAME": "command", "VALUE":
        "pmcmd startworkflow -nowait wkf_Load_AE2E_WILTEL_NETWORK_DISCO"}}]
    connection = node("connection", "connectionreference", "CONNECTIONREFERENCE")
    unresolved = node("unresolved", "unresolved_reference", "[REDACTED]",
                      status="unresolved", status_reason="connection_value_redacted")
    scheduler = node("scheduler", "scheduler", "Scheduler")
    edges = [{"id": "contains-" + n["id"], "source": "wf", "target": n["id"],
              "kind": "contains", "attributes": {}, "evidence": {}} for n in originals]
    edges += [
        {"id": "definition", "source": "command", "target": "command_def", "kind": "task_definition",
         "attributes": {}, "evidence": {}},
        {"id": "contains-connection", "source": "wf", "target": "connection", "kind": "contains",
         "attributes": {}, "evidence": {}},
        {"id": "connection-link", "source": "connection", "target": "unresolved", "kind": "connection_reference",
         "attributes": {}, "evidence": {}},
        {"id": "schedule-link", "source": "wf", "target": "scheduler", "kind": "schedule_reference",
         "attributes": {}, "evidence": {}},
        {"id": "start-timer", "source": "start", "target": "timer", "kind": "workflow_control",
         "attributes": {}, "evidence": {}},
        {"id": "timer-session", "source": "timer", "target": "session", "kind": "workflow_control",
         "attributes": {}, "evidence": {}},
        {"id": "session-email", "source": "session", "target": "email", "kind": "workflow_control",
         "attributes": {"CONDITION": "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows >=35"}, "evidence": {}},
        {"id": "session-command", "source": "session", "target": "command", "kind": "workflow_control",
         "attributes": {"CONDITION": "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows <35"}, "evidence": {}},
    ]
    model = {"package": {"sha256": "test-package"}, "nodes": [workflow, *originals,
             command_definition, connection, unresolved, scheduler], "edges": edges, "diagnostics": [], "coverage": {}}
    evidence = _Evidence(model, "wf", [], [])
    task_ids = [node["id"] for node in originals]
    design = {
        "title": "Intentionally misleading one-task mapping",
        "tasks": [{"id": "all-in-one", "label": "All workflow tasks",
                   "implementation_kind": "notebook_task", "purpose": "Handle workflow behavior",
                   "evidence_ids": task_ids}],
        "edges": [],
        "rationale": "Test only: this design intentionally collapses incompatible task behavior.",
        "alternatives": [{"label": "Keep current workflow", "rationale": "Comparison only",
                          "tradeoffs": ["No modernization"], "evidence_ids": [workflow["id"]]}],
        "coverage": [{"original_task_id": node["id"], "proposed_task_ids": ["all-in-one"],
                      "disposition": "mapped", "rationale": "Intentional invalid test mapping",
                      "evidence_ids": [node["id"]]} for node in originals],
        "evidence_ids": task_ids,
    }
    with pytest.raises(DesignQualityError) as caught:
        validate_submission(design, model, "wf", set(evidence.records), evidence.records)
    fields = {item["field"] for item in caught.value.issues}
    assert "coverage.Start" in fields
    assert "cadence" in fields
    assert "edges" in fields
    assert "scoped_unresolved.eml_on_SUCCESS" in fields
    assert "scoped_unresolved.cmd_AE2E_CALL_WILTEL_NET_DIS_PULL" in fields
    assert "scoped_unresolved" in fields
    assert "artifact_plan" in fields


def test_exports_are_full_artifacts_restart_reads_gzip_once(ready):
    client, app = ready
    index = app.state.service.active
    # Keep this identity assertion scoped to one artifact. The canonical index
    # cache is intentionally one-entry-bounded across the full test process.
    with _CACHE_LOCK:
        _PACKAGE_INDEX_CACHE.clear()
    response = client.get("/api/export/understanding")
    assert response.status_code == 200 and response.headers["content-type"] == "application/gzip"
    assert "understanding.json.gz" in response.headers["content-disposition"]
    exported = json.loads(gzip.decompress(response.content))
    assert exported == index.model
    assert next(n for n in exported["nodes"] if n["kind"] == "source")["fields"][-1]["attributes"]["NAME"] == "c129"
    assert client.get("/api/export/english").content == (index.directory / "english.md").read_bytes()
    # Force a true disk read, not only a same-process cache hit.
    with _CACHE_LOCK:
        _CACHE.pop(str(index.directory.resolve()), None)
    restarted = create_app(app.state.service.root, app.state.service.sample)
    with client_for(restarted) as second:
        assert second.get("/api/overview").json()["ready"]
        loaded = restarted.state.service.active
        assert loaded.model == index.model
        third_app = create_app(app.state.service.root, app.state.service.sample)
        with client_for(third_app):
            third = third_app.state.service.active
            assert third.model == loaded.model
            assert third.nodes == loaded.nodes and third.out == loaded.out


@pytest.mark.parametrize("name,content,status", [
    ("package.zip", b"PK\x03\x04", 415), ("../../outside.xml", b"<POWERMART/>", 400),
    ("C:\\outside.xml", b"<POWERMART/>", 400), ("empty.xml", b"", 400),
])
def test_package_upload_validation_no_paths(ready, name, content, status):
    client, app = ready
    response = post(client, "/api/packages/upload", files={"file": (name, content, "application/octet-stream")})
    assert response.status_code == status, response.text
    assert not list((app.state.service.state / "uploads").iterdir())
    assert not (app.state.service.root.parent / "outside.xml").exists()


@pytest.mark.parametrize("content", [b"<POWERMART><broken>", b"<!DOCTYPE POWERMART [<!ENTITY x 'boom'>]><POWERMART>&x;</POWERMART>", b"<not_informatica/>"])
def test_malformed_upload_fails_job_without_partial_activation(ready, content):
    client, app = ready
    original = client.get("/api/overview").json()["package"]["sha256"]
    response = post(client, "/api/packages/upload", files={"file": ("bad.xml", content, "application/xml")})
    assert response.status_code == 202, response.text
    record = wait_record(client, "/api/jobs/" + response.json()["id"])
    assert record["status"] == "failed"
    assert client.get("/api/overview").json()["package"]["sha256"] == original
    assert not list(app.state.service.packages.glob(".stage-*"))
    assert not list((app.state.service.state / "uploads").iterdir())


def test_upload_byte_limits_before_multipart_and_streamed(ready, monkeypatch):
    client, app = ready
    assert post(client, "/api/packages/upload", content=b"x", headers={"Content-Length": str(MAX_PACKAGE_BYTES + 65537)}).status_code == 413
    assert post(client, "/api/documents", content=b"x", headers={"Content-Length": str(MAX_DOCUMENT_BYTES + 65537)}).status_code == 413
    monkeypatch.setattr("workbench.app.MAX_PACKAGE_BYTES", 128)
    response = post(client, "/api/packages/upload", files={"file": ("large.xml", b"x" * 256)})
    assert response.status_code == 413
    assert not list((app.state.service.state / "uploads").iterdir())
    # No Content-Length: middleware must still bound a chunked body.
    response = post(client, "/api/packages/upload", content=iter([b"x" * 70000]), headers={"Content-Type": "multipart/form-data; boundary=a"})
    assert response.status_code == 413, response.text


def test_documents_redaction_bounded_text_raw_hash_and_staleness(ready):
    client, app = ready
    proposals = client.get("/api/proposals").json()["items"]
    workflow = proposals[0]["workflow_id"]
    raw = b"password=never-persist-in-text\n" + b"Owner evidence\n" * 400
    response = post(client, "/api/documents", files={"file": ("owner.md", raw)}, data={"workflow_id": workflow, "description": "token=private-description"})
    assert response.status_code == 201, response.text
    record = response.json()
    assert "never-persist-in-text" not in json.dumps(record)
    assert "private-description" not in json.dumps(record)
    assert record["text_length"] > 4000 and record["text_truncated"]
    assert len(record["text"]) == 4000
    assert record["metadata"]["sha256"] == sha256(raw).hexdigest()
    raw_path = app.state.service.active.directory / "evidence" / record["metadata"]["stored_name"]
    assert raw_path.name == sha256(raw).hexdigest() + ".md" and raw_path.read_bytes() == raw
    assert client.get("/api/documents").json()["total"] == 1
    items = client.get("/api/proposals").json()["items"]
    affected_summary = next(p for p in items if p["workflow_id"] == workflow)
    unaffected_summary = next(p for p in items if p["workflow_id"] != workflow)
    affected = client.get(f"/api/proposals/{affected_summary['id']}").json()
    unaffected = client.get(f"/api/proposals/{unaffected_summary['id']}").json()
    assert affected["stale"] and not unaffected["stale"]
    assert affected["blockers"] == proposals[0]["blockers"]
    response = post(client, "/api/attestations", json={"workflow_id": workflow, "field": "lifecycle", "value": "active", "comment": "Owner says active"})
    assert response.status_code == 201
    assert response.json()["metadata"]["verification_status"] == "unverified"
    assert response.json()["blockers_cleared"] is False
    assert post(client, "/api/attestations", json={"workflow_id": workflow, "field": "approve", "value": "yes"}).status_code == 422
    assert post(client, "/api/attestations", json={"workflow_id": workflow, "field": "lifecycle", "value": "verified"}).status_code == 422
    assert post(client, "/api/documents", files={"file": ("../../x.md", b"text")}).status_code == 400
    assert post(client, "/api/documents", files={"file": ("x.exe", b"text")}).status_code == 415
    assert post(client, "/api/documents", files={"file": ("x.txt", b"\xff\xfe")}).status_code == 422


def test_pdf_failures_are_explicit(ready):
    from pypdf import PdfWriter
    client, app = ready
    for encrypted in (False, True):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        if encrypted:
            writer.encrypt("not-a-real-password")
        data = io.BytesIO()
        writer.write(data)
        response = post(client, "/api/documents", files={"file": ("evidence.pdf", data.getvalue(), "application/pdf")})
        assert response.status_code == 422
        assert ("Encrypted" if encrypted else "No text") in response.json()["detail"]
    response = post(client, "/api/documents", files={"file": ("bad.pdf", b"not a PDF")})
    assert response.status_code == 422 and "Invalid PDF" in response.json()["detail"]
    assert client.get("/api/documents").json()["total"] == 0
    assert not list((app.state.service.state / "uploads").iterdir())


def test_safe_store_settings_and_secret_rejection(ready):
    client, app = ready
    assert client.get("/api/settings").json()["settings"] == {}
    response = post(client, "/api/settings", json={"github_owner": "example", "github_repo": "migration", "host": "https://example.databricks.com"})
    assert response.status_code == 200
    assert response.json()["connectivity_verified"] is False
    for values in ({"token": "never-save"}, {"github_repo": "token=never-save"}, {"host": "https://user:password@host/"},
                   {"host": "http://host"}, None, []):
        response = post(client, "/api/settings", json=values)
        assert response.status_code == 422, response.text
    assert client.get("/api/settings").json()["settings"]["github_repo"] == "migration"


def test_settings_api_advertises_parent_owned_expanded_store_contract(ready, monkeypatch):
    client, app = ready
    keys = {"github_owner", "github_repo", "host", "default_branch", "dev_host", "test_host", "prod_host"}
    assert set(client.get("/api/settings").json()["allowed_keys"]) == keys
    values = {"default_branch": "main", "dev_host": "https://dev.example.com",
              "test_host": "https://test.example.com", "prod_host": "https://prod.example.com"}
    # Domain validation belongs to the parent change. Verify the adapter forwards
    # the exact expanded contract, rather than duplicating Store validation here.
    calls = []
    def settings(package_hash, patch=None):
        calls.append(patch)
        return patch or {}
    monkeypatch.setattr(app.state.service.store, "settings", settings)
    response = post(client, "/api/settings", json=values)
    assert response.status_code == 200 and response.json()["settings"] == values
    assert set(response.json()["allowed_keys"]) == keys
    assert calls == [values]
    assert post(client, "/api/settings", json={"token": "not-allowed"}).status_code == 422
    assert calls == [values]


def test_existing_package_activates_verified_disk_not_reparsed_model(ready, monkeypatch):
    client, app = ready
    original = app.state.service.active
    expected = json.loads(gzip.decompress(client.get("/api/export/understanding").content))
    manifest = json.loads((original.directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_version"]
    assert manifest["analyzer_sha256"]
    from workbench.understanding import analyze_package
    def different_parser(path):
        model = analyze_package(path)
        model["summary"] = "NEW PARSER MUST NOT REPLACE IMMUTABLE DISK MODEL"
        model["nodes"].pop()
        return model
    monkeypatch.setattr("workbench.app.analyze_package", different_parser)
    with _CACHE_LOCK:
        _CACHE.pop(str(original.directory.resolve()), None)
    result = load_sample(client)
    assert app.state.service.active.model == expected
    assert result["node_count"] == len(expected["nodes"])
    assert json.loads((original.directory / "manifest.json").read_text(encoding="utf-8")) == manifest
    assert not list(app.state.service.packages.glob(".stage-*"))


def test_existing_package_corruption_never_activates_reparse(ready):
    client, app = ready
    original = app.state.service.active
    path = original.directory / "understanding.json.gz"
    path.write_bytes(path.read_bytes() + b"corrupt")
    response = post(client, "/api/packages/sample")
    result = wait_record(client, "/api/jobs/" + response.json()["id"])
    assert result["status"] == "failed"
    assert app.state.service.active is original
    assert not list(app.state.service.packages.glob(".stage-*"))


def test_provisional_artifacts_are_atomic_immutable_and_restart_persistent(ready, monkeypatch):
    client, app = ready
    index = app.state.service.active
    for scenario in ("original", "bronze"):
        response = client.get("/api/proposals", params={"scenario": scenario})
        assert response.status_code == 200
        path = index.directory / f"proposals-{scenario}.json"
        before = path.read_bytes()
        drafts = json.loads(before)
        assert drafts == index.proposals[scenario]
        assert all(p["version"] == 1 and p["status"] == "provisional" for p in drafts)
        proposal = drafts[0]
        assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1,
                                                 "action": "reject", "comment": "Not approved"}).status_code == 200
        assert client.get("/api/proposals", params={"scenario": scenario}).status_code == 200
        assert path.read_bytes() == before
        restarted_index = PackageIndex(index.model, index.directory)
        def forbidden(*args):
            pytest.fail("Persisted structural drafts must not be regenerated")
        monkeypatch.setattr(restarted_index, "walk", forbidden)
        assert restarted_index.proposal_summaries(scenario) == drafts
        assert path.read_bytes() == before
    assert not list(index.directory.glob("*.tmp"))


def test_provisional_artifact_failed_replace_leaves_no_partial_file(ready, monkeypatch):
    client, app = ready
    import workbench.app as adapter
    original_replace = adapter.os.replace
    def fail_proposal_replace(source, destination):
        if Path(destination).name == "proposals-original.json":
            raise OSError("Offline injected write failure")
        return original_replace(source, destination)
    monkeypatch.setattr(adapter.os, "replace", fail_proposal_replace)
    index = app.state.service.active
    with pytest.raises(OSError, match="injected write failure"):
        app.state.service.lite_records(index, "original")
    assert "original" not in index.proposals
    assert not (index.directory / "proposals-original.json").exists()
    assert not list(index.directory.glob("*.tmp"))
    monkeypatch.setattr(adapter.os, "replace", original_replace)
    assert client.get("/api/proposals").status_code == 200


def test_review_response_is_committed_snapshot_not_later_revision(ready, monkeypatch):
    client, app = ready
    proposal = client.get("/api/proposals").json()["items"][0]
    original_save = app.state.service.store.save_review
    def racing_review(*args, **kwargs):
        committed = original_save(*args, **kwargs)
        original_save(args[0], proposal["id"], committed["version"], "reject", "Later writer")
        return committed
    monkeypatch.setattr(app.state.service.store, "save_review", racing_review)
    response = post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1,
                                                "action": "comment", "comment": "First writer"})
    committed = response.json()
    assert response.status_code == 200
    assert committed["version"] == committed["review"]["version"] == 2
    assert committed["status"] == committed["review"]["status"] == "provisional"
    current = client.get("/api/proposals/" + proposal["id"]).json()
    assert current["version"] == 3 and current["status"] == "rejected"


def test_reviews_proposal_filter_precedes_pagination(ready):
    client, app = ready
    first, second = client.get("/api/proposals").json()["items"]
    for proposal, version in ((first, 1), (second, 1), (first, 2)):
        assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": version,
                                                 "action": "comment", "comment": str(version)}).status_code == 200
    response = client.get("/api/reviews", params={"proposal_id": first["id"], "offset": 1, "limit": 1})
    page = response.json()
    assert page["total"] == 2 and len(page["items"]) == 1 and not page["truncated"]
    assert page["items"][0]["proposal_id"] == first["id"] and page["items"][0]["version"] == 3
    assert client.get("/api/reviews", params={"proposal_id": "f" * 64}).json()["total"] == 0
    assert client.get("/api/reviews", params={"proposal_id": "bad"}).status_code == 422


class FakeRuntime:
    def __init__(self):
        self.started, self.release, self.called = Event(), Event(), []

    def status(self):
        return {"available": True, "roles": ["supervisor"], "reason": "offline injected test runtime"}

    def run(self, model, workflow_id, scenario, prompt, documents, feedback, *, cancellation_event=None,
            progress_callback=None):
        self.called.append((model, workflow_id, scenario, prompt, documents, feedback))
        self.started.set()
        assert self.release.wait(10)
        cited_workflow = next(node for node in model["nodes"] if node["id"] == workflow_id)
        cited_edge = next(edge for edge in model["edges"] if edge["kind"] == "workflow_control")
        return {"status": "needs_information", "summary": "Offline wiring test only", "blockers": ["Unknown lifecycle"], "generation_allowed": True,
            "findings": [{"role": "supervisor", "status": "needs_information", "summary": "Inspect this workflow.",
                      "evidence_ids": [cited_workflow["id"], cited_edge["id"]]}]}


def queue_offline_agent(client, app, monkeypatch, workflow_id, scenario="original"):
    runtime = FakeRuntime()
    runtime.release.set()
    app.state.service.runtime = runtime
    monkeypatch.setenv("WORKBENCH_MODEL", "openai:offline-not-invoked")
    queued = []
    monkeypatch.setattr(app.state.service.agent_executor, "submit", lambda fn, *args: queued.append((fn, args)))
    response = post(client, "/api/agents/run", json={"workflow_id": workflow_id, "scenario": scenario, "consent": True})
    assert response.status_code == 202, response.text
    return response.json(), runtime, queued


def test_agent_scoped_immutable_capture_occurs_before_worker_submission(ready, monkeypatch):
    client, app = ready
    service = app.state.service
    selected, unrelated = client.get("/api/proposals").json()["items"]
    bronze = next(p for p in client.get("/api/proposals?scenario=bronze").json()["items"]
                  if p["workflow_id"] == selected["workflow_id"])
    package_hash = service.active.model["package"]["sha256"]
    # Missing workflow metadata means package-wide evidence. Blank workflow ID
    # is also the normal upload representation for package-wide evidence.
    global_doc = service.store.save_document(package_hash, "global.md", "Global context", {})
    blank_doc = service.store.save_document(package_hash, "blank.md", "Shared context", {"workflow_id": ""})
    selected_doc = service.store.save_document(package_hash, "selected.md", "Selected context", {"workflow_id": selected["workflow_id"]})
    secret_doc = service.store.save_document(package_hash, "unrelated.md", "UNRELATED PRIVATE EVIDENCE", {"workflow_id": unrelated["workflow_id"]})
    for proposal in (selected, unrelated, bronze):
        assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1,
                                                 "action": "comment", "comment": proposal["name"]}).status_code == 200
    captured, runtime, queued = queue_offline_agent(client, app, monkeypatch, selected["workflow_id"])
    assert not runtime.called
    assert len(captured["input_version"]) == 64
    assert captured["input_versions"]["proposals"] == {selected["id"]: 2}
    assert captured["input_hashes"]["policy"] and captured["input_hashes"]["skills"]
    path = "/api/agents/runs/" + captured["id"]
    assert not client.get(path).json()["stale"]
    later_doc = service.store.save_document(package_hash, "later.md", "Added while queued", {"workflow_id": selected["workflow_id"]})
    assert post(client, "/api/reviews", json={"proposal_id": selected["id"], "expected_version": 2,
                                             "action": "reject", "comment": "Queued invalidation"}).status_code == 200
    assert client.get(path).json()["stale"]
    # Worker execution must not query live Store documents/reviews at all.
    def forbidden(*args):
        pytest.fail("Worker accessed live mutable evidence")
    monkeypatch.setattr(service.store, "documents", forbidden)
    monkeypatch.setattr(service.store, "get_reviews", forbidden)
    monkeypatch.setattr(service.store, "load_state", forbidden)
    fn, args = queued[0]
    fn(*args)
    call = runtime.called[0]
    assert {d["id"] for d in call[4]} == {global_doc["id"], blank_doc["id"], selected_doc["id"]}
    assert secret_doc["id"] not in {d["id"] for d in call[4]}
    assert later_doc["id"] not in {d["id"] for d in call[4]}
    assert len(call[5]) == 1 and call[5][0]["proposal_id"] == selected["id"]
    assert call[5][0]["version"] == 2 and call[5][0]["action"] == "comment"
    terminal = service.record(captured["id"], agent=True)
    assert terminal["status"] == "completed" and terminal["input_version"] == captured["input_version"]


@pytest.mark.parametrize("change,stale", [
    ("selected_document", True), ("global_document", True), ("selected_reject", True),
    ("selected_rework", True), ("proposal_only", True), ("policy", True), ("skill", True),
    ("unrelated_document", False), ("unrelated_feedback", False), ("other_scenario", False),
])
def test_run_freshness_uses_same_scoped_input_version(ready, monkeypatch, tmp_path, change, stale):
    client, app = ready
    service = app.state.service
    selected, unrelated = client.get("/api/proposals").json()["items"]
    bronze = next(p for p in client.get("/api/proposals?scenario=bronze").json()["items"]
                  if p["workflow_id"] == selected["workflow_id"])
    import workbench.agents as agents
    skills = tmp_path / "skills"
    skills.mkdir()
    skill = skills / "skill.md"
    skill.write_text("First skill version", encoding="utf-8")
    monkeypatch.setattr(agents, "SKILLS_ROOT", skills)
    captured, runtime, queued = queue_offline_agent(client, app, monkeypatch, selected["workflow_id"])
    fn, args = queued[0]
    fn(*args)
    path = "/api/agents/runs/" + captured["id"]
    assert not client.get(path).json()["stale"]
    package_hash = service.active.model["package"]["sha256"]
    if change.endswith("document"):
        metadata = {} if change == "global_document" else {"workflow_id": (unrelated if change == "unrelated_document" else selected)["workflow_id"]}
        service.store.save_document(package_hash, "new.md", "New evidence", metadata)
    elif change == "policy":
        monkeypatch.setattr(agents, "_POLICY", agents._POLICY + " Additional offline policy.")
    elif change == "skill":
        skill.write_text("Second skill version", encoding="utf-8")
    elif change == "proposal_only":
        original_load = service.store.load_state
        def changed_proposal(package_hash):
            state = original_load(package_hash)
            next(p for p in state["proposals"] if p["id"] == selected["id"])["version"] += 1
            return state
        monkeypatch.setattr(service.store, "load_state", changed_proposal)
    else:
        proposal = unrelated if change == "unrelated_feedback" else bronze if change == "other_scenario" else selected
        action = "request_rework" if change == "selected_rework" else "reject"
        assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1,
                                                 "action": action, "comment": change}).status_code == 200
    current = client.get(path).json()
    assert current["stale"] is stale
    assert current["input_version"] == captured["input_version"]
    assert (current["current_input_version"] != captured["input_version"]) is stale
    listed = next(r for r in client.get("/api/agents/runs").json()["items"] if r["id"] == captured["id"])
    assert listed["stale"] is stale and listed["current_input_version"] == current["current_input_version"]


def test_agent_consent_configuration_one_slot_and_durable_terminal_output(tmp_path, small_xml, monkeypatch):
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    runtime = FakeRuntime()
    app = create_app(tmp_path / "data", small_xml, runtime)
    try:
        with client_for(app) as client:
            load_sample(client)
            workflow = client.get("/api/objects?kind=workflow").json()["items"][0]["id"]
            body = {"workflow_id": workflow, "scenario": "bronze", "prompt": "Review token=secret-prompt"}
            assert post(client, "/api/agents/run", json=body).status_code == 403
            assert post(client, "/api/agents/run", json={**body, "consent": True}).status_code == 409
            monkeypatch.setenv("WORKBENCH_MODEL", "openai:offline-not-invoked")
            assert post(client, "/api/agents/run", json={**body, "consent": "true"}).status_code == 422
            response = post(client, "/api/agents/run", json={**body, "consent": True})
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            assert runtime.started.wait(10)
            workflow_name = app.state.service.active.nodes[workflow]["name"]
            writer_id = "a" * 32
            package_hash = app.state.service.active.model["package"]["sha256"]
            writer_record = {"id": writer_id, "kind": "spark_draft",
                "agent_run_id": run_id, "status": "running", "phase": "candidate_code_generation",
                "package_hash": package_hash, "created_at": "2026-11-13T00:00:00+00:00",
                "started_at": "2026-11-13T00:00:01+00:00"}
            writer_path = (app.state.service.root / "package-contexts" / package_hash
                           / "state" / "jobs" / f"{writer_id}.json")
            writer_path.parent.mkdir(parents=True, exist_ok=True)
            writer_path.write_text(json.dumps(writer_record), encoding="utf-8")
            live_status = client.get(f"/api/agents/runs/{run_id}?status_only=true").json()
            assert live_status["workflow_name"] == workflow_name
            assert live_status["phase"] == "agent_analysis"
            assert live_status["candidate_writer"]["status"] == "running"
            assert live_status["candidate_writer"]["phase"] == "candidate_code_generation"
            history = client.get("/api/agents/runs?status_only=true").json()
            listed = next(item for item in history["items"] if item["id"] == run_id)
            assert listed["workflow_name"] == workflow_name
            assert listed["phase"] == "agent_analysis"
            assert listed["candidate_writer"]["id"] == writer_id
            assert history["active_run_present"] is True
            # Simulate a pre-fix persisted row. Status-only lookups must enrich
            # display metadata without loading an agent/project child runtime.
            legacy = app.state.service.record(run_id, agent=True)
            legacy.pop("workflow_name", None)
            legacy.pop("phase", None)
            app.state.service.update_record(legacy, agent=True)
            repaired = client.get(f"/api/agents/runs/{run_id}?status_only=true").json()
            assert repaired["workflow_name"] == workflow_name
            assert repaired["phase"] == "agent_analysis"
            assert post(client, "/api/agents/run", json={**body, "consent": True}).status_code == 409
            assert client.get("/api/health").status_code == 200
            runtime.release.set()
            record = wait_record(client, "/api/agents/runs/" + run_id)
            assert record["status"] == "completed" and record["output"]["status"] == "needs_information"
            assert record["output"]["generation_allowed"] is False and record["output"]["approved"] is False
            assert record["workflow_name"] == app.state.service.active.nodes[workflow]["name"]
            refs = {item["id"]: item for item in record["evidence_references"]}
            assert refs[workflow]["label"] == app.state.service.active.nodes[workflow]["name"]
            assert refs[next(edge["id"] for edge in app.state.service.active.model["edges"]
                             if edge["kind"] == "workflow_control")]["label"].count("→") == 1
            assert "secret-prompt" not in runtime.called[0][3]
            assert runtime.called[0][0] is app.state.service.active.model
            assert client.get("/api/agents/runs").json()["total"] == 1
            assert client.get("/api/agents/runs/../../bad").status_code == 404
    finally:
        runtime.release.set()
    with client_for(create_app(tmp_path / "data", small_xml, runtime)) as restarted:
        assert restarted.get("/api/agents/runs/" + run_id).json()["status"] == "completed"
        assert len(runtime.called) == 1


def test_agent_run_can_be_stopped_cooperatively(tmp_path, small_xml, monkeypatch):
    monkeypatch.setenv("WORKBENCH_MODEL", "openai:offline-not-invoked")

    class CancelRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.cancel_observed = Event()

        def run(self, model, workflow_id, scenario, prompt, documents, feedback, *, cancellation_event=None):
            self.called.append((model, workflow_id, scenario, prompt, documents, feedback))
            self.started.set()
            assert cancellation_event is not None
            assert cancellation_event.wait(10)
            self.cancel_observed.set()
            assert self.release.wait(10)
            return {"status": "cancelled", "summary": "Cooperative stop observed.",
                    "findings": [], "redesigns": [], "approved": False,
                    "generation_allowed": False, "deployment_allowed": False}

    runtime = CancelRuntime()
    app = create_app(tmp_path / "cancel", small_xml, runtime)
    with client_for(app) as client:
        load_sample(client)
        workflow = client.get("/api/objects?kind=workflow").json()["items"][0]["id"]
        body = {"workflow_id": workflow, "scenario": "bronze", "prompt": "Review selected workflow",
                "consent": True}
        run = post(client, "/api/agents/run", json=body)
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]
        assert runtime.started.wait(10)
        assert client.post(f"/api/agents/runs/{run_id}/cancel").status_code == 403
        stopped = post(client, f"/api/agents/runs/{run_id}/cancel")
        assert stopped.status_code == 202 and stopped.json()["cancellation_requested"] is True
        assert runtime.cancel_observed.wait(10)
        repeated = post(client, f"/api/agents/runs/{run_id}/cancel")
        assert repeated.status_code == 202
        assert repeated.json()["cancellation_requested_at"] == stopped.json()["cancellation_requested_at"]
        assert client.get(f"/api/agents/runs/{run_id}").json()["status"] == "running"
        runtime.release.set()
        record = wait_record(client, f"/api/agents/runs/{run_id}")
        assert record["status"] == "cancelled"
        assert record["output"]["status"] == "cancelled"
        assert record["output"]["approved"] is False
        assert post(client, f"/api/agents/runs/{run_id}/cancel").status_code == 409


def test_agent_delivery_status_does_not_call_blocked_draft_success():
    from workbench.app import _agent_delivery_status

    output = {"status": "draft_available", "redesigns": [{"id": "design"}],
              "role_completion": [{"role": "architecture", "status": "completed"},
                                  {"role": "semantics", "status": "failed"}]}
    assert _agent_delivery_status(output, [{"status": "completed", "manifest_status": "blocked"}], True) == (
        "partial", "Incomplete specialist results: semantics. Candidate notebook/code generation is blocked or incomplete.")
    assert _agent_delivery_status(output, [{"status": "completed", "manifest_status": "generation_complete"}], True)[0] == "partial"
    complete = {**output, "role_completion": [{"role": "architecture", "status": "completed"}]}
    assert _agent_delivery_status(complete, [{"status": "completed", "manifest_status": "generation_complete"}], True)[0] == "completed"


def test_restart_interrupted_jobs_fail_explicitly(tmp_path, small_xml):
    app = create_app(tmp_path / "data", small_xml)
    for directory in ("jobs", "agent-runs"):
        path = app.state.service.state / directory / ("a" * 32 + ".json")
        path.write_text(json.dumps({"id": "a" * 32, "status": "running"}), encoding="utf-8")
    with client_for(app) as client:
        record = client.get("/api/jobs/" + "a" * 32).json()
        assert record["status"] == "failed" and "interrupted" in record["error"]
        assert client.get("/api/overview").json()["ready"] is False


def test_package_executor_single_slot_cpu_work_not_request_loop(tmp_path, small_xml, monkeypatch):
    from workbench.understanding import analyze_package
    started, release = Event(), Event()
    def held_parser(path):
        started.set()
        assert release.wait(10)
        return analyze_package(path)
    monkeypatch.setattr("workbench.app.analyze_package", held_parser)
    app = create_app(tmp_path / "data", small_xml)
    try:
        with client_for(app) as client:
            response = post(client, "/api/packages/sample")
            assert response.status_code == 202 and started.wait(10)
            assert post(client, "/api/packages/sample").status_code == 409
            assert post(client, "/api/packages/upload", files={"file": ("other.xml", b"<POWERMART/>")}).status_code == 409
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/overview").json()["ready"] is False
            release.set()
            assert wait_record(client, "/api/jobs/" + response.json()["id"])["status"] == "completed"
    finally:
        release.set()


@pytest.fixture(scope="module")
def full_sample(tmp_path_factory):
    if not REAL_XML.is_file():
        pytest.skip("Full sample XML is not present")
    directory = tmp_path_factory.mktemp("full-sample-api")
    app = create_app(directory / "workbench", REAL_XML)
    with client_for(app) as client:
        assert client.get("/api/overview").json()["ready"] is False
        record = load_sample(client)
        yield client, app, record
    app.state.service.active = None
    gc.collect()


@pytest.mark.slow
def test_full_61mb_sample_api_persistence_proposals_and_restart(full_sample):
    client, app, record = full_sample
    overview = client.get("/api/overview").json()
    assert overview["ready"] and overview["package"]["size_bytes"] == REAL_XML.stat().st_size
    assert overview["package"]["size_bytes"] > 60_000_000
    index = app.state.service.active
    assert overview["counts"] == dict(Counter(n["kind"] for n in index.model["nodes"]))
    assert overview["counts"]["workflow"] == 243
    directory = app.state.service.packages / overview["package"]["sha256"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["node_count"] == len(index.model["nodes"])
    assert manifest["edge_count"] == len(index.model["edges"])
    assert manifest["artifact_version"] == "workbench-understanding-v2"
    assert manifest["analyzer_sha256"] == sha256((ROOT / "workbench" / "understanding.py").read_bytes()).hexdigest()
    assert (directory / "source.xml").stat().st_size == REAL_XML.stat().st_size
    assert (app.state.service.state / "reviews.sqlite").is_file()
    response = client.get("/api/proposals")
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 243
    assert result["limit"] == 20 and len(result["items"]) == 20
    first_detail = client.get("/api/proposals/" + result["items"][0]["id"]).json()
    assert len(first_detail["graph"]["nodes"]) <= 100 and len(first_detail["evidence"]) <= 50
    assert all(p["blockers"] and not p["generation_allowed"] for p in result["items"])
    artifact = directory / "proposals-original.json"
    artifact_hash = sha256(artifact.read_bytes()).hexdigest()
    drafts = json.loads(artifact.read_bytes())
    assert len(drafts) == 243 and all(p["version"] == 1 and p["status"] == "provisional" for p in drafts)
    del drafts
    assert not list(directory.glob("*.tmp"))
    # Stream actual artifact from disk to validate every record, without making
    # a second 400MB JSON string in the test client.
    with _CACHE_LOCK:
        _CACHE.pop(str(directory.resolve()), None)
    original_count, original_edges = len(index.model["nodes"]), len(index.model["edges"])
    app.state.service.active = None
    del index
    gc.collect()
    restarted = create_app(app.state.service.root, REAL_XML)
    with client_for(restarted) as second:
        actual = second.get("/api/overview").json()
        assert actual["ready"] and actual["counts"] == overview["counts"]
        assert len(restarted.state.service.active.model["nodes"]) == original_count
        assert len(restarted.state.service.active.model["edges"]) == original_edges
        persisted = restarted.state.service.active.proposal_summaries("original")
        assert len(persisted) == 243
        assert sha256(artifact.read_bytes()).hexdigest() == artifact_hash
        workflow = actual["workflows"][0]
        detail = second.get("/api/object/" + workflow["id"]).json()
        assert len(detail["graph"]["nodes"]) <= 100
        assert second.get("/api/export/english").status_code == 200
        # Restore the module fixture for any later full-sample checks.
        app.state.service.active = restarted.state.service.active
    print(json.dumps({"sample_bytes": REAL_XML.stat().st_size, "counts": overview["counts"],
                      "nodes": original_count, "edges": original_edges,
                      "ingest_seconds": record["elapsed_seconds"], "proposal_build_seconds": result["build_seconds"],
                      "proposal_response_bytes": len(response.content), "gzip_bytes": (directory / "understanding.json.gz").stat().st_size,
                      "signatures": {"create_app": str(inspect.signature(create_app)),
                                     "PackageIndex.load": str(inspect.signature(PackageIndex.load)),
                                     "PackageIndex.proposal_summaries": str(inspect.signature(PackageIndex.proposal_summaries)),
                                     "agent_inputs": str(inspect.signature(app.state.service.agent_inputs)),
                                     "run_freshness": str(inspect.signature(app.state.service.run_freshness)),
                                     "reviews_endpoint": str(inspect.signature(next(r.endpoint for r in app.routes if r.path == "/api/reviews" and "GET" in r.methods))),
                                     "AgentRuntime": str(inspect.signature(AgentRuntime)),
                                     "AgentRuntime.run": str(inspect.signature(AgentRuntime.run)),
                                     "Store.save_review": str(inspect.signature(Store.save_review))}}, sort_keys=True))