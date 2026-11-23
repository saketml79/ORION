"""Offline exact-scope/auth proofs for package-independent model setup."""
from copy import deepcopy
import json

import pytest
from databricks.sdk.errors import PermissionDenied

from workbench import databricks_models as dm
from workbench.delta_runtime import DeltaRuntime
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B, create, save_model
from workbench.tests.test_hosted_registry import uploaded, fresh, committed


def test_workspace_context_uses_fresh_identity_without_any_state_access(backend, monkeypatch):
    from workbench.tests.test_hosted import HOST, ORIGIN
    verified = []
    original = backend.resolve

    def resolve(host, token, **kwargs):
        verified.append(token)
        return original(host, token, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Workspace context must not touch storage, models, or full application runtime")

    monkeypatch.setattr(backend, "resolve", resolve)
    monkeypatch.setattr(DeltaRuntime, "__init__", forbidden)
    monkeypatch.setattr("workbench.hosted.provision", forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)
    monkeypatch.setattr(dm.WorkspaceModels, "discover", forbidden)
    monkeypatch.setattr(dm, "build_chat_models", forbidden)
    with browser(backend) as client:
        assert client.get('/api/models/workspace').status_code == 401
        for token in (TOKEN_A2, TOKEN_B):
            result = client.get('/api/models/workspace', headers=headers(token,
                **{'X-Workbench-Project': 'unrestored', 'X-Workbench-Package': 'unrestored'}))
            assert result.status_code == 200, result.text
            assert result.json() == {"workspace": HOST, "mode": "obo", "profile": None,
                                     "auth_check": "verified", "connected": False}
            assert ORIGIN not in result.text and token not in result.text
            assert result.headers['cache-control'] == 'no-store'
    assert verified == [TOKEN_A2, TOKEN_B]
    assert not backend.calls and not backend.rows and not backend.objects


def selected(project, package, token=TOKEN_A2):
    return headers(token, **{'X-Workbench-Project': project, 'X-Workbench-Package': package})


def test_discovery_requires_fresh_auth_but_no_storage_or_runtime(backend, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('Discovery must not restore, provision, build clients or save routes')
    monkeypatch.setattr(DeltaRuntime, 'restore', forbidden)
    monkeypatch.setattr('workbench.hosted.provision', forbidden)
    monkeypatch.setattr(dm, 'build_chat_models', forbidden)
    with browser(backend) as client:
        assert client.post('/api/models/connect', headers={'X-Workbench-Request': '1'}).status_code == 401
        assert client.post('/api/models/connect', headers={'x-forwarded-access-token': TOKEN_A2}).status_code == 403
        for token in (TOKEN_A2, TOKEN_B):
            result = client.post('/api/models/connect', headers=headers(token))
            assert result.status_code == 200, result.text
            assert result.json()['connected'] is True
            assert result.json()['user_id'] == ('bob-id' if token == TOKEN_B else 'alice-id')
            assert not result.json()['default_route_configured']
            assert set(result.json()['timings']) == {'authentication_seconds', 'endpoint_list_seconds'}
            assert all(value >= 0 for value in result.json()['timings'].values())
    assert not backend.rows and not backend.objects


def test_saved_package_routes_use_only_scoped_settings_not_xml_or_history(backend, uploaded, monkeypatch):
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    scope = selected(project, package)
    with browser(backend) as client:
        response = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, scope)
        assert response.status_code == 200, response.text
        rows, objects = deepcopy(backend.rows), deepcopy(backend.objects)
        backend.calls.clear()
        monkeypatch.setattr(DeltaRuntime, 'restore', lambda *a, **k: pytest.fail('No full restore'))
        monkeypatch.setattr(DeltaRuntime, 'flush', lambda *a, **k: pytest.fail('No settings write'))
        monkeypatch.setattr('workbench.app.PackageIndex.load', lambda *a, **k: pytest.fail('No XML index'))
        monkeypatch.setattr(dm, 'build_chat_models', lambda *a, **k: pytest.fail('Status must not construct model clients'))
        result = client.get('/api/models/status', headers=scope)
        assert result.status_code == 200, result.text
        assert result.json()['active_endpoint'] == 'chat'
        assert result.json()['workspace'] == dm._host('https://workspace.example.com')
        assert result.json()['inference_ready'] is True
        assert result.json()['runtime_verified'] is False
        root = client.get('/api/models/status', headers=headers(TOKEN_A2))
        assert root.status_code == 200 and root.json()['active_endpoint'] is None
    assert rows == backend.rows and objects == backend.objects
    statements = [sql for _, action, sql in backend.calls if action == 'sql']
    assert not any(sql.startswith(('CREATE', 'MERGE', 'INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert not any('.`jobs`' in sql or '.`audit_events`' in sql for sql in statements)
    downloads = [path for _, action, path in backend.calls if action == 'download']
    assert len(downloads) == 2  # Committed membership manifests only, settings stay inline.


def test_project_without_package_can_save_root_model_route(backend):
    with browser(backend) as client:
        project = create(client)
        scope = headers(TOKEN_A2, **{'X-Workbench-Project': project})
        result = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, scope)
        assert result.status_code == 200, result.text
        result = client.get('/api/models/status', headers=scope)
        assert result.status_code == 200 and result.json()['active_endpoint'] == 'chat'
        records = [row for table, rows in backend.rows.items() if table.endswith('.model_routes') for row in rows]
        assert records and all(row['context_id'] == 'hosted-model-settings-v1'
                       and row['project_id'] == project and row['package_hash'] == '__none__'
                       for row in records)


@pytest.mark.parametrize('case,expected', [('missing_project', 404), ('wrong_package', 403),
    ('cross_user', 404), ('denied', 503), ('corrupt', 503), ('missing_row', 503)])
def test_model_projection_fails_closed_without_local_fallback(backend, uploaded, tmp_path, monkeypatch, case, expected):
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    with browser(backend) as client:
        response = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, selected(project, package))
        assert response.status_code == 200, response.text
        token = TOKEN_A2
        if case == 'missing_project':
            project = '0' * 32
        elif case == 'wrong_package':
            package = '0' * 64
        elif case == 'cross_user':
            create(client, 'Bob', TOKEN_B)
            token = TOKEN_B
        elif case == 'denied':
            original = backend.client
            def denied(token):
                result = original(token)
                def fail(**kw):
                    raise PermissionDenied('sensitive ' + token)
                result.statement_execution.execute_statement = fail
                return result
            monkeypatch.setattr(backend, 'client', denied)
        else:
            runtime = fresh(backend, tmp_path)
            records = next(rows for table, rows in backend.rows.items() if table.endswith('.model_routes'))
            row = next(row for row in records if row['migration_id'] == 'hosted-model-settings-v1')
            if case == 'corrupt':
                row['payload'] = '{}'
            else:
                # Missing committed project membership must not become a settings fallback.
                generation = committed(runtime)['migration_id']
                memberships = next(rows for table, rows in backend.rows.items() if table.endswith('.projects'))
                memberships.remove(next(row for row in memberships if row['migration_id'] == generation
                                        and row['source_path'] == 'projects/projects.sqlite'
                                        and row['source_table'] == 'projects'))
        result = client.get('/api/models/status', headers=selected(project, package, token))
        assert result.status_code == expected, result.text
        assert TOKEN_A2 not in result.text and 'sensitive' not in result.text


def test_model_save_never_restores_xml_or_builds_clients_and_preserves_every_unrelated_record(
        backend, uploaded, tmp_path, monkeypatch):
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    runtime = fresh(backend, tmp_path / 'before')
    from workbench.hosted_registry import committed_generation
    receipt, manifest = committed_generation(runtime)
    before = runtime._records(manifest['inventory'], receipt['migration_id'])
    objects_before = deepcopy(backend.objects)
    def forbidden(*args, **kwargs):
        pytest.fail('A route save must not restore XML, audit, SQLite or model clients')
    monkeypatch.setattr(DeltaRuntime, 'restore', forbidden)
    monkeypatch.setattr(DeltaRuntime, 'restore_audit', forbidden)
    monkeypatch.setattr(DeltaRuntime, 'flush', forbidden)
    monkeypatch.setattr('workbench.hosted.user_app', forbidden)
    monkeypatch.setattr(dm, 'build_chat_models', forbidden)
    monkeypatch.setattr(DeltaRuntime, '_records', forbidden)
    with browser(backend) as client:
        result = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, selected(project, package))
        assert result.status_code == 200 and result.json()['runtime_verified'] is False
        assert result.json()['project_id'] == project and result.json()['package_hash'] == package
    after_runtime = fresh(backend, tmp_path / 'after')
    after_receipt, after_manifest = committed_generation(after_runtime)
    assert after_receipt == receipt and after_manifest == manifest
    assert backend.objects == objects_before
    for table, rows in before.items():
        persisted = next(values for name, values in backend.rows.items() if name.endswith('.' + table))
        assert sorted((r['record_id'], r['content_hash']) for r in persisted
                      if r['migration_id'] == receipt['migration_id']) == sorted(
                          (r['record_id'], r['content_hash']) for r in rows)
    journal = [row for name, rows in backend.rows.items() if name.endswith('.model_routes')
               for row in rows if row['migration_id'] == 'hosted-model-settings-v1']
    assert len(journal) == 1 and len(journal[0]['payload'].encode()) < 48 * 1024


def test_apply_accepts_while_writer_busy_and_deduplicates_exact_scope(backend, uploaded, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from uuid import uuid4
    import workbench.hosted_model_writes as writes
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    entered, release = Event(), Event()
    original = writes.select_model
    calls = []
    def blocked(*args, **kwargs):
        calls.append(True); entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)
    monkeypatch.setattr(writes, 'select_model', blocked)
    with browser(backend) as client, ThreadPoolExecutor() as executor:
        scope = selected(project, package)
        version = client.get('/api/models/status', headers=scope).json()['route_version']
        first = {'endpoint': 'chat', 'tool_support_confirmed': True,
                 'expected_route_version': version, 'operation_id': uuid4().hex}
        try:
            accepted = client.post('/api/models/select', headers=scope, json=first)
            assert accepted.status_code == 202, accepted.text
            assert entered.wait(10)
            duplicate = client.post('/api/models/select', headers=scope, json=first)
            assert duplicate.status_code == 202
            assert duplicate.json()['operation_id'] == first['operation_id']
            other_scope = headers(TOKEN_A2, **{'X-Workbench-Project': project})
            assert client.post('/api/models/select', headers=other_scope, json=first).status_code == 409
            second = {**first, 'operation_id': uuid4().hex}
            queued = executor.submit(client.post, '/api/models/select', headers=scope, json=second).result(10)
            assert queued.status_code == 202, queued.text
            # Both acknowledgements returned while the actual writer remains held.
            assert len(calls) == 1 and not release.is_set()
            assert client.get('/api/hosted/operations/' + first['operation_id'], headers=headers(TOKEN_B)).status_code != 200
        finally:
            release.set()
        client.app.routes[-1].app.operations.close(wait=True)
        completed = client.get('/api/hosted/operations/' + first['operation_id'], headers=scope).json()
        failed = client.get('/api/hosted/operations/' + second['operation_id'], headers=scope).json()
        assert completed['status'] == 'completed'
        assert failed['status'] == 'failed' and failed['action_status'] == 409
        assert len(calls) == 2  # Repeated first click did not execute again.
        assert not client.app.routes[-1].app.user_queues


def test_two_actual_async_model_saves_reuse_writer_and_preserve_fresh_identity(backend):
    with browser(backend) as client:
        create(client)
        for token in (TOKEN_A2, TOKEN_A2):
            result = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, headers(token))
            assert result.status_code == 200 and result.json()['selected_endpoint'] == 'chat'
            assert result.json()['committed_generation']
            assert result.json()['runtime_verified'] is False
            assert not client.app.routes[-1].app.user_queues
        result = client.get('/api/models/status', headers=headers(TOKEN_A2))
        assert result.status_code == 200 and result.json()['active_endpoint'] == 'chat'


