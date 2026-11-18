"""Explicit, additive XML source-schema fixtures for Databricks testing.

Not a live source-schema discovery service or a migration binding approval.
Only SOURCE/SOURCEFIELD attributes are read. Unknown types fail planning.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
from pathlib import Path
import re

from defusedxml.ElementTree import iterparse


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", value):
        raise ValueError("Unsafe fixture identifier")
    return "`" + value + "`"


def mock_type(attributes, database):
    kind = attributes["DATATYPE"].lower()
    if kind in {"number", "number(p,s)", "decimal", "numeric", "money"}:
        precision, scale = int(attributes["PRECISION"]), int(attributes["SCALE"])
        if not 1 <= precision <= 38 or not 0 <= scale <= precision:
            raise ValueError("Decimal outside Databricks precision/scale range")
        return f"DECIMAL({precision},{scale})"
    if kind in {"char", "nchar", "nvarchar", "nvarchar2", "varchar", "varchar2", "string", "text", "nclob"}:
        return "STRING"
    if kind == "long" and database == "Oracle":
        return "STRING"
    if kind in {"raw", "varbinary"}:
        return "BINARY"
    if kind in {"date", "datetime", "smalldatetime"}:
        # Oracle DATE carries time-of-day, so do not narrow it to Spark DATE.
        return "TIMESTAMP_NTZ"
    if kind in {"int", "integer"}:
        return "INT"
    if kind == "smallint" or kind == "tinyint" and database in {"Sybase", "Microsoft SQL Server"}:
        return "SMALLINT"
    if kind == "float":
        return "DOUBLE"
    raise ValueError(f"Unsupported XML datatype: {kind} / {database}")


def fixture_namespace(identity):
    """Complete XML namespace shared by planning and metadata observation."""
    if len(identity) != 6 or any(not isinstance(v, str) or v == "[REDACTED]" for v in identity):
        raise ValueError("Complete unredacted source identity is required")
    namespace_hash = sha256(json.dumps(identity[:-1], separators=(",", ":")).encode()).hexdigest()[:12]
    readable = re.sub(r"[^a-z0-9_]+", "_", "_".join(identity[2:5]).lower()).strip("_")[:72] or "unspecified"
    schema, table = "src_" + readable + "_" + namespace_hash, identity[-1].lower()
    identifier(schema)
    identifier(table)
    return schema, table


def plan_sources(path, catalog):
    identifier(catalog)
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    stack, tables, identities, names = [], [], set(), set()
    for event, elem in iterparse(path, events=("start", "end"), forbid_entities=True, forbid_external=True):
        tag = elem.tag.rsplit("}", 1)[-1]
        if event == "start":
            stack.append((tag, dict(elem.attrib)))
            continue
        if tag == "SOURCE":
            attrs = dict(elem.attrib)
            scope = {k: a.get("NAME", "") for k, a in stack if k in {"REPOSITORY", "FOLDER"}}
            identity = [scope.get("REPOSITORY", ""), scope.get("FOLDER", ""), attrs.get("DATABASETYPE", ""),
                        attrs.get("DBDNAME", ""), attrs.get("OWNERNAME", ""), attrs["NAME"]]
            key = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
            if key in identities:
                raise ValueError("Duplicate complete XML source identity requires explicit disposition")
            identities.add(key)
            schema, table = fixture_namespace(identity)
            if (schema, table) in names:
                raise ValueError("Case-insensitive fixture table collision")
            names.add((schema, table))
            fields = []
            for child in elem:
                if child.tag.rsplit("}", 1)[-1] != "SOURCEFIELD":
                    continue
                field = dict(child.attrib)
                identifier(field["NAME"])
                fields.append({"name": field["NAME"], "type": mock_type(field, attrs.get("DATABASETYPE", "")),
                               "nullable": field.get("NULLABLE") != "NOTNULL",
                               "xml_type": field["DATATYPE"], "precision": field["PRECISION"], "scale": field["SCALE"]})
            if not fields or len({f["name"].lower() for f in fields}) != len(fields):
                raise ValueError("Missing or duplicate fixture columns")
            tables.append({"xml_identity": identity, "schema": schema, "table": table, "columns": fields,
                           "binding_status": "test_fixture_only_not_approved_source_binding"})
        # Retain SOURCEFIELD children until the enclosing SOURCE is consumed.
        inside_source = any(t == "SOURCE" for t, _ in stack[:-1])
        if not inside_source:
            elem.clear()
        stack.pop()
    return {"version": 1, "package_sha256": digest.hexdigest(), "catalog": catalog, "tables": tables,
            "table_count": len(tables), "column_count": sum(len(t["columns"]) for t in tables),
            "schema_count": len({t["schema"] for t in tables}),
            "warnings": ["Empty fixtures, not source connectivity, data parity or verified semantic bindings.",
                         "Character widths, collation, padding and source-specific constraints are not enforced.",
                         "Oracle DATE becomes TIMESTAMP_NTZ; Oracle LONG becomes STRING; unsigned tinyint becomes SMALLINT.",
                         "Database/folder/owner metadata are namespace labels, not verified physical connections."]}


def table_sql(plan, table):
    columns = ",\n".join(identifier(c["name"]) + " " + c["type"] + ("" if c["nullable"] else " NOT NULL")
                         for c in table["columns"])
    name = ".".join(identifier(v) for v in (plan["catalog"], table["schema"], table["table"]))
    return (f"CREATE TABLE IF NOT EXISTS {name} (\n{columns}\n) USING DELTA "
            "TBLPROPERTIES ('orion.fixture'='xml_source_schema_only', "
            f"'orion.package_sha256'='{plan['package_sha256']}')")


def execute(client, warehouse, statement):
    result = client.statement_execution.execute_statement(statement, warehouse, wait_timeout="50s", row_limit=10000)
    while result.status.state.value in {"PENDING", "RUNNING"}:
        # get_statement is observational; SDK/network latency bounds this path.
        result = client.statement_execution.get_statement(result.statement_id)
    if result.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"SQL failed ({result.statement_id}): {result.status.error}")
    return result


def provision(client, warehouse, plan):
    from databricks.sdk.errors import NotFound
    catalog = plan["catalog"]
    # Never populate an existing catalog: fixtures are isolated and non-destructive.
    try:
        client.catalogs.get(catalog)
    except NotFound:
        client.catalogs.create(catalog, comment="ORION empty XML source-schema fixtures. Not live source data.")
    else:
        raise ValueError("Fixture catalog already exists; refusing to alter existing objects")
    schemas = sorted({t["schema"] for t in plan["tables"]})
    for schema in schemas:
        client.schemas.create(schema, catalog, comment="XML namespace fixture; see binding manifest for original identity")
    def create(table):
        execute(client, warehouse, table_sql(plan, table))
        return table["schema"], table["table"]
    completed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for _ in pool.map(create, plan["tables"]):
            completed += 1
            if completed % 25 == 0:
                print(f"Created {completed}/{len(plan['tables'])} empty tables", flush=True)
    # Independent warehouse metadata comparison of every table's column count.
    query = (f"SELECT table_schema,table_name,COUNT(*) FROM {identifier(catalog)}.information_schema.columns "
             "WHERE table_schema <> 'information_schema' GROUP BY table_schema,table_name")
    result = execute(client, warehouse, query)
    rows = result.result.data_array or []
    actual = {(r[0], r[1]): int(r[2]) for r in rows}
    expected = {(t["schema"], t["table"]): len(t["columns"]) for t in plan["tables"]}
    if result.result.next_chunk_index is not None or actual != expected:
        raise RuntimeError("Table/column inventory verification incomplete or mismatched")
    return {"tables_created": completed, "columns_verified": sum(actual.values()),
            "schemas_created": len(schemas), "metadata_verification": "passed", "data_loaded": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--volume-directory", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = plan_sources(args.xml, args.catalog)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "source-schema-manifest.json"
    manifest.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (args.output / "source-tables.sql").write_text(";\n\n".join(table_sql(plan, t) for t in plan["tables"]) + ";\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in plan.items() if k != "tables"}), flush=True)
    if args.apply:
        from workbench.databricks_models import profile_identity
        identity = profile_identity()
        print("Using authenticated CLI OAuth user ID " + identity.user_id, flush=True)
        receipt = provision(identity.client, args.warehouse, plan)
        receipt.update(user_id=identity.user_id, catalog=args.catalog, package_sha256=plan["package_sha256"])
        (args.output / "creation-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        if not args.volume_directory.startswith("/Volumes/") or ".." in args.volume_directory.split("/"):
            raise ValueError("A safe Volume destination is required")
        identity.client.files.create_directory(args.volume_directory)
        for file in args.output.iterdir():
            if file.name in {"source-schema-manifest.json", "source-tables.sql", "creation-receipt.json"}:
                identity.client.files.upload(args.volume_directory + "/" + file.name, io.BytesIO(file.read_bytes()), overwrite=False)
        print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()