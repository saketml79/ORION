"""Duplicate XML references are reused only inside one authenticated restore."""
from hashlib import sha256

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from workbench.tests.test_delta_runtime import adapter, authority


def test_duplicate_sources_require_one_fresh_download_per_restore(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / 'writer')
    runtime.restore()
    content = b'<POWERMART><REPOSITORY NAME="R"/></POWERMART>'
    package = 'a' * 64
    source = runtime.local / 'packages' / package / 'source.xml'
    staged = runtime.local / 'projects' / ('b' * 32) / 'staged' / (package + '.xml')
    for path in (source, staged):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    digest = sha256(content).hexdigest()
    remote = runtime.publish_source(source, source.name, digest)
    runtime.flush()
    client = authority[0]
    download = client.files.download
    calls = []

    def observe(path):
        calls.append(path)
        return download(path)

    monkeypatch.setattr(client.files, 'download', observe)
    for name in ('first', 'second'):
        restored = adapter(authority, tmp_path / name)
        restored.restore()
        assert (restored.local / source.relative_to(runtime.local)).read_bytes() == content
        assert (restored.local / staged.relative_to(runtime.local)).read_bytes() == content
    assert calls.count(remote) == 2


@pytest.mark.parametrize('failure', ['missing', 'changed', 'denied'])
def test_new_restore_does_not_reuse_old_source_access(authority, tmp_path, monkeypatch, failure):
    runtime = adapter(authority, tmp_path / 'writer')
    runtime.restore()
    content = b'<POWERMART/>'
    source = runtime.local / 'packages' / ('a' * 64) / 'source.xml'
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    remote = runtime.publish_source(source, source.name, sha256(content).hexdigest())
    runtime.flush()
    adapter(authority, tmp_path / 'first').restore()
    client = authority[0]
    if failure == 'missing':
        del client.objects[remote]
    elif failure == 'changed':
        client.objects[remote] = b'x' * len(content)
    else:
        original = client.files.download
        def reject(path):
            if path == remote:
                raise PermissionDenied('source unavailable')
            return original(path)
        monkeypatch.setattr(client.files, 'download', reject)
    restored = adapter(authority, tmp_path / 'second')
    with pytest.raises((NotFound, PermissionDenied, ValueError)):
        restored.restore()
    assert not restored.ready
