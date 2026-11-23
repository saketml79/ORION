"""Canonical source execution obligations, not an executable migration or approval.

Business candidates require a session/mapping and a wired non-control target.
Control profiles use exported endpoint type AND complete field shape, never a
workflow-name blacklist. Unknown/partial evidence fails closed.
"""
from collections import defaultdict
import base64
import gzip
import io
import json
import lzma

import networkx as nx

from workbench import delta_state as ds

VERSION = "canonical-business-contract-v2"
MAX_CONTRACT_BYTES = 2 * 1024 * 1024
# Explicit shortlist priorities from R18, not inferred business/layer approval.
# Every ID must still pass the canonical session, wiring and target-role test.
LEDGER_PRIORITY_IDS = (
    "1f2d8e0005f5c66ad13b678b3d05600dfe66397ef3b8f0fb1ff745e725c7ec1e",
    "3a0bd7a5542c3d85242eea6b7728052c78756b953b16aede8b742a376cc221d0",
    "aace026d8594f07c73cc7cae17ca43a0dd958cce4274d2dbc0f9b2a3f4cccb09",
    "6e35f8e262fa5241078bafbfa7b32f9382b998214857026d60f94af28bde3ad5",
    "de407411c86398dfa43f9ba2fd637a648606b54323f0a2626556ee61e1aec9a8",
    "1a5ea5e269314305b7e6354826587e26c56f47436e84d730244413e21b7ddab2",
    "40158c7bef80e37d08c3c6b312bb8ab37123b74f4508d6316983f0b2b60ff720",
    "9d9f0522b3776b460514ba513ccb4bb4e7698f2fa22a3e374ef2edf2acf73820",
    "cc83602a87198ef25f7356f6650eaba26945e95135b7c9916821be701dcc118d",
    "0ea0a448cb7a402cef914159a2f03dc1c8a1bca0ff2f5a40084d3b9478e1f897",
)
CONTROL_FIELDS = {
    "AE2E_JOB_DEP_CHK_COUNT": {"TEMPLATE_ID", "COUNTER"},
    "AE2E_JOB_LOG": {"RUN_ID", "JOB_NAME", "START_TIME", "END_TIME"},
    "AE2E_UNIX_JOB_LOG": {"RUN_ID", "TEMPLATE_ID", "RUN_DATE", "START_TIME", "END_TIME", "LAST_JOB_STEP", "NEXAS_UPD_IND"},
    "TARGET_DUMMY": {"COUNT", "NEWFIELD", "NEWFIELD1", "NEWFIELD2", "NEWFIELD3", "NEWFIELD4"},
    "DUMMY_TGT": {"LINE"},
}


def pack_source_contract(contract):
    raw = ds._json(contract).encode()
    if len(raw) > MAX_CONTRACT_BYTES:
        raise ValueError("Source contract exceeds bounded decoded size")
    return {"encoding": "lzma-base64-source-contract-v2", "decoded_bytes": len(raw),
        "decoded_sha256": ds._hash(contract), "version": contract["version"],
        "workflow_id": contract["workflow_id"], "contract_sha256": contract["contract_sha256"],
        "data": base64.b64encode(lzma.compress(raw, preset=6)).decode("ascii")}


def unpack_source_contract(payload):
    if payload.get("encoding") != "lzma-base64-source-contract-v2":
        raise ValueError("Unrecognized source contract encoding")
    size = payload.get("decoded_bytes")
    if type(size) is not int or not 0 < size <= MAX_CONTRACT_BYTES:
        raise ValueError("Invalid source contract decoded bound")
    decoder = lzma.LZMADecompressor(memlimit=32 * 1024 * 1024)
    raw = decoder.decompress(base64.b64decode(payload["data"], validate=True), max_length=size + 1)
    if len(raw) != size or not decoder.eof or decoder.unused_data:
        raise ValueError("Source contract decoded size mismatch")
    contract = json.loads(raw)
    if (ds._hash(contract) != payload["decoded_sha256"]
            or contract.get("version") != payload["version"] or contract.get("workflow_id") != payload["workflow_id"]
            or contract.get("contract_sha256") != payload["contract_sha256"]
            or contract["contract_sha256"] != ds._hash({k: v for k, v in contract.items() if k != "contract_sha256"})):
        raise ValueError("Source execution contract mismatch")
    return contract


