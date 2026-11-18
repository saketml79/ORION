"""Read-only, bounded readiness projection. No generation, evaluation or authority writes."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

from fastapi import HTTPException, Query, Request

from workbench import delta_state as ds
from workbench.validation_campaigns import FORMAT, input_pins
from workbench.validation_readiness import blocked_release_gate
from workbench.validation_metrics import campaign_metrics

MAX_RECORDS = 2000
MAX_BYTES = 8 * 1024 * 1024
ROLES = ("supervisor", "discovery", "semantics", "architecture", "validation", "artifact")
FAMILIES = ("runs", "designs", "reviews", "proposals", "documents", "jobs", "campaigns", "loops", "attempts")


def local_evidence(service, package, project):
    """Read existing service-owned files/databases only, never instantiate a writer."""
    result = {key: [] for key in FAMILIES}
    complete = True
    used = 0

    def add(key, value):
        nonlocal complete, used
        used += len(ds._json(value).encode())
        if len(result[key]) >= MAX_RECORDS or used > MAX_BYTES:
            complete = False
            return False
        result[key].append(value)
        return True

    for directory, family in (("agent-runs", "runs"), ("jobs", "jobs"), ("validation-campaigns/" + package, "campaigns")):
        for number, path in enumerate((service.state / directory).glob("*.json")):
            if number >= MAX_RECORDS or path.stat().st_size > MAX_BYTES:
                complete = False
                break
            value = json.loads(path.read_bytes())
            if family == "campaigns":
                from workbench.validation_campaigns import CampaignStore, MANIFEST_FORMAT
                candidate = value.get("summary", {}) if value.get("format") == MANIFEST_FORMAT else value
                if candidate.get("project_id") != project or candidate.get("pins", {}).get("package_hash") != package:
                    continue
                value = CampaignStore(local=service.state / "validation-campaigns").summary(project, package, path.stem)
                if value.get("artifact_availability") == "unavailable":
                    complete = False
            elif value.get("package_hash") != package:
                continue
            if not add(family, value):
                break
    queries = (("reviews.sqlite", "proposals", "proposals"), ("reviews.sqlite", "events", "reviews"),
               ("reviews.sqlite", "documents", "documents"), ("redesigns.sqlite", "redesign_versions", "designs"),
               ("redesigns.sqlite", "redesign_reviews", "reviews"), ("loops.sqlite", "runs", "loops"),
               ("loops.sqlite", "attempts", "attempts"))
    for filename, table, family in queries:
        path = service.state / filename
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if table == "redesign_reviews":
                rows = db.execute("SELECT r.* FROM redesign_reviews r JOIN redesign_versions v ON r.id=v.id AND r.version=v.version WHERE v.package_hash=? LIMIT ?", (package, MAX_RECORDS + 1))
            else:
                # Identifiers are the fixed table literals above, never request input.
                rows = db.execute('SELECT * FROM "' + table + '" WHERE package_hash=? LIMIT ?', (package, MAX_RECORDS + 1))
            for number, row in enumerate(rows):
                if number == MAX_RECORDS:
                    complete = False
                    break
                if len(row["payload"].encode()) + used > MAX_BYTES:
                    complete = False
                    break
                value = json.loads(row["payload"])
                if table == "events":
                    if row["kind"] != "review":
                        continue
                    value = {"sequence": row["sequence"], "package_hash": package,
                             "proposal_id": row["proposal_id"], "kind": row["kind"], **value}
                if table == "redesign_reviews":
                    value = {**value, "design_id": row["id"]}
                if not add(family, value):
                    break
    return result, complete


def hosted_evidence(runtime, inventory, generation, scope, *, families=None, workflow_id=None, design_id=None):
    """Verify exact committed package families; campaigns have independent immutable envelopes."""
    from workbench.hosted_object_reads import scope_records
    from workbench.validation_campaigns import CampaignStore
    result = {key: [] for key in FAMILIES}
    used = 0
    if families is not None and "lessons" in families:
        result["lessons"] = []

    def reference_budget(value):
        if isinstance(value, dict):
            if "$artifact" in value and value["$artifact"].get("byte_size", MAX_BYTES + 1) + used > MAX_BYTES:
                raise ValueError("Readiness external evidence byte bound exceeded")
            for item in value.values():
                reference_budget(item)
        elif isinstance(value, list):
            for item in value:
                reference_budget(item)
    mappings = {"proposal_versions": {"redesign_versions": "designs", "proposals": "proposals"},
        "review_events": {"events": "reviews", "redesign_reviews": "reviews"},
        "documents": {"documents": "documents"}, "agent_runs": {"json": "runs"},
        "jobs": {"json": "jobs"}, "loop_runs": {"runs": "loops"}, "loop_attempts": {"attempts": "attempts"}}
    if families is not None and "lessons" in families:
        mappings["loop_lessons"] = {"lessons": "lessons"}
    for table, sources in mappings.items():
        if families is not None:
            sources = {source: family for source, family in sources.items() if family in families}
            if not sources:
                continue
        rows = scope_records(runtime, table, inventory, generation, scope, maximum=MAX_RECORDS)
        for row in rows:
            family = sources.get(row["source_table"])
            # JSON structural proposal exports and unrelated job records are not conversions.
            if family is None:
                continue
            if workflow_id is not None and family in {"designs", "lessons"} and row["workflow_id"] != workflow_id:
                continue
            if design_id is not None and (family == "designs" or row["source_table"] == "redesign_reviews") and row["entity_id"] != design_id:
                continue
            packed = json.loads(row["payload"])
            reference_budget(packed)
            raw = runtime._decode(packed)
            value = raw if row["source_table"] == "json" else json.loads(raw["payload"])
            if family == "designs" and (
                    json.loads(row["source_key"]) != [raw["id"], raw["version"]]
                    or raw["id"] != value.get("id") or raw["version"] != value.get("version")
                    or raw["workflow_id"] != value.get("workflow_id")
                    or row["workflow_id"] != raw["workflow_id"]
                    or raw["package_hash"] != scope["package_hash"]
                    or raw["scenario"] != value.get("scenario")):
                raise ValueError("Redesign source identity mismatch")
            if family == "lessons" and (
                    row["source_path"] != f'projects/{scope["project_id"]}/package-contexts/{scope["package_hash"]}/state/loops.sqlite'
                    or json.loads(row["source_key"]) != [raw["id"]]
                    or raw["id"] != value.get("id")
                    or raw["package_hash"] != scope["package_hash"]
                    or raw["workflow_id"] != value.get("workflow_id")
                    or row["workflow_id"] != raw["workflow_id"]):
                raise ValueError("Redesign lesson source identity mismatch")
            if row["source_table"] == "events":
                if raw["kind"] != "review":
                    continue
                value = {"sequence": raw["sequence"], "package_hash": scope["package_hash"],
                         "proposal_id": raw["proposal_id"], "kind": raw["kind"], **value}
            elif row["source_table"] == "redesign_reviews":
                value = {**value, "design_id": raw["id"]}
            used += len(ds._json(value).encode())
            if used > MAX_BYTES:
                raise ValueError("Readiness evidence byte bound exceeded")
            if value.get("package_hash", scope["package_hash"]) != scope["package_hash"]:
                raise ValueError("Readiness payload package mismatch")
            if len(result[family]) >= MAX_RECORDS:
                raise ValueError("Readiness evidence family bound exceeded")
            result[family].append(value)
    if families is not None and "campaigns" not in families:
        return result, True
    store = CampaignStore(delta=runtime.store, artifacts=runtime.artifacts)
    cursor = None
    campaigns_complete = True
    while True:
        reports, cursor, more = store.page(scope["project_id"], scope["package_hash"], cursor)
        used += sum(len(ds._json(r).encode()) for r in reports)
        result["campaigns"].extend(reports)
        if any(r.get("artifact_availability") == "unavailable" for r in reports):
            campaigns_complete = False
        if len(result["campaigns"]) > MAX_RECORDS or used > MAX_BYTES:
            raise ValueError("Readiness campaign bound exceeded")
        if not more:
            break
    return result, campaigns_complete


def current_versions(index, evidence):
    """Same advisory snapshot hash as Service.agent_inputs, without save_proposals."""
    from workbench.app import _hash, _digest, agent_module
    versions = {}
    proposals = {}
    for item in evidence["proposals"]:
        key = item.get("id")
        if key and item.get("version", 0) >= proposals.get(key, {}).get("version", 0):
            proposals[key] = item
    hashes = {"policy": _hash(agent_module._POLICY), "skills": _hash({
        str(p.relative_to(agent_module.SKILLS_ROOT)): _digest(p)
        for p in sorted(agent_module.SKILLS_ROOT.rglob("*.md"))})}
    pins = input_pins(index)
    for record in evidence["runs"] + evidence["designs"] + evidence["jobs"]:
        workflow, scenario = record.get("workflow_id"), record.get("scenario")
        if workflow not in index.nodes or scenario not in {"original", "bronze"}:
            continue
        key = workflow, scenario
        if key in versions:
            continue
        selected = []
        for summary in index.proposal_summaries(scenario):
            if summary["workflow_id"] == workflow:
                selected.append(proposals.get(summary["id"], {k: summary[k] for k in (
                    "id", "workflow_id", "name", "scenario", "version", "status", "blockers", "generation_allowed", "deployment_allowed")}))
        selected.sort(key=lambda p: p["id"])
        ids = {p["id"] for p in selected}
        documents = sorted((d for d in evidence["documents"] if d.get("metadata", {}).get("workflow_id") in {None, "", workflow}), key=lambda d: d["id"])
        reviews = sorted((r for r in evidence["reviews"] if r.get("proposal_id") in ids and r.get("kind") == "review"), key=lambda r: r["sequence"])
        snapshot = {"package_hash": pins["package_hash"], "workflow_id": workflow, "scenario": scenario,
                    "artifact_hash": pins["understanding_sha256"], "documents": documents, "reviews": reviews,
                    "proposals": selected, "hashes": hashes}
        if pins["analysis_version"]:
            snapshot["source_analysis_version"] = pins["analysis_version"]
        versions[key] = _hash(snapshot)
    return versions


def assess(index, project, evidence, complete, *, offset=0, limit=25, scenario="original", generation=None,
           workflow_id=None, knowledge=None):
    pins = input_pins(index)
    package_workflows = sorted(index.kinds.get("workflow", ()), key=lambda w: (w["name"].casefold(), w["id"]))
    ids = {w["id"] for w in package_workflows}
    if workflow_id is not None and workflow_id not in ids:
        raise HTTPException(404, "Unknown workflow in the selected package")
    workflows = [w for w in package_workflows if workflow_id is None or w["id"] == workflow_id]
    versions = current_versions(index, evidence) if complete else {}

    def freshness(record):
        key = record.get("workflow_id"), record.get("scenario")
        if not complete or key not in versions or record.get("source", {}).get("kind") == "loop":
            return "not_verified"
        return "current" if record.get("input_version") == versions[key] else "stale"

    designs = [d for d in evidence["designs"] if d.get("workflow_id") in ids and d.get("scenario") == scenario]
    latest = {}
    for design in designs:
        if design.get("version", 0) > latest.get(design["workflow_id"], {}).get("version", 0):
            latest[design["workflow_id"]] = design
    current = {w: d for w, d in latest.items() if freshness(d) == "current"}
    draft_freshness_complete = complete and all(freshness(d) != "not_verified" for d in latest.values())
    runs = [r for r in evidence["runs"] if r.get("workflow_id") in ids and r.get("scenario") == scenario]
    jobs = [r for r in evidence["jobs"] if r.get("workflow_id") in ids and r.get("scenario") == scenario and r.get("kind") == "spark_draft"]
    receipts = [r for r in jobs if r.get("status") == "completed" and r.get("manifest_sha256") and freshness(r) == "current"
                and r.get("design_id") == current.get(r["workflow_id"], {}).get("id")
                and r.get("design_version") == current.get(r["workflow_id"], {}).get("version")]
    campaigns = []
    for report in evidence["campaigns"]:
        if (report.get("format") != FORMAT or report.get("project_id") != project
                or report.get("pins", {}).get("package_hash") != pins["package_hash"]):
            raise ValueError("Campaign scope mismatch")
        summary = report.get("verified_manifest_summary") is True
        hash_key = "summary_sha256" if summary else "report_sha256"
        if report.get(hash_key) != ds._hash({k: v for k, v in report.items() if k != hash_key}):
            raise ValueError("Campaign integrity mismatch")
        if report.get("workflow_id") in ids and report.get("scenario") == scenario:
            campaigns.append(report)
    fresh_campaigns = [r for r in campaigns if r["pins"] == pins and r.get("artifact_availability") != "unavailable"]
    validation = campaign_metrics(fresh_campaigns, ids, complete=complete)
    outcomes = Counter(r.get("status", "unknown") for r in runs)
    reviews = Counter()
    role_reviews = {role: Counter() for role in ROLES}
    for review in evidence["reviews"]:
        if review.get("design_id"):
            design = next((d for d in designs if d["id"] == review["design_id"] and d["version"] == review.get("version")), None)
            if design:
                reviews[review["action"]] += 1
                if design.get("author_role") in role_reviews:
                    role_reviews[design["author_role"]][review["action"]] += 1
        else:
            proposal = next((p for p in evidence["proposals"] if p.get("id") == review.get("proposal_id") and p.get("scenario") == scenario), None)
            if proposal:
                reviews[review.get("action", "comment")] += 1
    agents = []
    for role in ROLES:
        findings = [(r, f) for r in runs for f in (r.get("output") or {}).get("findings", []) if f.get("role") == role]
        role_runs = [r for r in runs if any(f.get("role") == role for f in (r.get("output") or {}).get("findings", []))
            or any(c.get("role") == role for c in (r.get("output") or {}).get("role_completion", []))]
        latest_run = max(role_runs, key=lambda r: (r.get("created_at", ""), r["id"]), default=None)
        completion = next((c for c in (latest_run.get("output") or {}).get("role_completion", []) if c.get("role") == role), {}) if latest_run else {}
        authored = [d for d in designs if d.get("author_role") == role]
        agents.append({"role": role, "finding_count": len(findings), "typed_design_versions": len(authored),
            "latest_run_id": latest_run.get("id") if latest_run else None,
            "latest_status": completion.get("status", latest_run.get("status")) if latest_run else "not_assessed",
            "freshness": freshness(latest_run) if latest_run else "not_assessed",
            "reported_usage": (latest_run.get("output") or {}).get("usage") if latest_run else None,
            "latest_findings": [{"summary": f.get("summary", "")[:400], "status": f.get("status"),
                "blockers": f.get("blockers", [])[:10], "citation_count": len(f.get("evidence_ids", []))}
                for r, f in findings if latest_run and r["id"] == latest_run["id"]][:6],
            "feedback": dict(role_reviews[role]), "model_accuracy": None,
            "agentic_evaluation": "not_run", "schema_basis": "Stored typed designs/findings only; not data parity."})
    loops = {r["id"]: r for r in evidence["loops"] if r.get("workflow_id") in ids and r.get("scenario") == scenario}
    evaluations = [{"attempt_id": a["id"], "workflow_id": loops[a["run_id"]]["workflow_id"],
        "input_version": loops[a["run_id"]].get("input_version"), "created_at": a.get("created_at"),
        "sample_count": 1, "source": "deterministic_loop_rubric", "rubric": a["rubric"],
        "freshness": "not_verified", "scope": "advisory_schema_and_citations_not_model_accuracy"}
        for a in evidence["attempts"] if a.get("run_id") in loops and a.get("rubric")]
    reasons = [
        {"code": "GENERATION_TRACKING_INCOMPLETE", "impact": "Executable coverage cannot be established.", "next_step": "Track hash-pinned generation receipts for response-only SQL/workflow exports; Spark receipts are drafts only."},
        {"code": "BUSINESS_RUNTIME_NOT_ASSESSED", "impact": "Controlled fixture/schema checks do not prove full business execution.", "next_step": "The owner must supply original input snapshots, exact parameter-file values, approved source and target bindings, and the AE2E_COMMON.TRUNCATE_TABLE and GET_PRODUCT_KEY_ID procedure specifications and bodies where applicable. Implement the missing workflow behavior before separately authorizing execution."},
        {"code": "OUTPUT_PARITY_APPROVAL_NOT_TRACKED", "impact": "Production readiness remains blocked.", "next_step": "For at least four distinct business workflows, the owner must supply independently acquired original Oracle outputs and matching candidate outputs for the same inputs, parameters and business date. Compare typed rows, nulls and duplicates, retain independent run receipts, and obtain explicit parity/release approvals for the exact versions. Empty fixture tables do not qualify."}]
    if not complete:
        reasons.append({"code": "EVIDENCE_BOUND_REACHED", "impact": "Historical aggregates and freshness are unknown.", "next_step": "Narrow or archive history through an approved workflow; do not interpret the bounded sample as totals."})
    if any(r.get("artifact_availability") == "unavailable" for r in campaigns):
        reasons.append({"code": "CAMPAIGN_ARTIFACT_UNAVAILABLE", "impact": "Committed report artifact or length metadata missing; coverage is unavailable, not a partial pass.",
            "next_step": "Inspect the scoped immutable artifact and publication receipt. Do not renew evidence from local exports."})
    if not validation["coverage_available"]:
        reasons.append({"code": "CURRENT_SOURCE_EXECUTION_CONTRACTS_UNAVAILABLE",
            "impact": "Missing or stale classifier/source contracts cannot establish business coverage.",
            "next_step": "Create a separately consented campaign against exact selected understanding and analysis pins; do not import offline export folders as service authority."})
    if validation["counts"]["campaign_unassigned_targets"]:
        reasons.append({"code": "TARGET_LAYERS_UNASSIGNED",
            "impact": f'{validation["counts"]["campaign_unassigned_targets"]} distinct scoped targets have no reviewed layer mapping.',
            "next_step": "Review target IDs, source column/snapshot contracts and bronze/silver/gold placement; retain flat-file disposition blockers."})
    if complete and not draft_freshness_complete:
        reasons.append({"code": "ADVISORY_INPUT_FRESHNESS_NOT_VERIFIED", "impact": "Current logical-draft coverage cannot be counted reliably.",
            "next_step": "Review loop-derived design input contracts in the existing design review; package membership alone does not establish freshness."})
    counts = {"total_workflows": len(workflows), "structurally_analyzed": len(workflows),
        "agent_assessed": len({r["workflow_id"] for r in runs if freshness(r) == "current" and (r.get("output") or {}).get("findings")}) if complete else None,
        "logical_draft": len(current) if draft_freshness_complete else None,
        "generated_draft_receipts": len({r["workflow_id"] for r in receipts}) if draft_freshness_complete and all(freshness(r) != "not_verified" for r in jobs) else None,
        "executable": None, "runtime_validated": None, "output_parity_approved": None}
    counts.update(validation["counts"])
    rows = []
    for workflow in workflows[offset:offset + limit]:
        wid = workflow["id"]
        design = latest.get(wid)
        design_reviews = sorted((r for r in evidence["reviews"]
            if design and r.get("design_id") == design.get("id") and r.get("version") == design.get("version")),
            key=lambda r: (r.get("review_version", 0), r.get("created_at", "")))
        decisions = [r for r in design_reviews if r.get("action") != "comment"]
        latest_decision = decisions[-1] if decisions else None
        design_state = None
        if design:
            body = design.get("design") or {}
            task_rows = body.get("tasks", [])
            edge_rows = body.get("edges", [])
            coverage_rows = body.get("coverage", [])
            artifact_plan = body.get("artifact_plan", [])
            unresolved_rows = body.get("scoped_unresolved", [])
            reuse_rows = body.get("reuse_groups", [])
            alternatives = body.get("alternatives", [])
            projected_body = {
                "title": body.get("title"), "rationale": body.get("rationale"),
                "tasks": task_rows[:100], "edges": edge_rows[:200],
                "coverage": coverage_rows[:200], "artifact_plan": artifact_plan[:100],
                "scoped_unresolved": unresolved_rows[:100], "reuse_groups": reuse_rows[:100],
                "alternatives": alternatives[:20], "blockers": body.get("blockers", [])[:100],
                "cadence": body.get("cadence"), "evidence_ids": body.get("evidence_ids", [])[:200],
                "counts": {"tasks": len(task_rows), "edges": len(edge_rows), "coverage": len(coverage_rows),
                    "artifact_plan": len(artifact_plan), "scoped_unresolved": len(unresolved_rows),
                    "reuse_groups": len(reuse_rows), "alternatives": len(alternatives)},
                "truncated": any((len(task_rows) > 100, len(edge_rows) > 200, len(coverage_rows) > 200,
                    len(artifact_plan) > 100, len(unresolved_rows) > 100, len(reuse_rows) > 100,
                    len(alternatives) > 20))}
            current_input = versions.get((wid, scenario))
            design_state = {"id": design.get("id"), "version": design.get("version"),
                "title": body.get("title"), "author_role": design.get("author_role"),
                "freshness": freshness(design), "input_version": design.get("input_version"),
                "current_input_version": current_input,
                "latest_review_action": latest_decision.get("action") if latest_decision else "pending",
                "latest_review_version": latest_decision.get("review_version") if latest_decision else None,
                "outline_drafting_allowed": bool(latest_decision
                    and latest_decision.get("action") == "approve_for_drafting"
                    and freshness(design) == "current" and not body.get("blockers")),
                "design": projected_body}
        draft_rows = []
        if design:
            for record in sorted((r for r in jobs if r.get("workflow_id") == wid
                                  and r.get("design_id") == design.get("id")
                                  and r.get("design_version") == design.get("version")),
                                 key=lambda r: (r.get("created_at", ""), r.get("id", "")), reverse=True)[:20]:
                output = record.get("output") or {}
                artifact_hashes = output.get("artifact_hashes", {}) if isinstance(output, dict) else {}
                draft_rows.append({"job_id": record.get("id"), "status": record.get("status"),
                    "design_id": record.get("design_id"), "design_version": record.get("design_version"),
                    "input_version": record.get("input_version"), "manifest_sha256": record.get("manifest_sha256"),
                    "artifact_hashes": dict(list(artifact_hashes.items())[:200]) if isinstance(artifact_hashes, dict) else {},
                    "artifact_count": len(artifact_hashes) if isinstance(artifact_hashes, dict) else 0,
                    "artifacts_truncated": isinstance(artifact_hashes, dict) and len(artifact_hashes) > 200,
                    "task_statuses": [{"task_id": t.get("task_id"), "status": t.get("status"),
                        "artifact_paths": t.get("artifact_paths", [])[:30], "blockers": t.get("blockers", [])[:20]}
                        for t in output.get("tasks", [])[:100]] if isinstance(output, dict) else [],
                    "artifact_approved": output.get("artifact_approved") is True if isinstance(output, dict) else False,
                    "execution_allowed": output.get("execution_allowed") is True if isinstance(output, dict) else False,
                    "deployment_allowed": output.get("deployment_allowed") is True if isinstance(output, dict) else False})
        workflow_campaigns = [r for r in fresh_campaigns if r["workflow_id"] == wid]
        business_evidence = validation["workflows"].get(wid)
        rows.append({"workflow_id": wid, "name": workflow["name"],
            "stage": "logical_draft" if wid in current else "not_verified" if design and freshness(design) == "not_verified" else "structural_only",
            "design_version": design.get("version") if design else None,
            "design_freshness": freshness(design) if design else "not_assessed",
            "proposed_state": {"design": design_state, "draft_receipts": draft_rows,
                "basis": "exact selected-package saved redesign and Spark-draft records; no source-to-Databricks correspondence inferred",
                "artifact_hashes_basis": "saved job receipt only; artifact bytes are not reverified by this projection"},
            "blockers": (design.get("design") or {}).get("blockers", []) if design else [],
            "campaign_blockers": [b for r in workflow_campaigns for b in r.get("blockers", [])][:20],
            "campaign_count": len(workflow_campaigns), "business_validation_evidence": business_evidence,
            "reason": "Source execution contract available; business runtime explicitly NOT_RUN and independent outputs unavailable." if business_evidence and business_evidence["runtime_status"] == "NOT_RUN" else "Business runtime and output parity not assessed.",
            "next_step": "Review exact current design and blockers." if design else "Request an explicitly consented advisory review in Agent activity."})
    checks = Counter(c["status"] for r in fresh_campaigns for c in r.get("checks", []))
    issue_counts = Counter(b["code"] for r in fresh_campaigns for b in r.get("blockers", []))
    from workbench.architecture_knowledge import unavailable
    return {"format": "orion-readiness-v1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "architecture_knowledge": knowledge if knowledge is not None else unavailable(),
        "scope": {"project_id": project, "pins": pins, "scenario": scenario, "generation": generation},
        "counts": counts, "stage_counts": {"structural_only": len(workflows) - len(current) if draft_freshness_complete else None,
            "logical_draft": counts["logical_draft"]},
        "count_basis": "Structural-only and current logical-draft stages are exclusive. Other metrics overlap and are not conversion percentages. Generated draft receipts record manifest hashes, not independently reverified artifact bytes or complete executable workflows; response-only exports are untracked.",
        "evidence_complete": complete, "record_bound": MAX_RECORDS, "byte_bound": MAX_BYTES,
        "release_gate": blocked_release_gate(), "production_ready": False, "execution_allowed": False,
        "deployment_allowed": False, "reasons": reasons, "agents": agents,
        "business_validation": validation,
        "agent_run_outcomes": dict(outcomes), "human_feedback": dict(reviews),
        "issue_counts": dict(issue_counts), "issue_count_basis": "Recorded current-campaign blocker occurrences, not distinct workflow totals or inferred missing coverage.",
        "feedback_basis": "Historical advisory outcomes. Outline approval is not parity/release approval; role attribution only for exact authored design versions.",
        "evaluations": evaluations[:50], "evaluation_count": len(evaluations) if complete else None,
        "evaluation_truncated": len(evaluations) > 50, "agentic_evaluation": "not_run", "model_accuracy": None,
        "campaigns": {"current": len(fresh_campaigns), "stale": len(campaigns) - len(fresh_campaigns),
            "check_statuses": dict(checks), "basis": "Controlled fixtures and technical checks only, not business parity.",
            "receipts": [{"id": r["id"], "workflow_id": r["workflow_id"], "pins": r["pins"],
                "created_at": r["created_at"], "status": r["status"], "controlled_fixture": r.get("controlled_fixture"),
                "report_sha256": r["report_sha256"], "source_contract_version": r.get("selection_contract", {}).get("version"),
                "artifact_availability": r.get("artifact_availability", "legacy_inline"),
                "artifact_content_verified_this_read": r.get("artifact_content_verified_this_read", True),
                "freshness": "current" if r["pins"] == pins else "stale"} for r in campaigns[:50]]},
        "workflows": {"items": rows, "total": len(workflows), "package_total": len(package_workflows),
            "offset": offset, "limit": limit, "selected_workflow_id": workflow_id,
            "truncated": offset + limit < len(workflows)}}


def install_readiness(app):
    service = app.state.service
    from workbench.migration_lineage import install_lineage
    install_lineage(app)

    @app.get("/api/readiness")
    def readiness(request: Request, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                  scenario: str = Query("original", pattern="^(original|bronze)$"),
                  workflow_id: str | None = Query(None, pattern=r"^[a-f0-9]{64}$")):
        if getattr(app.state, "hosted_request_authenticated", False):
            raise HTTPException(409, "Hosted readiness requires the verified selected-package projection; no local fallback")
        index = service.require()
        package = index.model["package"]["sha256"]
        selected = request.headers.get("X-Workbench-Package")
        if selected and selected != package:
            raise HTTPException(409, "Selected package changed; refresh readiness")
        project = request.headers.get("X-Workbench-Project") or "__legacy__"
        evidence, complete = local_evidence(service, package, project)
        return assess(index, project, evidence, complete, offset=offset, limit=limit, scenario=scenario,
                  workflow_id=workflow_id)