"""Offline committed-generation GET parity and concurrent writer isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from workbench import delta_state as ds
from workbench.app import _create_package_app
from workbench.delta_runtime import DeltaRuntime
from workbench.redesign import RedesignStore
from workbench.target_review import target_review
from workbench.tests.test_cohort_design_actions import notebook
from workbench.tests.test_hosted import backend, browser, headers, offline, create, HOST, TOKEN_A2, TOKEN_B
from workbench.tests.test_hosted_object_reads import full_index, selected
from workbench.tests.test_hosted_registry import fresh, committed, rewrite_manifest
from workbench.tests.test_package_context import parsed
from workbench.tests.test_redesign import design_for


@pytest.fixture
def saved(backend, tmp_path):
    extra = '<WORKFLOW NAME="Writer"><TASKINSTANCE NAME="write" TASKTYPE="Session" TASKNAME="WriteSession"/></WORKFLOW>'
    _, _, xml = parsed(tmp_path, extra=extra, command='pmcmd startworkflow -f F -nowait Writer')
    with browser(backend) as client:
        project = create(client)
        response = client.post(f'/api/projects/{project}/packages/upload', params={'stage_only': 'true'},
            headers=headers(), files={'files': ('review.xml', xml.read_bytes(), 'application/xml')})
        assert response.status_code == 202, response.text
        assert client.post(f'/api/projects/{project}/analyze', headers=headers()).status_code == 202
        detail = client.get(f'/api/projects/{project}', headers=headers()).json()
    project, package, index = full_index(backend, (project, None, detail), tmp_path / 'canonical')
    runtime = fresh(backend, tmp_path / 'seed')
    runtime.restore(initialize=False)
    prefix = f'projects/{project}/package-contexts/{package}/'
    app = _create_package_app(runtime.local / prefix, xml)
    ids = {row['name']: row['id'] for row in index.kinds['workflow']}
    expected = {}
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as local:
        service = app.state.service
        service.active = index
        for scenario in ('original', 'bronze'):
            service.proposals(index, scenario)
        original = index.proposal_summaries('original')[0]
        service.store.save_review(package, original['id'], 1, 'comment', 'Keep the captured diagnostic')
        service.store.save_document(package, 'evidence.txt', 'Independent evidence still needs review',
                                    {'workflow_id': original['workflow_id']})
        inventory = target_review(index, ids['Parent'], 'cohort')
        result = local.post('/api/cohorts', headers={'X-Workbench-Request': '1'}, json={
            'request_id': 'c' * 32, 'workflow_id': ids['Parent'], 'scope': 'cohort',
            'input_version': inventory['input_version'], 'scenario': 'bronze', 'consent': True})
        assert result.status_code == 201, result.text
        target = inventory['targets']['items'][0]['target_id']
        result = local.post('/api/target-selections/' + target, headers={'X-Workbench-Request': '1'}, json={
            'workflow_id': ids['Parent'], 'scope': 'cohort', 'input_version': inventory['input_version'],
            'expected_version': 0, 'layer': 'silver', 'catalog': 'user_fixture', 'schema_name': 'review',
            'table': 'target', 'is_test_fixture': True})
        assert result.status_code == 200, result.text
        design = design_for(index.model, ids['Writer'])
        design['tasks'][0]['implementation_kind'] = 'notebook_task'
        design_record = RedesignStore(service.state / 'redesigns.sqlite').append(package, ids['Writer'], 'bronze',
            {'role': 'architecture', 'design': design, 'retrieved_evidence_ids': design['evidence_ids']},
            {'record_id': 'd' * 32, 'runtime_run_id': 'e' * 32, 'submission_index': 0,
             'kind': 'agent', 'input_version': 'a' * 64}, 0)
        job_id, run = 'a' * 32, 'b' * 32
        directory = service.root / 'generated-drafts' / package / design_record['id'] / '1' / run
        (directory / 'notebooks').mkdir(parents=True)
        body = notebook().encode()
        (directory / 'notebooks/load.ipynb').write_bytes(body)
        manifest = {'run_id': run, 'package_hash': package, 'design_id': design_record['id'],
            'design_version': 1, 'input_version': 'a' * 64,
            'artifact_hashes': {'notebooks/load.ipynb': sha256(body).hexdigest()},
            'tasks': [{'task_id': design['tasks'][0]['id'], 'status': 'drafted',
                       'artifact_paths': ['notebooks/load.ipynb']}]}
        raw = ds._json(manifest).encode()
        (directory / 'manifest.json').write_bytes(raw)
        job = {'id': job_id, 'kind': 'spark_draft', 'status': 'completed', 'package_hash': package,
            'workflow_id': ids['Writer'], 'scenario': 'bronze', 'design_id': design_record['id'],
            'design_version': 1, 'input_version': 'a' * 64, 'output': {'run_id': run},
            'manifest_sha256': sha256(raw).hexdigest()}
        service.update_record(job)
        service.update_record({**job, 'id': 'f' * 32, 'status': 'running'})
        requests = [('/api/proposals', {'scenario': scenario, 'limit': 2, 'offset': 1})
                    for scenario in ('original', 'bronze')]
        requests += [('/api/proposals/' + original['id'], {'scenario': 'original'}),
            ('/api/redesigns/' + design_record['id'], {'version': 1, 'draft_jobs': '1'}),
            ('/api/redesigns/' + design_record['id'], {'draft_jobs': 'true'}),
            ('/api/proposals', {'workflow_id': ids['Parent']}),
            ('/api/cohorts/' + 'c' * 32, {}),
            ('/api/target-selections/' + ids['Parent'] + '/' + target, {}),
            ('/api/target-selections/' + ids['Writer'] + '/' + target, {}),
            ('/api/jobs/' + job_id, {}), ('/api/jobs/' + job_id + '/spark-drafts', {}),
            ('/api/jobs/' + job_id + '/completeness', {})]
        for path, query in requests:
            response = local.get(path, params=query)
            assert response.status_code == 200, response.text
            expected[(path, tuple(query.items()))] = response.json()
    runtime.flush()
    return project, package, ids, target, expected


def forbid_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A selected GET must not restore, publish, parse XML or construct model clients')
    for method in ('restore', 'restore_audit', 'flush', 'flush_audit', '_records', '_database', '_verify_source'):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    monkeypatch.setattr('workbench.hosted.user_app', forbidden)
    monkeypatch.setattr('workbench.app.analyze_package', forbidden)
    monkeypatch.setattr('workbench.understanding.analyze_package', forbidden)
    monkeypatch.setattr('workbench.agents.AgentRuntime.__init__', forbidden)
    monkeypatch.setattr('workbench.app._Service.lite_records', forbidden)


def test_json_gets_overlap_two_jobs_and_held_model_writer_without_restore(backend, saved, monkeypatch):
    project, package, ids, target, expected = saved
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    forbid_runtime(monkeypatch)
    import workbench.hosted_object_reads as reads
    original = reads.object_read_response
    gate, parallel = Barrier(2), [True]
    def concurrent_read(*args, **kwargs):
        if parallel[0]:
            gate.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(reads, 'object_read_response', concurrent_read)
    with browser(backend) as client:
        dispatcher = getattr(client.app, 'routes')[-1].app
        owner = sha256((HOST + '\n' + 'alice-id').encode()).hexdigest()
        queue = {'lock': object(), 'requests': 1, 'active': {'operation_id': '9' * 32, 'path': '/api/models/select'}}
        dispatcher.user_queues[owner] = queue
        while dispatcher.capacity.acquire(blocking=False):
            pass
        dispatcher.users[owner] = 1
        try:
            def read(entry):
                (path, query), wanted = entry
                response = client.get(path, params=dict(query), headers=selected(project, package))
                assert response.status_code == 200, response.text
                actual = response.json()
                if 'build_seconds' in actual:
                    actual.pop('build_seconds')
                    wanted = {key: value for key, value in wanted.items() if key != 'build_seconds'}
                assert actual == wanted
                assert response.headers['cache-control'] == 'no-store'
                return path
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert len(list(pool.map(read, expected.items()))) == len(expected)
            parallel[0] = False
            for path in ('/api/jobs/' + 'f' * 32, '/api/jobs/' + 'a' * 32):
                assert client.get(path, headers=selected(project, package)).status_code == 200
            pending = client.get('/api/jobs/' + 'f' * 32 + '/spark-drafts', headers=selected(project, package))
            assert pending.status_code == 409 and 'not committed' in pending.json()['detail']
            assert queue['requests'] == 1 and dispatcher.users[owner] == 1
        finally:
            dispatcher.users.pop(owner)
            dispatcher.user_queues.pop(owner)
    assert (backend.rows, backend.objects) == before
    statements = [sql for _, action, sql in backend.calls if action == 'sql']
    assert not any(sql.startswith(('CREATE', 'MERGE', 'INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert not any(f'.`{family}`' in sql for sql in statements for family in (
        'audit_events', 'agent_runs', 'model_routes', 'agent_checkpoints', 'loop_runs', 'loop_attempts'))
    assert all(token == TOKEN_A2 for token, *_ in backend.calls)


def test_writer_outputs_do_not_hydrate_canonical_index(backend, saved, monkeypatch):
    project, package, _, _, _ = saved
    forbid_runtime(monkeypatch)
    monkeypatch.setattr('workbench.app.PackageIndex.load', lambda *a, **k: pytest.fail('Draft GET needs no canonical graph'))
    backend.calls.clear()
    with browser(backend) as client:
        for suffix in ('', '/spark-drafts', '/completeness'):
            result = client.get('/api/jobs/' + 'a' * 32 + suffix, headers=selected(project, package))
            assert result.status_code == 200, result.text
        complete = result.json()
        assert complete['status'] == 'blocked' and complete['freshness'] == 'unknown'
        assert not complete['execution_allowed'] and not complete['deployment_allowed']
    downloads = [backend.objects[path] for _, action, path in backend.calls if action == 'download']
    assert not any(raw.startswith((b'\x1f\x8b', b'<POWERMART')) for raw in downloads)


@pytest.mark.parametrize('job_scope', ['other_workflow', 'matching_writer', 'other_kind'])
def test_saved_design_jobs_skip_unrelated_and_output_artifact_downloads(backend, saved, tmp_path, monkeypatch, job_scope):
    from workbench.hosted_review_reads import MAX_BYTES
    project, package, ids, _, _ = saved
    runtime = fresh(backend, tmp_path / 'large-job')
    runtime.restore(initialize=False)
    directory = runtime.local / f'projects/{project}/package-contexts/{package}/state/jobs'
    job = json.loads((directory / ('a' * 32 + '.json')).read_bytes())
    saved_job = deepcopy(job)
    job.update(id='1' * 32, created_at='2099-01-01T00:00:00+00:00')
    large_output = 'x' * (MAX_BYTES + 1)
    job['output'] = {**job['output'], 'artifact_text': large_output}
    if job_scope == 'other_workflow':
        job['workflow_id'] = ids['Parent']
        # Even non-output metadata from an unrelated workflow must not download.
        job['diagnostic'] = large_output
    elif job_scope == 'other_kind':
        job['kind'] = 'sql_draft'
    (directory / (job['id'] + '.json')).write_text(ds._json(job), encoding='utf-8')
    runtime.flush()
    backend.calls.clear()
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get('/api/redesigns/' + job['design_id'],
            params={'version': job['design_version'], 'draft_jobs': '1'}, headers=selected(project, package))
        assert response.status_code == 200, response.text
        jobs = response.json()['draft_jobs']
        assert {'a' * 32, 'f' * 32} <= {value['id'] for value in jobs}
        for value in jobs:
            assert {key: value[key] for key in ('kind', 'package_hash', 'workflow_id', 'scenario',
                                               'input_version', 'design_id', 'design_version')} == {
                key: saved_job[key] for key in ('kind', 'package_hash', 'workflow_id', 'scenario',
                                               'input_version', 'design_id', 'design_version')}
            assert set(value) == {'id', 'status', 'created_at', 'design_id', 'design_version',
                                  'kind', 'package_hash', 'workflow_id', 'scenario', 'input_version'}
        if job_scope == 'matching_writer':
            assert jobs[0]['id'] == job['id'], 'The latest matching writer must not disappear'
        else:
            assert job['id'] not in {value['id'] for value in jobs}
    downloads = [backend.objects[path] for _, action, path in backend.calls if action == 'download']
    assert large_output.encode() not in downloads


@pytest.mark.parametrize('bad', ['identity', 'metadata_bound', 'unrelated_row_hash'])
def test_saved_design_job_projection_keeps_relevant_validation_and_full_scope_integrity(backend, saved, tmp_path, monkeypatch, bad):
    from workbench.hosted_review_reads import MAX_BYTES
    project, package, ids, _, _ = saved
    runtime = fresh(backend, tmp_path / 'bad-job')
    runtime.restore(initialize=False)
    directory = runtime.local / f'projects/{project}/package-contexts/{package}/state/jobs'
    job = json.loads((directory / ('a' * 32 + '.json')).read_bytes())
    design, version = job['design_id'], job['design_version']
    if bad == 'identity':
        job['id'] = '0' * 32
    elif bad == 'metadata_bound':
        job['input_version'] = 'x' * (MAX_BYTES + 1)
    else:
        job['workflow_id'] = ids['Parent']
    (directory / ('a' * 32 + '.json')).write_text(ds._json(job), encoding='utf-8')
    runtime.flush()
    if bad == 'unrelated_row_hash':
        generation = committed(runtime)['migration_id']
        row = next(row for name, rows in backend.rows.items() if name.endswith('.jobs') for row in rows
                   if row['migration_id'] == generation and row['source_path'].endswith('/' + 'a' * 32 + '.json'))
        row['payload'] = '{}'
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get('/api/redesigns/' + design,
            params={'version': version, 'draft_jobs': 'true'}, headers=selected(project, package))
        assert response.status_code == 503, response.text


def test_saved_design_job_projection_keeps_2000_record_scope_bound():
    from types import SimpleNamespace
    from workbench.hosted_review_reads import Evidence, MAX_RECORDS
    scope = {'project_id': 'a' * 32, 'package_hash': 'b' * 64, 'context_id': 'package:test'}
    inventory = {'jobs': [{**scope, 'record_id': f'{number:064x}', 'content_hash': 'c' * 64}
                          for number in range(MAX_RECORDS + 1)]}
    runtime = SimpleNamespace(store=SimpleNamespace(read_table=lambda *a, **k: pytest.fail('Bound must be checked before querying')))
    assert MAX_RECORDS == 2000
    with pytest.raises(ValueError, match='bound exceeded'):
        Evidence(runtime, inventory, 'generation', scope).rows('jobs')


@pytest.mark.parametrize('route', ['proposals', 'cohort', 'selection', 'draft'])
@pytest.mark.parametrize('bad', ['user', 'package', 'project', 'row_hash', 'missing_row'])
def test_fresh_scope_and_complete_inventory_fail_closed(backend, saved, tmp_path, monkeypatch, route, bad):
    project, package, ids, target, _ = saved
    path = {'proposals': '/api/proposals', 'cohort': '/api/cohorts/' + 'c' * 32,
        'selection': '/api/target-selections/' + ids['Parent'] + '/' + target,
        'draft': '/api/jobs/' + 'a' * 32 + '/spark-drafts'}[route]
    with browser(backend) as client:
        assert client.get(path, headers=selected(project, package)).status_code == 200
        if bad == 'user':
            create(client, 'Other actor', TOKEN_B)
        elif bad in {'row_hash', 'missing_row'}:
            runtime = fresh(backend, tmp_path / 'corrupt')
            generation = committed(runtime)['migration_id']
            table = {'proposals': 'proposal_versions', 'cohort': 'jobs', 'selection': 'settings', 'draft': 'jobs'}[route]
            rows = next(rows for name, rows in backend.rows.items() if name.endswith('.' + table))
            row = next(row for row in rows if row['migration_id'] == generation and row['package_hash'] == package
                       and row['context_id'] == 'package:' + project + ':' + package)
            if bad == 'row_hash':
                row['payload'] = '{}'
            else:
                rows.remove(row)
        forbid_runtime(monkeypatch)
        response = client.get(path, headers=selected('0' * 32 if bad == 'project' else project,
            '0' * 64 if bad == 'package' else package, TOKEN_B if bad == 'user' else TOKEN_A2))
        assert response.status_code == (404 if bad in {'project', 'user'} else 403 if bad == 'package' else 503), response.text
        assert TOKEN_A2 not in response.text


def test_self_consistent_wrong_sql_source_key_is_not_trusted(backend, saved, tmp_path, monkeypatch):
    project, package, ids, target, _ = saved
    runtime = fresh(backend, tmp_path / 'corrupt')
    generation = committed(runtime)['migration_id']
    row = next(row for name, rows in backend.rows.items() if name.endswith('.settings') for row in rows
               if row['migration_id'] == generation and row['source_table'] == 'target_selections')
    old = row['record_id'], row['content_hash']
    packed = json.loads(row['payload'])
    packed['workflow_id'] = '0' * 64
    row['payload'] = ds._json(packed)
    row['content_hash'] = ds._content_hash(row)
    def revision(manifest):
        item = next(item for item in manifest['inventory']['settings']
                    if (item['record_id'], item['content_hash']) == old)
        item['content_hash'] = row['content_hash']
    rewrite_manifest(runtime, backend, revision)
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get('/api/target-selections/' + ids['Parent'] + '/' + target,
                              headers=selected(project, package))
        assert response.status_code == 503


def test_stale_saved_scope_is_reported_not_cleared_on_same_canonical(backend, saved, tmp_path, monkeypatch):
    project, package, ids, target, _ = saved
    runtime = fresh(backend, tmp_path / 'old-inputs')
    generation = committed(runtime)['migration_id']
    changes = {}
    for name, rows in backend.rows.items():
        for row in rows:
            if row['migration_id'] != generation or row['source_table'] not in {'cohort_runs', 'target_selections'}:
                continue
            packed = json.loads(row['payload'])
            value = json.loads(packed['payload'])
            if row['source_table'] == 'cohort_runs':
                value['request']['input_version'] = '0' * 64
            else:
                value['input_version'] = '0' * 64
            old = row['record_id'], row['content_hash']
            packed['payload'] = ds._json(value)
            row['payload'] = ds._json(packed)
            row['content_hash'] = ds._content_hash(row)
            changes[old] = row['content_hash']
    assert len(changes) == 2
    def revision(manifest):
        for table in ('jobs', 'settings'):
            for item in manifest['inventory'][table]:
                old = item['record_id'], item['content_hash']
                if old in changes:
                    item['content_hash'] = changes[old]
    rewrite_manifest(runtime, backend, revision)
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get('/api/cohorts/' + 'c' * 32, headers=selected(project, package))
        assert response.status_code == 200 and response.json()['stale'] is True
        response = client.get('/api/target-selections/' + ids['Parent'] + '/' + target,
                              headers=selected(project, package))
        assert response.status_code == 200 and response.json()['selection']['stale'] is True
        assert response.json()['selection']['is_test_fixture'] is True
        assert response.json()['selection']['approved'] is False
    assert (backend.rows, backend.objects) == before


def test_committed_but_incomplete_cohort_does_not_claim_complete(backend, saved, tmp_path, monkeypatch):
    project, package, _, _, _ = saved
    runtime = fresh(backend, tmp_path / 'incomplete')
    generation = committed(runtime)['migration_id']
    rows = next(rows for name, rows in backend.rows.items() if name.endswith('.jobs'))
    removed = next(row for row in rows if row['migration_id'] == generation and row['source_table'] == 'cohort_workflows')
    rows.remove(removed)
    def revision(manifest):
        manifest['inventory']['jobs'] = [item for item in manifest['inventory']['jobs']
            if item['record_id'] != removed['record_id']]
    rewrite_manifest(runtime, backend, revision)
    forbid_runtime(monkeypatch)
    with browser(backend) as client:
        response = client.get('/api/cohorts/' + 'c' * 32, headers=selected(project, package))
        assert response.status_code == 409 and 'incomplete' in response.json()['detail']


def test_read_allowlist_does_not_expand_mutations_or_bundle_approval_routes():
    from workbench.hosted_object_reads import ROUTE
    assert ROUTE.fullmatch('/api/proposals')
    assert ROUTE.fullmatch('/api/jobs/' + 'a' * 32 + '/completeness')
    for path in ('/api/cohorts', '/api/target-selections', '/api/proposals/sql-drafts',
                 '/api/jobs/' + 'a' * 32 + '/native-bundles', '/api/models/select'):
        assert ROUTE.fullmatch(path) is None