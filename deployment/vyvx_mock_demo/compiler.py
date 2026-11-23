"""Manually reviewed synthetic pilot compiler. No LLM, app state or remote side effects."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

from source_contract import ROOT, WORKFLOW, read_contract
from fixtures import CONTROL_TABLES, RUN_DATE, TEMPLATE_ID, bronze_rows, expected_rows

OWNER = "ssuman@bpcs.com"
SCHEMA = "vyvx_mock_demo"
MARKER = "orion.vyvx.mock.v1"
CATALOGS = {layer: f"orion_{layer}_test_ssuman" for layer in ("bronze", "silver", "gold")}

def walk(obj):
    yield obj
    for child in obj.get("children", []):
        yield from walk(child)

def xml_type(a):
    dtype = a["DATATYPE"].lower()
    if dtype in {"date", "datetime", "date/time"}:
        return "TIMESTAMP"  # Preserve Oracle DATE's time component.
    if dtype == "number(p,s)":
        return f"DECIMAL({int(a['PRECISION'])},{int(a['SCALE'])})"
    if dtype in {"varchar2", "nvarchar2", "char", "nchar"}:
        return "STRING"
    raise ValueError(f"Unreviewed XML type: {dtype}")

def definition(contract, name, tag, dbd=None):
    found = [d for d in contract["definitions"] if d["tag"] == tag and
             d["attrs"].get("NAME") == name and (dbd is None or d["attrs"].get("DBDNAME") == dbd)]
    if len(found) != 1:
        raise ValueError(f"Ambiguous or missing exact {tag} {name}/{dbd}: {len(found)}")
    return found[0]

def columns(obj, tag):
    return [(c["attrs"]["NAME"], xml_type(c["attrs"])) for c in obj["children"] if c["tag"] == tag]

def qualified(layer, name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Unsafe table name")
    return f"{CATALOGS[layer]}.{SCHEMA}.{name}"

def build_contract():
    xml = read_contract()
    btp = definition(xml, "AE2E_BTP_STG3", "SOURCE", "ATLDEV")
    vbus = definition(xml, "AE2E_VBUS_STG", "SOURCE", "atldev")
    btp_target = definition(xml, "AE2E_BILL_TAMER_PREP", "TARGET")
    vbus_target = definition(xml, "AE2E_VBUS_PREP", "TARGET")
    tables = {}
    for name, obj, kind in (("AE2E_BTP_STG3", btp, "btp"), ("AE2E_VBUS_STG", vbus, "vbus")):
        cols = columns(obj, "SOURCEFIELD")
        tables[name] = {"name": qualified("bronze", name), "role": "source", "columns": cols,
            "rows": bronze_rows(cols, kind), "xml_identity": obj["attrs"]}
    for name, obj, kind, layer in (("AE2E_BILL_TAMER_PREP", btp_target, "btp", "silver"),
                                  ("AE2E_VBUS_PREP", vbus_target, "vbus", "gold")):
        cols = columns(obj, "TARGETFIELD")
        tables[name] = {"name": qualified(layer, name), "role": "destination", "columns": cols,
            "expected": expected_rows(cols, kind), "xml_identity": obj["attrs"]}
    for name, data in CONTROL_TABLES.items():
        tables[name] = {"name": qualified("bronze", name), "role": "source", **data,
                        "provenance": "MINIMAL_SYNTHETIC_EXTERNAL_CONTROL_REPLACEMENT"}
    log_cols = [("RUN_ID", "DECIMAL(38,0)"), ("JOB_NAME", "STRING"),
                ("START_TIME", "TIMESTAMP"), ("END_TIME", "TIMESTAMP")]
    unix_cols = [("RUN_ID", "DECIMAL(38,0)"), ("TEMPLATE_ID", "DECIMAL(38,0)"),
                 ("RUN_DATE", "TIMESTAMP"), ("START_TIME", "TIMESTAMP"), ("END_TIME", "TIMESTAMP")]
    receipt_cols = [("RUN_ID", "STRING"), ("PHASE", "STRING"), ("PAYLOAD", "STRING")]
    for name, cols in (("AE2E_JOB_LOG", log_cols), ("AE2E_UNIX_JOB_LOG", unix_cols),
                       ("mock_email_receipts", receipt_cols), ("mock_dependency_receipts", receipt_cols),
                       ("mock_log_check_receipts", receipt_cols)):
        tables[name] = {"name": qualified("gold", name), "role": "destination", "columns": cols}
    maps = {}
    for mapping, instance, key in (("m_LOAD_AE2E_VYVX_BTP_PREP", "AE2E_BILL_TAMER_PREP_INS", "btp"),
                                  ("m_Load_AE2E_VYVX_VBUS_PREP", "AE2E_VBUS_PREP_INS", "ecckt"),
                                  ("m_Load_AE2E_VYVX_VBUS_PREP", "AE2E_VBUS_PREP1_INS", "l3")):
        maps[key] = {c["attrs"]["TOFIELD"]: c["attrs"]["FROMFIELD"]
                     for c in xml["selected"][mapping]["children"] if c["tag"] == "CONNECTOR"
                     and c["attrs"].get("TOINSTANCE") == instance}
    phases = [{"owner": name, "property": obj["attrs"]["NAME"], "sql": obj["attrs"]["VALUE"]}
              for name, root in [(n, x) for n, x in xml["selected"].items()] +
                                [(x["attrs"]["NAME"], x) for x in xml["sessions"]]
              for obj in walk(root) if obj["attrs"].get("NAME") in {"Sql Query", "Pre SQL", "Post SQL"}
              and obj["attrs"].get("VALUE")]
    # Post SQL is stored on the session writer; preserve the actual XML phase.
    post = [p["sql"] for p in phases if "DELETE" in p["sql"] and "BILL_TAMER_PREP" in p["sql"]]
    if len(post) != 1:
        raise ValueError(f"Expected one BTP post-SQL phase, got {len(post)}")
    blacklist = re.findall(r"'([0-9]+)'", post[0].split("AND template_id")[0])
    if "76104" not in blacklist or "9999" not in blacklist:
        raise ValueError("BTP deletion contract changed")
    return {"marker": MARKER, "owner": OWNER, "workflow": WORKFLOW,
        "package_sha256": xml["package_sha256"], "schema": SCHEMA, "catalogs": CATALOGS,
        "run_date": RUN_DATE, "template_id": TEMPLATE_ID, "tables": tables, "projections": maps,
        "sql_phases": phases, "btp_gl_delete_codes": blacklist,
        "workflow_links": [c["attrs"] for c in xml["selected"][WORKFLOW]["children"] if c["tag"] == "WORKFLOWLINK"],
        "disposition": "USER_APPROVED_SYNTHETIC_MOCK_ONLY_NOT_INFORMATICA_PARITY",
        "mock_substitutions": {"get_product_key_id": "Fixed finite ID lookup on system, circuit:ban, date, -1",
            "truncate": "Python exact destination allowlist with owner/schema/package markers",
            "sequence": "Job run ID, or retained same-date UNIX run ID on resume",
            "java": "Ported 54x300-second polling algebra; ready synthetic dependency, virtual timeout only",
            "email": "JSON receipt only, no SMTP", "transactions": "Sequential Delta statements, no Oracle transaction parity",
            "date_filter": "Reviewed 052806/mm/dd/yy literal normalized to 2006-05-28",
            "l3": "ASCII synthetic circuit regex normalization; no universal Oracle collation guarantee",
            "load_order": "ECCKT insert then L3 NOT EXISTS fallback, deterministic mock phase order",
            "dummy_file_targets": "Gold receipt tables, not byte-identical file exports",
            "auth": "Current labs user, no grants/group/production changes"}}

PHASES = ["dependency", "btp_log_check", "btp_load", "btp_merge", "vbus_log_check",
          "vbus_load", "vbus_merge", "email_mock", "verify"]

def job_settings(workspace_path):
    def notebook(key, dependencies, run_if="ALL_SUCCESS"):
        return {"task_key": key, **({"depends_on": [{"task_key": dep} for dep in dependencies]} if dependencies else {}),
                "run_if": run_if, "max_retries": 0, "timeout_seconds": 1800,
                "notebook_task": {"notebook_path": f"{workspace_path}/notebooks/{key}", "source": "WORKSPACE",
                    "base_parameters": {"ack": "{{job.parameters.ack}}", "run_id": "{{job.run_id}}"}}}
    def condition(key, predecessor):
        return {"task_key": key, "depends_on": [{"task_key": predecessor}], "max_retries": 0,
                "condition_task": {"op": "EQUAL_TO", "left": f"{{{{tasks.{predecessor}.values.needs_load}}}}", "right": "true"}}
    tasks = [notebook("dependency", []), notebook("btp_log_check", ["dependency"]), condition("btp_needed", "btp_log_check")]
    load = notebook("btp_load", [])
    load["depends_on"] = [{"task_key": "btp_needed", "outcome": "true"}]
    tasks += [load, notebook("btp_merge", ["btp_log_check", "btp_load"], "ALL_DONE"),
              notebook("vbus_log_check", ["btp_merge"]), condition("vbus_needed", "vbus_log_check")]
    load = notebook("vbus_load", [])
    load["depends_on"] = [{"task_key": "vbus_needed", "outcome": "true"}]
    tasks += [load, notebook("vbus_merge", ["vbus_log_check", "vbus_load"], "ALL_DONE"),
              notebook("email_mock", ["vbus_merge"]), notebook("verify", ["email_mock"])]
    return {"name": "[MOCK ONLY] VYVX HIST PREP PULL", "description": "Synthetic manually compiled VYVX pilot. Not Informatica/Oracle parity. Sources preserved.",
        "max_concurrent_runs": 1, "queue": {"enabled": False}, "performance_target": "STANDARD",
        "run_as": {"user_name": OWNER}, "timeout_seconds": 7200,
        "tags": {"orion_mock": MARKER, "orion_owner": OWNER, "aidevkit_project": "ai-dev-kit"},
        "parameters": [{"name": "ack", "default": "MOCK_ONLY"}], "tasks": tasks}

def export(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    contract = build_contract()
    code_digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode() +
        b"".join((Path(__file__).parent / n).read_bytes() for n in
                 ("compiler.py", "runtime.py", "fixtures.py", "source_contract.py"))).hexdigest()[:16]
    workspace_path = f"/Workspace/Users/{OWNER}/orion_vyvx_mock_demo_v1/releases/{code_digest}/execution"
    def write(name, value):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    write("contract.json", json.dumps(contract, indent=2) + "\n")
    write("job.json", json.dumps(job_settings(workspace_path), indent=2) + "\n")
    import yaml
    write("databricks.yml", yaml.safe_dump({"bundle": {"name": "orion-vyvx-mock-demo"},
        "workspace": {"root_path": workspace_path.rsplit("/", 1)[0] + "/bundle"}, "sync": {"exclude": ["**/*.ipynb", "reports/**", "manifest.json"]},
        "resources": {"jobs": {"vyvx_mock": job_settings(workspace_path)}},
        "targets": {"demo": {"default": True, "workspace": {"host": "https://adb-1866518241053589.9.azuredatabricks.net"}}}}, sort_keys=False))
    for name in ("runtime.py", "fixtures.py"):
        write(name, (Path(__file__).parent / name).read_text(encoding="utf-8"))
    # Reuse only the already source-reviewed Java polling core, copied as a library.
    sys.path.insert(0, str(ROOT))
    from workbench.dependency_compiler import RUNTIME_SOURCE
    write("dependency_check.py", RUNTIME_SOURCE)
    for phase in PHASES:
        markdown = f"# MOCK ONLY: {phase}\nVYVX XML-informed synthetic pilot, not Informatica parity. No production bindings."
        code = f'''import sys\nimport json\nsys.path.insert(0, {workspace_path!r})\nfrom runtime import execute\ndbutils.widgets.text("ack", "MOCK_ONLY")\ndbutils.widgets.text("run_id", "interactive_not_allowed")\nresult = execute(spark, dbutils, {phase!r}, dbutils.widgets.get("ack"), dbutils.widgets.get("run_id"), {workspace_path!r})\ndbutils.notebook.exit(json.dumps(result, sort_keys=True))\n'''
        ast.parse(code)
        cells = [{"cell_type": "markdown", "metadata": {"language": "markdown", "id": f"vyvx-{phase}-md"}, "source": markdown.splitlines(True)},
                 {"cell_type": "code", "metadata": {"language": "python", "id": f"vyvx-{phase}-py"}, "execution_count": None, "outputs": [], "source": code.splitlines(True)}]
        write(f"notebooks/{phase}.ipynb", json.dumps({"cells": cells, "metadata": {"language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}, indent=2) + "\n")
        write(f"notebooks/{phase}.py", "# Databricks notebook source\n# MAGIC %md\n" +
              "\n".join("# MAGIC " + s for s in markdown.splitlines()) + "\n\n# COMMAND ----------\n" + code)
    hashes = {str(p.relative_to(destination)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in destination.rglob("*") if p.is_file()}
    write("manifest.json", json.dumps({"marker": MARKER, "workspace_path": workspace_path,
        "package_sha256": contract["package_sha256"], "artifacts": hashes}, indent=2) + "\n")
    print(json.dumps({"export": str(destination), "files": len(hashes), "sources":
        {name: {"columns": len(t["columns"]), "rows": len(t["rows"])} for name, t in contract["tables"].items() if t["role"] == "source"}}, indent=2))

if __name__ == "__main__":
    export(sys.argv[1])