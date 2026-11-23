"""Local, hash-pinned recovery. No inference, clients, worker restart or history edits."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from workbench.domain import _hash, _redact


def safe_exception_class(exc):
    # Keep custom names only from known dependency namespaces; never expose
    # arbitrary dynamically-created class names or exception messages.
    known_modules = ("databricks.sdk.errors", "openai", "anthropic", "httpx", "httpx2",
                     "langchain_core", "langgraph", "pydantic", "mlflow")
    for base in type(exc).__mro__:
        name, module = base.__name__, base.__module__
        if module == "builtins" and issubclass(base, Exception):
            return name
        if (re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", name)
                and any(module == allowed or module.startswith(allowed + ".") for allowed in known_modules)
                and issubclass(base, Exception)):
            return name
    return "Exception"


def _digest(path):
    with Path(path).open("rb") as stream:
        value = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _read_db(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or invalid recovery evidence")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return closing(db)


def _original(root, public_id):
    if not isinstance(public_id, str) or not re.fullmatch(r"[a-f0-9]{32}", public_id):
        raise ValueError("Invalid public run identity")
    path = root / "state" / "agent-runs" / (public_id + ".json")
    if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Invalid public run evidence")
    raw = path.read_bytes()
    record = json.loads(raw)
    if record.get("id") != public_id:
        raise ValueError("Public run identity mismatch")
    return record, sha256(raw).hexdigest()


def verified_output(root, public_id):
    """Read only. Require a unique terminal runtime in the public invocation window."""
    root = Path(root).resolve()
    record, public_hash = _original(root, public_id)
    if record.get("status") in {"queued", "running", "cancelling"} or not record.get("finished_at"):
        raise ValueError("Recovery requires a terminal public run")
    start, end = (datetime.fromisoformat(record[k]) for k in ("started_at", "finished_at"))
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("Invalid public invocation window")
    expected = {**record["input_hashes"], "documents": record["input_versions"]["documents"],
                "feedback": record["input_versions"]["reviews"]}
    if not expected or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                           for v in expected.values()):
        raise ValueError("Invalid captured input pins")
    # A later same-scope public invocation cannot lend its runtime to this one.
    for path in (root / "state" / "agent-runs").glob("*.json"):
        other = json.loads(path.read_bytes())
        if (other.get("id") != public_id and all(other.get(k) == record.get(k)
                for k in ("package_hash", "workflow_id", "scenario"))):
            created = datetime.fromisoformat(other["created_at"])
            if start < created < end:
                end = created
    directory = root / "agents"
    candidates = []
    for path in [directory / "agents.sqlite3", *directory.glob("identities/*/agents.sqlite3")]:
        if not path.is_file():
            continue
        with _read_db(path) as db:
            rows = db.execute("SELECT run_id,namespace,input_hashes,state,result,created_at FROM agent_runs "
                              "WHERE created_at>=? AND result IS NOT NULL", (record["started_at"],)).fetchall()
        for run_id, namespace, raw_hashes, state, raw_result, created in rows:
            hashes = json.loads(raw_hashes)
            stamp = datetime.fromisoformat(created)
            identity = hashes.get("identity_scope")
            if (not start <= stamp <= end or state == "running"
                    or not re.fullmatch(r"[a-f0-9]{32}", run_id)
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in hashes.values())
                    or any(hashes.get(k) != v for k, v in expected.items())
                    or namespace != _hash([record["package_hash"], hashes, record["workflow_id"], record["scenario"]])
                    or (path.parent == directory and identity is not None)
                    or (path.parent != directory and identity != path.parent.name)):
                continue
            output = json.loads(raw_result)
            pinned = record.get("output") or {}
            if (output.get("run_id") != run_id or output.get("input_hashes") != hashes
                    or output.get("status") != state
                    or (pinned.get("run_id") is not None and pinned.get("run_id") != run_id)):
                continue
            from workbench.agent_usage_receipt import current_usage_receipt
            usage = current_usage_receipt(root, {**record, "output": output})
            if usage["status"] != "verified":
                continue
            roles = sorted({item["role"] for item in output.get("redesigns", [])})
            if set(roles) - {"architecture", "supervisor"}:
                continue
            checkpoints = {role: _digest(path.parent / ("checkpoint_" + role + ".sqlite3")) for role in roles}
            pins = {"public_run_id": public_id, "public_sha256": public_hash, "runtime_run_id": run_id,
                    "namespace": namespace, "identity_scope_hash": identity,
                    "result_sha256": sha256(raw_result.encode("utf-8")).hexdigest(),
                    "checkpoint_sha256": checkpoints}
            candidates.append((record, output, path, pins))
    if len(candidates) != 1:
        raise ValueError("No unique verified original runtime output")
    return candidates[0]


def recovered_output(root, public_id):
    """Separate observation for detail GET. Never overwrite historical output."""
    try:
        _, output, _, pins = verified_output(root, public_id)
        observations = []
        observation_path = Path(root) / "state" / "agent-recoveries.sqlite"
        if observation_path.is_file():
            with _read_db(observation_path) as db:
                rows = db.execute("SELECT id,payload FROM observations").fetchall()
            for key, raw in rows:
                value = json.loads(raw)
                if value.get("pins") != pins or _hash(value) != key:
                    continue
                with _read_db(Path(root) / "state" / "redesigns.sqlite") as db:
                    for number, ref in enumerate(value["redesign_refs"]):
                        row = db.execute("SELECT payload FROM redesign_versions WHERE id=? AND version=?",
                                         (ref["id"], ref["version"])).fetchone()
                        saved = json.loads(row[0]) if row else {}
                        if (saved.get("source", {}).get("record_id") != public_id
                                or saved.get("source", {}).get("runtime_run_id") != pins["runtime_run_id"]
                                or saved.get("design") != output["redesigns"][number]["design"]):
                            raise ValueError("Invalid recovery design observation")
                observations.append({"observation_id": key, "redesign_refs": value["redesign_refs"]})
        return {"status": "verified", "historical_output_unchanged": True,
                "pins": pins, "output": _redact(output),
                "observations": observations,
                "design_persistence": "recovered_original" if observations else "requires_explicit_pinned_recovery"}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return {"status": "unverified", "historical_output_unchanged": True, "output": None}


def checkpoint_receipts(path, role, runtime_id, workflow_id, model, records, submissions):
    """Pair successful ToolMessages with exact AI tool-call arguments in this thread.

    No pickle loading and no checkpoint creation/setup. Only the messages channel
    is decoded. Reasoning and arbitrary exception text never leave this function.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from workbench.migration_planning import PlanningIndex
    from workbench.retrieval_receipts import planning_receipt, replay_planning
    from workbench.redesign import Design
    serde = JsonPlusSerializer(pickle_fallback=False)
    calls, seen, receipts, accepted = {}, {}, [], set()
    planning = PlanningIndex(model)
    with _read_db(path) as db:
        rows = db.execute("SELECT type,value FROM writes WHERE thread_id=? AND checkpoint_ns='' "
                          "AND channel='messages' ORDER BY rowid", (runtime_id + ":" + role,))
        for kind, raw in rows:
            if kind not in {"msgpack", "json"} or len(raw) > 16 * 1024 * 1024:
                raise ValueError("Unsupported checkpoint evidence encoding")
            value = serde.loads_typed((kind, raw))
            for message in value if isinstance(value, list) else [value]:
                for call in getattr(message, "tool_calls", []):
                    if call["id"] in calls and calls[call["id"]] != call:
                        raise ValueError("Conflicting checkpoint tool call")
                    calls[call["id"]] = call
                call_id = getattr(message, "tool_call_id", None)
                name = getattr(message, "name", None)
                if not call_id or name not in {"workflow_planning_contract", "submit_redesign"}:
                    continue
                fingerprint = _hash(message.model_dump(mode="json"))
                if call_id in seen:
                    if seen[call_id] != fingerprint:
                        raise ValueError("Conflicting checkpoint tool result")
                    continue
                seen[call_id] = fingerprint
                call = calls.get(call_id)
                if not call or call.get("name") != name or getattr(message, "status", None) != "success":
                    continue
                result, args = json.loads(message.content), call["args"]
                if name == "workflow_planning_contract":
                    if set(args) - {"section", "offset", "limit", "related_workflow_id"}:
                        raise ValueError("Invalid checkpoint planning arguments")
                    selected = args.get("related_workflow_id") or workflow_id
                    section, offset, limit = args.get("section", "overview"), args.get("offset", 0), args.get("limit", 12)
                    if result.get("status") == "related_workflow_not_retrieved":
                        continue
                    canonical = planning.agent_contract(selected, section, offset=offset, limit=limit)
                    if result != _redact(canonical):
                        raise ValueError("Checkpoint planning page differs from canonical evidence")
                    receipts.append(planning_receipt(role, selected, section, offset, limit, canonical))
                    replay_planning(model, workflow_id, receipts, records)
                elif result.get("recorded") is True:
                    number = result.get("submission_index")
                    if type(number) is not int or not 0 <= number < len(submissions):
                        raise ValueError("Invalid accepted checkpoint submission")
                    submission = submissions[number]
                    if (submission["role"] != role or set(args) != {"design"}
                            or Design.model_validate(_redact(args["design"])).model_dump() != submission["design"]):
                        raise ValueError("Accepted checkpoint design differs from original result")
                    accepted.add(number)
    if accepted != {n for n, item in enumerate(submissions) if item["role"] == role}:
        raise ValueError("Original accepted design checkpoint receipt missing")
    return receipts


