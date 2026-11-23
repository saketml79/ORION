"""Opt-in local CI/CD deployment of one immutable candidate Design.

Not installed automatically. Preview/prepare are entirely read-only and offline.
Only deploy authenticates or invokes remote validation/deployment, after consent.
No Jobs run/submit, scheduling, promotion, historical status edits or code repair.
The owning local app must retain its loopback/CSRF and project-dispatch boundary.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock, Thread
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


HASH = r"^[a-f0-9]{64}$"
IDENTIFIER = r"^[a-f0-9]{32}$"
ACTIVE = {"queued", "authenticating", "staging", "validating", "deploying", "verifying"}
_LOCKS: dict[str, Lock] = {}
_LOCK_GUARD = Lock()
_ENV_KEYS = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT",
             "TEMP", "TMP", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"}


class SelectedJobSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(pattern=IDENTIFIER)
    package_hash: str = Field(pattern=HASH)
    workflow_id: str = Field(min_length=1, max_length=160)
    scenario: Literal["bronze", "original"]
    design_id: str = Field(pattern=HASH)
    design_version: int = Field(ge=1)
    design_sha256: str = Field(pattern=HASH)
    input_version: str = Field(pattern=HASH)
    manifest_sha256: str = Field(pattern=HASH)
    profile: Literal["labs"] = "labs"


class SelectedJobDeployRequest(SelectedJobSelection):
    consent: Literal[True]
    expected_workspace: str = Field(min_length=8, max_length=256)
    preview_sha256: str = Field(pattern=HASH)

    @model_validator(mode="before")
    @classmethod
    def literal_consent(cls, value):
        if isinstance(value, dict) and value.get("consent") is not True:
            raise ValueError("Explicit boolean consent:true is required")
        return value


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return sha256(_encoded(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fail(message):
    raise HTTPException(409, message)


def _safe_directory(path, root):
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root):
        _fail("Deployment storage escaped the selected service")
    for item in (path, *path.parents):
        if item.is_symlink():
            _fail("Symlinks are prohibited in deployment storage")
        if item == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        _fail("Deployment storage escaped the selected service")
    return path


def _scope(service, selection):
    from workbench.knowledge_store import project_for_root
    if project_for_root(Path(service.root)) != selection.project_id:
        raise HTTPException(403, "Select a registered project package context")
    if service.require().model["package"]["sha256"] != selection.package_hash:
        raise HTTPException(403, "Selected package does not match this project context")


def _notebook(content, path):
    from workbench.spark_codegen import _notebook as validate
    from workbench.understanding import _INLINE_SECRET
    validate(content, path)  # Validation only; preserve the original saved bytes.
    if _INLINE_SECRET.search(content):
        _fail("Credential-bearing notebook cannot be deployed")
    notebook = json.loads(content)
    ids = []
    for cell in notebook["cells"]:
        identifier = cell["metadata"].get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier):
            _fail("Saved notebook requires an explicit valid cell identity")
        ids.append(identifier)
    if len(ids) != len(set(ids)):
        _fail("Saved notebook cell identities collide")


def _owned_notebooks(design, manifest, result):
    """No basename guessing, no synthetic drafted rows, no status promotion."""
    from workbench.spark_codegen import _path
    artifacts = result["artifacts"]
    staged = result["staged_artifacts"]
    rows = manifest.get("tasks", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _fail("Invalid saved task inventory")
    indexed = {row.get("task_id"): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) - {task["id"] for task in design["tasks"]}:
        _fail("Saved task inventory does not match the selected Design")
    availability = manifest.get("candidate_availability", {})
    candidate_rows = availability.get("tasks", [])
    if not isinstance(candidate_rows, list) or any(not isinstance(row, dict) for row in candidate_rows):
        _fail("Invalid candidate ownership inventory")
    candidates = {row.get("task_id"): row for row in candidate_rows}
    if len(candidates) != len(candidate_rows):
        _fail("Duplicate candidate ownership rows")
    if set(candidates) - {task["id"] for task in design["tasks"]}:
        _fail("Candidate ownership inventory does not match the selected Design")
    owned, files, hashes = {}, {}, {}
    for task in design["tasks"]:
        if task["implementation_kind"] != "notebook_task":
            continue
        key = task["id"]
        paths = indexed.get(key, {}).get("artifact_paths", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            _fail("Invalid task artifact ownership")
        options = {path: artifacts[path] for path in paths if path.endswith(".ipynb") and path in artifacts}
        # A failed writer commit must not hide code saved at the exact Design
        # artifact-plan path. The bounded reader already verified its checksum.
        for plan in design.get("artifact_plan", []):
            if plan.get("artifact_kind") != "notebook" or plan.get("proposed_task_ids") != [key]:
                continue
            planned = plan.get("relative_path", "")
            path = planned if planned.startswith("notebooks/") else "notebooks/" + planned
            if path in staged:
                options[path] = staged[path]
        candidate = candidates.get(key, {})
        notebooks = candidate.get("notebooks", [])
        if notebooks:
            if availability.get("task_ownership_basis") != "exact_current_bindings_and_retrieved_citations_only":
                _fail("Saved candidate lacks exact task ownership evidence")
            for item in notebooks:
                if not isinstance(item, dict):
                    _fail("Invalid candidate notebook receipt")
                path, digest = item.get("relative_path"), item.get("content_sha256")
                # Completed saves retain audit receipts, but the bounded reader
                # serves their bytes as committed artifacts. Only use committed
                # bytes already owned by this exact task; never mask bad staging.
                content = staged.get(path) if path in staged else artifacts.get(path) if path in paths else None
                if (not isinstance(content, str) or digest != sha256(content.encode()).hexdigest()
                        or item.get("audit_relative_path") != f"staged-artifacts/{digest}/{path}"):
                    _fail("Candidate notebook ownership checksum mismatch")
                if path in options and options[path] != content:
                    _fail("Committed and candidate notebook bytes conflict")
                options[path] = content
        if len(options) != 1:
            _fail("Every notebook task needs exactly one checksum-bound owned saved notebook")
        path, content = next(iter(options.items()))
        _path(path, "notebook")
        _notebook(content, path)
        owned[key] = path
        files[path] = content
        hashes[path] = sha256(content.encode()).hexdigest()
    # Preserve immutable auxiliary code at its exact relative path. Do not upload
    # unowned sibling notebooks or checkpoints. Nothing is invented or repaired.
    from workbench.understanding import _INLINE_SECRET
    for path, content in {**artifacts, **staged}.items():
        if path.startswith(("modules/", "sql/")):
            from workbench.spark_codegen import _python, _sql
            _path(path, "module" if path.startswith("modules/") else "sql")
            _python(content) if path.startswith("modules/") else _sql(content)
            if _INLINE_SECRET.search(content):
                _fail("Credential-bearing auxiliary artifact cannot be deployed")
            files[path] = content
            hashes[path] = sha256(content.encode()).hexdigest()
    if len({path.casefold() for path in files}) != len(files):
        _fail("Artifact paths collide on the local filesystem")
    return owned, files, hashes


def _native_job(design, owned, files):
    """Translate source ConditionSpec directly, without fake condition artifacts.

    The existing full draft helper requires accepted condition manifest rows and
    condition files. Those are bookkeeping, not native Jobs operand authority.
    Use its published-key and parameter checks, plus the exact source schema.
    """
    from workbench.redesign import ConditionSpec
    from workbench.native_job_draft import _published_keys, _checked_parameters
    from databricks.sdk.service.jobs import Task
    tasks = design["tasks"]
    keys = [task["id"] for task in tasks]
    if (not keys or len(keys) > 1000 or len(set(keys)) != len(keys)
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) for key in keys)):
        _fail("Design task keys must be unique native Jobs identifiers")
    by_id = {task["id"]: task for task in tasks}
    dependencies, rules = {key: [] for key in keys}, {}
    for edge in design["edges"]:
        source, target = edge["source"], edge["target"]
        if source not in by_id or target not in by_id or source == target:
            _fail("Invalid Design dependency endpoint")
        conditional = by_id[source]["implementation_kind"] == "condition_task"
        outcome, rule = edge.get("outcome"), edge.get("run_if", "ALL_SUCCESS")
        if conditional:
            if outcome not in {"true", "false"} or rule != "ALL_SUCCESS" or not edge.get("evidence_ids"):
                _fail("Condition branches require explicit source-evidenced outcomes")
        elif outcome is not None or edge.get("condition") is not None:
            _fail("A conditional edge requires an explicit native condition task")
        if (rule not in {"ALL_SUCCESS", "AT_LEAST_ONE_SUCCESS", "NONE_FAILED", "ALL_DONE",
                         "AT_LEAST_ONE_FAILED", "ALL_FAILED"}
                or target in rules and rules[target] != rule
                or any(dep["task_key"] == source for dep in dependencies[target])):
            _fail("Ambiguous Design dependency or run condition")
        rules[target] = rule
        dependencies[target].append({"task_key": source, **({"outcome": outcome} if conditional else {})})
    remaining = {key: {dep["task_key"] for dep in deps} for key, deps in dependencies.items()}
    while remaining:
        ready = {key for key, parents in remaining.items() if not parents}
        if not ready:
            _fail("Design dependency graph contains a cycle")
        remaining = {key: parents - ready for key, parents in remaining.items() if key not in ready}
    output = []
    for task in tasks:
        key, kind = task["id"], task["implementation_kind"]
        item = {"task_key": key}
        if kind == "notebook_task":
            if task.get("condition_task") is not None:
                _fail("Notebook task cannot contain structured condition operands")
            item["notebook_task"] = {"notebook_path": "./" + owned[key], "source": "WORKSPACE"}
        elif kind == "condition_task":
            spec = ConditionSpec.model_validate(task.get("condition_task"))
            if not any(dep["task_key"] == key for deps in dependencies.values() for dep in deps):
                _fail("Condition task needs an explicit outcome branch")
            ancestors, pending = set(), [key]
            while pending:
                current = pending.pop()
                if current in ancestors:
                    continue
                ancestors.add(current)
                if rules.get(current, "ALL_SUCCESS") == "ALL_SUCCESS":
                    pending.extend(dep["task_key"] for dep in dependencies[current])
            compiled = {"op": spec.op}
            for side in ("left", "right"):
                operand = getattr(spec, side)
                if operand.kind == "literal":
                    if spec.op not in {"EQUAL_TO", "NOT_EQUAL"} and not re.fullmatch(
                            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", operand.value):
                        _fail("Numeric comparison needs explicit numeric literal operands")
                    compiled[side] = operand.value
                else:
                    producer = operand.task_id
                    if producer == key or producer not in ancestors or producer not in owned:
                        _fail("Condition operand must reference a guaranteed upstream notebook")
                    if operand.key not in _published_keys(files[owned[producer]]):
                        _fail("Condition operand lacks an explicit upstream task value publication")
                    compiled[side] = "{{tasks." + producer + ".values." + operand.key + "}}"
            item["condition_task"] = compiled
        else:
            _fail("Selected Design requires an unsupported native task implementation")
        if dependencies[key]:
            item.update(depends_on=dependencies[key], run_if=rules[key])
        if Task.from_dict(item).as_dict() != item:
            _fail("Jobs SDK changed the native task definition")
        output.append(item)
    parameters = _checked_parameters(design, {path: files[path] for path in set(owned.values())})
    return {"performance_target": "STANDARD", "max_concurrent_runs": 1, "tasks": output,
            **({"parameters": parameters} if parameters else {})}


def preview(service, job_id: str, selection: SelectedJobSelection):
    """Pure selected-result read. No CLI, SDK identity calls or filesystem writes."""
    from workbench.draft_artifact_reads import read_result
    from workbench.redesign import store_for
    from workbench.spark_codegen import _digest as design_digest
    if not re.fullmatch(IDENTIFIER, job_id):
        raise HTTPException(404, "Unknown selected draft job")
    _scope(service, selection)
    record = service.record(job_id)
    expected = selection.model_dump(exclude={"project_id", "profile", "design_sha256"})
    if (record.get("id") != job_id or record.get("kind") != "spark_draft"
            or any(record.get(key) != value for key, value in expected.items())):
        _fail("Selected saved-result pins changed")
    result = read_result(service.root, record, job_id)
    if result.get("notebook_revisions"):
        _fail("Returned notebook edits require a fresh reviewed candidate and parameter contract before deployment; original generation receipts are unchanged")
    manifest = result["manifest"]
    exact = store_for(service).get(selection.design_id, selection.package_hash, selection.design_version)
    for key in ("package_hash", "workflow_id", "scenario", "input_version"):
        if exact.get(key) != getattr(selection, key):
            _fail("Selected immutable Design scope changed")
    if (exact.get("id") != selection.design_id or exact.get("version") != selection.design_version
            or design_digest(exact["design"]) != selection.design_sha256
            or manifest.get("design_sha256") != selection.design_sha256):
        _fail("Selected immutable Design checksum changed")
    # Manifest does not always contain workflow/scenario fields. Where present,
    # they must agree; the exact saved job and Design always carry both pins.
    for key in ("workflow_id", "scenario", "project_id"):
        if key in manifest and manifest[key] != getattr(selection, key):
            _fail("Manifest scope disagrees with selected Design")
    if manifest.get("draft_authority") not in {"explicit_agent_run_candidate",
            "explicit_current_design_candidate", "explicit_parent_candidate_retry"}:
        _fail("Select an explicitly requested saved candidate result")
    owned, files, hashes = _owned_notebooks(exact["design"], manifest, result)
    scope_hash = _digest([selection.project_id, selection.package_hash, selection.workflow_id])
    name, key = "orion-selected-" + scope_hash[:48], "selected_job_" + scope_hash[:32]
    job = _native_job(exact["design"], owned, files)
    job.update(name=name, tags={"aidevkit_project": "orion-selected-dev",
        "orion_project": selection.project_id, "orion_package": selection.package_hash,
        "orion_design": selection.design_id, "orion_workflow_scope": scope_hash})
    # CLI resolves current_user, not a textbox. Deployment rebinds this to the
    # user independently verified through the selected profile SDK and CLI.
    root = "/Workspace/Users/${workspace.current_user.userName}/.bundle/" + name + "/dev"
    bundle = {"bundle": {"name": name}, "resources": {"jobs": {key: job}},
        "sync": {"include": sorted(files)},
        "targets": {"dev": {"mode": "development", "workspace": {"root_path": root}}}}
    pins = {"job_id": job_id, **selection.model_dump()}
    plan = {"pins": pins, "bundle": bundle, "artifact_hashes": hashes, "artifact_mapping": owned}
    return {**plan, "preview_sha256": _digest(plan), "artifacts": files,
        "manifest_status": manifest.get("status"), "unresolved": {
            "design": deepcopy(exact["design"].get("scoped_unresolved", [])),
            "design_blockers": deepcopy(exact["design"].get("blockers", [])),
            "saved_tasks": deepcopy(manifest.get("tasks", [])),
            "candidate_tasks": deepcopy(manifest.get("candidate_availability", {}).get("tasks", []))},
        "execution_allowed": False, "production_ready": False, "semantic_parity": "unverified",
        "deployment_scope": "user_confirmed_unscheduled_development_only"}


class DeployFailure(RuntimeError):
    """Fixed diagnostic vocabulary only; never raw process/SDK errors."""


class LocalCLI:
    """Fixed argument vectors and an allowlisted environment. No ambient auth."""
    def __init__(self, *, run=None, executable=None, environ=None):
        source = dict(os.environ if environ is None else environ)
        self.env = {key: value for key, value in source.items() if key.upper() in _ENV_KEYS}
        config = Path(source.get("DATABRICKS_CONFIG_FILE", "~/.databrickscfg")).expanduser().resolve()
        self.env["DATABRICKS_CONFIG_FILE"] = str(config)
        self.config_file = config
        self.executable = executable or shutil.which("databricks", path=self.env.get("PATH", ""))
        if not self.executable or Path(self.executable).suffix.lower() in {".bat", ".cmd", ".ps1"}:
            raise DeployFailure("cli_unavailable")
        self.run = run or subprocess.run

    def _call(self, args, cwd, timeout=120):
        try:
            completed = self.run([self.executable, *args], cwd=str(cwd), env=self.env,
                shell=False, capture_output=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DeployFailure("cli_timeout") from None
        except PermissionError:
            raise DeployFailure("local_permission_denied") from None
        except OSError:
            raise DeployFailure("cli_process_failed") from None
        if completed.returncode != 0:
            text = (completed.stderr or "") + (completed.stdout or "")
            code = ("permission_denied" if re.search(r"(?i)PERMISSION_DENIED|\b403\b|permission denied", text)
                    else "authentication_failed" if re.search(r"(?i)UNAUTHENTICATED|\b401\b", text)
                    else "cli_command_failed")
            raise DeployFailure(code)
        return completed.stdout

    def supports_select(self, cwd):
        help_text = self._call(["bundle", "deploy", "--help"], cwd)
        if not re.search(r"(?m)^\s*(?:-[A-Za-z],\s*)?--select(?:[ =\s])", help_text):
            raise DeployFailure("cli_selected_deploy_unsupported")

    def user(self, cwd):
        try:
            return json.loads(self._call(["current-user", "me", "--profile", "labs", "--output", "json"], cwd))
        except ValueError:
            raise DeployFailure("cli_identity_unverified") from None

    def validate(self, cwd):
        self._call(["bundle", "validate", "--target", "dev", "--profile", "labs"], cwd, 300)

    def deploy(self, cwd, key):
        if not re.fullmatch(r"selected_job_[a-f0-9]{32}", key):
            raise DeployFailure("invalid_job_resource")
        self._call(["bundle", "deploy", "--target", "dev", "--profile", "labs",
                    "--select", "jobs." + key, "--auto-approve"], cwd, 900)

    def summary(self, cwd):
        try:
            return json.loads(self._call(["bundle", "summary", "--target", "dev",
                                         "--profile", "labs", "--output", "json"], cwd))
        except ValueError:
            raise DeployFailure("deployment_receipt_unverified") from None


def _value(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _authenticated(cli, expected_workspace, cwd, identity_factory=None):
    from workbench.databricks_models import _host, _profile_host, profile_identity
    try:
        host = _profile_host("labs", path=cli.config_file)
        if _host(expected_workspace) != host:
            raise DeployFailure("workspace_mismatch")
        identity = (identity_factory or profile_identity)("labs")
        if (identity.mode != "profile" or identity.host != host
                or _host(identity.client.config.host) != host):
            raise DeployFailure("workspace_identity_mismatch")
        sdk_user = identity.client.current_user.me()
        cli_user = cli.user(cwd)
        username = _value(sdk_user, "user_name")
        if (str(_value(sdk_user, "id")) != identity.user_id
                or str(cli_user.get("id")) != identity.user_id
                or cli_user.get("userName") != username
                or not isinstance(username, str)
                or not re.fullmatch(r"[A-Za-z0-9_@.+-]{1,256}", username)
                or username in {".", ".."}):
            raise DeployFailure("cli_sdk_user_mismatch")
        return identity, username
    except DeployFailure:
        raise
    except Exception:
        raise DeployFailure("workspace_authentication_unavailable") from None


class SelectedJobDeployer:
    """One process owns service.state; bounded worker, durable truthful receipts.

    New installers reconcile interrupted receipts, never retry a cloud operation.
    Test seams inject CLI/SDK/thread factories, not HTTP command arguments.
    """
    def __init__(self, service, *, cli_factory=LocalCLI, identity_factory=None, thread_factory=Thread):
        self.service, self.cli_factory = service, cli_factory
        self.identity_factory, self.thread_factory = identity_factory, thread_factory
        self.root = _safe_directory(Path(service.state) / "selected-job-deploy", service.root)
        self._memory = {}
        with _LOCK_GUARD:
            self.lock = _LOCKS.setdefault(str(self.root), Lock())

    def _save(self, receipt):
        from workbench.notebook_runner import _atomic_json
        identifier = receipt["id"]
        path = _safe_directory(self.root / (identifier + ".json"), self.service.root)
        self._memory[identifier] = deepcopy(receipt)
        _atomic_json(path, receipt)

    def _progress(self, receipt, status, **fields):
        receipt.update(status=status, updated_at=_now(), **fields)
        receipt["progress"].append({"status": status, "at": receipt["updated_at"]})
        self._save(receipt)

    def recover(self):
        """Call once at mount; writes deployment receipts only, no cloud reads."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            if not self.root.exists():
                return
            for path in self.root.glob("*.json"):
                if not re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                    continue
                receipt = self.status(path.stem)
                if receipt["status"] in ACTIVE:
                    self._progress(receipt, "partial" if receipt["remote_mutation_possible"] else "failed",
                        error_code="process_interrupted_not_retried", finished_at=_now())
        finally:
            self.lock.release()

    def status(self, identifier):
        if not re.fullmatch(IDENTIFIER, identifier):
            raise HTTPException(404, "Unknown selected deployment")
        path = _safe_directory(self.root / (identifier + ".json"), self.service.root)
        try:
            with path.open("rb") as stream:
                raw = stream.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ValueError
            value = json.loads(raw)
            if value["id"] != identifier:
                raise ValueError
            memory = self._memory.get(identifier)
            if memory and memory.get("error_code") == "deployment_receipt_write_failed":
                return deepcopy(memory)
            return value
        except FileNotFoundError:
            raise HTTPException(404, "Unknown selected deployment") from None
        except (OSError, ValueError, KeyError):
            value = self._memory.get(identifier)
            if value:
                return deepcopy(value)
            _fail("Deployment receipt unavailable; no remote success is assumed")

    def start(self, job_id, body: SelectedJobDeployRequest):
        if body.consent is not True:
            raise HTTPException(403, "Explicit development deployment consent is required")
        selection = SelectedJobSelection.model_validate(body.model_dump(exclude={
            "consent", "expected_workspace", "preview_sha256"}))
        plan = preview(self.service, job_id, selection)
        if plan["preview_sha256"] != body.preview_sha256:
            _fail("Selected preview changed; prepare and confirm the exact saved job again")
        if not self.lock.acquire(blocking=False):
            _fail("A selected deployment is already active in this project package")
        if not self.service.agent_slot.acquire(blocking=False):
            self.lock.release()
            _fail("Wait for the current agent operation before deploying")
        receipt = {"id": uuid4().hex, "pins": plan["pins"], "preview_sha256": plan["preview_sha256"],
            "status": "queued", "created_at": _now(), "progress": [], "execution_allowed": False,
            "production_ready": False, "remote_mutation_possible": False,
            "workspace_job_id": None, "error_code": None}
        try:
            self._save(receipt)
            self.thread_factory(target=self._worker, args=(receipt, plan, body.expected_workspace),
                                name="selected-job-deploy", daemon=True).start()
        except Exception:
            try:
                self._progress(receipt, "failed", error_code="worker_start_failed", finished_at=_now())
            except Exception:
                pass
            self.service.agent_slot.release()
            self.lock.release()
            _fail("Deployment worker or local receipt could not start; no remote action confirmed")
        return self.status(receipt["id"])

    def _worker(self, receipt, plan, expected_workspace):
        try:
            cli = self.cli_factory()
            # Local help first. Unsupported CLI versions never authenticate.
            cli.supports_select(self.service.root)
            self._progress(receipt, "authenticating")
            identity, username = _authenticated(cli, expected_workspace, self.service.root, self.identity_factory)
            self._progress(receipt, "staging", workspace=identity.host, user_id=identity.user_id)
            directory = _safe_directory(self.root / receipt["id"] / "bundle", self.service.root)
            directory.mkdir(parents=True, exist_ok=False)
            bundle = deepcopy(plan["bundle"])
            name = bundle["bundle"]["name"]
            bundle["targets"]["dev"]["workspace"] = {"host": identity.host,
                "root_path": "/Workspace/Users/" + username + "/.bundle/" + name + "/dev"}
            for path, content in {"databricks.yml": _encoded(bundle) + "\n", **plan["artifacts"]}.items():
                target = _safe_directory(directory / path, self.service.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                if path != "databricks.yml" and sha256(target.read_bytes()).hexdigest() != plan["artifact_hashes"][path]:
                    raise DeployFailure("staged_artifact_checksum_mismatch")
            if len(bundle["resources"]["jobs"]) != 1:
                raise DeployFailure("selected_scope_invalid")
            key = next(iter(bundle["resources"]["jobs"]))
            self._progress(receipt, "validating")
            cli.validate(directory)
            # Recheck CLI/SDK identity after validation, immediately before the
            # mutating command. No alternate run_as, environment auth or profile.
            identity, current_username = _authenticated(cli, expected_workspace, directory, self.identity_factory)
            if current_username != username or identity.user_id != receipt["user_id"]:
                raise DeployFailure("workspace_identity_changed")
            self._progress(receipt, "deploying", remote_mutation_possible=True)
            cli.deploy(directory, key)
            self._progress(receipt, "verifying")
            summary = cli.summary(directory)
            resources = summary.get("resources", {}).get("jobs", {})
            if set(resources) != {key}:
                raise DeployFailure("deployment_receipt_unverified")
            job_id = resources[key].get("id")
            if not re.fullmatch(r"[1-9][0-9]{0,19}", str(job_id)):
                raise DeployFailure("deployment_receipt_unverified")
            deployed = identity.client.jobs.get(int(job_id))  # Read-only verification, never run.
            settings = _value(deployed, "settings")
            actual_tasks = [_value(task, "task_key") for task in (_value(settings, "tasks") or [])]
            expected_tasks = [task["task_key"] for task in bundle["resources"]["jobs"][key]["tasks"]]
            if (len(actual_tasks) != len(expected_tasks) or set(actual_tasks) != set(expected_tasks)
                    or _value(settings, "schedule") is not None
                    or _value(settings, "continuous") is not None or _value(settings, "trigger") is not None):
                raise DeployFailure("deployed_job_scope_unverified")
            returned_id = _value(deployed, "job_id")
            if returned_id != int(job_id):
                raise DeployFailure("deployed_job_scope_unverified")
            tags = _value(settings, "tags") or {}
            if any(tags.get(tag) != value for tag, value in bundle["resources"]["jobs"][key]["tags"].items()):
                raise DeployFailure("deployed_job_scope_unverified")
            actual = {_value(task, "task_key"): task for task in _value(settings, "tasks")}
            for expected in bundle["resources"]["jobs"][key]["tasks"]:
                task = actual[expected["task_key"]]
                task = task if isinstance(task, dict) else task.as_dict()
                if any(task.get(cluster) is not None for cluster in (
                        "new_cluster", "existing_cluster_id", "job_cluster_key")):
                    raise DeployFailure("deployed_job_not_serverless")
                if (task.get("depends_on", []) != expected.get("depends_on", [])
                        or task.get("run_if", "ALL_SUCCESS") != expected.get("run_if", "ALL_SUCCESS")
                        or task.get("condition_task") != expected.get("condition_task")):
                    raise DeployFailure("deployed_job_scope_unverified")
                if "notebook_task" in expected:
                    path = plan["artifact_mapping"][expected["task_key"]]
                    destination = bundle["targets"]["dev"]["workspace"]["root_path"] + "/files/" + path
                    notebook = task.get("notebook_task") or {}
                    if (notebook.get("notebook_path") not in {destination, destination[:-6]}
                            or notebook.get("source", "WORKSPACE") != "WORKSPACE"):
                        raise DeployFailure("deployed_notebook_scope_unverified")
            self._progress(receipt, "succeeded", workspace_job_id=int(job_id), finished_at=_now())
        except Exception as error:
            code = (str(error) if isinstance(error, DeployFailure)
                    else "local_permission_denied" if isinstance(error, PermissionError)
                    else "permission_denied" if getattr(error, "status_code", None) == 403
                    else "deployment_operation_failed")
            # A deploy timeout/nonzero result can already have uploaded files or
            # created the Job. Report partial, not clean failure or fake success.
            status = "partial" if receipt["remote_mutation_possible"] else "failed"
            try:
                self._progress(receipt, status, error_code=code, finished_at=_now())
            except Exception:
                receipt.update(status=status, error_code="deployment_receipt_write_failed", finished_at=_now())
                self._memory[receipt["id"]] = deepcopy(receipt)
        finally:
            self.service.agent_slot.release()
            self.lock.release()


def install_selected_job_deploy(app, **dependencies):
    """Explicit mount hook for create_app's local project child, not hosted OBO."""
    if getattr(app.state, "hosted_request_authenticated", False):
        raise RuntimeError("Selected local CLI deployment cannot be mounted in hosted OBO mode")
    deployer = SelectedJobDeployer(app.state.service, **dependencies)
    deployer.recover()
    app.state.selected_job_deployer = deployer

    @app.post("/api/jobs/{job_id}/selected-job/preview")
    @app.post("/api/jobs/{job_id}/selected-job/prepare")
    def prepare(job_id: str, body: SelectedJobSelection):
        try:
            return preview(app.state.service, job_id, body)
        except HTTPException:
            raise
        except Exception:
            _fail("Selected saved Design or notebook structure could not be verified")

    @app.post("/api/jobs/{job_id}/selected-job/deploy", status_code=202)
    def deploy(job_id: str, body: SelectedJobDeployRequest):
        try:
            return deployer.start(job_id, body)
        except HTTPException:
            raise
        except Exception:
            _fail("Selected deployment could not be prepared; no success is assumed")

    @app.get("/api/selected-job-deployments/{deployment_id}")
    def progress(deployment_id: str):
        receipt = deployer.status(deployment_id)
        _scope(app.state.service, SelectedJobSelection.model_validate({
            key: value for key, value in receipt["pins"].items() if key != "job_id"}))
        return receipt

    return deployer