"""Additive release sequence contracts. Fake commands/identity only, not executed here.

No network, Databricks CLI, browser, models or customer Jobs. These tests cannot
serve as the required real multiple-workflow visible local verification receipt.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from deployment import release_hosted as release
from deployment import stage_hosted
from workbench import package_fixture_bootstrap as bootstrap

USER = "reviewer@example.test"
HOST = "https://workspace.example.test"
APP_ORIGIN = "https://app.example.test"
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_external_activity(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("No network, CLI, real identity or models in release sequence tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(bootstrap, "fresh_profile_identity", forbidden)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def baseline():
    return {"name": "orion-migration-workbench", "id": "existing-app-id", "url": APP_ORIGIN,
            "user_api_scopes": list(release.SCOPES), "effective_user_api_scopes": [*release.SCOPES, "iam.current-user:read"],
            "compute_min_instances": 1, "compute_max_instances": 1, "compute_size": "MEDIUM",
            "service_principal_id": 123, "service_principal_client_id": "platform-identity-not-for-fixtures",
            "resources": None, "forward_user_access_token": True,
            "git_repository": None, "git_source": None, "default_git_source": None,
            "source_code_path": "/Workspace/Users/reviewer/previous/files",
            "default_source_code_path": "/Workspace/Users/reviewer/src/old-deployment",
            "active_deployment": {"deployment_id": "old-deployment", "source_code_path": "/Workspace/Users/reviewer/src/old-deployment",
                                  "status": {"state": "SUCCEEDED"}},
            "app_status": {"state": "RUNNING"}, "compute_status": {"state": "ACTIVE"}}


class FakeOperator:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.app = baseline()
        self.fail_at = None
        self.pending = False
        self.bad_readback = False
        self.bad_final_source = False
        self.not_stopped = False
        self.bootstrap_incomplete = False
        self.wrong_journal_host = False
        self.wrong_journal_owner = False
        self.drift_stage = False
        self.version = "Databricks CLI v1.13.0\n"
        self.me = NS(id="human-user-id", user_name=USER, active=True,
                     schemas=["urn:ietf:params:scim:schemas:core:2.0:User"])
        self.identity = NS(mode="profile", host=HOST, user_id=self.me.id,
                           client=NS(current_user=NS(me=lambda: self.me)))

    def checkpoint(self, name):
        self.calls.append(name)
        if name == self.fail_at:
            raise RuntimeError("SECRET raw external CLI JSON/credential details must never be copied")

    def provider(self, profile):
        self.checkpoint("identity")
        assert profile == "labs"
        return self.identity

    def apply_fixture(self, plan, xml, output, warehouse, expected_sha, consent, identity_provider):
        self.checkpoint("bootstrap")
        assert plan == self.fixture and bootstrap.file_hash(xml) == plan["package_sha256"]
        assert warehouse == "approved-warehouse" and expected_sha == plan["plan_sha256"]
        assert consent == "CREATE EMPTY FIXTURES " + expected_sha
        assert identity_provider("labs") is self.identity
        with (output / "creation-receipts.jsonl").open("x", encoding="utf-8") as stream:
            bootstrap.event(stream, plan, "plan", plan["catalog"], "preflight", user_id=self.me.id,
                            workspace="https://wrong.example.test" if self.wrong_journal_host else HOST,
                            warehouse=warehouse)
            if self.bootstrap_incomplete:
                bootstrap.event(stream, plan, "plan", plan["catalog"], "uncertain")
                return
            objects = [("catalog", plan["catalog"], None)] + [
                ("schema", plan["catalog"] + "." + layer, None) for layer in bootstrap.LAYERS] + [
                ("table", table["physical_fqn"], table) for table in plan["tables"]]
            for kind, name, table in objects:
                owner = "wrong@example.test" if self.wrong_journal_owner else USER
                readback = {"full_name": name, "owner": owner, "comment": bootstrap.marker(plan)}
                if table:
                    readback = {"full_name": name, "owner": owner, "table_type": "MANAGED", "format": "DELTA",
                                "columns": table["columns"], "properties": bootstrap.properties(plan, table), "empty_verified": True}
                bootstrap.event(stream, plan, kind, name, "created", metadata_verified=True, readback=readback)
            bootstrap.event(stream, plan, "plan", plan["catalog"], "complete", tables_verified=len(plan["tables"]),
                            data_loaded=False, bindings_approved=False)

    def stage(self, *args, **kwargs):
        self.checkpoint("stage")
        result = stage_hosted.stage(*args, **kwargs)
        if self.drift_stage:
            (Path(args[0]) / "workbench/package_fixture_bootstrap.py").write_text("# altered")
        return result

    def runner(self, argv, *, cwd, env, timeout):
        assert Path(argv[0]).is_absolute() and 0 < timeout <= 900
        assert env["DATABRICKS_HOST"] == HOST and env["DATABRICKS_AUTH_TYPE"] == "databricks-cli"
        assert "DATABRICKS_TOKEN" not in env and "DATABRICKS_CLIENT_SECRET" not in env
        if argv[1:] == ["--version"]:
            self.checkpoint("version")
            return self.version
        assert argv[argv.index("--profile") + 1] == "labs"
        if argv[1] == "bundle":
            assert argv[argv.index("-t") + 1] == "dev"
            assert argv[argv.index("--var") + 1] == "app_name=orion-migration-workbench"
            assert Path(cwd).name == "stage"
            name = argv[2]
            if name == "deployment":
                assert argv[3:6] == ["bind", "workbench", "existing-app-id"]
                name = "bind"
            elif name == "run":
                assert argv[3] == "workbench"  # App resource only, never a Job.
            self.checkpoint(name)
            # Real CLI emits state, which must not invalidate source hashes.
            state = Path(cwd) / ".databricks/bundle/dev"
            state.mkdir(parents=True, exist_ok=True)
            (state / "state.json").write_text("{}")
            if name == "deploy":
                self.app["source_code_path"] = "/Workspace/Users/reviewer/replacement/files"
            if name == "run":
                self.app["compute_status"]["state"] = "ACTIVE"
                self.app["app_status"]["state"] = "RUNNING"
                new_source = "/Workspace/Users/reviewer/src/new-deployment"
                self.app["default_source_code_path"] = new_source
                self.app["active_deployment"] = {"deployment_id": "new-deployment", "source_code_path": new_source,
                                                  "status": {"state": "SUCCEEDED"}}
                if self.bad_readback:
                    self.app["service_principal_id"] = 999
                if self.bad_final_source:
                    self.app["source_code_path"] = "/Workspace/Users/unknown/files"
            return "SECRET CLI JSON not to log"
        assert argv[1] == "apps" and argv[2] in {"get", "stop"}
        self.checkpoint(argv[2])
        if argv[2] == "get":
            result = deepcopy(self.app)
            if self.pending:
                result["pending_deployment"] = {"deployment_id": "unknown-in-flight"}
            return json.dumps(result)
        assert argv[3] == "orion-migration-workbench"
        if not self.not_stopped:
            self.app["compute_status"]["state"] = "STOPPED"
            self.app["app_status"]["state"] = "STOPPED"
        return "SECRET stop output not to log"


@pytest.fixture
def scenario(tmp_path):
    xml = tmp_path / "package.xml"
    xml.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <SOURCE NAME="CONTROL" DATABASETYPE="Oracle" DBDNAME="D" OWNERNAME="$$OWNER">
        <SOURCEFIELD NAME="COUNT_VALUE" DATATYPE="number" PRECISION="38" SCALE="5" NULLABLE="NULL"/>
      </SOURCE>
      <TARGET NAME="CONTROL" DATABASETYPE="Oracle" OWNERNAME="$$TARGET">
        <TARGETFIELD NAME="COUNT_VALUE" DATATYPE="number" PRECISION="38" SCALE="5" NULLABLE="NULL"/>
      </TARGET>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    fixture = bootstrap.build_plan(xml, USER)
    fixtures = tmp_path / "fixtures"
    bootstrap.export_plan(fixture, fixtures)
    app = tmp_path / "baseline.json"
    write_json(app, baseline())
    source = tmp_path / "source-config.json"
    write_json(source, {"source_code_path": "/Workspace/Users/reviewer/replacement/files",
                        "default_source_code_path": "/Workspace/Users/reviewer/src/{deployment_id}",
                        "active_source_code_path": "/Workspace/Users/reviewer/src/{deployment_id}"})
    cli = tmp_path / "fake-databricks.exe"
    cli.write_bytes(b"not executable, fake runner only")
    options = dict(xml=xml, fixtures=fixtures, output=tmp_path / "release", stage=tmp_path / "stage",
                   workspace_host=HOST, app_origin=APP_ORIGIN, app_name="orion-migration-workbench",
                   app_id="existing-app-id", user_id="human-user-id", warehouse="approved-warehouse",
                   cli=cli, baseline=app, source_config=source)
    plan = release.plan_release(**options)
    operator = FakeOperator(fixture)
    gates = {}
    for kind in ("idle", "regression", "visible"):
        evidence = tmp_path / (kind + "-evidence.txt")
        evidence.write_text("operator-reviewed actual evidence for " + kind)
        evidence_sha = bootstrap.file_hash(evidence)
        value = {"format": "orion-hosted-release-gate-v1", "kind": kind, "status": "VERIFIED", "bindings": plan["bindings"],
                 "verified_at": NOW.isoformat(), "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
                 "evidence": [{"path": str(evidence), "sha256": evidence_sha}]}
        if kind == "idle":
            value.update(scope="hosted-all-users", authority="operator-confirmed", traffic_frozen=True, active_runs=0, active_writers=0)
        elif kind == "regression":
            value.update(passed=10, failed=0, errors=0, skipped=0)
        else:
            value.update(surface="actual-shared-local-app", workflows=[
                {"workflow_id": workflow, "status": "PASS", "evidence_sha256": evidence_sha} for workflow in ("workflow-one", "workflow-two")])
        value["receipt_sha256"] = bootstrap.fingerprint(value)
        gates[kind] = tmp_path / (kind + ".json")
        write_json(gates[kind], value)
    return NS(plan=plan, fixture=fixture, options=options, gates=gates, operator=operator)


def apply(scenario, **overrides):
    plan, operator = scenario.plan, scenario.operator
    kwargs = dict(expected_release_sha=plan["release_sha256"], expected_fixture_sha=scenario.fixture["plan_sha256"],
                  consent="RELEASE EXISTING APP " + plan["release_sha256"], idle_receipt=scenario.gates["idle"],
                  regression_receipt=scenario.gates["regression"], visible_receipt=scenario.gates["visible"],
                  confirm_hosted_idle=True, runner=operator.runner, identity_provider=operator.provider,
                  bootstrap_apply=operator.apply_fixture, stager=operator.stage, clock=lambda: NOW)
    kwargs.update(overrides)
    return release.apply_release(plan["output"], **kwargs)


def receipts(scenario):
    path = Path(scenario.plan["output"]) / "release-receipts.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def revise_gate(scenario, kind, edit):
    path = scenario.gates[kind]
    value = release.load(path)
    edit(value)
    value["receipt_sha256"] = bootstrap.fingerprint({k: v for k, v in value.items() if k != "receipt_sha256"})
    write_json(path, value)


def test_dry_plan_does_not_stage_authenticate_or_execute(scenario):
    assert scenario.operator.calls == []
    assert not Path(scenario.plan["stage"]).exists()
    assert not (Path(scenario.plan["fixtures"]) / "creation-receipts.jsonl").exists()
    assert scenario.plan["remote_deploy_complete"] is False
    assert release.load(Path(scenario.plan["output"]) / "release-plan.json") == scenario.plan
    assert all(not name.endswith(".xml") for name in scenario.plan["code"])


def test_current_quoted_templates_match_preview_and_exact_staged_bytes(tmp_path):
    entries, tools = release.code_preview(HOST, APP_ORIGIN, "orion_dev_", "approved-warehouse")
    stage = tmp_path / "stage"
    manifest = stage_hosted.stage(stage, HOST, APP_ORIGIN, "orion_dev_", warehouse="approved-warehouse")
    assert {row["path"] for row in manifest} == set(entries) | {"empty-fixture-operator.json"}
    for name, info in entries.items():
        assert (stage / name).stat().st_size == info["size"]
        assert bootstrap.file_hash(stage / name) == info["sha256"]
    assert release.load(stage / "package-manifest.json") == manifest
    assert tools == bootstrap.fingerprint({name: bootstrap.file_hash(release.ROOT / name) for name in (
        "deployment/stage_hosted.py", "deployment/release_hosted.py", "workbench/tests/test_fixture_release_sequence.py")})
    runtime = yaml.safe_load((stage / "app.yaml").read_text(encoding="utf-8"))
    assert runtime == {"command": ["python", "-m", "workbench.hosted"], "env": [
        {"name": key, "value": value} for key, value in (
            ("WORKBENCH_TRUST_DATABRICKS_PROXY", "1"), ("WORKBENCH_DATABRICKS_HOST", HOST),
            ("WORKBENCH_APP_ORIGIN", APP_ORIGIN), ("WORKBENCH_CATALOG_PREFIX", "orion_dev_"),
            ("WORKBENCH_SQL_WAREHOUSE_ID", "approved-warehouse"), ("PYTHONDONTWRITEBYTECODE", "1"),
            ("LANGCHAIN_TRACING_V2", "false"), ("LANGSMITH_TRACING", "false"))]}
    bundle = yaml.safe_load((stage / "databricks.yml").read_text(encoding="utf-8"))
    assert set(bundle["resources"]) == {"apps"}
    assert set(bundle["resources"]["apps"]) == {"workbench"}
    app = bundle["resources"]["apps"]["workbench"]
    assert app["config"] == runtime
    assert app["name"] == "${var.app_name}" and app["source_code_path"] == "."
    assert type(app["compute_min_instances"]) is int and app["compute_min_instances"] == 1
    assert type(app["compute_max_instances"]) is int and app["compute_max_instances"] == 1
    assert app["user_api_scopes"] == ["catalog.catalogs", "catalog.schemas", "catalog.tables", "files", "model-serving", "sql"]


def test_config_comparison_ignores_only_yaml_presentation():
    template = (release.ROOT / "deployment/hosted/app.yaml").read_text(encoding="utf-8")
    reformatted = template.rstrip("\n") + "\n\n# Same reviewed config\n"
    reformatted = reformatted.replace('value: "false"', "value: 'false'")
    assert release.config_fingerprint(template) == release.config_fingerprint(reformatted)


@pytest.mark.parametrize("name,old,new", [
    ("app.yaml", 'value: "false"', "value: false"),
    ("app.yaml", 'value: "1"', "value: 1"),
    ("app.yaml", 'value: "__WORKSPACE_HOST__"', 'value: "https://wrong.example.test"'),
    ("app.yaml", 'value: "__APP_ORIGIN__"', 'value: "https://wrong.example.test"'),
    ("app.yaml", 'value: "__CATALOG_PREFIX__"', 'value: "wrong_"'),
    ("app.yaml", 'value: "__SQL_WAREHOUSE_ID__"', 'value: "wrong-warehouse"'),
    ("app.yaml", 'command: ["python", "-m", "workbench.hosted"]', 'command: ["python", "-m", "workbench.package_fixture_bootstrap"]'),
    ("app.yaml", "env:\n", 'extra: true\nenv:\n'),
    ("app.yaml", "env:\n", 'command: ["python"]\nenv:\n'),
    ("databricks.yml", "compute_max_instances: 1", "compute_max_instances: true"),
    ("databricks.yml", "        - sql", "        - jobs"),
    ("databricks.yml", "name: ${var.app_name}", "name: other-app"),
    ("databricks.yml", "source_code_path: .", "source_code_path: /Workspace/other"),
    ("databricks.yml", "  apps:\n", "  jobs: {}\n  apps:\n"),
    ("databricks.yml", 'value: "false"', "value: false"),
])
def test_actual_template_config_drift_is_rejected_without_staging(monkeypatch, name, old, new):
    original = Path.read_text
    template = release.ROOT / "deployment/hosted" / name
    assert old in original(template, encoding="utf-8")

    def changed(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text.replace(old, new, 1) if path == template else text

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(ValueError):
        release.code_preview(HOST, APP_ORIGIN, "orion_dev_", "approved-warehouse")


def test_ordered_release_only_after_exact_fixture_journal_and_gates(scenario, monkeypatch, capsys):
    monkeypatch.setenv("DATABRICKS_TOKEN", "must-not-be-inherited")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "must-not-be-inherited")
    assert apply(scenario)["remote_deploy_complete"] is True
    assert scenario.operator.calls == ["version", "identity", "get", "bootstrap", "stage", "validate", "bind",
                                       "get", "stop", "get", "deploy", "get", "run", "get"]
    rows = receipts(scenario)
    final = rows[-1]
    assert final["state"] == "release_complete" and final["app_identity_preserved"]
    assert not final["remote_source_bytes_verified"] and not final["hosted_user_smoke_verified"]
    assert not final["business_jobs_executed"]
    assert set(final["external_deliverables"]) == {"xml", *release.EXTERNAL, "creation-receipts.jsonl"}
    stage = Path(scenario.plan["stage"])
    assert all(".xml" != p.suffix.lower() for p in stage.rglob("*"))
    staged = next(row["staged_files"] for row in rows if row["state"] == "staged_code_verified")
    assert all(bootstrap.file_hash(stage / name) == sha for name, sha in staged.items())
    assert not capsys.readouterr().out
    assert "SECRET" not in (Path(scenario.plan["output"]) / "release-receipts.jsonl").read_text()


@pytest.mark.parametrize("kind", ["idle", "regression", "visible"])
def test_missing_gate_fails_before_any_remote_or_bootstrap(scenario, kind):
    scenario.gates[kind].unlink()  # Owned test fixture only, production never deletes.
    with pytest.raises((ValueError, FileNotFoundError)):
        apply(scenario)
    assert scenario.operator.calls == []
    assert not Path(scenario.plan["stage"]).exists()


@pytest.mark.parametrize("change", ["local-idle", "active", "no-freeze", "expired", "wrong-code", "wrong-owner", "wrong-host",
                                   "failed-regression", "skipped-regression", "single-workflow", "offline-browser", "evidence-drift"])
def test_bad_gates_never_authenticate_or_mutate(scenario, change):
    kind = "idle"
    edits = {
        "local-idle": lambda v: v.update(scope="selected-local-app"),
        "active": lambda v: v.update(active_writers=1),
        "no-freeze": lambda v: v.update(traffic_frozen=False),
        "expired": lambda v: v.update(expires_at=NOW.isoformat()),
        "wrong-code": lambda v: v["bindings"].update(staged_code_sha256="0" * 64),
        "wrong-owner": lambda v: v["bindings"].update(user_sha256="0" * 64),
        "wrong-host": lambda v: v["bindings"].update(host="https://other.example.test"),
        "failed-regression": lambda v: v.update(failed=1),
        "skipped-regression": lambda v: v.update(skipped=1),
        "single-workflow": lambda v: v.update(workflows=v["workflows"][:1]),
        "offline-browser": lambda v: v.update(surface="offline-browser-harness"),
        "evidence-drift": lambda v: v["evidence"][0].update(sha256="0" * 64),
    }
    if change in {"failed-regression", "skipped-regression"}:
        kind = "regression"
    elif change in {"single-workflow", "offline-browser"}:
        kind = "visible"
    revise_gate(scenario, kind, edits[change])
    with pytest.raises(ValueError):
        apply(scenario)
    assert scenario.operator.calls == []


@pytest.mark.parametrize("override", [{"confirm_hosted_idle": False}, {"consent": "yes"},
                                     {"expected_fixture_sha": "0" * 64}, {"expected_release_sha": "0" * 64}])
def test_exact_consent_fixture_and_release_hash_required(scenario, override):
    with pytest.raises(ValueError):
        apply(scenario, **override)
    assert scenario.operator.calls == []


@pytest.mark.parametrize("where", ["xml", "source-tables.sql", "target-tables.sql", "cli", "code"])
def test_input_or_current_code_drift_blocks_before_remote(scenario, where, monkeypatch):
    if where == "code":
        original = release.code_preview
        def drift(*args):
            entries, tools = original(*args)
            entries["app.yaml"]["sha256"] = "0" * 64
            return entries, tools
        monkeypatch.setattr(release, "code_preview", drift)
    else:
        path = Path(scenario.plan[where]) if where in {"xml", "cli"} else Path(scenario.plan["fixtures"]) / where
        with path.open("ab") as stream:
            stream.write(b"\nchanged")
    with pytest.raises(ValueError):
        apply(scenario)
    assert scenario.operator.calls == []


@pytest.mark.parametrize("fail_at", ["version", "identity", "get", "bootstrap", "stage", "validate", "bind", "stop", "deploy", "run"])
def test_step_failure_stops_later_steps_and_retains_partial_receipt(scenario, fail_at):
    scenario.operator.fail_at = fail_at
    with pytest.raises(RuntimeError, match="Release stopped"):
        apply(scenario)
    assert scenario.operator.calls[-1] == fail_at
    final = receipts(scenario)[-1]
    assert final["state"] == "stopped_requires_manual_reconciliation"
    assert final["no_retry"] and final["no_delete"] and not final["remote_deploy_complete"]
    assert "SECRET" not in json.dumps(receipts(scenario))
    assert Path(scenario.plan["output"]).exists()
    assert (Path(scenario.plan["fixtures"]) / "creation-manifest.json").exists()


@pytest.mark.parametrize("bad", ["pending", "identity-host", "service-principal", "journal-incomplete", "journal-host", "journal-owner",
                                "staged-code", "not-stopped", "version", "readback", "final-source"])
def test_unknown_activity_and_identity_journal_stage_or_readback_drift_fail_closed(scenario, bad):
    operator = scenario.operator
    if bad == "pending":
        operator.pending = True
    elif bad == "identity-host":
        operator.identity.host = "https://wrong.example.test"
    elif bad == "service-principal":
        operator.me.schemas = ["urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal"]
    elif bad == "journal-incomplete":
        operator.bootstrap_incomplete = True
    elif bad == "journal-host":
        operator.wrong_journal_host = True
    elif bad == "journal-owner":
        operator.wrong_journal_owner = True
    elif bad == "staged-code":
        operator.drift_stage = True
    elif bad == "not-stopped":
        operator.not_stopped = True
    elif bad == "version":
        operator.version = "Databricks CLI v999\n"
    elif bad == "readback":
        operator.bad_readback = True
    else:
        operator.bad_final_source = True
    with pytest.raises(RuntimeError):
        apply(scenario)
    assert receipts(scenario)[-1]["state"] == "stopped_requires_manual_reconciliation"
    if bad not in {"not-stopped", "readback", "final-source"}:
        assert "stop" not in operator.calls
    if bad == "not-stopped":
        assert "deploy" not in operator.calls and "run" not in operator.calls
    if bad.startswith("journal"):
        assert "stage" not in operator.calls


def test_repeated_release_is_rejected_without_commands_or_receipt_cleanup(scenario):
    apply(scenario)
    before = (Path(scenario.plan["output"]) / "release-receipts.jsonl").read_bytes()
    calls = list(scenario.operator.calls)
    with pytest.raises(ValueError):
        apply(scenario)
    assert scenario.operator.calls == calls
    assert before == (Path(scenario.plan["output"]) / "release-receipts.jsonl").read_bytes()
    assert Path(scenario.plan["stage"]).exists()


def test_operator_lock_rejects_overlap_before_remote(scenario):
    with release.operator_lock(scenario.plan):
        with pytest.raises(OSError):
            apply(scenario)
    assert scenario.operator.calls == []


def test_gate_expiry_during_bootstrap_holds_stage(scenario):
    original = scenario.operator.apply_fixture
    current = [NOW]
    def bootstrap_then_expire(*args, **kwargs):
        original(*args, **kwargs)
        current[0] = NOW + timedelta(minutes=11)
    with pytest.raises(RuntimeError):
        apply(scenario, bootstrap_apply=bootstrap_then_expire, clock=lambda: current[0])
    assert "bootstrap" in scenario.operator.calls and "stage" not in scenario.operator.calls
    assert (Path(scenario.plan["fixtures"]) / "creation-receipts.jsonl").exists()


@pytest.mark.parametrize("field,value", [("id", "wrong"), ("compute_max_instances", 2), ("user_api_scopes", ["sql"]),
                                       ("forward_user_access_token", False), ("resources", [{"name": "new"}])])
def test_baseline_drift_prevents_bootstrap_or_app_mutation(scenario, field, value):
    scenario.operator.app[field] = value
    with pytest.raises(RuntimeError):
        apply(scenario)
    assert "bootstrap" not in scenario.operator.calls and "stop" not in scenario.operator.calls


def test_cli_missing_apply_gates_and_checksum_prints_no_raw_values(scenario, capsys):
    with pytest.raises(SystemExit, match="Release held"):
        release.main(["--apply", "--output", scenario.plan["output"], "--consent", "SECRET"])
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err
    assert scenario.operator.calls == []


def test_dry_plan_default_cli_rejects_existing_outputs_without_auth(scenario):
    args = []
    for key, value in scenario.options.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    with pytest.raises(SystemExit, match="Release held"):
        release.main(args)
    assert scenario.operator.calls == []


def test_cli_default_new_plan_is_offline_and_reports_not_deployed(scenario, tmp_path, capsys):
    options = {**scenario.options, "output": tmp_path / "second-release", "stage": tmp_path / "second-stage"}
    args = []
    for key, value in options.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    release.main(args)
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "review_required" and result["remote_deploy_complete"] is False
    assert not options["stage"].exists() and scenario.operator.calls == []


def test_command_runner_quarantines_error_payloads_without_real_cli(monkeypatch, capsys):
    import subprocess

    calls = []
    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return NS(returncode=1, stdout='{"access_token":"SECRET"}', stderr="SECRET CLI error")
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Operator CLI step failed") as caught:
        release.command_runner(["fake", "bundle", "validate"], cwd="fake", env={}, timeout=30)
    assert "SECRET" not in str(caught.value)
    assert calls[0][1]["capture_output"] and calls[0][1]["shell"] is False
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert not capsys.readouterr().out