"""Offline loop regressions: real SQLite, parser, HTTP and shared worker slot.

Only runtime.run is a fake adapter. These tests make no model/provider calls.
Executor barriers and events, not timing guesses, establish completion.
"""
from copy import deepcopy
import json
import sqlite3
from threading import Event

from fastapi.testclient import TestClient
import pytest

from workbench.agents import ROLES, _Evidence
from workbench.app import _create_package_app, create_app
from workbench.loops import GATES, grade


HEADERS = {"X-Workbench-Request": "1"}
XML = b'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>
<WORKFLOW NAME="One"><TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>
<WORKFLOW NAME="Two"/></FOLDER></REPOSITORY></POWERMART>'''


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


def post(client, path, body=None, **kwargs):
    return client.post(path, headers={**HEADERS, **kwargs.pop("headers", {})}, json=body, **kwargs)


def supported(workflow_id):
    return {"status": "advisory", "summary": "Structural advice, not parity.", "run_id": "underlying-trace",
            "findings": [{"role": role, "status": "supported", "summary": "Scoped structural finding",
                          "evidence_ids": [workflow_id]} for role in ROLES]}


class Runtime:
    def __init__(self):
        self.calls = []
        self.respond = lambda call, number: supported(call[1])
        self.ready = True

    def status(self):
        # Explicit workspace role-model readiness, without WORKBENCH_MODEL.
        return {"available": self.ready, "authentication": "model_provided"}

    def run(self, *args):
        self.calls.append(deepcopy(args))
        return self.respond(args, len(self.calls))


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    runtime = Runtime()
    source = tmp_path / "sample.xml"
    source.write_bytes(XML)
    app = _create_package_app(tmp_path / "data", source, runtime)
    with client_for(app) as client:
        result = post(client, "/api/packages/sample")
        assert result.status_code == 202
        app.state.service.package_executor.submit(lambda: None).result(timeout=10)
        assert client.get("/api/jobs/" + result.json()["id"]).json()["status"] == "completed"
        workflows = client.get("/api/overview").json()["workflows"]
        ids = {w["name"]: w["id"] for w in workflows}
        yield client, app, runtime, ids


def submit(client, workflow_id, **values):
    response = post(client, "/api/loops/run", {"workflow_id": workflow_id, "scenario": "original", "consent": True, **values})
    assert response.status_code == 202, response.text
    return response.json()


def completed(client, app, record):
    app.state.service.agent_executor.submit(lambda: None).result(timeout=10)
    response = client.get("/api/loops/runs/" + record["id"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] not in {"queued", "running"}, result
    return result


def assert_gates(record):
    for key, value in GATES.items():
        assert record[key] == value


def test_read_only_overview_head_no_package_or_model_calls(tmp_path):
    runtime = Runtime()
    app = _create_package_app(tmp_path / "empty", tmp_path / "none.xml", runtime)
    with client_for(app) as client:
        response = client.get("/api/loops")
        assert response.status_code == 200
        data = response.json()
        assert len(data["definitions"]) == 4
        assert data["runs"] == data["events"] == data["lessons"] == []
        assert data["current_auth_status"]["inference_ready"]
        assert not data["current_auth_status"]["authentication_verified"]
        assert client.head("/api/loops").status_code == 200
        assert runtime.calls == []
        assert_gates(data)


def test_real_runtime_adapter_call_trace_usage_and_audit(ready):
    client, app, runtime, ids = ready
    run = completed(client, app, submit(client, ids["One"], prompt="Inspect this workflow"))
    assert len(runtime.calls) == 1
    model, workflow, scenario, prompt, documents, feedback = runtime.calls[0]
    assert model == app.state.service.active.model and workflow == ids["One"]
    assert scenario == "original" and prompt == "Inspect this workflow"
    assert documents == feedback == []
    assert run["status"] == "completed" and run["outcome"] == "valid_advisory"
    attempt = run["attempts"][0]
    assert len(attempt["findings"]) == 6 and all(c["passed"] for c in attempt["rubric"]["checks"])
    assert attempt["trace_reference"]["runtime_run_id"] == "underlying-trace"
    assert attempt["usage_status"] == "unknown"
    assert attempt["input_tokens"] is attempt["output_tokens"] is attempt["total_tokens"] is None
    assert not run["stale"] and run["input_version"] == run["current_input_version"]
    assert_gates(run)
    assert_gates(attempt)
    rows = client.get("/api/logs", params={"run_id": run["id"]}).json()["items"]
    assert {r["event"] for r in rows} == {"loop_started", "loop_verification", "loop_completed"}
    assert all(r["input_tokens"] is None for r in rows)


@pytest.mark.parametrize("defect", ["missing_role", "foreign_citation", "empty_citation", "schema", "status"])
def test_repairable_defects_retry_actual_adapter_on_same_snapshot(ready, defect):
    client, app, runtime, ids = ready
    def respond(call, number):
        output = supported(call[1])
        if number == 1:
            if defect == "missing_role":
                output["findings"].pop()
                output["status"] = "needs_information"  # Existing runtime missing-role status.
            elif defect == "foreign_citation":
                output["findings"][0]["evidence_ids"] = [ids["Two"]]
            elif defect == "empty_citation":
                output["findings"][0]["evidence_ids"] = []
            elif defect == "schema":
                output["findings"][0]["unexpected"] = "extra"
            else:
                output["status"] = "invented"
        return output
    runtime.respond = respond
    result = completed(client, app, submit(client, ids["One"], max_attempts=3))
    assert len(runtime.calls) == result["attempts_count"] == 2
    assert result["outcome"] == "valid_advisory"
    assert result["attempts"][0]["rubric"]["repairable"]
    assert "Repair advisory output only" in runtime.calls[1][3]
    assert runtime.calls[0][:3] == runtime.calls[1][:3]
    assert runtime.calls[0][4:] == runtime.calls[1][4:]


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_retry_budget_persisted_and_never_exceeded(ready, budget):
    client, app, runtime, ids = ready
    runtime.respond = lambda call, number: {"status": "advisory", "findings": []}
    result = completed(client, app, submit(client, ids["One"], max_attempts=budget))
    assert len(runtime.calls) == len(result["attempts"]) == budget
    assert result["outcome"] == "invalid_output"
    assert [a["number"] for a in result["attempts"]] == list(range(1, budget + 1))


@pytest.mark.parametrize("reason", ["needs_information", "unsupported", "blocker", "runtime_blocker", "failed", "authority"])
def test_missing_external_evidence_failures_and_authority_are_not_retryable(ready, reason):
    client, app, runtime, ids = ready
    def respond(call, number):
        output = supported(call[1])
        if reason in {"needs_information", "unsupported", "failed"}:
            output["findings"][0]["status"] = reason
        elif reason == "blocker":
            output["findings"][0]["blockers"] = ["Owner lifecycle evidence is missing"]
        elif reason == "runtime_blocker":
            output["blockers"] = ["Independent source-binding evidence is missing"]
        else:
            output["approved"] = True
        return output
    runtime.respond = respond
    result = completed(client, app, submit(client, ids["One"], max_attempts=3))
    assert len(runtime.calls) == 1
    assert result["outcome"] == ("failed" if reason in {"failed", "authority"} else "needs_information")
    assert not result["attempts"][0]["rubric"]["repairable"]
    assert_gates(result)


def test_node_edge_document_feedback_citations_use_actual_evidence_format(ready):
    client, app, runtime, ids = ready
    index = app.state.service.active
    assert post(client, "/api/documents", files={"file": ("design.md", b"Owner evidence", "text/markdown")}).status_code == 201
    proposal = next(p for p in client.get("/api/proposals").json()["items"] if p["workflow_id"] == ids["One"])
    assert post(client, "/api/reviews", {"proposal_id": proposal["id"], "expected_version": 1,
                                       "action": "comment", "comment": "Check transaction boundary"}).status_code == 200
    inputs = app.state.service.agent_inputs(index, ids["One"], "original")
    snapshot = json.loads(inputs["snapshot"])
    def respond(call, number):
        assert call[4] == snapshot["documents"] and call[5] == snapshot["reviews"]
        evidence = _Evidence(call[0], call[1], call[4], call[5])
        refs = [call[1], evidence.scoped_edges[0]["id"], *evidence.external_refs]
        assert any(r.startswith("document:0:") for r in refs)
        assert any(r.startswith("feedback:0:") for r in refs)
        output = supported(call[1])
        for finding in output["findings"]:
            finding["evidence_ids"] = refs
        return output
    runtime.respond = respond
    result = completed(client, app, submit(client, ids["One"]))
    assert result["outcome"] == "valid_advisory"
    assert result["input_versions"] == inputs["input_versions"]


def test_frozen_inputs_and_event_invalidation_while_running(ready):
    client, app, runtime, ids = ready
    entered, release = Event(), Event()
    def respond(call, number):
        if number == 1:
            entered.set()
            assert release.wait(10)
            call[4].append({"adapter_mutation": True})
            return {"status": "advisory", "findings": []}
        assert call[4] == call[5] == []
        return supported(call[1])
    runtime.respond = respond
    try:
        run = submit(client, ids["One"])
        assert entered.wait(10)
        assert post(client, "/api/documents", files={"file": ("new.md", b"New evidence", "text/markdown")}).status_code == 201
        event = post(client, "/api/loops/events", {"workflow_id": ids["One"], "event_type": "document_added", "reference": "new evidence"})
        assert event.status_code == 201 and event.json()["invalidated_runs"] == 1
        assert not event.json()["rerun_scheduled"]
    finally:
        release.set()
    result = completed(client, app, run)
    assert len(runtime.calls) == 2 and result["stale"]
    assert result["input_version"] != result["current_input_version"]
    assert result["stale_event_id"] == event.json()["id"]


@pytest.mark.parametrize("event_type", ["document_added", "review_changed", "source_binding_changed", "manual_request"])
def test_events_are_persisted_scoped_and_never_infer(ready, event_type):
    client, app, runtime, ids = ready
    one = completed(client, app, submit(client, ids["One"]))
    two = completed(client, app, submit(client, ids["Two"]))
    event = post(client, "/api/loops/events", {"workflow_id": ids["One"], "event_type": event_type, "reference": "token=private-value"})
    assert event.status_code == 201
    assert event.json()["reference"] == "[REDACTED]"
    assert client.get("/api/loops/runs/" + one["id"]).json()["stale"]
    assert not client.get("/api/loops/runs/" + two["id"]).json()["stale"]
    assert len(runtime.calls) == 2
    assert client.get("/api/loops").json()["events"][0]["id"] == event.json()["id"]


def test_draft_review_learning_applies_only_approved_workflow_feedback(ready):
    client, app, runtime, ids = ready
    first = completed(client, app, submit(client, ids["One"]))
    def draft(text):
        response = post(client, "/api/loops/lessons", {"run_id": first["id"], "lesson": text, "scope": "workflow_only"})
        assert response.status_code == 201
        return response.json()
    lesson = draft("Check scoped owner evidence; password=do-not-save")
    rejected = draft("Rejected guidance")
    draft("Pending guidance")
    # Drafts cannot become runtime feedback.
    completed(client, app, submit(client, ids["One"]))
    assert runtime.calls[-1][5] == []
    approved = post(client, "/api/loops/lessons/" + lesson["id"] + "/review", {"decision": "approve", "comment": "Local review"})
    assert approved.status_code == 200
    assert approved.json()["authority"] == "local_reviewer_unverified"
    assert approved.json()["production_promotion_allowed"] is False
    assert_gates(approved.json())
    assert post(client, "/api/loops/lessons/" + rejected["id"] + "/review", {"decision": "reject"}).status_code == 200
    assert post(client, "/api/loops/lessons/" + lesson["id"] + "/review", {"decision": "reject"}).status_code == 409
    assert client.get("/api/loops/runs/" + first["id"]).json()["stale"]
    second = completed(client, app, submit(client, ids["One"]))
    feedback = runtime.calls[-1][5]
    assert len(feedback) == 1 and feedback[0]["lesson_id"] == lesson["id"]
    assert "untrusted" in feedback[0]["trust"]
    assert "do-not-save" not in feedback[0]["lesson"]
    assert first["input_hashes"]["approved_lessons"] != second["input_hashes"]["approved_lessons"]
    completed(client, app, submit(client, ids["Two"]))
    assert runtime.calls[-1][5] == []
    # Workflow-only lessons carry over scenarios, never over workflow identity.
    completed(client, app, submit(client, ids["One"], scenario="bronze"))
    assert runtime.calls[-1][5] == feedback
    events = app.state.audit.records()[0]
    assert any(e["event"] == "loop_learning_drafted" for e in events)
    assert any(e["event"] == "loop_learning_reviewed" for e in events)


def test_shared_slot_blocks_other_runs_and_releases_after_runtime_failure(ready):
    client, app, runtime, ids = ready
    entered, release = Event(), Event()
    def fail(call, number):
        entered.set()
        assert release.wait(10)
        raise RuntimeError("Bearer never-persist-provider-secret")
    runtime.respond = fail
    try:
        run = submit(client, ids["One"])
        assert entered.wait(10)
        body = {"workflow_id": ids["One"], "consent": True}
        assert post(client, "/api/loops/run", body).status_code == 409
        assert post(client, "/api/agents/run", body).status_code == 409
        assert not app.state.service.agent_slot.acquire(blocking=False)
    finally:
        release.set()
    result = completed(client, app, run)
    assert result["status"] == result["attempts"][0]["status"] == "failed"
    assert "never-persist-provider-secret" not in json.dumps(result)
    runtime.respond = lambda call, number: supported(call[1])
    assert completed(client, app, submit(client, ids["One"]))["outcome"] == "valid_advisory"


def test_scheduling_failure_marks_run_failed_and_releases_slot(ready, monkeypatch):
    client, app, runtime, ids = ready
    def fail(*args, **kwargs):
        raise RuntimeError("executor shut down")
    with monkeypatch.context() as patch:
        patch.setattr(app.state.service.agent_executor, "submit", fail)
        with pytest.raises(RuntimeError, match="executor shut down"):
            submit(client, ids["One"])
    assert app.state.loops.overview()["runs"][0]["status"] == "failed"
    assert completed(client, app, submit(client, ids["One"]))["outcome"] == "valid_advisory"


def test_reported_usage_only_and_all_sensitive_text_redacted(ready):
    client, app, runtime, ids = ready
    def respond(call, number):
        assert call[3] == "[REDACTED]"
        output = supported(call[1])
        output.update(summary="Bearer private-summary", run_id="password=private-trace",
                      usage_metadata={"input_tokens": 12, "output_tokens": 7, "provider_token": "private-usage"})
        output["findings"][0]["summary"] = "api_key=private-finding"
        return output
    runtime.respond = respond
    run = completed(client, app, submit(client, ids["One"], prompt="token=private-prompt"))
    attempt = run["attempts"][0]
    assert attempt["input_tokens"] == 12 and attempt["output_tokens"] == 7
    assert attempt["total_tokens"] is None and attempt["usage_status"] == "reported_by_runtime"
    with sqlite3.connect(app.state.loops.path) as db:
        contents = " ".join(row[0] for table in ("runs", "attempts", "events", "lessons")
                            for row in db.execute(f"SELECT payload FROM {table}"))
    assert "private-" not in contents
    verification = next(e for e in app.state.audit.records()[0] if e["event"] == "loop_verification")
    assert verification["input_tokens"] == 12 and verification["total_tokens"] is None


def test_sqlite_restart_recovers_only_interrupted_work_no_auto_execution(ready):
    client, app, runtime, ids = ready
    run = completed(client, app, submit(client, ids["One"]))
    event = post(client, "/api/loops/events", {"workflow_id": ids["One"], "event_type": "manual_request", "reference": "manual"}).json()
    lesson = post(client, "/api/loops/lessons", {"run_id": run["id"], "lesson": "Remember local evidence", "scope": "workflow_only"}).json()
    assert post(client, "/api/loops/lessons/" + lesson["id"] + "/review", {"decision": "approve"}).status_code == 200
    abandoned = {k: v for k, v in run.items() if k != "attempts"}
    abandoned.update(id="interrupted-run", status="running")
    app.state.loops._save("runs", abandoned)
    attempt = {**abandoned, "id": "interrupted-attempt", "run_id": abandoned["id"]}
    app.state.loops._save("attempts", attempt)
    app.state.service.close()
    restarted_runtime = Runtime()
    restarted = _create_package_app(app.state.service.root, app.state.service.sample, restarted_runtime)
    with client_for(restarted) as other:
        restored = other.get("/api/loops/runs/" + run["id"]).json()
        assert restored["stale"] and len(restored["attempts"]) == 1
        interrupted = other.get("/api/loops/runs/interrupted-run").json()
        assert interrupted["status"] == interrupted["attempts"][0]["status"] == "failed"
        assert "Process interrupted" in interrupted["error"]
        data = other.get("/api/loops").json()
        assert data["events"][0]["id"] == event["id"]
        assert data["lessons"][0]["status"] == "approved"
        assert restarted_runtime.calls == []


def test_validation_consent_unavailable_limits_and_no_authority_routes(ready):
    client, app, runtime, ids = ready
    body = {"workflow_id": ids["One"], "consent": True}
    assert client.post("/api/loops/run", json=body).status_code == 403
    assert post(client, "/api/loops/run", {"workflow_id": ids["One"]}).status_code == 403
    for values in ({"max_attempts": 0}, {"max_attempts": 4}, {"max_attempts": True}, {"max_attempts": "2"},
                   {"scenario": "other"}, {"consent": "true"}, {"prompt": "x" * 8001}, {"deploy": True}):
        assert post(client, "/api/loops/run", {**body, **values}).status_code == 422
    assert post(client, "/api/loops/run", {**body, "workflow_id": "unknown"}).status_code == 404
    assert post(client, "/api/loops/events", {"workflow_id": ids["One"], "event_type": "auto_deploy", "reference": "a"}).status_code == 422
    assert post(client, "/api/loops/lessons", {"run_id": "x", "lesson": "a", "scope": "global"}).status_code == 422
    assert post(client, "/api/loops/lessons/no/review", {"decision": "promote"}).status_code == 422
    assert post(client, "/api/loops/lessons", {"run_id": "x", "lesson": "a", "scope": "workflow_only"}).status_code == 404
    for path in ("/api/loops?limit=101", "/api/loops?limit=0"):
        assert client.get(path).status_code == 422
    assert client.get("/api/loops/runs/unknown").status_code == 404
    runtime.ready = False
    assert post(client, "/api/loops/run", body).status_code == 409
    assert not client.get("/api/loops").json()["current_auth_status"]["inference_ready"]
    assert runtime.calls == []
    for path in ("/api/loops/approve", "/api/loops/deploy", "/api/loops/promote"):
        assert post(client, path, body).status_code == 404


def test_bounded_overview_and_hash_freshness_without_events(ready):
    client, app, runtime, ids = ready
    first = completed(client, app, submit(client, ids["One"]))
    completed(client, app, submit(client, ids["One"]))
    data = client.get("/api/loops?limit=1").json()
    assert len(data["runs"]) == 1 and data["totals"]["runs"] == 2 and data["truncated"]
    proposal = next(p for p in client.get("/api/proposals").json()["items"] if p["workflow_id"] == ids["One"])
    assert post(client, "/api/reviews", {"proposal_id": proposal["id"], "expected_version": 1,
                                       "action": "comment", "comment": "New scoped review"}).status_code == 200
    assert client.get("/api/loops/runs/" + first["id"]).json()["stale"]
    assert len(runtime.calls) == 2


def test_unresolved_scoped_graph_requires_information_not_model_retry():
    model = {"nodes": [{"id": "w", "kind": "workflow"}, {"id": "u", "kind": "unresolved_reference"}],
             "edges": [{"id": "e", "source": "w", "target": "u", "kind": "contains"}]}
    result = grade(supported("w"), _Evidence(model, "w", [], []))
    assert result["status"] == "needs_information" and not result["repairable"]


def compare(client, baseline, candidate):
    return post(client, "/api/loops/hill-climb", {"baseline_run_id": baseline["id"],
                                               "candidate_run_id": candidate["id"]})


def comparison_pair(ready):
    client, app, runtime, ids = ready
    def incomplete(call, number):
        value = supported(call[1])
        value["findings"].pop()
        return value
    runtime.respond = incomplete
    baseline = completed(client, app, submit(client, ids["One"], max_attempts=1))
    runtime.respond = lambda call, number: supported(call[1])
    candidate = completed(client, app, submit(client, ids["One"], prompt="Different candidate prompt"))
    return baseline, candidate


def test_hill_climb_improvement_offline_immutable_and_restart(ready):
    client, app, runtime, ids = ready
    baseline, candidate = comparison_pair(ready)
    calls = len(runtime.calls)
    runtime.ready = False  # Offline comparisons need no live model authorization.
    response = compare(client, baseline, candidate)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["baseline_run_id"] == baseline["id"]
    assert report["candidate_run_id"] == candidate["id"]
    assert report["compatible"] and report["no_regressions"] and report["accepted"]
    assert report["improvements"] == ["six_required_roles"] and report["regressions"] == []
    assert report["recommendation"] == "candidate_recommended_for_review"
    assert report["eval_scope"] == "advisory_rubric_only"
    assert report["automatic_changes"] is False
    assert not report["current_auth_status"]["inference_ready"]
    assert not report["run_auth"][baseline["id"]]["actor_authenticated"]
    assert_gates(report)
    assert len(runtime.calls) == calls
    with sqlite3.connect(app.state.loops.path) as db:
        saved = db.execute("SELECT payload FROM comparisons WHERE id=?", (report["id"],)).fetchone()[0]
        assert json.loads(saved) == report
        for statement in ("UPDATE comparisons SET payload='{}'", "DELETE FROM comparisons"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(statement)
    from workbench.loops import LoopService
    restarted = LoopService(app.state.service, app.state.audit)
    assert restarted._get("comparisons", report["id"], report["package_hash"]) == report
    assert len(runtime.calls) == calls


@pytest.mark.parametrize("case", ["equal", "regression", "critical_unknown", "latest_attempt"])
def test_hill_climb_no_improvement_regression_unknown_and_latest_only(ready, case):
    client, app, runtime, ids = ready
    if case == "equal":
        baseline = completed(client, app, submit(client, ids["One"]))
        candidate = completed(client, app, submit(client, ids["One"]))
    else:
        baseline, candidate = comparison_pair(ready)
        attempt = deepcopy(candidate["attempts"][-1])
        if case in {"regression", "critical_unknown"}:
            criterion = "known_scoped_citations" if case == "regression" else "no_blocking_unknowns"
            next(c for c in attempt["rubric"]["checks"] if c["criterion"] == criterion)["passed"] = False
        else:
            # Latest attempt overrides an earlier improved one, even if a run's
            # cached outcome or attempts_count claims the earlier result.
            attempt.update(id="latest-recorded", number=2, rubric=baseline["attempts"][-1]["rubric"])
        app.state.loops._save("attempts", attempt)
    report = compare(client, baseline, candidate).json()
    assert report["compatible"] and not report["accepted"]
    assert report["recommendation"] == "not_recommended"
    if case in {"equal", "latest_attempt"}:
        assert report["improvements"] == []
    else:
        assert report["regressions"]
    if case == "latest_attempt":
        assert report["candidate_attempt_id"] == "latest-recorded"
    assert_gates(report)


@pytest.mark.parametrize("mismatch", ["workflow_id", "scenario", "input_hashes", "input_versions", "evidence_version"])
def test_hill_climb_incompatible_context_and_source_hashes(ready, mismatch):
    client, app, runtime, ids = ready
    baseline, candidate = comparison_pair(ready)
    stored = app.state.loops._get("runs", candidate["id"], candidate["package_hash"])
    if mismatch in {"workflow_id", "scenario"}:
        candidate = completed(client, app, submit(client, ids["Two"] if mismatch == "workflow_id" else ids["One"],
                                                 scenario="bronze" if mismatch == "scenario" else "original"))
    else:
        stored[mismatch] = {"source": "different"} if mismatch != "evidence_version" else "different-source"
        app.state.loops._save("runs", stored)
    response = compare(client, baseline, candidate)
    assert response.status_code == 201, response.text
    report = response.json()
    assert not report["compatible"] and not report["accepted"]
    assert mismatch in report["incompatibilities"]


@pytest.mark.parametrize("case", ["self", "missing", "foreign_package", "stale_event", "stale_input", "running", "consent", "no_rubric"])
def test_hill_climb_rejects_invalid_or_unverifiable_runs(ready, case):
    client, app, runtime, ids = ready
    baseline, candidate = comparison_pair(ready)
    stored = app.state.loops._get("runs", candidate["id"], candidate["package_hash"])
    expected = 409
    if case == "self":
        candidate, expected = baseline, 422
    elif case == "missing":
        candidate, expected = {"id": "missing"}, 404
    elif case == "foreign_package":
        stored["package_hash"], expected = "other-package", 404
        with app.state.loops.connect() as db:
            db.execute("UPDATE runs SET package_hash=?,payload=? WHERE id=?",
                       (stored["package_hash"], json.dumps(stored), stored["id"]))
    elif case == "stale_event":
        post(client, "/api/loops/events", {"workflow_id": ids["One"], "event_type": "manual_request", "reference": "invalidate"})
    elif case == "stale_input":
        stored["input_version"] = "old-source-snapshot"
        app.state.loops._save("runs", stored)
    elif case == "running":
        stored["status"] = "running"
        app.state.loops._save("runs", stored)
    elif case == "consent":
        stored["consent"], expected = False, 403
        app.state.loops._save("runs", stored)
    else:
        attempt = {**candidate["attempts"][-1], "id": "failed-latest", "number": 3, "status": "failed"}
        app.state.loops._save("attempts", attempt)
    assert compare(client, baseline, candidate).status_code == expected
    with app.state.loops.connect() as db:
        assert db.execute("SELECT count(*) FROM comparisons").fetchone()[0] == 0


def test_hill_climb_request_boundary_and_strict_schema(ready):
    client, app, runtime, ids = ready
    body = {"baseline_run_id": "a", "candidate_run_id": "b"}
    assert client.post("/api/loops/hill-climb", json=body).status_code == 403
    for values in ({"candidate_run_id": ""}, {"baseline_run_id": 1}, {"apply_prompt": True}):
        assert post(client, "/api/loops/hill-climb", {**body, **values}).status_code == 422
    assert not runtime.calls


def test_project_contexts_isolate_identical_packages_and_same_workflow_ids(tmp_path, monkeypatch):
    from workbench.tests.test_projects import create, upload, selection
    monkeypatch.delenv("WORKBENCH_MODEL", raising=False)
    app = create_app(tmp_path / "projects", tmp_path / "none.xml")
    with client_for(app) as client:
        a, b = create(client, "A"), create(client, "B")
        for project in (a, b):
            assert upload(client, app, project, [("shared.xml", XML)])["status"] == "completed"
        ah, bh = selection(a, XML), selection(b, XML)
        workflow = client.get("/api/overview", headers=ah).json()["workflows"][0]["id"]
        # Explicit test adapter installed in A only, not a model fallback in B.
        child = app.state.projects.contexts[(a, ah["X-Workbench-Package"])]["app"]
        child.state.service.runtime = Runtime()
        response = post(client, "/api/loops/run", {"workflow_id": workflow, "consent": True}, headers=ah)
        assert response.status_code == 202
        child.state.service.agent_executor.submit(lambda: None).result(timeout=10)
        run_id = response.json()["id"]
        lesson = post(client, "/api/loops/lessons", {"run_id": run_id, "lesson": "A only", "scope": "workflow_only"}, headers=ah).json()
        assert post(client, "/api/loops/lessons/" + lesson["id"] + "/review", {"decision": "approve"}, headers=ah).status_code == 200
        assert client.get("/api/loops/runs/" + run_id, headers=bh).status_code == 404
        assert client.get("/api/loops/runs/" + run_id).status_code == 409
        other = client.get("/api/loops", headers=bh).json()
        assert other["runs"] == other["lessons"] == other["events"] == []
        assert post(client, "/api/loops/lessons/" + lesson["id"] + "/review", {"decision": "reject"}, headers=bh).status_code == 404
        assert app.state.loops.path != child.state.loops.path