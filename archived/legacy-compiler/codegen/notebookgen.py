
def generate_notebook(columns, sql):
    md = "# MAGIC %md\n# MAGIC Columns:\n"
    for c in columns:
        md += f"# MAGIC - {c}\n"
    body = f"spark.sql(\"\"\"{sql}\"\"\")\nprint('Rows:', spark.sql('SELECT COUNT(*) FROM ('+sql+')').first()[0])"
    return md + "\n" + body
