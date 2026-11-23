"""Canonical package graph contracts, with real parser and full sample coverage."""
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from workbench.app import PackageIndex, _create_package_app, create_app
from workbench.package_graph import package_graph
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[2]
MUTATION = {"X-Workbench-Request": "1"}


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


def ingest(client, app):
    response = client.post("/api/packages/sample", headers=MUTATION)
    assert response.status_code == 202, response.text
    app.state.service.package_executor.submit(lambda: None).result(timeout=180)
    assert client.get("/api/jobs/" + response.json()["id"]).json()["status"] == "completed"


@pytest.fixture
def small_index(tmp_path):
    path = tmp_path / "sample.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <SOURCE NAME="Src"/><TARGET NAME="Dst"/><MAPPING NAME="M"/>
    <SESSION NAME="S" MAPPINGNAME="M"/>
    <WORKFLOW NAME="A"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="S"/></WORKFLOW>
    <WORKFLOW NAME="B"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="S"/></WORKFLOW>
    <WORKFLOW NAME="C"><TASK TYPE="Command" NAME="Call_A"><ATTRIBUTE NAME="Command" VALUE="call A"/></TASK></WORKFLOW>
    </FOLDER><FOLDER NAME="Other"><MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>
    <WORKFLOW NAME="D"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="S"/></WORKFLOW>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return PackageIndex(analyze_package(path), tmp_path)


def test_shared_mapping_is_explicit_undirected_relation_with_verified_paths(small_index):
    model_before = deepcopy(small_index.model)
    graph = package_graph(small_index)
    assert graph["total_nodes"] == 4 and len(graph["nodes"]) == 4
    assert graph["total_edges"] == 1 and len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["kind"] == edge["relation"] == "shared_definition"
    assert edge["directed"] is False and "not dependency" in edge["label"]
    assert {small_index.nodes[edge[k]]["name"] for k in ("source", "target")} == {"A", "B"}
    for key in ("source_path", "target_path"):
        path = edge["evidence"][key]
        assert path["edges"][0]["source"] == edge["source" if key == "source_path" else "target"]
        assert path["edges"][-1]["target"] == edge["shared_definition_id"]
        assert {e["kind"] for e in path["edges"]} == {"contains", "task_definition", "session_mapping"}
        for item in path["edges"]:
            assert item in [small_index.path({e["target"]: e, e["source"]: None}, e["target"])[0]
                            for e in small_index.model["edges"]]
    assert graph["has_cycles"] is False
    assert small_index.model == model_before
    assert all(n["assessment"]["status"] == "neutral" for n in graph["nodes"])


def test_assessments_scoped_error_only_and_no_false_source_health(small_index):
    model = deepcopy(small_index.model)
    workflow = next(n for n in model["nodes"] if n["kind"] == "workflow" and n["name"] == "A")
    model["diagnostics"] = [{"severity": "error", "code": "STRUCTURE_ERROR", "object_id": workflow["id"],
                             "message": "Confirmed scoped structural error"},
                            {"severity": "error", "object_id": "not-in-model", "message": "Unscoped"}]
    index = PackageIndex(model, small_index.directory)
    graph = package_graph(index)
    for node in graph["nodes"]:
        assessment = node["assessment"]
        assert assessment["status"] == ("red" if node["id"] == workflow["id"] else "neutral")
        assert assessment["basis"] == "structural_unverified" and assessment["evidence_ids"]
        assert assessment["source"] == assessment["transformation"] == assessment["destination"] == "unknown"
        assert assessment["agent_finding"] is None
    source = package_graph(index, "source")["nodes"][0]
    assert source["assessment"]["status"] == "neutral"
    assert "healthy" not in source and source["assessment"]["semantic_status"] == "unverified"
    model["diagnostics"][0]["severity"] = "warning"
    assert all(n["assessment"]["status"] == "neutral" for n in package_graph(PackageIndex(model, index.directory))["nodes"])


