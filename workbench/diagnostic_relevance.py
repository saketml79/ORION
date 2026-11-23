"""Scenario-specific interpretation, never a rewrite of canonical diagnostic history."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DiagnosticRelevance:
    category: str
    impact: str
    reason: str
    owner_id: str | None
    references: list[dict]
    basis: str = "selected_saved_canonical_analysis"
    resolved: bool = False


def project_diagnostic(diagnostic, nodes, outgoing, scenario):
    owner = diagnostic.get("object_id", diagnostic.get("node_id"))
    related = [nodes[target] for target in outgoing.get(owner, ())
               if target in nodes and nodes[target].get("kind") == "unresolved_reference"]
    if nodes.get(owner, {}).get("kind") == "unresolved_reference":
        related.append(nodes[owner])
    refs = [{"id": node["id"], "kind": node["kind"],
             "expected_kinds": node.get("attributes", {}).get("expected_kinds", []),
             "status": node.get("attributes", {}).get("status"),
             "status_reason": node.get("attributes", {}).get("status_reason")}
            for node in related]
    code = str(diagnostic.get("code", "")).upper()
    connection = bool(refs) and all(set(r["expected_kinds"]) == {"connection"} for r in refs)
    # Older snapshots lack status_reason. Exact canonical kind is sufficient;
    # message wording alone is not proof of an external connection reference.
    if connection:
        result = DiagnosticRelevance("legacy_connection_context",
            "non_blocking_context" if scenario == "bronze" else "human_review",
            ("Historical Informatica connection metadata is not a source blocker in the Unity Catalog input scenario. The selected source tables are read from Unity Catalog; bronze/silver/gold are independent Databricks destination layers."
             if scenario == "bronze" else
             "Review the original-system connection and approved runtime binding. Missing connection metadata does not prevent a logical outline."), owner, refs)
    elif code in {"UNSUPPORTED_CONTROL", "UNSUPPORTED_CONTROL_IMPLEMENTATION",
                  "UNSUPPORTED_MAPPING_IMPLEMENTATION", "CONTROL_NOT_IMPLEMENTED", "NOT_IMPLEMENTED"}:
        result = DiagnosticRelevance("application_capability", "affected_task_review",
            "The application has not implemented this control adapter. This is not proof of a missing source definition. Cite the affected control and preserve its behavior as scoped work.", owner, refs)
    else:
        result = DiagnosticRelevance("canonical_evidence_gap", "affected_task_blocker",
            "Inspect the exact owning object and reference. Missing session, mapping, source, field or control evidence still blocks affected design/implementation claims; no blanket resolution is inferred.", owner, refs)
    return asdict(result)