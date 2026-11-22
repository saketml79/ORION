"""
Validation script: Compare original Informatica SQL vs generated Databricks SQL
Shows what was preserved, what was transformed, and what could be lost in conversion.
"""
import os
import json
import glob
from pathlib import Path
from ir.ast_extractor import extract_ast_semantics


def load_mapping_ir(mapping_ir_file: str) -> dict:
    """Load MappingIR JSON for analysis."""
    try:
        with open(mapping_ir_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load {mapping_ir_file}: {e}")
        return {}


def extract_sql_semantics(sql: str) -> dict:
    """Extract semantic features from SQL."""
    if not sql.strip():
        return {"is_valid": False, "error": "Empty SQL"}
    
    semantics = extract_ast_semantics(sql)
    return {
        "is_valid": semantics.get("is_valid", False),
        "statement_type": semantics.get("type"),
        "tables": semantics.get("tables", []),
        "joins": len(semantics.get("joins", [])),
        "has_where": bool(semantics.get("where")),
        "aggregations": len(semantics.get("aggregations", [])),
        "group_by": len(semantics.get("group_by", [])),
        "order_by": len(semantics.get("order_by", [])),
    }


def compare_sql_files(output_dir: str = "output") -> dict:
    """
    Compare original Informatica SQL vs generated Databricks SQL.
    
    Outputs:
    - SQL pairs with semantic analysis
    - Validation report showing what was preserved
    - Warnings for potentially lost transformations
    """
    report = {
        "summary": {
            "total_mappings": 0,
            "with_original_sql": 0,
            "semantic_matches": 0,
            "warnings": [],
        },
        "sql_pairs": [],
    }
    
    # Find all validation JSON files
    validation_files = glob.glob(f"{output_dir}/validation/*_SQL_VALIDATION.json")
    
    for validation_file in validation_files:
        with open(validation_file, 'r', encoding='utf-8') as f:
            validation = json.load(f)
        
        task_key = validation.get("task_key", "")
        mapping_name = validation.get("mapping_name", "")
        
        # Load original SQL
        original_sql = ""
        original_file = f"{output_dir}/databricks/sql_original/{task_key}_INFORMATICA.sql"
        if os.path.exists(original_file):
            with open(original_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Strip comments header
                lines = content.split('\n')
                original_sql = '\n'.join([l for l in lines if not l.startswith('--')])
        
        # Load generated SQL
        generated_sql = ""
        generated_file = f"{output_dir}/databricks/sql/{task_key}.sql"
        if os.path.exists(generated_file):
            with open(generated_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Strip comments header
                lines = content.split('\n')
                generated_sql = '\n'.join([l for l in lines if not l.startswith('--')])
        
        report["summary"]["total_mappings"] += 1
        
        if validation.get("has_original_sql"):
            report["summary"]["with_original_sql"] += 1
        
        # Extract semantics
        original_semantics = extract_sql_semantics(original_sql) if original_sql else {}
        generated_semantics = extract_sql_semantics(generated_sql) if generated_sql else {}
        
        # Build comparison record
        comparison = {
            "task_key": task_key,
            "mapping": mapping_name,
            "original": {
                "has_sql": bool(original_sql),
                "length": len(original_sql),
                "semantics": original_semantics,
            },
            "generated": {
                "has_sql": bool(generated_sql),
                "length": len(generated_sql),
                "semantics": generated_semantics,
            },
            "validation": validation.get("sql_semantics", {}),
            "issues": [],
        }
        
        # Check for potential issues
        if original_semantics.get("is_valid") and generated_semantics.get("is_valid"):
            # Check if key transformations were preserved
            orig_tables = set(original_semantics.get("tables", []))
            gen_tables = set(generated_semantics.get("tables", []))
            
            orig_joins = original_semantics.get("joins", 0)
            gen_joins = generated_semantics.get("joins", 0)
            
            orig_aggs = original_semantics.get("aggregations", 0)
            gen_aggs = generated_semantics.get("aggregations", 0)
            
            orig_where = original_semantics.get("has_where", False)
            gen_where = generated_semantics.get("has_where", False)
            
            # Flag missing transformations
            if orig_joins > 0 and gen_joins == 0:
                comparison["issues"].append({
                    "severity": "HIGH",
                    "type": "MISSING_JOIN",
                    "detail": f"Original had {orig_joins} JOIN(s), generated has none",
                })
            
            if orig_aggs > 0 and gen_aggs == 0:
                comparison["issues"].append({
                    "severity": "HIGH",
                    "type": "MISSING_AGGREGATION",
                    "detail": f"Original had {orig_aggs} aggregation(s), generated has none",
                })
            
            if orig_where and not gen_where:
                comparison["issues"].append({
                    "severity": "MEDIUM",
                    "type": "MISSING_WHERE",
                    "detail": "Original had WHERE clause, generated does not",
                })
            
            if orig_tables and orig_tables != gen_tables:
                comparison["issues"].append({
                    "severity": "MEDIUM",
                    "type": "TABLE_MISMATCH",
                    "detail": f"Tables differ: original={orig_tables}, generated={gen_tables}",
                })
            
            # Semantic match score
            if not comparison["issues"]:
                report["summary"]["semantic_matches"] += 1
        
        report["sql_pairs"].append(comparison)
    
    return report


def generate_verification_report(output_dir: str = "output"):
    """Generate comprehensive verification report."""
    report = compare_sql_files(output_dir)
    
    # Write JSON report
    report_file = f"{output_dir}/validation/SQL_VERIFICATION_REPORT.json"
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    # Write markdown report
    md_file = f"{output_dir}/validation/SQL_VERIFICATION_REPORT.md"
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write("# SQL Generation Verification Report\n\n")
        f.write(f"## Summary\n")
        f.write(f"- Total mappings analyzed: {report['summary']['total_mappings']}\n")
        f.write(f"- Mappings with original SQL: {report['summary']['with_original_sql']}\n")
        f.write(f"- Semantically matching: {report['summary']['semantic_matches']}\n")
        f.write(f"- Potential issues found: {sum(len(p.get('issues', [])) for p in report['sql_pairs'])}\n\n")
        
        # Issues by type
        if report['summary']['warnings']:
            f.write("## Critical Issues\n")
            for warning in report['summary']['warnings']:
                f.write(f"- ⚠️ {warning}\n")
            f.write("\n")
        
        # High severity issues
        high_severity = []
        for pair in report['sql_pairs']:
            for issue in pair.get('issues', []):
                if issue.get('severity') == 'HIGH':
                    high_severity.append((pair.get('mapping'), issue))
        
        if high_severity:
            f.write("## High Severity Issues (Data Loss Risk)\n")
            for mapping, issue in high_severity:
                f.write(f"### {mapping}\n")
                f.write(f"- **Issue**: {issue.get('type')}\n")
                f.write(f"- **Detail**: {issue.get('detail')}\n\n")
        
        # Detailed results
        f.write("## Detailed Results by Mapping\n\n")
        for pair in report['sql_pairs']:
            f.write(f"### {pair.get('mapping', 'Unknown')}\n")
            f.write(f"- **Task**: {pair.get('task_key')}\n")
            
            if pair['original'].get('has_sql'):
                f.write(f"- **Original SQL**: {pair['original']['length']} chars\n")
                f.write(f"  - Tables: {pair['original']['semantics'].get('tables', [])}\n")
                f.write(f"  - JOINs: {pair['original']['semantics'].get('joins', 0)}\n")
                f.write(f"  - WHERE: {pair['original']['semantics'].get('has_where', False)}\n")
                f.write(f"  - Aggregations: {pair['original']['semantics'].get('aggregations', 0)}\n")
            else:
                f.write(f"- **Original SQL**: Not found\n")
            
            f.write(f"- **Generated SQL**: {pair['generated']['length']} chars\n")
            f.write(f"  - Tables: {pair['generated']['semantics'].get('tables', [])}\n")
            f.write(f"  - JOINs: {pair['generated']['semantics'].get('joins', 0)}\n")
            f.write(f"  - WHERE: {pair['generated']['semantics'].get('has_where', False)}\n")
            f.write(f"  - Aggregations: {pair['generated']['semantics'].get('aggregations', 0)}\n")
            
            if pair.get('issues'):
                f.write(f"- **Issues Found**: {len(pair['issues'])}\n")
                for issue in pair['issues']:
                    severity_emoji = "🔴" if issue.get('severity') == 'HIGH' else "🟡"
                    f.write(f"  - {severity_emoji} {issue.get('type')}: {issue.get('detail')}\n")
            else:
                f.write(f"- **Issues Found**: None ✅\n")
            
            f.write("\n")
    
    print(f"✅ Verification report generated:")
    print(f"   JSON Report: {report_file}")
    print(f"   Markdown Report: {md_file}")
    
    return report


if __name__ == "__main__":
    report = generate_verification_report()
    print(f"\n📊 Results:")
    print(f"   Total Mappings: {report['summary']['total_mappings']}")
    print(f"   With Original SQL: {report['summary']['with_original_sql']}")
    print(f"   Semantic Matches: {report['summary']['semantic_matches']}")
