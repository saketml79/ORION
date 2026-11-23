"""Read-only local accounting beside immutable historical public run output."""
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3

from workbench.domain import _hash, _redact
from workbench.agent_skill_receipt import skill_activity


def model_activity(events):
    """Project already scope-verified rows, without another query or raw payloads.

    A turn is one actual model handler call, including summary calls, not an
    inferred agent iteration. Missing provider counters stay unknown per field.
    """
    turns, calls, per_role = [], {}, {}
    keys = ("input_tokens", "output_tokens", "total_tokens")
    for role, call_id, event, payload, created in events:
        if event not in {"model_started", "model_completed", "model_failed"}:
            continue
        if (role not in {"discovery", "architecture", "semantics", "artifact", "validation", "supervisor"}
                or not isinstance(call_id, str) or not re.fullmatch(r"[a-f0-9]{32}", call_id)):
            raise ValueError("Invalid model call identity")
        stamp = datetime.fromisoformat(created)
        if stamp.tzinfo is None:
            raise ValueError("Model timestamp must have a timezone")
        endpoint = payload.get("endpoint")
        if endpoint is not None and (not isinstance(endpoint, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", endpoint)
            or _redact(endpoint) != endpoint):
            raise ValueError("Invalid endpoint label")
        if event == "model_started":
            if call_id in calls:
                raise ValueError("Duplicate model start")
            turn = {"turn": len(turns) + 1, "call_id": call_id, "role": role,
                    "endpoint": endpoint, "status": "started", "started_at": created,
                    "completed_at": None, "failed_at": None, "duration_ms": None,
                    "duration_source": None, "usage_reported": None,
                    **{key: None for key in keys}, **{key + "_unknown": True for key in keys}}
            calls[call_id] = turn
            turns.append(turn)
            continue
        turn = calls.get(call_id)
        if (turn is None or turn["status"] != "started" or turn["role"] != role
                or turn["endpoint"] != endpoint
                or stamp < datetime.fromisoformat(turn["started_at"])):
            raise ValueError("Unmatched model completion")
        turn["status"] = event.removeprefix("model_")
        turn["completed_at" if event == "model_completed" else "failed_at"] = created
        duration = payload.get("duration_ms")
        if duration is not None:
            valid_number = type(duration) in (int, float)
            if not valid_number or duration < 0 or (type(duration) is float and not math.isfinite(duration)):
                raise ValueError("Invalid measured duration")
            turn["duration_ms"] = duration
            turn["duration_source"] = "recorded_monotonic"
        else:
            turn["duration_ms"] = (stamp - datetime.fromisoformat(turn["started_at"])).total_seconds() * 1000
            turn["duration_source"] = "recorded_timestamps"
        if type(payload.get("usage_reported")) is not bool:
            raise ValueError("Invalid usage presence flag")
        turn["usage_reported"] = payload["usage_reported"]
        for key in keys:
            value = payload.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Invalid token counter")
            turn[key] = value
            turn[key + "_unknown"] = value is None
    for turn in turns:
        role = per_role.setdefault(turn["role"], {"role": turn["role"], "model_started": 0,
            "model_completed": 0, "model_failed": 0, "pending_calls": 0,
            **{"known_" + key: 0 for key in keys}, **{key + "_reported_calls": 0 for key in keys}})
        role["model_started"] += 1
        if turn["status"] == "started":
            role["pending_calls"] += 1
        else:
            role["model_" + turn["status"]] += 1
        for key in keys:
            if turn[key] is not None:
                role["known_" + key] += turn[key]
                role[key + "_reported_calls"] += 1
    for role in per_role.values():
        for key in keys:
            complete = role[key + "_reported_calls"] == role["model_started"]
            role[key] = role["known_" + key] if complete else None
            role[key + "_unknown"] = not complete
            role[key + "_unknown_calls"] = role["model_started"] - role[key + "_reported_calls"]
        role["accounting_complete"] = all(role[key] is not None for key in keys)
    return {"per_role": list(per_role.values()), "turns": turns,
            "turn_definition": "one_actual_model_call", "turn_count": len(turns)}


def tool_activity(events):
    """Project scope-verified tool calls only, never arguments or result payloads.

    The producer records tool_name on both ends today. A historical terminal
    without a name can use its exact role/call start, never a neighbouring call.
    Provider model_retry_* events have a separate call ID and are not tool
    retries. No current tool producer emits explicit retry metadata.
    """
    def unavailable(reason):
        return {"tool_activity_status": "unavailable",
                "tool_activity_unavailable_reason": reason,
                "tool_turns": [], "tool_turn_count": None,
                "tool_turn_definition": "one_actual_tool_call"}

    turns, calls = [], {}
    for role, call_id, event, payload, created in events:
        if event not in {"tool_started", "tool_completed", "tool_failed", "tool_rejected"}:
            continue
        if (role not in {"discovery", "architecture", "semantics", "artifact", "validation", "supervisor"}
                or not isinstance(call_id, str) or not re.fullmatch(r"[a-f0-9]{32}", call_id)):
            return unavailable("invalid_tool_call_identity")
        if not isinstance(payload, dict):
            return unavailable("invalid_tool_payload")
        name = payload.get("tool_name")
        if name is not None and (not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", name)
                or _redact(name) != name):
            return unavailable("invalid_tool_name")
        try:
            stamp = datetime.fromisoformat(created)
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                return unavailable("invalid_tool_timestamp")
        except (ValueError, TypeError, OverflowError):
            return unavailable("invalid_tool_timestamp")
        duration = payload.get("duration_ms")
        if duration is not None:
            try:
                if type(duration) not in (int, float) or duration < 0 or not math.isfinite(duration):
                    return unavailable("invalid_tool_duration")
            except OverflowError:
                return unavailable("invalid_tool_duration")
        key = (role, call_id)
        if event == "tool_started":
            if key in calls:
                return unavailable("duplicate_tool_start")
            if name is None:
                return unavailable("missing_tool_start_name")
            turn = {"turn": len(turns) + 1, "call_id": call_id, "role": role,
                    "tool_name": name, "status": "started", "started_at": created,
                    "completed_at": None, "failed_at": None, "rejected_at": None,
                    "duration_ms": None, "duration_source": None,
                    "recorded_retry_count": None, "retry_status": "not_recorded"}
            calls[key] = turn
            turns.append(turn)
            continue
        turn = calls.get(key)
        if turn is None or turn["status"] != "started":
            return unavailable("unmatched_tool_completion")
        if name is not None and name != turn["tool_name"]:
            return unavailable("tool_name_mismatch")
        start = datetime.fromisoformat(turn["started_at"])
        if stamp < start:
            return unavailable("tool_timestamp_before_start")
        turn["status"] = event.removeprefix("tool_")
        turn[turn["status"] + "_at"] = created
        turn["duration_ms"] = duration if duration is not None else (stamp - start).total_seconds() * 1000
        turn["duration_source"] = "recorded_monotonic" if duration is not None else "recorded_timestamps"
    return {"tool_activity_status": "recorded", "tool_activity_unavailable_reason": None,
            "tool_turns": turns, "tool_turn_count": len(turns),
            "tool_turn_definition": "one_actual_tool_call"}


def current_usage_receipt(root, record):
    """Fail closed on provenance mismatch; never initialize or repair a ledger."""
    unavailable = {"status": "unverified", "source": "local_agent_usage_ledger",
                   "historical_output_unchanged": True, "usage": None}
    output = record.get("output") or {}
    hashes = output.get("input_hashes")
    run_id = output.get("run_id")
    if (not isinstance(hashes, dict) or not hashes
            or any(not isinstance(k, str) or not isinstance(v, str)
               or not re.fullmatch(r"[a-f0-9]{64}", v) for k, v in hashes.items())
            or not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id)
            or any(hashes.get(k) != v for k, v in record.get("input_hashes", {}).items())):
        return unavailable
    identity = hashes.get("identity_scope")
    directory = Path(root) / "agents"
    if identity is not None:
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            return unavailable
        directory = directory / "identities" / identity
    path = directory / "agents.sqlite3"
    if not path.is_file():
        return unavailable
    namespace = _hash([record.get("package_hash"), hashes,
                       record.get("workflow_id"), record.get("scenario")])
    keys = ("input_tokens", "output_tokens", "total_tokens")
    known = dict.fromkeys(keys, 0)
    present = dict.fromkeys(keys, 0)
    started = completed = reported = failed = count = 0
    model_calls = {}
    activity_events = []
    first = last = None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT namespace,input_hashes FROM agent_runs WHERE run_id=?",
                             (run_id,)).fetchone()
            if row is None or row[0] != namespace or json.loads(row[1]) != hashes:
                return unavailable
            for event_id, event_namespace, role, call_id, event, raw, created in db.execute(
                    "SELECT id,namespace,role,call_id,event,payload,created_at FROM agent_usage WHERE run_id=? ORDER BY id",
                    (run_id,)):
                payload = json.loads(raw)
                if (payload.get("run_id") != run_id or event_namespace != namespace
                        or payload.get("namespace") != namespace or payload.get("input_hashes") != hashes
                        or payload.get("role") != role or payload.get("call_id") != call_id
                        or payload.get("event") != event):
                    return unavailable
                count += 1
                activity_events.append((role, call_id, event, payload, created))
                first = event_id if first is None else first
                last = event_id
                if event == "model_started":
                    if call_id in model_calls:
                        return unavailable
                    model_calls[call_id] = (role, "started")
                    started += 1
                elif event == "model_failed":
                    if model_calls.get(call_id) != (role, "started"):
                        return unavailable
                    model_calls[call_id] = (role, "failed")
                    failed += 1
                elif event == "model_completed":
                    if model_calls.get(call_id) != (role, "started"):
                        return unavailable
                    model_calls[call_id] = (role, "completed")
                    completed += 1
                    if type(payload.get("usage_reported")) is not bool:
                        return unavailable
                    reported += int(payload["usage_reported"])
                    for key in keys:
                        value = payload.get(key)
                        if value is not None:
                            if type(value) is not int or value < 0:
                                return unavailable
                            known[key] += value
                            present[key] += 1
            activity = model_activity(activity_events)
            skills = skill_activity(activity_events)
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        return unavailable
    events = {"table": "agent_usage", "run_id": run_id,
              "count": count, "first_id": first, "last_id": last}
    if (not count or completed + failed > started or reported > completed
            or events != (output.get("usage") or {}).get("events")):
        return unavailable
    complete = completed == started and failed == 0 and all(n == started for n in present.values())
    usage = {**{key: known[key] if present[key] == started else None for key in keys},
             **{"known_" + key: value for key, value in known.items()},
             "reported_calls": reported, "unreported_calls": started - reported,
             "failed_calls": failed, "accounting_complete": complete, "events": events}
    return {"status": "verified", "source": "local_agent_usage_ledger",
            "historical_output_unchanged": True, "public_run_id": record["id"],
            "run_id": run_id, "namespace": namespace, "input_hashes": hashes,
            "identity_scope_hash": identity, "usage": usage, **activity,
            **tool_activity(activity_events),
            "skill_stats": skills}