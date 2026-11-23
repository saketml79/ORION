"""Additive, local-only WIL parent/child artifact API, separate from wrappers.

Compile the exact reviewed canonical snapshot. Never change canonical bytes,
saved analyses, existing wrapper APIs, approval state or hosted artifacts.
"""
from collections import Counter
from hashlib import sha256
import argparse
import ast
import inspect
import json
from pathlib import Path
import re

import yaml
import sqlglot
from sqlglot.errors import ParseError
from sqlglot.tokenizer_core import TokenType

from workbench import wil_control
from workbench import wil_runtime_contract
from workbench.demo_conversion import _digest, _without_annotations
from workbench.dependency_compiler import compile_dependency_check
from workbench.package_parity import PARENT_ID, build_parity_candidate
from workbench.wil_mapping_compiler import MappingCompiler, sql_candidate
from workbench.workflow_artifacts import _notebook, compile_workflow_artifacts


VERSION = "wil-parent-child-source-compiler-v2"
PACKAGE_SHA256 = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
UNIT_SHA256 = "eae9d6f0f419dd13ba6da85c4444459b983c9b8e234a1720ecfb521a1a2dce44"
CHILD_ID = "1f2d8e0005f5c66ad13b678b3d05600dfe66397ef3b8f0fb1ff745e725c7ec1e"


def discover_external_evidence(root, *, remote_observation=None):
    """Streaming repository search. Report locations/hashes only, never secrets.

    Covers multiline DDL and full local source/output/document trees, excluding
    installed dependencies and caches. A declaration is a candidate, not a
    reviewed procedure implementation. It cannot clear any conversion gate.
    """
    root = Path(root).resolve()
    excluded = {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache"}
    extensions = {".xml", ".sql", ".txt", ".md", ".py", ".json", ".yaml", ".yml",
                  ".html", ".pkb", ".pks", ".pkg", ".pls", ".ipynb"}
    ddl = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
                     r"(?:PROCEDURE|FUNCTION|PACKAGE\s+BODY)\s+"
                     r"(?:[\w\"]+\s*\.\s*)?(?:\"?AE2E_COMMON\"?|\"?TRUNCATE_TABLE\"?|\"?GET_PRODUCT_KEY_ID\"?)\b", re.I)
    counts, candidates, parameter_files, unreadable, reference_candidates = Counter(), [], [], [], []
    files = sorted(p for p in root.rglob("*") if p.is_file() and not excluded.intersection(p.parts))
    for path in files:
        relative = path.relative_to(root).as_posix()
        if re.search(r"(?:reference|expected|oracle)[_-].*(?:output|result)|(?:output|result)[_-].*(?:reference|expected|oracle)", path.name, re.I):
            reference_candidates.append({"path": relative, "status": "unverified_not_an_independent_paired_run_receipt"})
        if re.fullmatch(r"p_ae2e(?:_prep)?\.txt(?:\..+)?", path.name, re.I):
            parameter_files.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest(),
                                    "status": "contents_not_loaded_or_approved"})
        if path.suffix.lower() not in extensions:
            continue
        counts[path.suffix.lower()] += 1
        found = False
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                tail = ""
                for line_number, line in enumerate(stream, 1):
                    # Avoid self-reporting the search regex or documentation of
                    # missing bodies as DDL. Store no matched source payload.
                    window = tail + line
                    if ddl.search(window) and not found:
                        candidates.append({"path": relative, "ending_line": line_number,
                                           "status": "DDL_candidate_requires_exact_body_review"})
                        found = True
                    tail = window[-4096:]
        except OSError:
            unreadable.append(relative)
    return {"scope": "entire_local_repo_excluding_installed_dependencies_and_caches",
            "file_count": len(files), "scanned_extensions": dict(counts),
            "parameter_files": parameter_files, "procedure_ddl_candidates": candidates,
            "reference_output_filename_candidates": reference_candidates,
            "unreadable_paths": unreadable, "local_search_complete": not unreadable,
            "remote_volume_metadata": remote_observation or {"status": "not_checked_by_local_search", "contents_read": False}}


def _control(snapshot, wid):
    nodes = {n["id"]: n for n in snapshot["nodes"]}
    tasks = [n for n in snapshot["nodes"] if n["kind"] == "taskinstance" and n.get("parent_id") == wid]
    links = [{**e["attributes"], "evidence_id": e["id"]} for e in snapshot["edges"]
             if e["kind"] == "workflow_control" and nodes[e["source"]].get("parent_id") == wid]
    for link in links:
        # Parse every predicate, including paths not selected in a preview.
        if link.get("CONDITION"):
            wil_control.link_matches(link["CONDITION"], {})
    return tasks, links


