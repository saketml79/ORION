"""Identical canonical presenters for full runtimes and verified read projections."""
from fastapi import HTTPException
from workbench.domain import _redact


def object_response(index, object_id, offset=0, limit=100):
    from workbench.app import _bounded
    from workbench.explanations import explain_workflow
    if object_id not in index.nodes:
        raise HTTPException(404, "Unknown canonical object ID")
    node = index.nodes[object_id]
    graph = index.focus(object_id)
    children = index.children.get(object_id, ())
    diagnostics = index.diagnostics.get(object_id, ())
    explanation = explain_workflow(index.nodes, index.out, object_id) if node["kind"] == "workflow" else None
    return _redact({"node": _bounded(node), "summary": node["summary"],
            "children": [_bounded(n) for n in children[offset:offset + limit]],
            "behavior_explanation": explanation, "children_total": len(children),
            "children_truncated": offset + limit < len(children), "offset": offset, "limit": limit,
            "graph": graph, "graph_total_nodes": graph["total_nodes"], "graph_truncated": graph["truncated"],
            "diagnostics": list(diagnostics[:50]), "diagnostics_total": len(diagnostics),
            "diagnostics_truncated": len(diagnostics) > 50})


def migration_response(index, workflow_id, offset=0, limit=40):
    from workbench.package_context import PackageContext
    if index.nodes.get(workflow_id, {}).get("kind") != "workflow":
        raise HTTPException(404, "Unknown canonical workflow ID")
    context = getattr(index, "migration_context_index", None)
    if context is None:
        context = PackageContext(index.model)
        index.migration_context_index = context
    return _redact(context.contract(workflow_id, offset=offset, limit=limit))