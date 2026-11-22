"""Concurrent verified graph ownership and real projected cross-route reads."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from workbench.hosted_object_reads import _immutable_index, _REVIEW_INDEX_CACHE, _REVIEW_INDEX_FLIGHTS
from workbench.tests.test_hosted_object_reads import clear_artifact_cache, full_index, selected
from workbench.tests.test_hosted import TOKEN_A, TOKEN_A2, backend, browser, offline
from workbench.tests.test_hosted_registry import uploaded


@pytest.fixture(autouse=True)
def clean_cache():
    _REVIEW_INDEX_CACHE.clear()
    yield
    assert not _REVIEW_INDEX_FLIGHTS
    _REVIEW_INDEX_CACHE.clear()


def test_concurrent_presenters_hydrate_once_with_request_local_facades():
    started, release = Event(), Event()
    calls = []
    def hydrate():
        calls.append('hydrate'); started.set()
        assert release.wait(5)
        return SimpleNamespace(directory=Path('owner'), model={'pin':'immutable'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_immutable_index, ('same',), Path('first'), hydrate, 1024)
        assert started.wait(5)
        second = pool.submit(_immutable_index, ('same',), Path('second'), hydrate, 1024)
        release.set()
        left, right = first.result(5), second.result(5)
    assert calls == ['hydrate']
    assert left is not right and left.model is right.model
    assert left.directory == Path('first') and right.directory == Path('second')


def test_failed_owner_is_not_cached_and_next_explicit_read_can_retry():
    def failed():
        raise ValueError('bad immutable evidence')
    with pytest.raises(ValueError, match='bad immutable evidence'):
        _immutable_index(('bad',), Path('one'), failed, 1024)
    assert not _REVIEW_INDEX_CACHE and not _REVIEW_INDEX_FLIGHTS
    assert _immutable_index(('bad',), Path('two'), lambda: SimpleNamespace(directory=None), 1024).directory == Path('two')


def test_identity_and_content_revision_never_share():
    calls = []
    def hydrate():
        calls.append(1)
        return SimpleNamespace(directory=None)
    for key in [('alice', 'v1'), ('bob', 'v1'), ('alice', 'v2')]:
        _immutable_index(key, Path('one'), hydrate, 1024)
    assert len(calls) == 3


def test_real_cross_route_requests_download_canonical_once_but_keep_fresh_authority(backend, uploaded, tmp_path):
    project, package, index = full_index(backend, uploaded, tmp_path / 'baseline')
    workflow = index.kinds['workflow'][0]['id']
    canonical = (index.directory / 'understanding.json.gz').read_bytes()
    before_rows, before_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    clear_artifact_cache()
    backend.calls.clear()
    with browser(backend) as client:
        paths = ['/api/object/' + workflow, '/api/workflows/' + workflow + '/migration-context',
                 '/api/redesigns', '/api/proposals?scenario=original&workflow_id=' + workflow]
        for path in paths:
            previous_calls = len(backend.calls)
            response = client.get(path, headers=selected(project, package))
            assert response.status_code == 200, response.text
            assert any(action == 'sql' and '.`migration_receipts`' in sql
                       for _, action, sql in backend.calls[previous_calls:])
        assert client.get(paths[0], headers=selected(project, '0'*64)).status_code == 403
    downloads = [path for _, action, path in backend.calls if action == 'download']
    assert sum(backend.objects[path] == canonical for path in downloads) == 1
    assert backend.rows == before_rows and backend.objects == before_objects


@pytest.mark.parametrize('paths', [
    ('/api/overview', '/api/objects?inventory=true&kind=workflow', '/api/workflow-invocation-summary'),
    ('/api/workflow-invocation-summary', '/api/objects?inventory=true&kind=workflow', '/api/overview'),
])
def test_overview_inventory_summary_share_one_hydration_with_fresh_authority(
        backend, uploaded, tmp_path, monkeypatch, paths):
    from databricks.sdk.errors import PermissionDenied
    import workbench.app as app
    import workbench.hosted_object_reads as reads

    project, package, baseline = full_index(backend, uploaded, tmp_path / 'baseline')
    canonical = (baseline.directory / 'understanding.json.gz').read_bytes()
    source = (baseline.directory / 'source.xml').read_bytes()
    english = (baseline.directory / 'english.md').read_bytes()
    clear_artifact_cache()
    backend.calls.clear()
    models, facades, keys, volume_checks = [], [], [], []
    read_model, immutable_index, client_factory = app._read_model, reads._immutable_index, backend.client
    revoked = False

    def tracked_model(*args, **kwargs):
        models.append(True)
        return read_model(*args, **kwargs)

    def tracked_index(key, directory, load, estimated_memory_bytes):
        index = immutable_index(key, directory, load, estimated_memory_bytes)
        keys.append(key)
        facades.append(index)
        return index

    def tracked_client(token):
        client = client_factory(token)
        volume_read = client.volumes.read
        def verify_volume(name):
            volume_checks.append(token)
            if revoked:
                raise PermissionDenied('revoked Volume permission')
            return volume_read(name)
        client.volumes.read = verify_volume
        return client

    monkeypatch.setattr(app, '_read_model', tracked_model)
    monkeypatch.setattr(reads, '_immutable_index', tracked_index)
    monkeypatch.setattr(backend, 'client', tracked_client)
    before_rows, before_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    with browser(backend) as client:
        for path, token in zip(paths, (TOKEN_A, TOKEN_A2, TOKEN_A), strict=True):
            prior_checks = len(volume_checks)
            response = client.get(path, headers=selected(project, package, token))
            assert response.status_code == 200, response.text
            assert token in volume_checks[prior_checks:]
        assert len(models) == 1
        assert len(keys) == 3 and keys[0] == keys[1] == keys[2]
        assert len({id(index) for index in facades}) == 3
        assert all(index.model is facades[0].model and index.nodes is facades[0].nodes for index in facades)
        assert len(reads._REVIEW_INDEX_CACHE) == 1
        revoked = True
        response = client.get('/api/workflow-invocation-summary', headers=selected(project, package, TOKEN_A2))
        assert response.status_code == 503, response.text
        assert volume_checks[-1] == TOKEN_A2 and len(models) == 1 and len(facades) == 3
    downloads = [backend.objects[path] for _, action, path in backend.calls if action == 'download']
    assert downloads.count(canonical) == downloads.count(source) == downloads.count(english) == 1
    assert backend.rows == before_rows and backend.objects == before_objects


def test_warm_graph_does_not_bypass_revoked_volume_permission(backend, uploaded, tmp_path, monkeypatch):
    from databricks.sdk.errors import PermissionDenied
    project, package, index = full_index(backend, uploaded, tmp_path / 'baseline')
    path = '/api/object/' + index.kinds['workflow'][0]['id']
    with browser(backend) as client:
        assert client.get(path, headers=selected(project, package)).status_code == 200
        original = backend.client
        def revoked(token):
            value = original(token)
            def denied(*args, **kwargs):
                raise PermissionDenied('revoked Volume permission')
            value.volumes.read = denied
            return value
        monkeypatch.setattr(backend, 'client', revoked)
        assert client.get(path, headers=selected(project, package)).status_code == 503