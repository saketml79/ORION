"""Original XML integration plus portable generated notebook execution, offline only."""
from copy import deepcopy
from dataclasses import fields
from functools import lru_cache
import ast
import json
from pathlib import Path, PurePosixPath
import unittest

from databricks.sdk.service import compute, jobs
import pytest
import yaml

from workbench.demo_conversion import ORIGINAL_SHA256, REVIEWED_SCOPE_SHA256, WORKFLOW_NAME, _digest, _without_annotations, scoped_model
from workbench.understanding import analyze_package
from workbench.workflow_artifacts import _bundle, compile_workflow_artifacts, supported_workflows


ROOT = Path(__file__).resolve().parents[2]

# Independent expectations from the actual XML, never derived from compiler metadata.
MEMBERS = [
    (WORKFLOW_NAME, "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38",
     REVIEWED_SCOPE_SHA256, 18300, "wkf_Load_AE2E_WILTEL_NETWORK_DISCO",
     "[$$INFA_ENV]  WIL_NET_DISCO failed since dependency not satisfied",
     "Wiltel Network Disco has failed since job dependency not satisfied."),
    ("wkf_AE2E_DUMMY_CALL_WIL_NET_INS", "3507aed6f85c7c2dbc5dca041f6662f506e8ef0a623fa9642febf55c0ad3b203",
     "5a3520a403d525818c309625bf778222350b9bcc1b9061e8c7bee4bfb24ceb4a", 18299,
     "wkf_Load_AE2E_WILTEL_NETWORK_INSTALL",
     "[$$INFA_ENV]  WIL_NET_INSTALL failed since dependency not satisfied",
     "Wiltel Network Install has failed since Job dependency has not satisfied."),
    ("wkf_AE2E_DUMMY_CALL_WIL_CUS_INST", "87856a03806ee5c53746761a542b409f588d1768223106ca32486daf1bfa6fa8",
     "c58fb55e7fd0cb029ae42f5e3514689f9c583b1fe617c8d16040ed0b6795d762", 18297,
     "wkf_Load_AE2E_WILTEL_CUSTOMER_INSTALL",
     "[$$INFA_ENV]  WIL_CUS_INST failed since dependency not satisfied",
     "Wiltel Customer Install has failed since Job dependency has not satisfied."),
    ("wkf_AE2E_DUMMY_CALL_WIL_CUS_DIS", "4d30896b15af879a2011782a66e1446f8de8fbd4bc1c681d14c15c5eb58d10a0",
     "37bbc5e4c2f74758ccc82a596cf57f85ca1a42f1883bc9c00b2b9599b81c4777", 18298,
     "wkf_Load_AE2E_WILTEL_CUSTOMER_DISCO",
     "[$$INFA_ENV]  WIL_CUS_DISCO failed since dependency not satisfied",
     "Wiltel Customer Disco has failed since Job dependency has not satisfied."),
]


@lru_cache(maxsize=1)
def _parsed_scopes():
    model = analyze_package(ROOT / "input" / "LumenExample.XML")
    assert model["package"]["sha256"] == ORIGINAL_SHA256
    names = {member[0] for member in MEMBERS} | {"wkf_AE2E_DUMMY_CALL_WIL_FGD_INS"}
    return {n["name"]: (scoped_model(model, n["id"]), n["id"])
            for n in model["nodes"] if n["kind"] == "workflow" and n["name"] in names}


@pytest.fixture(scope="module")
def canonical():
    # Includes the workflow and all referenced mapping/session/config definitions.
    return _parsed_scopes()[WORKFLOW_NAME]


@pytest.fixture(scope="module")
def result(canonical):
    return compile_workflow_artifacts(*canonical)