def test_actual_workflow_reference_cycles_and_unknown_outside_page(tmp_path):
    path = tmp_path / "cycles.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <WORKFLOW NAME="A"><TASKINSTANCE NAME="B" TASKTYPE="Workflow" TASKNAME="B"/></WORKFLOW>
    <WORKFLOW NAME="B"><TASKINSTANCE NAME="A" TASKTYPE="Workflow" TASKNAME="A"/></WORKFLOW>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    index = PackageIndex(analyze_package(path), tmp_path)
    graph = package_graph(index)
    assert graph["total_edges"] == 2
    assert graph["has_cycles"] is True and graph["page_has_cycles"] is True
    assert all(e["kind"] == "definition_reference" and e["directed"] for e in graph["edges"])
    page = package_graph(index, limit=1)
    assert page["has_cycles"] is None and page["outside_page_cycles"] == "unknown"
    assert not page["page_has_cycles"] and page["total_edges"] == 2


def test_child_route_kind_bounds_and_project_dispatch(tmp_path, small_index):
    sample = small_index.directory / "sample.xml"
    app = create_app(tmp_path / "data", sample)
    with client_for(app) as client:
        assert client.get("/api/package-graph").status_code == 409
        for query in ("limit=101", "limit=0", "offset=-1", "kind=task", "limit=bad",
                  "all=bad", "all=true&offset=1", "all=true&limit=101"):
            assert client.get("/api/package-graph?" + query).status_code == 422
        project = client.post("/api/projects", headers=MUTATION, json={"name": "Scoped"}).json()
        response = client.post(f"/api/projects/{project['id']}/packages/sample", headers=MUTATION,
                               json={"names": [sample.name]})
        # Trusted input root is a server setting, never an HTTP path.
        assert response.status_code == 404
        app.state.projects.input_root = sample.parent
        response = client.post(f"/api/projects/{project['id']}/packages/sample", headers=MUTATION,
                               json={"names": [sample.name]})
        assert response.status_code == 202
        app.state.projects.futures[response.json()["id"]].result(timeout=30)
        headers = {"X-Workbench-Project": project["id"],
                   "X-Workbench-Package": small_index.model["package"]["sha256"]}
        for kind, total in (("workflow", 4), ("mapping", 2), ("source", 1), ("target", 1)):
            graph = client.get(f"/api/package-graph?kind={kind}", headers=headers)
            assert graph.status_code == 200, graph.text
            assert graph.json()["total_nodes"] == total
            full = client.get(f"/api/package-graph?kind={kind}&all=true", headers=headers)
            assert full.status_code == 200 and full.json()["all_nodes"] is True
            assert full.json()["nodes"] == graph.json()["nodes"]
            assert full.json()["edges"] == graph.json()["edges"]
            assert full.json()["truncated"] is False
        assert client.get("/api/package-graph").status_code == 409
        assert client.get("/api/package-graph", headers={"X-Workbench-Project": project["id"]}).status_code == 409
    # Route also exists on a standalone factory child, not just the parent.
    child = _create_package_app(tmp_path / "child", sample)
    with client_for(child) as client:
        ingest(client, child)
        assert client.get("/api/package-graph").status_code == 200


