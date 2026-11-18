"""Real hosted commit boundary, append-only observation, no execution replay."""
from copy import deepcopy
from hashlib import sha256
import gzip
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench import delta_state as ds
from workbench.hosted_operations import API, HostedOperations, current_progress
from workbench.hosted_storage import provision
from workbench.operation_reconciliation import reconcile_analysis
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A, HOST, create
from workbench.tests.test_hosted_registry import fresh
from workbench.tests.test_source_analysis import JAVA_XML


@pytest.fixture
def run_case(backend, tmp_path, monkeypatch):
    services = []
    with browser(backend) as client:
        project = create(client)
        response = client.post(f"/api/projects/{project}/packages/upload", params={"stage_only": "true"},
            headers=headers(), files={"files": ("java.xml", JAVA_XML, "application/xml")})
        assert response.status_code == 202, response.text
        assert client.post(f"/api/projects/{project}/analyze", headers=headers()).status_code == 202
        package = sha256(JAVA_XML).hexdigest()
        selected = headers(**{"X-Workbench-Project": project, "X-Workbench-Package": package})
        status = client.get("/api/source-analysis", headers=selected).json()
        # Match the already-restored live baseline. First-ever restore rewrites
        # JSON whitespace/key order, which this strict verifier rightly rejects
        # as canonical byte drift if included in the observed analysis commit.
        stable = fresh(backend, tmp_path / "stable-baseline")
        stable.restore(initialize=False)
        stable.flush()
        before = fresh(backend, tmp_path / "before")._latest()
        identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
        remote = provision(identity, "orion_dev_", initialize=False)

        def run(mode="after_commit"):
            service = HostedOperations("warehouse", "test-process")
            services.append(service)
            request = {"id": uuid4().hex, "method": "POST", "path": "/api/source-analysis/rebuild",
                "body": {"consent": True, "package_hash": package,
                         "expected_understanding_sha256": status["understanding_sha256"],
                         "expected_analysis_version": None}}
            calls = []
            original_read = service._read
            lost = []
            if mode == "progress_readback":
                def read(store, operation):
                    history = original_read(store, operation)
                    if history and history[-1]["progress"]["stage"] == "responding" and not lost:
                        lost.append(True)
                        return history[:-1]
                    return history
                monkeypatch.setattr(service, "_read", read)

            def execute(*args):
                calls.append(1)
                if mode == "before_commit":
                    raise RuntimeError("Injected pre-commit error")
                result = client.post(request["path"], headers=selected, json=request["body"])
                assert result.status_code == 200, result.text
                if mode == "after_commit":
                    raise RuntimeError("Injected lost response after verified publication")
                if mode == "progress_readback":
                    current_progress()("responding")
                return result.status_code, result.json()

            code, _ = service.handle(identity, remote, "POST", API, "", selected, request, execute)
            assert code == 202
            service.close()
            runtime = fresh(backend, tmp_path / ("read-" + request["id"]))
            store = service._store(identity, remote)
            terminal = service._read(store, request["id"])
            return SimpleNamespace(service=service, runtime=runtime, store=store, request=request,
                history=terminal, calls=calls, before=before, project=project, package=package,
                selected=selected, identity=identity, remote=remote, backend=backend, client=client)

        yield run
    for service in services:
        service.close()


def reconciliation(case):
    latest = case.runtime._latest()
    generation = latest["migration_id"]
    pointer = next(r for name, rows in case.backend.rows.items() if name.endswith(".artifact_manifest")
                   for r in rows if r["migration_id"] == generation and r["source_path"].endswith("/source-analysis.json"))
    data = json.loads(case.runtime._object(json.loads(pointer["payload"])))
    archive = next(r for name, rows in case.backend.rows.items() if name.endswith(".artifact_manifest")
                   for r in rows if r["migration_id"] == generation and r["source_path"].endswith(data["analysis_version"] + ".json.gz"))
    document = json.loads(gzip.decompress(case.runtime._object(json.loads(archive["payload"]))))
    return {"consent": True, "request": case.request, "project_id": case.project,
        "parent_generation": case.before["migration_id"], "committed_generation": generation,
        "analysis_version": data["analysis_version"], "artifact_sha256": data["artifact_sha256"],
        "analyzer_hashes": document["analyzer_hashes"]}


