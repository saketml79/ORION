"""Exact-byte exports and fail-closed scope, offline only."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from workbench.app import _create_package_app
from workbench.cohort_review import reviewed_members
from workbench.draft_bundle import publish
from workbench.package_context import PackageContext
from workbench.package_graph import package_graph
from workbench.release_preview import preview, archive
from workbench.tests.test_native_job_draft_conditions import conditional_fixture
from workbench.tests.test_target_review import source  # noqa: F401
from workbench.tests.test_package_graph import small_index  # noqa: F401


@pytest.fixture
def saved_bundle(tmp_path):
    design, manifest, artifacts = conditional_fixture()
    manifest['run_id'] = 'c' * 32
    record = {'id': 'd' * 32, 'package_hash': 'a' * 64, 'workflow_id': 'e' * 64,
              'design_id': 'b' * 64, 'design_version': 3, 'input_version': 'f' * 64,
              'manifest_sha256': '1' * 64, 'output': {'run_id': manifest['run_id']}}
    root = tmp_path / 'generated-drafts' / record['package_hash'] / record['design_id'] / '3' / manifest['run_id']
    root.mkdir(parents=True)
    return publish(SimpleNamespace(root=tmp_path), record, {'design': design},
        {'manifest': manifest, 'artifacts': artifacts}, '/Workspace/Users/test')


def test_export_preserves_original_notebook_bytes_pins_and_zip(saved_bundle):
    result = preview([saved_bundle])
    report, artifacts = result['report'], result['artifacts']
    assert report['artifact_scope_complete'] and report['exported_member_count'] == 1
    assert not report['execution_allowed'] and not report['deployment_allowed'] and not report['production_ready']
    assert report['members'][0]['design_version'] == 3
    assert report['source_output_parity'] == 'not_established'
    document = json.loads(artifacts['databricks.yml'])
    job = next(iter(document['resources']['jobs'].values()))
    assert job['performance_target'] == 'STANDARD' and job['max_concurrent_runs'] == 1
    assert not any(key in job for key in ('schedule', 'continuous', 'trigger', 'run_as', 'job_clusters'))
    prefix = 'members/' + 'e' * 64 + '/'
    with ZipFile(BytesIO(archive(result))) as zipped:
        for path, content in saved_bundle['artifacts'].items():
            if path != 'migration-job.yaml':
                assert artifacts[prefix + path] == content
                assert zipped.read(prefix + path) == content.encode('utf-8')
        hashes = json.loads(zipped.read('export-manifest.json'))['files']
        for path, digest in hashes.items():
            assert sha256(zipped.read(path)).hexdigest() == digest
    for task in job['tasks']:
        if task.get('notebook_task'):
            assert task['notebook_task']['notebook_path'][2:] in artifacts
    ci = artifacts['.github/workflows/validate.yml']
    assert 'bundle validate' in ci and 'bundle deploy' not in ci and 'bundle run' not in ci


def test_cohort_export_never_duplicates_one_mock_for_missing_members(saved_bundle):
    members = ['e' * 64, '9' * 64, '8' * 64]
    result = preview([saved_bundle], members)
    assert result['report']['member_count'] == 3 and result['report']['exported_member_count'] == 1
    assert not result['report']['artifact_scope_complete']
    assert sum(row['status'] == 'missing_exact_reviewed_bundle' for row in result['report']['members']) == 2
    assert len(json.loads(result['artifacts']['databricks.yml'])['resources']['jobs']) == 1


def test_export_rejects_duplicate_outside_scope_and_mixed_inputs(saved_bundle):
    with pytest.raises(ValueError):
        preview([saved_bundle, saved_bundle])
    with pytest.raises(ValueError):
        preview([saved_bundle], ['9' * 64])
    other = deepcopy(saved_bundle)
    other['receipt']['workflow_id'] = '9' * 64
    other['receipt']['package_hash'] = '8' * 64
    with pytest.raises(ValueError):
        preview([saved_bundle, other])


def test_explicit_member_override_requires_owner_unit_and_reason(source):
    index, ids, _ = source
    inventory = {'workflow_ids': [ids['Parent'], ids['Writer']]}
    request = {'workflow_id': ids['Parent'], 'scope': 'cohort', 'member_ids': [ids['Parent']]}
    with pytest.raises(Exception) as rejected:
        reviewed_members(index, inventory, request)
    assert rejected.value.status_code == 422
    request.update(owner='Responsible migration owner', migration_unit='Reviewed unit', scope_rationale='Child remains external with explicit completion contract')
    assert reviewed_members(index, inventory, request) == [ids['Parent']]
    request['member_ids'].append('0' * 64)
    with pytest.raises(Exception):
        reviewed_members(index, inventory, request)


def test_member_paths_preserve_literal_nowait_not_completion(source):
    index, ids, _ = source
    context = PackageContext(index.model).contract(ids['Writer'])
    assert context['cohort']['literal_component_complete']
    parent = next(row for row in context['cohort']['items'] if row['workflow_id'] == ids['Parent'])
    assert 'callers and siblings' in parent['inclusion_reason']
    assert parent['inclusion_path']
    assert all(not edge['completion_dependency_verified'] for edge in parent['inclusion_path'])


def test_workflow_chunks_retain_cross_page_edges_and_exact_paths(small_index):
    expected = package_graph(small_index, all_nodes=True)
    chunks = [package_graph(small_index, offset=offset, limit=1, chunk=True) for offset in range(expected['total_nodes'])]
    nodes = {row['id']: row for chunk in chunks for row in chunk['nodes']}
    edges = {row['id']: row for chunk in chunks for row in chunk['edges']}
    assert nodes == {row['id']: row for row in expected['nodes']}
    assert edges == {row['id']: row for row in expected['edges']}
    assert all(chunk['edge_limit'] is None and chunk['chunk'] for chunk in chunks)
    with pytest.raises(ValueError):
        package_graph(small_index, limit=101, chunk=True)
    with pytest.raises(ValueError):
        package_graph(small_index, kind='mapping', chunk=True)


def test_release_api_refuses_unconsented_or_unknown_bundles(source, tmp_path):
    index, _, path = source
    app = _create_package_app(tmp_path / 'api', path)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        body = {'bundles': [{'job_id': 'd' * 32, 'bundle_id': 'a' * 64}], 'consent': False}
        assert client.post('/api/releases/preview', json=body, headers={'X-Workbench-Request': '1'}).status_code == 403
        body['consent'] = True
        assert client.post('/api/releases/preview', json=body, headers={'X-Workbench-Request': '1'}).status_code in (404, 409)
        assert client.post('/api/releases/archive', json={**body, 'execute': True}, headers={'X-Workbench-Request': '1'}).status_code == 422


def test_saved_override_roundtrip_retains_excluded_member_and_hard_gates(source, tmp_path):
    from workbench.target_review import target_review
    index, ids, path = source
    app = _create_package_app(tmp_path / 'override', path)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        inventory = target_review(index, ids['Parent'], 'cohort')
        body = {'request_id': 'c' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
            'input_version': inventory['input_version'], 'scenario': 'bronze', 'consent': True,
            'member_ids': [ids['Parent']], 'owner': 'Owner', 'migration_unit': 'Reviewed parent unit',
            'scope_rationale': 'Child retained as external boundary, completion unverified',
            'semantic_links': ['Owner supplied dependency note, not runtime proof']}
        response = client.post('/api/cohorts', json=body, headers={'X-Workbench-Request': '1'})
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved['excluded_member_ids'] == [ids['Writer']]
        assert [row['workflow_id'] for row in saved['workflows']] == [ids['Parent']]
        assert not saved['architecture_approved'] and not saved['execution_allowed'] and not saved['deployment_allowed']
        assert client.get('/api/cohorts/' + body['request_id']).json() == saved
        assert client.post('/api/cohorts', json=body, headers={'X-Workbench-Request': '1'}).json() == saved