"""Explicit authorized labs-user provisioning, immutable staging, Jobs run and DEST-only cleanup.

Run snapshots are insert-only. No app deployment, grants, production catalog or
shared empty-fixture mutation. SDK waiters wait for actual remote Job completion.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import shutil

from databricks.sdk.errors import NotFound
from databricks.sdk.service import jobs, workspace, sql
from preflight import client
from runtime import CATALOGS, DESTINATIONS, MARKER, OWNER, SCHEMA, canonical_rows, identifier, summary

WAREHOUSE = "e3a0fc2c08db05bb"

def now():
    return datetime.now(timezone.utc).isoformat()

def snapshot(folder, label, value):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"{stamp}-{label}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("SNAPSHOT", path)
    return path

def settings_match(actual, wanted):
    """Account only for observed server defaults, never waive execution policy.
    Jobs get omits explicit max_retries=0 and adds disabled=false/empty email
    notifications plus ALL_SUCCESS/timeout=0 on condition tasks.
    """
    if any(actual.get(key) for key in ("schedule", "continuous", "trigger", "triggers", "job_clusters", "email_notifications", "webhook_notifications")):
        return False
    if any(actual.get(key) != value for key, value in wanted.items() if key != "tasks"):
        return False
    if len(actual.get("tasks", [])) != len(wanted["tasks"]):
        return False
    observed = {task["task_key"]: task for task in actual["tasks"]}
    for expected in wanted["tasks"]:
        task = dict(observed.get(expected["task_key"], {}))
        if task.pop("disabled", False) or task.pop("email_notifications", {}) or task.pop("webhook_notifications", {}):
            return False
        task.setdefault("max_retries", 0)
        if "condition_task" in expected:
            if task.pop("run_if", "ALL_SUCCESS") != "ALL_SUCCESS" or task.pop("timeout_seconds", 0) != 0:
                return False
        if task != expected:
            return False
    return True

class Remote:
    def __init__(self, export_path, w=None):
        self.path = Path(export_path)
        self.c = json.loads((self.path / "contract.json").read_bytes())
        self.manifest = json.loads((self.path / "manifest.json").read_bytes())
        for name, digest in self.manifest["artifacts"].items():
            if hashlib.sha256((self.path / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Export artifact modified: {name}")
        self.w = w or client()
        if self.w.current_user.me().user_name != OWNER or self.c["owner"] != OWNER:
            raise RuntimeError("Unexpected labs user")
        if self.c["marker"] != MARKER or set(self.c["catalogs"].values()) != CATALOGS or self.c["schema"] != SCHEMA:
            raise RuntimeError("Unexpected destination contract")
        self.reports = self.path / "reports"
        self.identity_path = self.path / "job_identity.json"

    def statement(self, statement, parameters=None):
        response = self.w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE, statement=statement, wait_timeout="50s",
            on_wait_timeout=sql.ExecuteStatementRequestOnWaitTimeout.CANCEL,
            row_limit=51, byte_limit=4 * 1024 * 1024,
            parameters=[sql.StatementParameterListItem(name=k, value=str(v)) for k, v in (parameters or {}).items()])
        if response.status is None or response.status.state != sql.StatementState.SUCCEEDED:
            error = response.status.error.as_dict() if response.status and response.status.error else {}
            raise RuntimeError(json.dumps({"statement_id": response.statement_id,
                "state": response.status.state.value if response.status and response.status.state else "unknown", "error": error}))
        if not response.result or not response.result.data_array:
            return []
        if response.result.next_chunk_index is not None:
            raise RuntimeError("Unexpected chunking for bounded mock result")
        columns = [c.name for c in response.manifest.schema.columns]
        return [dict(zip(columns, row)) for row in response.result.data_array]

    def active_guard(self):
        for job in self.w.jobs.list(name="[MOCK ONLY] VYVX HIST PREP PULL"):
            actual = self.w.jobs.get(job.job_id)
            self.job_guard(actual)
            if any(self.w.jobs.list_runs(job_id=actual.job_id, active_only=True)):
                raise RuntimeError("Active demo Job prevents provisioning/staging/cleanup")

    @staticmethod
    def job_guard(job):
        if job.creator_user_name != OWNER or job.settings.run_as.user_name != OWNER or (
            job.settings.tags.get("orion_mock") != MARKER or job.settings.tags.get("orion_owner") != OWNER):
            raise RuntimeError("Existing Job does not have exact owner and stable mock markers")

    def schema_guard(self, catalog):
        if self.w.catalogs.get(catalog).owner != OWNER:
            raise RuntimeError("Catalog not owned by approved labs user")
        obj = self.w.schemas.get(f"{catalog}.{SCHEMA}")
        if obj.owner != OWNER or obj.comment != MARKER or (obj.properties or {}).get("orion.package_sha256") != self.c["package_sha256"]:
            raise RuntimeError("Unowned/unmarked existing demo schema")
        expected = {t["name"].split(".")[2].lower() for t in self.c["tables"].values() if t["name"].startswith(catalog + ".")}
        actual = {t.name.lower() for t in self.w.tables.list(catalog_name=catalog, schema_name=SCHEMA)}
        if not actual <= expected:
            raise RuntimeError("Unknown existing table in demo schema, no mutation permitted")

    def table_guard(self, key, *, destination=False):
        spec = self.c["tables"][key]
        name = spec["name"]
        identifier(name)
        if destination and (key not in DESTINATIONS or spec["role"] != "destination"):
            raise ValueError("Cannot truncate a source or unlisted destination")
        self.schema_guard(name.split(".")[0])
        obj = self.w.tables.get(name)
        props = obj.properties or {}
        if obj.owner != OWNER or props.get("orion.mock") != MARKER or props.get("orion.package_sha256") != self.c["package_sha256"]:
            raise RuntimeError("Unowned or unmarked table")
        observed = [(col.name, col.type_text.upper()) for col in obj.columns]
        expected = [tuple(c) for c in spec["columns"]]
        if observed != expected:
            raise RuntimeError(f"Unexpected live table schema: {name}: {observed} != {expected}")

    def rows(self, key):
        self.table_guard(key)
        data = self.statement(f"SELECT * FROM {identifier(self.c['tables'][key]['name'])} LIMIT 51")
        if len(data) > 50:
            raise RuntimeError("Demo table exceeds approved row cap")
        return data

    def source_snapshot(self):
        result = {}
        for key, spec in self.c["tables"].items():
            if spec["role"] == "source":
                rows = self.rows(key)
                if canonical_rows(rows, spec["columns"]) != canonical_rows(spec["rows"], spec["columns"]):
                    raise RuntimeError(f"Live source differs from seeded fixture: {key}")
                result[key] = summary(rows, spec["columns"])
        return result

    def provision(self):
        self.active_guard()
        for catalog in sorted(CATALOGS):
            if self.w.catalogs.get(catalog).owner != OWNER:
                raise RuntimeError("Unexpected catalog owner")
            try:
                self.w.schemas.get(f"{catalog}.{SCHEMA}")
            except NotFound:
                self.w.schemas.create(name=SCHEMA, catalog_name=catalog, comment=MARKER,
                    properties={"orion.package_sha256": self.c["package_sha256"]})
            self.schema_guard(catalog)
        created = []
        for key, spec in self.c["tables"].items():
            try:
                self.w.tables.get(spec["name"])
            except NotFound:
                cols = ",".join(f"`{name}` {dtype}" for name, dtype in spec["columns"])
                self.statement(f"CREATE TABLE {identifier(spec['name'])} ({cols}) USING DELTA COMMENT '{MARKER}: MOCK ONLY' TBLPROPERTIES ('orion.mock'='{MARKER}', 'orion.package_sha256'='{self.c['package_sha256']}')")
                created.append(spec["name"])
            self.table_guard(key)
            if spec["role"] == "source":
                existing = self.rows(key)
                if not existing:
                    # Only exact owner+marker+schema-verified empty mock inputs.
                    expressions, parameters = [], {}
                    for index, row in enumerate(spec["rows"]):
                        values = []
                        for column, dtype in spec["columns"]:
                            value = row[column]
                            parameter = f"r{index}_{column}"
                            values.append(f"CAST(:{parameter} AS {dtype})" if value is not None else f"CAST(NULL AS {dtype})")
                            if value is not None:
                                parameters[parameter] = value
                        expressions.append("SELECT " + ",".join(values))
                    self.statement(f"INSERT INTO {identifier(spec['name'])} " + " UNION ALL ".join(expressions), parameters)
                elif canonical_rows(existing, spec["columns"]) != canonical_rows(spec["rows"], spec["columns"]):
                    raise RuntimeError("Existing source differs: never overwrite populated source")
        sources = self.source_snapshot()
        snapshot(self.reports, "provision", {"time": now(), "created_tables": created, "sources": sources, "mock_only": True})
        return sources

    def stage_job(self):
        self.active_guard()
        root = self.manifest["workspace_path"]
        # Content-addressed release directory. No overwrite or deletion.
        try:
            self.w.workspace.get_status(root)
        except NotFound:
            self.w.workspace.mkdirs(root)
            for name in ("runtime.py", "fixtures.py", "dependency_check.py", "contract.json", "manifest.json"):
                self.w.workspace.upload(f"{root}/{name}", (self.path / name).read_bytes(), format=workspace.ImportFormat.RAW, overwrite=False)
            self.w.workspace.mkdirs(root + "/notebooks")
            for path in sorted((self.path / "notebooks").glob("*.py")):
                self.w.workspace.upload(f"{root}/notebooks/{path.stem}", path.read_bytes(),
                    format=workspace.ImportFormat.SOURCE, language=workspace.Language.PYTHON, overwrite=False)
        else:
            with self.w.workspace.download(root + "/manifest.json", format=workspace.ExportFormat.AUTO) as handle:
                previous = json.loads(handle.read())
            if previous != self.manifest:
                raise RuntimeError("Existing release manifest differs; never overwrite")
        settings_data = json.loads((self.path / "job.json").read_bytes())
        settings = jobs.JobSettings.from_dict(settings_data)
        candidates = list(self.w.jobs.list(name=settings.name))
        if len(candidates) > 1:
            raise RuntimeError("Duplicate named demo Jobs, no arbitrary selection")
        if candidates:
            job = self.w.jobs.get(candidates[0].job_id)
            self.job_guard(job)
            old = job.settings.as_dict()
            # Updating ONLY this exactly owned/marked Job to a new immutable release.
            if not settings_match(old, settings.as_dict()):
                self.w.jobs.update(job_id=job.job_id, new_settings=settings)
            job_id = job.job_id
        else:
            job_id = self.w.jobs.create(**{k: v for k, v in settings.__dict__.items() if v is not None}).job_id
        actual = self.w.jobs.get(job_id)
        self.job_guard(actual)
        wanted = settings.as_dict()
        actual_settings = actual.settings.as_dict()
        if not settings_match(actual_settings, wanted):
            raise RuntimeError("Deployed native Job differs from intended settings")
        identity = {"job_id": job_id, "owner": OWNER, "marker": MARKER, "workspace_path": root,
                    "url": f"{self.w.config.host}/jobs/{job_id}", "deployed_at": now()}
        if not self.identity_path.exists():
            with self.identity_path.open("x", encoding="utf-8") as handle:
                json.dump(identity, handle, indent=2)
        elif json.loads(self.identity_path.read_bytes())["job_id"] != job_id:
            raise RuntimeError("Saved demo job identity mismatch")
        snapshot(self.reports, "deployed-job", {**identity, "settings": actual_settings})
        return identity

    def run(self, identity, label):
        self.active_guard()
        job_id = identity["job_id"]
        waiter = self.w.jobs.run_now(job_id=job_id, job_parameters={"ack": "MOCK_ONLY"})
        run_id = waiter.response.run_id
        snapshot(self.reports, label + "-submitted", {"time": now(), "job_id": job_id, "run_id": run_id})
        print("REMOTE_RUN_SUBMITTED", run_id, flush=True)
        try:
            waiter.result(timeout=timedelta(hours=2))
        except Exception:
            failed = self.w.jobs.get_run(run_id)
            outputs = {}
            for task in failed.tasks or []:
                if task.run_id is not None and task.notebook_task is not None:
                    outputs[task.task_key] = self.w.jobs.get_run_output(task.run_id).as_dict()
            snapshot(self.reports, label + "-FAILED", {"time": now(), "run": failed.as_dict(), "task_outputs": outputs})
            raise
        run = self.w.jobs.get_run(run_id)
        outputs = {task.task_key: self.w.jobs.get_run_output(task.run_id).as_dict()
                   for task in run.tasks or [] if task.run_id is not None and task.notebook_task is not None}
        if run.state.result_state != jobs.RunResultState.SUCCESS:
            snapshot(self.reports, label + "-FAILED", {"time": now(), "run": run.as_dict(), "outputs": outputs})
            raise RuntimeError("Remote Job did not succeed")
        proof = json.loads(outputs["verify"]["notebook_output"]["result"])
        if proof["status"] != "PASS_MOCK_EXPECTATIONS_ONLY" or proof["sources_before"] != proof["sources_after"]:
            raise RuntimeError("Remote independent validation did not pass")
        if label == "resume":
            for task in run.tasks:
                if task.task_key in {"btp_load", "vbus_load"} and task.state.life_cycle_state != jobs.RunLifeCycleState.SKIPPED:
                    raise RuntimeError("Resume run unexpectedly executed prep load")
            for phase in ("btp_log_check", "vbus_log_check"):
                payload = json.loads(outputs[phase]["notebook_output"]["result"])
                if payload["target_success_rows"] != 1 or payload["needs_load"]:
                    raise RuntimeError("Resume log guard did not skip completed work")
        snapshot(self.reports, label + "-VERIFIED", {"time": now(), "run": run.as_dict(), "proof": proof, "task_outputs": outputs})
        print("REMOTE_RUN_VERIFIED", run_id, run.state.result_state.value, flush=True)
        return {"run_id": run_id, "proof": proof, "run": run.as_dict()}

    def cleanup(self, verified):
        if verified["proof"]["status"] != "PASS_MOCK_EXPECTATIONS_ONLY" or verified["run"]["state"]["result_state"] != "SUCCESS":
            raise RuntimeError("No successful independent proof, refusing cleanup")
        self.active_guard()
        before = self.source_snapshot()
        if before != verified["proof"]["sources_after"]:
            raise RuntimeError("Sources changed since remote verification")
        # Precheck ALL destinations before the first destructive statement.
        for key in sorted(DESTINATIONS):
            self.table_guard(key, destination=True)
        counts_before = {key: len(self.rows(key)) for key in sorted(DESTINATIONS)}
        for key in sorted(DESTINATIONS):
            self.active_guard()
            self.table_guard(key, destination=True)
            self.statement(f"TRUNCATE TABLE {identifier(self.c['tables'][key]['name'])}")
        after = self.source_snapshot()
        counts_after = {key: len(self.rows(key)) for key in sorted(DESTINATIONS)}
        if before != after or any(counts_after.values()):
            raise AssertionError("Destination cleanup or source preservation failed")
        return snapshot(self.reports, "CLEANUP_VERIFIED", {"time": now(), "verified_run_id": verified["run_id"],
            "sources_before": before, "sources_after": after, "destination_counts_before": counts_before,
            "destination_counts_after": counts_after, "status": "DESTINATIONS_EMPTY_SOURCES_PRESERVED", "no_active_job": True})

def validate_bundle(path):
    # CLI validation residue is confined to and removed with an owned temp stage.
    with tempfile.TemporaryDirectory(prefix="orion-vyvx-validate-") as scratch:
        shutil.copytree(path, scratch, dirs_exist_ok=True, ignore=shutil.ignore_patterns("reports", ".databricks"))
        subprocess.run(["databricks", "bundle", "validate", "--profile", "labs", "-t", "demo"], cwd=scratch, check=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export")
    parser.add_argument("--action", choices=("validate", "execute"), required=True)
    args = parser.parse_args()
    if args.action == "validate":
        validate_bundle(args.export)
    else:
        remote = Remote(args.export)
        baseline = remote.provision()
        identity = remote.stage_job()
        fresh = remote.run(identity, "fresh")
        if fresh["proof"]["sources_before"] != baseline:
            raise RuntimeError("Baseline changed before execution")
        resumed = remote.run(identity, "resume")
        for key in ("AE2E_BILL_TAMER_PREP", "AE2E_VBUS_PREP", "AE2E_JOB_LOG", "AE2E_UNIX_JOB_LOG"):
            if fresh["proof"]["outputs"][key]["sha256"] != resumed["proof"]["outputs"][key]["sha256"]:
                raise RuntimeError("Resume mutated completed output/log records")
        cleanup = remote.cleanup(resumed)
        snapshot(remote.reports, "FINAL", {"time": now(), "job": identity, "fresh_run_id": fresh["run_id"],
            "resume_run_id": resumed["run_id"], "cleanup_report": str(cleanup), "source_tables":
            [t["name"] for t in remote.c["tables"].values() if t["role"] == "source"],
            "destination_tables": [remote.c["tables"][key]["name"] for key in sorted(DESTINATIONS)],
            "status": "READY_TO_RERUN_MOCK_ONLY", "informatica_parity": False})
        print("SUCCESS_READY_TO_RERUN", identity["url"])