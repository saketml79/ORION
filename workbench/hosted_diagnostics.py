"""Bounded per-user, current-process diagnostics for hosted HTTP failures.

This ring buffer is deliberately separate from committed audit rows so that a
Delta write conflict can still be diagnosed when the audit-table MERGE failed.
It contains error metadata and safe frames, never credentials, bodies or SQL.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
import re
from threading import RLock
from uuid import uuid4

from workbench.delta_state import safe_error
from workbench.domain import _redact
from workbench.redaction import redact_text


def owner_key(host: str, user_id: str) -> str:
    return sha256((host + "\n" + user_id).encode("utf-8")).hexdigest()


def _redact_message(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return redact_text(value)[:1200]


class HostedDiagnostics:
    def __init__(self, *, per_user_limit=300):
        if type(per_user_limit) is not int or not 1 <= per_user_limit <= 1000:
            raise ValueError("Invalid hosted diagnostic retention limit")
        self._limit = per_user_limit
        self._events = defaultdict(lambda: deque(maxlen=self._limit))
        self._lock = RLock()

    def record(self, owner: str, *, method: str, path: str, status: int,
               error: BaseException, frames=(), phase=None) -> dict:
        details = safe_error(error)
        code = getattr(error, "error_code", None)
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code):
            details["error_code"] = code
        resource_type = getattr(error, "diagnostic_resource_type", None)
        resource_name = getattr(error, "diagnostic_resource_name", None)
        if (resource_type == "unity_catalog_table" and isinstance(resource_name, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}){2}", resource_name)):
            details["resource_type"] = resource_type
            details["resource_name"] = resource_name
        resource_type = getattr(error, "diagnostic_resource_type", None)
        resource_name = getattr(error, "diagnostic_resource_name", None)
        if (resource_type == "unity_catalog_table" and isinstance(resource_name, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}){2}", resource_name)):
            details["resource_type"] = resource_type
            details["resource_name"] = resource_name
        for name in ("sql_state", "statement_id"):
            value = getattr(error, name, None)
            if isinstance(value, str) and len(value) <= 128 and not any(ord(c) < 32 for c in value):
                details[name] = value
        message = _redact_message(getattr(error, "diagnostic_message", None))
        if message:
            details["provider_message"] = message
        event = {
            "id": uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR" if status >= 500 else "WARNING",
            "event": "hosted_request_failure",
            "method": str(method)[:12], "path": str(path).split("?", 1)[0][:240],
            "status_code": int(status), "error_type": type(error).__name__,
            "error": details,
            "frames": [{"file": str(name)[:80], "function": str(function)[:120],
                        "line": int(line)} for name, function, line in list(frames)[-12:]],
            "storage": "current_app_instance_memory_only",
        }
        if isinstance(phase, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]{0,80}", phase):
            event["phase"] = phase
        event = _redact(event)
        with self._lock:
            self._events[owner].append(event)
        return event

    def list(self, owner: str, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 300:
            raise ValueError("Diagnostic limit must be between 1 and 300")
        with self._lock:
            rows = list(self._events.get(owner, ()))
        return list(reversed(rows[-limit:]))

    def find(self, owner: str, diagnostic_id: str):
        if not isinstance(diagnostic_id, str) or not re.fullmatch(r"[a-f0-9]{32}", diagnostic_id):
            return None
        with self._lock:
            return next((dict(item) for item in self._events.get(owner, ())
                         if item.get("id") == diagnostic_id), None)
