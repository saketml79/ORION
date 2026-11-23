"""Bounded canonical estate views. Shared definitions are not dependencies.

No semantic health, runtime behavior, or agent findings are inferred here.
Only canonical containment and exact resolved definition edges are traversed.
"""
from collections import defaultdict, deque
from hashlib import sha256
from itertools import combinations

import networkx as nx

from workbench.domain import _hash


REFERENCE_KINDS = {"task_definition", "session_mapping", "instance_of", "shortcut_reference"}
EDGE_LIMIT = 2000
PATH_LIMIT = 80


def _paths(index, root, stop_kinds=()):
    parents = {root: None}
    queue = deque([root])
    while queue:
        source = queue.popleft()
        if source != root and index.nodes[source]["kind"] in stop_kinds:
            continue
        # Instance-field nodes dominate the full export. Filter their edges
        # before sorting rather than sorting and then discarding every port.
        eligible = (e for e in index.out.get(source, ())
                if e["kind"] in REFERENCE_KINDS | {"contains"}
                and e["target"] in index.nodes
                and index.nodes[e["target"]]["kind"] != "instance_field")
        for edge in sorted(eligible, key=lambda e: e["id"]):
            target = edge["target"]
            if (edge["kind"] not in REFERENCE_KINDS | {"contains"}
                    or target in parents or target not in index.nodes
                    or index.nodes[target]["kind"] == "instance_field"):
                continue
            parents[target] = edge
            queue.append(target)
    return parents


def _path(index, parents, target):
    path = index.path(parents, target)
    return {"edges": [{k: e[k] for k in ("id", "source", "target", "kind", "evidence")}
                      for e in path[:PATH_LIMIT]],
            "total_edges": len(path), "truncated": len(path) > PATH_LIMIT}


def _assessment(index, node):
    parents = _paths(index, node["id"])
    errors = []
    for object_id in sorted(parents):
        for diagnostic in index.diagnostics.get(object_id, ()):
            if diagnostic.get("severity") == "error":
                errors.append(diagnostic.get("id") or _hash(diagnostic))
    return {"status": "red" if errors else "neutral", "basis": "structural_unverified",
            "reason": ("Canonical error diagnostics are scoped to this object or an exact structural reference. "
                       "Semantic and runtime behavior remain unverified." if errors else
                       "Structural metadata only; absence of error diagnostics is not evidence of semantic health."),
            "evidence_ids": sorted(set(errors))[:50] if errors else [node["id"]],
            "error_count": len(errors), "evidence_truncated": len(set(errors)) > 50,
            "source": "unknown", "transformation": "unknown", "destination": "unknown",
            "semantic_status": "unverified", "agent_finding": None}


