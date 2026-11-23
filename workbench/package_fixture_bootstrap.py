"""Explicit full-package EMPTY fixtures. Offline planning; operator-only apply.

No app installer, data loading, migration binding approval or startup hooks.
The deployment bundle packages this operator, not a provisioning orchestrator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from defusedxml.ElementTree import iterparse

from workbench.source_fixtures import identifier, plan_sources
from workbench.target_fixtures import plan_targets

FORMAT = "orion-full-package-empty-v1"
LAYERS = ("bronze", "silver", "gold")
TYPE = re.compile(r"STRING|BINARY|TIMESTAMP_NTZ|INT|SMALLINT|DOUBLE|DECIMAL\(([0-9]+),([0-9]+)\)")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous duplicate JSON key")
            result[key] = value
        return result

    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs)


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def user_key(label):
    if not isinstance(label, str) or not label.strip() or any(ord(c) < 32 for c in label):
        raise ValueError("An explicit user label is required for offline planning")
    return sha256(label.encode()).hexdigest()


def catalog_name(user_label, package_sha, explicit=None):
    owner = user_key(user_label)
    if not re.fullmatch(r"[a-f0-9]{64}", package_sha):
        raise ValueError("Invalid package SHA")
    # Complete user and package hashes, not potentially colliding short prefixes.
    prefix = "orion_empty_" + owner + "_" + package_sha
    name = explicit if explicit is not None else prefix
    identifier(name)
    if name != prefix and not (name.startswith(prefix + "_") and len(name) > len(prefix) + 1):
        raise ValueError("Explicit catalog must retain the dedicated user+package namespace")
    return name


def assignment_key(kind, xml_identity):
    """Assignment keys pin kind AND the exact six-component XML identity."""
    return json.dumps([kind, xml_identity], ensure_ascii=True, separators=(",", ":"))


def build_plan(xml, user_label, catalog=None, assignments=None, profile="labs"):
    if profile != "labs":
        raise ValueError("Only the explicit labs user profile is supported")
    package_sha = file_hash(xml)
    catalog = catalog_name(user_label, package_sha, catalog)
    assignments = dict(assignments or {})
    # The legacy SOURCE planner treats missing nullability as nullable. This
    # operator must reject ambiguity instead, without changing that prior path.
    counts = {"SOURCE": 0, "TARGET": 0}
    for event_type, elem in iterparse(xml, events=("start", "end"),
                                    forbid_entities=True, forbid_external=True):
        tag = elem.tag.rsplit("}", 1)[-1]
        if event_type == "start":
            if tag in counts:
                counts[tag] += 1
            if tag in {"SOURCEFIELD", "TARGETFIELD"} and elem.get("NULLABLE") not in {"NULL", "NOTNULL"}:
                raise ValueError("Explicit source/target column nullability is required")
        else:
            elem.clear()
    sources, targets = plan_sources(xml, catalog), plan_targets(xml, catalog)
    if sources["package_sha256"] != package_sha or targets["package_sha256"] != package_sha:
        raise ValueError("XML changed during planning")
    if counts != {"SOURCE": len(sources["tables"]), "TARGET": len(targets["tables"])}:
        raise ValueError("Full XML definition inventory mismatch")
    tables, keys, physical, identities = [], set(), set(), set()
    for kind, inventory in (("SOURCE", sources), ("TARGET", targets)):
        for original in inventory["tables"]:
            identity = original["xml_identity"]
            key = assignment_key(kind, identity)
            if key in keys:
                raise ValueError("Duplicate complete XML identity")
            keys.add(key)
            identity_hash = fingerprint(identity)
            layer = assignments.get(key, "bronze" if kind == "SOURCE" else "silver")
            if layer not in LAYERS or kind == "SOURCE" and layer != "bronze":
                raise ValueError("Sources must use bronze; target assignments must name a medallion layer")
            readable = re.sub(r"[^a-z0-9_]+", "_", identity[-1].lower()).strip("_")[:64] or "table"
            name = kind.lower() + "_" + readable + "_" + identity_hash
            fqn = ".".join((catalog, layer, name))
            if fqn.casefold() in physical:
                raise ValueError("Physical fixture collision")
            physical.add(fqn.casefold())
            identities.add((kind, identity_hash))
            columns = original["columns"]
            for column in columns:
                identifier(column["name"])
                match = TYPE.fullmatch(column["type"])
                if not match or type(column["nullable"]) is not bool:
                    raise ValueError("Unsupported or ambiguous column")
                if match.group(1) and not 1 <= int(match.group(1)) <= 38:
                    raise ValueError("Invalid decimal precision")
                if match.group(2) and not 0 <= int(match.group(2)) <= int(match.group(1)):
                    raise ValueError("Invalid decimal scale")
            if not columns or len({c["name"].casefold() for c in columns}) != len(columns):
                raise ValueError("Missing or ambiguous columns")
            tables.append({"xml_identity": identity, "xml_identity_sha256": identity_hash,
                           "original_xml_name": identity[-1], "assignment_key": key,
                           "physical_fqn": fqn, "table": name, "kind": kind, "layer": layer,
                           "basis": "explicit_full_xml_identity_assignment" if key in assignments else
                           ("source_empty_bronze" if kind == "SOURCE" else "provisional_target_silver_testing_only"),
                           "columns": columns})
    if set(assignments) - keys:
        raise ValueError("Assignment references an unknown full XML identity")
    if len(identities) != len(tables) or not tables:
        raise ValueError("Empty inventory or full-identity hash collision")
    source_names = {t["physical_fqn"].casefold() for t in tables if t["kind"] == "SOURCE"}
    target_names = {t["physical_fqn"].casefold() for t in tables if t["kind"] == "TARGET"}
    if source_names & target_names:
        raise ValueError("Source equals target physical destination")
    if file_hash(xml) != package_sha:
        raise ValueError("XML changed during planning")
    plan = {"format": FORMAT, "package_sha256": package_sha, "user_label": user_label,
            "user_sha256": user_key(user_label), "profile": profile, "catalog": catalog,
            "schemas": list(LAYERS), "assignments": assignments,
            "tables": sorted(tables, key=lambda t: t["assignment_key"]),
            "source_count": len(sources["tables"]), "target_count": len(targets["tables"]),
            "data_loaded": False, "bindings_approved": False, "gold_lineage_verified": False,
            "warnings": ["All XML definitions included, including control/log and orphan targets.",
                         "Default target silver placement is provisional testing, not verified lineage.",
                         "Gold schema is created; gold tables require explicit target assignments, not fabricated lineage.",
                         "Empty structural fixtures do not prove business equivalence or connectivity.",
                         *sources["warnings"], *targets["warnings"]]}
    plan["plan_sha256"] = fingerprint(plan)
    return plan


def marker(plan):
    return json.dumps({"fixture": FORMAT, "package": plan["package_sha256"],
                       "user": plan["user_sha256"], "plan": plan["plan_sha256"]}, sort_keys=True)


def properties(plan, table):
    return {"orion.fixture": FORMAT, "orion.package_sha256": plan["package_sha256"],
            "orion.user_sha256": plan["user_sha256"], "orion.plan_sha256": plan["plan_sha256"],
            "orion.xml_identity_sha256": table["xml_identity_sha256"],
            "orion.kind": table["kind"], "orion.layer": table["layer"]}


def table_sql(plan, table):
    name = ".".join(identifier(part) for part in table["physical_fqn"].split("."))
    columns = ",\n".join(identifier(c["name"]) + " " + c["type"] +
                          ("" if c["nullable"] else " NOT NULL") for c in table["columns"])
    props = ", ".join("'" + k + "'='" + v + "'" for k, v in properties(plan, table).items())
    sql = f"CREATE TABLE {name} (\n{columns}\n) USING DELTA TBLPROPERTIES ({props})"
    if "$$" in sql:
        raise ValueError("Unresolved Informatica token in executable SQL")
    return sql


def export_plan(plan, output):
    """Only a new directory, exclusively created files, never overwrite/cleanup."""
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("Symlink output is forbidden")
    if fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("Plan checksum mismatch")
    sql = {kind: [table_sql(plan, t) for t in plan["tables"] if t["kind"] == kind]
           for kind in ("SOURCE", "TARGET")}
    output.mkdir(parents=True, exist_ok=False)
    files = {"creation-manifest.json": json.dumps(plan, indent=2, sort_keys=True),
             "source-tables.sql": ";\n\n".join(sql["SOURCE"]) + (";\n" if sql["SOURCE"] else ""),
             "target-tables.sql": ";\n\n".join(sql["TARGET"]) + (";\n" if sql["TARGET"] else "")}
    for name, content in files.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def verify_table(info, plan, table, username):
    value = lambda v: getattr(v, "value", v)
    actual = [(c.name, c.type_text.upper().replace(" ", ""), c.nullable) for c in info.columns or []]
    expected = [(c["name"], c["type"], c["nullable"]) for c in table["columns"]]
    allowed_system = {"delta.minReaderVersion", "delta.minWriterVersion", "delta.enableDeletionVectors",
                      "delta.columnMapping.mode", "delta.columnMapping.maxColumnId",
                      "delta.feature.appendOnly", "delta.feature.invariants", "delta.feature.deletionVectors"}
    extra_properties = set(info.properties or {}) - set(properties(plan, table))
    for key in extra_properties & allowed_system:
        prop = info.properties[key]
        allowed = ({"true", "false"} if key == "delta.enableDeletionVectors" else
                   {"none", "name", "id"} if key == "delta.columnMapping.mode" else
                   {"supported"} if key.startswith("delta.feature.") else None)
        if not isinstance(prop, str) or (prop not in allowed if allowed is not None else not prop.isdecimal()):
            raise ValueError("Unexpected Delta system property value")
    if (info.full_name != table["physical_fqn"] or info.owner != username or
            value(info.table_type) != "MANAGED" or value(info.data_source_format) != "DELTA" or
            actual != expected or extra_properties - allowed_system or any((info.properties or {}).get(k) != v
                                      for k, v in properties(plan, table).items())):
        raise ValueError("Exact managed Delta metadata, provenance or ownership mismatch")
    return {"full_name": info.full_name, "owner": info.owner,
            "table_type": value(info.table_type), "format": value(info.data_source_format),
            "columns": [{"name": c.name, "type": c.type_text, "nullable": c.nullable}
                        for c in info.columns], "properties": dict(info.properties or {})}


def verify_container(info, kind, name, plan, username):
    actual = info.name if kind == "catalog" else info.full_name
    if actual != name or info.owner != username or info.comment != marker(plan):
        raise ValueError("Dedicated container ownership/provenance mismatch")
    return {"full_name": actual, "owner": info.owner, "comment": info.comment}


def verify_inventory(client, plan, username, missing):
    """An existing dedicated catalog cannot hide unplanned user objects."""
    if plan["catalog"] in missing:
        children = [plan["catalog"] + "." + layer for layer in LAYERS] + [
            table["physical_fqn"] for table in plan["tables"]]
        if any(name not in missing for name in children):
            raise ValueError("Inconsistent catalog/schema absence")
        return
    if any(t["physical_fqn"] not in missing and plan["catalog"] + "." + t["layer"] in missing
           for t in plan["tables"]):
        raise ValueError("Inconsistent schema/table absence")
    expected = {t["physical_fqn"] for t in plan["tables"]}
    for schema in client.schemas.list(catalog_name=plan["catalog"]):
        name = schema.name
        if name == "information_schema":
            continue  # UC-owned system metadata, never created or changed here.
        full = plan["catalog"] + "." + name
        if name == "default":
            if schema.owner != username:
                raise ValueError("Automatic default schema is not user-owned")
        elif name in LAYERS:
            verify_container(client.schemas.get(full), "schema", full, plan, username)
        else:
            raise ValueError("Unplanned schema in dedicated fixture catalog")
        for table in client.tables.list(catalog_name=plan["catalog"], schema_name=name):
            if table.full_name not in expected:
                raise ValueError("Unplanned object in dedicated fixture catalog")


def fresh_profile_identity(profile):
    """SDK 0.157.0 single-attempt transport, installed before the first me().

    Only this newly constructed operator client is adapted. The SDK otherwise
    retries transport errors and Retry-After responses, including CREATE POSTs.
    No global SDK patch, ambient client, credential fallback or config mutation.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    from workbench.databricks_models import profile_identity

    def factory(**kwargs):
        client = WorkspaceClient(**kwargs)
        transport = client.api_client._api_client
        perform = transport._perform

        def single_attempt(*args, **kw):
            try:
                return perform(*args, **kw)
            except NotFound:
                raise
            except Exception:
                # Neutral exception has no retry_after_secs and is not one of
                # the SDK retryable transport/Databricks exception classes.
                raise RuntimeError("Single-attempt operator request failed") from None

        transport._perform = single_attempt
        return client

    return profile_identity(profile, client_factory=factory)


