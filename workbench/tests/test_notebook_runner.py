import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from threading import BoundedSemaphore

from workbench.notebook_runner import _clean_output, _parameter_policy, install_notebook_runner, parameter_names


def test_widget_parameter_names_are_scoped_to_python_cells():
    notebook = {"cells": [
        {"cell_type": "markdown", "source": 'dbutils.widgets.get("not_a_parameter")'},
        {"cell_type": "code", "source": 'x = dbutils.widgets.get("demo_mode")\ny = _get_widget("source_table", "")'},
    ]}
    assert parameter_names(notebook) == {"demo_mode", "source_table"}


def test_wait_notebook_is_demo_only_and_has_bounded_runtime():
    parameters = {"demo_mode": "false", "wait_seconds": "300"}
    policy = _parameter_policy("notebooks/wkf/t_wait.ipynb", parameters, True, "", "bronze", "time.sleep(wait_seconds)")
    assert policy["mutates_data"] is False
    assert policy["wait_seconds"] == 300
    assert parameters["demo_mode"] == "true"
    with pytest.raises(HTTPException) as error:
        _parameter_policy("notebooks/wkf/t_wait.ipynb", {"wait_seconds": "301"}, True, "", "bronze")
    assert error.value.status_code == 422


def test_read_only_counter_does_not_require_destination_parameters():
    parameters = {"uc_catalog": "main", "bronze_source_table": "main.raw.counter", "demo_mode": "true"}
    result = _parameter_policy("notebooks/wkf/t_counter.ipynb", parameters, False, "", "bronze",
                               "source_df = spark.sql(source_sql)")
    assert result['mutates_data'] is False
    assert result['output_table'] is None
    assert 'target_table' not in parameters


def test_counter_requires_exact_distinct_source_and_confirmed_output():
    mutating_prefix = ("source_df = spark.sql(source_sql)\n"
        'source_df.write.format("delta").mode("append").saveAsTable(output_fqn)\n'
        "spark.sql(post_insert_sql)\nspark.sql(delete_sql)")
    parameters = {"uc_catalog": "main", "bronze_source_table": "main.raw.counter",
        "dev_catalog": "main", "dev_schema": "orion_demo", "dev_table": "counter_dev", "demo_mode": "true"}
    policy = _parameter_policy("notebooks/wkf/t_counter.ipynb", parameters, True,
                               "main.orion_demo.counter_dev", "bronze", mutating_prefix)
    assert policy["mutates_data"] is True
    assert policy["source_table"] == "main.raw.counter"
    assert policy["output_table"] == "main.orion_demo.counter_dev"
    with pytest.raises(HTTPException) as missing_ack:
        _parameter_policy("notebooks/wkf/t_counter.ipynb", dict(parameters), False,
                          "main.orion_demo.counter_dev", "bronze", mutating_prefix)
    assert missing_ack.value.status_code == 403
    with pytest.raises(HTTPException) as collision:
        same = {**parameters, "target_table": "", "dev_catalog": "main", "dev_schema": "raw", "dev_table": "counter"}
        _parameter_policy("notebooks/wkf/t_counter.ipynb", same, True, "main.raw.counter", "bronze", mutating_prefix)
    assert collision.value.status_code == 422


def test_counter_rejects_non_uc_source_scenario_and_invalid_identifiers():
    read_prefix = "source_df = spark.sql(source_sql)"
    base = {"uc_catalog": "main", "bronze_source_table": "main.raw.counter",
        "dev_catalog": "main", "dev_schema": "orion_demo", "dev_table": "counter_dev"}
    with pytest.raises(HTTPException) as scenario:
        _parameter_policy("notebooks/wkf/t_counter.ipynb", base, True,
                          "main.orion_demo.counter_dev", "original", read_prefix)
    assert scenario.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        _parameter_policy("notebooks/wkf/t_counter.ipynb", {**base, "dev_schema": "raw; DROP TABLE x"}, True,
                          "main.raw; DROP TABLE x.counter_dev", "bronze", read_prefix)
    assert invalid.value.status_code == 422


def test_placeholder_notebooks_cannot_disable_demo_mode_or_start_side_effects():
    for path in ("notebooks/wkf/t_child_launch.ipynb", "notebooks/wkf/t_email.ipynb"):
        with pytest.raises(HTTPException) as error:
            _parameter_policy(path, {"demo_mode": "false"}, True, "", "bronze")
        assert error.value.status_code == 422
        result = _parameter_policy(path, {"demo_mode": "true"}, True, "", "bronze")
        assert result["mutates_data"] is False


def test_run_output_is_redacted_and_bounded():
    text = "safe\npassword='never-print-this'\n" + "x" * 50
    output = _clean_output(text, 20)
    assert len(output) <= 20
    assert "never-print-this" not in output


