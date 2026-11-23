"""Pure, fingerprint-pinned compiler for four reviewed WIL counter wrappers.

No filesystem, network, deployment, agent, fixture or credential access. Returned
text is a deployable *shape*, not permission to execute or a parity certificate.
Only canonical parsing of the explicitly reviewed members is supported.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

import yaml

from workbench.demo_conversion import (
    CHILD_NAME, ORIGINAL_SHA256, REVIEWED_SCOPE_SHA256, WORKFLOW_NAME,
    _digest, _without_annotations, scoped_model,
)
from workbench.understanding import _clean


_REFERENCE = re.compile(r"\${1,2}[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_REQUIRED = {
    "bronze_source_table": "Existing three-part Delta source table, never an Oracle connection.",
    "target_table": "Existing distinct three-part Delta counter table with TEMPLATE_ID and COUNTER.",
    "child_job_id": "Approved real job implementing wkf_Load_AE2E_WILTEL_NETWORK_DISCO; not generated here.",
    "support_list": "Resolved $$SUPPORT_LIST, comma or semicolon separated mailboxes.",
    "infa_env": "Resolved $$INFA_ENV used verbatim in the XML subject template.",
    "email_host": "Reachable SMTP host with valid TLS certificate.",
    "email_port": "Explicit SMTP TCP port.",
    "email_from": "Approved SMTP sender mailbox.",
    "email_tls_mode": "ssl or starttls only; no unencrypted SMTP.",
    "email_secret_scope": "Databricks secret scope readable by the run identity.",
    "email_username_secret_key": "Secret key containing SMTP username, not plaintext credentials.",
    "email_password_secret_key": "Secret key containing SMTP password, not plaintext credentials.",
    "partition_semantics_ack": "Must equal TEMPLATE_ID_18300 after owner verifies Oracle partition membership.",
}
_OPTIONAL = {"sdk_config_profile": "", "sdk_config_file": ""}

# Verified with analyze_package on the actual Lumen XML, not normalized names or
# a family-wide semantic signature. Each pin covers the complete canonical scope.
_MEMBERS = {
    WORKFLOW_NAME: {
        "workflow_id": "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38",
        "scope_sha256": REVIEWED_SCOPE_SHA256, "template_id": 18300, "child_name": CHILD_NAME,
    },
    "wkf_AE2E_DUMMY_CALL_WIL_NET_INS": {
        "workflow_id": "3507aed6f85c7c2dbc5dca041f6662f506e8ef0a623fa9642febf55c0ad3b203",
        "scope_sha256": "5a3520a403d525818c309625bf778222350b9bcc1b9061e8c7bee4bfb24ceb4a",
        "template_id": 18299, "child_name": "wkf_Load_AE2E_WILTEL_NETWORK_INSTALL",
    },
    "wkf_AE2E_DUMMY_CALL_WIL_CUS_INST": {
        "workflow_id": "87856a03806ee5c53746761a542b409f588d1768223106ca32486daf1bfa6fa8",
        "scope_sha256": "c58fb55e7fd0cb029ae42f5e3514689f9c583b1fe617c8d16040ed0b6795d762",
        "template_id": 18297, "child_name": "wkf_Load_AE2E_WILTEL_CUSTOMER_INSTALL",
    },
    "wkf_AE2E_DUMMY_CALL_WIL_CUS_DIS": {
        "workflow_id": "4d30896b15af879a2011782a66e1446f8de8fbd4bc1c681d14c15c5eb58d10a0",
        "scope_sha256": "37bbc5e4c2f74758ccc82a596cf57f85ca1a42f1883bc9c00b2b9599b81c4777",
        "template_id": 18298, "child_name": "wkf_Load_AE2E_WILTEL_CUSTOMER_DISCO",
    },
}


def supported_workflows():
    """Detached review metadata, not execution bindings or extra result keys."""
    return [{"workflow_name": name, **member} for name, member in _MEMBERS.items()]


def _required(template_id=18300, child_name=CHILD_NAME):
    return {**_REQUIRED,
            "child_job_id": f"Approved real job implementing {child_name}; not generated here.",
            "partition_semantics_ack": f"Must equal TEMPLATE_ID_{template_id} after owner verifies Oracle partition membership."}


def _evidence(record):
    # Canonical evidence has ordinal locators only. Never copy arbitrary payloads.
    locator = record.get("evidence", {}).get("locator", "")
    return {"locator": locator} if isinstance(locator, str) and re.fullmatch(
        r"(?:/[A-Za-z_][A-Za-z0-9_]*\[[0-9]+\])+", locator
    ) else {}


def _attributes(attributes, tag):
    # Canonical derived instance_field records also contain dictionaries, unlike
    # raw XML string attributes. Apply parser redaction recursively, not str().
    strings = {key: value for key, value in attributes.items() if isinstance(value, str)}
    cleaned = _clean(strings, tag)
    for key, value in attributes.items():
        if key in cleaned:
            continue
        if _clean({key: ""}, tag)[key] == "[REDACTED]":
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = _attributes(value, tag)
        elif isinstance(value, list):
            cleaned[key] = [_attributes(item, tag) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value
    return cleaned


def _parameter_inventory(model, scope):
    """All declarations, session properties and references, no paging/truncation.

    Parent identities supplement graph containment. No folder-wide name search,
    sibling workflow imports or traversal into the invoked child's implementation.
    A reference is evidence, not a declaration/default or a resolved binding.
    """
    by_id = {n["id"]: n for n in model["nodes"]}
    selected = {n["id"] for n in scope["nodes"]}
    children = {}
    for node in model["nodes"]:
        children.setdefault(node.get("parent_id"), []).append(node["id"])
    pending = list(selected)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in selected:
                selected.add(child)
                pending.append(child)

    def ownership(node):
        path, seen = [], set()
        current = node
        while current and current["id"] not in seen:
            seen.add(current["id"])
            path.append({"id": current["id"], "kind": current["kind"], "name": current.get("name", "")})
            current = by_id.get(current.get("parent_id"))
        return list(reversed(path))

    rows = []
    def append(node, kind, attributes, evidence, location, **extra):
        rows.append({"kind": kind, "owner_id": node["id"], "owner_kind": node["kind"],
                     "parent_id": node.get("parent_id"), "scope": ownership(node),
                     "location": location, "attributes": attributes,
                     "evidence": evidence, **extra})

    def references(node, attributes, evidence, location):
        for key, value in sorted(attributes.items()):
            if isinstance(value, dict):
                references(node, value, evidence, location + "/" + key)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    references(node, {str(index): item}, evidence, location + "/" + key)
            elif isinstance(value, str):
                for match in _REFERENCE.finditer(value):
                    append(node, "reference", {"attribute": key, "expression": value}, evidence,
                           location, name=match.group(), span=[match.start(), match.end()],
                           resolution="evidence_only_not_an_execution_binding")

    for node_id in sorted(selected):
        node = by_id[node_id]
        attrs = _attributes(node.get("attributes", {}), node["kind"].upper())
        evidence = _evidence(node)
        if node["kind"] in {"mappingvariable", "workflowvariable"}:
            append(node, node["kind"], attrs, evidence, "attributes", name=attrs.get("NAME", ""))
        references(node, attrs, evidence, "attributes")
        session_owned = any(p["kind"] == "session" for p in ownership(node))
        for collection in ("properties", "fields"):
            for index, record in enumerate(node.get(collection, [])):
                attrs = _attributes(record.get("attributes", {}), record["kind"].upper())
                location = f"{collection}/{index}"
                if collection == "properties" and session_owned:
                    append(node, "session_property", attrs, _evidence(record), location,
                           name=attrs.get("NAME", ""))
                references(node, attrs, _evidence(record), location)
    for edge in sorted(model["edges"], key=lambda e: e["id"]):
        if edge["source"] in selected and edge["target"] in selected:
            references(by_id[edge["source"]], _attributes(edge.get("attributes", {}), "EDGE"),
                       _evidence(edge), "edges/" + edge["id"])
    # Declaration NAME attributes are evidence, not uses of their own value.
    used = {row["name"] for row in rows if row["kind"] == "reference"
            and row["attributes"]["attribute"] != "NAME"}
    bindings = {
        "$$SUPPORT_LIST": {"kind": "runtime_parameter", "parameters": ["support_list"]},
        "$$INFA_ENV": {"kind": "runtime_parameter", "parameters": ["infa_env"]},
        "$$AE2E_SCHEMA": {"kind": "runtime_parameters", "parameters": ["bronze_source_table", "target_table"],
                          "detail": "Explicit whole-table bindings replace legacy schema qualification; not a schema substitution."},
        "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows": {
            "kind": "runtime_task_value", "task_key": "counter", "key": "target_success_rows"},
    }
    for row in rows:
        if row["kind"] not in {"reference", "mappingvariable", "workflowvariable"}:
            continue
        name = row["name"]
        row["execution_binding"] = dict(bindings.get(name, {
            "kind": "historical_context", "detail": "Not an executable generated binding. Legacy domain, PM paths and unused task built-ins remain evidence only."}))
        if row["kind"] != "reference":
            row["referenced_outside_declaration_name"] = name in used
            row["classification"] = ("system" if row["attributes"].get("USERDEFINED") == "NO"
                                     else "operational" if name in used else "unused")
    return rows


def _notebook(name, code, workflow_name=WORKFLOW_NAME):
    title = f"# {workflow_name}: {name}\nBronze implementation candidate. Execution approval and bindings required."
    cells = []
    for index, (kind, language, text) in enumerate((("markdown", "markdown", title), ("code", "python", code))):
        cell = {"cell_type": kind, "metadata": {"language": language,
                "id": sha256(f"wil-bronze-v1:{workflow_name}:{name}:{index}".encode()).hexdigest()[:24]},
                "source": text.splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    notebook = {"nbformat": 4, "nbformat_minor": 5, "cells": cells,
                "metadata": {"language_info": {"name": "python"}, "kernelspec": {
                    "name": "python3", "display_name": "Python 3", "language": "python"}}}
    source = "# Databricks notebook source\n# MAGIC %md\n"
    source += "\n".join("# MAGIC " + line for line in title.splitlines())
    source += "\n\n# COMMAND ----------\n\n" + code.rstrip() + "\n"
    return json.dumps(notebook, indent=2) + "\n", source


def _runtime(template_id=18300, workflow_name=WORKFLOW_NAME, child_name=CHILD_NAME):
    return (f"TEMPLATE_ID = {template_id!r}\nWORKFLOW_NAME = {workflow_name!r}\n"
            f"CHILD_WORKFLOW_NAME = {child_name!r}\n") + '''import re
from email.utils import getaddresses

def parameter(name):
    dbutils.widgets.text(name, "")
    return dbutils.widgets.get(name).strip()

''' + f"required_parameters = {tuple(_required(template_id, child_name))!r}\n" + '''p = {name: parameter(name) for name in required_parameters}
missing = [name for name, value in p.items() if not value or "$" in value or value == "[REDACTED]"]
if missing:
    raise ValueError("Explicit execution bindings required: " + ", ".join(missing))
for name in p:
    if "\\r" in p[name] or "\\n" in p[name]:
        raise ValueError("Newlines forbidden in parameter " + name)

def table_identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Use a simple three-part Unity Catalog table identifier")
    return ".".join("`" + part + "`" for part in value.split("."))

source_table = table_identifier(p["bronze_source_table"])
target_table = table_identifier(p["target_table"])
if source_table.casefold() == target_table.casefold():
    raise ValueError("bronze_source_table and target_table must be distinct")
if not re.fullmatch(r"[1-9][0-9]{0,18}", p["child_job_id"]):
    raise ValueError("A positive explicit child_job_id is required")
if not p["email_port"].isdigit() or not 1 <= int(p["email_port"]) <= 65535:
    raise ValueError("Invalid email_port")
if p["email_tls_mode"] not in {"ssl", "starttls"}:
    raise ValueError("email_tls_mode must be ssl or starttls")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", p["email_host"]):
    raise ValueError("Invalid email_host")
for name in ("email_secret_scope", "email_username_secret_key", "email_password_secret_key"):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", p[name]):
        raise ValueError("Invalid secret reference " + name)
if p["partition_semantics_ack"] != f"TEMPLATE_ID_{TEMPLATE_ID}":
    raise ValueError("Oracle partition membership must be reviewed before execution")

def mailboxes(value):
    pieces = value.replace(";", ",").split(",")
    if not pieces or any(not part.strip() for part in pieces):
        raise ValueError("Empty email recipient")
    addresses = getaddresses(pieces)
    if len(addresses) != len(pieces) or any(not re.fullmatch(r"[^\\s@<>]+@[^\\s@<>]+\\.[^\\s@<>]+", addr) for _, addr in addresses):
        raise ValueError("Invalid email mailbox")
    return [address for _, address in addresses]

recipients = mailboxes(p["support_list"])
senders = mailboxes(p["email_from"])
if len(senders) != 1:
    raise ValueError("Exactly one email_from mailbox required")
sdk_options = {key: parameter(name) for key, name in
               (("profile", "sdk_config_profile"), ("config_file", "sdk_config_file"))}
sdk_options = {key: value for key, value in sdk_options.items() if value}
'''


def _programs(subject, body, template_id=18300, workflow_name=WORKFLOW_NAME, child_name=CHILD_NAME):
    base = _runtime(template_id, workflow_name, child_name)
    return {
        "wait": base + '''# Exactly one XML timer, not a polling loop. Validate all bindings before sleeping.
import time
time.sleep(300)
''',
        "counter": base + '''# Both bindings must be existing Delta tables. Never create or seed production data.
for table in (source_table, target_table):
    if spark.sql(f"DESCRIBE DETAIL {table}").first()["format"].lower() != "delta":
        raise ValueError("Both bound tables must use Delta")
target_fields = {field.name.upper(): field.dataType.simpleString().lower()
                 for field in spark.table(target_table).schema.fields}
if set(target_fields) != {"TEMPLATE_ID", "COUNTER"} or target_fields != {
        "TEMPLATE_ID": "decimal(38,0)", "COUNTER": "decimal(5,0)"}:
    raise ValueError("Target requires exactly TEMPLATE_ID DECIMAL(38,0), COUNTER DECIMAL(5,0)")
# Pin a Delta version so count and INSERT read the same unfiltered source snapshot.
source_version = int(spark.sql(f"DESCRIBE HISTORY {source_table} LIMIT 1").first()["version"])
if source_version < 0:
    raise ValueError("Invalid source Delta version")
source_query = (f"SELECT CAST({TEMPLATE_ID} AS DECIMAL(38,0)) AS TEMPLATE_ID, "
                f"CAST(1 AS DECIMAL(5,0)) AS COUNTER FROM {source_table} VERSION AS OF {source_version}")
target_success_rows = spark.sql(source_query).count()
spark.sql(f"INSERT INTO {target_table} (TEMPLATE_ID, COUNTER) " + source_query)
# This metric excludes the following post-SQL row and deletion, even if they reset the table.
dbutils.jobs.taskValues.set(key="target_success_rows", value=target_success_rows)
dbutils.jobs.taskValues.set(key="source_version", value=source_version)
spark.sql(f"INSERT INTO {target_table} (TEMPLATE_ID, COUNTER) VALUES ({TEMPLATE_ID}, 1)")
partition_rows = spark.sql(f"SELECT count(*) AS n FROM {target_table} WHERE TEMPLATE_ID = {TEMPLATE_ID}").first()["n"]
if partition_rows >= 60:
    spark.sql(f"DELETE FROM {target_table} WHERE TEMPLATE_ID = {TEMPLATE_ID}")
dbutils.jobs.taskValues.set(key="partition_reset", value=partition_rows >= 60)
# Separate Delta commits, not the Oracle multi-statement transaction/commit interval.
''',
        "launch_child": base + '''from databricks.sdk import WorkspaceClient
if dbutils.jobs.taskValues.get(taskKey="counter", key="target_success_rows") >= 35:
    raise RuntimeError("Child launch requires TgtSuccessRows < 35")
client = WorkspaceClient(**sdk_options)
# Real Jobs submission, intentionally do NOT wait for completion with result().
submission = client.jobs.run_now(job_id=int(p["child_job_id"]))
child_run_id = submission.response.run_id
if not child_run_id:
    raise RuntimeError("Child submission returned no run id")
dbutils.jobs.taskValues.set(key="child_run_id", value=child_run_id)
''',
        "email": base + '''import smtplib
import ssl
from email.message import EmailMessage
if dbutils.jobs.taskValues.get(taskKey="counter", key="target_success_rows") < 35:
    raise RuntimeError("Email requires TgtSuccessRows >= 35")
username = dbutils.secrets.get(scope=p["email_secret_scope"], key=p["email_username_secret_key"])
password = dbutils.secrets.get(scope=p["email_secret_scope"], key=p["email_password_secret_key"])
if not username or not password:
    raise ValueError("SMTP credential secrets must be nonempty")
message = EmailMessage()
message["From"] = senders[0]
message["To"] = ", ".join(recipients)
''' + f'message["Subject"] = {subject!r}.replace("$$INFA_ENV", p["infa_env"])\nmessage.set_content({body!r})\n' + '''context = ssl.create_default_context()
if p["email_tls_mode"] == "ssl":
    with smtplib.SMTP_SSL(p["email_host"], int(p["email_port"]), timeout=30, context=context) as smtp:
        smtp.login(username, password)
        smtp.send_message(message, from_addr=senders[0], to_addrs=recipients)
else:
    with smtplib.SMTP(p["email_host"], int(p["email_port"]), timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(message, from_addr=senders[0], to_addrs=recipients)
dbutils.jobs.taskValues.set(key="email_delivered", value=True)
''',
    }


def _bundle(template_id=18300, workflow_name=WORKFLOW_NAME, child_name=CHILD_NAME):
    required = _required(template_id, child_name)
    member_key = workflow_name.removeprefix("wkf_AE2E_DUMMY_CALL_").lower()
    parameters = [{"name": name, "default": "${var." + name + "}"} for name in (*required, *_OPTIONAL)]
    def notebook(key, dependencies=()):
        task = {"task_key": key, "max_retries": 0, "environment_key": "workflow_python",
                "notebook_task": {"notebook_path": f"./notebooks/{key}.py", "source": "WORKSPACE"}}
        if dependencies:
            task["depends_on"] = [{"task_key": d} if isinstance(d, str) else d for d in dependencies]
        return task

    tasks = [notebook("wait"), notebook("counter", ("wait",)), {
        "task_key": "threshold", "max_retries": 0, "depends_on": [{"task_key": "counter"}],
        "condition_task": {"left": "{{tasks.counter.values.target_success_rows}}", "op": "LESS_THAN", "right": "35"}},
        notebook("launch_child", ({"task_key": "threshold", "outcome": "true"},)),
        notebook("email", ({"task_key": "threshold", "outcome": "false"},))]
    variables = {name: {"description": description} for name, description in required.items()}
    variables.update({name: {"description": "Optional SDK configuration; no credential value.", "default": value}
                      for name, value in _OPTIONAL.items()})
    variables["serverless_environment_version"] = {
        "description": "Explicit workspace-supported serverless environment version. SDK schema verified locally; workspace capability unverified."}
    bundle = {"bundle": {"name": member_key.replace("_", "-") + "-bronze"}, "sync": {"exclude": ["**/*.ipynb"]},
              "variables": variables, "resources": {"jobs": {member_key: {
                  "name": workflow_name + " bronze", "max_concurrent_runs": 1,
                  "performance_target": "STANDARD", "tags": {"aidevkit_project": "ai-dev-kit"},
                  "parameters": parameters, "tasks": tasks, "environments": [{
                      "environment_key": "workflow_python", "spec": {
                          "environment_version": "${var.serverless_environment_version}",
                          "dependencies": ["databricks-sdk==0.157.0"]}}]}}},
              "targets": {"dev": {"default": True, "mode": "development"}}}
    return bundle, tasks, parameters


def compile_workflow_artifacts(model, workflow_id, scenario="bronze"):
    """Return artifact text and native Jobs configs; never execute or export them.

    Unsupported scenarios/semantic drift raise ValueError. Inputs are never
    modified. A package receipt plus the exact scoped semantic hash is mandatory.
    """
    if scenario != "bronze":
        raise ValueError("Original scenario not supported: Oracle connections, physical partition DDL and transaction equivalence require a separate implementation")
    # Scope traversal is a semantic guard for supported examples, not a way to
    # diagnose arbitrary workflows. Classify unsupported selection first.
    selected = next((n for n in model["nodes"] if n["id"] == workflow_id and n["kind"] == "workflow"), None)
    member = _MEMBERS.get(selected["name"]) if selected else None
    if member is None or workflow_id != member["workflow_id"]:
        raise ValueError("Unsupported workflow; only four explicitly pinned WIL counter wrappers are supported")
    scope = scoped_model(model, workflow_id)
    workflow = next(n for n in scope["nodes"] if n["id"] == workflow_id)
    workflow_name = workflow["name"]
    member = _MEMBERS.get(workflow_name)
    if member is None or workflow_id != member["workflow_id"]:
        raise ValueError("Unsupported workflow; only four explicitly pinned WIL counter wrappers are supported")
    scope_hash = _digest(_without_annotations({k: scope[k] for k in ("nodes", "edges")}))
    if scope_hash != member["scope_sha256"]:
        raise ValueError("Unsupported or changed scoped semantics; review required")
    package_hash = scope["package"].get("sha256")
    if not isinstance(package_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", package_hash):
        raise ValueError("Original package SHA-256 receipt required")
    email = next(n for n in scope["nodes"] if n["kind"] == "task" and n["name"] == "eml_on_SUCCESS")
    email_properties = {p["attributes"]["NAME"]: p["attributes"].get("VALUE", "") for p in email["properties"]}
    template_id, child_name = member["template_id"], member["child_name"]
    subject, body = email_properties["Email Subject"], email_properties["Email Text"]
    programs = _programs(subject, body, template_id, workflow_name, child_name)
    bundle, tasks, parameters = _bundle(template_id, workflow_name, child_name)
    requirements = [
        {"id": "bindings", "required": list(_REQUIRED), "status": "unbound",
         "detail": "Supply required DAB variables and validate run-time overrides. No table, job or SMTP binding is invented."},
        {"id": "bronze", "status": "requires_verification",
         "detail": "Existing immutable Delta source snapshot and distinct writable Delta target required. Bronze ingestion/freshness is external. No original source connection is needed."},
        {"id": "partition", "status": "requires_owner_acknowledgement",
         "detail": f"Oracle partition DDL unavailable. Verify partition AE2E_JOB_DEP_CHK_COUNT_{template_id} equals TEMPLATE_ID={template_id} before setting partition_semantics_ack=TEMPLATE_ID_{template_id}."},
        {"id": "transaction_gap", "status": "not_equivalent",
         "detail": "Oracle multi-statement post SQL COMMIT, target commit interval 10000 and rollback-on-error NO are not reproduced by separate Delta statement commits. Partial failure/restart/replay can duplicate inserts; no automatic retries or transaction parity claim."},
        {"id": "concurrency", "status": "requires_operational_control",
         "detail": "max_concurrent_runs=1 serializes this job only. Exclude other target writers, retain source Delta history, forbid replay without reconciliation. Bronze source and target are distinct, not live Oracle self-target state."},
        {"id": "child", "status": "requires_real_binding",
         "detail": f"Bind an approved real child Job implementing {child_name}. Child implementation and child completion are not generated or verified; run_now is asynchronous."},
        {"id": "email", "status": "requires_secrets_and_network",
         "detail": "Resolve $$SUPPORT_LIST and $$INFA_ENV, grant secret ACLs, configure SMTP TLS egress and sender authorization. SMTP username/password only come from dbutils.secrets.get."},
        {"id": "compute", "status": "workspace_unverified",
         "detail": "SDK 0.157.0 supports Task.environment_key, JobEnvironment and compute.Environment.environment_version. Supply a supported serverless environment version and verify notebook support, UC SELECT/MODIFY rights and SDK Jobs permission. STANDARD, on demand, no schedule."},
        {"id": "approval", "status": "not_approved",
         "detail": "Validate bundle and perform independent data/security/parity review before separate deploy/execution approval. Compiler does not access credentials, files or remote services."},
    ]
    inventory = _parameter_inventory(model, scope)
    validation = {"scope_sha256": scope_hash, "reviewed_scope_match": True,
                  "original_package_sha256": package_hash, "package_matches_reviewed": package_hash == ORIGINAL_SHA256,
                  "bundle_schema": "SDK shape tested locally, CLI/workspace validation not performed by compiler",
                  "local_tests": "not_run_by_compiler", "remote_execution": "not_run",
                  "deployment": "not_performed", "semantic_equivalence": "not_established"}
    from workbench.package_parity import completion_gate
    validation["package_completion_gate"] = completion_gate(model, workflow_id)
    artifacts = {}
    for name, code in programs.items():
        notebook, source = _notebook(name, code, workflow_name)
        artifacts[f"notebooks/{name}.ipynb"] = notebook
        artifacts[f"notebooks/{name}.py"] = source
    artifacts["databricks.yml"] = yaml.safe_dump(bundle, sort_keys=False)
    artifacts["parameter_inventory.json"] = json.dumps(inventory, indent=2) + "\n"
    artifacts["deployment_requirements.json"] = json.dumps(requirements, indent=2) + "\n"
    artifacts["validation.json"] = json.dumps(validation, indent=2) + "\n"
    artifacts["README.md"] = _README.format(workflow_name=workflow_name, template_id=template_id,
                                          child_name=child_name, subject=subject, body=body)
    artifacts["tests/test_workflow_contract.py"] = (
        f"TEMPLATE_ID = {template_id!r}\nEMAIL_SUBJECT = {subject!r}\nEMAIL_BODY = {body!r}\n"
        + _CONTRACT_TESTS)
    return {"artifacts": artifacts, "tasks": tasks, "parameters": parameters,
            "parameter_inventory": inventory, "deployment_requirements": requirements,
            "validation": validation, "ready_for_execution": False}


_README = '''# {workflow_name} bronze workflow candidate

Pure compiler output, NOT execution/deployment approval or original-workflow parity.
No demo tables, replacement child, disabled email or mock production dependencies.

## Bind and review

Supply every DAB variable without a default. In particular bronze_source_table,
target_table and child_job_id have NO invented binding. Tables must be distinct
three-part Unity Catalog Delta identifiers. Target must already exist with exactly
TEMPLATE_ID DECIMAL(38,0), COUNTER DECIMAL(5,0). No CREATE/overwrite/seed SQL runs.
Review deployment_requirements.json, including the mandatory Oracle partition
membership acknowledgement. Original scenario and Oracle ingestion are unsupported.
The child {child_name} must be an independently implemented real job, not a compiler substitute.
No extra child parameters are invented. Configure child-specific bindings on that job.

SMTP requires host, explicit port, sender, ssl/starttls, recipient support_list and
infa_env plus scope and key NAMES for username/password secrets. Never put actual
credentials in parameters, files or notebook source. Grant secret read permission to
the run identity. SDK uses normal workspace authentication; optional sdk_config_profile
and sdk_config_file select an approved SDK configuration, not token parameter values.
Verify Jobs permissions, UC permissions, SMTP egress and serverless notebook support.
Supply serverless_environment_version supported by that workspace. SDK schema 0.157.0
was inspected locally; no workspace capability or CLI deployment validation claimed.

All notebook tasks validate every required binding before any sleep or write. This
does not prove table access, secret existence, network reachability or child correctness.
STANDARD serverless, max_concurrent_runs=1, zero retries, no schedule, dev development.
Review DAB configuration and validate it locally before seeking separate deployment
and execution approval. This compiler never deploys, writes artifacts or calls APIs.
YAML uses SOURCE .py notebooks; sync excludes .ipynb to avoid duplicate remote names.
The JSON notebooks are equivalent inspectable source, not a second synced copy.

## Preserved boundaries and explicit gaps

One wait task sleeps 300 seconds, then the counter executes SELECT {template_id},1 for ALL
bronze source rows (no template filter). A pinned Delta version binds count and
INSERT to the same snapshot. Successful main INSERT count is published before
post SQL. Post SQL adds one ({template_id},1) row, then removes only template {template_id} when
its target count is >=60. Branches use pre-post-SQL target_success_rows: <35 submits
real Jobs run_now without result(); >=35 sends real TLS SMTP email. The exact XML
subject is {subject}
The exact XML body is {body}
Recipient is the explicit resolution of $$SUPPORT_LIST. No polling loop is invented.

Oracle physical partition equivalence is NOT known. An owner must verify the
template predicate represents that partition. Oracle COMMIT/rollback and commit
interval 10000 cannot be equated to separate Delta commits. No distributed atomicity,
exactly-once replay, restart equivalence or concurrent-writer safety is claimed.
Failures after inserts or external side effects require reconciliation, not blind
rerun. Concurrency limit covers this job only. Bronze freshness and child completion
are external. Full variable inventory preserves declarations, scopes, session
properties and $/$$ references with XML evidence; it does not pretend unused legacy
built-ins, INFA_DOMAIN or PM paths are resolved executable parameters.

## Legacy variable disposition

The inventory adds execution_binding and declaration classification without losing
the original XML attributes or evidence. Declaration names alone are not value uses.
USERDEFINED=NO declarations are system; used user declarations are operational;
user declarations with no value references are unused. This is static scope usage,
not evidence of historical runtime values. Explicit generated bindings are:

* $$SUPPORT_LIST -> support_list; $$INFA_ENV -> infa_env.
* $$AE2E_SCHEMA -> bronze_source_table and target_table, explicit full identifiers,
    not literal schema-name substitution. Baseline source SQL used AE2E; these bindings
    intentionally replace that physical Oracle context, not its ingestion.
* $s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows -> counter.target_success_rows task value.
* $$INFA_DOMAIN, PM paths and other task built-ins remain historical context only.
    Legacy pmcmd INFA_USERNAME/INFA_PASSWORD names do not become credential parameters:
    child_job_id and normal SDK authentication replace command submission, not the child.
* SMTP host/port/sender/TLS and secret references are new explicit operational bindings.

Only four individually pinned wrappers are supported, not arbitrary WIL workflows.

## Portable tests

Run python -m unittest discover -s tests -p test_workflow_contract.py in an exported
directory. These execute generated notebook bodies against strict local test doubles,
including numeric boundaries, async SDK and SMTP TLS. No Spark, SDK installation,
network or credentials are needed. They do NOT establish remote execution parity.
'''


_CONTRACT_TESTS = r'''"""Portable generated-program tests. Doubles only here, never in production notebooks."""
import ast
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class Frame:
    def __init__(self, rows):
        self.rows = rows
    def count(self):
        return len(self.rows)
    def first(self):
        return self.rows[0]

class Spark:
    def __init__(self, source_rows, target_rows):
        self.source = list(source_rows)
        self.target = list(target_rows)
        self.statements = []
    def table(self, name):
        if name != "`cat`.`sch`.`counter`":
            raise AssertionError(name)
        return SimpleNamespace(schema=SimpleNamespace(fields=[
            SimpleNamespace(name="TEMPLATE_ID", dataType=SimpleNamespace(simpleString=lambda: "decimal(38,0)")),
            SimpleNamespace(name="COUNTER", dataType=SimpleNamespace(simpleString=lambda: "decimal(5,0)"))]))
    def sql(self, sql):
        self.statements.append(sql)
        source = "`cat`.`sch`.`bronze`"
        target = "`cat`.`sch`.`counter`"
        query = (f"SELECT CAST({TEMPLATE_ID} AS DECIMAL(38,0)) AS TEMPLATE_ID, "
                 "CAST(1 AS DECIMAL(5,0)) AS COUNTER FROM " + source + " VERSION AS OF 7")
        if sql in ("DESCRIBE DETAIL " + source, "DESCRIBE DETAIL " + target):
            return Frame([{"format": "delta"}])
        if sql == "DESCRIBE HISTORY " + source + " LIMIT 1":
            return Frame([{"version": 7}])
        if sql == query:
            return Frame([(TEMPLATE_ID, 1) for _ in self.source])
        if sql == "INSERT INTO " + target + " (TEMPLATE_ID, COUNTER) " + query:
            self.target.extend([(TEMPLATE_ID, 1)] * len(self.source))
        elif sql == "INSERT INTO " + target + f" (TEMPLATE_ID, COUNTER) VALUES ({TEMPLATE_ID}, 1)":
            self.target.append((TEMPLATE_ID, 1))
        elif sql == "SELECT count(*) AS n FROM " + target + f" WHERE TEMPLATE_ID = {TEMPLATE_ID}":
            return Frame([{"n": sum(t == TEMPLATE_ID for t, _ in self.target)}])
        elif sql == "DELETE FROM " + target + f" WHERE TEMPLATE_ID = {TEMPLATE_ID}":
            self.target = [row for row in self.target if row[0] != TEMPLATE_ID]
        else:
            raise AssertionError("Unexpected SQL: " + sql)
        return Frame([])

class Harness:
    def __init__(self, source_rows=(), target_rows=()):
        self.spark = Spark(source_rows, target_rows)
        self.p = dict(bronze_source_table="cat.sch.bronze", target_table="cat.sch.counter",
                      child_job_id="987", support_list="ops@example.com; team@example.com", infa_env="PRD",
                      email_host="smtp.example.com", email_port="465", email_from="sender@example.com",
                      email_tls_mode="ssl", email_secret_scope="scope", email_username_secret_key="user",
                      email_password_secret_key="pass", partition_semantics_ack=f"TEMPLATE_ID_{TEMPLATE_ID}")
        self.values, self.sleeps, self.submissions, self.mail, self.events, self.secret_reads = {}, [], [], [], [], []
    def run(self, name):
        code = "".join(json.loads((ROOT / ("notebooks/" + name + ".ipynb")).read_text())["cells"][1]["source"])
        widgets = SimpleNamespace(text=lambda n, d: self.p.setdefault(n, d), get=self.p.__getitem__)
        def set_value(*, key, value):
            self.values[(name, key)] = value
            self.events.append((key, value, len(self.spark.target)))
        def get_secret(*, scope, key):
            self.secret_reads.append((scope, key))
            return "sensitive-test-only-" + key
        dbutils = SimpleNamespace(widgets=widgets, secrets=SimpleNamespace(get=get_secret), jobs=SimpleNamespace(
            taskValues=SimpleNamespace(set=set_value, get=lambda *, taskKey, key: self.values[(taskKey, key)])))
        def run_now(**kw):
            self.submissions.append(kw)
            return SimpleNamespace(response=SimpleNamespace(run_id=123))  # No result() method.
        sdk = ModuleType("databricks.sdk")
        def client(**kw):
            self.sdk_options = kw
            return SimpleNamespace(jobs=SimpleNamespace(run_now=run_now))
        sdk.WorkspaceClient = client
        package = ModuleType("databricks")
        package.sdk = sdk
        harness = self
        class SMTP:
            def __init__(self, host, port, **kwargs):
                harness.events.append(("connect", host, port, kwargs))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def ehlo(self):
                harness.events.append(("ehlo",))
            def starttls(self, **kwargs):
                harness.events.append(("starttls", kwargs))
            def login(self, username, password):
                assert username == "sensitive-test-only-user" and password == "sensitive-test-only-pass"
                harness.events.append(("login",))
            def send_message(self, message, **kwargs):
                harness.mail.append((message, kwargs))
        with patch.dict(sys.modules, {"databricks": package, "databricks.sdk": sdk}), patch("time.sleep", self.sleeps.append), patch("smtplib.SMTP_SSL", SMTP), patch("smtplib.SMTP", SMTP):
            exec(compile(code, name, "exec"), {"dbutils": dbutils, "spark": self.spark})

class ContractTests(unittest.TestCase):
    def test_notebook_json_and_source(self):
        for path in (ROOT / "notebooks").glob("*.ipynb"):
            notebook = json.loads(path.read_text())
            self.assertEqual(notebook["nbformat"], 4)
            source = path.with_suffix(".py").read_text()
            ast.parse(source)
            for cell in notebook["cells"]:
                self.assertIn("id", cell["metadata"])
                self.assertEqual(cell["metadata"]["language"], "python" if cell["cell_type"] == "code" else "markdown")
                if cell["cell_type"] == "code":
                    ast.parse("".join(cell["source"]))
                    self.assertIn("".join(cell["source"]).rstrip(), source)

    def test_numeric_boundaries_and_async(self):
        for n in (0, 29, 30, 34, 35, 59, 60):
            for initial in (0, 24, 59, 60):
                with self.subTest(n=n, initial=initial):
                    h = Harness([(999, None)] * n, [(TEMPLATE_ID, 1)] * initial + [(999, 7)])
                    h.run("wait")
                    self.assertEqual(h.sleeps, [300])
                    h.run("counter")
                    self.assertEqual(h.values[("counter", "target_success_rows")], n)
                    self.assertIn(("target_success_rows", n, initial + 1 + n), h.events)
                    expected = [(999, 7)] if initial + n + 1 >= 60 else [(TEMPLATE_ID, 1)] * initial + [(999, 7)] + [(TEMPLATE_ID, 1)] * (n + 1)
                    self.assertEqual(h.spark.target, expected)
                    self.assertEqual(len(h.spark.source), n)
                    h.run("launch_child" if n < 35 else "email")
                    if n < 35:
                        self.assertEqual(h.submissions, [{"job_id": 987}])
                        self.assertEqual(h.values[("launch_child", "child_run_id")], 123)
                        self.assertFalse(h.mail)
                    else:
                        self.assertFalse(h.submissions)
                        message, kw = h.mail[0]
                        self.assertEqual(message["Subject"], EMAIL_SUBJECT.replace("$$INFA_ENV", "PRD"))
                        self.assertEqual(message.get_content().strip(), EMAIL_BODY)
                        self.assertEqual(kw["to_addrs"], ["ops@example.com", "team@example.com"])
                        self.assertEqual(h.secret_reads, [("scope", "user"), ("scope", "pass")])

    def test_required_bindings_fail_before_sleep_or_writes(self):
        valid = Harness().p
        for name in valid:
            for task in ("wait", "counter", "launch_child", "email"):
                with self.subTest(name=name, task=task):
                    h = Harness([1], [(TEMPLATE_ID, 1)])
                    h.p[name] = ""
                    with self.assertRaises(ValueError):
                        h.run(task)
                    self.assertFalse(h.sleeps or h.spark.statements or h.submissions or h.mail)

    def test_tls_and_sdk_options(self):
        h = Harness([1] * 35)
        h.p["email_tls_mode"] = "starttls"
        h.p["email_port"] = "587"
        h.run("counter")
        h.run("email")
        tags = [e[0] for e in h.events]
        self.assertEqual(tags[-5:], ["ehlo", "starttls", "ehlo", "login", "email_delivered"])
        h = Harness()
        h.p.update(sdk_config_profile="approved", sdk_config_file="/approved/config")
        h.run("counter")
        h.run("launch_child")
        self.assertEqual(h.sdk_options, {"profile": "approved", "config_file": "/approved/config"})

    def test_bad_bindings_and_branch_guards(self):
        for name, value in (("bronze_source_table", "cat.sch.counter"), ("target_table", "x; DROP TABLE y"),
                            ("child_job_id", "0"), ("email_tls_mode", "plain"), ("email_port", "65536"),
                            ("support_list", "$$SUPPORT_LIST"), ("infa_env", "bad\r\nBcc: victim@example.com"),
                            ("partition_semantics_ack", "unverified"),
                            ("partition_semantics_ack", f"TEMPLATE_ID_{TEMPLATE_ID + 1}")):
            h = Harness()
            h.p[name] = value
            with self.assertRaises(ValueError):
                h.run("wait")
            self.assertFalse(h.sleeps or h.spark.statements)
        for count, task in ((34, "email"), (35, "launch_child")):
            h = Harness([1] * count)
            h.run("counter")
            with self.assertRaises(RuntimeError):
                h.run(task)
            self.assertFalse(h.mail or h.submissions or h.secret_reads)

if __name__ == "__main__":
    unittest.main()
'''