"""Read-only folder understanding and conservative, nonexecuting candidate files.

No agents, inference, Spark, deployment, durable writes or guessed CDC contracts.
Saved XML is hash verified and streamed into a disposable folder-only canonical
model. Original ancestor attributes and folder ordinals preserve definition IDs.
Cross-folder references not in that model remain explicit unresolved blockers.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import iterparse
from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict

from workbench.source_evidence import workflow_invocations
from workbench.understanding import analyze_package


FORMAT = "folder-migration-candidate-v1"
NOTICE = ("Candidate layer plan and syntax fragments, not a complete runtime or Genie-equivalent "
          "conversion. No inference, execution, deployment, source data reads or cloud writes. "
          "XML counts describe definitions and occurrences, never observed data row counts.")
LAYERS = [
    {"id": "capture", "name": "Source capture", "status": "proposed",
     "purpose": "Retain exact source projections and acquisition predicates; ingestion bindings are unresolved."},
    {"id": "stage", "name": "Temporary transformation views", "status": "candidate_partial",
     "purpose": "Preserve source SQL, MD5 expressions, filters, joins and connector contracts without inventing target writes."},
    {"id": "history", "name": "History / change application", "status": "blocked",
     "purpose": "Choose AUTO CDC or snapshot comparison only after business keys, order and snapshot completeness are verified."},
    {"id": "publish", "name": "Target publication", "status": "blocked",
     "purpose": "Reconcile target write modes, pre/post SQL, procedures and workflow calls before any publication."},
]


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def _digest(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _folder(index, folder_id):
    folder = index.nodes.get(folder_id)
    if not folder or folder["kind"] != "folder":
        raise HTTPException(404, "Unknown canonical folder ID in selected package")
    return folder


def folder_list(index, offset=0, limit=50):
    """List saved identities only, no XML reparse and no service mutation."""
    counts = defaultdict(Counter)
    for node in index.model["nodes"]:
        counts[node.get("scope", {}).get("folder_id")][node["kind"]] += 1
    rows = []
    for folder in index.kinds.get("folder", []):
        row = {key: deepcopy(folder.get(key)) for key in ("id", "name", "scope", "evidence")}
        row.update(folder_id=folder["id"], counts=dict(counts[folder["id"]]))
        rows.append(row)
    rows.sort(key=lambda row: (row["scope"].get("repository", ""), row["name"], row["id"]))
    return {"schema_version": FORMAT, "package_hash": index.model["package"]["sha256"],
            "items": rows[offset:offset + limit], "total": len(rows), "offset": offset,
            "limit": limit, "has_more": offset + limit < len(rows), "notice": NOTICE,
            "generation_allowed": False, "deployment_allowed": False}


def _source_model(index, folder_id):
    """Stream only the selected folder, retaining enclosing XML identity ordinals.

    The temporary file is not an ingestion, saved overlay or canonical rewrite.
    All security/redaction/reference behavior belongs to the existing analyzer.
    """
    folder = _folder(index, folder_id)
    source = index.directory / "source.xml"
    expected = index.model["package"]["sha256"]
    if not source.is_file() or source.is_symlink():
        raise HTTPException(409, "Verified saved source.xml is required for folder analysis")
    selected_locator = folder["evidence"]["locator"]
    stack = []
    root = None
    active = False
    matched = 0
    with source.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != expected:
            raise HTTPException(409, "Saved source checksum mismatch")
        stream.seek(0)
        for event, elem in iterparse(stream, events=("start", "end"),
                                     forbid_dtd=False, forbid_entities=True, forbid_external=True):
            tag = elem.tag.rsplit("}", 1)[-1]
            if event == "start":
                ordinal = 1
                if stack:
                    stack[-1][2][tag] += 1
                    ordinal = stack[-1][2][tag]
                locator = (stack[-1][1] if stack else "") + f"/{tag}[{ordinal}]"
                stack.append((elem, locator, Counter()))
                if root is None:
                    root = elem
                if tag == "FOLDER":
                    active = locator == selected_locator
                    matched += int(active)
            else:
                # Retain wrappers for ordinals, not other folders' full contents.
                if not active and tag not in {"POWERMART", "REPOSITORY", "FOLDER"}:
                    elem.clear()
                    if len(stack) > 1:
                        stack[-2][0].remove(elem)
                stack.pop()
                if tag == "FOLDER":
                    active = False
        stream.seek(0)
        if file_digest(stream, "sha256").hexdigest() != expected or _digest(source) != expected:
            raise HTTPException(409, "Saved source changed during folder analysis")
    if matched != 1 or root is None:
        raise HTTPException(409, "Saved canonical folder locator no longer matches XML")
    with TemporaryDirectory(prefix="orion-folder-migration-") as temporary:
        path = Path(temporary) / "scope.xml"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        model = analyze_package(path)
    by_id = {node["id"]: node for node in model["nodes"]}
    if folder_id not in by_id or by_id[folder_id]["evidence"] != folder["evidence"]:
        raise HTTPException(409, "Current analyzer differs from saved canonical folder identity; no automatic migration")
    model["package"] = deepcopy(index.model["package"])
    # Strip empty ordinal wrappers outside the selected folder from the compiler.
    selected = {n["id"] for n in model["nodes"] if n.get("scope", {}).get("folder_id") == folder_id}
    model["nodes"] = [n for n in model["nodes"] if n["id"] in selected]
    model["edges"] = [e for e in model["edges"] if e["source"] in selected and e["target"] in selected]
    model["diagnostics"] = [d for d in model.get("diagnostics", []) if d.get("object_id") in selected]
    model["counts"] = dict(Counter(n["kind"] for n in model["nodes"]))
    model["coverage"] = {"scope": "temporary_folder_only", "cross_folder_resolution": "not_expanded"}
    return model


def _compile(model, folder_id):
    """Existing SQL compiler, with an explicitly synthetic inventory-only root.

    This anchor collects ALL definitions, including mappings unused by workflows.
    It is not a new workflow, dependency edge, execution plan or saved model edit.
    """
    from workbench.sql_artifacts import compile_sql_artifacts
    anchor_id = sha256((FORMAT + ":inventory:" + folder_id).encode()).hexdigest()
    anchor = {"id": anchor_id, "kind": "workflow", "name": "Folder inventory anchor (not executable)",
              "parent_id": None, "scope": {}, "attributes": {}, "properties": [], "fields": [],
              "evidence": {"locator": "synthetic:folder_inventory_anchor"}}
    edge = {"id": sha256((anchor_id + folder_id).encode()).hexdigest(), "kind": "contains",
            "source": anchor_id, "target": folder_id, "attributes": {}, "evidence": anchor["evidence"]}
    transient = dict(model, nodes=[anchor, *model["nodes"]], edges=[edge, *model["edges"]])
    return compile_sql_artifacts(transient, anchor_id)


def _evidence(node):
    return {"node_id": node["id"], "locator": node.get("evidence", {}).get("locator", "")}


def _records(model):
    """All nonempty behavioral settings, not just conveniently convertible SQL."""
    rows = []
    for node in model["nodes"]:
        for collection in ("fields", "properties"):
            for ordinal, record in enumerate(node.get(collection, [])):
                attrs = record.get("attributes", {})
                if collection == "fields" and not attrs.get("EXPRESSION"):
                    continue
                if collection == "properties" and not (attrs.get("VALUE") or record.get("text")):
                    continue
                rows.append({**_evidence(node), "owner_name": node["name"],
                             "owner_kind": node["kind"], "collection": collection, "ordinal": ordinal,
                             "record": deepcopy(record)})
    return rows


def _mapping_members(model):
    nodes = {n["id"]: n for n in model["nodes"]}
    members = defaultdict(list)
    for node in model["nodes"]:
        parent = node
        seen = set()
        while parent and parent["id"] not in seen:
            seen.add(parent["id"])
            if parent["kind"] == "mapping":
                members[parent["id"]].append(node)
                break
            parent = nodes.get(parent.get("parent_id"))
    return nodes, members


def _table_rows(model, compilation):
    nodes, members = _mapping_members(model)
    mapping_by_member = {n["id"]: mid for mid, group in members.items() for n in group}
    mappings_by_source = defaultdict(set)
    instances_by_source = defaultdict(set)
    outgoing = defaultdict(list)
    for edge in model["edges"]:
        outgoing[edge["source"]].append(edge)
    for edge in model["edges"]:
        if edge["kind"] in {"instance_of", "shortcut_reference"} and nodes[edge["target"]]["kind"] == "source":
            mid = mapping_by_member.get(edge["source"])
            if mid:
                mappings_by_source[edge["target"]].add(mid)
                instances_by_source[edge["target"]].add(edge["source"])
    rows = []
    for source in sorted((n for n in model["nodes"] if n["kind"] == "source"), key=lambda n: (n["name"], n["id"])):
        mids = sorted(mappings_by_source[source["id"]])
        qualifier_ids = set()
        for instance_id in instances_by_source[source["id"]]:
            for edge in outgoing[instance_id]:
                if edge["kind"] != "data_flow":
                    continue
                for definition in outgoing[edge["target"]]:
                    if definition["kind"] == "instance_of":
                        target = nodes[definition["target"]]
                        if target.get("attributes", {}).get("TYPE") == "Source Qualifier":
                            qualifier_ids.add(target["id"])
        sql = [item for item in compilation.sql if item.node_id in qualifier_ids
               and item.role.value == "source_query" and item.original_sql.strip()]
        # Literal acquisition predicates are structural, never runtime proof.
        incremental = [s for s in sql if re.search(r"\bINCREMENTAL\b", s.original_sql, re.I)]
        initial = [s for s in sql if re.search(r"\bINITIAL\b|\bSNAPSHOT\b", s.original_sql, re.I)]
        keys = [f["attributes"]["NAME"] for f in source.get("fields", [])
                if "PRIMARY" in f.get("attributes", {}).get("KEYTYPE", "").upper()]
        attrs = source.get("attributes", {})
        rows.append({"id": source["id"], "name": source["name"], "database_type": attrs.get("DATABASETYPE"),
                     "database": attrs.get("DBDNAME"), "owner": attrs.get("OWNERNAME"),
                     "sap_metadata_hint": "SAP" in (attrs.get("DBDNAME", "") + attrs.get("OWNERNAME", "")).upper(),
                     "field_count": len(source.get("fields", [])), "mapping_ids": mids,
                     "mapping_names": [nodes[mid]["name"] for mid in mids],
                     "acquisition_mode": "initial_and_incremental_predicates" if incremental and initial else
                         "incremental_predicate" if incremental else "initial_or_snapshot_predicate" if initial else "unknown",
                     "mode_evidence_scope": "direct_connector_source_qualifier_query_not_runtime_proof",
                     "source_qualifier_ids": sorted(qualifier_ids),
                     "incremental_query_count": len(incremental), "initial_or_snapshot_query_count": len(initial),
                     "sql_ids": [s.id for s in sql], "row_count": None, "row_count_status": "not_observed",
                     "declared_source_keys": keys, "verified_cdc_keys": None, "verified_sequence_by": None,
                     "snapshot_completeness": "unknown", "recommended_change_application": "unknown",
                     "proposed_options": ["AUTO CDC after verified keys, change feed and sequence",
                                          "AUTO CDC FROM SNAPSHOT after verified keys and ordered complete snapshots"],
                     "evidence": source.get("evidence", {})})
    return rows


def _expression_candidate(node):
    """Translate only a small literal expression grammar, never the whole mapping.

    Input and variable ports must be supplied by an independently verified upstream
    view. Informatica local variables cannot be evaluated as sibling SQL aliases.
    """
    result = {**_evidence(node), "name": node["name"], "status": "blocked", "sql": None,
              "required_input_columns": [], "blockers": [], "translation_claim": "syntax_fragment_only"}
    fields = [f.get("attributes", {}) for f in node.get("fields", [])]
    if any("VARIABLE" in f.get("PORTTYPE", "").upper() for f in fields):
        result["blockers"].append("INFORMATICA_VARIABLE_PORT_ORDER_REQUIRES_IMPLEMENTATION")
        return result
    output = [f for f in fields if "OUTPUT" in f.get("PORTTYPE", "").upper()]
    if not output:
        result["blockers"].append("NO_OUTPUT_PORTS")
        return result
    try:
        import sqlglot
        from sqlglot import exp
        rendered = []
        allowed = {"MD5", "LTRIM", "RTRIM", "CHR"}
        for field in output:
            expression = field.get("EXPRESSION") or field["NAME"]
            # Narrow language overlap, no guessed IIF/DECODE/date/procedure behavior.
            if "$$" in expression or "[REDACTED]" in expression:
                raise ValueError("UNRESOLVED_EXPRESSION_PARAMETER")
            calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", expression))
            if not {c.upper() for c in calls} <= allowed or re.search(r"[;\n]|--|/\*", expression):
                raise ValueError("UNSUPPORTED_INFORMATICA_EXPRESSION")
            tree = sqlglot.parse_one(expression, read="spark", into=exp.Condition,
                                     error_level=sqlglot.ErrorLevel.RAISE)
            if any(isinstance(p, (exp.Query, exp.Command, exp.Subquery, exp.Star)) for p in tree.walk()):
                raise ValueError("UNSUPPORTED_INFORMATICA_EXPRESSION")
            # Reject unknown UDFs even if the lexer missed quoted syntax.
            if any(isinstance(p, exp.Anonymous) for p in tree.walk()):
                raise ValueError("UNSUPPORTED_INFORMATICA_EXPRESSION")
            result["required_input_columns"].extend(c.name for c in tree.find_all(exp.Column))
            rendered.append(tree.sql(dialect="spark", unsupported_level=sqlglot.ErrorLevel.RAISE)
                            + " AS " + exp.to_identifier(field["NAME"], quoted=True).sql(dialect="spark"))
        result.update(status="candidate_syntax_only", sql="SELECT\n  " + ",\n  ".join(rendered)
                      + "\nFROM __REQUIRED_UPSTREAM_VIEW__", required_input_columns=sorted(set(result["required_input_columns"])))
        result["blockers"] = ["UPSTREAM_PORT_BINDING_REQUIRED", "INFORMATICA_NULL_TYPE_AND_HASH_PARITY_UNVERIFIED"]
    except ImportError:
        result["blockers"].append("SQLGLOT_DEPENDENCY_MISSING")
    except Exception as error:
        code = str(error)
        result["blockers"].append(code if re.fullmatch(r"[A-Z_]+", code) else "EXPRESSION_TRANSLATION_BLOCKED")
    return result


def _build(index, folder_id):
    model = _source_model(index, folder_id)
    folder = _folder(index, folder_id)
    compilation = _compile(model, folder_id)
    records = _records(model)
    patterns = []
    for row in records:
        attrs = row["record"].get("attributes", {})
        name, value = attrs.get("NAME", ""), attrs.get("EXPRESSION", attrs.get("VALUE", row["record"].get("text", "")))
        categories = []
        if re.search(r"\bMD5\s*\(", value, re.I):
            categories.append("md5_expression")
        if "join" in name.lower():
            categories.append("join_setting")
        if "filter" in name.lower():
            categories.append("filter_setting")
        if re.search(r"truncate|insert|update|delete|source rows", name, re.I):
            categories.append("write_behavior_setting")
        if re.search(r"pre.*sql|post.*sql", name, re.I):
            categories.append("pre_post_sql")
        for category in categories:
            patterns.append({"category": category, "name": name, "value": value,
                             "node_id": row["node_id"], "evidence": row["record"].get("evidence", {})})
    counts = Counter(n["kind"] for n in model["nodes"])
    transforms = Counter(n.get("attributes", {}).get("TYPE", "unknown") for n in model["nodes"] if n["kind"] == "transformation")
    tables = _table_rows(model, compilation)
    invocations = workflow_invocations(model)
    expressions = [_expression_candidate(n) for n in model["nodes"]
                   if n["kind"] == "transformation" and n.get("attributes", {}).get("TYPE") == "Expression"]
    sql_inventory = [asdict(item) for item in compilation.sql]
    blockers = deepcopy(compilation.manifest["blockers"])
    blockers += [
        {"code": "CDC_CONTRACT_UNVERIFIED", "node_id": folder_id,
         "detail": "No verified business keys, event sequence or complete ordered snapshot contract. MD5 is not a business key or sequence."},
        {"code": "TARGET_AND_ORCHESTRATION_NOT_IMPLEMENTED", "node_id": folder_id,
         "detail": "All writer settings, pre/post SQL, procedure and child calls must be reconciled, not dropped."},
    ]
    for call in invocations:
        blockers.append({"code": "CHILD_WORKFLOW_NOT_CONVERTED", "node_id": call["command_owner_id"],
                         "detail": call.get("resolution_reason"), "evidence": call.get("evidence")})
    report = {
        "schema_version": FORMAT, "package_hash": model["package"]["sha256"], "folder_id": folder_id,
        "folder": {k: deepcopy(folder.get(k)) for k in ("id", "name", "scope", "evidence")},
        "status": "proposed_layers_with_partial_candidates", "notice": NOTICE,
        "business_purpose": {"summary": f"Folder {folder['name']} contains {counts['mapping']} mapping definitions and {counts['workflow']} workflow definitions connecting the listed source and target metadata.",
                             "status": "structural_summary_not_inferred_business_validation",
                             "captured_description": folder.get("attributes", {}).get("DESCRIPTION", ""),
                             "evidence_ids": [folder_id], "domain_intent": "unverified"},
        "counts": dict(counts), "transformation_types": dict(transforms), "tables": tables,
        "table_summary": {"source_definitions": len(tables), "sap_odbc_source_definitions": sum(
            t["database_type"] == "ODBC" and t["sap_metadata_hint"] for t in tables),
            "incremental_predicate_rows": sum(t["incremental_query_count"] > 0 for t in tables),
            "initial_or_snapshot_predicate_rows": sum(t["initial_or_snapshot_query_count"] > 0 for t in tables),
            "observed_data_row_count": None},
        "layers": deepcopy(LAYERS), "patterns": patterns, "sql_inventory": sql_inventory,
        "expression_candidates": expressions, "workflow_invocations": invocations,
        "workflows": [{"id": n["id"], "name": n["name"], "evidence": n["evidence"]} for n in model["nodes"] if n["kind"] == "workflow"],
        "blockers": blockers,
        "evidence_flags": {"saved_xml_checksum_verified": True, "folder_identity_verified": True,
                           "canonical_mutated": False, "source_data_observed": False,
                           "agents_invoked": False, "external_inference": False,
                           "semantic_parity_verified": False, "genie_equivalent": False,
                           "cross_folder_definitions_expanded": False},
        "generation_allowed": False, "candidate_generation_available": True,
        "execution_permitted": False, "deployment_allowed": False,
    }
    report["input_version"] = sha256(_json({"format": FORMAT, "package": model["package"],
        "folder": folder_id, "nodes": model["nodes"], "edges": model["edges"],
        "module_hashes": {name: _digest(Path(__file__).with_name(name)) for name in
                          ("folder_migration.py", "understanding.py", "source_evidence.py", "sql_artifacts.py")}}).encode()).hexdigest()
    return report, model, records, compilation


def folder_plan(index, folder_id):
    return _build(index, folder_id)[0]


def _comment_sql(text):
    # Raw SQL and source settings are never runnable fallback programs.
    return "\n".join("-- " + line for line in text.splitlines()) + "\n"


def generate_artifacts(index, folder_id):
    report, model, records, compilation = _build(index, folder_id)
    files = {
        "plan.json": _json(report),
        "evidence/folder_graph.json": _json(model),
        "evidence/behavior_records.json": _json(records),
        "evidence/workflow_invocations.json": _json(report["workflow_invocations"]),
        "evidence/sql_inventory.json": _json(report["sql_inventory"]),
        "evidence/sql_compiler_manifest.json": _json(compilation.manifest),
        "README.md": "# Folder migration candidates\n\n" + NOTICE + "\n\n"
            "Source capture, temporary stage views, history and publication are proposed layers, not a verified Genie plan. "
            "No AUTO CDC flow is emitted: XML key declarations alone do not verify business keys, sequencing or snapshot completeness. "
            "The full folder graph and behavior records retain all fields, connectors, writer settings, pre/post SQL, scripts and child calls. "
            "Cross-folder definitions are not expanded. Captured SQL is commented evidence, never runnable fallback. "
            "Translated SQL uses required identifier markers and is syntax-only. No SELECT-star surrogate is generated. "
            "Expression fragments preserve explicit output expressions but require upstream binding and null/type/hash parity tests. "
            "No targets, schemas, jobs or pipelines are created. No agents were invoked.\n",
    }
    queries = {}
    for item in compilation.sql:
        if not item.original_sql.strip():
            continue
        stem = f"{item.role.value}_{item.id}"
        files["captured_sql/" + stem + ".sql"] = _comment_sql(
            "CAPTURED SOURCE EVIDENCE ONLY; " + item.status + "\n" + item.original_sql)
        if item.statements:
            files["translated_sql/" + stem + ".sql"] = (
                "-- CANDIDATE syntax only. Required identifier bindings. Not automatic execution.\n"
                + ";\n".join(s["spark_sql_template"] for s in item.statements) + ";\n")
            if item.role.value in {"source_query", "lookup_query", "transformation_sql"}:
                queries[item.id] = {"sql": item.statements[0]["spark_sql_template"],
                                    "bindings": item.bindings, "required_columns": None}
    for candidate in report["expression_candidates"]:
        files["stage/" + candidate["node_id"] + ".json"] = _json(candidate)
        if candidate["sql"]:
            files["stage/" + candidate["node_id"] + ".sql"] = (
                "-- CANDIDATE expression projection only. Upstream ports and parity unverified.\n"
                + candidate["sql"] + ";\n")
            queries[candidate["node_id"]] = {"sql": candidate["sql"], "bindings": {
                "upstream": {"marker": "__REQUIRED_UPSTREAM_VIEW__"}},
                "required_columns": candidate["required_input_columns"]}
    files["layers/capture.sql"] = _comment_sql(_json({"layer": report["layers"][0],
        "source_query_ids": [s.id for s in compilation.sql if s.role.value == "source_query"],
        "tables": report["tables"]}))
    files["layers/history.sql"] = _comment_sql(
        "BLOCKED: proposed AUTO CDC / AUTO CDC FROM SNAPSHOT only.\n"
        "Required: verified business keys, change ordering or ordered full snapshots, deletion/null-key contract.\n"
        "MD5 change detection does not prove SCD2, snapshot completeness, business keys or event order.")
    files["layers/publish.sql"] = _comment_sql(_json({"layer": report["layers"][3],
        "write_behavior": [p for p in report["patterns"] if p["category"] in {"write_behavior_setting", "pre_post_sql"}],
        "workflow_invocations": report["workflow_invocations"], "blockers": report["blockers"]}))
    files["layers/stage.py"] = '''"""Candidate temporary views only. Not a complete Lakeflow pipeline.
