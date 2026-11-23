"""Offline UC discovery regressions, including real compiled Deep Agents tools."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from databricks.sdk.service.catalog import CatalogsAPI, SchemasAPI, TablesAPI
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from workbench.agent_uc_metadata import API_PAGE_LIMIT, UCMetadata, digest
from workbench.agents import AgentRuntime, ROLES, _result, build_agents
from workbench.tests.test_agents import OfflineModel, fake, sample_model


def canonical_model():
    model = sample_model()
    ids = {n["id"]: digest(n["id"]) for n in model["nodes"]}
    for node in model["nodes"]:
        node["id"] = ids[node["id"]]
        if node["kind"] == "source":
            node.update(name="TIBAN", attributes={"NAME": "TIBAN", "DATABASETYPE": "ODBC",
                        "DBDNAME": "SAP_HANA", "OWNERNAME": "SAPDEV"},
                        scope={"repository": "SAP", "folder": "AP"})
    for edge in model["edges"]:
        edge.update(id=digest(edge["id"]), source=ids[edge["source"]], target=ids[edge["target"]])
    model["diagnostics"][0]["object_id"] = ids["unknown"]
    # Same physical name, different XML identity and canonical ID.
    oracle = deepcopy(next(n for n in model["nodes"] if n["kind"] == "source"))
    oracle.update(id=digest("oracle-source"), scope={"repository": "Oracle", "folder": "AP"})
    oracle["attributes"].update(DATABASETYPE="Oracle", DBDNAME="ORADB", OWNERNAME="AP")
    model["nodes"].append(oracle)
    model["edges"].append({"id": digest("oracle-edge"), "source": ids["wf"],
                           "target": oracle["id"], "kind": "contains", "attributes": {}, "evidence": {}})
    foreign = deepcopy(oracle)
    foreign["id"] = digest("foreign-source")
    model["nodes"].append(foreign)
    # Evidence traversal can see a nested workflow; binding traversal must stop.
    model["edges"].extend([
        {"id": digest("foreign-workflow-edge"), "source": ids["wf"], "target": ids["outside"],
         "kind": "contains", "attributes": {}, "evidence": {}},
        {"id": digest("foreign-source-edge"), "source": ids["outside"], "target": foreign["id"],
         "kind": "contains", "attributes": {}, "evidence": {}}])
    return model, ids


def table(name="chosen.sap.TIBAN"):
    catalog, schema, short = name.split(".")
    return NS(full_name=name, catalog_name=catalog, schema_name=schema, name=short,
              properties={"password": "NEVER-PROPERTIES"}, comment="NEVER-COMMENT instructions",
              storage_location="https://user:NEVER-LOCATION@example.com", view_definition="NEVER-SQL",
              columns=[NS(name="IBAN", type_text="STRING", type_name="STRING", nullable=True,
                          type_precision=None, type_scale=None, position=0,
                          comment="NEVER-COLUMN-COMMENT")])


class MetadataClient:
    def __init__(self):
        self.calls = []
        self.error = None
        self.catalogs = NS(list=self.catalogs_list)
        self.schemas = NS(list=self.schemas_list)
        self.tables = NS(list=self.tables_list, get=self.tables_get)

    def catalogs_list(self, **kwargs):
        self.calls.append(("catalogs.list", kwargs))
        return iter([NS(name="chosen"), NS(name="other")])

    def schemas_list(self, **kwargs):
        self.calls.append(("schemas.list", kwargs))
        catalog = kwargs["catalog_name"]
        return iter([NS(name="sap", catalog_name=catalog, full_name=catalog + ".sap")])

    def tables_list(self, **kwargs):
        self.calls.append(("tables.list", kwargs))
        return iter([table(kwargs["catalog_name"] + "." + kwargs["schema_name"] + ".TIBAN")])

    def tables_get(self, **kwargs):
        self.calls.append(("tables.get", kwargs))
        if self.error:
            raise self.error
        return table(kwargs["full_name"])


class ToolChoosingModel(OfflineModel):
    """Respond to actual tool receipts, including the run-generated observation hash."""
    role: str = "discovery"
    canonical_id: str = ""
    _step: int = PrivateAttr(default=0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen.append(messages)
        step = self._step
        self._step += 1
        if step == 0:
            calls = [("source_contracts", {}), ("uc_list_catalogs", {"limit": 1}),
                     ("uc_list_schemas", {"catalog": "chosen"}),
                     ("uc_list_tables", {"catalog": "chosen", "schema_name": "sap"})]
        elif step == 1:
            calls = [("uc_get_table", {"full_name": "chosen.sap.TIBAN"})]
        elif step == 2:
            observation = next(json.loads(m.content) for m in reversed(messages)
                               if isinstance(m, ToolMessage) and m.name == "uc_get_table")
            calls = [("record_uc_binding", {"binding": {
                "canonical_id": self.canonical_id, "full_name": "chosen.sap.TIBAN",
                "observation_id": observation["observation_id"],
                "observation_sha256": observation["observation_sha256"],
                "reason": "SAP database owner and captured IBAN column evidence select this candidate.",
                "evidence_refs": [self.canonical_id]}})]
        elif step == 3:
            calls = [("uc_metadata_state", {})]
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Candidate only, no parity or approval."))])
        response = AIMessage(content="", tool_calls=[{"id": self.role + str(step) + str(i),
                                                     "name": name, "args": args}
                                                    for i, (name, args) in enumerate(calls)])
        return ChatResult(generations=[ChatGeneration(message=response)])


class UCMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model, self.ids = canonical_model()
        self.client = MetadataClient()
        self.factories = 0
        self.host = "https://selected.example.com"
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        network.start()
        self.addCleanup(network.stop)
        env = patch.dict("os.environ", {"WORKBENCH_MODEL": "", "LANGSMITH_TRACING": "false",
                                       "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)

    def provider(self):
        self.factories += 1
        return NS(client=self.client, host=self.host, user_id="NEVER-USER", token="NEVER-TOKEN")

    def build(self, **kwargs):
        return build_agents(self.root, self.model, self.ids["wf"], "original",
                            chat_model=fake(), **kwargs)

    def binding(self, observation, canonical_id=None, **kwargs):
        return {"canonical_id": canonical_id or self.ids["source"],
                "full_name": observation["query"]["full_name"],
                "observation_id": observation["observation_id"],
                "observation_sha256": observation["observation_sha256"],
                "reason": "Captured database, owner and column evidence.",
                "evidence_refs": [canonical_id or self.ids["source"]], **kwargs}

    def test_all_six_actual_compiled_roles_choose_tools_and_export_persisted_projection(self):
        routes = {role: ToolChoosingModel(role=role, canonical_id=self.ids["source"],
                  responses=[AIMessage(content="unused")], custom_get_token_ids=lambda text: []) for role in ROLES}
        audit = []
        with build_agents(self.root, self.model, self.ids["wf"], "original", role_models=routes,
                          uc_identity_provider=self.provider, audit_sink=audit.append) as bundle:
            self.assertEqual(self.factories, 0)
            self.assertTrue(all(not model._seen for model in routes.values()))
            for role in ROLES:
                graph = bundle.supervisor if role == "supervisor" else bundle.specialists[role]
                names = set(graph.nodes["tools"].bound.tools_by_name)
                self.assertTrue({"uc_get_table", "uc_list_catalogs", "uc_list_schemas", "uc_list_tables",
                                 "record_uc_binding", "uc_metadata_state"}.issubset(names))
                self.assertFalse(names & {"execute", "sql", "write_file", "search"})
                result = graph.invoke({"messages": [{"role": "user", "content": "Inspect XML and UC metadata candidates."}]},
                                      {"configurable": {"thread_id": role}, "recursion_limit": 48})
                saved = next(json.loads(m.content) for m in result["messages"]
                             if isinstance(m, ToolMessage) and m.name == "record_uc_binding")
                self.assertTrue(saved["recorded"])
                self.assertEqual(saved["binding"]["role"], role)
                self.assertFalse(saved["binding"]["approved"])
            projection = _result(bundle, "completed", "Offline metadata review")
            self.assertEqual(len(projection["uc_table_bindings"]), 6)
            self.assertEqual(len(projection["uc_metadata_observations"]), 24)
            self.assertFalse(projection["generation_allowed"])
            self.assertFalse(projection["deployment_allowed"])
            self.assertFalse(projection["approved"])
            bundle.ledger.finish(bundle.run_id, projection)
            with closing(sqlite3.connect(self.root / "agents.sqlite3")) as conn:
                rows = conn.execute("SELECT result FROM agent_runs WHERE run_id=?", (bundle.run_id,)).fetchone()
            self.assertEqual(json.loads(rows[0])["uc_table_bindings"], projection["uc_table_bindings"])
            encoded = json.dumps(projection)
            for secret in ("NEVER-PROPERTIES", "NEVER-COMMENT", "NEVER-LOCATION", "NEVER-SQL",
                           "NEVER-COLUMN-COMMENT", "NEVER-TOKEN", "NEVER-USER"):
                self.assertNotIn(secret, encoded)
            self.assertEqual(self.factories, 24)
            self.assertEqual({row[0] for row in self.client.calls},
                             {"catalogs.list", "schemas.list", "tables.list", "tables.get"})
            self.assertTrue(any(e.get("tool_name") == "record_uc_binding" and e.get("event") == "tool_completed" for e in audit))

    def test_same_named_source_and_target_candidates_keep_exact_canonical_ownership(self):
        with self.build(uc_identity_provider=self.provider) as bundle:
            tools = bundle.tools["discovery"]
            contracts = tools["source_contracts"].invoke({})
            names = [row for row in contracts["contracts"] if row["kind"] == "source"]
            self.assertEqual(len(names), 2)
            self.assertEqual({row["captured"]["name"] for row in names}, {"TIBAN"})
            observation = tools["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})
            for canonical_id in (self.ids["source"], digest("oracle-source"), self.ids["target"]):
                saved = tools["record_uc_binding"].invoke({"binding": self.binding(observation, canonical_id)})
                self.assertTrue(saved["recorded"])
                value = saved["binding"]
                self.assertEqual(value["binding_sha256"], digest({k: v for k, v in value.items() if k != "binding_sha256"}))
            self.assertEqual({b["canonical_id"] for b in bundle.uc_table_bindings},
                             {self.ids["source"], digest("oracle-source"), self.ids["target"]})
            self.assertEqual(bundle.uc_table_bindings[-1]["kind"], "target")
            # No names-based semantic decision: distinct XML IDs remain distinct candidates.
            self.assertTrue(all("not_semantic_proof" in b["confidence"] for b in bundle.uc_table_bindings))

    def test_role_evidence_ownership_cross_scope_and_list_only_rejected(self):
        with self.build(uc_identity_provider=self.provider) as bundle:
            tools = bundle.tools["discovery"]
            obs = tools["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})
            self.assertFalse(tools["record_uc_binding"].invoke({"binding": self.binding(obs)})["recorded"])
            tools["lookup_evidence"].invoke({"evidence_id": digest("foreign-source")})
            foreign = tools["record_uc_binding"].invoke({"binding": self.binding(obs, digest("foreign-source"))})
            self.assertEqual(foreign["error"], "canonical_id_out_of_role_workflow_scope")
            tools["source_contracts"].invoke({})
            other = bundle.tools["semantics"]["record_uc_binding"].invoke({"binding": self.binding(obs)})
            self.assertFalse(other["recorded"])
            bad_ref = tools["record_uc_binding"].invoke({"binding": self.binding(obs, evidence_refs=[self.ids["source"], "invented"])})
            self.assertEqual(bad_ref["error"], "evidence_not_owned_by_role")
            listed = tools["uc_list_tables"].invoke({"catalog": "chosen", "schema_name": "sap"})
            only_list = tools["record_uc_binding"].invoke({"binding": self.binding(obs,
                observation_id=listed["observation_id"], observation_sha256=listed["observation_sha256"])})
            self.assertEqual(only_list["error"], "inspect_exact_table_first")
            self.assertFalse(tools["record_uc_binding"].invoke({"binding": self.binding(obs, self.ids["wf"])})["recorded"])

    def test_related_invocation_binding_scope_expands_only_exact_retrieved_role(self):
        foreign = digest("foreign-source")
        def report(_index, workflow, section, **kwargs):
            if section == "invocations":
                return {"items": [{"caller_workflow_id": self.ids["wf"], "candidate_workflow_ids": [self.ids["outside"]]}],
                        "evidence_ids": [self.ids["outside"]]}
            return {"items": [], "evidence_ids": [foreign]}
        with self.build(uc_identity_provider=self.provider) as bundle, patch(
                "workbench.migration_planning.PlanningIndex.agent_contract", report):
            tools = bundle.tools["discovery"]
            denied = tools["workflow_planning_contract"].invoke({"related_workflow_id": self.ids["outside"]})
            self.assertEqual(denied["status"], "related_workflow_not_retrieved")
            tools["workflow_planning_contract"].invoke({"section": "invocations"})
            tools["workflow_planning_contract"].invoke({"section": "definitions", "related_workflow_id": self.ids["outside"]})
            obs = tools["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})
            self.assertTrue(tools["record_uc_binding"].invoke({"binding": self.binding(obs, foreign)})["recorded"])
            self.assertNotIn(foreign, bundle.tools["semantics"]["uc_metadata_state"].invoke({})["allowed_binding_ids"])

    def test_integrity_wrong_table_wrong_run_and_detached_projection(self):
        with self.build(uc_identity_provider=self.provider) as bundle:
            tools = bundle.tools["discovery"]
            tools["source_contracts"].invoke({})
            obs = tools["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})
            payload = {k: v for k, v in obs.items() if k not in {"observation_id", "observation_sha256"}}
            self.assertEqual(digest(payload), obs["observation_sha256"])
            for mutation in ({"observation_sha256": "0" * 64}, {"observation_id": "0" * 64},
                             {"full_name": "chosen.oracle.TIBAN"}):
                self.assertFalse(tools["record_uc_binding"].invoke({"binding": self.binding(obs, **mutation)})["recorded"])
            obs["items"][0]["columns"][0]["name"] = "tampered"
            bundle.uc_metadata_observations[0]["status"] = "tampered"
            self.assertEqual(bundle.uc_metadata_observations[0]["items"][0]["columns"][0]["name"], "IBAN")
            self.assertTrue(tools["record_uc_binding"].invoke({"binding": self.binding(obs)})["recorded"])
            bundle.uc_table_bindings[0]["full_name"] = "tampered"
            self.assertEqual(bundle.uc_table_bindings[0]["full_name"], "chosen.sap.TIBAN")
            other = UCMetadata(self.provider, "different-run")
            self.assertEqual(other.record("discovery", self.binding(obs), {self.ids["source"]: "source"},
                                         {self.ids["source"]})["error"], "observation_not_in_this_run")

    def test_permission_errors_unknown_qualified_table_not_found_only(self):
        class Failure(Exception):
            def __init__(self, code):
                self.error_code = code
                super().__init__("password=NEVER-ERROR-PROSE")
        state = UCMetadata(self.provider, "run")
        for code in ("PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND", "SCHEMA_DOES_NOT_EXIST", "CATALOG_DOES_NOT_EXIST"):
            self.client.error = Failure(code)
            obs = state.observe("discovery", "tables.get", full_name="chosen.sap.TIBAN")
            self.assertEqual(obs["status"], "unknown")
            self.assertIsNone(obs["exists"])
            self.assertNotIn("NEVER-ERROR-PROSE", json.dumps(obs))
        self.client.error = Failure("TABLE_DOES_NOT_EXIST")
        missing = state.observe("discovery", "tables.get", full_name="chosen.sap.TIBAN")
        self.assertFalse(missing["exists"])
        self.assertEqual(missing["absence_scope"], "chosen.sap.TIBAN")
        self.client.catalogs = NS(list=lambda **kwargs: (_ for _ in ()).throw(Failure("TABLE_DOES_NOT_EXIST")))
        self.assertIsNone(state.observe("discovery", "catalogs.list")["exists"])

    def test_unavailable_provider_no_sdk_fallback_and_fresh_selected_identity(self):
        with patch("databricks.sdk.WorkspaceClient", side_effect=AssertionError("No default SDK auth")):
            with self.build() as bundle:
                self.assertEqual(bundle.tools["artifact"]["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})["status"], "provider_unavailable")
            state = UCMetadata(self.provider, "run")
            state.observe("discovery", "tables.get", full_name="chosen.sap.TIBAN")
            state.observe("semantics", "tables.get", full_name="chosen.sap.TIBAN")
            self.assertEqual(self.factories, 2)
            self.host = "https://other.example.com"
            changed = state.observe("validation", "tables.get", full_name="chosen.sap.TIBAN")
            self.assertEqual(changed["status"], "unknown")
            self.assertEqual(len(self.client.calls), 2)
            unsafe = UCMetadata(lambda: NS(host="https://user:secret@example.com", client=self.client), "unsafe")
            self.assertEqual(unsafe.observe("discovery", "catalogs.list")["status"], "unknown")
            failing = UCMetadata(lambda: (_ for _ in ()).throw(RuntimeError("token=NEVER")), "failed")
            self.assertEqual(failing.observe("discovery", "catalogs.list")["status"], "unknown")

    def test_paginated_actual_sdk_metadata_empty_pages_counts_and_page_bound(self):
        class API:
            _cfg = NS(workspace_id=None)
            def __init__(self):
                self.calls = []
                self.endless = False
            def do(self, method, path, **kwargs):
                self.calls.append((method, path, kwargs))
                token = kwargs["query"].get("page_token")
                if self.endless:
                    return {"catalogs": [], "next_page_token": "again"}
                key = path.rsplit("/", 1)[-1]
                if not token:
                    return {key: [], "next_page_token": "second"}
                if key == "catalogs":
                    return {key: [{"name": "one"}, {"name": "two"}, {"name": "three"}]}
                if key == "schemas":
                    return {key: [{"name": "sap", "catalog_name": "chosen", "full_name": "chosen.sap"}]}
                return {key: [{"name": "TIBAN", "catalog_name": "chosen", "schema_name": "sap", "full_name": "chosen.sap.TIBAN"}]}
        api = API()
        client = NS(catalogs=CatalogsAPI(api), schemas=SchemasAPI(api), tables=TablesAPI(api))
        state = UCMetadata(lambda: NS(host=self.host, client=client), "sdk-run")
        first = state.observe("discovery", "catalogs.list", limit=2)
        self.assertEqual(first["returned_count"], 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        self.assertNotIn("total", first)
        last = state.observe("discovery", "catalogs.list", offset=2, limit=2)
        self.assertEqual(last["returned_count"], 1)
        self.assertFalse(last["has_more"])
        self.assertEqual(state.observe("discovery", "schemas.list", catalog="chosen")["items"][0]["full_name"], "chosen.sap")
        self.assertEqual(state.observe("discovery", "tables.list", catalog="chosen", schema="sap")["items"][0]["full_name"], "chosen.sap.TIBAN")
        before = len(api.calls)
        api.endless = True
        self.assertEqual(state.observe("discovery", "catalogs.list")["status"], "unknown")
        self.assertEqual(len(api.calls) - before, API_PAGE_LIMIT)
        self.assertTrue(all(call[0] == "GET" and call[2]["query"]["max_results"] == 50 for call in api.calls))

    def test_input_bounds_full_columns_and_prose_redaction(self):
        state = UCMetadata(self.provider, "run")
        for kwargs in ({"limit": 51}, {"offset": 1000}, {"limit": 0}, {"offset": -1}):
            self.assertEqual(state.observe("discovery", "catalogs.list", **kwargs)["status"], "invalid_request")
        for name in ("TIBAN", "sap.TIBAN", "chosen.sap.TIBAN.extra", "chosen.sap.table;SELECT"):
            self.assertEqual(state.observe("discovery", "tables.get", full_name=name)["status"], "invalid_request")
        self.assertEqual(self.factories, 0)
        with self.build(uc_identity_provider=self.provider) as bundle:
            tools = bundle.tools["discovery"]
            tools["source_contracts"].invoke({})
            obs = tools["uc_get_table"].invoke({"full_name": "chosen.sap.TIBAN"})
            self.assertEqual(obs["items"][0]["columns"], [{"name": "IBAN", "type_text": "STRING", "type_name": "STRING",
                "type_precision": None, "type_scale": None, "position": 0, "nullable": True}])
            saved = tools["record_uc_binding"].invoke({"binding": self.binding(obs,
                reason="<b>Evidence</b> password=NEVER-REASON Bearer NEVER-BEARER")})
            self.assertTrue(saved["recorded"])
            self.assertNotIn("NEVER-", json.dumps(saved))
            self.assertNotIn("<", saved["binding"]["reason"])
        invalid = table()
        invalid.columns *= 1001
        self.client.tables = NS(get=lambda **kwargs: invalid)
        self.assertEqual(state.observe("discovery", "tables.get", full_name="chosen.sap.TIBAN")["status"], "unknown")

    def test_no_table_timer_tasks_legal(self):
        model = deepcopy(self.model)
        model["nodes"] = [n for n in model["nodes"] if n["kind"] not in {"source", "target"}]
        ids = {n["id"] for n in model["nodes"]}
        model["edges"] = [e for e in model["edges"] if e["source"] in ids and e["target"] in ids]
        with build_agents(self.root, model, self.ids["wf"], "original", chat_model=fake(),
                          uc_identity_provider=self.provider) as bundle:
            graph = bundle.specialists["artifact"]
            graph.invoke({"messages": [{"role": "user", "content": "Review timer, no tables needed."}]},
                         {"configurable": {"thread_id": "timer"}, "recursion_limit": 10})
            self.assertEqual(bundle.tools["artifact"]["source_contracts"].invoke({})["count"], 0)
            self.assertEqual(_result(bundle, "completed", "Timer review")["uc_table_bindings"], [])
            self.assertEqual(self.factories, 0)

    def test_compiled_invalid_binding_feedback_does_not_echo_prose_or_credentials(self):
        model = fake([
            AIMessage(content="", tool_calls=[{"id": "invalid", "name": "record_uc_binding", "args": {
                "binding": {"canonical_id": "invalid", "reason": "password=NEVER-INVALID-PROSE"}}}]),
            AIMessage(content="No binding was saved.")])
        with build_agents(self.root, self.model, self.ids["wf"], "original", chat_model=model) as bundle:
            result = bundle.specialists["discovery"].invoke(
                {"messages": [{"role": "user", "content": "Test invalid typed submission."}]},
                {"configurable": {"thread_id": "invalid-binding"}, "recursion_limit": 12})
            message = next(m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "record_uc_binding")
            self.assertNotIn("NEVER-INVALID-PROSE", message.content)
            self.assertFalse(json.loads(message.content)["recorded"])
            self.assertEqual(bundle.uc_table_bindings, [])

    def test_runtime_factory_forwarding_without_invoking_it(self):
        runtime = AgentRuntime(self.root, role_models={role: fake() for role in ROLES},
                               uc_identity_provider=self.provider)
        with patch("workbench.agents.build_agents", side_effect=RuntimeError("offline stop")) as build:
            runtime.run(self.model, self.ids["wf"], "original", "review", [], [])
            self.assertIs(build.call_args.kwargs["uc_identity_provider"], runtime.uc_identity_provider)
            self.assertEqual(self.factories, 0)


if __name__ == "__main__":
    unittest.main()