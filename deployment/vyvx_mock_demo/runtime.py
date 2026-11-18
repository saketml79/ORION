"""Reusable serverless SQL/Python synthetic pilot runtime. Never import-executes SQL.

This finite demo explicitly mocks external procedures, sequence, dependency data,
email and transaction boundaries. XML-informed expected targets are not Oracle parity.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re

MARKER = "orion.vyvx.mock.v1"
OWNER = "ssuman@bpcs.com"
CATALOGS = {f"orion_{layer}_test_ssuman" for layer in ("bronze", "silver", "gold")}
SCHEMA = "vyvx_mock_demo"
DESTINATIONS = {"AE2E_BILL_TAMER_PREP", "AE2E_VBUS_PREP", "AE2E_JOB_LOG", "AE2E_UNIX_JOB_LOG",
                "mock_email_receipts", "mock_dependency_receipts", "mock_log_check_receipts"}

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Unsafe three-part identifier")
    catalog, schema, _ = value.split(".")
    if catalog not in CATALOGS or schema != SCHEMA:
        raise ValueError("Only pinned synthetic catalogs/schema are permitted")
    return ".".join(f"`{part}`" for part in value.split("."))

def normalize(value, dtype):
    if value is None:
        return None
    if dtype.startswith("DECIMAL"):
        scale = int(dtype.split(",")[1][:-1])
        with localcontext() as ctx:
            ctx.prec = 50
            return format(Decimal(str(value)).quantize(Decimal(1).scaleb(-scale)), "f")
    if dtype == "TIMESTAMP":
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is not None:
            from datetime import timezone
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ", timespec="microseconds")
    return str(value)

def canonical_rows(rows, columns):
    return sorted(json.dumps([normalize(row[name], dtype) for name, dtype in columns],
                             separators=(",", ":"), ensure_ascii=True) for row in rows)

def summary(rows, columns):
    data = canonical_rows(rows, columns)
    return {"count": len(data), "sha256": hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest(),
            "columns": [list(c) for c in columns]}

def table(c, name):
    return identifier(c["tables"][name]["name"])

def guard(spark, c, name, *, destination=False):
    spec = c["tables"][name]
    if c["marker"] != MARKER or c["owner"] != OWNER or c["schema"] != SCHEMA:
        raise ValueError("Invalid mock contract")
    if destination and (name not in DESTINATIONS or spec["role"] != "destination"):
        raise ValueError("Source or unknown table cannot be mutated")
    catalog, schema, obj = spec["name"].split(".")
    identifier(spec["name"])
    row = spark.sql(f"SELECT table_owner FROM `{catalog}`.information_schema.tables WHERE table_schema=:schema AND table_name=:name",
                    args={"schema": schema, "name": obj.lower()}).first()
    if row is None or row["table_owner"] != OWNER:
        raise RuntimeError("Unowned or missing demo table")
    props = {r["key"]: r["value"] for r in spark.sql(f"SHOW TBLPROPERTIES {table(c, name)}").collect()}
    if props.get("orion.mock") != MARKER or props.get("orion.package_sha256") != c["package_sha256"]:
        raise RuntimeError("Missing/mismatched demo table marker")
    schema_props = {r[0].strip().lower(): r[1] for r in spark.sql(f"DESCRIBE SCHEMA EXTENDED `{catalog}`.`{schema}`").collect()}
    if schema_props.get("owner") != OWNER or schema_props.get("comment") != MARKER:
        raise RuntimeError("Missing/mismatched demo schema owner/marker")

def mock_truncate_table(spark, c, name):
    """Approved MOCK replacement, not the missing AE2E_COMMON procedure body."""
    guard(spark, c, name, destination=True)
    spark.sql(f"TRUNCATE TABLE {table(c, name)}").collect()

def bounded_rows(spark, c, name):
    df = spark.sql(f"SELECT * FROM {table(c, name)}")
    actual = [(f.name, f.dataType.simpleString().upper()) for f in df.schema.fields]
    expected = [tuple(col) for col in c["tables"][name]["columns"]]
    if actual != expected:
        raise AssertionError(f"Typed schema mismatch: {name}: {actual} != {expected}")
    rows = [r.asDict() for r in df.limit(51).collect()]
    if len(rows) > 50:
        raise AssertionError("Synthetic table exceeds 50-row cap")
    return rows

def sources_snapshot(spark, c):
    snapshots = {}
    for name, spec in c["tables"].items():
        if spec["role"] == "source":
            guard(spark, c, name)
            rows = bounded_rows(spark, c, name)
            if canonical_rows(rows, spec["columns"]) != canonical_rows(spec["rows"], spec["columns"]):
                raise AssertionError(f"Source fixture changed: {name}")
            snapshots[name] = summary(rows, spec["columns"])
    return snapshots

def append_receipt(spark, c, name, run_id, phase, payload):
    guard(spark, c, name, destination=True)
    spark.sql(f"INSERT INTO {table(c, name)} VALUES (:run_id, :phase, :payload)",
              args={"run_id": run_id, "phase": phase, "payload": json.dumps(payload, sort_keys=True)}).collect()

def log_count(spark, c, job_name):
    return int(spark.sql(f"""SELECT count(*) AS n FROM {table(c, 'AE2E_JOB_LOG')} j
      WHERE j.run_id=(SELECT u.run_id FROM {table(c, 'AE2E_UNIX_JOB_LOG')} u
       WHERE u.template_id=(SELECT template_id FROM {table(c, 'mock_template')}
                             WHERE template_name='INFA_VBUS_HIST_PREP_PULL')
       AND u.run_date=CAST(:run_date AS TIMESTAMP))
       AND j.job_name=:job_name AND j.end_time IS NOT NULL""",
      args={"run_date": c["run_date"], "job_name": job_name}).first()["n"])

def start_log(spark, c, job_name, run_id):
    for name in ("AE2E_UNIX_JOB_LOG", "AE2E_JOB_LOG"):
        guard(spark, c, name, destination=True)
    spark.sql(f"""INSERT INTO {table(c, 'AE2E_UNIX_JOB_LOG')}
        SELECT CAST(:run_id AS DECIMAL(38,0)), CAST(:template AS DECIMAL(38,0)),
          CAST(:run_date AS TIMESTAMP), current_timestamp(), CAST(NULL AS TIMESTAMP)
        WHERE NOT EXISTS (SELECT 1 FROM {table(c, 'AE2E_UNIX_JOB_LOG')}
          WHERE template_id=CAST(:template AS DECIMAL(38,0)) AND run_date=CAST(:run_date AS TIMESTAMP))""",
        args={"run_id": run_id, "template": c["template_id"], "run_date": c["run_date"]}).collect()
    spark.sql(f"""INSERT INTO {table(c, 'AE2E_JOB_LOG')}
        SELECT run_id,:job_name,current_timestamp(),CAST(NULL AS TIMESTAMP)
        FROM {table(c, 'AE2E_UNIX_JOB_LOG')} u
        WHERE template_id=CAST(:template AS DECIMAL(38,0)) AND run_date=CAST(:run_date AS TIMESTAMP)
        AND NOT EXISTS (SELECT 1 FROM {table(c, 'AE2E_JOB_LOG')} j WHERE j.run_id=u.run_id AND j.job_name=:job_name)""",
        args={"template": c["template_id"], "run_date": c["run_date"], "job_name": job_name}).collect()

def end_log(spark, c, job_name, *, final=False):
    guard(spark, c, "AE2E_JOB_LOG", destination=True)
    spark.sql(f"""UPDATE {table(c, 'AE2E_JOB_LOG')} SET end_time=current_timestamp()
        WHERE job_name=:job_name AND run_id=(SELECT run_id FROM {table(c, 'AE2E_UNIX_JOB_LOG')}
          WHERE template_id=CAST(:template AS DECIMAL(38,0)) AND run_date=CAST(:run_date AS TIMESTAMP))""",
        args={"job_name": job_name, "template": c["template_id"], "run_date": c["run_date"]}).collect()
    if final:
        guard(spark, c, "AE2E_UNIX_JOB_LOG", destination=True)
        spark.sql(f"UPDATE {table(c, 'AE2E_UNIX_JOB_LOG')} SET end_time=current_timestamp() WHERE template_id=CAST(:template AS DECIMAL(38,0)) AND run_date=CAST(:run_date AS TIMESTAMP)",
                  args={"template": c["template_id"], "run_date": c["run_date"]}).collect()

def projection(c, key, target, expressions):
    parts = []
    for name, dtype in c["tables"][target]["columns"]:
        port = c["projections"][key].get(name)
        value = expressions[port] if port is not None else "NULL"
        parts.append(f"CAST({value} AS {dtype}) AS `{name}`")
    return ",\n".join(parts)

def mock_get_product_key_id(spark, c):
    """Finite deterministic ID lookup using mocked external key/system relations.
    Missing keys fail, never allocate production IDs or silently drop source rows.
    """
    sql = f"""SELECT b.*, k.ae2e_key_id AS mock_key FROM {table(c, 'AE2E_BTP_STG3')} b
        LEFT JOIN {table(c, 'mock_system')} s ON s.system_name='VBUS'
        LEFT JOIN {table(c, 'mock_product_keys')} k ON k.system_id=s.system_id
         AND k.src_system_num_id=concat(coalesce(b.ecckt_stripped,''),':',coalesce(b.ban,''))
         AND k.run_date=CAST(:run_date AS TIMESTAMP) AND k.mode_id=-1
        WHERE b.bill_date>=TIMESTAMP '2006-05-28 00:00:00'
         AND (b.gl_code LIKE '761%' OR b.company='133')"""
    df = spark.sql(sql, args={"run_date": c["run_date"]})
    if df.filter("mock_key IS NULL").limit(1).count():
        raise AssertionError("Missing synthetic product-key fixture")
    df.createOrReplaceTempView("vyvx_btp_keyed")

def load_btp(spark, c, run_id):
    start_log(spark, c, "MAP_VBUS_BTP_PREP", run_id)
    mock_truncate_table(spark, c, "AE2E_BILL_TAMER_PREP")
    mock_get_product_key_id(spark, c)
    ports = {port: f"b.`{port}`" for port in c["projections"]["btp"].values() if not port.startswith("out_")}
    ports.update(out_AE2E_KEY_ID="b.mock_key", out_TEMPLATE_ID="CAST(:template AS DECIMAL(38,0))", out_PULL_CRITERIA="'BTP:ORIGIN'")
    values = projection(c, "btp", "AE2E_BILL_TAMER_PREP", ports)
    spark.sql(f"INSERT INTO {table(c, 'AE2E_BILL_TAMER_PREP')} SELECT {values} FROM vyvx_btp_keyed b",
              args={"template": c["template_id"]}).collect()
    written = len(bounded_rows(spark, c, "AE2E_BILL_TAMER_PREP"))
    # Exact captured deletion list and 8-prefix, scoped to the mock template.
    codes = ",".join("'" + value + "'" for value in c["btp_gl_delete_codes"])
    for predicate in (f"gl_code IN ({codes})", "gl_code LIKE '8%'"):
        spark.sql(f"DELETE FROM {table(c, 'AE2E_BILL_TAMER_PREP')} WHERE ({predicate}) AND template_id=CAST(:template AS DECIMAL(38,0))",
                  args={"template": c["template_id"]}).collect()
    end_log(spark, c, "MAP_VBUS_BTP_PREP")
    return {"target_success_rows": written, "final_rows": len(bounded_rows(spark, c, "AE2E_BILL_TAMER_PREP")), "mock_procedure": True}

def load_vbus(spark, c, run_id):
    start_log(spark, c, "MAP_VBUS_HIST_PREP", run_id)
    mock_truncate_table(spark, c, "AE2E_VBUS_PREP")
    # The exact SQ SELECT DISTINCT consumes all 39 VBUS columns, including
    # unconnected comments. Distinct is before target projection, not after.
    select = []
    date_strings = {"CUSTOMER_BILL_END_DATE", "CUSTOMER_BILL_START_DATE", "INVOICE_DATE"}
    for name, _ in c["tables"]["AE2E_VBUS_STG"]["columns"]:
        expr = f"upper(date_format(v.`{name}`, 'dd-MMM-yyyy'))" if name in date_strings else (
               f"CAST(v.`{name}` AS STRING)" if name in {"MRC_CUSTOMER", "NRC_CUSTOMER"} else f"v.`{name}`")
        select.append(f"{expr} AS `{name}`")
    spark.sql(f"SELECT DISTINCT {','.join(select)} FROM {table(c, 'AE2E_VBUS_STG')} v").createOrReplaceTempView("vyvx_vbus_sq")
    counts = {}
    for key in ("ecckt", "l3"):
        if key == "ecckt":
            prep = f"SELECT DISTINCT ae2e_key_id,template_id,ecckt_stripped FROM {table(c, 'AE2E_BILL_TAMER_PREP')} WHERE template_id=CAST(:template AS DECIMAL(38,0))"
            join = "p.ecckt_stripped=v.ecckt_stripped"
        else:
            prep = f"""SELECT DISTINCT a.ae2e_key_id,a.template_id,
              upper(regexp_replace(a.l3_circuit_id,'[^a-zA-Z0-9]','')) AS l3_circuit_id
              FROM {table(c, 'AE2E_BILL_TAMER_PREP')} a WHERE a.template_id=CAST(:template AS DECIMAL(38,0))
               AND NOT EXISTS (SELECT 1 FROM {table(c, 'AE2E_VBUS_PREP')} b
                  WHERE a.ae2e_key_id=b.ae2e_key_id AND a.template_id=b.template_id)"""
            join = "p.l3_circuit_id=v.l3_circuit_id"
        ports = {port: f"v.`{port}`" for port in c["projections"][key].values()}
        ports.update(AE2E_KEY_ID="p.ae2e_key_id", TEMPLATE_ID="p.template_id",
                     ECCKT_STRIPPED_PREP="p.ecckt_stripped", L3_CIRCUIT_ID1="v.l3_circuit_id",
                     out_Pull_Criteria="'BTP:ECCKT_STRPD'" if key == "ecckt" else "'BTP:L3_CKT_ID'")
        values = projection(c, key, "AE2E_VBUS_PREP", ports)
        query = f"SELECT {values} FROM ({prep}) p JOIN vyvx_vbus_sq v ON {join}"
        count = spark.sql(query, args={"template": c["template_id"]}).count()
        counts[key] = count
        spark.sql(f"INSERT INTO {table(c, 'AE2E_VBUS_PREP')} {query}", args={"template": c["template_id"]}).collect()
    # Sequential equivalents of the XML post-SQL on this mock's own target.
    target = table(c, "AE2E_VBUS_PREP")
    args = {"template": c["template_id"]}
    spark.sql(f"""DELETE FROM {target} a WHERE a.pull_criteria='BTP:L3_CKT_ID' AND a.template_id=CAST(:template AS DECIMAL(38,0))
       AND EXISTS (SELECT 1 FROM {target} b WHERE b.pull_criteria='BTP:ECCKT_STRPD' AND a.ae2e_key_id=b.ae2e_key_id AND a.template_id=b.template_id)""", args=args).collect()
    spark.sql(f"""DELETE FROM {target} o WHERE o.template_id=CAST(:template AS DECIMAL(38,0))
       AND o.order_create_date < (SELECT max(i.order_create_date) FROM {target} i
          WHERE o.template_id=i.template_id AND o.ae2e_key_id=i.ae2e_key_id)""", args=args).collect()
    spark.sql(f"""DELETE FROM {target} WHERE template_id=CAST(:template AS DECIMAL(38,0)) AND order_status='CANCEL'
       AND ae2e_key_id IN (SELECT ae2e_key_id FROM {target} WHERE template_id=CAST(:template AS DECIMAL(38,0))
         GROUP BY ae2e_key_id,order_create_date HAVING count(*)>1)""", args=args).collect()
    end_log(spark, c, "MAP_VBUS_HIST_PREP", final=True)
    return {"target_success_rows": sum(counts.values()), "branch_rows": counts,
            "final_rows": len(bounded_rows(spark, c, "AE2E_VBUS_PREP"))}

def verify(spark, dbutils, c, run_id):
    after = sources_snapshot(spark, c)
    before = json.loads(dbutils.jobs.taskValues.get(taskKey="dependency", key="sources_before"))
    if before != after:
        raise AssertionError("Source checksums changed during run")
    outputs = {}
    for name in DESTINATIONS:
        rows = bounded_rows(spark, c, name)
        spec = c["tables"][name]
        if "expected" in spec and Counter(canonical_rows(rows, spec["columns"])) != Counter(canonical_rows(spec["expected"], spec["columns"])):
            raise AssertionError(f"Independent exact typed multiset mismatch: {name}")
        outputs[name] = {**summary(rows, spec["columns"]), "actual_rows":
            [[normalize(row[col], dtype) for col, dtype in spec["columns"]] for row in rows]}
    jobs = bounded_rows(spark, c, "AE2E_JOB_LOG")
    unix = bounded_rows(spark, c, "AE2E_UNIX_JOB_LOG")
    if len(jobs) != 2 or {r["JOB_NAME"] for r in jobs} != {"MAP_VBUS_BTP_PREP", "MAP_VBUS_HIST_PREP"} or len(unix) != 1:
        raise AssertionError("Invalid mock lifecycle log counts")
    if any(r["START_TIME"] is None or r["END_TIME"] is None or r["END_TIME"] < r["START_TIME"] for r in jobs + unix):
        raise AssertionError("Incomplete lifecycle timestamps")
    if any(r["RUN_ID"] != unix[0]["RUN_ID"] for r in jobs) or str(unix[0]["TEMPLATE_ID"]) != c["template_id"]:
        raise AssertionError("Invalid log correlation")
    for name in ("mock_email_receipts", "mock_dependency_receipts", "mock_log_check_receipts"):
        current = [r for r in bounded_rows(spark, c, name) if r["RUN_ID"] == run_id]
        expected = 2 if name == "mock_log_check_receipts" else 1
        if len(current) != expected:
            raise AssertionError("Missing/duplicate phase receipts")
        payloads = [json.loads(r["PAYLOAD"]) for r in current]
        if name == "mock_email_receipts" and payloads[0].get("delivery") != "disabled_mock":
            raise AssertionError("Unexpected email delivery")
        if name == "mock_dependency_receipts" and payloads[0] != {"remaining": 0, "queries": 1, "sleeps": 0, "mock": True}:
            raise AssertionError("Unexpected dependency behavior")
    return {"status": "PASS_MOCK_EXPECTATIONS_ONLY", "run_id": run_id,
        "package_sha256": c["package_sha256"], "sources_before": before, "sources_after": after,
        "outputs": outputs, "oracle_informatica_parity": False, "production_ready": False,
        "validated": ["all XML target types/columns", "independent full typed multisets including nulls/duplicates",
                      "positive/negative source filters", "ECCKT/L3/unmatched sentinels", "post-SQL deletion",
                      "mock keys", "lifecycle log counts/correlation/times", "source hashes unchanged", "mock email/dependency"]}

def execute(spark, dbutils, phase, ack, run_id, workspace_path):
    if ack != "MOCK_ONLY" or not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("MOCK_ONLY acknowledgment and native Job run ID required")
    c = json.loads((Path(workspace_path) / "contract.json").read_text(encoding="utf-8"))
    if spark.sql("SELECT current_user() AS owner").first()["owner"] != OWNER:
        raise RuntimeError("Unexpected execution identity")
    spark.sql("SET TIME ZONE 'UTC'").collect()
    for name in DESTINATIONS:
        guard(spark, c, name, destination=True)
    result = {"phase": phase, "mock": True}
    if phase == "dependency":
        from dependency_check import run_spark_dependency_check
        snapshot = sources_snapshot(spark, c)
        dbutils.jobs.taskValues.set(key="sources_before", value=json.dumps(snapshot, sort_keys=True))
        # Ready fixture must remain ready; fail immediately rather than actually
        # sleeping 270 minutes if it is changed. Polling algebra tested virtually.
        def no_real_sleep(_):
            raise AssertionError("Synthetic dependency unexpectedly missing")
        poll = run_spark_dependency_check(spark,
            dependency_table=c["tables"]["mock_dependency"]["name"],
            template_table=c["tables"]["mock_template"]["name"],
            job_log_table=c["tables"]["mock_dependency_completion"]["name"],
            template_name="INFA_VBUS_HIST_PREP_PULL", job_run_date="11072026", sleep=no_real_sleep)
        payload = {"remaining": poll.remaining, "queries": poll.queries, "sleeps": poll.sleeps, "mock": True}
        append_receipt(spark, c, "mock_dependency_receipts", run_id, phase, payload)
        result.update(payload)
    elif phase in {"btp_log_check", "vbus_log_check"}:
        job_name = "MAP_VBUS_BTP_PREP" if phase.startswith("btp") else "MAP_VBUS_HIST_PREP"
        n = log_count(spark, c, job_name)
        dbutils.jobs.taskValues.set(key="needs_load", value="true" if n == 0 else "false")
        dbutils.jobs.taskValues.set(key="completed_rows", value=n)
        append_receipt(spark, c, "mock_log_check_receipts", run_id, phase, {"target_success_rows": n, "mock_file_replacement": True})
        result.update(target_success_rows=n, needs_load=n == 0)
    elif phase in {"btp_load", "vbus_load"}:
        result.update((load_btp if phase == "btp_load" else load_vbus)(spark, c, run_id))
        dbutils.jobs.taskValues.set(key="target_success_rows", value=result["target_success_rows"])
        dbutils.jobs.taskValues.set(key="load_succeeded", value=True)
    elif phase in {"btp_merge", "vbus_merge"}:
        prefix = phase.split("_")[0]
        n = int(dbutils.jobs.taskValues.get(taskKey=f"{prefix}_log_check", key="completed_rows", default=-1))
        succeeded = dbutils.jobs.taskValues.get(taskKey=f"{prefix}_load", key="load_succeeded", default=False)
        loaded_rows = int(dbutils.jobs.taskValues.get(taskKey=f"{prefix}_load", key="target_success_rows", default=0))
        eligible = n > 0 or (succeeded and (prefix == "btp" or loaded_rows > 0))
        if not eligible:
            raise RuntimeError("Source decision OR merge has no successful eligible predecessor")
        result.update(completed_log_rows=n, loaded_rows=loaded_rows, decision="SUCCEEDED")
    elif phase == "email_mock":
        append_receipt(spark, c, "mock_email_receipts", run_id, phase,
            {"delivery": "disabled_mock", "subject": "MOCK VYVX prep success", "workflow": c["workflow"], "real_recipients": []})
        result["delivery"] = "disabled_mock"
    elif phase == "verify":
        return verify(spark, dbutils, c, run_id)
    else:
        raise ValueError("Unknown demo phase")
    return result