def campaign_source_contract(materialized, pins):
    """Canonical-reference normal form, not a duplicate copy of package evidence.

    Preserve the full materialization hash and every task/session/mapping use.
    Large edge/property inventories already belong to the exact canonical input.
    Offline exports materialize them separately; these refs never read exports
    as hosted authority or silently substitute another understanding version.
    """
    reference = {"authority": "selected_immutable_canonical_understanding", "pins": pins,
        "materialized_contract_sha256": materialized["contract_sha256"],
        "runtime_obligation_count": len(materialized["runtime_obligations"]),
        "runtime_obligations_sha256": ds._hash(materialized["runtime_obligations"]),
        "control_link_count": len(materialized["control_links"]),
        "control_links_sha256": ds._hash(materialized["control_links"]),
        "external_connection_count": len(materialized["external_connections"]),
        "external_connections_sha256": ds._hash(materialized["external_connections"]),
        "mappings_sha256": ds._hash(materialized["mappings"]),
        "standalone_reference_is_execution_receipt": False}
    mappings = [{"mapping_id": m["mapping_id"], "source_definition_ids": m["source_definition_ids"],
        "targets": m["targets"], "unresolved_reference_ids": m["unresolved_reference_ids"],
        "data_flow_edge_count": len(m["data_flow_edge_ids"]),
        "data_flow_edge_ids_sha256": ds._hash(m["data_flow_edge_ids"]), "scope_sha256": m["scope_sha256"]}
        for m in materialized["mappings"]]
    # Construct a new normal form; do not mutate/remove canonical evidence.
    contract = {k: materialized[k] for k in ("version", "workflow_id", "classification", "business_target_ids",
        "tasks", "invocations", "source_scope_sha256", "approved", "execution_allowed", "output_parity",
        "required_contracts", "selection_basis")}
    contract.update(canonical_evidence_reference=reference, mappings=mappings)
    contract["contract_sha256"] = ds._hash(contract)
    return pack_source_contract(contract)


def verify_materialized_contract(payload, materialized, pins):
    if campaign_source_contract(materialized, pins) != payload:
        raise ValueError("Canonical source materialization does not match campaign refs")
    return materialized


def target_role(node):
    fields = {f.get("attributes", {}).get("NAME", "").upper() for f in node.get("fields", [])}
    name = node.get("name", "").upper()
    database = node.get("attributes", {}).get("DATABASETYPE", "").casefold()
    expected_database = "flat file" if name in {"TARGET_DUMMY", "DUMMY_TGT"} else "oracle"
    if fields and fields == CONTROL_FIELDS.get(name) and database == expected_database:
        return "control_or_placeholder"
    # A mismatched known control profile is unknown, not a business promotion.
    if not fields or name in CONTROL_FIELDS:
        return "unclassified"
    return "business_target_candidate"


