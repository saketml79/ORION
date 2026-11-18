"""Explicit CREATE-only empty test fixtures under fresh user OBO.

No ACL changes, data seeding, Jobs, production writes, automatic retries or
cleanup deletes. A partial failure is reported with resources actually observed.
"""
from datetime import datetime, timezone

from workbench import delta_state as ds
from workbench.target_fixtures import catalog_names, target_sql


def provision_test_catalogs(delta, report, *, consent, confirmed_names, expected_report_hash, journal):
    if consent is not True:
        raise PermissionError("Separate provisioning consent required")
    if (report["report_sha256"] != expected_report_hash
            or report["report_sha256"] != ds._hash({k: v for k, v in report.items() if k != "report_sha256"})
            or report["workspace_id"] != delta.workspace_id or report["user_id"] != delta.user_id):
        raise PermissionError("Exact campaign owner and hash required")
    plan = report["catalog_plan"]
    # Hosted plans use the verified immutable SCIM user ID, never a browser label.
    if (confirmed_names != plan["names"] or confirmed_names != catalog_names(delta.user_id, report["id"])
            or report["bindings"]["source_fixture_catalog"].casefold() in {n.casefold() for n in confirmed_names.values()}
            or plan["target_errors"] or len(plan["targets"]) > 200):
        raise ValueError("Dedicated test plan invalid or incomplete")
    for table in plan["targets"]:
        layer = table.get("destination_layer")
        if (layer is not None and (layer not in confirmed_names
                or table.get("destination_catalog") != confirmed_names[layer])):
            raise ValueError("Exact target layer/catalog assignment required")
    # Fresh identity and state catalog/schema ownership. No SP fallback.
    username = delta._authorize()
    present = {row[0].casefold() for row in delta._execute("SHOW CATALOGS")}
    if present.intersection(n.casefold() for n in confirmed_names.values()):
        raise ValueError("Test catalog already exists; no changes or reuse permitted")
    started = {"phase": "started", "campaign_id": report["id"], "report_sha256": expected_report_hash,
               "catalog_names": confirmed_names, "warehouse_id": delta.warehouse,
               "created_at": datetime.now(timezone.utc).isoformat(), "data_loaded": False,
               "automatic_retry_allowed": False}
    # Durable intent must be acknowledged before the first DDL statement.
    journal(started)
    observed, schemas, tables = [], [], []
    result = {"phase": "finished", "campaign_id": report["id"], "report_sha256": expected_report_hash,
              "status": "failed", "catalog_names": confirmed_names, "observed_catalogs": observed,
              "observed_schemas": schemas, "verified_tables": tables, "data_loaded": False,
              "source_catalog_modified": False, "acl_changes": False, "remote_jobs_run": False,
              "automatic_retry_allowed": False, "business_parity": "not_established"}
    try:
        for name in confirmed_names.values():
            delta._execute("CREATE CATALOG " + ds._identifier(name))
            info = delta.client.catalogs.get(name)
            if info.name != name or info.owner != username:
                raise PermissionError("Created catalog ownership could not be verified")
            observed.append(name)
        assigned = [t for t in plan["targets"] if t.get("destination_layer") is not None]
        for catalog, schema in sorted({(t["destination_catalog"], t["schema"]) for t in assigned}):
            delta._execute("CREATE SCHEMA " + ds._identifier(catalog) + "." + ds._identifier(schema))
            info = delta.client.schemas.get(catalog + "." + schema)
            if info.owner != username or info.full_name != catalog + "." + schema:
                raise PermissionError("Created schema ownership could not be verified")
            schemas.append(info.full_name)
        for table in assigned:
            catalog = table["destination_catalog"]
            delta._execute(target_sql(catalog, table, report["pins"]["package_hash"]))
            name = ".".join((catalog, table["schema"], table["table"]))
            info = delta.client.tables.get(name)
            actual = [(c.name.casefold(), c.type_text.upper().replace(" ", ""), c.nullable) for c in info.columns or []]
            expected = [(c["name"].casefold(), c["type"], c["nullable"]) for c in table["columns"]]
            if (info.full_name != name or info.owner != username or actual != expected
                    or getattr(info.data_source_format, "value", info.data_source_format) != "DELTA"
                    or (info.properties or {}).get("orion.fixture") != "xml_target_schema_only"
                    or (info.properties or {}).get("orion.package_sha256") != report["pins"]["package_hash"]):
                raise ValueError("Destination fixture metadata verification failed")
            tables.append(name)
        result["status"] = "passing"
        result["unassigned_target_ids"] = [t["target_id"] for t in plan["targets"] if t.get("destination_layer") is None]
        result["target_schema_status"] = "BLOCKED" if result["unassigned_target_ids"] else "PASS"
        result["basis"] = "Empty fixture metadata only, not populated runtime parity"
    except Exception as error:
        result["error"] = ds.safe_error(error)
        result["basis"] = "Partial or failed CREATE-only attempt. Inspect before any new action; no rollback deletes."
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    journal(result)
    return result