def compile_report(source_report, *, external_evidence=None):
    if source_report.get("package_sha256") != PACKAGE_SHA256 or source_report.get("selected_workflow_id") != PARENT_ID:
        raise ValueError("Only the exact reviewed WIL source package is accepted")
    snapshot = source_report["evidence_snapshot"]
    if _digest(_without_annotations(snapshot)) != UNIT_SHA256:
        raise ValueError("Canonical WIL evidence changed; source review/re-pin required")
    artifact_model = {"package": {"sha256": PACKAGE_SHA256}, **snapshot}
    dependency = compile_dependency_check(artifact_model)
    artifacts = dict(dependency["artifacts"])
    sessions = []
    for use in source_report["session_uses"]:
        if len(use["mapping_definition_ids"]) != 1 or len(use["session_definition_ids"]) != 1:
            raise ValueError("Ambiguous session definition")
        plan = MappingCompiler(snapshot, use["mapping_definition_ids"][0], use["session_definition_ids"][0]).compile()
        plan.update({"session_instance_id": use["instance_id"], "name": use["instance_name"],
                     "workflow_id": use["workflow_id"], "error_settings": use["error_settings"],
                     "instance_properties": use["instance_properties"],
                     "session_gate": "BLOCKED_EXTERNAL_PARAMETERS_BINDINGS_WRITER_AND_TRANSACTION_CONTRACT"})
        # Compile every nonempty scoped SQL phase separately, never substitute
        # it for the mapping query or run it early as a pre-load shortcut.
        plan["sql_phases"] = []
        for phase in source_report["sql_phase_contracts"]:
            if phase["owner_id"] not in use["session_scope_node_ids"] or not phase["text"]:
                continue
            item = dict(phase)
            item.update(status="evidence_only", ready=False)
            if phase["role"] != "Call Text":
                try:
                    # Token boundaries preserve quoted semicolons and original
                    # source spelling/parameters, unlike split(';') or AST
                    # round-tripping source defaults into another dialect.
                    tokens = sqlglot.Tokenizer().tokenize(phase["text"])
                    starts, parts = 0, []
                    for token in tokens:
                        if token.token_type == TokenType.SEMICOLON:
                            parts.append(phase["text"][starts:token.start])
                            starts = token.end + 1
                    parts.append(phase["text"][starts:])
                    item["statements"] = []
                    for ordinal, part in enumerate(p for p in parts if p.strip()):
                        statement = {"ordinal": ordinal, "source_text": part, "ready": False}
                        try:
                            statement["candidate"] = sql_candidate(part)
                            statement["status"] = "compiled_sql_phase_candidate"
                        except ValueError as error:
                            statement.update(status="blocked", blocker=str(error))
                        item["statements"].append(statement)
                    item["status"] = "compiled_sql_phase_candidate" if all(
                        p["status"] == "compiled_sql_phase_candidate" for p in item["statements"]) else "partially_compiled_sql_phase"
                    item["execution_order_gate"] = "Session pipeline target-load-order and transaction boundary not implemented"
                except (ValueError, ParseError):
                    item["blocker"] = "External call, sequence or unsupported SQL phase"
            plan["sql_phases"].append(item)
        sessions.append(plan)
        artifacts["sessions/" + use["instance_name"] + ".json"] = json.dumps(plan, indent=2, sort_keys=True) + "\n"
        for target in plan["target_candidates"]:
            artifacts["sql/" + use["instance_name"] + "__" + target["instance_id"][:12] + ".sql"] = (
                "-- SOURCE-GROUNDED CANDIDATE. NOT EXECUTED, NOT APPROVED, NOT PARITY.\n"
                "-- Whole-table binding tokens are unresolved. See corresponding session contract.\n"
                + target.get("insert_sql", target["select_sql"]) + ";\n")
    tasks, links = _control(snapshot, CHILD_ID)
    if len(tasks) != 15 or sum(t["attributes"]["TASKTYPE"] == "Session" for t in tasks) != 9:
        raise ValueError("Unexpected child task/session scope")
    child = {"workflow_id": CHILD_ID, "tasks": tasks, "links": links,
             "logical_routing": "exact_predicates_AND_OR_and_skip_candidate",
             "scheduler_timing": "not_equivalent_certified: ALL_DONE waits all predecessors of an OR merge",
             "ready": False, "execution_status": "not_run"}
    artifacts["child_control.json"] = json.dumps(child, indent=2, sort_keys=True) + "\n"
    artifacts["modules/wil_control.py"] = inspect.getsource(wil_control)
    artifacts["modules/wil_runtime_contract.py"] = inspect.getsource(wil_runtime_contract)
    required = {
        "version": VERSION, "package_sha256": PACKAGE_SHA256, "unit_sha256": UNIT_SHA256,
        "parameters": source_report["parameter_file_contracts"],
        "scoped_parameter_inventory": source_report["scoped_parameter_inventory"],
        "procedures": source_report["stored_procedures"],
        "table_bindings": [{"session_instance_id": s["session_instance_id"], "table_tokens": s["table_tokens"],
                            "target_instances": [{"instance_id": t["instance_id"], "definition": t["target"]}
                                                 for t in s["target_candidates"]]} for s in sessions],
        "approvals": "No fixture/table/name match is approval. Supply owner, reviewed scope/hash, types, freshness and explicit disposition.",
        "parameter_precedence": "Session-instance parameter filename is retained separately from session/workflow declarations. No contents/defaults guessed.",
        "transaction_choice": {"status": "owner_choice_required", "source_commit_interval": 10000,
            "source_rollback_on_errors": "NO", "choices": [
                "Preserve Oracle partition/key/procedure/commit and recovery effects through a separately reviewed execution design",
                "Explicitly approve a changed Delta atomicity/recovery contract with expected failure/concurrency outputs"],
            "default": None, "multi_table_delta_equivalence_claimed": False},
        "reference_outputs": source_report["required_output_evidence"],
        "external_evidence": external_evidence or {"status": "not_searched_by_this_pure_API"},
        "execution_allowed": False, "deployment_allowed": False,
    }
    artifacts["required_owner_inputs.json"] = json.dumps(required, indent=2, sort_keys=True) + "\n"
    # Serverless STANDARD candidate only. All source instances remain distinct;
    # ALL_DONE plus notebook eligibility prevents an OR merge becoming AND.
    # Earliest-trigger scheduler timing remains an explicit acceptance boundary.
    job_tasks = []
    for task in tasks:
        name = task["name"]
        use = next((s for s in sessions if s["session_instance_id"] == task["id"]), None)
        blockers = [b for o in use["operators"] for b in o["blockers"]] if use else []
        receipt = {"task": name, "type": task["attributes"]["TASKTYPE"], "execution_status": "not_run",
                   "ready": False, "blockers": blockers,
                   "required_contract": "See required_owner_inputs.json; no external contract has been approved"}
        # Execute structural eligibility only. Business stages fail closed with
        # an actual failed task, never successful stub output or zero row count.
        code = inspect.getsource(wil_control) + "\nimport json\n"
        code += "task = " + repr(task) + "\nlinks = " + repr(links) + "\nreceipt = " + repr(receipt) + "\n"
        code += '''outcomes = {}
for link in links:
    if link['TOTASK'] != task['name']:
        continue
    previous = link['FROMTASK']
    status = dbutils.jobs.taskValues.get(taskKey=previous, key='source_status', default='NOT_EXECUTED')
    count = dbutils.jobs.taskValues.get(taskKey=previous, key='target_success_rows', default=-1)
    outcomes[previous] = {'status': status, 'target_success_rows': count if type(count) is int and count >= 0 else None}
if not eligibility(task, links, outcomes):
    receipt['execution_status'] = 'SKIPPED'
    dbutils.jobs.taskValues.set(key='source_status', value='SKIPPED')
elif task['attributes']['TASKTYPE'] in {'Start', 'Decision'}:
    receipt['execution_status'] = 'STRUCTURAL_CONTROL_SUCCEEDED_NOT_BUSINESS_PARITY'
    dbutils.jobs.taskValues.set(key='source_status', value='SUCCEEDED')
else:
    dbutils.jobs.taskValues.set(key='source_status', value='FAILED')
    print(json.dumps(receipt, sort_keys=True))
    raise RuntimeError('Source business candidate blocked. See exact session and owner-input contracts.')
print(json.dumps(receipt, sort_keys=True))
'''
        notebook, source = _notebook(name, code, "WIL_NET_DIS full child, BLOCKED candidate")
        artifacts["notebooks/" + name + ".ipynb"] = notebook
        artifacts["notebooks/" + name + ".py"] = source
        job_tasks.append({"task_key": name, "run_if": "ALL_DONE",
            "depends_on": [{"task_key": link["FROMTASK"]} for link in links if link["TOTASK"] == name],
            "notebook_task": {"notebook_path": "../notebooks/" + name + ".py", "source": "WORKSPACE"},
            "max_retries": 0, "description": "BLOCKED source inspection candidate, not a converted business task"})
    job = {"name": "WIL_NET_DIS_CHILD_BLOCKED_REVIEW_CANDIDATE", "performance_target": "STANDARD",
           "tags": {"aidevkit_project": "ai-dev-kit", "conversion_status": "blocked_not_parity"},
           "max_concurrent_runs": 1, "tasks": job_tasks}
    artifacts["jobs/child.review.json"] = json.dumps(job, indent=2, sort_keys=True) + "\n"
    artifacts["jobs/child.review.yaml"] = yaml.safe_dump({"resources": {"jobs": {"wil_child_review": job}}}, sort_keys=False)
    counts = Counter(o["status"] for s in sessions for o in s["operators"] if o["type"] != "Source Definition")
    report = {"version": VERSION, "package_sha256": PACKAGE_SHA256, "unit_sha256": UNIT_SHA256,
        "parent_workflow_id": PARENT_ID, "child_workflow_id": CHILD_ID,
        "child_tasks": len(tasks), "child_sessions": 9, "session_uses": len(sessions), "unique_mappings": 7,
        "operator_use_counts_including_target_writers": dict(counts),
        "transformation_use_counts_excluding_source_and_target_definitions": dict(Counter(
            o["status"] for s in sessions for o in s["operators"]
            if o["type"] not in {"Source Definition", "Target Definition"})),
        "unique_transformation_definition_counts": dict(Counter({o["definition_id"]: o["status"]
            for s in sessions for o in s["operators"]
            if o["type"] not in {"Source Definition", "Target Definition"}}.values())),
        "individual_expression_port_counts": dict(Counter(p["status"] for s in sessions for o in s["operators"]
                                                            for p in o.get("port_candidates", []))),
        "compiled_insert_candidates": sum("insert_sql" in t for s in sessions for t in s["target_candidates"]),
        "mapping_coverage": [{"name": s["name"], "mapping_id": s["mapping_id"],
             "compiled_operator_uses": sum(o["status"] == "compiled_sql_candidate" for o in s["operators"]),
             "blocked_operator_uses": sum(o["status"] == "blocked" for o in s["operators"]),
             "target_sql_candidates": len(s["target_candidates"]), "fully_converted_session": False,
             "blockers": [{"instance_id": o["instance_id"], "name": o["name"], "reasons": o["blockers"]}
                          for o in s["operators"] if o["blockers"]]} for s in sessions],
        "dependency_core": dependency["contract"], "child_routing": child["logical_routing"],
        "child_scheduler_timing": child["scheduler_timing"],
        "gate": {"ready": False, "converted_sessions": 0, "full_package_conversion": False,
                 "output_parity": "not_established", "execution_allowed": False, "deployment_allowed": False},
        "external_evidence": required["external_evidence"],
        "limitations": ["Child Jobs file is a blocked inspection shape, not an executable converted workflow",
            "Session writer/file formatting, write ordering, rejects, recovery, email and async launch integration remain gated",
            "SP body, sequence allocation and physical bindings are not replaced from names",
            "Compiled SQL syntax and offline tests are not real Oracle/Spark output parity"],
        "model_calls": 0, "business_execution": "not_run", "canonical_mutations": 0}
    artifacts["implementation_report.json"] = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for name, content in artifacts.items():
        if name.endswith(".py"):
            ast.parse(content, filename=name)
        elif name.endswith((".json", ".ipynb")):
            json.loads(content)
    return {"report": report, "artifacts": artifacts, "sessions": sessions, "child_control": child}


