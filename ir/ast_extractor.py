"""
Step 5: Extract comprehensive semantic information from sqlglot AST.

Produces true Abstract Syntax Tree representation with joins, filters, 
aggregations, column lineage, and transformation logic preserved.
"""
from sqlglot import exp, parse_one
from typing import Any


def extract_ast_semantics(sql_text: str) -> dict[str, Any]:
    """
    Parse SQL with sqlglot and extract full semantic details.
    
    Returns AST-preserving structure with:
    - Join conditions and grain
    - Filter predicates
    - Aggregation functions
    - Column lineage
    - Transformation expressions
    """
    try:
        ast = parse_one(sql_text, read="oracle")
    except Exception as exc:
        return {
            "is_valid": False,
            "error": str(exc),
            "ast": None,
            "type": None,
        }

    # Determine statement type
    stmt_type = type(ast).__name__  # 'Select', 'Insert', 'Update', etc.

    # Always preserve AST for downstream use
    result = {
        "is_valid": True,
        "ast": ast,  # Full sqlglot AST object
        "type": stmt_type,
        "sql_repr": sql_text,
    }

    # Extract SELECT-specific semantics
    if isinstance(ast, exp.Select):
        # Tables and sources
        tables = [t.name for t in ast.find_all(exp.Table) if t.name]
        result["tables"] = sorted(set(tables))

        # Join information
        joins = []
        for join in ast.find_all(exp.Join):
            raw_kind = join.args.get("kind")
            if isinstance(raw_kind, (list, tuple)) and raw_kind:
                join_type = str(raw_kind[0]).upper()
            elif raw_kind:
                join_type = str(raw_kind).upper()
            else:
                join_type = "INNER"
            on_expr = join.args.get("on")
            joined_table = join.this.name if hasattr(join.this, "name") else str(join.this)
            joins.append({
                "type": join_type,
                "table": joined_table,
                "condition": on_expr.sql(dialect="oracle") if on_expr else None,
            })
        if joins:
            result["joins"] = joins

        # Filter predicates (WHERE clause)
        where_expr = ast.args.get("where")
        if where_expr:
            result["where"] = where_expr.sql(dialect="oracle")

        # Aggregation functions
        agg_funcs = []
        for func in ast.find_all(exp.Avg, exp.Sum, exp.Count, exp.Max, exp.Min):
            agg_funcs.append({
                "function": type(func).__name__.lower(),
                "expression": func.sql(dialect="oracle"),
            })
        if agg_funcs:
            result["aggregations"] = agg_funcs

        # GROUP BY
        group_by_expr = ast.args.get("group")
        if group_by_expr:
            group_cols = [col.sql(dialect="oracle") for col in group_by_expr.expressions]
            result["group_by"] = group_cols

        # ORDER BY
        order_by_expr = ast.args.get("order")
        if order_by_expr:
            order_cols = [col.sql(dialect="oracle") for col in order_by_expr.expressions]
            result["order_by"] = order_cols

        # Projected columns (SELECT list) with aliasing preserved
        projections = []
        for expr in ast.expressions:
            alias = expr.alias
            col_name = expr.alias_or_name
            expr_sql = expr.sql(dialect="oracle")
            projections.append({
                "name": col_name,
                "alias": alias,
                "expression": expr_sql,
                "type": type(expr).__name__,
            })
        result["projections"] = projections

    elif isinstance(ast, exp.Insert):
        # INSERT semantics
        target_table = ast.this.name if hasattr(ast.this, "name") else str(ast.this)
        result["target_table"] = target_table
        
        # Check for OVERWRITE vs regular INSERT
        is_overwrite = "OVERWRITE" in ast.sql(dialect="oracle").upper()
        result["insert_mode"] = "overwrite" if is_overwrite else "append"

        # Subquery (if present)
        select_expr = ast.expression
        if select_expr and isinstance(select_expr, exp.Select):
            result["source_select"] = select_expr.sql(dialect="oracle")

    elif isinstance(ast, exp.Update):
        # UPDATE semantics
        target_table = ast.this.name if hasattr(ast.this, "name") else str(ast.this)
        result["target_table"] = target_table
        result["update_mode"] = "update"
        
        # SET expressions
        set_exprs = []
        for set_item in ast.args.get("set", []):
            if hasattr(set_item, "this") and hasattr(set_item, "expression"):
                set_exprs.append({
                    "column": set_item.this.sql(dialect="oracle"),
                    "value": set_item.expression.sql(dialect="oracle"),
                })
        if set_exprs:
            result["set_clauses"] = set_exprs

    elif isinstance(ast, exp.Delete):
        # DELETE semantics
        target_table = ast.this.name if hasattr(ast.this, "name") else str(ast.this)
        result["target_table"] = target_table
        result["delete_mode"] = "delete"
        
        # WHERE condition
        where_expr = ast.args.get("where")
        if where_expr:
            result["where"] = where_expr.sql(dialect="oracle")

    return result


def extract_column_lineage(ast: exp.Expression) -> dict[str, list[str]]:
    """
    Extract column lineage: map output columns to input columns.
    
    Returns: {"output_column": ["input_col1", "input_col2"], ...}
    """
    lineage = {}
    
    if not isinstance(ast, exp.Select):
        return lineage

    for expr in ast.expressions:
        output_name = expr.alias_or_name
        if not output_name:
            continue

        # Find all columns referenced in this expression
        input_cols = []
        for col in expr.find_all(exp.Column):
            if col.table:
                input_cols.append(f"{col.table}.{col.name}")
            else:
                input_cols.append(col.name)

        if input_cols:
            lineage[output_name] = list(set(input_cols))

    return lineage


def extract_transformation_logic(ast: exp.Expression) -> dict[str, Any]:
    """
    Extract high-level transformation classification.
    
    Identifies: aggregation, join, filter, sort, case logic, UDF calls, etc.
    """
    logic = {
        "has_aggregation": False,
        "has_join": False,
        "has_filter": False,
        "has_window_function": False,
        "has_case_logic": False,
        "has_udf": False,
        "complexity_score": 0,
    }

    for node in ast.find_all(exp.Expression):
        if isinstance(node, (exp.Avg, exp.Sum, exp.Count, exp.Max, exp.Min)):
            logic["has_aggregation"] = True
            logic["complexity_score"] += 2

        elif isinstance(node, exp.Join):
            logic["has_join"] = True
            logic["complexity_score"] += 2

        elif isinstance(node, exp.Where):
            logic["has_filter"] = True
            logic["complexity_score"] += 1

        elif isinstance(node, exp.Window):
            logic["has_window_function"] = True
            logic["complexity_score"] += 3

        elif isinstance(node, exp.If):
            logic["has_case_logic"] = True
            logic["complexity_score"] += 1

        elif isinstance(node, exp.Anonymous):
            # Likely a function call
            if node.name and not node.name.upper() in {"CAST", "COALESCE", "NULLIF"}:
                logic["has_udf"] = True
                logic["complexity_score"] += 2

    return logic
