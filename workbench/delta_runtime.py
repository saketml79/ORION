"""Hosted authority: committed Delta generations and immutable Volume artifacts.

SQLite is an empty, disposable execution cache reconstructed from Delta source
columns. Database artifacts contain schema DDL, NEVER a database row snapshot.
Commit-last visibility requires one process and one action per user. It is not a
multi-table cloud transaction, distributed lock, or durable background worker.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing, contextmanager, nullcontext
from hashlib import sha256
import gc
from itertools import chain
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from threading import RLock
from uuid import uuid4

from databricks.sdk.errors import NotFound

from workbench import delta_state as ds
from workbench.hosted_storage import HASH, MAX_BYTES, MAX_FILES, VolumeSnapshots, volume_path
from workbench.hosted_session import VerifiedSourceCache, VerifiedWorkingSnapshot
from workbench.hosted_write_lock import serialize_delta_write
from workbench.intake_limits import max_xml_bytes

RUNTIME_SCOPE = dict(project_id="__runtime__", package_hash="__none__", context_id="hosted-runtime-v1")
AUDIT_SCOPE = dict(project_id="__runtime__", package_hash="__none__", context_id="hosted-http-audit-v1")
DATABASE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
_VERIFIED_ARTIFACT_CACHE = OrderedDict()
_VERIFIED_ARTIFACT_CACHE_LOCK = RLock()
_VERIFIED_ARTIFACT_CACHE_BYTES = 0
_VERIFIED_ARTIFACT_CACHE_LIMIT = 512 * 1024 * 1024
_VERIFIED_ARTIFACT_MEMORY_RESERVE = 1536 * 1024 * 1024
_WORKING_SNAPSHOT_CACHE = VerifiedWorkingSnapshot(max_bytes=MAX_BYTES)
_COMMITTED_SOURCE_CACHE = VerifiedSourceCache()
_GENERATION_RECORD_CACHE = OrderedDict()
_GENERATION_RECORD_CACHE_LOCK = RLock()
_GENERATION_RECORD_CACHE_LIMIT = 4
_GENERATION_RECORD_CACHE_MAX_BYTES = 64 * 1024 * 1024
_GENERATION_RECORD_CACHE_BYTES = 0


def _generation_record_key(store, remote, artifact_root, receipt):
    return (store.workspace_id, store.user_id, store.catalog, store.schema, remote, artifact_root,
            receipt["migration_id"], receipt["sequence"], receipt["content_hash"])


def _generation_records_cached(store, key):
    with _GENERATION_RECORD_CACHE_LOCK:
        value = _GENERATION_RECORD_CACHE.get(key)
        if value is None:
            return None
        _GENERATION_RECORD_CACHE.move_to_end(key)
        records, _, snapshot_key = value
    # The receipt is read fresh by the caller. Re-check current table ownership
    # and schema before exposing any retained row data to this request.
    username = store._authorize()
    for table in ds.TABLES:
        if table != "migration_receipts":
            store._verify_table(table, username)
    return records, snapshot_key


def _generation_records_publish(key, records, snapshot_key):
    global _GENERATION_RECORD_CACHE_BYTES
    encoded_size = sum(len(ds._json(rows).encode("utf-8")) for rows in records.values())
    if encoded_size > _GENERATION_RECORD_CACHE_MAX_BYTES:
        return False
    with _GENERATION_RECORD_CACHE_LOCK:
        previous = _GENERATION_RECORD_CACHE.pop(key, None)
        if previous is not None:
            _GENERATION_RECORD_CACHE_BYTES -= previous[1]
        # A newer commit for the same identity makes the older working set
        # unreachable. Keep no more than one generation per user namespace.
        for stale_key in tuple(_GENERATION_RECORD_CACHE):
            if stale_key[:6] == key[:6]:
                _, stale_size, _ = _GENERATION_RECORD_CACHE.pop(stale_key)
                _GENERATION_RECORD_CACHE_BYTES -= stale_size
        while (_GENERATION_RECORD_CACHE
               and (_GENERATION_RECORD_CACHE_BYTES + encoded_size > _GENERATION_RECORD_CACHE_MAX_BYTES
                    or len(_GENERATION_RECORD_CACHE) >= _GENERATION_RECORD_CACHE_LIMIT)):
            _, evicted = _GENERATION_RECORD_CACHE.popitem(last=False)
            _GENERATION_RECORD_CACHE_BYTES -= evicted[1]
        _GENERATION_RECORD_CACHE[key] = (records, encoded_size, snapshot_key)
        _GENERATION_RECORD_CACHE_BYTES += encoded_size
    return True


def _container_memory_state(*, cgroup_file=Path("/proc/self/cgroup"),
                            mountinfo_file=Path("/proc/self/mountinfo"),
                            cgroup_v2_root=Path("/sys/fs/cgroup"),
                            cgroup_v1_root=Path("/sys/fs/cgroup/memory")):
    """Return the tightest visible finite cgroup memory current/limit pair."""
    candidates = [(cgroup_v2_root, "memory.current", "memory.max", "v2"),
                  (cgroup_v1_root, "memory.usage_in_bytes", "memory.limit_in_bytes", "v1")]
    cgroups, mounts = [], []
    cgroup_readable = mountinfo_readable = False
    try:
        for line in cgroup_file.read_text(encoding="ascii").splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            _, controllers, group = parts
            if controllers and "memory" not in controllers.split(","):
                continue
            cgroups.append(("v1" if controllers else "v2", group))
        cgroup_readable = True
    except (OSError, ValueError):
        pass
    try:
        for line in mountinfo_file.read_text(encoding="ascii").splitlines():
            halves = line.split(" - ", 1)
            if len(halves) != 2:
                continue
            left, right = halves[0].split(), halves[1].split()
            if len(left) < 5 or len(right) < 3 or right[0] not in {"cgroup", "cgroup2"}:
                continue
            controller = "v2" if right[0] == "cgroup2" else "v1"
            if controller == "v1" and "memory" not in right[2].split(","):
                continue
            unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)
            mounts.append((controller, unescape(left[3]), Path(unescape(left[4]))))
        mountinfo_readable = True
    except (OSError, ValueError):
        pass

    for controller, group in cgroups:
        current_name, limit_name = (("memory.current", "memory.max") if controller == "v2"
                                    else ("memory.usage_in_bytes", "memory.limit_in_bytes"))
        matching_mounts = [mount for mount in mounts if mount[0] == controller]
        if not matching_mounts:
            root = cgroup_v2_root if controller == "v2" else cgroup_v1_root
            matching_mounts = [(controller, "/", root)]
        for _, mount_root, mount_point in matching_mounts:
            root_text = str(mount_root).replace("\\", "/").rstrip("/") or "/"
            group_text = str(group).replace("\\", "/")
            if group_text == root_text:
                relative = Path()
            elif root_text == "/" or group_text.startswith(root_text.rstrip("/") + "/"):
                relative = Path(group_text[len(root_text):].lstrip("/"))
            else:
                # Namespaced cgroup paths may be relative to the mounted subtree.
                relative = Path(group_text.lstrip("/"))
            candidate = (mount_point / relative).resolve()
            root = mount_point.resolve()
            if candidate != root and root not in candidate.parents:
                candidate = root
            while candidate == root or root in candidate.parents:
                candidates.append((candidate, current_name, limit_name, controller))
                if candidate == root:
                    break
                candidate = candidate.parent

    states = []
    seen = set()
    files_found = finite_limits_found = 0
    controllers_found = set()
    for base, current_name, limit_name, controller in candidates:
        current_path, limit_path = base / current_name, base / limit_name
        pair = (str(current_path), str(limit_path))
        if pair in seen:
            continue
        seen.add(pair)
        try:
            current = int(Path(current_path).read_text(encoding="ascii").strip())
            raw_limit = Path(limit_path).read_text(encoding="ascii").strip()
            files_found += 1
            if raw_limit == "max":
                continue
            limit = int(raw_limit)
            if 0 <= current <= limit < (1 << 60):
                states.append((current, limit))
                finite_limits_found += 1
                controllers_found.add(controller)
        except (OSError, ValueError):
            continue
    _LAST_CONTAINER_MEMORY_DIAGNOSTIC.clear()
    _LAST_CONTAINER_MEMORY_DIAGNOSTIC.update({
        "cgroup_file_readable": cgroup_readable,
        "mountinfo_file_readable": mountinfo_readable,
        "memory_controller_mounts": len(mounts),
        "memory_files_found": files_found,
        "finite_limits_found": finite_limits_found,
        "controllers": sorted(controllers_found),
    })
    return min(states, key=lambda value: value[1]) if states else None


_LAST_CONTAINER_MEMORY_DIAGNOSTIC = {}


def _artifact_cache_key(store, root, digest, size):
    return store.workspace_id, store.user_id, root, digest, size


def _artifact_cache_get(key):
    with _VERIFIED_ARTIFACT_CACHE_LOCK:
        raw = _VERIFIED_ARTIFACT_CACHE.get(key)
        if raw is not None:
            _VERIFIED_ARTIFACT_CACHE.move_to_end(key)
        return raw


def _artifact_cache_put(key, raw):
    global _VERIFIED_ARTIFACT_CACHE_BYTES
    size = len(raw)
    if not size or size > _VERIFIED_ARTIFACT_CACHE_LIMIT:
        return
    state = _container_memory_state()
    if state is not None and state[0] + size + _VERIFIED_ARTIFACT_MEMORY_RESERVE > state[1]:
        return
    with _VERIFIED_ARTIFACT_CACHE_LOCK:
        previous = _VERIFIED_ARTIFACT_CACHE.pop(key, None)
        if previous is not None:
            _VERIFIED_ARTIFACT_CACHE_BYTES -= len(previous)
        while (_VERIFIED_ARTIFACT_CACHE
               and _VERIFIED_ARTIFACT_CACHE_BYTES + size > _VERIFIED_ARTIFACT_CACHE_LIMIT):
            _, evicted = _VERIFIED_ARTIFACT_CACHE.popitem(last=False)
            _VERIFIED_ARTIFACT_CACHE_BYTES -= len(evicted)
        _VERIFIED_ARTIFACT_CACHE[key] = raw
        _VERIFIED_ARTIFACT_CACHE_BYTES += size


def _artifact_cache_clear_user(workspace_id, user_id):
    global _VERIFIED_ARTIFACT_CACHE_BYTES
    with _VERIFIED_ARTIFACT_CACHE_LOCK:
        for key in tuple(_VERIFIED_ARTIFACT_CACHE):
            if key[:2] == (workspace_id, user_id):
                _VERIFIED_ARTIFACT_CACHE_BYTES -= len(_VERIFIED_ARTIFACT_CACHE.pop(key))
    gc.collect()


class RequestStateStore(ds.DeltaStateStore):
    """Verification memoization for ONE freshly authenticated hosted action only.

    No client, principal, namespace or verification survives the dispatcher action.
    SQL/Files requests still undergo server-side OBO authorization individually.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._binding = (self.client, self.warehouse, self.workspace_id, self.user_id, self.catalog, self.schema)
        self._username = None
        self._verified = set()

    def _authorize(self):
        if (self._binding != (self.client, self.warehouse, self.workspace_id, self.user_id, self.catalog, self.schema)
                or self.client.config.host.rstrip("/").lower() != self.workspace_id):
            raise PermissionError("Request store identity changed")
        if self._username is None:
            self._username = super()._authorize()
        return self._username

    def _verify_table(self, table, username):
        if username != self._authorize():
            raise PermissionError("Request table owner changed")
        if table not in self._verified:
            super()._verify_table(table, username)
            self._verified.add(table)

    def migration_counts(self, tables, generation):
        username = self._authorize()
        branches = []
        for table in tables:
            self._verify_table(table, username)
            # Table labels come only from the fixed ds.TABLES allowlist.
            branches.append(f"SELECT '{table}' AS table_name, count(*) AS row_count FROM {self._name(table)} "
                            "WHERE workspace_id=:workspace_id AND user_id=:user_id AND migration_id=:migration_id")
        rows = self._execute(" UNION ALL ".join(branches), {
            "workspace_id": self.workspace_id, "user_id": self.user_id, "migration_id": ds._text(generation)})
        counts = {name: int(count) for name, count in rows}
        if len(rows) != len(tables) or set(counts) != set(tables) or any(v < 0 for v in counts.values()):
            raise ValueError("Invalid generation counts")
        return counts

    def generation_page(self, table, generation, after=None):
        """Read one bounded page across this user's scopes in a generation."""
        username = self._authorize()
        self._verify_table(table, username)
        values = {"workspace_id": self.workspace_id, "user_id": self.user_id,
                  "migration_id": ds._text(generation)}
        where = " AND ".join(ds._identifier(k) + "=:" + k for k in values)
        if after is not None:
            values.update(cursor_id=ds._text(after[0]), cursor_hash=ds._text(after[1]),
                          cursor_migration=ds._text(after[2]))
            where += " AND (record_id,content_hash,migration_id) > (:cursor_id,:cursor_hash,:cursor_migration)"
        names = ",".join(ds._identifier(k) for k in ds.COLUMNS)
        rows = self._execute(
            f"SELECT {names} FROM {self._name(table)} WHERE {where} "
            "ORDER BY record_id,content_hash,migration_id LIMIT 50", values)
        return [{k: int(v) if ds.COLUMNS[k] == "BIGINT" else v
                 for k, v in zip(ds.COLUMNS, row, strict=True)} for row in rows]