def event(stream, plan, kind, name, state, **details):
    stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                             "plan_sha256": plan["plan_sha256"], "package_sha256": plan["package_sha256"],
                             "kind": kind, "name": name, "state": state, **details}, sort_keys=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def execute_create(client, warehouse, statement, on_submitted):
    """One submission, bounded observations, no resubmission or raw errors."""
    result = client.statement_execution.execute_statement(statement, warehouse,
                                                         wait_timeout="50s", row_limit=1)
    on_submitted(result.statement_id)
    for _ in range(20):
        state = getattr(result.status.state, "value", result.status.state)
        if state == "SUCCEEDED":
            return result.statement_id
        if state not in {"PENDING", "RUNNING"}:
            raise RuntimeError("DDL did not succeed")
        result = client.statement_execution.get_statement(result.statement_id)
    raise RuntimeError("DDL outcome requires manual reconciliation")


def verify_empty(client, warehouse, table):
    name = ".".join(identifier(part) for part in table["physical_fqn"].split("."))
    result = client.statement_execution.execute_statement(
        "SELECT EXISTS(SELECT 1 FROM " + name + " LIMIT 1)", warehouse, wait_timeout="50s", row_limit=1)
    for _ in range(20):
        state = getattr(result.status.state, "value", result.status.state)
        if state == "SUCCEEDED":
            rows = result.result.data_array
            if result.result.next_chunk_index is not None or rows not in ([["false"]], [[False]]):
                raise ValueError("Fixture is populated or empty-table verification is incomplete")
            return
        if state not in {"PENDING", "RUNNING"}:
            raise ValueError("Empty-table verification failed")
        result = client.statement_execution.get_statement(result.statement_id)
    raise ValueError("Empty-table verification remains uncertain")


