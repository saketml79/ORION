"""
Step 8: LLM-driven Code Generation from IR.

Uses OpenAI API to generate semantically aware code directly from IR objects.
Produces optimized Databricks SQL, notebooks, and workflow orchestration code.
"""
import os
import json
import re
from typing import Optional, Any
from ir.ir_schema import MappingIR, SessionIR, WorkflowIR, ir_to_json_str
from sqlglot import transpile


def get_openai_client():
    """Initialize OpenAI client from environment variable."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key.strip().lower().startswith("sk-your-"):
        raise ValueError(
            "OPENAI_API_KEY not set. "
            "Please set environment variable: export OPENAI_API_KEY=sk-..."
        )
    
    from openai import OpenAI
    return OpenAI(api_key=api_key)


def generate_sql_from_ir(
    mapping_ir_obj: MappingIR,
    session_ir_obj: SessionIR,
    source_schema: str = "raw",
    target_schema: str = "cur",
) -> str:
    """
    Generate optimized Databricks SQL from Mapping + Session IR.
    
    Uses LLM to synthesize SQL that respects:
    - Join conditions and grains
    - Filter predicates
    - Aggregation logic
    - Write mode (append/overwrite/update)
    """
    # If original Informatica SQL exists, translate it first (deterministic and traceable).
    original_sql = _pick_original_sql_snippet(mapping_ir_obj)
    if original_sql:
        translated = _translate_informatica_sql_to_databricks(original_sql, source_schema)
        return _wrap_select_for_target(translated, mapping_ir_obj, session_ir_obj, target_schema)

    try:
        client = get_openai_client()
    except ValueError:
        # Fallback to deterministic SQL generation if API key not available
        return _generate_sql_fallback(mapping_ir_obj, session_ir_obj, source_schema, target_schema)
    
    prompt = f"""Generate optimized Databricks SQL code for the following data transformation:

MAPPING SPECIFICATION (from sqlglot AST analysis):
{ir_to_json_str(mapping_ir_obj)}

SESSION EXECUTION CONTEXT:
{ir_to_json_str(session_ir_obj)}

TARGET SCHEMA: {target_schema}
SOURCE SCHEMA: {source_schema}

Write the EXACT SQL statement to load data from sources to target.
Use INSERT {session_ir_obj.write_mode.value.upper() if session_ir_obj.write_mode.value != 'append' else 'INTO'} syntax.
Include SELECT with explicit columns, JOIN conditions, WHERE filters, GROUP BY, and ORDER BY as applicable.
Target table: {target_schema}.{mapping_ir_obj.name}
Return ONLY the SQL statement, no explanation."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0.3,  # Low temp for deterministic output
        max_tokens=1024,
    )
    
    sql = response.choices[0].message.content.strip()
    sql = _strip_markdown_fences(sql)
    return sql


def _pick_original_sql_snippet(mapping_ir_obj: MappingIR) -> str:
    """Pick the most informative original SQL snippet from mapping IR."""
    snippets = getattr(mapping_ir_obj, "sql_snippets", None) or []
    best_sql = ""
    best_score = -1
    for snippet in snippets:
        sql = (snippet.get("sql") or "").strip()
        if not sql:
            continue
        lowered = sql.lower()
        score = len(sql)
        if " join " in lowered:
            score += 500
        if " where " in lowered:
            score += 200
        if lowered.startswith("select"):
            score += 100
        if score > best_score:
            best_score = score
            best_sql = sql
    return best_sql


def _translate_informatica_sql_to_databricks(sql: str, source_schema: str) -> str:
    """Translate Informatica/Oracle SQL into Databricks dialect with parameter-safe placeholders."""
    normalized = sql.replace("\r\n", "\n").strip().rstrip(";")

    # Replace $$SCHEMA.TABLE style tokens with configured source schema.
    normalized = re.sub(r"\$\$[A-Za-z0-9_]+\.", f"{source_schema}.", normalized)

    # Replace remaining $$PARAM tokens with SQL-safe placeholders.
    normalized = re.sub(r"\$\$([A-Za-z0-9_]+)", r"PARAM_\1", normalized)

    try:
        translated = transpile(normalized, read="oracle", write="databricks")
        if translated:
            return translated[0].strip().rstrip(";")
    except Exception:
        pass

    # Keep normalized SQL if transpilation cannot fully parse parameterized syntax.
    return normalized