class SourceContracts:
    def __init__(self, context):
        self.context = context
        self.nodes = context.nodes
        self.edges = defaultdict(list)
        for edge in context.model["edges"]:
            self.edges[edge["source"]].append(edge)
        self.cache = {}

    def closure(self, node_id):
        if node_id not in self.cache:
            seen, pending = {node_id}, [node_id]
            while pending:
                for target in self.context.out.get(pending.pop(), ()):
                    if target not in seen and self.nodes[target]["kind"] != "workflow":
                        seen.add(target)
                        pending.append(target)
            self.cache[node_id] = seen
        return self.cache[node_id]

    def mapping(self, mapping_id):
        local = self.closure(mapping_id)
        instances = {i for i in local if self.nodes[i]["kind"] == "instance"}
        definitions = {i: [e["target"] for e in self.edges[i] if e["kind"] in {"instance_of", "shortcut_reference"}]
                       for i in instances}
        flows = [e for i in sorted(instances) for e in self.edges[i]
                 if e["kind"] == "data_flow" and e["target"] in instances]
        graph = nx.DiGraph()
        graph.add_nodes_from(instances)
        graph.add_edges_from((e["source"], e["target"]) for e in flows)
        roots = []
        for instance, ids in definitions.items():
            for definition in ids:
                node = self.nodes[definition]
                query = any(p.get("attributes", {}).get("NAME", "").casefold() == "sql query"
                            and p.get("attributes", {}).get("VALUE", "").strip() not in {"", "[REDACTED]"}
                            for p in node.get("properties", []))
                if node["kind"] == "source" or (node["kind"] == "transformation"
                        and node.get("attributes", {}).get("TYPE", "").casefold() == "source qualifier"
                        and node.get("fields") and query):
                    roots.append(instance)
        targets = []
        for instance, ids in sorted(definitions.items()):
            for definition in sorted(ids):
                node = self.nodes[definition]
                if node["kind"] != "target":
                    continue
                wired = sorted(r for r in set(roots) if r != instance and nx.has_path(graph, r, instance))
                targets.append({"target_id": definition, "target_instance_id": instance,
                    "role": target_role(node), "wired_source_instance_ids": wired,
                    "field_schema_sha256": ds._hash(node.get("fields", []))})
        return {"mapping_id": mapping_id, "targets": targets,
            "source_definition_ids": sorted(i for i in local if self.nodes[i]["kind"] == "source"),
            "unresolved_reference_ids": sorted(i for i in local if self.nodes[i]["kind"] == "unresolved_reference"),
            "data_flow_edge_ids": sorted(e["id"] for e in flows),
            "scope_sha256": ds._hash(sorted(local))}

    def workflow(self, workflow_id):
        local = self.context.local(workflow_id)
        tasks = []
        mappings = {}
        for task_id in sorted(i for i in local if self.nodes[i]["kind"] == "taskinstance"):
            task = self.nodes[task_id]
            session_ids = sorted(i for i in self.closure(task_id) if self.nodes[i]["kind"] == "session")
            mapping_ids = sorted(i for i in self.closure(task_id) if self.nodes[i]["kind"] == "mapping")
            for mapping_id in mapping_ids:
                if mapping_id not in mappings:
                    mappings[mapping_id] = self.mapping(mapping_id)
            tasks.append({"task_instance_id": task_id, "name": task["name"],
                "task_type": task.get("attributes", {}).get("TASKTYPE"),
                "enabled_setting": task.get("attributes", {}).get("ISENABLED"),
                "session_ids": session_ids, "mapping_ids": mapping_ids,
                "runtime_status": "NOT_RUN", "contract_sha256": ds._hash({
                    "task": task, "session_closure": [self.nodes[i] for i in sorted(self.closure(task_id))]})})
        business = sorted({t["target_id"] for m in mappings.values() for t in m["targets"]
                           if t["role"] == "business_target_candidate" and t["wired_source_instance_ids"]})
        targets = [t for m in mappings.values() for t in m["targets"]]
        unknown = any(t["role"] == "unclassified" or (t["role"] == "business_target_candidate"
                      and not t["wired_source_instance_ids"]) for t in targets)
        calls = [r for r in self.context.invocations if r["caller_workflow_id"] == workflow_id]
        role = "business_definition_candidate" if business else "unclassified" if unknown else (
            "launch_wrapper" if calls else "control_only" if targets else "no_business_mapping_evidence")
        connections = [{"id": i, "status": self.nodes[i].get("attributes", {}).get("status", "unverified"),
            "reason": self.nodes[i].get("attributes", {}).get("status_reason", "physical_connection_unverified")}
            for i in sorted(local) if self.nodes[i]["kind"] == "connectionreference"]
        runtime = []
        for i in sorted(local):
            node = self.nodes[i]
            if node["kind"] not in {"session", "sessionextension", "sesstransformationinst", "transformation",
                                    "mappingvariable", "workflowvariable", "source", "target"}:
                continue
            runtime.append({"id": i, "kind": node["kind"], "name": node["name"],
                "parent_id": node.get("parent_id"), "type": node.get("attributes", {}).get("TYPE"),
                "evidence": node.get("evidence", {}), "metadata_sha256": ds._hash(node),
                "property_obligations": [{"ordinal": ordinal, "name": p.get("attributes", {}).get("NAME"),
                    "sha256": ds._hash(p), "evidence": p.get("evidence", {})}
                    for ordinal, p in enumerate(node.get("properties", []))],
                "physical_binding": None, "snapshot_id": None, "approved": False})
        contract = {"version": VERSION, "workflow_id": workflow_id, "classification": role,
            "business_target_ids": business, "tasks": tasks,
            "runtime_obligations": runtime,
            "control_links": [{"id": e["id"], "source": e["source"], "target": e["target"],
                "attributes_sha256": ds._hash(e["attributes"]), "evidence": e.get("evidence", {})}
                for i in sorted(local) for e in self.edges[i] if e["kind"] == "workflow_control"],
            "mappings": [mappings[i] for i in sorted(mappings)], "external_connections": connections,
            "invocations": [{"owner_id": r["command_owner_id"], "candidate_workflow_ids": r["candidate_workflow_ids"],
                "wait_mode": r.get("wait_mode"), "resolution": r["resolution"],
                "completion_dependency_established": False} for r in calls],
            "source_scope_sha256": ds._hash(sorted(local)), "approved": False,
            "execution_allowed": False, "output_parity": False,
            "required_contracts": ["physical_sources_and_snapshots", "scoped_parameters_and_clock",
                "target_columns_layers_and_file_disposition", "procedures_sequences_transactions",
                "conditional_control_retry_recovery", "independent_original_candidate_outputs"],
            "selection_basis": "session_mapping_wired_noncontrol_target_schema_not_workflow_name"}
        contract["contract_sha256"] = ds._hash(contract)
        return contract

    def cohort_inventory(self, workflow_id):
        cohort = nx.node_connected_component(self.context.graph.to_undirected(as_view=True), workflow_id)
        rows = []
        for wid in sorted(cohort):
            contract = self.workflow(wid)
            rows.append({"workflow_id": wid, "name": self.nodes[wid]["name"],
                "classification": contract["classification"], "contract_sha256": contract["contract_sha256"],
                "mapping_ids": [m["mapping_id"] for m in contract["mappings"]],
                "business_target_ids": contract["business_target_ids"],
                "invocation_owner_ids": [r["owner_id"] for r in contract["invocations"]]})
        return rows


def target_assignment_contract(targets, context):
    """Requested three layers are intent, not nine fabricated mapping approvals."""
    rows = []
    for target in targets:
        node = context.nodes[target["target_id"]]
        rows.append({"target_id": target["target_id"], "schema": target["schema"], "table": target["table"],
            "role": target_role(node), "destination_layer": target["destination_layer"],
            "destination_catalog": target["destination_catalog"], "approved": False,
            "file_disposition_required": node.get("attributes", {}).get("DATABASETYPE", "").casefold() == "flat file",
            "column_contract_sha256": ds._hash(target["columns"]),
            "required_review": ["layer_rationale", "source_snapshot_and_column_mapping", "keys_types_nulls_width_clock",
                "write_mode_transactions_rejects_retry", "file_delivery_disposition_if_applicable"]})
    return {"version": VERSION, "requested_layers": ["bronze", "silver", "gold"], "targets": rows,
        "unassigned_target_ids": sorted(r["target_id"] for r in rows if r["destination_layer"] is None),
        "approved": False, "execution_allowed": False, "contract_sha256": ds._hash(rows)}