def package_graph(index, kind="workflow", offset=0, limit=60, *, all_nodes=False, chunk=False):
    """Full-kind counts, stable canonical IDs, bounded nodes/edges and path evidence.

    Workflow reference paths stop at the next workflow, so transitive callers
    are not presented as direct calls. Shared mappings have undirected relations
    and cannot create execution cycles. No XML names/command text are resolved.
    """
    if kind not in {"workflow", "mapping", "source", "target"} or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid package graph bounds or kind")
    if not isinstance(all_nodes, bool) or (all_nodes and offset != 0):
        raise ValueError("All nodes requires a boolean flag and zero offset")
    if type(chunk) is not bool or (chunk and (all_nodes or kind != 'workflow')):
        raise ValueError('Chunk aggregation is supported for workflow metadata only')
    candidates = sorted(index.kinds.get(kind, ()), key=lambda n: n["id"])
    ids = {n["id"] for n in candidates}
    # Explicit full-kind reads are not full XML/field-lineage reads. Preserve
    # ordinary paging bounds, but do not discard cross-page relations in All.
    page = candidates if all_nodes else candidates[offset:offset + limit]
    edge_limit = None if all_nodes or chunk else EDGE_LIMIT
    selected = {n["id"] for n in page}
    paths = {}
    consumers = defaultdict(list)
    if kind == "workflow":
        for node in candidates:
            parents = _paths(index, node["id"], {"workflow", "mapping", "source", "target"})
            paths[node["id"]] = parents
            for target in sorted(parents):
                if index.nodes[target]["kind"] == "mapping":
                    consumers[target].append(node["id"])

    def relations():
        for source in sorted(ids):
            for edge in sorted(index.out.get(source, ()), key=lambda e: e["id"]):
                if edge["target"] in ids:
                    yield {**edge, "directed": True, "relation": edge["kind"], "label": edge["kind"]}
            if kind == "workflow":
                parents = paths[source]
                direct_targets = {e["target"] for e in index.out.get(source, ()) if e["target"] in ids}
                for target in sorted(ids & parents.keys() - {source} - direct_targets):
                    yield {"id": _hash(["definition_reference", source, target]), "source": source,
                           "target": target, "kind": "definition_reference", "relation": "definition_reference",
                           "directed": True, "label": "Exact task definition reference (not runtime dependency)",
                           "evidence": {"path": _path(index, parents, target)}
                           if source in selected and (chunk or target in selected) and (edge_limit is None or len(edges) < edge_limit) else {}}
        for mapping_id in sorted(consumers):
            for source, target in combinations(sorted(consumers[mapping_id]), 2):
                yield {"id": _hash(["shared_definition", source, target, mapping_id]),
                       "source": source, "target": target, "kind": "shared_definition",
                       "relation": "shared_definition", "directed": False,
                       "label": "Shared exact mapping definition (not dependency)",
                       "shared_definition_id": mapping_id,
                       "evidence": {"source_path": _path(index, paths[source], mapping_id),
                                    "target_path": _path(index, paths[target], mapping_id)}
                       if source in selected and (chunk or target in selected) and (edge_limit is None or len(edges) < edge_limit) else {}}

    edges, total_edges, page_edges = [], 0, 0
    directed = nx.DiGraph()
    directed.add_nodes_from(selected)
    for edge in relations():
        total_edges += 1
        if edge["source"] not in selected or (not chunk and edge["target"] not in selected):
            continue
        page_edges += 1
        if edge["directed"]:
            directed.add_edge(edge["source"], edge["target"])
        if edge_limit is None or len(edges) < edge_limit:
            edges.append(edge)
    nodes = [{**{k: n[k] for k in ("id", "kind", "name", "scope", "parent_id", "evidence")},
              "group_id": n["scope"].get("folder_id") or n["parent_id"],
              "assessment": _assessment(index, n)} for n in page]
    group_ids = sorted({n["group_id"] for n in nodes if n["group_id"] in index.nodes})
    groups = [{k: index.nodes[i][k] for k in ("id", "kind", "name", "parent_id")} for i in group_ids]
    complete = len(page) == len(candidates) and page_edges == len(edges)
    manifest_path = index.directory / 'manifest.json'
    evidence_version = _hash([index.model['package']['sha256'], index.model.get('source_analysis_version'),
        sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.exists() else None])
    return {"kind": kind, "package_hash": index.model["package"]["sha256"],
            "evidence_version": evidence_version,
            "nodes": nodes, "edges": edges, "groups": groups, "total_nodes": len(candidates),
            "total_edges": total_edges, "page_total_edges": page_edges,
            "offset": offset, "limit": len(candidates) if all_nodes else limit,
            "all_nodes": all_nodes, "node_limit": len(candidates) if all_nodes else 100,
            "chunk": chunk, "next_offset": offset + len(page) if offset + len(page) < len(candidates) else None,
            "edge_limit": edge_limit,
            "truncated": len(page) < len(candidates) or len(edges) < page_edges,
            "has_more": offset + len(page) < len(candidates),
            "scope": "canonical_same_kind_and_exact_definition_relations_not_full_lineage",
            "has_cycles": not nx.is_directed_acyclic_graph(directed) if complete else None,
            "page_has_cycles": not nx.is_directed_acyclic_graph(directed),
            "cycle_scope": "returned_page_directed_relations_only",
            "outside_page_cycles": "none_outside_page" if complete else "unknown",
            "assessment": {"basis": "structural_unverified", "semantic_status": "unverified",
                           "source": "unknown", "transformation": "unknown", "destination": "unknown"},
            "legend": {"neutral": "Source structure only; package risk not assessed, not marked safe",
                       "amber": "Recorded incomplete assessment, not an unassessed source object",
                       "red": "Scoped canonical error diagnostic, not inferred semantic failure",
                       "green": "Not assigned by this structural-only view"},
            "generation_allowed": False, "deployment_allowed": False}