def _snapshot(root, record, index, hashes):
    # Legacy snapshots were not saved. Recover only independently reconstructible
    # empty supporting inputs plus immutable, exact historical proposal versions.
    if hashes["documents"] != _hash([]) or hashes["feedback"] != _hash([]):
        raise ValueError("Historical nonempty supporting snapshot unavailable")
    proposals = []
    with _read_db(root / "state" / "reviews.sqlite") as db:
        for identifier, version in sorted(record["input_versions"]["proposals"].items()):
            row = db.execute("SELECT payload FROM proposals WHERE package_hash=? AND proposal_id=? AND version=?",
                             (record["package_hash"], identifier, version)).fetchone()
            if row is None:
                raise ValueError("Historical proposal input missing")
            proposals.append(json.loads(row[0]))
    manifest = json.loads((index.directory / "manifest.json").read_bytes())
    snapshot = {"package_hash": record["package_hash"], "workflow_id": record["workflow_id"],
                "scenario": record["scenario"], "artifact_hash": manifest["understanding_sha256"],
                "documents": [], "reviews": [], "proposals": proposals, "hashes": record["input_hashes"]}
    if index.model.get("source_analysis_version"):
        snapshot["source_analysis_version"] = index.model["source_analysis_version"]
    if _hash(snapshot) != record["input_version"] or _hash(index.model) != hashes["model"]:
        raise ValueError("Historical captured input or canonical model pin mismatch")
    return snapshot


