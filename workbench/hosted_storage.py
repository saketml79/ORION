"""User-owned Unity Catalog provisioning and bounded Files/source helpers.

Hosted requests now use delta_runtime.DeltaRuntime. VolumeSnapshots is retained
only for explicit legacy/offline compatibility, NOT hosted application authority.
No SQLite connection ever opens a Volume path. Legacy publication is ordered:
blobs, immutable manifest, HEAD. An interrupted action can leave orphan blobs,
but cannot publish a manifest referencing incomplete uploads. Single app replica
and one action per user are required; this is deliberately not distributed CAS.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from databricks.sdk.errors import AlreadyExists, NotFound
from databricks.sdk.service.catalog import VolumeType

from workbench.volume_intake import _get, _relative, _root
from workbench.intake_limits import max_xml_bytes

HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_FILES = 20000
MAX_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def volume_path(value):
    if not isinstance(value, str) or not value.startswith("/Volumes/"):
        raise ValueError("An absolute Unity Catalog Volume file path is required")
    parts = value.split("/")
    if len(parts) < 6:
        raise ValueError("A Volume file, not a directory, is required")
    _root(*parts[2:5])
    _relative("/".join(parts[5:]))
    return value


def _owned(resource, username):
    # Exact principal ownership, never group membership or mere USE permission.
    return _get(resource, "owner") == username


def provision(identity, prefix, *, initialize=True):
    """Use readable username slug if owned, otherwise deterministic ID suffix.

    Re-read after create, including AlreadyExists races. Permission errors are
    never mistaken for absence. A conflict at the hashed name fails closed.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", prefix):
        raise ValueError("Unsafe catalog prefix")
    client = identity.client
    user = client.current_user.me()
    username = _get(user, "user_name", _get(user, "userName"))
    if _get(user, "id") != identity.user_id or not isinstance(username, str) or not username:
        raise ValueError("A resolved human username is required")
    slug = re.sub(r"[^a-z0-9_]+", "_", username.split("@", 1)[0].lower()).strip("_")[:48] or "user"
    namespace = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
    base = prefix + slug
    for candidate in (base, base + "_" + namespace[:16]):
        try:
            catalog = client.catalogs.get(candidate)
        except NotFound:
            if not initialize:
                continue
            try:
                client.catalogs.create(candidate, comment="User-owned workbench Delta metadata and Volume artifacts")
            except AlreadyExists:
                pass
            catalog = client.catalogs.get(candidate)
        if _owned(catalog, username):
            break
    else:
        raise PermissionError("User catalog ownership collision")
    schema_name = candidate + ".workbench"
    try:
        schema = client.schemas.get(schema_name)
    except NotFound:
        if not initialize:
            raise
        try:
            client.schemas.create("workbench", candidate)
        except AlreadyExists:
            pass
        schema = client.schemas.get(schema_name)
    if not _owned(schema, username):
        raise PermissionError("Workbench schema must be owned by the requesting user")
    full_name = schema_name + ".artifacts"
    try:
        volume = client.volumes.read(full_name)
    except NotFound:
        if not initialize:
            raise
        try:
            client.volumes.create(candidate, "workbench", "artifacts", VolumeType.MANAGED)
        except AlreadyExists:
            pass
        volume = client.volumes.read(full_name)
    if not _owned(volume, username):
        raise PermissionError("Artifact volume must be owned by the requesting user")
    return _root(candidate, "workbench", "artifacts") + "/users/" + namespace


