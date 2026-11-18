"""Read-only campaign coverage projection, never log counts or parity authority."""
from hashlib import sha256
from pathlib import Path

from workbench import delta_state as ds
from workbench.validation_contracts import VERSION, unpack_source_contract


def campaign_metrics(reports, workflow_ids, *, complete):
    semantic_hash = sha256(Path(__file__).with_name("validation_contracts.py").read_bytes()).hexdigest()
    current = [r for r in reports if r.get("selection_contract", {}).get("version") == VERSION
               and r.get("semantic_fingerprints", {}).get("validation_contracts.py") == semantic_hash]
    stale_count = len(reports) - len(current)
    sizes = [c.get("source_execution_contract", {}).get("decoded_bytes")
             for r in current for c in r.get("workflow_candidates", [])]
    if any(type(size) is not int or size <= 0 for size in sizes):
        raise ValueError("Invalid source contract decoded evidence size")
    decoded_complete = sum(sizes) <= 8 * 1024 * 1024
    if not decoded_complete:
        current = []
    latest, targets = {}, {}
    for report in sorted(current, key=lambda r: (r["created_at"], r["id"])):
        if report.get("verified_manifest_summary") is True:
            if report.get("summary_sha256") != ds._hash({k: v for k, v in report.items() if k != "summary_sha256"}):
                raise ValueError("Campaign metrics summary mismatch")
            if report.get("artifact_availability") == "unavailable":
                continue
            metrics = report["campaign_metrics"]
            for wid, row in metrics["workflows"].items():
                if wid not in workflow_ids or row["campaign_id"] != report["id"] or row["report_sha256"] != report["report_sha256"]:
                    raise ValueError("Campaign metrics summary scope mismatch")
                latest[wid] = row
            # Manifest keeps exact distinct target identities, not additive counts.
            for target in metrics.get("target_summary", []):
                targets[target["target_id"]] = target
            continue
        for row in report.get("workflow_candidates", []):
            contract = unpack_source_contract(row.get("source_execution_contract", {}))
            if (row.get("workflow_id") not in workflow_ids or contract.get("workflow_id") != row["workflow_id"]
                    or contract.get("version") != VERSION or contract.get("classification") != "business_definition_candidate"
                    or contract.get("canonical_evidence_reference", {}).get("pins") != report["pins"]
                    or contract.get("canonical_evidence_reference", {}).get("authority") != "selected_immutable_canonical_understanding"
                    or contract.get("contract_sha256") != ds._hash({k: v for k, v in contract.items() if k != "contract_sha256"})):
                raise ValueError("Campaign source execution contract mismatch")
            latest[row["workflow_id"]] = {"workflow_id": row["workflow_id"], "campaign_id": report["id"],
                "report_sha256": report["report_sha256"], "contract_sha256": contract["contract_sha256"],
                "contract_version": VERSION, "runtime_status": row.get("runtime_status", "unavailable"),
                "reference_status": row.get("reference_status", "unavailable"),
                "blocker_codes": row.get("blocker_codes", []),
                "source_contract_available": True, "output_parity": False}
        assignment = report.get("target_assignment_contract", {})
        if assignment.get("version") != VERSION or assignment.get("contract_sha256") != ds._hash(assignment.get("targets", [])):
            raise ValueError("Campaign target assignment contract mismatch")
        for target in assignment["targets"]:
            targets[target["target_id"]] = target
    # A zero is observed only in a complete, nonempty scoped contract inventory.
    # Executable/parity receipt ingestion does not exist; no Boolean can mint it.
    available = complete and bool(current)
    return {"version": "campaign-coverage-v2", "source_contract_version": VERSION,
        "counts": {"campaign_business_candidates": len(latest) if available else None,
            "campaign_runtime_not_run": sum(r["runtime_status"] == "NOT_RUN" for r in latest.values()) if available else None,
            "campaign_unassigned_targets": sum(t["destination_layer"] is None for t in targets.values()) if available else None,
            "campaign_targets_requiring_file_disposition": sum(t["file_disposition_required"] for t in targets.values()) if available else None},
        "coverage_available": available, "semantic_stale_campaigns": stale_count,
        "decoded_evidence_complete": decoded_complete,
        "missing_workflow_scope_count": len(workflow_ids - set(latest)) if complete else None,
        "workflows": latest, "business_output_comparisons": None,
        "runtime_receipt_ingestion": "not_implemented", "output_parity": False,
        "target_summary": [{k: t[k] for k in ("target_id", "destination_layer", "file_disposition_required")} for t in targets.values()],
        "basis": "Distinct canonical business candidates and explicit NOT_RUN/target assignment obligations from exact current campaign hashes, input pins and classifier version. Source inventories are content-addressed references to the selected immutable canonical input, not duplicated runtime receipts. These overlap package stages and are not successful runtime or output comparisons."}