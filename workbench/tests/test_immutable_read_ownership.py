"""Concurrent verified graph ownership and real projected cross-route reads."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from workbench.hosted_object_reads import _immutable_index, _REVIEW_INDEX_CACHE, _REVIEW_INDEX_FLIGHTS
from workbench.tests.test_hosted_object_reads import full_index, selected
from workbench.tests.test_hosted import backend, browser, offline
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
        first = pool.submit(_immutable_index, ('same',), Path('first'), hydrate)
        assert started.wait(5)
        second = pool.submit(_immutable_index, ('same',), Path('second'), hydrate)
        release.set()
        left, right = first.result(5), second.result(5)
    assert calls == ['hydrate']
    assert left is not right and left.model is right.model
    assert left.directory == Path('first') and right.directory == Path('second')


def test_failed_owner_is_not_cached_and_next_explicit_read_can_retry():
    def failed():
        raise ValueError('bad immutable evidence')
    with pytest.raises(ValueError, match='bad immutable evidence'):
        _immutable_index(('bad',), Path('one'), failed)
    assert not _REVIEW_INDEX_CACHE and not _REVIEW_INDEX_FLIGHTS
    assert _immutable_index(('bad',), Path('two'), lambda: SimpleNamespace(directory=None)).directory == Path('two')


def test_identity_and_content_revision_never_share():
    calls = []
    def hydrate():
        calls.append(1)
        return SimpleNamespace(directory=None)
    for key in [('alice', 'v1'), ('bob', 'v1'), ('alice', 'v2')]:
        _immutable_index(key, Path('one'), hydrate)
    assert len(calls) == 3


def test_real_cross_route_requests_download_canonical_once_but_keep_fresh_authority(backend, uploaded, tmp_path):
    project, package, index = full_index(backend, uploaded, tmp_path / 'baseline')
    workflow = index.kinds['workflow'][0]['id']
    canonical = (index.directory / 'understanding.json.gz').read_bytes()
    before_rows, before_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    with browser(backend) as client:
        paths = ['/api/object/' + workflow, '/api/workflows/' + workflow + '/migration-context',
                 '/api/redesigns', '/api/proposals?scenario=original&workflow_id=' + workflow]
        for path in paths:
            response = client.get(path, headers=selected(project, package))
            assert response.status_code == 200, response.text
        assert client.get(paths[0], headers=selected(project, '0'*64)).status_code == 403
    downloads = [path for _, action, path in backend.calls if action == 'download']
    assert sum(backend.objects[path] == canonical for path in downloads) == 1
    # Each successful read still downloads a fresh committed generation manifest.
    assert len(downloads) >= len(paths) + 1
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