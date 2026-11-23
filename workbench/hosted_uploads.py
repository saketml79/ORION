"""Opt-in durable chunk intake. No route installation, state restore or execution.

Call handle after fresh trusted OBO resolution, outside the long-action lock.
POST complete is an observation, NOT an async submission or generation commit.
The parent must allowlist POST /api/hosted/uploads/{id}/finalize in its operation
dispatcher. Its worker calls materialize, stages that file with the old manager,
flushes the complete generation, then calls mark_committed. Analysis is separate.
Only the parent owns worker lifetime and the returned temporary file's cleanup.

One app replica is required: striped process locks serialize journal writers and
per-user outstanding-byte reservations, not distributed transactions. No client,
request, credential, upload content or mutable head is retained by this service.
Uncommitted manifests reserve batch capacity durably, including after restart.
Historical reservation scans fail closed above MAX_UPLOADS, never truncate.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
from threading import RLock

from databricks.sdk.errors import AlreadyExists
from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.databricks_models import AuthenticationUnavailable
from workbench.delta_runtime import DeltaRuntime, RequestStateStore
from workbench.hosted_registry import _registry
from workbench.hosted_storage import volume_path

API = "/api/hosted/uploads"
CHUNK_BYTES = 4 * 1024 * 1024
READ_BYTES = 1024 * 1024
MAX_CHUNKS = 8192
MAX_UPLOADS = 4096
MAX_METADATA = 16 * 1024
_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ROUTE = re.compile(re.escape(API) + r"/([a-f0-9]{32})(?:/(complete|chunks/(0|[1-9][0-9]{0,4})))?\Z")
_LOCKS = tuple(RLock() for _ in range(64))


def _lock(store):
    digest = sha256((store.workspace_id + "\n" + store.user_id).encode()).digest()
    return _LOCKS[digest[0] % len(_LOCKS)]


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    def constant(_):
        raise ValueError("Non-finite JSON value")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _checked(value, forbidden=()):
    raw = value if isinstance(value, bytes) else ds._json(value).encode()
    ds._safe(value)
    if any(secret in raw for secret in forbidden):
        raise ValueError("Credential material cannot be persisted")
    return raw


def _forbidden(identity, headers):
    values = list(identity.client.config.authenticate().values())
    values.extend(value for key, value in headers.items()
                  if any(word in key.lower() for word in
                         ("authorization", "token", "cookie", "secret", "credential", "api-key")))
    return tuple({part.encode() for value in values if isinstance(value, str) and value
                  for part in (value, value.removeprefix("Bearer ")) if part})


class HostedUploads:
    """Immutable manifest, chunk receipts and explicit post-generation commit receipt.

    X-Chunk-SHA256 is optional for non-browser callers. If supplied it must be a
    lowercase hex SHA256 matching the body. The server always computes a digest
    and verifies the entire immutable Volume object before acknowledging a chunk.
    """

    def __init__(self, warehouse, max_file_bytes=8 * 1024**3,
                 max_batch_bytes=32 * 1024**3, chunk_bytes=CHUNK_BYTES):
        self.warehouse = ds._text(warehouse)
        if (any(type(v) is not int or v <= 0 for v in
                (max_file_bytes, max_batch_bytes, chunk_bytes))
                or chunk_bytes > CHUNK_BYTES or max_file_bytes > chunk_bytes * MAX_CHUNKS
                or max_batch_bytes < max_file_bytes):
            raise ValueError("Invalid upload limits")
        self.max_file_bytes = max_file_bytes
        self.max_batch_bytes = max_batch_bytes
        self.chunk_bytes = chunk_bytes

    def _store(self, identity, remote):
        if identity.mode != "obo":
            raise AuthenticationUnavailable("Fresh OBO identity required")
        parts = volume_path(remote + "/HEAD.json").split("/")
        namespace = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
        if parts[5:] != ["users", namespace, "HEAD.json"]:
            raise PermissionError("Upload namespace mismatch")
        store = RequestStateStore(identity.client, self.warehouse, parts[2], parts[3], identity.user_id)
        if store.workspace_id != identity.host:
            raise PermissionError("Upload workspace mismatch")
        username = store._authorize()
        store._verify_table("jobs", username)
        volume = store.client.volumes.read(".".join(parts[2:5]))
        if ds._get(volume, "owner") != username:
            raise PermissionError("Upload Volume owner mismatch")
        return store

    @staticmethod
    def _scope(upload_id):
        if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id):
            raise ValueError("Invalid upload ID")
        return dict(project_id="__uploads__", package_hash="__none__",
                    context_id="hosted-upload:" + upload_id)

    def _record(self, store, upload_id, key, payload):
        scope = self._scope(upload_id)
        return dict(store.make_record(
            "jobs", **scope, source_path="hosted-uploads", source_table="uploads",
            source_key=key, payload=payload, kind=key.split(":")[0]),
            migration_id=scope["context_id"])

    def _manifest(self, payload):
        from workbench.app import _filename

        if (not isinstance(payload, dict) or set(payload) != {
                "id", "project_id", "name", "size_bytes", "chunk_bytes", "total_chunks"}
                or not isinstance(payload["id"], str) or not _ID.fullmatch(payload["id"])
                or not isinstance(payload["project_id"], str) or not _ID.fullmatch(payload["project_id"])
                or type(payload["size_bytes"]) is not int
                or not 1 <= payload["size_bytes"] <= self.max_file_bytes
                or type(payload["chunk_bytes"]) is not int
                or not 1 <= payload["chunk_bytes"] <= self.chunk_bytes
                or type(payload["total_chunks"]) is not int
                or not 1 <= payload["total_chunks"] <= MAX_CHUNKS
                or payload["total_chunks"] != (payload["size_bytes"] + payload["chunk_bytes"] - 1) // payload["chunk_bytes"]):
            raise ValueError("Invalid upload manifest")
        _filename(payload["name"], {".xml"})
        _checked(payload)
        return payload

    def _decode(self, store, upload_id, row):
        if (set(row) != set(ds.COLUMNS)
                or any(type(row[k]) is not (int if t == "BIGINT" else str)
                       for k, t in ds.COLUMNS.items())
                or len(row["payload"].encode()) > MAX_METADATA):
            raise ValueError("Invalid upload envelope")
        payload = _json(row["payload"])
        _checked(payload)
        if row != self._record(store, upload_id, row["source_key"], payload):
            raise ValueError("Upload journal envelope or checksum mismatch")
        return payload

    @staticmethod
    def _size(manifest, index):
        if type(index) is not int or not 0 <= index < manifest["total_chunks"]:
            raise HTTPException(400, "Invalid chunk index.")
        return min(manifest["chunk_bytes"], manifest["size_bytes"] - index * manifest["chunk_bytes"])

    def _read(self, store, upload_id):
        rows, cursor = [], None
        # Explicit cap includes the manifest and optional commit receipt.
        for _ in range((MAX_CHUNKS + 2) // 50 + 1):
            page = store.read_table("jobs", **self._scope(upload_id), limit=50, after=cursor)
            if len(page) > 50:
                raise ValueError("Oversize upload journal page")
            for row in page:
                position = tuple(row[k] for k in ("record_id", "content_hash", "migration_id"))
                if cursor is not None and position <= cursor:
                    raise ValueError("Non-advancing upload journal cursor")
                cursor = position
                rows.append(row)
                if len(rows) > MAX_CHUNKS + 2:
                    raise ValueError("Upload journal limit exceeded")
            if len(page) < 50:
                break
        else:
            raise ValueError("Upload journal pagination limit exceeded")
        entries = {}
        for row in rows:
            key = row["source_key"]
            if key in entries:
                raise ValueError("Ambiguous immutable upload receipt")
            entries[key] = self._decode(store, upload_id, row)
        manifest = entries.pop("manifest", None)
        if manifest is None:
            if entries:
                raise ValueError("Upload receipts without a manifest")
            return None, {}, None
        try:
            self._manifest(manifest)
        except HTTPException:
            raise ValueError("Invalid persisted upload filename") from None
        if manifest["id"] != upload_id:
            raise ValueError("Upload manifest ID mismatch")
        committed = entries.pop("committed", None)
        chunks = {}
        for key, payload in entries.items():
            if (not re.fullmatch(r"chunk:(0|[1-9][0-9]{0,4})", key)
                    or not isinstance(payload, dict) or set(payload) != {"index", "sha256", "size_bytes"}
                    or type(payload["index"]) is not int or key != "chunk:" + str(payload["index"])
                    or not 0 <= payload["index"] < manifest["total_chunks"]
                    or not isinstance(payload["sha256"], str) or not _HASH.fullmatch(payload["sha256"])
                    or type(payload["size_bytes"]) is not int
                    or payload["size_bytes"] != self._size(manifest, payload["index"])):
                raise ValueError("Invalid chunk receipt")
            chunks[payload["index"]] = payload
        if committed is not None:
            if (not isinstance(committed, dict) or set(committed) != {"result"}
                    or not isinstance(committed["result"], dict)
                    or len(chunks) != manifest["total_chunks"]):
                raise ValueError("Invalid upload commitment")
        return manifest, chunks, committed

    def _append(self, store, upload_id, key, payload):
        if len(_checked(payload)) > MAX_METADATA:
            raise ValueError("Upload metadata exceeds limit")
        record = self._record(store, upload_id, key, payload)
        store.upsert_records("jobs", [record], migration_id=record["migration_id"])
        manifest, chunks, committed = self._read(store, upload_id)
        actual = (manifest if key == "manifest" else committed if key == "committed"
                  else chunks.get(payload["index"]))
        if actual != payload:
            raise RuntimeError("Upload journal append is unconfirmed")

    def _inventory(self, store, key):
        """Only reservation/commit metadata, never source blobs or full state."""
        values = {"workspace_id": store.workspace_id, "user_id": store.user_id,
                  "project_id": "__uploads__", "package_hash": "__none__",
                  "source_path": "hosted-uploads", "source_table": "uploads", "source_key": key}
        cursor, found = None, {}
        for _ in range(MAX_UPLOADS // 50 + 1):
            params = dict(values)
            where = " AND ".join(ds._identifier(k) + "=:" + k for k in values)
            if cursor is not None:
                params.update(zip(("cursor_id", "cursor_hash", "cursor_migration"), cursor, strict=True))
                where += " AND (record_id,content_hash,migration_id) > (:cursor_id,:cursor_hash,:cursor_migration)"
            names = ",".join(ds._identifier(k) for k in ds.COLUMNS)
            raw = store._execute(f"SELECT {names} FROM {store._name('jobs')} WHERE {where} "
                                 "ORDER BY record_id,content_hash,migration_id LIMIT 50", params)
            if len(raw) > 50:
                raise ValueError("Oversize reservation page")
            for cells in raw:
                row = {k: int(v) if ds.COLUMNS[k] == "BIGINT" else v
                       for k, v in zip(ds.COLUMNS, cells, strict=True)}
                position = tuple(row[k] for k in ("record_id", "content_hash", "migration_id"))
                if cursor is not None and position <= cursor:
                    raise ValueError("Non-advancing reservation cursor")
                cursor = position
                upload_id = row["context_id"].removeprefix("hosted-upload:")
                payload = self._decode(store, upload_id, row)
                if upload_id in found or len(found) >= MAX_UPLOADS:
                    raise ValueError("Ambiguous or excessive reservation history")
                if key == "manifest":
                    self._manifest(payload)
                    if payload["id"] != upload_id:
                        raise ValueError("Reservation ID mismatch")
                elif not isinstance(payload, dict) or set(payload) != {"result"} or not isinstance(payload["result"], dict):
                    raise ValueError("Invalid commitment reservation")
                found[upload_id] = payload
            if len(raw) < 50:
                return found
        raise ValueError("Reservation pagination limit exceeded")

    def _reserve(self, store, size):
        manifests, committed = self._inventory(store, "manifest"), self._inventory(store, "committed")
        if set(committed) - set(manifests):
            raise ValueError("Commitment without reservation")
        if len(manifests) >= MAX_UPLOADS:
            raise HTTPException(409, "Upload reservation history limit reached.")
        if size + sum(m["size_bytes"] for key, m in manifests.items() if key not in committed) > self.max_batch_bytes:
            raise HTTPException(413, "Outstanding upload batch exceeds byte limit.")

    @staticmethod
    def _public(manifest, chunks, committed):
        ready = len(chunks) == manifest["total_chunks"]
        return {**manifest, "total_bytes": manifest["size_bytes"],
                "status": "committed" if committed else "ready_for_finalize" if ready else "uploading",
                "ready_for_finalize": ready, "durable_bytes": sum(c["size_bytes"] for c in chunks.values()),
                "chunks": [chunks[i] for i in sorted(chunks)],
                **({"result": committed["result"]} if committed else {})}

    @staticmethod
    def _chunk_path(remote, upload_id, index):
        return volume_path(f"{remote}/upload-chunks/{upload_id}/{index}.bin")

    @staticmethod
    def _stream(identity, path, size, expected, *, sink=None, whole=None, forbidden=(), tail=b""):
        response = identity.client.files.download(path)
        if response.contents is None:
            raise ValueError("Missing chunk contents")
        digest, received = sha256(), 0
        overlap = max([8192, *(len(s) for s in forbidden)])
        with closing(response.contents) as stream:
            while True:
                requested = min(READ_BYTES, size - received + 1)
                block = stream.read(requested)
                if not isinstance(block, bytes) or len(block) > requested:
                    raise ValueError("Invalid chunk stream")
                if not block:
                    break
                received += len(block)
                if received > size:
                    raise ValueError("Oversize immutable chunk")
                _checked(tail + block, forbidden)
                tail = (tail + block)[-overlap:]
                digest.update(block)
                if whole is not None:
                    whole.update(block)
                if sink is not None:
                    sink.write(block)
        if received != size or digest.hexdigest() != expected:
            raise ValueError("Immutable chunk size or checksum mismatch")
        return tail

    def handle(self, identity, remote, method, path, query, headers, body, execute):
        """Return (status, JSON) or None. execute is deliberately never invoked.

        Bodies: start is bounded JSON bytes/dict, PUT is bytes only, complete is
        empty. Unknown routes, including internal finalize, belong to the parent.
        Integrity/storage failures return fixed 503, never upstream messages.
        """
        match = _ROUTE.fullmatch(path)
        start = method == "POST" and path == API
        if not start and not (match and ((method == "GET" and match[2] is None)
                                        or (method == "PUT" and match[3] is not None)
                                        or (method == "POST" and match[2] == "complete"))):
            return None
        try:
            store = self._store(identity, remote)
            forbidden = _forbidden(identity, headers)
            if query:
                raise HTTPException(400, "Upload endpoints do not accept query arguments.")
            with _lock(store):
                if start:
                    try:
                        if isinstance(body, (bytes, str)):
                            if len(body if isinstance(body, bytes) else body.encode()) > MAX_METADATA:
                                raise ValueError("Oversize request")
                            body = _json(body)
                        if (not isinstance(body, dict) or set(body) != {"id", "project_id", "name", "size_bytes"}
                                or len(_checked(body, forbidden)) > MAX_METADATA):
                            raise ValueError("Invalid request")
                        size = body["size_bytes"]
                        if type(size) is not int or size < 1:
                            raise ValueError("Invalid size")
                        if size > self.max_file_bytes:
                            raise HTTPException(413, "Upload exceeds file byte limit.")
                        manifest = self._manifest({**body, "chunk_bytes": self.chunk_bytes,
                                                   "total_chunks": (size + self.chunk_bytes - 1) // self.chunk_bytes})
                    except (ValueError, TypeError, RecursionError, UnicodeError):
                        raise HTTPException(400, "Invalid or unsafe upload manifest.") from None
                    # _registry reads committed metadata only. No restore/audit.
                    runtime = DeltaRuntime(lambda: identity, remote, tempfile.gettempdir(),
                                           warehouse=self.warehouse, store=store)
                    _, registry = _registry(runtime)
                    if not any(project == manifest["project_id"] for project, _ in registry["projects"]):
                        raise HTTPException(404, "Project not found.")
                    upload_id = manifest["id"]
                    old, chunks, committed = self._read(store, upload_id)
                    if old is not None:
                        if old != manifest:
                            raise HTTPException(409, "Upload ID already has a different manifest.")
                        return 200, self._public(old, chunks, committed)
                    self._reserve(store, size)
                    self._append(store, upload_id, "manifest", manifest)
                    return 201, self._public(manifest, {}, None)
                assert match is not None
                upload_id = match[1]
                manifest, chunks, committed = self._read(store, upload_id)
                if manifest is None:
                    raise HTTPException(404, "Upload not found.")
                if method == "PUT":
                    index = int(match[3])
                    size = self._size(manifest, index)
                    if not isinstance(body, bytes):
                        raise HTTPException(400, "A bounded binary chunk is required.")
                    if len(body) > self.chunk_bytes:
                        raise HTTPException(413, "Chunk exceeds byte limit.")
                    if len(body) != size:
                        raise HTTPException(400, "Chunk has an incorrect byte size.")
                    try:
                        _checked(body, forbidden)
                    except ValueError:
                        raise HTTPException(400, "Unsafe chunk content.") from None
                    digest = sha256(body).hexdigest()
                    checksums = [value for key, value in headers.items() if key.lower() == "x-chunk-sha256"]
                    if checksums and (len(checksums) != 1 or checksums[0] != digest):
                        raise HTTPException(400, "Chunk checksum does not match.")
                    payload = {"index": index, "sha256": digest, "size_bytes": size}
                    if index in chunks and chunks[index] != payload:
                        raise HTTPException(409, "Chunk index already has different content.")
                    if committed and index not in chunks:
                        raise HTTPException(409, "Upload is already committed.")
                    path = self._chunk_path(remote, upload_id, index)
                    identity.client.files.create_directory(path.rsplit("/", 1)[0])
                    try:
                        identity.client.files.upload(path, BytesIO(body), overwrite=False)
                    except AlreadyExists:
                        pass  # Existing immutable bytes must pass the same readback.
                    try:
                        self._stream(identity, path, size, digest, forbidden=forbidden)
                    except ValueError:
                        raise HTTPException(409, "Stored chunk failed integrity verification.") from None
                    if index not in chunks:
                        self._append(store, upload_id, "chunk:" + str(index), payload)
                    return 200, payload
                if method == "POST":
                    if body not in (None, b"", ""):
                        raise HTTPException(400, "Complete requires an empty body.")
                    if len(chunks) != manifest["total_chunks"]:
                        raise HTTPException(409, "Upload has missing chunks.")
                return 200, self._public(manifest, chunks, committed)
        except HTTPException as error:
            # Only fixed local validation messages can escape this boundary.
            return error.status_code, {"detail": error.detail}
        except Exception as error:
            auth = (isinstance(error, AuthenticationUnavailable)
                    or type(error).__name__ == "Unauthenticated"
                    or getattr(error, "error_code", None) == "UNAUTHENTICATED")
            return (401 if auth else 503), {"detail": "User authentication is required." if auth else "Hosted upload is unavailable."}

    def materialize(self, identity, remote, upload_id, tempdir):
        """Worker-only: return (Path, name, project_id, sha256, size_bytes).

        Streams sequential chunks with <=1MiB reads. Failure removes the partial
        file. Caller must unlink a successful temporary file in its own finally.
        This never restores state, stages a package or claims a generation commit.
        """
        store = self._store(identity, remote)
        forbidden = _forbidden(identity, {})
        with _lock(store):
            manifest, chunks, _ = self._read(store, upload_id)
        if manifest is None or len(chunks) != manifest["total_chunks"]:
            raise ValueError("Upload is missing chunks")
        digest, tail = sha256(), b""
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", prefix="hosted-upload-",
                                             dir=tempdir, delete=False) as target:
                path = Path(target.name)
                for index in range(manifest["total_chunks"]):
                    chunk = chunks[index]
                    tail = self._stream(identity, self._chunk_path(remote, upload_id, index),
                                        chunk["size_bytes"], chunk["sha256"], sink=target,
                                        whole=digest, forbidden=forbidden, tail=tail)
            if path.stat().st_size != manifest["size_bytes"]:
                raise ValueError("Materialized upload size mismatch")
            return path, manifest["name"], manifest["project_id"], digest.hexdigest(), manifest["size_bytes"]
        except BaseException:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    def mark_committed(self, identity, remote, upload_id, result):
        """Worker-only: call ONLY AFTER the parent's full generation commit.

        A small public JSON result is persisted, never tokens or worker arguments.
        Identical retries are idempotent; different results conflict. No attempt
        is made here to execute, restore, flush or infer a generation commitment.
        """
        store = self._store(identity, remote)
        if not isinstance(result, dict):
            raise ValueError("A public commit result object is required")
        payload = {"result": result}
        if len(_checked(payload, _forbidden(identity, {}))) > MAX_METADATA:
            raise ValueError("Upload commit result exceeds limit")
        with _lock(store):
            manifest, chunks, committed = self._read(store, upload_id)
            if manifest is None or len(chunks) != manifest["total_chunks"]:
                raise ValueError("Cannot commit an incomplete upload")
            if committed is not None and committed != payload:
                raise ValueError("Upload already has a different commit result")
            if committed is None:
                self._append(store, upload_id, "committed", payload)
            return self._public(manifest, chunks, payload)