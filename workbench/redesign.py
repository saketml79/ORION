"""Logical, tool-submitted designs and append-only human review. No execution APIs.

SQLite is scoped to the owning service root. Hosted persistence must explicitly
map this database; local SQLite alone is not distributed CAS or authentication.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
import re
import sqlite3
import shutil
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator


Text = Annotated[str, Field(min_length=1, max_length=2400)]
Identifier = Annotated[str, Field(min_length=1, max_length=160)]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
TaskKind = Literal["notebook_task", "sql_task", "pipeline_task", "run_job_task",
                   "condition_task", "for_each_task", "python_wheel_task",
                   "spark_python_task", "spark_jar_task", "dbt_task"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConditionLiteral(StrictModel):
    kind: Literal["literal"]
    value: Annotated[str, Field(min_length=1, max_length=2400)]

    @model_validator(mode="after")
    def no_dynamic_reference(self):
        if "{" in self.value or "}" in self.value:
            raise ValueError("Literal operands cannot contain dynamic reference syntax")
        return self


class ConditionTaskValue(StrictModel):
    kind: Literal["task_value"]
    task_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,100}$")]
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,100}$")]


class ConditionSpec(StrictModel):
    left: ConditionLiteral | ConditionTaskValue
    right: ConditionLiteral | ConditionTaskValue
    op: Literal["EQUAL_TO", "NOT_EQUAL", "GREATER_THAN", "GREATER_THAN_OR_EQUAL",
                "LESS_THAN", "LESS_THAN_OR_EQUAL"]


class LogicalTask(StrictModel):
    id: Identifier
    label: Text
    implementation_kind: TaskKind
    purpose: Text
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)
    condition_task: ConditionSpec | None = None

    @model_validator(mode="after")
    def condition_kind(self):
        if self.condition_task is not None and self.implementation_kind != "condition_task":
            raise ValueError("Structured conditions require a condition_task")
        return self


class LogicalEdge(StrictModel):
    source: Identifier
    target: Identifier
    condition: Text | None = None
    outcome: Literal["true", "false"] | None = None
    run_if: Literal["ALL_SUCCESS", "AT_LEAST_ONE_SUCCESS", "NONE_FAILED", "ALL_DONE",
                    "AT_LEAST_ONE_FAILED", "ALL_FAILED"] = "ALL_SUCCESS"
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class TaskCoverage(StrictModel):
    original_task_id: Identifier
    proposed_task_ids: list[Identifier] = Field(max_length=100)
    disposition: Literal["mapped", "disabled", "removed"]
    rationale: Text
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class Alternative(StrictModel):
    label: Text
    rationale: Text
    tradeoffs: list[Text] = Field(min_length=1, max_length=30)
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class ReuseGroup(StrictModel):
    id: Identifier
    proposed_task_ids: list[Identifier] = Field(min_length=1, max_length=100)
    rationale: Text
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class Parameter(StrictModel):
    name: Identifier
    purpose: Text
    required: bool
    value_type: Literal["string", "integer", "number", "boolean", "object", "array"]


class MappingArtifactPlan(StrictModel):
    """Proposed file identity and responsibility, never file contents or bindings."""

    relative_path: Annotated[str, Field(min_length=1, max_length=240)]
    artifact_kind: Literal["notebook", "module"]
    proposed_task_ids: list[Identifier] = Field(min_length=1, max_length=100)
    mapping_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    description: Text
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def notebook_extension(cls, value):
        # Agents commonly omit an extension or propose .py for notebook source.
        # Canonicalize only that unambiguous representation to the writer's
        # required Jupyter JSON path; keep all other invalid suffixes fail-closed.
        if isinstance(value, dict) and value.get("artifact_kind") == "notebook":
            path = value.get("relative_path")
            if isinstance(path, str) and (not PurePosixPath(path).suffix or path.endswith(".py")):
                normalized = dict(value)
                normalized["relative_path"] = path[:-3] + ".ipynb" if path.endswith(".py") else path + ".ipynb"
                return normalized
        return value

    @model_validator(mode="after")
    def relative_only(self):
        parts = self.relative_path.split("/")
        if (not re.fullmatch(r"[A-Za-z0-9_./-]+", self.relative_path)
                or any(p in {"", ".", ".."} for p in parts)
                or not self.relative_path.endswith((".py", ".ipynb"))):
            raise ValueError("Artifact path must be a relative .py or .ipynb proposal path without traversal")
        if self.artifact_kind == "module" and not self.relative_path.endswith(".py"):
            raise ValueError("Module proposal requires a .py path")
        if self.artifact_kind == "notebook" and not self.relative_path.endswith(".ipynb"):
            raise ValueError("Notebook proposal requires a .ipynb path")
        return self


class ScopedUnresolved(StrictModel):
    proposed_task_ids: list[Identifier] = Field(min_length=1, max_length=100)
    category: Literal["connection", "parameter", "mapping", "cadence", "control", "notification", "validation"]
    impact: Literal["execution_blocker", "human_review", "peripheral_note"]
    description: Text
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class CadencePlan(StrictModel):
    launch_mode: Literal["on_demand", "scheduled", "unknown"]
    preservation: Text
    operational_activation: Literal["unverified"] = "unverified"
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)


class Design(StrictModel):
    """An outline only. Physical bindings, scripts, SQL and artifact bodies have no fields."""

    schema_version: Literal[1] = 1
    title: Text
    tasks: list[LogicalTask] = Field(min_length=1, max_length=1000)
    edges: list[LogicalEdge] = Field(max_length=4000)
    rationale: Text
    alternatives: list[Alternative] = Field(min_length=1, max_length=30)
    coverage: list[TaskCoverage] = Field(max_length=4000)
    # The per-task, edge, coverage, alternative and artifact citations are the
    # authoritative evidence. This aggregate is a convenience, so omission is safe.
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    reuse_groups: list[ReuseGroup] = Field(default_factory=list, max_length=100)
    parameters: list[Parameter] = Field(default_factory=list, max_length=100)
    proposed_constraints: list[Text] = Field(default_factory=list, max_length=100)
    blockers: list[Text] = Field(default_factory=list, max_length=100)
    artifact_plan: list[MappingArtifactPlan] = Field(default_factory=list, max_length=1000)
    scoped_unresolved: list[ScopedUnresolved] = Field(default_factory=list, max_length=1000)
    cadence: CadencePlan | None = None

    @model_validator(mode="after")
    def logical_only(self):
        # Reject executable payloads even when smuggled into descriptive fields.
        for value in _strings(self.model_dump()):
            if re.search(r"```|<script\b|\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)|"
                         r"SELECT\s+.+\s+FROM|INSERT\s+INTO|DELETE\s+FROM|DROP\s+TABLE)|"
                         r"(?:^|\n)\s*(?:import\s+\w|from\s+\w+\s+import|def\s+\w+\s*\(|"
                         r"spark\.sql\s*\(|dbutils\.|%sql|#!/)", value, re.I):
                raise ValueError("Executable content is not allowed in a logical design")
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate proposed task IDs")
        known = set(ids)
        adjacency = {identifier: [] for identifier in ids}
        indegree = dict.fromkeys(ids, 0)
        pairs = set()
        for edge_index, edge in enumerate(self.edges):
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"Unknown proposed edge endpoint at edges[{edge_index}].source/target")
            pair = (edge.source, edge.target)
            if pair in pairs:
                raise ValueError("Duplicate proposed edge")
            pairs.add(pair)
            if edge.outcome is not None and next(t for t in self.tasks if t.id == edge.source).implementation_kind != "condition_task":
                raise ValueError("Conditional outcome requires a condition_task source")
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1
        ready = [identifier for identifier in ids if indegree[identifier] == 0]
        visited = 0
        while ready:
            identifier = ready.pop()
            visited += 1
            for target in adjacency[identifier]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(ids):
            raise ValueError("Proposed tasks must form a DAG without cycles")
        originals = [item.original_task_id for item in self.coverage]
        if len(set(originals)) != len(originals):
            raise ValueError("Duplicate original task coverage")
        referenced = set()
        for item in self.coverage:
            if set(item.proposed_task_ids) - known:
                raise ValueError("Coverage refers to unknown proposed tasks")
            if item.disposition == "mapped" and not item.proposed_task_ids:
                raise ValueError("Mapped original task requires proposed task IDs")
            referenced.update(item.proposed_task_ids)
        if known - referenced:
            raise ValueError("Every proposed task needs original behavior coverage")
        for group in self.reuse_groups:
            if set(group.proposed_task_ids) - known:
                raise ValueError("Reuse group refers to unknown proposed tasks")
        for item in [*self.artifact_plan, *self.scoped_unresolved]:
            if set(item.proposed_task_ids) - known:
                raise ValueError("Artifact or unresolved item refers to unknown proposed tasks")
        paths = [item.relative_path for item in self.artifact_plan]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate artifact proposal paths")
        for values in ([g.id for g in self.reuse_groups], [p.name for p in self.parameters]):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate group or parameter identifier")
        return self


class DesignQualityError(ValueError):
    """Evidence-backed semantic contract failures, separate from JSON schema errors."""

    def __init__(self, issues):
        super().__init__("Design does not preserve captured workflow semantics")
        self.issues = issues


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def cited_ids(design):
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_ids":
                    yield from item
                else:
                    yield from collect(item)
        elif isinstance(value, list):
            for item in value:
                yield from collect(item)
    return set(collect(design.model_dump()))


def build_wil_net_dis_candidate(model, workflow_id, scenario="original"):
    """Build a deterministic, logical-only WIL_NET_DIS proposal from canonical XML.

    This is a bounded compiler aid for one reviewed workflow, not executable
    code, a physical binding, a child-workflow conversion, or proof of parity.
    """
    if scenario not in {"original", "bronze"}:
        raise ValueError("scenario must be original or bronze")
    nodes = {node["id"]: node for node in model["nodes"]}
    workflow = nodes.get(workflow_id)
    if not workflow or workflow.get("name") != "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS":
        raise ValueError("The deterministic WIL_NET_DIS proposal applies only to its exact selected workflow")
    originals = {node.get("name"): node for node in model["nodes"]
                 if node.get("kind") == "taskinstance" and node.get("parent_id") == workflow_id}
    required_names = {"Start", "tmr_WAIT_FOR_DEPENDENCY", "s_m_AE2E_JOB_CHECK_COUNTER",
                      "cmd_AE2E_CALL_WILTEL_NET_DIS_PULL", "eml_on_SUCCESS"}
    if set(originals) != required_names:
        raise ValueError("The selected workflow task inventory differs from the deterministic proposal contract")

    def control(source_name, target_name):
        source_id, target_id = originals[source_name]["id"], originals[target_name]["id"]
        matches = [edge for edge in model["edges"] if edge.get("kind") == "workflow_control"
                   and edge.get("source") == source_id and edge.get("target") == target_id]
        if len(matches) != 1:
            raise ValueError("A required WIL_NET_DIS control link is missing or ambiguous")
        return matches[0]

    timer_session = control("tmr_WAIT_FOR_DEPENDENCY", "s_m_AE2E_JOB_CHECK_COUNTER")
    session_command = control("s_m_AE2E_JOB_CHECK_COUNTER", "cmd_AE2E_CALL_WILTEL_NET_DIS_PULL")
    session_email = control("s_m_AE2E_JOB_CHECK_COUNTER", "eml_on_SUCCESS")
    if not session_command.get("attributes", {}).get("CONDITION") or not session_email.get("attributes", {}).get("CONDITION"):
        raise ValueError("Both captured TgtSuccessRows predicates are required")
    if "<" not in session_command["attributes"]["CONDITION"] or ">=" not in session_email["attributes"]["CONDITION"]:
        raise ValueError("The captured async-child and dependency-email threshold branches changed")

    outgoing = {}
    for edge in model["edges"]:
        outgoing.setdefault(edge["source"], []).append(edge)
    session_definition = [edge["target"] for edge in outgoing.get(
        originals["s_m_AE2E_JOB_CHECK_COUNTER"]["id"], ()) if edge.get("kind") == "task_definition"]
    mapping_names = {nodes[identifier].get("attributes", {}).get("MAPPINGNAME")
                     for identifier in session_definition if identifier in nodes}
    mappings = [node for node in model["nodes"] if node.get("kind") == "mapping"
                and node.get("name") in mapping_names and node.get("scope", {}).get("folder") == "B_AE2E"]
    if len(mappings) != 1:
        raise ValueError("The counter session mapping is missing or ambiguous")
    mapping = mappings[0]

    scope = _workflow_scope(nodes, outgoing, workflow_id)
    connections = [node for node in model["nodes"] if node.get("kind") == "connectionreference"
                   and node["id"] in scope]
    connection_evidence = []
    for node in connections:
        connection_evidence.append(node["id"])
        if node.get("parent_id") in nodes:
            connection_evidence.append(node["parent_id"])
    connection_description = (
        "Review the Unity Catalog bindings supplied by bronze_source_table and target_table before execution. "
        "This scenario reads already-landed bronze data; historical Informatica connections are not required. "
        "Unverified configuration does not prevent boilerplate generation; no Unity Catalog lookup was performed."
        if scenario == "bronze" else
        "Review the scoped Informatica connection references and bind verified source and target resources before execution. "
        "Connection names are historical references, not verified runtime bindings. Unavailable configuration does not "
        "prevent boilerplate generation; no Unity Catalog lookup was performed."
    )

    scheduler_link = next((edge for edge in model["edges"] if edge.get("kind") == "schedule_reference"
                           and edge.get("source") == workflow_id), None)
    cadence_evidence = [workflow_id]
    schedule_type = "unknown"
    if scheduler_link:
        cadence_evidence.append(scheduler_link["id"])
        scheduler_id = scheduler_link["target"]
        for edge in outgoing.get(scheduler_id, ()):
            if edge.get("kind") == "contains" and edge.get("target") in nodes:
                schedule = nodes[edge["target"]]
                if schedule.get("kind") == "scheduleinfo":
                    schedule_type = schedule.get("attributes", {}).get("SCHEDULETYPE", "unknown")
                    cadence_evidence.append(schedule["id"])
                    break
    launch_mode = "on_demand" if schedule_type.upper() == "ONDEMAND" else "unknown"
    if launch_mode == "unknown":
        raise ValueError("The WIL_NET_DIS on-demand launch cadence is no longer captured")

    start = originals["Start"]["id"]
    timer = originals["tmr_WAIT_FOR_DEPENDENCY"]["id"]
    session = originals["s_m_AE2E_JOB_CHECK_COUNTER"]["id"]
    command = originals["cmd_AE2E_CALL_WILTEL_NET_DIS_PULL"]["id"]
    email = originals["eml_on_SUCCESS"]["id"]
    return {
        "title": "WIL_NET_DIS dependency gate and branch",
        "tasks": [
            {"id": "dependency_gate", "label": "Wait and read dependency counter",
             "implementation_kind": "notebook_task",
             "purpose": "Wait five minutes relative to the prior task, then run mapping m_LOAD_AE2E_JOB_DEP_CHK_COUNTER and expose TgtSuccessRows for branching.",
             "evidence_ids": [timer, session, mapping["id"]]},
            {"id": "dependency_branch", "label": "Dependency counter decision",
             "implementation_kind": "condition_task",
             "purpose": "Route by the captured TgtSuccessRows threshold: below 35 launches the child asynchronously; 35 or more sends the captured dependency-failure email.",
             "evidence_ids": [session_command["id"], session_email["id"]]},
            {"id": "launch_child_async", "label": "Asynchronous child workflow launch",
             "implementation_kind": "python_wheel_task",
             "purpose": "Logical adapter for the captured pmcmd -nowait launch of wkf_Load_AE2E_WILTEL_NETWORK_DISCO; runtime implementation remains unresolved.",
             "evidence_ids": [command]},
            {"id": "notify_dependency_failure", "label": "Dependency failure notification",
             "implementation_kind": "python_wheel_task",
             "purpose": "Logical adapter for the captured Email task; its body says the dependency was not satisfied despite the task name containing SUCCESS.",
             "evidence_ids": [email]},
        ],
        "edges": [
            {"source": "dependency_gate", "target": "dependency_branch",
             "evidence_ids": [timer_session["id"]]},
            {"source": "dependency_branch", "target": "launch_child_async",
             "condition": session_command["attributes"]["CONDITION"], "outcome": "true",
             "evidence_ids": [session_command["id"]]},
            {"source": "dependency_branch", "target": "notify_dependency_failure",
             "condition": session_email["attributes"]["CONDITION"], "outcome": "false",
             "evidence_ids": [session_email["id"]]},
        ],
        "rationale": "Combine the relative wait and counter Session, then branch by exact row count. Keep both external side effects as logical adapters pending owner review. The 15 tasks inside the referenced child workflow are out of scope and are not represented as converted.",
        "alternatives": [{
            "label": "Preserve timer and counter as separate Jobs tasks",
            "rationale": "Retain the original boundary between the dependency wait and counter evaluation.",
            "tradeoffs": ["Adds a Jobs task boundary but more closely preserves independent retry and failure handling."],
            "evidence_ids": [timer, session, timer_session["id"]],
        }],
        "coverage": [
            {"original_task_id": start, "proposed_task_ids": [], "disposition": "removed",
             "rationale": "Enabled Start is administrative workflow entry, not business work.", "evidence_ids": [start]},
            {"original_task_id": timer, "proposed_task_ids": ["dependency_gate"], "disposition": "mapped",
             "rationale": "The five-minute relative wait is combined with the following counter evaluation.", "evidence_ids": [timer]},
            {"original_task_id": session, "proposed_task_ids": ["dependency_gate", "dependency_branch"], "disposition": "mapped",
             "rationale": "Runs the captured counter mapping before threshold routing.", "evidence_ids": [session, mapping["id"]]},
            {"original_task_id": command, "proposed_task_ids": ["launch_child_async"], "disposition": "mapped",
             "rationale": "Preserves the captured nonblocking child launch as an unresolved logical adapter.", "evidence_ids": [command]},
            {"original_task_id": email, "proposed_task_ids": ["notify_dependency_failure"], "disposition": "mapped",
             "rationale": "Preserves the notification branch and its actual dependency-failure message.", "evidence_ids": [email]},
        ],
        "evidence_ids": [workflow_id, start, timer, session, command, email, mapping["id"],
                         timer_session["id"], session_command["id"], session_email["id"], *connection_evidence,
                         *cadence_evidence],
        "reuse_groups": [],
        "parameters": [
            {"name": "bronze_source_table", "purpose": "Unity Catalog table containing already-landed bronze source data.",
             "required": True, "value_type": "string"},
            {"name": "target_table", "purpose": "Unity Catalog target table binding to review before execution.",
             "required": True, "value_type": "string"},
        ] if scenario == "bronze" else [],
        "proposed_constraints": [
            "Do not convert the -nowait child launch into a blocking job dependency.",
            "Preserve the captured email body and do not infer success from the task name.",
            "Do not convert the five-minute in-run wait into a recurring workflow schedule.",
        ],
        "blockers": [],
        "artifact_plan": [{
            "relative_path": "notebooks/wil_net_dis_dependency_gate.ipynb", "artifact_kind": "notebook",
            "proposed_task_ids": ["dependency_gate"], "mapping_ids": [mapping["id"]],
            "description": "Notebook responsibility for the relative wait and counter-mapping boundary. Generated boilerplate artifacts are provided separately from this logical plan; runtime bindings and parity require verification.",
            "evidence_ids": [timer, session, mapping["id"]],
        }],
        "scoped_unresolved": [
            {"proposed_task_ids": ["launch_child_async"], "category": "control", "impact": "execution_blocker",
             "description": "The captured command uses pmcmd -nowait. Confirm an asynchronous Databricks child-job invocation contract and preserve nonblocking behavior; the child workflow is outside this proposal.",
             "evidence_ids": [command, session_command["id"]]},
            {"proposed_task_ids": ["notify_dependency_failure"], "category": "notification", "impact": "peripheral_note",
             "description": "Confirm recipient routing and email delivery configuration. The captured message says the dependency was not satisfied; do not infer success from the task name.",
             "evidence_ids": [email, session_email["id"]]},
            {"proposed_task_ids": ["dependency_gate"], "category": "connection", "impact": "human_review",
             "description": connection_description,
             "evidence_ids": [session, mapping["id"]] if scenario == "bronze" else connection_evidence or [session, mapping["id"]]},
        ],
        "cadence": {"launch_mode": launch_mode,
                    "preservation": "Preserve ONDEMAND workflow launch. The five-minute timer is an in-run wait relative to the previous task, not a recurring schedule. External triggers and operational activation remain unverified.",
                    "evidence_ids": cadence_evidence},
    }


def _workflow_scope(nodes, outgoing, workflow_id):
    """Return the bounded structural closure for deterministic proposal checks."""
    from workbench.domain import _RELATIONS
    scope, queue = {workflow_id}, [workflow_id]
    while queue:
        for edge in outgoing.get(queue.pop(), ()):
            target = edge.get("target")
            if edge.get("kind") in _RELATIONS and target in nodes and target not in scope:
                scope.add(target)
                queue.append(target)
    return scope


def validate_submission(design, model, workflow_id, retrieved, records, scenario="original"):
    if scenario not in {"original", "bronze"}:
        raise ValueError("scenario must be original or bronze")
    design = Design.model_validate(design)
    originals = {node["id"]: node for node in model["nodes"]
                 if node["kind"] == "taskinstance" and node.get("parent_id") == workflow_id}
    if {item.original_task_id for item in design.coverage} != set(originals):
        raise ValueError("Coverage must include ALL direct original workflow taskinstances, including disabled tasks")
    citations = cited_ids(design)
    if citations - set(retrieved) or citations - set(records):
        raise ValueError("Citations must be real evidence already retrieved in this role and scope")
    for item in design.coverage:
        if item.original_task_id not in item.evidence_ids:
            raise ValueError("Coverage must cite its original canonical task ID")
        disabled = originals[item.original_task_id].get("attributes", {}).get("ISENABLED", "").upper() in {"NO", "FALSE", "0"}
        if disabled != (item.disposition == "disabled"):
            raise ValueError("Disabled original tasks require an explicit disabled disposition")
    for artifact in design.artifact_plan:
        for mapping_id in artifact.mapping_ids:
            if (mapping_id not in artifact.evidence_ids
                    or records.get(mapping_id, {}).get("kind") != "mapping"):
                raise ValueError("Artifact mapping IDs must cite retrieved canonical mapping definitions")
    quality_issues = _quality_issues(design, model, workflow_id, originals, scenario)
    if quality_issues:
        raise DesignQualityError(quality_issues)
    return design


def _quality_issues(design, model, workflow_id, originals, scenario="original"):
    """Reject common but unsafe Informatica-to-Lakeflow category mistakes."""
    nodes = {node["id"]: node for node in model["nodes"]}
    outgoing = {}
    for edge in model["edges"]:
        outgoing.setdefault(edge["source"], []).append(edge)
    tasks = {task.id: task for task in design.tasks}
    coverage = {item.original_task_id: item for item in design.coverage}
    wil_net_dis_contract = nodes[workflow_id].get("name") == "wkf_AE2E_DUMMY_CALL_WIL_NET_DIS"
    issues = []

    def issue(field, message):
        issues.append({"field": field, "issue": message})

    def mapped(original_id):
        return [tasks[task_id] for task_id in coverage[original_id].proposed_task_ids
                if task_id in tasks]

    for parameter in design.parameters:
        purpose = parameter.purpose
        schema_variable = (re.search(r"\$\$[A-Za-z_][A-Za-z0-9_]*schema\b", purpose, re.IGNORECASE)
                           or re.search(r"\bschema\s+variable\b", purpose, re.IGNORECASE))
        catalog_claim = parameter.name.casefold() in {"target_catalog", "catalog"}
        if catalog_claim and schema_variable and re.search(r"\bderived\s+from\b", purpose, re.IGNORECASE):
            issue(f"parameters.{parameter.name}.purpose",
                  "A Unity Catalog catalog cannot be derived from a schema variable. Resolve target_catalog independently from target-specific evidence or leave it as a required empty runtime parameter; do not describe the target as mapped.")

    # Informatica Start is the workflow entry marker, not business work.
    for original_id, original in originals.items():
        task_type = original.get("attributes", {}).get("TASKTYPE", "").casefold()
        original_label = original.get("name", task_type or "task")
        row = coverage[original_id]
        disabled = original.get("attributes", {}).get("ISENABLED", "").upper() in {"NO", "FALSE", "0"}
        expected_start_disposition = "disabled" if disabled else "removed"
        if task_type == "start" and (row.disposition != expected_start_disposition or row.proposed_task_ids):
            issue(f"coverage.{original_label}",
                  "Start is administrative workflow entry. Mark an enabled Start removed, or a disabled Start disabled, with no proposed executable task.")
        if wil_net_dis_contract and task_type == "email" and row.proposed_task_ids and not any(
                unresolved.category == "notification" and unresolved.impact == "peripheral_note"
                and set(unresolved.proposed_task_ids) & set(row.proposed_task_ids)
                for unresolved in design.scoped_unresolved):
            issue(f"scoped_unresolved.{original_label}",
                  "Keep email delivery as a task-scoped notification peripheral_note until recipient and delivery configuration are verified.")

    timers = [identifier for identifier, node in originals.items()
              if node.get("attributes", {}).get("TASKTYPE", "").casefold() == "timer"]
    has_cadence_evidence = any(edge.get("kind") == "schedule_reference" and edge.get("source") == workflow_id
                               for edge in model["edges"])
    if wil_net_dis_contract and timers and has_cadence_evidence:
        invalid_timer_tasks = [task_id for identifier in timers for task_id in coverage[identifier].proposed_task_ids
                               if task_id in tasks and tasks[task_id].implementation_kind in {"condition_task", "run_job_task"}]
        if invalid_timer_tasks:
            issue("coverage.Timer",
                  "A Timer is an in-run wait, not a condition task or launch schedule. Consolidate the wait with work or use a supported executable task.")
        if design.cadence is None:
            issue("cadence", "Capture workflow launch cadence separately from the in-run Timer wait.")
        elif not re.search(r"wait|timer|in-run|in run", design.cadence.preservation, re.I):
            issue("cadence.preservation", "Explain that the captured Timer is an in-run wait, not a recurring workflow schedule.")

    # Every captured conditional control link must become a branch from a
    # condition_task. Citing it on an ordinary SQL edge does not preserve control.
    for control in model["edges"] if wil_net_dis_contract else ():
        if (control.get("kind") != "workflow_control" or control.get("source") not in originals
                or control.get("target") not in originals
                or not control.get("attributes", {}).get("CONDITION")):
            continue
        matches = [edge for edge in design.edges if control["id"] in edge.evidence_ids]
        if not matches:
            if not control.get("attributes", {}).get("CONDITION"):
                continue
            issue("edges", "Represent every conditional Informatica control link as an evidence-cited Lakeflow branch.")
            continue
        original_condition = re.sub(r"\s+", "", control["attributes"]["CONDITION"])
        for index, edge in enumerate(matches):
            source_task = tasks.get(edge.source)
            if source_task is None or source_task.implementation_kind != "condition_task":
                issue(f"edges.{index}.source", "Conditional control links must leave a condition_task, not a SQL or notebook task.")
            if edge.outcome not in {"true", "false"}:
                issue(f"edges.{index}.outcome", "Set the branch outcome to true or false on each condition_task edge.")
            if not edge.condition or original_condition not in re.sub(r"\s+", "", edge.condition):
                issue(f"edges.{index}.condition", "Preserve the exact captured Informatica predicate on this branch.")

    # Side effects and physical bindings remain visible, scoped review items.
    scope = _workflow_scope(nodes, outgoing, workflow_id)
    connection_ids = {identifier for identifier in scope if nodes[identifier]["kind"] == "connectionreference"}
    if wil_net_dis_contract:
        binding_reviews = [item for item in design.scoped_unresolved
                           if item.category == "connection" and item.impact == "human_review"]
        if scenario == "bronze":
            parameters = {parameter.name: parameter for parameter in design.parameters}
            for name in ("bronze_source_table", "target_table"):
                parameter = parameters.get(name)
                if parameter is None or not parameter.required or parameter.value_type != "string":
                    issue(f"parameters.{name}", "Bronze requires a native string parameter with required=true for this Unity Catalog table binding.")
            if not binding_reviews:
                issue("scoped_unresolved", "Represent Unity Catalog bronze source and target bindings as connection/human_review, not historical Informatica connection requirements.")
        elif connection_ids and not any(connection_ids & set(item.evidence_ids) for item in binding_reviews):
            issue("scoped_unresolved", "Represent scoped Informatica connection references as evidence-cited connection/human_review. Verified runtime bindings are needed before execution, not boilerplate generation.")

    for original_id, original in originals.items() if wil_net_dis_contract else ():
        if original.get("attributes", {}).get("TASKTYPE", "").casefold() != "command":
            continue
        pending = [target for edge in outgoing.get(original_id, ())
                   if edge.get("kind") == "task_definition" for target in [edge["target"]]]
        seen = set()
        command_text = []
        while pending:
            current = pending.pop()
            if current in seen or current not in nodes:
                continue
            seen.add(current)
            node = nodes[current]
            command_text.extend(str(value) for value in node.get("attributes", {}).values())
            command_text.extend(str(value) for prop in node.get("properties", [])
                                for value in prop.get("attributes", {}).values())
            pending.extend(edge["target"] for edge in outgoing.get(current, ())
                           if edge.get("kind") == "contains")
        if any("-nowait" in value.casefold() for value in command_text):
            task_ids = set(coverage[original_id].proposed_task_ids)
            if not any(item.category == "control" and item.impact in {"human_review", "peripheral_note", "execution_blocker"}
                       and task_ids & set(item.proposed_task_ids) for item in design.scoped_unresolved):
                issue(f"scoped_unresolved.{original.get('name', 'Command')}",
                    "The captured command uses -nowait. Record this external call as a non-executing informational placeholder with a scoped control note. Do not require or launch a child Job in single-workflow conversion.")

    return issues


GATES = {"advisory_only": True, "approved": False, "generation_allowed": False,
         "deployment_allowed": False}


def artifact_completeness(design):
    """Outline advice only; do not reject or mutate historical logical designs."""
    issues = []
    for task in design.get("tasks", []):
        if task.get("implementation_kind") == "condition_task" and not task.get("condition_task"):
            issues.append({"task_id": task["id"], "code": "STRUCTURED_CONDITION_REQUIRED",
                "reason": "Branch text is a logical outline. Artifact planning requires explicit left operand, operator and right operand; no threshold or task-value key is inferred."})
    return {"issues": issues, "outline_only": True, "execution_ready": False,
            "compiled": False}


def list_item(record):
    """List identity/status only. Fetch exact version detail when selected."""
    return ({key: record[key] for key in ("id", "package_hash", "workflow_id", "scenario",
             "version", "status", "input_version", "created_at", "author_role")}
            | {"design": {"title": record["design"]["title"]}, **GATES})


def projected_read(index, evidence, query, identifier=None):
    """Read verified committed redesign families, not a restored execution runtime.

    Loop freshness includes exactly the approved lesson window selected by the
    restored Delta row ordering, not history, execution caches or model clients.
    """
    from workbench.readiness import current_versions
    package = index.model["package"]["sha256"]
    rows = evidence["designs"]
    scenario = query.get("scenario", [None])[-1]
    workflow = query.get("workflow_id", [None])[-1]
    if scenario not in {None, "original", "bronze"}:
        raise HTTPException(422, "Invalid redesign scenario")
    if workflow is not None and index.nodes.get(workflow, {}).get("kind") != "workflow":
        raise HTTPException(404, "Unknown workflow in this package")
    rows = [r for r in rows if r["package_hash"] == package
            and (scenario is None or r["scenario"] == scenario)
            and (workflow is None or r["workflow_id"] == workflow)
            and (identifier is None or r["id"] == identifier)]
    if any(r["source"]["kind"] == "loop" for r in rows) and "lessons" not in evidence:
        raise ValueError("Redesign lesson evidence unavailable")
    versions = current_versions(index, evidence)

    def present(record, detail=False):
        current = versions.get((record["workflow_id"], record["scenario"]))
        if current is None:
            raise ValueError("Redesign current input evidence unavailable")
        if record["source"]["kind"] == "loop":
            from workbench.app import _hash
            # Full restore inserts verified rows in record_id order. SQLite's
            # rowid DESC LIMIT 20 therefore selects the last 20 approved rows.
            approved = [l for l in evidence["lessons"] if l["package_hash"] == package
                        and l["workflow_id"] == record["workflow_id"] and l["status"] == "approved"]
            approved = sorted(approved[-20:], key=lambda lesson: lesson["id"])
            current = _hash([current, approved])
        stale = record["input_version"] != current
        events = sorted((r for r in evidence["reviews"] if r.get("design_id") == record["id"]
                         and r.get("version") == record["version"]), key=lambda r: r["review_version"])
        decisions = [r for r in events if r["action"] != "comment"]
        status = decisions[-1]["action"] if decisions else "draft"
        result = {**(record if detail else list_item(record)), "status": status,
                  "current_input_version": current, "stale": stale, **GATES}
        if detail:
            result.update(reviews=events, review_version=len(events),
                outline_drafting_allowed=bool(decisions and decisions[-1]["action"] == "approve_for_drafting"
                    and not stale and not record["design"]["blockers"]),
                evidence_references=index.evidence_references(cited_ids(Design.model_validate(record["design"]))),
                artifact_completeness=artifact_completeness(record["design"]))
        return result

    rows.sort(key=lambda r: (r["version"], r["id"]), reverse=True)
    try:
        if identifier is not None:
            version = int(query["version"][-1]) if "version" in query else None
            if version is not None and version < 1:
                raise ValueError()
            selected = next((r for r in rows if version is None or r["version"] == version), None)
            if selected is None:
                raise HTTPException(404, "Unknown redesign in this package")
            return {**present(selected, True), 'latest_version': max(r['version'] for r in rows)}
        offset, limit = int(query.get("offset", ["0"])[-1]), int(query.get("limit", ["50"])[-1])
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid redesign version or pagination") from None
    return {"items": [present(r) for r in rows[offset:offset + limit]], "total": len(rows),
            "offset": offset, "limit": limit, "truncated": offset + limit < len(rows), **GATES}


class Conflict(ValueError):
    pass


class Review(StrictModel):
    action: Literal["comment", "request_changes", "reject", "approve_for_drafting"]
    comment: Text
    expected_version: int = Field(ge=1)
    expected_review_version: int = Field(default=0, ge=0)
    expected_input_version: Hash
    consent: bool = False


class DeleteDesignRequest(StrictModel):
    version: int = Field(ge=1)
    expected_review_version: int = Field(ge=0)
    consent: bool


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


class RedesignStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS redesign_versions (
                    id TEXT NOT NULL, package_hash TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    scenario TEXT NOT NULL, version INTEGER NOT NULL, source_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, PRIMARY KEY(id, version));
                CREATE TABLE IF NOT EXISTS redesign_reviews (
                    id TEXT NOT NULL, version INTEGER NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(id, version, revision));
                CREATE TRIGGER IF NOT EXISTS redesign_versions_no_update BEFORE UPDATE ON redesign_versions
                    BEGIN SELECT RAISE(ABORT, 'immutable redesign version'); END;
                CREATE TRIGGER IF NOT EXISTS redesign_versions_no_delete BEFORE DELETE ON redesign_versions
                    BEGIN SELECT RAISE(ABORT, 'immutable redesign version'); END;
                CREATE TRIGGER IF NOT EXISTS redesign_reviews_no_update BEFORE UPDATE ON redesign_reviews
                    BEGIN SELECT RAISE(ABORT, 'append-only redesign review'); END;
                CREATE TRIGGER IF NOT EXISTS redesign_reviews_no_delete BEFORE DELETE ON redesign_reviews
                    BEGIN SELECT RAISE(ABORT, 'append-only redesign review'); END;
                CREATE TABLE IF NOT EXISTS redesign_version_tombstones (
                    id TEXT NOT NULL, package_hash TEXT NOT NULL, version INTEGER NOT NULL,
                    source_key TEXT NOT NULL UNIQUE, deleted_at TEXT NOT NULL,
                    PRIMARY KEY(id, version));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, package_hash, workflow_id, scenario, submission, source, expected_version):
        identifier = sha256(_json([package_hash, workflow_id, scenario]).encode()).hexdigest()
        source_key = _json([source["record_id"], source["runtime_run_id"], source["submission_index"]])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload FROM redesign_versions WHERE source_key=?", (source_key,)).fetchone()
            if existing:
                if json.loads(existing[0])["design"] != submission["design"]:
                    raise Conflict("A source submission cannot be replaced")
                return json.loads(existing[0])
            deleted = db.execute("SELECT 1 FROM redesign_version_tombstones WHERE source_key=?", (source_key,)).fetchone()
            if deleted:
                raise Conflict("This source submission was deleted; start a new agent run to create a fresh design")
            live_version = db.execute("SELECT coalesce(max(version),0) FROM redesign_versions WHERE id=?", (identifier,)).fetchone()[0]
            deleted_version = db.execute("SELECT coalesce(max(version),0) FROM redesign_version_tombstones WHERE id=?",
                                         (identifier,)).fetchone()[0]
            current = max(live_version, deleted_version)
            if current != expected_version:
                raise Conflict("Redesign version changed; run a new explicit agent review")
            record = {"id": identifier, "package_hash": package_hash, "workflow_id": workflow_id,
                      "scenario": scenario, "version": current + 1, "status": "draft",
                      "design": submission["design"], "author_role": submission["role"],
                      "retrieved_evidence_ids": submission["retrieved_evidence_ids"],
                      "source": source, "input_version": source["input_version"],
                      "created_at": datetime.now(timezone.utc).isoformat(), **GATES}
            db.execute("INSERT INTO redesign_versions VALUES (?,?,?,?,?,?,?)",
                       (identifier, package_hash, workflow_id, scenario, current + 1, source_key, _json(record)))
        return record

    def list(self, package_hash, workflow_id=None, scenario=None):
        where, values = ["package_hash=?"], [package_hash]
        for key, value in (("workflow_id", workflow_id), ("scenario", scenario)):
            if value is not None:
                where.append(key + "=?")
                values.append(value)
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM redesign_versions WHERE " + " AND ".join(where) + " ORDER BY version DESC", values).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, identifier, package_hash, version=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM redesign_versions WHERE id=? AND package_hash=? "
                             + ("AND version=? " if version is not None else "") + "ORDER BY version DESC LIMIT 1",
                             (identifier, package_hash, version) if version is not None else (identifier, package_hash)).fetchone()
            if not row:
                raise KeyError(identifier)
            record = json.loads(row[0])
            reviews = db.execute("SELECT payload FROM redesign_reviews WHERE id=? AND version=? ORDER BY revision",
                                 (identifier, record["version"])).fetchall()
        events = [json.loads(row[0]) for row in reviews]
        decisions = [event for event in events if event["action"] != "comment"]
        return {**record, "status": decisions[-1]["action"] if decisions else "draft",
                "reviews": events, "review_version": len(events),
                "outline_drafting_allowed": bool(decisions and decisions[-1]["action"] == "approve_for_drafting")}

    def delete_version(self, identifier, package_hash, version, expected_review_version):
        """Physically remove one exact design/review version and retain a content-free tombstone."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT source_key FROM redesign_versions WHERE id=? AND package_hash=? AND version=?",
                             (identifier, package_hash, version)).fetchone()
            if row is None:
                raise KeyError(identifier)
            revisions = db.execute("SELECT count(*) FROM redesign_reviews WHERE id=? AND version=?",
                                   (identifier, version)).fetchone()[0]
            if revisions != expected_review_version:
                raise Conflict("Design review history changed; refresh the exact version before deleting")
            deleted_at = datetime.now(timezone.utc).isoformat()
            # Deletion is an explicit user action. Drop immutable guards only
            # inside this write transaction, then restore them before commit.
            db.execute("DROP TRIGGER IF EXISTS redesign_versions_no_delete")
            db.execute("DROP TRIGGER IF EXISTS redesign_reviews_no_delete")
            db.execute("DELETE FROM redesign_reviews WHERE id=? AND version=?", (identifier, version))
            db.execute("DELETE FROM redesign_versions WHERE id=? AND package_hash=? AND version=?",
                       (identifier, package_hash, version))
            db.execute("INSERT INTO redesign_version_tombstones VALUES (?,?,?,?,?)",
                       (identifier, package_hash, version, row[0], deleted_at))
            db.execute("CREATE TRIGGER redesign_versions_no_delete BEFORE DELETE ON redesign_versions "
                       "BEGIN SELECT RAISE(ABORT, 'immutable redesign version'); END")
            db.execute("CREATE TRIGGER redesign_reviews_no_delete BEFORE DELETE ON redesign_reviews "
                       "BEGIN SELECT RAISE(ABORT, 'append-only redesign review'); END")
        return {"id": identifier, "package_hash": package_hash, "version": version,
                "deleted": True, "deleted_at": deleted_at}

    def review(self, identifier, package_hash, body, current_input_version, actor):
        if body.consent is not True:
            raise PermissionError("Explicit consent:true is required for a review action")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM redesign_versions WHERE id=? AND package_hash=? ORDER BY version DESC LIMIT 1",
                             (identifier, package_hash)).fetchone()
            if not row:
                raise KeyError(identifier)
            proposal = json.loads(row[0])
            revision = db.execute("SELECT count(*) FROM redesign_reviews WHERE id=? AND version=?",
                                  (identifier, proposal["version"])).fetchone()[0]
            if proposal["version"] != body.expected_version or revision != body.expected_review_version:
                raise Conflict("Proposal or review version changed")
            if body.expected_input_version != current_input_version:
                raise Conflict("Current input version changed")
            if body.action == "approve_for_drafting":
                if proposal["input_version"] != current_input_version:
                    raise Conflict("Stale redesign cannot be approved for drafting")
                if proposal["design"]["blockers"]:
                    raise Conflict("Unresolved redesign blockers prevent drafting approval")
            event = {"action": body.action, "comment": body.comment,
                     "version": proposal["version"], "review_version": revision + 1,
                     "input_version": current_input_version, "consent": True, **actor,
                     "created_at": datetime.now(timezone.utc).isoformat(), **GATES}
            db.execute("INSERT INTO redesign_reviews VALUES (?,?,?,?)",
                       (identifier, proposal["version"], revision + 1, _json(event)))
        return event


