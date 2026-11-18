"""Offline regression only: fake CLI/SDK, real temporary immutable receipts."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from threading import BoundedSemaphore
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from workbench import selected_job_deploy as deploy
from workbench.spark_codegen import _digest as design_digest


PACKAGE, DESIGN, INPUT = "a" * 64, "b" * 64, "c" * 64
PROJECT, JOB, RUN = "d" * 32, "e" * 32, "f" * 32
HOST = "https://workspace.example"
USER, USERNAME = "12345", "reviewer@example.com"
NOTEBOOK = "notebooks/workflow/producer.ipynb"
FOLLOWUP = "notebooks/workflow/followup.ipynb"
SECRET = "never-print-this-credential"


def notebook(source="print('source retained')\n# unresolved business behavior remains source text"):
    return json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {"language": "markdown", "id": "intro"}, "source": "# Candidate"},
        {"cell_type": "code", "metadata": {"language": "python", "id": "code"},
         "source": source, "execution_count": None, "outputs": []}]}, ensure_ascii=False) + "\n"


class ImmediateThread:
    def __init__(self, target, args, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class FakeProcess:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.failure = None
        self.help = "Usage: databricks bundle deploy\n      --select strings   Select resources (jobs.KEY)\n"
        self.cli_user = {"id": USER, "userName": USERNAME}
        self.summary_extra = False

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), deepcopy(kwargs)))
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert "DATABRICKS_TOKEN" not in kwargs["env"]
        assert "AZURE_CLIENT_SECRET" not in kwargs["env"]
        assert "UNRELATED_API_KEY" not in kwargs["env"]
        command = args[1:3]
        if self.failure and command == self.failure[0]:
            result = self.failure[1]
            if isinstance(result, BaseException):
                raise result
            return SimpleNamespace(returncode=result, stdout=SECRET, stderr="PERMISSION_DENIED " + SECRET)
        text = ""
        if command == ["bundle", "deploy"] and "--help" in args:
            text = self.help
        elif command == ["current-user", "me"]:
            text = json.dumps(self.cli_user)
        elif command == ["bundle", "deploy"]:
            root = Path(kwargs["cwd"])
            bundle = json.loads((root / "databricks.yml").read_text())
            assert set(bundle["resources"]) == {"jobs"}
            assert len(bundle["resources"]["jobs"]) == 1
            key, job = next(iter(bundle["resources"]["jobs"].items()))
            assert args[args.index("--select") + 1] == "jobs." + key
            assert args[args.index("--profile") + 1] == "labs"
            assert args[args.index("--target") + 1] == "dev"
            assert not any(key in job for key in ("schedule", "continuous", "trigger", "run_as", "job_clusters"))
            tasks = deepcopy(job["tasks"])
            for task in tasks:
                if "notebook_task" in task:
                    path = task["notebook_task"]["notebook_path"][2:]
                    assert (root / path).read_bytes() == self.fixture.files[path].encode()
                    task["notebook_task"]["notebook_path"] = bundle["targets"]["dev"]["workspace"]["root_path"] + "/files/" + path[:-6]
            self.fixture.settings = {**job, "tasks": tasks}
            self.fixture.deployed_key = key
        elif command == ["bundle", "summary"]:
            jobs = {self.fixture.deployed_key: {"id": "789"}}
            if self.summary_extra:
                jobs["unselected_job"] = {"id": "790"}
            text = json.dumps({"resources": {"jobs": jobs}})
        elif command != ["bundle", "validate"]:
            pytest.fail("Unexpected CLI operation: " + repr(command))
        return SimpleNamespace(returncode=0, stdout=text, stderr="")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    import workbench.redesign as redesign
    import workbench.databricks_models as models
    root = tmp_path / "projects" / PROJECT / "package-contexts" / PACKAGE
    state = root / "state"
    state.mkdir(parents=True)
    design = {"tasks": [{"id": "producer", "implementation_kind": "notebook_task"}],
        "edges": [], "parameters": [], "artifact_plan": [],
        "blockers": ["Candidate semantics remain unverified"],
        "scoped_unresolved": [{"proposed_task_ids": ["producer"], "category": "control",
            "description": "Keep exact unresolved source logic", "impact": "execution_blocker"}]}
    exact = {"id": DESIGN, "version": 3, "package_hash": PACKAGE, "workflow_id": "wf/selected",
        "scenario": "bronze", "input_version": INPUT, "design": design}
    job = {"id": JOB, "kind": "spark_draft", "status": "completed", "package_hash": PACKAGE,
        "workflow_id": "wf/selected", "scenario": "bronze", "input_version": INPUT,
        "design_id": DESIGN, "design_version": 3, "output": {"run_id": RUN}}
    fixture = SimpleNamespace(root=root, state=state, job=job, exact=exact, files={NOTEBOOK: notebook()},
        committed={}, manifest=None, settings=None, sdk_calls=[], identity_calls=[])
    directory = root / "generated-drafts" / PACKAGE / DESIGN / "3" / RUN

    def commit():
        directory.mkdir(parents=True, exist_ok=True)
        staged, ownership = [], []
        for path, content in fixture.files.items():
            digest = sha256(content.encode()).hexdigest()
            audit = "staged-artifacts/" + digest + "/" + path
            target = directory / audit
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
            staged.append({"relative_path": path, "kind": "notebook", "content_sha256": digest,
                "audit_relative_path": audit, "bytes": len(content.encode()), "staged": True,
                "committed": False, "artifact_approved": False, "execution_allowed": False,
                "deployment_allowed": False})
            owner = "producer" if path == NOTEBOOK else "followup" if path == FOLLOWUP else None
            if owner:
                ownership.append({"task_id": owner, "code_available": True,
                    "notebooks": [{"relative_path": path, "content_sha256": digest, "audit_relative_path": audit}],
                    "unresolved_items": [{"kind": "translation_metadata_missing"}]})
        artifact_hashes = {}
        for path, content in fixture.committed.items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
            artifact_hashes[path] = sha256(content.encode()).hexdigest()
        manifest = {"run_id": RUN, "package_hash": PACKAGE, "design_id": DESIGN,
            "design_version": 3, "input_version": INPUT, "design_sha256": design_digest(exact["design"]),
            "status": "blocked", "draft_authority": "explicit_current_design_candidate",
            "tasks": [{"task_id": task["id"], "status": "blocked", "artifact_paths": [],
                "blockers": ["bookkeeping incomplete"]} for task in exact["design"]["tasks"]],
            "artifact_hashes": artifact_hashes, "staged_drafts": staged,
            "candidate_availability": {"task_ownership_basis": "exact_current_bindings_and_retrieved_citations_only",
                "tasks": ownership}}
        if fixture.manifest is not None:
            manifest = fixture.manifest
        fixture.manifest = manifest
        raw = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(raw)
        job["manifest_sha256"] = sha256(raw).hexdigest()
        fixture.selection = deploy.SelectedJobSelection(project_id=PROJECT, package_hash=PACKAGE,
            workflow_id="wf/selected", scenario="bronze", design_id=DESIGN, design_version=3,
            design_sha256=manifest["design_sha256"], input_version=INPUT, manifest_sha256=job["manifest_sha256"])

    fixture.commit = commit
    commit()
    service = SimpleNamespace(root=root, state=state, agent_slot=BoundedSemaphore(1),
        require=lambda: SimpleNamespace(model={"package": {"sha256": PACKAGE}}),
        record=lambda identifier: deepcopy(job) if identifier == JOB else {})
    fixture.service = service
    monkeypatch.setattr(redesign, "store_for", lambda service: SimpleNamespace(get=lambda *args: deepcopy(exact)))
    monkeypatch.setattr(models, "_profile_host", lambda profile="labs", **kwargs: HOST)

    def get_job(job_id):
        fixture.sdk_calls.append(("get", job_id))
        return {"job_id": job_id, "settings": deepcopy(fixture.settings)}

    def forbidden(*args, **kwargs):
        pytest.fail("Jobs execution or live authentication is prohibited")

    sdk = SimpleNamespace(config=SimpleNamespace(host=HOST),
        current_user=SimpleNamespace(me=lambda: {"id": USER, "user_name": USERNAME}),
        jobs=SimpleNamespace(get=get_job, run_now=forbidden, submit=forbidden, create=forbidden))
    fixture.identity = SimpleNamespace(mode="profile", host=HOST, user_id=USER, client=sdk)

    def identity_factory(profile):
        fixture.identity_calls.append(profile)
        return fixture.identity

    fixture.identity_factory = identity_factory
    monkeypatch.setattr(models, "profile_identity", forbidden)
    fixture.process = FakeProcess(fixture)
    cfg = tmp_path / "profile-config"
    cfg.write_text("[labs]\nhost = " + HOST)
    fixture.cli = deploy.LocalCLI(run=fixture.process, executable="databricks.exe", environ={
        "PATH": "safe-test-path", "HOME": str(tmp_path), "DATABRICKS_CONFIG_FILE": str(cfg),
        "DATABRICKS_TOKEN": SECRET, "DATABRICKS_HOST": "https://wrong.example",
        "AZURE_CLIENT_SECRET": SECRET, "UNRELATED_API_KEY": SECRET})
    fixture.deployer = deploy.SelectedJobDeployer(service, cli_factory=lambda: fixture.cli,
        identity_factory=identity_factory, thread_factory=ImmediateThread)
    return fixture


def test_design_planned_notebook_available_without_writer_metadata_acceptance(saved):
    saved.exact["design"]["artifact_plan"] = [{"artifact_kind": "notebook",
        "relative_path": "workflow/producer.ipynb", "proposed_task_ids": ["producer"]}]
    saved.manifest = None
    saved.commit()
    saved.manifest.pop("candidate_availability")
    saved.commit()
    before = {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}
    plan = deploy.preview(saved.service, JOB, saved.selection)
    assert plan["artifact_mapping"] == {"producer": NOTEBOOK}
    assert plan["manifest_status"] == "blocked"
    assert len(plan["bundle"]["resources"]["jobs"]) == 1
    assert before == {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}


def request_for(saved, **overrides):
    plan = deploy.preview(saved.service, JOB, saved.selection)
    return deploy.SelectedJobDeployRequest(**{**saved.selection.model_dump(), "consent": True,
        "expected_workspace": HOST, "preview_sha256": plan["preview_sha256"], **overrides})


def test_blocked_bookkeeping_preview_is_offline_immutable_and_one_job(saved):
    before = {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}
    job_before = deepcopy(saved.job)
    plan = deploy.preview(saved.service, JOB, saved.selection)
    assert plan["manifest_status"] == "blocked"
    assert plan["artifacts"][NOTEBOOK] == saved.files[NOTEBOOK]
    assert "unresolved business behavior remains source text" in plan["artifacts"][NOTEBOOK]
    assert len(plan["bundle"]["resources"]["jobs"]) == 1
    native = next(iter(plan["bundle"]["resources"]["jobs"].values()))
    assert [task["task_key"] for task in native["tasks"]] == ["producer"]
    assert "new_cluster" not in native["tasks"][0]
    assert native["performance_target"] == "STANDARD"
    assert "${workspace.current_user.userName}" in plan["bundle"]["targets"]["dev"]["workspace"]["root_path"]
    assert plan["execution_allowed"] is False and plan["production_ready"] is False
    assert saved.process.calls == saved.identity_calls == []
    assert before == {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}
    assert saved.job == job_before
    assert not saved.deployer.root.exists()


def test_native_condition_from_source_operands_has_no_fake_notebook(saved):
    saved.files[NOTEBOOK] = notebook('dbutils.jobs.taskValues.set(key="count", value=1)')
    saved.files[FOLLOWUP] = notebook("print('exact branch notebook')")
    saved.exact["design"]["tasks"].extend([
        {"id": "decision", "implementation_kind": "condition_task", "condition_task": {
            "left": {"kind": "task_value", "task_id": "producer", "key": "count"},
            "right": {"kind": "literal", "value": "60"}, "op": "GREATER_THAN_OR_EQUAL"}},
        {"id": "followup", "implementation_kind": "notebook_task"}])
    saved.exact["design"]["edges"] = [
        {"source": "producer", "target": "decision", "evidence_ids": ["source"]},
        {"source": "decision", "target": "followup", "outcome": "false", "evidence_ids": ["source"]}]
    saved.manifest = None
    saved.commit()
    plan = deploy.preview(saved.service, JOB, saved.selection)
    tasks = next(iter(plan["bundle"]["resources"]["jobs"].values()))["tasks"]
    assert tasks[1]["condition_task"] == {"op": "GREATER_THAN_OR_EQUAL",
        "left": "{{tasks.producer.values.count}}", "right": "60"}
    assert tasks[2]["depends_on"] == [{"task_key": "decision", "outcome": "false"}]
    assert set(plan["artifact_mapping"]) == {"producer", "followup"}
    assert len(plan["bundle"]["resources"]["jobs"]) == 1


@pytest.mark.parametrize("field,value", [("project_id", "0" * 32), ("package_hash", "0" * 64),
    ("workflow_id", "another"), ("design_id", "0" * 64), ("design_version", 4),
    ("design_sha256", "0" * 64), ("input_version", "0" * 64), ("manifest_sha256", "0" * 64)])
def test_exact_selection_pins_fail_closed_before_authentication(saved, field, value):
    selection = saved.selection.model_copy(update={field: value})
    with pytest.raises((HTTPException, KeyError)):
        deploy.preview(saved.service, JOB, selection)
    assert saved.process.calls == saved.identity_calls == []


@pytest.mark.parametrize("tamper", ["manifest", "notebook", "ownership", "cell_language", "missing_cell_id"])
def test_real_reader_and_owned_notebook_structure_fail_closed(saved, tamper):
    directory = saved.root / "generated-drafts" / PACKAGE / DESIGN / "3" / RUN
    if tamper == "manifest":
        (directory / "manifest.json").write_bytes(b"{}")
    elif tamper == "notebook":
        audit = saved.manifest["staged_drafts"][0]["audit_relative_path"]
        (directory / audit).write_bytes(b"corrupt")
    elif tamper == "ownership":
        saved.manifest["candidate_availability"]["tasks"][0]["notebooks"][0]["content_sha256"] = "0" * 64
        saved.commit()
    else:
        book = json.loads(saved.files[NOTEBOOK])
        if tamper == "cell_language":
            book["cells"][1]["metadata"]["language"] = "scala"
        else:
            del book["cells"][1]["metadata"]["id"]
        saved.files[NOTEBOOK] = json.dumps(book)
        saved.manifest = None
        saved.commit()
    with pytest.raises((HTTPException, ValueError)):
        deploy.preview(saved.service, JOB, saved.selection)
    assert saved.identity_calls == []


def test_no_ownership_guessing_from_filenames_or_unselected_notebooks(saved):
    saved.files["notebooks/another/unused.ipynb"] = notebook("print('not selected')")
    saved.manifest = None
    saved.commit()
    plan = deploy.preview(saved.service, JOB, saved.selection)
    assert set(plan["artifacts"]) == {NOTEBOOK}
    saved.manifest["candidate_availability"]["tasks"] = []
    saved.commit()
    with pytest.raises(HTTPException):
        deploy.preview(saved.service, JOB, saved.selection)


def test_committed_notebook_can_be_used_without_candidate_bookkeeping(saved):
    saved.committed = {NOTEBOOK: saved.files[NOTEBOOK]}
    saved.manifest = None
    saved.commit()
    saved.manifest["candidate_availability"]["tasks"] = []
    saved.manifest["tasks"][0]["artifact_paths"] = [NOTEBOOK]
    saved.commit()
    assert deploy.preview(saved.service, JOB, saved.selection)["artifacts"][NOTEBOOK] == saved.files[NOTEBOOK]


@pytest.mark.parametrize("kind", ["condition_task", "run_job_task", "sql_task"])
def test_unresolved_operands_and_unsupported_tasks_are_not_invented(saved, kind):
    saved.exact["design"]["tasks"][0]["implementation_kind"] = kind
    saved.manifest = None
    saved.commit()
    with pytest.raises((HTTPException, ValueError)):
        deploy.preview(saved.service, JOB, saved.selection)
    assert saved.identity_calls == []


def test_success_deploys_exact_one_job_stages_bytes_and_never_runs(saved):
    historical = {path: path.read_bytes() for path in (saved.root / "generated-drafts").rglob("*") if path.is_file()}
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == "succeeded", receipt
    assert receipt["workspace_job_id"] == 789
    assert saved.sdk_calls == [("get", 789)]
    assert saved.identity_calls == ["labs", "labs"]
    steps = [args[1:3] for args, _ in saved.process.calls]
    assert steps == [["bundle", "deploy"], ["current-user", "me"], ["bundle", "validate"],
        ["current-user", "me"], ["bundle", "deploy"], ["bundle", "summary"]]
    actual_deploy = [args for args, _ in saved.process.calls if args[1:3] == ["bundle", "deploy"] and "--help" not in args]
    assert len(actual_deploy) == 1
    assert not any(word in {"run", "run-now", "submit", "bind", "destroy", "--force-lock"} for args, _ in saved.process.calls for word in args)
    staging = saved.deployer.root / receipt["id"] / "bundle"
    bundle = json.loads((staging / "databricks.yml").read_text())
    assert bundle["targets"]["dev"]["workspace"]["root_path"].startswith("/Workspace/Users/" + USERNAME + "/")
    assert historical == {path: path.read_bytes() for path in historical}
    assert [step["status"] for step in receipt["progress"]] == ["authenticating", "staging", "validating", "deploying", "verifying", "succeeded"]
    assert SECRET not in json.dumps(receipt)
    assert saved.service.agent_slot.acquire(blocking=False)
    saved.service.agent_slot.release()


@pytest.mark.parametrize("command,status,code", [(["bundle", "validate"], "failed", "permission_denied"),
    (["bundle", "deploy"], "partial", "permission_denied"),
    (["bundle", "summary"], "partial", "permission_denied")])
def test_cli_permissions_are_truthful_sanitized_terminal(saved, command, status, code):
    # Deploy --help is local and must succeed before the actual mutating failure.
    original = saved.process.__call__
    def process(args, **kwargs):
        if args[1:3] == command and "--help" not in args:
            saved.process.calls.append((args, kwargs))
            return SimpleNamespace(returncode=1, stdout=SECRET, stderr="PERMISSION_DENIED " + SECRET)
        return original(args, **kwargs)
    saved.cli.run = process
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == status and receipt["error_code"] == code
    assert receipt["workspace_job_id"] is None
    assert SECRET not in json.dumps(receipt)
    assert SECRET not in (saved.deployer.root / (receipt["id"] + ".json")).read_text()


def test_deploy_timeout_is_partial_not_success_or_clean_failure(saved):
    original = saved.process.__call__
    def process(args, **kwargs):
        if args[1:3] == ["bundle", "deploy"] and "--help" not in args:
            raise subprocess.TimeoutExpired(args, 900, output=SECRET, stderr=SECRET)
        return original(args, **kwargs)
    saved.cli.run = process
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == "partial"
    assert receipt["remote_mutation_possible"] is True
    assert receipt["error_code"] == "cli_timeout"
    assert SECRET not in json.dumps(receipt)


def test_select_feature_is_checked_offline_before_authentication(saved):
    saved.process.help = "Usage: bundle deploy\n--target string\n"
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "cli_selected_deploy_unsupported"
    assert saved.identity_calls == []
    assert len(saved.process.calls) == 1


@pytest.mark.parametrize("mismatch", ["workspace", "cli_user", "sdk_host", "username_path"])
def test_workspace_and_user_must_match_selected_labs_identity(saved, mismatch):
    workspace = HOST
    if mismatch == "workspace":
        workspace = "https://other.example"
    elif mismatch == "cli_user":
        saved.process.cli_user["id"] = "other-user"
    elif mismatch == "sdk_host":
        saved.identity.client.config.host = "https://other.example"
    else:
        saved.identity.client.current_user.me = lambda: {"id": USER, "user_name": "../../escape"}
        saved.process.cli_user["userName"] = "../../escape"
    receipt = saved.deployer.start(JOB, request_for(saved, expected_workspace=workspace))
    assert receipt["status"] == "failed"
    assert receipt["remote_mutation_possible"] is False
    assert not any("--select" in args for args, _ in saved.process.calls)


def test_extra_summary_job_is_partial_never_success(saved):
    saved.process.summary_extra = True
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == "partial" and receipt["error_code"] == "deployment_receipt_unverified"
    assert saved.sdk_calls == []


def test_local_staging_permission_error_is_failed_without_deploy(saved, monkeypatch):
    real_open = Path.open
    def denied(path, *args, **kwargs):
        if path.name == "databricks.yml":
            raise PermissionError(SECRET)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    receipt = saved.deployer.start(JOB, request_for(saved))
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "local_permission_denied"
    assert not receipt["remote_mutation_possible"]
    assert SECRET not in json.dumps(receipt)


def test_stable_bundle_name_is_project_package_workflow_not_design_version(saved):
    first = deploy.preview(saved.service, JOB, saved.selection)
    saved.exact["design"]["blockers"].append("another unresolved detail")
    saved.manifest = None
    saved.commit()
    second = deploy.preview(saved.service, JOB, saved.selection)
    assert first["bundle"]["bundle"]["name"] == second["bundle"]["bundle"]["name"]
    assert first["preview_sha256"] != second["preview_sha256"]
    with pytest.raises(HTTPException):
        saved.deployer.start(JOB, request_for(saved, preview_sha256=first["preview_sha256"]))
    assert saved.process.calls == []


@pytest.mark.parametrize("consent", [False, "true", 1, None])
def test_consent_is_explicit_boolean_only(saved, consent):
    with pytest.raises(ValueError):
        request_for(saved, consent=consent)
    assert saved.process.calls == []


def test_no_http_arbitrary_arguments_or_environment_allowed(saved):
    for field in ("args", "environment", "workspace_root", "job_id", "target"):
        with pytest.raises(ValueError):
            request_for(saved, **{field: "untrusted"})


def test_offline_post_routes_and_polling_mount(saved):
    app = FastAPI()
    app.state.service = saved.service
    deploy.install_selected_job_deploy(app, cli_factory=lambda: saved.cli,
        identity_factory=saved.identity_factory, thread_factory=ImmediateThread)
    client = TestClient(app)
    body = saved.selection.model_dump()
    before = set(saved.root.rglob("*"))
    for suffix in ("preview", "prepare"):
        response = client.post(f"/api/jobs/{JOB}/selected-job/{suffix}", json=body)
        assert response.status_code == 200, response.text
    assert set(saved.root.rglob("*")) == before
    assert saved.process.calls == saved.identity_calls == []
    result = client.post(f"/api/jobs/{JOB}/selected-job/deploy", json=request_for(saved).model_dump())
    assert result.status_code == 202, result.text
    identifier = result.json()["id"]
    calls = len(saved.process.calls)
    assert client.get("/api/selected-job-deployments/" + identifier).json()["status"] == "succeeded"
    assert len(saved.process.calls) == calls


def test_recovery_marks_interrupted_mutation_partial_without_cloud(saved):
    receipt = {"id": "1" * 32, "pins": {"job_id": JOB, **saved.selection.model_dump()},
        "status": "deploying", "progress": [], "remote_mutation_possible": True, "execution_allowed": False}
    saved.deployer._save(receipt)
    saved.deployer.recover()
    result = saved.deployer.status(receipt["id"])
    assert result["status"] == "partial" and result["error_code"] == "process_interrupted_not_retried"
    assert saved.process.calls == saved.identity_calls == []


def test_symlink_storage_is_rejected_before_any_cloud(saved, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    try:
        saved.deployer.root.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit unprivileged symlinks")
    with pytest.raises(HTTPException):
        saved.deployer.start(JOB, request_for(saved))
    assert saved.process.calls == []


def test_hosted_obo_mount_is_refused(saved):
    app = FastAPI()
    app.state.service = saved.service
    app.state.hosted_request_authenticated = True
    with pytest.raises(RuntimeError):
        deploy.install_selected_job_deploy(app)


# These are executable Python/Spark statements, not task-name or prose proxies.
# They are inspected and saved offline, never executed against Spark or a workspace.
BEHAVIOR_CASES = [
    pytest.param("import time\ntime.sleep(0)\n", {}, id="timer-no-parameters"),
    pytest.param('dbutils.widgets.text("delay_seconds", "0")\n'
        'import time\ntime.sleep(float(dbutils.widgets.get("delay_seconds")))\n',
        {"delay_seconds": "0"}, id="timer-own-widget"),
    pytest.param('dbutils.widgets.text("log_message", "review complete")\n'
        'import logging\nlogging.getLogger("candidate").info(dbutils.widgets.get("log_message"))\n',
        {"log_message": "review complete"}, id="logging-no-tables"),
    pytest.param('dbutils.widgets.text("source_table", "main.raw.events")\n'
        'source_table = dbutils.widgets.get("source_table")\n'
        'spark.sql(f"SELECT COUNT(*) AS event_count FROM {source_table}").show()\n',
        {"source_table": "main.raw.events"}, id="aggregate-source-only"),
    pytest.param('dbutils.widgets.text("target_table", "main.review.events")\n'
        'target_table = dbutils.widgets.get("target_table")\n'
        'spark.sql(f"INSERT INTO {target_table} (id, amount) VALUES (1, 7)")\n',
        {"target_table": "main.review.events"}, id="insert-target-only"),
    pytest.param('dbutils.widgets.text("target_table", "main.review.events")\n'
        'target_table = dbutils.widgets.get("target_table")\n'
        'spark.sql(f"MERGE INTO {target_table} AS t USING (SELECT 1 AS id, 7 AS amount) AS s "\n'
        '          "ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount "\n'
        '          "WHEN NOT MATCHED THEN INSERT (id, amount) VALUES (s.id, s.amount)")\n',
        {"target_table": "main.review.events"}, id="merge-target-only"),
    pytest.param('from pyspark.sql import functions as F\n'
        'rows = spark.createDataFrame([(1, None), (2, 7)], "id int, amount int")\n'
        'rows.select("id", F.coalesce("amount", F.lit(0)).alias("amount")).show()\n',
        {}, id="transform-no-tables-or-parameters"),
]


@pytest.mark.parametrize("source,expected", BEHAVIOR_CASES)
def test_selected_preview_publishes_only_actual_behavior_widgets_without_rewriting(saved, source, expected):
    content = notebook(source)
    saved.files[NOTEBOOK] = content
    # Deliberately contradictory design hints must not invent universal bindings.
    saved.exact["design"]["parameters"] = [
        {"name": name, "default": "invented"}
        for name in ("source_table", "target_table", "uc_catalog", "dev_schema")]
    saved.manifest = None
    saved.commit()
    before = {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}
    plan = deploy.preview(saved.service, JOB, saved.selection)
    assert plan["artifacts"] == {NOTEBOOK: content}
    assert plan["artifact_hashes"] == {NOTEBOOK: sha256(content.encode("utf-8")).hexdigest()}
    assert json.loads(plan["artifacts"][NOTEBOOK])["cells"][1]["source"] == source
    native = next(iter(plan["bundle"]["resources"]["jobs"].values()))
    assert native.get("parameters", []) == [
        {"name": name, "default": default} for name, default in sorted(expected.items())]
    if not expected:
        assert "parameters" not in native
    assert native["tasks"] == [{"task_key": "producer", "notebook_task": {
        "notebook_path": "./" + NOTEBOOK, "source": "WORKSPACE"}}]
    assert "base_parameters" not in native["tasks"][0]["notebook_task"]
    assert before == {path: path.read_bytes() for path in saved.root.rglob("*") if path.is_file()}
    assert saved.process.calls == saved.identity_calls == saved.sdk_calls == []


@pytest.fixture
def attachment(saved, monkeypatch):
    from workbench import databricks_models as models, draft_artifact_reads as reads, redesign
    from workbench.notebook_runner import install_notebook_runner

    def forbidden(*args, **kwargs):
        pytest.fail("Attachment must never submit, create, run, or poll a Jobs API operation")

    class NoJobsAPI:
        def __getattr__(self, name):
            forbidden(name)

    index = saved.service.require()
    saved.service.require = Mock(return_value=index)
    saved.service.record = Mock(return_value=saved.job)
    saved.service.agent_inputs = Mock(return_value={"input_version": INPUT})
    reader = Mock(return_value={"manifest": saved.manifest, "artifacts": {}, "staged_artifacts": {}})
    store = SimpleNamespace(get=Mock(return_value=saved.exact))
    store_factory = Mock(return_value=store)
    monkeypatch.setattr(reads, "read_result", reader)
    monkeypatch.setattr(redesign, "store_for", store_factory)
    workspace = SimpleNamespace(mkdirs=Mock(), upload=Mock(),
        get_status=Mock(return_value=SimpleNamespace(object_id=456)))
    current_user = Mock(return_value=SimpleNamespace(id=USER, user_name=USERNAME))
    identity = SimpleNamespace(host=HOST, user_id=USER, client=SimpleNamespace(
        workspace=workspace, current_user=SimpleNamespace(me=current_user), jobs=NoJobsAPI()))
    host = Mock(return_value=HOST)
    authenticate = Mock(return_value=identity)
    monkeypatch.setattr(models, "_profile_host", host)
    monkeypatch.setattr(models, "profile_identity", authenticate)
    app = FastAPI()
    app.state.service = saved.service
    install_notebook_runner(app)
    return SimpleNamespace(saved=saved, index=index, reader=reader, store=store,
        store_factory=store_factory, workspace=workspace, current_user=current_user,
        identity=identity, host=host, authenticate=authenticate,
        client=TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50123)))


@pytest.mark.parametrize("source,expected", BEHAVIOR_CASES)
@pytest.mark.parametrize("path", ["notebooks/custom/arbitrary_candidate.ipynb",
    "notebooks/another/a-name-with-no-WIL-role.ipynb"])
@pytest.mark.parametrize("storage", ["artifacts", "staged_artifacts"])
def test_attachment_arbitrary_names_upload_exact_bytes_without_any_parameters(attachment, source, expected, path, storage):
    from databricks.sdk.service.workspace import ImportFormat, Language
    a = attachment
    content = notebook(source)
    book = json.loads(content)
    # Preserve saved outputs, execution state, Unicode and formatting as well as code.
    book["cells"][0]["source"] = "# Saved candidate: café"
    book["cells"][1].update(execution_count=7, outputs=[{
        "output_type": "stream", "name": "stdout", "text": "saved output\n"}])
    content = json.dumps(book, ensure_ascii=False, indent=2) + "\n"
    a.reader.return_value[storage] = {path: content}
    before = {file: file.read_bytes() for file in a.saved.root.rglob("*") if file.is_file()}
    body = {"consent": True, "artifact_path": path, "expected_workspace": HOST}
    assert "parameters" not in body and "confirmed_output_table" not in body
    response = a.client.post(f"/api/jobs/{JOB}/spark-drafts/attach", json=body)
    assert response.status_code == 200, response.text
    digest = sha256(content.encode("utf-8")).hexdigest()
    receipt = response.json()
    assert re.fullmatch(r"[a-f0-9]{32}", receipt["session_id"])
    folder = f"/Workspace/Users/{USERNAME}/ORION-notebooks/edits/{receipt['session_id']}"
    remote = folder + "/" + Path(path).stem
    a.workspace.mkdirs.assert_called_once_with(folder)
    a.workspace.upload.assert_called_once_with(remote, content.encode("utf-8"),
        format=ImportFormat.JUPYTER, language=Language.PYTHON, overwrite=False)
    a.workspace.get_status.assert_called_once_with(remote)
    a.saved.service.require.assert_called_once_with()
    a.saved.service.record.assert_called_once_with(JOB)
    assert a.reader.call_count == 2
    assert all(call.args == (a.saved.root, a.saved.job, JOB) for call in a.reader.call_args_list)
    assert a.store.get.call_args_list == [((DESIGN, PACKAGE, 3),)]
    assert a.store_factory.call_count == 1
    for call in a.store_factory.call_args_list:
        assert call.args == (a.saved.service,)
    a.saved.service.agent_inputs.assert_not_called()
    a.host.assert_called_once_with()
    a.authenticate.assert_called_once_with("labs")
    a.current_user.assert_called_once_with()
    receipt = response.json()
    assert receipt["artifact_path"] == path and receipt["artifact_sha256"] == digest
    assert receipt["status"] == "attached" and receipt["execution_started"] is False
    assert receipt["notebook_path"] == remote
    assert receipt["notebook_url"] == HOST + "/editor/notebooks/456"
    assert "parameters" not in receipt and "workspace_run_id" not in receipt
    assert all(file.read_bytes() == raw for file, raw in before.items())
    session_path = a.saved.root / 'state' / 'notebook-edits' / JOB / 'sessions' / (receipt['session_id'] + '.json')
    assert json.loads(session_path.read_text(encoding='utf-8'))['artifact_sha256'] == digest
    assert not (a.saved.state / "notebook-runs").exists()
    assert a.saved.process.calls == a.saved.identity_calls == a.saved.sdk_calls == []


@pytest.mark.parametrize("gate,status", [
    ("job-kind", 404), ("package", 404), ("unfinished", 409),
    ("manifest-input", 409),
    ("missing-artifact", 404), ("invalid-cell", 409),
    ("workspace", 409), ("identity-host", 403), ("user", 403),
    ("unsafe-username", 403), ("unconfirmed-object", 502),
])
def test_attachment_retains_receipt_design_input_and_identity_gates(attachment, gate, status):
    a = attachment
    path = "notebooks/arbitrary/unrelated_name.ipynb"
    content = notebook("import logging\nlogging.getLogger('candidate').info('no tables')\n")
    a.reader.return_value["staged_artifacts"] = {path: content}
    body = {"consent": True, "artifact_path": path, "expected_workspace": HOST}
    if gate == "job-kind":
        a.saved.job["kind"] = "agent"
    elif gate == "package":
        a.saved.job["package_hash"] = "0" * 64
    elif gate == "unfinished":
        a.saved.job["status"] = "running"
    elif gate == "latest-design":
        a.store.get.side_effect = [a.saved.exact, {**a.saved.exact, "version": 4}]
    elif gate == "manifest-input":
        a.reader.return_value["manifest"]["input_version"] = "0" * 64
    elif gate == "current-input":
        a.saved.service.agent_inputs.return_value = {"input_version": "0" * 64}
    elif gate == "missing-artifact":
        a.reader.return_value["staged_artifacts"] = {}
    elif gate == "invalid-cell":
        book = json.loads(content)
        book["cells"][1]["metadata"]["language"] = "scala"
        a.reader.return_value["staged_artifacts"][path] = json.dumps(book)
    elif gate == "workspace":
        body["expected_workspace"] = "https://other.example"
    elif gate == "identity-host":
        a.identity.host = "https://other.example"
    elif gate == "user":
        a.current_user.return_value.id = "another-user"
    elif gate == "unsafe-username":
        a.current_user.return_value.user_name = "../../escape"
    else:
        a.workspace.get_status.return_value.object_id = None
    response = a.client.post(f"/api/jobs/{JOB}/spark-drafts/attach", json=body)
    assert response.status_code == status, response.text
    if gate != "unconfirmed-object":
        a.workspace.mkdirs.assert_not_called()
        a.workspace.upload.assert_not_called()
        a.workspace.get_status.assert_not_called()
    else:
        a.workspace.upload.assert_called_once()
        assert response.json().get("status") != "attached"
    if gate not in {"identity-host", "user", "unsafe-username", "unconfirmed-object"}:
        a.authenticate.assert_not_called()
    assert not (a.saved.state / "notebook-runs").exists()
    assert a.saved.process.calls == a.saved.identity_calls == a.saved.sdk_calls == []


@pytest.mark.parametrize('drift', ['latest-design', 'current-input'])
def test_attachment_opens_saved_revision_without_execution_freshness_gate(attachment, drift):
    a = attachment
    path = 'notebooks/arbitrary/review.ipynb'
    a.reader.return_value['staged_artifacts'] = {path: notebook()}
    if drift == 'latest-design':
        a.store.get.side_effect = [a.saved.exact, {**a.saved.exact, 'version': 4}]
    else:
        a.saved.service.agent_inputs.side_effect = AssertionError('Attachment must not recompute live inputs')
    response = a.client.post(f'/api/jobs/{JOB}/spark-drafts/attach', json={
        'consent': True, 'artifact_path': path, 'expected_workspace': HOST})
    assert response.status_code == 200, response.text
    assert response.json()['execution_started'] is False
    a.saved.service.agent_inputs.assert_not_called()
    assert a.store.get.call_count == 1
    a.workspace.upload.assert_called_once()