def test_four_wil_workflows_have_readable_evidence_grounded_explanations():
    from workbench.explanations import explain_workflow, model_indexes
    scopes = _parsed_scopes()
    for name, _, _, _, child, subject, _ in MEMBERS:
        model, workflow_id = scopes[name]
        nodes, outgoing = model_indexes(model)
        report = explain_workflow(nodes, outgoing, workflow_id)
        overview = ' '.join(report['sections'][0]['items'])
        code = ' '.join(report['sections'][1]['items'])
        assert report['sections'][0]['title'] == 'What this workflow does'
        assert report['coverage']['task_count'] == 5
        assert report['coverage']['control_link_count'] == 4
        assert child in overview and 'without waiting for it to finish' in overview
        assert 'wait of 5 minutes' in overview and 'not a workflow schedule' in overview
        assert 'fewer than 35 successful target rows' in overview
        assert 'at least 35 successful target rows' in overview
        assert subject in overview
        assert 'session-specific setting' in code and 'mapping definition' in code
        assert 'inserts rows, then deletes rows, then commits changes' in code
        assert report['semantic_parity'] == 'unverified'


def test_pure_deterministic_exact_api_and_artifacts(canonical, result):
    model, workflow_id = canonical
    original = deepcopy(model)
    assert compile_workflow_artifacts(model, workflow_id) == result
    assert model == original
    assert set(result) == {"artifacts", "tasks", "parameters", "parameter_inventory", "deployment_requirements", "validation", "ready_for_execution"}
    assert result["ready_for_execution"] is False
    assert set(result["artifacts"]) == {
        "databricks.yml", "README.md", "parameter_inventory.json", "deployment_requirements.json", "validation.json",
        "tests/test_workflow_contract.py",
        *(f"notebooks/{task}.{suffix}" for task in ("wait", "counter", "launch_child", "email") for suffix in ("py", "ipynb")),
    }
    assert result["validation"]["reviewed_scope_match"] is True
    assert result["validation"]["scope_sha256"] == REVIEWED_SCOPE_SHA256
    assert len(model["nodes"]) == 82
    assert len(model["edges"]) == 124
    assert result["validation"]["package_matches_reviewed"] is True
    assert result["validation"]["remote_execution"] == "not_run"


def test_native_bundle_schema_and_required_variables(result):
    _assert_native_bundle(result, "wil_net_dis")


def _assert_native_bundle(result, member_key):
    bundle = yaml.safe_load(result["artifacts"]["databricks.yml"])
    assert bundle["sync"]["exclude"] == ["**/*.ipynb"]
    assert bundle["targets"] == {"dev": {"default": True, "mode": "development"}}
    job = bundle["resources"]["jobs"][member_key]
    assert job["tasks"] == result["tasks"]
    assert job["parameters"] == result["parameters"]
    assert job["max_concurrent_runs"] == 1
    assert not {"schedule", "trigger", "continuous", "job_clusters"} & job.keys()
    assert "environment_key" in {f.name for f in fields(jobs.Task)}
    assert "environment_version" in {f.name for f in fields(compute.Environment)}
    settings = jobs.JobSettings.from_dict(job)
    assert settings.as_dict() == job  # SDK rejects dropped/unknown native fields via exact roundtrip.
    for task in job["tasks"]:
        assert jobs.Task.from_dict(task).as_dict() == task
        assert task["max_retries"] == 0
        if "notebook_task" in task:
            assert task["environment_key"] == "workflow_python"
            assert task["notebook_task"]["notebook_path"].endswith(".py")
        else:
            assert task["condition_task"] == {"left": "{{tasks.counter.values.target_success_rows}}", "op": "LESS_THAN", "right": "35"}
    tasks = {t["task_key"]: t for t in job["tasks"]}
    assert tasks["counter"]["depends_on"] == [{"task_key": "wait"}]
    assert tasks["threshold"]["depends_on"] == [{"task_key": "counter"}]
    assert tasks["launch_child"]["depends_on"] == [{"task_key": "threshold", "outcome": "true"}]
    assert tasks["email"]["depends_on"] == [{"task_key": "threshold", "outcome": "false"}]
    for name, declaration in bundle["variables"].items():
        if name not in {"sdk_config_profile", "sdk_config_file"}:
            assert "default" not in declaration
    assert all(p["default"] == "${var." + p["name"] + "}" for p in result["parameters"])


