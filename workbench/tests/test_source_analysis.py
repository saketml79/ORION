"""Saved older artifact enrichment, no remote access or model invocation."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from workbench.app import PackageIndex, _atomic_json, _digest, _write_model, create_app
from workbench.migration_planning import PlanningIndex
from workbench.tests.test_app import client_for, HEADERS
from workbench.understanding import analyze_package


JAVA_XML = ('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
<MAPPING NAME="M"><TRANSFORMATION NAME="Java" TYPE="Custom Transformation">
<METADATAEXTENSION DOMAINNAME="Java_Transform_Domain" NAME="OnInputRow_Method_Snippet" VALUE='''
            + quoteattr('String password="private-secret";\ndo { Thread.sleep(300000); timeoutcnt++; } while (total > 0);')
            + '''/></TRANSFORMATION></MAPPING><SESSION NAME="S" MAPPINGNAME="M"/>
<WORKFLOW NAME="W"><TASKINSTANCE NAME="T" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
</FOLDER></REPOSITORY></POWERMART>''').encode()


def saved_legacy(root, xml=JAVA_XML):
    source = root / "input.xml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(xml)
    model = analyze_package(source)
    for node in model["nodes"]:
        for prop in node.get("properties", []):
            prop.pop("embedded_code", None)
    package = model["package"]["sha256"]
    directory = root / "packages" / package
    directory.mkdir(parents=True)
    (directory / "source.xml").write_bytes(xml)
    _write_model(directory / "understanding.json.gz", model)
    (directory / "english.md").write_text("Historical immutable explanation", encoding="utf-8")
    _atomic_json(directory / "manifest.json", {"sha256": package, "schema_version": model["schema_version"],
        "node_count": len(model["nodes"]), "edge_count": len(model["edges"]),
        "understanding_sha256": _digest(directory / "understanding.json.gz")})
    _atomic_json(root / "state" / "active.json", {"sha256": package, "activated_at": "historical"})
    return directory, model


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    directory, model = saved_legacy(tmp_path / "saved")
    app = create_app(directory.parents[1], tmp_path / "absent.xml")
    with client_for(app) as client:
        assert app.state.service.active is not None
        yield client, app.state.service, directory, model


def rebuild_body(client):
    status = client.get("/api/source-analysis").json()
    return {"package_hash": status["package_hash"],
            "expected_understanding_sha256": status["understanding_sha256"],
            "expected_analysis_version": status["analysis_version"], "consent": True}


def test_explicit_rebuild_joins_java_without_changing_canonical_history_or_ids(legacy):
    client, service, directory, model = legacy
    workflow = next(n["id"] for n in model["nodes"] if n["kind"] == "workflow")
    index = service.require()
    proposal = service.lite_records(index, "original")[0]
    service.store.save_review(model["package"]["sha256"], proposal["id"], proposal["version"],
                              "comment", "Preserve the historical human review")
    old_inputs = service.agent_inputs(index, workflow, "original")
    history = deepcopy(service.store.load_state(model["package"]["sha256"]))
    canonical = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    active = (service.state / "active.json").read_bytes()
    before_model = deepcopy(index.model)
    assert PlanningIndex(index.model).agent_contract(workflow, "embedded_code")["total"] == 0
    body = rebuild_body(client)
    response = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["embedded_code_records"] == 1 and result["canonical_artifacts_unchanged"]
    assert not result["generation_allowed"] and not result["deployment_allowed"]
    status = client.get("/api/source-analysis").json()
    assert status["analysis_state"] == "current" and not status["explicit_rebuild_required"]
    assert all((directory / name).read_bytes() == raw for name, raw in canonical.items())
    assert (service.state / "active.json").read_bytes() == active
    assert service.store.load_state(model["package"]["sha256"]) == history
    assert index.model == before_model
    enriched = service.require()
    assert set(enriched.nodes) == set(index.nodes) and enriched.model["edges"] == index.model["edges"]
    report = PlanningIndex(enriched.model).agent_contract(workflow, "embedded_code")
    assert report["total"] == 1 and "Thread.sleep(300000)" in json.dumps(report)
    assert "private-secret" not in json.dumps(report)
    assert service.agent_inputs(enriched, workflow, "original")["input_version"] != old_inputs["input_version"]
    assert client.post("/api/source-analysis/rebuild", headers=HEADERS, json=body).status_code == 409
    second = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=rebuild_body(client))
    assert second.status_code == 200 and second.json()["analysis_version"] == result["analysis_version"]
    assert len(list((directory / "analysis").glob("*.json.gz"))) == 1
    cold = PackageIndex.load(directory, cache_scope="restart-other-scope")
    assert cold.model == enriched.model


@pytest.mark.parametrize("case", ["consent", "package", "base", "version", "package_busy", "agent_busy", "source"])
def test_rebuild_fresh_scope_and_consent_guards(legacy, case):
    client, service, directory, _ = legacy
    body = rebuild_body(client)
    acquired = None
    if case == "consent":
        body["consent"] = False
    elif case == "package":
        body["package_hash"] = "0" * 64
    elif case == "base":
        body["expected_understanding_sha256"] = "0" * 64
    elif case == "version":
        body["expected_analysis_version"] = "0" * 64
    elif case in {"package_busy", "agent_busy"}:
        acquired = service.package_slot if case == "package_busy" else service.agent_slot
        assert acquired.acquire(blocking=False)
    else:
        (directory / "source.xml").write_bytes(b"corrupt")
    try:
        response = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=body)
        assert response.status_code == (403 if case == "consent" else 409), response.text
        assert not (directory / "source-analysis.json").exists()
    finally:
        if acquired:
            acquired.release()


def test_get_never_reparses_and_warm_cache_cannot_mask_corrupt_sidecar(legacy, monkeypatch):
    client, service, directory, _ = legacy
    result = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=rebuild_body(client))
    assert result.status_code == 200
    def forbidden(*args, **kwargs):
        pytest.fail("GET must not reparse or rebuild saved analysis")
    monkeypatch.setattr("workbench.understanding.analyze_package", forbidden)
    assert not client.get("/api/source-analysis").json()["source_reparsed"]
    PackageIndex.load(directory)
    sidecar = directory / "analysis" / (result.json()["analysis_version"] + ".json.gz")
    sidecar.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        PackageIndex.load(directory)


def test_versions_are_immutable_and_previous_versions_remain_available(legacy, monkeypatch):
    client, service, directory, _ = legacy
    first = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=rebuild_body(client)).json()
    old = directory / "analysis" / (first["analysis_version"] + ".json.gz")
    raw = old.read_bytes()
    import workbench.source_analysis as analysis
    digest = analysis._digest
    monkeypatch.setattr(analysis, "_digest", lambda path: "1" * 64 if Path(path).name == "source_evidence.py" else digest(path))
    second = client.post("/api/source-analysis/rebuild", headers=HEADERS, json=rebuild_body(client))
    assert second.status_code == 200, second.text
    assert second.json()["analysis_version"] != first["analysis_version"]
    assert old.read_bytes() == raw and len(list((directory / "analysis").glob("*.json.gz"))) == 2
    monkeypatch.setattr(analysis, "_digest", digest)
    status = client.get("/api/source-analysis").json()
    assert status["analysis_state"] == "stale_analyzer" and status["explicit_rebuild_required"]