def store_for(service):
    return RedesignStore(service.state / "redesigns.sqlite")


def capture_run(service, index, record, output):
    """Shared worker hook. Only structured submit_redesign receipts, never prose.

    Call from the agent or loop worker with its captured server-side run record.
    The loop caller should carry redesign_expected_version from its queue boundary.
    """
    from workbench.agents import _Evidence
    if not isinstance(output, dict) or output.get("status") in {"failed", "unavailable"}:
        return []
    submissions = output.get("redesigns", [])
    if not submissions:
        return []
    if record.get("consent") is not True or not isinstance(submissions, list) or len(submissions) > 10:
        raise ValueError("Invalid redesign run receipt")
    runtime_id = output.get("run_id")
    if not isinstance(runtime_id, str) or not re.fullmatch(r"[a-f0-9]{32}", runtime_id):
        raise ValueError("Redesign requires a real source runtime run ID")
    # Read the immutable run input snapshot, not mutable current evidence.
    captured = record.get("redesign_snapshot")
    if not isinstance(captured, dict):
        raise ValueError("Captured redesign input snapshot is required")
    evidence = _Evidence(index.model, record["workflow_id"], captured["documents"], captured["reviews"])
    # Neighbor evidence can be cited only if the runtime actually retrieved its page.
    for page in output.get("neighbor_pages", []):
        evidence.package_neighbors(page["offset"], page["limit"])
    from workbench.retrieval_receipts import replay_planning
    role_records = replay_planning(index.model, record["workflow_id"],
                                  output.get("planning_retrievals", []), evidence.records)
    validated = []
    for submission in submissions:
        required = {"role", "design", "retrieved_evidence_ids"}
        if (not isinstance(submission, dict)
                or not required <= set(submission)
                or set(submission) - required - {"design_origin"}
                or submission["role"] not in {"architecture", "supervisor"}
                or ("design_origin" in submission and submission["design_origin"] not in
                    {"agent_submitted", "deterministic_workflow_compiler"})):
            raise ValueError("Invalid redesign tool submission receipt")
        design = validate_submission(submission["design"], index.model, record["workflow_id"],
                                     submission["retrieved_evidence_ids"],
                                     role_records.get(submission["role"], evidence.records), scenario=record["scenario"])
        validated.append({**submission, "design": design.model_dump()})
    store = store_for(service)
    expected = record.get("redesign_expected_version", 0)
    saved = []
    for number, submission in enumerate(validated):
        source = {"record_id": record["id"], "runtime_run_id": runtime_id, "submission_index": number,
                  "kind": "loop" if "base_input_version" in record else "agent",
                  "input_version": record["input_version"],
                  "base_input_version": record.get("base_input_version", record["input_version"]),
                  "input_hashes": record["input_hashes"], "input_versions": record["input_versions"],
                  "runtime_input_hashes": output.get("input_hashes", {})}
        if "design_origin" in submission:
            source["design_origin"] = submission["design_origin"]
        saved.append(store.append(record["package_hash"], record["workflow_id"], record["scenario"],
                                  submission, source, expected + number))
    return saved


