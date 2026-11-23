"""Real Delta/Files adapter contracts with offline OBO identities, not an in-memory facade."""
from copy import deepcopy
import json

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from workbench import delta_state as ds
from workbench.architecture_knowledge import PILLARS
from workbench.knowledge_store import KnowledgeStore, CurateBody, project_for_root
from workbench.tests.test_architecture_knowledge import graph, HOST, USER, PROJECT
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B
from workbench.tests.test_hosted_registry import fresh, uploaded
from workbench.tests.test_hosted_object_reads import selected, full_index


def store_for(backend, tmp_path, token=TOKEN_A2):
    runtime = fresh(backend, tmp_path, token)
    runtime.store.ensure_tables()
    return KnowledgeStore(runtime.store, runtime.artifacts)


def test_immutable_uc_graph_restores_with_fresh_identity_no_ddl_on_read(backend, tmp_path):
    store = store_for(backend, tmp_path / "writer")
    value = graph()
    before = len(backend.objects)
    with pytest.raises(PermissionError):
        store.save(value, consent=False)
    assert len(backend.objects) == before
    receipt = store.save(value, consent=True)
    assert receipt["readback_verified"] and not receipt["deployment_allowed"]
    assert receipt["artifact"]["volume_path"].startswith("/Volumes/orion_dev_alice/workbench/artifacts/delta-state/users/")
    runtime = fresh(backend, tmp_path / "reader", TOKEN_A2)
    backend.calls.clear()
    loaded = KnowledgeStore(runtime.store, runtime.artifacts).load(PROJECT)
    assert loaded.value == value
    assert not any(action == "sql" and sql.startswith(("CREATE", "MERGE")) for _, action, sql in backend.calls)
    assert store.save(value, consent=True) == receipt
    assert len(store.manifests(PROJECT)) == 1
    assert all("pickle" not in raw.decode("utf-8", errors="ignore").lower() for raw in backend.objects.values())
    assert store.load("2" * 32) is None
    bob = store_for(backend, tmp_path / "bob", TOKEN_B)
    assert bob.load(PROJECT) is None
    with pytest.raises(PermissionError):
        bob.save(value, consent=True)


@pytest.mark.parametrize("case", ["missing", "tampered", "manifest", "permission"])
def test_unavailable_latest_never_falls_back_to_old_snapshot(backend, tmp_path, monkeypatch, case):
    store = store_for(backend, tmp_path)
    store.save(graph(), consent=True)
    newest = graph()
    receipt = store.save(newest, consent=True)
    path = receipt["artifact"]["volume_path"]
    if case == "missing":
        del backend.objects[path]
    elif case == "tampered":
        backend.objects[path] += b" "
    elif case == "manifest":
        row = next(r for rows in backend.rows.values() for r in rows if r["source_key"] == newest["version"])
        m = json.loads(row["payload"])
        m["project_id"] = "2" * 32
        row["payload"] = ds._json(m)
    else:
        monkeypatch.setattr(store.artifacts.store.client.files, "download", lambda *a, **k: (_ for _ in ()).throw(PermissionDenied("denied")))
    with pytest.raises((ValueError, PermissionError, NotFound, PermissionDenied)):
        store.load(PROJECT)


def test_failed_upload_does_not_publish_manifest(backend, tmp_path, monkeypatch):
    store = store_for(backend, tmp_path)
    monkeypatch.setattr(store.artifacts.store.client.files, "upload", lambda *a, **k: (_ for _ in ()).throw(PermissionDenied("denied")))
    with pytest.raises(PermissionDenied):
        store.save(graph(), consent=True)
    assert store.manifests(PROJECT) == []


def test_scoped_hosted_curation_api_explicit_consent_and_readiness(backend, uploaded, tmp_path, monkeypatch):
    import workbench.knowledge_store as ks
    project, package, _ = full_index(backend, uploaded, tmp_path / "full")
    calls = []
    def research(specs, workspace, user, selected_project):
        assert (workspace, user, selected_project) == (HOST, USER, project)
        calls.append(True)
        return graph(project=project)
    monkeypatch.setattr(ks, "curate", research)
    scope = selected(project, package)
    body = {"consent": False, "features": [__import__("workbench.knowledge_curation", fromlist=["seed_requests"]).seed_requests()[0]]}
    with browser(backend) as client:
        missing = client.get("/api/architecture-knowledge", headers=scope)
        assert missing.status_code == 200, missing.text
        assert missing.json()["fresh_ga_count"] is None
        refused = client.post("/api/architecture-knowledge/curate", headers={**scope, "X-Workbench-Request": "1"}, json=body)
        assert refused.status_code == 403 and not calls
        accepted = client.post("/api/architecture-knowledge/curate", headers={**scope, "X-Workbench-Request": "1"}, json={**body, "consent": True})
        assert accepted.status_code == 201, accepted.text
        assert len(calls) == 1 and accepted.json()["receipt"]["readback_verified"]
        assert client.get("/api/architecture-knowledge", headers=selected(project, package, TOKEN_B)).status_code in {401, 403, 404, 503}
        readiness = client.get("/api/readiness", headers=scope)
        assert readiness.status_code == 200, readiness.text
        k = readiness.json()["architecture_knowledge"]
        assert k["fresh_ga_count"] == 1 and set(k["pillars"]) == set(PILLARS)
        assert readiness.json()["counts"]["runtime_validated"] is None
        assert not readiness.json()["production_ready"]


def test_curation_schema_and_namespace_no_self_declared_state(tmp_path):
    from workbench.knowledge_curation import seed_requests
    from workbench.hosted_operations import HostedOperations
    spec = seed_requests()[0]
    with pytest.raises(ValueError):
        CurateBody.model_validate({"consent": True, "features": [{**spec, "release_state": "GA"}]})
    assert project_for_root(tmp_path / PROJECT / "package-contexts" / ("a" * 64)) == PROJECT
    assert project_for_root(tmp_path) is None
    for method, path in (("GET", "/api/architecture-knowledge"), ("POST", "/api/architecture-knowledge/curate")):
        request = {"id": "a" * 32, "method": method, "path": path, "body": {}}
        assert HostedOperations._request(request, ())[1]["path"] == path