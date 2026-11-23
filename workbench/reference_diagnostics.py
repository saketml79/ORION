"""Read-only explanation of canonical gaps and static SQL dependencies.

No fixture/data lookup, canonical mutation, runtime binding or inferred graph edge.
SQLGlot parses statements; parameters and Java expressions remain unverified.
"""
from collections import Counter


def source_blocking_reference(node, nodes):
    expected = set(node.get("attributes", {}).get("expected_kinds", []))
    if expected & {"source", "mapping", "session", "worklet"}:
        return True
    owner = nodes.get(node.get("parent_id"), {})
    return "instance" in expected and owner.get("attributes", {}).get("TRANSFORMATIONTYPE") == "Source Definition"


def reference_report(nodes, local, *, offset=0, limit=40):
    refs, connections = [], []
    for key in sorted(local):
        node = nodes[key]
        attrs = node.get("attributes", {})
        if node["kind"] == "unresolved_reference":
            owner = nodes.get(node.get("parent_id"), {})
            historical = attrs.get("status_reason") == "connection_value_redacted"
            status = ("historical_connection_redaction" if historical else
                      "ambiguous_definition" if attrs.get("status") == "ambiguous" else
                      "runtime_parameter_required" if "$" in node["name"] else
                      "unresolved_session_override" if owner.get("kind") in {"sessionextension", "sesstransformationinst"} else
                      "missing_exported_definition")
            refs.append({"reference_id": key, "name": node["name"], "status": status,
                         "expected_kinds": attrs.get("expected_kinds", []),
                         "candidate_ids": attrs.get("candidate_ids", []),
                         "required_qualifiers": attrs.get("required_qualifiers", {}),
                         "owner_id": owner.get("id"), "owner_name": owner.get("name"),
                         "evidence": node.get("evidence", {}),
                         "action": ("Saved analysis withheld this connection value. Supply a safe alias/connection export; do not infer missing UC tables." if historical else
                                    "Inspect the owning session/task definition and qualified mapping. A current-parser versioned analysis is required to correct historical resolution; this read does not rewrite it." if status == "unresolved_session_override" else
                                    "Supply literal parameter values or the referenced script without secrets." if status == "runtime_parameter_required" else
                                    "Review candidates and exact repository/folder/type/database/owner qualifiers; do not choose a bare-name match." if status == "ambiguous_definition" else
                                    "Provide the exact missing definition or supplemental export in the stated scope. Fixture rows cannot resolve XML identity.")})
        elif node["kind"] == "connectionreference" and attrs.get("status") in {"external", "unknown"}:
            connections.append({"reference_id": key, "status": attrs.get("status_reason"),
                                "action": "Supply the external connection definition and approved runtime binding without credentials."})
    def page(rows):
        selected = rows[offset:offset + limit]
        return {"items": selected, "total": len(rows), "offset": offset, "limit": limit,
                "has_more": offset + len(selected) < len(rows), "truncated": len(selected) != len(rows)}
    blocking = sum(source_blocking_reference(nodes[r["reference_id"]], nodes) for r in refs)
    return {"summary": {"unresolved_total": len(refs), "source_definition_blockers": blocking,
                        "categories": dict(Counter(r["status"] for r in refs)),
                        "external_connection_metadata_gaps": len(connections),
                        "physical_table_existence": "not_observed", "source_data_availability": "not_observed"},
            "references": page(refs), "connections": page(connections),
            "basis": "selected_canonical_analysis_not_automatically_reparsed",
            "history_changed": False, "resolved_by_fixture_data": False}


def sql_dependencies(nodes, local):
    """Extract SQL table identities, excluding CTE aliases; never resolve bindings.

    Unqualified identifiers and unknown dialects are disclosed rather than
    guessed from a fixture catalog. No SQL or raw parameter values are returned.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    names = {"sql query", "source query", "sql override", "lookup sql override", "pre sql", "post sql", "pre session sql", "post session sql"}
    rows = []
    for key in sorted(local):
        owner = nodes[key]
        for ordinal, prop in enumerate(owner.get("properties", [])):
            embedded = prop.get("embedded_code", {})
            if embedded.get("sql_expressions"):
                rows.append({"owner_id": key, "property_ordinal": ordinal, "phase": "embedded Java SQL",
                             "evidence": prop.get("evidence", {}), "tables": [],
                             "status": "dynamic_java_expression_not_standalone_sql",
                             "binding_status": "unverified", "physical_existence": "not_observed",
                             "source_sha256": embedded.get("source_sha256"),
                             "action": "Review the captured Java expression, safe parameter bindings and called procedure bodies. Do not treat string fragments as resolved SQL tables."})
            attrs = prop.get("attributes", {})
            phase = " ".join(attrs.get("NAME", "").lower().replace("-", " ").replace("_", " ").split())
            sql = attrs.get("VALUE", "").strip()
            if phase not in names or not sql:
                continue
            row = {"owner_id": key, "property_ordinal": ordinal, "phase": phase,
                   "evidence": prop.get("evidence", {}), "tables": [],
                   "binding_status": "unverified", "physical_existence": "not_observed",
                   "dialect": "not_exported_generic_parse_only"}
            if "$" in sql or "[REDACTED]" in sql:
                row.update(status="parameterized_or_redacted_sql", action="Supply safe parameter bindings and complete SQL before resolving table identity.")
            elif len(sql) > 65536:
                row.update(status="sql_exceeds_analysis_bound", action="Review the complete property evidence separately; no dependencies inferred.")
            else:
                try:
                    trees = sqlglot.parse(sql, error_level=sqlglot.ErrorLevel.RAISE)
                    tables = set()
                    unsupported = False
                    for tree in trees:
                        if tree is None:
                            continue
                        if isinstance(tree, exp.Command):
                            unsupported = True
                            continue
                        cte_refs = set()
                        for scope in traverse_scope(tree):
                            for table in scope.tables:
                                if not isinstance(scope.sources.get(table.alias_or_name), exp.Table):
                                    cte_refs.add(id(table))
                        for table in tree.find_all(exp.Table):
                            if id(table) not in cte_refs:
                                if not table.parts or not all(isinstance(part, exp.Identifier) for part in table.parts):
                                    unsupported = True
                                else:
                                    tables.add(tuple(part.name for part in table.parts))
                    row["tables"] = [{"parts": list(parts), "qualification": "explicit_schema" if len(parts) >= 2 else "schema_required",
                                      "resolution": "static_sql_reference_not_canonical_or_physical_binding"} for parts in sorted(tables)]
                    row.update(status="partially_unsupported_sql" if unsupported else "static_sql_dependencies",
                               action="Confirm source dialect, exact database/schema identity and runtime parameters. These are SQL evidence, not missing-table observations.")
                except (sqlglot.errors.SqlglotError, ValueError, TypeError):
                    row.update(status="sql_parse_unverified", action="Confirm source dialect and provide complete static SQL or procedure body. No table is declared missing.")
            rows.append(row)
    return rows