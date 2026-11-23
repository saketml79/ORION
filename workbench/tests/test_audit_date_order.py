import csv
import io
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from fastapi import FastAPI

from workbench.audit import _log_timestamp_sort_key, install_audit


def make_audit_routes(tmp_path):
    app = FastAPI()
    app.state.service = SimpleNamespace(state=tmp_path)
    install_audit(app, record_initialization=False)
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    return app.state.audit, routes["/api/logs"], routes["/api/logs/export"]


def list_logs(logs, *, level="", role="", run_id="", offset=0, limit=50):
    return logs(level=level, role=role, run_id=run_id, offset=offset, limit=limit)


def test_missing_timestamp_is_not_a_known_instant():
    assert _log_timestamp_sort_key({})[0] is False


def test_timestamp_order_offsets_invalid_values_and_pages(tmp_path):
    audit, logs, _ = make_audit_routes(tmp_path)
    events = [
        ("late", "2026-11-24T00:00:00+00:00"),
        ("old", "2026-11-20T00:00:00+00:00"),
        ("equal-first", "2026-11-23T12:00:00+02:00"),
        ("equal-second", "2026-11-23T10:00:00Z"),
        ("malformed", "not-a-date"),
        ("missing", None),
        ("naive", "2026-12-01T10:00:00"),
        ("non-string", 9999999999),
        ("overflow", "0001-01-01T00:00:00+14:00"),
        ("minimum", "0001-01-01T00:00:00+00:00"),
    ]
    for name, timestamp in events:
        audit.append({"message": name, "timestamp": timestamp})

    expected = ["late", "equal-second", "equal-first", "old", "minimum",
                "overflow", "non-string", "naive", "missing", "malformed"]
    pages = [list_logs(logs, offset=offset, limit=3) for offset in range(0, 12, 3)]
    assert [row["message"] for page in pages for row in page["items"]] == expected
    assert all(page["total"] == len(events) and page["chain_valid"] for page in pages)
    assert [row["message"] for row in pages[-1]["items"]] == expected[-1:]
    records, valid = audit.records()
    assert valid
    assert [row["message"] for row in records] == [name for name, _ in events]


def test_ties_use_database_sequence_even_if_payload_sequence_is_overridden(tmp_path):
    audit, logs, _ = make_audit_routes(tmp_path)
    audit.append({"message": "first", "sequence": 9999, "timestamp": "2026-11-23T10:00:00Z"})
    audit.append({"message": "second", "sequence": 1, "timestamp": "2026-11-23T10:00:00Z"})
    assert [row["message"] for row in list_logs(logs)["items"]] == ["second", "first"]


def test_filters_pagination_and_export_preserve_contract(tmp_path):
    audit, logs, export = make_audit_routes(tmp_path)
    for message, timestamp, level, role, run_id in [
        ("first", "2026-11-23T10:00:00Z", "ERROR", None, "run-a"),
        ("second", "2026-11-25T10:00:00Z", "ERROR", "discovery", "run-a"),
        ("third", "2026-11-24T10:00:00Z", "ERROR", None, "run-a"),
        ("fourth", "2026-11-26T10:00:00Z", "INFO", None, "run-a"),
        ("fifth", "2026-11-27T10:00:00Z", "ERROR", "", "run-b"),
    ]:
        audit.append({"message": message, "timestamp": timestamp, "level": level, "role": role, "run_id": run_id})
    params = {"level": "error", "role": "__application__", "run_id": "run-a"}
    first = list_logs(logs, **params, offset=0, limit=1)
    second = list_logs(logs, **params, offset=1, limit=1)
    assert first["total"] == second["total"] == 2
    assert [first["items"][0]["message"], second["items"][0]["message"]] == ["third", "first"]
    assert [row["message"] for row in list_logs(logs, role="discovery")["items"]] == ["second"]
    exported = export(**params)
    rows = list(csv.DictReader(io.StringIO(exported.body.decode("utf-8"))))
    assert [row["message"] for row in rows] == ["first", "third"]


def test_chain_validation_covers_filtered_out_rows(tmp_path):
    audit, logs, _ = make_audit_routes(tmp_path)
    audit.append({"message": "excluded", "level": "INFO", "timestamp": "2026-11-24T00:00:00Z"})
    audit.append({"message": "included", "level": "ERROR", "timestamp": "2026-11-23T00:00:00Z"})
    with closing(sqlite3.connect(audit.path)) as db, db:
        db.execute("DROP TRIGGER audit_no_update")
        db.execute("UPDATE audit_events SET payload = replace(payload, 'excluded', 'modified') WHERE sequence = 1")
    result = list_logs(logs, level="error")
    assert [row["message"] for row in result["items"]] == ["included"]
    assert result["chain_valid"] is False