def test_one_time_serverless_run_is_scoped_and_returns_notebook_output(monkeypatch, tmp_path):
    import workbench.databricks_models as models
    import workbench.draft_artifact_reads as artifact_reads
    import workbench.redesign as redesign

    package_hash, design_id, input_version = "a" * 64, "b" * 64, "c" * 64
    job_id, run_id, user_id = "d" * 32, 987654, "test-user"
    artifact_path = "notebooks/wkf/t_wait.ipynb"
    notebook = {"nbformat": 4, "cells": [
        {"cell_type": "markdown", "metadata": {"id": "cell-intro", "language": "markdown"}, "source": "# Wait test"},
        {"cell_type": "code", "metadata": {"id": "cell-one", "language": "python"},
         "source": 'dbutils.widgets.get("demo_mode")\ndbutils.widgets.get("wait_seconds")'},
        {"cell_type": "code", "metadata": {"id": "cell-two", "language": "python"},
         "source": 'print("later cell must not run")'}]}
    content = __import__("json").dumps(notebook)
    job = {"id": job_id, "kind": "spark_draft", "status": "completed", "package_hash": package_hash,
        "workflow_id": "wf/selected", "scenario": "bronze", "design_id": design_id,
        "design_version": 3, "input_version": input_version, "output": {"run_id": "e" * 32}, "manifest_sha256": "f" * 64}
    manifest = {"design_id": design_id, "design_version": 3, "package_hash": package_hash,
        "input_version": input_version, "scenario": "bronze"}
    record = {**job}
    app_service = SimpleNamespace(root=tmp_path, state=tmp_path, agent_slot=BoundedSemaphore(1),
        require=lambda: SimpleNamespace(model={"package": {"sha256": package_hash}}),
        agent_inputs=lambda index, workflow_id, scenario: {"input_version": input_version},
        record=lambda requested: record if requested == job_id else None)

    calls = []
    state = {"life_cycle_state": "RUNNING", "result_state": None}
    class Client:
        current_user = SimpleNamespace(me=lambda: SimpleNamespace(id=user_id, user_name="user@example.com"))
        workspace = SimpleNamespace(
            mkdirs=lambda path: calls.append(("mkdirs", path)),
            upload=lambda *args, **kwargs: calls.append(("upload", args, kwargs)),
            get_status=lambda path: SimpleNamespace(object_id=123))
        jobs = SimpleNamespace(
            submit=lambda **kwargs: (calls.append(("submit", kwargs)) or SimpleNamespace(response=SimpleNamespace(run_id=run_id))),
            cancel_run=lambda selected: calls.append(("cancel_run", selected)),
            get_run=lambda selected: SimpleNamespace(state=SimpleNamespace(
                life_cycle_state=state["life_cycle_state"], result_state=state["result_state"]),
                run_page_url="https://workspace.example/jobs/1/runs/987654"),
            get_run_output=lambda selected: SimpleNamespace(
                notebook_output=SimpleNamespace(result="wait completed", truncated=False),
                logs="safe runtime log", logs_truncated=False, error=None))

    identity = SimpleNamespace(host="https://workspace.example", user_id=user_id, client=Client())
    monkeypatch.setattr(models, "_profile_host", lambda: identity.host)
    monkeypatch.setattr(models, "profile_identity", lambda profile: identity)
    monkeypatch.setattr(artifact_reads, "read_result", lambda root, record, selected: {
        "manifest": manifest, "artifacts": {}, "staged_artifacts": {artifact_path: content}})
    exact = {"id": design_id, "version": 3, "workflow_id": "wf/selected", "scenario": "bronze",
        "input_version": input_version,
        "current_input_version": input_version, "stale": False}
    monkeypatch.setattr(redesign, "store_for", lambda service: SimpleNamespace(get=lambda *args: exact))

    app = FastAPI()
    app.state.service = app_service
    install_notebook_runner(app)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50123))
    attached = client.post(f"/api/jobs/{job_id}/spark-drafts/attach", json={
        "consent": True, "artifact_path": artifact_path, "expected_workspace": identity.host})
    assert attached.status_code == 200, attached.text
    assert attached.json()["notebook_url"] == identity.host + "/editor/notebooks/123"
    assert attached.json()["execution_started"] is False
    assert not any(call[0] == "submit" for call in calls)
    assert calls[-1][1][1] == content.encode("utf-8")
    calls.clear()
    response = client.post(f"/api/jobs/{job_id}/spark-drafts/run", json={
        "consent": True, "artifact_path": artifact_path, "cell_number": 2, "expected_workspace": identity.host,
        "parameters": {"demo_mode": "true", "wait_seconds": "0"},
        "side_effects_confirmed": True, "confirmed_output_table": ""})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result.get("workspace_run_id") == run_id and result["status"] == "queued" and result["cell_number"] == 2, result.get("message", result)
    submission = next(call[1] for call in calls if call[0] == "submit")
    assert submission["performance_target"].value == "STANDARD"
    assert submission["tasks"][0].max_retries == 0
    assert submission["tasks"][0].new_cluster is None
    assert submission["tasks"][0].notebook_task.base_parameters == {"demo_mode": "true", "wait_seconds": "0"}
    upload = next(call for call in calls if call[0] == "upload")
    uploaded_notebook = __import__("json").loads(upload[1][1])
    assert len(uploaded_notebook["cells"]) == 2
    assert "later cell must not run" not in uploaded_notebook["cells"][-1]["source"]
    assert upload[2]["format"].value == "JUPYTER"

    status = client.get(f"/api/notebook-runs/{result['id']}")
    assert status.status_code == 200 and status.json()["status"] == "running"
    stopped = client.post(f"/api/notebook-runs/{result['id']}/cancel", json={"consent": True})
    assert stopped.status_code == 202 and stopped.json()["status"] == "cancel_requested"
    repeated_stop = client.post(f"/api/notebook-runs/{result['id']}/cancel", json={"consent": True})
    assert repeated_stop.status_code == 202
    assert sum(call[0] == "cancel_run" for call in calls) == 1
    state.update(life_cycle_state="TERMINATED", result_state="SUCCESS")
    finished = client.get(f"/api/notebook-runs/{result['id']}")
    assert finished.status_code == 200
    assert finished.json()["output"] == "wait completed"
    assert finished.json()["logs"] == "safe runtime log"
    assert sum(call[0] == "submit" for call in calls) == 1


