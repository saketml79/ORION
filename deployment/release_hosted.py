"""Operator-only empty-fixture -> existing Apps release. Offline plan is default.

Usage (from checkout, nothing runs on import):
  python -m deployment.release_hosted --help
  Plan: supply --xml, --fixtures (bootstrap's exported plan), --output NEW,
        --stage NEW, --workspace-host, --app-origin, --app-name, --app-id,
        --user-id, --warehouse, --cli ABSOLUTE, --baseline reviewed JSON,
        --source-config reviewed replacement source-path JSON.
  Apply: --apply --output SAME --expected-release-sha SHA
         --expected-fixture-sha SHA --consent 'RELEASE EXISTING APP SHA'
         --idle-receipt PATH --regression-receipt PATH --visible-receipt PATH
         --confirm-hosted-idle

Baseline JSON is an operator-reviewed local Apps GET projection, never fetched
by planning. It contains the fields in PRESERVED plus source_code_path,
default_source_code_path and active_deployment {deployment_id, source_code_path}.
Null optional fields must be explicit. Git sources/resources are unsupported.
Source-config JSON has exactly source_code_path, default_source_code_path,
active_source_code_path. Each is an exact /Workspace path, optionally containing
{deployment_id}, replaced only with the final observed deployment ID. No path
guessing or source-config mutation is performed. Readback rejects other paths.

Gate JSON: format=orion-hosted-release-gate-v1, kind=idle|regression|visible,
status=VERIFIED, bindings=plan['bindings'], evidence=[{path, sha256}],
verified_at/expires_at UTC, receipt_sha256=fingerprint(all other fields).
Idle: scope=hosted-all-users, authority=operator-confirmed, traffic_frozen=true,
active_runs=active_writers=0. Regression: passed>0, failed=errors=skipped=0.
Visible: surface=actual-shared-local-app, workflows=[{workflow_id, status=PASS,
evidence_sha256}] with >=2 distinct workflow IDs and evidence hashes.
Receipt timestamps last at most 24 hours (idle at most 15 minutes). Gates are
operator attestations with hashed evidence, not signatures or inferred truth.
Traffic must remain held through replacement; a local selection cannot prove
hosted all-user idle. No distributed lease exists. This wrapper serializes
operators sharing this OS user's temp directory on this machine and stops
ONLY the reviewed, confirmed-idle app.

CLI v1.13.0 / labs / dev is the recorded existing release protocol. No auth
login, Jobs, inference, retry, automatic rollback or cleanup. Failures require
manual reconciliation. Receipt JSONL is fsynced before every attempted step.
Source XML, both SQL files, fixture manifest and creation journal stay external;
their hashes are separate release deliverables, not Apps source or startup DDL.
The unchanged staging helper may remove its own brand-new incomplete directory
on copy failure; this wrapper never deletes stages, journals or release receipts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace

import yaml

from deployment import stage_hosted
from workbench import package_fixture_bootstrap as bootstrap

ROOT = stage_hosted.ROOT
FORMAT = "orion-existing-app-release-v1"
CLI_VERSION = "1.13.0"
SCOPES = ["catalog.catalogs", "catalog.schemas", "catalog.tables", "files", "model-serving", "sql"]
PRESERVED = ("name", "id", "url", "user_api_scopes", "effective_user_api_scopes",
             "compute_min_instances", "compute_max_instances", "compute_size",
             "service_principal_id", "service_principal_client_id", "resources",
             "forward_user_access_token", "git_repository", "git_source", "default_git_source")
EXTERNAL = ("creation-manifest.json", "source-tables.sql", "target-tables.sql")
SHA = re.compile(r"[a-f0-9]{64}")


def require(condition, message="Release review gate rejected"):
    if not condition:
        raise ValueError(message)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


class ReviewedConfigLoader(yaml.SafeLoader):
    """Reject duplicate/non-string keys instead of silently discarding config."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str) and key not in result, "Duplicate or non-string YAML key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def config_fingerprint(text):
    # JSON fingerprints distinguish quoted strings, booleans and integers.
    # Raw rendered bytes remain independently pinned in code_preview entries.
    return bootstrap.fingerprint(yaml.load(text, Loader=ReviewedConfigLoader))


def load(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024)
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def checked_path(path, *, external=False):
    path = Path(path).absolute()
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents), "Symlink path rejected")
    path = path.resolve()
    if external:
        require(path != ROOT and ROOT not in path.parents, "Release inputs and outputs must be external")
    return path


def stamped(value):
    require(isinstance(value, str))
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None)
    return result.astimezone(timezone.utc)