@pytest.mark.parametrize('selection', [
    {}, {'endpoint': 'chat', 'tool_support_confirmed': False, 'expected_route_version': 'a' * 64},
    {'endpoint': '../bad', 'tool_support_confirmed': True, 'expected_route_version': 'a' * 64},
    {'endpoint': 'chat', 'tool_support_confirmed': True, 'expected_route_version': 'unknown'},
])
def test_invalid_model_submission_never_gets_a_durable_acceptance(backend, selection):
    from uuid import uuid4
    with browser(backend) as client:
        create(client)
        operation = uuid4().hex
        response = client.post('/api/models/select', headers=headers(), json={**selection, 'operation_id': operation})
        assert response.status_code == 400, response.text
        assert client.get('/api/hosted/operations/' + operation, headers=headers()).status_code == 404
        assert not client.app.routes[-1].app.user_queues


def test_settings_revision_survives_later_generation_and_is_loaded_before_client_construction(
        backend, uploaded, tmp_path, monkeypatch):
    from workbench.hosted import _CURRENT, user_app
    from workbench.tests.test_hosted import HOST
    from workbench.hosted_storage import provision
    from workbench.hosted_model_reads import model_status
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    with browser(backend) as client:
        saved = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, selected(project, package))
        assert saved.status_code == 200
        # A later unrelated full-state writer must not erase the settings journal.
        create(client, 'Unrelated later project')
        status = client.get('/api/models/status', headers=selected(project, package)).json()
        assert status['settings_revision'] == saved.json()['settings_revision']
    identity = backend.resolve(HOST, TOKEN_A2, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, 'orion_dev_', initialize=False)
    runtime = DeltaRuntime(lambda: identity, remote, tmp_path / 'execution', warehouse='warehouse')
    runtime.restore(initialize=False)
    # Full content restoration alone does not materialize separate preferences.
    target = runtime.local / f'projects/{project}/package-contexts/{package}/models/model-routing.json'
    assert not target.exists()
    built = []
    def build(fresh_identity, routes):
        built.append((fresh_identity.user_id, fresh_identity.client.config.authenticate()['Authorization'], routes))
        return {role: FakeListChatModel(responses=['unused']) for role in routes}
    monkeypatch.setattr(dm, 'build_chat_models', build)
    token = _CURRENT.set(identity)
    app = child = None
    try:
        app = user_app(runtime.local, runtime, read_only=False, execution_models=True)
        child = app.state.projects.factory(target.parents[1])
        assert built and built[-1][0:2] == ('alice-id', 'Bearer ' + TOKEN_A2)
        assert set(built[-1][2]) == set(dm.ROLE_PURPOSES)
        assert all(route['endpoint'] == 'chat' for route in built[-1][2].values())
        assert model_status(runtime, identity, project, package)['route_version'] == saved.json()['route_version']
    finally:
        for item in (child, app):
            if item is not None:
                if hasattr(item.state, 'projects'):
                    item.state.projects.close()
                item.state.service.close()
        _CURRENT.reset(token)