def compile_parent_child(model, *, external_evidence=None):
    """Pure programmatic artifact API. Does not alter existing wrapper results."""
    original = build_parity_candidate(model)["report"]
    candidate = compile_report(original, external_evidence=external_evidence)
    parent = compile_workflow_artifacts(model, PARENT_ID)
    # Preserve the supported wrapper as a separate artifact subtree, not a new
    # blocking run_job_task that silently changes the exported -nowait launch.
    for key, content in parent["artifacts"].items():
        candidate["artifacts"]["parent_wrapper/" + key] = content
    return candidate


def export_candidate(candidate, destination):
    destination = Path(destination)
    for name in candidate["artifacts"]:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name:
            raise ValueError("Unsafe artifact path")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"version": VERSION, "package_sha256": PACKAGE_SHA256, "unit_sha256": UNIT_SHA256,
                "ready": False, "output_parity": "not_established", "artifacts": {}}
    for name, content in sorted(candidate["artifacts"].items()):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        with path.open("xb") as stream:
            stream.write(data)
        manifest["artifacts"][name] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    for name, expected in manifest["artifacts"].items():
        data = (destination / name).read_bytes()
        if len(data) != expected["bytes"] or sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError("Export readback mismatch")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--search-root", type=Path, required=True)
    args = parser.parse_args()
    from workbench.understanding import analyze_package
    candidate = compile_parent_child(analyze_package(args.source),
                                     external_evidence=discover_external_evidence(args.search_root))
    manifest = export_candidate(candidate, args.destination)
    print(json.dumps({"artifact_count": len(manifest["artifacts"]), "destination": str(args.destination),
                      "ready": False, "business_execution": "not_run", "deployed": False}))