"""Deterministic, reviewed WIL_NET_DIS counter/control TEST conversion.

Pure compile_demo(model, workflow_id, bindings) is the parent API boundary.
It neither calls agents nor edits proposal state. Export is an explicit action.
Only the pinned canonical scope is supported, not arbitrary SQL or workflows.
No deployment, email delivery, credentials, or original child implementation.
"""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
import yaml


WORKFLOW_NAME = "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS"
CHILD_NAME = "wkf_Load_AE2E_WILTEL_NETWORK_DISCO"
ORIGINAL_SHA256 = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
REVIEWED_SCOPE_SHA256 = "6ad24732742252cf16c5b099f4a5f030882eae6c81665296d075f20334f9910e"
_FOLLOW = {"contains", "session_mapping", "instance_of", "config_reference"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
ACK = "ISOLATED_TEST_ONLY"


class DemoBindings(BaseModel):
    """Explicit generation approval, never deployment or production approval.

    The schema must already exist and be dedicated to this demo. All table names
    are compiler-owned and run-scoped. No original source/target binding exists.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    catalog: str
    schema_name: str
    mode: Literal["isolated_semantic_test"] = "isolated_semantic_test"
    generation_approved: StrictBool
    fixture_replacements_approved: StrictBool
    fixture_count: StrictInt = Field(default=35, ge=0, le=1000)
    wait_seconds: StrictInt = Field(default=300, ge=0, le=300)
    email_delivery: Literal["disabled_mock"] = "disabled_mock"
    child_binding: Literal["test_replacement"] = "test_replacement"

    @model_validator(mode="after")
    def validate_contract(self):
        if not self.generation_approved or not self.fixture_replacements_approved:
            raise ValueError("Explicit generation and test replacement approval required")
        if not _IDENTIFIER.fullmatch(self.catalog) or not _IDENTIFIER.fullmatch(self.schema_name):
            raise ValueError("Only simple Unity Catalog identifiers are supported")
        if self.catalog.lower() in {"system", "hive_metastore", "samples"}:
            raise ValueError("Use an approved writable Unity Catalog test catalog")
        if not self.schema_name.startswith("orion_demo_"):
            raise ValueError("An existing dedicated orion_demo_ schema is required")
        return self


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _without_annotations(value):
    if isinstance(value, dict):
        return {k: _without_annotations(v) for k, v in value.items() if k not in {"summary", "evidence"}}
    if isinstance(value, list):
        return [_without_annotations(v) for v in value]
    return value


def scoped_model(model: dict, workflow_id: str) -> dict:
    """Bound canonical definition closure, retaining all internal edge kinds.

    This is also useful to cache a small read-only unit-test snapshot. The caller
    owns authenticity of the parsed model and original package receipt.
    """
    nodes = model["nodes"]
    by_id = {n["id"]: n for n in nodes}
    if len(by_id) != len(nodes) or len({e["id"] for e in model["edges"]}) != len(model["edges"]):
        raise ValueError("Duplicate canonical identities")
    if by_id.get(workflow_id, {}).get("kind") != "workflow":
        raise ValueError("workflow_id must identify a canonical workflow")
    outgoing = defaultdict(list)
    for edge in model["edges"]:
        if edge["kind"] in _FOLLOW:
            outgoing[edge["source"]].append(edge["target"])
    selected, queue = {workflow_id}, deque([workflow_id])
    while queue:
        for target in outgoing[queue.popleft()]:
            if target not in by_id:
                raise ValueError("Dangling canonical reference")
            if target not in selected:
                selected.add(target)
                queue.append(target)
    if any(e["source"] in selected and e["target"] not in selected for e in model["edges"]):
        raise ValueError("Unsupported reference outside reviewed scope")
    return {
        "package": dict(model["package"]),
        "nodes": sorted((n for n in nodes if n["id"] in selected), key=lambda n: n["id"]),
        "edges": sorted((e for e in model["edges"] if e["source"] in selected
                         and e["target"] in selected), key=lambda e: e["id"]),
    }


def counter_oracle(rows: list[tuple[int, int | None]]) -> dict:
    """Independent, bounded multiset oracle for the reviewed insert/post-SQL.

    Success rows exclude post-SQL. The SELECT has NO template predicate. Writer
    update/delete capability flags do not override Treat source rows as Insert.
    Oracle partition removal is interpreted as TEMPLATE_ID=18300, demo only.
    """
    if len(rows) > 1000 or any(not isinstance(r, tuple) or len(r) != 2
                               or type(r[0]) is not int
                               or (r[1] is not None and type(r[1]) is not int) for r in rows):
        raise ValueError("Use at most 1000 typed fixture rows")
    success = len(rows)
    result = list(rows) + [(18300, 1)] * success + [(18300, 1)]
    reset = sum(template == 18300 for template, _ in result) >= 60
    if reset:
        result = [r for r in result if r[0] != 18300]
    return {"success_rows": success, "rows": result, "partition_reset": reset,
            "branch": "email_mock" if success >= 35 else "async_child_test"}


def _notebook(name: str, code: str) -> tuple[str, str]:
    title = f"# {name}\nISOLATED SEMANTIC TEST. Not a complete original migration."
    cells = []
    for index, (kind, source) in enumerate((("markdown", title), ("code", code))):
        cell = {"cell_type": kind, "metadata": {
            "id": _digest(["wil-net-dis-test-v1", name, index])[:24],
            "language": "markdown" if kind == "markdown" else "python"},
            "source": source.splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    nb = {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
          "metadata": {"language_info": {"name": "python"},
                       "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}}
    exported = "# Databricks notebook source\n# MAGIC %md\n"
    exported += "\n".join("# MAGIC " + line for line in title.splitlines())
    exported += "\n\n# COMMAND ----------\n\n" + code.rstrip() + "\n"
    return json.dumps(nb, indent=2) + "\n", exported


def _runtime(bindings: DemoBindings) -> str:
    # Literal bindings cannot become SQL fragments, file paths or widget defaults.
    return f'''import json
import re

def parameter(name, default):
    dbutils.widgets.text(name, default)
    return dbutils.widgets.get(name)

if parameter("test_ack", "REQUIRES_EXPLICIT_TEST_ACK") != {ACK!r}:
    raise RuntimeError("Explicit ISOLATED_TEST_ONLY execution acknowledgement required")
run_id = parameter("run_id", "")
if not re.fullmatch(r"[0-9]{{1,30}}", run_id):
    raise ValueError("A Lakeflow job run_id is required")
fixture_text = parameter("fixture_count", {str(bindings.fixture_count)!r})
wait_text = parameter("wait_seconds", {str(bindings.wait_seconds)!r})
if not re.fullmatch(r"[0-9]{{1,4}}", fixture_text) or not re.fullmatch(r"[0-9]{{1,3}}", wait_text):
    raise ValueError("Invalid fixture or wait parameter")
fixture_count, wait_seconds = int(fixture_text), int(wait_text)
if fixture_count > 1000 or wait_seconds > 300:
    raise ValueError("Fixture bounds exceeded")
table = "`{bindings.catalog}`.`{bindings.schema_name}`.`orion_demo_wil_net_dis_" + run_id + "`"
'''


def _programs(bindings: DemoBindings) -> dict[str, str]:
    base = _runtime(bindings)
    return {
        "setup": base + '''# CREATE without IF NOT EXISTS fails on collisions. No overwrite or cleanup.
spark.sql(f"CREATE TABLE {table} (TEMPLATE_ID DECIMAL(38,0) NOT NULL, COUNTER DECIMAL(5,0)) USING DELTA TBLPROPERTIES ('orion.demo' = 'isolated_semantic_test')")
spark.sql(f"INSERT INTO {table} SELECT CAST(18300 AS DECIMAL(38,0)), CAST(1 AS DECIMAL(5,0)) FROM range({fixture_count})")
assert spark.table(table).count() == fixture_count
dbutils.jobs.taskValues.set(key="table", value=table)
''',
        "wait": base + '''# Runs on JOB COMPUTE, never in the app HTTP server.
# XML contains one timer edge, NOT a polling loop. Default remains 300 seconds.
import time
time.sleep(wait_seconds)
dbutils.jobs.taskValues.set(key="wait_seconds", value=wait_seconds)
''',
        "counter": base + '''assert dbutils.jobs.taskValues.get(taskKey="setup", key="table") == table
assert dbutils.jobs.taskValues.get(taskKey="wait", key="wait_seconds") == wait_seconds
# Materialize the source cardinality before self-target writes. No source WHERE.
# Identity connectors only; no expression or update-strategy transformation.
success_rows = spark.table(table).count()
if success_rows != fixture_count:
    raise RuntimeError("Fixture changed before session execution")
spark.sql(f"INSERT INTO {table} SELECT CAST(18300 AS DECIMAL(38,0)), CAST(1 AS DECIMAL(5,0)) FROM range({success_rows})")
# Session Post SQL, after successful target writes. Not included in TgtSuccessRows.
spark.sql(f"INSERT INTO {table} VALUES (18300, 1)")
partition_rows = spark.sql(f"SELECT count(*) AS n FROM {table} WHERE TEMPLATE_ID = 18300").first()["n"]
if partition_rows >= 60:
    spark.sql(f"DELETE FROM {table} WHERE TEMPLATE_ID = 18300")
# Each Delta statement commits separately. Oracle transaction equivalence unproven.
dbutils.jobs.taskValues.set(key="success_rows", value=success_rows)
dbutils.jobs.taskValues.set(key="partition_reset", value=partition_rows >= 60)
''',
        "verify": base + '''success_rows = dbutils.jobs.taskValues.get(taskKey="counter", key="success_rows")
reset = dbutils.jobs.taskValues.get(taskKey="counter", key="partition_reset")
expected_reset = (2 * fixture_count + 1) >= 60
expected_rows = 0 if expected_reset else 2 * fixture_count + 1
assert success_rows == fixture_count, "TgtSuccessRows excludes Post SQL"
assert reset == expected_reset
rows = spark.table(table).collect()
assert len(rows) == expected_rows
assert all(int(row["TEMPLATE_ID"]) == 18300 and int(row["COUNTER"]) == 1 for row in rows)
dbutils.jobs.taskValues.set(key="verified", value=True)
''',
        "email_mock": base + '''assert dbutils.jobs.taskValues.get(taskKey="counter", key="success_rows") >= 35
receipt = {"kind": "email_mock", "delivery_enabled": False,
           "recipient": "$$SUPPORT_LIST", "subject": "[$$INFA_ENV]  WIL_NET_DISCO failed since dependency not satisfied",
           "text": "Wiltel Network Disco has failed since job dependency not satisfied."}
print(json.dumps(receipt))
dbutils.jobs.taskValues.set(key="delivery_enabled", value=False)
dbutils.jobs.taskValues.set(key="branch", value="email_mock")
''',
        "dispatch_child_test": base + '''assert dbutils.jobs.taskValues.get(taskKey="counter", key="success_rows") < 35
from databricks.sdk import WorkspaceClient
client = WorkspaceClient()
user = client.current_user.me()
if not user.user_name or not user.emails:
    raise RuntimeError("A current user identity is required; no service principal fallback")
child_id = parameter("child_job_id", "")
if not re.fullmatch(r"[0-9]{1,30}", child_id):
    raise ValueError("Explicit generated child TEST job id required")
# run_now returns an SDK waiter with its submission response. Never call result().
submission = client.jobs.run_now(job_id=int(child_id),
    job_parameters={"test_ack": "ISOLATED_TEST_ONLY", "parent_run_id": run_id},
    idempotency_token="orion-demo-wil-net-dis-" + run_id)
child_run_id = submission.response.run_id
if not child_run_id:
    raise RuntimeError("Child submission returned no run id")
dbutils.jobs.taskValues.set(key="child_run_id", value=child_run_id)
dbutils.jobs.taskValues.set(key="branch", value="async_child_test")
print(json.dumps({"child_run_id": child_run_id, "completion_waited": False,
                  "original_child_converted": False, "submitted_by": user.user_name}))
''',
        "verify_email": base + '''assert dbutils.jobs.taskValues.get(taskKey="email_mock", key="branch") == "email_mock"
assert dbutils.jobs.taskValues.get(taskKey="email_mock", key="delivery_enabled") is False
assert dbutils.jobs.taskValues.get(taskKey="verify", key="verified") is True
print("PASS: original numeric >=35 branch, email delivery explicitly disabled")
''',
        "verify_child": base + '''assert dbutils.jobs.taskValues.get(taskKey="dispatch_child_test", key="branch") == "async_child_test"
assert dbutils.jobs.taskValues.get(taskKey="dispatch_child_test", key="child_run_id") > 0
assert dbutils.jobs.taskValues.get(taskKey="verify", key="verified") is True
print("PASS: original numeric <35 branch, child TEST submission accepted; completion unverified")
''',
        "child_test_replacement": '''# Explicit replacement, NOT wkf_Load_AE2E_WILTEL_NETWORK_DISCO business logic.
import re
dbutils.widgets.text("test_ack", "REQUIRES_EXPLICIT_TEST_ACK")
dbutils.widgets.text("parent_run_id", "")
assert dbutils.widgets.get("test_ack") == "ISOLATED_TEST_ONLY"
assert re.fullmatch(r"[0-9]{1,30}", dbutils.widgets.get("parent_run_id"))
print("CHILD TEST REPLACEMENT ONLY. No original child transformations or destinations executed.")
''',
    }


def _bundle(bindings: DemoBindings) -> dict:
    def notebook(key, dependencies=(), **parameters):
        task = {"task_key": key, "max_retries": 0,
                "notebook_task": {"notebook_path": f"./notebooks/{key}.py", "source": "WORKSPACE",
                                  "base_parameters": {"run_id": "{{job.run_id}}", **parameters}}}
        if dependencies:
            task["depends_on"] = [{"task_key": dep} if isinstance(dep, str) else dep for dep in dependencies]
        return task

    condition = {"task_key": "threshold", "max_retries": 0,
                 "depends_on": [{"task_key": "verify"}],
                 "condition_task": {"op": "GREATER_THAN_OR_EQUAL",
                                    "left": "{{tasks.counter.values.success_rows}}", "right": "35"}}
    tasks = [notebook("setup"), notebook("wait", ("setup",)), notebook("counter", ("wait",)),
             notebook("verify", ("counter",)), condition,
             notebook("email_mock", ({"task_key": "threshold", "outcome": "true"},)),
             notebook("dispatch_child_test", ({"task_key": "threshold", "outcome": "false"},),
                      child_job_id="${resources.jobs.child_test.id}"),
             notebook("verify_email", ("email_mock",)), notebook("verify_child", ("dispatch_child_test",))]
    common = {"max_concurrent_runs": 1, "performance_target": "STANDARD",
              "run_as": {"user_name": "${workspace.current_user.userName}"},
              "tags": {"aidevkit_project": "ai-dev-kit", "orion_scope": "isolated_semantic_test"}}
    return {"bundle": {"name": "orion-wil-net-dis-semantic-test"},
            "sync": {"exclude": ["**/*.ipynb"]},
            "resources": {"jobs": {
                "counter_demo": {**common, "name": "[TEST ONLY] WIL_NET_DIS counter and control",
                                 "parameters": [{"name": "fixture_count", "default": str(bindings.fixture_count)},
                                                {"name": "wait_seconds", "default": str(bindings.wait_seconds)},
                                                {"name": "test_ack", "default": "REQUIRES_EXPLICIT_TEST_ACK"}],
                                 "tasks": tasks},
                "child_test": {**common, "name": "[TEST REPLACEMENT] NOT original network disco child",
                               "parameters": [{"name": "test_ack", "default": "REQUIRES_EXPLICIT_TEST_ACK"},
                                              {"name": "parent_run_id", "default": ""}],
                               "tasks": [notebook("child_test_replacement")]},
                "boundary_suite": {**common, "name": "[TEST ONLY] WIL_NET_DIS boundaries 34 and 35",
                                   "tasks": [
                                       {"task_key": "case_34", "max_retries": 0,
                                        "run_job_task": {"job_id": "${resources.jobs.counter_demo.id}",
                                                         "job_parameters": {"fixture_count": "34", "wait_seconds": "0", "test_ack": ACK}}},
                                       {"task_key": "case_35", "max_retries": 0,
                                        "depends_on": [{"task_key": "case_34"}],
                                        "run_job_task": {"job_id": "${resources.jobs.counter_demo.id}",
                                                         "job_parameters": {"fixture_count": "35", "wait_seconds": "0", "test_ack": ACK}}},
                                   ]},
            }}, "targets": {"dev": {"default": True, "mode": "development"}}}


def compile_demo(model: dict, workflow_id: str, bindings: DemoBindings | dict) -> dict:
    """Return proposal + artifact text map, no IO, agents or deployment.

    Fail closed on ANY scoped semantic, structure, field or property change.
    The original package hash is provenance, not proof of deployment parity.
    """
    bindings = DemoBindings.model_validate(bindings)
    scope = scoped_model(model, workflow_id)
    workflow = next(n for n in scope["nodes"] if n["id"] == workflow_id)
    if workflow["name"] != WORKFLOW_NAME:
        raise ValueError("Unsupported workflow; only the reviewed WIL_NET_DIS scope is supported")
    scope_hash = _digest(_without_annotations({k: scope[k] for k in ("nodes", "edges")}))
    if scope_hash != REVIEWED_SCOPE_SHA256:
        raise ValueError("Unsupported or changed canonical semantics; scoped review required")
    package_hash = scope["package"].get("sha256", "")
    if not isinstance(package_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", package_hash):
        raise ValueError("Original package SHA-256 receipt required")
    gaps = [
        "Original child has 15 task instances and is NOT converted; generated child is a labeled test replacement.",
        "Email recipient/environment parameters are unresolved; delivery is disabled, content retained as a mock receipt.",
        "Oracle source/target connections remain unresolved; dedicated run-owned Delta fixture replaces BOTH endpoints.",
        "Oracle physical partition membership is assumed TEMPLATE_ID=18300 for the test; physical DDL is unavailable.",
        "Delta per-statement commits do not prove Oracle transaction, rollback, partial-write or restart equivalence.",
        "Original session commit interval 10000 and rollback-on-error NO are not emulated by this bounded fixture.",
        "Fixture is a controlled snapshot, not evidence of live source freshness or concurrent-writer equivalence.",
        "Remote Spark execution, child completion, user SDK authentication and workspace capability remain unverified.",
    ]
    coverage = []
    dispositions = {"Start": ("setup", "test setup precedes original timer"),
                    "tmr_WAIT_FOR_DEPENDENCY": ("wait", "single 300-second timer; explicit test override supported"),
                    "s_m_AE2E_JOB_CHECK_COUNTER": ("counter", "constrained insert and post-SQL on isolated fixture only"),
                    "eml_on_SUCCESS": ("email_mock", "mock only; delivery disabled despite legacy task name"),
                    "cmd_AE2E_CALL_WILTEL_NET_DIS_PULL": ("dispatch_child_test", "async SDK submission to labeled child test replacement")}
    for node in scope["nodes"]:
        if node["kind"] == "taskinstance" and node["parent_id"] == workflow_id:
            target, disposition = dispositions[node["name"]]
            coverage.append({"original_id": node["id"], "original_name": node["name"],
                             "original_enabled": node["attributes"]["ISENABLED"],
                             "generated_task": target, "disposition": disposition, "evidence": node["evidence"]})
    proposal = {
        "workflow_id": workflow_id, "name": WORKFLOW_NAME, "author": "deterministic_constrained_compiler",
        "status": "isolated_semantic_test_generated", "equivalence": "not_established",
        "generation_allowed": True, "deployment_allowed": False, "production_ready": False,
        "contract": bindings.model_dump(), "original_child": {"name": CHILD_NAME, "converted": False},
        "binding_status": "test_namespace_unverified_no_original_source_target_binding",
        "rationale": "Keep the real counter mutation and numeric branching; isolate unresolved external effects. No AI authorship claimed.",
        "semantics": {"template_id": 18300, "threshold": 35, "reset_threshold": 60,
                      "original_wait_seconds": 300, "effective_wait_seconds": bindings.wait_seconds,
                      "source_predicate": None, "mapping_default_template_id": 18537,
                      "session_override_template_id": 18300, "row_mode": "Insert",
                      "success_rows_excludes_post_sql": True, "timer_is_poll_loop": False,
                      "child_launch": "nowait", "email_delivery_enabled": False},
        "coverage": coverage, "gaps": gaps,
        "graph": {"nodes": [{"id": t["task_key"], "kind": next(k for k in ("notebook_task", "condition_task", "run_job_task") if k in t)}
                            for t in _bundle(bindings)["resources"]["jobs"]["counter_demo"]["tasks"]],
                  "edges": [{"source": d["task_key"], "target": t["task_key"], "outcome": d.get("outcome")}
                            for t in _bundle(bindings)["resources"]["jobs"]["counter_demo"]["tasks"] for d in t.get("depends_on", [])]},
        "validation": {"local_tests": "not_run_by_compiler", "bundle_schema": "not_run_by_compiler",
                       "remote_execution": "not_run", "deployment": "not_authorized"},
    }
    artifacts = {}
    for name, code in _programs(bindings).items():
        notebook, source = _notebook(name, code)
        artifacts[f"notebooks/{name}.ipynb"] = notebook
        artifacts[f"notebooks/{name}.py"] = source
    artifacts["databricks.yml"] = yaml.safe_dump(_bundle(bindings), sort_keys=False)
    artifacts["proposal.json"] = json.dumps(proposal, indent=2) + "\n"
    artifacts["tests/test_counter_contract.py"] = _exported_tests()
    artifacts["README.md"] = _instructions(bindings)
    manifest = {
        "format_version": 1, "compiler": "wil-net-dis-isolated-v1", "mode": bindings.mode,
        "original_package": scope["package"], "reviewed_original_sha256": ORIGINAL_SHA256,
        "original_package_matches_reviewed": package_hash == ORIGINAL_SHA256,
        "workflow_id": workflow_id, "workflow_name": WORKFLOW_NAME, "scope_sha256": scope_hash,
        "scoped_node_ids": [n["id"] for n in scope["nodes"]],
        "scope_node_count": len(scope["nodes"]), "scope_edge_count": len(scope["edges"]),
        "contract_sha256": _digest(bindings.model_dump()), "coverage": coverage, "gaps": gaps,
        "tests": {"exported": "tests/test_counter_contract.py", "local_status": "not_run_by_compiler",
                  "remote_status": "not_run", "boundary_job": "boundary_suite"},
        "artifacts": {path: {"sha256": sha256(text.encode("utf-8")).hexdigest(),
                             "size_bytes": len(text.encode("utf-8"))} for path, text in sorted(artifacts.items())},
    }
    # Manifest intentionally excludes itself, avoiding an impossible self-hash.
    artifacts["manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    return {"proposal": proposal, "manifest": manifest, "artifacts": artifacts}


def _exported_tests() -> str:
    return '''"""Portable contract tests, no Spark or Databricks access. Not runtime parity proof."""
import ast
from hashlib import sha256
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ContractTests(unittest.TestCase):
    def test_artifact_hashes(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        for name, receipt in manifest["artifacts"].items():
            self.assertEqual(sha256((ROOT / name).read_bytes()).hexdigest(), receipt["sha256"])

    def test_notebooks_compile(self):
        for path in (ROOT / "notebooks").glob("*.ipynb"):
            notebook = json.loads(path.read_text(encoding="utf-8"))
            for cell in notebook["cells"]:
                self.assertIn("id", cell["metadata"])
                self.assertIn("language", cell["metadata"])
                if cell["cell_type"] == "code":
                    ast.parse("".join(cell["source"]))

    def test_numeric_contract(self):
        for n, email, remaining in [(0, False, 1), (29, False, 59), (30, False, 0), (34, False, 0), (35, True, 0)]:
            with self.subTest(n=n):
                rows = [(18300, 1)] * n
                success_rows = len(rows)
                rows.extend([(18300, 1)] * success_rows)
                rows.append((18300, 1))
                if len(rows) >= 60:
                    rows.clear()
                self.assertEqual(success_rows >= 35, email)
                self.assertEqual(len(rows), remaining)

if __name__ == "__main__":
    unittest.main()
'''


def _instructions(bindings: DemoBindings) -> str:
    return f'''# WIL_NET_DIS isolated semantic test, NOT full migration

No deployment or remote execution has been performed by this compiler. All local
test and CLI validation receipts must be reported separately. The proposal and
manifest contain the exact scope, original hash, content hashes and gaps.

## Explicit prerequisites and approval gates

An administrator/user must approve use of the existing dedicated Unity Catalog
schema {bindings.catalog}.{bindings.schema_name}, confirm serverless notebook and
SDK availability, CREATE TABLE/SELECT/MODIFY rights and user-owned Jobs permissions.
Namespace existence and privileges are UNVERIFIED. Sample catalog demo_catalog is
a placeholder, not a discovered or authorized resource. Regenerate with explicit
approved test bindings before execution. No schema is provisioned by this bundle.
Do not bind this demo to the original counter table. No service principal run_as.
Review every artifact and obtain separate deployment approval before deploying.
Use the installed Databricks CLI to validate the dev bundle before any deployment.
STANDARD serverless is selected, batch test with no latency SLA. No schedules.

After separately approved deployment, boundary_suite runs counter_demo for 34 and
35 fixture rows with wait_seconds=0. counter_demo retains wait_seconds={bindings.wait_seconds}
(XML default 300), fixture_count={bindings.fixture_count}. Manual main runs require
test_ack=ISOLATED_TEST_ONLY. The suite supplies this explicit TEST acknowledgement.
Its run_job_task waits for the DEMO job only. The original -nowait command is
represented by SDK run_now submission without result(), not a blocking Run Job.
Child submission acceptance does not prove child completion. Inspect child runs
separately. The child notebook contains no original business transformations.

The session SELECT has no WHERE and overrides the mapping's DUAL/18537 default
with one (18300,1) row per source row. Treat source rows as Insert is authoritative,
not the writer's update/delete capability flags. Post-SQL adds one more row, then
deletes only template 18300 if that template count is >=60. Branches use successful
session target rows BEFORE Post-SQL. There is one timer, no polling loop.

New run-scoped tables are created without IF NOT EXISTS. No DROP, TRUNCATE,
CREATE OR REPLACE or overwrite is generated. Tables are deliberately retained for
inspection; owner-approved manual cleanup is separate. Retries are disabled.
Job success means selected TEST assertions passed, not migration equivalence.

## Portable local tests

Run the exported tests/test_counter_contract.py with Python unittest. These check
artifact integrity, Python syntax and boundary expectations only, not Spark parity.
The compiler repository additionally executes generated notebook bodies against
strict local Spark/SDK fakes, checks actual original XML and validates bundle shape.
LLM agents may review this deterministic proposal later; no AI authorship implied.
'''


def export_demo(result: dict, destination: Path) -> list[str]:
    """Write a NEW directory only, never overwrite existing release artifacts."""
    artifacts = result["artifacts"]
    manifest = json.loads(artifacts["manifest.json"])
    if set(artifacts) != set(manifest["artifacts"]) | {"manifest.json"}:
        raise ValueError("Artifact inventory mismatch")
    for name, text in artifacts.items():
        if not isinstance(name, str) or "\\" in name or name.startswith("/") or ":" in name or any(
                part in {"", ".", ".."} for part in name.split("/")):
            raise ValueError("Unsafe artifact path")
        if name != "manifest.json" and manifest["artifacts"][name] != {
                "sha256": sha256(text.encode("utf-8")).hexdigest(), "size_bytes": len(text.encode("utf-8"))}:
            raise ValueError("Artifact content hash mismatch")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, text in artifacts.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return sorted(artifacts)