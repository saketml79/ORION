"""Bounded, local-only HTTP adapter for structural evidence and advisory review.

No dotenv, startup ingestion, executable generation, deployment, or live discovery.
One process owns a data directory. Package and agent workers each have one slot,
with no unbounded executor queue. JSON job records outlive browser connections;
interrupted jobs fail explicitly on restart, never pretend to resume.

Agent timeouts are cooperative, as provided by AgentRuntime. A thread cannot kill
an in-flight provider call. The slot stays occupied until that call returns and
process shutdown may wait for it. This is not a production durable worker.
Raw evidence bytes are local sensitive files; only extracted text is redacted.
Regex redaction is defense in depth, not a guarantee. Never upload secrets.
"""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict, deque
from copy import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import io
import ipaddress
import inspect
import json
import os
from pathlib import Path
import re
import shutil
from threading import Lock, RLock, BoundedSemaphore, Event
import time
import tempfile
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakValueDictionary

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from workbench.release_preview import ReleaseSelection

from workbench import agents as agent_module
from workbench.agents import AgentRuntime, _configured_model
from workbench.domain import Store, _BASE_BLOCKERS, _hash, _logical, _redact, _RELATIONS
from workbench.understanding import analyze_package
from workbench.capabilities import capability_inventory
from workbench.run_control import RunControl


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name("static")
MAX_PACKAGE_BYTES = 80 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_GRAPH_NODES = 100
MAX_GRAPH_EDGES = 2000
MAX_EVIDENCE = 50
ARTIFACT_VERSION = "workbench-understanding-v2"
SETTINGS_KEYS = ["github_owner", "github_repo", "host", "default_branch", "dev_host", "test_host", "prod_host"]
_ID = re.compile(r"[a-f0-9]{64}\Z")
_JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
_CACHE: WeakValueDictionary = WeakValueDictionary()
_CACHE_LOCK = RLock()
# Hosted actions rebuild the SQLite/runtime cache in a fresh temporary directory
# per request. Keep one verified immutable canonical PackageIndex strongly
# reachable across those disposable directories, otherwise every API read
# decompresses the full XML understanding and rebuilds hundreds of thousands of
# node/edge references. One-entry bound protects the Apps process memory budget.
_PACKAGE_INDEX_CACHE = OrderedDict()
_PACKAGE_INDEX_CACHE_LIMIT = 1