def seal(value, key):
    require(value.get(key) == bootstrap.fingerprint({k: v for k, v in value.items() if k != key}),
            "Checksum mismatch")


def reviewed_fixture(xml, fixtures):
    plan = load(fixtures / EXTERNAL[0])
    rebuilt = bootstrap.build_plan(xml, plan["user_label"], plan["catalog"], plan["assignments"], plan["profile"])
    require(plan == rebuilt, "Fixture plan no longer matches entire external XML")
    for kind, name in (("SOURCE", EXTERNAL[1]), ("TARGET", EXTERNAL[2])):
        sql = [bootstrap.table_sql(plan, t) for t in plan["tables"] if t["kind"] == kind]
        expected = ";\n\n".join(sql) + (";\n" if sql else "")
        require((fixtures / name).read_text(encoding="utf-8") == expected, "Reviewed fixture SQL drift")
    return plan


def app_projection(app):
    require(isinstance(app, dict) and all(k in app for k in PRESERVED), "Incomplete reviewed app identity")
    result = {key: app[key] for key in PRESERVED}
    for key in ("source_code_path", "default_source_code_path", "active_deployment"):
        require(key in app, "Missing reviewed source identity")
    active = app["active_deployment"]
    require(isinstance(active, dict) and set(("deployment_id", "source_code_path")) <= set(active))
    result["source"] = {"source_code_path": app["source_code_path"],
                        "default_source_code_path": app["default_source_code_path"],
                        "active_deployment": {k: active[k] for k in ("deployment_id", "source_code_path")}}
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", result["name"] or ""))
    require(isinstance(result["id"], str) and re.fullmatch(r"[A-Za-z0-9-]{1,128}", result["id"]))
    require(result["user_api_scopes"] == SCOPES, "Requested scopes cannot change")
    require(isinstance(result["effective_user_api_scopes"], list) and
            set(SCOPES) <= set(result["effective_user_api_scopes"]), "Effective OBO scopes unavailable")
    require(type(result["compute_min_instances"]) is int and result["compute_min_instances"] == 1 and
            type(result["compute_max_instances"]) is int and result["compute_max_instances"] == 1,
            "Exactly one app instance required")
    require(result["resources"] in (None, []) and
            all(result[k] is None for k in ("git_repository", "git_source", "default_git_source")),
            "Only the existing file-based no-resource app is supported")
    require(result["forward_user_access_token"] is True, "OBO forwarding must remain enabled")
    require(isinstance(result["compute_size"], str) and result["compute_size"])
    require(result["service_principal_id"] is not None and result["service_principal_client_id"],
            "Platform app identity must be preserved, never used for bootstrap")
    for path in (app["source_code_path"], app["default_source_code_path"], active["source_code_path"]):
        require(isinstance(path, str) and path.startswith("/Workspace/") and
                not any(c in path for c in ("\n", "\r", "\\")) and ".." not in path.split("/"),
                "Reviewed existing source configuration required")
    require(isinstance(active["deployment_id"], str) and re.fullmatch(r"[A-Za-z0-9-]{1,128}", active["deployment_id"]))
    return result


