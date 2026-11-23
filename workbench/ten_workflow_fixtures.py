"""Create isolated empty XML target fixtures, never business bindings or data.

This separate CLI does not alter campaign approval, application state or jobs.
Each planned target is mirrored in each catalog for structural testing only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from databricks.sdk.errors import NotFound

from workbench import delta_state as ds
from workbench.databricks_models import profile_identity
from workbench.package_context import PackageContext
from workbench.target_fixtures import _checked, target_sql, target_table
from workbench.understanding import analyze_package
from workbench.validation_contracts import LEDGER_PRIORITY_IDS, SourceContracts

PACKAGE_HASH = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
WAREHOUSE = "e3a0fc2c08db05bb"
NAMES = {layer: f"orion_{layer}_test_ssuman" for layer in ("bronze", "silver", "gold")}
COMMENT = "Empty test fixtures XML-defined not production approved"


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_plan(xml, identity):
    if file_hash(xml) != PACKAGE_HASH:
        raise ValueError("XML package fingerprint mismatch")
    model = analyze_package(xml)
    if model["package"]["sha256"] != PACKAGE_HASH or file_hash(xml) != PACKAGE_HASH:
        raise ValueError("XML changed during analysis")
    context = PackageContext(model)
    contracts = SourceContracts(context)
    selected, all_ids = [], set()
    for wid in LEDGER_PRIORITY_IDS:
        node = context.nodes.get(wid)
        if not node or node["kind"] != "workflow":
            raise ValueError("Required workflow missing")
        local = context.local(wid)
        contract = contracts.workflow(wid)
        if contract["classification"] != "business_definition_candidate":
            raise ValueError("Required workflow no longer qualifies")
        unresolved = [i for i in local if context.nodes[i]["kind"] == "unresolved_reference"]
        if unresolved:
            raise ValueError("Unresolved workflow definition references")
        all_ids.update(local)
        selected.append({"workflow_id": wid, "name": node["name"],
            "scope": node["scope"], "task_sessions": contract["tasks"],
            "mappings": contract["mappings"],
            "source_ids": sorted(i for i in local if context.nodes[i]["kind"] == "source"),
            "target_ids": sorted(i for i in local if context.nodes[i]["kind"] == "target")})
    targets, sources = [], []
    for node in (context.nodes[i] for i in sorted(all_ids)):
        if node["kind"] == "target":
            fields = [f["attributes"] for f in node.get("fields", []) if f["kind"] == "targetfield"]
            row = target_table(node["attributes"], fields, node["scope"])
            row.update(target_id=node["id"], evidence=node.get("evidence"), xml_fields=fields)
            targets.append(row)
        elif node["kind"] == "source":
            fields = [f["attributes"] for f in node.get("fields", []) if f["kind"] == "sourcefield"]
            if not fields or any(not f.get("NAME") or not f.get("DATATYPE") for f in fields):
                raise ValueError("Source definition lacks field metadata")
            sources.append({"source_id": node["id"], "scope": node["scope"],
                "attributes": node["attributes"], "fields": fields, "evidence": node.get("evidence")})
    _checked(targets)
    if not sources or not targets:
        raise ValueError("No source or target definitions")
    plan = {"format": "ten-workflow-empty-xml-fixtures-v1", "package_sha256": PACKAGE_HASH,
        "workspace_id": identity.host, "user_id": identity.user_id, "warehouse_id": WAREHOUSE,
        "catalogs": NAMES, "description": COMMENT, "workflows": selected, "sources": sources,
        "targets": targets, "schema_count_per_catalog": len({t["schema"] for t in targets}),
        "target_count_per_catalog": len(targets), "column_count_per_catalog": sum(len(t["columns"]) for t in targets),
        "source_binding_approved": False, "destination_binding_approved": False,
        "three_layer_placement_approved": False, "data_loaded": False,
        "meaning": "Identical empty target-schema fixtures in each catalog, not an actual medallion mapping",
        "limitations": ["XML DBDNAME and OWNERNAME may be empty; physical namespace is unverified.",
            "String width, collation, keys, file delivery, Oracle DATE semantics and business behavior require review.",
            "Source definitions are inventory only; no source tables are created or changed."]}
    plan["plan_sha256"] = ds._hash(plan)
    return plan


def verify_plan(plan, expected_hash, identity, xml):
    if (plan.get("plan_sha256") != expected_hash
            or ds._hash({k: v for k, v in plan.items() if k != "plan_sha256"}) != expected_hash
            or plan.get("workspace_id") != identity.host or plan.get("user_id") != identity.user_id
            or plan.get("package_sha256") != PACKAGE_HASH or file_hash(xml) != PACKAGE_HASH
            or plan.get("warehouse_id") != WAREHOUSE or plan.get("catalogs") != NAMES
            or [w["workflow_id"] for w in plan.get("workflows", [])] != list(LEDGER_PRIORITY_IDS)):
        raise ValueError("Plan, identity or input fingerprint mismatch")
    targets = _checked(plan["targets"])
    if (len(targets) != plan["target_count_per_catalog"]
            or len({t["schema"] for t in targets}) != plan["schema_count_per_catalog"]
            or sum(len(t["columns"]) for t in targets) != plan["column_count_per_catalog"]):
        raise ValueError("Plan inventory mismatch")
    for table in targets:
        target_sql(NAMES["bronze"], table, PACKAGE_HASH)


def event(stream, phase, kind, name, **details):
    row = {"time": datetime.now(timezone.utc).isoformat(), "phase": phase,
           "kind": kind, "name": name, **details}
    stream.write(json.dumps(row, sort_keys=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
    print(json.dumps(row, sort_keys=True), flush=True)


def _value(value):
    return getattr(value, "value", value)


def verify_table(info, full_name, table, username):
    actual = [(c.name.casefold(), c.type_text.upper().replace(" ", ""), c.nullable) for c in info.columns or []]
    expected = [(c["name"].casefold(), c["type"], c["nullable"]) for c in table["columns"]]
    if (info.full_name.casefold() != full_name.casefold() or info.owner != username
            or _value(info.table_type) != "MANAGED" or _value(info.data_source_format) != "DELTA"
            or actual != expected or (info.properties or {}).get("orion.package_sha256") != PACKAGE_HASH
            or (info.properties or {}).get("orion.fixture") != "xml_target_schema_only"):
        raise ValueError("Managed Delta table metadata mismatch")


def provision(plan, identity, stream):
    client = identity.client
    me = client.current_user.me()
    if me.id != identity.user_id or not me.user_name:
        raise PermissionError("Authenticated user changed")
    username = me.user_name
    # Check every name before the first CREATE. A 403 is not treated as absence.
    for name in NAMES.values():
        try:
            client.catalogs.get(name)
        except NotFound:
            pass
        else:
            raise ValueError("A planned catalog already exists")
    event(stream, "started", "plan", plan["plan_sha256"], user=username)
    current_kind, current_name = "plan", plan["plan_sha256"]
    store = ds.DeltaStateStore(client, WAREHOUSE, "unused", "unused", identity.user_id)
    try:
        for name in NAMES.values():
            current_kind, current_name = "catalog", name
            event(stream, "create_requested", current_kind, name)
            client.catalogs.create(name=name, comment=COMMENT)
            event(stream, "create_acknowledged", current_kind, name)
            info = client.catalogs.get(name)
            if info.name != name or info.owner != username:
                raise ValueError("Catalog ownership mismatch")
            event(stream, "verified", current_kind, name)
        for name in NAMES.values():
            for schema in sorted({t["schema"] for t in plan["targets"]}):
                current_kind, current_name = "schema", name + "." + schema
                event(stream, "create_requested", current_kind, current_name)
                client.schemas.create(name=schema, catalog_name=name, comment=COMMENT)
                event(stream, "create_acknowledged", current_kind, current_name)
                info = client.schemas.get(current_name)
                if info.full_name != current_name or info.owner != username:
                    raise ValueError("Schema ownership mismatch")
                event(stream, "verified", current_kind, current_name)
        for name in NAMES.values():
            for table in plan["targets"]:
                current_kind, current_name = "table", ".".join((name, table["schema"], table["table"]))
                event(stream, "create_requested", current_kind, current_name)
                sql = target_sql(name, table, PACKAGE_HASH).replace(
                    " TBLPROPERTIES ", " COMMENT '" + COMMENT + "' TBLPROPERTIES ", 1)
                store._execute(sql)
                event(stream, "create_acknowledged", current_kind, current_name)
                verify_table(client.tables.get(current_name), current_name, table, username)
                event(stream, "verified", current_kind, current_name)
        event(stream, "finished", "plan", plan["plan_sha256"], status="verified",
              catalogs=len(NAMES), schemas=len(NAMES) * plan["schema_count_per_catalog"],
              tables=len(NAMES) * plan["target_count_per_catalog"], data_loaded=False)
        return True
    except Exception as error:
        event(stream, "failed", current_kind, current_name, error=ds.safe_error(error),
              statement_id=getattr(error, "statement_id", None),
              sql_state=getattr(error, "sql_state", None), no_retry=True, no_cleanup=True)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    identity = profile_identity("labs")
    if args.action == "plan":
        plan = build_plan(args.xml, identity)
        args.directory.mkdir(parents=True, exist_ok=False)
        with (args.directory / "plan.json").open("x", encoding="utf-8") as out:
            json.dump(plan, out, indent=2, sort_keys=True)
        print(json.dumps({k: plan[k] for k in ("plan_sha256", "catalogs", "schema_count_per_catalog",
            "target_count_per_catalog", "column_count_per_catalog")}, sort_keys=True))
    else:
        plan = json.loads((args.directory / "plan.json").read_bytes())
        verify_plan(plan, args.plan_sha256, identity, args.xml)
        with (args.directory / "events.jsonl").open("x", encoding="utf-8") as out:
            if not provision(plan, identity, out):
                raise SystemExit(1)


if __name__ == "__main__":
    main()