def _wrap_select_for_target(sql: str, mapping_ir_obj: MappingIR, session_ir_obj: SessionIR, target_schema: str) -> str:
    """Wrap translated SELECT into target INSERT statement when needed."""
    cleaned = _strip_markdown_fences(sql).strip().rstrip(";")
    lowered = cleaned.lower()
    if lowered.startswith(("insert ", "merge ", "update ", "delete ", "create ")):
        return cleaned

    if session_ir_obj.write_mode.value == "overwrite":
        insert_keyword = "INSERT OVERWRITE"
    else:
        insert_keyword = "INSERT INTO"
    target = f"{target_schema}.{mapping_ir_obj.name}"
    return f"{insert_keyword} {target}\n{cleaned}"


def _strip_markdown_fences(sql: str) -> str:
    """Remove ```sql fences returned by models."""
    stripped = sql.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def generate_notebook_from_ir(
    workflow_name: str,
    session_ir_obj: SessionIR,
    mapping_ir_obj: MappingIR,
) -> str:
    """
    Generate Databricks notebook Python code from Mapping + Session IR.
    
    Uses LLM to produce complete, runnable notebook with:
    - SQL execution with error handling
    - Data quality checks
    - Logging and monitoring
    - Result validation
    """
    try:
        client = get_openai_client()
    except ValueError as e:
        # Fallback to deterministic notebook generation
        return _generate_notebook_fallback(workflow_name, session_ir_obj, mapping_ir_obj)
    
    prompt = f"""Generate a Databricks notebook (Python) for the following ETL session:

WORKFLOW: {workflow_name}
SESSION: {session_ir_obj.name}

MAPPING DETAILS:
{ir_to_json_str(mapping_ir_obj)}

SESSION EXECUTION:
{ir_to_json_str(session_ir_obj)}

Code requirements:
1. Use Databricks SQL execution with proper error handling
2. Log transformation start/end times and row counts
3. Validate data quality (non-null key columns, range checks)
4. Write results to {session_ir_obj.write_mode.value.upper()} mode
5. Return success/failure status

Return ONLY the complete Python notebook code (Databricks format), no explanation."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0.3,
        max_tokens=2048,
    )
    
    notebook_code = response.choices[0].message.content.strip()
    return notebook_code


def generate_workflow_orchestration_from_ir(workflow_ir_obj: WorkflowIR) -> str:
    """
    Generate Databricks Workflow JSON configuration from Workflow IR.
    
    Uses LLM to produce optimized task orchestration respecting:
    - Dependency edges
    - Node roles and skip conditions
    - Parallelization hints
    - Error handling strategies
    """
    try:
        client = get_openai_client()
    except ValueError as e:
        # Fallback to deterministic workflow generation
        return _generate_workflow_orchestration_fallback(workflow_ir_obj)
    
    prompt = f"""Generate a Databricks Workflow JSON configuration for the following DAG:

WORKFLOW DEFINITION:
{ir_to_json_str(workflow_ir_obj)}

Requirements:
1. Each node becomes a task with task_key and dependencies
2. depends_on lists tasks from "edges" where target matches task_key
3. Set timeout_seconds for each task based on complexity
4. Mark skippable tasks appropriately
5. Include retry logic for data-load sessions

