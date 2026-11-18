"""Read-only catalog of the actually compiled agent tools and trusted skill sources.

This module parses only the known agent/tool source files and reads only the
fixed workbench/skills tree. It never imports or instantiates a model, searches
the web, or mutates skills/knowledge.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException, Query, Request

from workbench.agents import ROLES

WORKBENCH = Path(__file__).resolve().parent
SKILLS_ROOT = WORKBENCH / "skills"
_ANALYSIS_PATH = WORKBENCH / "agents.py"
_WRITER_PATH = WORKBENCH / "spark_codegen.py"
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_SKILL_TOTAL_BYTES = 4 * 1024 * 1024
_MAX_PAGE_SIZE = 100
_KNOWLEDGE_ROLES = ("architecture", "supervisor", "validation")
_WRITER_ROLE = "spark_codegen"
_WRITER_LABEL = "Artifact writer (separate code-generation run)"
_HARNESS_NAMES = ("ls", "read_file", "glob", "grep")


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _read_trusted(path: Path, allowed_root: Path) -> tuple[bytes, str]:
    resolved = path.resolve(strict=True)
    root = allowed_root.resolve(strict=True)
    if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
        raise ValueError("Trusted source path failed its scope check")
    raw = resolved.read_bytes()
    if len(raw) > _MAX_SOURCE_BYTES:
        raise ValueError("Trusted source exceeds the catalog bound")
    return raw, raw.decode("utf-8", errors="strict")


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [item.id for item in node.elts if isinstance(item, ast.Name)]
    if isinstance(node, ast.Name):
        return [node.id]
    return []


def _condition_roles(test: ast.AST, roles: tuple[str, ...]) -> set[str] | None:
    """Resolve only simple role comparisons; never evaluate source expressions."""
    if isinstance(test, ast.BoolOp):
        parts = [_condition_roles(part, roles) for part in test.values]
        if any(part is None for part in parts):
            return None
        if isinstance(test.op, ast.And):
            return set.intersection(*(part for part in parts)) if parts else set()
        return set.union(*(part for part in parts)) if parts else set()
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        nested = _condition_roles(test.operand, roles)
        return set(roles) - nested if nested is not None else None
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if isinstance(left, ast.Name) and left.id == "role":
        if isinstance(op, ast.In):
            values = _names(right) if isinstance(right, (ast.List, ast.Tuple, ast.Set)) else []
            values.extend([item.value for item in getattr(right, "elts", [])
                           if isinstance(item, ast.Constant) and isinstance(item.value, str)])
            return set(values) & set(roles)
        if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and isinstance(right.value, str):
            matched = {right.value} & set(roles)
            return set(roles) - matched if isinstance(op, ast.NotEq) else matched
    if isinstance(right, ast.Name) and right.id == "role" and isinstance(left, ast.Constant) and isinstance(left.value, str):
        matched = {left.value} & set(roles)
        if isinstance(op, ast.Eq):
            return matched
        if isinstance(op, ast.NotEq):
            return set(roles) - matched
    return None


def _tool_definitions(function: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    found = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any((isinstance(decorator, ast.Name) and decorator.id == "tool")
               or (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
                   and decorator.func.id == "tool") for decorator in node.decorator_list):
            found[node.name] = node
    return found


def _analysis_roles(function: ast.FunctionDef, names: set[str]) -> tuple[dict[str, set[str]], dict[str, str]]:
    exposures = {name: set() for name in names}
    conditions: dict[str, str] = {}

    def collect(statements: list[ast.stmt], active: set[str], condition: str = "") -> None:
        for statement in statements:
            if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "common"
                                                         for target in statement.targets):
                for name in _names(statement.value):
                    if name in exposures:
                        exposures[name].update(active)
                        if condition:
                            conditions[name] = condition
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id == "common":
                for name in _names(statement.value):
                    if name in exposures:
                        exposures[name].update(active)
                        if condition:
                            conditions[name] = condition
            elif isinstance(statement, ast.If):
                matched = _condition_roles(statement.test, ROLES)
                if matched is None:
                    rendered = ast.unparse(statement.test)[:200]
                    collect(statement.body, active, rendered if not condition else f"{condition}; {rendered}")
                    if statement.orelse:
                        collect(statement.orelse, active, rendered if not condition else f"{condition}; else {rendered}")
                else:
                    collect(statement.body, active & matched, condition)
                    collect(statement.orelse, active - matched, condition)
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "common":
                    added = []
                    if call.func.attr == "append" and call.args:
                        added = _names(call.args[0])
                    elif call.func.attr == "extend" and call.args:
                        added = _names(call.args[0])
                    for name in added:
                        if name in exposures:
                            exposures[name].update(active)
                            if condition:
                                conditions[name] = condition

    collect(function.body, set(ROLES))
    return exposures, conditions


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments = ast.unparse(node.args)
    return f"{node.name}({arguments})"


def _tool_item(name: str, node: ast.FunctionDef | ast.AsyncFunctionDef, path: str,
               file_hash: str, category: str, roles: list[str], condition: str | None = None) -> dict[str, Any]:
    doc = ast.get_docstring(node, clean=True) or "No tool docstring is declared in source."
    description = next((line.strip() for line in doc.splitlines() if line.strip()), "No description")
    return {
        "id": f"{category}:{name}", "name": name, "category": category,
        "description": description[:400], "signature": _signature(node)[:400],
        "roles": roles, "conditional": bool(condition), "condition": condition,
        "source": {"path": path, "line_start": node.lineno, "line_end": node.end_lineno,
                   "file_sha256": file_hash,
                   "snippet_sha256": _digest(doc.encode("utf-8"))},
    }


def _tools_catalog() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    analysis_raw, analysis_text = _read_trusted(_ANALYSIS_PATH, WORKBENCH)
    writer_raw, writer_text = _read_trusted(_WRITER_PATH, WORKBENCH)
    analysis_tree, writer_tree = ast.parse(analysis_text), ast.parse(writer_text)
    role_function = next((node for node in ast.walk(analysis_tree)
                          if isinstance(node, ast.FunctionDef) and node.name == "role_tools"), None)
    if role_function is None:
        raise ValueError("Agent tool catalog declaration was not found")
    analysis_defs = _tool_definitions(role_function)
    exposures, conditions = _analysis_roles(role_function, set(analysis_defs))
    result = []
    for name, node in analysis_defs.items():
        roles = sorted(exposures[name])
        if roles:
            result.append(_tool_item(name, node, "workbench/agents.py", _digest(analysis_raw),
                                     "analysis", roles, conditions.get(name)))

    writer_defs = {}
    for parent in ast.walk(writer_tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        definitions = _tool_definitions(parent)
        for statement in ast.walk(parent):
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "tools" for t in statement.targets):
                selected = set(_names(statement.value))
                if selected:
                    writer_defs.update({name: definitions[name] for name in selected if name in definitions})
    for name, node in writer_defs.items():
        result.append(_tool_item(name, node, "workbench/spark_codegen.py", _digest(writer_raw),
                                 "writer", [_WRITER_ROLE]))

    analysis_names = set(analysis_defs)
    writer_names = set(writer_defs)
    harness_roles = list(ROLES) + [_WRITER_ROLE]
    for name in _HARNESS_NAMES:
        result.append({"id": f"harness:{name}", "name": name, "category": "harness",
                       "description": "Deep Agents filesystem harness operation, not a domain @tool function.",
                       "signature": name, "roles": harness_roles,
                       "conditional": False, "condition": None,
                       "source": {"path": "Deep Agents runtime harness", "line_start": None,
                                  "line_end": None, "file_sha256": None, "snippet_sha256": None}})
    result.append({"id": "harness:task", "name": "task", "category": "harness",
                   "description": "Conditional Deep Agents delegation harness, supplied only when supervisor subagents are compiled.",
                   "signature": "task(...)", "roles": ["supervisor"], "conditional": True,
                   "condition": "compile_role('supervisor', specs)",
                   "source": {"path": "Deep Agents runtime harness", "line_start": None,
                              "line_end": None, "file_sha256": None, "snippet_sha256": None}})
    result.sort(key=lambda item: (item["category"], item["name"]))
    return result, {"analysis_unique_tools": len(analysis_names), "writer_unique_tools": len(writer_names),
                    "harness_operations": len(_HARNESS_NAMES) + 1,
                    "internet_search_tool_available": False,
                    "online_search_tool_available": False}


def _split_text(value: str, limit: int = 400) -> list[str]:
    text = re.sub(r"\s+", " ", value).strip()
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        boundary = text.rfind(" ", 0, limit + 1)
        if boundary < 1:
            boundary = limit
        chunks.append(text[:boundary].strip())
        text = text[boundary:].strip()
    return [chunk for chunk in chunks if chunk]


def _skill_snippet_id(path: str, heading: str, snippet_hash: str, occurrence: int) -> str:
    stable = _digest(f"{path}\0{heading}\0{snippet_hash}\0{occurrence}".encode("utf-8"))
    return f"skill:{stable}"


def _skill_sources() -> list[tuple[Path, list[str]]]:
    sources: list[tuple[Path, list[str]]] = []
    total_bytes = 0
    for role in (*ROLES, _WRITER_ROLE):
        root = (SKILLS_ROOT / role).resolve(strict=True)
        if role in ROLES:
            candidates = [root / "CONTEXT.md"]
            candidates.extend(sorted(root.glob("library/**/SKILL.md")))
            required = [root / "CONTEXT.md", root / "library" / "review" / "SKILL.md"]
        else:
            candidates = [root / "CONTEXT.md", root / "library" / "implementation" / "SKILL.md"]
            required = list(candidates)
        if any(not path.is_file() or path.is_symlink() for path in required):
            raise ValueError("A required compiled skill source is missing or unsafe")
        for path in candidates:
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError("A skill source symlink is not allowed")
            raw, _ = _read_trusted(path, SKILLS_ROOT)
            total_bytes += len(raw)
            if total_bytes > _MAX_SKILL_TOTAL_BYTES:
                raise ValueError("Skill source catalog exceeds its total bound")
            sources.append((path, [role]))
    shared = SKILLS_ROOT / "production-conversion-contract.md"
    raw, _ = _read_trusted(shared, SKILLS_ROOT)
    total_bytes += len(raw)
    if total_bytes > _MAX_SKILL_TOTAL_BYTES:
        raise ValueError("Skill source catalog exceeds its total bound")
    sources.append((shared, [*ROLES]))
    return sources


def _skill_items() -> list[dict[str, Any]]:
    observed_at = datetime.now(timezone.utc).isoformat()
    items: list[dict[str, Any]] = []
    for path, roles in _skill_sources():
        raw, content = _read_trusted(path, SKILLS_ROOT)
        lines = content.splitlines()
        file_hash = _digest(raw)
        relative = path.resolve().relative_to(WORKBENCH.parent.resolve()).as_posix()
        occurrences: dict[tuple[str, str], int] = {}
        blocks: list[tuple[int, int, str, str]] = []
        title = ""
        start = None
        paragraph: list[str] = []
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()[:180]
            if stripped:
                if start is None:
                    start = number
                paragraph.append(stripped)
            elif paragraph and start is not None:
                blocks.append((start, number - 1, title, " ".join(paragraph)))
                paragraph, start = [], None
        if paragraph and start is not None:
            blocks.append((start, len(lines), title, " ".join(paragraph)))
        for line_start, line_end, heading, paragraph_text in blocks:
            for snippet in _split_text(paragraph_text):
                snippet_hash = _digest(snippet.encode("utf-8"))
                key = (heading, snippet_hash)
                occurrence = occurrences.get(key, 0)
                occurrences[key] = occurrence + 1
                items.append({
                    "id": _skill_snippet_id(relative, heading, snippet_hash, occurrence),
                    "kind": "skill_snippet", "label": heading or path.stem,
                    "text": snippet, "roles": roles,
                    "source": {"path": relative, "line_start": line_start, "line_end": line_end,
                               "file_sha256": file_hash, "snippet_sha256": snippet_hash,
                               "external_url": None},
                    "freshness": {"state": "current_local_file_read", "observed_at": observed_at,
                                  "published_at": None, "updated_at": None, "expires_at": None,
                                  "note": "Local bundled source observed in this response; not an internet-currentness claim."},
                    "lifecycle": {"discard": "not_recorded", "superseded_by": "not_recorded"},
                })
    return items


def _knowledge_store(request: Request) -> tuple[str, Any | None]:
    app = request.app
    if getattr(app.state, "hosted_request_authenticated", False) is not True:
        return "not_connected", None
    provider = getattr(app.state, "knowledge_store_provider", None)
    service = getattr(app.state, "service", None)
    if not callable(provider) or service is None:
        return "not_connected", None
    from workbench.knowledge_store import project_for_root
    project = project_for_root(Path(service.root))
    if not project:
        return "not_connected", None
    if request.headers.get("X-Workbench-Project") != project:
        return "scope_mismatch", None
    try:
        snapshot = provider().load(project)
    except Exception:
        return "storage_read_failed", None
    return ("available", snapshot) if snapshot is not None else ("not_curated", None)


def _knowledge_items(request: Request) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    items = _skill_items()
    status, snapshot = _knowledge_store(request)
    knowledge_status: dict[str, Any] = {
        "status": status, "version": None, "graph_sha256": None,
        "feature_count": None, "node_count": 0, "edge_count": 0,
        "skill_snippet_count": len(items),
        "fresh_source_count": 0, "expired_source_count": 0,
        "latest_catalogue_guaranteed": False,
        "internet_search_tool_available": False,
        "source_fetching_performed": False,
        "discard_or_supersede_tracking": "not_present_in_current_knowledge_schema",
    }
    if snapshot is None:
        knowledge_status["freshness_note"] = (
            "No authenticated existing graph was read. Only checked-in skill files are listed; no local substitute or web search was used."
        )
        return items, knowledge_status, []

    value = snapshot.value
    raw_nodes = value["data"]["nodes"]
    doc_map = {node["id"]: node for node in raw_nodes if node["kind"] == "document"}
    now = datetime.now(timezone.utc)
    for node in raw_nodes:
        refs = [doc_map[identifier] for identifier in node.get("evidence_ids", []) if identifier in doc_map]
        if node["kind"] == "document" and node.get("source"):
            refs.append(node)
        source_refs = []
        freshness_states = []
        for document in refs:
            source = document.get("source") or {}
            expires = source.get("expires_at")
            expired = False
            if expires:
                try:
                    expired = datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now
                except (TypeError, ValueError):
                    expired = True
            freshness_states.append("expired" if expired else "fresh")
            source_refs.append({"label": document.get("label", "Official source"),
                "url": source.get("url"), "retrieved_at": source.get("retrieved_at"),
                "published_at": source.get("published_at"), "updated_at": source.get("updated_at"),
                "expires_at": expires, "source_sha256": source.get("sha256"),
                "section_sha256": source.get("section_sha256"), "excerpt": source.get("excerpt", "")})
        parts = _split_text(node.get("summary", "")) or [node.get("label", "")[:400]]
        for part_index, part in enumerate(parts):
            items.append({
                "id": f"knowledge:{node['id']}:{part_index}", "kind": "architecture_knowledge",
                "node_kind": node["kind"], "label": node.get("label", "")[:180],
                "text": part[:400], "roles": list(_KNOWLEDGE_ROLES), "release_state": node.get("release_state", "unknown"),
                "verification": node.get("verification", "unverified"), "source_refs": source_refs,
                "freshness": {"state": "expired" if "expired" in freshness_states else ("fresh_source_window" if source_refs else "no_source_window"),
                    "retrieved_at": max((ref["retrieved_at"] for ref in source_refs if ref["retrieved_at"]), default=None),
                    "published_at": next((ref["published_at"] for ref in source_refs if ref["published_at"]), None),
                    "updated_at": next((ref["updated_at"] for ref in source_refs if ref["updated_at"]), None),
                    "expires_at": min((ref["expires_at"] for ref in source_refs if ref["expires_at"]), default=None),
                    "note": "Fresh means within the source's recorded 14-day retrieval window, not latest internet coverage."},
                "lifecycle": {"discard": "not_recorded", "superseded_by": "not_recorded"},
                "source": {"path": None, "line_start": None, "line_end": None,
                    "file_sha256": value["graph_sha256"], "snippet_sha256": _digest(part.encode("utf-8")),
                    "node_id": node["id"]},
            })
    feature_count = sum(node["kind"] == "feature" for node in raw_nodes)
    source_nodes = list(doc_map.values())
    def is_expired(document: dict[str, Any]) -> bool:
        value = (document.get("source") or {}).get("expires_at")
        if not value:
            return False
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")) <= now
        except (TypeError, ValueError):
            return True
    knowledge_status.update({"version": value["version"], "graph_sha256": value["graph_sha256"],
        "feature_count": feature_count, "node_count": len(raw_nodes),
        "skill_snippet_count": knowledge_status["skill_snippet_count"],
        "edge_count": len(value["data"]["edges"]),
        "fresh_source_count": sum(not is_expired(doc) for doc in source_nodes),
        "expired_source_count": sum(is_expired(doc) for doc in source_nodes),
        "freshness_note": "Stored curated source timestamps are shown as recorded. Expired evidence remains visible and is not described as current."})
    return items, knowledge_status, value["data"]["edges"]


def install_agent_library(app) -> None:
    @app.get("/api/agent-library")
    def agent_library(request: Request,
                      section: str = Query("tools", pattern="^(tools|knowledge)$"),
                      view: str = Query("all", pattern="^(all|skills|architecture)$"),
                      category: str = Query("all", pattern="^(all|analysis|writer|harness)$"),
                      offset: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=_MAX_PAGE_SIZE)):
        if section == "tools":
            items, tool_status = _tools_catalog()
            if category != "all":
                items = [item for item in items if item["category"] == category]
            roles = [{"id": role, "label": role.title()} for role in ROLES]
            roles.append({"id": _WRITER_ROLE, "label": _WRITER_LABEL})
            status = {**tool_status, "roles": roles,
                "analysis_role_count": len(ROLES), "writer_is_separate": True,
                "internet_search_tool_available": False,
                "online_search_tool_available": False,
                "notes": ["Analysis tools are the decorated functions compiled by workbench.agents.role_tools.",
                    "Writer tools are the separate spark_codegen artifact-writing run, not the analysis artifact role.",
                    "Filesystem and delegation entries are Deep Agents harness operations, not domain tool functions.",
                    "No internet-search agent tool is declared."]}
            status["active_category"] = category
            return _page(section, items, offset, limit, status)
        items, status, edges = _knowledge_items(request)
        if view == "skills":
            items = [item for item in items if item["kind"] == "skill_snippet"]
        elif view == "architecture":
            items = [item for item in items if item["kind"] == "architecture_knowledge"]
        status["active_view"] = view
        response = _page(section, items, offset, limit, status)
        visible_ids = {item.get("source", {}).get("node_id") for item in response["items"]}
        response["edges"] = [edge for edge in edges
            if edge["source"] in visible_ids and edge["target"] in visible_ids]
        response["complete_catalog"] = not response["has_more"]
        return response


def _page(section: str, items: list[dict[str, Any]], offset: int, limit: int,
          status: dict[str, Any]) -> dict[str, Any]:
    total = len(items)
    page = items[offset:offset + limit]
    return {"section": section, "items": page, "total": total, "offset": offset,
        "limit": limit, "returned": len(page), "has_more": offset + len(page) < total,
        "complete": offset + len(page) >= total, "status": status}