@pytest.mark.parametrize("mode", ["after_commit", "progress_readback"])
def test_committed_ack_failure_observation_is_separate_idempotent_and_never_replays(run_case, monkeypatch, mode):
    case = run_case(mode)
    assert case.history[-1]["status"] == "failed"
    assert case.history[-1]["failure_phase"] == ("journal_readback" if mode == "progress_readback" else "action_execution")
    body = reconciliation(case)
    original_rows = deepcopy(case.backend.rows)
    original_objects = deepcopy(case.backend.objects)
    def forbidden(*args, **kwargs):
        pytest.fail("Reconciliation must not execute/flush/parse/reupload")
    monkeypatch.setattr("workbench.understanding.analyze_package", forbidden)
    monkeypatch.setattr("workbench.delta_runtime.DeltaRuntime.flush", forbidden)
    monkeypatch.setattr("workbench.delta_runtime.DeltaRuntime.restore", forbidden)
    path = API + "/" + case.request["id"] + "/reconcile"
    verified = (reconcile_analysis(case.service, case.store, case.runtime, case.request["id"], body, case.selected)
                if mode == "after_commit" else None)
    response = case.client.post(path, headers=case.selected, json=body)
    assert response.status_code == 200, response.text
    observation = response.json()["observation"]
    if verified is not None:
        assert observation == verified
    assert observation["observation"] == "committed_with_ack_failure"
    assert observation["committed_generation"] == body["committed_generation"]
    assert observation["terminal_history_sha256"] == ds._hash(case.history)
    assert case.service._read(case.store, case.request["id"]) == case.history
    assert case.backend.objects == original_objects
    for name, rows in original_rows.items():
        assert all(row in case.backend.rows[name] for row in rows)
    added = [row for name, rows in case.backend.rows.items() for row in rows if row not in original_rows.get(name, [])]
    assert len(added) == 1 and added[0]["context_id"] == "hosted-reconciliation:" + case.request["id"]
    snapshot = deepcopy(case.backend.rows)
    repeated = case.client.post(path, headers=case.selected, json=body)
    assert repeated.status_code == 200 and repeated.json() == response.json()
    duplicate = case.service.handle(case.identity, case.remote, "POST", API, "", case.selected, case.request, forbidden)
    assert duplicate[0] == 200 and duplicate[1]["status"] == "failed"
    assert duplicate[1]["observation"] == observation
    assert case.backend.rows == snapshot and case.calls == [1]
    assert case.runtime._latest()["migration_id"] == body["committed_generation"]


def test_error_before_commit_stays_failed_and_cannot_reconcile(run_case):
    case = run_case("before_commit")
    assert case.runtime._latest() == case.before
    body = {"consent": True, "request": case.request, "project_id": case.project,
        "parent_generation": case.before["migration_id"], "committed_generation": uuid4().hex,
        "analysis_version": "1" * 64, "artifact_sha256": "2" * 64,
        "analyzer_hashes": {"understanding.py": "3" * 64, "source_evidence.py": "4" * 64}}
    snapshot = deepcopy(case.backend.rows)
    with pytest.raises(ValueError, match="current direct child"):
        reconcile_analysis(case.service, case.store, case.runtime, case.request["id"], body, case.selected)
    assert case.backend.rows == snapshot and case.history[-1]["status"] == "failed"


@pytest.mark.parametrize("field", ["consent", "project", "request", "overlay", "analyzer", "parent", "commit", "source_ref", "credential"])
def test_reconciliation_rejects_mismatched_pins_scope_or_canonical_state_without_write(run_case, field, monkeypatch):
    case = run_case()
    body = reconciliation(case)
    if field == "consent": body["consent"] = False
    elif field == "project": body["project_id"] = "0" * 32
    elif field == "request": body["request"]["body"]["expected_understanding_sha256"] = "0" * 64
    elif field == "overlay": body["artifact_sha256"] = "0" * 64
    elif field == "analyzer": body["analyzer_hashes"]["source_evidence.py"] = "0" * 64
    elif field == "parent": body["parent_generation"] = "0" * 32
    elif field == "commit": body["committed_generation"] = "0" * 32
    elif field == "credential":
        body["analyzer_hashes"]["source_evidence.py"] = "0" * 64
        monkeypatch.setattr(case.store.client.config, "authenticate", lambda: {"Authorization": "Bearer " + "0" * 64})
    else:
        row = next(r for name, rows in case.backend.rows.items() if name.endswith(".artifact_manifest") for r in rows
                   if r["migration_id"] == body["committed_generation"] and r["source_path"].endswith("/source.xml"))
        row["artifact_sha256"] = "0" * 64
    snapshot = deepcopy(case.backend.rows)
    with pytest.raises((ValueError, PermissionError)):
        reconcile_analysis(case.service, case.store, case.runtime, case.request["id"], body, case.selected)
    assert case.backend.rows == snapshot


def test_actual_analysis_result_gates_complete_and_do_not_need_reconciliation(run_case):
    case = run_case("normal")
    assert case.history[-1]["status"] == "completed"
    assert case.history[-1]["result"]["generation_allowed"] is False
    assert case.history[-1]["result"]["deployment_allowed"] is False
    with pytest.raises(ValueError, match="failed request"):
        reconcile_analysis(case.service, case.store, case.runtime, case.request["id"], reconciliation(case), case.selected)