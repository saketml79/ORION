"""One reviewed Java dependency check, not a general Java or mapping compiler.

Compilation requires the complete canonical mapping fingerprint, including the
original Java hash sidecar. Older redacted artifacts must be explicitly rebuilt.
Returned programs are candidates only. Compilation never runs SQL or sleeps.
"""
from collections import defaultdict
from hashlib import sha256
import json

from workbench.demo_conversion import _digest, _without_annotations
from workbench.domain import _RELATIONS


MAPPING_ID = "15c33b500c7921cbf0e19f82afb883eabb7e11d7c8955d0ebd03c94f4e22d528"
MAPPING_SHA256 = "d6396df9601a2f8a5160c9b284d77062ed91fa2bd4dfb2433d32ecc936816150"
JAVA_SHA256 = "440b5d8e78c4c8bdb300cab35bd59ff40b0f1561cfbb2b718923d61402140c49"


def definition_scope(model, root_id):
    """Complete canonical definition closure, never enter another workflow."""
    nodes = {n["id"]: n for n in model["nodes"]}
    if len(nodes) != len(model["nodes"]) or root_id not in nodes:
        raise ValueError("Missing or duplicate canonical identity")
    out = defaultdict(list)
    for edge in model["edges"]:
        if edge["kind"] in _RELATIONS:
            out[edge["source"]].append(edge["target"])
    seen, pending = {root_id}, [root_id]
    while pending:
        for target in out[pending.pop()]:
            if target not in nodes:
                raise ValueError("Dangling canonical reference")
            if target not in seen and nodes[target]["kind"] != "workflow":
                seen.add(target)
                pending.append(target)
    return {"nodes": sorted((nodes[i] for i in seen), key=lambda n: n["id"]),
            "edges": sorted((e for e in model["edges"] if e["source"] in seen
                             and e["target"] in seen), key=lambda e: e["id"])}


def compile_dependency_check(model):
    scope = definition_scope(model, MAPPING_ID)
    fingerprint = _digest(_without_annotations(scope))
    if fingerprint != MAPPING_SHA256:
        raise ValueError("Unsupported dependency mapping fingerprint; rebuild/review source evidence")
    java = [p.get("embedded_code", {}) for n in scope["nodes"] for p in n.get("properties", [])
            if p.get("embedded_code", {}).get("source_sha256") == JAVA_SHA256]
    if len(java) != 1:
        raise ValueError("Pinned original Java evidence required")
    contract = {
        "compiler": "wil-dependency-check-v1", "mapping_id": MAPPING_ID,
        "mapping_sha256": fingerprint, "java_sha256": JAVA_SHA256,
        "max_wait_count": 54, "sleep_seconds": 300, "final_query_after_last_sleep": False,
        "timeout_output": "last_remaining_count", "exception_output": 1,
        "error_expression": "IIF(in_VALUE > 0, ERROR('Max Wait Time Exceeded'), 'Good')",
        "implementation_scope": "Java query/polling plus downstream error expression only",
        "session_target_writes_converted": False, "full_mapping_converted": False,
        "sql_semantics": "EXCEPT DISTINCT, scalar template subquery, exact run_date equality, end_time IS NOT NULL",
        "required_bindings": ["dependency_table", "template_table", "job_log_table", "template_name", "job_run_date"],
        "binding_contract": {
            "tables": "Explicit three-part identifiers and compatible numeric IDs; no Oracle schema substitution",
            "template_name": "Resolved literal string, not an unresolved parameter; equality/collation requires owner verification",
            "job_run_date": "Explicit MMDDYYYY or empty string for Oracle empty-string NULL; other Oracle date spellings unsupported",
            "clock": "Spark session timezone/current_date must match Oracle TRUNC(SYSDATE); clock is reevaluated each query",
            "types": "Log RUN_DATE must preserve midnight DATE/timestamp equality; never truncate/cast the log column",
            "source": "Live-equivalent refreshed tables, not frozen empty fixtures, plus scalar-subquery cardinality semantics",
        },
        "limitations": ["No JDBC resource-cleanup equivalence or connection setup translation",
                        "No general Java translation, ingestion, target writes, session ERROR reject-file or restart parity",
                        "SQL is statically parsed and offline semantics tested, not executed on Spark/Oracle"],
        "remote_execution": "not_run", "output_parity": "not_established",
        "deployment_allowed": False, "production_ready": False,
    }
    artifacts = {"modules/dependency_check.py": RUNTIME_SOURCE,
                 "dependency_contract.json": json.dumps(contract, indent=2) + "\n"}
    return {"contract": contract, "artifacts": artifacts,
            "artifact_sha256": {k: sha256(v.encode()).hexdigest() for k, v in artifacts.items()}}