def test_real_243_workflows_deterministic_60_node_pages_and_canonical_ids(tmp_path):
    app = _create_package_app(tmp_path / "real", ROOT / "input" / "LumenExample.XML")
    with client_for(app) as client:
        ingest(client, app)
        index = app.state.service.require()
        ids, totals, assessments, paged_edges = [], set(), {}, {}
        for offset in range(0, 243, 60):
            response = client.get(f"/api/package-graph?kind=workflow&offset={offset}&limit=60")
            assert response.status_code == 200, response.text
            graph = response.json()
            assert graph["total_nodes"] == 243 and len(graph["nodes"]) == min(60, 243 - offset)
            assert graph["has_cycles"] is None and graph["outside_page_cycles"] == "unknown"
            assert len(graph["edges"]) <= 2000
            page_ids = [n["id"] for n in graph["nodes"]]
            assert page_ids == sorted(page_ids)
            assert all(index.nodes[n["id"]]["kind"] == "workflow" for n in graph["nodes"])
            assert all(n["assessment"]["status"] == "neutral" for n in graph["nodes"])
            assert all(e["source"] in page_ids and e["target"] in page_ids for e in graph["edges"])
            assert all(e["kind"] != "dependency" for e in graph["edges"])
            ids.extend(page_ids)
            totals.add(graph["total_edges"])
            assessments.update({n["id"]: n["assessment"] for n in graph["nodes"]})
            paged_edges.update({e["id"]: e for e in graph["edges"]})
        assert len(totals) == 1
        assert len(set(ids)) == 243 and set(ids) == {n["id"] for n in index.kinds["workflow"]}
        again = client.get("/api/package-graph?limit=60").json()
        assert [n["id"] for n in again["nodes"]] == ids[:60]
        empty = client.get("/api/package-graph?offset=243&limit=60").json()
        assert empty["nodes"] == [] and empty["total_nodes"] == 243 and not empty["has_more"]
        full_response = client.get("/api/package-graph?kind=workflow&all=true")
        assert full_response.status_code == 200, full_response.text
        full = full_response.json()
        assert [n["id"] for n in full["nodes"]] == ids
        assert len(full["nodes"]) == full["limit"] == full["node_limit"] == 243
        assert full["edge_limit"] is None and full["all_nodes"] is True
        assert not full["truncated"] and not full["has_more"]
        assert len(full["edges"]) == full["total_edges"] == full["page_total_edges"] == totals.pop()
        assert full["has_cycles"] in (True, False)
        assert {n["id"]: n["assessment"] for n in full["nodes"]} == assessments
        all_edges = {e["id"]: e for e in full["edges"]}
        assert len(all_edges) == len(full["edges"])
        assert all(all_edges[edge_id] == edge for edge_id, edge in paged_edges.items())
        # Unioning pages alone loses cross-page relationships. All is a server
        # full-kind projection, not a renderer assertion or concatenated pages.
        assert set(all_edges) - set(paged_edges)
        assert all(e["source"] in ids and e["target"] in ids for e in full["edges"])
        assert full["generation_allowed"] is full["deployment_allowed"] is False


def test_explicit_all_retains_more_than_2000_evidence_backed_relations(tmp_path):
    path = tmp_path / "dense.xml"
    workflows = ''.join(f'<WORKFLOW NAME="W{i}"><TASKINSTANCE NAME="Run" TASKTYPE="Session" TASKNAME="S"/></WORKFLOW>'
                        for i in range(66))
    path.write_text('<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">'
                    '<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>' + workflows +
                    '</FOLDER></REPOSITORY></POWERMART>', encoding="utf-8")
    index = PackageIndex(analyze_package(path), tmp_path)
    before = deepcopy(index.model)
    canonical_edges = {e["id"]: e for e in index.model["edges"]}
    paged = package_graph(index, limit=100)
    assert len(paged["edges"]) == 2000 and paged["page_total_edges"] == 2145 and paged["truncated"]
    full = package_graph(index, all_nodes=True)
    assert len(full["nodes"]) == 66 and len(full["edges"]) == 2145
    assert not full["truncated"] and full["has_cycles"] is False
    assert all(not e["directed"] and e["kind"] == "shared_definition" for e in full["edges"])
    for edge in full["edges"]:
        for key, root in (("source_path", "source"), ("target_path", "target")):
            evidence = edge["evidence"][key]
            assert evidence["edges"][0]["source"] == edge[root]
            assert evidence["edges"][-1]["target"] == edge["shared_definition_id"]
            assert all(canonical_edges[e["id"]]["evidence"] == e["evidence"] for e in evidence["edges"])
    assert index.model == before
    assert all(n["assessment"]["status"] == "neutral" for n in full["nodes"])
    for arguments in ({"offset": 1, "all_nodes": True}, {"all_nodes": "true"}, {"limit": 101, "all_nodes": True}):
        with pytest.raises(ValueError):
            package_graph(index, **arguments)