def test_settings_stale_version_and_bad_readback_never_report_success(backend, uploaded, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from workbench.hosted_model_writes import select_model
    from workbench.hosted_model_reads import model_status
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    runtime = fresh(backend, tmp_path)
    identity = runtime.identity_factory()
    body = {'endpoint': 'chat', 'tool_support_confirmed': True,
            'expected_route_version': '0' * 64}
    before = deepcopy(backend.rows)
    with pytest.raises(HTTPException) as error:
        select_model(runtime, identity, body, project, package)
    assert error.value.status_code == 409 and backend.rows == before
    body['expected_route_version'] = model_status(runtime, identity, project, package)['route_version']
    stages = []
    monkeypatch.setattr(runtime.store, 'upsert_records', lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match='unconfirmed'):
        select_model(runtime, identity, body, project, package, stages.append)
    assert stages == ['route_validate', 'route_commit', 'route_readback']
    assert backend.rows == before


def test_legacy_saved_route_remains_readable_until_explicit_scoped_save(backend, uploaded, tmp_path):
    from workbench.hosted_model_writes import _legacy_full_generation_select_model
    from workbench.hosted_model_reads import model_status
    project, _, detail = uploaded
    package = detail['packages'][0]['sha256']
    runtime = fresh(backend, tmp_path / 'legacy')
    identity = runtime.identity_factory()
    version = model_status(runtime, identity, project, package)['route_version']
    legacy = _legacy_full_generation_select_model(runtime, identity,
        {'endpoint': 'chat', 'tool_support_confirmed': True, 'expected_route_version': version}, project, package)
    with browser(backend) as client:
        status = client.get('/api/models/status', headers=selected(project, package))
        assert status.status_code == 200 and status.json()['active_endpoint'] == 'chat'
        assert status.json()['settings_revision'] is None
        saved = save_model(client, {'endpoint': 'chat', 'tool_support_confirmed': True}, selected(project, package))
        assert saved.status_code == 200
        assert saved.json()['parent_generation'] == legacy['committed_generation']
        status = client.get('/api/models/status', headers=selected(project, package)).json()
        assert status['settings_revision'] == saved.json()['settings_revision']