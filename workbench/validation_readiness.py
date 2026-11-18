"""Read-only distinct-workflow obligations, never a parity certificate issuer.

Reuse canonical invocation/definition closures. No wrappers, session counts,
mock children or controlled synthetic differentials qualify as business proof.
"""
import networkx as nx

from workbench.validation_contracts import SourceContracts, LEDGER_PRIORITY_IDS


STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")


def blocked_release_gate():
    return {"status": "BLOCKED", "minimum_business_workflows": 4,
            "verified_business_workflows": 0, "deployment_allowed": False,
            "reference_kind": "original_independent_outputs_required",
            "synthetic_contract_tests_qualify": False, "wrapper_smokes_qualify": False,
            "criteria": {name: "NOT_RUN" for name in (
                "complete_business_execution", "typed_column_multiset", "keys_nulls_aggregates",
                "rejects_and_side_effects", "retry_failure_concurrency", "optimization_baseline",
                "least_privilege", "serverless_runtime")},
            "certificate_issuer_implemented": False}


def workflow_readiness(context, selected, limit=10):
    """Select actual business definitions from the selected invocation cohort.

    If fewer than ten business definitions exist, do not pad with wrappers or
    repeated session uses. Zero unresolved references does not prove execution.
    """
    context.local(selected)
    cohort = nx.node_connected_component(context.graph.to_undirected(as_view=True), selected)
    summaries = [context.summary(w) for w in cohort]
    contracts = SourceContracts(context)
    evidence = {s["workflow_id"]: contracts.workflow(s["workflow_id"]) for s in summaries}
    eligible = [s for s in summaries if evidence[s["workflow_id"]]["classification"] == "business_definition_candidate"]
    outward = nx.descendants(context.graph, selected)
    eligible.sort(key=lambda s: (s["workflow_id"] != selected,
                                 s["workflow_id"] not in outward,
                                 LEDGER_PRIORITY_IDS.index(s["workflow_id"]) if s["workflow_id"] in LEDGER_PRIORITY_IDS else len(LEDGER_PRIORITY_IDS),
                                 s["name"], s["workflow_id"]))
    rows = []
    for summary in eligible[:limit]:
        local = context.local(summary["workflow_id"])
        nodes = [context.nodes[i] for i in local]
        types = {str(n.get("attributes", {}).get("TYPE", "")).casefold()
                 for n in nodes if n["kind"] == "transformation"}
        blockers = ["FULL_SESSION_AND_WORKFLOW_RUNTIME_NOT_VERIFIED",
                    "APPROVED_SCOPED_BINDINGS_AND_PARAMETERS_REQUIRED",
                    "INDEPENDENT_NONEMPTY_BUSINESS_OUTPUTS_REQUIRED"]
        if "stored procedure" in types:
            blockers.append("EXTERNAL_PROCEDURE_BEHAVIOR_REQUIRED")
        if "sequence" in types or "sequence generator" in types:
            blockers.append("SEQUENCE_ALLOCATION_AND_RETRY_CONTRACT_REQUIRED")
        if "java" in types or "java transformation" in types:
            blockers.append("JAVA_SESSION_INTEGRATION_NOT_VERIFIED")
        if summary["unresolved_references"]:
            blockers.append("UNRESOLVED_CANONICAL_REFERENCES")
        rows.append({**summary, "status": "BLOCKED", "runtime_status": "NOT_RUN",
                     "source_execution_contract": evidence[summary["workflow_id"]],
                     "reference_kind": "unavailable", "reference_status": "BLOCKED",
                     "fully_converted": False, "business_outputs_verified": False,
                     "blocker_codes": blockers, "job_id": None, "run_id": None,
                     "count_semantics": "distinct_workflow_definition_not_session_or_wrapper"})
    return rows