class RequestArtifacts(ds.VolumeArtifacts):
    def __init__(self, store, volume):
        super().__init__(store, volume)
        self._verified_binding = None
        self.memory_cache_hits = 0

    def authorize(self):
        self.store._authorize()
        binding = (self.store.client, self.store.workspace_id, self.store.user_id,
                   self.store.catalog, self.store.schema, self.volume, self.root)
        if self._verified_binding is None:
            super().authorize()
            self._verified_binding = binding
        elif binding != self._verified_binding:
            raise PermissionError("Request artifact namespace changed")

    def get(self, digest, *, max_bytes=1024 * 1024 * 1024):
        """Fetch only on a cold content key; authenticate this request before cache reuse."""
        self.authorize()
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid artifact checksum")
        key = _artifact_cache_key(self.store, self.root, digest, max_bytes)
        raw = _artifact_cache_get(key)
        if raw is not None:
            if len(raw) > max_bytes or sha256(raw).hexdigest() != digest:
                _artifact_cache_clear_user(self.store.workspace_id, self.store.user_id)
                raise ValueError("Cached immutable artifact checksum mismatch")
            self.memory_cache_hits += 1
            return raw
        raw = super().get(digest, max_bytes=max_bytes)
        _artifact_cache_put(key, raw)
        return raw


