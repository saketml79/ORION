"""Offline deterministic planner and explicit SDK deploy/run CLI.

--plan is strictly local. --deploy-run PLAN is a separately invoked cloud action
using only profile_identity('labs'). No imports or tests authenticate or deploy.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid

from workbench.source_fixtures import plan_sources
from workbench.synthetic_source_metadata import extract_metadata, sensitive
from workbench.synthetic_source_runtime import (
    CATALOG, CONCURRENCY, FORMAT, FixtureError, digest, insert_statement,
    validate_plan, validate_value,
)

PACKAGE_SHA256 = "08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "workbench" / "synthetic-source-data"


def file_hash(path):
    checksum = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def key_name(name):
    return bool(re.search(r"(?:^|_)(?:id|key)$|^id$|^key$", name, re.I))


def build_domains(tables, metadata):
    columns = {(i, c["name"].casefold()): c for i, t in enumerate(tables) for c in t["columns"]}
    parent = {}
    evidence = []

    def find(endpoint):
        parent.setdefault(endpoint, endpoint)
        if parent[endpoint] != endpoint:
            parent[endpoint] = find(parent[endpoint])
        return parent[endpoint]

    def join(left, right):
        a, b = find(left), find(right)
        parent[max(a, b)] = min(a, b)

    names = {}
    for endpoint in sorted(columns):
        if key_name(endpoint[1]) and not sensitive(endpoint[1]):
            if endpoint[1] in names:
                join(names[endpoint[1]], endpoint)
            else:
                names[endpoint[1]] = endpoint
                find(endpoint)
    for hint in metadata["keys"]:
        endpoint = (hint["table_index"], hint["column"])
        if endpoint in columns and not sensitive(endpoint[1]):
            find(endpoint)
    for relation in metadata["relationships"]:
        left, right = tuple(relation["left"]), tuple(relation["right"])
        if left in columns and right in columns and not any(sensitive(e[1]) for e in (left, right)):
            join(left, right)
            evidence.append(relation)
    groups = {}
    for endpoint in sorted(parent):
        groups.setdefault(find(endpoint), []).append(endpoint)
    domains, assignments = [], {}
    for members in groups.values():
        usable, capacity = True, 9
        for endpoint in members:
            column = columns[endpoint]
            kind = column["type"]
            if kind.startswith("DECIMAL"):
                p, s = map(int, re.findall(r"\d+", kind))
                if p == s:
                    capacity = 0  # Only zero is universal across integer/fraction domains.
            elif kind not in {"STRING", "INT", "SMALLINT", "DOUBLE"}:
                usable = False
        domain_id = "id_" + digest([[tables[i]["xml_identity"], name] for i, name in members])[:20]
        joins = [e for e in evidence if tuple(e["left"]) in members and tuple(e["right"]) in members]
        domains.append({"domain_id": domain_id, "members": [list(e) for e in members],
                        "confidence": 0.8 if joins else 0.35,
                        "basis": "connector_traced_equality_and_name_hints" if joins else "column_name_or_xml_key_hint_only",
                        "shared_values_supported": usable, "capacity": capacity,
                        "relationship_parity": "not_established", "evidence": joins})
        if usable:
            assignments.update({endpoint: (domain_id, capacity) for endpoint in members})
    return domains, assignments


def coerce(column, value):
    kind = column["type"]
    if kind in {"STRING", "TIMESTAMP_NTZ", "BINARY"} or kind.startswith("DECIMAL"):
        value = str(value)
    elif kind in {"INT", "SMALLINT"}:
        value = int(value)
    elif kind == "DOUBLE":
        value = float(value)
    validate_value(column, value)
    return value


def generated_value(column, row_index, seed, anchor, domain=None):
    kind = column["type"]
    if sensitive(column["name"]) and column["nullable"]:
        return None
    if domain is not None:
        _, capacity = domain
        number = 0 if not capacity else 1 + row_index % capacity
        return coerce(column, number)
    checksum = sha256((seed + ":" + column["name"].casefold()).encode()).hexdigest()
    number = 1 + (int(checksum[:8], 16) + row_index) % 9
    if kind == "STRING":
        width = int(column["precision"])
        return ("SYNTHETIC_" + checksum[:12] + "_" + str(row_index + 1))[:min(width if width > 0 else 64, 4000)]
    if kind.startswith("DECIMAL"):
        _, scale = map(int, re.findall(r"\d+", kind))
        return "0." + "0" * (scale - 1) + str(number) if scale else str(number)
    if kind in {"INT", "SMALLINT", "DOUBLE"}:
        return number
    if kind == "TIMESTAMP_NTZ":
        return (datetime.combine(anchor, datetime.min.time()) + timedelta(seconds=row_index)).isoformat(sep=" ")
    if kind == "BINARY":
        width = int(column["precision"])
        return checksum[:2 * min(max(width, 1), 16)]
    raise FixtureError("unsupported_generation_type")


def build_plan(xml, *, rows_per_table=10, anchor_date="2026-01-01", require_serializable=False):
    if type(rows_per_table) is not int or not 1 <= rows_per_table <= 1000:
        raise ValueError("rows_per_table must be within 1..1000")
    anchor = date.fromisoformat(anchor_date)
    source = plan_sources(xml, CATALOG)
    metadata = extract_metadata(xml, source["tables"])
    if file_hash(xml) != source["package_sha256"]:
        raise ValueError("XML changed during planning")
    domains, assignments = build_domains(source["tables"], metadata)
    literals = {}
    for item in metadata["literals"]:
        endpoint = (item["table_index"], item["column"])
        column = next(c for c in source["tables"][endpoint[0]]["columns"] if c["name"].casefold() == endpoint[1])
        try:
            value = coerce(column, item["value"])
        except (ValueError, TypeError):
            metadata["withheld"]["literal_outside_type_or_width"] = metadata["withheld"].get("literal_outside_type_or_width", 0) + 1
            continue
        # Only status/flag/category names are assigned comparison literals.
        # Date filters, arbitrary business columns and identifiers are not
        # silently overwritten with literal guesses from unrelated queries.
        if re.search(r"status|(?:^|_)(?:flag|ind|type|code)$", endpoint[1]):
            literals.setdefault(endpoint, []).append(value)
    special = {}
    for item in metadata["counter_rows"]:
        values = {k: v for k, v in item["values"].items() if k in {"template_id", "counter"}}
        special.setdefault(item["table_index"], {})[digest(values)] = values
    tables = []
    for index, table in enumerate(source["tables"]):
        table = dict(table)
        overrides = [special[index][key] for key in sorted(special.get(index, {}))]
        count = len(overrides) if overrides else rows_per_table
        rows = []
        seed = digest(table["xml_identity"])
        for row_index in range(count):
            row = []
            for column in table["columns"]:
                endpoint = (index, column["name"].casefold())
                if overrides and endpoint[1] in overrides[row_index]:
                    value = coerce(column, overrides[row_index][endpoint[1]])
                elif endpoint in literals:
                    values = sorted(set(literals[endpoint]), key=str)
                    value = values[row_index % len(values)]
                else:
                    value = generated_value(column, row_index, seed, anchor, assignments.get(endpoint))
                validate_value(column, value)
                row.append(value)
            rows.append(row)
        table.update(synthetic_rows=rows, row_count=count,
                     generation="captured_counter_select_literals" if overrides else "bounded_deterministic",
                     key_domains={name: value[0] for (i, name), value in assignments.items() if i == index})
        tables.append(table)
    plan = {"format": FORMAT, "catalog": CATALOG, "package_sha256": source["package_sha256"],
            "table_count": source["table_count"], "column_count": source["column_count"],
            "schema_count": source["schema_count"], "tables": tables,
            "rows_per_table": rows_per_table, "anchor_date": anchor_date,
            "require_serializable": require_serializable, "metadata": metadata, "id_domains": domains,
            "concurrency_boundary": CONCURRENCY, "relationship_parity": "not_established",
            "business_parity": "not_established", "source_binding_approved": False,
            "warnings": ["Only empty existing matching Delta fixtures are eligible; populated tables are skipped.",
                         "Name domains are low-confidence hints, not foreign keys. Computed and ambiguous joins are withheld.",
                         "Small shared ID domains may repeat; uniqueness and cardinality are not guaranteed.",
                         "Captured counter constants do not reproduce WIL session row counts, thresholds, post-SQL or business parity.",
                         "Dependency settings preserve actual query table tokens; absent SOURCE definitions remain unresolved.",
                         "Dependency parameters, clocks, logs and external code are not resolved or executed.",
                         "Status literals are observed examples, not exhaustive allowed-value contracts."]}
    plan["plan_sha256"] = digest(plan)
    validate_plan(plan)
    return plan


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)


def create_local_plan(xml, **options):
    plan = build_plan(xml, **options)
    if plan["package_sha256"] != PACKAGE_SHA256 or plan["table_count"] != 583:
        raise ValueError("Only the approved original 583-table XML package is deployable")
    directory = OUTPUT / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "plan.json", plan)
    # Separate reviewable SQL artifact. The remote runtime regenerates SQL from
    # validated values, and never executes this local file as a script.
    with (directory / "conditional-inserts.sql").open("x", encoding="utf-8") as output:
        output.write(";\n\n".join(insert_statement(plan, t) for t in plan["tables"]) + ";\n")
    return directory / "plan.json"


def notebook_source(workspace_root):
    """Python SOURCE export, not an ipynb document. No execution on import."""
    return ("# Databricks notebook source\n"
            "import json\nimport sys\n"
            f"sys.path.insert(0, {workspace_root!r})\n"
            "from synthetic_source_runtime import run_file\n"
            "dbutils.widgets.text('plan_sha256', '')\n"
            "dbutils.widgets.text('expected_user', '')\n"
            "dbutils.widgets.text('require_serializable', 'false')\n"
            f"receipt = run_file(spark, {(workspace_root + '/plan.json')!r}, {workspace_root!r},\n"
            "    expected_hash=dbutils.widgets.get('plan_sha256'),\n"
            "    expected_user=dbutils.widgets.get('expected_user'),\n"
            "    require_serializable=dbutils.widgets.get('require_serializable') == 'true')\n"
            "dbutils.notebook.exit(json.dumps(receipt, sort_keys=True))\n")


def deploy_run(plan_path, xml, *, timeout_minutes=180, identity_factory=None):
    """Explicit cloud path; no alternate identity, update/reset or retries.

    SDK API calls are recorded by phase. A failed/unknown cloud acknowledgement
    is not automatically retried. Partial deployment and population receipts
    stay in a unique local output directory; no cleanup deletes cloud evidence.
    """
    from databricks.sdk.service import jobs, workspace
    from workbench.databricks_models import profile_identity

    plan = json.loads(Path(plan_path).read_bytes())
    validate_plan(plan)
    if (plan["package_sha256"] != PACKAGE_SHA256 or plan["table_count"] != 583
            or build_plan(xml, rows_per_table=plan["rows_per_table"], anchor_date=plan["anchor_date"],
                          require_serializable=plan["require_serializable"]) != plan):
        raise ValueError("Plan differs from original XML scope/evidence/values")
    directory = OUTPUT / ("deployment-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    deployment = {"status": "started", "plan_sha256": plan["plan_sha256"], "profile": "labs",
                  "phase": "identity", "job_id": None, "run_id": None, "errors": []}
    client = None
    remote = None
    try:
        identity = (identity_factory or profile_identity)("labs")
        if identity.mode != "profile":
            raise FixtureError("explicit_labs_profile_required")
        client = identity.client
        user = client.current_user.me()
        if user.id != identity.user_id or not user.user_name or "/" in user.user_name:
            raise FixtureError("identity_changed")
        deployment.update(workspace=identity.host, user_id=identity.user_id)
        # Personal Workspace path plus unique UUID. Never overwrite shared app,
        # fixture loader, notebook or Job assets.
        remote = "/Workspace/Users/" + user.user_name + "/orion-synthetic-source-data/" + uuid.uuid4().hex
        deployment["workspace_directory"] = remote
        deployment["phase"] = "workspace_mkdirs"
        client.workspace.mkdirs(remote)
        runtime = Path(__file__).with_name("synthetic_source_runtime.py").read_bytes()
        notebook = notebook_source(remote).encode()
        payloads = [("synthetic_source_runtime.py", runtime, workspace.ImportFormat.AUTO, None),
                    ("plan.json", json.dumps(plan, sort_keys=True, separators=(",", ":")).encode(), workspace.ImportFormat.AUTO, None),
                    ("generate_sources", notebook, workspace.ImportFormat.SOURCE, workspace.Language.PYTHON)]
        for name, content, fmt, language in payloads:
            deployment["phase"] = "workspace_upload_" + name
            # WorkspaceClient.workspace is WorkspaceExt, whose multipart upload
            # accepts binary content. The service import_ base64 content limit
            # would reject this complete plan. AUTO keeps these headerless
            # Python/JSON assets as files; only the notebook uses SOURCE.
            client.workspace.upload(remote + "/" + name, content, format=fmt, language=language, overwrite=False)
            if fmt == workspace.ImportFormat.AUTO:
                with client.workspace.download(remote + "/" + name, format=workspace.ExportFormat.AUTO) as readback:
                    if readback.read() != content:
                        raise FixtureError("workspace_file_readback_mismatch")
        deployment["runtime_sha256"] = sha256(runtime).hexdigest()
        deployment["phase"] = "jobs_create"
        response = client.jobs.create(
            name="orion-synthetic-sources-" + remote.rsplit("/", 1)[-1],
            description="Empty-only original XML fixtures. Synthetic, no business/relationship parity.",
            max_concurrent_runs=1, performance_target=jobs.PerformanceTarget.STANDARD,
            tags={"aidevkit_project": "ai-dev-kit", "orion_scope": "synthetic_source_fixtures",
                  "orion_plan": plan["plan_sha256"][:32]},
            run_as=jobs.JobRunAs(user_name=user.user_name),
            tasks=[jobs.Task(task_key="generate_sources", max_retries=0, timeout_seconds=timeout_minutes * 60,
                            notebook_task=jobs.NotebookTask(notebook_path=remote + "/generate_sources",
                                source=jobs.Source.WORKSPACE, base_parameters={
                                    "plan_sha256": plan["plan_sha256"], "expected_user": user.user_name,
                                    "require_serializable": str(plan["require_serializable"]).lower()}))])
        if response.job_id is None:
            raise FixtureError("job_create_acknowledgement_missing")
        deployment["job_id"] = response.job_id
        write_json(directory / "deployment-created.json", deployment)
        deployment["phase"] = "jobs_run_now"
        started = client.jobs.run_now(job_id=response.job_id, idempotency_token=remote.rsplit("/", 1)[-1])
        deployment["run_id"] = started.response.run_id
        write_json(directory / "run-started.json", deployment)
        deployment["phase"] = "jobs_wait"
        completed = started.result(timeout=timedelta(minutes=timeout_minutes + 20))
        deployment["run_state"] = completed.state.as_dict() if completed.state else None
        deployment["status"] = "run_finished_receipt_pending"
    except BaseException as error:
        deployment["status"] = "failed_or_unconfirmed"
        deployment["errors"].append(str(error) if isinstance(error, FixtureError) else "sdk_failure_" + type(error).__name__)
    finally:
        if client is not None and remote is not None and deployment["run_id"] is not None:
            try:
                # AUTO direct download for files. Final file exists on success AND
                # handled failure. If unavailable, recover latest immutable
                # checkpoint and explicitly mark it partial, never successful.
                entries = list(client.workspace.list(remote))
                receipts = sorted(e.path for e in entries if e.path and re.fullmatch(
                    r"receipt-(?:final|start|[0-9]{5})\.json", e.path.rsplit("/", 1)[-1]))
                final = remote + "/receipt-final.json"
                candidate = final if final in receipts else next((p for p in reversed(receipts) if "receipt-0" in p),
                                                                 remote + "/receipt-start.json")
                with client.workspace.download(candidate, format=workspace.ExportFormat.AUTO) as exported:
                    receipt = json.loads(exported.read())
                if receipt.get("plan_sha256") != plan["plan_sha256"] or receipt.get("catalog") != CATALOG:
                    raise FixtureError("receipt_scope_mismatch")
                write_json(directory / "population-receipt.json", receipt)
                deployment["receipt_path"] = candidate
                if (candidate == final and receipt.get("status") == "completed"
                        and deployment["status"] == "run_finished_receipt_pending"
                        and deployment.get("run_state", {}).get("result_state") == "SUCCESS"):
                    deployment["status"] = "completed"
                else:
                    deployment["status"] = "failed_or_partial"
            except BaseException as error:
                deployment["status"] = "failed_receipt_unavailable"
                deployment["errors"].append("receipt_failure_" + type(error).__name__)
        deployment["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(directory / "deployment-receipt.json", deployment)
    if deployment["status"] != "completed":
        raise FixtureError("deployment_or_run_failed_receipt_at_" + str(directory))
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Local only; no identity or cloud calls")
    action.add_argument("--deploy-run", type=Path, metavar="PLAN", help="Explicit cloud deployment and run")
    parser.add_argument("--xml", type=Path, default=ROOT / "input" / "LumenExample.XML")
    parser.add_argument("--rows-per-table", type=int, default=10)
    parser.add_argument("--anchor-date", default="2026-01-01")
    parser.add_argument("--require-serializable", action="store_true")
    parser.add_argument("--timeout-minutes", type=int, default=180)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_minutes <= 1440:
        parser.error("timeout-minutes must be within 1..1440")
    if args.plan:
        path = create_local_plan(args.xml, rows_per_table=args.rows_per_table,
                                 anchor_date=args.anchor_date, require_serializable=args.require_serializable)
        print(json.dumps({"status": "planned_local_only", "plan": str(path)}))
    else:
        path = deploy_run(args.deploy_run, args.xml, timeout_minutes=args.timeout_minutes)
        print(json.dumps({"status": "completed", "receipt_directory": str(path)}))


if __name__ == "__main__":
    main()