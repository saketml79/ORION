"""Fresh OBO scoped readiness, committed inventories and independent campaigns."""
from copy import deepcopy

import pytest

from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_operations import HostedOperations
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B, create
from workbench.tests.test_hosted_registry import uploaded, fresh
from workbench.tests.test_hosted_object_reads import selected, full_index


def test_verified_projection_no_full_restore_no_writes_or_source_download(backend, uploaded, tmp_path, monkeypatch):
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    def forbidden(*a, **kw):
        pytest.fail('Readiness must not restore execution state, invoke models or publish')
    for method in ('restore', 'restore_audit', 'flush', 'flush_audit', '_database', '_verify_source'):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    with browser(backend) as client:
        result = client.get('/api/readiness?limit=25', headers=selected(project, package))
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['scope']['project_id'] == project and data['scope']['pins']['package_hash'] == package
        assert data['counts']['total_workflows'] == len(index.kinds['workflow'])
        assert data['counts']['runtime_validated'] is None
        assert client.get('/api/readiness', headers=selected(project, package, TOKEN_B)).status_code in (401, 404, 503)
    assert (backend.rows, backend.objects) == before
    assert not any(action == 'sql' and sql.startswith(('CREATE', 'MERGE', 'INSERT', 'UPDATE', 'DELETE')) for _, action, sql in backend.calls)


def test_discover_real_immutable_hosted_campaign_without_local_fallback(backend, uploaded, tmp_path, monkeypatch):
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    scope = selected(project, package)
    with browser(backend) as client:
        pins = client.get('/api/validation/context', headers=scope).json()['pins']
        report = client.post('/api/validation/campaigns', headers={**scope, 'X-Workbench-Request': '1'}, json={
            'consent': True, 'workflow_id': index.kinds['workflow'][0]['id'], 'scenario': 'original',
            'expected_package_hash': package, 'expected_understanding_sha256': pins['understanding_sha256'],
            'expected_analysis_version': pins['analysis_version'], 'source_fixture_catalog': 'existing_source'})
        assert report.status_code == 201, report.text
        assert report.headers['X-Workbench-Campaign-Publication'] == 'published'
        blob = report.headers['X-Workbench-Campaign-Artifact-SHA256']
        backend.calls.clear()
        monkeypatch.setattr(DeltaRuntime, 'restore', lambda *a, **k: pytest.fail('No full restore'))
        response = client.get('/api/readiness', headers=scope)
        assert response.status_code == 200, response.text
        assert response.json()['campaigns']['current'] == 1
        assert response.json()['counts']['output_parity_approved'] is None
        assert response.json()['issue_counts']
        assert not any(action == 'download' and path.endswith('/blobs/' + blob) for _, action, path in backend.calls)
        assert any(action == 'metadata' and path.endswith('/blobs/' + blob) for _, action, path in backend.calls)
        backend.calls.clear()
        exported = client.get('/api/validation/campaigns/' + report.json()['id'] + '/export', headers=scope)
        assert exported.status_code == 200, exported.text
        assert exported.json() == report.json()
        assert exported.headers['X-Workbench-Campaign-Artifact-SHA256'] == blob
        assert exported.headers['X-Workbench-Campaign-Current-Input-Pins-Match'] == 'true'
        assert not any(action == 'download' and '/uploads/' in path for _, action, path in backend.calls)
        canonical = next(row['artifact_path'] for rows in backend.rows.values() for row in rows
                 if row['source_path'].endswith('/understanding.json.gz'))
        assert not any(action == 'download' and path == canonical for _, action, path in backend.calls)
        artifact_path = next(path for path in backend.objects if path.endswith('/blobs/' + blob))
        del backend.objects[artifact_path]
        unavailable = client.get('/api/readiness', headers=scope)
        assert unavailable.status_code == 200, unavailable.text
        missing = unavailable.json()
        assert missing['business_validation']['coverage_available'] is False
        assert all(value is None for value in missing['business_validation']['counts'].values())
        assert any(r['code'] == 'CAMPAIGN_ARTIFACT_UNAVAILABLE' for r in missing['reasons'])
        assert missing['release_gate']['verified_business_workflows'] == 0
        assert not missing['deployment_allowed']
        exported = client.get('/api/validation/campaigns/' + report.json()['id'] + '/export', headers=scope)
        assert exported.status_code in (409, 503), exported.text