def confirm_user(identity, plan):
    me = identity.client.current_user.me()
    schemas = {getattr(schema, "value", schema) for schema in (getattr(me, "schemas", None) or [])}
    if (not me.id or me.id != identity.user_id or not me.user_name or me.user_name != plan["user_label"] or
            getattr(me, "active", True) is False or
            "urn:ietf:params:scim:schemas:core:2.0:User" not in schemas or
            any("ServicePrincipal" in schema for schema in schemas)):
        raise PermissionError("Fresh human user identity does not match the reviewed namespace")
    catalog_name(me.user_name, plan["package_sha256"], plan["catalog"])
    return me


def apply_plan(plan, xml, output, warehouse, expected_plan_sha, consent, identity_provider=None):
    """Validate all objects before CREATE; exclusive journal forbids automatic retry."""
    if consent != "CREATE EMPTY FIXTURES " + str(expected_plan_sha):
        raise PermissionError("Exact checksum-bound CREATE consent is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", warehouse or ""):
        raise ValueError("An explicit existing warehouse is required")
    rebuilt = build_plan(xml, plan["user_label"], plan["catalog"], plan["assignments"], plan["profile"])
    if plan != rebuilt or expected_plan_sha != rebuilt["plan_sha256"]:
        raise ValueError("Entire plan or package checksum mismatch")
    output = Path(output)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("Symlink output is forbidden")
    if read_json(output / "creation-manifest.json") != plan:
        raise ValueError("Saved review manifest mismatch")
    for kind in ("SOURCE", "TARGET"):
        statements = [table_sql(plan, t) for t in plan["tables"] if t["kind"] == kind]
        expected = ";\n\n".join(statements) + (";\n" if statements else "")
        if (output / (kind.lower() + "-tables.sql")).read_text(encoding="utf-8") != expected:
            raise ValueError("Reviewed SQL changed")
    # No identity/provider construction in planning or import. No hosted/SP fallback.
    if identity_provider is None:
        identity_provider = fresh_profile_identity
    identity = identity_provider(plan["profile"])
    if identity.mode != "profile":
        raise PermissionError("Only fresh explicit user-profile identity may apply")
    client = identity.client
    me = confirm_user(identity, plan)
    from databricks.sdk.errors import NotFound
    objects = [("catalog", plan["catalog"], None)] + [
        ("schema", plan["catalog"] + "." + layer, None) for layer in LAYERS] + [
        ("table", t["physical_fqn"], t) for t in plan["tables"]]
    APIs = {"catalog": client.catalogs, "schema": client.schemas, "table": client.tables}
    with (output / "creation-receipts.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        event(stream, plan, "plan", plan["catalog"], "preflight", user_id=me.id,
              workspace=identity.host, warehouse=warehouse, no_retry=True, no_delete=True)
        missing = set()
        kind, name = "plan", plan["catalog"]
        try:
            for kind, name, table in objects:
                try:
                    info = APIs[kind].get(name)
                except NotFound:
                    missing.add(name)
                else:
                    if table is None:
                        verify_container(info, kind, name, plan, me.user_name)
                    else:
                        verify_table(info, plan, table, me.user_name)
                        verify_empty(client, warehouse, table)
            verify_inventory(client, plan, me.user_name, missing)
            # Recheck source file after the complete metadata preflight, before any DDL.
            if file_hash(xml) != plan["package_sha256"]:
                raise ValueError("XML changed before DDL")
        except Exception as error:
            event(stream, plan, kind, name, "failure", phase="preflight", error_type=type(error).__name__)
            raise RuntimeError("Preflight rejected; no DDL submitted. Inspect durable receipt.") from None
        for kind, name, table in objects:
            requested = acknowledged = False
            statement_id = None
            submission = {}
            try:
                # Re-read to close ordinary preflight drift. Race at CREATE remains fail-closed.
                try:
                    info = APIs[kind].get(name)
                except NotFound:
                    if name not in missing:
                        raise ValueError("Verified object disappeared")
                    confirm_user(identity, plan)
                    event(stream, plan, kind, name, "create_requested")
                    requested = True
                    if kind == "catalog":
                        client.catalogs.create(name=name, comment=marker(plan))
                    elif kind == "schema":
                        client.schemas.create(name=name.split(".")[1], catalog_name=plan["catalog"],
                                              comment=marker(plan))
                    else:
                        def submitted(identifier_value):
                            submission["statement_id"] = identifier_value
                            event(stream, plan, kind, name, "statement_submitted", statement_id=identifier_value)

                        statement_id = execute_create(client, warehouse, table_sql(plan, table), submitted)
                    acknowledged = True
                    event(stream, plan, kind, name, "create_acknowledged", statement_id=statement_id)
                    info = APIs[kind].get(name)
                else:
                    if name in missing:
                        raise ValueError("Object appeared after preflight; manual reconciliation required")
                if table is None:
                    readback = verify_container(info, kind, name, plan, me.user_name)
                else:
                    readback = verify_table(info, plan, table, me.user_name)
                    verify_empty(client, warehouse, table)
                    readback["empty_verified"] = True
                event(stream, plan, kind, name, "created" if acknowledged else "reused-verified",
                      metadata_verified=True, readback=readback, statement_id=statement_id)
            except Exception as error:
                event(stream, plan, kind, name, "uncertain" if requested else "failure",
                      phase="readback" if acknowledged else "submission", error_type=type(error).__name__,
                      statement_id=statement_id or submission.get("statement_id"), no_retry=True, no_delete=True)
                raise RuntimeError("Apply stopped. Reconcile receipts manually; do not retry or delete.") from None
        event(stream, plan, "plan", plan["catalog"], "complete", data_loaded=False,
              bindings_approved=False, tables_verified=len(plan["tables"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--user-label", help="Exact expected login name; offline plan only")
    parser.add_argument("--profile", default="labs", choices=("labs",))
    parser.add_argument("--assignments", type=Path, help="JSON object keyed by exact assignment_key")
    parser.add_argument("--warehouse")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha")
    parser.add_argument("--consent", help="Exact text: CREATE EMPTY FIXTURES <plan SHA>")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if not args.expected_plan_sha or not args.warehouse or not args.consent:
                parser.error("Apply requires --warehouse, --expected-plan-sha and --consent")
            plan = read_json(args.output / "creation-manifest.json")
            if (args.profile != plan["profile"] or args.catalog and args.catalog != plan["catalog"] or
                    args.user_label and args.user_label != plan["user_label"] or args.assignments):
                parser.error("Apply uses the exact saved plan, not replacement assignments or identity")
            apply_plan(plan, args.xml, args.output, args.warehouse, args.expected_plan_sha, args.consent)
            print(json.dumps({"state": "complete", "plan_sha256": plan["plan_sha256"], "data_loaded": False}))
        else:
            if not args.user_label:
                parser.error("Offline plan requires --user-label; planning never authenticates")
            assignments = read_json(args.assignments) if args.assignments else None
            if assignments is not None and not isinstance(assignments, dict):
                parser.error("Assignments must be a JSON object")
            plan = build_plan(args.xml, args.user_label, args.catalog, assignments, args.profile)
            export_plan(plan, args.output)
            print(json.dumps({"state": "review_required", "plan_sha256": plan["plan_sha256"],
                              "catalog": plan["catalog"], "source_count": plan["source_count"],
                              "target_count": plan["target_count"], "data_loaded": False}))
    except Exception:
        # SDK/auth exception bodies can include credentials. Never print them.
        raise SystemExit("Bootstrap stopped. Review local manifest/receipts; no automatic retry or cleanup.") from None


if __name__ == "__main__":
    main()