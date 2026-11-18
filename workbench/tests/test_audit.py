import csv
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from workbench.app import create_app
from workbench.audit import AuditLog


def test_real_logs_routes_and_inputs(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        response = client.get("/api/input-packages")
        assert response.status_code == 200
        assert any(p["name"] == "LumenExample.XML" for p in response.json()["items"])
        assert response.headers["x-correlation-id"]
        data = client.get("/api/logs").json()
        assert data["chain_valid"] and data["total"] >= 2
        assert any("/api/input-packages" in row["message"] for row in data["items"])
        assert all(row["total_tokens"] is None for row in data["items"])
        exported = client.get("/api/logs/export")
        assert exported.status_code == 200
        assert len(list(csv.DictReader(io.StringIO(exported.text)))) >= 2
        assert client.get("/api/logs?limit=101").status_code == 422


def test_append_only_and_chain_validation(tmp_path):
    path = tmp_path / "audit.sqlite"
    audit = AuditLog(path)
    audit.append({"event": "test", "message": "actual event"})
    assert audit.records()[1]
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM audit_events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE audit_events SET payload='tampered'")