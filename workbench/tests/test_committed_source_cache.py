"""Committed source reuse with fresh Files metadata, using only fake authority."""
from hashlib import sha256
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from workbench import delta_runtime as dr
from workbench.tests.delta_runtime_fakes import RuntimeStatementClient
from workbench.tests.test_hosted_working_snapshot import REMOTE, TOKEN, setup


@pytest.fixture
def sources(setup, monkeypatch):
    client, identity, runtime, writer, generation, snapshot = setup
    cache = dr.VerifiedSourceCache()
    monkeypatch.setattr(dr, "_COMMITTED_SOURCE_CACHE", cache)
    versions = {}
    metadata_calls, downloads = [], []
    download = client.files.download

    def metadata(path):
        metadata_calls.append(path)
        if path not in client.objects:
            raise NotFound("source missing")
        return SimpleNamespace(content_length=len(client.objects[path]),
                               last_modified=versions.get(path, "Wed, 18 Nov 2026 12:00:00 GMT"))

    def observed_download(path):
        downloads.append(path)
        return download(path)

    monkeypatch.setattr(client.files, "get_metadata", metadata, raising=False)
    monkeypatch.setattr(client.files, "download", observed_download)
    yield client, identity, runtime, writer, cache, versions, metadata_calls, downloads
    cache.clear()


def test_committed_source_is_reused_across_restore_flush_and_generation(sources):
    client, _, runtime, writer, _, _, metadata_calls, downloads = sources
    path = next(iter(writer.sources.values()))
    cold = runtime("source-cache-cold")
    assert cold.restore(initialize=False)
    assert cold.source_cache_ready and cold.source_cache_reason == "ready"
    assert downloads.count(path) == 1
    assert metadata_calls.count(path) >= 2

    downloads.clear()
    metadata_calls.clear()
    warm = runtime("source-cache-warm")
    assert warm.restore(initialize=False)
    assert warm.source_cache_ready and path not in downloads
    assert metadata_calls.count(path) >= 2
    assert warm.flush() != cold.generation
    assert path not in downloads
    again = runtime("source-cache-new-generation")
    assert again.restore(initialize=False) and again.source_cache_ready
    assert path not in downloads


@pytest.mark.parametrize("damage", ["missing", "denied", "changed"])
def test_source_revocation_missing_and_changed_metadata_do_not_reuse(sources, monkeypatch, damage):
    client, _, runtime, writer, _, versions, _, downloads = sources
    assert runtime("source-cache-before-damage").restore(initialize=False)
    path = next(iter(writer.sources.values()))
    downloads.clear()
    if damage == "missing":
        del client.objects[path]
    elif damage == "changed":
        client.objects[path] = b"x" * len(client.objects[path])
        versions[path] = "Thu, 19 Nov 2026 12:00:00 GMT"
    else:
        metadata = client.files.get_metadata
        download = client.files.download
        def denied_metadata(name):
            if name == path:
                raise PermissionDenied("revoked")
            return metadata(name)
        def denied_download(name):
            if name == path:
                raise PermissionDenied("revoked")
            return download(name)
        monkeypatch.setattr(client.files, "get_metadata", denied_metadata)
        monkeypatch.setattr(client.files, "download", denied_download)
    current = runtime("source-cache-after-damage")
    with pytest.raises((NotFound, PermissionDenied, ValueError)):
        current.restore(initialize=False)
    assert not current.ready and not list(current.local.iterdir())
    if damage == "changed":
        assert path not in downloads


@pytest.mark.parametrize("metadata_state", ["unsupported", "missing_fields", "post_unavailable"])
def test_unavailable_metadata_falls_back_to_download(sources, monkeypatch, metadata_state):
    client, _, runtime, writer, _, _, _, downloads = sources
    assert runtime("source-cache-before-unavailable").restore(initialize=False)
    path = next(iter(writer.sources.values()))
    downloads.clear()
    original = client.files.get_metadata
    calls = []
    def metadata(name):
        if name != path:
            return original(name)
        calls.append(name)
        if metadata_state == "missing_fields":
            return SimpleNamespace(content_length=len(client.objects[name]), last_modified=None)
        if metadata_state == "unsupported" or len(calls) == 2:
            raise RuntimeError("metadata unavailable")
        return original(name)
    monkeypatch.setattr(client.files, "get_metadata", metadata)
    current = runtime("source-cache-unavailable")
    assert current.restore(initialize=False)
    assert current.ready and not current.source_cache_ready
    assert current.source_cache_reason == "source_metadata_unavailable"
    assert downloads.count(path) == 1


@pytest.mark.parametrize("warm", [False, True])
def test_metadata_change_during_download_or_cache_copy_is_rejected(sources, monkeypatch, warm):
    client, _, runtime, writer, _, _, _, _ = sources
    if warm:
        assert runtime("source-cache-race-seed").restore(initialize=False)
    path = next(iter(writer.sources.values()))
    original = client.files.get_metadata
    calls = []
    def metadata(name):
        result = original(name)
        if name == path:
            calls.append(name)
            if len(calls) >= 2:
                result.last_modified = "Thu, 19 Nov 2026 12:00:00 GMT"
        return result
    monkeypatch.setattr(client.files, "get_metadata", metadata)
    current = runtime("source-cache-race")
    with pytest.raises(ValueError, match="metadata changed"):
        current.restore(initialize=False)
    assert not current.ready and not list(current.local.iterdir())


