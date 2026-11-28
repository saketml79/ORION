"""Read-only release gate for exact XML fixtures and the existing source seed Job."""
import argparse
import base64
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.workspace import ExportFormat

from workbench.complete_target_fixtures import (
    CATALOGS, FORMAT, HOST, PACKAGE, USER, fingerprint, verify_table,
)
from workbench.fixture_metadata import _column, _signature
from workbench.source_fixtures import identifier
from workbench.synthetic_source_data import CATALOG, notebook_source


def digest(data):
    return sha256(data).hexdigest()


def parse_json(data):
    def reject_constant(_value):
        raise ValueError("Nonfinite JSON is forbidden")
    return json.loads(data, parse_constant=reject_constant)


def checked_plan(data, expected_sha):
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise ValueError("An exact reviewed plan SHA is required")
    plan = parse_json(data)
    if (plan.get("plan_sha256") != expected_sha
            or fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}) != expected_sha
            or plan.get("package_sha256") != PACKAGE):
        raise ValueError("Reviewed plan/package checksum mismatch")
    return plan


def workspace_file(client, path):
    parts = PurePosixPath(path)
    if (not path.startswith("/Workspace/Users/" + USER + "/")
            or ".." in parts.parts or "\\" in path):
        raise ValueError("Only the fixture owner's exact Workspace file path is allowed")
    with client.workspace.download(path, format=ExportFormat.AUTO) as stream:
        return stream.read()


def target_plan(data, expected_sha):
    plan = checked_plan(data, expected_sha)
    if (plan.get("format") != FORMAT or plan.get("host") != HOST
            or plan.get("user") != USER or plan.get("catalogs") != list(CATALOGS)
            or plan.get("targets_per_catalog") != 307
            or plan.get("schemas_per_catalog") != 14 or len(plan.get("objects", [])) != 921):
        raise ValueError("Entire reviewed three-layer target scope is required")
    names = set()
    counts = Counter()
    for obj in plan["objects"]:
        table = obj["target"]
        for name in (obj["catalog"], table["schema"], table["table"]):
            identifier(name)
        full_name = ".".join((obj["catalog"], table["schema"], table["table"]))
        if (obj["catalog"] not in CATALOGS or obj["full_name"] != full_name
                or full_name in names or not table["columns"]):
            raise ValueError("Invalid or repeated canonical target identity")
        names.add(full_name)
        counts[obj["catalog"]] += 1
    if counts != Counter({catalog: 307 for catalog in CATALOGS}):
        raise ValueError("Each target catalog requires exactly 307 canonical definitions")
    if any(len({obj["target"]["schema"] for obj in plan["objects"]
                if obj["catalog"] == catalog}) != 14 for catalog in CATALOGS):
        raise ValueError("Each target catalog requires its 14 canonical schemas")
    return plan


def source_job(client, job_id, reviewed):
    job = client.jobs.get(job_id)
    if (job.creator_user_name != USER or job.settings is None
            or fingerprint(job.settings.as_dict()) != reviewed["job_settings_sha256"]
            or job.settings.run_as is None or job.settings.run_as.user_name != USER
            or len(job.settings.tasks or []) != 1):
        raise ValueError("Existing source seed Job definition/owner/Run-as drift")
    task = job.settings.tasks[0].notebook_task
    if task is None or getattr(task.source, "value", task.source) != "WORKSPACE":
        raise ValueError("Existing single Workspace notebook seed task is required")
    prefix = "/Workspace/Users/" + USER + "/orion-synthetic-source-data/"
    if not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{32}/generate_sources", task.notebook_path):
        raise ValueError("Source seed notebook is outside its reviewed owner scope")
    remote = task.notebook_path.rsplit("/", 1)[0]
    runtime = workspace_file(client, remote + "/synthetic_source_runtime.py")
    if digest(runtime) != reviewed["runtime_sha256"]:
        raise ValueError("Deployed source runtime bytes differ from the reviewed fingerprint")
    exported = client.workspace.export(task.notebook_path, format=ExportFormat.SOURCE)
    if exported.content is None or base64.b64decode(exported.content, validate=True) != notebook_source(remote).encode():
        raise ValueError("Deployed source notebook differs from its reviewed generator")
    parameters = task.base_parameters or {}
    source = checked_plan(workspace_file(client, remote + "/plan.json"),
                          parameters.get("plan_sha256", ""))
    if (source.get("catalog") != CATALOG or source.get("table_count") != 583
            or source.get("schema_count") != 136 or len(source.get("tables", [])) != 583):
        raise ValueError("Entire reviewed source fixture scope is required")
    return source


