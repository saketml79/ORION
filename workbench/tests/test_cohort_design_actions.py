"""Offline only: no model provider, workspace, Jobs execution or catalog writes."""
from threading import Event
import json

import pytest
from fastapi.testclient import TestClient

from workbench.app import _create_package_app
from workbench.native_job_draft import build
from workbench.spark_codegen import ContractError
from workbench.target_review import target_review
from workbench.tests.test_target_review import source  # noqa: F401


class Runtime:
    def __init__(self):
        self.calls = []
        self.finished = Event()

    def status(self):
        return {'available': True, 'authentication': 'model_provided'}

    def run(self, model, workflow_id, scenario, prompt, documents, reviews, *,
            cancellation_event=None, progress_callback=None, knowledge_preview_consent=False):
        self.calls.append((workflow_id, scenario, prompt))
        self.finished.set()
        return {'status': 'completed', 'findings': []}


def test_explicit_independent_design_action_and_no_replay(source, tmp_path, monkeypatch):
    index, ids, path = source
    runtime = Runtime()
    app = _create_package_app(tmp_path / 'user', path, agent_runtime=runtime)
    service = app.state.service
    monkeypatch.setattr(service, 'agent_inputs', lambda *args: {
        'snapshot': json.dumps({'documents': [], 'reviews': []}), 'input_version': 'a' * 64})
    monkeypatch.setattr('workbench.redesign.capture_run', lambda *args: [])
    headers = {'X-Workbench-Request': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        saved = client.post('/api/cohorts', headers=headers, json={
            'request_id': 'a' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
            'scenario': 'bronze', 'input_version': inventory['input_version'], 'consent': True}).json()
        assert not runtime.calls
        item = next(row for row in saved['workflows'] if row['workflow_id'] == ids['Parent'])
        url = f"/api/cohorts/{saved['id']}/workflows/{item['workflow_id']}/design"
        body = {'operation_id': item['operation_id'], 'consent': True}
        assert client.post(url, headers=headers, json={**body, 'consent': False}).status_code == 403
        assert client.post(url, headers=headers, json={**body, 'operation_id': '0' * 32}).status_code == 409
        assert not runtime.calls
        response = client.post(url, headers=headers, json=body)
        assert response.status_code == 202, response.text
        assert runtime.finished.wait(10)
        service.agent_executor.shutdown(wait=True)
        assert len(runtime.calls) == 1 and runtime.calls[0][0] == ids['Parent']
        assert 'Java' in runtime.calls[0][2] and 'submit_redesign' in runtime.calls[0][2]
        prompt = runtime.calls[0][2]
        assert 'unapproved candidate-code draft' in prompt
        assert 'No separate design approval is required' in prompt
        assert 'No execution, deployment or parity approval is granted' in prompt
        assert 'do not silently draft other cohort members' in prompt
        assert 'human approval of the exact design is required before drafting' not in prompt
        assert 'No code, execution or deployment' not in prompt
        assert service.record(response.json()['id'], agent=True)['generate_code'] is True
        after = client.get('/api/cohorts/' + saved['id']).json()
        done = next(row for row in after['workflows'] if row['workflow_id'] == ids['Parent'])
        other = next(row for row in after['workflows'] if row['workflow_id'] == ids['Writer'])
        assert done['status'] == 'needs_information' and done['agent_run_id'] == response.json()['id']
        assert other['status'] == 'awaiting_individual_review'
        assert client.post(url, headers=headers, json=body).status_code == 409
        assert len(runtime.calls) == 1
    other_app = _create_package_app(tmp_path / 'other', path, agent_runtime=Runtime())
    with TestClient(other_app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        other_app.state.service.active = index
        assert client.get('/api/cohorts/' + saved['id']).status_code == 404


def test_review_only_api_keeps_candidate_writer_disabled(source, tmp_path, monkeypatch):
    index, ids, path = source
    runtime = Runtime()
    app = _create_package_app(tmp_path / 'review-only', path, agent_runtime=runtime)
    service = app.state.service
    monkeypatch.setattr(service, 'agent_inputs', lambda *args: {
        'snapshot': json.dumps({'documents': [], 'reviews': []}), 'input_version': 'a' * 64})
    # Even a captured logical design must not turn the review-only call into code.
    monkeypatch.setattr('workbench.redesign.capture_run', lambda *args: [{'id': 'review-design', 'version': 1}])
    def no_writer(*args, **kwargs):
        pytest.fail('Review-only request cannot invoke the candidate writer')
    monkeypatch.setattr('workbench.spark_codegen.draft_agent_design', no_writer)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        service.active = index
        response = client.post('/api/agents/run', headers={'X-Workbench-Request': '1'}, json={
            'workflow_id': ids['Parent'], 'scenario': 'bronze', 'prompt': 'Review only', 'consent': True})
        assert response.status_code == 202, response.text
        assert runtime.finished.wait(10)
        service.agent_executor.shutdown(wait=True)
        record = service.record(response.json()['id'], agent=True)
        assert record['generate_code'] is False
        assert record['redesign_refs'] == [{'id': 'review-design', 'version': 1}]
        assert record['output']['candidate_code_drafts'] == []
        assert not record['output']['approved'] and not record['output']['deployment_allowed']
        assert len(runtime.calls) == 1 and runtime.calls[0][2] == 'Review only'


def notebook():
    return json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': ['x = 1'],
         'outputs': [], 'execution_count': None}]})


