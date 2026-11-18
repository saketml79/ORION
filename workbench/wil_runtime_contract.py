"""Nonexecuting statement preparation and independent-output comparison API.

No Spark/SDK dependency, no default bindings, no publication of parity. Actual
execution still needs the full session/procedure/transaction implementation.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import json
import re

import sqlglot
from sqlglot import exp


def prepare_statement(sql, table_tokens, parameters, *, approved_bindings):
    """Render reviewed whole-table bindings and return scalar args separately.

    An explicit caller approval receipt is necessary but never sufficient for
    task execution. This function cannot change the candidate's negative gate.
    """
    trees = sqlglot.parse(sql, read="databricks")
    if len(trees) != 1 or trees[0] is None:
        raise ValueError("Exactly one statement required")
    tree = trees[0]
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        token = table.name
        if token not in table_tokens:
            if token not in cte_names:
                raise ValueError("Unregistered physical table: " + token)
            continue
        binding = approved_bindings.get(token, {})
        if (binding.get("approved") is not True or not binding.get("owner")
                or binding.get("source_identity") != table_tokens[token]
                or not re.fullmatch(r"[0-9a-f]{64}", binding.get("review_sha256", ""))):
            raise ValueError("Owner-approved exact binding receipt required: " + token)
        name = binding.get("table", "")
        if not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*\.[A-Za-z_]\w*", name):
            raise ValueError("Simple three-part Unity Catalog identifier required")
        replacement = exp.to_table(name)
        replacement.set("alias", table.args.get("alias"))
        table.replace(replacement)
    required = {p.name for p in tree.find_all(exp.Placeholder)}
    args = {}
    for name in required:
        if name not in parameters:
            raise ValueError("Explicit scoped scalar required: " + name)
        if re.search(r"password|secret|token|uname|credential", name, re.I):
            raise ValueError("Credentials are not scalar business bindings")
        value = parameters[name]
        if name == "p_JOB_RUN_DATE":
            if not isinstance(value, str):
                raise ValueError("MMDDYYYY string or explicit empty string required")
            if value == "":
                value = None  # Reviewed Oracle empty-string NULL boundary.
            else:
                if not re.fullmatch(r"[0-9]{8}", value):
                    raise ValueError("Strict MMDDYYYY date required")
                value = datetime.strptime(value, "%m%d%Y").date().isoformat()
        elif value is not None and type(value) not in {str, int, float, bool}:
            raise ValueError("Unsupported scalar datatype")
        if isinstance(value, str) and ("$$" in value or value == "[REDACTED]"):
            raise ValueError("Unresolved parameter")
        args[name] = value
    return tree.sql(dialect="databricks"), args


def compare_reference_outputs(reference_rows, candidate_rows):
    """Typed multiset differential, not business parity from counts alone.

    Designed for supplied offline outputs. Equal rows do not certify effects,
    source snapshots, schema, rejects, procedures, clock or recovery.
    """
    def atom(value):
        if isinstance(value, Decimal):
            return ("decimal", str(value))
        if value is None:
            return ("null", None)
        if type(value) in {str, int, bool}:
            return (type(value).__name__, value)
        if type(value) is float:
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError("Nonfinite value needs an explicit comparison contract")
            return ("float", value.hex())
        if isinstance(value, datetime):
            return ("datetime", value.isoformat())
        raise ValueError("Unsupported reference value datatype")
    def inventory(rows):
        return Counter(json.dumps([(key, atom(value)) for key, value in sorted(row.items())],
                                  ensure_ascii=False, separators=(",", ":")) for row in rows)
    reference, candidate = inventory(reference_rows), inventory(candidate_rows)
    return {"rows_equal": reference == candidate, "reference_count": sum(reference.values()),
            "candidate_count": sum(candidate.values()),
            "missing_multiset_rows": sum((reference - candidate).values()),
            "extra_multiset_rows": sum((candidate - reference).values()),
            "output_parity": "not_established", "runtime_parity": "not_established"}