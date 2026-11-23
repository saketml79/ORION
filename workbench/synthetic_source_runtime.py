"""Standalone stdlib-only runtime imported beside the serverless notebook.

SQL/DataFrame APIs only. Never creates, alters, replaces or deletes UC objects.
Only module-generated INSERT SQL is executed. Imported plans are validated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

CATALOG = "infotodbxmig_src_dev_ssuman"
FORMAT = "orion-synthetic-source-data-v1"
CONCURRENCY = ("Single self-reading conditional INSERT per table; existing Serializable isolation is honored. "
               "No table properties are changed. No proven atomic empty-only guarantee against unrelated writers "
               "under other isolation levels or concurrent metadata changes. max_concurrent_runs=1 is per Job only. "
               "Counts are observations, not cross-table transactions or proof of write attribution.")


class FixtureError(ValueError):
    """Safe fixed error code, not an upstream message."""


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", value):
        raise FixtureError("unsafe_identifier")
    return "`" + value + "`"


def full_name(plan, table):
    return ".".join(identifier(v) for v in (plan["catalog"], table["schema"], table["table"]))


def validate_value(column, value):
    kind = column["type"]
    if value is None:
        if not column["nullable"]:
            raise FixtureError("null_in_required_column")
        return
    if kind == "STRING":
        width = int(column["precision"])
        if not isinstance(value, str) or len(value) > min(width if width > 0 else 64, 4000):
            raise FixtureError("string_width_mismatch")
    elif kind.startswith("DECIMAL("):
        match = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", kind)
        if match is None:
            raise FixtureError("invalid_decimal_type")
        precision, scale = map(int, match.groups())
        if not 1 <= precision <= 38 or not 0 <= scale <= precision:
            raise FixtureError("invalid_decimal_type")
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise FixtureError("invalid_decimal")
            # Exact coefficient arithmetic, independent of Decimal context (28).
            exponent = number.as_tuple().exponent
            if not isinstance(exponent, int):
                raise FixtureError("invalid_decimal")
            fractional = max(0, -exponent)
            if fractional > scale or number.copy_abs() >= Decimal(10) ** (precision - scale):
                raise FixtureError("decimal_bounds_mismatch")
        except (InvalidOperation, TypeError):
            raise FixtureError("invalid_decimal") from None
    elif kind in {"INT", "SMALLINT"}:
        limit = 2147483647 if kind == "INT" else 32767
        if type(value) is not int or not -limit - 1 <= value <= limit:
            raise FixtureError("integer_bounds_mismatch")
    elif kind == "DOUBLE":
        if type(value) not in {int, float} or not -1000000 <= value <= 1000000:
            raise FixtureError("double_bounds_mismatch")
    elif kind == "TIMESTAMP_NTZ":
        try:
            if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is not None:
                raise FixtureError("timestamp_mismatch")
        except ValueError:
            raise FixtureError("timestamp_mismatch") from None
    elif kind == "BINARY":
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{2}){1,64}", value):
            raise FixtureError("binary_mismatch")
    else:
        raise FixtureError("unsupported_type")


def validate_plan(plan, expected_hash=None):
    if (plan.get("format") != FORMAT or plan.get("catalog") != CATALOG
            or not re.fullmatch(r"[0-9a-f]{64}", plan.get("package_sha256", ""))
            or plan.get("plan_sha256") != digest({k: v for k, v in plan.items() if k != "plan_sha256"})
            or expected_hash is not None and plan["plan_sha256"] != expected_hash):
        raise FixtureError("plan_scope_or_hash_mismatch")
    tables = plan.get("tables", [])
    if not tables or len(tables) != plan["table_count"]:
        raise FixtureError("plan_inventory_mismatch")
    seen = set()
    for table in tables:
        name = full_name(plan, table)
        if name in seen:
            raise FixtureError("duplicate_table")
        seen.add(name)
        identity = table["xml_identity"]
        if len(identity) != 6 or any(not isinstance(v, str) or v == "[REDACTED]" for v in identity):
            raise FixtureError("invalid_xml_identity")
        namespace_hash = sha256(json.dumps(identity[:-1], separators=(",", ":")).encode()).hexdigest()[:12]
        readable = re.sub(r"[^a-z0-9_]+", "_", "_".join(identity[2:5]).lower()).strip("_")[:72] or "unspecified"
        if table["schema"] != "src_" + readable + "_" + namespace_hash or table["table"] != identity[-1].lower():
            raise FixtureError("namespace_mismatch")
        column_names = []
        for column in table["columns"]:
            identifier(column["name"])
            if type(column["nullable"]) is not bool or not re.fullmatch(
                    r"STRING|INT|SMALLINT|DOUBLE|BINARY|TIMESTAMP_NTZ|DECIMAL\((?:[1-9]|[12][0-9]|3[0-8]),(?:[0-9]|[12][0-9]|3[0-8])\)", column["type"]):
                raise FixtureError("invalid_column_type")
            if column["type"].startswith("DECIMAL"):
                p, s = map(int, re.findall(r"\d+", column["type"]))
                if s > p:
                    raise FixtureError("invalid_column_type")
            column_names.append(column["name"].casefold())
        if not column_names or len(set(column_names)) != len(column_names):
            raise FixtureError("invalid_columns")
        rows = table["synthetic_rows"]
        if not 1 <= len(rows) <= 1000:
            raise FixtureError("row_budget_mismatch")
        for row in rows:
            if len(row) != len(table["columns"]):
                raise FixtureError("row_schema_mismatch")
            for column, value in zip(table["columns"], row):
                validate_value(column, value)


def sql_value(column, value):
    validate_value(column, value)
    if value is None:
        text = "NULL"
    elif column["type"] == "BINARY":
        text = "unhex('" + value + "')"
    else:
        # All non-null scalars are string literals cast to validated XML types.
        # Escaping backslashes also prevents Spark escaped-string injection.
        text = "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"
    return "CAST(" + text + " AS " + column["type"] + ")"


def insert_statement(plan, table):
    name = full_name(plan, table)
    cols = ", ".join(identifier(c["name"]) for c in table["columns"])
    rows = ",\n".join("(" + ", ".join(sql_value(c, v) for c, v in zip(table["columns"], row)) + ")"
                      for row in table["synthetic_rows"])
    return (f"INSERT INTO {name} ({cols}) SELECT {cols} FROM VALUES\n{rows}\n"
            f"AS synthetic_fixture ({cols}) WHERE NOT EXISTS (SELECT 1 FROM {name})")


def observe(spark, plan, table):
    name = full_name(plan, table)
    detail = spark.sql("DESCRIBE DETAIL " + name).first().asDict(recursive=True)
    properties = detail.get("properties") or {}
    if (str(detail.get("format", "")).lower() != "delta" or not detail.get("id")
            or properties.get("orion.fixture") != "xml_source_schema_only"
            or properties.get("orion.package_sha256") != plan["package_sha256"]):
        raise FixtureError("fixture_marker_or_delta_mismatch")
    actual = [(f.name, f.dataType.simpleString().upper().replace("INTEGER", "INT"), f.nullable)
              for f in spark.table(name).schema.fields]
    expected = [(c["name"], c["type"], c["nullable"]) for c in table["columns"]]
    if actual != expected:
        raise FixtureError("exact_schema_mismatch")
    return {"delta_table_id": detail["id"],
            "isolation": properties.get("delta.isolationLevel", "WriteSerializable (default, not verified live)")}


def run(spark, plan, receipt_directory, *, expected_hash=None, expected_user=None, require_serializable=False):
    """Fail closed, persist partial progress, never retry a failed write.

    Receipts contain safe codes only. A commit may succeed even if an API/count
    subsequently fails: such entries explicitly retain commit uncertainty.
    """
    directory = Path(receipt_directory)
    receipt = {"format": FORMAT + "-receipt", "status": "started", "tables": [], "errors": [],
               "concurrency_boundary": CONCURRENCY, "relationship_parity": "not_established",
               "business_parity": "not_established", "plan_sha256": plan.get("plan_sha256"),
               "package_sha256": plan.get("package_sha256"), "catalog": plan.get("catalog")}
    sequence = 0

    def persist(filename):
        receipt["updated_at"] = datetime.now(timezone.utc).isoformat()
        with (directory / filename).open("x", encoding="utf-8") as output:
            json.dump(receipt, output, sort_keys=True, indent=2)
            output.flush()

    def checkpoint():
        nonlocal sequence
        sequence += 1
        persist(f"receipt-{sequence:05d}.json")

    current = None
    try:
        persist("receipt-start.json")  # Prove receipt path writable before SQL.
        validate_plan(plan, expected_hash)
        if expected_user is not None and spark.sql("SELECT current_user() AS principal").first()["principal"] != expected_user:
            raise FixtureError("run_as_identity_mismatch")
        receipt["tables"] = [{"schema": t["schema"], "table": t["table"], "count_before": None,
                              "count_after": None, "skipped": False, "inserted": None,
                              "status": "not_processed", "errors": [], "commit_uncertain": False}
                             for t in plan["tables"]]
        # Verify ALL markers/schemas before the first write, not just each table
        # lazily after earlier tables have already been populated.
        for table, entry in zip(plan["tables"], receipt["tables"]):
            current = entry
            entry.update(observe(spark, plan, table))
            if require_serializable and entry["isolation"].casefold() != "serializable":
                raise FixtureError("serializable_required_not_available")
        checkpoint()
        for table, entry in zip(plan["tables"], receipt["tables"]):
            current = entry
            name = full_name(plan, table)
            before = int(spark.sql("SELECT COUNT(*) AS n FROM " + name).first()["n"])
            entry["count_before"] = before
            if before:
                entry.update(count_after=before, skipped=True, inserted=0, status="skipped_populated")
                checkpoint()
                continue
            fresh = observe(spark, plan, table)
            if fresh != {k: entry[k] for k in ("delta_table_id", "isolation")}:
                raise FixtureError("metadata_changed_before_write")
            entry.update(status="insert_requested", commit_uncertain=True)
            checkpoint()
            output = spark.sql(insert_statement(plan, table)).collect()
            # Do not attribute count-after minus count-before to this Job.
            metrics = output[0].asDict() if output else {}
            affected = metrics.get("num_inserted_rows", metrics.get("num_affected_rows"))
            entry["inserted"] = int(affected) if affected is not None else None
            after = int(spark.sql("SELECT COUNT(*) AS n FROM " + name).first()["n"])
            entry["count_after"] = after
            entry["commit_uncertain"] = False
            if affected == 0:
                entry.update(skipped=True, status="skipped_nonempty_at_statement_snapshot")
            elif after != len(table["synthetic_rows"]) or affected is not None and int(affected) != len(table["synthetic_rows"]):
                raise FixtureError("post_write_count_mismatch_possible_concurrency")
            else:
                entry["status"] = "insert_completed_count_observed"
            checkpoint()
        current = None
        receipt["status"] = "completed"
    except BaseException as error:
        code = str(error) if isinstance(error, FixtureError) else "runtime_failure_" + type(error).__name__
        receipt["status"] = "failed"
        receipt["errors"].append(code)
        if current is not None:
            current["errors"].append(code)
            current["status"] = "failed"
        raise FixtureError(code) from None
    finally:
        # If this fails, the notebook fails too. Earlier immutable checkpoints
        # remain recoverable; the caller must never report a missing receipt as
        # successful population. Workspace flush/export must be verified.
        persist("receipt-final.json")
    return receipt


def run_file(spark, plan_path, receipt_directory, *, expected_hash, expected_user, require_serializable=False):
    """Persist a safe failure even when JSON loading fails before run()."""
    try:
        with Path(plan_path).open(encoding="utf-8") as stream:
            plan = json.load(stream)
        if not isinstance(plan, dict):
            raise FixtureError("plan_object_required")
    except Exception:
        with (Path(receipt_directory) / "receipt-final.json").open("x", encoding="utf-8") as output:
            json.dump({"format": FORMAT + "-receipt", "status": "failed", "catalog": CATALOG,
                       "plan_sha256": expected_hash, "tables": [], "errors": ["plan_read_failed"],
                       "relationship_parity": "not_established", "business_parity": "not_established"}, output)
        raise FixtureError("plan_read_failed") from None
    return run(spark, plan, receipt_directory, expected_hash=expected_hash, expected_user=expected_user,
               require_serializable=require_serializable)