def test_complete_scoped_evidence_inventory(canonical, result):
    model, _ = canonical
    inventory = result["parameter_inventory"]
    declarations = [i for i in inventory if i["kind"] in {"mappingvariable", "workflowvariable"}]
    expected = [n for n in model["nodes"] if n["kind"] in {"mappingvariable", "workflowvariable"}]
    assert len(declarations) == len(expected)
    assert {i["owner_id"] for i in declarations} == {n["id"] for n in expected}
    for item in declarations:
        node = next(n for n in expected if n["id"] == item["owner_id"])
        assert item["attributes"] == node["attributes"]
        assert item["parent_id"] == node["parent_id"]
        assert item["evidence"] == node["evidence"]
        assert item["scope"][-1]["id"] == node["id"]
    references = {i["name"] for i in inventory if i["kind"] == "reference"}
    assert {"$$SUPPORT_LIST", "$$INFA_ENV", "$$INFA_DOMAIN", "$$AE2E_SCHEMA", "$PMSourceFileDir",
            "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows"} <= references
    assert any(i["kind"] == "session_property" and i["name"] == "Commit Interval" and i["attributes"]["VALUE"] == "10000" for i in inventory)
    assert json.loads(result["artifacts"]["parameter_inventory.json"]) == inventory


def test_parent_id_inventory_closure_and_redactions(canonical):
    # Inventory must not rely solely on contains edges, nor expose sensitive defaults.
    model, workflow_id = deepcopy(canonical)
    mapping = next(n for n in model["nodes"] if n["kind"] == "mapping")
    model["nodes"].append({"id": "parent-only-variable", "kind": "mappingvariable", "name": "$$API_SECRET",
                           "parent_id": mapping["id"], "attributes": {"NAME": "$$API_SECRET", "DEFAULTVALUE": "do-not-export"},
                           "properties": [], "fields": [], "evidence": {"locator": "/MAPPING[1]/MAPPINGVARIABLE[1]"}})
    result = compile_workflow_artifacts(model, workflow_id)
    item = next(i for i in result["parameter_inventory"] if i["owner_id"] == "parent-only-variable" and i["kind"] == "mappingvariable")
    assert item["attributes"]["DEFAULTVALUE"] == "[REDACTED]"
    assert item["classification"] == "unused"
    assert item["referenced_outside_declaration_name"] is False
    assert "do-not-export" not in json.dumps(result)


@pytest.mark.parametrize("drift", ["sql", "field", "control", "disabled", "property", "edge", "name", "hash"])
def test_fail_closed_semantic_guard(canonical, drift):
    model, workflow_id = deepcopy(canonical)
    if drift == "sql":
        owner = next(n for n in model["nodes"] if any(p["attributes"].get("NAME") == "Sql Query" for p in n["properties"]))
        next(p for p in owner["properties"] if p["attributes"]["NAME"] == "Sql Query")["attributes"]["VALUE"] += " WHERE TEMPLATE_ID=18300"
    elif drift == "field":
        next(n for n in model["nodes"] if n["kind"] == "target")["fields"][0]["attributes"]["SCALE"] = "2"
    elif drift == "control":
        next(e for e in model["edges"] if e["kind"] == "workflow_control")["attributes"]["CONDITION"] = "TRUE"
    elif drift == "disabled":
        next(n for n in model["nodes"] if n["kind"] == "taskinstance")["attributes"]["ISENABLED"] = "NO"
    elif drift == "property":
        model["nodes"][0]["properties"].append({"kind": "attribute", "attributes": {"NAME": "unsafe", "VALUE": "x"}})
    elif drift == "edge":
        model["edges"] = [e for e in model["edges"] if e["kind"] != "data_flow"]
    elif drift == "name":
        next(n for n in model["nodes"] if n["id"] == workflow_id)["name"] = "other"
    else:
        model["package"]["sha256"] = ""
    with pytest.raises(ValueError):
        compile_workflow_artifacts(model, workflow_id)


def test_original_scenario_is_explicitly_unsupported(canonical):
    with pytest.raises(ValueError, match="Original scenario not supported"):
        compile_workflow_artifacts(*canonical, scenario="original")


