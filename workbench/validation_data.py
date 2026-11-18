"""Bounded typed multiset comparisons. Never return row values or row hashes.

This evaluator is for controlled fixtures and independently acquired snapshots,
not a source connector. Strict precision rejects implicit rounding/coercion.
"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
import re


MAX_ROWS = 10000


def _value(value, column):
    if value is None:
        if not column["nullable"]:
            raise ValueError("Null in required column")
        return ("null",)
    kind = column["type"]
    if kind == "DOUBLE":
        if type(value) is not float or not math.isfinite(value):
            raise ValueError("Finite explicit IEEE double required")
        # Exact float representation. No approximate tolerance or decimal cast.
        return (kind, value.hex())
    if kind == "STRING":
        if not isinstance(value, str):
            raise ValueError("String required")
        width = column.get("width")
        if width is not None:
            unit = column.get("width_unit")
            if unit not in {"characters", "utf8_bytes"}:
                raise ValueError("Explicit character width unit required")
            if (len(value) if unit == "characters" else len(value.encode("utf-8"))) > width:
                raise ValueError("Character width exceeded")
        return (kind, value)
    match = re.fullmatch(r"DECIMAL\(([1-9][0-9]?),([0-9]+)\)", kind)
    if match:
        precision, scale = map(int, match.groups())
        if precision > 38 or scale > precision or isinstance(value, (float, bool)):
            raise ValueError("Invalid decimal contract or floating-point input")
        if not isinstance(value, (str, int, Decimal)):
            raise ValueError("Exact decimal required")
        number = Decimal(value)
        with localcontext() as context:
            context.prec = 80
            quantum = Decimal(1).scaleb(-scale)
            if (not number.is_finite() or number != number.quantize(quantum)
                    or abs(number) >= Decimal(10) ** (precision - scale)):
                raise ValueError("Decimal precision/scale exceeded")
            return (kind, format(number.quantize(quantum), "f"))
    if kind in {"INT", "SMALLINT", "BIGINT"}:
        bits = {"INT": 32, "SMALLINT": 16, "BIGINT": 64}[kind]
        if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise ValueError("Integer range/type mismatch")
        return (kind, value)
    if kind in {"DATE", "TIMESTAMP_NTZ"}:
        if not isinstance(value, str):
            raise ValueError("ISO date/time string required")
        if kind == "DATE":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Exact ISO date required")
            return (kind, date.fromisoformat(value).isoformat())
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
            raise ValueError("Naive ISO timestamp with microsecond precision required")
        return (kind, datetime.fromisoformat(value).isoformat(timespec="microseconds"))
    if kind == "BINARY" and isinstance(value, bytes):
        return (kind, value.hex())
    raise ValueError("Unsupported comparison datatype")


def compare_rows(expected, actual, columns, *, keys=()):
    """Exact typed bag comparison, including multiplicity and key diagnostics.

    Counts are diagnostics, never the equality criterion. No fuzzy tolerance,
    silent trimming, Oracle empty-string coercion or timestamp truncation.
    """
    if (not isinstance(expected, list) or not isinstance(actual, list)
            or max(len(expected), len(actual)) > MAX_ROWS or not columns or len(columns) > 1000):
        raise ValueError("Comparison exceeds controlled snapshot bound")
    names = [c["name"] for c in columns]
    if (len(set(n.casefold() for n in names)) != len(names) or len(set(keys)) != len(keys)
            or not set(keys) <= set(names)):
        raise ValueError("Invalid columns or comparison keys")
    for column in columns:
        if type(column.get("nullable")) is not bool or not isinstance(column.get("type"), str):
            raise ValueError("Explicit type/nullability required")
        if column.get("width") is not None and (type(column["width"]) is not int or column["width"] < 1):
            raise ValueError("Invalid character width")

    def snapshot(rows):
        bag, key_bag, nulls, errors, null_keys = Counter(), Counter(), 0, 0, 0
        for row in rows:
            try:
                if not isinstance(row, dict) or set(row) != set(names):
                    raise ValueError("Row schema mismatch")
                values = tuple(_value(row[n], c) for n, c in zip(names, columns, strict=True))
                bag[values] += 1
                nulls += sum(v == ("null",) for v in values)
                if keys:
                    key = tuple(values[names.index(n)] for n in keys)
                    key_bag[key] += 1
                    null_keys += any(v == ("null",) for v in key)
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                errors += 1
        return bag, key_bag, nulls, errors, null_keys

    left, lk, ln, le, lnk = snapshot(expected)
    right, rk, rn, re_, rnk = snapshot(actual)
    missing, unexpected = sum((left - right).values()), sum((right - left).values())
    column_results = []
    for position, column in enumerate(columns):
        def projected(bag):
            result = Counter()
            for row, count in bag.items():
                result[row[position]] += count
            return result
        lcol, rcol = projected(left), projected(right)
        numeric = column["type"] in {"INT", "SMALLINT", "BIGINT"} or column["type"].startswith("DECIMAL(")
        aggregate = None
        if numeric and not (le or re_):
            with localcontext() as context:
                context.prec = 80
                aggregate = (sum(Decimal(v[1]) * count for v, count in lcol.items() if v != ("null",))
                             == sum(Decimal(v[1]) * count for v, count in rcol.items() if v != ("null",)))
        column_results.append({"name": column["name"],
                               "multiset_match": not (le or re_) and lcol == rcol,
                               "null_count_match": lcol[("null",)] == rcol[("null",)],
                               "numeric_sum_match": aggregate})
    return {"status": "passing" if not (le or re_ or missing or unexpected) else "failed",
            "expected_rows": len(expected), "actual_rows": len(actual),
            "missing_rows": missing, "unexpected_rows": unexpected,
            "expected_invalid_rows": le, "actual_invalid_rows": re_,
            "expected_null_cells": ln, "actual_null_cells": rn,
            "expected_duplicate_keys": sum(v - 1 for v in lk.values()),
            "actual_duplicate_keys": sum(v - 1 for v in rk.values()),
            "expected_null_keys": lnk, "actual_null_keys": rnk,
            "keys": list(keys), "comparison": "exact_typed_multiset",
            "column_checks": column_results, "aggregate_values_exported": False,
            "rows_logged": False, "row_hashes_exported": False}


def controlled_fixture_suite():
    """Execute a fixed generated notebook and a separate literal reference.

    Synthetic arithmetic only. Never represents the four WIL child loads.
    No caller-provided code is accepted or executed.
    """
    source = (
        "from decimal import Decimal\n"
        "def transform(rows):\n"
        "    return [dict(id=r['id'], amount=str(Decimal(r['amount']) * Decimal('1.25')), "
        "label=r['label'], occurred=r['occurred']) for r in rows]\n"
    )
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "code", "metadata": {"language": "python"},
         "source": source.splitlines(keepends=True), "outputs": [], "execution_count": None}]}
    # Round-trip the actual notebook document rather than a second test function.
    document = json.loads(json.dumps(notebook))
    namespace = {}
    exec(compile("".join(document["cells"][0]["source"]), "controlled_fixture_notebook", "exec"), namespace)
    inputs = [dict(id=1, amount="8.00", label=None, occurred="2026-11-05T12:34:56.123456"),
              dict(id=1, amount="8.00", label=None, occurred="2026-11-05T12:34:56.123456"),
              dict(id=2, amount="-4.00", label="é", occurred="2026-11-05T00:00:00")]
    # Independent expected values, not transform() used as its own oracle.
    expected = [dict(id=1, amount="10.00", label=None, occurred="2026-11-05T12:34:56.123456"),
                dict(id=1, amount="10.00", label=None, occurred="2026-11-05T12:34:56.123456"),
                dict(id=2, amount="-5.00", label="é", occurred="2026-11-05T00:00:00")]
    columns = [{"name": "id", "type": "INT", "nullable": False},
               {"name": "amount", "type": "DECIMAL(12,2)", "nullable": False},
               {"name": "label", "type": "STRING", "nullable": True, "width": 2, "width_unit": "utf8_bytes"},
               {"name": "occurred", "type": "TIMESTAMP_NTZ", "nullable": False}]
    actual = namespace["transform"](inputs)
    result = compare_rows(expected, actual, columns, keys=("id",))
    input_result = compare_rows(inputs, inputs, columns, keys=("id",))
    # Negative control: same count but wrong value must fail.
    wrong = [dict(row) for row in actual]
    wrong[0]["amount"] = "10.01"
    negative = compare_rows(expected, wrong, columns, keys=("id",))
    return {"basis": "controlled_synthetic_fixture_not_business_parity",
            "column_contract": columns,
            "input_validation": input_result, "output_comparison": result,
            "same_count_negative_control": "passing" if negative["status"] == "failed" else "failed",
            "notebook": notebook, "business_parity": "not_established"}