def code_preview(host, app_origin, prefix, warehouse):
    """Pure local allowlist snapshot, not a staging operation or remote read.

    Matches the unchanged staging helper. Generated operator/package metadata
    are checked separately after staging; every source/static/skill/config byte
    is included here. Helper/wrapper hashes also gate any packaging logic drift.
    """
    require(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", prefix or ""))
    sources = list((ROOT / "workbench").glob("*.py"))
    sources.append(ROOT / "workbench/static/lineage-example.json")
    for folder, suffixes in (("static", stage_hosted.ASSET_SUFFIXES), ("skills", {".md", ".py", ".json"})):
        sources.extend(p for p in (ROOT / "workbench" / folder).rglob("*") if p.suffix.lower() in suffixes)
    entries = {}
    for source in sources:
        checked_path(source)
        require(source.is_file() and source.stat().st_size <= 10 * 1024 * 1024)
        entries[source.relative_to(ROOT).as_posix()] = {"sha256": bootstrap.file_hash(source), "size": source.stat().st_size}
    skill_library, official_sources = stage_hosted.official_skill_sources()
    for source, relative_target in official_sources:
        data = skill_library._read_bytes(source)
        key = (Path("workbench") / "official_skills" / relative_target).as_posix()
        require(key not in entries, "Duplicate official skill package path")
        entries[key] = {"sha256": bootstrap.sha256(data).hexdigest(), "size": len(data)}
    for name in ("LICENSE", "NOTICE", "SOURCE.json"):
        source = ROOT / "workbench" / "skill_notices" / "databricks" / name
        checked_path(source)
        data = skill_library._read_bytes(source)
        key = (Path("workbench") / "official_skills" / "databricks" / name).as_posix()
        require(key not in entries, "Duplicate Databricks provider notice path")
        entries[key] = {"sha256": bootstrap.sha256(data).hexdigest(), "size": len(data)}
    requirements = ROOT / "workbench/requirements.txt"
    checked_path(requirements)
    entries["requirements.txt"] = {"sha256": bootstrap.file_hash(requirements), "size": requirements.stat().st_size}
    configs = {}
    for name in ("app.yaml", "databricks.yml"):
        source = ROOT / "deployment/hosted" / name
        checked_path(source)
        text = source.read_text(encoding="utf-8")
        for key, value in (("__WORKSPACE_HOST__", host), ("__APP_ORIGIN__", app_origin),
                           ("__CATALOG_PREFIX__", prefix), ("__SQL_WAREHOUSE_ID__", warehouse)):
            text = text.replace('"' + key + '"', json.dumps(value))
        configs[name] = text
        # stage_hosted.write_text uses native newlines on Windows.
        data = text.replace("\n", os.linesep).encode("utf-8")
        entries[name] = {"sha256": bootstrap.sha256(data).hexdigest(), "size": len(data)}
    runtime = 'command: ["python", "-m", "workbench.hosted"]\nenv:\n'
    env = (("WORKBENCH_TRUST_DATABRICKS_PROXY", "1"), ("WORKBENCH_DATABRICKS_HOST", host),
           ("WORKBENCH_APP_ORIGIN", app_origin), ("WORKBENCH_CATALOG_PREFIX", prefix),
           ("WORKBENCH_SQL_WAREHOUSE_ID", warehouse), ("PYTHONDONTWRITEBYTECODE", "1"),
           ("LANGCHAIN_TRACING_V2", "false"), ("LANGSMITH_TRACING", "false"))
    runtime += "".join("  - name: " + key + "\n    value: " + json.dumps(value) + "\n" for key, value in env)
    require(config_fingerprint(configs["app.yaml"]) == config_fingerprint(runtime), "Runtime template changed; new operator review required")
    bundle = configs["databricks.yml"]
    # Compare the entire reviewed config, not a subset. Extensions fail closed.
    expected = '''bundle:
  name: modernization-workbench-hosted
variables:
  app_name:
    description: Reviewed workspace-unique name, lowercase letters, digits and hyphens, at most 30 characters
    default: orion-migration-workbench
resources:
  apps:
    workbench:
      name: ${var.app_name}
      description: User-scoped proposal-first evidence workbench. No executable migration or deployment authority.
      source_code_path: .
      compute_min_instances: 1
      compute_max_instances: 1
      config:
'''
    expected += "".join("        " + line + "\n" for line in runtime.rstrip("\n").split("\n"))
    expected += "      user_api_scopes:\n" + "".join("        - " + scope + "\n" for scope in SCOPES)
    expected += "targets:\n  dev:\n    default: true\n"
    require(config_fingerprint(bundle) == config_fingerprint(expected), "Bundle template changed; app-only single-instance configuration required")
    tools = {name: bootstrap.file_hash(checked_path(ROOT / name)) for name in
             ("deployment/stage_hosted.py", "deployment/release_hosted.py", "workbench/tests/test_fixture_release_sequence.py")}
    return entries, bootstrap.fingerprint(tools)


def plan_release(*, xml, fixtures, output, stage, workspace_host, app_origin, app_name,
                 app_id, user_id, warehouse, cli, baseline, source_config, catalog_prefix="orion_dev_"):
    xml, fixtures, output, stage = [checked_path(p, external=True) for p in (xml, fixtures, output, stage)]
    paths = (xml, fixtures, output, stage)
    require(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(paths) for b in paths[i + 1:]), "External deliverables must be separate")
    require(not stage.exists() and not output.exists(), "New stage and release directory required")
    require(not (fixtures / "creation-receipts.jsonl").exists(), "Fixture apply journal already exists; reconcile manually")
    host, app_origin = stage_hosted.origin(workspace_host), stage_hosted.origin(app_origin)
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", warehouse or ""))
    require(isinstance(user_id, str) and re.fullmatch(r"[A-Za-z0-9-]{1,128}", user_id))
    cli = checked_path(cli)
    require(cli.is_file() and cli.suffix.lower() in ("", ".exe"), "Explicit existing CLI executable required")
    fixture = reviewed_fixture(xml, fixtures)
    identity = app_projection(load(checked_path(baseline)))
    require(identity["name"] == app_name and identity["id"] == app_id and
            stage_hosted.origin(identity["url"]) == app_origin, "Reviewed app identity mismatch")
    replacement_source = load(checked_path(source_config))
    require(isinstance(replacement_source, dict) and set(replacement_source) ==
            {"source_code_path", "default_source_code_path", "active_source_code_path"})
    for value in replacement_source.values():
        require(isinstance(value, str) and value.startswith("/Workspace/") and
                not any(c in value for c in ("\n", "\r", "\\")) and ".." not in value.split("/") and
                "{" not in value.replace("{deployment_id}", "") and "}" not in value.replace("{deployment_id}", ""),
                "Exact reviewed replacement source configuration required")
    require("{deployment_id}" not in replacement_source["source_code_path"],
            "Bundle source path must be known before starting the replacement")
    code, tools = code_preview(host, app_origin, catalog_prefix, warehouse)
    bindings = {"staged_code_sha256": bootstrap.fingerprint(code), "release_tools_sha256": tools,
                "package_sha256": fixture["package_sha256"], "fixture_plan_sha256": fixture["plan_sha256"],
                "user_sha256": fixture["user_sha256"], "user_id": user_id,
                "host": host, "app_name": app_name, "app_id": app_id,
                "app_baseline_sha256": bootstrap.fingerprint(identity),
                "replacement_source_sha256": bootstrap.fingerprint(replacement_source)}
    plan = {"format": FORMAT, "profile": "labs", "target": "dev", "cli_version": CLI_VERSION,
            "xml": str(xml), "fixtures": str(fixtures), "output": str(output), "stage": str(stage),
            "host": host, "app_origin": app_origin, "catalog_prefix": catalog_prefix, "warehouse": warehouse,
            "cli": str(cli), "cli_sha256": bootstrap.file_hash(cli), "app": identity,
            "code": code, "bindings": bindings, "replacement_source": replacement_source,
            "deliverables": {name: bootstrap.file_hash(fixtures / name) for name in EXTERNAL},
            "sequence": ["baseline_get", "bootstrap_apply", "stage", "bundle_validate", "bind_existing",
                         "idle_recheck", "stop_confirmed_idle_app", "verify_stopped", "bundle_deploy",
                         "verify_no_writer", "bundle_run_workbench", "app_readback"],
            "remote_deploy_complete": False}
    plan["release_sha256"] = bootstrap.fingerprint(plan)
    output.mkdir(parents=True, exist_ok=False)
    with (output / "release-plan.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(plan, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return plan


def gate(path, kind, plan, now):
    value = load(checked_path(path, external=True))
    seal(value, "receipt_sha256")
    require(value.get("format") == "orion-hosted-release-gate-v1" and value.get("kind") == kind and
            value.get("status") == "VERIFIED" and value.get("bindings") == plan["bindings"])
    start, end = stamped(value["verified_at"]), stamped(value["expires_at"])
    require(start <= now < end and 0 < (end - start).total_seconds() <= (900 if kind == "idle" else 86400),
            "Gate receipt is stale or unbounded")
    evidence = value.get("evidence")
    require(isinstance(evidence, list) and evidence, "Hashed external evidence required")
    hashes = set()
    for item in evidence:
        require(isinstance(item, dict) and SHA.fullmatch(item.get("sha256", "")))
        require(bootstrap.file_hash(checked_path(item["path"], external=True)) == item["sha256"], "Evidence hash mismatch")
        hashes.add(item["sha256"])
    if kind == "idle":
        require(value.get("scope") == "hosted-all-users" and value.get("authority") == "operator-confirmed" and
                value.get("traffic_frozen") is True and type(value.get("active_runs")) is int and
                value["active_runs"] == 0 and type(value.get("active_writers")) is int and value["active_writers"] == 0,
                "Hosted all-user idle and held traffic must be explicitly attested")
    elif kind == "regression":
        require(type(value.get("passed")) is int and value["passed"] > 0 and
                all(type(value.get(k)) is int and value[k] == 0 for k in ("failed", "errors", "skipped")))
    else:
        workflows = value.get("workflows")
        require(value.get("surface") == "actual-shared-local-app" and isinstance(workflows, list) and len(workflows) >= 2)
        require(all(isinstance(w, dict) and isinstance(w.get("workflow_id"), str) and w["workflow_id"] and
                    w.get("status") == "PASS" and w.get("evidence_sha256") in hashes for w in workflows))
        require(len({w["workflow_id"] for w in workflows}) == len(workflows), "Multiple distinct visible workflows required")
    return bootstrap.file_hash(path)


def complete_journal(plan, fixture):
    path = Path(plan["fixtures"]) / "creation-receipts.jsonl"
    require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024, "Missing bounded fixture creation journal")
    rows = [json.loads(line, object_pairs_hook=unique) for line in path.read_text(encoding="utf-8").splitlines()]
    require(len(rows) >= 2 and all(r.get("plan_sha256") == fixture["plan_sha256"] and
            r.get("package_sha256") == fixture["package_sha256"] for r in rows))
    first, last = rows[0], rows[-1]
    require(first.get("kind") == "plan" and first.get("state") == "preflight" and
            first.get("name") == fixture["catalog"] and first.get("workspace") == plan["host"] and
            first.get("user_id") == plan["bindings"]["user_id"] and first.get("warehouse") == plan["warehouse"],
            "Fixture journal owner/workspace mismatch")
    require(last.get("kind") == "plan" and last.get("state") == "complete" and
            last.get("name") == fixture["catalog"] and last.get("tables_verified") == len(fixture["tables"]) and
            last.get("data_loaded") is False and last.get("bindings_approved") is False)
    allowed = {"preflight", "create_requested", "statement_submitted", "create_acknowledged", "created", "reused-verified", "complete"}
    require(all(r.get("state") in allowed for r in rows), "Partial or uncertain fixture journal")
    objects = {("catalog", fixture["catalog"]): None,
               **{("schema", fixture["catalog"] + "." + layer): None for layer in bootstrap.LAYERS},
               **{("table", t["physical_fqn"]): t for t in fixture["tables"]}}
    require(all((r.get("kind"), r.get("name")) in objects and
                r.get("state") not in {"preflight", "complete"} for r in rows[1:-1]),
            "Journal contains unrelated objects or extra plan lifecycle events")
    successes = [r for r in rows if r["state"] in {"created", "reused-verified"}]
    require(len(successes) == len(objects) and {(r["kind"], r["name"]) for r in successes} == set(objects),
            "Full fixture object completion required")
    for row in successes:
        require(row.get("metadata_verified") is True)
        readback, table = row["readback"], objects[(row["kind"], row["name"])]
        if table is None:
            require(readback == {"full_name": row["name"], "owner": fixture["user_label"], "comment": bootstrap.marker(fixture)})
        else:
            require(readback.get("empty_verified") is True)
            info = SimpleNamespace(full_name=readback["full_name"], owner=readback["owner"],
                                   table_type=readback["table_type"], data_source_format=readback["format"],
                                   properties=readback["properties"], columns=[SimpleNamespace(**c) for c in
                                   ({"name": c["name"], "type_text": c["type"], "nullable": c["nullable"]}
                                    for c in readback["columns"])])
            bootstrap.verify_table(info, fixture, table, fixture["user_label"])
    return bootstrap.file_hash(path)