def test_counter_collision_fails_before_databricks_identity_or_upload(monkeypatch, tmp_path):
    import json
    import workbench.draft_artifact_reads as artifact_reads
    import workbench.redesign as redesign

    package_hash, design_id, input_version = "a" * 64, "b" * 64, "c" * 64
    job_id = "d" * 32
    path = "notebooks/wkf/t_counter.ipynb"
    notebook = {"nbformat": 4, "cells": [{"cell_type": "code", "metadata": {"id": "cell-counter", "language": "python"},
        "source": 'dbutils.widgets.get("demo_mode")\ndbutils.widgets.get("uc_catalog")\n'
                  'dbutils.widgets.get("bronze_source_table")\ndbutils.widgets.get("dev_catalog")\n'
                  'dbutils.widgets.get("dev_schema")\ndbutils.widgets.get("dev_table")\n'
                  'dbutils.widgets.get("target_table")\nsource_df = spark.sql(source_sql)\n'
                  'source_df.write.format("delta").mode("append").saveAsTable(output_fqn)\n'
                  'spark.sql(post_insert_sql)\nspark.sql(delete_sql)'}]}
    content = json.dumps(notebook)
    job = {"id": job_id, "kind": "spark_draft", "status": "completed", "package_hash": package_hash,
        "workflow_id": "wf/selected", "scenario": "bronze", "design_id": design_id,
        "design_version": 3, "input_version": input_version, "output": {"run_id": "e" * 32}}
    app = FastAPI()
    app.state.service = SimpleNamespace(root=tmp_path, state=tmp_path, agent_slot=BoundedSemaphore(1),
        require=lambda: SimpleNamespace(model={"package": {"sha256": package_hash}}),
        agent_inputs=lambda index, workflow_id, scenario: {"input_version": input_version}, record=lambda _: job)
    monkeypatch.setattr(artifact_reads, "read_result", lambda *args: {
        "manifest": {"design_id": design_id, "design_version": 3, "package_hash": package_hash,
            "input_version": input_version, "scenario": "bronze"},
        "artifacts": {}, "staged_artifacts": {path: content}})
    exact = {"id": design_id, "version": 3, "workflow_id": "wf/selected", "scenario": "bronze",
        "input_version": input_version,
        "current_input_version": input_version, "stale": False}
    monkeypatch.setattr(redesign, "store_for", lambda service: SimpleNamespace(get=lambda *args: exact))
    install_notebook_runner(app)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50123))
    response = client.post(f"/api/jobs/{job_id}/spark-drafts/run", json={
        "consent": True, "artifact_path": path, "cell_number": 1, "expected_workspace": "https://workspace.example",
        "parameters": {"demo_mode": "true", "uc_catalog": "main", "bronze_source_table": "main.raw.counter",
            "dev_catalog": "main", "dev_schema": "raw", "dev_table": "counter", "target_table": ""},
        "side_effects_confirmed": True, "confirmed_output_table": "main.raw.counter"})
    assert response.status_code == 422
    assert not list(tmp_path.glob("notebook-runs/*.json"))