def table_inventory(client, catalog, schemas):
    if client.catalogs.get(catalog).owner != USER:
        raise ValueError("Fixture catalog ownership drift")
    tables = {}
    for schema in sorted(schemas):
        identifier(schema)
        for table in client.tables.list(catalog, schema, include_browse=True,
                                       omit_columns=False, omit_properties=False):
            if table.full_name in tables or not table.full_name.startswith(catalog + "." + schema + "."):
                raise ValueError("Ambiguous metadata table inventory")
            tables[table.full_name] = table
    return tables


def require_table(tables, name):
    if name not in tables:
        raise ValueError("An expected canonical fixture is missing; owner provisioning is required")
    return tables[name]


def verify_sources(client, plan):
    expected = {}
    for table in plan["tables"]:
        identifier(table["schema"])
        identifier(table["table"])
        name = ".".join((CATALOG, table["schema"], table["table"]))
        if name in expected:
            raise ValueError("Repeated canonical source identity")
        expected[name] = table
    tables = table_inventory(client, CATALOG, {t["schema"] for t in expected.values()})
    for name, expected_table in expected.items():
        info = require_table(tables, name)
        properties = info.properties or {}
        if (info.owner != USER or getattr(info.table_type, "value", info.table_type) != "MANAGED"
                or getattr(info.data_source_format, "value", info.data_source_format) != "DELTA"
                or properties.get("orion.fixture") != "xml_source_schema_only"
                or properties.get("orion.package_sha256") != PACKAGE
                or not info.columns
                or _signature([_column(c) for c in info.columns]) != _signature(expected_table["columns"])):
            raise ValueError("Source ordered schema/owner/managed Delta/provenance mismatch")
    return len(expected)


def verify_targets(client, plan):
    counts = {}
    for catalog in CATALOGS:
        objects = [obj for obj in plan["objects"] if obj["catalog"] == catalog]
        tables = table_inventory(client, catalog, {obj["target"]["schema"] for obj in objects})
        for obj in objects:
            verify_table(require_table(tables, obj["full_name"]), obj)
        counts[catalog] = len(objects)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--target-plan", type=Path)
    location.add_argument("--target-plan-workspace")
    parser.add_argument("--expected-target-plan-sha", required=True)
    parser.add_argument("--source-job-id", type=int, required=True)
    parser.add_argument("--source-fingerprint", type=Path,
                        default=ROOT / "deployment" / "source-seed-deployment-fingerprint.json")
    parser.add_argument("--profile", choices=("labs",))
    args = parser.parse_args(argv)
    try:
        if args.source_job_id <= 0:
            raise ValueError("An exact existing source seed Job ID is required")
        reviewed = parse_json(args.source_fingerprint.read_bytes())
        if reviewed.get("format") != "orion-source-seed-deployment-fingerprint-v1":
            raise ValueError("Reviewed source deployment fingerprint is required")
        source = (ROOT / "workbench" / "synthetic_source_runtime.py").read_text(encoding="utf-8")
        if digest(source.encode()) != reviewed["runtime_git_sha256"]:
            raise ValueError("Reviewed source runtime Git revision drift")
        client = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
        if client.config.host.rstrip("/") != HOST:
            raise ValueError("Selected workspace differs from the reviewed fixture workspace")
        raw = (args.target_plan.read_bytes() if args.target_plan
               else workspace_file(client, args.target_plan_workspace))
        targets = target_plan(raw, args.expected_target_plan_sha)
        sources = source_job(client, args.source_job_id, reviewed)
        source_count = verify_sources(client, sources)
        target_counts = verify_targets(client, targets)
        print(json.dumps({"status": "verified", "sources": source_count,
                          "targets": list(target_counts.values()),
                          "source_job_definition_and_artifacts": "verified",
                          "data_rows_read": False, "cloud_mutations": False}))
    except (DatabricksError, ValueError, KeyError, TypeError, OSError) as error:
        print("Fixture deployment preflight failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
