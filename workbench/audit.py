"""Append-only local HTTP audit. Hash chaining detects edits, not administrator tampering."""
import csv
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import sqlite3
from time import monotonic
from uuid import uuid4

from fastapi import Query
from fastapi.responses import Response


class AuditLog:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS audit_events(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)")
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
            """)

    def append(self, event):
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
            sequence, previous = (last[0] + 1, last[1]) if last else (1, "0" * 64)
            payload = json.dumps({"sequence": sequence, "timestamp": datetime.now(timezone.utc).isoformat(),
                                  "input_tokens": None, "output_tokens": None, "total_tokens": None,
                                  "usage_status": "not_reported_http_event", **event}, sort_keys=True, separators=(",", ":"))
            digest = sha256((previous + payload).encode()).hexdigest()
            db.execute("INSERT INTO audit_events VALUES(?,?,?,?)", (sequence, payload, previous, digest))

    def records(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            rows = db.execute("SELECT payload,previous_hash,event_hash FROM audit_events ORDER BY sequence").fetchall()
        previous, valid, result = "0" * 64, True, []
        for payload, parent, digest in rows:
            valid = valid and parent == previous and sha256((parent + payload).encode()).hexdigest() == digest
            result.append({**json.loads(payload), "previous_hash": parent, "event_hash": digest})
            previous = digest
        return result, valid


class AuditMiddleware:
    def __init__(self, app, audit):
        self.app, self.audit = app, audit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["path"].startswith("/api/logs"):
            return await self.app(scope, receive, send)
        # Hosted read-only requests use a disposable SQLite cache and commit
        # diagnostics through the Databricks request boundary. Appending a
        # per-read hash-chain row here would create sequence races between
        # concurrent reads and make observations wait on a mutable shared tail.
        if scope.get("workbench.hosted_read_only") is True:
            return await self.app(scope, receive, send)
        start, correlation, status = monotonic(), uuid4().hex, 500
        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = list(message.get("headers", [])) + [(b"x-correlation-id", correlation.encode())]
            await send(message)
        try:
            await self.app(scope, receive, capture)
        finally:
            # Never record bodies, query strings, cookies, forwarded tokens, or exception text.
            self.audit.append({"event": "http_request_completed", "level": "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                               "role": None, "run_id": None, "package_hash": None, "correlation_id": correlation,
                               "message": f"{scope['method']} {scope['path'][:240]} returned HTTP {status}",
                               "status_code": status, "duration_ms": round((monotonic() - start) * 1000, 2)})


def install_audit(app, *, record_initialization=True):
    audit = AuditLog(app.state.service.state / "audit.sqlite")
    app.state.audit = audit
    app.state.service.audit = audit
    app.add_middleware(AuditMiddleware, audit=audit)
    if record_initialization:
        audit.append({"event": "audit_initialized", "level": "INFO", "message": "Local audit service initialized; no historical events backfilled.", "role": None, "run_id": None})

    def selected(level, role, run_id):
        rows, valid = audit.records()
        return [r for r in rows if (not level or r.get("level") == level.upper())
                and (not role or r.get("role") == role) and (not run_id or r.get("run_id") == run_id)], valid

    @app.get("/api/logs")
    def logs(level: str = "", role: str = "", run_id: str = "", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        rows, valid = selected(level, role, run_id)
        rows.reverse()
        return {"items": rows[offset:offset + limit], "total": len(rows), "chain_valid": valid,
                "storage": "local SQLite append-only API, hash chained; not UC/WORM storage", "offset": offset, "limit": limit,
                "usage_status": "HTTP events only; model token usage is not yet instrumented"}

    @app.get("/api/logs/export")
    def export(level: str = "", role: str = "", run_id: str = ""):
        rows, _ = selected(level, role, run_id)
        stream = io.StringIO(newline="")
        fields = ["sequence", "timestamp", "event", "level", "role", "run_id", "correlation_id", "message", "input_tokens", "output_tokens", "total_tokens", "usage_status", "previous_hash", "event_hash"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v) for k, v in record.items()})
        return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'})