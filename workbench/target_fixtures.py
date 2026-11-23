"""Deterministic TARGET/TARGETFIELD planner, separate from source fixtures.

Only empty, CREATE-only dedicated test destinations. Never rewrites the
previous source catalog or forces a mapping through three transformation layers.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from defusedxml.ElementTree import iterparse

from workbench.source_fixtures import fixture_namespace, identifier, mock_type


def target_table(attributes, fields, scope):
    identity = [scope.get("repository", ""), scope.get("folder", ""),
                attributes.get("DATABASETYPE", ""), attributes.get("DBDNAME", ""),
                attributes.get("OWNERNAME", ""), attributes["NAME"]]
    schema, table = fixture_namespace(identity)
    schema = "tgt_" + schema[4:]
    columns = []
    for field in fields:
        identifier(field["NAME"])
        if field.get("NULLABLE") not in {"NOTNULL", "NULL"}:
            raise ValueError("Explicit target nullability required")
        # Flat-file double is an IEEE floating target, not DECIMAL(38,0).
        # Keep source fixture behavior unchanged and retain the original type.
        kind = ("DOUBLE" if field["DATATYPE"].lower() == "double"
            and attributes.get("DATABASETYPE") == "Flat File"
            else mock_type(field, attributes.get("DATABASETYPE", "")))
        columns.append({"name": field["NAME"], "type": kind,
                        "nullable": field["NULLABLE"] != "NOTNULL",
                        "xml_type": field["DATATYPE"], "precision": field["PRECISION"],
                        "scale": field["SCALE"], "key_type": field.get("KEYTYPE", "not_captured"),
                        "character_width": field["PRECISION"] if kind == "STRING" else None,
                        "character_width_unit": "unverified", "constraints_verified": False})
    if not columns or len({c["name"].casefold() for c in columns}) != len(columns):
        raise ValueError("Missing or colliding target columns")
    return {"xml_identity": identity, "schema": schema, "table": table, "columns": columns,
            "binding_status": "test_fixture_only_unapproved_target"}


def _checked(tables):
    identities, names = set(), set()
    for table in tables:
        identity = json.dumps(table["xml_identity"], separators=(",", ":"))
        name = (table["schema"].casefold(), table["table"].casefold())
        if identity in identities or name in names:
            raise ValueError("Duplicate target identity or destination collision")
        identities.add(identity)
        names.add(name)
    return tables


def plan_targets(path, catalog):
    identifier(catalog)
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    stack, tables = [], []
    for event, elem in iterparse(path, events=("start", "end"), forbid_entities=True, forbid_external=True):
        tag = elem.tag.rsplit("}", 1)[-1]
        if event == "start":
            stack.append((tag, dict(elem.attrib)))
            continue
        if tag == "TARGET":
            scope = {k.lower(): a.get("NAME", "") for k, a in stack if k in {"REPOSITORY", "FOLDER"}}
            fields = [dict(c.attrib) for c in elem if c.tag.rsplit("}", 1)[-1] == "TARGETFIELD"]
            tables.append(target_table(dict(elem.attrib), fields, scope))
        if not any(t == "TARGET" for t, _ in stack[:-1]):
            elem.clear()
        stack.pop()
    return {"version": 1, "catalog": catalog, "package_sha256": digest.hexdigest(),
            "tables": _checked(tables), "data_loaded": False,
            "warnings": ["Character widths, byte/character semantics, padding and collation need owner verification.",
                         "Oracle constraints, DATE clock, procedure effects and key allocation are not certified."]}


def catalog_names(user_label, campaign_id):
    if not isinstance(user_label, str) or not re.fullmatch(r"[a-f0-9]{32}", campaign_id):
        raise ValueError("Invalid test namespace input")
    readable = re.sub(r"[^a-z0-9]+", "_", user_label.split("@")[0].lower()).strip("_")[:24] or "user"
    owner = sha256(user_label.encode()).hexdigest()[:8]
    prefix = f"orion_test_{readable}_{owner}_{campaign_id[:12]}"
    return {layer: prefix + "_" + layer for layer in ("bronze", "silver", "gold")}


def target_sql(catalog, table, package_hash):
    identifier(catalog)
    if not re.fullmatch(r"[a-f0-9]{64}", package_hash):
        raise ValueError("Invalid package fingerprint")
    columns = ", ".join(identifier(c["name"]) + " " + c["type"] + ("" if c["nullable"] else " NOT NULL")
                        for c in table["columns"])
    # Types come exclusively from the deterministic planner, not request input.
    for c in table["columns"]:
        if not re.fullmatch(r"STRING|BINARY|TIMESTAMP_NTZ|INT|SMALLINT|DOUBLE|DECIMAL\([0-9]+,[0-9]+\)", c["type"]):
            raise ValueError("Unsafe destination type")
    name = ".".join(identifier(v) for v in (catalog, table["schema"], table["table"]))
    return (f"CREATE TABLE {name} ({columns}) USING DELTA TBLPROPERTIES "
            f"('orion.fixture'='xml_target_schema_only','orion.package_sha256'='{package_hash}')")