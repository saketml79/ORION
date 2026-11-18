"""Versioned read-only package migration context, not a compiler or control graph.

Derived from the loaded canonical model, including older immutable artifacts.
No reparsing, source reads, remote calls, canonical mutations or approval changes.
"""
from collections import defaultdict

import networkx as nx

from workbench.domain import _RELATIONS
from workbench.source_evidence import workflow_invocations
from workbench.reference_diagnostics import reference_report, source_blocking_reference, sql_dependencies


CONTEXT_VERSION = "package-migration-context-v1"


class PackageContext:
    def __init__(self, model):
        self.model = model
        self.nodes = {n["id"]: n for n in model["nodes"]}
        self.out = defaultdict(list)
        for edge in model["edges"]:
            if edge["kind"] in _RELATIONS:
                self.out[edge["source"]].append(edge["target"])
        self.invocations = workflow_invocations(model)
        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(n["id"] for n in model["nodes"] if n["kind"] == "workflow")
        for row in self.invocations:
            # Ambiguity is a question, not multiple invented invocation edges.
            if len(row["candidate_workflow_ids"]) == 1:
                self.graph.add_edge(row["caller_workflow_id"], row["candidate_workflow_ids"][0])
        self._local = {}
        self._sql = {}

    def local(self, workflow_id):
        if self.nodes.get(workflow_id, {}).get("kind") != "workflow":
            raise ValueError("workflow_id must identify a canonical workflow definition")
        if workflow_id not in self._local:
            seen, pending = {workflow_id}, [workflow_id]
            while pending:
                for target in self.out.get(pending.pop(), ()):
                    if target not in seen and self.nodes[target]["kind"] != "workflow":
                        seen.add(target)
                        pending.append(target)
            self._local[workflow_id] = seen
        return self._local[workflow_id]

    def summary(self, workflow_id):
        node = self.nodes[workflow_id]
        local = self.local(workflow_id)
        counts = {kind: sum(self.nodes[i]["kind"] == kind for i in local)
                  for kind in ("source", "target", "mapping", "unresolved_reference")}
        source_refs = [i for i in local if self.nodes[i]["kind"] == "unresolved_reference"
                   and source_blocking_reference(self.nodes[i], self.nodes)]
        return {"workflow_id": workflow_id, "name": node["name"],
                "scope": {k: node.get("scope", {}).get(k) for k in ("repository", "folder")},
                "direct_sources": counts["source"], "direct_targets": counts["target"],
                "mappings": counts["mapping"], "unresolved_references": counts["unresolved_reference"],
                "source_status": ("direct_sources_captured_not_bound" if counts["source"] else
                                  "no_resolved_sources" if source_refs else
                                  "no_direct_sources"),
                "readiness": "not_assessed", "evidence_ids": [workflow_id]}

    @staticmethod
    def _identity(node):
        attrs = node.get("attributes", {})
        # Complete exported identity only. Missing/dynamic qualifiers cannot
        # prove a physical match; SQL side effects are not covered by this test.
        values = tuple(attrs.get(k, "") for k in ("DATABASETYPE", "DBDNAME", "OWNERNAME", "NAME"))
        if not all(values) or any("$" in v or "[REDACTED]" in v for v in values):
            return None
        return (node.get("scope", {}).get("repository", ""), *values)

    def contract(self, workflow_id, *, offset=0, limit=40):
        self.local(workflow_id)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Invalid package context pagination")
        cohort = nx.node_connected_component(self.graph.to_undirected(as_view=True), workflow_id)
        ordered = [workflow_id, *sorted(cohort - {workflow_id})]
        related = [r for r in self.invocations if r["caller_workflow_id"] in cohort
               or cohort.intersection(r["candidate_workflow_ids"])]
        summaries = [self.summary(i) for i in ordered]
        undirected = self.graph.to_undirected(as_view=True)
        for summary in summaries:
            member = summary['workflow_id']
            path = nx.shortest_path(undirected, workflow_id, member)
            summary['inclusion_reason'] = ('Selected source workflow' if member == workflow_id else
                'Connected through literal calls, including callers and siblings; not a data-only relationship')
            summary['inclusion_path'] = [{
                'caller_workflow_id': call['caller_workflow_id'],
                'candidate_workflow_ids': call['candidate_workflow_ids'],
                'command_owner_id': call['command_owner_id'],
                'wait_mode': call.get('wait_mode', 'unspecified'),
                'completion_dependency_verified': False
            } for left, right in zip(path, path[1:]) for call in self.invocations
                if len(call['candidate_workflow_ids']) == 1
                and {call['caller_workflow_id'], call['candidate_workflow_ids'][0]} == {left, right}]
        descendants = nx.descendants(self.graph, workflow_id)
        child_summaries = [self.summary(i) for i in sorted(descendants)]
        rows = []
        for row in related:
            wait = row.get("wait_mode", "unspecified")
            rows.append({**row, "caller_name": self.nodes[row["caller_workflow_id"]]["name"],
                "command_name": self.nodes.get(row["command_owner_id"], {}).get("name"),
                "candidate_names": [self.nodes[i]["name"] for i in row["candidate_workflow_ids"]],
                "direction": "outward" if row["caller_workflow_id"] == workflow_id else
                             "inward" if workflow_id in row["candidate_workflow_ids"] else "cohort",
                "completion": "asynchronous_no_completion_dependency" if wait == "nowait" else
                              "command_wait_requested_not_verified_completion" if wait == "wait" else
                              "completion_semantics_unresolved",
                "enforced_completion_dependency": False})
        cycles = [sorted(c) for c in nx.strongly_connected_components(self.graph.subgraph(cohort))
                  if len(c) > 1 or any(self.graph.has_edge(i, i) for i in c)]
        # Compare exact source/target identities within this invocation cohort,
        # not shared mappings, labels or an inferred estate dependency graph.
        resources = defaultdict(lambda: {"source": [], "target": []})
        incomplete_identities = 0
        for wid in ordered:
            for i in sorted(self.local(wid)):
                n = self.nodes[i]
                if n["kind"] not in {"source", "target"}:
                    continue
                identity = self._identity(n)
                if identity is None:
                    incomplete_identities += 1
                else:
                    resources[identity][n["kind"]].append((wid, i))
        potentials = []
        for identity, uses in sorted(resources.items()):
            writers = sorted({w for w, _ in uses["target"]})
            readers = sorted({w for w, _ in uses["source"]})
            if len(writers) > 1 or any(w != r for w in writers for r in readers):
                potentials.append({"identity": list(identity), "writer_workflow_ids": writers,
                    "reader_workflow_ids": readers, "shared_write": len(writers) > 1,
                    "evidence_ids": sorted({i for group in uses.values() for _, i in group}),
                    "basis": "exact_exported_identity_potential_only_runtime_binding_unverified",
                    "enforced_dependency": False})
        questions = ["Which outputs, keys, row counts, values and side effects must remain equivalent?",
                     "Confirm runtime repository routing, parameter files, launch conditions and failure/restart ownership.",
                     "Which upstream source snapshots or bronze bindings establish readiness? Invocation is not data readiness."]
        blockers = ["Package output parity, source/target bindings and operational readiness have not been assessed."]
        unresolved = [r for r in rows if r["resolution"] != "scoped_literal_candidate"]
        if unresolved:
            blockers.append(f"{len(unresolved)} child invocation references are missing, ambiguous or dynamic/unsupported; affected output coverage is unknown.")
        if cycles:
            blockers.append("Invocation cycles require explicit re-entry, concurrency and termination contracts; this is not a completion DAG.")
        if any(r.get("wait_mode") == "nowait" for r in rows):
            questions.append("Which launches must remain nonblocking, and where is child completion actually checked?")
        if potentials:
            questions.append("Confirm ownership, ordering, transactions and idempotency for potential data dependencies/shared writes.")
        def page(values):
            selected = values[offset:offset + limit]
            return {"items": selected, "total": len(values), "offset": offset, "limit": limit,
                    "has_more": offset + len(selected) < len(values),
                    "next_offset": offset + len(selected) if offset + len(selected) < len(values) else None,
                    "truncated": len(selected) != len(values)}
        selected = self.summary(workflow_id)
        local = self.local(workflow_id)
        if workflow_id not in self._sql:
            self._sql[workflow_id] = sql_dependencies(self.nodes, local)
        diagnostic = reference_report(self.nodes, local, offset=offset, limit=limit)
        outward = [r for r in rows if r["direction"] == "outward"]
        selected["source_explanation"] = (
            "Captured source definitions are metadata, not approved bindings or populated source data." if selected["direct_sources"] else
            "Source definition resolution is blocked. Inspect the exact references below and supply missing qualified evidence." if selected["source_status"] == "no_resolved_sources" else
            "Orchestration workflow. Sources belong to the called workflow scopes listed below; review each child separately." if outward else
            "No direct source definition is captured in this workflow. Inspect command/SQL evidence and external scripts before concluding that no data is used.")
        return {"version": CONTEXT_VERSION, "package_hash": self.model["package"]["sha256"],
                "workflow_id": workflow_id, "selected": selected,
                "reference_diagnostics": diagnostic,
                "sql_dependencies": {**page(self._sql[workflow_id]), "binding_status": "unverified"},
                "cohort": {**page(summaries), "basis": "connected_exact_literal_invocation_candidates_not_execution_order",
                           "closure_complete": not unresolved, "literal_component_complete": True,
                           "direct_source_count_sum": sum(s["direct_sources"] for s in summaries),
                           "direct_target_count_sum": sum(s["direct_targets"] for s in summaries),
                           "count_semantics": "per_workflow_counts_shared_definitions_may_repeat"},
                "referenced_children": {**page(child_summaries),
                    "source_count_sum": sum(s["direct_sources"] for s in child_summaries),
                    "target_count_sum": sum(s["direct_targets"] for s in child_summaries),
                    "basis": "outward_transitive_literal_candidates_not_all_connected_cohort_members"},
                "invocations": page(rows), "outside_references": page(unresolved),
                "cycles": page(sorted(cycles)), "potential_data_dependencies": page(potentials),
                "data_identity_coverage": {"scope": "invocation_cohort_only_excludes_SQL_and_external_writers",
                                           "incomplete_identity_uses": incomplete_identities},
                "convert_together": "Review the entire invocation cohort as one migration contract, preserving outputs and launch semantics. This is not a requirement to merge all workflows into one Job.",
                "keep_external_explicitly": "Any excluded child, upstream scheduler, source or shared writer needs an owner, binding, timing/failure contract and explicit disposition. Unrelated workflows are not silently included.",
                "blockers": blockers, "human_questions": questions,
                "evidence_ids": [workflow_id], "risk_status": "not_assessed",
                "invocation_coverage": "strict_literal_pmcmd_evidence_only_other_launchers_not_inferred",
                "canonical_edges_changed": False, "child_metadata_reads": False,
                "candidate_complete": False, "production_ready": False,
                "generation_allowed": False, "deployment_allowed": False}