def install_redesigns(app):
    service = app.state.service

    def current(index, record):
        if record["source"]["kind"] == "loop":
            return app.state.loops._inputs(index, record["workflow_id"], record["scenario"])[0]["input_version"]
        return service.agent_inputs(index, record["workflow_id"], record["scenario"])["input_version"]

    def display(index, record):
        version = current(index, record)
        stale = record["input_version"] != version
        evidence_ids = cited_ids(Design.model_validate(record["design"]))
        return {**record, "current_input_version": version, "stale": stale,
            "artifact_completeness": artifact_completeness(record["design"]),
            "evidence_references": index.evidence_references(evidence_ids),
                "outline_drafting_allowed": bool(record.get("outline_drafting_allowed") and not stale
                                                 and not record["design"]["blockers"]), **GATES}

    def get(index, identifier, version=None):
        try:
            return store_for(service).get(identifier, index.model["package"]["sha256"], version)
        except KeyError:
            raise HTTPException(404, "Unknown redesign in this package") from None

    @app.get("/api/redesigns")
    def redesigns(workflow_id: str | None = None, scenario: Literal["original", "bronze"] | None = None,
                  offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        index = service.require()
        if workflow_id is not None:
            service.workflow(index, workflow_id)
        rows = store_for(service).list(index.model["package"]["sha256"], workflow_id, scenario)
        items = [display(index, get(index, row["id"], row["version"])) for row in rows[offset:offset + limit]]
        return {"items": items, "total": len(rows), "offset": offset, "limit": limit,
                "truncated": offset + limit < len(rows), **GATES}

    @app.get("/api/redesigns/{redesign_id}")
    def redesign(redesign_id: str, version: int | None = Query(default=None, ge=1), draft_jobs: bool = False):
        index = service.require()
        value = display(index, get(index, redesign_id, version))
        value['latest_version'] = get(index, redesign_id)['version']
        if draft_jobs:
            from workbench.draft_artifact_reads import matching_jobs
            paths = sorted((service.state / 'jobs').glob('*.json'))
            if len(paths) > 2000:
                raise HTTPException(422, 'Saved writer receipt bound exceeded')
            jobs = []
            for path in paths:
                if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                    raise HTTPException(409, 'Invalid saved writer receipt')
                jobs.append(json.loads(path.read_bytes()))
            value['draft_jobs'] = matching_jobs(value, jobs)
        return value

    @app.delete("/api/redesigns/{redesign_id}")
    def delete_redesign(redesign_id: str, body: DeleteDesignRequest):
        """Delete one explicitly confirmed saved version and its generated draft files."""
        if body.consent is not True:
            raise HTTPException(403, "Explicit delete confirmation is required")
        index = service.require()
        package_hash = index.model["package"]["sha256"]
        store = store_for(service)
        with service.lock:
            try:
                record = store.get(redesign_id, package_hash, body.version)
            except KeyError:
                raise HTTPException(404, "Saved design version not found in this package") from None
            if record.get("id") != redesign_id or record.get("version") != body.version:
                raise HTTPException(409, "Saved design scope changed; refresh before deleting")

            jobs_dir = service.state / "jobs"
            job_paths = sorted(jobs_dir.glob("*.json"))
            if len(job_paths) > 2000:
                raise HTTPException(422, "Saved writer inventory exceeds the safe delete bound")
            related_jobs = []
            for path in job_paths:
                if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                    raise HTTPException(409, "Saved writer inventory is invalid; no design was deleted")
                try:
                    job_record = json.loads(path.read_bytes())
                except (OSError, ValueError):
                    continue
                if (isinstance(job_record, dict) and path.stem == job_record.get("id")
                        and job_record.get("kind") == "spark_draft"
                        and job_record.get("package_hash") == package_hash
                        and job_record.get("design_id") == redesign_id
                        and job_record.get("design_version") == body.version):
                    if job_record.get("status") in {"queued", "running", "cancelling"}:
                        raise HTTPException(409, "A writer for this design is still active; wait for its terminal status before deleting")
                    related_jobs.append(path)

            root = service.root.resolve()
            version_dir = root / "generated-drafts" / package_hash / redesign_id / str(body.version)
            artifact_dirs = []
            if version_dir.exists():
                for ancestor in (version_dir, *version_dir.parents):
                    if ancestor == root.parent:
                        break
                    if ancestor.is_symlink():
                        raise HTTPException(409, "Generated draft path contains a symlink; no design was deleted")
                if not version_dir.resolve().is_relative_to(root) or not version_dir.is_dir():
                    raise HTTPException(409, "Generated draft path is outside the selected package; no design was deleted")
                children = list(version_dir.iterdir())
                if any(not child.is_dir() or child.is_symlink()
                       or not re.fullmatch(r"[a-f0-9]{32}", child.name) for child in children):
                    raise HTTPException(409, "Generated draft inventory is invalid; no design was deleted")
                for child in children:
                    for descendant in child.rglob("*"):
                        if descendant.is_symlink():
                            raise HTTPException(409, "Generated draft inventory contains a symlink; no design was deleted")
                    artifact_dirs.append(child)

            targets = artifact_dirs + related_jobs
            quarantine = service.state / (".delete-" + uuid4().hex)
            moved = []
            try:
                if targets:
                    quarantine.mkdir(exist_ok=False)
                    for number, target in enumerate(targets):
                        destination = quarantine / str(number)
                        os.replace(target, destination)
                        moved.append((destination, target))
                deleted = store.delete_version(redesign_id, package_hash, body.version,
                                               body.expected_review_version)
            except Conflict as exc:
                for source, target in reversed(moved):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                shutil.rmtree(quarantine, ignore_errors=True)
                raise HTTPException(409, str(exc)) from None
            except Exception:
                for source, target in reversed(moved):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                shutil.rmtree(quarantine, ignore_errors=True)
                raise HTTPException(409, "Delete was not confirmed; stored data was restored where possible") from None
            try:
                if quarantine.exists():
                    shutil.rmtree(quarantine)
                cleanup_pending = False
            except OSError:
                cleanup_pending = True
            return {**deleted, "writer_jobs_removed": len(related_jobs),
                    "artifact_runs_removed": len(artifact_dirs), "cleanup_pending": cleanup_pending,
                    "parent_agent_history_preserved": True, "source_package_preserved": True}

    @app.post("/api/redesigns/{redesign_id}/reviews", status_code=201)
    def review(redesign_id: str, body: Review):
        index = service.require()
        with service.lock:
            record = get(index, redesign_id)
            # Identity comes exclusively from the hosted server action context.
            from workbench.hosted import _CURRENT
            identity = _CURRENT.get(None)
            actor = {"actor": identity.user_id if identity else "local-reviewer",
                     "actor_authenticated": identity is not None,
                     "authentication": "databricks_obo" if identity else "local_label_not_production_auth"}
            try:
                event = store_for(service).review(redesign_id, index.model["package"]["sha256"], body,
                                                   current(index, record), actor)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from None
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from None
            return {"event": event, "redesign": display(index, get(index, redesign_id)), **GATES}