Return ONLY the JSON workflow definition (valid JSON format), no explanation."""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0.2,
        max_tokens=2048,
    )
    
    workflow_json_str = response.choices[0].message.content.strip()
    return workflow_json_str


# Fallback functions (deterministic, no LLM required)

def _generate_sql_fallback(
    mapping_ir: MappingIR,
    session_ir: SessionIR,
    source_schema: str = "raw",
    target_schema: str = "cur",
) -> str:
    """Deterministic SQL generation when API key not available."""
    # Build column list
    columns = [p.output_column for p in mapping_ir.projections] if mapping_ir.projections else ["*"]
    col_str = ", ".join(columns)
    
    # Build FROM clause
    if mapping_ir.source_tables:
        from_part = f"FROM {source_schema}.{mapping_ir.source_tables[0]}"
    else:
        from_part = ""
    
    # Build WHERE clause
    where_part = ""
    if mapping_ir.where_clause:
        clause = mapping_ir.where_clause.strip()
        where_part = clause if clause.upper().startswith("WHERE ") else f"WHERE {clause}"
    
    # Build JOIN clauses
    join_parts = []
    for join in mapping_ir.joins:
        join_type = _normalize_join_type(join.join_type)
        table = join.table
        condition = join.condition or "TRUE"
        join_parts.append(f"{join_type} JOIN {source_schema}.{table} ON {condition}")
    join_str = "\n".join(join_parts) if join_parts else ""
    
    # Build GROUP BY if aggregations present
    group_by_str = ""
    if mapping_ir.group_by_columns:
        group_col_str = ", ".join(mapping_ir.group_by_columns)
        group_by_str = f"GROUP BY {group_col_str}"
    
    # Build ORDER BY if specified
    order_by_str = ""
    if mapping_ir.order_by_columns:
        order_col_str = ", ".join(mapping_ir.order_by_columns)
        order_by_str = f"ORDER BY {order_col_str}"
    
    # Determine INSERT mode
    insert_keyword = "INSERT OVERWRITE" if session_ir.write_mode.value == "overwrite" else "INSERT INTO"
    
    # Assemble final SQL
    sql_lines = [
        f"{insert_keyword} {target_schema}.{mapping_ir.name}",
        f"SELECT {col_str}",
        from_part,
    ]
    
    if join_str:
        sql_lines.append(join_str)
    if where_part:
        sql_lines.append(where_part)
    if group_by_str:
        sql_lines.append(group_by_str)
    if order_by_str:
        sql_lines.append(order_by_str)
    
    return "\n".join(line for line in sql_lines if line)


def _normalize_join_type(join_type: str) -> str:
    """Normalize join types parsed from SQLGlot/legacy representations."""
    token = (join_type or "INNER").strip().upper()
    mapping = {
        "I": "INNER",
        "INNER": "INNER",
        "L": "LEFT",
        "LEFT": "LEFT",
        "R": "RIGHT",
        "RIGHT": "RIGHT",
        "F": "FULL",
        "FULL": "FULL",
        "C": "CROSS",
        "CROSS": "CROSS",
    }
    return mapping.get(token, token)


def _generate_notebook_fallback(
    workflow_name: str,
    session_ir: SessionIR,
    mapping_ir: MappingIR,
) -> str:
    """Deterministic notebook generation when API key not available."""
    return f"""# Databricks notebook source
# Generated for: {workflow_name} / {session_ir.name}
# Write Mode: {session_ir.write_mode.value.upper()}

import json
from datetime import datetime
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()

# Logging
task_name = "{session_ir.name}"
start_time = datetime.utcnow().isoformat()
print(f"Starting {{task_name}} at {{start_time}}")

try:
    # TODO: Execute transformation SQL
    # This is a placeholder. Use the mapping_ir and session_ir to generate actual SQL.
    
    spark.sql(\"\"\"
    SELECT 1 as count
    \"\"\")
    
    end_time = datetime.utcnow().isoformat()
    print(f"Completed {{task_name}} at {{end_time}}")
    dbutils.notebook.exit(json.dumps({{"status": "success", "task": task_name}}))
    
except Exception as e:
    error_time = datetime.utcnow().isoformat()
    print(f"Failed {{task_name}} at {{error_time}}: {{str(e)}}")
    dbutils.notebook.exit(json.dumps({{"status": "error", "task": task_name, "error": str(e)}}))
"""


def _generate_workflow_orchestration_fallback(workflow_ir: WorkflowIR) -> str:
    """Deterministic workflow JSON generation when API key not available."""
    tasks = []
    
    for node in workflow_ir.nodes:
        task = {
            "task_key": node.name,
            "description": f"Task: {node.name} (role: {node.role.value})",
            "depends_on": [],
        }
        
        # Find dependencies for this node
        for edge in workflow_ir.edges:
            if edge.target == node.name:
                task["depends_on"].append({
                    "task_key": edge.source,
                })
        
        tasks.append(task)
    
    workflow_json = {
        "name": workflow_ir.name,
        "tasks": tasks,
    }
    
    return json.dumps(workflow_json, indent=2)