def recover_design(root, public_id, pins, *, consent=False, index=None):
    """Explicit capture only. Return original result and append a separate observation."""
    if consent is not True:
        raise PermissionError("Explicit consent:true is required")
    root = Path(root).resolve()
    record, output, ledger_path, verified = verified_output(root, public_id)
    if pins != verified:
        raise ValueError("Recovery pins changed or do not match exact original evidence")
    if record.get("consent") is not True:
        raise PermissionError("Original run consent missing")
    for path in (root / "state" / "agent-runs").glob("*.json"):
        if json.loads(path.read_bytes()).get("status") in {"queued", "running", "cancelling"}:
            raise ValueError("Active run present; recovery must not compete with its writer")
    if index is None:
        from workbench.app import PackageIndex
        index = PackageIndex.load(root / "packages" / record["package_hash"])
    if index.model["package"]["sha256"] != record["package_hash"]:
        raise ValueError("Recovery package mismatch")
    snapshot = _snapshot(root, record, index, output["input_hashes"])
    from workbench.agents import _Evidence
    from workbench.redesign import capture_run
    evidence = _Evidence(index.model, record["workflow_id"], snapshot["documents"], snapshot["reviews"])
    receipts = []
    for role, digest in verified["checkpoint_sha256"].items():
        path = ledger_path.parent / ("checkpoint_" + role + ".sqlite3")
        if _digest(path) != digest:
            raise ValueError("Checkpoint evidence changed")
        receipts.extend(checkpoint_receipts(path, role, output["run_id"], record["workflow_id"],
                                            index.model, evidence.records, output["redesigns"]))
    if verified_output(root, public_id)[3] != verified:
        raise ValueError("Recovery evidence changed before capture")
    saved = capture_run(SimpleNamespace(state=root / "state"), index,
                        {**record, "redesign_snapshot": snapshot}, {**output, "planning_retrievals": receipts})
    if [item["design"] for item in saved] != [item["design"] for item in output["redesigns"]]:
        raise ValueError("Recovered design differs from accepted original")
    if verified_output(root, public_id)[3] != verified:
        raise ValueError("Recovery evidence changed after capture")
    observation = {"kind": "verified_original_design_recovery", "pins": verified,
                   "planning_retrievals": receipts,
                   "redesign_refs": [{"id": item["id"], "version": item["version"]} for item in saved],
                   "consent": True, "historical_output_unchanged": True,
                   "writer_invoked": False, "approved": False, "deployment_allowed": False}
    key = _hash(observation)
    with closing(sqlite3.connect(root / "state" / "agent-recoveries.sqlite")) as db:
        with db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS recovery_no_update BEFORE UPDATE ON observations
                    BEGIN SELECT RAISE(ABORT, 'append-only recovery observation'); END;
                CREATE TRIGGER IF NOT EXISTS recovery_no_delete BEFORE DELETE ON observations
                    BEGIN SELECT RAISE(ABORT, 'append-only recovery observation'); END;
            """)
            db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?)",
                       (key, json.dumps(observation, sort_keys=True), datetime.now(timezone.utc).isoformat()))
    return {"observation_id": key, **observation}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--public-run-id", required=True)
    parser.add_argument("--pins-json", help="Exact JSON pins returned by the read-only invocation")
    parser.add_argument("--consent", action="store_true", help="Persist original design only, no writer or inference")
    args = parser.parse_args()
    try:
        if args.consent:
            result = recover_design(args.root, args.public_run_id, json.loads(args.pins_json or "null"), consent=True)
        else:
            _, output, _, pins = verified_output(args.root, args.public_run_id)
            result = {"status": "verified", "pins": pins, "finding_count": len(output.get("findings", [])),
                      "accepted_design_count": len(output.get("redesigns", [])), "read_only": True}
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "exception_class": safe_exception_class(exc),
                          "error": "Recovery verification failed; no inference or history rewrite attempted."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()