def test_current_credential_is_scanned_again_on_cache_hit(sources):
    _, _, runtime, _, cache, _, _, _ = sources
    assert runtime("source-cache-token-seed").restore(initialize=False)
    assert all(TOKEN.encode() not in entry[1].read_bytes() for entry in cache._entries.values())
    current = runtime("source-cache-new-token", token="verified")
    with pytest.raises(ValueError, match="Credential material"):
        current.restore(initialize=False)
    assert not current.ready


def test_source_larger_than_artifact_limit_is_not_rejected(sources, monkeypatch):
    _, _, runtime, writer, _, _, _, downloads = sources
    current = runtime("source-cache-large")
    assert current.restore(initialize=False)
    path = next(iter(writer.sources.values()))
    digest = next(iter(writer.sources))
    size = len((current.local / "packages" / ("a" * 64) / "source.xml").read_bytes())
    monkeypatch.setattr(dr, "MAX_BYTES", 2)
    downloads.clear()
    metadata, hit = current._committed_source({"volume_path": path, "sha256": digest,
                                               "byte_size": size, "source": True})
    assert size > dr.MAX_BYTES and metadata is not None and hit and path not in downloads


def test_source_cache_is_isolated_by_user(sources, tmp_path, monkeypatch):
    client, _, runtime, writer, cache, _, _, _ = sources
    assert runtime("source-cache-first-user").restore(initialize=False)
    path = next(iter(writer.sources.values()))
    content = client.objects[path]
    digest = sha256(content).hexdigest()
    other = RuntimeStatementClient()
    other.user = SimpleNamespace(id="other-user", user_name="other@example.com")
    other.catalog_owner = other.schema_owner = other.volume_owner = other.user.user_name
    other.objects = client.objects
    other_identity = SimpleNamespace(client=other, host=other.config.host, user_id=other.user.id)
    metadata_calls, downloads = [], []
    download = other.files.download
    def metadata(name):
        metadata_calls.append(name)
        if name not in other.objects:
            raise NotFound("source missing")
        return SimpleNamespace(content_length=len(other.objects[name]),
                               last_modified="Wed, 18 Nov 2026 12:00:00 GMT")
    def observed(name):
        downloads.append(name)
        return download(name)
    monkeypatch.setattr(other.files, "get_metadata", metadata, raising=False)
    monkeypatch.setattr(other.files, "download", observed)
    second = dr.DeltaRuntime(lambda: other_identity, REMOTE, tmp_path / "other-user",
                             warehouse="warehouse", forbidden_token=TOKEN)
    assert second.restore() is False
    source = second.local / "packages" / ("a" * 64) / "source.xml"
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    second.reference(digest, path)
    second.flush()
    assert downloads.count(path) >= 2  # New source and first committed-generation verification.
    assert metadata_calls.count(path) >= 2
    assert len(cache._entries) == 2


def test_existing_user_selected_volume_source_is_reused_after_commit(sources, monkeypatch):
    client, _, runtime, _, _, _, _, downloads = sources
    current = runtime("selected-source-writer")
    assert current.restore(initialize=False)
    path = "/Volumes/selected_catalog/selected_schema/selected_volume/folder/selected.xml"
    content = b"<POWERMART><REPOSITORY NAME='selected'/></POWERMART>"
    digest = sha256(content).hexdigest()
    client.objects[path] = content
    staged = current.local / "projects" / ("b" * 32) / "staged" / (digest + ".xml")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(content)
    current.reference(digest, path)
    events = []
    metadata, download = client.files.get_metadata, client.files.download
    def observe_metadata(name):
        if name == path:
            events.append("metadata")
        return metadata(name)
    def observe_download(name):
        if name == path:
            events.append("download")
        return download(name)
    monkeypatch.setattr(client.files, "get_metadata", observe_metadata)
    monkeypatch.setattr(client.files, "download", observe_download)
    current.flush()
    # A newly selected source still takes the original initial verification.
    assert events[0] == "download"
    downloads.clear()
    restored = runtime("selected-source-restored")
    assert restored.restore(initialize=False) and restored.source_cache_ready
    assert path not in downloads
    assert (restored.local / staged.relative_to(current.local)).read_bytes() == content


def test_corrupt_cached_source_is_not_used(sources):
    _, _, runtime, _, cache, _, _, _ = sources
    assert runtime("source-cache-corruption-seed").restore(initialize=False)
    entry = next(iter(cache._entries.values()))
    raw = entry[1].read_bytes()
    entry[1].write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    current = runtime("source-cache-corrupt")
    with pytest.raises(ValueError, match="checksum"):
        current.restore(initialize=False)
    assert not current.ready and not list(current.local.iterdir())
    assert not cache._entries


def test_cache_candidate_is_not_published_if_metadata_changes_after_copy(sources, monkeypatch):
    client, _, runtime, writer, cache, _, _, _ = sources
    path = next(iter(writer.sources.values()))
    original = client.files.get_metadata
    calls = []
    def metadata(name):
        result = original(name)
        if name == path:
            calls.append(name)
            if len(calls) == 4:
                result.last_modified = "Thu, 19 Nov 2026 12:00:00 GMT"
        return result
    monkeypatch.setattr(client.files, "get_metadata", metadata)
    current = runtime("source-cache-capture-race")
    with pytest.raises(ValueError, match="metadata changed"):
        current.restore(initialize=False)
    assert len(calls) == 4 and not cache._entries and not current.ready
