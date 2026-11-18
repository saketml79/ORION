"""Actual full XML parsed ONCE; scoped copies drive constrained compiler tests.

Generated notebook bodies execute locally with strict Spark/SDK doubles. These
are NOT remote Spark, Oracle transaction or original child parity tests.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml

from workbench.demo_conversion import (
    ACK, CHILD_NAME, DemoBindings, ORIGINAL_SHA256, REVIEWED_SCOPE_SHA256,
    WORKFLOW_NAME, compile_demo, counter_oracle, export_demo, scoped_model,
)
from workbench.agents import AgentRuntime, ROLES, build_agents
from workbench.redesign import build_wil_net_dis_candidate, validate_submission
from workbench.tests.test_redesign import chat
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def canonical_scope():
    # Mandatory original integration, not a synthetic XML chosen to fit codegen.
    path = ROOT / "input" / "LumenExample.XML"
    assert path.is_file(), "The actual original XML is required for this regression"
    model = analyze_package(path)
    assert model["package"]["sha256"] == ORIGINAL_SHA256
    workflows = [n for n in model["nodes"] if n["kind"] == "workflow" and n["name"] == WORKFLOW_NAME]
    assert len(workflows) == 1
    child = next(n for n in model["nodes"] if n["kind"] == "workflow" and n["name"] == CHILD_NAME)
    assert sum(n["kind"] == "taskinstance" and n["parent_id"] == child["id"] for n in model["nodes"]) == 15
    workflow_id = workflows[0]["id"]
    return scoped_model(model, workflow_id), workflow_id


@pytest.fixture
def bindings():
    return DemoBindings(catalog="sandbox", schema_name="orion_demo_counter",
                        generation_approved=True, fixture_replacements_approved=True)


@pytest.fixture
def result(canonical_scope, bindings):
    model, workflow_id = canonical_scope
    return compile_demo(model, workflow_id, bindings)


def test_actual_scope_semantics_and_proposal(result):
    proposal = result["proposal"]
    assert result["manifest"]["scope_sha256"] == REVIEWED_SCOPE_SHA256
    assert result["manifest"]["scope_node_count"] == 82
    assert result["manifest"]["scope_edge_count"] == 124
    assert result["manifest"]["original_package_matches_reviewed"] is True
    assert len(proposal["coverage"]) == 5
    assert {c["original_enabled"] for c in proposal["coverage"]} == {"YES"}
    assert proposal["equivalence"] == "not_established"
    assert proposal["deployment_allowed"] is False
    assert proposal["author"] == "deterministic_constrained_compiler"
    assert proposal["semantics"]["mapping_default_template_id"] == 18537
    assert proposal["semantics"]["session_override_template_id"] == 18300
    assert proposal["semantics"]["original_wait_seconds"] == 300
    assert proposal["semantics"]["timer_is_poll_loop"] is False
    assert proposal["original_child"]["converted"] is False
    assert len(proposal["gaps"]) >= 8


def test_logical_outline_tool_submits_evidence_checked_four_task_candidate(canonical_scope, tmp_path):
    from workbench.agents import _Evidence

    model, workflow_id = canonical_scope
    candidate = build_wil_net_dis_candidate(model, workflow_id)
    evidence = _Evidence(model, workflow_id, [], [])
    validated = validate_submission(candidate, model, workflow_id,
                                    set(evidence.records), evidence.records)
    assert len(validated.tasks) == 4
    assert len(validated.edges) == 3
    assert len(validated.coverage) == 5
    assert validated.coverage[0].disposition == "removed"
    assert validated.cadence.launch_mode == "on_demand"
    assert {item.category for item in validated.scoped_unresolved} == {
        "connection", "control", "notification"}

    with build_agents(tmp_path / "agents", model, workflow_id, "original", chat_model=chat()) as bundle:
        tool = bundle.tools["architecture"].get("submit_wil_net_dis_outline")
        assert tool is not None
        receipt = tool.invoke({})
        assert receipt["recorded"] is True and receipt["status"] == "draft"
        assert len(bundle.redesigns) == 1
        assert bundle.redesigns[0]["design"] == validated.model_dump()


def test_agent_runtime_does_not_replace_missing_agent_design_with_reference_outline(canonical_scope, tmp_path):
    from langchain_core.messages import AIMessage
    from workbench.tests.test_redesign import tool_call

    model, workflow_id = canonical_scope
    routes = {}
    for role in ROLES:
        finding = {"role": role, "summary": "The role did not verify semantic parity.",
                   "evidence_ids": [], "assumptions": [], "blockers": [],
                   "proposed_changes": [], "alternatives": [], "status": "needs_information"}
        responses = [tool_call("submit_finding", {"finding": finding}, role + "-finding"),
                     AIMessage(content="Evidence recorded; no parity claim.")]
        if role in {"architecture", "supervisor"}:
            responses.append(AIMessage(content="No additional typed design submitted."))
        routes[role] = chat(responses)

    runtime = AgentRuntime(tmp_path / "runtime", role_models=routes)
    output = runtime.run(model, workflow_id, "original", "Produce a Lakeflow redesign", [], [])
    assert output["status"] == "proposal_failed", output
    assert output["design_origin"] is None
    assert output["design_submission"]["accepted_count"] == 0
    assert not output["redesigns"]
    assert not output["approved"] and not output["generation_allowed"] and not output["deployment_allowed"]


@pytest.mark.parametrize("change", ["sql", "disabled", "new_property", "new_node", "edge", "field", "missing_edge", "external_edge"])
def test_semantic_drift_fails_closed(canonical_scope, bindings, change):
    original, workflow_id = canonical_scope
    model = deepcopy(original)
    if change == "sql":
        node = next(n for n in model["nodes"] if n["kind"] == "sesstransformationinst" and
                    any(p["attributes"].get("NAME") == "Sql Query" for p in n["properties"]))
        next(p for p in node["properties"] if p["attributes"]["NAME"] == "Sql Query")["attributes"]["VALUE"] += " WHERE TEMPLATE_ID=18300"
    elif change == "disabled":
        next(n for n in model["nodes"] if n["kind"] == "taskinstance")["attributes"]["ISENABLED"] = "NO"
    elif change == "new_property":
        model["nodes"][0]["properties"].append({"kind": "attribute", "attributes": {"NAME": "Pre SQL", "VALUE": "DELETE FROM x"}})
    elif change == "new_node":
        model["nodes"].append({"id": "new", "parent_id": workflow_id, "kind": "mystery", "properties": []})
        model["edges"].append({"id": "new", "source": workflow_id, "target": "new", "kind": "contains"})
    elif change == "edge":
        next(e for e in model["edges"] if e["kind"] == "workflow_control")["attributes"]["CONDITION"] = "TRUE"
    elif change == "field":
        next(n for n in model["nodes"] if n["kind"] == "target")["fields"][0]["attributes"]["SCALE"] = "2"
    elif change == "missing_edge":
        model["edges"] = [e for e in model["edges"] if e["kind"] != "data_flow"]
    else:
        model["edges"].append({"id": "new", "source": workflow_id, "target": "unreviewed", "kind": "new_side_effect"})
    with pytest.raises(ValueError, match="Unsupported"):
        compile_demo(model, workflow_id, bindings)


@pytest.mark.parametrize("override", [
    {"generation_approved": False}, {"fixture_replacements_approved": False},
    {"generation_approved": "true"}, {"fixture_count": True}, {"fixture_count": -1},
    {"wait_seconds": 301}, {"catalog": "a.b"}, {"schema_name": "production"},
    {"catalog": "system"}, {"catalog": "bad`; DROP TABLE x"},
    {"email_delivery": "real"}, {"child_binding": "production"}, {"target_table": "prod.data"},
])
def test_explicit_contract_rejects_unsafe_or_unknown(bindings, override):
    with pytest.raises(ValueError):
        DemoBindings.model_validate({**bindings.model_dump(), **override})


def test_unknown_workflow_and_missing_package_receipt(canonical_scope, bindings):
    model, workflow_id = canonical_scope
    with pytest.raises(ValueError, match="workflow_id"):
        compile_demo(model, "not-a-workflow", bindings)
    other = deepcopy(model)
    next(n for n in other["nodes"] if n["id"] == workflow_id)["name"] = "other"
    with pytest.raises(ValueError, match="Unsupported workflow"):
        compile_demo(other, workflow_id, bindings)
    other = deepcopy(model)
    other["package"]["sha256"] = ""
    with pytest.raises(ValueError, match="receipt"):
        compile_demo(other, workflow_id, bindings)


def test_deterministic_artifacts_hashes_cells_and_native_bundle(result, canonical_scope, bindings):
    assert result == compile_demo(*canonical_scope, bindings)
    artifacts = result["artifacts"]
    assert len(artifacts) == 23  # Nine notebooks in two formats, plus five review/release files.
    cell_ids = []
    for path, text in artifacts.items():
        if path != "manifest.json":
            receipt = result["manifest"]["artifacts"][path]
            assert receipt == {"sha256": sha256(text.encode()).hexdigest(), "size_bytes": len(text.encode())}
        if path.endswith(".ipynb"):
            notebook = json.loads(text)
            assert notebook["nbformat"] == 4
            for cell in notebook["cells"]:
                cell_ids.append(cell["metadata"]["id"])
                if cell["cell_type"] == "code":
                    assert cell["metadata"]["language"] == "python"
                    ast.parse("".join(cell["source"]))
                    assert "".join(cell["source"]).rstrip() in artifacts[path.replace(".ipynb", ".py")]
        if path.endswith(".py"):
            ast.parse(text)
    assert len(cell_ids) == len(set(cell_ids))
    bundle = yaml.safe_load(artifacts["databricks.yml"])
    assert bundle["sync"]["exclude"] == ["**/*.ipynb"]
    jobs = bundle["resources"]["jobs"]
    tasks = {t["task_key"]: t for t in jobs["counter_demo"]["tasks"]}
    assert tasks["threshold"]["condition_task"]["op"] == "GREATER_THAN_OR_EQUAL"
    assert tasks["dispatch_child_test"]["depends_on"] == [{"task_key": "threshold", "outcome": "false"}]
    assert tasks["email_mock"]["depends_on"] == [{"task_key": "threshold", "outcome": "true"}]
    assert jobs["counter_demo"]["parameters"][1]["default"] == "300"
    for job in jobs.values():
        assert job["run_as"] == {"user_name": "${workspace.current_user.userName}"}
        assert job["performance_target"] == "STANDARD"
        for task in job["tasks"]:
            assert sum(k in task for k in ("notebook_task", "run_job_task", "condition_task")) == 1
            assert not ({"existing_cluster_id", "new_cluster", "job_cluster_key"} & task.keys())
            assert task["max_retries"] == 0
    suite = jobs["boundary_suite"]["tasks"]
    assert [t["run_job_task"]["job_parameters"]["fixture_count"] for t in suite] == ["34", "35"]
    assert all(t["run_job_task"]["job_parameters"]["wait_seconds"] == "0" for t in suite)
    assert "run_job_task" not in tasks["dispatch_child_test"]
    assert ".result(" not in artifacts["notebooks/dispatch_child_test.py"]


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def first(self):
        return self.rows[0]

    def collect(self):
        return self.rows


class SparkFixture:
    """Strictly recognize ONLY emitted SQL; unknown statements fail the test."""

    def __init__(self):
        self.tables = {}
        self.statements = []

    def table(self, name):
        return Frame(list(self.tables[name]))

    def sql(self, sql):
        self.statements.append(sql)
        table = re.search(r"`[A-Za-z_][A-Za-z0-9_]*`\.`orion_demo_[A-Za-z0-9_]+`\.`orion_demo_wil_net_dis_[0-9]+`", sql).group()
        if sql.startswith("CREATE TABLE "):
            if table in self.tables:
                raise RuntimeError("table already exists")
            self.tables[table] = []
        elif sql.startswith("INSERT INTO "):
            n = int(re.search(r"range\(([0-9]+)\)", sql).group(1)) if "FROM range(" in sql else 1
            self.tables[table].extend({"TEMPLATE_ID": Decimal(18300), "COUNTER": Decimal(1)} for _ in range(n))
        elif sql.startswith("SELECT count(*) AS n FROM ") and sql.endswith(" WHERE TEMPLATE_ID = 18300"):
            return Frame([{"n": sum(r["TEMPLATE_ID"] == 18300 for r in self.tables[table])}])
        elif sql.startswith("DELETE FROM ") and sql.endswith(" WHERE TEMPLATE_ID = 18300"):
            self.tables[table] = [r for r in self.tables[table] if r["TEMPLATE_ID"] != 18300]
        else:
            raise AssertionError(f"Unsupported generated SQL: {sql}")
        return Frame([])


class NotebookHarness:
    def __init__(self, artifacts, n, monkeypatch, wait=0, user=True):
        self.artifacts, self.spark, self.values = artifacts, SparkFixture(), {}
        self.parameters = {"test_ack": ACK, "run_id": "123456", "fixture_count": str(n),
                           "wait_seconds": str(wait), "child_job_id": "987"}
        self.submissions, self.sleeps, self.client_constructions = [], [], []
        monkeypatch.setattr("time.sleep", self.sleeps.append)
        import databricks.sdk

        def client():
            self.client_constructions.append(True)
            identity = SimpleNamespace(user_name="test@example.com" if user else None,
                                       emails=["test@example.com"] if user else [])
            return SimpleNamespace(current_user=SimpleNamespace(me=lambda: identity),
                                   jobs=SimpleNamespace(run_now=self.run_now))

        monkeypatch.setattr(databricks.sdk, "WorkspaceClient", client)

    def run_now(self, **kwargs):
        self.submissions.append(kwargs)
        # Deliberately no result() method: a blocking wait would fail this test.
        return SimpleNamespace(response=SimpleNamespace(run_id=123789))

    def run(self, task):
        def text(name, default):
            self.parameters.setdefault(name, default)

        def set_value(*, key, value):
            self.values[(task, key)] = value

        def get_value(*, taskKey, key):
            return self.values[(taskKey, key)]

        dbutils = SimpleNamespace(widgets=SimpleNamespace(text=text, get=self.parameters.__getitem__),
                                  jobs=SimpleNamespace(taskValues=SimpleNamespace(set=set_value, get=get_value)))
        code = json.loads(self.artifacts[f"notebooks/{task}.ipynb"])["cells"][1]["source"]
        exec(compile("".join(code), task, "exec"), {"dbutils": dbutils, "spark": self.spark})


@pytest.mark.parametrize("n,branch,remaining", [(0, "async_child_test", 1), (29, "async_child_test", 59),
                                                 (30, "async_child_test", 0), (34, "async_child_test", 0),
                                                 (35, "email_mock", 0)])
def test_generated_notebooks_execute_real_boundary_semantics(result, monkeypatch, n, branch, remaining):
    h = NotebookHarness(result["artifacts"], n, monkeypatch)
    for task in ("setup", "wait", "counter", "verify"):
        h.run(task)
    oracle = counter_oracle([(18300, 1)] * n)
    assert h.values[("counter", "success_rows")] == oracle["success_rows"]
    assert h.values[("counter", "partition_reset")] == oracle["partition_reset"]
    assert len(next(iter(h.spark.tables.values()))) == len(oracle["rows"]) == remaining
    assert h.sleeps == [0]
    if branch == "email_mock":
        h.run("email_mock")
        h.run("verify_email")
        assert h.submissions == [] and h.client_constructions == []
    else:
        h.run("dispatch_child_test")
        h.run("verify_child")
        assert len(h.submissions) == 1
        assert h.submissions[0] == {"job_id": 987, "job_parameters": {"test_ack": ACK, "parent_run_id": "123456"},
                                    "idempotency_token": "orion-demo-wil-net-dis-123456"}


def test_unfiltered_select_and_partition_scoped_delete(result, monkeypatch):
    h = NotebookHarness(result["artifacts"], 35, monkeypatch)
    h.run("setup")
    table = next(iter(h.spark.tables))
    original = [(18300, None)] * 30 + [(999, 7)] * 5
    h.spark.tables[table] = [{"TEMPLATE_ID": Decimal(t), "COUNTER": None if c is None else Decimal(c)} for t, c in original]
    h.run("wait")
    h.run("counter")
    expected = counter_oracle(original)
    assert expected["success_rows"] == h.values[("counter", "success_rows")] == 35
    assert [(int(r["TEMPLATE_ID"]), int(r["COUNTER"])) for r in h.spark.tables[table]] == expected["rows"] == [(999, 7)] * 5


def test_runtime_ack_collision_wait_and_identity_guards(result, monkeypatch):
    h = NotebookHarness(result["artifacts"], 34, monkeypatch, wait=300, user=False)
    h.parameters["test_ack"] = "false"
    with pytest.raises(RuntimeError, match="acknowledgement"):
        h.run("setup")
    assert h.spark.tables == {}
    h.parameters["test_ack"] = ACK
    h.run("setup")
    with pytest.raises(RuntimeError, match="already exists"):
        h.run("setup")
    h.run("wait")
    assert h.sleeps == [300]
    h.run("counter")
    with pytest.raises(RuntimeError, match="user identity"):
        h.run("dispatch_child_test")
    assert h.submissions == []
    h.parameters["run_id"] = "123`; DROP TABLE production"
    with pytest.raises(ValueError, match="run_id"):
        h.run("setup")


def test_export_is_new_only_portable_tests_and_tamper_guard(result, tmp_path):
    directory = tmp_path / "release"
    names = export_demo(result, directory)
    assert names == sorted(result["artifacts"])
    with pytest.raises(FileExistsError):
        export_demo(result, directory)
    scope = {"__file__": str(directory / "tests" / "test_counter_contract.py"), "__name__": "export_contract_tests"}
    exec(result["artifacts"]["tests/test_counter_contract.py"], scope)
    tests = scope["ContractTests"]()
    tests.test_artifact_hashes()
    tests.test_notebooks_compile()
    tests.test_numeric_contract()
    tampered = deepcopy(result)
    tampered["artifacts"]["notebooks/setup.py"] += "# tampered"
    with pytest.raises(ValueError, match="hash"):
        export_demo(tampered, tmp_path / "tampered")
    assert not (tmp_path / "tampered").exists()


def test_no_agent_server_sleep_or_original_target_overwrite(result):
    for name, text in result["artifacts"].items():
        if name.startswith("notebooks/") and name.endswith(".py"):
            assert not re.search(r"DROP TABLE|TRUNCATE|CREATE OR REPLACE|\.mode\(['\"]overwrite", text, re.I)
            if "time.sleep(" in text:
                assert name == "notebooks/wait.py"
    assert "pmcmd" not in result["artifacts"]["notebooks/dispatch_child_test.py"]
    assert "original child transformations" in result["artifacts"]["notebooks/child_test_replacement.py"]


def test_generated_child_is_only_a_labeled_replacement(result, monkeypatch):
    h = NotebookHarness(result["artifacts"], 34, monkeypatch)
    h.parameters["parent_run_id"] = "123456"
    h.run("child_test_replacement")
    assert h.spark.tables == {} and h.submissions == []
    h.parameters["test_ack"] = "false"
    with pytest.raises(AssertionError):
        h.run("child_test_replacement")


def test_non_target_row_oracle_and_bad_rows():
    assert counter_oracle([(999, None)] * 35)["rows"] == [(999, None)] * 35 + [(18300, 1)] * 36
    with pytest.raises(ValueError):
        counter_oracle([(18300, True)])