"""Offline project regressions use real parsing and completion futures, no polls."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from workbench.app import create_app
from workbench import projects


MUTATION = {"X-Workbench-Request": "1"}


def xml(name="W"):
    return (f'<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">'
            f'<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>'
            f'<WORKFLOW NAME="{name}"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/>'
            '</WORKFLOW></FOLDER></REPOSITORY></POWERMART>').encode()


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


def post(client, path, **kwargs):
    return client.post(path, headers={**MUTATION, **kwargs.pop("headers", {})}, **kwargs)


def create(client, name="Project", description="Description"):
    response = post(client, "/api/projects", json={"name": name, "description": description})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def selection(project_id, content):
    return {"X-Workbench-Project": project_id, "X-Workbench-Package": sha256(content).hexdigest()}


def upload(client, app, project_id, entries):
    response = post(client, f"/api/projects/{project_id}/packages/upload",
                    files=[("files", (name, data, "application/xml")) for name, data in entries])
    assert response.status_code == 202, response.text
    return complete(client, app, project_id, response.json()["id"])


def complete(client, app, project_id, job_id):
    app.state.projects.futures[job_id].result(timeout=30)
    response = client.get(f"/api/projects/{project_id}/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "data"
    app = create_app(root, tmp_path / "unused.xml")
    inputs = tmp_path / "input"
    inputs.mkdir()
    app.state.projects.input_root = inputs
    with client_for(app) as client:
        yield client, app, root, inputs


def test_real_multi_package_same_names_hash_dedup_partial_and_default_unchanged(workspace):
    client, app, root, _ = workspace
    project_id = create(client, " New project ")
    first, second = xml("First"), xml("Second")
    result = upload(client, app, project_id, [("same.xml", first), ("same.xml", second),
                                             ("renamed.XML", first), ("bad.xml", b"<broken>")])
    assert result["status"] == "partial"
    assert result["processed"] == 4 and result["completed_count"] == 2
    assert result["skipped_count"] == 1 and result["failed_count"] == 1
    assert [i["status"] for i in result["items"]] == ["completed", "completed", "skipped", "failed"]
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["name"] == "New project" and detail["package_count"] == 3
    assert detail["ready_package_count"] == 2 and detail["jobs"][0]["id"] == result["id"]
    for data, name in ((first, "First"), (second, "Second")):
        headers = selection(project_id, data)
        overview = client.get("/api/overview", headers=headers).json()
        assert overview["ready"] and overview["workflows"][0]["name"] == name
        assert overview["package"]["sha256"] == sha256(data).hexdigest()
        assert client.get("/api/export/understanding", headers=headers).status_code == 200
    assert not client.get("/api/overview").json()["ready"]
    no_selection = client.get("/api/overview", headers={"X-Workbench-Project": project_id}).json()
    assert not no_selection["ready"] and no_selection["project"]["id"] == project_id
    assert client.get("/api/objects", headers={"X-Workbench-Project": project_id}).status_code == 409
    assert not list(app.state.projects.uploads.iterdir())
    assert not app.state.projects.contexts.get((project_id, sha256(b"<broken>").hexdigest()))
    assert (root / "projects" / project_id / "active.json").exists()


def test_same_hash_cross_project_reviews_documents_settings_restart(workspace):
    client, app, root, _ = workspace
    a, b = create(client, "A"), create(client, "B")
    data = xml()
    for project_id in (a, b):
        assert upload(client, app, project_id, [("shared.xml", data)])["status"] == "completed"
    ah, bh = selection(a, data), selection(b, data)
    proposal = client.get("/api/proposals", headers=ah).json()["items"][0]
    response = post(client, "/api/reviews", headers=ah, json={"proposal_id": proposal["id"],
                    "expected_version": 1, "action": "comment", "comment": "Only A"})
    assert response.status_code == 200, response.text
    assert post(client, "/api/documents", headers=ah, files={"file": ("a.md", b"Only A", "text/markdown")}).status_code == 201
    assert post(client, "/api/settings", headers=ah, json={"github_owner": "project-a"}).status_code == 200
    for route in ("reviews", "documents"):
        assert client.get("/api/" + route, headers=ah).json()["total"] == 1
        assert client.get("/api/" + route, headers=bh).json()["total"] == 0
    assert client.get("/api/proposals", headers=bh).json()["items"][0]["version"] == 1
    assert not client.get("/api/settings", headers=bh).json()["settings"].get("github_owner")
    # Close all project workers before simulating a new process over the same files.
    app.state.projects.close()
    restarted = create_app(root, app.state.service.sample)
    with client_for(restarted) as other:
        assert len(other.get("/api/projects").json()["items"]) == 2
        assert other.get("/api/reviews", headers=ah).json()["total"] == 1
        assert other.get("/api/documents", headers=ah).json()["total"] == 1
        assert other.get("/api/reviews", headers=bh).json()["total"] == 0
        assert other.get("/api/settings", headers=ah).json()["settings"]["github_owner"] == "project-a"
        assert other.get(f"/api/projects/{a}").json()["ready_package_count"] == 1


def test_request_scoped_selection_parallel_reads_and_bounded_cache(workspace):
    client, app, _, _ = workspace
    project_id = create(client)
    payloads = [xml(name) for name in ("One", "Two", "Three")]
    upload(client, app, project_id, [("same.xml", data) for data in payloads])
    assert app.state.projects.contexts == {}  # Batch models are not retained.
    def read(pair):
        data, expected = pair
        response = client.get("/api/overview", headers=selection(project_id, data))
        assert response.status_code == 200, response.text
        assert response.json()["workflows"][0]["name"] == expected
    # Two hot contexts stay independent under overlapping HTTP requests.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(read, [(payloads[0], "One"), (payloads[1], "Two")] * 8))
    read((payloads[2], "Three"))
    assert len(app.state.projects.contexts) == 2
    read((payloads[0], "One"))
    assert len(app.state.projects.contexts) == 2


def test_trusted_local_selection_snapshot_and_no_arbitrary_paths(workspace):
    client, app, _, inputs = workspace
    project_id = create(client)
    (inputs / "first.XML").write_bytes(xml("First"))
    (inputs / "second.xml").write_bytes(xml("Second"))
    (inputs / "ignored.txt").write_text("no")
    items = client.get("/api/input-packages").json()["items"]
    assert items == [{"name": "first.XML", "size_bytes": len(xml("First"))},
                     {"name": "second.xml", "size_bytes": len(xml("Second"))}]
    response = post(client, f"/api/projects/{project_id}/packages/sample", json={"names": ["first.XML", "second.xml"]})
    assert response.status_code == 202, response.text
    result = complete(client, app, project_id, response.json()["id"])
    assert result["completed_count"] == 2
    for name in ("../first.XML", "C:\\first.XML", "/first.XML", "missing.xml"):
        response = post(client, f"/api/projects/{project_id}/packages/sample", json={"names": [name]})
        assert response.status_code == (404 if name == "missing.xml" else 400)
    assert not list(app.state.projects.uploads.iterdir())


def test_import_failure_is_recorded_with_safe_stage_and_audit(workspace, monkeypatch):
    from workbench import app as app_module
    client, app, _, _ = workspace
    project_id = create(client)
    def fail(_):
        raise OSError(13, 'private XML content or token must not be logged')
    monkeypatch.setattr(app_module, 'analyze_package', fail)
    result = upload(client, app, project_id, [('sample.xml', xml())])
    assert result['status'] == 'failed'
    diagnostic = result['items'][0]['error_diagnostic']
    assert diagnostic == {'stage': 'parsing_xml', 'error_type': 'PermissionError', 'errno': 13}
    events, valid = app.state.audit.records()
    matching = [event for event in events if event.get('run_id') == result['id']
                and event['event'] == 'project_package_import_failed']
    assert valid and len(matching) == 1 and matching[0]['diagnostic'] == diagnostic
    assert 'private XML content' not in json.dumps(result) + json.dumps(matching)
    response = client.get(f"/api/projects/{project_id}/jobs/{result['id']}/logs",
                          headers={'X-Workbench-Project': project_id})
    assert response.status_code == 200 and response.json()['items'][0]['diagnostic'] == diagnostic
    other = create(client, 'Another project')
    assert client.get(f"/api/projects/{other}/jobs/{result['id']}/logs").status_code == 404


def test_project_security_unknown_ids_stale_membership_and_job_scope(workspace):
    client, app, _, _ = workspace
    a, b = create(client, "A"), create(client, "B")
    result = upload(client, app, a, [("a.xml", xml("A"))])
    ah, bh = selection(a, xml("A")), selection(b, xml("A"))
    assert client.get("/api/overview", headers=bh).status_code == 403
    assert client.get("/api/overview", headers={**ah, "X-Workbench-Project": "f" * 32}).status_code == 404
    assert client.get("/api/overview", headers={"X-Workbench-Package": ah["X-Workbench-Package"]}).status_code == 400
    assert client.get("/api/overview", headers={**ah, "X-Workbench-Package": "../other"}).status_code == 403
    assert client.get(f"/api/projects/{b}/jobs/{result['id']}").status_code == 404
    assert client.get(f"/api/projects/{b}", headers=ah).status_code == 403
    assert post(client, "/api/packages/sample", headers=ah).status_code == 409
    assert post(client, "/api/packages/upload", headers=ah, files={"file": ("other.xml", xml())}).status_code == 409
    assert client.post("/api/projects", json={"name": "unsafe"}).status_code == 403
    assert post(client, "/api/projects", headers={"Origin": "http://evil.example"}, json={"name": "unsafe"}).status_code == 403
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/overview", headers=ah).headers["x-content-type-options"] == "nosniff"
    # Object IDs from another package must not resolve in the selected child.
    upload(client, app, b, [("b.xml", xml("B"))])
    workflow = client.get("/api/objects?kind=workflow", headers=ah).json()["items"][0]
    assert client.get("/api/object/" + workflow["id"], headers=selection(b, xml("B"))).status_code == 404
    with TestClient(create_app(app.state.service.root.parent / "remote"), base_url="http://127.0.0.1",
                    client=("203.0.113.1", 4)) as remote:
        assert remote.get("/api/projects").status_code == 403


@pytest.mark.parametrize("body", [{"name": " "}, {"name": "x" * 121}, {"name": 12},
                                  {"name": "valid", "description": "x" * 4001},
                                  {"name": "x\x00"}, {"name": "valid", "path": "elsewhere"}])
def test_project_validation(workspace, body):
    client, *_ = workspace
    assert post(client, "/api/projects", json=body).status_code == 422


@pytest.mark.parametrize("name,data,expected", [("../bad.xml", b"x", 400), ("C:\\bad.xml", b"x", 400),
                                                ("bad.zip", b"x", 415), ("empty.xml", b"", 400)])
def test_upload_validation_and_cleanup(workspace, name, data, expected):
    client, app, *_ = workspace
    project_id = create(client)
    response = post(client, f"/api/projects/{project_id}/packages/upload", files=[("files", (name, data))])
    assert response.status_code == expected, response.text
    assert not list(app.state.projects.uploads.iterdir())
    assert client.get(f"/api/projects/{project_id}").json()["packages"] == []


def test_batch_bounds_cleanup_no_unbounded_queue(workspace, monkeypatch):
    client, app, _, inputs = workspace
    project_id = create(client)
    url = f"/api/projects/{project_id}/packages/upload"
    assert post(client, url, files=[("files", (f"{i}.xml", xml())) for i in range(11)]).status_code == 400
    assert post(client, f"/api/projects/{project_id}/packages/sample", json={"names": ["x.xml"] * 11}).status_code == 422
    assert post(client, url, headers={"Content-Length": str(projects.MAX_BATCH_BYTES + 65537)}, content=b"x").status_code == 413
    monkeypatch.setattr(projects, "MAX_FILE_BYTES", 4)
    assert post(client, url, files=[("files", ("x.xml", b"12345"))]).status_code == 413
    monkeypatch.setattr(projects, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(projects, "MAX_BATCH_BYTES", 6)
    assert post(client, url, files=[("files", ("one.xml", b"1234")), ("files", ("two.xml", b"5678"))]).status_code == 413
    (inputs / "one.xml").write_bytes(b"1234")
    (inputs / "two.xml").write_bytes(b"5678")
    assert post(client, f"/api/projects/{project_id}/packages/sample", json={"names": ["one.xml", "two.xml"]}).status_code == 413
    assert not list(app.state.projects.uploads.iterdir())
    assert app.state.projects.slot.acquire(blocking=False)
    try:
        assert post(client, url, files=[("files", ("x.xml", b"123"))]).status_code == 409
    finally:
        app.state.projects.slot.release()


def test_interrupted_jobs_fail_explicitly_on_restart(workspace):
    client, app, root, _ = workspace
    project_id = create(client)
    digest, job_id = "a" * 64, "b" * 32
    item = {"name": "interrupted.xml", "sha256": digest, "status": "running"}
    app.state.projects.registry.save("packages", project_id, item)
    app.state.projects.registry.save("jobs", project_id, {"id": job_id, "status": "running", "items": [item]})
    app.state.projects.close()
    with client_for(create_app(root, app.state.service.sample)) as second:
        record = second.get(f"/api/projects/{project_id}/jobs/{job_id}").json()
        assert record["status"] == "failed" and record["items"][0]["status"] == "failed"
        assert second.get(f"/api/projects/{project_id}").json()["packages"][0]["status"] == "failed"
        assert second.get("/api/overview", headers={"X-Workbench-Project": project_id,
                                                  "X-Workbench-Package": digest}).status_code == 403


def load_legacy(client, app, inputs, data=None):
    data = data or xml("Legacy")
    path = inputs / "legacy.xml"
    path.write_bytes(data)
    app.state.service.sample = path
    result = post(client, "/api/packages/sample")
    assert result.status_code == 202, result.text
    app.state.service.package_executor.submit(lambda: None).result(timeout=30)
    assert client.get("/api/jobs/" + result.json()["id"]).json()["status"] == "completed"
    return data


def test_adopt_current_isolated_snapshot_preserves_artifacts_reviews_idempotent_restart(workspace):
    client, app, root, inputs = workspace
    data = load_legacy(client, app, inputs)
    proposal = client.get("/api/proposals").json()["items"][0]
    assert post(client, "/api/reviews", json={"proposal_id": proposal["id"], "expected_version": 1,
                "action": "comment", "comment": "Preserve legacy review"}).status_code == 200
    assert post(client, "/api/documents", files={"file": ("legacy.md", b"Legacy evidence")}).status_code == 201
    assert post(client, "/api/settings", json={"github_owner": "legacy-owner"}).status_code == 200
    legacy = app.state.service.require()
    originals = {p.relative_to(root): p.read_bytes() for p in legacy.directory.rglob("*") if p.is_file()}
    originals[root.joinpath("state/reviews.sqlite").relative_to(root)] = (root / "state/reviews.sqlite").read_bytes()
    originals[root.joinpath("state/active.json").relative_to(root)] = (root / "state/active.json").read_bytes()
    response = post(client, "/api/projects/adopt-current", json={"name": "My current XML", "description": "Imported"})
    assert response.status_code == 201, response.text
    project = response.json()
    project_id = project["id"]
    assert not project["reused"] and project["package"]["imported"]
    assert project["legacy_snapshot_sha256"] == sha256(data).hexdigest()
    assert client.get(f"/api/projects/{project_id}").json()["ready_package_count"] == 1
    for path, content in originals.items():
        assert (root / path).read_bytes() == content
    headers = selection(project_id, data)
    assert client.get("/api/reviews", headers=headers).json() == client.get("/api/reviews").json()
    assert client.get("/api/documents", headers=headers).json() == client.get("/api/documents").json()
    assert client.get("/api/settings", headers=headers).json()["settings"]["github_owner"] == "legacy-owner"
    assert client.get("/api/package-graph", headers=headers).json()["nodes"][0]["id"] == legacy.kinds["workflow"][0]["id"]
    # A new project review cannot change the legacy database or its effective revision.
    assert post(client, "/api/reviews", headers=headers, json={"proposal_id": proposal["id"], "expected_version": 2,
                "action": "comment", "comment": "New project only"}).status_code == 200
    assert client.get("/api/reviews").json()["total"] == 1
    assert client.get("/api/reviews", headers=headers).json()["total"] == 2
    again = post(client, "/api/projects/adopt-current", json={"name": "My current XML"}).json()
    assert again["id"] == project_id and again["reused"]
    assert len(client.get("/api/projects").json()["items"]) == 1
    for path, content in originals.items():
        assert (root / path).read_bytes() == content
    app.state.projects.close()
    with client_for(create_app(root, app.state.service.sample)) as second:
        assert second.get("/api/reviews", headers=headers).json()["total"] == 2
        assert second.get("/api/reviews").json()["total"] == 1
        assert second.get("/api/overview", headers=headers).json()["workflows"][0]["name"] == "Legacy"
        assert second.get(f"/api/projects/{project_id}").json()["name"] == "My current XML"


def test_adoption_busy_corrupt_and_failed_snapshot_never_restarts_legacy(workspace, monkeypatch):
    client, app, root, inputs = workspace
    assert post(client, "/api/projects/adopt-current", json={"name": "No active"}).status_code == 409
    load_legacy(client, app, inputs)
    index = app.state.service.require()
    marker = root / "state/jobs" / ("f" * 32 + ".json")
    marker.write_text(json.dumps({"status": "running", "id": "f" * 32}))
    for slot in (app.state.service.package_slot, app.state.service.agent_slot, app.state.projects.slot):
        assert slot.acquire(blocking=False)
        try:
            response = post(client, "/api/projects/adopt-current", json={"name": "Busy"})
            assert response.status_code == 409
        finally:
            slot.release()
    assert json.loads(marker.read_text())["status"] == "running"
    source = index.directory / "source.xml"
    original = source.read_bytes()
    source.write_bytes(b"corrupt")
    assert post(client, "/api/projects/adopt-current", json={"name": "Corrupt"}).status_code == 409
    source.write_bytes(original)
    def failed_copy(*args, **kwargs):
        raise OSError("simulated snapshot failure")
    with monkeypatch.context() as patch:
        patch.setattr(projects.shutil, "copytree", failed_copy)
        assert post(client, "/api/projects/adopt-current", json={"name": "Failed"}).status_code == 409
    assert client.get("/api/projects").json()["items"] == []
    assert not list((root / "projects").glob("*/package-contexts"))
    assert post(client, "/api/projects/adopt-current", json={"name": "Good"}).status_code == 201
    assert json.loads(marker.read_text())["status"] == "running"
    assert app.state.service.require() is index


def test_adoption_hardlink_fallback_and_sqlite_independence(workspace, monkeypatch):
    client, app, root, inputs = workspace
    data = load_legacy(client, app, inputs)
    def no_links(*args, **kwargs):
        raise OSError("hardlinks unavailable")
    monkeypatch.setattr(projects.os, "link", no_links)
    adopted = post(client, "/api/projects/adopt-current", json={"name": "Copied"})
    assert adopted.status_code == 201, adopted.text
    context = app.state.projects.context_root(adopted.json()["id"], sha256(data).hexdigest())
    assert (context / "packages" / sha256(data).hexdigest() / "source.xml").read_bytes() == data
    with sqlite3.connect(context / "state/reviews.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (root / "state/reviews.sqlite").exists()


def test_project_rename_persists_without_changing_id_membership_or_paths(workspace):
    client, app, root, _ = workspace
    project_id = create(client, "Before")
    upload(client, app, project_id, [("one.xml", xml())])
    package_records = client.get(f"/api/projects/{project_id}").json()["packages"]
    response = client.patch(f"/api/projects/{project_id}", headers=MUTATION,
                            json={"name": " After ", "description": "New description"})
    assert response.status_code == 200, response.text
    renamed = response.json()
    assert renamed["id"] == project_id and renamed["name"] == "After" and renamed["updated_at"]
    assert renamed["description"] == "New description"
    assert client.get(f"/api/projects/{project_id}").json()["packages"] == package_records
    assert (root / "projects" / project_id / "package-contexts").is_dir()
    assert client.patch(f"/api/projects/{project_id}", json={"name": "No header"}).status_code == 403
    assert client.patch(f"/api/projects/{project_id}", headers=MUTATION, json={"name": " "}).status_code == 422
    assert client.patch("/api/projects/" + "f" * 32, headers=MUTATION, json={"name": "Missing"}).status_code == 404
    app.state.projects.close()
    with client_for(create_app(root, app.state.service.sample)) as second:
        assert second.get(f"/api/projects/{project_id}").json()["name"] == "After"


def test_stage_then_explicit_analyze_partial_statuses_snapshots_and_documents(workspace, monkeypatch):
    client, app, root, inputs = workspace
    project_id = create(client)
    valid, invalid = xml("Staged"), b"<broken>"
    response = post(client, f"/api/projects/{project_id}/packages/upload?stage_only=true",
                    files=[("files", ("valid.xml", valid)), ("files", ("bad.xml", invalid))])
    assert response.status_code == 202, response.text
    staged = response.json()
    assert staged["status"] == "staged" and not staged["analysis_started"] and staged["job_id"] is None
    assert all(i["status"] == "staged" for i in staged["items"])
    assert app.state.projects.futures == {} and app.state.projects.contexts == {}
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["jobs"] == [] and detail["ready_package_count"] == 0
    assert not list((root / "projects" / project_id).glob("package-contexts/*"))
    assert client.get("/api/package-graph", headers=selection(project_id, valid)).status_code == 403
    assert client.get("/api/objects", headers=selection(project_id, valid)).status_code == 403
    docs_url = f"/api/projects/{project_id}/documents"
    text = b"Project context\npassword=do-not-expose\n" + b"e" * 70000
    document = post(client, docs_url, files={"file": ("context.md", text)} )
    assert document.status_code == 201, document.text
    assert not document.json()["semantic_review_started"]
    assert "do-not-expose" not in document.text
    assert client.get(docs_url).json()["total"] == 1
    assert post(client, docs_url, files={"file": ("context.md", text)}).status_code == 201
    assert client.get(docs_url).json()["total"] == 1
    # Document persistence does not create analysis work or expose raw text.
    assert app.state.projects.futures == {}
    result = post(client, f"/api/projects/{project_id}/analyze")
    assert result.status_code == 202, result.text
    job = complete(client, app, project_id, result.json()["id"])
    assert job["status"] == "partial" and job["completed_count"] == job["failed_count"] == 1
    assert {i["status"] for i in job["items"]} == {"completed", "failed"}
    for data in (valid, invalid):
        assert (root / "projects" / project_id / "staged" / (sha256(data).hexdigest() + ".xml")).read_bytes() == data
    assert client.get("/api/package-graph", headers=selection(project_id, valid)).json()["total_nodes"] == 1
    attached = client.get("/api/documents", headers=selection(project_id, valid)).json()
    assert attached["total"] == 1 and attached["items"][0]["metadata"]["scope"] == "project_wide"
    assert "do-not-expose" not in str(attached)
    assert post(client, f"/api/projects/{project_id}/analyze").status_code == 409
    assert not list(app.state.projects.uploads.iterdir())
    # Sample staging uses an immutable snapshot, not the later source contents.
    (inputs / "local.xml").write_bytes(xml("Original staged sample"))
    staged = post(client, f"/api/projects/{project_id}/packages/sample?stage_only=true", json={"names": ["local.xml"]})
    assert staged.status_code == 202 and staged.json()["status"] == "staged"
    (inputs / "local.xml").write_bytes(xml("Changed after staging"))
    result = post(client, f"/api/projects/{project_id}/analyze")
    assert complete(client, app, project_id, result.json()["id"])["status"] == "completed"
    overview = client.get("/api/overview", headers=selection(project_id, xml("Original staged sample"))).json()
    assert overview["workflows"][0]["name"] == "Original staged sample"


def test_staged_restart_corruption_busy_dedup_limits_and_project_document_validation(workspace, monkeypatch):
    client, app, root, inputs = workspace
    project_id = create(client)
    url = f"/api/projects/{project_id}/packages/upload?stage_only=true"
    assert post(client, url, files=[("files", ("a.xml", xml()))]).status_code == 202
    assert post(client, url, files=[("files", ("renamed.xml", xml()))]).status_code == 202
    assert len(client.get(f"/api/projects/{project_id}").json()["packages"]) == 1
    with monkeypatch.context() as patch:
        patch.setattr(projects, "MAX_FILES", 1)
        assert post(client, url, files=[("files", ("b.xml", xml("B")))]).status_code == 413
    docs_url = f"/api/projects/{project_id}/documents"
    for name, data, status in (("../a.txt", b"x", 400), ("a.xml", b"x", 415), ("a.txt", b"", 400),
                               ("a.txt", b"\xff", 422), ("a.txt", b" ", 422), ("bad.pdf", b"bad", 422)):
        assert post(client, docs_url, files={"file": (name, data)}).status_code == status
    assert post(client, docs_url, files={"file": ("a.md", b"x")}, data={"workflow_id": "anything"}).status_code == 422
    assert post(client, docs_url, headers={"Content-Length": str(10 * 1024 * 1024 + 65537)}, content=b"x").status_code == 413
    assert post(client, "/api/projects/" + "f" * 32 + "/documents", files={"file": ("a.md", b"x")}).status_code == 404
    assert not list(app.state.service.state.joinpath("uploads").iterdir())
    assert app.state.projects.slot.acquire(blocking=False)
    try:
        assert post(client, f"/api/projects/{project_id}/analyze").status_code == 409
    finally:
        app.state.projects.slot.release()
    app.state.projects.close()
    with client_for(create_app(root, app.state.service.sample)) as second:
        detail = second.get(f"/api/projects/{project_id}").json()
        assert detail["packages"][0]["status"] == "staged" and detail["jobs"] == []
        snapshot = root / "projects" / project_id / "staged" / (sha256(xml()).hexdigest() + ".xml")
        snapshot.write_bytes(b"corrupt")
        assert post(second, f"/api/projects/{project_id}/analyze").status_code == 409
        assert second.get(f"/api/projects/{project_id}").json()["jobs"] == []