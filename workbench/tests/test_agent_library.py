from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from workbench import agent_library as library
from workbench.agents import ROLES


def test_tool_catalog_uses_ast_role_compilation_and_separates_writer_and_harness():
    tools, counts = library._tools_catalog()
    analysis = {item["name"]: item for item in tools if item["category"] == "analysis"}
    writer = {item["name"]: item for item in tools if item["category"] == "writer"}
    harness = {item["name"]: item for item in tools if item["category"] == "harness"}

    assert analysis["lookup_evidence"]["roles"] == sorted(ROLES)
    assert analysis["architecture_knowledge"]["roles"] == ["architecture", "supervisor", "validation"]
    assert analysis["submit_redesign"]["roles"] == ["architecture", "supervisor"]
    assert analysis["submit_wil_net_dis_outline"]["conditional"] is True
    assert "workflow_id" in analysis["submit_wil_net_dis_outline"]["condition"]
    assert writer and all(item["roles"] == ["spark_codegen"] for item in writer.values())
    assert "stage_generation_artifact" in writer
    assert set(harness) == {"ls", "read_file", "glob", "grep", "task"}
    assert harness["task"]["roles"] == ["supervisor"] and harness["task"]["conditional"] is True
    assert counts["internet_search_tool_available"] is False
    assert counts["online_search_tool_available"] is False
    for item in [*analysis.values(), *writer.values()]:
        source = item["source"]
        raw = (Path(__file__).resolve().parents[2] / source["path"]).read_bytes()
        assert source["file_sha256"] == sha256(raw).hexdigest()
        assert source["line_start"] > 0 and source["line_end"] >= source["line_start"]
        assert len(item["description"]) <= 400


def test_skill_snippets_have_bounded_exact_local_citations_and_complete_role_coverage():
    items = library._skill_items()
    assert items
    roles = {role for item in items for role in item["roles"]}
    assert set(ROLES) | {"spark_codegen"} <= roles
    for item in items:
        source = item["source"]
        path = (Path(__file__).resolve().parents[2] / source["path"]).resolve()
        assert path.is_relative_to(library.SKILLS_ROOT.resolve())
        assert source["file_sha256"] == sha256(path.read_bytes()).hexdigest()
        assert source["line_start"] >= 1 and source["line_end"] >= source["line_start"]
        assert len(item["text"]) <= 400
        assert len(item["id"]) == len("skill:") + 64
        assert item["freshness"]["state"] == "current_local_file_read"
        assert item["freshness"]["expires_at"] is None
        assert item["lifecycle"] == {"discard": "not_recorded", "superseded_by": "not_recorded"}


def test_trusted_source_reader_rejects_files_outside_named_skill_tree():
    path = Path(__file__).resolve().parents[1] / "agents.py"
    try:
        library._read_trusted(path, library.SKILLS_ROOT)
    except ValueError:
        pass
    else:
        raise AssertionError("a non-skill source path was accepted")


def test_pagination_reports_exact_completeness_without_silent_truncation():
    values = [{"id": str(index)} for index in range(7)]
    first = library._page("knowledge", values, 0, 3, {})
    second = library._page("knowledge", values, 3, 3, {})
    last = library._page("knowledge", values, 6, 3, {})
    assert (first["total"], first["returned"], first["has_more"], first["complete"]) == (7, 3, True, False)
    assert (second["offset"], second["has_more"], second["complete"]) == (3, True, False)
    assert (last["offset"], last["returned"], last["has_more"], last["complete"]) == (6, 1, False, True)


