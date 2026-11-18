"""Durable local advisory loops around, not instead of, Deep Agents.

No inference on reads, event delivery, learning, or restart. The shared agent
executor/slot owns execution. SQLite is local persistence, not production auth.
Only detached documents/reviews and locally reviewed, untrusted lessons enter
the existing runtime. A passing rubric validates advisory output, never parity.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from threading import RLock
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workbench.agents import Finding, ROLES, _BASE_BLOCKERS, _Evidence
from workbench.domain import _hash, _json, _redact
from workbench.model_access import workspace_ready
from workbench.tracing import export_evaluation


def _now():
    return datetime.now(timezone.utc).isoformat()


GATES = {"advisory_only": True, "approved": False, "generation_allowed": False,
         "deployment_allowed": False, "semantic_parity": "unproven"}
DEFINITIONS = [
    {"id": "agent_model_tools", "execution": "Existing runtime.run owns the actual Deep Agents tool loop; outer attempts reference its run_id."},
    {"id": "verification_retry", "execution": "Deterministic schema, six-role, scoped-citation and blocker rubric. Only repairable output defects retry, at most three calls on frozen evidence."},
    {"id": "event_trigger", "execution": "Persist event and mark workflow runs stale. No automatic inference; new consent is required."},
    {"id": "learning", "execution": "Draft then local unverified review. Approved workflow-only lessons are untrusted feedback, never skills or policy updates."},
]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunBody(_Body):
    workflow_id: str = Field(min_length=1, max_length=1024)
    scenario: Literal["original", "bronze"] = "original"
    prompt: str = Field(default="", max_length=8000)
    consent: bool = False
    max_attempts: int = Field(default=2, ge=1, le=3)


class EventBody(_Body):
    workflow_id: str = Field(min_length=1, max_length=1024)
    event_type: Literal["document_added", "review_changed", "source_binding_changed", "manual_request"]
    reference: str = Field(min_length=1, max_length=1024)


class ComparisonBody(_Body):
    baseline_run_id: str = Field(min_length=1, max_length=128)
    candidate_run_id: str = Field(min_length=1, max_length=128)


class LessonBody(_Body):
    run_id: str = Field(min_length=1, max_length=128)
    lesson: str = Field(min_length=1, max_length=4000)
    scope: Literal["workflow_only"]


class ReviewBody(_Body):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


def grade(output, evidence):
    """Deterministic output rubric. Baseline execution gates are not defects.

    Finding blockers, scoped unresolved objects, and extra runtime blockers are
    missing evidence, not something another model call can repair.
    """
    checks, findings = [], []

    def check(name, passed, details=None):
        checks.append({"criterion": name, "passed": bool(passed), "details": details})

    if not isinstance(output, dict):
        return {"status": "invalid_output", "repairable": True, "checks": [
            {"criterion": "output_object", "passed": False, "details": None}]}
    # Replay only the runtime's explicit bounded retrieval manifest. This keeps
    # neighbor citations verifiable without importing cross-workflow documents.
    pages = output.get("neighbor_pages", [])
    if isinstance(pages, list) and len(pages) <= 100:
        for page in pages:
            if (isinstance(page, dict) and set(page) == {"offset", "limit"}
                    and type(page["offset"]) is int and page["offset"] >= 0
                    and type(page["limit"]) is int and 1 <= page["limit"] <= 10):
                evidence.package_neighbors(**page)
    raw = output.get("findings", [])
    schema_ok = isinstance(raw, list) and len(raw) <= 60
    if schema_ok:
        for item in raw:
            try:
                findings.append(Finding.model_validate(item).model_dump())
            except ValidationError:
                schema_ok = False
    check("finding_schema", schema_ok)
    check("runtime_status", output.get("status") in {"advisory", "needs_information", "failed", "unavailable"})
    missing = sorted(set(ROLES) - {f["role"] for f in findings})
    check("six_required_roles", not missing, missing)
    invalid = sorted({ref for f in findings for ref in f["evidence_ids"] if ref not in evidence.records})
    check("known_scoped_citations", not invalid, _redact(invalid[:40]))
    check("supported_findings_cite_evidence", all(f["evidence_ids"] for f in findings if f["status"] == "supported"))
    unknowns = [f["role"] for f in findings if f["status"] in {"needs_information", "unsupported"} or f["blockers"]]
    unresolved = [i for i in evidence.scope if evidence.nodes[i]["kind"] == "unresolved_reference"
                  or evidence.nodes[i].get("attributes", {}).get("status") in {"unresolved", "ambiguous"}]
    extra_blockers = output.get("blockers", [])
    blocker_schema = isinstance(extra_blockers, list) and all(isinstance(b, str) for b in extra_blockers)
    check("blocker_schema", blocker_schema)
    external = blocker_schema and any(b not in _BASE_BLOCKERS for b in extra_blockers)
    needs_information = bool(unknowns or unresolved or external or output.get("status") == "needs_information" and not missing)
    check("no_blocking_unknowns", not needs_information,
          {"roles": unknowns, "unresolved_ids": unresolved[:40], "runtime_blockers": bool(external)})
    failed = output.get("status") in {"failed", "unavailable"} or any(f["status"] == "failed" for f in findings)
    check("runtime_completed", not failed)
    closed = all(output.get(k, False) is False for k in ("approved", "generation_allowed", "deployment_allowed"))
    check("authority_gates_closed", closed)
    passed = all(c["passed"] for c in checks)
    return {"status": "failed" if failed or not closed else "needs_information" if needs_information
            else "valid_advisory" if passed else "invalid_output",
            "repairable": not (failed or not closed or needs_information or passed), "checks": checks}


def _usage(output):
    """Copy only explicitly reported numeric usage. Never infer totals."""
    result: dict[str, int | None] = {k: None for k in ("input_tokens", "output_tokens", "total_tokens")}
    if isinstance(output, dict):
        source = output.get("usage_metadata", output.get("usage", {}))
        if isinstance(source, dict):
            for key in result:
                value = source.get(key)
                if type(value) is int and value >= 0:
                    result[key] = value
    return {**result, "usage_status": "reported_by_runtime" if any(v is not None for v in result.values()) else "unknown"}


class LoopService:
    def __init__(self, service, audit):
        self.service, self.audit, self.lock = service, audit, RLock()
        self.path = service.state / "loops.sqlite"
        with self.connect() as db:
            for table in ("runs", "attempts", "events", "lessons"):
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, package_hash TEXT NOT NULL, workflow_id TEXT NOT NULL, payload TEXT NOT NULL)")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_scope ON {table}(package_hash, workflow_id)")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS comparisons (
                    id TEXT PRIMARY KEY, package_hash TEXT NOT NULL,
                    workflow_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS comparisons_no_update BEFORE UPDATE ON comparisons
                BEGIN SELECT RAISE(ABORT, 'Comparisons are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS comparisons_no_delete BEFORE DELETE ON comparisons
                BEGIN SELECT RAISE(ABORT, 'Comparisons are immutable'); END;
            """)
            # Contexts are re-created only after their shared executor is closed.
            for table in ("runs", "attempts"):
                for row in db.execute(f"SELECT id,payload FROM {table}").fetchall():
                    record = json.loads(row[1])
                    if record["status"] in {"queued", "running"}:
                        record.update(status="failed", outcome="failed", finished_at=_now(),
                                      error="Process interrupted; restart only by a new manual consented run.")
                        db.execute(f"UPDATE {table} SET payload=? WHERE id=?", (_json(record), row[0]))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, table, record, db=None):
        # All callers construct allowlisted records. Scrub strings recursively;
        # numeric usage is appended only after redaction in the worker.
        values = (record["id"], record["package_hash"], record["workflow_id"], _json(record))
        sql = f"INSERT INTO {table} VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload"
        if db is not None:
            db.execute(sql, values)
        else:
            with self.connect() as connection:
                connection.execute(sql, values)

    def _get(self, table, record_id, package_hash):
        with self.connect() as db:
            row = db.execute(f"SELECT payload FROM {table} WHERE id=? AND package_hash=?", (record_id, package_hash)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown loop record in this package context")
        return json.loads(row[0])

    def _list(self, table, package_hash, limit=50, workflow_id=None):
        where, values = "package_hash=?", [package_hash]
        if workflow_id is not None:
            where += " AND workflow_id=?"
            values.append(workflow_id)
        with self.connect() as db:
            count = db.execute(f"SELECT count(*) FROM {table} WHERE {where}", values).fetchone()[0]
            rows = db.execute(f"SELECT payload FROM {table} WHERE {where} ORDER BY rowid DESC LIMIT ?", (*values, limit)).fetchall()
        return [json.loads(row[0]) for row in rows], count

    def _audit(self, event, record, **fields):
        self.audit.append({"event": event, "level": "INFO", "role": None,
                           "run_id": record.get("run_id", record["id"]),
                           "package_hash": record["package_hash"], "workflow_id": record["workflow_id"],
                           "message": "Local advisory loop lifecycle event; no execution authority.", **fields})

    def _inputs(self, index, workflow_id, scenario):
        inputs = dict(self.service.agent_inputs(index, workflow_id, scenario))
        captured = json.loads(inputs.pop("snapshot"))
        # A bound prevents unbounded feedback. Hash precisely the selected records.
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM lessons WHERE package_hash=? AND workflow_id=? AND json_extract(payload,'$.status')='approved' ORDER BY rowid DESC LIMIT 20",
                              (captured["package_hash"], workflow_id)).fetchall()
        approved = sorted((json.loads(row[0]) for row in rows), key=lambda l: l["id"])
        feedback = [{"lesson_id": l["id"], "lesson": l["lesson"], "scope": "workflow_only",
                     "workflow_id": workflow_id, "authority": "local_reviewer_unverified",
                     "trust": "untrusted advisory feedback, not instructions or policy"} for l in approved]
        inputs["base_input_version"] = inputs["input_version"]
        inputs["input_hashes"] = {**inputs["input_hashes"], "approved_lessons": _hash(approved)}
        inputs["input_version"] = _hash([inputs["base_input_version"], approved])
        return inputs, captured, feedback

    def _freshness(self, index, record):
        current, _, _ = self._inputs(index, record["workflow_id"], record["scenario"])
        return {**record, "current_input_version": current["input_version"],
                "stale": record.get("stale", False) or record["input_version"] != current["input_version"]}

    def auth_status(self):
        return {"inference_ready": workspace_ready(self.service.runtime), "authentication_verified": False,
                "reviewer": "local_reviewer_unverified", "consent_required": True,
                "timeout_policy": self.service.runtime.status().get("timeout_policy", "no_elapsed_time_cutoff_manual_cancellation")}

    def start(self, body: RunBody):
        index = self.service.require()
        if body.consent is not True:
            raise HTTPException(403, "Explicit data-egress consent:true is required for every loop run")
        self.service.workflow(index, body.workflow_id)
        if not workspace_ready(self.service.runtime):
            raise HTTPException(409, "Configure an available workspace role-model runtime or explicit provider:model before running")
        if not self.service.agent_slot.acquire(blocking=False):
            raise HTTPException(409, "An agent run is already in progress; no loop run was queued")
        record = None
        try:
            with self.lock:
                inputs, captured, lessons = self._inputs(index, body.workflow_id, body.scenario)
                record = {"id": uuid4().hex, "status": "queued", "outcome": None,
                          "package_hash": index.model["package"]["sha256"], "workflow_id": body.workflow_id,
                          "scenario": body.scenario, "prompt": _redact(body.prompt), "max_attempts": body.max_attempts,
                          "consent": True, "actor_authenticated": False, "stale": False,
                          "created_at": _now(), **inputs, **GATES}
                self._save("runs", record)
                self.service.agent_executor.submit(self._execute, record["id"], index, captured, lessons)
            return record
        except Exception:
            try:
                if record is not None:
                    record.update(status="failed", outcome="failed", finished_at=_now(), error="Loop scheduling failed; request a new manual run.")
                    self._save("runs", record)
            finally:
                self.service.agent_slot.release()
            raise

    def _update_run(self, run_id, package_hash, **changes):
        # Merge, never overwrite event invalidation delivered during inference.
        with self.lock:
            record = self._get("runs", run_id, package_hash)
            record.update(changes)
            self._save("runs", record)
            return record

    def _execute(self, run_id, index, captured, lessons):
        package_hash = index.model["package"]["sha256"]
        attempt = None
        try:
            record = self._update_run(run_id, package_hash, status="running", started_at=_now())
            self._audit("loop_started", record)
            prompt = record["prompt"]
            for number in range(1, record["max_attempts"] + 1):
                # Deserialize each time so adapter mutations cannot change retry inputs.
                documents = json.loads(_json(captured["documents"]))
                feedback = json.loads(_json(captured["reviews"] + lessons))
                evidence = _Evidence(index.model, record["workflow_id"], documents, feedback)
                attempt = {"id": uuid4().hex, "run_id": run_id, "number": number,
                           "package_hash": package_hash, "workflow_id": record["workflow_id"],
                           "status": "running", "created_at": _now(), **GATES}
                self._save("attempts", attempt)
                output = self.service.runtime.run(index.model, record["workflow_id"], record["scenario"],
                                                  prompt, documents, feedback)
                # Bound adapter output before persistence or detailed processing.
                if len(_json(output)) > 262144:
                    output = None
                rubric = grade(output, evidence)
                tracing = output.get("tracing") if isinstance(output, dict) else None
                evaluation = export_evaluation(tracing, rubric,
                    identity_scope=getattr(self.service.runtime, "identity_scope", ""),
                    run_id=output.get("run_id") if isinstance(output, dict) else None)
                findings = output.get("findings", []) if isinstance(output, dict) else []
                safe_findings = []
                if isinstance(findings, list):
                    for finding in findings[:60]:
                        try:
                            safe_findings.append(_redact(Finding.model_validate(finding).model_dump()))
                        except ValidationError:
                            pass
                trace = output.get("run_id") if isinstance(output, dict) else None
                attempt.update(status="completed", finished_at=_now(), rubric=rubric, findings=safe_findings,
                               tracing=tracing, tracing_evaluation=evaluation,
                               trace_reference={"runtime_run_id": str(_redact(trace))[:128] if isinstance(trace, str) else None,
                                                "source": "service.runtime.run", "tool_trace": "owned by underlying runtime; not duplicated"},
                               summary=str(_redact(str(output.get("summary", ""))))[:2000] if isinstance(output, dict) else "Invalid or oversized runtime output.",
                               **_usage(output))
                self._save("attempts", attempt)
                self._audit("loop_verification", record, attempt=number, verdict=rubric["status"], **_usage(output))
                if not rubric["repairable"] or number == record["max_attempts"]:
                    break
                defects = [c["criterion"] for c in rubric["checks"] if not c["passed"]]
                prompt = record["prompt"] + "\nRepair advisory output only: " + ", ".join(defects) + ". Use captured evidence IDs and all six Finding roles. Do not invent missing evidence or grant authority."
            assert attempt is not None
            outcome = attempt["rubric"]["status"]
            record = self._update_run(run_id, package_hash, status="failed" if outcome == "failed" else "completed",
                                      outcome=outcome, attempts_count=attempt["number"], finished_at=_now())
            self._audit("loop_completed", record, verdict=outcome)
        except Exception:
            if attempt is not None and attempt["status"] == "running":
                attempt.update(status="failed", finished_at=_now(), error="Advisory runtime or verification failed.")
                self._save("attempts", attempt)
            record = self._update_run(run_id, package_hash, status="failed", outcome="failed", finished_at=_now(),
                                      error="Advisory runtime or persistence failed. No execution authority granted.")
            self._audit("loop_completed", record, verdict="failed")
        finally:
            self.service.agent_slot.release()

    def run(self, run_id):
        index = self.service.require()
        record = self._get("runs", run_id, index.model["package"]["sha256"])
        # Query by run_id in JSON to avoid another workflow's attempts hiding these.
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM attempts WHERE package_hash=? AND json_extract(payload,'$.run_id')=? ORDER BY rowid LIMIT 3",
                              (record["package_hash"], run_id)).fetchall()
        return {**self._freshness(index, record), "attempts": [json.loads(r[0]) for r in rows]}

    def overview(self, limit=50):
        index = self.service.active
        result = {"definitions": DEFINITIONS, "current_auth_status": self.auth_status(), **GATES}
        if index is None:
            return {**result, "runs": [], "events": [], "lessons": [], "totals": {"runs": 0, "events": 0, "lessons": 0}, "limit": limit, "truncated": False}
        totals = {}
        for table in ("runs", "events", "lessons"):
            rows, totals[table] = self._list(table, index.model["package"]["sha256"], limit)
            result[table] = [self._freshness(index, r) for r in rows] if table == "runs" else rows
        return {**result, "totals": totals, "limit": limit, "truncated": any(n > limit for n in totals.values())}

    def compare(self, body: ComparisonBody):
        """Compare recorded latest rubrics only. Never invoke or promote a model."""
        if body.baseline_run_id == body.candidate_run_id:
            raise HTTPException(422, "Baseline and candidate must be different runs")
        index = self.service.require()
        package_hash = index.model["package"]["sha256"]
        with self.lock:
            runs = [self._freshness(index, self._get("runs", rid, package_hash))
                    for rid in (body.baseline_run_id, body.candidate_run_id)]
            if any(r["stale"] for r in runs):
                raise HTTPException(409, "Comparison requires fresh inputs for both runs")
            if any(r["status"] not in {"completed", "failed"} for r in runs):
                raise HTTPException(409, "Comparison requires finished runs")
            if any(r.get("consent") is not True for r in runs):
                raise HTTPException(403, "Comparison requires recorded consent for both runs")
            attempts = []
            with self.connect() as db:
                for run in runs:
                    row = db.execute(
                        "SELECT payload FROM attempts WHERE package_hash=? AND workflow_id=? "
                        "AND json_extract(payload,'$.run_id')=? "
                        "ORDER BY json_extract(payload,'$.number') DESC, rowid DESC LIMIT 1",
                        (package_hash, run["workflow_id"], run["id"])).fetchone()
                    attempt = json.loads(row[0]) if row else {}
                    if attempt.get("status") != "completed" or not attempt.get("rubric", {}).get("checks"):
                        raise HTTPException(409, "Latest attempt requires a completed recorded rubric")
                    attempts.append(attempt)
            baseline, candidate = runs
            mismatches = [key for key in ("package_hash", "workflow_id", "scenario", "input_version",
                                         "input_hashes", "input_versions", "evidence_version")
                          if baseline.get(key) is None or baseline.get(key) != candidate.get(key)]
            outcomes = []
            for attempt in attempts:
                checks = attempt["rubric"]["checks"]
                if (any(not isinstance(c, dict) or not isinstance(c.get("criterion"), str)
                        or type(c.get("passed")) is not bool for c in checks)
                        or len({c["criterion"] for c in checks}) != len(checks)):
                    raise HTTPException(409, "Recorded rubric is malformed")
                outcomes.append({c["criterion"]: c["passed"] for c in checks})
            before, after = outcomes
            if before.keys() != after.keys():
                mismatches.append("rubric_criteria")
            regressions = sorted(k for k, passed in before.items() if passed and after.get(k) is not True)
            improvements = sorted(k for k, passed in before.items() if not passed and after.get(k) is True)
            critical_unknown = after.get("no_blocking_unknowns") is not True
            compatible = not mismatches
            accepted = (compatible and not regressions and bool(improvements) and not critical_unknown
                        and all(after.get(k) is True for k in ("authority_gates_closed", "runtime_completed")))
            record = {"id": uuid4().hex, "created_at": _now(), "package_hash": package_hash,
                      "workflow_id": baseline["workflow_id"], "baseline_run_id": baseline["id"],
                      "candidate_run_id": candidate["id"], "baseline_attempt_id": attempts[0]["id"],
                      "candidate_attempt_id": attempts[1]["id"], "compatible": compatible,
                      "incompatibilities": mismatches, "no_regressions": not regressions,
                      "improvements": improvements, "regressions": regressions, "accepted": accepted,
                      "critical_semantic_unknown": critical_unknown,
                      "recommendation": "candidate_recommended_for_review" if accepted else "not_recommended",
                      "eval_scope": "advisory_rubric_only", "current_auth_status": self.auth_status(),
                      "run_auth": {r["id"]: {"consent": r["consent"],
                                   "actor_authenticated": r.get("actor_authenticated", False)} for r in runs},
                      "inputs": {r["id"]: {k: r.get(k) for k in ("input_version", "input_hashes",
                                 "input_versions", "evidence_version", "current_input_version", "stale")} for r in runs},
                      "rubric_hashes": {a["id"]: _hash(a["rubric"]) for a in attempts},
                      "automatic_changes": False, **GATES}
            with self.connect() as db:
                db.execute("INSERT INTO comparisons VALUES (?,?,?,?)",
                           (record["id"], package_hash, record["workflow_id"], _json(record)))
        self._audit("loop_comparison_recorded", record)
        return record

    def event(self, body: EventBody):
        index = self.service.require()
        self.service.workflow(index, body.workflow_id)
        record = {"id": uuid4().hex, "package_hash": index.model["package"]["sha256"],
                  "workflow_id": body.workflow_id, "event_type": body.event_type, "reference": _redact(body.reference),
                  "created_at": _now(), "rerun_scheduled": False, "consent_required": True}
        with self.lock, self.connect() as db:
            self._save("events", record, db)
            rows = db.execute("SELECT payload FROM runs WHERE package_hash=? AND workflow_id=?",
                              (record["package_hash"], body.workflow_id)).fetchall()
            for row in rows:
                run = json.loads(row[0])
                run.update(stale=True, stale_event_id=record["id"])
                self._save("runs", run, db)
        self._audit("loop_event_received", record)
        return {**record, "invalidated_runs": len(rows)}

    def lesson(self, body: LessonBody):
        index = self.service.require()
        run = self._get("runs", body.run_id, index.model["package"]["sha256"])
        record = {"id": uuid4().hex, "run_id": run["id"], "package_hash": run["package_hash"],
                  "workflow_id": run["workflow_id"], "lesson": _redact(body.lesson), "scope": body.scope,
                  "status": "draft", "created_at": _now(), "authority": "local_reviewer_unverified",
                  "production_promotion_allowed": False, **GATES}
        with self.lock:
            self._save("lessons", record)
        self._audit("loop_learning_drafted", record)
        return record

    def review(self, lesson_id, body: ReviewBody):
        index = self.service.require()
        with self.lock:
            record = self._get("lessons", lesson_id, index.model["package"]["sha256"])
            if record["status"] != "draft":
                raise HTTPException(409, "Lesson already reviewed; create a new draft for another review")
            record.update(status="approved" if body.decision == "approve" else "rejected",
                          comment=_redact(body.comment), reviewed_at=_now(), actor_authenticated=False)
            self._save("lessons", record)
        self._audit("loop_learning_reviewed", record, decision=body.decision)
        return record


def install_loops(app):
    loops = LoopService(app.state.service, app.state.audit)
    app.state.loops = loops

    @app.get("/api/loops")
    @app.head("/api/loops")
    def overview(limit: int = Query(50, ge=1, le=100)):
        return loops.overview(limit)

    @app.post("/api/loops/run", status_code=202)
    def start(body: RunBody):
        return loops.start(body)

    @app.post("/api/loops/hill-climb", status_code=201)
    def compare(body: ComparisonBody):
        return loops.compare(body)

    @app.get("/api/loops/runs/{run_id}")
    def run(run_id: str):
        return loops.run(run_id)

    @app.post("/api/loops/events", status_code=201)
    def event(body: EventBody):
        return loops.event(body)

    @app.post("/api/loops/lessons", status_code=201)
    def lesson(body: LessonBody):
        return loops.lesson(body)

    @app.post("/api/loops/lessons/{lesson_id}/review")
    def review(lesson_id: str, body: ReviewBody):
        return loops.review(lesson_id, body)