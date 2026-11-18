"""Offline, actual SDK middleware regressions. No provider credentials required."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from workbench.agents import AgentRuntime, ROLES, _result, build_agents
from workbench.tests.test_agents import OfflineModel, fake, sample_model


def measured(content="complete", **kwargs):
    return AIMessage(content=content, usage_metadata={
        "input_tokens": 11, "output_tokens": 7, "total_tokens": 18,
        "input_token_details": {"cache_read": 5},
        "output_token_details": {"reasoning": 3}}, **kwargs)


class FailingModel(OfflineModel):
    def _generate(self, *args, **kwargs):
        raise RuntimeError("authorization=NEVER-STORE provider body")


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict("os.environ", {"WORKBENCH_MODEL": "", "LANGSMITH_TRACING": "false",
                                            "LANGCHAIN_TRACING_V2": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Do not patch socket.connect: Windows asyncio needs its local socketpair.
        self.network = patch("socket.create_connection", side_effect=AssertionError("No provider network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def build(self, chat=None, **kwargs):
        return build_agents(self.root, sample_model(), "wf", "original",
                            chat_model=chat or fake([measured()]), **kwargs)

    def invoke(self, graph, thread="usage"):
        return graph.invoke({"messages": [HumanMessage(content="NEVER-STORE input")]},
                            {"configurable": {"thread_id": thread}, "recursion_limit": 20})

    def events(self, bundle):
        with bundle.ledger.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM agent_usage WHERE run_id=? ORDER BY id", (bundle.run_id,))]

    def test_actual_calls_role_attribution_repeated_messages_and_subsets(self):
        with self.build() as bundle:
            for role, graph in {"supervisor": bundle.supervisor, **bundle.specialists}.items():
                self.invoke(graph, role + "1")
                self.invoke(graph, role + "2")
            events = self.events(bundle)
            finished = [e for e in events if e["event"] == "model_completed"]
            self.assertEqual(len(finished), 12)
            self.assertEqual(len({e["call_id"] for e in finished}), 12)
            for role in ROLES:
                self.assertEqual(sum(e["role"] == role for e in finished), 2)
            self.assertTrue(all(e["cached_tokens"] == 5 and e["reasoning_tokens"] == 3 for e in finished))
            usage = _result(bundle, "advisory", "test")["usage"]
            self.assertEqual(usage["total_tokens"], 12 * 18)
            self.assertEqual(usage["input_tokens"], 12 * 11)
            self.assertEqual(usage["output_tokens"], 12 * 7)
            self.assertEqual(usage["reported_calls"], 12)
            self.assertTrue(usage["accounting_complete"])
            self.assertEqual(usage["events"]["count"], 24)
            self.assertTrue(all(e["namespace"] == bundle.namespace and e["input_hashes"] == bundle.input_hashes
                                for e in events))
            self.assertNotIn("NEVER-STORE", json.dumps(events))
            self.assertTrue(all(e["duration_ms"] >= 0 for e in finished))

    def test_fallback_usage_unknown_and_partial_counters(self):
        fallback = AIMessage(content="fallback", response_metadata={"token_usage": {
            "prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24,
            "prompt_tokens_details": {"cached_tokens": 12},
            "completion_tokens_details": {"reasoning_tokens": 2},
            "authorization": "NEVER-STORE"}, "secret": "NEVER-STORE"})
        with self.build(fake([measured(), fallback, AIMessage(content="unknown")])) as bundle:
            for i in range(3):
                self.invoke(bundle.specialists["discovery"], str(i))
            usage = _result(bundle, "advisory", "test")["usage"]
            self.assertEqual(usage["known_input_tokens"], 31)
            self.assertEqual(usage["known_output_tokens"], 11)
            self.assertEqual(usage["known_total_tokens"], 42)
            self.assertEqual(usage["reported_calls"], 2)
            self.assertEqual(usage["unreported_calls"], 1)
            self.assertIsNone(usage["total_tokens"])
            self.assertIsNone(usage["input_tokens"])
            self.assertFalse(usage["accounting_complete"])
            completed = [e for e in self.events(bundle) if e["event"] == "model_completed"]
            self.assertEqual(completed[1]["cached_tokens"], 12)
            self.assertFalse(completed[2]["usage_reported"])
            self.assertIsNone(completed[2]["input_tokens"])
            self.assertNotIn("NEVER-STORE", json.dumps(self.events(bundle)))
        partial = AIMessage(content="partial", response_metadata={"token_usage": {"prompt_tokens": 9}})
        with self.build(fake([partial])) as bundle:
            self.invoke(bundle.supervisor)
            usage = bundle.ledger.usage(bundle.run_id)
            self.assertEqual(usage["input_tokens"], 9)
            self.assertIsNone(usage["output_tokens"])
            self.assertIsNone(usage["total_tokens"])
            self.assertFalse(usage["accounting_complete"])

    def test_actual_model_failure_and_public_runtime_usage(self):
        routes = {role: fake([measured()]) for role in ROLES}
        routes["semantics"] = FailingModel(responses=[AIMessage(content="unused")])
        result = AgentRuntime(self.root, role_models=routes).run(sample_model(), "wf", "original", "test", [], [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["usage"]["reported_calls"], 1)
        self.assertEqual(result["usage"]["failed_calls"], 1)
        self.assertEqual(result["usage"]["unreported_calls"], 1)
        self.assertIsNone(result["usage"]["total_tokens"])
        self.assertEqual(result["usage"]["known_total_tokens"], 18)
        self.assertNotIn("NEVER-STORE", json.dumps(result))
        with closing(sqlite3.connect(self.root / "agents.sqlite3")) as db:
            serialized = " ".join(row[0] for row in db.execute("SELECT payload FROM agent_usage"))
            self.assertNotIn("NEVER-STORE", serialized)
            self.assertIn("model_call_failed", serialized)

    def test_real_delegation_has_distinct_role_endpoints(self):
        routes = {role: fake([measured()]) for role in ROLES}
        for role, model in routes.items():
            model.model_name = "endpoint-" + role
        routes["supervisor"].responses = [measured("", tool_calls=[{
            "id": "delegate", "name": "task", "args": {
                "subagent_type": "discovery", "description": "NEVER-STORE delegation"}}]), measured()]
        routes["artifact"].model_name = "https://secret:NEVER-STORE@example.com"
        with build_agents(self.root, sample_model(), "wf", "original", role_models=routes) as bundle:
            self.invoke(bundle.supervisor)
            self.invoke(bundle.specialists["artifact"], "artifact")
            finished = [e for e in self.events(bundle) if e["event"] == "model_completed"]
            self.assertEqual([e["role"] for e in finished], ["supervisor", "discovery", "supervisor", "artifact"])
            self.assertEqual(finished[1]["endpoint"], "endpoint-discovery")
            self.assertEqual(finished[-1]["endpoint"], "unspecified")
            self.assertEqual(bundle.ledger.usage(bundle.run_id)["total_tokens"], 72)
            self.assertNotIn("NEVER-STORE", json.dumps(self.events(bundle)))

    def capture_middleware(self, chat=None):
        from deepagents import create_deep_agent
        captured = {}

        def capture(**kwargs):
            captured[kwargs["name"]] = kwargs["middleware"]
            return create_deep_agent(**kwargs)

        with patch("deepagents.create_deep_agent", side_effect=capture):
            bundle = self.build(chat)
        self.addCleanup(bundle.close)
        return bundle, captured

    def test_summary_model_call_and_retries_are_audited(self):
        from langchain_core.exceptions import ContextOverflowError

        bundle, middleware = self.capture_middleware()
        summary = next(m for m in middleware["discovery"] if m.name == "SummarizationMiddleware")
        text = summary._create_summary([HumanMessage(content="NEVER-STORE summary history")])
        self.assertEqual(text, "complete")
        completed = [e for e in self.events(bundle) if e["event"] == "model_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["total_tokens"], 18)
        self.assertEqual(self.events(bundle)[0]["purpose"], "summarization")
        original = OfflineModel._generate
        attempts = [0]

        def retry(model, *args, **kwargs):
            attempts[0] += 1
            if attempts[0] == 1:
                raise ContextOverflowError("NEVER-STORE overflow")
            return original(model, *args, **kwargs)

        with patch.object(OfflineModel, "_generate", retry):
            summary._create_summary([HumanMessage(content="history " * 100)])
        usage = bundle.ledger.usage(bundle.run_id)
        self.assertEqual(usage["failed_calls"], 1)
        self.assertEqual(usage["reported_calls"], 2)
        self.assertFalse(usage["accounting_complete"])
        self.assertNotIn("NEVER-STORE", json.dumps(self.events(bundle)))

    def test_tool_success_denied_lookup_and_real_tool_failure(self):
        responses = [measured("", tool_calls=[{"id": "ok", "name": "package_summary", "args": {}}]),
                     measured("", tool_calls=[{"id": "denied", "name": "lookup_evidence",
                                              "args": {"evidence_id": "NEVER-STORE outside"}}]),
                     measured()]
        with self.build(fake(responses)) as bundle:
            self.invoke(bundle.specialists["discovery"])
            tools = [e for e in self.events(bundle) if e["event"].startswith("tool_")]
            self.assertEqual([e["event"] for e in tools], ["tool_started", "tool_completed", "tool_started", "tool_failed"])
            self.assertEqual(tools[-1]["error"], "lookup_denied")
            self.assertNotIn("NEVER-STORE", json.dumps(tools))
        chat = fake([measured("", tool_calls=[{"id": "fail", "name": "impact", "args": {}}]), measured()])
        with self.build(chat) as bundle, patch("workbench.agents.impact_analysis", side_effect=RuntimeError("NEVER-STORE")):
            try:
                self.invoke(bundle.supervisor)
            except RuntimeError:
                pass  # ToolNode error policy may propagate instead of returning ToolMessage.
            failed = [e for e in self.events(bundle) if e["event"] == "tool_failed"]
            self.assertEqual(len(failed), 1)
            self.assertFalse(failed[0]["success"])
            self.assertEqual(failed[0]["error"], "tool_call_failed")
            self.assertNotIn("NEVER-STORE", json.dumps(self.events(bundle)))

    def test_async_hooks_error_tool_status_allowlist_and_sync_only_graph(self):
        bundle, captured = self.capture_middleware()
        budget = next(m for m in captured["discovery"] if m.name == "Budget")
        request = ModelRequest(model=fake(), messages=[HumanMessage(content="NEVER-STORE")])

        async def exercise():
            async def good_model(req):
                return ModelResponse(result=[measured()])

            async def bad_model(req):
                raise RuntimeError("NEVER-STORE")

            await budget.awrap_model_call(request, good_model)
            with self.assertRaises(RuntimeError):
                await budget.awrap_model_call(request, bad_model)
            tool_request = SimpleNamespace(tool_call={"name": "package_summary", "args": {"secret": "NEVER-STORE"}})

            async def bad_tool(req):
                return ToolMessage(content="NEVER-STORE error", tool_call_id="bad", status="error")

            await budget.awrap_tool_call(tool_request, bad_tool)
            tool_request.tool_call["name"] = "execute"
            with self.assertRaises(PermissionError):
                await budget.awrap_tool_call(tool_request, bad_tool)
            with self.assertRaisesRegex(RuntimeError, "Async agent graphs are prohibited"):
                await budget.abefore_model({}, None)

        asyncio.run(exercise())
        self.assertEqual(bundle.ledger.usage(bundle.run_id)["failed_calls"], 1)
        self.assertEqual(len([e for e in self.events(bundle) if e["event"] == "tool_failed"]), 2)
        self.assertNotIn("NEVER-STORE", json.dumps(self.events(bundle)))
        with self.assertRaises(PermissionError):
            budget.wrap_tool_call(SimpleNamespace(tool_call={"name": "execute", "args": {}}),
                                  lambda req: self.fail("Denied handler must never execute"))

    def test_immutable_restart_scoped_ledger_bridge_and_no_network_status(self):
        bridged = []

        def sink(event):
            bridged.append(event.copy())
            event["role"] = "tampered"
            raise RuntimeError("bridge unavailable")

        with self.build(audit_sink=sink) as bundle:
            self.invoke(bundle.specialists["discovery"])
            old_id = bundle.run_id
            self.assertEqual(len(bridged), 2)
            self.assertTrue(all(e["role"] == "discovery" for e in self.events(bundle)))
            with bundle.ledger.connect() as db:
                for sql in ("UPDATE agent_usage SET role='tampered'", "DELETE FROM agent_usage"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                        db.execute(sql)
        with self.build() as bundle:
            self.assertEqual(bundle.ledger.usage(old_id)["total_tokens"], 18)
            self.assertEqual(bundle.ledger.usage(bundle.run_id)["reported_calls"], 0)
            self.invoke(bundle.supervisor)
            self.assertEqual(bundle.ledger.usage(bundle.run_id)["total_tokens"], 18)
        routes = {role: fake([measured()]) for role in ROLES}
        with patch.object(OfflineModel, "_generate", side_effect=AssertionError("No model calls")):
            self.assertTrue(AgentRuntime(self.root, role_models=routes).status()["available"])
        public = AgentRuntime(self.root / "public", role_models=routes, audit_sink=bridged.append).run(
            sample_model(), "wf", "original", "review", [], [])
        self.assertEqual(public["usage"]["reported_calls"], 6)
        self.assertEqual(public["usage"]["total_tokens"], 108)


if __name__ == "__main__":
    unittest.main()