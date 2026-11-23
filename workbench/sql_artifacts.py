"""Pure, general canonical-XML SQL draft compiler. No Spark, auth or app execution.

API: compile_sql_artifacts(model, workflow_id, source_dialect=...,
                          source_dialects={canonical_node_id: dialect})
returns Compilation (typed tasks/SQL inventory, JSON manifest, text artifacts).
export_sql_artifacts(result, new_directory) is the only filesystem operation.

SQLGlot validates/transpiles syntax, NOT data parity or full Informatica semantics.
Every mapping graph is retained but its non-SQL implementation remains blocked.
Only explicit calls to exported functions can run SQL, outside this compiler.
The root requirements declare sqlglot; workbench/requirements.txt does not. Hosted
integration must resolve that dependency separately. This module changes neither.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from workbench.domain import _RELATIONS
from workbench.understanding import _INLINE_SECRET, REDACTED


class SQLRole(str, Enum):
    PRE = "pre_sql"
    SOURCE = "source_query"
    LOOKUP = "lookup_query"
    TRANSFORM = "transformation_sql"
    TARGET = "target_override"
    POST = "post_sql"
    UNKNOWN = "unknown_sql"


_PHASE = {SQLRole.PRE: 0, SQLRole.SOURCE: 1, SQLRole.LOOKUP: 2,
          SQLRole.TRANSFORM: 3, SQLRole.TARGET: 4, SQLRole.POST: 5, SQLRole.UNKNOWN: 6}
_VENDORS = {"oracle": "oracle", "microsoftsqlserver": "tsql", "sqlserver": "tsql",
            "sybase": "tsql", "db2": "db2", "teradata": "teradata",
            "postgresql": "postgres", "postgres": "postgres", "mysql": "mysql",
            "snowflake": "snowflake", "spark": "spark", "databricks": "databricks",
            "redshift": "redshift"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _hash(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _order(node: dict) -> tuple:
    # Canonical understanding sorts nodes by content hash, not XML order. Compare
    # numeric locator ordinals: [2] precedes [10]. Retain arrays without sorting.
    locator = node.get("evidence", {}).get("locator", "")
    return tuple((part.split("[")[0], int(part.split("[")[1].rstrip("]")))
                 if re.fullmatch(r"[^\[]+\[\d+\]", part) else (part, 0)
                 for part in locator.split("/")), node["id"]


@dataclass
class SQLInventory:
    id: str
    node_id: str
    task_ids: list[str]
    role: SQLRole
    phase: int
    ordinal: int
    property_name: str
    origin: str
    original_sql: str
    original_sha256: str
    evidence: dict
    context: dict = field(default_factory=dict)
    source_dialect: str | None = None
    status: str = "pending"
    statements: list[dict] = field(default_factory=list)
    bindings: dict[str, dict] = field(default_factory=dict)
    planned_artifacts: list[str] = field(default_factory=list)
    emitted_artifacts: list[str] = field(default_factory=list)
    blocker_codes: list[str] = field(default_factory=list)


@dataclass
class TaskInventory:
    node_id: str
    kind: str
    name: str
    enabled: str
    closure_node_ids: list[str]
    sql_ids: list[str]
    mapping_ids: list[str]
    phase_plan: list[dict]
    status: str = "unsupported"
    blocker_codes: list[str] = field(default_factory=list)


@dataclass
class Compilation:
    workflow_id: str
    tasks: list[TaskInventory]
    sql: list[SQLInventory]
    manifest: dict
    artifacts: dict[str, str]


def _role(name: str, node: dict) -> SQLRole:
    name = re.sub(r"[^a-z0-9]", "", name.lower())
    if name.startswith(("presql", "sessionpresql", "presessionsql", "pretargetsql")):
        return SQLRole.PRE
    if name.startswith(("postsql", "sessionpostsql", "postsessionsql", "posttargetsql")):
        return SQLRole.POST
    if "lookup" in name and "sql" in name:
        return SQLRole.LOOKUP
    if "target" in name and "sql" in name:
        return SQLRole.TARGET
    if name in {"updateoverride", "insertoverride", "deleteoverride"}:
        return SQLRole.TARGET
    attributes = node.get("attributes", {})
    node_type = " ".join(str(attributes.get(key, "")) for key in
                         ("TRANSFORMATIONTYPE", "TRANSFORMATION_TYPE", "TYPE", "SUBTYPE")).lower()
    if name in {"sqlquery", "sourcequery", "sourcesql", "sourcefilteroverride"}:
        if node["kind"] == "target" or "target" in node_type or "writer" in node_type:
            return SQLRole.TARGET
        if "lookup" in node_type:
            return SQLRole.LOOKUP
        if "sql transformation" in node_type or node_type.strip() == "sql":
            return SQLRole.TRANSFORM
        return SQLRole.SOURCE
    if name == "sqloverride":
        if node["kind"] == "target" or "target" in node_type or "writer" in node_type:
            return SQLRole.TARGET
        if "lookup" in node_type:
            return SQLRole.LOOKUP
        if "source" in node_type or "reader" in node_type:
            return SQLRole.SOURCE
    if name in {"sql", "sqlscript", "sqltransformationquery", "sqltransformation"}:
        return SQLRole.TRANSFORM
    return SQLRole.UNKNOWN


def _closure(start: str, outgoing: dict) -> set[str]:
    found, queue = {start}, deque([start])
    while queue:
        for target in outgoing.get(queue.popleft(), ()):
            if target not in found:
                found.add(target)
                queue.append(target)
    return found


def _dialect(node: dict, by_id: dict, outgoing: dict, explicit: Mapping[str, str],
             default: str | None, role: SQLRole) -> str | None:
    if node["id"] in explicit:
        return explicit[node["id"]]
    if default is not None:
        return default
    current = node
    seen = set()
    while current and current["id"] not in seen:
        seen.add(current["id"])
        vendors = set()
        for candidate_id in _closure(current["id"], outgoing):
            candidate = by_id.get(candidate_id, {})
            # Source SQL must not silently inherit a different target vendor.
            if candidate.get("kind") == ("target" if role in {SQLRole.SOURCE, SQLRole.LOOKUP} else "source"):
                continue
            attrs = candidate.get("attributes", {})
            vendor = attrs.get("DATABASETYPE", attrs.get("DBTYPE", ""))
            if vendor:
                vendors.add(_VENDORS.get(re.sub(r"[^a-z0-9]", "", vendor.lower()), "unknown"))
        if vendors:
            return next(iter(vendors)) if len(vendors) == 1 and "unknown" not in vendors else None
        current = by_id.get(current.get("parent_id"))
        if current and current["kind"] in {"folder", "repository", "powermart"}:
            break
    return None


def _translate(item: SQLInventory) -> None:
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope

    trees = sqlglot.parse(item.original_sql, read=item.source_dialect,
                         error_level=sqlglot.ErrorLevel.RAISE)
    trees = [tree for tree in trees if tree is not None]
    if not trees:
        raise ValueError("SQL contains no statements")
    query_role = item.role in {SQLRole.SOURCE, SQLRole.LOOKUP, SQLRole.TRANSFORM}
    if query_role and (len(trees) != 1 or not isinstance(trees[0], exp.Query)):
        raise ValueError("Query role requires one read-only query")
    if item.role == SQLRole.UNKNOWN:
        raise ValueError("Unknown SQL role requires a reviewed disposition")
    for ordinal, tree in enumerate(trees):
        query = isinstance(tree, exp.Query)
        if not query and not isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
            raise ValueError("Only queries and explicit INSERT/UPDATE/DELETE commands are supported")
        # Reject executable extensions, unknown UDFs, file readers and SELECT INTO.
        for part in tree.walk():
            if isinstance(part, (exp.Command, exp.Into, exp.Anonymous, exp.Placeholder, exp.Parameter)):
                raise ValueError("Unsupported executable extension or unresolved parameter")
            if part is not tree and isinstance(part, (exp.DDL, exp.DML)):
                raise ValueError("Nested executable statements are unsupported")
            if isinstance(part, exp.Column) and (part.db or part.catalog):
                raise ValueError("Qualified column requires a reviewed alias contract")
            if isinstance(part, exp.Func) and any(word in part.sql_name().upper()
                                                for word in ("READ_", "SCAN", "HTTP", "JDBC")):
                raise ValueError("External reader requires an explicit ingestion implementation")
        if "$$" in item.original_sql or REDACTED in item.original_sql:
            raise ValueError("Unresolved or redacted SQL")
        virtual, source_tables = set(), set()
        for scope in traverse_scope(tree):
            for table, source in scope.selected_sources.values():
                if isinstance(source, Scope) and isinstance(table, exp.Table):
                    virtual.add(id(table))
                elif isinstance(source, exp.Table):
                    source_tables.add(id(source))
        write_keys = []
        target = tree.this if not query else None
        if isinstance(target, exp.Schema):
            target = target.this
        if not query and not isinstance(target, exp.Table):
            raise ValueError("Command requires a simple explicit target table")
        for table in list(tree.find_all(exp.Table)):
            if id(table) in virtual:
                continue
            if not isinstance(table.this, exp.Identifier) or any(
                    not isinstance(part, exp.Identifier) for part in table.parts):
                raise ValueError("Dynamic or external table expression is unsupported")
            original = table.copy()
            original.set("alias", None)
            identity = original.sql(dialect=item.source_dialect)
            key = "table_" + _hash(identity)[:20]
            marker = "__WB_BIND_" + _hash(identity) + "__"
            if marker in item.original_sql:
                raise ValueError("Binding marker collision")
            item.bindings[key] = {"original_identifier": identity, "marker": marker,
                                  "contract": "explicit_three_part_identifier_no_default"}
            if table is target:
                write_keys.append(key)
            if not table.alias and id(table) in source_tables:
                # Preserve src.column qualifiers after replacing FROM src.
                table.set("alias", exp.TableAlias(this=table.this.copy()))
            if table is target:
                for column in tree.find_all(exp.Column):
                    if column.table == table.name:
                        raise ValueError("Qualified DML target requires a reviewed alias contract")
            # Aliases stay intact. Nothing maps original tables to physical names.
            table.set("this", exp.to_identifier(marker))
            table.set("db", None)
            table.set("catalog", None)
        if not query and not write_keys:
            raise ValueError("Unresolved write target")
        # Exercise the documented transpile API with strict unsupported handling.
        translated = sqlglot.transpile(tree.sql(dialect=item.source_dialect),
                                      read=item.source_dialect, write="spark",
                                      error_level=sqlglot.ErrorLevel.RAISE,
                                      unsupported_level=sqlglot.ErrorLevel.RAISE)[0]
        sqlglot.parse_one(translated, read="spark", error_level=sqlglot.ErrorLevel.RAISE)
        item.statements.append({"ordinal": ordinal, "spark_sql_template": translated,
                                "kind": "query" if query else "command",
                                "write_binding_keys": write_keys})


_RUNTIME = '''from __future__ import annotations
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Mapping
if TYPE_CHECKING:
    from pyspark.sql import DataFrame

@dataclass(frozen=True)
class ExecutionContext:
    spark: Any
    acknowledged_draft_execution: bool
    allowed_write_namespaces: frozenset[tuple[str, str]]

def _validated(bindings: Mapping[str, str]) -> dict[str, tuple[str, str, str]]:
    if set(bindings) != set(BINDING_CONTRACT):
        raise ValueError("Exact explicit identifier bindings required, no defaults")
    result = {}
    for key, value in bindings.items():
        if not isinstance(value, str):
            raise ValueError("Bindings must be three-part identifier strings")
        parts = value.split(".")
        if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
            raise ValueError("Only three-part simple identifiers are accepted")
        result[key] = tuple(parts)
    return result

def _render(template: str, validated: dict) -> str:
    for key, contract in BINDING_CONTRACT.items():
        template = template.replace(contract["marker"], ".".join("`" + p + "`" for p in validated[key]))
    return template

'''


def _program(item: SQLInventory, workflow_id: str) -> str:
    code = "# Databricks notebook source\n# DRAFT: syntax translation only, no parity or full mapping coverage.\n"
    code += _RUNTIME
    code += "WORKFLOW_ID = " + repr(workflow_id) + "\n"
    code += "SQL_ROLE_ID = " + repr(item.id) + "\n"
    code += "BINDING_CONTRACT = " + repr(item.bindings) + "\n"
    code += "STATEMENTS = " + repr(item.statements) + "\n\n"
    if item.role in {SQLRole.SOURCE, SQLRole.LOOKUP, SQLRole.TRANSFORM}:
        name = {SQLRole.SOURCE: "source_query", SQLRole.LOOKUP: "lookup_query",
                SQLRole.TRANSFORM: "transformation_query"}[item.role]
        code += f'''def {name}(spark: Any, *, bindings: Mapping[str, str]) -> DataFrame:
    """Return a DataFrame, never materialize a target or invoke automatically."""
    validated = _validated(bindings)
    return spark.sql(_render(STATEMENTS[0]["spark_sql_template"], validated))
'''
    else:
        code += '''def execute_commands(context: ExecutionContext, *, bindings: Mapping[str, str]) -> list[Any]:
    """Explicit draft-only invocation. Caller owns transactions and phase ordering.

    All write targets must be in explicitly approved isolated draft namespaces.
    Namespace approval is a caller contract, not production authentication.
    """
    if not isinstance(context, ExecutionContext) or context.acknowledged_draft_execution is not True:
        raise ValueError("Explicit execution context and draft acknowledgement required")
    validated = _validated(bindings)
    for statement in STATEMENTS:
        for key in statement["write_binding_keys"]:
            if validated[key][:2] not in context.allowed_write_namespaces:
                raise ValueError("Write target outside explicitly approved draft namespaces")
    # Validate the entire batch before executing any statement. No auto commit/retry.
    return [context.spark.sql(_render(s["spark_sql_template"], validated)) for s in STATEMENTS]
'''
    return code


def _notebook(item: SQLInventory, workflow_id: str, program: str) -> str:
    cells = []
    title = ("# SQL draft: " + item.role.value + "\nSyntax translation only, not parity. "
             "Functions are defined, not called. Full mapping/control implementation is unsupported.")
    for ordinal, (kind, source) in enumerate((("markdown", title), ("code", program))):
        cell = {"cell_type": kind, "id": _hash([workflow_id, item.id, ordinal])[:32],
                "metadata": {"id": _hash([workflow_id, item.id, ordinal])[:32],
                             "language": "markdown" if kind == "markdown" else "python",
                             "workflow_id": workflow_id, "sql_role_id": item.id},
                "source": source.splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    return _json({"nbformat": 4, "nbformat_minor": 5, "cells": cells,
                  "metadata": {"workflow_id": workflow_id, "sql_role_id": item.id,
                               "language_info": {"name": "python"},
                               "kernelspec": {"name": "python3", "language": "python",
                                              "display_name": "Python 3"}}})


def compile_sql_artifacts(model: dict, workflow_id: str, *, source_dialect: str | None = None,
                          source_dialects: Mapping[str, str] | None = None) -> Compilation:
    """Compile an isolated draft from canonical understanding.analyze_package output.

    Does not mutate model, create files, run generated code, connect or deploy.
    Missing references block affected tasks only. Original SQL remains in manifest
    even when parsing fails; there is never a runnable original-SQL fallback.
    Canonical parser-redacted text is the available original, not recovered secrets.
    Explicit dialects override XML vendor inference. No dialect is guessed.
    """
    by_id = {n["id"]: n for n in model["nodes"]}
    if len(by_id) != len(model["nodes"]):
        raise ValueError("Duplicate canonical node IDs")
    if len({e["id"] for e in model["edges"]}) != len(model["edges"]):
        raise ValueError("Duplicate canonical edge IDs")
    if by_id.get(workflow_id, {}).get("kind") != "workflow":
        raise ValueError("workflow_id must be a canonical workflow ID, not a name")
    outgoing: dict[str, list[str]] = defaultdict(list)
    session_instance_refs: dict[str, list[dict]] = defaultdict(list)
    for edge in model["edges"]:
        if edge["kind"] in _RELATIONS:
            outgoing[edge["source"]].append(edge["target"])
        if edge["kind"] == "session_instance":
            session_instance_refs[edge["source"]].append(edge)
    selected = _closure(workflow_id, outgoing)
    scoped = sorted((n for n in model["nodes"] if n["id"] in selected), key=_order)
    scoped_edges = [edge for edge in model["edges"] if edge["source"] in selected]
    # Check only data this workflow can export. Serializing the entire estate
    # for every workflow both rejects unrelated secrets and makes a small SQL
    # inventory pay the cost of the full multi-hundred-thousand-node package.
    safety_scope = {"nodes": scoped, "edges": scoped_edges,
                    "diagnostics": [d for d in model.get("diagnostics", [])
                                    if d.get("object_id") in selected]}
    # Canonical XML parsing deliberately replaces source credentials with this
    # sentinel. It is safe provenance text, not an actual credential value.
    safety_text = _json(safety_scope).replace(REDACTED, "REDACTED_VALUE")
    if _INLINE_SECRET.search(safety_text):
        raise ValueError("Selected workflow contains credential-bearing text and is not safely canonicalized")
    tasks = []
    task_scopes = {}
    for node in scoped:
        if node["kind"] in {"taskinstance", "session", "task"}:
            closure = _closure(node["id"], outgoing)
            task_scopes[node["id"]] = closure
            tasks.append(TaskInventory(node["id"], node["kind"], node["name"],
                                       node.get("attributes", {}).get("ISENABLED", "unknown"),
                                       sorted(closure), [], [], []))
    blockers: list[dict] = []

    def block(code: str, node_id: str, *, sql_id: str | None = None, detail: str = "") -> None:
        owners = [task.node_id for task in tasks if node_id in task_scopes[task.node_id]]
        blockers.append({"code": code, "node_id": node_id, "task_ids": owners,
                         "sql_id": sql_id, "detail": detail})
        for task in tasks:
            if task.node_id in owners and code not in task.blocker_codes:
                task.blocker_codes.append(code)

    for missing in sorted(selected - by_id.keys()):
        block("MISSING_CANONICAL_REFERENCE", missing)
    for node in scoped:
        if node["kind"] == "unresolved_reference":
            block("UNRESOLVED_REFERENCE", node["id"], detail=str(node.get("attributes", {}).get("status", "unresolved")))
        if node["kind"] in {"mapping", "mapplet"}:
            block("UNSUPPORTED_MAPPING_IMPLEMENTATION", node["id"],
                  detail="Full ports/connectors/properties retained in graph; non-SQL code and target writer are not implemented.")
        if any("embedded_code" in p for p in node.get("properties", [])):
            block("EMBEDDED_JAVA_REQUIRES_REVIEW", node["id"],
                  detail="Sanitized Java and dynamic SQL expressions are supplemental property evidence, not standalone SQL. Preserve polling, parameters and failure semantics separately.")
        if node["kind"] == "task" and node.get("attributes", {}).get("TYPE", "").lower() != "session":
            block("UNSUPPORTED_CONTROL_IMPLEMENTATION", node["id"],
                  detail="Control metadata retained, not converted to executable orchestration.")
    inventory = []
    for node in scoped:
        occurrences = [("property", index, p.get("attributes", {}).get("NAME", ""),
                        p.get("attributes", {}).get("VALUE", p.get("text", "")), p.get("evidence", {}))
                       for index, p in enumerate(node.get("properties", []))]
        occurrences += [("attribute", index, name, value, node.get("evidence", {}))
                        for index, (name, value) in enumerate(node.get("attributes", {}).items())]
        for origin, ordinal, name, text, evidence in occurrences:
            role = _role(name, node)
            if "sql" not in name.lower() and role == SQLRole.UNKNOWN:
                continue
            # SQL-affecting settings are retained too, but are never guessed to be
            # statements. Empty SQL properties remain visible as empty evidence.
            original = str(text)
            item_id = _hash([workflow_id, node["id"], origin, ordinal, name])
            item = SQLInventory(item_id, node["id"],
                                [t.node_id for t in tasks if node["id"] in task_scopes[t.node_id]],
                                role, _PHASE[role], ordinal, name, origin, original,
                                sha256(original.encode("utf-8")).hexdigest(), deepcopy(evidence))
            ancestors = []
            parent = node
            while parent and parent["id"] not in ancestors:
                ancestors.append(parent["id"])
                parent = by_id.get(parent.get("parent_id"))
            item.context = {"owner_kind": node["kind"], "ancestor_ids": ancestors,
                            "scope": deepcopy(node.get("scope", {})),
                            "disposition": "session_override" if any(
                                by_id[a]["kind"] == "session" for a in ancestors) else "mapping_or_definition",
                            "references": deepcopy(session_instance_refs.get(node["id"], []))}
            item.source_dialect = _dialect(node, by_id, outgoing, source_dialects or {}, source_dialect, role)
            inventory.append(item)
    artifacts = {}
    for item in inventory:
        if not item.original_sql.strip():
            item.status = "empty"
            continue
        stem = "sql/" + item.role.value + "_" + item.id
        item.planned_artifacts = [stem + ".py", stem + ".ipynb"]
        reason = None
        if item.source_dialect is None:
            reason = "SOURCE_DIALECT_REQUIRED"
        else:
            try:
                _translate(item)
            except ImportError:
                reason = "SQLGLOT_DEPENDENCY_MISSING"
            except Exception as error:
                reason = "SQL_TRANSLATION_BLOCKED"
                # Do not embed exception strings, which may contain SQL literals.
                item.blocker_codes.append(type(error).__name__)
        if reason:
            item.status = "blocked"
            item.blocker_codes.append(reason)
            item.statements.clear()
            item.bindings.clear()
            block(reason, item.node_id, sql_id=item.id)
            continue
        item.status = "draft_syntax_only"
        item.emitted_artifacts = list(item.planned_artifacts)
        program = _program(item, workflow_id)
        artifacts[stem + ".py"] = program
        artifacts[stem + ".ipynb"] = _notebook(item, workflow_id, program)
    for task in tasks:
        owned = [item for item in inventory if task.node_id in item.task_ids]
        task.sql_ids = [item.id for item in owned]
        task.mapping_ids = [n["id"] for n in scoped if n["kind"] in {"mapping", "mapplet"}
                            and n["id"] in task_scopes[task.node_id]]
        task.phase_plan = [{"phase": role.value, "sql_ids": [item.id for item in owned if item.role == role]}
                           for role in sorted(SQLRole, key=lambda role: (_PHASE[role], role.value))]
        task.status = "blocked" if task.blocker_codes else "partial_sql_draft" if owned else "unsupported"
        if not any(item.emitted_artifacts for item in owned):
            block("NO_SQL_IMPLEMENTATION", task.node_id,
                  detail="No synthetic notebook generated; original graph retained.")
            task.status = "blocked"
    graph_name = "graph_" + _hash(workflow_id) + ".json"
    artifacts[graph_name] = _json({"workflow_id": workflow_id, "package": model.get("package", {}),
                                  "nodes": scoped,
                                  "edges": [e for e in model["edges"] if e["source"] in selected],
                                  "diagnostics": [d for d in model.get("diagnostics", [])
                                                  if d.get("object_id") in selected]})
    manifest = {"schema_version": "sql-draft-v1", "workflow_id": workflow_id,
                "package": deepcopy(model.get("package", {})), "status": "isolated_draft_incomplete",
                "release_ready": False, "execution_permitted": False,
                "translation_claim": "syntax_only_not_semantic_or_data_parity",
                "ordering_contract": "Inventory preserves each owner's property array order, with owners in numeric locator order. Exact cross-owner XML interleaving is unavailable in the canonical model. Phase plan is not orchestration; overrides are retained, never silently selected.",
                "limitations": ["No non-SQL transformation or mapping writer implementation.",
                                "Session overrides and mapping defaults require explicit reconciliation.",
                                "No automatic control-flow, transaction, disabled-task or schedule execution.",
                                "Oracle DATE/TO_DATE, null, precision and timezone parity require independent tests.",
                                "Caller must bind identifiers to approved draft datasets; no physical defaults."],
                "dependency_notes": ["sqlglot is declared in root requirements.txt but missing from workbench/requirements.txt used for hosted packaging."],
                "tasks": [asdict(task) for task in tasks], "sql_inventory": [asdict(item) for item in inventory],
                "blockers": blockers, "graph_artifact": graph_name,
                "artifacts": [{"path": name, "sha256": sha256(text.encode("utf-8")).hexdigest()}
                              for name, text in artifacts.items()]}
    artifacts["manifest.json"] = _json(manifest)
    return Compilation(workflow_id, tasks, inventory, manifest, artifacts)


def export_sql_artifacts(result: Compilation, new_directory: Path) -> Path:
    """Write a NEW isolated directory only, never overwrite a release or notebook."""
    destination = Path(new_directory)
    # Validate names before creating anything, including caller-mutated results.
    for name in result.artifacts:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("Artifact path must be relative and confined")
    destination.mkdir(exist_ok=False)
    for name, text in result.artifacts.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return destination