# One generated implementation, exercised directly by offline tests. No SDK,
# credentials, default physical bindings, execution on import, or test substitute.
RUNTIME_SOURCE = '''"""Pinned dependency core candidate. Not a complete Informatica session.
Use run_spark_dependency_check only after separate binding/execution approval.
Caller owns Spark session lifecycle and verifies Oracle-compatible clock/types.
"""
from dataclasses import dataclass
from datetime import datetime
import re
import time

MAX_WAIT_COUNT = 54
SLEEP_SECONDS = 300

@dataclass(frozen=True)
class PollResult:
    remaining: int
    queries: int
    sleeps: int
    status: str

class DependencyFailure(RuntimeError):
    def __init__(self, result):
        super().__init__("Max Wait Time Exceeded")
        self.result = result

class DependencyTimeout(DependencyFailure):
    pass

class DependencyQueryError(DependencyFailure):
    pass

def poll_dependency_count(query_count, sleep=time.sleep):
    """Preserve the Java do/while, then exp_CheckJavaReturn ERROR behavior.
    Exception details are intentionally not copied into generated receipts.
    Python process cancellation (BaseException) is not converted into success.
    """
    queries = sleeps = 0
    result = None
    try:
        while True:
            queries += 1
            total = query_count()
            if type(total) is not int or total < 0 or total > 2147483647:
                raise ValueError("COUNT must fit the original Java integer contract")
            if total == 0:
                result = PollResult(total, queries, sleeps, "satisfied")
                break
            sleeps += 1
            sleep(SLEEP_SECONDS)
            if sleeps == MAX_WAIT_COUNT:
                result = PollResult(total, queries, sleeps, "timeout")
                break
    except Exception:
        result = PollResult(1, queries, sleeps, "exception")
    # Raise outside except: remote exception messages/credentials are not retained
    # as a chained context. ERROR is not a successful string/boolean outcome.
    if result.status == "exception":
        raise DependencyQueryError(result)
    if result.remaining > 0:
        raise DependencyTimeout(result)
    return result

def table_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Explicit simple three-part table identifier required")
    return ".".join("`" + part + "`" for part in value.split("."))

def dependency_statement(*, dependency_table, template_table, job_log_table,
                         template_name, job_run_date):
    tables = [table_identifier(v) for v in (dependency_table, template_table, job_log_table)]
    if not isinstance(template_name, str) or not template_name or "$" in template_name:
        raise ValueError("Resolved template_name required")
    if not isinstance(job_run_date, str):
        raise ValueError("Explicit MMDDYYYY or empty-string run date required")
    if job_run_date:
        if not re.fullmatch(r"[0-9]{8}", job_run_date):
            raise ValueError("Only reviewed MMDDYYYY date form supported")
        run_date = datetime.strptime(job_run_date, "%m%d%Y").date().isoformat()
    else:
        run_date = None
    # Scalar subquery is intentional: missing template -> NULL; multiple rows ->
    # database error, never a join that multiplies dependencies or silently picks.
    sql = f"""SELECT count(1) AS remaining FROM (
 SELECT depends_on_template_id FROM {tables[0]}
 WHERE template_id = (SELECT template_id FROM {tables[1]} WHERE template_name = :template_name)
 EXCEPT DISTINCT
 SELECT template_id FROM {tables[2]}
 WHERE run_date = COALESCE(CAST(:job_run_date AS DATE), current_date())
 AND end_time IS NOT NULL
) AS missing_dependencies"""
    return sql, {"template_name": template_name, "job_run_date": run_date}

def run_spark_dependency_check(spark, *, dependency_table, template_table,
                             job_log_table, template_name, job_run_date, sleep=time.sleep):
    def query_count():
        # Validation/date conversion inside Java-equivalent exception boundary.
        sql, args = dependency_statement(dependency_table=dependency_table,
            template_table=template_table, job_log_table=job_log_table,
            template_name=template_name, job_run_date=job_run_date)
        row = spark.sql(sql, args=args).first()
        if row is None:
            raise ValueError("COUNT returned no row")
        return int(row["remaining"])
    return poll_dependency_count(query_count, sleep)
'''