def test_no_fixture_substitutes_and_explicit_gaps(result):
    production = "\n".join(v for k, v in result["artifacts"].items() if k.startswith("notebooks/") and k.endswith(".py"))
    assert not any(term in production for term in ("range(", "fixture", "mock", "CREATE TABLE", "TRUNCATE", ".result(", "pmcmd"))
    assert "time.sleep(300)" in result["artifacts"]["notebooks/wait.py"]
    assert "time.sleep" not in result["artifacts"]["notebooks/counter.py"]
    requirements = {r["id"]: r for r in result["deployment_requirements"]}
    assert requirements["transaction_gap"]["status"] == "not_equivalent"
    assert "10000" in requirements["transaction_gap"]["detail"]
    assert requirements["bindings"]["status"] == "unbound"
    assert "No original source connection" in requirements["bronze"]["detail"]


class _ArtifactPath:
    """Only the portable suite's read-only path surface, no artifact exports."""

    def __init__(self, artifacts, path=""):
        self.artifacts, self.path = artifacts, PurePosixPath(path)

    def __truediv__(self, part):
        return _ArtifactPath(self.artifacts, str(self.path / part))

    def read_text(self):
        return self.artifacts[str(self.path)]

    def glob(self, pattern):
        return [_ArtifactPath(self.artifacts, key) for key in self.artifacts
                if PurePosixPath(key).parent == self.path and PurePosixPath(key).match(pattern)]

    def with_suffix(self, suffix):
        return _ArtifactPath(self.artifacts, str(self.path.with_suffix(suffix)))


def _portable_suite(result):
    namespace = {"__file__": str(ROOT / "tests/test_workflow_contract.py"), "__name__": "generated_contract"}
    exec(compile(result["artifacts"]["tests/test_workflow_contract.py"], "generated_contract", "exec"), namespace)
    namespace["ROOT"] = _ArtifactPath(result["artifacts"])
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(namespace["ContractTests"])
    outcome = unittest.TestResult()
    suite.run(outcome)
    assert outcome.testsRun == 5
    assert outcome.wasSuccessful(), outcome.errors + outcome.failures


def test_generated_portable_semantic_suite(result):
    _portable_suite(result)


