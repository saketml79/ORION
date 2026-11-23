"""Deterministic, nonexecuting runtime migration proposals from canonical graphs.

Input must be the complete output of ``understanding.analyze_package``, not a
legacy IR or generated artifact. The caller owns ingestion and must not mutate
the model during this index's lifetime. No files, models, network or generators
are accessed. Structural reachability is never proof of runtime equivalence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

import networkx as nx

from workbench.domain import _RELATIONS
from workbench.understanding import mapping_fingerprints


PLANNING_VERSION = "1.0"
_MAX_LIMIT = 200
_REVIEWS = (
    ("schedule", "Compare triggers, calendars, time zones, concurrency and upstream dependencies."),
    ("checkpoint", "Compare recovery, restart, replay, checkpoints and idempotency boundaries."),
    ("transaction", "Compare commit/rollback scope, target writes, pre/post SQL and side effects."),
    ("parameters", "Verify parameter scopes, defaults, overrides and physical bindings per invocation."),
    ("sequencing", "Verify conditional, failure and skip behavior; shared targets do not establish order."),
)
_SCHEDULE_KINDS = frozenset({
    "scheduler", "scheduleinfo", "scheduleoptions", "startoptions", "endoptions",
    "recurring", "repeat", "dailyfrequency", "custom", "filter", "timer",
})


def _bound_number(value, name, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum or 'unbounded'}]")


def _page(items, offset=0, limit=100):
    total = len(items)
    selected = items[offset:offset + limit]
    return {"items": selected, "total": total, "offset": offset,
            "returned": len(selected), "truncated": len(selected) != total,
            "has_more": offset + len(selected) < total,
            "next_offset": offset + len(selected) if offset + len(selected) < total else None}


class PlanningIndex:
    """Indexed canonical relations, cached closures and exact resource identities.

    ``estate_manifest(offset=0, limit=50)`` pages workflow coverage, not payloads.
    ``workflow_contract(workflow_id, *, offset=0, limit=100, field_limit=50,
    attribute_limit=100, text_limit=4096)`` pages *each* top-level collection
    independently at that offset. Nested collections always start at zero and
    expose totals/truncation. Increase bounds (up to 200) for nested detail, or
    retrieve the canonical model through the parent application. Truncated
    evidence must never be treated as an executable specification.
    """

    def __init__(self, model: dict):
        self.model = model
        self.nodes = {node["id"]: node for node in model["nodes"]}
        if len(self.nodes) != len(model["nodes"]):
            raise ValueError("Canonical node IDs must be unique")
        self.out = defaultdict(list)
        self.children = defaultdict(list)
        self.controls = defaultdict(list)
        for edge in model["edges"]:
            if edge["source"] not in self.nodes or edge["target"] not in self.nodes:
                raise ValueError("Canonical edge endpoints must exist")
            self.out[edge["source"]].append(edge)
            if edge["kind"] == "contains":
                self.children[edge["source"]].append(edge["target"])
            elif edge["kind"] == "workflow_control":
                self.controls[edge["source"]].append(edge)
        for edges in self.out.values():
            edges.sort(key=lambda edge: edge["id"])
        self.workflow_ids = sorted(i for i, n in self.nodes.items() if n["kind"] == "workflow")
        self._closures = {}
        self._workflow_counts = {}
        self._resource_users = None
        self._fingerprints = None

    def _walk(self, start):
        seen, pending = {start}, [start]
        while pending:
            for edge in self.out.get(pending.pop(), ()):
                if edge["kind"] in _RELATIONS and edge["target"] not in seen:
                    seen.add(edge["target"])
                    pending.append(edge["target"])
        return seen

    def agent_contract(self, workflow_id: str, section: str = "overview", *,
                       offset: int = 0, limit: int = 12, text_limit: int = 16384) -> dict:
        """Paged XML planning evidence, not a generated graph or execution contract.

        One field/property per record avoids losing expressions to a nested list
        cap. IDs always identify existing canonical owners or edges. Long strings
        carry explicit truncation paths; retrieval does not establish parity.
        This path deliberately avoids estate-wide resource-user analysis.
        """
        _bound_number(offset, "offset", 0)
        _bound_number(limit, "limit", 1, 20)
        _bound_number(text_limit, "text_limit", 1, 16384)
        sections = {"overview", "tasks", "control_links", "definitions", "mapping_fields", "behavior",
                    "mapping_links", "properties", "connections", "parameters", "embedded_code", "invocations"}
        if section not in sections:
            raise ValueError("Unknown planning section")
        reached = self._closure(workflow_id)
        # A called workflow is an invocation boundary, not owned implementation.
        local, pending = {workflow_id}, [workflow_id]
        while pending:
            for edge in self.out.get(pending.pop(), ()):
                target = edge["target"]
                if (edge["kind"] in _RELATIONS and target in reached and target not in local
                        and self.nodes[target]["kind"] != "workflow"):
                    local.add(target)
                    pending.append(target)
        ids = sorted(local)
        direct = [i for i in ids if self.nodes[i]["kind"] == "taskinstance"
                  and self.nodes[i].get("parent_id") == workflow_id]
        rows = []
        if section == "invocations":
            from workbench.source_evidence import workflow_invocations
            rows = [row for row in workflow_invocations(self.model)
                if row["caller_workflow_id"] == workflow_id
                or workflow_id in row["candidate_workflow_ids"]]
        if section == "overview":
            from workbench.explanations import explain_workflow
            english = explain_workflow(self.nodes, self.out, workflow_id, limit=100)
            return {"workflow_id": workflow_id, "sections": sorted(sections),
                    "direct_task_ids": direct, "english": english,
                    "evidence_ids": english["evidence_ids"],
                    "policy": {"draft_with_scoped_unknowns": True,
                               "missing_email_is_not_global_design_blocker": True,
                               "start_is_administrative_coverage_not_required_notebook": True,
                               "production_data_required_for_logical_design": False},
                    "generation_allowed": False, "deployment_allowed": False}
        if section in {"tasks", "definitions", "connections", "parameters"}:
            kinds = {"tasks": {"taskinstance"},
                     "definitions": {"workflow", "session", "mapping", "task", "worklet", "transformation", "source", "target", "sessionextension", "timer"},
                     "connections": {"connection", "connectionreference"},
                     "parameters": {"mappingvariable", "workflowvariable"}}[section]
            rows = [{"id": i, "kind": self.nodes[i]["kind"], "name": self.nodes[i]["name"],
                     "parent_id": self.nodes[i].get("parent_id"),
                     "attributes": self.nodes[i].get("attributes", {}),
                     "evidence": self.nodes[i].get("evidence", {}),
                     "evidence_ids": [i]} for i in ids if self.nodes[i]["kind"] in kinds]
        elif section in {"control_links", "mapping_links"}:
            rows = [{**e, "evidence_ids": [e["id"], e["source"], e["target"]]}
                    for i in ids for e in self.out.get(i, ())
                    if e["target"] in local and (e["kind"] == "workflow_control"
                        if section == "control_links" else e["kind"] != "contains"
                        and e["kind"] != "workflow_control")]
        elif section != "invocations":
            collection = "fields" if section == "mapping_fields" else "properties"
            def relevant(record):
                if section == "embedded_code":
                    return "embedded_code" in record
                if section != "behavior":
                    return True
                if "embedded_code" in record:
                    return True
                attributes = record.get("attributes", {})
                name = str(attributes.get("NAME", "")).lower()
                return any(term in name for term in ("sql", "query", "filter", "join", "command", "email",
                    "parameter", "connection", "commit", "rollback", "recovery", "truncate", "insert",
                    "update", "delete", "source rows", "timer", "wait", "stop on", "fail", "condition"))
            rows = [{"id": i, "owner_kind": self.nodes[i]["kind"], "owner_name": self.nodes[i]["name"],
                     "parent_id": self.nodes[i].get("parent_id"), "ordinal": ordinal,
                     "record": record, "evidence_ids": [i]}
                    for i in ids for ordinal, record in enumerate(self.nodes[i].get(collection, []))
                    if relevant(record)]
        page = _page(rows, offset, limit)
        truncations = []

        def bounded(value, path) -> Any:
            if isinstance(value, str):
                if len(value) > text_limit:
                    truncations.append({"path": path, "total": len(value), "returned": text_limit})
                return value[:text_limit]
            if isinstance(value, dict):
                return {k: bounded(v, path + [k]) for k, v in value.items()}
            if isinstance(value, list):
                return [bounded(v, path + [j]) for j, v in enumerate(value)]
            return value

        page["items"] = bounded(page["items"], [])
        return {"workflow_id": workflow_id, "section": section, **page,
                "text_truncations": truncations,
                "evidence_ids": sorted({i for r in page["items"] for i in r["evidence_ids"]}),
                "basis": "captured_xml_not_runtime_parity", "generation_allowed": False}

    def _closure(self, workflow_id):
        if workflow_id not in self.nodes or self.nodes[workflow_id]["kind"] != "workflow":
            raise ValueError("workflow_id must identify a canonical workflow definition")
        if workflow_id not in self._closures:
            self._closures[workflow_id] = self._walk(workflow_id)
        return self._closures[workflow_id]

    def _ids(self, reached, *kinds):
        return sorted(i for i in reached if self.nodes[i]["kind"] in kinds)

    def _control_edges(self, reached):
        # Control relations are not reachability/definition relations. Preserve
        # them separately, including parallel links and unresolved endpoints.
        return sorted((e for i in reached for e in self.controls.get(i, ())), key=lambda e: e["id"])

    def _counts(self, workflow_id):
        if workflow_id not in self._workflow_counts:
            reached = self._closure(workflow_id)
            counts = Counter(self.nodes[i]["kind"] for i in reached)
            self._workflow_counts[workflow_id] = {
                "objects": len(reached), "taskinstances": counts["taskinstance"],
                "direct_taskinstances": sum(self.nodes[i]["kind"] == "taskinstance"
                                             for i in self.children[workflow_id]),
                "sessions": counts["session"], "mappings": counts["mapping"],
                "sources": counts["source"], "targets": counts["target"],
                "worklets": counts["worklet"],
                "unresolved_references": counts["unresolved_reference"],
                "unresolved_fields": sum(self.nodes[i]["kind"] == "instance_field"
                    and self.nodes[i]["attributes"].get("status") != "resolved" for i in reached),
                "control_links": len(self._control_edges(reached)),
            }
        return dict(self._workflow_counts[workflow_id])

    def _users(self):
        if self._resource_users is None:
            users = defaultdict(set)
            for workflow_id in self.workflow_ids:
                for resource_id in self._closure(workflow_id):
                    if self.nodes[resource_id]["kind"] in {"mapping", "target"}:
                        users[resource_id].add(workflow_id)
            self._resource_users = {i: sorted(ids) for i, ids in users.items()}
        return self._resource_users

    def _provenance(self):
        return {"package_sha256": self.model["package"]["sha256"],
                "model_schema_version": self.model["schema_version"],
                "planning_version": PLANNING_VERSION,
                "evidence_basis": "complete_canonical_structural_graph_not_runtime_verification"}

    def estate_manifest(self, offset: int = 0, limit: int = 50) -> dict:
        """All-workflow totals plus a bounded page of lightweight coverage rows."""
        _bound_number(offset, "offset", 0)
        _bound_number(limit, "limit", 1, _MAX_LIMIT)
        totals = Counter()
        for workflow_id in self.workflow_ids:
            totals.update(self._counts(workflow_id))
        rows = [{"workflow_id": i, "name": self.nodes[i]["name"][:4096],
             "name_character_total": len(self.nodes[i]["name"]),
             "name_truncated": len(self.nodes[i]["name"]) > 4096,
                 "coverage": self._counts(i), "status": "proposed_only",
                 "execution_ready": False} for i in self.workflow_ids[offset:offset + limit]]
        workflows = _page(self.workflow_ids, offset, limit)
        workflows["items"] = rows
        return {"provenance": self._provenance(), "status": "proposed_only",
                "workflow_count": len(self.workflow_ids),
                "coverage_totals": dict(totals),
                "coverage_totals_basis": "sum_of_workflow_closures_shared_objects_count_per_workflow",
                "estate_object_counts": dict(sorted(Counter(n["kind"] for n in self.nodes.values()).items())),
                "workflows": workflows, "generation_allowed": False, "deployment_allowed": False}

    def workflow_contract(self, workflow_id: str, *, offset: int = 0, limit: int = 100,
                          field_limit: int = 50, attribute_limit: int = 100,
                          text_limit: int = 4096) -> dict:
        """Propose preserved execution boundaries without inventing runtime facts."""
        _bound_number(offset, "offset", 0)
        for value, name in ((limit, "limit"), (field_limit, "field_limit"),
                            (attribute_limit, "attribute_limit")):
            _bound_number(value, name, 1, _MAX_LIMIT)
        _bound_number(text_limit, "text_limit", 1, 16384)
        reached = self._closure(workflow_id)

        def bounded(value):
            """Bound every metadata dimension; record exact truncation paths."""
            truncations = []

            def visit(item, path):
                if isinstance(item, str):
                    if len(item) > text_limit:
                        truncations.append({"path": path, "total": len(item), "returned": text_limit})
                    return item[:text_limit]
                if isinstance(item, dict):
                    keys = sorted(item)
                    if len(keys) > attribute_limit:
                        truncations.append({"path": path, "total": len(keys), "returned": attribute_limit})
                    return {k: visit(item[k], path + [k]) for k in keys[:attribute_limit]}
                if isinstance(item, list):
                    if len(item) > attribute_limit:
                        truncations.append({"path": path, "total": len(item), "returned": attribute_limit})
                    return [visit(v, path + [j]) for j, v in enumerate(item[:attribute_limit])]
                return deepcopy(item)

            result = visit(value, [])
            return {"value": result, "truncated": bool(truncations), "truncations": truncations}

        def metadata_page(records, count):
            result = _page(records, 0, count)
            result["items"] = [bounded(record) for record in result["items"]]
            return result

        def node_record(node_id):
            n = self.nodes[node_id]
            return {"id": node_id, "kind": n["kind"], "name": bounded(n["name"]),
                    "parent_id": n["parent_id"], "scope": bounded(n.get("scope", {})),
                    "attributes": bounded(n["attributes"]),
                    "fields": metadata_page(n.get("fields", []), field_limit),
                    "properties": metadata_page(n.get("properties", []), limit),
                    "evidence": bounded(n.get("evidence", {}))}

        def edge_record(e):
            return {"id": e["id"], "source": e["source"], "target": e["target"],
                    "kind": e["kind"], "attributes": bounded(e["attributes"]),
                    "evidence": bounded(e.get("evidence", {}))}

        def page_records(ids, render=node_record):
            result = _page(ids, offset, limit)
            result["items"] = [render(i) for i in result["items"]]
            return result

        local_closures = {}

        def members_for(node_id):
            if node_id not in local_closures:
                local_closures[node_id] = self._walk(node_id)
            return local_closures[node_id]

        def closure_summary(node_id):
            members = members_for(node_id)
            return {kind + "_ids": _page(self._ids(members, kind), 0, limit)
                    for kind in ("session", "mapping", "source", "target", "unresolved_reference")}

        def pipeline_record(node_id):
            return {**node_record(node_id), "closure": closure_summary(node_id),
                    "write_semantics": {"status": "unknown", "verified": False,
                                        "reason": "XML attributes/properties are evidence, not verified write behavior."}}

        def task_record(node_id):
            definitions = [e for e in self.out.get(node_id, ()) if e["kind"] == "task_definition"]
            page = _page(definitions, 0, limit)
            page["items"] = [{"relation": edge_record(e), "definition": node_record(e["target"])}
                             for e in page["items"]]
            return {**node_record(node_id), "definitions": page,
                    "closure": closure_summary(node_id),
                    "boundary_status": "direct" if self.nodes[node_id]["parent_id"] == workflow_id
                    else "nested_definition_not_flattened",
                    "control_semantics": "unverified_preserve_original_failure_skip_and_enabled_attributes"}

        def endpoint_record(node_id):
            n = self.nodes[node_id]
            fields = _page(n.get("fields", []), 0, field_limit)
            fields["items"] = [bounded(f) for f in fields["items"]]
            return {**node_record(node_id),
                    "logical_identity": {"canonical_definition_id": node_id,
                                         "qualifiers": bounded(n["attributes"]),
                                         "scope": bounded(n.get("scope", {}))},
                    "schema": {"status": "captured_unverified" if fields["total"] else "not_captured",
                               "fields": fields},
                    "connection_status": "unverified",
                    "physical_binding": None,
                    "required_physical_bindings": ["environment", "connection", "physical_object"],
                    "binding_status": "required_unverified",
                    "write_semantics": {"status": "unknown", "verified": False}}

        controls = self._control_edges(reached)
        task_ids = self._ids(reached, "taskinstance")
        task_graph = nx.DiGraph()
        task_graph.add_nodes_from(task_ids)
        task_graph.add_edges_from((e["source"], e["target"]) for e in controls)
        components = sorted((sorted(c) for c in nx.strongly_connected_components(task_graph)
                             if len(c) > 1 or any(task_graph.has_edge(i, i) for i in c)),
                            key=lambda c: c[0])

        def component_record(members):
            ids = set(members)
            edges = [e["id"] for i in members for e in self.controls.get(i, ()) if e["target"] in ids]
            return {"members": _page(members, 0, limit), "edges": _page(sorted(edges), 0, limit),
                    "status": "blocked_cycle_requires_runtime_semantics_review"}

        users = self._users()
        target_ids = self._ids(reached, "target")
        conflicts = [i for i in target_ids if len(users.get(i, [])) > 1]

        def conflict_record(target_id):
            return {"target_definition_id": target_id, "workflow_ids": _page(users[target_id], 0, limit),
                    "status": "potential_shared_target_conflict", "write_semantics": "unknown",
                    "sequence": "unknown", "inferred_ordering": False,
                    "physical_alias_conflicts": "unknown_until_bindings_verified"}

        mapping_ids = self._ids(reached, "mapping")
        candidates = [{"kind": "shared_mapping_identity", "mapping_id": i,
                       "workflow_ids": users[i]} for i in mapping_ids if len(users.get(i, [])) > 1]
        if self._fingerprints is None:
            self._fingerprints = mapping_fingerprints(self.model)
        candidates += [{"kind": "metadata_fingerprint", **group} for group in self._fingerprints
                       if set(group["mapping_ids"]) & reached]

        def candidate_record(candidate):
            return {**{k: deepcopy(v) for k, v in candidate.items()
                       if k not in {"workflow_ids", "mapping_ids"}},
                    **{k: _page(candidate[k], 0, limit) for k in ("workflow_ids", "mapping_ids") if k in candidate},
                    "status": "candidate_only_not_semantic_equivalence", "safe_merge": False,
                    "parameterization_authorized": False,
                    "required_reviews": [name for name, _ in _REVIEWS]}

        nested = self._ids(reached - {workflow_id}, "worklet", "workflow")
        missing_mapping_tasks = []
        for task_id in task_ids:
            task_closure = members_for(task_id)
            if not self._ids(task_closure, "mapping"):
                missing_mapping_tasks.append(task_id)
        unresolved = self._ids(reached, "unresolved_reference")
        unresolved_fields = sorted(i for i in reached if self.nodes[i]["kind"] == "instance_field"
                                   and self.nodes[i]["attributes"].get("status") != "resolved")
        external_connections = sorted(i for i in reached if self.nodes[i]["kind"] == "connectionreference"
            and self.nodes[i]["attributes"].get("status") in {"external", "unknown"})
        blockers = ["physical_bindings_unverified", "write_and_control_semantics_unverified",
                    "schedule_checkpoint_transaction_comparison_required", "owner_approval_required"]
        if external_connections:
            blockers.append("external_connection_metadata_required")
        if not mapping_ids:
            blockers.append("no_resolved_mapping")
        if not target_ids or not self._ids(reached, "source"):
            blockers.append("source_target_coverage_incomplete")
        if unresolved or unresolved_fields:
            blockers.append("unresolved_references_or_fields")
        if missing_mapping_tasks:
            blockers.append("tasks_without_mapping_require_disposition_not_silent_omission")
        if nested:
            blockers.append("nested_workflow_or_worklet_boundary_blocked")
        if components:
            blockers.append("task_cycles_require_review")
        if conflicts:
            blockers.append("shared_target_sequence_unknown")
        unsupported = self.model.get("coverage", {}).get("unsupported_tags", {})
        if unsupported:
            blockers.append("package_unsupported_tags_require_review")

        definition_links = sorted((e for i in reached for e in self.out.get(i, ())
                                   if e["kind"] in _RELATIONS and e["kind"] != "contains"),
                                  key=lambda e: e["id"])
        # All remaining captured objects, including extension writer settings,
        # configurations, connection references, transformations and shortcuts.
        # These are evidence, not silently interpreted runtime semantics.
        main_kinds = {"workflow", "worklet", "taskinstance", "session", "mapping", "source", "target"}
        runtime_ids = sorted(i for i in reached if self.nodes[i]["kind"] not in main_kinds)

        return {
            "provenance": self._provenance(), "workflow_id": workflow_id,
            "status": "proposed_only", "workflow": node_record(workflow_id),
            "coverage": self._counts(workflow_id),
            "coverage_basis": "canonical_forward_definition_closure_including_nested_boundaries",
            "tasks": page_records(task_ids, task_record),
            "sessions": page_records(self._ids(reached, "session"), pipeline_record),
            "mappings": page_records(mapping_ids, pipeline_record),
            "sources": page_records(self._ids(reached, "source"), endpoint_record),
            "targets": page_records(target_ids, endpoint_record),
            "definition_links": page_records(definition_links, edge_record),
            "runtime_evidence": page_records(runtime_ids),
            "control_links": page_records(controls, edge_record),
            "sequencing": {"policy": "preserve_exact_conditional_edges_no_inferred_ordering",
                           "parallel_execution_authorized": False, "inferred_edges": [],
                           "cross_workflow_sequence": "unknown", "topological_flattening": False},
            "task_cycles": page_records(components, component_record),
            "tasks_without_mapping": _page(missing_mapping_tasks, offset, limit),
            "unresolved_references": page_records(unresolved),
            "external_connection_metadata": {**page_records(external_connections),
                "basis": "external_or_redacted_connection_is_metadata_gap_not_missing_schema_or_broken_definition"},
            "unresolved_fields": page_records(unresolved_fields),
            "nested_boundaries": page_records(nested),
            "nested_boundary_policy": "blocked_preserve_definition_and_invocation_do_not_inline",
            "schedule_evidence": page_records(self._ids(reached, *_SCHEDULE_KINDS)),
            "parameter_evidence": page_records(self._ids(reached, "mappingvariable", "workflowvariable")),
            "potential_shared_target_conflicts": page_records(conflicts, conflict_record),
            "reuse_candidates": page_records(candidates, candidate_record),
            "combine_parameterized_jobs": {
                "authorized": False, "status": "blocked_pending_explicit_comparison_and_approval",
                "mandatory_reviews": [{"dimension": name, "status": "unverified", "requirement": explanation}
                                      for name, explanation in _REVIEWS],
                "differences": "unknown_not_proven_equivalent",
                "parameter_strategy": "retain_per_invocation_values_and_scope_candidate_only"},
            "readiness": {"execution_ready": False, "blockers": blockers,
                          "missing_connection_implies_missing_schema": False},
            "unsupported_tags": bounded(unsupported),
            "generation_allowed": False, "notebook_generation_allowed": False,
            "deployment_allowed": False,
        }