No import-time Spark execution, target writes, CDC flows or inferred ingestion.
Caller supplies Spark, all table bindings, reviewed input schemas and consent.
"""
import re

QUERIES = ''' + repr(queries) + '''

def candidate_frame(spark, query_id, *, bindings, consent=False):
    if consent is not True:
        raise ValueError("Explicit candidate execution consent is required")
    candidate = QUERIES[query_id]
    if set(bindings) != set(candidate["bindings"]):
        raise ValueError("Exact explicit table/input-view bindings required")
    sql = candidate["sql"]
    for key, contract in candidate["bindings"].items():
        value = bindings[key]
        parts = value.split(".") if isinstance(value, str) else []
        expected = 1 if key == "upstream" else 3
        if len(parts) != expected or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
            raise ValueError("Required simple upstream view or three-part table identifier")
        if key == "upstream":
            missing = set(candidate["required_columns"]) - set(spark.table(value).columns)
            if missing:
                raise ValueError("Upstream port binding incomplete")
        sql = sql.replace(contract["marker"], ".".join("`" + p + "`" for p in parts))
    return spark.sql(sql)

def register_candidate_view(spark, query_id, *, bindings, view_name, consent=False):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", view_name):
        raise ValueError("Simple temporary view identifier required")
    frame = candidate_frame(spark, query_id, bindings=bindings, consent=consent)
    frame.createOrReplaceTempView(view_name)
    return frame

def register_lakeflow_candidate(dp, spark, query_id, *, bindings, view_name, consent=False):
    """Opt-in SDP temporary view scaffold, not CDC/history or a complete pipeline.

    Review runtime compatibility independently. The caller supplies the installed
    pyspark.pipelines module. No pipeline is created or invoked by this file.
    """
    if consent is not True:
        raise ValueError("Explicit candidate registration consent is required")
    # Validate registration without calling Spark or starting a pipeline.
    if query_id not in QUERIES or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", view_name):
        raise ValueError("Known query and simple view identifier required")
    @dp.temporary_view(name=view_name)
    def candidate_view():
        return candidate_frame(spark, query_id, bindings=bindings, consent=True)
    return candidate_view
'''
    manifest = {"schema_version": FORMAT, "folder_id": folder_id, "package_hash": report["package_hash"],
                "input_version": report["input_version"], "status": "candidate_partial_not_runtime_complete",
                "translation_claim": "syntax_only_not_semantic_parity", "genie_equivalent": False,
                "execution_permitted": False, "deployment_allowed": False, "release_ready": False,
                "synthetic_inventory_anchor": compilation.workflow_id,
                "synthetic_anchor_is_executable_workflow": False,
                "sql_inventory": report["sql_inventory"], "blockers": report["blockers"],
                "artifacts": [{"path": name, "sha256": sha256(content.encode()).hexdigest(),
                               "bytes": len(content.encode())} for name, content in sorted(files.items())]}
    files["manifest.json"] = _json(manifest)
    return {"schema_version": FORMAT, "folder_id": folder_id, "package_hash": report["package_hash"],
            "input_version": report["input_version"], "status": manifest["status"], "manifest": manifest,
            "artifacts": [{"path": name, "content": content, "sha256": sha256(content.encode()).hexdigest(),
                           "language": "python" if name.endswith(".py") else "sql" if name.endswith(".sql") else "json" if name.endswith(".json") else "markdown"}
                          for name, content in sorted(files.items())],
            "notice": NOTICE, "persisted": False, "execution_permitted": False, "deployment_allowed": False}


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consent: bool


def install_folder_migration(app, service):
    @app.get("/api/folder-migration/folders")
    def folders(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)):
        return folder_list(service.require(), offset, limit)

    @app.get("/api/folder-migration/folders/{folder_id}")
    def plan(folder_id: str):
        return folder_plan(service.require(), folder_id)

    @app.post("/api/folder-migration/folders/{folder_id}/artifacts")
    def artifacts(folder_id: str, body: GenerateBody):
        if body.consent is not True:
            raise HTTPException(403, "Explicit candidate artifact generation consent:true is required")
        return generate_artifacts(service.require(), folder_id)