@pytest.mark.parametrize("member", MEMBERS, ids=[m[0] for m in MEMBERS])
def test_verified_xml_member_artifacts_and_portable_suite(member):
    name, workflow_id, scope_hash, template_id, child, subject, body = member
    model, actual_id = _parsed_scopes()[name]
    original = deepcopy(model)
    assert actual_id == workflow_id
    assert (len(model["nodes"]), len(model["edges"])) == (82, 124)
    assert _digest(_without_annotations({k: model[k] for k in ("nodes", "edges")})) == scope_hash
    session = next(n for n in model["nodes"] if n["kind"] == "session")
    assert session["name"] == "s_m_AE2E_JOB_CHECK_COUNTER"
    assert session["attributes"]["MAPPINGNAME"] == "m_LOAD_AE2E_JOB_DEP_CHK_COUNTER"
    session_props = {p["attributes"]["NAME"]: p["attributes"].get("VALUE") for p in session["properties"]}
    assert session_props["Treat source rows as"] == "Insert"
    assert session_props["Commit Interval"] == "10000"
    source = next(n for n in model["nodes"] if n["kind"] == "sesstransformationinst"
                  and n["name"] == "SQ_AE2E_JOB_DEP_CHK_COUNT" and n["parent_id"] == session["id"]
                  and n["attributes"]["TRANSFORMATIONTYPE"] == "Source Qualifier")
    sql = next(p["attributes"]["VALUE"] for p in source["properties"] if p["attributes"]["NAME"] == "Sql Query")
    schema = "AE2E" if template_id == 18300 else "$$AE2E_SCHEMA"
    assert " ".join(sql.split()) == f"SELECT {template_id} AS template_id, 1 as COUNTER FROM {schema}.AE2E_JOB_DEP_CHK_COUNT"
    target = next(n for n in model["nodes"] if n["kind"] == "sesstransformationinst"
                  and n["name"] == "AE2E_JOB_DEP_CHK_COUNT" and n["parent_id"] == session["id"]
                  and n["attributes"]["TRANSFORMATIONTYPE"] == "Target Definition")
    post = next(p["attributes"]["VALUE"] for p in target["properties"] if p["attributes"]["NAME"] == "Post SQL")
    assert " ".join(post.split()) == (
        f"INSERT INTO $$AE2E_SCHEMA.AE2E_JOB_DEP_CHK_COUNT VALUES({template_id},1); "
        f"delete from $$AE2E_SCHEMA.AE2E_JOB_DEP_CHK_COUNT PARTITION(AE2E_JOB_DEP_CHK_COUNT_{template_id}) "
        f"where 60 <=(select count(1) from $$AE2E_SCHEMA.AE2E_JOB_DEP_CHK_COUNT PARTITION(AE2E_JOB_DEP_CHK_COUNT_{template_id})); commit;")
    command = next(n for n in model["nodes"] if n["kind"] == "task" and n["attributes"].get("TYPE") == "Command")
    assert any(p["attributes"].get("VALUE", "").endswith("-nowait " + child) for p in command["properties"])
    email = next(n for n in model["nodes"] if n["kind"] == "task" and n["attributes"].get("TYPE") == "Email")
    assert {p["attributes"]["NAME"]: p["attributes"]["VALUE"] for p in email["properties"]} == {
        "Email User Name": "$$SUPPORT_LIST", "Email Subject": subject, "Email Text": body}
    conditions = {"".join(e["attributes"]["CONDITION"].split()) for e in model["edges"] if e["kind"] == "workflow_control"}
    assert conditions == {"", "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows<35", "$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows>=35"}

    result = compile_workflow_artifacts(model, workflow_id)
    assert model == original
    assert compile_workflow_artifacts(model, workflow_id) == result
    assert set(result) == {"artifacts", "tasks", "parameters", "parameter_inventory", "deployment_requirements", "validation", "ready_for_execution"}
    assert result["validation"]["scope_sha256"] == scope_hash
    assert result["validation"]["reviewed_scope_match"] is True
    assert result["ready_for_execution"] is False
    bundle = yaml.safe_load(result["artifacts"]["databricks.yml"])
    member_key = name.removeprefix("wkf_AE2E_DUMMY_CALL_").lower()
    assert bundle["bundle"]["name"] == member_key.replace("_", "-") + "-bronze"
    assert set(bundle["resources"]["jobs"]) == {member_key}
    job = bundle["resources"]["jobs"][member_key]
    assert job["name"] == name + " bronze"
    assert jobs.JobSettings.from_dict(job).as_dict() == job
    assert job["tasks"] == result["tasks"]
    assert job["parameters"] == result["parameters"]
    _assert_native_bundle(result, member_key)
    assert child in bundle["variables"]["child_job_id"]["description"]
    assert f"TEMPLATE_ID_{template_id}" in bundle["variables"]["partition_semantics_ack"]["description"]
    readme = result["artifacts"]["README.md"]
    assert all(value in readme for value in (name, child, str(template_id), subject, body))
    for other in MEMBERS:
        if other != member:
            assert other[0] not in readme
            assert other[4] not in readme
            assert str(other[3]) not in readme
    requirements = {r["id"]: r for r in result["deployment_requirements"]}
    assert f"TEMPLATE_ID_{template_id}" in requirements["partition"]["detail"]
    assert child in requirements["child"]["detail"]
    for task in ("wait", "counter", "launch_child", "email"):
        notebook = json.loads(result["artifacts"][f"notebooks/{task}.ipynb"])
        assert name in "".join(notebook["cells"][0]["source"])
        code = "".join(notebook["cells"][1]["source"])
        parsed = ast.parse(code)
        constants = {n.targets[0].id: ast.literal_eval(n.value) for n in parsed.body
                     if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                     and n.targets[0].id in {"TEMPLATE_ID", "WORKFLOW_NAME", "CHILD_WORKFLOW_NAME"}}
        assert constants == {"TEMPLATE_ID": template_id, "WORKFLOW_NAME": name, "CHILD_WORKFLOW_NAME": child}
        ast.parse(result["artifacts"][f"notebooks/{task}.py"])
    _portable_suite(result)


