"""Canonical, role-local replay of successful planning retrievals, never ID grants."""
from workbench.domain import _hash, _redact


def planning_receipt(role, workflow_id, section, offset, limit, report):
    return {"role": role, "workflow_id": workflow_id, "section": section,
            "offset": offset, "limit": limit,
            "evidence_ids": list(report["evidence_ids"]),
            "report_sha256": _hash(_redact(report))}


def replay_planning(model, workflow_id, receipts, records):
    """Return separate evidence maps per role. Validate ordered invocation access.

    Recompute every page from the pinned canonical model. A receipt cannot grant
    all nodes of a related workflow, another role's access, or arbitrary IDs.
    Base evidence retains the existing selected-workflow/neighbor contract.
    """
    from workbench.migration_planning import PlanningIndex
    if not isinstance(receipts, list) or len(receipts) > 10000:
        raise ValueError("Invalid planning retrieval receipts")
    if not receipts:
        return {}
    planning = PlanningIndex(model)
    nodes = {n["id"]: n for n in model["nodes"]}
    edges = {e["id"]: e for e in model["edges"]}
    roles, related = {}, {}
    keys = {"role", "workflow_id", "section", "offset", "limit", "evidence_ids", "report_sha256"}
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != keys:
            raise ValueError("Invalid planning retrieval receipt")
        role = receipt["role"]
        if role not in {"supervisor", "discovery", "semantics", "architecture", "validation", "artifact"}:
            raise ValueError("Invalid retrieval role")
        selected = receipt["workflow_id"]
        allowed = related.setdefault(role, set())
        if selected != workflow_id and selected not in allowed:
            raise ValueError("Related workflow was not retrieved by this role")
        if (type(receipt["offset"]) is not int or receipt["offset"] < 0
                or type(receipt["limit"]) is not int or not 1 <= receipt["limit"] <= 20
                or not isinstance(receipt["section"], str)):
            raise ValueError("Invalid planning page")
        report = planning.agent_contract(selected, receipt["section"],
                                         offset=receipt["offset"], limit=receipt["limit"])
        if receipt != planning_receipt(role, selected, receipt["section"], receipt["offset"], receipt["limit"], report):
            raise ValueError("Planning retrieval does not match canonical page")
        role_records = roles.setdefault(role, dict(records))
        if receipt["section"] == "invocations":
            for row in report["items"]:
                allowed.update([row["caller_workflow_id"], *row["candidate_workflow_ids"]])
            role_records.update({i: nodes[i] for i in report["evidence_ids"] if i in nodes})
        if selected != workflow_id:
            role_records.update({i: nodes[i] for i in report["evidence_ids"] if i in nodes})
            role_records.update({i: edges[i] for i in report["evidence_ids"] if i in edges})
    return roles