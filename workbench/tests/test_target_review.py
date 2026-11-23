"""Source-target review only; never invokes a model or writes remote resources."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from workbench.app import PackageIndex, _create_package_app
from workbench.domain import _RELATIONS
from workbench.package_context import PackageContext
from workbench.target_review import target_review
from workbench.tests.test_package_context import parsed


@pytest.fixture
def source(tmp_path):
    extra = '<WORKFLOW NAME="Writer"><TASKINSTANCE NAME="write" TASKTYPE="Session" TASKNAME="WriteSession"/></WORKFLOW>'
    model, ids, path = parsed(tmp_path, extra=extra, command='pmcmd startworkflow -f F -nowait Writer')
    original = next(n for n in model['nodes'] if n['kind'] == 'target')
    original['fields'] = [{'attributes': {'NAME': 'ID', 'DATATYPE': 'number', 'PRECISION': '5',
                                         'SCALE': '0', 'NULLABLE': 'NULL', 'KEYTYPE': 'NOT A KEY'}}]
    duplicate = deepcopy(original)
    duplicate['id'] = 'f' * 64
    duplicate['name'] = 'Other'
    duplicate['attributes']['NAME'] = 'Other'
    model['nodes'].append(duplicate)
    local = PackageContext(model).local(ids['Writer'])
    reference = next(e for e in model['edges'] if e['target'] == original['id']
                     and e['source'] in local and e['kind'] in _RELATIONS)
    edge = deepcopy(reference)
    edge['id'] = 'e' * 64
    edge['target'] = duplicate['id']
    model['edges'].append(edge)
    (tmp_path / 'manifest.json').write_text(json.dumps({'understanding_sha256': 'c' * 64}), encoding='utf-8')
    return PackageIndex(model, tmp_path), ids, path


def test_target_scope_pagination_and_version_guard(source):
    index, ids, _ = source
    parent = target_review(index, ids['Parent'], 'workflow')
    assert parent['targets']['total'] == 0
    first = target_review(index, ids['Parent'], 'cohort', limit=1)
    assert first['workflow_count'] == 2 and first['targets']['total'] == 2
    assert first['targets']['has_more'] and first['targets']['next_offset'] == 1
    row = first['targets']['items'][0]
    assert row['exported_identity']['DATABASETYPE'] == 'Oracle'
    assert row['columns'][0]['NAME'] == 'ID'
    assert row['workflow_ids'] == [ids['Writer']]
    assert row['destination_layer'] is None and row['destination_catalog'] is None
    assert row['fixture_candidate']['status'] == 'unverified_test_fixture_candidate'
    assert not row['approved'] and not first['assignment_persisted']
    assert not first['execution_allowed'] and not first['deployment_allowed']
    second = target_review(index, ids['Parent'], 'cohort', offset=1, limit=1, expected=first['input_version'])
    assert second['targets']['items'][0]['target_id'] != row['target_id']
    assert not second['targets']['has_more']
    with pytest.raises(Exception) as error:
        target_review(index, ids['Parent'], 'cohort', expected='0' * 64)
    assert error.value.status_code == 409


def test_api_is_explicit_read_only_and_rejects_bad_scope(source, tmp_path):
    index, ids, path = source
    app = _create_package_app(tmp_path / 'runtime', path)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        result = client.get(f"/api/workflows/{ids['Parent']}/target-review", params={'scope': 'cohort'})
        assert result.status_code == 200 and result.json()['targets']['total'] == 2
        assert client.get(f"/api/workflows/{ids['Parent']}/target-review?scope=all").status_code == 422
        assert client.get(f"/api/workflows/{ids['Parent']}/target-review?limit=101").status_code == 422
        assert client.get('/api/workflows/missing/target-review').status_code == 422
        url = f"/api/workflows/{ids['Parent']}/target-review"
        assert client.post(url).status_code == 403  # mutation guard precedes routing
        assert client.post(url, headers={'X-Workbench-Request': '1'}).status_code == 405


def test_flat_file_fixture_candidate_uses_the_canonical_double_mapping(source):
    index, ids, _ = source
    target = next(n for n in index.model['nodes'] if n['kind'] == 'target')
    target['attributes']['DATABASETYPE'] = 'Flat File'
    target['fields'][0]['attributes'].update(DATATYPE='double', PRECISION='15', SCALE='0')
    from workbench.target_fixtures import target_table
    row = target_table(target['attributes'], [target['fields'][0]['attributes']], target['scope'])
    assert row['columns'][0]['type'] == 'DOUBLE'
    inventory = target_review(index, ids['Parent'], 'cohort')
    reviewed = next(r for r in inventory['targets']['items'] if r['target_id'] == target['id'])
    assert reviewed['fixture_candidate']['schema_name'] == row['schema']
    assert reviewed['fixture_candidate']['table'] == row['table']
    assert not reviewed['approved']