@pytest.mark.parametrize("member", MEMBERS, ids=[m[0] for m in MEMBERS])
@pytest.mark.parametrize("drift", ["sql", "post_sql", "email", "child", "declaration", "control", "field", "identity"])
def test_all_member_full_scope_pins_reject_drift(member, drift):
    model, workflow_id = deepcopy(_parsed_scopes()[member[0]])
    if drift in {"sql", "post_sql"}:
        property_name = "Sql Query" if drift == "sql" else "Post SQL"
        owner = next(n for n in model["nodes"] if n["kind"] == "sesstransformationinst"
                     and any(p["attributes"].get("NAME") == property_name for p in n["properties"]))
        next(p for p in owner["properties"] if p["attributes"]["NAME"] == property_name)["attributes"]["VALUE"] += " changed"
    elif drift in {"email", "child"}:
        kind = "Email" if drift == "email" else "Command"
        owner = next(n for n in model["nodes"] if n["kind"] == "task" and n["attributes"].get("TYPE") == kind)
        owner["properties"][-1]["attributes"]["VALUE"] += " changed"
    elif drift == "declaration":
        next(n for n in model["nodes"] if n["kind"] == "workflowvariable")["attributes"]["DEFAULTVALUE"] = "changed"
    elif drift == "control":
        next(e for e in model["edges"] if e["kind"] == "workflow_control")["attributes"]["CONDITION"] = "TRUE"
    elif drift == "field":
        next(n for n in model["nodes"] if n["kind"] == "target")["fields"][0]["attributes"]["SCALE"] = "2"
    else:
        workflow = next(n for n in model["nodes"] if n["id"] == workflow_id)
        workflow["name"] = MEMBERS[(MEMBERS.index(member) + 1) % len(MEMBERS)][0]
    with pytest.raises(ValueError, match="Unsupported"):
        compile_workflow_artifacts(model, workflow_id)


def test_only_four_explicit_members_and_baseline_bundle_defaults(result):
    metadata = supported_workflows()
    assert metadata == [{"workflow_name": n, "workflow_id": i, "scope_sha256": h, "template_id": t, "child_name": c}
                        for n, i, h, t, c, _, _ in MEMBERS]
    metadata[0]["template_id"] = 0
    assert supported_workflows()[0]["template_id"] == 18300
    bundle, tasks, parameters = _bundle()
    assert bundle == yaml.safe_load(result["artifacts"]["databricks.yml"])
    assert tasks == result["tasks"] and parameters == result["parameters"]
    # A real, structurally similar unreviewed wrapper is not inferred as supported.
    with pytest.raises(ValueError, match="Unsupported workflow"):
        compile_workflow_artifacts(*_parsed_scopes()["wkf_AE2E_DUMMY_CALL_WIL_FGD_INS"])


def test_inventory_execution_bindings_and_declaration_classification(result):
    inventory = result["parameter_inventory"]
    declarations = [i for i in inventory if i["kind"] in {"mappingvariable", "workflowvariable"}]
    assert {i["classification"] for i in declarations} <= {"unused", "system", "operational"}
    assert all(i["classification"] == "system" for i in declarations if i["attributes"].get("USERDEFINED") == "NO")
    by_name = {i["name"]: i for i in declarations}
    for name in ("$$SUPPORT_LIST", "$$INFA_ENV", "$$INFA_DOMAIN", "$$AE2E_SCHEMA"):
        assert by_name[name]["classification"] == "operational"
        assert by_name[name]["referenced_outside_declaration_name"] is True
    assert by_name["$$SUPPORT_LIST"]["execution_binding"] == {"kind": "runtime_parameter", "parameters": ["support_list"]}
    assert by_name["$$INFA_ENV"]["execution_binding"] == {"kind": "runtime_parameter", "parameters": ["infa_env"]}
    assert by_name["$$AE2E_SCHEMA"]["execution_binding"]["parameters"] == ["bronze_source_table", "target_table"]
    assert by_name["$$INFA_DOMAIN"]["execution_binding"]["kind"] == "historical_context"
    metric = by_name["$s_m_AE2E_JOB_CHECK_COUNTER.TgtSuccessRows"]
    assert metric["classification"] == "system"
    assert metric["execution_binding"] == {"kind": "runtime_task_value", "task_key": "counter", "key": "target_success_rows"}
    assert all(i["execution_binding"]["kind"] == "historical_context" for i in inventory
               if i["kind"] == "reference" and i["name"].startswith("$PM"))