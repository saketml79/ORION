"""No inference, deployment, UC writes or live services in these tests."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from databricks.sdk.errors import PermissionDenied
from fastapi.testclient import TestClient

from workbench.app import PackageIndex, _create_package_app
from workbench.draft_completeness import assess
from workbench.target_review import target_review
from workbench.tests.test_target_review import source  # noqa: F401
from workbench.tests.test_delta_runtime import authority, adapter  # noqa: F401


def test_scoped_review_and_target_versions_survive_delta_restart(source, authority, tmp_path):
    index, ids, path = source
    runtime = adapter(authority, tmp_path / 'first')
    runtime.restore()
    app = _create_package_app(runtime.local, path)
    headers = {'X-Workbench-Request': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        target = inventory['targets']['items'][0]['target_id']
        review = {'request_id': 'a' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
                  'input_version': inventory['input_version'], 'scenario': 'bronze', 'consent': True}
        assert client.post('/api/cohorts', json={**review, 'consent': False}, headers=headers).status_code == 403
        response = client.post('/api/cohorts', json=review, headers=headers)
        assert response.status_code == 201, response.text
        saved = response.json()
        assert len(saved['workflows']) == 2 and not saved['autonomous_worker_available']
        assert all(w['status'] == 'awaiting_individual_review' and w['agent_run_id'] is None for w in saved['workflows'])
        assert client.post('/api/cohorts', json=review, headers=headers).json() == saved
        assert client.post('/api/cohorts', json={**review, 'scenario': 'original'}, headers=headers).status_code == 409
        assert client.post('/api/cohorts', json={**review, 'request_id': 'b' * 32, 'input_version': '0' * 64}, headers=headers).status_code == 409
        body = {'workflow_id': ids['Parent'], 'scope': 'cohort', 'input_version': inventory['input_version'],
                'expected_version': 0, 'layer': 'silver', 'catalog': 'catalog_one',
                'schema_name': 'schema_one', 'table': 'target_one', 'is_test_fixture': True}
        selection = client.post('/api/target-selections/' + target, json=body, headers=headers)
        assert selection.status_code == 200, selection.text
        assert selection.json()['selection']['qualified_name'] == '`catalog_one`.`schema_one`.`target_one`'
        assert selection.json()['selection']['metadata_verified'] is False
        assert selection.json()['selection']['schema_compatible'] is None
        assert selection.json()['selection']['is_test_fixture'] is True
        assert client.post('/api/target-selections/' + target, json=body, headers=headers).status_code == 409
        assert client.post('/api/target-selections/' + target, json={**body, 'expected_version': 1, 'table': 'bad.name'}, headers=headers).status_code == 422
        assert client.post('/api/target-selections/' + '0' * 64, json=body, headers=headers).status_code == 404
        assert client.get('/api/target-selections/' + ids['Writer'] + '/' + target).json()['version'] == 0
    runtime.flush()
    restored = adapter(authority, tmp_path / 'restart')
    restored.restore()
    with closing(sqlite3.connect(restored.local / 'state/cohort.sqlite')) as db:
        assert db.execute('SELECT count(*) FROM cohort_runs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM cohort_workflows').fetchone()[0] == 2
        row = json.loads(db.execute('SELECT payload FROM target_selections').fetchone()[0])
        assert row['version'] == 1 and row['target_id'] == target
    restarted_app = _create_package_app(restored.local, path)
    with TestClient(restarted_app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        restarted_app.state.service.active = index
        assert client.get('/api/cohorts/' + 'a' * 32).json()['workflows'] == saved['workflows']
        assert client.get('/api/target-selections/' + ids['Parent'] + '/' + target).json()['version'] == 1
        assert client.post('/api/cohorts', json=review, headers=headers).json()['workflows'] == saved['workflows']
        index.model['source_analysis_version'] = 'changed'
        assert client.get('/api/cohorts/' + 'a' * 32).json()['stale'] is True
        assert client.get('/api/target-selections/' + ids['Parent'] + '/' + target).json()['selection']['stale'] is True
        index.model.pop('source_analysis_version')
    other = _create_package_app(tmp_path / 'other-user', path)
    with TestClient(other, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        other.state.service.active = index
        assert client.get('/api/cohorts/' + 'a' * 32).status_code == 404
        assert client.get('/api/target-selections/' + ids['Parent'] + '/' + target).json()['version'] == 0


def test_32_workflows_are_independent_records_not_a_completion_dag(source, tmp_path):
    index, ids, path = source
    model = deepcopy(index.model)
    parent_task = next(n for n in model['nodes'] if n['kind'] == 'task' and n['name'] == 'call')
    child = next(n for n in model['nodes'] if n['id'] == ids['Writer'])
    for i in range(30):
        clone = deepcopy(child)
        clone.update(id=f'{i + 1:064x}', name=f'Extra{i}')
        model['nodes'].append(clone)
        prop = deepcopy(parent_task['properties'][0])
        prop['attributes']['VALUE'] = f'pmcmd startworkflow -f F -nowait Extra{i}'
        parent_task['properties'].append(prop)
    expanded = PackageIndex(model, index.directory)
    inventory = target_review(expanded, ids['Parent'], 'cohort')
    assert inventory['workflow_count'] == 32
    app = _create_package_app(tmp_path / 'expanded', path)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = expanded
        response = client.post('/api/cohorts', headers={'X-Workbench-Request': '1'}, json={
            'request_id': 'c' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
            'input_version': inventory['input_version'], 'scenario': 'original', 'consent': True})
        assert response.status_code == 201, response.text
        result = response.json()
        assert len(result['workflows']) == len({row['workflow_id'] for row in result['workflows']}) == 32
        assert all(row['agent_run_id'] is None for row in result['workflows'])


def test_target_metadata_is_fresh_explicit_and_never_approved(source, tmp_path, monkeypatch):
    index, ids, path = source
    app = _create_package_app(tmp_path / 'metadata', path)
    provider = Mock()
    client_api = SimpleNamespace(schemas=Mock(), tables=Mock())
    client_api.schemas.get.return_value = SimpleNamespace(full_name='catalog_one.schema_one')
    client_api.tables.get.return_value = SimpleNamespace(full_name='catalog_one.schema_one.target_one',
        columns=[SimpleNamespace(name='ID', type_text='decimal(5,0)', nullable=True)])
    provider.return_value = SimpleNamespace(mode='obo', host='https://workspace.example', user_id='user-one', client=client_api)
    client_api.config = SimpleNamespace(host='https://workspace.example')
    client_api.current_user = SimpleNamespace(me=Mock(return_value=SimpleNamespace(id='user-one', user_name='alice')))
    monkeypatch.setenv('WORKBENCH_SQL_WAREHOUSE_ID', 'warehouse')
    statements = []
    monkeypatch.setattr('workbench.delta_state.DeltaStateStore._execute',
                        lambda self, sql: statements.append((self.user_id, sql)) or [])
    app.state.fixture_identity_provider = provider
    headers = {'X-Workbench-Request': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        target = inventory['targets']['items'][0]['target_id']
        body = {'workflow_id': ids['Parent'], 'scope': 'cohort', 'input_version': inventory['input_version'],
                'expected_version': 0, 'layer': 'silver', 'catalog': 'catalog_one',
                'schema_name': 'schema_one', 'table': 'target_one', 'is_test_fixture': True}
        assert client.post('/api/target-selections/' + target, json=body, headers=headers).status_code == 200
        verify = '/api/target-selections/' + target + '/verify'
        request = {'workflow_id': ids['Parent'], 'expected_version': 1, 'consent': True}
        assert client.post(verify, json={**request, 'consent': False}, headers=headers).status_code == 403
        assert client.post(verify, json={**request, 'expected_version': 2}, headers=headers).status_code == 409
        provider.assert_not_called()
        observation = client.post(verify, json=request, headers=headers).json()
        assert observation['metadata_verified'] and observation['schema_compatible']
        assert observation['identity']['user_id'] == 'user-one'
        assert observation['is_test_fixture'] and not observation['approved']
        assert not observation['data_access_verified'] and observation['data_access_status'] == 'not_tested'
        checked = client.post(verify, json={**request, 'check_select': True}, headers=headers).json()
        assert checked['data_access_verified'] and checked['data_access_status'] == 'select_zero_row_probe_succeeded'
        assert statements == [('user-one', 'SELECT 1 FROM `catalog_one`.`schema_one`.`target_one` WHERE 1=0')]
        assert not checked['approved'] and not checked['execution_allowed']
        assert not observation['verification_persisted'] and observation['semantic_parity'] == 'not_established'
        assert not client.get('/api/target-selections/' + ids['Parent'] + '/' + target).json()['selection']['metadata_verified']
        client_api.tables.get.side_effect = PermissionDenied('private bearer detail')
        denied = client.post(verify, json=request, headers=headers).json()
        assert denied['status'] == 'metadata_error' and not denied['metadata_verified']
        assert denied['schema_compatible'] is None and 'private bearer detail' not in json.dumps(denied)
        index.model['source_analysis_version'] = 'changed'
        assert client.post(verify, json=request, headers=headers).status_code == 409
        assert provider.call_count == 3
        index.model.pop('source_analysis_version')


def test_missing_saved_cohort_member_fails_closed(source, tmp_path):
    index, ids, path = source
    app = _create_package_app(tmp_path / 'incomplete', path)
    headers = {'X-Workbench-Request': '1'}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        body = {'request_id': 'a' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
                'input_version': inventory['input_version'], 'scenario': 'original', 'consent': True}
        assert client.post('/api/cohorts', json=body, headers=headers).status_code == 201
        with sqlite3.connect(app.state.service.state / 'cohort.sqlite') as db:
            db.execute('DELETE FROM cohort_workflows WHERE workflow_id=?', (ids['Writer'],))
        response = client.get('/api/cohorts/' + body['request_id'])
        assert response.status_code == 409 and 'incomplete' in response.json()['detail']
        assert client.post('/api/cohorts', json=body, headers=headers).status_code == 409


def test_completeness_never_equates_notebooks_with_job_or_parity():
    design = {'tasks': [{'id': 'load', 'implementation_kind': 'notebook_task'},
                        {'id': 'check', 'implementation_kind': 'condition_task'}],
              'edges': [{'source': 'load', 'target': 'check', 'condition': 'success'}]}
    manifest = {'tasks': [{'task_id': 'load', 'status': 'drafted', 'artifact_paths': ['notebooks/load.ipynb']},
                          {'task_id': 'check', 'status': 'blocked', 'artifact_paths': []}]}
    notebook = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': ['x = 1'],
         'outputs': [], 'execution_count': None}]})
    result = assess(design, manifest, {'notebooks/load.ipynb': notebook}, stale=None)
    assert result['status'] == 'incomplete' and not result['deployment_allowed']
    assert not result['execution_allowed']
    assert any('Native Jobs YAML was not produced' in reason for reason in result['blockers'])
    assert any('did not finish task check' in reason for reason in result['blockers'])
    assert result['freshness'] == 'unknown' and result['semantic_parity'] == 'not_established'
    broken = assess(design, manifest, {'notebooks/load.ipynb': '{'}, stale=True)
    assert any('static validation' in reason for reason in broken['blockers'])
    assert broken['freshness'] == 'stale'