def command_runner(argv, *, cwd, env, timeout):
    """Single bounded attempt. Never log stdout, stderr, CLI JSON or errors."""
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True,
                                encoding="utf-8", check=False, stdin=subprocess.DEVNULL, shell=False)
        require(result.returncode == 0, "Operator CLI step failed")
        return result.stdout
    except Exception:
        raise RuntimeError("Operator CLI step failed; reconcile durable receipt") from None


@contextmanager
def operator_lock(plan):
    """Persistent lock file, never deleted; process lock is released on close."""
    directory = checked_path(Path(tempfile.gettempdir()) / "orion-existing-app-release-locks", external=True)
    directory.mkdir(exist_ok=True)
    key = bootstrap.fingerprint([plan["host"], plan["app"]["id"]])
    path = checked_path(directory / (key + ".lock"), external=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def apply_release(output, *, expected_release_sha, expected_fixture_sha, consent,
                  idle_receipt, regression_receipt, visible_receipt, confirm_hosted_idle=False,
                  runner=None, identity_provider=None, bootstrap_apply=None, stager=None, clock=None):
    """Injectable seams are for fake tests. CLI provides no replacement commands."""
    output = checked_path(output, external=True)
    plan = load(output / "release-plan.json")
    seal(plan, "release_sha256")
    require(plan.get("format") == FORMAT and plan.get("profile") == "labs" and plan.get("target") == "dev" and
            plan.get("cli_version") == CLI_VERSION and str(output) == plan["output"])
    require(expected_release_sha == plan["release_sha256"] and
            expected_fixture_sha == plan["bindings"]["fixture_plan_sha256"] and
            consent == "RELEASE EXISTING APP " + expected_release_sha, "Exact checksum-bound release consent required")
    require(confirm_hosted_idle is True, "Explicit hosted-idle confirmation flag required")
    xml, fixtures, stage = [checked_path(plan[k], external=True) for k in ("xml", "fixtures", "stage")]
    require(not stage.exists() and not (fixtures / "creation-receipts.jsonl").exists() and
            not (output / "release-receipts.jsonl").exists(), "No automatic retry or overwrite")
    fixture = reviewed_fixture(xml, fixtures)
    require(fixture["plan_sha256"] == expected_fixture_sha and
            {name: bootstrap.file_hash(fixtures / name) for name in EXTERNAL} == plan["deliverables"] and
            plan["bindings"]["package_sha256"] == fixture["package_sha256"] and
            plan["bindings"]["user_sha256"] == fixture["user_sha256"] and
            plan["bindings"]["host"] == plan["host"] and
            plan["bindings"]["app_id"] == plan["app"]["id"] and
            plan["bindings"]["app_name"] == plan["app"]["name"] and
            plan["bindings"]["app_baseline_sha256"] == bootstrap.fingerprint(plan["app"]) and
            plan["bindings"]["replacement_source_sha256"] == bootstrap.fingerprint(plan["replacement_source"]) and
            plan["bindings"]["staged_code_sha256"] == bootstrap.fingerprint(plan["code"]),
            "Gate bindings must describe the exact reviewed release inputs")
    require(bootstrap.file_hash(checked_path(plan["cli"])) == plan["cli_sha256"])
    runner = runner or command_runner
    identity_provider = identity_provider or bootstrap.fresh_profile_identity
    bootstrap_apply = bootstrap_apply or bootstrap.apply_plan
    stager = stager or stage_hosted.stage
    clock = clock or (lambda: datetime.now(timezone.utc))
    gate_paths = {"idle": idle_receipt, "regression": regression_receipt, "visible": visible_receipt}
    journal_hash = None

    def local_gates():
        code, tools = code_preview(plan["host"], plan["app_origin"], plan["catalog_prefix"], plan["warehouse"])
        require(code == plan["code"] and tools == plan["bindings"]["release_tools_sha256"], "Current code drift")
        require(bootstrap.file_hash(xml) == plan["bindings"]["package_sha256"] and
                bootstrap.file_hash(checked_path(plan["cli"])) == plan["cli_sha256"] and
                {name: bootstrap.file_hash(checked_path(fixtures / name)) for name in EXTERNAL} == plan["deliverables"],
                "Current external release deliverable or CLI drift")
        if journal_hash is not None:
            require(complete_journal(plan, fixture) == journal_hash, "Completed fixture journal drift")
        return {kind: gate(path, kind, plan, clock()) for kind, path in gate_paths.items()}

    gate_hashes = local_gates()  # ALL gates before ANY remote operation or provider construction.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("DATABRICKS_") or
           k.upper() == "DATABRICKS_CONFIG_FILE"}
    env["DATABRICKS_HOST"] = plan["host"]
    env["DATABRICKS_AUTH_TYPE"] = "databricks-cli"
    current_step = "preflight"
    with operator_lock(plan), (output / "release-receipts.jsonl").open("x", encoding="utf-8", newline="\n") as journal:
        def record(state, **details):
            journal.write(json.dumps({"time": clock().isoformat(), "release_sha256": expected_release_sha,
                                      "step": current_step, "state": state, **details}, sort_keys=True) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        def step(name, action):
            nonlocal current_step
            current_step = name
            record("requested")
            result = action()
            record("complete")
            return result

        def cli(*args, bundle=False):
            argv = [plan["cli"], *args, "--profile", "labs"]
            if bundle:
                argv += ["-t", "dev", "--var", "app_name=" + plan["app"]["name"]]
            return runner(argv, cwd=str(stage if stage.exists() else output), env=env, timeout=900)

        def get_app():
            value = json.loads(cli("apps", "get", plan["app"]["name"], "-o", "json"), object_pairs_hook=unique)
            # CLI omits optional null fields; explicit projection does not omit them.
            for key in ("resources", "git_repository", "git_source", "default_git_source"):
                value.setdefault(key, None)
            require(not value.get("pending_deployment"), "Unknown pending hosted deployment; do not stop")
            return value

        def preserved(app, *, source=True):
            projection = app_projection(app)
            require({k: projection[k] for k in PRESERVED} == {k: plan["app"][k] for k in PRESERVED}, "Existing app identity/config drift")
            if source:
                require(projection["source"] == plan["app"]["source"], "Existing source/deployment drift")
            return projection

        try:
            record("gates_verified", gate_hashes=gate_hashes, hosted_idle_boundary="operator-attested all-user idle and held traffic; not proved by local selection")
            version = step("cli_version", lambda: runner([plan["cli"], "--version"], cwd=str(output), env=env, timeout=30))
            require(version.strip() in ("Databricks CLI v" + CLI_VERSION, "Databricks CLI " + CLI_VERSION), "Existing CLI version mismatch")
            identity = step("human_identity", lambda: identity_provider("labs"))
            require(identity.mode == "profile" and identity.host == plan["host"] and identity.user_id == plan["bindings"]["user_id"], "Fresh labs human/workspace mismatch")
            bootstrap.confirm_user(identity, fixture)
            baseline = step("baseline_get", get_app)
            preserved(baseline)
            require(baseline.get("app_status", {}).get("state") == "RUNNING", "Unknown existing app lifecycle; no automatic stop")
            record("baseline_verified", app_baseline_sha256=bootstrap.fingerprint(plan["app"]))
            local_gates()
            step("bootstrap_apply", lambda: bootstrap_apply(fixture, xml, fixtures, plan["warehouse"], expected_fixture_sha,
                 "CREATE EMPTY FIXTURES " + expected_fixture_sha, identity_provider=lambda profile: identity))
            journal_hash = complete_journal(plan, fixture)
            deliverables = {"xml": bootstrap.file_hash(xml), **plan["deliverables"], "creation-receipts.jsonl": journal_hash}
            record("external_deliverables_verified", sha256=deliverables)
            local_gates()
            step("stage", lambda: stager(stage, plan["host"], plan["app_origin"], plan["catalog_prefix"], warehouse=plan["warehouse"]))

            def stage_hashes():
                # CLI owns .databricks bundle state, excluded by the bundle sync
                # protocol. It is not app source. Reject symlinks even there.
                all_paths = list(stage.rglob("*"))
                for path in all_paths:
                    checked_path(path)
                files = {p.relative_to(stage).as_posix(): p for p in all_paths if p.is_file() and
                         p.relative_to(stage).parts[0] != ".databricks"}
                require(set(files) == set(plan["code"]) | {"package-manifest.json", "empty-fixture-operator.json"}, "Unexpected staged deliverable")
                for name, info in plan["code"].items():
                    path = checked_path(files[name])
                    require(path.stat().st_size == info["size"] and bootstrap.file_hash(path) == info["sha256"], "Staged code does not match reviewed gates")
                for path in files.values():
                    checked_path(path)
                require(load(stage / "package-manifest.json") == [
                    {"path": name, "size": files[name].stat().st_size} for name in sorted(files) if name != "package-manifest.json"], "Staging manifest mismatch")
                operator = load(stage / "empty-fixture-operator.json")
                require(operator.get("automatic") is False and operator.get("module") == "workbench.package_fixture_bootstrap")
                return {name: bootstrap.file_hash(path) for name, path in sorted(files.items())}

            staged_hashes = stage_hashes()
            record("staged_code_verified", staged_code_sha256=plan["bindings"]["staged_code_sha256"], staged_files=staged_hashes)
            require(local_gates() == gate_hashes)
            step("bundle_validate", lambda: cli("bundle", "validate", bundle=True))
            require(local_gates() == gate_hashes and stage_hashes() == staged_hashes)
            # New stages have no bundle state. Explicit bind prevents creating a namesake app.
            step("bind_existing", lambda: cli("bundle", "deployment", "bind", "workbench", plan["app"]["id"], "--auto-approve", bundle=True))
            def recheck():
                require(local_gates() == gate_hashes)
                require(stage_hashes() == staged_hashes)
                require(complete_journal(plan, fixture) == journal_hash)
                require({name: bootstrap.file_hash(fixtures / name) for name in EXTERNAL} == plan["deliverables"])
                value = get_app()
                preserved(value)
                require(value.get("app_status", {}).get("state") == "RUNNING")
            step("idle_recheck", recheck)
            step("stop_confirmed_idle_app", lambda: cli("apps", "stop", plan["app"]["name"], "-o", "json"))

            def stopped(*, deployed=False):
                app = get_app()
                projection = preserved(app, source=not deployed)
                if deployed:
                    require(app["source_code_path"] == plan["replacement_source"]["source_code_path"] and
                            projection["source"]["active_deployment"]["deployment_id"] ==
                            plan["app"]["source"]["active_deployment"]["deployment_id"],
                            "Unexpected source/start before bundle run")
                require(app.get("compute_status", {}).get("state") == "STOPPED", "Old writer not verified stopped; no deployment/start")
            step("verify_stopped", stopped)
            require(local_gates() == gate_hashes and stage_hashes() == staged_hashes)
            step("bundle_deploy", lambda: cli("bundle", "deploy", "--auto-approve", bundle=True))
            step("verify_no_writer", lambda: stopped(deployed=True))
            require(local_gates() == gate_hashes and stage_hashes() == staged_hashes)
            step("bundle_run_workbench", lambda: cli("bundle", "run", "workbench", bundle=True))
            app = step("app_readback", get_app)
            projection = preserved(app, source=False)
            require(app.get("app_status", {}).get("state") == "RUNNING" and
                    app.get("active_deployment", {}).get("status", {}).get("state") == "SUCCEEDED" and
                    projection["source"]["active_deployment"]["deployment_id"] !=
                    plan["app"]["source"]["active_deployment"]["deployment_id"] and
                    {"source_code_path": projection["source"]["source_code_path"],
                     "default_source_code_path": projection["source"]["default_source_code_path"],
                     "active_source_code_path": projection["source"]["active_deployment"]["source_code_path"]} ==
                    {k: v.replace("{deployment_id}", projection["source"]["active_deployment"]["deployment_id"])
                     for k, v in plan["replacement_source"].items()},
                    "Replacement status, deployment identity or source configuration is unverified")
            require(local_gates() == gate_hashes and stage_hashes() == staged_hashes)
            record("release_complete", remote_deploy_complete=True, app_identity_preserved=True,
                   deployment_id=projection["source"]["active_deployment"]["deployment_id"],
                   source_configuration_sha256=bootstrap.fingerprint(projection["source"]),
                   external_deliverables=deliverables, remote_source_bytes_verified=False,
                   hosted_user_smoke_verified=False, business_jobs_executed=False)
        except BaseException:
            record("stopped_requires_manual_reconciliation", remote_deploy_complete=False,
                   no_retry=True, no_delete=True, no_automatic_restart=True)
            raise RuntimeError("Release stopped. Inspect durable receipt; no automatic retry, restart or cleanup.") from None
    return {"state": "release_complete", "remote_deploy_complete": True, "hosted_user_smoke_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    for key in ("xml", "fixtures", "stage", "cli", "baseline", "source-config", "idle-receipt", "regression-receipt", "visible-receipt"):
        parser.add_argument("--" + key, type=Path)
    for key in ("workspace-host", "app-origin", "app-name", "app-id", "user-id", "warehouse",
                "expected-release-sha", "expected-fixture-sha", "consent"):
        parser.add_argument("--" + key)
    parser.add_argument("--catalog-prefix", default="orion_dev_")
    parser.add_argument("--confirm-hosted-idle", action="store_true")
    args = vars(parser.parse_args(argv))
    try:
        if args["apply"]:
            require(all(args[k] for k in ("expected_release_sha", "expected_fixture_sha", "consent", "idle_receipt", "regression_receipt", "visible_receipt")))
            require(not any(args[k] for k in ("xml", "fixtures", "stage", "cli", "baseline", "source_config", "workspace_host", "app_origin", "app_name", "app_id", "user_id", "warehouse")), "Apply uses only the saved reviewed plan")
            result = apply_release(**{k: args[k] for k in ("output", "expected_release_sha", "expected_fixture_sha", "consent", "idle_receipt", "regression_receipt", "visible_receipt", "confirm_hosted_idle")})
        else:
            require(not any(args[k] for k in ("expected_release_sha", "expected_fixture_sha", "consent", "idle_receipt", "regression_receipt", "visible_receipt", "confirm_hosted_idle")))
            keys = ("xml", "fixtures", "output", "stage", "workspace_host", "app_origin", "app_name", "app_id", "user_id", "warehouse", "cli", "baseline", "source_config", "catalog_prefix")
            require(all(args[k] for k in keys))
            plan = plan_release(**{k: args[k] for k in keys})
            result = {"state": "review_required", "release_sha256": plan["release_sha256"],
                      "fixture_plan_sha256": plan["bindings"]["fixture_plan_sha256"], "remote_deploy_complete": False}
        print(json.dumps(result, sort_keys=True))
    except Exception:
        raise SystemExit("Release held. Review external plan/gates/receipts. No automatic retry or cleanup.") from None


if __name__ == "__main__":
    main()