def test_failed_agent_run_is_not_reported_as_missing_information(source, tmp_path, monkeypatch):
    index, ids, path = source
    runtime = Runtime()
    def fail(*args, **kwargs):
        runtime.finished.set()
        raise RuntimeError('private provider error')
    runtime.run = fail
    app = _create_package_app(tmp_path / 'user', path, agent_runtime=runtime)
    service = app.state.service
    monkeypatch.setattr(service, 'agent_inputs', lambda *args: {
        'snapshot': json.dumps({'documents': [], 'reviews': []}), 'input_version': 'a' * 64})
    headers = {'X-Workbench-Request': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        saved = client.post('/api/cohorts', headers=headers, json={
            'request_id': 'a' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
            'scenario': 'bronze', 'input_version': inventory['input_version'], 'consent': True}).json()
        item = next(row for row in saved['workflows'] if row['workflow_id'] == ids['Parent'])
        url = f"/api/cohorts/{saved['id']}/workflows/{item['workflow_id']}/design"
        response = client.post(url, headers=headers, json={'operation_id': item['operation_id'], 'consent': True})
        assert response.status_code == 202
        assert runtime.finished.wait(10)
        service.agent_executor.shutdown(wait=True)
        after = client.get('/api/cohorts/' + saved['id']).json()
        done = next(row for row in after['workflows'] if row['workflow_id'] == ids['Parent'])
        assert done['status'] == 'failed' and done['agent_run_id'] == response.json()['id']
        assert 'private provider error' not in json.dumps(after)
        assert client.post(url, headers=headers, json={'operation_id': item['operation_id'], 'consent': True}).status_code == 409


def test_native_job_draft_is_explicit_and_fail_closed():
    design = {'tasks': [{'id': 'first', 'implementation_kind': 'notebook_task'},
                        {'id': 'second', 'implementation_kind': 'notebook_task'}],
              'edges': [{'source': 'first', 'target': 'second', 'condition': None,
                         'outcome': None, 'run_if': 'ALL_SUCCESS'}]}
    manifest = {'tasks': [{'task_id': key, 'status': 'drafted', 'artifact_paths': [f'notebooks/{key}.ipynb']}
                          for key in ('first', 'second')]}
    artifacts = {f'notebooks/{key}.ipynb': notebook() for key in ('first', 'second')}
    result = json.loads(build(design, manifest, artifacts, '/Workspace/Users/test/release'))
    job = result['resources']['jobs']['migration_draft']
    assert job['performance_target'] == 'STANDARD'
    assert job['tasks'][1]['depends_on'] == [{'task_key': 'first'}]
    assert job['tasks'][0]['notebook_task']['notebook_path'] == '/Workspace/Users/test/release/notebooks/first'
    for bad_design, bad_manifest, bad_artifacts, root in [
        ({'tasks': [], 'edges': []}, {'tasks': []}, {}, '/Workspace/test'),
        ({**design, 'edges': [{**design['edges'][0], 'condition': 'unknown'}]}, manifest, artifacts, '/Workspace/test'),
        (design, manifest, {}, '/Workspace/test'),
        (design, manifest, artifacts, '/Workspace/../escape'),
        ({**design, 'edges': design['edges'] + [{'source': 'second', 'target': 'first'}]}, manifest, artifacts, '/Workspace/test'),
    ]:
        with pytest.raises(ContractError):
            build(bad_design, bad_manifest, bad_artifacts, root)