def _quote(name):
    # Source column names are not Delta identifiers (checkpoint schemas vary).
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("Invalid SQLite source identifier")
    return '"' + name.replace('"', '""') + '"'


class DeltaRuntime(VolumeSnapshots):
    """Reuse only bounded Files/source publication helpers, not snapshot methods."""

    def __init__(self, identity_factory, remote_root, local_root, *, warehouse,
                 forbidden_token="", store=None, artifacts=None):
        super().__init__(identity_factory, remote_root, local_root, forbidden_token=forbidden_token)
        identity = identity_factory()
        parts = remote_root.split("/")
        self.store = store or RequestStateStore(identity.client, warehouse, parts[2], parts[3], identity.user_id)
        if self.store.user_id != identity.user_id or self.store.workspace_id != identity.host:
            raise PermissionError("Runtime store identity mismatch")
        self.artifacts = artifacts or RequestArtifacts(self.store, parts[4])
        self.generation, self.sequence = None, 0
        self.receipt_marker = None
        self.ready = False
        self._materialized = False
        self._cache_only = False
        self.working_snapshot_cache_hit = False
        self.working_snapshot_cache_ready = False
        self.working_snapshot_cache_reason = "not_attempted"
        self.working_snapshot_memory_diagnostic = {}
        self.source_cache_ready = False
        self.source_cache_reason = "not_attempted"
        self._committed_sources = set()
        self._audit_sequences = {}
        self._snapshot_objects = None
        self._snapshot_capture = None
        self._snapshot_capture_bytes = 0

    def _checked(self, data):
        ds._safe(data)
        if self.forbidden and self.forbidden in data:
            raise ValueError("Credential material cannot be persisted")
        return data

    def _source_key(self, reference):
        digest, size = reference["sha256"], reference["byte_size"]
        if (not isinstance(digest, str) or not HASH.fullmatch(digest)
                or type(size) is not int or not 0 <= size <= max_xml_bytes()):
            raise ValueError("Invalid source size or checksum")
        path = volume_path(reference["volume_path"])
        return sha256(ds._json([self.store.workspace_id, self.store.user_id, self.remote,
                                self.artifacts.root, path, digest, size]).encode()).hexdigest()

    def _source_metadata(self, path):
        """Fresh OBO metadata, or None when metadata cannot safely validate reuse."""
        identity = self.identity_factory()
        if identity.host != self.store.workspace_id or identity.user_id != self.store.user_id:
            raise PermissionError("Runtime source identity mismatch")
        try:
            metadata = identity.client.files.get_metadata(volume_path(path))
        except Exception:
            # The original download remains the authority when HEAD is unavailable,
            # including denied/missing metadata. Never use cached bytes in that case.
            return None
        size = ds._get(metadata, "content_length")
        modified = ds._get(metadata, "last_modified")
        if (type(size) is not int or size < 0 or not isinstance(modified, str)
                or not modified or len(modified) > 256):
            return None
        encoded = modified.encode("utf-8")
        self._checked(encoded)
        return size, sha256(encoded).hexdigest()

    def _committed_source(self, reference, target=None):
        """Verify a source already named by the current committed generation."""
        key = self._source_key(reference)
        path, digest, size = reference["volume_path"], reference["sha256"], reference["byte_size"]
        if not self.forbidden:
            self.source_cache_ready = False
            self.source_cache_reason = "current_credential_unavailable"
            self._verify_source(reference, target)
            return None, False
        before = self._source_metadata(path)
        if before is None:
            _COMMITTED_SOURCE_CACHE.discard(key)
            self.source_cache_ready = False
            self.source_cache_reason = "source_metadata_unavailable"
            self._verify_source(reference, target)
            return None, False
        if before[0] != size:
            _COMMITTED_SOURCE_CACHE.discard(key)
            self.source_cache_ready = False
            self.source_cache_reason = "source_metadata_changed"
            raise ValueError("Committed source size changed")
        try:
            hit = _COMMITTED_SOURCE_CACHE.copy(key, before, digest, size, self._scan, target)
        except Exception:
            self.source_cache_ready = False
            self.source_cache_reason = "source_cache_verification_failed"
            raise
        if not hit:
            self.source_cache_ready = False
            if self.source_cache_reason != "source_metadata_unavailable":
                self.source_cache_reason = "source_cache_miss"
            self._verify_source(reference, target)
        after = self._source_metadata(path)
        if after is None:
            _COMMITTED_SOURCE_CACHE.discard(key)
            self.source_cache_ready = False
            self.source_cache_reason = "source_metadata_unavailable"
            if hit:
                if target is not None:
                    Path(target).unlink(missing_ok=True)
                self._verify_source(reference, target)
            return None, False
        if before != after:
            _COMMITTED_SOURCE_CACHE.discard(key)
            self.source_cache_ready = False
            self.source_cache_reason = "source_metadata_changed"
            if target is not None:
                Path(target).unlink(missing_ok=True)
            raise ValueError("Committed source metadata changed during verification")
        return after, hit

    def materialize_committed_source(self, reference, target):
        """Materialize a verified committed source and optionally retain its scanned copy."""
        metadata, hit = self._committed_source(reference, target)
        if metadata is None or hit:
            return
        key = self._source_key(reference)

        def verify_capture():
            current = self._source_metadata(reference["volume_path"])
            if current is None:
                _COMMITTED_SOURCE_CACHE.discard(key)
                return False
            if current != metadata:
                _COMMITTED_SOURCE_CACHE.discard(key)
                raise ValueError("Committed source metadata changed during cache capture")
            return True

        _COMMITTED_SOURCE_CACHE.put(key, metadata, target, reference["sha256"],
                                    reference["byte_size"], self._scan, verify_capture)

    def _upload(self, path, stream, *, overwrite=False):
        # Source publication happens before parsing, so enforce the same secret
        # checks there as on generation artifacts, not just at final flush.
        if self._source_upload(path):
            # Base scanner dispatches to _checked on every overlapping chunk.
            return super()._upload(path, stream, overwrite=overwrite)
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Hosted artifact exceeds storage limits")
        self._checked(raw)
        stream.seek(0)
        return super()._upload(path, stream, overwrite=overwrite)

    def _latest(self):
        username = self.store._authorize()
        self.store._verify_table("migration_receipts", username)
        values = {"workspace_id": self.store.workspace_id, "user_id": self.store.user_id,
                  **RUNTIME_SCOPE, "status": "committed"}
        where = " AND ".join(ds._identifier(k) + "=:" + k for k in values)
        names = ",".join(ds._identifier(k) for k in ds.COLUMNS)
        rows = self.store._execute(
            f"SELECT {names} FROM {self.store._name('migration_receipts')} WHERE {where} "
            "ORDER BY sequence DESC LIMIT 2", values)
        decoded = [{k: int(v) if ds.COLUMNS[k] == "BIGINT" else v
                    for k, v in zip(ds.COLUMNS, row, strict=True)} for row in rows]
        for row in decoded:
            if (row["content_hash"] != ds._content_hash(row) or row["sequence"] < 1
                    or any(row[k] != value for k, value in values.items())):
                raise ValueError("Invalid committed generation receipt")
        if len(decoded) > 1 and decoded[0]["sequence"] == decoded[1]["sequence"]:
            raise ValueError("Overlapping hosted writers; generation is ambiguous")
        return decoded[0] if decoded else None

    def session_authority(self):
        """Freshly validate the committed generation and every owned namespace table.

        This intentionally does not hydrate files or build an index. The receipt,
        manifest, table ownership, and Volume ownership are still checked with the
        current request's OBO-bound store and artifact adapter.
        """
        receipt = self._latest()
        if receipt is None:
            try:
                self._read(self.remote + "/HEAD.json", 4096)
            except NotFound:
                self.artifacts.authorize()
                return None, None
            raise ValueError("Legacy snapshot exists; explicit Delta import required")
        payload_raw = receipt["payload"]
        if not isinstance(payload_raw, str):
            raise ValueError("Invalid committed generation receipt payload")
        payload = json.loads(payload_raw)
        reference = payload.get("manifest")
        if (not isinstance(reference, dict) or set(reference) != {"volume_path", "sha256", "byte_size"}
                or reference["volume_path"] != self.artifacts._path(reference["sha256"])):
            raise ValueError("Invalid committed generation manifest reference")
        manifest = json.loads(self._object(reference))
        generation = receipt["migration_id"]
        if (manifest.get("format") != 1 or manifest.get("generation") != generation
                or manifest.get("workspace_id") != self.store.workspace_id
                or manifest.get("user_id") != self.store.user_id
                or manifest.get("sequence") != receipt["sequence"]
                or not isinstance(manifest.get("inventory"), dict)
                or set(manifest["inventory"]) != set(ds.TABLES) - {"migration_receipts"}):
            raise PermissionError("Invalid generation namespace")
        username = self.store._authorize()
        for table in ds.TABLES:
            self.store._verify_table(table, username)
        self.artifacts.authorize()
        marker = (receipt["migration_id"], receipt["sequence"], receipt["content_hash"],
                  reference["sha256"])
        return marker, manifest

    def _object(self, reference):
        digest, size = reference["sha256"], reference["byte_size"]
        if reference.get("source") is True:
            raise ValueError("Source artifacts require streaming verification")
        if type(size) is not int or not 0 <= size <= MAX_BYTES:
            raise ValueError("Invalid artifact size")
        # Source references can be in another user-selected Volume. All other
        # references are resolved by digest within this exact user's namespace.
        if reference["volume_path"] != self.artifacts._path(digest):
            raise PermissionError("Cross-namespace artifact reference")
        objects = self._snapshot_objects
        if objects is not None and digest in objects:
            self.artifacts.authorize()
            raw = objects[digest]
        else:
            # Reuse the independently bounded authorized artifact cache; if
            # its entry was evicted, perform the ordinary verified Volume read.
            raw = self.artifacts.get(digest, max_bytes=size)
        if len(raw) != size or sha256(raw).hexdigest() != digest:
            raise ValueError("Artifact size or checksum mismatch")
        self._checked(raw)
        return raw

    def _decode(self, value):
        if isinstance(value, dict):
            if set(value) == {"$artifact", "encoding"}:
                raw = self._object(value["$artifact"])
                encoding = value["encoding"]
                if encoding == "binary":
                    return raw
                if encoding == "utf-8":
                    return raw.decode("utf-8")
                if encoding == "json":
                    return self._decode(json.loads(raw))
                raise ValueError("Unknown metadata encoding")
            return {k: self._decode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._decode(v) for v in value]
        return value

    def _records(self, inventory, generation):
        if set(inventory) != set(ds.TABLES) - {"migration_receipts"}:
            raise ValueError("Incomplete generation table inventory")
        counts = (self.store.migration_counts(list(inventory), generation)
                  if isinstance(self.store, RequestStateStore) else None)
        result = {table: [] for table in inventory}
        for table, expected in inventory.items():
            if counts is not None:
                if counts[table] != len(expected):
                    raise ValueError("Incomplete or unexpected generation records")
                for entry in expected:
                    for field in ("project_id", "package_hash", "context_id"):
                        ds._text(entry[field])
                identity = lambda row: (row["project_id"], row["package_hash"], row["context_id"],
                                        row["record_id"], row["content_hash"])
                wanted = sorted(identity(row) for row in expected)
                cursor = None
                while len(result[table]) < len(expected):
                    page = self.store.generation_page(table, generation, after=cursor)
                    if not page or len(page) > 50:
                        raise ValueError("Incomplete or unexpected generation records")
                    for row in page:
                        if (row["workspace_id"] != self.store.workspace_id
                                or row["user_id"] != self.store.user_id
                                or row["migration_id"] != generation
                                or row["content_hash"] != ds._content_hash(row)):
                            raise ValueError("Invalid generation row envelope or checksum")
                        key = (row["record_id"], row["content_hash"], row["migration_id"])
                        if cursor is not None and key <= cursor:
                            raise ValueError("Non-advancing generation cursor")
                        cursor = key
                        result[table].append(row)
                    if len(result[table]) > len(expected):
                        raise ValueError("Incomplete or unexpected generation records")
                if sorted(identity(row) for row in result[table]) != wanted:
                    raise ValueError("Incomplete or unexpected generation records")
                # Preserve the original scope-first, per-scope keyset order.
                result[table].sort(key=lambda row: (row["project_id"], row["package_hash"],
                                                    row["context_id"], row["record_id"],
                                                    row["content_hash"], row["migration_id"]))
                continue
            scopes = {(r["project_id"], r["package_hash"], r["context_id"]) for r in expected}
            for project, package, context in sorted(scopes):
                cursor = None
                while True:
                    page = self.store.read_table(table, project_id=project, package_hash=package,
                                                 context_id=context, migration_id=generation, after=cursor)
                    for row in page:
                        if row["content_hash"] != ds._content_hash(row):
                            raise ValueError("Delta row checksum mismatch")
                    result[table].extend(page)
                    if len(page) < 50:
                        break
                    last = page[-1]
                    next_cursor = (last["record_id"], last["content_hash"], last["migration_id"])
                    if cursor is not None and next_cursor <= cursor:
                        raise ValueError("Non-advancing generation cursor")
                    cursor = next_cursor
            pairs = lambda rows: sorted((r["record_id"], r["content_hash"]) for r in rows)
            count = counts[table] if counts is not None else self.store.migration_count(table, generation)
            if pairs(result[table]) != pairs(expected) or count != len(expected):
                raise ValueError("Incomplete or unexpected generation records")
        return result

    def _audit_files(self):
        return sorted(self.local.rglob("audit.sqlite"))

    @staticmethod
    def _audit_schema(path):
        # Explicitly close connections: sqlite3's transaction context alone does
        # not close its handle, which prevents private-cache cleanup on Windows.
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS audit_events(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
            """)

    @staticmethod
    def _audit_rows(path):
        with closing(sqlite3.connect(path)) as db:
            rows = db.execute("SELECT sequence,payload,previous_hash,event_hash FROM audit_events ORDER BY sequence").fetchall()
        previous = "0" * 64
        for expected, (sequence, payload, parent, digest) in enumerate(rows, 1):
            if (sequence != expected or parent != previous
                    or sha256((parent + payload).encode()).hexdigest() != digest):
                raise ValueError("Invalid hosted audit chain")
            previous = digest
        return rows

    def restore_audit(self):
        """Overlay only durable HTTP audit tails, never application content.

        A later content generation includes these exact SQLite audit columns.
        Tail predicates avoid replaying already-checkpointed journal history.
        """
        if self._cache_only:
            raise ValueError("A cache-only restore cannot be used for audit")
        baselines = {}
        for path in self._audit_files():
            with closing(sqlite3.connect(path)) as db:
                baselines[path.relative_to(self.local).as_posix()] = db.execute(
                    "SELECT coalesce(max(sequence),0) FROM audit_events").fetchone()[0]
        username = self.store._authorize()
        self.store._verify_table("audit_events", username)
        values = {"workspace_id": self.store.workspace_id, "user_id": self.store.user_id,
                  **AUDIT_SCOPE, "migration_id": AUDIT_SCOPE["context_id"]}
        where = " AND ".join(ds._identifier(k) + "=:" + k for k in values)
        tails = []
        for index, (name, sequence) in enumerate(baselines.items()):
            values[f"path{index}"] = name
            values[f"seq{index}"] = sequence
            tails.append(f"(source_path=:path{index} AND sequence>CAST(:seq{index} AS BIGINT))")
        if baselines:
            absent = "source_path NOT IN (" + ",".join(f":path{i}" for i in range(len(baselines))) + ")"
            where += " AND (" + " OR ".join([absent, *tails]) + ")"
        names = ",".join(ds._identifier(k) for k in ds.COLUMNS)
        cursor = None
        while True:
            paging = ""
            if cursor is not None:
                values.update(cursor_path=cursor[0], cursor_sequence=cursor[1], cursor_id=cursor[2])
                paging = " AND (source_path,sequence,record_id) > (:cursor_path,CAST(:cursor_sequence AS BIGINT),:cursor_id)"
            rows = self.store._execute(
                f"SELECT {names} FROM {self.store._name('audit_events')} WHERE {where}{paging} "
                "ORDER BY source_path,sequence,record_id LIMIT 50 /* hosted audit tail */", values)
            for raw in rows:
                row = {k: int(v) if ds.COLUMNS[k] == "BIGINT" else v
                       for k, v in zip(ds.COLUMNS, raw, strict=True)}
                if (row["content_hash"] != ds._content_hash(row)
                        or any(row[k] != values[k] for k in ("workspace_id", "user_id", *AUDIT_SCOPE, "migration_id"))):
                    raise ValueError("Invalid hosted audit journal envelope")
                name = self._local_path(row["source_path"])
                if Path(name).name != "audit.sqlite" or row["source_table"] != "audit_events":
                    raise ValueError("Invalid hosted audit source")
                path = self.local / name
                path.parent.mkdir(parents=True, exist_ok=True)
                self._audit_schema(path)
                payload = json.loads(row["payload"])
                if set(payload) != {"sequence", "payload", "previous_hash", "event_hash"}:
                    raise ValueError("Invalid hosted audit columns")
                with closing(sqlite3.connect(path)) as db, db:
                    last = db.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
                    sequence, previous = (last[0] + 1, last[1]) if last else (1, "0" * 64)
                    if (payload["sequence"] != sequence or row["sequence"] != sequence
                            or payload["previous_hash"] != previous
                            or sha256((previous + payload["payload"]).encode()).hexdigest() != payload["event_hash"]):
                        raise ValueError("Hosted audit chain mismatch")
                    db.execute("INSERT INTO audit_events VALUES(?,?,?,?)",
                               [payload[k] for k in ("sequence", "payload", "previous_hash", "event_hash")])
            if len(rows) < 50:
                break
            next_cursor = (row["source_path"], row["sequence"], row["record_id"])
            if cursor is not None and next_cursor <= cursor:
                raise ValueError("Non-advancing audit cursor")
            cursor = next_cursor
        for path in self._audit_files():
            self._audit_rows(path)
            with closing(sqlite3.connect(path)) as db:
                self._audit_sequences[path.relative_to(self.local).as_posix()] = db.execute(
                    "SELECT coalesce(max(sequence),0) FROM audit_events").fetchone()[0]

    def flush_audit(self):
        """Acknowledge observational reads only after exact audit-tail readback.

        No artifact upload, content rows, or committed-generation receipt is written.
        A failed/ambiguous journal append fails the request closed.
        """
        if not self.ready or not self._materialized:
            raise ValueError("Materialized authoritative restore must succeed before audit commit")
        records = []
        for path in self._audit_files():
            name = self._local_path(path.relative_to(self.local).as_posix())
            self._audit_rows(path)
            with closing(sqlite3.connect(path)) as db:
                for sequence, payload, previous, digest in db.execute(
                        "SELECT sequence,payload,previous_hash,event_hash FROM audit_events WHERE sequence>? ORDER BY sequence",
                        (self._audit_sequences.get(name, 0),)):
                    record = self.store.make_record(
                        "audit_events", **AUDIT_SCOPE, source_path=name, source_table="audit_events",
                        source_key=str(sequence), sequence=sequence,
                        payload={"sequence": sequence, "payload": payload, "previous_hash": previous, "event_hash": digest})
                    self._checked(ds._json(record).encode())
                    records.append(record)
        if records:
            serialize_delta_write(self.store.upsert_records, "audit_events", records,
                                  migration_id=AUDIT_SCOPE["context_id"])
            # Reconstruct the durable tail into a disposable cache for independent
            # checksum/chain verification without trusting the submitted objects.
            with tempfile.TemporaryDirectory(prefix="workbench-audit-verify-") as temporary:
                verify = DeltaRuntime(self.identity_factory, self.remote, temporary, warehouse=self.store.warehouse,
                                      store=self.store, artifacts=self.artifacts)
                # The committed content generation can contain audit prefixes
                # older than this journal. Seed only that already-verified prefix.
                for path in self._audit_files():
                    name = path.relative_to(self.local).as_posix()
                    other = Path(temporary) / name
                    other.parent.mkdir(parents=True, exist_ok=True)
                    self._audit_schema(other)
                    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(other)) as target, target:
                        target.executemany("INSERT INTO audit_events VALUES(?,?,?,?)", source.execute(
                            "SELECT * FROM audit_events WHERE sequence<=? ORDER BY sequence",
                            (self._audit_sequences.get(name, 0),)))
                verify.restore_audit()
                for path in self._audit_files():
                    other = Path(temporary) / path.relative_to(self.local)
                    if not other.exists():
                        raise ValueError("Hosted audit publication is unconfirmed")
                    with closing(sqlite3.connect(path)) as expected, closing(sqlite3.connect(other)) as actual:
                        wanted = expected.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
                        got = actual.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
                        if got != wanted:
                            raise ValueError("Hosted audit publication is unconfirmed")

    def restore(self, *, initialize=True, progress=None, materialize=True):
        """Restore authority, initializing missing Delta tables on mutations only.

        Hosted observational requests pass initialize=False. They still authorize
        this fresh user's namespace, verify each table actually used and execute
        real OBO SQL. An empty generation needs only receipts and the audit journal,
        not schema checks for the other nineteen metadata families. No schema or
        privilege verdict is cached across requests; missing tables fail closed.
        Cache-only preparation verifies and admits the same snapshot without
        copying it to the request directory; it cannot be used for writes.
        """
        if type(materialize) is not bool or (not materialize and initialize):
            raise ValueError("Cache-only restore requires a read-only request")
        self.ready = False
        self._materialized = False
        self._cache_only = not materialize
        self.working_snapshot_cache_hit = False
        self.working_snapshot_cache_ready = False
        self.working_snapshot_cache_reason = "not_attempted"
        self.working_snapshot_memory_diagnostic = {}
        self.source_cache_ready = False
        self.source_cache_reason = "not_attempted"
        self._committed_sources = set()
        self.sources = {}
        if progress is not None:
            progress("metadata_restore")
        self.local.mkdir(parents=True, exist_ok=True)
        if any(self.local.iterdir()):
            raise ValueError("Hosted cache must be empty; local fallback is forbidden")
        if initialize:
            self.store.ensure_tables()
        receipt = self._latest()
        if receipt is None:
            # Never mistake an old snapshot estate for a new empty user. Moving
            # old estates requires an explicitly reviewed import, not backup restore.
            try:
                self._read(self.remote + "/HEAD.json", 4096)
            except NotFound:
                if progress is not None:
                    progress("artifact_files", verified_files=0, total_files=0)
                self.receipt_marker = None
                self.ready = True
                self._materialized = materialize
                return False
            raise ValueError("Legacy snapshot exists; explicit Delta import required")
        payload = json.loads(receipt["payload"])
        manifest = json.loads(self._object(payload["manifest"]))
        generation = receipt["migration_id"]
        if (manifest.get("format") != 1 or manifest.get("generation") != generation
                or manifest.get("workspace_id") != self.store.workspace_id
                or manifest.get("user_id") != self.store.user_id
                or manifest.get("sequence") != receipt["sequence"]):
            raise PermissionError("Invalid generation namespace")
        self.receipt_marker = (receipt["migration_id"], receipt["sequence"], receipt["content_hash"],
                               payload["manifest"]["sha256"])
        generation_key = _generation_record_key(self.store, self.remote, self.artifacts.root, receipt)
        retained = _generation_records_cached(self.store, generation_key)
        if retained is None:
            records = self._records(manifest["inventory"], generation)
            cache_key = self._snapshot_key(receipt, payload["manifest"], records)
        else:
            records, cache_key = retained
        try:
            result = self._hydrate_generation(records, generation, receipt["sequence"], cache_key, progress,
                                              materialize=materialize)
            self._materialized = materialize
            if result and retained is None:
                _generation_records_publish(generation_key, records, cache_key)
            return result
        finally:
            self._snapshot_objects = None
            self._snapshot_capture = None

    def _snapshot_key(self, receipt, manifest_reference, records):
        # A cache key cannot stand in for the fresh row reads above. Bind every
        # complete row envelope, not merely counts or record IDs, to the entry.
        fingerprint = sha256()
        for table in sorted(records):
            for value in chain((table,), records[table]):
                encoded = ds._json(value).encode("utf-8")
                fingerprint.update(len(encoded).to_bytes(8, "big"))
                fingerprint.update(encoded)
        namespace = sha256(ds._json([self.store.workspace_id, self.store.user_id,
                                     self.remote, self.artifacts.root]).encode()).hexdigest()
        return (namespace, receipt["content_hash"], receipt["migration_id"],
            manifest_reference["sha256"], fingerprint.hexdigest(), str(receipt["sequence"]))

    @contextmanager
    def _borrow_snapshot(self, cache_key):
        with _WORKING_SNAPSHOT_CACHE.borrow(cache_key, self._scan) as cached:
            try:
                yield cached
            finally:
                self._snapshot_objects = None
                self._snapshot_capture = None
                if cached is not None:
                    cached[0].clear()
                    cached[1].clear()

    @contextmanager
    def _capture_snapshot(self, enabled):
        slot = _WORKING_SNAPSHOT_CACHE.capture_slot() if enabled else nullcontext(False)
        with slot as capturing:
            try:
                yield capturing
            finally:
                if self._snapshot_capture is not None:
                    self._snapshot_capture.clear()
                self._snapshot_capture = None

    def _hydrate_generation(self, records, generation, sequence, cache_key, progress, *, prime_only=False,
                            materialize=True):
        files = records["artifact_manifest"]
        if len(files) > MAX_FILES:
            raise ValueError("Generation file limit exceeded")
        verified = 0
        if progress is not None:
            progress("artifact_files", verified_files=verified, total_files=len(files))
        by_path = {}
        for table, rows in records.items():
            if table != "artifact_manifest":
                for row in rows:
                    by_path.setdefault(row["source_path"], []).append(row)
        seen, total = set(), 0
        verified_sources = {}
        source_candidates = {}
        source_metadata = {}
        source_refs = set()
        source_cacheable = True
        # Build outside the cache, publish locally only after complete validation.
        borrowed = (self._borrow_snapshot(cache_key)
                if self.forbidden and not prime_only else nullcontext(None))
        temporary_dir = tempfile.TemporaryDirectory(prefix="workbench-hydrate-")
        with borrowed as cached, self._capture_snapshot(cached is None and bool(self.forbidden)) as capturing, \
            temporary_dir as temporary:
            if prime_only and not capturing:
                return False
            cache_hit = cached is not None
            staging = Path(temporary)
            cache_names = set()
            self._snapshot_objects = cached[1] if cached is not None else None
            self._snapshot_capture = {} if capturing else None
            self._snapshot_capture_bytes = 0
            for item in files:
                name = self._local_path(item["source_path"])
                if name in seen:
                    raise ValueError("Duplicate active file")
                seen.add(name)
                reference = self._decode(json.loads(item["payload"]))
                source = reference.get("source") is True
                size = reference["byte_size"]
                if type(size) is not int or size < 0:
                    raise ValueError("Invalid artifact size")
                total += 0 if source else size
                if total > MAX_BYTES:
                    raise ValueError("Generation byte limit exceeded")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                rows = by_path.pop(name, [])
                if source:
                    if rows or not (target.name == "source.xml" or ("staged" in target.parts and target.suffix == ".xml")):
                        raise ValueError("Invalid source artifact classification")
                    source_key = (reference["volume_path"], reference["sha256"], size)
                    source_refs.add(source_key)
                    previous = verified_sources.get(source_key)
                    if previous is None:
                        metadata, hit = self._committed_source(reference, target)
                        if metadata is None:
                            source_cacheable = False
                        else:
                            source_metadata[self._source_key(reference)] = metadata
                            if not hit:
                                source_candidates[source_key] = (reference, target, metadata)
                        verified_sources[source_key] = target
                    else:
                        # Reuse only within this authenticated restore. Scan and
                        # hash each destination; never persist an access verdict.
                        try:
                            with previous.open("rb") as stream, target.open("wb") as output:
                                actual = self._scan(stream, size, output)
                            if actual != (reference["sha256"], size):
                                raise ValueError("Source size or checksum mismatch")
                        except BaseException:
                            target.unlink(missing_ok=True)
                            raise
                    self.reference(reference["sha256"], reference["volume_path"])
                    verified += 1
                    if progress is not None:
                        progress("artifact_files", verified_files=verified, total_files=len(files))
                    continue
                cache_names.add(name)
                if cached is not None:
                    digest = reference["sha256"]
                    if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                            or size > MAX_BYTES or reference["volume_path"] != self.artifacts._path(digest)):
                        raise ValueError("Invalid cached artifact reference")
                    # Preserve the original artifact's current-token check as
                    # well as the check of the reconstructed file bytes.
                    self._object(reference)
                    if name not in cached[0]:
                        raise ValueError("Incomplete cached working snapshot")
                    cached_path, cached_digest, cached_size = cached[0][name]
                    with cached_path.open("rb") as stream, target.open("wb") as output:
                        actual = self._scan(stream, cached_size, output)
                    if actual != (cached_digest, cached_size):
                        raise ValueError("Cached working snapshot checksum mismatch")
                elif target.suffix in DATABASE_SUFFIXES:
                    schema = json.loads(self._object(reference))
                    self._database(target, schema, rows)
                elif target.suffix == ".json" and ("generated-drafts" not in Path(name).parts or rows):
                    # The artifact is integrity evidence, not JSON authority.
                    self._object(reference)
                    target.write_text(ds._json(self._json_file(target.name, rows)), encoding="utf-8")
                else:
                    if rows:
                        raise ValueError("Unexpected records for binary artifact")
                    target.write_bytes(self._object(reference))
                verified += 1
                if progress is not None:
                    progress("artifact_files", verified_files=verified, total_files=len(files))
            if by_path:
                raise ValueError("Metadata rows missing from active file manifest")
            if cached is not None and set(cached[0]) != cache_names:
                raise ValueError("Unexpected cached working snapshot files")
            for reference, path, metadata in source_candidates.values():
                key = self._source_key(reference)
                before = self._source_metadata(reference["volume_path"])
                if before is None:
                    source_cacheable = False
                    self.source_cache_reason = "source_metadata_unavailable"
                    continue
                if before != metadata:
                    _COMMITTED_SOURCE_CACHE.discard(key)
                    raise ValueError("Committed source metadata changed during cache capture")
                def verify_capture():
                    after = self._source_metadata(reference["volume_path"])
                    if after is None:
                        _COMMITTED_SOURCE_CACHE.discard(key)
                        self.source_cache_reason = "source_metadata_unavailable"
                        return False
                    if before != after:
                        _COMMITTED_SOURCE_CACHE.discard(key)
                        raise ValueError("Committed source metadata changed during cache capture")
                    return True
                admitted = _COMMITTED_SOURCE_CACHE.put(key, metadata, path, reference["sha256"],
                                                       reference["byte_size"], self._scan, verify_capture)
                if not admitted:
                    source_cacheable = False
                    if self.source_cache_reason != "source_metadata_unavailable":
                        self.source_cache_reason = "source_cache_admission_failed"
            if source_cacheable and any(not _COMMITTED_SOURCE_CACHE.contains(key, metadata)
                                        for key, metadata in source_metadata.items()):
                source_cacheable = False
                self.source_cache_reason = "source_cache_evicted"
            # Copy only after validation, without cross-directory rename locks
            # on Windows. The private cache is never observable until return.
            if not prime_only and materialize:
                shutil.copytree(staging, self.local, dirs_exist_ok=True)
            candidate = None
            if cached is None and self._snapshot_capture is not None:
                candidate = _WORKING_SNAPSHOT_CACHE.prepare(cache_key, staging, cache_names, self._snapshot_capture,
                                                            self._scan)
            # Cleanup must complete before a candidate becomes visible.
            temporary_dir.cleanup()
            self._snapshot_objects = None
            cached = None
            self.generation, self.sequence = generation, sequence
            self.ready = True
            self._committed_sources = source_refs
            self.source_cache_ready = source_cacheable
            if source_cacheable:
                self.source_cache_reason = "ready" if source_refs else "no_sources"
            cache_ready = cache_hit
            if candidate is not None:
                cache_ready = _WORKING_SNAPSHOT_CACHE.publish(candidate)
            self.working_snapshot_cache_reason = (
                "cache_hit" if cache_hit else
                _WORKING_SNAPSHOT_CACHE.last_admission_reason if self.forbidden else
                "cache_not_eligible")
            if self.working_snapshot_cache_reason == "container_memory_unavailable":
                self.working_snapshot_memory_diagnostic = dict(_LAST_CONTAINER_MEMORY_DIAGNOSTIC)
            self.working_snapshot_cache_hit = cache_hit
            self.working_snapshot_cache_ready = cache_ready
        return True

    def _json_file(self, name, rows):
        if any(row["source_table"] != "json" for row in rows):
            raise ValueError("Invalid JSON source rows")
        if name == "model-routing.json":
            return {"version": 1, "identities": {
                json.loads(row["source_key"]): self._decode(json.loads(row["payload"])) for row in rows}}
        if name.startswith("proposals-") and (not rows or all(type(json.loads(r["source_key"])) is int for r in rows)):
            ordered = sorted(rows, key=lambda r: json.loads(r["source_key"]))
            if [json.loads(r["source_key"]) for r in ordered] != list(range(len(ordered))):
                raise ValueError("Non-contiguous proposal JSON records")
            return [self._decode(json.loads(row["payload"])) for row in ordered]
        if len(rows) != 1:
            raise ValueError("Expected one JSON metadata document")
        return self._decode(json.loads(rows[0]["payload"]))

    @staticmethod
    def _schema(path):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            objects = [dict(type=typ, name=name, sql=sql) for typ, name, sql in db.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,name")]
            columns = {o["name"]: [r[1] for r in db.execute("PRAGMA table_xinfo(" + _quote(o["name"]) + ")") if r[6] == 0]
                       for o in objects if o["type"] == "table"}
        return {"format": "sqlite-ddl-v1", "objects": objects, "columns": columns}

    def _database(self, path, schema, rows):
        if schema.get("format") != "sqlite-ddl-v1":
            raise ValueError("Database row snapshots are not accepted")
        with closing(sqlite3.connect(path)) as db, db:
            # No triggers execute while Delta rows are inserted. Never unpickle
            # checkpoint cells. Original SQLite column names select raw values,
            # not build_records' supplementary *_decoded convenience fields.
            db.execute("PRAGMA trusted_schema=OFF")
            for obj in schema["objects"]:
                if obj["type"] == "table":
                    if not obj["sql"].lstrip().upper().startswith("CREATE TABLE"):
                        raise ValueError("Only ordinary SQLite schema tables are supported")
                    db.execute(obj["sql"])
            for row in rows:
                table = row["source_table"]
                columns = schema["columns"][table]
                payload = self._decode(json.loads(row["payload"]))
                db.execute("INSERT INTO " + _quote(table) + " (" + ",".join(map(_quote, columns))
                           + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                           [payload[column] for column in columns])
            for obj in schema["objects"]:
                if obj["type"] != "table":
                    if obj["type"] not in {"index", "trigger", "view"} or not obj["sql"].lstrip().upper().startswith("CREATE "):
                        raise ValueError("Invalid SQLite schema object")
                    db.execute(obj["sql"])
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Reconstructed SQLite integrity check failed")

    def flush(self, *, prime_cache=True):
        if not self.ready or not self._materialized:
            raise ValueError("Materialized authoritative restore must succeed before commit")
        files, schemas, source_refs, total = [], {}, {}, 0
        for path in sorted(self.local.rglob("*")):
            if path.is_symlink():
                raise ValueError("Hosted symlinks are forbidden")
            if not path.is_file() or path.name.endswith((".lock", "-wal", "-shm", "-journal")):
                continue
            name = path.relative_to(self.local).as_posix()
            if any(part.startswith(".") for part in name.split("/")):
                continue
            self._local_path(name)
            if len(files) >= MAX_FILES:
                raise ValueError("Hosted state exceeds storage limits")
            if path.name == "source.xml" or ("staged" in path.parts and path.suffix == ".xml"):
                size = path.stat().st_size
                if size > max_xml_bytes():
                    raise ValueError("Source exceeds explicit XML storage limits")
                with path.open("rb") as stream:
                    digest, actual_size = self._scan(stream, max_xml_bytes())
                if actual_size != size:
                    raise ValueError("Snapshot changed after capture")
                if digest not in self.sources:
                    raise ValueError("Source must be persisted before analysis")
                ref = {"volume_path": self.sources[digest], "sha256": digest, "byte_size": size, "source": True}
                if (ref["volume_path"], digest, size) in self._committed_sources:
                    self._committed_source(ref)
                else:
                    self._verify_source(ref)
                source_refs[digest] = ref
                files.append({"path": name, "sha256": digest, "byte_size": size})
                continue
            raw = path.read_bytes()
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError("Hosted state exceeds storage limits")
            digest = sha256(raw).hexdigest()
            self._checked(raw)
            files.append({"path": name, "sha256": digest, "byte_size": len(raw)})
            if path.suffix in DATABASE_SUFFIXES:
                schemas[digest] = ds._json(self._schema(path)).encode()

        # Complete secret/size/inventory preflight before uploading any new data.
        # build_records still captures every exact source column, but its raw
        # SQLite file upload is replaced by DDL-only metadata, never backup bytes.
        with tempfile.TemporaryDirectory(prefix="workbench-records-") as temporary:
            staged = Path(temporary)
            runtime = self

            class StagedArtifacts:
                def put_file(self, path, item):
                    # Parent-owned build_records must invoke this hook BEFORE
                    # read_bytes for source.xml/staged XML. No global patching.
                    with path.open("rb") as stream:
                        digest, size = runtime._scan(stream, max_xml_bytes())
                    if digest != item["sha256"] or size != item["byte_size"]:
                        raise ValueError("Snapshot changed after capture")
                    reference = source_refs.get(digest)
                    if reference is None or reference["byte_size"] != size:
                        raise ValueError("Source must be persisted before analysis")
                    return reference

                def put(self, raw):
                    runtime._checked(raw)
                    digest = sha256(raw).hexdigest()
                    if digest in source_refs:
                        return source_refs[digest]
                    raw = schemas.get(digest, raw)
                    digest = sha256(raw).hexdigest()
                    runtime._checked(raw)
                    (staged / digest).write_bytes(raw)
                    return {"volume_path": runtime.artifacts._path(digest), "sha256": digest, "byte_size": len(raw)}

            records = ds.build_records(self.store, ds.Snapshot(self.local, files), StagedArtifacts())
            generation = uuid4().hex
            manifest = {"format": 1, "workspace_id": self.store.workspace_id, "user_id": self.store.user_id,
                        "generation": generation, "sequence": self.sequence + 1, "parent": self.generation,
                        "inventory": ds._inventory(records)}
            raw_manifest = self._checked(ds._json(manifest).encode())
            if len(raw_manifest) > ds.MAX_JSON:
                raise ValueError("Generation inventory exceeds bound")
            for rows in records.values():
                for row in rows:
                    if len(row["payload"].encode()) > ds.MAX_PAYLOAD:
                        raise ValueError("Generation payload exceeds bound")
            for path in sorted(staged.iterdir()):
                self.artifacts.put(path.read_bytes())
        reference = self.artifacts.put(raw_manifest)
        for table, rows in records.items():
            if rows:
                if table == "audit_events":
                    serialize_delta_write(self.store.upsert_records, table, rows, migration_id=generation)
                else:
                    self.store.upsert_records(table, rows, migration_id=generation)
        verified_records = self._records(manifest["inventory"], generation)
        latest = self._latest()
        if (latest["migration_id"] if latest else None) != self.generation:
            raise ValueError("Committed parent changed; overlapping writers are forbidden")
        receipt = self.store.make_record(
            "migration_receipts", **RUNTIME_SCOPE, source_path="hosted-runtime", source_table="generation",
            source_key=generation, payload={"manifest": reference, "parent": self.generation},
            sequence=self.sequence + 1, status="committed", artifact_path=reference["volume_path"],
            artifact_sha256=reference["sha256"], byte_size=reference["byte_size"])
        # The single final Delta append is the only visibility point. A lost
        # response can still be ambiguous; never assert remote rollback.
        self.store.upsert_records("migration_receipts", [receipt], migration_id=generation)
        committed = self._latest()
        if committed is None or committed["migration_id"] != generation:
            raise ValueError("Generation publication is unconfirmed")
        self.generation, self.sequence = generation, self.sequence + 1
        self.receipt_marker = (committed["migration_id"], committed["sequence"],
                       committed["content_hash"], reference["sha256"])
        self._committed_sources = {(ref["volume_path"], ref["sha256"], ref["byte_size"])
                                   for ref in source_refs.values()}
        self.source_cache_ready = False
        self.source_cache_reason = "source_cache_not_primed"
        if self.forbidden and prime_cache:
            try:
                # The readback, not submitted rows or mutable local files, is
                # the evidence for the exact generation being admitted.
                order = lambda row: (row["project_id"], row["package_hash"], row["context_id"],
                                     row["record_id"], row["content_hash"], row["migration_id"])
                exact = (committed == dict(receipt, migration_id=generation)
                         and all(sorted(verified_records[table], key=order)
                                 == sorted((dict(row, migration_id=generation) for row in rows), key=order)
                                 for table, rows in records.items() if table != "migration_receipts"))
                if exact and self._object(reference) == raw_manifest:
                    key = self._snapshot_key(committed, reference, verified_records)
                    generation_key = _generation_record_key(self.store, self.remote,
                                                             self.artifacts.root, committed)
                    _generation_records_publish(generation_key, verified_records, key)
                    verifier = DeltaRuntime(self.identity_factory, self.remote, self.local,
                                            warehouse=self.store.warehouse,
                                            forbidden_token=self.forbidden.decode("utf-8"),
                                            store=self.store, artifacts=self.artifacts)
                    verifier._hydrate_generation(verified_records, generation, self.sequence, key, None,
                                                 prime_only=True)
                    self.source_cache_ready = verifier.source_cache_ready
                    self.source_cache_reason = verifier.source_cache_reason
            except Exception:
                # Cache admission is an optimization, never a commit verdict.
                pass
        return generation