def _evict_hosted_package_indexes():
    """Release hosted-only canonical graphs before admitting a different package."""
    with _CACHE_LOCK:
        for key in tuple(_PACKAGE_INDEX_CACHE):
            if key[0] != "local":
                _PACKAGE_INDEX_CACHE.pop(key, None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _scope_capabilities(hosted_authenticated=False):
    """Static support and verified request identity, never execution authority."""
    from workbench.workflow_artifacts import supported_workflows
    return {
        "generation": {
            "available": True, "status": "bounded_reviewed_workflows",
            "scenario": "bronze", "supported_workflows": supported_workflows(),
            "general_codegen_available": False,
            "generation_allowed": False, "deployment_allowed": False,
            "reason": "A fingerprint-pinned compiler is available for four reviewed bronze workflows only. Changed scopes and the original-source scenario are blocked. Bindings, validation and separate approval/execution/deployment gates still apply; arbitrary transformation graphs and general codegen are not supported."},
        "general_code_generation": False,
        "deployment": False,
        "hosted_application": {
            "available": hosted_authenticated,
            "status": "app_hosted" if hosted_authenticated else "local_app",
            "reason": "The workbench application is hosted on Databricks Apps. This does not deploy generated Jobs or clear release gates." if hosted_authenticated else "This is the local workbench, not a hosted application deployment."},
        "authentication": {
            "available": True if hosted_authenticated else None,
            "status": "request_obo_authenticated" if hosted_authenticated else "local_only",
            "scope": "hosted_request_proxy_obo" if hosted_authenticated else "local_loopback_boundary",
            "inference_verified": False,
            "reason": "Your Databricks identity was verified for this request. Access to an AI model is checked separately." if hosted_authenticated else "This local app is available on this computer only and does not verify user identity. Access to an AI model is checked separately."},
        "model_inference_authentication": {
            "available": None, "status": "inference_unverified", "inference_verified": False,
            "reason": "Selecting a model or connecting to the workspace does not test the model. The model is used when you start the agents."},
    }


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(7):
            try:
                os.replace(temp, path)
                break
            except PermissionError as error:
                # Windows scanners/readers can briefly hold the destination
                # without delete sharing. Retry only that transient replace;
                # never fall back to truncating the committed record.
                if os.name != 'nt' or getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 6:
                    raise
                time.sleep(min(0.05 * (2 ** attempt), 0.8))
    finally:
        temp.unlink(missing_ok=True)


def _digest(path, progress=None):
    digest = sha256()
    size = Path(path).stat().st_size
    completed = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            completed += len(chunk)
            digest.update(chunk)
            if progress is not None and (completed % (16 * 1024 * 1024) < len(chunk) or completed == size):
                progress("verifying_artifact", completed=completed, total=size, unit="bytes")
    return digest.hexdigest()


def _write_model(path, model):
    """Valid JSON, one record per line. Never materialize the full JSON string.

    Buffer above gzip to avoid millions of tiny compression writes. This format
    also permits record-at-a-time restart loading without json.load's 400MB text
    allocation in addition to the decoded model.
    """
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(io.BufferedWriter(compressed, 1024 * 1024), encoding="utf-8") as stream:
                stream.write("{\n")
                for position, (key, value) in enumerate(model.items()):
                    stream.write(json.dumps(key) + ":\n")
                    if key in {"nodes", "edges", "diagnostics"}:
                        stream.write("[\n")
                        for index, record in enumerate(value):
                            json.dump(record, stream, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
                            stream.write(",\n" if index + 1 < len(value) else "\n")
                        stream.write("]")
                    else:
                        json.dump(value, stream, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
                    stream.write(",\n" if position + 1 < len(model) else "\n")
                stream.write("}\n")


def _read_model(path, progress=None, expected=None):
    """Read our line-oriented JSON artifact with only one encoded record in RAM."""
    model = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        if stream.readline().strip() != "{":
            raise ValueError("Invalid understanding artifact")
        for line in stream:
            if line.strip() == "}":
                break
            key = json.loads(line.strip().removesuffix(":"))
            if key in {"nodes", "edges", "diagnostics"}:
                if stream.readline().strip() != "[":
                    raise ValueError("Invalid understanding collection")
                records = []
                for record in stream:
                    if record.strip() in {"]", "],"}:
                        break
                    records.append(json.loads(record.strip().removesuffix(",")))
                    if progress is not None and key in {"nodes", "edges"} and len(records) % 4096 == 0:
                        total = expected.get("node_count" if key == "nodes" else "edge_count") if expected else None
                        progress("decoding_nodes" if key == "nodes" else "decoding_edges",
                                 completed=len(records), total=total,
                                 unit="nodes" if key == "nodes" else "edges")
                model[key] = records
                if progress is not None and key in {"nodes", "edges"}:
                    total = expected.get("node_count" if key == "nodes" else "edge_count") if expected else None
                    progress("decoding_nodes" if key == "nodes" else "decoding_edges",
                             completed=len(records), total=total,
                             unit="nodes" if key == "nodes" else "edges")
            else:
                model[key] = json.loads(stream.readline().strip().removesuffix(","))
    if not {"package", "nodes", "edges", "counts", "coverage"} <= model.keys():
        raise ValueError("Incomplete understanding artifact")
    return model


def _bounded(value, limit=100, depth=0):
    """Display copies only. Canonical model/export are never mutated or capped."""
    if depth == 0:
        value = _redact(value)
    if depth > 12:
        return {"omitted": "display depth limit"}
    if isinstance(value, str):
        return value if len(value) <= 12000 else value[:12000] + " [TRUNCATED; full value in export]"
    if isinstance(value, list):
        return [_bounded(v, limit, depth + 1) for v in value[:limit]]
    if isinstance(value, dict):
        result = {k: _bounded(v, limit, depth + 1) for k, v in value.items()}
        for key, item in value.items():
            if isinstance(item, list):
                result[key + "_total"] = len(item)
                result[key + "_truncated"] = len(item) > limit
        return result
    return value


def _agent_delivery_status(output, candidate_drafts, generate_code):
    """Keep accepted design, specialist completeness and notebook drafting distinct."""
    status = output.get("status") if isinstance(output, dict) else None
    if status == "cancelled":
        return "cancelled", "Agent run stopped by user; any in-flight call completed before stop was observed."
    if status in {"failed", "unavailable", "proposal_failed"}:
        return "failed", "No valid design was accepted; no candidate code was produced."
    roles = output.get("role_completion", []) if isinstance(output, dict) else []
    incomplete_roles = sorted({item.get("role", "unknown") for item in roles
                               if isinstance(item, dict) and item.get("status") != "completed"})
    designs = output.get("redesigns", []) if isinstance(output, dict) else []
    draft_blocked = bool(generate_code and designs and (
        not candidate_drafts or any(item.get("status") != "completed"
                                    or item.get("manifest_status") != "generation_complete"
                                    for item in candidate_drafts)))
    if incomplete_roles or draft_blocked:
        parts = []
        if incomplete_roles:
            parts.append("Incomplete specialist results: " + ", ".join(incomplete_roles) + ".")
        if draft_blocked:
            parts.append("Candidate notebook/code generation is blocked or incomplete.")
        return "partial", " ".join(parts)
    return "completed", "Requested analysis and candidate-drafting stages completed; execution and parity remain unverified."


def _brief(node):
    return {k: node[k] for k in ("id", "kind", "name", "scope", "parent_id")}


def _overview_agent(runtime, hosted_authenticated=False):
    """Preserve the overview's request-scoped runtime status semantics."""
    agent = runtime.status()
    from workbench.model_access import workspace_ready
    agent = {**agent, "available": workspace_ready(runtime),
             "egress_consent_required": True,
             "timeout_policy": agent.get("timeout_policy", "no_elapsed_time_cutoff_manual_cancellation")}
    if not agent["available"] and os.environ.get("WORKBENCH_TRUST_DATABRICKS_PROXY") == "1":
        agent["reason"] = "Six migration agents are available, but no model is selected. Connect workspace models and choose a chat model for the agents."
    return agent


def _package_overview(index):
    """Pure package projection shared by local and selected hosted overviews."""
    model = index.model
    diagnostics = model.get("diagnostics", [])
    return {
        "package": model["package"], "counts": model["counts"], "summary": model["summary"],
        "coverage": _bounded(model["coverage"]),
        "diagnostics_count": len(diagnostics), "diagnostics": diagnostics[:50],
        "diagnostics_truncated": len(diagnostics) > 50,
        "workflows": [_brief(node) for node in index.kinds.get("workflow", ())],
    }


def _overview_payload(index, job_id, agent, startup_error, hosted_authenticated):
    """Build the public overview contract without reading service state."""
    capabilities = {"xml_understanding": True, "bounded_original_graphs": True, "provisional_reviews": True,
                    "supporting_evidence": True, "zip_upload": False,
                    **_scope_capabilities(hosted_authenticated),
                    "captured_sql_drafts": {
                        "available": True, "status": "syntax_drafts_only",
                        "reason": "Explicitly consented captured-SQL transpilation can produce notebook drafts. Unsupported SQL is blocked; non-SQL transformations, semantic parity and deployment are not supplied."},
                    "github_publication": False,
                    "agents": {
                        "available": agent["available"] is True,
                        "status": "model_route_configured" if agent["available"] is True else "roles_configured_model_route_required",
                        "description": (
                            f"{len(agent.get('roles', []))} Deep Agents role definitions are installed: "
                            f"{', '.join(agent.get('roles', []))}. "
                            + ("A model is configured for the agents; the model has not been tested by this check."
                               if agent["available"] is True else
                               "No user-selected model route is configured. In this hosted app, use Connect workspace models and explicitly select an endpoint. "
                               "The role definitions exist, but no agent can execute until a route is selected and authorized.")
                        ),
                        "configured_roles": agent.get("roles", []),
                        "inference_verified": False,
                    },
                    "semantic_translation": {
                        "available": False, "status": "not_implemented",
                        "reason": "SQL syntax transpilation and workflow understanding exist, but general behavior-preserving conversion of Informatica transformation graphs into PySpark or Databricks SQL is not implemented."},
                    "metadata_reuse_analysis": {
                        "available": True, "status": "structural_candidates_only",
                        "reason": "Mapping fingerprints and workflow planning contracts expose structural reuse candidates. Candidates are not proven semantic equivalence, approved shared libraries or automatic task consolidation."}}
    result = {"ready": index is not None, "status": "ready" if index else "not_ready", "package": None,
              "job_id": job_id, "agent": agent, "capabilities": capabilities, "startup_error": startup_error,
              "capability_inventory": capability_inventory(hosted_authenticated),
              "generation_allowed": False, "deployment_allowed": False}
    if index is not None:
        result.update(_package_overview(index))
    return result


class PackageIndex:
    """One shared model and reference-only indices per persisted artifact."""

    def __init__(self, model, directory, progress=None):
        self.model, self.directory = model, Path(directory)
        self.nodes = {n["id"]: n for n in model["nodes"]}
        self.children = defaultdict(list)
        self.kinds = defaultdict(list)
        self.out = defaultdict(list)
        self.adjacency = defaultdict(list)
        self.diagnostics = defaultdict(list)
        for number, node in enumerate(model["nodes"], 1):
            self.children[node["parent_id"]].append(node)
            self.kinds[node["kind"]].append(node)
            if progress is not None and number % 4096 == 0:
                progress("indexing_nodes", completed=number, total=len(model["nodes"]), unit="nodes")
        if progress is not None:
            progress("indexing_nodes", completed=len(model["nodes"]), total=len(model["nodes"]), unit="nodes")
        for number, edge in enumerate(model["edges"], 1):
            self.out[edge["source"]].append(edge)
            self.adjacency[edge["source"]].append(edge)
            if edge["target"] != edge["source"]:
                self.adjacency[edge["target"]].append(edge)
            if progress is not None and number % 8192 == 0:
                progress("indexing_edges", completed=number, total=len(model["edges"]), unit="edges")
        if progress is not None:
            progress("indexing_edges", completed=len(model["edges"]), total=len(model["edges"]), unit="edges")
        for diagnostic in model.get("diagnostics", []):
            self.diagnostics[diagnostic.get("object_id")].append(diagnostic)
        self.proposals = {}
        self.proposal_build_seconds = 0.0
        self.proposal_lock = Lock()
        self.impact_lock = Lock()
        self.impacts = {}
        self.evidence_reference_cache = {}

    def evidence_references(self, evidence_ids):
        """Resolve cited IDs to concise, human-readable captured XML objects."""
        wanted = {value for value in evidence_ids if isinstance(value, str)}
        missing_edges = wanted - self.nodes.keys() - self.evidence_reference_cache.keys()
        if missing_edges:
            for edge in self.model["edges"]:
                identifier = edge["id"]
                if identifier not in missing_edges:
                    continue
                source, target = self.nodes.get(edge["source"]), self.nodes.get(edge["target"])
                left = source["name"] if source else "Unknown source object"
                right = target["name"] if target else "Unknown target object"
                self.evidence_reference_cache[identifier] = {
                    "id": identifier, "kind": edge["kind"], "label": f"{left} → {right}",
                    "locator": edge.get("evidence", {}).get("locator", "XML locator not captured")}
                missing_edges.remove(identifier)
                if not missing_edges:
                    break
            for identifier in missing_edges:
                self.evidence_reference_cache[identifier] = {
                    "id": identifier, "kind": "unresolved", "label": "Captured reference not found in this package",
                    "locator": "No XML locator available"}
        references = []
        for identifier in sorted(wanted):
            node = self.nodes.get(identifier)
            if node:
                references.append({"id": identifier, "kind": node["kind"], "label": node["name"],
                                   "locator": node.get("evidence", {}).get("locator", "XML locator not captured")})
            else:
                references.append(self.evidence_reference_cache[identifier])
        return references

    @classmethod
    def load(cls, directory, progress=None, cache_scope="local"):
        directory = Path(directory).resolve()
        key = str(directory)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (manifest["sha256"] != directory.name
            or manifest["understanding_sha256"] != _digest(
                directory / "understanding.json.gz",
                progress=(lambda stage, **kw: progress(stage, **kw)) if progress else None)):
            raise ValueError("Existing package artifact checksum mismatch")
        from workbench.source_analysis import analysis_pointer, enrich
        pointer = analysis_pointer(directory)
        canonical_key = (cache_scope, ARTIFACT_VERSION, manifest.get("artifact_version"), manifest["sha256"], manifest["schema_version"],
                         manifest["node_count"], manifest["edge_count"],
                         manifest["understanding_sha256"], manifest.get("analyzer_sha256"),
                         _hash(pointer) if pointer else None)
        with _CACHE_LOCK:
            cached = _PACKAGE_INDEX_CACHE.get(canonical_key)
            if cached is None:
                model = _read_model(directory / "understanding.json.gz", progress, manifest)
                model = enrich(model, directory, manifest, pointer)
                cached = cls(model, directory, progress=progress)
            elif pointer is not None:
                # Cache reuse never substitutes for current artifact integrity.
                enrich(cached.model, directory, manifest, pointer)
            if (cached.model["package"]["sha256"] != manifest["sha256"]
                    or cached.model["schema_version"] != manifest["schema_version"]
                    or len(cached.model["nodes"]) != manifest["node_count"]
                    or len(cached.model["edges"]) != manifest["edge_count"]):
                raise ValueError("Existing package artifact checksum mismatch")
            _PACKAGE_INDEX_CACHE[canonical_key] = cached
            _PACKAGE_INDEX_CACHE.move_to_end(canonical_key)
            while len(_PACKAGE_INDEX_CACHE) > _PACKAGE_INDEX_CACHE_LIMIT:
                _PACKAGE_INDEX_CACHE.popitem(last=False)
            if progress is not None:
                progress("index_cache", completed=1, total=1, unit="records")
            if cached.directory != directory:
                # The large graph and derived indices are immutable. Return a
                # shallow request-local facade with the currently verified
                # artifact path so file exports never point into another user's
                # disposable temp directory.
                current = copy(cached)
                current.directory = directory
            else:
                current = cached
            _CACHE[key] = current
            return current

    def walk(self, workflow_id):
        parents = {workflow_id: None}
        queue = deque([workflow_id])
        while queue:
            for edge in self.out.get(queue.popleft(), ()):
                target = edge["target"]
                if edge["kind"] in _RELATIONS and target not in parents:
                    parents[target] = edge
                    queue.append(target)
        return parents

    @staticmethod
    def path(parents, target):
        path = []
        while parents[target] is not None:
            edge = parents[target]
            path.append({k: edge[k] for k in ("id", "source", "target", "kind", "evidence")})
            target = edge["source"]
        return list(reversed(path))

    def focus(self, object_id):
        """Original local graph, not field-flow/estate traversal or optimization.

        Direct taskinstances/instances first, then up to three explicit definition
        hops. Never traverse instance_field nodes or their containment edges.
        """
        node = self.nodes[object_id]
        selected = {object_id: node}
        direct = self.children.get(object_id, ())
        wanted = "taskinstance" if node["kind"] in {"workflow", "worklet"} else "instance"
        eligible = [n for n in direct if n["kind"] != "instance_field"]
        eligible.sort(key=lambda n: (n["kind"] != wanted, n["id"]))
        for child in eligible[:MAX_GRAPH_NODES - 1]:
            selected[child["id"]] = child
        candidates = set(selected) | {n["id"] for n in eligible}
        frontier = list(selected)
        for _ in range(3):
            next_frontier = []
            for source in frontier:
                for edge in self.out.get(source, ()):
                    if edge["kind"] not in {"task_definition", "session_mapping", "instance_of", "shortcut_reference"}:
                        continue
                    target = edge["target"]
                    if self.nodes[target]["kind"] == "instance_field":
                        continue
                    candidates.add(target)
                    if target not in selected and len(selected) < MAX_GRAPH_NODES:
                        selected[target] = self.nodes[target]
                        next_frontier.append(target)
            frontier = next_frontier
        edges = []
        total_edges = 0
        for source in selected:
            for edge in self.out.get(source, ()):
                if edge["target"] in selected and edge["kind"] != "field_flow":
                    total_edges += 1
                    if len(edges) < MAX_GRAPH_EDGES:
                        edges.append(_bounded(edge))
        total = len(candidates)
        return {"nodes": [_bounded(n) for n in selected.values()], "edges": edges,
                "total_nodes": total, "total_edges": total_edges,
                "truncated": total > len(selected) or total_edges > len(edges),
                "scope": "original_local_structure_not_full_dependency_closure",
                "node_limit": MAX_GRAPH_NODES, "edge_limit": MAX_GRAPH_EDGES,
                "field_nodes_traversed": False}

    def proposal_summaries(self, scenario):
        """Lazy, conservative domain-equivalent boundary summaries, not full drafts.

        Deliberately do not call domain.build_proposals: it deep-copies every
        reached node/edge, including all fields, once for every workflow. All
        verification gates are retained. Metadata reuse analysis is not performed
        by this bounded adapter and is explicitly not claimed.
        """
        with self.proposal_lock:
            if scenario in self.proposals:
                return self.proposals[scenario]
            artifact = self.directory / f"proposals-{scenario}.json"
            if artifact.exists():
                proposals = json.loads(artifact.read_text(encoding="utf-8"))
                if (not isinstance(proposals, list)
                        or {p["workflow_id"] for p in proposals} != {w["id"] for w in self.kinds.get("workflow", ())}
                        or any(p["scenario"] != scenario or p["version"] != 1 or p["status"] != "provisional"
                               or p["id"] != _hash([self.model["package"]["sha256"], p["workflow_id"], scenario])
                               or p["generation_allowed"] or p["deployment_allowed"] for p in proposals)):
                    raise ValueError("Invalid immutable provisional proposal artifact")
                self.proposals[scenario] = proposals
                self.proposal_build_seconds = 0.0
                return proposals
            started = time.monotonic()
            proposals = []
            for workflow in self.kinds.get("workflow", ()):
                reached = self.walk(workflow["id"])
                mappings, unresolved, evidence = [], [], []
                sources, targets = [], []
                for object_id in sorted(reached):
                    node = self.nodes[object_id]
                    kind = node["kind"]
                    if len(evidence) < MAX_EVIDENCE:
                        evidence.append({"object_id": object_id, **node["evidence"]})
                    if kind == "mapping":
                        mappings.append(object_id)
                    if kind == "unresolved_reference" or node.get("attributes", {}).get("status") in {"unresolved", "ambiguous"}:
                        unresolved.append(object_id)
                    if kind in {"source", "target"}:
                        collection = sources if kind == "source" else targets
                        collection.append({"definition_id": object_id, "name": node["name"],
                                           "scope": node["scope"], "evidence": node["evidence"],
                                           "suggested_logical_name": _logical(node, "bronze" if kind == "source" else "silver"),
                                           "binding_status": "unbound", "bound_table": None,
                                           "origin": "original_system" if kind == "source" and scenario == "original" else "suggested_databricks_dataset",
                                           "medallion_layer": "bronze" if kind == "source" else "silver"})
                blockers = list(_BASE_BLOCKERS)
                if unresolved:
                    unresolved_nodes = [self.nodes[identifier] for identifier in unresolved]
                    redacted_connection_count = sum(
                        node.get("attributes", {}).get("status_reason") == "connection_value_redacted"
                        for node in unresolved_nodes)
                    other_unresolved_count = len(unresolved_nodes) - redacted_connection_count
                    if redacted_connection_count:
                        blockers.append(
                            f"{redacted_connection_count} Informatica connection value(s) are redacted in the XML; "
                            "review physical connection bindings before execution. This is not a Unity Catalog "
                            "schema/table failure, and no Unity Catalog lookup was performed."
                        )
                    if other_unresolved_count:
                        blockers.append(
                            f"{other_unresolved_count} other unresolved or ambiguous package reference(s)/field(s) "
                            "require review."
                        )
                if not mappings:
                    blockers.append("No verified mapping binding was discovered for this workflow.")
                if not sources or not targets:
                    blockers.append("Source-to-target coverage is incomplete or absent.")
                if self.model.get("coverage", {}).get("unsupported_tags"):
                    blockers.append("Unsupported package tags require scoped behavior review.")
                graph = self.focus(workflow["id"])
                proposals.append(_redact({
                    "id": _hash([self.model["package"]["sha256"], workflow["id"], scenario]),
                    "workflow_id": workflow["id"], "name": workflow["name"], "scenario": scenario,
                    "version": 1, "status": "provisional", "blockers": blockers,
                    "rationale": "Keep the original workflow boundary until operational and semantic evidence supports a change.",
                    "summary": f"Workflow {workflow['name']} references {len(mappings)} mapping definitions, {len(sources)} sources and {len(targets)} targets. Scenario: {scenario}. Bronze/silver names are unbound suggestions. Gold requires business-grain evidence. Lifecycle is unknown; external side effects require review. Structural candidate only, not verified translation or optimization.",
                    "alternatives": [{"kind": "retain_boundary", "status": "candidate_only"},
                                     {"kind": "decomposition", "status": "candidate_only", "mapping_ids": mappings[:100]}],
                    "sources": sources[:100], "sources_total": len(sources), "sources_truncated": len(sources) > 100,
                    "targets": targets[:100], "targets_total": len(targets), "targets_truncated": len(targets) > 100,
                    "mapping_ids": mappings[:100], "mapping_ids_total": len(mappings), "mapping_ids_truncated": len(mappings) > 100,
                    "evidence": evidence, "evidence_total": len(reached), "evidence_truncated": len(reached) > MAX_EVIDENCE,
                    "coverage": {"scoped_objects": len(reached), "mapping_count": len(mappings),
                                 "unresolved_ids": unresolved[:100], "unresolved_count": len(unresolved),
                                 "redacted_connection_count": sum(
                                     self.nodes[identifier].get("attributes", {}).get("status_reason") == "connection_value_redacted"
                                     for identifier in unresolved),
                                 "unresolved_ids_truncated": len(unresolved) > 100,
                                 "semantic_status": "unverified", "lifecycle": "unknown", "scope_status": "unverified",
                                 "connection_status": "unverified", "behavior_disposition": "pending", "structural_only": True,
                                 "metadata_reuse_analysis": "not_performed_by_bounded_adapter"},
                    "graph": graph, "graph_total_nodes": graph["total_nodes"], "graph_truncated": graph["truncated"],
                    "generation_allowed": False, "deployment_allowed": False, "summary_only": True,
                }))
            # Publish the entire immutable structural draft before caching it.
            # Reviews remain versioned in SQLite and never overwrite this file.
            _atomic_json(artifact, proposals)
            self.proposals[scenario] = proposals
            self.proposal_build_seconds = time.monotonic() - started
            return proposals

    def impact(self, workflow_id):
        """Exact directed/shared definition evidence, using the existing indices.

        Only one workflow parent map is retained at a time; per-estate closures
        are not cached as hundreds of copies of field-node reachability.
        """
        with self.impact_lock:
            if workflow_id in self.impacts:
                return self.impacts[workflow_id]
            selected = self.walk(workflow_id)
            definitions = {i for i in selected if self.nodes[i]["kind"] in {"mapping", "source", "target"}}
            groups = {k: [] for k in ("direct", "shared", "transitive", "unknown")}
            counts = {k: 0 for k in groups}
            affected = {workflow_id}

            def add(category, record):
                counts[category] += 1
                if len(groups[category]) < 50:
                    groups[category].append(record)

            for workflow in self.kinds.get("workflow", ()):
                other = workflow["id"]
                if other == workflow_id:
                    continue
                if other in selected:
                    path = self.path(selected, other)
                    add("direct", {"workflow_id": other, "name": workflow["name"], "path": path[:80], "path_edge_count": len(path)})
                    affected.add(other)
                    continue
                reached = self.walk(other)
                common = sorted(definitions & reached.keys())
                if common:
                    left, right = self.path(selected, common[0]), self.path(reached, common[0])
                    add("shared", {"workflow_id": other, "name": workflow["name"], "shared_definition_ids": common[:50],
                                   "shared_definition_count": len(common), "path": {"forward": left[:80], "reverse": list(reversed(right))[:80]},
                                   "path_edge_count": len(left) + len(right), "direction": "selected_to_definition_then_reverse_to_related"})
                    affected.add(other)
            for workflow in self.kinds.get("workflow", ()):
                if workflow["id"] in affected:
                    continue
                reached = self.walk(workflow["id"])
                called = sorted(affected & reached.keys())
                if called:
                    path = self.path(reached, called[0])
                    add("transitive", {"workflow_id": workflow["id"], "name": workflow["name"], "via_workflow_id": called[0],
                                       "direction": "caller_to_affected", "path": path[:80], "path_edge_count": len(path)})
            for object_id in sorted(selected):
                node = self.nodes[object_id]
                if node["kind"] == "unresolved_reference" or node.get("attributes", {}).get("status") in {"unresolved", "ambiguous"}:
                    add("unknown", {"object_id": object_id, "name": node["name"], "reason": node.get("attributes", {}).get("status", "unresolved"),
                                    "path": self.path(selected, object_id)[:80]})
            result = _redact({"workflow_id": workflow_id, **groups, "counts": counts,
                              "total_related_workflows": sum(counts[k] for k in ("direct", "shared", "transitive")),
                              "scoped_object_count": len(selected), "workflow_count": len(self.kinds.get("workflow", ())),
                              "limit_per_category": 50, "path_edge_limit": 80, "truncated": any(c > 50 for c in counts.values()),
                              "limitations": ["Lifecycle and runtime impact are unknown.", "Command-text workflow calls and equal physical table names are not verified references.",
                                              "Shared definitions indicate possible impact, not proven dataflow or safe merging."]})
            # Bounded report cache, never an estate of full closure graphs.
            if len(self.impacts) >= 16:
                self.impacts.pop(next(iter(self.impacts)))
            self.impacts[workflow_id] = result
            return result


class _LocalBoundary:
    """ASGI boundary, applied before multipart parsing and all static/API routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("workbench.local_boundary_checked"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        if scope["path"].startswith("/api/models/external/"):
            sensitive = {b"host", b"origin", b"content-length", b"content-type", b"cookie",
                         b"x-workbench-request", b"x-workbench-project", b"x-workbench-package"}
            names = [name.lower() for name, _ in scope["headers"] if name.lower() in sensitive]
            if len(names) != len(set(names)):
                return await reject(400, "Duplicate security-sensitive header")
        client = (scope.get("client") or ("", 0))[0]
        try:
            local = ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = False
        if not local:
            return await reject(403, "Loopback clients only; remote authentication is not implemented")
        try:
            host = urlsplit("http://" + headers.get("host", ""))
            local_host = host.hostname == "localhost" or ipaddress.ip_address(host.hostname or "").is_loopback
            valid_host = local_host and not host.username and not host.password and host.path in {"", "/"}
            host.port
        except ValueError:
            valid_host = False
        if not valid_host:
            return await reject(400, "A loopback Host header is required")
        mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        if mutation:
            if headers.get("x-workbench-request") != "1":
                return await reject(403, "Mutations require X-Workbench-Request: 1")
            if headers.get("sec-fetch-site") == "cross-site":
                return await reject(403, "Cross-site mutations are forbidden")
            origin = headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    same = (parsed.scheme == scope["scheme"] and parsed.netloc.lower() == headers.get("host", "").lower()
                            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
                except ValueError:
                    same = False
                if not same:
                    return await reject(403, "Cross-origin mutations are forbidden")
        path = scope["path"]
        limit = ((MAX_PACKAGE_BYTES if path == "/api/packages/upload" else MAX_DOCUMENT_BYTES) + 65536
                 if path in {"/api/packages/upload", "/api/documents"} else 65536)
        if re.fullmatch(r"/api/projects/[^/]+/packages/upload", path):
            limit = 160 * 1024 * 1024 + 65536
        if re.fullmatch(r"/api/projects/[^/]+/documents", path):
            limit = MAX_DOCUMENT_BYTES + 65536
        try:
            length = int(headers.get("content-length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await reject(400, "Invalid Content-Length")
        if length > limit:
            return await reject(413, "Request exceeds the upload/body size limit")
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, "Request exceeds the upload/body size limit")
            return message

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ]
            await send(message)

        checked_scope = {**scope, "workbench.local_boundary_checked": True}
        await self.app(checked_scope, bounded_receive, safe_send)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewBody(_Body):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_version: int = Field(ge=1)
    action: Literal["comment", "request_changes", "reject", "request_rework", "approve"]
    comment: str = Field(max_length=12000)
    actor: str = Field(default="local-reviewer", min_length=1, max_length=200)
    anchor: str | None = Field(default=None, max_length=2048)


class AttestationBody(_Body):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    field: Literal["lifecycle", "migration_scope", "invocation"]
    value: str = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=12000)
    actor: str = Field(default="local-reviewer", min_length=1, max_length=200)


class AgentBody(_Body):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario: Literal["original", "bronze"] = "bronze"
    prompt: str = Field(default="", max_length=12000)
    consent: bool = False
    generate_code: bool = False
    knowledge_preview_consent: bool = False


class SparkDraftBody(_Body):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    consent: bool


class NativeJobDraftBody(_Body):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_root: str = Field(min_length=12, max_length=220)
    consent: bool


class WorkflowArtifactsBody(_Body):
    workflow_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario: Literal["bronze"]
    consent: bool


def _stored_agent_progress(root, record):
    """Observe committed call events only, with an unambiguous public/runtime pin.

    Older active public records have no runtime output yet. Match their captured
    hashes and workflow namespace inside their persisted invocation time window.
    Never initialize a ledger, reconstruct source inputs, or expose call payloads.
    """
    from contextlib import closing
    import sqlite3

    unavailable = {"status": "unverified", "source": "local_agent_usage_ledger",
                   "historical_output_unchanged": True}
    # Terminal callers may still observe the verified ledger. A saved terminal
    # observation is preferred by renderers, not fabricated by this read path.
    pins = record.get("input_hashes")
    versions = record.get("input_versions") or {}
    if not isinstance(pins, dict) or not pins:
        return unavailable
    expected = {**pins, "documents": versions.get("documents"), "feedback": versions.get("reviews")}
    if any(not isinstance(v, str) or not _ID.fullmatch(v) for v in expected.values()):
        return unavailable
    output = record.get("output") or {}
    runtime_pin, runtime_hashes = output.get("run_id"), output.get("input_hashes")
    if (runtime_pin is not None and (not isinstance(runtime_pin, str) or not _JOB_ID.fullmatch(runtime_pin)
            or not isinstance(runtime_hashes, dict) or not runtime_hashes
            or any(runtime_hashes.get(k) != v for k, v in expected.items()))):
        return unavailable
    start = record.get("started_at")
    try:
        start_time = datetime.fromisoformat(start)
        if start_time.tzinfo is None:
            return unavailable
        next_time = None
        for path in (Path(root) / "state" / "agent-runs").glob("*.json"):
            other = json.loads(path.read_bytes())
            if (other.get("id") != record["id"] and other.get("package_hash") == record["package_hash"]
                    and other.get("workflow_id") == record["workflow_id"]
                    and other.get("scenario") == record["scenario"]):
                created = datetime.fromisoformat(other["created_at"])
                if created > start_time and (next_time is None or created < next_time):
                    next_time = created
        directory = Path(root) / "agents"
        paths = [directory / "agents.sqlite3", *directory.glob("identities/*/agents.sqlite3")]
        candidates = []
        for path in paths:
            if not path.is_file():
                continue
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
                db.execute("BEGIN")
                for runtime_id, namespace, raw_hashes, created in db.execute(
                        "SELECT run_id,namespace,input_hashes,created_at FROM agent_runs WHERE created_at>=?", (start,)):
                    hashes = json.loads(raw_hashes)
                    if (not isinstance(hashes, dict) or not hashes
                            or any(not isinstance(v, str) or not _ID.fullmatch(v) for v in hashes.values())
                            or any(hashes.get(k) != v for k, v in expected.items())
                            or (runtime_pin is not None and (runtime_id != runtime_pin or hashes != runtime_hashes))
                            or not _JOB_ID.fullmatch(runtime_id)
                            or namespace != _hash([record["package_hash"], hashes, record["workflow_id"], record["scenario"]])
                            or (path.parent != directory and hashes.get("identity_scope") != path.parent.name)
                            or (path.parent == directory and hashes.get("identity_scope") is not None)
                            or (next_time is not None and datetime.fromisoformat(created) >= next_time)):
                        continue
                    events = db.execute(
                        "SELECT id,namespace,role,call_id,event,payload,created_at FROM agent_usage WHERE run_id=? ORDER BY id",
                        (runtime_id,)).fetchall()
                    candidates.append((runtime_id, namespace, hashes, events))
        if len(candidates) != 1:
            return unavailable
        runtime_id, namespace, hashes, events = candidates[0]
        calls, counts, last_event = {}, Counter(), None
        tool_calls, last_tool_event = {}, None
        activity_events = []
        known = {key: 0 for key in ("input_tokens", "output_tokens", "total_tokens")}
        present = Counter()
        for event_id, event_namespace, role, call_id, event, raw, created in events:
            payload = json.loads(raw)
            endpoint, tool_name = payload.get("endpoint"), payload.get("tool_name")
            if any(value is not None and (not isinstance(value, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value)
                    or _redact(value) != value) for value in (
                        (endpoint,) if event.startswith("tool_") else (endpoint, tool_name))):
                return unavailable
            if (event_namespace != namespace or payload.get("namespace") != namespace
                    or payload.get("run_id") != runtime_id or payload.get("input_hashes") != hashes
                    or payload.get("role") != role or payload.get("call_id") != call_id
                    or payload.get("event") != event
                    or (not event.startswith("tool_") and (role not in agent_module.ROLES
                        or not isinstance(call_id, str) or not _JOB_ID.fullmatch(call_id)))):
                return unavailable
            if event == "skill_loaded":
                activity_events.append((role, call_id, event, payload, created))
                continue
            if event not in {"model_started", "model_completed", "model_failed", "tool_started", "tool_completed", "tool_failed", "tool_rejected"}:
                return unavailable
            kind, state = event.split("_", 1)
            if kind == "tool":
                # Tool lifecycle defects must not invalidate model accounting.
                # Only expose these legacy display fields if the projection
                # below validates the entire tool timeline.
                activity_events.append((role, call_id, event, payload, created))
                counts[event] += 1
                key = (role, call_id)
                if state == "started":
                    tool_calls[key] = {"role": role, "kind": kind, "status": state,
                                       "endpoint": endpoint, "tool_name": tool_name,
                                       "started_at": created}
                elif key in tool_calls:
                    tool_calls[key]["status"] = state
                last_tool_event = {"id": event_id, "role": role, "event": event,
                                   "created_at": created, "endpoint": endpoint,
                                   "tool_name": tool_name or tool_calls.get(key, {}).get("tool_name")}
                continue
            if state == "started":
                if call_id in calls:
                    return unavailable
                calls[call_id] = {"role": role, "kind": kind, "status": "started",
                                  "endpoint": endpoint, "tool_name": tool_name,
                                  "started_at": created}
            else:
                call = calls.get(call_id)
                if not call or call["role"] != role or call["kind"] != kind or call["status"] != "started":
                    return unavailable
                call["status"] = state
            counts[event] += 1
            activity_events.append((role, call_id, event, payload, created))
            last_event = {"id": event_id, "role": role, "event": event, "created_at": created,
                          "endpoint": endpoint, "tool_name": tool_name}
            if event == "model_completed":
                for key in known:
                    value = payload.get(key)
                    if value is not None:
                        if type(value) is not int or value < 0:
                            return unavailable
                        known[key] += value
                        present[key] += 1
        if not events:
            return unavailable
        active = [call for call in calls.values() if call["status"] == "started"]
        from workbench.agent_usage_receipt import model_activity, tool_activity
        from workbench.agent_skill_receipt import skill_activity
        activity = model_activity(activity_events)
        tools = tool_activity(activity_events)
        if tools["tool_activity_status"] == "recorded":
            active.extend(call for call in tool_calls.values() if call["status"] == "started")
            if last_tool_event is not None and (last_event is None or last_tool_event["id"] > last_event["id"]):
                last_event = last_tool_event
        return {"status": "verified", "source": "local_agent_usage_ledger",
                "historical_output_unchanged": True, "public_run_id": record["id"],
                "run_id": runtime_id, "namespace": namespace,
                "input_hashes": hashes, "identity_scope_hash": hashes.get("identity_scope"),
                "mapping_basis": "captured_hashes_workflow_namespace_invocation_window",
                "event_count": len(events), "counts": dict(counts), "active_calls": active,
                "last_event": last_event, **activity, **tools, "skill_stats": skill_activity(activity_events),
                "usage": {**{"known_" + key: value for key, value in known.items()},
                          **{key: known[key] if present[key] == counts["model_started"] else None for key in known},
                          "accounting_complete": counts["model_started"] == counts["model_completed"]
                              and all(present[key] == counts["model_started"] for key in known)}}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return unavailable


def _terminal_agent_observation(root, record):
    """Freeze numeric renderer inputs at final commit, never SVG or live state.

    Use the same scope-verified, read-only ledger projection as active status.
    Missing counters remain unknown, including failures before runtime output.
    This does not recover output or re-run a model, writer, or source analysis.
    """
    progress = _stored_agent_progress(root, record)
    output = record.get("output") or {}
    snapshot = {**progress, "schema_version": 1, "mode": "historical_not_live",
                "captured_at": _now(), "public_run_id": record["id"],
                "package_hash": record["package_hash"], "workflow_id": record["workflow_id"],
                "scenario": record["scenario"], "run_status": record["status"],
                "created_at": record.get("created_at"), "started_at": record.get("started_at"),
                "finished_at": record["finished_at"],
                "findings": output.get("findings", []),
                "role_completion": output.get("role_completion", []),
                "design_submission": output.get("design_submission"),
                "redesign_refs": record.get("redesign_refs", [])}
    # Detach nested values from mutable runtime/record objects before saving.
    return json.loads(json.dumps(snapshot))


def _saved_workflow_names(root, package_hash):
    """Resolve display names from already-persisted proposal summaries only."""
    database = Path(root) / "state" / "reviews.sqlite"
    if not database.is_file():
        return {}
    try:
        import sqlite3
        uri = database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            rows = db.execute("SELECT payload FROM proposals WHERE package_hash=?", (package_hash,))
            names = {}
            for (payload,) in rows:
                try:
                    proposal = json.loads(payload)
                except (TypeError, ValueError):
                    continue
                workflow_id, name = proposal.get("workflow_id"), proposal.get("name")
                if (isinstance(workflow_id, str) and workflow_id
                        and isinstance(name, str) and name.strip()):
                    names[workflow_id] = name.strip()
            return names
    except (OSError, sqlite3.Error):
        return {}


def _candidate_writer_status(root, parent_run_id, package_hash=None):
    """Return the latest compact candidate-writer status for a parent run."""
    root = Path(root)
    job_directories = [root / "state" / "jobs"]
    if isinstance(package_hash, str) and package_hash:
        job_directories.insert(0, root / "package-contexts" / package_hash / "state" / "jobs")
    matches = []
    try:
        for jobs in job_directories:
            for path in jobs.glob("*.json"):
                try:
                    job = json.loads(path.read_bytes())
                except (OSError, ValueError):
                    continue
                if (isinstance(job, dict) and job.get("id") == path.stem
                        and job.get("kind") == "spark_draft"
                        and job.get("agent_run_id") == parent_run_id):
                    matches.append(job)
    except OSError:
        return None
    if not matches:
        return None
    job = max(matches, key=lambda item: (str(item.get("created_at", "")), item["id"]))
    output = job.get("output") if isinstance(job.get("output"), dict) else {}
    result = {key: job.get(key) for key in ("id", "status", "phase", "created_at", "started_at", "finished_at")} | {
        "manifest_status": output.get("manifest_status", output.get("status")),
        "task_statuses": output.get("task_statuses", []),
    }
    failure_stage = job.get("failure_stage")
    if isinstance(failure_stage, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,80}", failure_stage):
        result["failure_stage"] = failure_stage
    exception_class = job.get("exception_class")
    if isinstance(exception_class, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", exception_class):
        result["exception_class"] = exception_class
    if job.get("error") == "Candidate code draft unavailable. No approval, execution or deployment occurred.":
        result["error"] = job["error"]
    diagnostic = job.get("failure_diagnostic") or output.get("failure_diagnostic")
    if isinstance(diagnostic, dict) and diagnostic.get("schema_version") == 1:
        operation = diagnostic.get("operation")
        exceptions = []
        for item in diagnostic.get("exceptions", [])[:4] if isinstance(diagnostic.get("exceptions"), list) else []:
            if not isinstance(item, dict):
                continue
            name = item.get("exception_class")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", name):
                continue
            frames = []
            for frame in item.get("stack", [])[-8:] if isinstance(item.get("stack"), list) else []:
                if not isinstance(frame, dict):
                    continue
                file_name, function, line = frame.get("file"), frame.get("function"), frame.get("line")
                if (isinstance(file_name, str) and len(file_name) <= 180 and not file_name.startswith(("/", "\\"))
                        and ".." not in file_name.split("/") and isinstance(function, str)
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}|<module>", function)
                        and type(line) is int and line > 0):
                    frames.append({"file": file_name, "function": function, "line": line})
            exceptions.append({"exception_class": name, "stack": frames})
        safe_operations = {"writer_invocation", "writer_model_handler", "writer_tool", "writer_graph",
                           "writer_model_progress_started", "writer_model_progress_completed",
                           "writer_model_progress_failed", "writer_progress_persistence",
                           "generated_code_validation", "writer_preflight_write", "writer_preflight_read",
                           "writer_preflight_rename", "writer_preflight_sqlite", "writer_preflight_cleanup",
                           "writer_preflight_temp_directory"}
        if operation in safe_operations and exceptions:
            result["failure_diagnostic"] = {"schema_version": 1, "operation": operation,
                                             "exceptions": exceptions}
    failure = job.get("error")
    if isinstance(failure, str) and failure in {"Candidate code draft unavailable. No approval, execution or deployment occurred."}:
        result["error"] = failure
    return result


def _run_or_writer_active(record):
    writer = record.get("candidate_writer") if isinstance(record.get("candidate_writer"), dict) else {}
    return (record.get("status") in {"queued", "running", "cancelling"}
            or writer.get("status") in {"queued", "running", "cancelling"})


def _stored_agent_status(root, run_id, expected_package=None):
    """Observe an atomic saved record, without runtime locks or input reconstruction.

    Project dispatch validates current registry ownership separately. Active
    selection is checked around the read, not cached as an authorization verdict.
    This observation does not establish present-day graph/input freshness.
    """
    if not _JOB_ID.fullmatch(run_id):
        raise HTTPException(404, "Unknown job/run ID")
    state = Path(root) / "state"
    try:
        selection = (state / "active.json").read_bytes()
        package_hash = json.loads(selection)["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(409, "Selected saved package is not verified") from None
    if not isinstance(package_hash, str) or not _ID.fullmatch(package_hash):
        raise HTTPException(409, "Selected saved package is not verified")
    if expected_package is not None and package_hash != expected_package:
        raise HTTPException(409, "Persisted project package does not match selection")
    try:
        record = json.loads((state / "agent-runs" / (run_id + ".json")).read_bytes())
    except (OSError, ValueError):
        raise HTTPException(404, "Unknown job/run ID") from None
    if not isinstance(record, dict) or record.get("id") != run_id or record.get("package_hash") != package_hash:
        raise HTTPException(404, "Unknown run in this package")
    progress = ({"status": "unverified", "source": "saved_terminal_observation"}
                if record.get("observation_snapshot") else _stored_agent_progress(root, record))
    try:
        unchanged = (state / "active.json").read_bytes() == selection
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(409, "Package selection changed during status read")
    record["workflow_name"] = (record.get("workflow_name")
                               or _saved_workflow_names(root, package_hash).get(record.get("workflow_id")))
    record["candidate_writer"] = _candidate_writer_status(root, run_id, record.get("package_hash"))
    if not record.get("phase") and record.get("status") in {"queued", "running", "cancelling"}:
        record["phase"] = "agent_analysis"
    return {**{k: v for k, v in record.items() if k != "output"},
            "live_activity": progress,
            "status_only": True, "observation_basis": "stored_run_record",
            "current_input_version": None, "stale": None,
            "input_freshness": "unverified_not_recomputed"}


def _stored_agent_runs(root, offset=0, limit=50, expected_package=None):
    """List atomic public metadata under the exact persisted active selection."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Invalid saved-run pagination")
    state = Path(root) / "state"
    try:
        selection = (state / "active.json").read_bytes()
        package_hash = json.loads(selection)["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(409, "Selected saved package is not verified") from None
    if not isinstance(package_hash, str) or not _ID.fullmatch(package_hash):
        raise HTTPException(409, "Selected saved package is not verified")
    if expected_package is not None and package_hash != expected_package:
        raise HTTPException(409, "Persisted project package does not match selection")
    items = []
    for path in (state / "agent-runs").glob("*.json"):
        if not _JOB_ID.fullmatch(path.stem):
            continue
        try:
            record = json.loads(path.read_bytes())
        except (OSError, ValueError):
            raise HTTPException(409, "Saved run history could not be verified") from None
        if not isinstance(record, dict) or record.get("id") != path.stem:
            raise HTTPException(409, "Saved run history could not be verified")
        if record.get("package_hash") == package_hash:
            public = {**{k: v for k, v in record.items() if k not in {"output", "observation_snapshot"}},
                          "status_only": True, "observation_basis": "stored_run_record",
                          "current_input_version": None, "stale": None,
                          "input_freshness": "unverified_not_recomputed"}
            public["candidate_writer"] = _candidate_writer_status(root, record["id"], record.get("package_hash"))
            items.append(public)
    try:
        unchanged = (state / "active.json").read_bytes() == selection
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(409, "Package selection changed during status read")
    workflow_names = _saved_workflow_names(root, package_hash)
    for record in items:
        record["workflow_name"] = (record.get("workflow_name")
                                   or workflow_names.get(record.get("workflow_id")))
        if not record.get("phase") and record.get("status") in {"queued", "running", "cancelling"}:
            record["phase"] = "agent_analysis"
    items.sort(key=lambda r: (str(r.get("created_at", "")), r["id"]), reverse=True)
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
            "status_only": True, "observation_basis": "stored_run_record",
            "active_run_present": any(_run_or_writer_active(r) for r in items),
            "run_status_verified": all(r.get("status") in {
                "queued", "running", "cancelling", "completed", "partial", "complete", "succeeded", "success",
                "failed", "error", "cancelled", "canceled", "rejected"}
                and (r.get("candidate_writer") is None or r["candidate_writer"].get("status") in {
                    "queued", "running", "cancelling", "completed", "partial", "complete", "succeeded", "success",
                    "failed", "error", "cancelled", "canceled", "rejected"}) for r in items),
            "truncated": offset + limit < len(items)}


class _Service:
    def __init__(self, data_dir, sample_path, runtime, *, executor_factory=ThreadPoolExecutor,
                 source_publisher=None, package_cache_scope="local"):
        self.root = Path(data_dir).resolve()
        self.sample = Path(sample_path).resolve()
        self.state = self.root / "state"
        self.packages = self.root / "packages"
        for directory in (self.state / "jobs", self.state / "agent-runs", self.packages, self.state / "uploads", self.root / "agents"):
            directory.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.state / "reviews.sqlite")
        self.runtime = runtime if runtime is not None else AgentRuntime(self.root / "agents")
        self.source_publisher = source_publisher
        self.package_cache_scope = package_cache_scope
        self.package_executor = executor_factory(max_workers=1, thread_name_prefix="workbench-package")
        self.agent_executor = executor_factory(max_workers=1, thread_name_prefix="workbench-agent")
        self.package_slot, self.agent_slot = BoundedSemaphore(1), BoundedSemaphore(1)
        self.lock = RLock()
        self.agent_cancel_events = {}
        self.agent_pause_controls = {}
        self.active = None
        self.active_job = None
        self.startup_error = None

    def start(self, progress=None):
        self.active = None
        self.active_job = None
        self.startup_error = None
        run_directories = [self.state / "jobs", self.state / "agent-runs"]
        run_directories.extend(self.root.glob("package-contexts/*/state/jobs"))
        for directory in run_directories:
            for path in directory.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record.get("status") in {"queued", "running", "cancelling"}:
                        record.update(status="failed", error="Process interrupted; not automatically resumed.", finished_at=_now())
                        if "pause_requested" in record:
                            record.update(pause_requested=False, pause_requested_at=None, paused=False,
                                          paused_at=None, pause_boundary=None, paused_boundary_count=0)
                        _atomic_json(path, record)
                except (OSError, ValueError):
                    self.startup_error = "An unreadable job record was ignored."
        active = self.state / "active.json"
        if active.exists():
            try:
                manifest = json.loads(active.read_text(encoding="utf-8"))
                package_hash = manifest["sha256"]
                if not _ID.fullmatch(package_hash):
                    raise ValueError("Invalid active package")
                directory = self.packages / package_hash
                persisted = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                if persisted["sha256"] != package_hash:
                    raise ValueError("Artifact checksum mismatch")
                index = PackageIndex.load(directory, progress=progress, cache_scope=self.package_cache_scope)
                if index.model["package"]["sha256"] != package_hash:
                    raise ValueError("Package mismatch")
                self.active = index
            except (OSError, ValueError, KeyError, EOFError):
                self.startup_error = "Active artifact could not be loaded or verified; load a package again."

    def close(self):
        # No false cancellation/timeout claims. Running jobs retain their slot and
        # persist completion even if a browser disconnects or lifespan closes.
        self.package_executor.shutdown(wait=True, cancel_futures=False)
        self.agent_executor.shutdown(wait=True, cancel_futures=False)

    def drain(self):
        """Wait for accepted package and agent work without shutting executors down."""
        for slot in (self.package_slot, self.agent_slot):
            slot.acquire()
            slot.release()

    def idle(self):
        acquired = []
        try:
            for slot in (self.package_slot, self.agent_slot):
                if not slot.acquire(blocking=False):
                    return False
                acquired.append(slot)
            return True
        finally:
            for slot in reversed(acquired):
                slot.release()

    def require(self):
        with self.lock:
            if self.active is None:
                raise HTTPException(409, "No package is ready; explicitly load the sample or upload XML")
            return self.active

    def workflow(self, index, workflow_id):
        if index.nodes.get(workflow_id, {}).get("kind") != "workflow":
            raise HTTPException(404, "Unknown canonical workflow ID")

    def record_path(self, record_id, agent=False):
        if not _JOB_ID.fullmatch(record_id):
            raise HTTPException(404, "Unknown job/run ID")
        return self.state / ("agent-runs" if agent else "jobs") / (record_id + ".json")

    def record(self, record_id, agent=False):
        path = self.record_path(record_id, agent)
        try:
            with self.lock:
                return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Unknown job/run ID") from None

    def update_record(self, record, agent=False):
        with self.lock:
            if agent:
                path = self.record_path(record["id"], agent=True)
                if path.is_file():
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if "observation_snapshot" in saved:
                        # Once committed, later metadata writes cannot revise
                        # the historical graph, counters, findings, or scope.
                        record["observation_snapshot"] = saved["observation_snapshot"]
                    if saved.get("cancellation_requested_at"):
                        record["cancellation_requested_at"] = saved["cancellation_requested_at"]
                control = self.agent_pause_controls.get(record["id"])
                if control is not None:
                    if record.get("status") not in {"queued", "running", "cancelling"}:
                        control.finish()
                    # The gate, not a worker's older record copy, owns these fields.
                    record.update(control.snapshot())
            _atomic_json(self.record_path(record["id"], agent), record)

    def package_job(self, source, filename, temporary=False, reserved=False):
        if not reserved and not self.package_slot.acquire(blocking=False):
            raise HTTPException(409, "A package job or upload is already in progress")
        record = {"id": uuid4().hex, "status": "queued", "kind": "package", "filename": filename, "created_at": _now()}
        try:
            self.update_record(record)
            with self.lock:
                self.active_job = record["id"]
            self.package_executor.submit(self._ingest, record, Path(source), filename, temporary)
        except Exception:
            if not reserved:
                self.package_slot.release()
            raise
        return record

    def _ingest(self, initial, source, filename, temporary):
        record = dict(initial)
        staging = self.packages / (".stage-" + record["id"])
        started = time.monotonic()
        failure_stage = "starting"
        try:
            record.update(status="running", phase="agent_analysis", started_at=_now())
            self.update_record(record)
            from workbench.intake_limits import max_xml_bytes
            package_limit = max_xml_bytes() if self.source_publisher is not None else MAX_PACKAGE_BYTES
            if source.stat().st_size > package_limit:
                raise ValueError("Package exceeds the configured XML byte limit")
            if self.source_publisher is not None:
                self.source_publisher(source, filename, _digest(source))
            failure_stage = "parsing_xml"
            model = analyze_package(source)
            if not any(n["kind"] == "powermart" and n["parent_id"] is None for n in model["nodes"]):
                raise ValueError("An Informatica POWERMART export is required")
            model["package"]["name"] = filename
            package_hash = model["package"]["sha256"]
            directory = self.packages / package_hash
            failure_stage = "copying_source"
            staging.mkdir()
            shutil.copyfile(source, staging / "source.xml")
            failure_stage = "writing_understanding"
            _write_model(staging / "understanding.json.gz", model)
            failure_stage = "writing_description"
            with (staging / "english.md").open("w", encoding="utf-8", newline="\n") as stream:
                stream.write("# Package structural understanding\n\n" + model["summary"] + "\n\n")
                stream.write("Lifecycle and runtime behavior are unverified. Full canonical fields and edges are in the compressed JSON export.\n\n")
                for node in model["nodes"]:
                    if node["kind"] != "instance_field":
                        stream.write(f"* {node['id']}: {node['summary']} Evidence: {node['evidence']['locator']}\n")
            manifest = {"sha256": package_hash, "filename": filename, "size_bytes": model["package"]["size_bytes"],
                        "schema_version": model["schema_version"], "counts": model["counts"],
                        "artifact_version": ARTIFACT_VERSION,
                        "analyzer_sha256": _digest(Path(__file__).with_name("understanding.py")),
                        "node_count": len(model["nodes"]), "edge_count": len(model["edges"]),
                        "understanding_sha256": _digest(staging / "understanding.json.gz"),
                        "artifacts": ["source.xml", "understanding.json.gz", "english.md"], "created_at": _now()}
            failure_stage = "writing_manifest"
            _atomic_json(staging / "manifest.json", manifest)
            failure_stage = "indexing_package"
            if directory.exists():
                # The parsed staging model may come from a newer analyzer.
                # Always activate the verified immutable artifact, never that
                # newly parsed model paired with an older on-disk export.
                index = PackageIndex.load(directory)
                shutil.rmtree(staging)
            else:
                staging.rename(directory)
                index = PackageIndex(model, directory)
            with _CACHE_LOCK:
                _CACHE[str(directory.resolve())] = index
            failure_stage = "activating_package"
            with self.lock:
                _atomic_json(self.state / "active.json", {"sha256": package_hash, "activated_at": _now()})
                self.active = index
                self.startup_error = None
            record.update(status="completed", package_hash=package_hash, node_count=len(index.model["nodes"]),
                          edge_count=len(index.model["edges"]), elapsed_seconds=round(time.monotonic() - started, 3))
        except Exception as exc:
            # Never include XML, filenames, provider exceptions or tracebacks.
            error = "Package parsing or persistence failed; previous active package is unchanged."
            if isinstance(exc, ValueError) and str(exc) in {"Package exceeds 80 MiB", "An Informatica POWERMART export is required", "Existing package artifact checksum mismatch"}:
                error = str(exc)
            error_type = type(exc).__name__
            diagnostic = {"stage": failure_stage, "error_type": error_type if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", error_type) else "Exception"}
            for key in ("errno", "winerror"):
                value = getattr(exc, key, None)
                if type(value) is int:
                    diagnostic[key] = value
            record.update(status="failed", error=error, error_diagnostic=diagnostic)
            audit = getattr(self, "audit", None)
            if audit is not None:
                try:
                    audit.append({"event": "package_import_failed", "level": "ERROR", "role": None,
                        "run_id": record["id"], "message": f"Package import failed during {failure_stage}: {diagnostic['error_type']}.",
                        "diagnostic": diagnostic})
                except Exception:
                    record["audit_status"] = "write_failed"
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if temporary:
                source.unlink(missing_ok=True)
            record["finished_at"] = _now()
            try:
                self.update_record(record)
            finally:
                with self.lock:
                    self.active_job = None
                self.package_slot.release()

    def lite_records(self, index, scenario):
        summaries = index.proposal_summaries(scenario)
        # Persist only immutable lite fields required by Store.save_review. Never
        # put graph/fields/evidence payload copies in SQLite on every review.
        lite = [{k: p[k] for k in ("id", "workflow_id", "name", "scenario", "version", "status", "blockers",
                                   "generation_allowed", "deployment_allowed")} for p in summaries]
        self.store.save_proposals(index.model["package"]["sha256"], lite)
        return summaries

    def evidence_version(self, index, workflow_id):
        documents = self.store.documents(index.model["package"]["sha256"])
        return _hash(sorted(d["id"] for d in documents if d.get("metadata", {}).get("workflow_id") in {None, "", workflow_id}))

    def agent_inputs(self, index, workflow_id, scenario):
        """One SQLite snapshot, scoped before hashing or passing to the runtime.

        Serialize at the submission boundary so the worker owns a detached,
        immutable value, not Store-backed objects or a live-read callback.
        This same function defines freshness for both queued and finished runs.
        """
        self.lite_records(index, scenario)
        state = self.store.load_state(index.model["package"]["sha256"])
        proposals = sorted((p for p in state["proposals"]
                            if p["workflow_id"] == workflow_id and p["scenario"] == scenario), key=lambda p: p["id"])
        proposal_ids = {p["id"] for p in proposals}
        documents = sorted((d for d in state["documents"]
                            if d.get("metadata", {}).get("workflow_id") in {None, "", workflow_id}), key=lambda d: d["id"])
        reviews = [r for r in state["reviews"] if r["proposal_id"] in proposal_ids]
        hashes = {"policy": _hash(agent_module._POLICY),
                  "skills": _hash({str(p.relative_to(agent_module.SKILLS_ROOT)): _digest(p)
                                   for p in sorted(agent_module.SKILLS_ROOT.rglob("*.md"))})}
        manifest = json.loads((index.directory / "manifest.json").read_text(encoding="utf-8"))
        snapshot = {"package_hash": index.model["package"]["sha256"], "workflow_id": workflow_id,
                    "scenario": scenario, "artifact_hash": manifest["understanding_sha256"],
                    "documents": documents, "reviews": reviews, "proposals": proposals, "hashes": hashes}
        if index.model.get("source_analysis_version"):
            snapshot["source_analysis_version"] = index.model["source_analysis_version"]
        return {"snapshot": json.dumps(snapshot, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False),
                "input_version": _hash(snapshot), "input_hashes": hashes,
                "input_versions": {"proposals": {p["id"]: p["version"] for p in proposals},
                                   "documents": _hash(documents), "reviews": _hash(reviews)},
                "evidence_version": _hash(sorted(d["id"] for d in documents))}

    def run_freshness(self, index, record):
        current = self.agent_inputs(index, record["workflow_id"], record["scenario"])["input_version"]
        return {**record, "current_input_version": current, "stale": record.get("input_version") != current}

    def proposals(self, index, scenario):
        summaries = self.lite_records(index, scenario)
        state = self.store.load_state(index.model["package"]["sha256"])
        current = {p["id"]: p for p in state["proposals"]}
        documents = state["documents"]
        items = []
        for summary in summaries:
            evidence = [d["id"] for d in documents if d.get("metadata", {}).get("workflow_id") in {None, "", summary["workflow_id"]}]
            items.append({**summary, **current.get(summary["id"], {}),
                          "evidence_version": _hash(sorted(evidence)), "supporting_evidence_count": len(evidence),
                          "stale": bool(evidence), "verification_status": "unverified",
                          "stale_reason": "Supporting evidence changed since structural proposal construction; independent revalidation required." if evidence else None})
        return items


def _filename(name, allowed):
    if not isinstance(name, str) or not name or len(name) > 200 or name in {".", ".."} or re.search(r'[/\\:\x00-\x1f\x7f]', name):
        raise HTTPException(400, "A safe display basename is required, not a path")
    if Path(name).suffix.lower() not in allowed:
        raise HTTPException(415, "Unsupported file type; XML only, ZIP archives are not supported" if allowed == {".xml"} else "Only .txt, .md and .pdf evidence files are supported")
    return name


async def _upload(request, service, allowed, maximum, suffix):
    """Bound multipart request before parsing, then stream a single file to disk."""
    form = await request.form(max_files=1, max_fields=4, max_part_size=16384)
    temp = None
    try:
        if set(form) - {"file", "workflow_id", "description"} or len(form.getlist("file")) != 1:
            raise HTTPException(400, "Exactly one file and supported metadata fields are required")
        upload = form.get("file")
        if not hasattr(upload, "filename") or not hasattr(upload, "read"):
            raise HTTPException(400, "A file upload is required")
        # python-multipart strips Windows drive paths from filename before we
        # see UploadFile.filename. Inspect the original part header as well so
        # path-bearing client input is rejected rather than silently sanitized.
        disposition = upload.headers.get("content-disposition", "")
        original_filename = re.search(r'filename\s*=\s*(?:"([^"]*)"|([^;]*))', disposition, re.I)
        if original_filename:
            raw_name = original_filename.group(1) if original_filename.group(1) is not None else original_filename.group(2)
            _filename(raw_name, allowed)
        name = _filename(upload.filename, allowed)
        descriptor, name_on_disk = tempfile.mkstemp(suffix=suffix, dir=service.state / "uploads")
        temp = Path(name_on_disk)
        size = 0
        with os.fdopen(descriptor, "wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, "Uploaded file exceeds the size limit")
                stream.write(chunk)
        if not size:
            raise HTTPException(400, "Empty files are not supported")
        metadata = {k: form.get(k, "") for k in ("workflow_id", "description")}
        if any(not isinstance(value, str) or len(value) > 12000 for value in metadata.values()):
            raise HTTPException(422, "Upload metadata must be bounded text")
        return temp, name, metadata
    except Exception:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise
    finally:
        await form.close()


def _document_text(path, name):
    if Path(name).suffix.lower() != ".pdf":
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            raise HTTPException(422, "Text documents must be UTF-8 encoded") from None
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HTTPException(503, "PDF extraction requires the optional pypdf dependency") from None
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise HTTPException(422, "Encrypted PDFs are not supported")
        if len(reader.pages) > 500:
            raise HTTPException(422, "PDF exceeds the 500-page extraction limit")
        pieces, size = [], 0
        for page in reader.pages:
            # Input/page/extracted-text bounds are not a hard PDF CPU or memory
            # sandbox. Extraction runs off the request event loop.
            text = page.extract_text() or ""
            size += len(text)
            if size > MAX_DOCUMENT_BYTES:
                raise HTTPException(422, "Extracted PDF text exceeds the size limit")
            pieces.append(text)
        result = "\n".join(pieces)
        if not result.strip():
            raise HTTPException(422, "No text could be extracted from this PDF; OCR is not provided")
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Invalid PDF or text extraction failed") from None


def _document_display(document):
    text = document.get("text", "")
    return {**document, "text": text[:4000], "text_length": len(text), "text_truncated": len(text) > 4000,
            "verification_status": "unverified", "actor_authenticated": False}


def _create_package_app(data_dir=None, sample_path=None, agent_runtime=None, *,
                        executor_factory=ThreadPoolExecutor, source_publisher=None, model_installer=None,
                        record_audit_initialization=True, package_cache_scope="local"):
    """Create an isolated FastAPI app; nothing is ingested or invoked implicitly.

    data_dir defaults to output/workbench. sample_path is a trusted local factory
    setting, not a user-provided HTTP path. Runtime injection is for offline tests;
    HTTP execution still requires WORKBENCH_MODEL and consent exactly true.
    """
    service = _Service(data_dir or ROOT / "output" / "workbench", sample_path or ROOT / "input" / "LumenExample.XML", agent_runtime,
                       executor_factory=executor_factory, source_publisher=source_publisher,
                       package_cache_scope=package_cache_scope)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(service.start)
        projects = getattr(app.state, "projects", None)
        if projects is not None:
            await run_in_threadpool(projects.start)
        try:
            yield
        finally:
            if projects is not None:
                await run_in_threadpool(projects.close)
            await run_in_threadpool(service.close)

    app = FastAPI(title="Local evidence workbench", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.add_middleware(_LocalBoundary)

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        message = str(_redact(str(exc)))[:2000]
        return JSONResponse({"detail": message}, status_code=409 if "conflict" in message.lower() or "Approval blocked" in message else 422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "local_only": True, "authentication": "not_implemented", "heartbeat_enabled": False,
            "capabilities": _scope_capabilities(),
            "capability_inventory": capability_inventory(),
                "generation_allowed": False, "deployment_allowed": False}

    @app.get("/api/tracing/status")
    def trace_status():
        from workbench.tracing import tracing_status
        return tracing_status()

    @app.get("/api/overview")
    def overview():
        with service.lock:
            index, job_id = service.active, service.active_job
        hosted_authenticated = getattr(app.state, "hosted_request_authenticated", False) is True
        agent = _overview_agent(service.runtime, hosted_authenticated)
        return _overview_payload(index, job_id, agent, service.startup_error, hosted_authenticated)

    @app.get("/api/workflow-invocation-summary")
    def workflow_invocation_summary():
        index = service.require()
        from workbench.package_context import workflow_invocation_summary as summarize
        return summarize(index)

    @app.post("/api/packages/sample", status_code=202)
    def sample():
        if not service.sample.is_file():
            raise HTTPException(404, "Configured sample is unavailable")
        if service.sample.stat().st_size > MAX_PACKAGE_BYTES:
            raise HTTPException(413, "Sample exceeds 80 MiB")
        return service.package_job(service.sample, service.sample.name)

    @app.post("/api/packages/upload", status_code=202)
    async def upload_package(request: Request):
        if not service.package_slot.acquire(blocking=False):
            raise HTTPException(409, "A package job or upload is already in progress")
        temp = None
        submitted = False
        try:
            temp, name, _ = await _upload(request, service, {".xml"}, MAX_PACKAGE_BYTES, ".xml")
            record = service.package_job(temp, name, temporary=True, reserved=True)
            submitted = True
            return record
        finally:
            if not submitted:
                if temp:
                    temp.unlink(missing_ok=True)
                service.package_slot.release()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        record = service.record(job_id)
        if (record.get("kind") == "spark_draft"
                and record.get("package_hash") != service.require().model["package"]["sha256"]):
            raise HTTPException(404, "Unknown draft job in this package")
        return record

    @app.get("/api/jobs/{job_id}/spark-drafts")
    def spark_draft_result(job_id: str):
        """Read only committed, checksum-bound manifest paths, never HTTP paths."""
        from workbench.draft_artifact_reads import read_result
        from workbench.native_job_draft import build, graph_metadata
        from workbench.spark_codegen import ContractError
        from workbench.redesign import store_for
        record = job(job_id)
        result = read_result(service.root, record, job_id)
        manifest = result["manifest"]
        index = service.require()
        for item in manifest.get("human_review_items", []):
            if not isinstance(item, dict):
                continue
            sources = []
            for evidence_id in item.get("evidence_ids", []):
                source = index.nodes.get(evidence_id)
                if source is None:
                    continue
                scope = source.get("scope") if isinstance(source.get("scope"), dict) else {}
                location = "/".join(part for part in (scope.get("repository"), scope.get("folder")) if part)
                sources.append({"name": source.get("name") or "Unnamed source object",
                                "kind": source.get("kind") or "Source object",
                                "location": location or "Workflow source"})
            item["evidence_sources"] = sources
        # Derive a schema-checked, non-uploaded Jobs/YAML draft from the exact
        # committed notebook bytes. An incomplete/blocked writer receipt has no
        # deployable-looking graph and reports the reason instead.
        try:
            design = store_for(service).get(manifest["design_id"], manifest["package_hash"],
                                            manifest["design_version"])
            if design["input_version"] != manifest["input_version"]:
                raise ValueError("Design input version mismatch")
            candidate_draft = manifest.get("draft_authority") in {
                "explicit_agent_run_candidate", "explicit_current_design_candidate",
                "explicit_parent_candidate_retry"}
            if (not candidate_draft and design["design"].get("blockers")) or (not candidate_draft and any(
                    item.get("impact") == "execution_blocker"
                    for item in design["design"].get("scoped_unresolved", []))):
                raise ValueError("Design retains execution blockers")
            if manifest.get("status") != "generation_complete" or any(
                    item.get("status") != "drafted" for item in manifest.get("tasks", [])):
                raise ValueError("One or more tasks remain blocked")
            workspace_root = (f"/Workspace/Shared/ORION/{record['workflow_id'][:12]}"
                              f"_{manifest['design_id'][:12]}_v{manifest['design_version']}")
            yaml_text = build(design["design"], manifest, result["artifacts"], workspace_root)
            native_jobs = graph_metadata(yaml_text, manifest, result["artifacts"], workspace_root)
            result["native_job"] = {"yaml": yaml_text, "native_jobs": native_jobs,
                                    "workspace_root": workspace_root,
                                    "execution_allowed": False, "deployment_allowed": False,
                                    "remote_validation": False}
        except ContractError as error:
            from workbench.redaction import redact_text
            detail = redact_text(str(error))[:800]
            result["native_job"] = None
            result["native_job_blocker"] = ("Jobs YAML validation blocked: " + detail) if detail else "Jobs YAML failed static validation."
        except Exception:
            result["native_job"] = None
            result["native_job_blocker"] = "Jobs YAML is unavailable because at least one task is blocked or lacks a validated notebook."
        from workbench.migration_evaluation import assess as assess_migration
        result["migration_evaluation"] = assess_migration(index, store_for(service).get(
            manifest["design_id"], manifest["package_hash"], manifest["design_version"]),
            manifest, {**result.get("staged_artifacts", {}), **result["artifacts"]},
            native_available=result["native_job"] is not None)
        return result

    @app.get("/api/jobs/{job_id}/spark-drafts/download")
    def download_spark_draft_artifact(job_id: str, path: str = Query(min_length=1, max_length=512)):
        """Download one exact checksum-verified saved artifact as an attachment."""
        result = spark_draft_result(job_id)
        content = result["artifacts"].get(path)
        if content is None:
            content = result.get("staged_artifacts", {}).get(path)
        if not isinstance(content, str):
            raise HTTPException(404, "Saved artifact was not found in this result")
        filename = path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", filename):
            raise HTTPException(422, "Saved artifact filename is invalid")
        return Response(content=content,
                        media_type="application/json" if filename.endswith((".ipynb", ".json")) else "text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/jobs/{job_id}/completeness")
    def spark_draft_completeness(job_id: str):
        from workbench.draft_completeness import assess
        from workbench.redesign import store_for
        from workbench.spark_codegen import _approved
        result = spark_draft_result(job_id)
        manifest = result["manifest"]
        try:
            latest = store_for(service).get(manifest["design_id"], manifest["package_hash"])
            exact = store_for(service).get(manifest["design_id"], manifest["package_hash"], manifest["design_version"])
        except (KeyError, ValueError):
            raise HTTPException(409, "Draft design is not available in this package") from None
        stale = True
        if latest["version"] == exact["version"]:
            try:
                _, current, inputs = _approved(service, exact, require_review=False)
                stale = not (current["input_version"] == manifest.get("input_version")
                             and inputs["input_hashes"] == manifest.get("input_hashes")
                             and inputs["input_versions"] == manifest.get("input_versions"))
            except Exception:
                stale = True
        return {"job_id": job_id, **assess(exact["design"], manifest, result["artifacts"],
                                             staged_artifacts=result.get("staged_artifacts", {}), stale=stale)}

    @app.post("/api/jobs/{job_id}/native-job-draft")
    def native_job_draft(job_id: str, body: NativeJobDraftBody):
        from workbench.native_job_draft import build, graph_metadata
        from workbench.redesign import store_for
        from workbench.spark_codegen import ContractError, _approved, _digest as design_digest
        if not body.consent:
            raise HTTPException(403, "Explicit consent is required to build a Jobs draft")
        result = spark_draft_result(job_id)
        manifest = result['manifest']
        try:
            design = store_for(service).get(manifest['design_id'], manifest['package_hash'], manifest['design_version'])
            _, design, inputs = _approved(service, design, require_review=False)
            if (manifest.get('design_sha256') != design_digest(design['design'])
                    or manifest.get('input_version') != design['input_version']
                    or manifest.get('input_hashes') != inputs['input_hashes']
                    or manifest.get('input_versions') != inputs['input_versions']):
                raise ContractError('Draft manifest does not match the current approved design and inputs')
            yaml = build(design['design'], manifest, result['artifacts'], body.workspace_root)
            native_jobs = graph_metadata(yaml, manifest, result['artifacts'], body.workspace_root)
        except (KeyError, ValueError, ContractError) as error:
            detail = str(error) if isinstance(error, ContractError) else 'Current design or task definition unavailable'
            raise HTTPException(409, detail) from None
        return {'job_id': job_id, 'design_id': manifest['design_id'], 'design_version': manifest['design_version'],
            'native_jobs': native_jobs,
                'yaml': yaml, 'status': 'draft_unverified', 'remote_validation': False,
                'semantic_parity': 'not_established', 'execution_allowed': False, 'deployment_allowed': False}

    def checked_bundle_inputs(job_id):
        from workbench.redesign import store_for
        from workbench.spark_codegen import ContractError, _approved, _digest as design_digest
        record = job(job_id)
        result = spark_draft_result(job_id)
        manifest = result['manifest']
        try:
            expected = store_for(service).get(manifest['design_id'], manifest['package_hash'], manifest['design_version'])
            _, design, inputs = _approved(service, expected, require_review=False)
            if (manifest.get('design_sha256') != design_digest(design['design'])
                    or manifest.get('input_version') != design['input_version']
                    or manifest.get('input_hashes') != inputs['input_hashes']
                    or manifest.get('input_versions') != inputs['input_versions']):
                raise ContractError('Draft manifest does not match the current approved design and inputs')
            return record, design, result
        except (KeyError, ValueError, ContractError) as error:
            detail = str(error) if isinstance(error, ContractError) else 'Current design unavailable'
            raise HTTPException(409, detail) from None

    @app.post('/api/jobs/{job_id}/native-bundles')
    def publish_native_bundle(job_id: str, body: NativeJobDraftBody):
        from workbench.draft_bundle import publish
        from workbench.spark_codegen import ContractError
        if body.consent is not True:
            raise HTTPException(403, 'Explicit consent is required to publish a draft bundle')
        try:
            with service.lock:
                record, design, result = checked_bundle_inputs(job_id)
                published = publish(service, record, design, result, body.workspace_root)
                return {key: value for key, value in published.items() if key != 'artifacts'}
        except (ContractError, OSError, KeyError, TypeError):
            raise HTTPException(409, 'Draft bundle publication failed; inspect saved state before retrying') from None

    @app.get('/api/jobs/{job_id}/native-bundles')
    def list_native_bundles(job_id: str):
        from workbench.draft_bundle import _bundle_root, read
        from workbench.spark_codegen import ContractError
        try:
            record, design, result = checked_bundle_inputs(job_id)
            parent = _bundle_root(service, record) / 'native-bundles'
            if parent.is_symlink():
                raise ContractError('Invalid draft bundle directory')
            if not parent.exists():
                return {'job_id': job_id, 'bundles': []}
            paths = sorted(parent.iterdir())
            if len(paths) > 100 or any(not _ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir() for path in paths):
                raise ContractError('Invalid draft bundle inventory')
            bundles = []
            for path in paths:
                try:
                    value = read(service, record, design, result, path.name)
                    bundles.append({'bundle_id': path.name, 'status': value['status'], 'receipt': value['receipt']})
                except ContractError:
                    bundles.append({'bundle_id': path.name, 'status': 'needs_reconciliation'})
            return {'job_id': job_id, 'bundles': bundles}
        except (ContractError, OSError, KeyError, TypeError):
            raise HTTPException(409, 'Draft bundle inventory is unavailable or invalid') from None

    @app.get('/api/jobs/{job_id}/native-bundles/{bundle_id}')
    def read_native_bundle(job_id: str, bundle_id: str):
        from workbench.draft_bundle import read
        from workbench.spark_codegen import ContractError
        try:
            record, design, result = checked_bundle_inputs(job_id)
            return read(service, record, design, result, bundle_id)
        except (ContractError, OSError, KeyError, TypeError):
            raise HTTPException(409, 'Draft bundle is unavailable or failed integrity validation') from None

    def release_inputs(body):
        from workbench.draft_bundle import read
        from workbench.release_preview import preview
        from workbench.cohort_review import _db, _inventory, reviewed_members
        from workbench.spark_codegen import ContractError
        if body.consent is not True:
            raise HTTPException(403, 'Explicit export consent is required, not deployment consent')
        members = None
        try:
            with service.lock:
                if body.cohort_id is not None:
                    index = service.require()
                    package = index.model['package']['sha256']
                    with _db(service) as db:
                        row = db.execute('SELECT payload FROM cohort_runs WHERE package_hash=? AND id=?',
                            (package, body.cohort_id)).fetchone() if db else None
                    if row is None:
                        raise HTTPException(404, 'Unknown saved review in this package')
                    request = json.loads(row['payload'])['request']
                    inventory = _inventory(index, request['workflow_id'], request['scope'], request['input_version'])
                    members = reviewed_members(index, inventory, request)
                bundles, scenarios = [], set()
                for selection in body.bundles:
                    record, design, result = checked_bundle_inputs(selection.job_id)
                    scenarios.add(design['scenario'])
                    bundles.append(read(service, record, design, result, selection.bundle_id))
                if len(scenarios) != 1 or (body.cohort_id is not None and next(iter(scenarios)) != request['scenario']):
                    raise ValueError('Scenario does not match chosen review')
                return preview(bundles, members)
        except (ContractError, ValueError, KeyError, TypeError, OSError):
            raise HTTPException(409, 'Exact approved bundle or chosen member scope is unavailable. Refresh saved records; no deployment performed.') from None

    @app.post('/api/releases/preview')
    def release_preview(body: ReleaseSelection):
        return release_inputs(body)

    @app.post('/api/releases/archive')
    def release_archive(body: ReleaseSelection):
        from workbench.release_preview import archive
        result = release_inputs(body)
        return Response(archive(result), media_type='application/zip', headers={
            'Content-Disposition': 'attachment; filename="reviewed-migration-dab.zip"',
            'X-Workbench-Export-SHA256': result['export_sha256']})

    def run_spark_draft(initial, expected):
        record = dict(initial)
        directory = (service.root / "generated-drafts" / expected["package_hash"]
                     / expected["id"] / str(expected["version"]))
        try:
            record.update(status="running", phase="agent_analysis", started_at=_now())
            service.update_record(record)
            from workbench.spark_codegen import draft_approved_design
            output = draft_approved_design(service, expected, consent=True)
            # The engine uses nonhidden service-root paths and commits manifest
            # last. Hosted ActionExecutor drains before DeltaRuntime.flush.
            manifest = service.root / output["relative_directory"] / "manifest.json"
            record.update(status="completed", output=output, manifest_sha256=_digest(manifest))
        except Exception as exc:
            from workbench.databricks_models import safe_failure_diagnostic
            record.update(status="failed", failure_diagnostic=safe_failure_diagnostic(exc, 'writer_invocation'),
                          error="Drafting failed or approval became stale. No execution or deployment was performed.")
        finally:
            record["finished_at"] = _now()
            try:
                # The engine writes the checkpoint directly with the Delta
                # authority's checkpoint_*.sqlite classifier name. No rename
                # or post-run database mutation is needed.
                service.update_record(record)
            finally:
                service.agent_slot.release()

    @app.post("/api/redesigns/{redesign_id}/spark-drafts", status_code=202)
    def start_spark_draft(redesign_id: str, body: SparkDraftBody):
        from workbench.redesign import store_for
        from workbench.spark_codegen import ContractError, _approved
        if body.consent is not True:
            raise HTTPException(403, "Explicit model data-egress and drafting consent:true is required for every draft")
        with service.lock:
            index = service.require()
            package_hash = index.model["package"]["sha256"]
            try:
                expected = store_for(service).get(redesign_id, package_hash, body.version)
            except KeyError:
                raise HTTPException(404, "Unknown redesign version in this package") from None
            try:
                _approved(service, expected, require_review=False)
            except ContractError:
                raise HTTPException(409, "The exact latest design and current inputs must be verified before drafting") from None
            if not service.agent_slot.acquire(blocking=False):
                raise HTTPException(409, "An agent or draft run is already in progress; no additional runs are queued")
            record = {"id": uuid4().hex, "kind": "spark_draft", "status": "queued", "created_at": _now(),
                      "package_hash": package_hash, "design_id": expected["id"], "design_version": body.version,
                      "workflow_id": expected["workflow_id"], "scenario": expected["scenario"],
                      "input_version": expected["input_version"], "consent": True,
                      "timeout_policy": "no_elapsed_time_cutoff_manual_cancellation"}
            try:
                service.update_record(record)
                # Reuse the injected executor, including hosted request/OBO context.
                service.agent_executor.submit(run_spark_draft, record, expected)
            except Exception:
                record.update(status="failed", error="Draft worker could not be queued.", finished_at=_now())
                try:
                    service.update_record(record)
                finally:
                    service.agent_slot.release()
                raise HTTPException(503, "Draft worker could not be queued") from None
        return record

    @app.get("/api/package-graph")
    def package_graph(kind: Literal["workflow", "mapping", "source", "target"] = "workflow",
                      offset: int = Query(default=0, ge=0), limit: int = Query(default=60, ge=1, le=100),
                      all_nodes: bool = Query(default=False, alias="all")):
        from workbench.package_graph import package_graph as build_graph
        if all_nodes and offset != 0:
            raise HTTPException(422, "All nodes requires zero offset")
        return build_graph(service.require(), kind, offset, limit, all_nodes=all_nodes)

    @app.get('/api/package-graph/chunk')
    def workflow_graph_chunk(offset: int = Query(default=0, ge=0),
                             limit: int = Query(default=100, ge=1, le=100)):
        from workbench.package_graph import package_graph as build_graph
        return build_graph(service.require(), 'workflow', offset, limit, chunk=True)

    @app.get("/api/objects")
    def objects(kind: str = "", q: str = Query(default="", max_length=200),
            offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
            inventory: bool = False, input_version: str | None = None):
        index = service.require()
        if inventory:
            from workbench.inventory_browser import inventory_response
            return inventory_response(index, kind, q, offset, limit, input_version)
        candidates = index.kinds.get(kind, ()) if kind else index.model["nodes"]
        term, total, items = q.casefold(), 0, []
        for node in candidates:
            if term and term not in node["name"].casefold():
                continue
            if offset <= total < offset + limit:
                items.append(_brief(node))
            total += 1
        return {"items": items, "total": total, "offset": offset, "limit": limit, "truncated": offset + len(items) < total}

    @app.get("/api/object/{object_id}")
    def object_detail(object_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        from workbench.object_views import object_response
        return object_response(service.require(), object_id, offset, limit)

    @app.get("/api/workflows/{workflow_id}/migration-context")
    def migration_context(workflow_id: str, offset: int = Query(default=0, ge=0),
                          limit: int = Query(default=40, ge=1, le=200)):
        from workbench.object_views import migration_response
        return migration_response(service.require(), workflow_id, offset, limit)

    @app.get("/api/workflows/{workflow_id}/target-review")
    def review_targets(workflow_id: str, scope: Literal["workflow", "cohort"] = "workflow",
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
                       input_version: str | None = None):
        from workbench.target_review import target_review
        try:
            return target_review(service.require(), workflow_id, scope, offset, limit, input_version)
        except ValueError:
            raise HTTPException(422, "Target review is unavailable for this workflow or evidence") from None

    from workbench.source_analysis import install_source_analysis
    install_source_analysis(app, service)

    from workbench.inventory_browser import install_inventory_browser
    install_inventory_browser(app, service)

    from workbench.folder_migration import install_folder_migration
    install_folder_migration(app, service)

    @app.post("/api/proposals/sql-drafts")
    def sql_drafts(body: dict):
        from workbench.sql_artifacts import compile_sql_artifacts
        if set(body) != {"workflow_id", "consent"} or body.get("consent") is not True:
            raise HTTPException(422, "Explicit artifact drafting consent and workflow_id are required")
        index = service.require()
        service.workflow(index, body["workflow_id"])
        result = compile_sql_artifacts(index.model, body["workflow_id"])
        return {"workflow_id": body["workflow_id"], "manifest": result.manifest,
                "artifacts": result.artifacts, "deployment_allowed": False,
                "notice": "SQL syntax drafts, not semantic parity or implementation of non-SQL transformations. Missing details block affected tasks, not the entire design."}

    def compile_scoped_workflow_artifacts(body: WorkflowArtifactsBody):
        from workbench.workflow_artifact_responses import compile_response
        if body.consent is not True:
            raise HTTPException(403, "Explicit workflow artifact generation consent:true is required")
        # The package dispatcher already selected the request's project/package
        # and hosted OBO context. Compile its active canonical model, never XML.
        index = service.require()
        return compile_response(index, body)

    @app.post("/api/proposals/workflow-artifacts")
    def workflow_artifacts(body: WorkflowArtifactsBody):
        return compile_scoped_workflow_artifacts(body)

    @app.post("/api/proposals/workflow-artifacts/archive")
    def workflow_artifacts_archive(body: WorkflowArtifactsBody):
        from workbench.workflow_artifact_responses import archive_response
        result = compile_scoped_workflow_artifacts(body)
        return archive_response(result, body.workflow_id)

    @app.post("/api/proposals/demo-conversion")
    def demo_conversion(body: dict):
        from workbench.demo_conversion import compile_demo, DemoBindings
        if set(body) != {"workflow_id", "bindings", "consent"} or body.get("consent") is not True:
            raise HTTPException(422, "Explicit consent and a reviewed isolated test binding contract are required")
        index = service.require()
        service.workflow(index, body["workflow_id"])
        try:
            bindings = DemoBindings.model_validate(body["bindings"])
        except ValueError:
            raise HTTPException(422, "Invalid isolated demo contract") from None
        # Always derive from original XML bytes using the current analyzer.
        # Do not silently overwrite the persisted analysis or existing reviews.
        model = analyze_package(index.directory / "source.xml")
        return compile_demo(model, body["workflow_id"], bindings)

    @app.get("/api/proposals")
    def proposals(scenario: Literal["original", "bronze"] = "original",
                  offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
                  workflow_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
        index = service.require()
        if workflow_id is not None:
            service.workflow(index, workflow_id)
        items = service.proposals(index, scenario)
        total = len(items)
        if workflow_id is not None:
            items = [item for item in items if item["workflow_id"] == workflow_id]
            total = len(items)
        page = items[offset:offset + limit]
        # Candidate cards need only bounded list metadata. The graph and evidence
        # detail are fetched for the selected workflow, never serialized for all
        # 243 candidates in a single Apps response.
        summaries = [{k: item[k] for k in ("id", "workflow_id", "name", "scenario", "version", "status",
                                                "blockers", "generation_allowed", "deployment_allowed", "summary")}
                     | {"coverage": {k: item["coverage"].get(k) for k in (
                         "scoped_objects", "mapping_count", "unresolved_count", "redacted_connection_count",
                         "semantic_status",
                         "lifecycle", "scope_status", "connection_status", "structural_only")}}
                     for item in page]
        return {"items": summaries, "total": total, "offset": offset, "limit": limit,
                "truncated": offset + len(summaries) < total, "scenario": scenario, "summary_only": True,
                "graph_node_limit": MAX_GRAPH_NODES, "evidence_limit": MAX_EVIDENCE,
                "build_seconds": index.proposal_build_seconds}

    @app.get("/api/proposals/{proposal_id}")
    def proposal(proposal_id: str, scenario: Literal["original", "bronze"] = "original"):
        for item in service.proposals(service.require(), scenario):
            if item["id"] == proposal_id:
                return item
        raise HTTPException(404, "Unknown proposal in this package/scenario")

    @app.get("/api/impact/{workflow_id}")
    def impact(workflow_id: str):
        index = service.require()
        service.workflow(index, workflow_id)
        return index.impact(workflow_id)

    @app.get("/api/reviews")
    def reviews(offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
                proposal_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
        items = service.store.get_reviews(service.require().model["package"]["sha256"])
        if proposal_id is not None:
            items = [item for item in items if item["proposal_id"] == proposal_id]
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
                "truncated": offset + limit < len(items), "actor_authenticated": False}

    @app.post("/api/reviews")
    def review(body: ReviewBody):
        index = service.require()
        for scenario in ("original", "bronze"):
            service.lite_records(index, scenario)
        result = service.store.save_review(index.model["package"]["sha256"], **body.model_dump())
        # Merge only immutable display fields. A fresh Store read could race a
        # later commit and mix its version/status with this transaction's event.
        summaries = index.proposal_summaries(result["scenario"])
        return {**next(p for p in summaries if p["id"] == result["id"]), **result, "actor_authenticated": False}

    @app.get("/api/documents")
    def documents(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        items = service.store.documents(service.require().model["package"]["sha256"])
        return {"items": [_document_display(d) for d in items[offset:offset + limit]], "total": len(items),
                "offset": offset, "limit": limit, "truncated": offset + limit < len(items)}

    @app.post("/api/documents", status_code=201)
    async def document_upload(request: Request):
        index = service.require()
        temp, name, metadata = await _upload(request, service, {".txt", ".md", ".pdf"}, MAX_DOCUMENT_BYTES, ".evidence")
        try:
            if metadata["workflow_id"]:
                service.workflow(index, metadata["workflow_id"])
            text = await run_in_threadpool(_document_text, temp, name)
            if not text.strip():
                raise HTTPException(422, "Document has no extractable text")
            raw_hash = await run_in_threadpool(_digest, temp)
            directory = index.directory / "evidence"
            directory.mkdir(exist_ok=True)
            destination = directory / (raw_hash + Path(name).suffix.lower())
            if not destination.exists():
                os.replace(temp, destination)
            record = await run_in_threadpool(service.store.save_document, index.model["package"]["sha256"], name, text,
                                             {**metadata, "sha256": raw_hash, "stored_name": destination.name,
                                              "verification_status": "unverified", "actor": "local-reviewer",
                                              "actor_authenticated": False, "raw_bytes_sensitive": True})
            return _document_display(record)
        finally:
            temp.unlink(missing_ok=True)

    @app.post("/api/attestations", status_code=201)
    def attestation(body: AttestationBody):
        index = service.require()
        service.workflow(index, body.workflow_id)
        choices = {"lifecycle": {"active", "inactive", "retired", "unknown"},
                   "migration_scope": {"in_scope", "out_of_scope", "deferred", "unknown"},
                   "invocation": {"scheduled", "manual", "external", "unknown"}}
        if body.value not in choices[body.field]:
            raise HTTPException(422, "Unsupported value for this attestation field")
        metadata = {**body.model_dump(exclude={"comment"}), "kind": "owner_attestation", "verification_status": "unverified",
                    "actor_authenticated": False, "generation_allowed": False, "deployment_allowed": False}
        record = service.store.save_document(index.model["package"]["sha256"], "owner-attestation.md", body.comment, metadata)
        return {**_document_display(record), "blockers_cleared": False}

    @app.get("/api/settings")
    def settings():
        return {"settings": service.store.settings(service.require().model["package"]["sha256"]),
                "allowed_keys": SETTINGS_KEYS, "connectivity_verified": False}

    @app.post("/api/settings")
    async def save_settings(request: Request):
        index = service.require()
        try:
            values = await request.json()
        except ValueError:
            raise HTTPException(422, "Settings must be a JSON object") from None
        if not isinstance(values, dict):
            raise HTTPException(422, "Settings must be a JSON object")
        if set(values) - set(SETTINGS_KEYS):
            raise HTTPException(422, "Only advertised nonsecret settings keys are accepted")
        result = await run_in_threadpool(service.store.settings, index.model["package"]["sha256"], values)
        return {"settings": result, "allowed_keys": SETTINGS_KEYS, "connectivity_verified": False}

    @app.get("/api/export/understanding")
    def export_understanding():
        return FileResponse(service.require().directory / "understanding.json.gz", media_type="application/gzip", filename="understanding.json.gz")

    @app.get("/api/export/english")
    def export_english():
        return FileResponse(service.require().directory / "english.md", media_type="text/markdown", filename="english.md")

    def run_agent(initial, index, body, snapshot, cancellation_event):
        record = dict(initial)
        failure_stage = "runtime"
        try:
            if cancellation_event.is_set():
                try:
                    latest = service.record(record["id"], agent=True)
                    record.update({k: latest[k] for k in ("cancellation_requested_at",) if k in latest})
                except HTTPException:
                    pass
            record.update(status="cancelling" if cancellation_event.is_set() else "running",
                          phase="agent_analysis", started_at=_now())
            service.update_record(record, agent=True)
            safe_prompt = _redact(body.prompt)
            assert isinstance(safe_prompt, str)
            captured = json.loads(snapshot)
            knowledge_options = {"knowledge_preview_consent": True} if body.knowledge_preview_consent else {}

            def model_progress(event):
                if event.get("event") not in {"model_retry_wait", "model_retry_resuming", "model_retry_succeeded"}:
                    return
                if cancellation_event.is_set():
                    latest = service.record(record["id"], agent=True)
                    record.update(status="cancelling", **{
                        k: latest[k] for k in ("cancellation_requested_at",) if k in latest})
                record.update(phase=event["event"], model_retry={
                    k: event[k] for k in ("event", "role", "status_code", "retry_count", "retry_wait_seconds")
                    if k in event})
                try:
                    service.update_record(record, agent=True)
                except Exception as error:
                    from workbench.databricks_models import safe_failure_diagnostic
                    record['progress_write_diagnostic'] = safe_failure_diagnostic(
                        error, 'agent_retry_progress_persistence')

            output = service.runtime.run(index.model, body.workflow_id, body.scenario, safe_prompt,
                                         captured["documents"], captured["reviews"],
                                         cancellation_event=cancellation_event,
                                                      progress_callback=model_progress, **knowledge_options,
                                                      **({"pause_control": service.agent_pause_controls[record["id"]]}
                                                          if record["id"] in service.agent_pause_controls else {}))
            if not isinstance(output, dict):
                raise ValueError("Invalid runtime output")
            safe_output = _redact(output)
            assert isinstance(safe_output, dict)
            # Retain completed runtime work even when capture or drafting fails.
            failure_stage = "persist_runtime_output"
            record.update(output=safe_output, phase="runtime_output_retained")
            service.update_record(record, agent=True)
            failure_stage = "capture_redesign"
            from workbench.redesign import capture_run
            saved_redesigns = capture_run(service, index, {**record, "redesign_snapshot": captured}, output)
            record["redesign_refs"] = [{"id": item["id"], "version": item["version"]} for item in saved_redesigns]
            failure_stage = "candidate_code_generation"
            candidate_drafts = []
            if (body.generate_code and saved_redesigns
                    and output.get("status") not in {"failed", "unavailable", "proposal_failed", "cancelled"}
                    and not cancellation_event.is_set()):
                record.update(phase="candidate_code_generation", design_finished_at=_now(),
                              phase_updated_at=_now())
                service.update_record(record, agent=True)
                # The single user-invoked Agent activity action includes a
                # clearly unapproved candidate-code draft for each exact
                # submitted design. No second manual loop, review approval,
                # execution or deployment is implied.
                from workbench.redesign import store_for
                from workbench.spark_codegen import draft_agent_design
                # One selected workflow/scenario, one final design, one writer.
                # Earlier revisions stay in history and are never drafted here.
                selected_designs = [submitted for submitted in saved_redesigns
                    if submitted.get('workflow_id') == record['workflow_id']
                    and submitted.get('scenario') == record['scenario']
                    and submitted.get('package_hash') == record['package_hash']]
                final_designs = [max(selected_designs, key=lambda submitted: submitted['version'])] if selected_designs else []
                for design_ref in final_designs:
                    record.update(phase="candidate_code_generation:1/1", phase_updated_at=_now())
                    service.update_record(record, agent=True)
                    draft_job = {"id": uuid4().hex, "kind": "spark_draft", "status": "running",
                                 "created_at": _now(), "started_at": _now(),
                                 "package_hash": record["package_hash"], "design_id": design_ref["id"],
                                 "design_version": design_ref["version"], "workflow_id": record["workflow_id"],
                                 "scenario": record["scenario"], "agent_run_id": record["id"],
                                 "input_version": record["input_version"], "advisory_only": True,
                                 "approved": False, "execution_allowed": False, "deployment_allowed": False}
                    service.update_record(draft_job)
                    writer_started = _now()
                    record["writer_progress"] = {
                        "stage": "preparing", "status": "in_progress",
                        "message": "Preparing the draft workspace and checking the saved design.",
                        "tool_name": None, "model_calls_started": 0, "tool_calls_started": 0,
                        "submission_attempts": 0, "files_staged": 0,
                        "file_chunks_staged": 0, "task_records_staged": 0,
                        "updated_at": writer_started,
                    }
                    record["phase_updated_at"] = writer_started
                    service.update_record(record, agent=True)
                    try:
                        exact_design = store_for(service).get(design_ref["id"], record["package_hash"],
                                                               design_ref["version"])
                        draft = draft_agent_design(service, exact_design, agent_run_id=record["id"],
                                                   consent=record.get("consent") is True,
                                                   budget=None)
                        manifest_path = service.root / draft["relative_directory"] / "manifest.json"
                        draft_job.update(status="completed", output={
                            "run_id": draft["run_id"], "relative_directory": draft["relative_directory"],
                            "manifest_status": draft["status"],
                            "failure": draft.get("failure"),
                            "failure_diagnostic": draft.get("failure_diagnostic"),
                            "task_statuses": [{"task_id": task.get("task_id"), "status": task.get("status"),
                                               "artifact_paths": task.get("artifact_paths", []),
                                               "blockers": task.get("blockers", [])}
                                              for task in draft.get("tasks", [])],
                            "validation": draft.get("validation"), "artifact_approved": False,
                            "execution_allowed": False, "deployment_allowed": False},
                            manifest_sha256=_digest(manifest_path))
                    except Exception as exc:
                        from workbench.agent_recovery import safe_exception_class
                        from workbench.databricks_models import safe_failure_diagnostic
                        if isinstance(exc, agent_module.AgentRunCancelled) and cancellation_event.is_set():
                            draft_job.update(status="cancelled",
                                             error="Candidate writer stopped by user at a safe boundary.")
                        else:
                            draft_job.update(status="failed", failure_stage="candidate_code_generation",
                                             exception_class=safe_exception_class(exc),
                                             failure_diagnostic=safe_failure_diagnostic(exc, 'writer_invocation'),
                                             error="Candidate code draft unavailable. No approval, execution or deployment occurred.")
                    finally:
                        draft_job["finished_at"] = _now()
                        service.update_record(draft_job)
                        writer_state = draft_job["status"]
                        writer_message = ("Candidate-code draft finished; inspect its manifest and review status."
                            if writer_state == "completed" else
                            "Candidate-code draft stopped. Inspect the sanitized failure details before retrying.")
                        record["writer_progress"] = {
                            **(record.get("writer_progress") or {}),
                            "stage": "final_result", "status": writer_state,
                            "message": writer_message, "updated_at": draft_job["finished_at"],
                            **({"failure_stage": draft_job["failure_stage"]}
                               if isinstance(draft_job.get("failure_stage"), str) else {}),
                            **({"exception_class": draft_job["exception_class"]}
                               if isinstance(draft_job.get("exception_class"), str) else {}),
                        }
                        record["phase_updated_at"] = draft_job["finished_at"]
                        try:
                            service.update_record(record, agent=True)
                        except Exception as error:
                            from workbench.databricks_models import safe_failure_diagnostic
                            draft_job["progress_write_diagnostic"] = safe_failure_diagnostic(
                                error, "writer_progress_persistence")
                            try:
                                service.update_record(draft_job)
                            except Exception:
                                pass
                    candidate_drafts.append({"job_id": draft_job["id"], "status": draft_job["status"],
                                             "design_id": design_ref["id"], "design_version": design_ref["version"],
                                             "manifest_status": (draft_job.get("output") or {}).get("manifest_status")})
            failure_stage = "delivery_summary"
            delivery_status, delivery_summary = _agent_delivery_status(output, candidate_drafts, body.generate_code)
            if cancellation_event.is_set() and delivery_status not in {"cancelled", "failed"}:
                delivery_status = "cancelled"
                delivery_summary = "Agent run stopped by user; any in-flight call completed before stop was observed."
            safe_output["delivery_status"] = delivery_status
            safe_output["delivery_summary"] = delivery_summary
            record.update(status=delivery_status,
                          phase="complete",
                          redesign_refs=[{"id": item["id"], "version": item["version"]} for item in saved_redesigns],
                          output={**safe_output, "candidate_code_drafts": candidate_drafts,
                                  "advisory_only": True, "approved": False,
                                  "generation_allowed": False, "deployment_allowed": False})
        except Exception as exc:
            from workbench.agent_recovery import safe_exception_class
            if isinstance(exc, agent_module.AgentRunCancelled) and cancellation_event.is_set():
                record.update(status="cancelled", phase="complete",
                              error="Agent run stopped by user at a safe boundary.")
            else:
                record.update(status="failed", failure_stage=failure_stage, exception_class=safe_exception_class(exc),
                              error="Advisory runtime failed. No approval or executable changes were made.")
        finally:
            record["finished_at"] = _now()
            if cancellation_event.is_set() and not record.get("cancellation_requested_at"):
                try:
                    latest = service.record(record["id"], agent=True)
                    if latest.get("cancellation_requested_at"):
                        record["cancellation_requested_at"] = latest["cancellation_requested_at"]
                except HTTPException:
                    pass
            try:
                if "observation_snapshot" not in record:
                    record["observation_snapshot"] = _terminal_agent_observation(service.root, record)
                service.update_record(record, agent=True)
                with service.lock:
                    service.agent_cancel_events.pop(record["id"], None)
                    service.agent_pause_controls.pop(record["id"], None)
                if record.get("cohort_id"):
                    from workbench.cohort_review import update_workflow
                    refs = record.get("redesign_refs", [])
                    state = ("failed" if record["status"] == "failed" else
                             "design_ready" if refs else "needs_information")
                    update_workflow(service, record["package_hash"], record["cohort_id"], record["workflow_id"],
                                    status=state, agent_run_id=record["id"], redesign_refs=refs,
                                    reason=("Design run failed. Inspect the recorded run and operation before any retry."
                                            if state == "failed" else
                                            "Review the submitted design before drafting. No deployment is authorized."
                                            if refs else "No usable design was submitted. Inspect the agent run and missing evidence."))
            finally:
                service.agent_slot.release()

    def _start_agent(body: AgentBody, cohort_id=None):
        index = service.require()
        if body.consent is not True:
            raise HTTPException(403, "Explicit data-egress consent:true is required for every agent run")
        service.workflow(index, body.workflow_id)
        from workbench.model_access import workspace_ready
        if not workspace_ready(service.runtime):
            raise HTTPException(409, "Agent unavailable; WORKBENCH_MODEL must explicitly select provider:model and runtime dependencies must be available")
        if not service.agent_slot.acquire(blocking=False):
            raise HTTPException(409, "An agent run is already in progress; no additional runs are queued")
        try:
            inputs = service.agent_inputs(index, body.workflow_id, body.scenario)
            snapshot = inputs.pop("snapshot")
            from workbench.redesign import store_for
            prior_redesigns = store_for(service).list(index.model["package"]["sha256"], body.workflow_id, body.scenario)
            record = {"id": uuid4().hex, "status": "queued", "workflow_id": body.workflow_id, "scenario": body.scenario,
                      "workflow_name": index.nodes[body.workflow_id]["name"],
                      "phase": "agent_analysis",
                      "redesign_expected_version": max((item["version"] for item in prior_redesigns), default=0),
                      "package_hash": index.model["package"]["sha256"], "created_at": _now(),
                      "consent": True, "generate_code": body.generate_code,
                      "actor": "local-reviewer", "actor_authenticated": False,
                      **inputs,
                      "timeout_policy": service.runtime.status().get("timeout_policy", "no_elapsed_time_cutoff_manual_cancellation"),
                      "advisory_only": True}
            if cohort_id is not None:
                record['cohort_id'] = cohort_id
            cancellation_event = Event()
            with service.lock:
                service.agent_cancel_events[record["id"]] = cancellation_event
                # Opt in only through a real explicit keyword parameter, never
                # assume test/extension runtimes accept a new kwarg via **kwargs.
                try:
                    pause_parameter = inspect.signature(service.runtime.run).parameters.get("pause_control")
                except (TypeError, ValueError):
                    pause_parameter = None
                from workbench.hosted import _CURRENT
                pause_supported = (not getattr(app.state, "hosted_request_authenticated", False)
                                   and _CURRENT.get(None) is None and pause_parameter is not None
                                   and pause_parameter.kind in {inspect.Parameter.KEYWORD_ONLY,
                                                                inspect.Parameter.POSITIONAL_OR_KEYWORD})
                record["pause_supported"] = pause_supported
                if pause_supported:
                    def pause_changed():
                        # RunControl invokes this outside its condition lock.
                        # Persist observed waits for existing status_only reads,
                        # including project reads which bypass the child app.
                        with service.lock:
                            if service.agent_pause_controls.get(record["id"]) is control:
                                current = service.record(record["id"], agent=True)
                                if current.get("status") in {"queued", "running", "cancelling"}:
                                    service.update_record(current, agent=True)

                    control = RunControl(cancellation_event, on_change=pause_changed)
                    service.agent_pause_controls[record["id"]] = control
            service.update_record(record, agent=True)
            if cohort_id is not None:
                from workbench.cohort_review import update_workflow
                update_workflow(service, record['package_hash'], cohort_id, body.workflow_id,
                                status='running', agent_run_id=record['id'],
                                reason='Agent design is running. No code or deployment is authorized.')
            service.agent_executor.submit(run_agent, record, index, body, snapshot, cancellation_event)
        except Exception:
            with service.lock:
                if "record" in locals():
                    service.agent_cancel_events.pop(record["id"], None)
                    service.agent_pause_controls.pop(record["id"], None)
            service.agent_slot.release()
            raise
        return record

    @app.post("/api/agents/run", status_code=202)
    def start_agent(body: AgentBody):
        return _start_agent(body)

    @app.post("/api/agents/runs/{run_id}/cancel", status_code=202)
    def cancel_agent_run(run_id: str):
        index = service.require()
        with service.lock:
            record = service.record(run_id, agent=True)
            if record.get("package_hash") != index.model["package"]["sha256"]:
                raise HTTPException(404, "Unknown run in this package")
            if record.get("status") not in {"queued", "running", "cancelling"}:
                raise HTTPException(409, "Only an active agent run can be stopped")
            cancellation_event = service.agent_cancel_events.get(run_id)
            if cancellation_event is None:
                raise HTTPException(409, "This run cannot be stopped by the current worker; refresh its status")
            cancellation_event.set()
            control = service.agent_pause_controls.get(run_id)
            if control is not None:
                control.cancel()
            if not record.get("cancellation_requested_at"):
                record["cancellation_requested_at"] = _now()
            service.update_record(record, agent=True)
        return {"id": run_id, "status": record["status"], "cancellation_requested": True,
                "cancellation_requested_at": record["cancellation_requested_at"],
                "detail": "Stop requested. Any in-flight model request may finish before the worker observes cancellation."}

    def control_agent_pause(run_id: str, *, resume: bool):
        # Same app middleware, selected project/package context and mutation
        # boundary as cancel. The POST itself is the user's control request;
        # this grants no model, generation, execution or deployment consent.
        index = service.require()
        with service.lock:
            record = service.record(run_id, agent=True)
            if record.get("package_hash") != index.model["package"]["sha256"]:
                raise HTTPException(404, "Unknown run in this package")
            from workbench.hosted import _CURRENT
            if getattr(app.state, "hosted_request_authenticated", False) or _CURRENT.get(None) is not None:
                raise HTTPException(501, "Hosted pause/resume is unsupported: durable actions drain workers before publishing state")
            if record.get("status") not in {"queued", "running"} or record.get("cancellation_requested_at"):
                raise HTTPException(409, "Only an active, non-cancelling agent run can be paused or resumed")
            control = service.agent_pause_controls.get(run_id)
            if control is None or control.cancellation_event.is_set():
                raise HTTPException(409, "This run has no pause-capable worker in the current process; refresh its status")
            if resume:
                control.resume()
            else:
                control.request_pause()
            service.update_record(record, agent=True)
            return {"id": run_id, "status": record["status"],
                    **{key: record[key] for key in control.snapshot()},
                    "detail": ("Resume requested. Safe-boundary admission is open; no new consent or grants were added."
                               if resume else "Pause requested for the next safe model/tool boundary. In-flight calls are not interrupted.")}

    @app.post("/api/agents/runs/{run_id}/pause", status_code=202)
    def pause_agent_run(run_id: str):
        return control_agent_pause(run_id, resume=False)

    @app.post("/api/agents/runs/{run_id}/resume", status_code=202)
    def resume_agent_run(run_id: str):
        return control_agent_pause(run_id, resume=True)

    def start_cohort_workflow(saved, workflow_id, cohort_id):
        prompt = ("Design a Databricks-native solution for the selected workflow in its complete package context. "
                  "Retrieve invocation, source, target, mapping, connection, SQL, Java and procedure evidence. "
                  "Compare related workflows for evidence-backed reuse, preserving invocation wait semantics, "
                  "conditions, cadence, transactions and recovery. Do not copy ETL shapes unnecessarily or "
                  "infer missing behavior. Cover every original task, including disabled tasks. Call submit_redesign "
                  "with citations, alternatives and task-scoped unresolved evidence. This explicit action also requests "
                  "the separate writer's unapproved candidate-code draft for supported tasks after design submission. "
                  "No separate design approval is required for that candidate draft. Analysis roles submit logical "
                  "designs and findings only; the writer submits actual code artifacts, not prose. No execution, "
                  "deployment or parity approval is granted. Treat the selected workflow as the authority for this "
                  "run; retrieve exact callers/children and shared-data evidence for cohort context, recommend any "
                  "scope expansion explicitly, and do not silently draft other cohort members or claim package closure.")
        return _start_agent(AgentBody(workflow_id=workflow_id, scenario=saved['request']['scenario'],
                                      prompt=prompt, consent=True, generate_code=True), cohort_id)

    @app.get("/api/agents/runs")
    def agent_runs(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                   status_only: bool = Query(default=False)):
        if status_only:
            return _stored_agent_runs(service.root, offset, limit)
        index = service.require()
        items = []
        for path in sorted((service.state / "agent-runs").glob("*.json"), reverse=True):
            record = service.record(path.stem, agent=True)
            if record.get("package_hash") == index.model["package"]["sha256"]:
                workflow = index.nodes.get(record.get("workflow_id"), {})
                public = {**{k: v for k, v in record.items() if k not in {"output", "observation_snapshot"}},
                          "workflow_name": workflow.get("name", "Unknown workflow")}
                public["candidate_writer"] = _candidate_writer_status(
                    service.root, record["id"], record.get("package_hash"))
                items.append(public)
        items.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return {"items": [service.run_freshness(index, r) for r in items[offset:offset + limit]],
            "total": len(items), "offset": offset, "limit": limit,
                "active_run_present": any(_run_or_writer_active(r) for r in items),
                "run_status_verified": all(r.get("status") in {
                    "queued", "running", "completed", "partial", "complete", "succeeded", "success",
                    "failed", "error", "cancelled", "canceled", "rejected"} for r in items),
                "truncated": offset + limit < len(items)}

    @app.get("/api/agents/runs/{run_id}")
    def agent_run(run_id: str, status_only: bool = Query(default=False)):
        if status_only:
            return _stored_agent_status(service.root, run_id)
        index = service.require()
        record = service.record(run_id, agent=True)
        if record.get("package_hash") != index.model["package"]["sha256"]:
            raise HTTPException(404, "Unknown run in this package")
        result = service.run_freshness(index, record)
        result["candidate_writer"] = _candidate_writer_status(
            service.root, run_id, record.get("package_hash"))
        if not record.get("output") and record.get("status") == "failed":
            from workbench.agent_recovery import recovered_output
            recovery = recovered_output(service.root, run_id)
            result["recovered_output"] = recovery.get("output")
            result["output_recovery_receipt"] = {k: v for k, v in recovery.items() if k != "output"}
        from workbench.agent_usage_receipt import current_usage_receipt
        result["current_usage_receipt"] = current_usage_receipt(service.root, record)
        if not record.get("observation_snapshot"):
            result["live_activity"] = _stored_agent_progress(service.root, record)
        workflow = index.nodes.get(record.get("workflow_id"), {})
        result["workflow_name"] = workflow.get("name", "Unknown workflow")
        findings = (record.get("output") or {}).get("findings", [])
        cited_ids = {identifier for finding in findings if isinstance(finding, dict)
                     for identifier in (finding.get("evidence_ids") or []) if isinstance(identifier, str)}
        result["evidence_references"] = index.evidence_references(cited_ids)
        # A read-only present-day interpretation beside, not inside, old output.
        from workbench.agents import _Evidence
        from workbench.diagnostic_relevance import project_diagnostic
        evidence = _Evidence(index.model, record["workflow_id"], [], [])
        relevance = [project_diagnostic(evidence.records[i], evidence.nodes, evidence.out, record["scenario"])
                     | {"diagnostic_id": i, "code": evidence.records[i].get("code")}
                     for i in evidence.diagnostic_refs]
        from workbench.validation_campaigns import input_pins
        result["diagnostic_assessment"] = {
            "basis": "current_selected_saved_analysis_not_reparsed", "historical_output_unchanged": True,
            "run_input_matches_current": result.get("stale") is False,
            "analysis_version": index.model.get("source_analysis_version"),
            "selected_input_pins": input_pins(index),
            "counts": dict(Counter(item["category"] for item in relevance)),
            "items": relevance[:100], "total": len(relevance), "truncated": len(relevance) > 100}
        from workbench.redesign import store_for
        result["submitted_design_links"] = []
        for ref in record.get("redesign_refs", []):
            try:
                saved = store_for(service).get(ref["id"], record["package_hash"], ref["version"])
            except KeyError:
                continue
            if (saved["source"]["record_id"] == record["id"]
                    and saved["workflow_id"] == record["workflow_id"] and saved["scenario"] == record["scenario"]
                    and saved["input_version"] == record["input_version"]):
                result["submitted_design_links"].append({"id": saved["id"], "version": saved["version"],
                    "workflow_id": saved["workflow_id"], "scenario": saved["scenario"],
                    "package_hash": saved["package_hash"], "record_id": record["id"]})
        return result

    @app.post("/api/agents/runs/{run_id}/recover-output")
    def recover_agent_output(run_id: str, body: dict):
        from workbench.agent_recovery import recover_design
        from workbench.hosted import _CURRENT
        if _CURRENT.get(None) is not None:
            raise HTTPException(409, "Original-output recovery is local only; no cloud writes are authorized")
        if set(body) != {"consent", "pins"} or body.get("consent") is not True:
            raise HTTPException(403, "Explicit consent:true and exact recovery pins are required")
        index = service.require()
        record = service.record(run_id, agent=True)
        if record.get("package_hash") != index.model["package"]["sha256"]:
            raise HTTPException(404, "Unknown run in this package")
        if not service.agent_slot.acquire(blocking=False):
            raise HTTPException(409, "Active agent run present; no recovery started")
        try:
            return recover_design(service.root, run_id, body["pins"], consent=True, index=index)
        except (ValueError, PermissionError, OSError):
            raise HTTPException(409, "Recovery verification failed; original history is unchanged") from None
        finally:
            service.agent_slot.release()

    @app.get("/")
    def frontend():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    from workbench.fixture_metadata import install_fixture_metadata
    install_fixture_metadata(app)
    from workbench.oracle_federation import install_oracle_federation
    install_oracle_federation(app)
    from workbench.model_price_rates import install_model_pricing
    install_model_pricing(app)
    from workbench.validation_campaigns import install_validation
    install_validation(app)
    from workbench.readiness import install_readiness
    install_readiness(app)
    from workbench.knowledge_store import install_knowledge
    install_knowledge(app)
    from workbench.agent_library import install_agent_library
    install_agent_library(app)
    from workbench.model_access import install_model_access
    (model_installer or install_model_access)(app)
    from workbench.audit import install_audit
    install_audit(app, record_initialization=record_audit_initialization)
    from workbench.loops import install_loops
    install_loops(app)
    from workbench.redesign import install_redesigns
    install_redesigns(app)
    from workbench.candidate_retry import install_candidate_retry
    install_candidate_retry(app)
    from workbench.notebook_runner import install_notebook_runner
    install_notebook_runner(app)
    from workbench.selected_job_deploy import install_selected_job_deploy
    if not getattr(app.state, 'hosted_request_authenticated', False):
        install_selected_job_deploy(app)
    from workbench.cohort_review import install_cohort_review
    install_cohort_review(app, service, start_cohort_workflow)
    from workbench.connections import install_connections
    install_connections(app)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_app(data_dir=None, sample_path=None, agent_runtime=None):
    """Preserve the default package app and add explicit, isolated projects.

    Injected runtimes remain default-context only. Project children always own
    their runtime, review database, active manifest, and immutable artifacts.
    """
    from workbench.projects import install_projects

    app = _create_package_app(data_dir, sample_path, agent_runtime)
    install_projects(app, _create_package_app, ROOT / "input")
    from workbench.volume_intake import install_volume_intake
    install_volume_intake(app)
    return app