"""Direct offline regression checks, deliberately not a pytest suite or model run."""
import ast
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event, Lock, Semaphore
from types import SimpleNamespace
from unittest.mock import patch

from workbench.agent_recovery import (
    _digest, _snapshot, checkpoint_receipts, recover_design, recovered_output,
    safe_exception_class, verified_output,
)
from workbench.agents import _Evidence, _result, build_agents
from workbench.app import PackageIndex, _now
from workbench.domain import _hash, _redact
from workbench.redesign import capture_run
from workbench.retrieval_receipts import replay_planning


def rejects(action, expected=(ValueError, PermissionError)):
    try:
        action()
    except expected:
        return
    raise AssertionError("Expected verification rejection")


def worker_check():
    source = ast.parse(Path("workbench/app.py").read_text(encoding="utf-8"))
    function = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == "run_agent")
    output = {"status": "draft_available", "run_id": "a" * 32, "findings": [{"summary": "Retained"}],
              "password": "never-public"}
    updates = []
    service = SimpleNamespace(runtime=SimpleNamespace(run=lambda *a, **k: output),
        update_record=lambda r, **k: updates.append(deepcopy(r)), lock=Lock(),
        agent_cancel_events={}, agent_slot=Semaphore(0))
    namespace = {"service": service, "json": json, "_redact": _redact, "_now": _now}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "worker-regression", "exec"), namespace)

    def failed_capture(*args):
        assert updates[-1]["output"]["findings"] == output["findings"]
        assert "never-public" not in json.dumps(updates)
        raise ValueError("credential=never-public")

    with patch("workbench.redesign.capture_run", failed_capture):
        namespace["run_agent"]({"id": "b" * 32}, SimpleNamespace(model={}),
                               SimpleNamespace(prompt="", workflow_id="wf", scenario="bronze",
                                               knowledge_preview_consent=False),
                               json.dumps({"documents": [], "reviews": []}), Event())
    final = updates[-1]
    assert final["status"] == "failed" and final["failure_stage"] == "capture_redesign"
    assert final["exception_class"] == "ValueError" and final["output"]["run_id"] == "a" * 32
    assert "never-public" not in json.dumps(final)
    custom = type("secret_exception_name", (ValueError,), {})
    assert safe_exception_class(custom("secret")) == "ValueError"
    print("PASS sanitized_output_before_capture_and_safe_failure")


def producer_check():
    from workbench.tests.test_source_evidence import package
    from workbench.tests.test_redesign import chat
    with TemporaryDirectory(prefix="orion-retrieval-producer-") as temp:
        model, ids = package.__wrapped__(Path(temp))
        fake = chat()
        with patch.object(type(fake), "_generate", side_effect=AssertionError("Inference forbidden")):
            with build_agents(Path(temp) / "agents", model, ids["Parent"], "original", chat_model=fake) as bundle:
                tool = bundle.tools["architecture"]["workflow_planning_contract"]
                assert tool.invoke({"related_workflow_id": ids["Child"]})["status"] == "related_workflow_not_retrieved"
                tool.invoke({"section": "invocations"})
                tool.invoke({"section": "embedded_code", "related_workflow_id": ids["Child"]})
                assert bundle.tools["architecture"]["lookup_evidence"].invoke({"evidence_id": ids["Java"]})["id"] == ids["Java"]
                assert bundle.tools["supervisor"]["lookup_evidence"].invoke({"evidence_id": ids["Java"]})["status"] == "not_found_or_out_of_scope"
                receipts = _result(bundle, "draft_available", "Direct check")["planning_retrievals"]
                base = _Evidence(model, ids["Parent"], [], []).records
                replayed = replay_planning(model, ids["Parent"], receipts, base)
                assert ids["Java"] in replayed["architecture"] and "supervisor" not in replayed
                forged = deepcopy(receipts)
                forged[0]["evidence_ids"].append(ids["Unrelated"])
                rejects(lambda: replay_planning(model, ids["Parent"], forged, base))
                wrong = deepcopy(receipts)
                wrong[1]["workflow_id"] = ids["Unrelated"]
                rejects(lambda: replay_planning(model, ids["Parent"], wrong, base))
                other_role = deepcopy(receipts)
                other_role[1]["role"] = "supervisor"
                rejects(lambda: replay_planning(model, ids["Parent"], other_role, base))
                rejects(lambda: replay_planning(model, ids["Parent"], receipts[1:], base))
    print("PASS live_producer_role_local_receipts_no_inference_forged_and_unrelated_rejected")