def test_actual_stored_graph_freshness_is_reported_without_claiming_latest_or_mutating_state():
    now = datetime.now(timezone.utc)
    retrieved = (now - timedelta(days=20)).isoformat()
    expires = (now - timedelta(days=6)).isoformat()
    source_hash = "a" * 64
    graph_hash = "b" * 64
    document = {"id": "doc-id", "kind": "document", "label": "Spark reference",
        "summary": "Official source excerpt", "source": {"url": "https://docs.databricks.com/aws/en/spark/sql",
        "retrieved_at": retrieved, "expires_at": expires, "published_at": "2024-01-01",
        "updated_at": None, "sha256": source_hash, "section_sha256": "c" * 64,
        "title": "Spark SQL", "excerpt": "Stored source excerpt"}}
    feature = {"id": "feature-id", "kind": "feature", "label": "Spark feature",
        "summary": "Historical curated summary", "release_state": "unknown", "verification": "curated",
        "evidence_ids": ["doc-id"], "pillars": ["accuracy"]}
    snapshot = SimpleNamespace(value={"version": "d" * 64, "graph_sha256": graph_hash,
        "created_at": retrieved, "data": {"nodes": [document, feature],
        "edges": [{"source": "feature-id", "target": "doc-id", "relation": "evidenced_by"}]}})
    app = FastAPI()
    app.state.hosted_request_authenticated = True
    app.state.service = SimpleNamespace(root=Path("C:/work/projects/") / ("e" * 32) / "package-contexts" / ("f" * 64))
    app.state.knowledge_store_provider = lambda: SimpleNamespace(load=lambda project: snapshot)
    request = Request({"type": "http", "method": "GET", "path": "/api/agent-library",
        "headers": [(b"x-workbench-project", b"e" * 32)], "app": app})

    items, status, edges = library._knowledge_items(request)
    graph_items = [item for item in items if item["kind"] == "architecture_knowledge"]
    assert status["status"] == "available"
    assert status["expired_source_count"] == 1
    assert status["latest_catalogue_guaranteed"] is False
    assert status["discard_or_supersede_tracking"] == "not_present_in_current_knowledge_schema"
    assert edges == snapshot.value["data"]["edges"]
    assert len(graph_items) == 2
    assert all(item["freshness"]["state"] == "expired" for item in graph_items)
    assert all(item["lifecycle"]["superseded_by"] == "not_recorded" for item in graph_items)
    assert all(item["roles"] == ["architecture", "supervisor", "validation"] for item in graph_items)


def test_local_catalog_does_not_fall_back_to_docs_or_make_web_claims():
    app = FastAPI()
    app.state.hosted_request_authenticated = False
    app.state.service = SimpleNamespace(root=Path("C:/local/") / ("e" * 32) / "package-contexts" / ("f" * 64))
    app.state.knowledge_store_provider = lambda: (_ for _ in ()).throw(AssertionError("provider must not be used locally"))
    request = Request({"type": "http", "method": "GET", "path": "/api/agent-library", "headers": [], "app": app})

    items, status, edges = library._knowledge_items(request)
    assert items and all(item["kind"] == "skill_snippet" for item in items)
    assert status["status"] == "not_connected"
    assert status["internet_search_tool_available"] is False
    assert status["source_fetching_performed"] is False
    assert edges == []


def test_route_is_installed_as_read_only_catalog_endpoint():
    app = FastAPI()
    library.install_agent_library(app)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/agent-library")
    assert route.methods == {"GET"}
    with TestClient(app) as client:
        response = client.get("/api/agent-library?section=tools&category=all&limit=100")
        assert response.status_code == 200
        result = response.json()
        assert result["complete"] is True
        assert result["status"]["internet_search_tool_available"] is False
        assert {item["category"] for item in result["items"]} == {"analysis", "writer", "harness"}
        first = client.get("/api/agent-library?section=knowledge&view=skills&limit=2").json()
        assert first["returned"] == 2 and first["total"] > 2 and first["has_more"] is True
        assert first["complete"] is False and first["status"]["status"] == "not_connected"
        last = client.get(f"/api/agent-library?section=knowledge&view=skills&offset={first['total'] - 1}&limit=2").json()
        assert last["returned"] == 1 and last["has_more"] is False and last["complete"] is True
