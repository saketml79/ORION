"""Explicit, single-notebook Databricks serverless runs for saved candidate drafts.

This never creates a persistent Job or deploys a bundle. Every run uploads one
checksum-verified notebook to the signed-in user's workspace and submits a
one-time serverless notebook task only after explicit user consent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


MAX_PARAMETERS = 24
MAX_PARAMETER_LENGTH = 1024
MAX_OUTPUT_CHARS = 48_000
RUN_TIMEOUT_SECONDS = 1800
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FQN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
_WIDGET_GET = re.compile(r"(?:dbutils\.widgets\.get|_get_widget)\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


class NotebookRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent: Literal[True]
    artifact_path: StrictStr = Field(min_length=1, max_length=512)
    cell_number: int = Field(ge=1, le=128)
    expected_workspace: StrictStr = Field(min_length=8, max_length=256)
    parameters: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=MAX_PARAMETERS)
    side_effects_confirmed: StrictBool = False
    confirmed_output_table: StrictStr = Field(default="", max_length=256)


class NotebookRunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]


class NotebookAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]
    artifact_path: StrictStr = Field(min_length=1, max_length=512)
    expected_workspace: StrictStr = Field(min_length=8, max_length=256)


def parameter_names(notebook: dict) -> set[str]:
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("Notebook cells are missing")
    names: set[str] = set()
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else source
        if not isinstance(source, str):
            raise ValueError("Notebook source is invalid")
        names.update(_WIDGET_GET.findall(source))
    return names


def _prefix_cells(notebook: dict, cell_number: int) -> list[dict]:
    cells = notebook.get("cells")
    if not isinstance(cells, list) or not 1 <= cell_number <= len(cells):
        raise HTTPException(422, "Selected cell number is not present in the saved notebook")
    prefix = cells[:cell_number]
    if prefix[-1].get("cell_type") != "code":
        raise HTTPException(422, "Choose a code cell. Markdown cells cannot be run.")
    return prefix


def _prefix_source(cells: list[dict]) -> str:
    parts = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        parts.append("".join(source) if isinstance(source, list) else str(source))
    return "\n".join(parts)


def _parameter_policy(relative_path: str, parameters: dict[str, str], side_effects_confirmed: bool,
                      confirmed_output_table: str, scenario: str, prefix_source: str = "") -> dict:
    name = relative_path.rsplit("/", 1)[-1]
    if name == "t_wait.ipynb":
        raw = parameters.get("wait_seconds", "")
        try:
            seconds = int(raw) if raw.strip() else 0
        except ValueError:
            raise HTTPException(422, "wait_seconds must be a whole number from 0 to 300") from None
        if not 0 <= seconds <= 300:
            raise HTTPException(422, "wait_seconds must be between 0 and 300 for an interactive test run")
        parameters["wait_seconds"] = str(seconds)
        parameters["demo_mode"] = "true"
        return {"side_effects": "No table reads or writes. Waits for the supplied duration.",
                "wait_seconds": seconds, "mutates_data": False}

    if name == "t_counter.ipynb":
        query_executes = "source_df = spark.sql(source_sql)" in prefix_source
        writes_output = bool(re.search(r"\.write\.(?:format\([^)]*\)\.)?mode\([^)]*\)\.saveAsTable\(output_fqn\)", prefix_source))
        post_insert = "spark.sql(post_insert_sql)" in prefix_source
        delete = "spark.sql(delete_sql)" in prefix_source
        mutates_data = writes_output or post_insert or delete
        if query_executes and scenario != "bronze":
            raise HTTPException(409, "This notebook assumes its source table is already in Unity Catalog. Select the Unity Catalog source scenario.")
        required = ("uc_catalog", "bronze_source_table") if query_executes else ()
        if mutates_data and not parameters.get("target_table", "").strip():
            required += ("dev_catalog", "dev_schema")
        for key in required:
            if not parameters.get(key, "").strip():
                raise HTTPException(422, f"Enter the required notebook parameter: {key}")
        source = parameters.get("bronze_source_table", "").strip()
        if query_executes and not _FQN.fullmatch(source):
            raise HTTPException(422, "bronze_source_table must be catalog.schema.table")
        for key in ("uc_catalog", "ae2e_schema", "dev_catalog", "dev_schema", "dev_table"):
            value = parameters.get(key, "")
            if value and not _IDENTIFIER.fullmatch(value.strip()):
                raise HTTPException(422, f"{key} must be a Unity Catalog identifier")
        output = ""
        if mutates_data:
            target = parameters.get("target_table", "").strip()
            output = target or ".".join((parameters.get("dev_catalog", "").strip(), parameters.get("dev_schema", "").strip(),
                                        parameters.get("dev_table", "AE2E_JOB_DEP_CHK_COUNT_DEV").strip() or "AE2E_JOB_DEP_CHK_COUNT_DEV"))
            if not _FQN.fullmatch(output):
                raise HTTPException(422, "The output table must be a fully qualified catalog.schema.table")
            if source.casefold() == output.casefold():
                raise HTTPException(422, "Source and output tables must differ")
        if mutates_data and (not side_effects_confirmed or confirmed_output_table != output):
            raise HTTPException(403, "Confirm the exact output table and possible row deletion before running this cell")
        if parameters.get("demo_mode", "true").strip().lower() not in {"true", "1", "yes"}:
            raise HTTPException(422, "Interactive candidate runs require demo_mode=true")
        parameters["demo_mode"] = "true"
        if output:
            parameters["target_table"] = output
        if mutates_data:
            side_effects = f"Reads {source}; appends to {output}; may delete rows for TEMPLATE_ID=18300 when the count reaches 60. Delta statements commit separately."
        elif query_executes:
            side_effects = f"Reads from {source}. No table writes occur in the cells through this selection."
        else:
            side_effects = "Runs parameter parsing and validation only. No table reads or writes occur in the cells through this selection."
        return {"side_effects": side_effects, "mutates_data": mutates_data,
            "source_table": source or None, "output_table": output or None}

    if name in {"t_child_launch.ipynb", "t_email.ipynb"}:
        if parameters.get("demo_mode", "true").strip().lower() not in {"true", "1", "yes"}:
            raise HTTPException(422, "This candidate only supports demo_mode=true. Real child launches and email delivery are not enabled.")
        parameters["demo_mode"] = "true"
        if name == "t_child_launch.ipynb":
            parameters["child_job_id"] = ""
            return {"side_effects": "Prints a simulated child-launch receipt only. No child Job is started.",
                    "mutates_data": False}
        return {"side_effects": "Prints a simulated email receipt only. No email is sent.",
                "mutates_data": False}

    raise HTTPException(422, "Only the four reviewed WIL notebook tasks can be run from this view")


def _clean_output(value, limit=MAX_OUTPUT_CHARS):
    from workbench.redaction import redact_text
    if not isinstance(value, str):
        return ""
    sanitized = redact_text(value)
    return sanitized[-limit:]


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sdk_value(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _local_time():
    return datetime.now(timezone.utc).isoformat()


def install_notebook_runner(app):
    """Install routes on a local package app only; no hosted identity fallback."""
    from workbench.app import _JOB_ID

    service = app.state.service
    run_root = service.state / "notebook-runs"
    run_lock = Lock()

    def load_candidate(job_id: str, artifact_path: str, *, for_execution: bool = True):
        if not _JOB_ID.fullmatch(job_id):
            raise HTTPException(404, "Saved notebook draft was not found")
        index = service.require()
        job = service.record(job_id)
        if job.get("kind") != "spark_draft" or job.get("package_hash") != index.model["package"]["sha256"]:
            raise HTTPException(404, "Saved notebook draft was not found in this package")
        from workbench.draft_artifact_reads import read_result
        receipt = read_result(service.root, job, job_id)
        manifest = receipt["manifest"]
        try:
            from workbench.redesign import store_for
            exact = store_for(service).get(manifest["design_id"], manifest["package_hash"], manifest["design_version"])
        except (KeyError, ValueError):
            raise HTTPException(409, "Saved design is unavailable. Refresh the design before running code.") from None
        if job.get("status") != "completed" or exact.get("input_version") != manifest.get("input_version"):
            raise HTTPException(409, "Saved notebook receipt does not match its original design")
        # Attachment copies an exact saved revision for review, not execution.
        # Do not recompute current inputs or require the latest design here.
        if for_execution:
            latest = store_for(service).get(manifest["design_id"], manifest["package_hash"])
            source = exact.get("source") or {}
            if source.get("kind") == "loop":
                current_input_version = app.state.loops._inputs(index, exact["workflow_id"], exact["scenario"])[0]["input_version"]
            else:
                current_input_version = service.agent_inputs(index, exact["workflow_id"], exact["scenario"])["input_version"]
            if latest["version"] != exact["version"] or exact.get("input_version") != current_input_version:
                raise HTTPException(409, "Only the latest design for current inputs can be run")
        if not isinstance(artifact_path, str) or not artifact_path.startswith("notebooks/") or not artifact_path.endswith(".ipynb"):
            raise HTTPException(422, "Select a notebook artifact from this saved result")
        content = receipt["staged_artifacts"].get(artifact_path) or receipt["artifacts"].get(artifact_path)
        if not isinstance(content, str):
            raise HTTPException(404, "Notebook file was not found in this saved result")
        try:
            notebook = json.loads(content)
            if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
                raise ValueError
            for cell in notebook["cells"]:
                metadata = cell.get("metadata") if isinstance(cell, dict) else None
                cell_type = cell.get("cell_type") if isinstance(cell, dict) else None
                expected_language = {"code": "python", "markdown": "markdown"}.get(cell_type)
                if (expected_language is None or not isinstance(metadata, dict)
                        or not isinstance(metadata.get("id"), str) or not metadata["id"]
                        or metadata.get("language") != expected_language):
                    raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(409, "Saved notebook format is invalid; no run submitted") from None
        return index, job, manifest, exact, content, notebook

    from workbench.notebook_edits import install as install_notebook_edits
    install_notebook_edits(app, load_candidate, run_lock)

    @app.post("/api/jobs/{job_id}/spark-drafts/attach")
    def attach_saved_notebook(job_id: str, body: NotebookAttachRequest):
        """Upload exact saved cells for native portal execution. Never submit a Job."""
        if body.consent is not True:
            raise HTTPException(403, "Confirm notebook attachment")
        return app.state.notebook_editor.attach(job_id, body)

    @app.post("/api/jobs/{job_id}/spark-drafts/run", status_code=202)
    def run_saved_notebook(job_id: str, body: NotebookRunRequest):
        if not service.agent_slot.acquire(blocking=False):
            raise HTTPException(409, "Wait for the current agent or notebook submission to finish before starting a notebook")
        if not run_lock.acquire(blocking=False):
            service.agent_slot.release()
            raise HTTPException(409, "Another notebook run is being submitted by this workbench")
        try:
            return _submit_saved_notebook(job_id, body)
        finally:
            run_lock.release()
            service.agent_slot.release()

    def _submit_saved_notebook(job_id: str, body: NotebookRunRequest):
        expected_names = {"consent", "artifact_path", "cell_number", "expected_workspace", "parameters",
                          "side_effects_confirmed", "confirmed_output_table"}
        if set(body.model_fields_set) != expected_names or body.consent is not True:
            raise HTTPException(403, "Explicit confirmation is required to run the selected notebook")
        index, job, manifest, exact, content, notebook = load_candidate(job_id, body.artifact_path)
        prefix = _prefix_cells(notebook, body.cell_number)
        prefix_notebook = {**notebook, "cells": prefix}
        prefix_source = _prefix_source(prefix)
        for cell in prefix_notebook["cells"]:
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
        execution_content = json.dumps(prefix_notebook, ensure_ascii=False, separators=(",", ":"))
        execution_digest = sha256(execution_content.encode("utf-8")).hexdigest()
        from workbench.databricks_models import AuthenticationUnavailable, profile_identity, _profile_host
        from databricks.sdk.service import jobs as jobs_api, workspace as workspace_api

        allowed_parameters = parameter_names(prefix_notebook)
        if set(body.parameters) != allowed_parameters:
            raise HTTPException(422, "Run parameters must match the selected notebook's declared widgets")
        if any(len(value) > MAX_PARAMETER_LENGTH for value in body.parameters.values()):
            raise HTTPException(413, "Notebook parameter exceeds the 1024-character limit")
        parameters = dict(body.parameters)
        policy = _parameter_policy(body.artifact_path, parameters, body.side_effects_confirmed,
                                   body.confirmed_output_table, manifest.get("scenario", ""), prefix_source)
        if body.side_effects_confirmed is not True:
            raise HTTPException(403, "Confirm the displayed notebook effects before running")
        try:
            configured_host = _profile_host()
        except AuthenticationUnavailable:
            raise HTTPException(401, "The labs workspace is not configured. Connect the Databricks workspace before running code.") from None
        if body.expected_workspace.rstrip("/") != configured_host.rstrip("/"):
            raise HTTPException(409, "The selected workspace changed. Refresh workspace settings before running code.")
        try:
            identity = profile_identity("labs")
        except AuthenticationUnavailable:
            raise HTTPException(401, "Databricks sign-in expired. Reauthenticate the labs profile, then try the Run button again.") from None
        if identity.host != configured_host:
            raise HTTPException(409, "Databricks identity does not match the selected workspace")
        try:
            user = identity.client.current_user.me()
        except Exception:
            raise HTTPException(401, "Databricks could not confirm the signed-in user. Reauthenticate before running code.") from None
        if not user.id or user.id != identity.user_id or not isinstance(user.user_name, str) or not re.fullmatch(
                r"[A-Za-z0-9._@+-]{1,256}", user.user_name):
            raise HTTPException(403, "The Databricks user identity could not be verified")

        run_root.mkdir(parents=True, exist_ok=True)
        prior_receipts = list(run_root.glob("*.json"))
        if len(prior_receipts) > 10_000:
            raise HTTPException(503, "Notebook run history is too large to check safely; no run submitted")
        active_statuses = {"preparing", "submitting", "submission_unknown", "queued", "pending", "running", "waiting"}
        for path in prior_receipts:
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise HTTPException(503, "Notebook run history could not be verified; no run submitted") from None
            same_scope = (previous.get("package_hash") == job.get("package_hash")
                          and previous.get("design_id") == exact["id"]
                          and previous.get("design_version") == exact["version"]
                          and previous.get("artifact_sha256") == execution_digest
                          and previous.get("user_id") == identity.user_id)
            if same_scope and previous.get("status") in active_statuses:
                raise HTTPException(409, "A run for this notebook is active or its submission is unconfirmed. Check the Databricks Jobs page before retrying.")

        run_receipt_id = uuid4().hex
        remote_dir = (f"/Workspace/Users/{user.user_name}/ORION-notebook-runs/"
                      f"{exact['id'][:12]}-v{exact['version']}-{run_receipt_id}")
        remote_notebook = remote_dir + f"/{Path(body.artifact_path).stem}_through_cell_{body.cell_number}.ipynb"
        receipt_path = run_root / f"{run_receipt_id}.json"
        receipt = {
            "id": run_receipt_id, "status": "preparing", "submission_status": "not_submitted",
            "workspace": identity.host, "user_id": identity.user_id, "user_name": user.user_name,
            "package_hash": job["package_hash"], "workflow_id": job["workflow_id"],
            "design_id": exact["id"], "design_version": exact["version"],
            "input_version": exact["input_version"], "writer_job_id": job_id,
            "artifact_path": body.artifact_path, "cell_number": body.cell_number,
            "artifact_sha256": execution_digest,
            "remote_notebook_path": remote_notebook, "parameters": parameters, "policy": policy,
            "created_at": _local_time(), "workspace_run_id": None,
        }
        _atomic_json(receipt_path, receipt)
        action_phase = "workspace_folder"
        try:
            identity.client.workspace.mkdirs(remote_dir)
            action_phase = "notebook_upload"
            identity.client.workspace.upload(remote_notebook, execution_content.encode("utf-8"),
                format=workspace_api.ImportFormat.JUPYTER, language=workspace_api.Language.PYTHON,
                overwrite=False)
            _atomic_json(receipt_path, {**receipt, "status": "submitting", "submission_status": "submitting"})
            action_phase = "serverless_run_submit_call"
            waiter = identity.client.jobs.submit(
                run_name=f"ORION {job['workflow_id'][:10]} {body.artifact_path.rsplit('/', 1)[-1]}",
                idempotency_token=run_receipt_id,
                performance_target=jobs_api.PerformanceTarget.STANDARD,
                timeout_seconds=RUN_TIMEOUT_SECONDS,
                tasks=[jobs_api.SubmitTask(
                    task_key="run_notebook", max_retries=0, timeout_seconds=RUN_TIMEOUT_SECONDS,
                    notebook_task=jobs_api.NotebookTask(notebook_path=remote_notebook,
                        source=jobs_api.Source.WORKSPACE, base_parameters=parameters))])
            action_phase = "serverless_run_submit_response"
            response = getattr(waiter, "response", None)
            workspace_run_id = _sdk_value(response, "run_id")
            action_phase = "serverless_run_identifier"
            if not isinstance(workspace_run_id, int) or workspace_run_id <= 0:
                raise RuntimeError("Databricks did not return a run identifier")
            receipt.update(status="queued", submission_status="submitted", workspace_run_id=workspace_run_id,
                           submitted_at=_local_time())
            _atomic_json(receipt_path, receipt)
        except Exception as error:
            # Do not retry an ambiguous submit. The persisted state says whether
            # a remote run may have been accepted and keeps the unique path for inspection.
            phase = "submission_unknown" if receipt.get("submission_status") == "submitting" else "failed_before_submit"
            receipt.update(status=phase, submission_status=phase,
                           message=("Databricks may have accepted the run. Check the workspace Jobs page before retrying."
                                    if phase == "submission_unknown" else
                                    f"Notebook {action_phase} failed ({type(error).__name__}). No run was confirmed."))
            _atomic_json(receipt_path, receipt)
            return {"id": run_receipt_id, "status": phase, "submission_status": phase,
                    "workspace": identity.host, "workflow_id": job["workflow_id"],
                    "design_version": exact["version"], "artifact_path": body.artifact_path,
                    "policy": policy, "message": receipt["message"], "created_at": receipt["created_at"]}
        return {key: receipt.get(key) for key in ("id", "status", "submission_status", "workspace",
            "workspace_run_id", "workflow_id", "design_version", "artifact_path", "cell_number", "policy", "created_at")}

    @app.get("/api/notebook-runs/catalogs")
    def notebook_run_catalogs():
        from itertools import islice
        from workbench.databricks_models import AuthenticationUnavailable, profile_identity, _profile_host
        try:
            host = _profile_host()
            identity = profile_identity("labs")
            user = identity.client.current_user.me()
            if identity.host != host or not user.id or user.id != identity.user_id:
                raise HTTPException(403, "Databricks user identity could not be verified")
            names = []
            for item in islice(identity.client.catalogs.list(max_results=200), 200):
                value = _sdk_value(item, "name")
                if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
                    names.append(value)
            names = sorted(set(names), key=str.casefold)
            preferred = names[0] if len(names) == 1 else None
            return {"workspace": host, "catalogs": names, "default_catalog": preferred,
                    "user_id": identity.user_id}
        except HTTPException:
            raise
        except AuthenticationUnavailable:
            raise HTTPException(401, "Databricks sign-in expired. Reauthenticate the labs profile to load catalog choices.") from None
        except Exception as error:
            raise HTTPException(502, f"Catalog choices could not be loaded ({type(error).__name__}). No notebook was run.") from None

    @app.get("/api/notebook-runs/{receipt_id}")
    def notebook_run_status(receipt_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", receipt_id):
            raise HTTPException(404, "Notebook run was not found")
        record_path = run_root / f"{receipt_id}.json"
        try:
            receipt = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404, "Notebook run was not found") from None
        index = service.require()
        if receipt.get("package_hash") != index.model["package"]["sha256"]:
            raise HTTPException(404, "Notebook run belongs to another selected package")
        return _poll_run(receipt_path=record_path, receipt=receipt)

    @app.post("/api/notebook-runs/{receipt_id}/cancel", status_code=202)
    def cancel_notebook_run(receipt_id: str, body: NotebookRunCancelRequest):
        if not re.fullmatch(r"[a-f0-9]{32}", receipt_id) or body.consent is not True:
            raise HTTPException(403, "Explicit stop confirmation is required")
        record_path = run_root / f"{receipt_id}.json"
        try:
            receipt = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404, "Notebook run was not found") from None
        if receipt.get("submission_status") != "submitted" or not isinstance(receipt.get("workspace_run_id"), int):
            raise HTTPException(409, "This notebook has no confirmed workspace run to stop")
        from workbench.databricks_models import AuthenticationUnavailable, profile_identity, _profile_host
        try:
            host = _profile_host()
            identity = profile_identity("labs")
            user = identity.client.current_user.me()
            if (identity.host != host or receipt.get("workspace") != host
                    or not user.id or user.id != receipt.get("user_id") or identity.user_id != user.id):
                raise HTTPException(403, "The current Databricks user cannot stop this run")
            if receipt.get("stop_requested_at"):
                return {"id": receipt_id, "status": "cancel_requested",
                        "message": "Stop was already requested. Checking the actual Databricks run state."}
            current = identity.client.jobs.get_run(receipt["workspace_run_id"])
            state = _sdk_value(_sdk_value(current, "state"), "life_cycle_state", "UNKNOWN")
            lifecycle = str(state).split(".")[-1]
            if lifecycle not in {"PENDING", "QUEUED", "RUNNING", "BLOCKED", "WAITING_FOR_RETRY"}:
                return {"id": receipt_id, "status": lifecycle.lower(), "message": "The Databricks run is no longer active."}
            identity.client.jobs.cancel_run(receipt["workspace_run_id"])
            receipt.update(stop_requested_at=_local_time(), status="cancel_requested")
            _atomic_json(record_path, receipt)
            return {"id": receipt_id, "status": "cancel_requested", "message": "Stop requested. Refreshing actual Databricks run state."}
        except HTTPException:
            raise
        except AuthenticationUnavailable:
            raise HTTPException(401, "Databricks sign-in expired. Reauthenticate to stop this run.") from None
        except Exception as error:
            raise HTTPException(502, f"Stop was not confirmed ({type(error).__name__}). Check Databricks Jobs; no duplicate run was submitted.") from None


def _poll_run(receipt_path: Path, receipt: dict):
    """Read an existing run. Never resubmits, repairs, cancels or deploys it."""
    from workbench.databricks_models import AuthenticationUnavailable, profile_identity, _profile_host

    if receipt.get("submission_status") != "submitted" or not isinstance(receipt.get("workspace_run_id"), int):
        return {key: receipt.get(key) for key in ("id", "status", "workflow_id", "design_version", "artifact_path",
            "cell_number", "workspace", "submission_status", "message", "created_at")}
    try:
        expected = _profile_host()
        if expected != receipt.get("workspace"):
            raise HTTPException(409, "The labs workspace changed. Reconnect and verify this run's scope.")
        identity = profile_identity("labs")
        if identity.host != expected or identity.user_id != receipt.get("user_id"):
            raise HTTPException(403, "The current Databricks user does not own this run")
        run = identity.client.jobs.get_run(receipt["workspace_run_id"])
        state = _sdk_value(run, "state")
        lifecycle = str(_sdk_value(state, "life_cycle_state", "UNKNOWN")).split(".")[-1]
        result_state = str(_sdk_value(state, "result_state", "")).split(".")[-1] or None
        response = {**{key: receipt.get(key) for key in ("id", "workflow_id", "design_version", "artifact_path",
                    "cell_number", "workspace", "workspace_run_id", "created_at")},
                    "status": lifecycle.lower(), "result_state": result_state,
                    "run_page_url": _sdk_value(run, "run_page_url"), "submission_status": "submitted"}
        if lifecycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            output = identity.client.jobs.get_run_output(receipt["workspace_run_id"])
            notebook_output = _sdk_value(output, "notebook_output")
            response["output"] = _clean_output(_sdk_value(notebook_output, "result", ""))
            response["logs"] = _clean_output(_sdk_value(output, "logs", ""))
            response["output_truncated"] = bool(_sdk_value(notebook_output, "truncated", False)
                                                 or _sdk_value(output, "logs_truncated", False))
            response["error"] = _clean_output(_sdk_value(output, "error", ""), 4000)
        _atomic_json(receipt_path, {**receipt, "status": response["status"],
                                    "result_state": response.get("result_state"),
                                    "last_observed_at": _local_time()})
        return response
    except HTTPException:
        raise
    except AuthenticationUnavailable:
        raise HTTPException(401, "Databricks sign-in expired. Reauthenticate the labs profile to read this run.") from None
    except Exception as error:
        raise HTTPException(502, f"Run status could not be read ({type(error).__name__}). No run was started again.") from None
