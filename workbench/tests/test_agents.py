"""Offline regressions for the real, pinned Deep Agents harness."""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from importlib.metadata import version
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr, ValidationError

from workbench.agents import AgentRuntime, Finding, ROLES, _design_submission_schema, _objective, _result, _safe, _safe_validation_issues, build_agents
from workbench.domain import _redact
from workbench.redesign import Design


class OfflineModel(FakeMessagesListChatModel):
    """SDK fake with the tool binding surface required by create_deep_agent."""

    model_name: str = "offline-endpoint"
    _seen: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def fake(responses=None):
    return OfflineModel(responses=responses or [AIMessage(content="Offline advisory.")],
                        custom_get_token_ids=lambda text: list(range(len(text) // 4)))


def sample_model(extra=0):
    def node(identifier, kind):
        return {"id": identifier, "kind": kind, "name": identifier, "scope": {},
                "attributes": {}, "evidence": {"locator": "/" + identifier},
                "fields": [], "properties": []}

    nodes = [node("wf", "workflow"), node("mapping", "mapping"),
             node("source", "source"), node("target", "target"),
             node("unknown", "unresolved_reference"), node("outside", "workflow")]
    nodes += [node(f"n{i}", "transformation") for i in range(extra)]
    edges = [{"id": "e" + n["id"], "source": "wf", "target": n["id"],
              "kind": "contains", "evidence": n["evidence"], "attributes": {}}
             for n in nodes if n["id"] not in {"wf", "outside"}]
    return {"package": {"sha256": "fixture-package"}, "nodes": nodes, "edges": edges,
            "coverage": {"unsupported_tags": {"ODD": 1}},
            "diagnostics": [{"object_id": "unknown", "code": "UNRESOLVED_REFERENCE"}]}


def finding(role="discovery", **overrides):
    return {"role": role, "summary": "Captured workflow needs review.",
            "evidence_ids": ["wf"], "assumptions": [], "blockers": ["Runtime parity is unknown."],
            "proposed_changes": [], "alternatives": [], "status": "needs_information", **overrides}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Network is forbidden even if a future regression accidentally selects
        # a provider or enables a hidden source/telemetry call.
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.env = patch.dict("os.environ", {"WORKBENCH_MODEL": "", "LANGSMITH_TRACING": "false",
                                            "LANGCHAIN_TRACING_V2": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def build(self, model=None, chat_model=None, **kwargs):
        return build_agents(self.root, model or sample_model(), "wf", "original",
                            chat_model=chat_model or fake(), **kwargs)

    def test_design_submission_errors_name_invalid_task_types_and_empty_edge_evidence(self):
        invalid = {
            "title": "Invalid outline", "tasks": [
                {"id": "task-a", "label": "Email", "implementation_kind": "email_task",
                 "purpose": "Notify", "evidence_ids": ["a" * 64]}],
            "edges": [{"source": "task-a", "target": "task-a", "evidence_ids": []}],
            "rationale": "Test only", "alternatives": [{"label": "Retain", "rationale": "Test",
                "tradeoffs": ["No change"], "evidence_ids": ["a" * 64]}],
            "coverage": [{"original_task_id": "a" * 64, "proposed_task_ids": ["task-a"],
                "disposition": "mapped", "rationale": "Test", "evidence_ids": ["a" * 64]}],
            "evidence_ids": ["a" * 64], "reuse_groups": [], "parameters": [],
            "proposed_constraints": [], "blockers": []}
        with self.assertRaises(ValidationError) as caught:
            Design.model_validate(invalid)
        issues = _safe_validation_issues(caught.exception)
        self.assertIn("tasks.0.implementation_kind", [issue["field"] for issue in issues])
        edge_issue = next(issue for issue in issues if issue["field"] == "edges.0.evidence_ids")
        self.assertIn("canonical XML evidence ID", edge_issue["issue"])

    def test_proposal_failure_result_is_not_reported_as_information_only(self):
        bundle = SimpleNamespace(
            findings=[], redesigns=[], design_requested=True,
            redesign_rejections=[{"role": "architecture", "error": "design_schema_validation_failed",
                "instruction": "Correct task kinds and edge evidence.",
                "validation_issues": [{"field": "tasks.0.implementation_kind", "issue": "Unsupported task kind."}]}],
            input_hashes={}, evidence=SimpleNamespace(neighbor_pages=[], neighbor_count=0, neighbor_retrieved=[]),
            role_completion=[], run_id="run-test", ledger=SimpleNamespace(usage=lambda _: {}))
        result = _result(bundle, "proposal_failed", "No logical design was accepted.")
        self.assertEqual(result["status"], "proposal_failed")
        self.assertEqual(result["design_submission"]["status"], "not_produced")
        self.assertEqual(result["design_submission"]["rejections"][0]["validation_issues"][0]["field"],
                         "tasks.0.implementation_kind")

    def test_architecture_guidance_is_workflow_neutral(self):
        source = Path(__file__).resolve().parents[1].joinpath("agents.py").read_text(encoding="utf-8")
        self.assertNotIn("The EWH mapping has an update-by-key write", source)
        self.assertIn("Do not assume a task type, session write mode", source)
        self.assertIn("Every proposed edge must use proposed task IDs at both ends", source)

    def test_exact_sdk_and_opt_in_status_without_key_lookup(self):
        self.assertEqual(version("deepagents"), "0.7.32")
        runtime = AgentRuntime(self.root)
        self.assertTrue(runtime.status()["sdk_available"])
        self.assertFalse(runtime.status()["available"])
        self.assertFalse(AgentRuntime(self.root, "openai:explicit").status()["available"])
        with patch.dict("os.environ", {"WORKBENCH_MODEL": "openai:explicit"}):
            self.assertTrue(runtime.status()["available"])
            self.assertFalse(runtime.status()["authentication_verified"])
            self.assertFalse(AgentRuntime(self.root, "other:different").status()["available"])
        for invalid in ("gpt-any", "", "openai:", "openai:model?api_key=SECRET"):
            with patch.dict("os.environ", {"WORKBENCH_MODEL": invalid}):
                self.assertFalse(runtime.status()["available"])
        self.assertFalse(self.root.joinpath("agents.sqlite3").exists())

    def test_no_dotenv_or_automatic_model_call(self):
        self.root.joinpath(".env").write_text("WORKBENCH_MODEL=openai:unapproved\nAPI_KEY=DO_NOT_READ")
        with patch("langchain.chat_models.init_chat_model", side_effect=AssertionError("Must not initialize")):
            result = AgentRuntime(self.root).run(sample_model(), "wf", "original", "review", [], [])
            self.assertEqual(result["status"], "unavailable")
            with self.assertRaises(ValueError):
                build_agents(self.root, sample_model(), "wf", "original")

    def test_compiled_roles_exact_task_tool_and_read_only_toolsets(self):
        with self.build() as bundle:
            self.assertEqual(set(bundle.specialists), set(ROLES[1:]))
            task = bundle.supervisor.nodes["tools"].bound.tools_by_name["task"]
            schema = task.args_schema.model_json_schema()
            self.assertIn("subagent_type", schema["properties"])
            for role in ROLES[1:]:
                self.assertIn(role, task.description)
            self.assertNotIn("- general-purpose:", task.description)
            for role, graph in {"supervisor": bundle.supervisor, **bundle.specialists}.items():
                names = set(graph.nodes["tools"].bound.tools_by_name)
                self.assertIn("sql_role_inventory", names)
                self.assertFalse(names & {"write_file", "edit_file", "delete", "execute"})
                if role != "supervisor":
                    self.assertNotIn("task", names)
                backend = bundle.contexts[role]["backend"]
                self.assertTrue(backend.write("/CONTEXT.md", "tampered").error)
                self.assertTrue(backend.edit("/CONTEXT.md", "policy", "bad").error)
                self.assertTrue(backend.delete("/CONTEXT.md").error)
                self.assertEqual(backend.upload_files([("/injected", b"bad")])[0].error, "permission_denied")
                with self.assertRaisesRegex(ValueError, "Path traversal not allowed"):
                    backend.read("/../agents.sqlite3")

    def test_each_role_actually_loads_own_memory_and_skill_and_reads_skill(self):
        shared_contract = Path(__file__).resolve().parents[1].joinpath(
            "skills/production-conversion-contract.md").read_text(encoding="utf-8")
        chat = fake([
            AIMessage(content="", tool_calls=[{"id": "read-skill", "name": "read_file",
                                               "args": {"file_path": "/library/review/SKILL.md", "limit": 200}}]),
            AIMessage(content="offline complete"),
        ])
        with self.build(chat_model=chat) as bundle:
            for role, graph in {"supervisor": bundle.supervisor, **bundle.specialists}.items():
                graph.invoke({"messages": [{"role": "user", "content": "Inspect your skill."}]},
                             {"configurable": {"thread_id": "load-" + role}, "recursion_limit": 12})
                messages = chat._seen[-1]
                system = messages[0].text
                self.assertIn(role.capitalize() if role != "validation" else "Independent validation", system)
                self.assertIn("/CONTEXT.md", system)
                self.assertIn("/library/review/SKILL.md", system)
                self.assertIn(shared_contract, system)
                self.assertIn(json.dumps(_design_submission_schema(), sort_keys=True,
                                         ensure_ascii=True, separators=(",", ":")), system)
                for requirement in ("caller_workflow_id/candidate_workflow_ids", "-nowait launch",
                                    "never expands workflow authority", "status updates",
                                    "constants, thresholds", "Artifact plans are optional",
                                    "Static", "submit_generation_artifact_set"):
                    self.assertIn(requirement.casefold(), system.casefold())
                for semantic in ("ports, types, nulls, duplicates, predicates",
                                 "complete Java control and data behavior",
                                 "procedure bodies and transitive calls",
                                 "Missing coverage remains unverified"):
                    self.assertIn(semantic, system)
                if role in {"architecture", "supervisor"}:
                    self.assertIn("No fixed task count", system)
                    self.assertIn("ALL direct original workflow taskinstance IDs", system)
                    self.assertNotIn("Call submit_wil_net_dis_outline", system)
                self.assertTrue(any(m.type == "tool" and "# Activation" in str(m.content) for m in messages))
                skill = Path(bundle.contexts[role]["backend_root"]).joinpath(
                    "library/review/SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(any(m.type == "tool" and all(line in str(m.content)
                                    for line in skill.splitlines() if line.strip()) for m in messages))
                self.assertEqual(bundle.contexts[role]["namespace"][-1], role)
                self.assertEqual(bundle.contexts[role]["memory"], ["/CONTEXT.md"])
                self.assertEqual(bundle.contexts[role]["skills"], ["/library/"])

    def test_actual_task_delegates_to_separately_compiled_specialist(self):
        chat = fake([
            AIMessage(content="", tool_calls=[{"id": "delegate", "name": "task",
                      "args": {"subagent_type": "discovery", "description": "Read-only discovery review."}}]),
            AIMessage(content="Discovery offline response."), AIMessage(content="Supervisor complete."),
        ])
        with self.build(chat_model=chat) as bundle:
            result = bundle.supervisor.invoke({"messages": [{"role": "user", "content": "Delegate discovery."}]},
                                              {"configurable": {"thread_id": "delegate"}, "recursion_limit": 16})
            self.assertTrue(any(m.type == "tool" and "Discovery offline response" in str(m.content)
                                for m in result["messages"]))
            self.assertTrue(any("Role: discovery" in str(messages[0].content) for messages in chat._seen))

    def test_role_models_delegate_through_actual_compiled_task_with_distinct_models(self):
        routes: dict[str, Any] = {role: fake() for role in ROLES}
        for role, chat in routes.items():
            chat.model_name = "endpoint-" + role
        routes["supervisor"].responses = [
            AIMessage(content="", tool_calls=[{"id": "delegate-" + role, "name": "task",
                "args": {"subagent_type": role, "description": "Read-only specialist review."}}])
            for role in ROLES[1:]
        ] + [AIMessage(content="Supervisor complete.")]
        for role in ROLES[1:]:
            routes[role].responses = [AIMessage(content=role + " distinct offline response.")]
        with patch("langchain.chat_models.init_chat_model", side_effect=AssertionError("No generic provider")), \
                build_agents(self.root, sample_model(), "wf", "original", role_models=routes,
                             identity_scope="workspace/user-a") as bundle:
            self.assertTrue(all(not chat._seen for chat in routes.values()))
            result = bundle.supervisor.invoke(
                {"messages": [{"role": "user", "content": "Delegate each specialist."}]},
                {"configurable": {"thread_id": "routed-delegation"}, "recursion_limit": 32})
            self.assertEqual(len(routes["supervisor"]._seen), 6)
            for role in ROLES[1:]:
                self.assertEqual(len(routes[role]._seen), 1)
                self.assertIn("Role: " + role, str(routes[role]._seen[0][0].content))
                self.assertTrue(any(m.type == "tool" and role + " distinct offline response" in str(m.content)
                                    for m in result["messages"]))

    def test_invalid_role_models_fail_before_provider_initialization_or_storage(self):
        routes: dict[str, Any] = {role: fake() for role in ROLES}
        invalid = ["provider:model", {}, {**routes, "extra": fake()},
                   {r: m for r, m in routes.items() if r != "supervisor"},
                   {**routes, "discovery": "provider:model"},
                   {**routes, "artifact": type("NotAModel", (), {"bind_tools": lambda self: None})()}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), patch(
                    "langchain.chat_models.init_chat_model", side_effect=AssertionError("No generic provider")):
                with self.assertRaises(ValueError):
                    AgentRuntime(self.root, role_models=value)
                with self.assertRaises(ValueError):
                    build_agents(self.root, sample_model(), "wf", "original", role_models=value)
        with patch.object(OfflineModel, "bind_tools", None):
            with self.assertRaises(ValueError):
                AgentRuntime(self.root, role_models=routes)
        with self.assertRaises(ValueError):
            AgentRuntime(self.root, role_models=routes, identity_scope=cast(Any, None))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_provided_model_status_is_explicit_safe_and_does_not_invoke(self):
        routes: dict[str, Any] = {role: fake() for role in ROLES}
        for role, chat in routes.items():
            chat.model_name = "endpoint-" + role
            chat.metadata = {"authorization": "Bearer NEVER-SERIALIZE"}
        routes["artifact"].model_name = "https://user:NEVER-SERIALIZE@example.com/model?token=SECRET"
        runtime = AgentRuntime(self.root, "ignored:legacy", role_models=routes, identity_scope="workspace/user-a")
        with patch("langchain.chat_models.init_chat_model", side_effect=AssertionError("No generic provider")):
            status = runtime.status()
        self.assertTrue(status["available"])
        self.assertTrue(status["model_configured"])
        self.assertEqual(status["authentication"], "model_provided")
        self.assertFalse(status["authentication_verified"])
        self.assertEqual(status["model_routes"]["discovery"], "endpoint-discovery")
        self.assertEqual(status["model_routes"]["artifact"], "unspecified")
        self.assertNotIn("NEVER-SERIALIZE", json.dumps(status))
        self.assertNotIn("workspace/user-a", json.dumps(status))
        self.assertTrue(all(not chat._seen for chat in routes.values()))
        self.assertEqual(list(self.root.iterdir()), [])
        with patch("workbench.agents.version", return_value="0.0"):
            self.assertFalse(runtime.status()["available"])

    def test_identity_memory_checkpoints_restart_and_route_hash_separation(self):
        routes: dict[str, Any] = {role: fake() for role in ROLES}
        config = {"configurable": {"thread_id": "same-thread"}, "recursion_limit": 10}

        def build(identity):
            return build_agents(self.root, sample_model(), "wf", "original", role_models=routes,
                                identity_scope=identity, run_id="same-run")

        with build("workspace/user-a") as first:
            first.specialists["discovery"].invoke(
                {"messages": [{"role": "user", "content": "Private identity A request."}]}, config)
            first.tools["discovery"]["lookup_evidence"].invoke({"evidence_id": "wf"})
            first.tools["discovery"]["remember_finding"].invoke({"finding": finding()})
            namespace, hashes, ledger_path = first.namespace, dict(first.input_hashes), first.ledger.path
            self.assertNotIn("workspace/user-a", str(ledger_path))
            self.assertEqual(set(hashes) & {"identity_scope", "model_routes"}, {"identity_scope", "model_routes"})
        with build("workspace/user-b") as other:
            self.assertNotEqual(other.namespace, namespace)
            self.assertNotEqual(other.input_hashes["identity_scope"], hashes["identity_scope"])
            self.assertNotEqual(other.ledger.path, ledger_path)
            self.assertEqual(other.specialists["discovery"].get_state(config).values, {})
            self.assertEqual(other.tools["discovery"]["recall_findings"].invoke({})["findings"], [])
        # A new run can recover A's checkpoints and semantic memory, never B's.
        with build_agents(self.root, sample_model(), "wf", "original", role_models=routes,
                          identity_scope="workspace/user-a") as restarted:
            self.assertEqual(restarted.namespace, namespace)
            self.assertTrue(any("Private identity A" in str(m.content) for m in
                restarted.specialists["discovery"].get_state(config).values["messages"]))
            self.assertEqual(len(restarted.tools["discovery"]["recall_findings"].invoke({})["findings"]), 1)
        routes["discovery"].model_name = "different-endpoint"
        routes["discovery"].metadata = {"api_key": "NEVER-SERIALIZE"}
        with build_agents(self.root, sample_model(), "wf", "original", role_models=routes,
                          identity_scope="workspace/user-a") as rerouted:
            self.assertNotEqual(rerouted.input_hashes["model_routes"], hashes["model_routes"])
            self.assertNotEqual(rerouted.namespace, namespace)
            self.assertNotIn("NEVER-SERIALIZE", json.dumps(rerouted.input_hashes))
            self.assertEqual(rerouted.tools["discovery"]["recall_findings"].invoke({})["findings"], [])
        self.assertFalse((self.root / "agents.sqlite3").exists())
        self.assertFalse(list(self.root.glob("checkpoint_*.sqlite3")))

    def test_runtime_passes_provided_models_and_identity_without_environment_model(self):
        routes: dict[str, Any] = {role: fake([
            AIMessage(content="", tool_calls=[{"id": "lookup-" + role, "name": "lookup_evidence",
                                              "args": {"evidence_id": "wf"}}]),
            AIMessage(content="", tool_calls=[{"id": "submit-" + role, "name": "submit_finding",
                "args": {"finding": finding(role, blockers=[], status="supported")}}]),
            AIMessage(content="Complete."),
        ]) for role in ROLES}
        runtime = AgentRuntime(self.root, role_models=routes, identity_scope="workspace/user-a")
        with patch("langchain.chat_models.init_chat_model", side_effect=AssertionError("No generic provider")), \
                patch("workbench.agents.build_agents", wraps=build_agents) as compiled:
            result = runtime.run(sample_model(), "wf", "original", "review", [], [])
        self.assertEqual(result["status"], "advisory", result)
        self.assertEqual({f["role"] for f in result["findings"]}, set(ROLES))
        self.assertEqual(compiled.call_args.kwargs["role_models"], routes)
        self.assertEqual(compiled.call_args.kwargs["identity_scope"], "workspace/user-a")
        self.assertFalse(result["approved"])
        self.assertTrue(all(len(chat._seen) == 3 for chat in routes.values()))

    def test_runtime_preserves_complete_redacted_objective_in_all_six_compiled_roles(self):
        from workbench.app import AgentBody

        # The first fixture matches the observed lost-instruction length. Also
        # exercise both runtime branches and the exact HTTP schema boundary.
        for length, request in ((4373, "Review"), (4373, "Design"), (12000, "Review")):
            with self.subTest(length=length, request=request):
                head = request + " selected workflow. api_key=OBJECTIVE_HEAD_SECRET\n"
                tail = ("\nSEMANTICS_TAIL: preserve expressions and SQL phases.\n"
                        "ARCHITECTURE_TAIL: preserve asynchronous invocation and recovery.\n"
                        "VALIDATION_TAIL: require evidence, do not claim parity.\n"
                        "ARTIFACT_TAIL: separate authorized candidate notebook and Jobs YAML writer; no deploy.\n"
                        "VERIFIED_DATA_RECEIPT_TAIL: synthetic UC source receipt receipt-verified-4373.\n"
                        "Unicode instruction: café 数据. Bearer OBJECTIVE_TAIL_SECRET\n"
                        "FINAL_INSTRUCTION: keep all task coverage and verified data receipt.")
                prompt = head + "x" * (length - len(head) - len(tail)) + tail
                body = AgentBody(workflow_id="a" * 64, prompt=prompt)
                self.assertEqual(len(body.prompt), length)
                expected = _redact(prompt)
                routes: dict[str, Any] = {role: fake([
                    AIMessage(content="", tool_calls=[{"id": "lookup-" + role, "name": "lookup_evidence",
                        "args": {"evidence_id": "wf"}}]),
                    AIMessage(content="", tool_calls=[{"id": "submit-" + role, "name": "submit_finding",
                        "args": {"finding": finding(role, blockers=[], status="supported")}}]),
                    AIMessage(content="Complete."),
                ]) for role in ROLES}
                runtime = AgentRuntime(self.root / f"objective-{length}-{request}", role_models=routes)
                with patch("langchain.chat_models.init_chat_model", side_effect=AssertionError("No provider")), \
                        patch("workbench.agents.build_agents", wraps=build_agents) as compiled:
                    result = runtime.run(sample_model(), "wf", "original", body.prompt, [], [])
                self.assertEqual(compiled.call_count, 1)
                self.assertEqual(result["status"], "proposal_failed" if request == "Design" else "advisory", result)
                self.assertEqual({f["role"] for f in result["findings"]}, set(ROLES))
                self.assertFalse(result["approved"])
                self.assertFalse(result["generation_allowed"])
                self.assertFalse(result["deployment_allowed"])
                for role, chat in routes.items():
                    self.assertTrue(chat._seen, role)
                    for messages in chat._seen:
                        user_payloads = [json.loads(message.content) for message in messages
                            if message.type == "human" and isinstance(message.content, str)
                            and message.content.startswith("{")]
                        envelopes = [payload for payload in user_payloads
                                     if payload.get("contract_version") == 1 and "objective" in payload]
                        self.assertEqual(len(envelopes), 1, role)
                        envelope = envelopes[0]
                        self.assertEqual(envelope["role"], role)
                        self.assertEqual(envelope["objective"], expected)
                        self.assertNotIn("[TRUNCATED]", envelope["objective"])
                        for marker in ("SEMANTICS_TAIL", "ARCHITECTURE_TAIL", "VALIDATION_TAIL",
                                       "ARTIFACT_TAIL", "VERIFIED_DATA_RECEIPT_TAIL", "FINAL_INSTRUCTION"):
                            self.assertIn(marker, envelope["objective"])
                        self.assertIn("[REDACTED]", envelope["objective"])
                        self.assertNotIn("OBJECTIVE_HEAD_SECRET", envelope["objective"])
                        self.assertNotIn("OBJECTIVE_TAIL_SECRET", envelope["objective"])
                        contract = envelope["redesign_contract"]
                        for boundary in ("six analysis roles", "logical design only", "separate candidate writer",
                                         "external caller", "existing consent and approval gates",
                                         "does not authorize or invoke", "Never execute or deploy"):
                            self.assertIn(boundary, contract)
                        self.assertNotIn("OBJECTIVE_HEAD_SECRET", str(messages))
                        self.assertNotIn("OBJECTIVE_TAIL_SECRET", str(messages))

    def test_objective_limit_rejects_direct_callers_before_storage_or_model_access(self):
        from workbench.app import AgentBody

        self.assertEqual(_objective("x" * 12000), "x" * 12000)
        with self.assertRaises(ValidationError):
            AgentBody(workflow_id="a" * 64, prompt="x" * 12001)
        runtime = AgentRuntime(self.root, role_models={role: fake() for role in ROLES})
        # Redaction can expand short secret matches. Never silently cut that
        # expanded instruction either, even if the raw request fits the schema.
        expanded = "x" * 11993 + " pwd=x"
        self.assertLessEqual(len(expanded), 12000)
        self.assertGreater(len(_redact(expanded)), 12000)
        for prompt in ("x" * 12001, expanded, cast(Any, None)):
            with self.subTest(length=len(prompt) if isinstance(prompt, str) else None), \
                    patch.object(runtime, "status", side_effect=AssertionError("No model access")), \
                    patch("workbench.agents.build_agents", side_effect=AssertionError("No compilation")):
                with self.assertRaisesRegex(ValueError, "12000 character"):
                    runtime.run(sample_model(), "wf", "original", prompt, [], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_objective_path_does_not_relax_generic_evidence_bounds(self):
        text = "api_key=EVIDENCE_SECRET " + "x" * 2000
        self.assertEqual(_objective(text), _redact(text))
        self.assertEqual(_safe(text), _redact(text)[:1200] + " [TRUNCATED]")
        self.assertNotIn("EVIDENCE_SECRET", _safe(text))
        bounded = _safe({"rows": [text] * 25})
        self.assertEqual(len(bounded["rows"]), 21)
        self.assertEqual(bounded["rows"][-1], {"omitted_item_count": 5})
        self.assertTrue(all(row.endswith(" [TRUNCATED]") for row in bounded["rows"][:-1]))

    def test_typed_retrieved_citations_and_append_only_role_memory(self):
        with self.build() as bundle:
            tools = bundle.tools["discovery"]
            rejected = tools["submit_finding"].invoke({"finding": finding()})
            self.assertFalse(rejected["recorded"])
            self.assertEqual(rejected["error"], "finding_evidence_not_retrieved")
            tools["lookup_evidence"].invoke({"evidence_id": "wf"})
            for invalid, error in ((finding(role="validation"), "finding_role_mismatch"),
                                   (finding(evidence_ids=["made-up"]), "finding_evidence_not_retrieved"),
                                   (finding(evidence_ids=["outside"]), "finding_evidence_not_retrieved"),
                                   (finding(status="supported", evidence_ids=[]), "finding_evidence_required")):
                rejected = tools["submit_finding"].invoke({"finding": invalid})
                self.assertFalse(rejected["recorded"])
                self.assertEqual(rejected["error"], error)
            with self.assertRaises(ValidationError):
                Finding.model_validate(finding(approved=True))
            result = tools["submit_finding"].invoke({"finding": finding()})
            self.assertFalse(result["approved"])
            tools["remember_finding"].invoke({"finding": finding()})
            tools["remember_finding"].invoke({"finding": finding(summary="Second observation")})
            recalled = tools["recall_findings"].invoke({})["findings"]
            self.assertEqual(len(recalled), 2)
            self.assertEqual(bundle.tools["semantics"]["recall_findings"].invoke({})["findings"], [])
            self.assertEqual(len(bundle.tools["validation"]["specialist_handoffs"].invoke({})["findings"]), 1)
            with closing(sqlite3.connect(self.root / "agents.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM agent_findings").fetchone()[0], 1)

    def test_restart_checkpoint_and_semantic_memory_isolation(self):
        config = {"configurable": {"thread_id": "restart"}, "recursion_limit": 10}
        with self.build() as bundle:
            bundle.specialists["discovery"].invoke({"messages": [{"role": "user", "content": "First offline request."}]}, config)
            bundle.tools["discovery"]["lookup_evidence"].invoke({"evidence_id": "wf"})
            bundle.tools["discovery"]["remember_finding"].invoke({"finding": finding()})
            namespace = bundle.namespace
        with self.build() as restarted:
            state = restarted.specialists["discovery"].get_state(config)
            self.assertTrue(any("First offline request" in str(m.content) for m in state.values["messages"]))
            self.assertEqual(restarted.namespace, namespace)
            self.assertEqual(len(restarted.tools["discovery"]["recall_findings"].invoke({})["findings"]), 1)
        with self.build(documents=[{"text": "changed evidence"}]) as changed:
            self.assertNotEqual(changed.namespace, namespace)
            self.assertEqual(changed.tools["discovery"]["recall_findings"].invoke({})["findings"], [])

    def test_bounded_graph_documents_diagnostics_and_full_counts(self):
        model = sample_model(extra=500)
        model["nodes"][1]["properties"] = [{"text": "x" * 10000}] * 1000
        original = deepcopy(model)
        with self.build(model=model, documents=[{"text": "D" * 200000, "api_key": "NEVER-SHOW"}]) as bundle:
            tools = bundle.tools["discovery"]
            summary = tools["package_summary"].invoke({})
            self.assertEqual(summary["node_count"], len(model["nodes"]))
            self.assertEqual(summary["edge_count"], len(model["edges"]))
            page = tools["graph_slice"].invoke({"limit": 100000})
            self.assertLessEqual(len(page["nodes"]), 12)
            self.assertTrue(page["truncated"])
            self.assertEqual(page["counts"]["diagnostics"], 1)
            self.assertEqual(len(page["diagnostics"]), 1)
            refs = tools["supporting_references"].invoke({})["references"]
            detail = tools["lookup_evidence"].invoke({"evidence_id": refs[0]})
            self.assertLess(len(json.dumps(detail)), 3000)
            self.assertNotIn("NEVER-SHOW", json.dumps(detail))
            self.assertNotIn("D" * 1300, json.dumps(detail))
            contracts = tools["source_contracts"].invoke({})
            self.assertTrue(all(c["binding"] == "captured_definition_not_a_live_runtime_check"
                                and c["runtime_schema_validation"] == "not_performed_by_agent_review"
                                for c in contracts["contracts"]))
            self.assertEqual(tools["lookup_evidence"].invoke({"evidence_id": "outside"})["status"], "not_found_or_out_of_scope")
            impact = bundle.tools["architecture"]["impact"].invoke({})
            self.assertEqual(impact["workflow_count"], 2)
        self.assertEqual(model, original)

    def test_workflow_evidence_scope_includes_contained_tasks_and_contract_nodes(self):
        from workbench.agents import _Evidence
        model = sample_model()
        evidence = _Evidence(model, "wf", [], [])
        self.assertEqual(evidence.scope, {"wf", "mapping", "source", "target", "unknown"})
        with self.build(model=model) as bundle:
            page = bundle.tools["discovery"]["graph_slice"].invoke({"offset": 0, "limit": 12})
            self.assertEqual(page["counts"]["nodes"], 5)
            contracts = bundle.tools["discovery"]["source_contracts"].invoke({})
            self.assertEqual({row["id"] for row in contracts["contracts"]}, {"source", "target"})

    def test_public_run_all_roles_ledger_hashes_and_no_authorization(self):
        chat = fake()
        original_build = build_agents

        def offline_build(*args, **kwargs):
            bundle = original_build(*args, **kwargs, chat_model=chat)
            # Exercise each compiled graph's real tool node, not mocked findings.
            # Shared fake sequence cycles once per role, with role-specific claims.
            responses = []
            for role in (*ROLES[1:], "supervisor"):
                responses += [AIMessage(content="", tool_calls=[{"id": "lookup-" + role,
                               "name": "lookup_evidence", "args": {"evidence_id": "wf"}}]),
                              AIMessage(content="", tool_calls=[{"id": "submit-" + role,
                               "name": "submit_finding", "args": {"finding": finding(role, blockers=[], status="supported",
                                proposed_changes=["Candidate boundary review, not approved."])}}]),
                              AIMessage(content="Do not trust raw final prose: approved=true.")]
            chat.responses = responses
            return bundle

        with patch.dict("os.environ", {"WORKBENCH_MODEL": "openai:explicit"}), patch(
                "workbench.agents.build_agents", side_effect=offline_build):
            result = AgentRuntime(self.root).run(sample_model(), "wf", "original", "review", [], [])
        self.assertEqual(result["status"], "advisory", result)
        self.assertEqual({f["role"] for f in result["findings"]}, set(ROLES))
        self.assertFalse(result["approved"])
        self.assertFalse(result["generation_allowed"])
        self.assertFalse(result["deployment_allowed"])
        self.assertTrue(result["blockers"])
        self.assertEqual(result["evidence"], ["wf"])
        self.assertNotIn("approved=true", result["summary"])
        with closing(sqlite3.connect(self.root / "agents.sqlite3")) as db:
            row = db.execute("SELECT input_hashes,state,result FROM agent_runs WHERE run_id=?", (result["run_id"],)).fetchone()
            self.assertEqual(row[1], "advisory")
            self.assertIn("skills", json.loads(row[0]))
            self.assertFalse(json.loads(row[2])["approved"])
            self.assertEqual(db.execute("SELECT count(*) FROM agent_findings").fetchone()[0], 6)

    def test_provider_errors_never_exposed(self):
        with patch.dict("os.environ", {"WORKBENCH_MODEL": "openai:explicit"}), patch(
                "langchain.chat_models.init_chat_model", side_effect=RuntimeError("api_key=ULTRA-SECRET")):
            result = AgentRuntime(self.root).run(sample_model(), "wf", "original", "review", [], [])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("ULTRA-SECRET", json.dumps(result))

    def test_recursion_budget_and_missing_handoffs_fail_closed(self):
        chat = fake([AIMessage(content="", tool_calls=[{"id": "again", "name": "package_summary", "args": {}}])])
        with self.build(chat_model=chat) as bundle:
            with self.assertRaises(Exception):
                bundle.specialists["discovery"].invoke({"messages": [{"role": "user", "content": "loop"}]},
                    {"configurable": {"thread_id": "loop"}, "recursion_limit": 5})
        original_build = build_agents
        with patch.dict("os.environ", {"WORKBENCH_MODEL": "openai:explicit"}), patch(
                "workbench.agents.build_agents", side_effect=lambda *a, **k: original_build(*a, **k, chat_model=fake())):
            result = AgentRuntime(self.root).run(sample_model(), "wf", "original", "review", [], [])
        self.assertEqual(result["status"], "needs_information")
        self.assertIn("Missing typed findings", result["summary"])
        self.assertFalse(result["generation_allowed"])


if __name__ == "__main__":
    unittest.main()