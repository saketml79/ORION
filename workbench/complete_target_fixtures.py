"""One-attempt completion of structural TARGET fixtures in EXISTING catalogs.

Offline --plan never authenticates. --apply is checksum/consent bound, claims
the plan once, preflights the entire scope before DDL, and never retries CREATE.
No data access, catalog creation, grants, Jobs, models, app deployment or delete.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from databricks.sdk.errors import NotFound

from workbench.package_fixture_bootstrap import (
    execute_create, file_hash, fingerprint, fresh_profile_identity, read_json,
)
from workbench.source_fixtures import identifier
from workbench.target_fixtures import plan_targets, target_sql

PACKAGE = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
HOST = "https://adb-1866518241053589.9.azuredatabricks.net"
USER = "ssuman@bpcs.com"
USER_ID = "4321958869410665"
CATALOGS = tuple(f"orion_{layer}_test_ssuman" for layer in ("bronze", "silver", "gold"))
WAREHOUSE = "e3a0fc2c08db05bb"
FORMAT = "orion-existing-target-completion-v1"
DISCLOSURE = "Empty XML structural fixtures only, not semantic medallion layers or approved migration bindings."


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def new_directory(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Symlink output forbidden")
    path.mkdir(parents=True, exist_ok=False)
    return path


def build_plan(xml):
    if file_hash(xml) != PACKAGE:
        raise ValueError("Pinned package mismatch")
    inventory = plan_targets(xml, CATALOGS[0])
    if inventory["package_sha256"] != PACKAGE or file_hash(xml) != PACKAGE:
        raise ValueError("Package changed during planning")
    tables = sorted(inventory["tables"], key=lambda t: (t["schema"], t["table"]))
    if len(tables) != 307:
        raise ValueError("All 307 XML TARGET definitions required")
    objects = []
    for catalog in CATALOGS:
        for table in tables:
            objects.append({"catalog": catalog, "full_name": ".".join((catalog, table["schema"], table["table"])),
                            "target": table, "sql": target_sql(catalog, table, PACKAGE)})
    plan = {"format": FORMAT, "profile": "labs", "host": HOST, "user": USER, "user_id": USER_ID,
            "warehouse": WAREHOUSE, "package_sha256": PACKAGE, "catalogs": list(CATALOGS),
            "targets_per_catalog": len(tables), "schemas_per_catalog": len({t["schema"] for t in tables}),
            "objects": objects, "data_loaded": False, "semantic_medallion": False,
            "disclosure": DISCLOSURE, "warnings": inventory["warnings"]}
    plan["plan_sha256"] = fingerprint(plan)
    return plan


def export_plan(plan, output):
    if fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("Plan checksum mismatch")
    output = new_directory(output)
    write_new(output / "plan.json", plan)
    return output


class Journal:
    """Append-only intent BEFORE request and durable sanitized observation AFTER."""

    def __init__(self, path, plan):
        self.stream = Path(path).open("x", encoding="utf-8", newline="\n")
        self.plan = plan

    def emit(self, state, **details):
        self.stream.write(json.dumps({"utc": now(), "plan_sha256": self.plan["plan_sha256"],
                                      "state": state, **details}, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def request(self, operation, name, call, project=None):
        self.emit("request", operation=operation, name=name)
        try:
            result = call()
        except NotFound:
            self.emit("not_found", operation=operation, name=name)
            raise
        except Exception as error:
            self.emit("request_failed", operation=operation, name=name, error_type=safe_type(error))
            raise RuntimeError("Request failed; inspect journal, no automatic retry") from None
        self.emit("response", operation=operation, name=name,
                  readback=project(result) if project else None)
        return result

    def close(self):
        self.stream.close()


def safe_type(error):
    # Never stringify a remote exception, SQL status.error, token or auth config.
    name = type(error).__name__
    return name if re.fullmatch(r"[A-Za-z_]{1,80}", name) else "Error"


def value(item):
    return getattr(item, "value", item)


def container_metadata(info, kind):
    return {"full_name": info.name if kind == "catalog" else info.full_name, "owner": info.owner}


def table_metadata(info):
    return {"full_name": info.full_name, "owner": info.owner,
            "table_type": value(info.table_type), "format": value(info.data_source_format),
            "columns": [{"name": c.name, "type": c.type_text, "nullable": c.nullable}
                        for c in info.columns or []],
            "fixture": (info.properties or {}).get("orion.fixture"),
            "package_sha256": (info.properties or {}).get("orion.package_sha256")}


def verify_container(info, kind, name):
    metadata = container_metadata(info, kind)
    if metadata != {"full_name": name, "owner": USER}:
        raise PermissionError("Container identity/ownership mismatch; CREATE permission not established")
    # UC ownership confers CREATE_SCHEMA/CREATE_TABLE and USE on this securable.
    # This never infers authority from a missing/failed permission response.
    return metadata


def verify_table(info, obj):
    metadata = table_metadata(info)
    actual = [(c["name"], c["type"].upper().replace(" ", ""), c["nullable"])
              for c in metadata["columns"]]
    expected = [(c["name"], c["type"], c["nullable"]) for c in obj["target"]["columns"]]
    if (metadata["full_name"] != obj["full_name"] or metadata["owner"] != USER
            or metadata["table_type"] != "MANAGED" or metadata["format"] != "DELTA"
            or actual != expected or any(type(c["nullable"]) is not bool for c in metadata["columns"])
            or metadata["fixture"] != "xml_target_schema_only" or metadata["package_sha256"] != PACKAGE):
        raise ValueError("Target ordered columns/types/nullability/owner/managed Delta/provenance mismatch")
    return metadata


def confirm_identity(identity, journal):
    if (identity.mode != "profile" or identity.host != HOST or identity.user_id != USER_ID
            or identity.client.config.host != HOST):
        raise PermissionError("Explicit labs workspace/user identity mismatch")
    me = journal.request("current_user.me", USER,
                         identity.client.current_user.me,
                         lambda u: {"id": u.id, "user": u.user_name, "active": u.active,
                                    "schemas": [value(s) for s in u.schemas or []]})
    schemas = {value(s) for s in me.schemas or []}
    if (me.id != USER_ID or me.user_name != USER or me.active is not True
            or "urn:ietf:params:scim:schemas:core:2.0:User" not in schemas
            or any("ServicePrincipal" in s for s in schemas)):
        raise PermissionError("Fresh active exact human labs identity required")


def get_container(client, journal, kind, name):
    api = client.catalogs if kind == "catalog" else client.schemas
    info = journal.request(kind + ".get", name, lambda: api.get(name),
                           lambda i: container_metadata(i, kind))
    verify_container(info, kind, name)
    return info


def get_table(client, journal, obj):
    info = journal.request("table.get", obj["full_name"],
                           lambda: client.tables.get(obj["full_name"]), table_metadata)
    verify_table(info, obj)
    return info


def inventory(client, journal, catalog):
    """Dynamic user-object inventory, including unrelated schemas/tables, no row SQL."""
    schemas = journal.request("schemas.list", catalog,
        lambda: list(client.schemas.list(catalog_name=catalog)),
        lambda rows: [container_metadata(s, "schema") for s in rows])
    result = {}
    for schema in schemas:
        if schema.name == "information_schema":
            continue
        if schema.full_name != catalog + "." + schema.name or schema.full_name in result:
            raise ValueError("Ambiguous schema inventory")
        tables = journal.request("tables.list", schema.full_name,
            lambda s=schema: list(client.tables.list(catalog_name=catalog, schema_name=s.name)),
            lambda rows: [{"full_name": t.full_name, "owner": t.owner,
                           "table_type": value(t.table_type), "table_id": t.table_id} for t in rows])
        names = [t.full_name for t in tables]
        if len(set(names)) != len(names) or any(not n.startswith(schema.full_name + ".") for n in names):
            raise ValueError("Ambiguous table inventory")
        result[schema.full_name] = {"owner": schema.owner,
            "tables": {t.full_name: {"owner": t.owner, "table_type": value(t.table_type),
                                      "table_id": t.table_id} for t in tables}}
    return result


def schema_names(plan):
    return sorted({obj["catalog"] + "." + obj["target"]["schema"] for obj in plan["objects"]})


def preflight(plan, identity, journal):
    client = identity.client
    confirm_identity(identity, journal)
    baseline = {}
    missing_schemas, missing_tables = set(), set()
    for catalog in plan["catalogs"]:
        get_container(client, journal, "catalog", catalog)  # NotFound MUST fail, never create catalog.
        baseline[catalog] = inventory(client, journal, catalog)
    for name in schema_names(plan):
        try:
            get_container(client, journal, "schema", name)
        except NotFound:
            if name in baseline[name.split(".")[0]]:
                raise ValueError("Listed schema disappeared")
            missing_schemas.add(name)
    for obj in plan["objects"]:
        name = obj["full_name"]
        schema = name.rsplit(".", 1)[0]
        try:
            get_table(client, journal, obj)
        except NotFound:
            if name in baseline[obj["catalog"]].get(schema, {}).get("tables", {}):
                raise ValueError("Listed table disappeared")
            missing_tables.add(name)
        else:
            if schema in missing_schemas or name not in baseline[obj["catalog"]].get(schema, {}).get("tables", {}):
                raise ValueError("GET/list inventory drift")
    snapshot = {"inventory": baseline, "missing_schemas": sorted(missing_schemas),
                "missing_tables": sorted(missing_tables), "ddl_count": len(missing_schemas) + len(missing_tables),
                "permission_basis": "Fresh exact UC catalog and existing target schema ownership",
                "disclosure": DISCLOSURE}
    journal.emit("preflight_complete", readback=snapshot)
    return snapshot


def run_completion(plan, xml, identity, journal, output):
    """Internal operator, invoked only after immutable-plan and one-shot claim gates."""
    client = identity.client
    summary = {"started_utc": now(), "plan_sha256": plan["plan_sha256"], "package_sha256": PACKAGE,
               "host": HOST, "user": USER, "user_id": USER_ID, "status": "preflight",
               "disclosure": DISCLOSURE, "data_loaded": False, "no_retry": True,
               "catalogs": {c: {"schemas_created": 0, "schemas_reused_verified": 0,
                                "tables_created": 0, "tables_reused_verified": 0} for c in plan["catalogs"]}}
    phase, name = "preflight", "all_targets"
    try:
        snapshot = preflight(plan, identity, journal)
        write_new(output / "preflight.json", snapshot)
        if file_hash(xml) != PACKAGE:
            raise ValueError("XML changed before DDL")
        missing_schemas, missing_tables = set(snapshot["missing_schemas"]), set(snapshot["missing_tables"])
        requested = False
        phase = "apply"
        objects = [("schema", n, None) for n in schema_names(plan)] + [
            ("table", obj["full_name"], obj) for obj in plan["objects"]]
        for kind, name, obj in objects:
            requested = False
            catalog = name.split(".")[0]
            counts = summary["catalogs"][catalog]
            missing = missing_schemas if kind == "schema" else missing_tables
            try:
                info = (get_container(client, journal, kind, name) if kind == "schema"
                        else get_table(client, journal, obj))
            except NotFound:
                if name not in missing:
                    raise ValueError("Verified object disappeared")
                confirm_identity(identity, journal)
                get_container(client, journal, "catalog", catalog)
                if kind == "table":
                    get_container(client, journal, "schema", name.rsplit(".", 1)[0])
                if file_hash(xml) != PACKAGE:
                    raise ValueError("XML changed before request")
                journal.emit("create_requested", kind=kind, name=name,
                             sql=obj["sql"] if obj else None)
                requested = True
                if kind == "schema":
                    journal.request("schema.create", name,
                        lambda: client.schemas.create(name=name.split(".")[1], catalog_name=catalog,
                                                      comment=DISCLOSURE))
                else:
                    # The exact saved target_sql, never IF NOT EXISTS or a fallback statement.
                    def submitted(statement_id):
                        journal.emit("statement_submitted", name=name, statement_id=statement_id)

                    execute_create(client, plan["warehouse"], obj["sql"], submitted)
                journal.emit("create_acknowledged", kind=kind, name=name)
                info = (get_container(client, journal, kind, name) if kind == "schema"
                        else get_table(client, journal, obj))
                counts[kind + "s_created"] += 1
                state = "created_verified"
            else:
                if name in missing:
                    raise ValueError("Object appeared after preflight; manual reconciliation required")
                counts[kind + "s_reused_verified"] += 1
                state = "reused_verified"
            journal.emit(state, kind=kind, name=name, metadata_verified=True,
                         readback=container_metadata(info, kind) if kind == "schema" else table_metadata(info))
            total = sum(v["tables_created"] + v["tables_reused_verified"] for v in summary["catalogs"].values())
            if kind == "table" and total % 25 == 0:
                print(json.dumps({"utc": now(), "tables_verified": total, "of": len(plan["objects"])}), flush=True)
        phase = "final_inventory"
        for catalog in plan["catalogs"]:
            get_container(client, journal, "catalog", catalog)
            after = inventory(client, journal, catalog)
            before = snapshot["inventory"][catalog]
            expected_new_schemas = {s for s in missing_schemas if s.startswith(catalog + ".")}
            if set(after) != set(before) | expected_new_schemas:
                raise ValueError("Final schema inventory changed unexpectedly")
            for schema, prior in before.items():
                if after[schema]["owner"] != prior["owner"]:
                    raise ValueError("Pre-existing schema owner changed")
                if any(after[schema]["tables"].get(n) != metadata for n, metadata in prior["tables"].items()):
                    raise ValueError("Pre-existing object inventory/identity changed")
            before_names = {n for s in before.values() for n in s["tables"]}
            after_names = {n for s in after.values() for n in s["tables"]}
            expected = {o["full_name"] for o in plan["objects"] if o["catalog"] == catalog}
            if after_names != before_names | expected:
                raise ValueError("Final table inventory changed unexpectedly")
            summary["catalogs"][catalog]["total_user_objects_before"] = len(before_names)
            summary["catalogs"][catalog]["total_user_objects_after"] = len(after_names)
            summary["catalogs"][catalog]["unrelated_inventory_preserved"] = True
        summary["status"] = "complete"
        summary["metadata_verified"] = True
    except BaseException as error:
        summary.update(status="partial_or_rejected", phase=phase, stopped_at=name,
                       error_type=safe_type(error), metadata_verified=False)
        journal.emit("stopped_no_retry", phase=phase, name=name, error_type=safe_type(error))
        if phase == "apply" and requested:
            # Unknown CREATE is NEVER repeated. Exactly one GET reconciles its metadata.
            try:
                info = (get_container(client, journal, kind, name) if kind == "schema"
                        else get_table(client, journal, obj))
                journal.emit("reconciled_present_no_retry", name=name,
                    readback=container_metadata(info, kind) if kind == "schema" else table_metadata(info))
                summary["uncertain_request_reconciled"] = "present_metadata_verified"
            except Exception:
                journal.emit("reconciliation_unresolved_no_retry", name=name)
                summary["uncertain_request_reconciled"] = "unresolved"
    finally:
        summary["finished_utc"] = now()
        journal.emit("finished", readback=summary)
        write_new(output / "summary.json", summary)
    return summary


class ObservedStatements:
    def __init__(self, original, journal):
        self.original, self.journal = original, journal

    def execute_statement(self, statement, warehouse, **kwargs):
        return self.journal.request("statement.execute", fingerprint(statement),
            lambda: self.original.execute_statement(statement, warehouse, **kwargs),
            lambda r: {"statement_id": r.statement_id, "state": value(r.status.state)})

    def get_statement(self, statement_id):
        return self.journal.request("statement.get", statement_id,
            lambda: self.original.get_statement(statement_id),
            lambda r: {"statement_id": r.statement_id, "state": value(r.status.state)})


def apply_plan(plan_dir, output, xml, expected_sha, consent, identity_provider=None):
    plan_dir = Path(plan_dir).absolute()
    if plan_dir.is_symlink() or any(p.is_symlink() for p in plan_dir.parents):
        raise ValueError("Symlink plan forbidden")
    plan = read_json(plan_dir / "plan.json")
    if plan != build_plan(xml) or expected_sha != plan["plan_sha256"]:
        raise ValueError("Entire saved plan/package/checksum mismatch")
    if consent != "CREATE MISSING TARGET FIXTURES " + expected_sha:
        raise PermissionError("Checksum-bound explicit consent required")
    output = new_directory(output)
    # One claim per saved plan, even if a different receipt directory is supplied.
    write_new(plan_dir / "apply-claim.json", {"utc": now(), "plan_sha256": expected_sha,
                                            "receipt_directory": str(output), "no_retry": True})
    write_new(output / "reviewed-plan.json", plan)
    journal = Journal(output / "journal.jsonl", plan)
    try:
        journal.emit("identity_requested", profile="labs", host=HOST, user=USER)
        if identity_provider is None:
            from workbench.databricks_models import _profile_host
            if _profile_host("labs") != HOST:
                raise PermissionError("Configured labs host differs from approved host")
        identity = (identity_provider or fresh_profile_identity)("labs")
        # Wrap only this freshly constructed single-attempt operator client.
        # WorkspaceClient exposes a read-only property, so use a tiny proxy.
        class Client:
            def __init__(self, original):
                self.original = original
                self.statement_execution = ObservedStatements(original.statement_execution, journal)

            def __getattr__(self, key):
                return getattr(self.original, key)

        from types import SimpleNamespace
        identity = SimpleNamespace(mode=identity.mode, host=identity.host, user_id=identity.user_id,
                                   client=Client(identity.client))
        return run_completion(plan, xml, identity, journal, output)
    except Exception as error:
        journal.emit("identity_or_setup_rejected", error_type=safe_type(error))
        raise RuntimeError("Identity/setup rejected; no retry. Inspect durable journal.") from None
    finally:
        journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--expected-plan-sha")
    parser.add_argument("--consent")
    args = parser.parse_args(argv)
    try:
        if args.plan:
            if args.plan_dir or args.expected_plan_sha or args.consent:
                raise ValueError("Plan mode does not accept apply arguments")
            plan = build_plan(args.xml)
            export_plan(plan, args.output)
            print(json.dumps({"state": "planned_offline", "plan_sha256": plan["plan_sha256"],
                              "targets_per_catalog": 307, "table_scope": len(plan["objects"]),
                              "schemas_per_catalog": plan["schemas_per_catalog"], "disclosure": DISCLOSURE}))
        else:
            if not args.plan_dir or not args.expected_plan_sha or not args.consent:
                raise ValueError("Apply requires saved plan, expected SHA and explicit consent")
            summary = apply_plan(args.plan_dir, args.output, args.xml, args.expected_plan_sha, args.consent)
            print(json.dumps(summary, sort_keys=True), flush=True)
            if summary["status"] != "complete":
                raise SystemExit(1)
    except Exception as error:
        raise SystemExit("Completion stopped (" + safe_type(error) + "). Inspect receipts; do not rerun.") from None


if __name__ == "__main__":
    main()