def api_check(root, index, public_id, pins, output):
    """Exercise the actual route bodies in-process, without starting the workbench."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import workbench.app as application
    app = FastAPI()
    service = SimpleNamespace(root=root, state=root / "state", agent_slot=Semaphore(1),
        require=lambda: index,
        record=lambda identifier, **kwargs: json.loads((root / "state" / "agent-runs" / (identifier + ".json")).read_bytes()),
        run_freshness=lambda i, r: {**r, "stale": False})
    tree = ast.parse(Path("workbench/app.py").read_text(encoding="utf-8"))
    names = {"agent_run", "recover_agent_output"}
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {**vars(application), "app": app, "service": service}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "recovery-api-regression", "exec"), namespace)
    protected = {p: _digest(p) for p in (root / "state" / "agent-runs").glob("*.json")}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        path = "/api/agents/runs/" + public_id
        detail = client.get(path)
        assert detail.status_code == 200
        value = detail.json()
        assert "output" not in value and value["recovered_output"] == _redact(output)
        assert value["status"] == "failed" and value["output_recovery_receipt"]["pins"] == pins
        assert client.post(path + "/recover-output", json={"consent": False, "pins": pins}).status_code == 403
        assert client.post(path + "/recover-output", json={"consent": True, "pins": {**pins, "public_sha256": "0" * 64}}).status_code == 409
        assert service.agent_slot.acquire(blocking=False)
        try:
            assert client.post(path + "/recover-output", json={"consent": True, "pins": pins}).status_code == 409
        finally:
            service.agent_slot.release()
        response = client.post(path + "/recover-output", json={"consent": True, "pins": pins})
        assert response.status_code == 200 and response.json()["writer_invoked"] is False
    assert all(_digest(path) == digest for path, digest in protected.items())
    print("PASS actual_detail_and_recovery_API_consent_pins_busy_original_history")


def historical_check(root, public_id):
    root = Path(root).resolve()
    record, output, ledger, pins = verified_output(root, public_id)
    public = root / "state" / "agent-runs" / (public_id + ".json")
    checkpoint = ledger.parent / "checkpoint_architecture.sqlite3"
    protected = {p: _digest(p) for p in (public, ledger, checkpoint)}
    index = PackageIndex.load(root / "packages" / record["package_hash"])
    snapshot = _snapshot(root, record, index, output["input_hashes"])
    evidence = _Evidence(index.model, record["workflow_id"], [], [])
    receipts = checkpoint_receipts(checkpoint, "architecture", output["run_id"], record["workflow_id"],
                                  index.model, evidence.records, output["redesigns"])
    child = "1f2d8e0005f5c66ad13b678b3d05600dfe66397ef3b8f0fb1ff745e725c7ec1e"
    assert child not in evidence.records
    authorized = replay_planning(index.model, record["workflow_id"], receipts, evidence.records)
    assert child in authorized["architecture"]
    # Invocation metadata grants this exact returned child, not its implementation closure.
    child_owned = {n["id"] for n in index.model["nodes"] if n.get("parent_id") == child}
    assert child_owned - authorized["architecture"].keys()
    with TemporaryDirectory(prefix="orion-exact-recovery-") as temp:
        target = Path(temp)
        state = target / "state"
        (state / "agent-runs").mkdir(parents=True)
        shutil.copy2(public, state / "agent-runs" / public.name)
        shutil.copy2(root / "state" / "reviews.sqlite", state / "reviews.sqlite")
        identities = target / "agents" / "identities" / ledger.parent.name
        identities.mkdir(parents=True)
        shutil.copy2(ledger, identities / ledger.name)
        shutil.copy2(checkpoint, identities / checkpoint.name)
        service = SimpleNamespace(state=state)
        rejects(lambda: capture_run(service, index, {**record, "redesign_snapshot": snapshot}, output))
        assert not (state / "redesigns.sqlite").exists()
        forged = deepcopy(receipts)
        forged[-1]["evidence_ids"].append("f" * 64)
        rejects(lambda: capture_run(service, index, {**record, "redesign_snapshot": snapshot},
                                    {**output, "planning_retrievals": forged}))
        bad_pins = {**pins, "runtime_run_id": "f" * 32}
        rejects(lambda: recover_design(target, public_id, bad_pins, consent=True, index=index))
        rejects(lambda: recover_design(target, public_id, pins, consent=False, index=index))
        observation = recover_design(target, public_id, pins, consent=True, index=index)
        assert recover_design(target, public_id, pins, consent=True, index=index) == observation
        with closing(sqlite3.connect(state / "redesigns.sqlite")) as db:
            rows = db.execute("SELECT payload FROM redesign_versions").fetchall()
            assert len(rows) == 1 and json.loads(rows[0][0])["design"] == output["redesigns"][0]["design"]
        observation_path = state / "agent-recoveries.sqlite"
        with closing(sqlite3.connect(observation_path)) as db:
            assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
            rejects(lambda: db.execute("UPDATE observations SET payload='{}'"), (sqlite3.IntegrityError,))
        display = recovered_output(target, public_id)
        assert display["status"] == "verified" and len(display["output"]["findings"]) == 6
        assert display["output"] == _redact(output) and display["design_persistence"] == "recovered_original"
        assert display["observations"][0]["redesign_refs"] == observation["redesign_refs"]
        assert _digest(state / "agent-runs" / public.name) == pins["public_sha256"]
        assert _digest(identities / ledger.name) == protected[ledger]
        assert _digest(identities / checkpoint.name) == protected[checkpoint]
        api_check(target, index, public_id, pins, output)
    assert all(_digest(path) == digest for path, digest in protected.items())
    print("PASS exact_original_capture_repro_replay_idempotency_consent_pins_appendonly_history_hashes")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--public-run-id", required=True)
    args = parser.parse_args()
    worker_check()
    producer_check()
    historical_check(args.root, args.public_run_id)
    print("DIRECT_RECOVERY_CHECKS=PASS; no suites, models, cloud calls or real-history writes")