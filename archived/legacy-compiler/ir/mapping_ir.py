
from ir.ast_extractor import extract_ast_semantics, extract_column_lineage, extract_transformation_logic
from ir.ir_schema import MappingIR, ColumnLineage, JoinSpec
from typing import Any


def mapping_ir_from_snippets(name: str, mapping_meta: dict, sql_snippets: list[dict]) -> MappingIR:
    """
    Build MappingIR from SQL snippets.
    
    Extracts AST semantics, column lineage, and transformation logic
    using sqlglot to produce business-aware intermediate representation.
    """
    source_tables = set()
    target_table = None
    all_joins = []
    all_projections = {}
    aggregations = []
    group_by_cols = set()
    order_by_cols = set()
    where_clause = None
    lineage = {}
    
    # Track transformation complexity
    has_aggregation = False
    has_join = False
    has_filter = False
    has_window = False
    complexity_score = 0
    
    sql_snippets_output = []

    for snippet in sql_snippets:
        sql_text = snippet.get("sql", "")
        attribute = snippet.get("attribute", "unknown")
        
        if not sql_text:
            continue

        # Extract full AST semantics
        semantics = extract_ast_semantics(sql_text)
        sql_snippets_output.append({
            "attribute": attribute,
            "sql": sql_text,
            "is_valid": semantics.get("is_valid", False),
            "type": semantics.get("type"),
        })

        if not semantics.get("is_valid"):
            continue

        # Aggregate table sources
        source_tables.update(semantics.get("tables", []))

        # Aggregate JOIN specifications
        for join in semantics.get("joins", []):
            all_joins.append(JoinSpec(
                table=join["table"],
                join_type=join.get("type", "INNER"),
                condition=join.get("condition"),
                is_on_primary_key=False  # TODO: hint from metadata
            ))
        has_join = has_join or bool(semantics.get("joins"))
        complexity_score += 2 * len(semantics.get("joins", []))

        # Aggregate WHERE filters
        if semantics.get("where"):
            where_clause = semantics.get("where")
            has_filter = True
            complexity_score += 1

        # Aggregate projections
        for proj in semantics.get("projections", []):
            lineage_cols = extract_column_lineage(semantics["ast"]).get(proj["name"], [])
            all_projections[proj["name"]] = ColumnLineage(
                output_column=proj["name"],
                input_columns=lineage_cols,
                expression=proj.get("expression"),
                transformation_type=proj.get("type"),
            )

        # Aggregate aggregations
        for agg in semantics.get("aggregations", []):
            aggregations.append(agg)
        has_aggregation = has_aggregation or bool(semantics.get("aggregations"))
        complexity_score += 2 * len(semantics.get("aggregations", []))

        # Aggregate GROUP BY
        group_by_cols.update(semantics.get("group_by", []))

        # Aggregate ORDER BY
        order_by_cols.update(semantics.get("order_by", []))

        # Extract target table (from INSERT/UPDATE/DELETE)
        if semantics.get("target_table"):
            target_table = semantics.get("target_table")

        # Track window functions
        if semantics.get("has_window_function"):
            has_window = True
            complexity_score += 3

    # Build MappingIR object
    mapping_obj = MappingIR(
        name=name,
        source_tables=sorted(source_tables),
        target_table=target_table,
        joins=all_joins,
        where_clause=where_clause,
        projections=list(all_projections.values()),
        aggregations=aggregations,
        group_by_columns=sorted(group_by_cols),
        order_by_columns=sorted(order_by_cols),
        has_aggregation=has_aggregation,
        has_join=has_join,
        has_filter=has_filter,
        has_window_function=has_window,
        complexity_score=complexity_score,
        sql_snippets=sql_snippets_output,
        metadata=mapping_meta,
    )

    return mapping_obj


# Legacy function for backward compatibility with old code
def mapping_ir(name, mapping_meta, sql_snippets):
    """Legacy wrapper. Use mapping_ir_from_snippets() for new code."""
    mir = mapping_ir_from_snippets(name, mapping_meta, sql_snippets)
    
    # Return dict format for compatibility
    return {
        "type": "mapping",
        "name": name,
        "attrs": mapping_meta,
        "columns": [p.output_column for p in mir.projections],
        "tables": mir.source_tables,
        "behavior": "transformative" if mir.projections or mir.source_tables else "unclassified",
        "ir": mir,  # Also include the proper IR object
    }
