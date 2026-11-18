"""Bounded, process-local read diagnostics. Never retains identities or clients."""
from collections import OrderedDict
from threading import Lock
import re
import time

from fastapi import HTTPException


def read_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise HTTPException(400, "XWorkbenchReadId must be lowercase UUID hex")
    return value


class HostedLoading:
    def __init__(self, *, maximum=256, per_user=16):
        self.maximum, self.per_user = maximum, per_user
        self._lock, self._items = Lock(), OrderedDict()

    def begin(self, owner, diagnostic_id):
        key = (owner, read_id(diagnostic_id))
        with self._lock:
            if key in self._items:
                raise HTTPException(409, "Read diagnostic id already used; supply a new UUID")
            # Evict only finished records. Active work must remain observable.
            for candidate in list(self._items):
                count = sum(k[0] == owner for k in self._items)
                if len(self._items) < self.maximum and count < self.per_user:
                    break
                if ((count < self.per_user or candidate[0] == owner)
                        and self._items[candidate]["status"] in {"succeeded", "failed"}):
                    del self._items[candidate]
            if len(self._items) >= self.maximum or sum(k[0] == owner for k in self._items) >= self.per_user:
                raise HTTPException(429, "Read diagnostic capacity reached")
            now = time.time()
            self._items[key] = dict(diagnostic_id=diagnostic_id, phase="queued", status="queued",
                                    started_at=now, phase_started_at=now, updated_at=now,
                                    verified_files=None, total_files=None, exception_class=None,
                                    _start=time.monotonic(), _end=None)

    def update(self, owner, diagnostic_id, phase, *, verified_files=None, total_files=None,
               status="running", exception_class=None):
        if phase not in {"queued", "provisioning", "metadata_restore", "reading_history", "artifact_files", "audit_restore",
                         "preparing_package", "audit_persist", "responding"}:
            raise ValueError("Unknown loading phase")
        if status not in {"queued", "running", "succeeded", "failed"}:
            raise ValueError("Unknown loading status")
        with self._lock:
            row = self._items[(owner, diagnostic_id)]
            if row["status"] in {"succeeded", "failed"}:
                return
            now = time.time()
            if row["phase"] != phase:
                row["phase_started_at"] = now
            row.update(phase=phase, status=status, updated_at=now)
            if total_files is not None:
                if (type(verified_files) is not int or type(total_files) is not int
                        or not 0 <= verified_files <= total_files):
                    raise ValueError("Invalid verified file counters")
                row.update(verified_files=verified_files, total_files=total_files)
            if status in {"succeeded", "failed"}:
                row["_end"] = time.monotonic()
                # Only a bounded exception class identifier, never exception text.
                row["exception_class"] = (exception_class if exception_class and
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", exception_class) else None)

    def snapshot(self, owner, diagnostic_id):
        with self._lock:
            row = self._items.get((owner, read_id(diagnostic_id)))
            if row is None:
                raise HTTPException(404, "Read diagnostic not found")
            return {**{k: v for k, v in row.items() if not k.startswith("_")},
                    "heartbeat_at": time.time(),
                    "elapsed_seconds": round((row["_end"] if row["_end"] is not None else time.monotonic())
                                             - row["_start"], 3)}

    def finish(self, owner, diagnostic_id, *, exception_class=None):
        phase = self.snapshot(owner, diagnostic_id)["phase"]
        self.update(owner, diagnostic_id, phase,
                    status="failed" if exception_class else "succeeded", exception_class=exception_class)


def warehouse_status(identity, warehouse_id):
    """An independent observation, not an inference from elapsed time or SQL."""
    try:
        warehouse = identity.client.warehouses.get(warehouse_id)
        state = getattr(warehouse.state, "value", warehouse.state)
        if state not in {"STARTING", "RUNNING", "STOPPING", "STOPPED", "DELETING", "DELETED"}:
            return {"availability": "unavailable", "state": None, "exception_class": None}
        return {"availability": "available", "state": state, "exception_class": None}
    except Exception as error:
        name = type(error).__name__
        return {"availability": "unavailable", "state": None,
                "exception_class": name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else None}