def test_readiness_exact_durable_route_whitelist():
    envelope = {'id': 'a' * 32, 'method': 'GET', 'path': '/api/readiness', 'body': {}}
    assert HostedOperations._request(envelope, ())[1]['path'] == '/api/readiness'
    for path in ('/api/readiness/write', '/api/readiness-other'):
        with pytest.raises(ValueError):
            HostedOperations._request({**envelope, 'path': path}, ())


@pytest.mark.parametrize('case', ['package', 'user_envelope', 'missing_record', 'bound'])
def test_readiness_fails_closed_on_scope_inventory_or_bound(backend, uploaded, tmp_path, monkeypatch, case):
    from workbench import delta_state as ds
    from workbench.hosted_object_reads import object_read_response
    from workbench.readiness import hosted_evidence
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    runtime = fresh(backend, tmp_path / 'read')
    if case == 'package':
        with browser(backend) as client:
            assert client.get('/api/readiness', headers=selected(project, '0' * 64)).status_code == 403
        return
    original = runtime.store.read_table
    def corrupt(table, **kwargs):
        rows = deepcopy(original(table, **kwargs))
        if table == 'artifact_manifest' and rows:
            if case == 'user_envelope':
                rows[0]['user_id'] = 'another-user'
                rows[0]['content_hash'] = ds._content_hash(rows[0])
            elif case == 'missing_record':
                rows.pop()
        return rows
    monkeypatch.setattr(runtime.store, 'read_table', corrupt)
    if case == 'bound':
        from workbench.hosted_registry import committed_generation
        receipt, inventory = committed_generation(runtime)
        # Duplicate excess scope entries must not trigger an unbounded read.
        scope = dict(project_id=project, package_hash=package, context_id='package:' + project + ':' + package)
        inventory = deepcopy(inventory['inventory'])
        inventory['agent_runs'] = [{'record_id': 'a' * 64, 'content_hash': 'b' * 64, **scope}] * 2001
        with pytest.raises(ValueError, match='bound exceeded'):
            hosted_evidence(runtime, inventory, receipt['migration_id'], scope)
    else:
        with pytest.raises((ValueError, PermissionError)):
            object_read_response(runtime, '/api/readiness', project, package, {})


def test_campaign_discovery_multiple_pages_and_tamper(backend, tmp_path):
    from workbench.validation_campaigns import CampaignStore, FORMAT
    from workbench import delta_state as ds
    runtime = fresh(backend, tmp_path / 'runtime')
    runtime.store.ensure_tables()
    store = CampaignStore(delta=runtime.store)
    project, package = 'a' * 32, 'b' * 64
    reports = []
    for number in range(51):
        report = {'id': f'{number:032x}', 'format': FORMAT, 'project_id': project,
                  'pins': {'package_hash': package}, 'workspace_id': runtime.store.workspace_id,
                  'user_id': runtime.store.user_id, 'workflow_id': 'w', 'created_at': '2026-11-06'}
        report['report_sha256'] = ds._hash(report)
        reports.append(report)
        runtime.store.upsert_records('jobs', [store._record(report)], migration_id='validation:' + report['id'])
    first, cursor, more = store.page(project, package)
    second, _, more_second = store.page(project, package, cursor)
    assert len(first) == 50 and more and len(second) == 1 and not more_second
    assert {r['id'] for r in first + second} == {r['id'] for r in reports}
    rows = next(rows for name, rows in backend.rows.items() if name.endswith('.jobs'))
    rows[0]['payload'] = '{}'
    with pytest.raises(ValueError, match='envelope mismatch'):
        store.page(project, package)