class VolumeSnapshots:
    def __init__(self, identity_factory, remote_root, local_root, *, forbidden_token=""):
        self.identity_factory = identity_factory
        self.remote = volume_path(remote_root + "/HEAD.json").rsplit("/", 1)[0]
        self.local = Path(local_root).resolve()
        self.sources = {}
        self.forbidden = forbidden_token.encode() if forbidden_token else b""

    def _client(self):
        return self.identity_factory().client

    def _read(self, path, limit):
        response = self._client().files.download(volume_path(path))
        if response.contents is None:
            raise ValueError("Missing Volume contents")
        with closing(response.contents) as stream:
            data = stream.read(limit + 1)
        if not isinstance(data, bytes) or len(data) > limit:
            raise ValueError("Volume object exceeds snapshot limits")
        return data

    def _checked(self, data):
        if self.forbidden and self.forbidden in data:
            raise ValueError("Credential material cannot be persisted")
        return data

    def _scan(self, stream, limit, target=None):
        """Hash and validate bounded chunks, including split credential patterns."""
        digest, size = sha256(), 0
        tail = b""
        partials = []
        while chunk := stream.read(CHUNK_BYTES):
            if not isinstance(chunk, bytes):
                raise ValueError("Invalid Volume stream contents")
            size += len(chunk)
            if size > limit:
                raise ValueError("Source or artifact exceeds explicit storage limits")
            data = tail + chunk
            self._checked(data)
            # SECRET_BYTES has unbounded whitespace and JWT segment lengths.
            # Carry compact equivalent prefixes as well as ordinary overlap.
            candidates = [data, *(prefix + chunk for prefix in partials)]
            partials = []
            for candidate in candidates:
                self._checked(candidate)
                bearer = re.search(rb"Bearer\s+$", candidate, re.I)
                if bearer:
                    partials.append(b"Bearer ")
                jwt = re.search(rb"eyJ([A-Za-z0-9_-]*)(?:\.([A-Za-z0-9_-]*)(?:\.([A-Za-z0-9_-]*))?)?$", candidate, re.I)
                if jwt:
                    partials.append(b"eyJ" + b".".join(group[:10] for group in jwt.groups() if group is not None))
            partials = list(set(partials))
            tail = data[-max(512, len(self.forbidden)):]
            digest.update(chunk)
            if target is not None:
                target.write(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _source_upload(path):
        return re.search(r"/uploads/[a-f0-9]{64}\.xml\Z", path) is not None

    def _upload(self, path, stream, *, overwrite=False):
        # Defense in depth: no forwarded OAuth token can enter durable files.
        limit = max_xml_bytes() if self._source_upload(path) else MAX_BYTES
        expected = self._scan(stream, limit)
        stream.seek(0)
        self._client().files.create_directory(path.rsplit("/", 1)[0])
        try:
            self._client().files.upload(volume_path(path), stream, overwrite=overwrite)
        except AlreadyExists:
            if overwrite:
                raise
            # Immutable uploads may be retried, but never trust preexisting bytes.
            response = self._client().files.download(volume_path(path))
            if response.contents is None:
                raise ValueError("Missing Volume contents")
            with closing(response.contents) as current:
                actual = self._scan(current, limit)
            if actual != expected:
                raise ValueError("Immutable Volume artifact checksum mismatch") from None

    def reference(self, digest, path):
        if not HASH.fullmatch(digest):
            raise ValueError("Invalid source checksum")
        self.sources[digest] = volume_path(path)

    def publish_source(self, path, name, digest):
        if not HASH.fullmatch(digest):
            raise ValueError("Invalid source checksum")
        path = Path(path)
        if path.stat().st_size > max_xml_bytes():
            raise ValueError("Source exceeds explicit XML storage limits")
        with path.open("rb") as stream:
            checksum, size = self._scan(stream, max_xml_bytes())
        if checksum != digest:
            raise ValueError("Source checksum mismatch")
        destination = self.sources.get(digest, self.remote + "/uploads/" + digest + ".xml")
        if digest not in self.sources:
            with path.open("rb") as stream:
                self._upload(destination, stream)
        self._verify_source({"volume_path": destination, "sha256": digest, "byte_size": size, "source": True})
        self.sources[digest] = destination
        return destination

    def _verify_source(self, reference, target=None):
        digest, size = reference["sha256"], reference["byte_size"]
        if (not isinstance(digest, str) or not HASH.fullmatch(digest)
                or type(size) is not int or not 0 <= size <= max_xml_bytes()):
            raise ValueError("Invalid source size or checksum")
        response = self._client().files.download(volume_path(reference["volume_path"]))
        if response.contents is None:
            raise ValueError("Missing Volume contents")
        with closing(response.contents) as stream:
            if target is None:
                actual = self._scan(stream, size)
            else:
                target = Path(target)
                try:
                    with target.open("wb") as output:
                        actual = self._scan(stream, size, output)
                    if actual != (digest, size):
                        raise ValueError("Source size or checksum mismatch")
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        if actual != (digest, size):
            raise ValueError("Source size or checksum mismatch")
        return reference

    @staticmethod
    def _local_path(value):
        _relative(value)
        parts = value.split("/")
        if not value or parts[0] not in {"state", "agents", "projects", "models", "packages", "generated-drafts"}:
            raise ValueError("Invalid snapshot file path")
        if any(part.startswith(".") for part in parts):
            raise ValueError("Hidden snapshot paths are forbidden")
        if "generated-drafts" in parts:
            i = parts.index("generated-drafts")
            prefix = parts[:i]
            if prefix and not (len(prefix) == 4 and prefix[0] == "projects"
                               and re.fullmatch(r"[a-f0-9]{32}", prefix[1])
                               and prefix[2] == "package-contexts" and HASH.fullmatch(prefix[3])):
                raise ValueError("Invalid draft scope")
            tail = parts[i + 1:]
            if (len(tail) < 5 or not HASH.fullmatch(tail[0]) or not HASH.fullmatch(tail[1])
                    or not re.fullmatch(r"[1-9][0-9]*", tail[2])
                    or not re.fullmatch(r"[a-f0-9]{32}", tail[3])
                    or (prefix and prefix[3] != tail[0])):
                raise ValueError("Invalid draft identity")
            artifact = tail[4:]
            if artifact[0] == "native-bundles":
                if len(artifact) < 3 or not HASH.fullmatch(artifact[1]):
                    raise ValueError("Invalid draft bundle identity")
                artifact = artifact[2:]
                allowed = {"bundle-receipt.json", "migration-job.yaml"}
            else:
                allowed = {"manifest.json", "checkpoint_spark_codegen.sqlite"}
            if not (len(artifact) == 1 and artifact[0] in allowed):
                from workbench.spark_codegen import _path
                kinds = {"notebooks": "notebook", "modules": "module", "sql": "sql", "tests": "test"}
                if artifact[0] not in kinds:
                    raise ValueError("Invalid draft artifact")
                _path("/".join(artifact), kinds[artifact[0]])
        return value

    def restore(self):
        try:
            head = json.loads(self._read(self.remote + "/HEAD.json", 4096))
        except NotFound:
            return False
        digest = head.get("manifest")
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError("Invalid snapshot pointer")
        raw = self._read(self.remote + "/snapshots/" + digest + ".json", MAX_MANIFEST)
        if sha256(raw).hexdigest() != digest:
            raise ValueError("Snapshot manifest checksum mismatch")
        manifest = json.loads(raw)
        if (manifest.get("version") != 1 or manifest.get("root") != self.remote
                or not isinstance(manifest.get("files"), list) or len(manifest["files"]) > MAX_FILES):
            raise ValueError("Invalid snapshot manifest")
        sources = manifest.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("Invalid source references")
        for key, path in sources.items():
            self.reference(key, path)
        total, seen = 0, set()
        for record in manifest["files"]:
            name, size, checksum = record["path"], record["size"], record["sha256"]
            self._local_path(name)
            source = record.get("source") is True
            if (name in seen or type(size) is not int or size < 0 or not HASH.fullmatch(checksum)
                    or (source and size > max_xml_bytes())
                    or (not source and total + size > MAX_BYTES)):
                raise ValueError("Invalid snapshot file record")
            seen.add(name)
            total += 0 if source else size
            remote = (self.sources[checksum] if record.get("source") is True
                      else self.remote + "/blobs/" + checksum)
            target = self.local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source:
                self._verify_source({"volume_path": remote, "sha256": checksum, "byte_size": size}, target)
                continue
            data = self._read(remote, size)
            if len(data) != size or sha256(data).hexdigest() != checksum:
                raise ValueError("Snapshot file checksum mismatch; source may have changed")
            target.write_bytes(data)
        return True

    def flush(self):
        files, total = [], 0
        # Workers and runtime checkpoint connections must be closed by caller.
        for path in sorted(self.local.rglob("*")):
            if path.is_symlink():
                raise ValueError("Snapshot symlinks are forbidden")
            if not path.is_file() or path.name.endswith((".lock", "-wal", "-shm")):
                continue
            relative = path.relative_to(self.local).as_posix()
            if any(part.startswith(".") for part in relative.split("/")):
                continue
            self._local_path(relative)
            # Use SQLite backup, never copy a live journal/database pair.
            backup = None
            if path.suffix in {".sqlite", ".sqlite3"}:
                backup = self.local / (".backup-" + uuid4().hex)
                with closing(sqlite3.connect(path)) as db, closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
            try:
                source = backup or path
                size = source.stat().st_size
                reference = (path.name == "source.xml" or ("staged" in path.parts and path.suffix == ".xml"))
                total += 0 if reference else size
                if reference and size > max_xml_bytes():
                    raise ValueError("Source exceeds explicit XML storage limits")
                if total > MAX_BYTES or len(files) >= MAX_FILES:
                    raise ValueError("Hosted snapshot exceeds explicit storage limits")
                with source.open("rb") as stream:
                    checksum, actual_size = self._scan(stream, max_xml_bytes() if reference else MAX_BYTES)
                    if actual_size != size:
                        raise ValueError("Snapshot changed after capture")
                    stream.seek(0)
                    if reference:
                        if checksum not in self.sources:
                            raise ValueError("Source must be persisted before analysis")
                        self._verify_source({"volume_path": self.sources[checksum], "sha256": checksum, "byte_size": size})
                    else:
                        self._upload(self.remote + "/blobs/" + checksum, stream)
                files.append({"path": relative, "size": size, "sha256": checksum, "source": reference})
            finally:
                if backup is not None:
                    backup.unlink(missing_ok=True)
        manifest = json.dumps({"version": 1, "root": self.remote, "sources": self.sources, "files": files},
                              sort_keys=True, separators=(",", ":")).encode()
        if len(manifest) > MAX_MANIFEST:
            raise ValueError("Hosted manifest exceeds size limit")
        checksum = sha256(manifest).hexdigest()
        self._upload(self.remote + "/snapshots/" + checksum + ".json", io.BytesIO(manifest))
        self._upload(self.remote + "/HEAD.json", io.BytesIO(json.dumps({"manifest": checksum}).encode()), overwrite=True)
        return checksum