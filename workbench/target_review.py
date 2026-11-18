"""Read-only source target inventory, never a binding, approval or conversion."""
from collections import defaultdict

import networkx as nx
from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.migration_lineage import page
from workbench.package_context import PackageContext
from workbench.validation_campaigns import input_pins
from workbench.validation_contracts import target_role
from workbench.target_fixtures import target_table


def target_review(index, workflow_id, scope, offset=0, limit=20, expected=None):
    if scope not in {"workflow", "cohort"} or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid target review scope or pagination")
    pins = input_pins(index)
    version = ds._hash(pins)
    if expected is not None and expected != version:
        raise HTTPException(409, "Package evidence changed; refresh target review")
    context = PackageContext(index.model)
    if context.nodes.get(workflow_id, {}).get("kind") != "workflow":
        raise ValueError("Unknown canonical workflow")
    workflows = ({workflow_id} if scope == "workflow" else
                 nx.node_connected_component(context.graph.to_undirected(as_view=True), workflow_id))
    uses = defaultdict(set)
    for wid in sorted(workflows):
        for node_id in context.local(wid):
            if context.nodes[node_id]["kind"] == "target":
                uses[node_id].add(wid)
    rows = []
    for node_id in sorted(uses):
        target = context.nodes[node_id]
        attrs = target.get("attributes", {})
        fields = []
        for field in target.get("fields", []):
            values = field.get("attributes", {})
            fields.append({key: values.get(key) for key in
                           ("NAME", "DATATYPE", "PRECISION", "SCALE", "NULLABLE", "KEYTYPE", "FIELDNUMBER")})
        try:
            candidate = target_table(attrs, fields, target.get("scope", {}))
            fixture_candidate = {"schema_name": candidate["schema"], "table": candidate["table"],
                                 "status": "unverified_test_fixture_candidate"}
        except (ValueError, KeyError, TypeError):
            fixture_candidate = None
        rows.append({"target_id": node_id, "name": target.get("name"),
                     "source_scope": target.get("scope", {}),
                     "exported_identity": {key: attrs.get(key) for key in
                                           ("DATABASETYPE", "DBDNAME", "OWNERNAME", "NAME")},
                     "source_evidence": target.get("evidence", {}), "columns": fields,
                     "role": target_role(target), "workflow_ids": sorted(uses[node_id]),
                     "destination_layer": None, "destination_catalog": None,
                     "destination_schema": None, "destination_table": None,
                     "approved": False, "fixture_candidate": fixture_candidate,
                     "file_disposition_required": str(attrs.get("DATABASETYPE", "")).casefold() == "flat file"})
    return {"format": "source-target-review-v1", "workflow_id": workflow_id,
            "scope": scope, "pins": pins, "input_version": version,
            "workflow_count": len(workflows), "workflow_ids": sorted(workflows),
            "targets": page(rows, offset, limit),
            "cohort_basis": "connected_literal_invocation_candidates_not_completion_order",
            "notice": "Source definitions only. Bronze, silver and gold assignments require explicit review; empty fixtures are not approved bindings.",
            "physical_bindings_verified": False, "assignment_persisted": False,
            "execution_allowed": False, "deployment_allowed": False}