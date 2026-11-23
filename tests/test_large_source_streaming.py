"""Bounded source I/O contracts. No cloud calls or multi-GiB allocations."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from databricks.sdk.errors import AlreadyExists

from workbench import delta_runtime as dr, intake_limits as limits
from workbench.hosted_storage import CHUNK_BYTES, VolumeSnapshots
from workbench.tests.test_delta_runtime import authority, adapter


class BoundedReader(BytesIO):
    def read(self, size=-1):
        assert 0 < size <= CHUNK_BYTES, "Source read must be bounded"
        return super().read(size)


def bounded_downloads(client):
    original = client.files.download

    def download(path):
        response = original(path)
        if "/uploads/" in path:
            response.contents = BoundedReader(response.contents.getvalue())
        return response

    client.files.download = download


def source_file(runtime, raw):
    path = runtime.local / "packages/p/source.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path, sha256(raw).hexdigest()


def test_limit_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("ORION_MAX_XML_BYTES", raising=False)
    monkeypatch.delenv("ORION_MAX_BATCH_BYTES", raising=False)
    assert limits.max_xml_bytes() == 8 * limits.GIB
    assert limits.max_batch_bytes() == 32 * limits.GIB
    monkeypatch.setenv("ORION_MAX_XML_BYTES", str(32 * limits.GIB))
    assert limits.max_batch_bytes() == limits.max_xml_bytes()
    monkeypatch.setenv("ORION_MAX_BATCH_BYTES", "100")
    with pytest.raises(ValueError, match="at least"):
        limits.max_batch_bytes()


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "bad", " 12", str(32 * limits.GIB + 1)])
def test_invalid_limits_fail_closed(monkeypatch, value):
    monkeypatch.setenv("ORION_MAX_XML_BYTES", value)
    with pytest.raises(ValueError):
        limits.max_xml_bytes()


def test_publish_retry_and_readback_are_bounded(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path)
    bounded_downloads(authority[0])
    raw = b"<xml>" + b"x" * (2 * CHUNK_BYTES) + b"</xml>"
    path, digest = source_file(runtime, raw)
    monkeypatch.setattr(runtime, "_read", lambda *a: pytest.fail("No source materialization"))
    destination = runtime.publish_source(path, path.name, digest)
    assert authority[0].objects[destination] == raw
    runtime.sources.clear()

    def exists(*a, **kw):
        raise AlreadyExists("exists")

    monkeypatch.setattr(authority[0].files, "upload", exists)
    runtime._upload(destination, BoundedReader(raw))
    runtime.publish_source(path, path.name, digest)
    authority[0].objects[destination] = raw[:-1] + b"!"
    with pytest.raises(ValueError, match="checksum"):
        runtime._upload(destination, BoundedReader(raw))
    with pytest.raises(ValueError, match="checksum"):
        runtime.publish_source(path, path.name, digest)


@pytest.mark.parametrize("credential", [
    b"Bearer abcdefghijklmnop", b"dapi" + b"a" * 32,
    b"eyJ" + b"a" * 10 + b"." + b"b" * 10 + b"." + b"c" * 10,
    b"-----BEGIN PRIVATE KEY-----", b"forwarded-secret-exact-token",
    b"Bearer " + b" " * (CHUNK_BYTES + 100) + b"abcdefghijkl",
    b"eyJ" + b"a" * (CHUNK_BYTES + 100) + b"." + b"b" * 10 + b"." + b"c" * 10,
])
def test_split_credentials_rejected_before_upload(authority, tmp_path, credential):
    runtime = adapter(authority, tmp_path)
    runtime.forbidden = b"forwarded-secret-exact-token"
    raw = b"x" * (CHUNK_BYTES - 7) + credential + b"</xml>"
    path, digest = source_file(runtime, raw)
    before = dict(authority[0].objects)
    with pytest.raises(ValueError, match="Credential") as error:
        runtime.publish_source(path, path.name, digest)
    assert credential not in str(error.value).encode()
    assert authority[0].objects == before


def test_size_digest_and_corrupt_readback_fail_closed(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path)
    path, digest = source_file(runtime, b"<xml/>")
    with pytest.raises(ValueError, match="checksum"):
        runtime.publish_source(path, path.name, "a" * 64)
    monkeypatch.setenv("ORION_MAX_XML_BYTES", "3")
    with pytest.raises(ValueError, match="XML"):
        runtime.publish_source(path, path.name, digest)
    monkeypatch.setenv("ORION_MAX_XML_BYTES", "100")
    original = authority[0].files.upload

    def corrupt(destination, stream, **kw):
        original(destination, stream, **kw)
        authority[0].objects[destination] = b"broken"

    monkeypatch.setattr(authority[0].files, "upload", corrupt)
    with pytest.raises(ValueError, match="checksum"):
        runtime.publish_source(path, path.name, digest)
    assert runtime.sources == {}


def test_verify_streams_to_disk_and_cleans_failed_target(authority, tmp_path):
    runtime = adapter(authority, tmp_path)
    bounded_downloads(authority[0])
    path, digest = source_file(runtime, b"x" * (CHUNK_BYTES + 23))
    remote = runtime.publish_source(path, path.name, digest)
    reference = dict(volume_path=remote, sha256=digest, byte_size=path.stat().st_size, source=True)
    target = tmp_path / "restored.xml"
    runtime._verify_source(reference, target)
    assert target.stat().st_size == reference["byte_size"]
    with pytest.raises(ValueError, match="streaming"):
        runtime._object(reference)
    authority[0].objects[remote] = b"corrupt"
    with pytest.raises(ValueError, match="checksum"):
        runtime._verify_source(reference, target)
    assert not target.exists()


def test_source_excluded_from_metadata_budget_and_file_hook_is_ready(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path / "first")
    runtime.restore()
    bounded_downloads(authority[0])
    path, digest = source_file(runtime, b"x" * (CHUNK_BYTES + 1))
    runtime.publish_source(path, path.name, digest)
    monkeypatch.setattr(dr, "MAX_BYTES", 1024)
    original_read = Path.read_bytes

    def guarded_read(path):
        assert path.name != "source.xml", "Source must not use read_bytes"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    # Exercise the pending parent integration contract, without modifying ds.
    # A real build_records still needs the file-hook dispatch before read_bytes.
    original_build = dr.ds.build_records

    def integrated_build(store, snapshot, artifacts):
        records = original_build(store, dr.ds.Snapshot(snapshot.root, []), artifacts)
        for item in snapshot.files:
            ref = artifacts.put_file(snapshot.root / item["path"], item)
            records["artifact_manifest"].append(store.make_record(
                "artifact_manifest", **dr.ds._scope(item["path"]), source_path=item["path"],
                source_table="file", source_key=dr.ds._json(item["path"]), payload=ref,
                artifact_path=ref["volume_path"], artifact_sha256=ref["sha256"], byte_size=ref["byte_size"]))
        return records

    monkeypatch.setattr(dr.ds, "build_records", integrated_build)
    runtime.flush()
    restored = adapter(authority, tmp_path / "restored")
    restored.restore()
    assert (restored.local / path.relative_to(runtime.local)).stat().st_size == CHUNK_BYTES + 1


def test_legacy_snapshot_streams_sources_outside_metadata_budget(authority, tmp_path, monkeypatch):
    identity, remote = authority[3:]
    original_upload = authority[0].files.upload

    def legacy_upload(path, stream, *, overwrite=False):
        if overwrite and path.endswith("/HEAD.json"):
            authority[0].objects[path] = stream.read()
        else:
            original_upload(path, stream, overwrite=overwrite)

    monkeypatch.setattr(authority[0].files, "upload", legacy_upload)
    runtime = VolumeSnapshots(lambda: identity, remote, tmp_path / "first")
    bounded_downloads(authority[0])
    path, digest = source_file(runtime, b"x" * (CHUNK_BYTES + 1))
    runtime.publish_source(path, path.name, digest)
    from workbench import hosted_storage
    monkeypatch.setattr(hosted_storage, "MAX_BYTES", 4096)
    runtime.flush()
    restart = VolumeSnapshots(lambda: identity, remote, tmp_path / "restart")
    assert restart.restore()
    assert (restart.local / path.relative_to(runtime.local)).stat().st_size == CHUNK_BYTES + 1


def test_metadata_secret_checks_and_size_budget_remain(authority, tmp_path, monkeypatch):
    runtime = adapter(authority, tmp_path)
    destination = runtime.remote + "/metadata.json"
    with pytest.raises(ValueError, match="Credential"):
        runtime._upload(destination, BytesIO(b"Bearer abcdefghijklmnop"))
    monkeypatch.setattr(dr, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="storage limits"):
        runtime._upload(destination, BytesIO(b"12345"))
    assert destination not in authority[0].objects


def test_split_noncredential_is_not_rewritten_into_a_secret(authority, tmp_path):
    runtime = adapter(authority, tmp_path)
    raw = b"x" * (CHUNK_BYTES - 6) + b"Bearerabcdefghijkl"
    assert runtime._scan(BoundedReader(raw), len(raw)) == (sha256(raw).hexdigest(), len(raw))