"""Opt-in, OBO-only activity writes to an existing monitoring table.

Parent integration (deliberately not installed here): construct one process-wide
Heartbeats.from_env(config.warehouse). After fresh hosted authentication and the
same-origin mutation guard, BEFORE the user action queue, offload touch(identity)
to a thread for POST /api/hosted/heartbeat. Ignore its request body completely.
Only a trusted operations wrapper may call touch(fresh_identity, 'active_work')
while work is actually running, with fresh OBO authorization on every pulse.
Never retain an identity/token in this service or start a background auth loop.

Existing table contract: app STRING, ts TIMESTAMP, user STRING. No DDL, grants,
owner checks, provisioning, service-principal fallback or remote polling. Pending
statements are NOT confirmed writes and do NOT start the verified-write cooldown.
An unconfirmed INSERT may finish later, so subsequent calls can append duplicates.
The rate limit is process-local, not a distributed uniqueness guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
import re
from threading import Lock
import time

from databricks.sdk.service.sql import (
    Disposition, ExecuteStatementRequestOnWaitTimeout, Format,
    StatementParameterListItem,
)

from .databricks_models import WorkspaceIdentity


INTERVAL_SECONDS = 300
MAX_USERS = 1024
_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_APP = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_WAREHOUSE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _state(response):
    state = _get(_get(response, "status"), "state")
    return getattr(state, "value", state)


@dataclass
class _User:
    lock: Lock = field(default_factory=Lock)
    verified_at: float | None = None


class Heartbeats:
    """One shared instance per worker; no credentials or clients are cached.

    Both table and app_name must be explicitly supplied. from_env is the hosted
    opt-in boundary; absent either variable disables all authentication/SQL work.
    Invalid supplied values raise fixed, nonsecret ValueErrors at construction.
    """

    def __init__(self, warehouse, table=None, app_name=None):
        parts = table.split(".") if isinstance(table, str) else []
        if table not in (None, "") and (len(parts) != 3 or not all(_PART.fullmatch(p) for p in parts)):
            raise ValueError("Heartbeat table must be a plain three-part SQL identifier")
        if app_name not in (None, "") and (not isinstance(app_name, str) or not _APP.fullmatch(app_name)):
            raise ValueError("Heartbeat app must be a short nonsecret application name")
        self.enabled = bool(table and app_name)
        if self.enabled and (not isinstance(warehouse, str) or not _WAREHOUSE.fullmatch(warehouse)):
            raise ValueError("Heartbeat requires the existing SQL warehouse identifier")
        self.warehouse = warehouse
        self.table = ".".join("`" + p + "`" for p in parts) if self.enabled else None
        self.app_name = app_name
        self._guard = Lock()
        self._users: dict[tuple[str, str], _User] = {}

    @classmethod
    def from_env(cls, warehouse):
        return cls(warehouse, os.environ.get("WORKBENCH_HEARTBEAT_TABLE"),
                   os.environ.get("WORKBENCH_HEARTBEAT_APP"))

    def status(self):
        return {"status": "enabled" if self.enabled else "disabled",
                "enabled": self.enabled, "interval_seconds": INTERVAL_SECONDS,
                "rate_limit_scope": "process", "written": False}

    def _result(self, status, **details):
        return {**self.status(), "status": status, **details}

    def _execute(self, identity, statement):
        return identity.client.statement_execution.execute_statement(
            statement=statement, warehouse_id=self.warehouse,
            parameters=[StatementParameterListItem(name="app", type="STRING", value=self.app_name),
                        StatementParameterListItem(name="user", type="STRING", value=identity.user_id)],
            wait_timeout="5s", on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
            disposition=Disposition.INLINE, format=Format.JSON_ARRAY, byte_limit=16384,
        )

    def touch(self, identity, event="user_activity"):
        """Return disabled/rejected/busy/throttled/pending/error/written status.

        event is a TRUSTED parent argument, never a browser request-body field.
        written=True requires INSERT success and a matching timestamped readback.
        Pending receipts expose only the phase, not upstream messages or handles.
        """
        if not self.enabled:
            return self.status()
        if event not in ("user_activity", "active_work"):
            return self._result("rejected", reason="invalid_event")
        if not isinstance(identity, WorkspaceIdentity) or identity.mode != "obo":
            return self._result("rejected", reason="obo_required")
        try:
            if identity.client.config.host.rstrip("/").lower() != identity.host:
                return self._result("rejected", reason="workspace_mismatch")
            if _get(identity.client.current_user.me(), "id") != identity.user_id:
                return self._result("rejected", reason="identity_mismatch")
            now = time.monotonic()
            key = (identity.host, identity.user_id)
            with self._guard:
                user = self._users.get(key)
                if user is None:
                    if len(self._users) >= MAX_USERS:
                        expired = next((k for k, u in self._users.items()
                                        if not u.lock.locked() and (u.verified_at is None or
                                            now - u.verified_at >= INTERVAL_SECONDS)), None)
                        if expired is None:
                            return self._result("busy", reason="capacity")
                        del self._users[expired]
                    user = self._users[key] = _User()
                if not user.lock.acquire(blocking=False):
                    return self._result("busy", reason="user_in_flight")
            try:
                now = time.monotonic()
                if user.verified_at is not None and now - user.verified_at < INTERVAL_SECONDS:
                    return self._result("throttled")
                response = self._execute(identity,
                    f"INSERT INTO {self.table} (app, ts, `user`) "
                    "VALUES (:app, current_timestamp(), :user)")
                state = _state(response)
                if state in ("PENDING", "RUNNING"):
                    return self._result("pending", phase="insert")
                if state != "SUCCEEDED":
                    return self._result("error", reason="insert_failed")
                response = self._execute(identity,
                    f"SELECT app, `user`, ts FROM {self.table} "
                    "WHERE app = :app AND `user` = :user AND ts IS NOT NULL ORDER BY ts DESC LIMIT 1")
                state = _state(response)
                if state in ("PENDING", "RUNNING"):
                    return self._result("pending", phase="readback")
                if state != "SUCCEEDED":
                    return self._result("error", reason="readback_failed")
                rows = _get(_get(response, "result"), "data_array", [])
                if (_get(_get(response, "manifest"), "truncated", False) or
                        _get(_get(response, "result"), "next_chunk_index") is not None or
                        not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != 3):
                    return self._result("error", reason="unverified_row")
                app, actor, timestamp = rows[0]
                if app != self.app_name or actor != identity.user_id or not isinstance(timestamp, str):
                    return self._result("error", reason="unverified_row")
                # Return an actual SQL timestamp, never a fabricated local time.
                datetime.fromisoformat(timestamp)
                user.verified_at = time.monotonic()
                return self._result("written", written=True, ts=timestamp)
            finally:
                user.lock.release()
        except Exception as error:
            # Never include upstream messages, SQL responses, tokens or user IDs.
            name = type(error).__name__
            safe_types = {"ValueError", "TypeError", "PermissionError", "TimeoutError",
                          "ConnectionError", "PermissionDenied", "Unauthenticated",
                          "AuthenticationUnavailable", "NotFound", "RuntimeError"}
            return self._result("error", error_type=name if name in safe_types else "OperationError")