"""Selected WIL parent + outward child evidence contract and conversion gate.

Inward callers are review boundaries, not additional Jobs to run. No remote
discovery, inference, SQL execution, deployment, or automatic approval is used.
Only this explicitly reviewed migration unit is assessed by this module.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from workbench.demo_conversion import WORKFLOW_NAME, CHILD_NAME, _digest
from workbench.dependency_compiler import MAPPING_ID, compile_dependency_check, definition_scope
from workbench.domain import _redact
from workbench.package_context import PackageContext


VERSION = "wil-parent-child-parity-v1"
PARENT_ID = "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38"


def build_parity_candidate(model, workflow_id=PARENT_ID, *, include_evidence=True, context=None):
    context = context or PackageContext(model)
    if context.model is not model:
        raise ValueError("Package context must belong to this model")
    parent = context.nodes.get(workflow_id, {})
    if workflow_id != PARENT_ID or parent.get("name") != WORKFLOW_NAME or parent.get("kind") != "workflow":
        raise ValueError("Only the reviewed WIL_NET_DIS parent migration unit is supported")
    units, pending, calls = {workflow_id}, [workflow_id], []
    while pending:
        current = pending.pop()
        for call in context.invocations:
            if call["caller_workflow_id"] != current:
                continue
            calls.append(call)
            if len(call["candidate_workflow_ids"]) == 1:
                target = call["candidate_workflow_ids"][0]
                if target not in units:
                    units.add(target)
                    pending.append(target)
    selected_ids = set().union(*(context.local(w) for w in units))
    selected = [context.nodes[i] for i in sorted(selected_ids)]
    definitions = {}
    uses, procedures, phases, parameter_files = [], [], [], []
    for node in selected:
        if node["kind"] == "mapping":
            scope = definition_scope(model, node["id"])
            transformations = [{"id": n["id"], "name": n["name"],
                "type": n.get("attributes", {}).get("TYPE", ""),
                "template": n.get("attributes", {}).get("TEMPLATENAME", ""),
                "implementation_status": "not_converted"}
                for n in scope["nodes"] if n["kind"] == "transformation"]
            definitions[node["id"]] = {
                "mapping_id": node["id"], "name": node["name"],
                "scope_sha256": _digest(scope),
                "source_ids": [n["id"] for n in scope["nodes"] if n["kind"] == "source"],
                "target_ids": [n["id"] for n in scope["nodes"] if n["kind"] == "target"],
                "transformation_ids": [n["id"] for n in scope["nodes"] if n["kind"] == "transformation"],
                "transformation_obligations": transformations,
                "unresolved_reference_ids": [n["id"] for n in scope["nodes"] if n["kind"] == "unresolved_reference"],
                "conversion_status": "not_converted",
                "reason": "No full implementation of " + ", ".join(sorted({t["type"] or t["template"] or "unknown transformation" for t in transformations}))
                          + "; preserve exact ports/connectors, instance SQL phases, target writes, rejects and recovery",
            }
        if node["kind"] == "transformation" and node.get("attributes", {}).get("TYPE") == "Stored Procedure":
            props = {p["attributes"].get("NAME"): p["attributes"].get("VALUE", "")
                     for p in node.get("properties", [])}
            procedures.append({"owner_id": node["id"], "parent_id": node.get("parent_id"),
                "qualified_name": props.get("Stored Procedure Name", ""),
                "call_text": props.get("Call Text", ""), "phase": props.get("Stored Procedure Type", ""),
                "ports": node.get("fields", []), "evidence": node.get("evidence", {}),
                "body_status": "not_captured_in_transformation_definition",
                "required_owner_input": "Exact package body including overloads, dependencies, grants, transaction and exception behavior",
                "replacement_generated": False})
        for ordinal, prop in enumerate(node.get("properties", [])):
            attrs = prop.get("attributes", {})
            name, value = attrs.get("NAME", ""), attrs.get("VALUE", "")
            if name == "Parameter Filename":
                parameter_files.append({"owner_id": node["id"], "path_expression": value,
                    "evidence": prop.get("evidence", {}), "contents_status": "not_supplied_to_this_contract"})
            if re.search(r"sql|query|override", name, re.I) or name == "Call Text":
                # Never flatten session overrides onto reusable mapping defaults.
                phases.append({"owner_id": node["id"], "owner_kind": node["kind"],
                    "parent_id": node.get("parent_id"), "property_ordinal": ordinal,
                    "role": name, "text": value, "evidence": prop.get("evidence", {}),
                    "implementation_status": "not_converted_or_executed"})
        if node["kind"] == "taskinstance" and node.get("attributes", {}).get("TASKTYPE") == "Session":
            scope = definition_scope(model, node["id"])
            session_ids = [n["id"] for n in scope["nodes"] if n["kind"] == "session"]
            mapping_ids = [n["id"] for n in scope["nodes"] if n["kind"] == "mapping"]
            uses.append({"workflow_id": node.get("parent_id"), "instance_id": node["id"],
                "instance_name": node["name"], "session_definition_ids": session_ids,
                "mapping_definition_ids": mapping_ids,
                "session_scope_node_ids": [n["id"] for n in scope["nodes"]],
                "instance_attributes": node.get("attributes", {}),
                "instance_properties": node.get("properties", []),
                "session_override_owner_ids": [n["id"] for n in scope["nodes"]
                    if n["kind"] in {"session", "sesstransformationinst", "sessionextension"}],
                "error_settings": [{"owner_id": n["id"], "property": p}
                    for n in scope["nodes"] for p in n.get("properties", [])
                    if re.search(r"stop on errors|rollback|recovery|commit", p.get("attributes", {}).get("NAME", ""), re.I)],
                "conversion_status": "not_converted",
                "reason": "Mapping core candidates do not implement session target writes, SQL phases or error/restart behavior"})
    artifacts = {}
    dependency_status = "missing_mapping_evidence"
    if MAPPING_ID in definitions:
        try:
            compiled = compile_dependency_check(model)
        except ValueError:
            dependency_status = "unsupported_fingerprint_or_missing_Java_sidecar"
        else:
            artifacts.update(compiled["artifacts"])
            dependency_status = "pinned_core_candidate_not_full_session"
            definitions[MAPPING_ID]["core_candidate_contract"] = compiled["contract"]
    incoming = [c for c in context.invocations if c["caller_workflow_id"] not in units
                and units.intersection(c["candidate_workflow_ids"])]
    child_present = any(context.nodes[w]["name"] == CHILD_NAME for w in units - {workflow_id})
    blockers = []
    def block(code, detail, evidence_ids):
        blockers.append({"code": code, "detail": detail, "evidence_ids": sorted(evidence_ids)})
    if not child_present:
        block("CHILD_IMPLEMENTATION_NOT_IN_MODEL", "Wrapper-only input cannot establish child coverage", [workflow_id])
    for call in calls:
        if len(call["candidate_workflow_ids"]) != 1:
            block("UNRESOLVED_CHILD_INVOCATION", "Missing, ambiguous or dynamic child reference", call["evidence_ids"])
    for mid, row in definitions.items():
        block("MAPPING_NOT_CONVERTED", row["name"] + ": " + row["reason"], [mid])
        if row["unresolved_reference_ids"]:
            block("CANONICAL_REFERENCES_UNRESOLVED", row["name"] + ": resolve exact exported references without inventing definitions", row["unresolved_reference_ids"])
    for proc in procedures:
        block("EXTERNAL_PROCEDURE_BODY_REQUIRED", proc["qualified_name"] + ": " + proc["body_status"], [proc["owner_id"]])
    for item in parameter_files:
        block("SCOPED_PARAMETER_VALUES_REQUIRED", item["path_expression"] + ": contents not supplied", [item["owner_id"]])
    if dependency_status != "pinned_core_candidate_not_full_session":
        block("DEPENDENCY_JAVA_NOT_SUPPORTED", dependency_status, [MAPPING_ID] if MAPPING_ID in definitions else [workflow_id])
    block("CHILD_CONTROL_AND_SESSION_ERROR_CONTRACT_NOT_CONVERTED",
          "Preserve log-check row-count decisions, success links, Stop on errors, retries and all four conditional load stages", units)
    block("PARENT_TRANSACTION_PARITY_NOT_ESTABLISHED",
          "Oracle partition membership, commit interval/rollback, concurrent writers and partial replay are not equivalent to separate Delta commits", [workflow_id])
    block("PHYSICAL_BINDINGS_AND_CLOCK_UNVERIFIED",
          "Resolved scoped parameters, source snapshots/freshness, target types and Oracle-compatible run-date clock require owner input", units)
    block("REAL_OUTPUT_DIFFERENTIAL_NOT_RUN",
          "No paired Oracle/Databricks outputs, duplicates/nulls, values, rejects, side effects or recovery receipts supplied", units)
    gate = {"status": "blocked", "full_package_conversion": False, "output_parity": "not_established",
            "converted_sessions": 0, "session_uses": len(uses), "unique_mappings": len(definitions),
            "dependency_check": dependency_status, "blockers": blockers,
            "generation_scope": "Partial candidates only; existing wrapper generation remains available",
            "deployment_allowed": False, "execution_allowed": False, "production_ready": False}
    report = {"version": VERSION, "package_sha256": model["package"]["sha256"],
        "selected_workflow_id": workflow_id,
        "scope": "selected parent plus outward exact invocation candidates, not connected estate cohort",
        "migration_unit": [context.summary(w) for w in sorted(units)],
        "inward_callers_review_only": incoming, "outward_invocations": calls,
        "launch_contract": "Preserve -nowait submission. Do not execute inward callers or launch a shared child twice. No completion edge inferred.",
        "session_uses": sorted(uses, key=lambda r: (r["workflow_id"], r["instance_name"])),
        "mapping_definitions": sorted(definitions.values(), key=lambda r: r["name"]),
        "stored_procedures": procedures, "sql_phase_contracts": phases,
        "parameter_file_contracts": parameter_files, "gate": gate,
        "required_output_evidence": {
            "identity": "Same business date, resolved per-instance parameters, source snapshots, types, keys and code hashes",
            "comparison": "Every target: multiset row comparison, nulls, numeric/date precision, reject rows and load counts, not counts alone",
            "effects": "Procedure, sequence, log, partition, transaction, email and asynchronous child submission observations",
            "failure_cases": "Timeout, query/sleep errors, missing/multiple templates, partial writes, retries, concurrent writers and restart/replay",
            "authority": "Owner-approved expected outcomes plus independently captured Oracle and Databricks run receipts",
        }, "remote_execution": "not_run", "model_calls": 0}
    if include_evidence:
        # Retain exact fields, connectors, per-instance overrides and provenance in
        # a separately indexed snapshot. Shared definitions are not duplicated uses.
        report["evidence_snapshot"] = {"nodes": selected,
            "edges": [e for e in model["edges"] if e["source"] in selected_ids and e["target"] in selected_ids]}
        from workbench.workflow_artifacts import _parameter_inventory
        report["scoped_parameter_inventory"] = _parameter_inventory(model, report["evidence_snapshot"])
    report = _redact(report)
    artifacts["parity_readiness.json"] = json.dumps(report, indent=2, sort_keys=True) + "\n"
    return {"report": report, "artifacts": artifacts}


def completion_gate(model, workflow_id, *, context=None):
    if workflow_id != PARENT_ID:
        return {"status": "not_assessed_by_selected_unit_compiler", "full_package_conversion": False,
                "output_parity": "not_established", "deployment_allowed": False}
    return build_parity_candidate(model, workflow_id, include_evidence=False, context=context)["report"]["gate"]


def export_parity_candidate(candidate, destination):
    """Explicit exclusive local export. Manifest last, never overwrite history."""
    destination = Path(destination)
    artifacts = candidate["artifacts"]
    for name in artifacts:
        if name not in {"parity_readiness.json", "modules/dependency_check.py", "dependency_contract.json"}:
            raise ValueError("Unexpected artifact path")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"version": VERSION, "package_sha256": candidate["report"]["package_sha256"],
                "full_package_conversion": False, "output_parity": "not_established",
                "artifacts": {}}
    for name, content in sorted(artifacts.items()):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        with path.open("xb") as stream:
            stream.write(data)
        manifest["artifacts"][name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


if __name__ == "__main__":
    import argparse
    from workbench.understanding import analyze_package
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    candidate = build_parity_candidate(analyze_package(args.source))
    manifest = export_parity_candidate(candidate, args.destination)
    print(json.dumps({"exported": str(args.destination), "artifact_count": len(manifest["artifacts"]),
                      "gate": candidate["report"]["gate"]["status"],
                      "full_package_conversion": False, "output_parity": "not_established", "deployed": False}))