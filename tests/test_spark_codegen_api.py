"""Authored offline API regressions. No provider or generated-code execution.

Worker tests substitute a file-writing stub, never a chat model. This module
does not run tests, models, deployments, or inference during import.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from workbench.app import _create_package_app
from workbench.redesign import Review, store_for
from workbench import spark_codegen as engine
from workbench.tests.test_delta_runtime import authority  # noqa: F401
from workbench.tests.test_redesign_ui import browser, ui  # noqa: F401
from playwright.sync_api import expect


HEADERS = {"X-Workbench-Request": "1"}


class HeldExecutor:
    """Deterministic queue: nothing runs until a test explicitly drains it."""
    def __init__(self, **kwargs):
        self.pending = []
        self.reject = False

    def submit(self, fn, *args, **kwargs):
        if self.reject:
            raise RuntimeError("Private executor failure")
        self.pending.append((fn, args, kwargs))

    def drain(self):
        fn, args, kwargs = self.pending.pop(0)
        fn(*args, **kwargs)

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def api(tmp_path):
    app = _create_package_app(tmp_path, executor_factory=HeldExecutor,
                              model_installer=lambda app: None)
    service = app.state.service
    model = {"package": {"sha256": "a" * 64}, "nodes": [
        {"id": "wf", "kind": "workflow", "parent_id": None, "attributes": {}},
        {"id": "task", "kind": "taskinstance", "parent_id": "wf", "attributes": {"ISENABLED": "YES"}},
    ], "edges": [], "diagnostics": []}
    index = SimpleNamespace(model=model, nodes={node["id"]: node for node in model["nodes"]})
    inputs = {"input_version": "b" * 64, "input_hashes": {"policy": "c" * 64},
              "input_versions": {"documents": "d" * 64, "reviews": "e" * 64}}
    service.agent_inputs = lambda *args: deepcopy(inputs)
    design = {"title": "Scoped shared load", "tasks": [
        {"id": "load", "label": "Load", "implementation_kind": "notebook_task",
         "purpose": "Retain captured behavior", "evidence_ids": ["task"]}],
        "edges": [], "rationale": "Separate drafting from outline review",
        "alternatives": [{"label": "Separate tasks", "rationale": "Keep the original boundary",
                          "tradeoffs": ["Repeated configuration"], "evidence_ids": ["task"]}],
        "coverage": [{"original_task_id": "task", "proposed_task_ids": ["load"], "disposition": "mapped",
                      "rationale": "Retain the enabled task", "evidence_ids": ["task"]}],
        "evidence_ids": ["task"], "reuse_groups": [], "parameters": [],
        "proposed_constraints": [], "blockers": []}
    store = store_for(service)
    record = store.append(model["package"]["sha256"], "wf", "original",
        {"design": design, "role": "architecture", "retrieved_evidence_ids": ["task"]},
        {"record_id": "source", "runtime_run_id": "1" * 32, "submission_index": 0,
         "kind": "agent", **deepcopy(inputs)}, 0)
    store.review(record["id"], record["package_hash"], Review(
        action="approve_for_drafting", comment="Outline accepted, separate drafting consent required",
        expected_version=1, expected_input_version=inputs["input_version"], consent=True),
        inputs["input_version"], {"actor": "reviewer"})
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        service.active = index
        yield SimpleNamespace(client=client, service=service, store=store, record=record,
                              inputs=inputs, index=index, root=tmp_path)


def queue(api, **body):
    return api.client.post(f'/api/redesigns/{api.record["id"]}/spark-drafts',
                           headers=HEADERS, json={"version": 1, "consent": True, **body})


def review(api, action):
    current = api.store.get(api.record["id"], api.record["package_hash"])
    return api.store.review(current["id"], current["package_hash"], Review(
        action=action, comment="Explicit review decision", expected_version=current["version"],
        expected_review_version=current["review_version"], expected_input_version=api.inputs["input_version"],
        consent=True), api.inputs["input_version"], {"actor": "reviewer"})


def fake_draft(service, expected, *, consent):
    """Exercise the real repository gate, but never instantiate/invoke a model."""
    assert consent is True
    engine._approved(service, expected)
    run = "9" * 32
    directory = (service.root / "generated-drafts" / expected["package_hash"]
                 / expected["id"] / str(expected["version"]) / run)
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / "checkpoint_spark_codegen.sqlite") as db:
        db.execute("CREATE TABLE checkpoints (id TEXT PRIMARY KEY, checkpoint BLOB)")
        db.execute("INSERT INTO checkpoints VALUES (?,?)", ("checkpoint", b"opaque-checkpoint-bytes"))
    content = "SELECT 1 AS draft_value\n"
    (directory / "sql").mkdir()
    (directory / "sql" / "draft.sql").write_text(content, encoding="utf-8", newline="\n")
    report = {"schema_version": 1, "run_id": run, "design_id": expected["id"],
              "design_version": expected["version"], "input_version": expected["input_version"],
              "package_hash": expected["package_hash"], "status": "blocked",
              "artifact_hashes": {"sql/draft.sql": sha256(content.encode()).hexdigest()},
              "artifact_approved": False, "execution_allowed": False, "deployment_allowed": False}
    (directory / "manifest.json").write_text(json.dumps(report), encoding="utf-8")
    return {**report, "relative_directory": directory.relative_to(service.root).as_posix()}


@pytest.mark.parametrize("body,status", [
    ({"consent": False}, 403), ({"consent": "true"}, 422), ({"version": True}, 422),
    ({"version": "1"}, 422), ({"version": 0}, 422), ({"approved": True}, 422),
])
def test_strict_body_and_explicit_consent_before_queue(api, body, status):
    assert queue(api, **body).status_code == status
    assert not api.service.agent_executor.pending
    assert not list((api.service.state / "jobs").glob("*.json"))


@pytest.mark.parametrize("change", ["reject", "request_changes", "new_version", "input", "hashes", "coverage", "disabled"])
def test_engine_authority_rejects_before_persisting_or_queueing(api, change):
    if change in {"reject", "request_changes"}:
        review(api, change)
    elif change == "new_version":
        api.store.append(api.record["package_hash"], "wf", "original",
            {"design": api.record["design"], "role": "architecture", "retrieved_evidence_ids": ["task"]},
            {**api.record["source"], "record_id": "next-source"}, 1)
    elif change == "input":
        api.inputs["input_version"] = "f" * 64
    elif change == "hashes":
        api.inputs["input_hashes"]["policy"] = "f" * 64
    elif change == "coverage":
        api.index.model["nodes"].append({"id": "extra", "kind": "taskinstance", "parent_id": "wf"})
    else:
        api.index.model["nodes"][1]["attributes"]["ISENABLED"] = "NO"
    assert queue(api).status_code == 409
    assert not api.service.agent_executor.pending
    assert not list((api.service.state / "jobs").glob("*.json"))


def test_unknown_version_and_other_package_are_not_queueable(api):
    assert queue(api, version=2).status_code == 404
    api.index.model["package"]["sha256"] = "f" * 64
    assert queue(api).status_code == 404
    assert not api.service.agent_executor.pending


def test_latest_without_approval_or_with_only_comment_is_not_queueable(api):
    api.store.append(api.record["package_hash"], "wf", "original",
        {"design": api.record["design"], "role": "architecture", "retrieved_evidence_ids": ["task"]},
        {**api.record["source"], "record_id": "next-source"}, 1)
    assert queue(api, version=2).status_code == 409
    review(api, "comment")
    assert queue(api, version=2).status_code == 409
    assert not api.service.agent_executor.pending


def test_missing_consent_cannot_be_inferred_from_approval(api):
    response = api.client.post(f'/api/redesigns/{api.record["id"]}/spark-drafts',
                               headers=HEADERS, json={"version": 1})
    assert response.status_code == 422
    assert not api.service.agent_executor.pending


def test_queue_is_explicit_persisted_and_shares_agent_slot(api, monkeypatch):
    monkeypatch.setattr(engine, "draft_approved_design", fake_draft)
    response = queue(api)
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "spark_draft" and job["status"] == "queued"
    assert not (api.root / "generated-drafts").exists()
    assert api.service.record(job["id"]) == job
    assert queue(api).status_code == 409
    assert len(api.service.agent_executor.pending) == 1
    assert not api.service.agent_slot.acquire(blocking=False)
    api.service.agent_executor.drain()
    assert api.service.agent_slot.acquire(blocking=False)
    api.service.agent_slot.release()
    saved = api.client.get(f'/api/jobs/{job["id"]}').json()
    assert saved["status"] == "completed"
    assert saved["output"]["status"] == "blocked"  # Completed worker is not successful generation.
    assert saved["output"]["execution_allowed"] is False
    result = api.client.get(f'/api/jobs/{job["id"]}/spark-drafts')
    assert result.status_code == 200
    assert result.json()["artifacts"] == {"sql/draft.sql": "SELECT 1 AS draft_value\n"}


def test_native_draft_and_bundle_http_return_exact_jobs_graph_metadata(api, monkeypatch):
    """Real approval, persisted checksum reader and builder; no model/Jobs call."""
    def notebook_draft(service, expected, *, consent):
        report = fake_draft(service, expected, consent=consent)
        directory = service.root / report['relative_directory']
        text = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
            {'cell_type': 'code', 'metadata': {'language': 'python', 'id': 'preserve-id'},
             'source': ['print("exact notebook")\n'], 'outputs': [], 'execution_count': None}]})
        path = 'notebooks/selected/load.ipynb'
        target = directory / path
        target.parent.mkdir(parents=True)
        target.write_bytes(text.encode())
        report['artifact_hashes'][path] = sha256(text.encode()).hexdigest()
        report.update(design_sha256=engine._digest(expected['design']),
                      input_hashes=deepcopy(api.inputs['input_hashes']),
                      input_versions=deepcopy(api.inputs['input_versions']),
                      tasks=[{'task_id': 'load', 'status': 'drafted', 'artifact_paths': [path]}])
        (directory / 'manifest.json').write_text(json.dumps(report), encoding='utf-8')
        return report
    monkeypatch.setattr(engine, 'draft_approved_design', notebook_draft)
    job = queue(api).json()
    api.service.agent_executor.drain()
    base = '/api/jobs/' + job['id']
    body = {'workspace_root': '/Workspace/Users/test', 'consent': True}
    assert api.client.post(base + '/native-job-draft', headers=HEADERS,
                           json={**body, 'consent': False}).status_code == 403
    draft = api.client.post(base + '/native-job-draft', headers=HEADERS, json=body)
    assert draft.status_code == 200, draft.text
    draft = draft.json()
    graph = draft['native_jobs'][0]
    assert graph['tasks'] == json.loads(draft['yaml'])['resources']['jobs']['migration_draft']['tasks']
    assert graph['artifact_mapping'] == {'load': {'notebook_path': 'notebooks/selected/load.ipynb'}}
    assert graph['tasks'][0]['notebook_task']['notebook_path'] == '/Workspace/Users/test/notebooks/selected/load'
    published = api.client.post(base + '/native-bundles', headers=HEADERS, json=body)
    assert published.status_code == 200, published.text
    reopened = api.client.get(base + '/native-bundles/' + published.json()['bundle_id'])
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['native_jobs'] == draft['native_jobs']
    assert reopened.json()['artifacts']['migration-job.yaml'] == draft['yaml']
    assert reopened.json()['execution_allowed'] is reopened.json()['deployment_allowed'] is False


def test_worker_rechecks_approval_and_never_exposes_provider_error(api, monkeypatch):
    monkeypatch.setattr(engine, "draft_approved_design", fake_draft)
    job = queue(api).json()
    review(api, "reject")
    api.service.agent_executor.drain()
    saved = api.service.record(job["id"])
    assert saved["status"] == "failed" and "output" not in saved
    assert not (api.root / "generated-drafts").exists()
    assert api.client.get(f'/api/jobs/{job["id"]}/spark-drafts').status_code == 409


def test_executor_rejection_persists_failed_record_and_releases_slot(api):
    api.service.agent_executor.reject = True
    response = queue(api)
    assert response.status_code == 503 and "Private" not in response.text
    saved = json.loads(next((api.service.state / "jobs").glob("*.json")).read_text())
    assert saved["status"] == "failed" and saved["finished_at"]
    assert api.service.agent_slot.acquire(blocking=False)
    api.service.agent_slot.release()


def complete(api, monkeypatch):
    monkeypatch.setattr(engine, "draft_approved_design", fake_draft)
    queued = queue(api).json()
    api.service.agent_executor.drain()
    job = api.service.record(queued["id"])
    return job, api.root / job["output"]["relative_directory"]


@pytest.mark.parametrize('add_coverage', [False, True])
def test_manifest_reads_preserve_historical_and_additive_production_metadata(api, monkeypatch, add_coverage):
    expected = {}

    def draft(service, record, *, consent):
        report = fake_draft(service, record, consent=consent)
        directory = service.root / report['relative_directory']
        if add_coverage:
            report.update(translation_coverage=None, input_data_basis='synthetic', behavior_basis='unknown',
                translation_assessment={'status': 'unverified', 'missing_items': [
                    {'task_id': 'load', 'source_record_keys': ['task:header']}],
                    'blocked_items': [], 'semantic_parity': 'unverified',
                    'execution_allowed': False, 'production_ready': False},
                production_contract_sha256='e' * 64)
            (directory / 'manifest.json').write_text(
                json.dumps({k: v for k, v in report.items() if k != 'relative_directory'}), encoding='utf-8')
        expected.update(json.loads((directory / 'manifest.json').read_text(encoding='utf-8')))
        return report

    monkeypatch.setattr(engine, 'draft_approved_design', draft)
    job = queue(api).json()
    api.service.agent_executor.drain()
    saved = api.service.record(job['id'])
    assert saved['status'] == 'completed'
    result = api.client.get(f'/api/jobs/{job["id"]}/spark-drafts')
    assert result.status_code == 200
    # Exact equality protects legacy identity, hashes, task status and gate keys,
    # as well as new metadata. Do not mask regressions with a subset comparison.
    assert result.json()['manifest'] == expected
    assert result.json()['artifacts'] == {'sql/draft.sql': 'SELECT 1 AS draft_value\n'}
    for key, value in expected.items():
        assert saved['output'][key] == value
    assert expected['artifact_approved'] is expected['execution_allowed'] is expected['deployment_allowed'] is False


def test_jobs_draft_rejects_manifest_without_exact_approved_design_hash(api, monkeypatch):
    job, _ = complete(api, monkeypatch)
    monkeypatch.setattr('workbench.native_job_draft.build',
                        lambda *args: pytest.fail('Unbound manifest must not reach Jobs builder'))
    response = api.client.post(f'/api/jobs/{job["id"]}/native-job-draft', headers=HEADERS,
                               json={'workspace_root': '/Workspace/Users/test', 'consent': True})
    assert response.status_code == 409
    assert 'current approved design and inputs' in response.json()['detail']


def fake_notebook_draft(service, expected, *, consent):
    engine._approved(service, expected)
    assert consent is True
    run = '9' * 32
    directory = (service.root / 'generated-drafts' / expected['package_hash'] /
                 expected['id'] / str(expected['version']) / run)
    (directory / 'notebooks').mkdir(parents=True)
    notebook = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': ['x = 1'],
         'outputs': [], 'execution_count': None}]})
    (directory / 'notebooks' / 'load.ipynb').write_text(notebook, encoding='utf-8')
    report = {'schema_version': 1, 'run_id': run, 'design_id': expected['id'],
              'design_version': expected['version'], 'design_sha256': engine._digest(expected['design']),
              'input_version': expected['input_version'], 'package_hash': expected['package_hash'],
              'input_hashes': expected['source']['input_hashes'],
              'input_versions': expected['source']['input_versions'],
              'status': 'generation_complete',
              'tasks': [{'task_id': 'load', 'status': 'drafted', 'artifact_paths': ['notebooks/load.ipynb']}],
              'artifact_hashes': {'notebooks/load.ipynb': sha256(notebook.encode()).hexdigest()},
              'artifact_approved': False, 'execution_allowed': False, 'deployment_allowed': False}
    (directory / 'manifest.json').write_text(json.dumps(report), encoding='utf-8')
    return {**report, 'relative_directory': directory.relative_to(service.root).as_posix()}


def test_draft_bundle_requires_consent_and_current_approval_and_detects_corruption(api, monkeypatch):
    monkeypatch.setattr(engine, 'draft_approved_design', fake_notebook_draft)
    assert not api.service.agent_executor.pending
    queued = queue(api).json()
    api.service.agent_executor.drain()
    url = f'/api/jobs/{queued["id"]}/native-bundles'
    body = {'workspace_root': '/Workspace/Users/test/release', 'consent': True}
    assert api.client.post(url, headers=HEADERS, json={**body, 'consent': False}).status_code == 403
    first = api.client.post(url, headers=HEADERS, json=body)
    assert first.status_code == 200, first.text
    acknowledgement = first.json()
    assert 'artifacts' not in acknowledgement
    bundle = api.client.get(url + '/' + acknowledgement['bundle_id']).json()
    assert bundle['semantic_parity'] == 'not_established'
    assert bundle['execution_allowed'] is bundle['deployment_allowed'] is False
    assert set(bundle['artifacts']) == {'notebooks/load.ipynb', 'migration-job.yaml'}
    assert api.client.post(url, headers=HEADERS, json=body).json() == acknowledgement
    assert api.client.get(url).json()['bundles'][0]['bundle_id'] == bundle['bundle_id']
    assert api.client.get(url + '/' + '0' * 64).status_code == 409
    directory = api.root / api.service.record(queued['id'])['output']['relative_directory']
    artifact = directory / 'native-bundles' / bundle['bundle_id'] / 'notebooks' / 'load.ipynb'
    artifact.write_text('corrupt', encoding='utf-8')
    assert api.client.get(url + '/' + bundle['bundle_id']).status_code == 409
    assert api.client.get(url).json()['bundles'][0]['status'] == 'needs_reconciliation'
    assert api.client.post(url, headers=HEADERS, json=body).status_code == 409
    review(api, 'reject')
    assert api.client.post(url, headers=HEADERS, json=body).status_code == 409


def test_native_bundle_survives_verified_delta_snapshot_restart(authority, tmp_path):
    from workbench.draft_bundle import publish, read
    from workbench.tests.test_delta_runtime import adapter

    runtime = adapter(authority, tmp_path / 'first')
    runtime.restore()
    root = runtime.local
    package, design_id, run = 'a' * 64, 'b' * 64, 'c' * 32
    directory = root / 'generated-drafts' / package / design_id / '1' / run
    directory.mkdir(parents=True)
    service = SimpleNamespace(root=root)
    notebook = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': ['x = 1'],
         'outputs': [], 'execution_count': None}]})
    design = {'design': {'tasks': [{'id': 'load', 'implementation_kind': 'notebook_task'}], 'edges': []}}
    manifest = {'run_id': run, 'tasks': [{'task_id': 'load', 'status': 'drafted',
                                           'artifact_paths': ['notebooks/load.ipynb']}]}
    result = {'manifest': manifest, 'artifacts': {'notebooks/load.ipynb': notebook}}
    record = {'id': 'd' * 32, 'package_hash': package, 'workflow_id': 'e' * 64,
              'design_id': design_id, 'design_version': 1, 'input_version': 'f' * 64,
              'manifest_sha256': '1' * 64, 'output': {'run_id': run}}
    bundle = publish(service, record, design, result, '/Workspace/Users/test/release')
    runtime.flush()
    restarted = adapter(authority, tmp_path / 'restart')
    assert restarted.restore() is True
    assert read(SimpleNamespace(root=restarted.local), record, design, result, bundle['bundle_id']) == bundle
    with pytest.raises(engine.ContractError):
        read(SimpleNamespace(root=restarted.local), {**record, 'workflow_id': '0' * 64},
             design, result, bundle['bundle_id'])


def test_draft_jobs_and_artifact_reads_are_package_scoped(api, monkeypatch):
    job, _ = complete(api, monkeypatch)
    api.index.model["package"]["sha256"] = "f" * 64
    for suffix in ("", "/spark-drafts"):
        assert api.client.get(f'/api/jobs/{job["id"]}{suffix}').status_code == 404


@pytest.fixture
def inventory_api(api):
    """Use the real index presenter for the inventory API and viewer workflow."""
    from workbench.app import PackageIndex

    model = deepcopy(api.index.model)
    model['nodes'][0]['id'] = 'wf/one'
    model['nodes'][1]['parent_id'] = 'wf/one'
    for node in model['nodes']:
        node['name'] = node['id']
    api.index = api.service.active = PackageIndex(model, api.root)
    api.record = api.store.append(model['package']['sha256'], 'wf/one', 'original',
        {'design': api.record['design'], 'role': 'architecture', 'retrieved_evidence_ids': ['task']},
        {**api.record['source'], 'record_id': 'inventory-source'}, 0)
    review(api, 'approve_for_drafting')
    return api


@pytest.mark.parametrize('changed', [None, 'kind', 'package_hash', 'workflow_id', 'scenario',
                                     'input_version', 'design_id', 'design_version'])
def test_exact_version_inventory_projects_actual_public_provenance_only(inventory_api, monkeypatch, changed):
    api = inventory_api
    job, _ = complete(api, monkeypatch)
    if changed:
        unrelated = {**job, 'id': '0' * 32, changed: 999 if changed == 'design_version' else 'different'}
        api.service.update_record(unrelated)
    api.store.append(api.record['package_hash'], api.record['workflow_id'], 'original',
        {'design': api.record['design'], 'role': 'architecture', 'retrieved_evidence_ids': ['task']},
        {**api.record['source'], 'record_id': 'next-source'}, 1)
    response = api.client.get(f'/api/redesigns/{api.record["id"]}',
                              params={'version': 1, 'draft_jobs': 'true'})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['version'] == 1 and value['latest_version'] == 2
    fields = ('id', 'status', 'created_at', 'design_id', 'design_version',
              'kind', 'package_hash', 'workflow_id', 'scenario', 'input_version')
    assert value['draft_jobs'] == [{key: job[key] for key in fields}]
    assert not {'output', 'manifest_sha256', 'input_hashes', 'input_versions', 'error'} & value['draft_jobs'][0].keys()
    latest = api.client.get(f'/api/redesigns/{api.record["id"]}',
                            params={'version': 2, 'draft_jobs': 'true'})
    assert latest.status_code == 200 and latest.json()['draft_jobs'] == []
    receipt = api.client.get(f'/api/jobs/{value["draft_jobs"][0]["id"]}/spark-drafts')
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['job_id'] == job['id']
    assert receipt.json()['manifest']['design_version'] == 1
    assert receipt.json()['artifacts'] == {'sql/draft.sql': 'SELECT 1 AS draft_value\n'}


@pytest.mark.parametrize('change_selection', [False, True])
def test_actual_api_inventory_ui_find_reads_only_matching_code_receipt(inventory_api, monkeypatch, ui, change_selection):
    """Production HTTP handlers and unchanged viewer, no enriched inventory mock."""
    from urllib.parse import parse_qs, urlsplit

    api = inventory_api
    job, _ = complete(api, monkeypatch)
    api.store.append(api.record['package_hash'], api.record['workflow_id'], 'original',
        {'design': api.record['design'], 'role': 'architecture', 'retrieved_evidence_ids': ['task']},
        {**api.record['source'], 'record_id': 'next-source'}, 1)
    page, _, _ = ui
    reads, held = [], []

    def forward(route):
        request = route.request
        url = urlsplit(request.url)
        assert request.method == 'GET', 'Saved code discovery must never submit a writer'
        response = api.client.get(url.path + ('?' + url.query if url.query else ''))
        assert response.status_code == 200, response.text
        reads.append((url.path, parse_qs(url.query), response.json()))
        if change_selection and parse_qs(url.query) == {'version': ['1'], 'draft_jobs': ['1']}:
            held.append((route, response))
        else:
            route.fulfill(status=response.status_code, content_type='application/json', body=response.content)

    page.route('**/api/redesigns?*', forward)
    page.route('**/api/redesigns/**', forward)
    page.route('**/api/jobs/**', forward)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_have_text('Scoped shared load')
    versions = {version: json.dumps([api.record['id'], version], separators=(',', ':')) for version in (1, 2)}
    expect(page.locator('#redesign-version')).to_have_value(versions[2])
    expect(page.locator('#spark-draft-find')).to_be_enabled()
    # The production viewer automatically finds receipts once per exact version.
    with page.expect_request('**/api/redesigns/' + api.record['id'] + '?version=1&draft_jobs=1'):
        page.locator('#redesign-version').select_option(versions[1])
    if change_selection:
        expect(page.locator('#spark-draft-find')).to_be_disabled()
        assert len(held) == 1
        page.locator('#redesign-version').select_option(versions[2])
        expect(page.locator('#spark-draft-find')).to_be_enabled()
        route, response = held.pop()
        route.fulfill(status=200, content_type='application/json', body=response.content)
        # A fresh exact-v2 inventory completes after the obsolete v1 response.
        page.get_by_role('tab', name='Notebook code & Job YAML', exact=True).click()
        page.locator('#spark-draft-find').click()
        expect(page.locator('#spark-draft-find')).to_be_enabled()
        expect(page.locator('#spark-draft-output')).to_be_empty()
        expect(page.get_by_role('button', name='Download sql/draft.sql', exact=True)).to_have_count(0)
        assert not any(path.startswith('/api/jobs/') for path, _, _ in reads)
    else:
        expect(page.get_by_role('button', name='Download sql/draft.sql', exact=True)).to_be_visible()
        expect(page.locator('#spark-draft-status')).to_contain_text('committed artifacts read and integrity-checked')
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download sql/draft.sql', exact=True).click()
        assert downloaded.value.path().read_bytes() == b'SELECT 1 AS draft_value\n'
        receipts = [value for path, _, value in reads if path.endswith('/spark-drafts')]
        assert len(receipts) == 1 and receipts[0]['job_id'] == job['id']
        assert receipts[0]['manifest']['design_version'] == 1
    inventories = [value for _, query, value in reads if query == {'version': ['1'], 'draft_jobs': ['1']}]
    assert len(inventories) == 1
    assert inventories[0]['draft_jobs'][0]['input_version'] == job['input_version']
    assert 'output' not in inventories[0]['draft_jobs'][0]
    assert not api.service.agent_executor.pending


@pytest.mark.parametrize("corruption", ["manifest", "artifact", "traversal", "checkpoint", "oversized"])
def test_result_never_reads_arbitrary_paths_or_corrupt_bytes(api, monkeypatch, corruption):
    job, directory = complete(api, monkeypatch)
    manifest_path = directory / "manifest.json"
    if corruption == "manifest":
        manifest_path.write_text("{}", encoding="utf-8")
    elif corruption == "artifact":
        (directory / "sql" / "draft.sql").write_text("Changed body", encoding="utf-8")
    else:
        manifest = json.loads(manifest_path.read_text())
        path = {"traversal": "sql/../../state/private.json", "checkpoint": "checkpoint_spark_codegen.sqlite",
                "oversized": "sql/draft.sql"}[corruption]
        if corruption == "oversized":
            raw = b"x" * (engine.MAX_CONTENT * 2 + 1)
            (directory / path).write_bytes(raw)
            digest = sha256(raw).hexdigest()
        else:
            digest = "0" * 64
        manifest["artifact_hashes"] = {path: digest}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        # Even a checksum-bound malicious manifest must fail the path/size gate.
        job["manifest_sha256"] = sha256(manifest_path.read_bytes()).hexdigest()
        api.service.update_record(job)
    response = api.client.get(f'/api/jobs/{job["id"]}/spark-drafts')
    assert response.status_code == 409 and "artifacts" not in response.json()


def test_result_rejects_artifact_symlinks_even_with_matching_bytes(api, monkeypatch):
    job, directory = complete(api, monkeypatch)
    artifact = directory / "sql" / "draft.sql"
    outside = api.root / "outside.sql"
    outside.write_bytes(artifact.read_bytes())
    artifact.unlink()
    try:
        artifact.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    assert api.client.get(f'/api/jobs/{job["id"]}/spark-drafts').status_code == 409


def test_checkpoint_bytes_are_preserved_and_existing_delta_classifier_accepts(api, monkeypatch):
    from workbench.delta_state import Snapshot, build_records
    _, directory = complete(api, monkeypatch)
    path = directory / "checkpoint_spark_codegen.sqlite"
    assert path.is_file() and not (directory / "checkpoint.sqlite").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT checkpoint FROM checkpoints").fetchone()[0] == b"opaque-checkpoint-bytes"
    raw = path.read_bytes()
    relative = path.relative_to(api.root).as_posix()
    store = SimpleNamespace(make_record=lambda table, **kwargs: kwargs)
    artifacts = SimpleNamespace(put=lambda value: {"volume_path": "/Volumes/test/schema/v/blob",
        "sha256": sha256(value).hexdigest(), "byte_size": len(value)})
    records = build_records(store, Snapshot(api.root, [{"path": relative,
                            "sha256": sha256(raw).hexdigest(), "byte_size": len(raw)}]), artifacts)
    assert len(records["agent_checkpoints"]) == 1
    assert records["agent_checkpoints"][0]["role"] == "spark_codegen"


def test_failed_engine_also_preserves_checkpoint_for_hosted_flush(api, monkeypatch):
    def fail(service, expected, *, consent):
        fake_draft(service, expected, consent=consent)
        raise RuntimeError("Bearer private-provider-detail")
    monkeypatch.setattr(engine, "draft_approved_design", fail)
    job = queue(api).json()
    api.service.agent_executor.drain()
    saved = api.service.record(job["id"])
    assert saved["status"] == "failed" and "Bearer" not in json.dumps(saved)
    assert len(list((api.root / "generated-drafts").rglob("checkpoint_spark_codegen.sqlite"))) == 1
    assert not list((api.root / "generated-drafts").rglob("checkpoint.sqlite"))


def test_existing_action_executor_carries_initiating_context(api, monkeypatch):
    from workbench.hosted import ActionExecutor, _CURRENT
    identity = SimpleNamespace(user_id="initiating-user")
    seen = []
    def draft(service, expected, *, consent):
        seen.append(_CURRENT.get())
        return fake_draft(service, expected, consent=consent)
    monkeypatch.setattr(engine, "draft_approved_design", draft)
    api.service.agent_executor = ActionExecutor(max_workers=1)
    token = _CURRENT.set(identity)
    try:
        job = queue(api).json()
    finally:
        _CURRENT.reset(token)
    api.service.agent_executor.submit(lambda: None).result(timeout=10)
    assert seen == [identity]
    assert api.service.record(job["id"])["status"] == "completed"


def test_ui_uses_real_routes_two_consents_and_no_automatic_submission():
    source = (Path(__file__).parents[1] / "workbench" / "static" / "redesign-review.js").read_text(encoding="utf-8")
    assert "Generate code (approved design)" in source
    assert "spark-draft-egress-consent" in source and "!egress.checked" in source
    assert "value.status !== 'approve_for_drafting'" in source
    assert "approval.version !== value.version" in source
    assert "record === value" in source
    assert "/api/redesigns/${encodeURIComponent(value.id)}/spark-drafts" in source
    assert "/api/jobs/${encodeURIComponent(id)}" in source
    assert "/api/jobs/${encodeURIComponent(id)}/spark-drafts" in source
    assert "pending = true; consent.checked = egress.checked = false" in source
    assert "No automatic retry" in source