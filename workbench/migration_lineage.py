"""Read-only canonical lineage normal form. No execution, storage or model calls.

Every exported connector/control edge retains its canonical ID and attributes.
Literal invocation candidates remain a separate relation, never completion edges.
Pages are slices of the same deterministic inventory; exports contain all slices.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

import networkx as nx
from fastapi import HTTPException, Query, Request

from workbench import delta_state as ds
from workbench.package_context import PackageContext
from workbench.validation_contracts import LEDGER_PRIORITY_IDS, SourceContracts
from workbench.validation_campaigns import input_pins

FORMAT = "canonical-migration-lineage-v1"
FAMILIES = ("nodes", "edges", "fields", "properties", "invocations", "obligations")
READ_FAMILIES = (*FAMILIES, "trace")
PAGE_BYTES = 2 * 1024 * 1024


def page(rows, offset, limit):
    chosen, size = [], 0
    for row in rows[offset:offset + limit]:
        packed = len(ds._json(row).encode())
        if packed > PAGE_BYTES:
            raise ValueError("One lineage record exceeds the page byte bound; use the exclusive offline export")
        if size + packed > PAGE_BYTES:
            break
        chosen.append(row)
        size += packed
    more = offset + len(chosen) < len(rows)
    return {"items": chosen, "total": len(rows), "offset": offset, "limit": limit,
            "has_more": more, "next_offset": offset + len(chosen) if more else None,
            "byte_bound": PAGE_BYTES, "returned_bytes": size}


class Lineage:
    def __init__(self, index):
        self.index = index
        self.pins = input_pins(index)
        self.context = PackageContext(index.model)
        self.contracts = SourceContracts(self.context)
        self.nodes = self.context.nodes
        self._inventories = {}
        # The ledger priorities identify the requested ten, not conversion status.
        self.selected = [i for i in LEDGER_PRIORITY_IDS if i in self.nodes and self.nodes[i]["kind"] == "workflow"]
        if not self.selected:
            self.selected = [i for i in sorted(self.context.graph.nodes)
                             if self.contracts.workflow(i)["classification"] == "business_definition_candidate"][:10]

    def workflow(self, wid):
        contract = self.contracts.workflow(wid)
        local = self.context.local(wid)
        return {"workflow_id": wid, "name": self.nodes[wid]["name"],
                "scope": self.nodes[wid].get("scope", {}),
                "classification": contract["classification"],
                "node_count": len(local), "kind_counts": dict(Counter(self.nodes[i]["kind"] for i in local)),
                "task_instance_ids": [r["task_instance_id"] for r in contract["tasks"]],
                "session_uses": contract["tasks"], "mapping_ids": [r["mapping_id"] for r in contract["mappings"]],
                "mapping_targets": [{"mapping_id": m["mapping_id"],
                    "mapping_name": self.nodes[m["mapping_id"]]["name"],
                    "targets": [{"target_id": t["target_id"], "name": self.nodes[t["target_id"]]["name"],
                        "target_instance_id": t["target_instance_id"], "role": t["role"],
                        "wired_source_instance_count": len(t["wired_source_instance_ids"]),
                        "destination_status": "unassigned", "approved": False}
                        for t in m["targets"]]}
                    for m in contract["mappings"]],
                "business_target_ids": contract["business_target_ids"],
                "target_assignments": [{"target_id": i, "name": self.nodes[i]["name"],
                    "destination_catalog": None, "destination_schema": None, "destination_table": None,
                    "status": "unassigned", "approved": False}
                    for i in sorted(local) if self.nodes[i]["kind"] == "target"],
                "source_children": sorted(self.context.graph.successors(wid)),
                "source_callers": sorted(self.context.graph.predecessors(wid)),
                "upstream_invocation_ids": sorted(nx.ancestors(self.context.graph, wid)),
                "downstream_invocation_ids": sorted(nx.descendants(self.context.graph, wid)),
                "proposed_target": {"status": "Not generated", "job_id": None, "name": None,
                    "artifact_correspondences": [], "execution_verified": False},
                "runtime_status": "NOT_RUN", "converted_complete": False,
                "source_contract_sha256": contract["contract_sha256"]}

    def inventory(self, wid):
        if wid in self._inventories:
            return self._inventories[wid]
        if self.nodes.get(wid, {}).get("kind") != "workflow":
            raise ValueError("Unknown canonical workflow")
        cohort = nx.node_connected_component(self.context.graph.to_undirected(as_view=True), wid)
        cohort_key = tuple(sorted(cohort))
        if cohort_key in self._inventories:
            self._inventories[wid] = self._inventories[cohort_key]
            return self._inventories[wid]
        ids = set().union(*(self.context.local(i) for i in cohort))
        # Some older analyzers omit port containment. Only exact parent identity
        # attaches those ports, never field names or guessed physical resources.
        ids.update(n["id"] for n in self.nodes.values()
                   if n["kind"] == "instance_field" and n.get("parent_id") in ids)
        edges = [e for e in self.index.model["edges"] if e["source"] in ids and e["target"] in ids]
        fields, properties, obligations, nodes = [], [], [], []
        for i in sorted(ids):
            n = self.nodes[i]
            nodes.append({k: v for k, v in n.items() if k not in {"fields", "properties", "summary"}})
            for ordinal, field in enumerate(n.get("fields", [])):
                fields.append({"id": "field:" + ds._hash([i, ordinal, field]), "owner_id": i,
                    "ordinal": ordinal, "sha256": ds._hash(field), "record": field})
            for ordinal, prop in enumerate(n.get("properties", [])):
                pid = "property:" + ds._hash([i, ordinal, prop])
                properties.append({"id": pid, "owner_id": i, "ordinal": ordinal,
                    "sha256": ds._hash(prop), "record": prop})
                attrs = prop.get("attributes", {})
                role, text = attrs.get("NAME", ""), attrs.get("VALUE", "")
                if text.strip() and (re.search(r"sql|query|override|procedure|parameter filename", role, re.I)
                                     or role == "Call Text" or prop.get("embedded_code")):
                    obligations.append({"id": "obligation:" + ds._hash([pid, role]), "owner_id": i,
                        "property_id": pid, "role": role, "status": "unresolved_external_or_dynamic"
                        if "$" in text or "[REDACTED]" in text or "procedure" in role.casefold() or role == "Call Text"
                        else "captured_not_runtime_bound", "dependency_resolution": "not_inferred_from_SQL_text",
                        "body_available": False if "procedure" in role.casefold() or role == "Call Text" else None,
                        "translation_verified": False, "evidence": prop.get("evidence", {})})
            if n["kind"] == "unresolved_reference" or (n["kind"] == "instance_field"
                    and n.get("attributes", {}).get("status") != "resolved"):
                obligations.append({"id": "missing:" + i, "owner_id": i,
                    "role": "canonical_reference", "status": "missing_or_ambiguous",
                    "evidence": n.get("evidence", {})})
            if n["kind"] == "connectionreference":
                obligations.append({"id": "binding:" + i, "owner_id": i, "role": "physical_connection",
                    "status": "unverified_not_missing_schema", "evidence": n.get("evidence", {})})
        invocations = []
        for call in self.context.invocations:
            if call["caller_workflow_id"] not in cohort and not cohort.intersection(call["candidate_workflow_ids"]):
                continue
            invocations.append({"id": "invocation:" + ds._hash(call), **call,
                "completion_dependency": False,
                "completion_semantics": "asynchronous_launch_no_completion_edge" if call.get("wait_mode") == "nowait"
                else "wait_requested_not_runtime_verified" if call.get("wait_mode") == "wait" else "unresolved"})
        result = {"nodes": nodes, "edges": sorted(edges, key=lambda e: e["id"]), "fields": fields,
                  "properties": properties, "invocations": invocations, "obligations": obligations,
                  "cohort_ids": sorted(cohort)}
        self._inventories[wid] = result
        self._inventories[cohort_key] = result
        return result

    def trace(self, wid, project, saved=None):
        """Exact source task identities and explicit saved coverage, never name matching.

        Saved readiness envelopes come from the existing scoped store. A writer
        receipt is not artifact-byte verification or a business execution result.
        """
        contract = self.contracts.workflow(wid)
        state = {}
        if saved and saved.get("scope", {}).get("project_id") == project and saved["scope"].get("pins") == self.pins:
            state = next((w.get("proposed_state", {}) for w in saved.get("workflows", {}).get("items", [])
                          if w.get("workflow_id") == wid), {})
        design = state.get("design") or {}
        current = (design.get("freshness") == "current" and design.get("input_version")
                   and design.get("input_version") == design.get("current_input_version"))
        body = design.get("design", {}) if current else {}
        tasks = {t["id"]: t for t in body.get("tasks", [])}
        rows = []
        for task in contract["tasks"]:
            tid = task["task_instance_id"]
            coverage = [r for r in body.get("coverage", []) if r.get("original_task_id") == tid]
            binding = coverage[0] if len(coverage) == 1 else {}
            targets = []
            keys = binding.get("proposed_task_ids", [])
            if binding.get("disposition") == "mapped" and keys and all(k in tasks for k in keys):
                for key in keys:
                    target = tasks[key]
                    paths = []
                    for receipt in state.get("draft_receipts", []):
                        if (receipt.get("input_version") != design.get("input_version")
                                or receipt.get("design_id") != design.get("id")
                                or receipt.get("design_version") != design.get("version")):
                            continue
                        for authored in receipt.get("task_statuses", []):
                            if authored.get("task_id") == key and authored.get("status") == "drafted":
                                for path in authored.get("artifact_paths", []):
                                    digest = receipt.get("artifact_hashes", {}).get(path)
                                    if (isinstance(path, str) and re.fullmatch(r"(?:notebooks|modules|sql|tests)/[A-Za-z0-9_./-]+", path)
                                            and ".." not in path.split("/") and isinstance(digest, str)
                                            and re.fullmatch(r"[a-f0-9]{64}", digest)):
                                        paths.append({"path": path, "sha256": digest})
                    targets.append({"task_key": key, "label": target.get("label", key),
                        "kind": target.get("implementation_kind"), "purpose": target.get("purpose"),
                        "condition": target.get("condition_task"), "artifacts": paths,
                        "status": "generated_draft_receipt_bytes_not_reverified" if paths else "proposed",
                        "code": None, "tables": [], "execution_result": "unknown"})
            def identities(ids):
                return [{"id": i, "name": self.nodes[i]["name"], "kind": self.nodes[i]["kind"]} for i in ids]
            rows.append({"id": tid, "source": {"id": tid, "name": task["name"], "kind": "taskinstance",
                "task_type": task.get("task_type"), "sessions": identities(task["session_ids"]),
                "mappings": identities(task["mapping_ids"]), "evidence": self.nodes[tid].get("evidence", {}),
                "original_properties": self.nodes[tid].get("properties", [])},
                "targets": targets, "status": "explicit_saved_coverage" if targets else "not_mapped",
                "changes": binding.get("rationale") if targets else "Not mapped: explicit current correspondence evidence not specified.",
                "execution_result": "unknown", "whole_workflow_converted": False})
        return {"rows": rows, "source_package_hash": self.pins["package_hash"], "workflow_id": wid,
                "job": {"name": None, "design_title": body.get("title"),
                    "status": "proposed" if body else "Not generated", "job_id": None},
            "design_truncated": body.get("truncated", False), "execution_result": "unknown",
            "artifact_bytes_verified": False, "production_ready": False,
            "basis": "exact canonical task IDs and current scoped saved coverage only; no name-based matching"}

    def response(self, project, *, wid=None, family="nodes", offset=0, limit=100, root=None, expected=None, saved=None, example=False):
        if family not in READ_FAMILIES or offset < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid lineage pagination")
        if expected and expected != ds._hash(self.pins):
            raise HTTPException(409, "Lineage input version changed; refresh the selected package")
        wid = wid or (self.selected[0] if self.selected else None)
        summaries = [self.workflow(i) for i in self.selected]
        result = {"format": FORMAT, "scope": {"project_id": project, "pins": self.pins},
            "input_version": ds._hash(self.pins), "selected_workflows": summaries,
            "selection_basis": "ledger_canonical_IDs_present_in_selected_package_or_wired_business_candidates",
            "requested_workflow_count": 10, "selected_workflow_count": len(summaries),
            "missing_ledger_workflow_ids": [i for i in LEDGER_PRIORITY_IDS if i not in self.nodes],
            "workflow_id": wid, "family": family, "root_id": root,
            "coverage": {"canonical_inventory_complete": True, "semantic_lineage_complete": False,
                "sql_external_dependencies_complete": False, "physical_bindings_verified": False,
                "entire_package_converted": False, "verified_business_workflows": 0,
                "capabilities": ["canonical_fields_and_connectors", "exact_source_control_conditions",
                                 "strict_literal_invocation_candidates", "continuation_paging"],
                "limitations": ["Invocation candidates are not execution or data readiness.",
                    "External procedure bodies, dynamic SQL/parameters and runtime bindings remain unresolved.",
                    "Stored source versions can differ from fresh offline shortlist analysis.",
                    "Source inventory never assumes generation. Explicit task trace separately reads current scoped saved draft coverage; receipt hashes are not artifact-byte or runtime verification."]},
            "deployment_allowed": False, "execution_allowed": False, "animation_basis": "selected_source_trace_not_execution",
            "artifact_correspondence_status": "not_verified_by_canonical_projection"}
        if wid is None:
            result.update(cohort=[], totals={k: 0 for k in FAMILIES}, page=page([], offset, limit), focus=None)
            return result
        inventory = self.inventory(wid)
        if root and root not in {r["id"] for r in inventory["nodes"]}:
            raise ValueError("Root is outside the exact invocation cohort")
        local = self.context.local(wid)
        # Focus graphs are one exact canonical hop. Expanding a clicked node is
        # explicit, so large mapping fields do not silently disappear from export.
        focus_id = root or wid
        incident = [e for e in inventory["edges"] if e["source"] == focus_id or e["target"] == focus_id]
        focus_ids = {focus_id} | {e[k] for e in incident for k in ("source", "target")}
        trace = self.trace(wid, project, saved)
        rows = trace.pop("rows") if family == "trace" else inventory[family]
        if root:
            if family == "nodes":
                rows = [r for r in rows if r["id"] in focus_ids]
            elif family == "edges":
                rows = incident
            elif family in {"properties", "fields", "obligations"}:
                rows = [r for r in rows if r["owner_id"] == root]
            elif family == "invocations":
                rows = [r for r in rows if r["caller_workflow_id"] == root or root in r["candidate_workflow_ids"]]
        result.update(cohort=[{"workflow_id": i, "name": self.nodes[i]["name"], "selected": i in self.selected}
                             for i in inventory["cohort_ids"]],
            invocation_neighborhood=page([r for r in inventory["invocations"]
                if r["caller_workflow_id"] == wid or wid in r["candidate_workflow_ids"]], 0, 200),
            selected=self.workflow(wid), totals={k: len(inventory[k]) for k in FAMILIES},
            selected_scope_counts=dict(Counter(self.nodes[i]["kind"] for i in local)), page=page(rows, offset, limit),
            focus={"id": focus_id, "nodes": page(sorted([r for r in inventory["nodes"] if r["id"] in focus_ids],
                key=lambda n: (n["id"] != focus_id, n["kind"] not in {"workflow", "taskinstance", "task", "session", "mapping", "source", "target", "instance", "transformation", "unresolved_reference", "instance_field"}, n["id"])), 0, 150),
                "edges": page(incident, 0, 200), "basis": "one_exact_canonical_hop_expand_nodes_for_deeper_lineage"})
        result["transformation_trace"] = trace
        if example:
            from workbench.lineage_example import reference
            result["illustrative_example"] = reference()
        return result


def install_lineage(app):
    @app.get("/api/readiness/lineage")
    def lineage(request: Request, workflow_id: str | None = None, root_id: str | None = None,
                family: str = Query("nodes", pattern="^(nodes|edges|fields|properties|invocations|obligations|trace)$"),
                offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), input_version: str | None = None,
                example: bool = False, scenario: str = Query("original", pattern="^(original|bronze)$")):
        if getattr(app.state, "hosted_request_authenticated", False):
            raise HTTPException(409, "Hosted lineage requires verified selected-package projection; no fallback")
        index = app.state.service.require()
        selected = request.headers.get("X-Workbench-Package")
        if selected and selected != index.model["package"]["sha256"]:
            raise HTTPException(409, "Selected package changed")
        try:
            project = request.headers.get("X-Workbench-Project") or "__legacy__"
            lineage_view = Lineage(index)
            saved = None
            if family == "trace":
                from workbench.readiness import local_evidence, assess
                evidence, complete = local_evidence(app.state.service, index.model["package"]["sha256"], project)
                saved = assess(index, project, evidence, complete, scenario=scenario,
                               workflow_id=workflow_id or (lineage_view.selected[0] if lineage_view.selected else None))
            return lineage_view.response(project, wid=workflow_id, family=family, offset=offset, limit=limit,
                root=root_id, expected=input_version, saved=saved, example=example)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None


def export(index, directory, *, project="__legacy__", shortlist=None, source=None, artifact_exports=()):
    """Explicit offline export to a NEW directory, not service/Delta authority."""
    lineage = Lineage(index)
    report = lineage.response(project)
    inventory = {k: {} for k in FAMILIES}
    workflow_inventory = []
    for wid in lineage.selected:
        unit = lineage.inventory(wid)
        workflow_inventory.append({"workflow_id": wid, "cohort_ids": unit["cohort_ids"],
            "selected_local_node_ids": sorted(lineage.context.local(wid)),
            "family_ids": {k: [r["id"] for r in unit[k]] for k in FAMILIES}})
        for family in FAMILIES:
            inventory[family].update({r["id"]: r for r in unit[family]})
    report.pop("page", None)
    report.pop("focus", None)
    report["linked_objects"] = {k: [v[i] for i in sorted(v)] for k, v in inventory.items()}
    report["workflow_inventory"] = workflow_inventory
    report["totals"] = {k: len(v) for k, v in inventory.items()}
    report["export_complete"] = True
    report["authority"] = "offline_selected_saved_canonical_version_not_hosted_activation_or_runtime_receipt"
    references = {}
    for label, path in (("shortlist", shortlist), ("source_xml", source)):
        if not path:
            continue
        path = Path(path)
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        references[label] = {"path": path.as_posix(), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}
        if label == "source_xml" and digest.hexdigest() != lineage.pins["package_hash"]:
            raise ValueError("XML does not match selected canonical package")
        if label == "shortlist":
            value = json.loads(path.read_bytes())
            if value["source_package_sha256"] != lineage.pins["package_hash"] or [r["workflow_id"] for r in value["workflows"]] != lineage.selected:
                raise ValueError("Shortlist package/workflow cohort mismatch")
            report["shortlist_source_basis"] = value["source_basis"]
            report["shortlist_contracts_not_substituted_for_saved_version"] = True
    report["input_references"] = references
    report["offline_artifact_references"] = []
    for exported in artifact_exports:
        exported = Path(exported).resolve()
        manifest_path = exported / "manifest.json"
        manifest_raw = manifest_path.read_bytes()
        artifact_manifest = json.loads(manifest_raw)
        entries = artifact_manifest.get("artifacts", {})
        verified = []
        for name, pin in sorted(entries.items()):
            artifact = (exported / name).resolve()
            if not artifact.is_relative_to(exported):
                raise ValueError("Artifact reference escapes its export")
            artifact_raw = artifact.read_bytes()
            if len(artifact_raw) != pin["bytes"] or sha256(artifact_raw).hexdigest() != pin["sha256"]:
                raise ValueError("Offline candidate artifact integrity mismatch")
            verified.append({"path": name, **pin, "byte_integrity_verified": True})
        implementation_path = exported / "implementation_report.json"
        implementation = json.loads(implementation_path.read_bytes()) if implementation_path.exists() else {}
        if implementation and implementation.get("package_sha256") != lineage.pins["package_hash"]:
            raise ValueError("Candidate implementation package mismatch")
        report["offline_artifact_references"].append({"directory": str(exported),
            "manifest_sha256": sha256(manifest_raw).hexdigest(), "artifacts": verified,
            "declared_package_sha256": implementation.get("package_sha256"),
            "declared_workflow_ids": [implementation[k] for k in ("child_workflow_id", "parent_workflow_id") if k in implementation],
            "selected_understanding_version_verified": False,
            "status": "hash_verified_offline_candidates_not_current_generation_or_execution_receipts",
            "full_workflow_converted": False, "execution_verified": False})
    report["report_sha256"] = ds._hash(report)
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / "lineage.json").open("xb") as stream:
        stream.write(raw)
    manifest = {"format": FORMAT, "pins": lineage.pins,
        "artifacts": {"lineage.json": {"sha256": sha256(raw).hexdigest(), "bytes": len(raw)}},
        "deployment_allowed": False}
    with (directory / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    return {"selected": [r["name"] for r in report["selected_workflows"]], "totals": report["totals"],
            "manifest": manifest, "semantic_lineage_complete": False}


if __name__ == "__main__":
    import argparse
    from workbench.app import PackageIndex
    parser = argparse.ArgumentParser(description="Exclusive offline full canonical lineage export, no remote calls")
    parser.add_argument("--package-directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--shortlist")
    parser.add_argument("--source")
    parser.add_argument("--artifact-export", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(export(PackageIndex.load(Path(args.package_directory)), args.output,
                            shortlist=args.shortlist, source=args.source, artifact_exports=args.artifact_export), indent=2))