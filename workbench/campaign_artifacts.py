"""Campaign-specific bounds over the existing immutable Volume artifact store.

Local files are an equivalent owned-service backend, never a hosted fallback.
Metadata probes establish availability only, not a fresh full-content hash proof.
"""
from hashlib import sha256
import os
from pathlib import Path

from databricks.sdk.errors import NotFound

from workbench import delta_state as ds


MAX_REPORT_BYTES = 2 * 1024 * 1024


class LocalArtifacts:
    def __init__(self, root):
        if any(p.is_symlink() for p in (Path(root), *Path(root).parents)):
            raise PermissionError("Campaign artifact root symlinks forbidden")
        self.root = Path(root).resolve()

    def _path(self, digest):
        if not ds.re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid campaign artifact hash")
        path = self.root / "blobs" / digest
        if any(p.is_symlink() for p in (path, path.parent, self.root)):
            raise PermissionError("Campaign artifact symlinks forbidden")
        return str(path)

    def authorize(self):
        if self.root.is_symlink():
            raise PermissionError("Campaign artifact root changed")

    def put(self, raw):
        ds._safe(raw)
        self.authorize()
        digest = sha256(raw).hexdigest()
        path = Path(self._path(digest))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if self.get(digest, max_bytes=len(raw)) != raw:
                raise ValueError("Conflicting immutable campaign artifact") from None
        return {"volume_path": str(path), "sha256": digest, "byte_size": len(raw)}

    def get(self, digest, *, max_bytes):
        self.authorize()
        with Path(self._path(digest)).open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes or sha256(raw).hexdigest() != digest:
            raise ValueError("Campaign artifact size/hash mismatch")
        return raw

    def size(self, digest):
        self.authorize()
        return Path(self._path(digest)).stat().st_size


def validate_reference(artifacts, reference):
    if not isinstance(reference, dict) or set(reference) != {"volume_path", "sha256", "byte_size"}:
        raise ValueError("Campaign artifact reference unavailable")
    size = reference["byte_size"]
    if type(size) is not int or not 0 < size <= MAX_REPORT_BYTES:
        raise ValueError("Campaign artifact size outside bound")
    artifacts.authorize()
    if isinstance(artifacts, ds.VolumeArtifacts) and artifacts.root != ds.VolumeArtifacts(artifacts.store, artifacts.volume).root:
        raise PermissionError("Campaign artifact user namespace changed")
    if reference["volume_path"] != artifacts._path(reference["sha256"]):
        raise PermissionError("Campaign artifact owner path mismatch")
    return reference


def read_artifact(artifacts, reference):
    validate_reference(artifacts, reference)
    raw = artifacts.get(reference["sha256"], max_bytes=reference["byte_size"])
    if len(raw) != reference["byte_size"] or sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("Campaign artifact truncated or corrupt")
    ds._safe(raw)
    return raw


def artifact_available(artifacts, reference):
    """HEAD only. Unknown/missing metadata is unavailable, never partial success."""
    validate_reference(artifacts, reference)
    try:
        if isinstance(artifacts, LocalArtifacts):
            size = artifacts.size(reference["sha256"])
        else:
            metadata = artifacts.store.client.files.get_metadata(reference["volume_path"])
            size = ds._get(metadata, "content_length")
            if isinstance(size, str) and size.isdecimal():
                size = int(size)
        return type(size) is int and size == reference["byte_size"]
    except (NotFound, FileNotFoundError):
        return False