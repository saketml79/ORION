"""Read-only projection contracts. No model, tool handler, network or real ledger."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from workbench.agent_usage_receipt import current_usage_receipt, model_activity, tool_activity
from workbench.domain import _hash


START = "2026-11-17T10:00:00+00:00"
END = "2026-11-17T10:00:02+00:00"
CALL = "a" * 32


def event(kind, call_id=CALL, role="discovery", created=None, **payload):
    return (role, call_id, kind, payload,
            created if created is not None else START if kind.endswith("_started") else END)


def pair(terminal="tool_completed", **payload):
    return [event("tool_started", tool_name="lookup_evidence"),
            event(terminal, tool_name="lookup_evidence", **payload)]


class ToolActivityTests(unittest.TestCase):
    def setUp(self):
        # These tests must never contact a provider or acquire a real database.
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo",
                       "sqlite3.connect"):
            guard = patch(target, side_effect=AssertionError("Unexpected I/O"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_completed_failed_rejected_and_pending_calls(self):
        rows = []
        for index, state in enumerate(("completed", "failed", "rejected", "started"), 1):
            call_id = str(index) * 32
            rows.append(event("tool_started", call_id=call_id, tool_name="submit_finding"))
            if state != "started":
                rows.append(event("tool_" + state, call_id=call_id,
                                  tool_name="submit_finding", duration_ms=12.5))
        result = tool_activity(rows)
        self.assertEqual(result["tool_activity_status"], "recorded")
        self.assertEqual(result["tool_turn_definition"], "one_actual_tool_call")
        self.assertEqual(result["tool_turn_count"], 4)
        for index, turn in enumerate(result["tool_turns"], 1):
            self.assertEqual(turn["turn"], index)
            self.assertIsNone(turn["recorded_retry_count"])
            self.assertEqual(turn["retry_status"], "not_recorded")
            if turn["status"] == "started":
                self.assertIsNone(turn["duration_ms"])
                self.assertIsNone(turn["duration_source"])
            else:
                self.assertEqual(turn[turn["status"] + "_at"], END)
                self.assertEqual(turn["duration_ms"], 12.5)
                self.assertEqual(turn["duration_source"], "recorded_monotonic")
            for state in ("completed", "failed", "rejected"):
                if state != turn["status"]:
                    self.assertIsNone(turn[state + "_at"])

    def test_duration_preference_zero_and_timestamp_fallback(self):
        for duration in (0, 7, 0.125):
            with self.subTest(duration=duration):
                turn = tool_activity(pair(duration_ms=duration))["tool_turns"][0]
                self.assertEqual(turn["duration_ms"], duration)
                self.assertEqual(turn["duration_source"], "recorded_monotonic")
        for payload in ({}, {"duration_ms": None}):
            turn = tool_activity(pair(**payload))["tool_turns"][0]
            self.assertEqual(turn["duration_ms"], 2000)
            self.assertEqual(turn["duration_source"], "recorded_timestamps")

    def test_nonfinite_negative_boolean_and_nonnumeric_durations_unavailable(self):
        for duration in (-1, float("nan"), float("inf"), -float("inf"), True,
                         "12", [], {}, 10 ** 400):
            with self.subTest(duration=repr(duration)):
                self.assert_unavailable(pair(duration_ms=duration), "invalid_tool_duration")
        self.assert_unavailable([
            event("tool_started", tool_name="lookup_evidence", duration_ms=-1)
        ], "invalid_tool_duration")

    def test_timestamps_require_timezone_and_nonnegative_elapsed_time(self):
        for stamp in ("bad", "2026-11-17T10:00:00", "", 42, False):
            for index in (0, 1):
                with self.subTest(stamp=stamp, index=index):
                    rows = pair()
                    rows[index] = (*rows[index][:4], stamp)
                    self.assert_unavailable(rows, "invalid_tool_timestamp")
        rows = pair(duration_ms=1)
        rows[1] = (*rows[1][:4], "2026-11-17T09:59:59+00:00")
        self.assert_unavailable(rows, "tool_timestamp_before_start")
        rows = pair()
        rows[1] = (*rows[1][:4], "2026-11-17T11:00:02+01:00")
        self.assertEqual(tool_activity(rows)["tool_turns"][0]["duration_ms"], 2000)

    def test_bad_ids_roles_names_and_payload_are_not_projected(self):
        for call_id in ("", "a" * 31, "A" * 32, "x" * 32, None, 123):
            self.assert_unavailable([event("tool_started", call_id=call_id, tool_name="read_file")],
                                    "invalid_tool_call_identity")
        self.assert_unavailable([event("tool_started", role="outsider", tool_name="read_file")],
                                "invalid_tool_call_identity")
        for name in ("", "read file", "read.file", "read/file", "x" * 101, 42,
                     "https://private.example/tool?token=PRIVATE_PAYLOAD"):
            self.assert_unavailable([event("tool_started", tool_name=name)], "invalid_tool_name")
        self.assert_unavailable([event("tool_started")], "missing_tool_start_name")
        self.assert_unavailable([("discovery", CALL, "tool_started", [], START)],
                                "invalid_tool_payload")

    def test_exact_role_and_call_pairing_interleaving_and_duplicate_detection(self):
        rows = [event("tool_started", tool_name="read_file"),
                event("tool_started", role="artifact", tool_name="read_file"),
                event("tool_completed", role="artifact", tool_name="read_file"),
                event("tool_failed", tool_name="read_file")]
        turns = tool_activity(rows)["tool_turns"]
        self.assertEqual([(t["role"], t["status"]) for t in turns],
                         [("discovery", "failed"), ("artifact", "completed")])
        self.assert_unavailable([rows[0], rows[2]], "unmatched_tool_completion")
        self.assert_unavailable([rows[0], rows[0]], "duplicate_tool_start")
        self.assert_unavailable(pair() + [pair()[1]], "unmatched_tool_completion")
        self.assert_unavailable([pair()[1]], "unmatched_tool_completion")
        self.assert_unavailable([pair()[0], event("tool_completed", call_id="b" * 32)],
                                "unmatched_tool_completion")
        self.assert_unavailable([pair()[0], event("tool_completed", tool_name="other_tool")],
                                "tool_name_mismatch")

    def test_terminal_without_name_uses_only_exact_start(self):
        rows = [pair()[0], event("tool_completed")]
        self.assertEqual(tool_activity(rows)["tool_turns"][0]["tool_name"], "lookup_evidence")
        self.assert_unavailable([event("tool_completed")], "unmatched_tool_completion")

    def test_repeated_names_rejections_and_provider_retries_do_not_infer_tool_retries(self):
        rows = pair("tool_rejected") + [
            event("model_retry_wait", retry_count=1, retry_wait_seconds=2.0, status_code=429),
            event("model_retry_resuming", retry_count=1, retry_wait_seconds=0.0, status_code=429),
            event("model_retry_succeeded", retry_count=1, retry_wait_seconds=0.0, status_code=429),
            event("tool_started", call_id="b" * 32, tool_name="lookup_evidence"),
            event("tool_completed", call_id="b" * 32, tool_name="lookup_evidence")]
        result = tool_activity(rows)
        self.assertEqual(result["tool_turn_count"], 2)
        self.assertTrue(all(t["recorded_retry_count"] is None for t in result["tool_turns"]))
        # No tool producer owns these fields. Do not invent a retry contract by
        # accepting arbitrary payload keys or borrowing model retry metadata.
        for value in (0, 2, -1, True, "PRIVATE_PAYLOAD", {"count": 2}):
            turn = tool_activity(pair(retry_count=value, retry_status=value,
                                      recorded_retry_count=value))["tool_turns"][0]
            self.assertIsNone(turn["recorded_retry_count"])
            self.assertEqual(turn["retry_status"], "not_recorded")

    def test_privacy_input_immutability_and_no_queries(self):
        rows = pair(args={"password": "PRIVATE_PAYLOAD"}, result="PRIVATE_PAYLOAD",
                    error="PRIVATE_PAYLOAD", input_hash="PRIVATE_PAYLOAD",
                    endpoint="PRIVATE_PAYLOAD", success=True)
        before = deepcopy(rows)
        result = tool_activity(rows)
        self.assertEqual(rows, before)
        self.assertNotIn("PRIVATE_PAYLOAD", json.dumps(result))
        self.assertEqual(set(result["tool_turns"][0]), {
            "turn", "call_id", "role", "tool_name", "status", "started_at",
            "completed_at", "failed_at", "rejected_at", "duration_ms", "duration_source",
            "recorded_retry_count", "retry_status"})
        self.assertEqual(tool_activity([])["tool_turn_count"], 0)

    def assert_unavailable(self, rows, reason):
        result = tool_activity(rows)
        self.assertEqual(result["tool_activity_status"], "unavailable")
        self.assertEqual(result["tool_activity_unavailable_reason"], reason)
        self.assertEqual(result["tool_turns"], [])
        self.assertIsNone(result["tool_turn_count"])
        self.assertNotIn("PRIVATE_PAYLOAD", json.dumps(result))


class ScopedReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Unexpected network"))
            guard.start()
            self.addCleanup(guard.stop)
        self.hashes = {"model": "1" * 64, "documents": "2" * 64, "feedback": "3" * 64}
        self.record = {"id": "4" * 32, "package_hash": "5" * 64,
                       "workflow_id": "6" * 64, "scenario": "original", "started_at": START,
                       "input_hashes": {"model": self.hashes["model"]},
                       "input_versions": {"documents": self.hashes["documents"],
                                          "reviews": self.hashes["feedback"]},
                       "observation_snapshot": {"turns": [{"turn": 1}], "schema_version": 1},
                       "output": {"run_id": "7" * 32, "input_hashes": self.hashes}}
        self.namespace = _hash([self.record["package_hash"], self.hashes,
                                self.record["workflow_id"], self.record["scenario"]])
        self.models = [event("model_started", call_id="c" * 32, endpoint="model-endpoint"),
                       event("model_completed", call_id="c" * 32, endpoint="model-endpoint",
                             usage_reported=True, input_tokens=3, output_tokens=2, total_tokens=5)]

    def project(self, reader, tools, tamper_scope=False):
        # Stub the existing SELECT results, not a second database or fixture app.
        # The same rows serve both readback paths, with no ledger writes allowed.
        rows = []
        for number, (role, call_id, kind, details, stamp) in enumerate(self.models + tools, 1):
            payload = {**details, "run_id": self.record["output"]["run_id"],
                       "namespace": self.namespace, "input_hashes": self.hashes,
                       "role": role, "call_id": call_id, "event": kind}
            if tamper_scope and number == len(self.models) + len(tools):
                payload["namespace"] = "8" * 64
            rows.append((number, self.namespace, role, call_id, kind, json.dumps(payload), stamp))
        self.record["output"]["usage"] = {"events": {
            "table": "agent_usage", "run_id": self.record["output"]["run_id"],
            "count": len(rows), "first_id": 1, "last_id": len(rows)}}
        before = deepcopy(self.record)
        queries = []

        def execute(sql, params=()):
            queries.append((sql, params))
            cursor = MagicMock()
            if sql == "BEGIN":
                return cursor
            if sql == "SELECT namespace,input_hashes FROM agent_runs WHERE run_id=?":
                cursor.fetchone.return_value = (self.namespace, json.dumps(self.hashes))
            elif sql == "SELECT run_id,namespace,input_hashes,created_at FROM agent_runs WHERE created_at>=?":
                cursor.__iter__.return_value = iter([
                    (self.record["output"]["run_id"], self.namespace, json.dumps(self.hashes), START)])
            elif sql == "SELECT id,namespace,role,call_id,event,payload,created_at FROM agent_usage WHERE run_id=? ORDER BY id":
                cursor.__iter__.return_value = iter(rows)
                cursor.fetchall.return_value = rows
            else:
                self.fail("Unexpected query or write: " + sql)
            return cursor

        db = MagicMock()
        db.execute.side_effect = execute
        with patch("sqlite3.connect", return_value=db) as connect, \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "glob", return_value=[]):
            result = reader(Path("receipt-test-root"), self.record)
        self.assertEqual(self.record, before)
        self.assertEqual(len(queries), 3)  # Existing BEGIN + two SELECTs only.
        connect.assert_called_once()
        self.assertIn("?mode=ro", connect.call_args.args[0])
        self.assertTrue(connect.call_args.kwargs["uri"])
        db.close.assert_called_once()
        return result

    def test_both_read_paths_share_projection_and_preserve_saved_history(self):
        from workbench.app import _stored_agent_progress

        tools = pair(duration_ms=4)
        expected = tool_activity(self.models + tools)
        model = model_activity(self.models)
        for reader in (current_usage_receipt, _stored_agent_progress):
            with self.subTest(reader=reader.__name__):
                receipt = self.project(reader, tools)
                self.assertEqual(receipt["status"], "verified")
                for key, value in {**model, **expected}.items():
                    self.assertEqual(receipt[key], value)
                self.assertEqual(receipt["usage"]["total_tokens"], 5)
                self.assertTrue(receipt["historical_output_unchanged"])
                self.assertNotIn("tool_turns", self.record["observation_snapshot"])
                self.assertNotIn("tool_turns", self.record["output"])

    def test_tool_defects_leave_verified_model_usage_available_in_both_paths(self):
        from workbench.app import _stored_agent_progress

        for tools in ([pair()[1]], [pair()[0], event("tool_completed", role="artifact")],
                      pair(duration_ms=-1), [event("tool_started", tool_name="private name")],
                      [event("tool_started", call_id="bad", tool_name="read_file")]):
            for reader in (current_usage_receipt, _stored_agent_progress):
                with self.subTest(reader=reader.__name__, tools=tools):
                    receipt = self.project(reader, tools)
                    self.assertEqual(receipt["status"], "verified")
                    self.assertEqual(receipt["tool_activity_status"], "unavailable")
                    self.assertIsNotNone(receipt["tool_activity_unavailable_reason"])
                    self.assertEqual(receipt["turns"], model_activity(self.models)["turns"])
                    self.assertEqual(receipt["usage"]["total_tokens"], 5)

    def test_scope_mismatch_still_fails_closed(self):
        from workbench.app import _stored_agent_progress

        for reader in (current_usage_receipt, _stored_agent_progress):
            with self.subTest(reader=reader.__name__):
                receipt = self.project(reader, pair(), tamper_scope=True)
                self.assertEqual(receipt["status"], "unverified")
                self.assertNotIn("tool_turns", receipt)

    def test_exact_role_call_pairing_preserves_pending_legacy_progress(self):
        from workbench.app import _stored_agent_progress

        tools = [event("tool_started", tool_name="read_file"),
                 event("tool_started", role="artifact", tool_name="read_file"),
                 event("tool_completed", role="artifact", tool_name="read_file")]
        receipt = self.project(_stored_agent_progress, tools)
        self.assertEqual(receipt["tool_turn_count"], 2)
        self.assertEqual([call["role"] for call in receipt["active_calls"]], ["discovery"])
        self.assertEqual(receipt["counts"]["tool_started"], 2)
        self.assertEqual(receipt["counts"]["tool_completed"], 1)
        self.assertEqual(receipt["last_event"]["event"], "tool_completed")