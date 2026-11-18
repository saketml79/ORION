"""Append-only UC Delta metadata and explicit, offline-snapshot migration.

No import side effects, provisioning of catalogs/schemas, hosted hooks, or auth
serialization. A caller supplies a freshly authenticated WorkspaceClient. SQL
values always use Statement API parameters, never literal interpolation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from threading import Lock
import time
from urllib.parse import urlsplit

from databricks.sdk.errors import AlreadyExists, NotFound
from databricks.sdk.service.sql import (
    Disposition, ExecuteStatementRequestOnWaitTimeout, Format,
    StatementParameterListItem,
)


TABLES = (
    "projects", "packages", "jobs", "proposal_versions", "review_events",
    "documents", "settings", "agent_runs", "agent_findings", "agent_memory",
    "agent_usage", "agent_checkpoints", "loop_runs", "loop_attempts",
    "loop_events", "loop_lessons", "loop_comparisons", "audit_events",
    "artifact_manifest", "model_routes", "migration_receipts",
)
# A shared envelope makes every table directly queryable without decoding JSON.
# Original source columns, nested keys and legacy actor values remain in payload.
COLUMNS = {
    "workspace_id": "STRING", "user_id": "STRING", "project_id": "STRING",
    "package_hash": "STRING", "context_id": "STRING", "record_id": "STRING",
    "content_hash": "STRING", "migration_id": "STRING", "source_path": "STRING",
    "source_table": "STRING", "source_key": "STRING", "entity_id": "STRING",
    "workflow_id": "STRING", "proposal_id": "STRING", "run_id": "STRING",
    "role": "STRING", "kind": "STRING", "status": "STRING", "actor": "STRING",
    "version": "BIGINT", "sequence": "BIGINT", "created_at": "STRING",
    "artifact_path": "STRING", "artifact_sha256": "STRING", "byte_size": "BIGINT",
    "payload": "STRING",
}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
SECRET_KEY = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|authorization|credential|credentials|api_key|private_key|client_secret)(?:$|_)", re.I)
SECRET_BYTES = re.compile(
    rb"(?:Bearer\s+[A-Za-z0-9_.~+/=-]{12,}|dapi[a-f0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)", re.I)
MAX_PAYLOAD = 48 * 1024
MAX_JSON = 64 * 1024 * 1024
BATCH_ROWS = 40
BATCH_BYTES = 512 * 1024
UPLOAD_WORKERS = 8
UPLOAD_BATCH = 64
# The hosted deployment requires one replica. Every adapter instance, including
# independent progress/audit writers, shares physical-table write serialization.
# No identity/client is retained here; idle entries are removed after waiters exit.
_WRITE_GUARD = Lock()
_WRITERS = {}


@contextmanager
def _table_writer(key):
    with _WRITE_GUARD:
        entry = _WRITERS.setdefault(key, [Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _WRITE_GUARD:
            entry[1] -= 1
            if not entry[1]:
                del _WRITERS[key]


SQLITE_MAP = {
    "projects.sqlite": {"projects": "projects", "packages": "packages", "jobs": "jobs"},
    "reviews.sqlite": {"proposals": "proposal_versions", "events": "review_events",
                       "documents": "documents", "settings": "settings"},
    "redesigns.sqlite": {"redesign_versions": "proposal_versions", "redesign_reviews": "review_events",
                         "redesign_version_tombstones": "proposal_versions"},
    "cohort.sqlite": {"cohort_runs": "jobs", "cohort_workflows": "jobs", "target_selections": "settings"},
    "agents.sqlite3": {name: name for name in ("agent_runs", "agent_findings", "agent_memory", "agent_usage")},
    "loops.sqlite": {name: "loop_" + name for name in ("runs", "attempts", "events", "lessons", "comparisons")},
    "audit.sqlite": {"audit_events": "audit_events"},
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Unsafe SQL identifier")
    return "`" + value + "`"


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid namespace or key")
    return value


def _safe(value):
    """Reject recognizable credentials, do not silently redact historical state."""
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY.search(str(key)) and item not in (None, "", "[REDACTED]"):
                # Numeric provider accounting fields are not credentials.
                if not (str(key).endswith("_tokens") and type(item) is int):
                    raise ValueError("Credential-shaped field in migration input")
            _safe(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _safe(item)
    elif isinstance(value, (str, bytes)):
        raw = value.encode("utf-8") if isinstance(value, str) else value
        if SECRET_BYTES.search(raw):
            raise ValueError("Credential-shaped bytes in migration input")
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                return
            _safe(nested)


def _content_hash(record):
    return _hash({k: record[k] for k in COLUMNS if k not in {"content_hash", "migration_id"}})


def safe_error(error):
    """Only fixed categories/codes, never exception messages, URLs or payloads."""
    kinds = {"ValueError", "TypeError", "PermissionError", "RuntimeError", "OSError",
             "AlreadyExists", "NotFound", "PermissionDenied", "Unauthenticated",
             "BadRequest", "InternalError", "ResourceExhausted", "TimeoutError",
             "ConnectionError", "AuthenticationUnavailable", "RoutingError", "DeltaStatementError"}
    codes = {"ALREADY_EXISTS", "RESOURCE_ALREADY_EXISTS", "NOT_FOUND", "RESOURCE_DOES_NOT_EXIST",
             "PERMISSION_DENIED", "UNAUTHENTICATED", "INVALID_PARAMETER_VALUE", "BAD_REQUEST",
             "INTERNAL_ERROR", "INTERNAL_SERVER_ERROR", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED",
             "TEMPORARILY_UNAVAILABLE", "IO_ERROR", "PARSE_SYNTAX_ERROR", "INVALID_ARGUMENT"}
    code = getattr(error, "error_code", None)
    return {"error_type": type(error).__name__ if type(error).__name__ in kinds else "OperationError",
            "error_code": code if isinstance(code, str) and code in codes else None}


def _emit(progress, stage, **details):
    if progress is not None:
        progress({"stage": stage, **details})


def _blob_kind(data):
    if data.startswith(b"SQLite format 3"):
        return "sqlite"
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.lstrip().startswith(b"<"):
        return "xml"
    if data.lstrip().startswith((b"{", b"[")):
        return "json"
    return "binary"


class DeltaStatementError(RuntimeError):
    def __init__(self, error, *, statement_id=None):
        details = safe_error(error)
        self.error_code = details["error_code"]
        code = _get(error, "error_code")
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code):
            self.error_code = code
        sql_state = _get(error, "sql_state", _get(error, "sqlstate"))
        self.sql_state = sql_state if isinstance(sql_state, str) and re.fullmatch(r"[A-Z0-9]{5}", sql_state) else None
        self.statement_id = (statement_id if isinstance(statement_id, str) and
                             re.fullmatch(r"[A-Za-z0-9_-]{1,128}", statement_id) else None)
        message = _get(error, "message")
        if not isinstance(message, str) or not message.strip():
            message = str(error)
        self.diagnostic_message = message[:2000]
        super().__init__("Delta statement unavailable; " + _json(details))


class DeltaStateStore:
    """Exact-user, exact-workspace, append-only metadata API.

    upsert_records inserts new content revisions only. It never mutates prior
    rows. Readers must select the desired application version themselves. This
    is not a distributed review CAS or a LangGraph checkpointer implementation.
    """

    def __init__(self, client, warehouse, catalog, schema, user_id):
        _identifier(catalog)
        _identifier(schema)
        self.client, self.warehouse = client, _text(warehouse)
        self.catalog, self.schema, self.user_id = catalog, schema, _text(user_id)
        host = urlsplit(client.config.host)
        if host.scheme != "https" or not host.hostname or host.username or host.password or host.query or host.fragment or host.path not in ("", "/"):
            raise ValueError("A credential-free HTTPS workspace origin is required")
        self.workspace_id = "https://" + host.netloc.lower()

    def _name(self, table):
        if table not in TABLES:
            raise ValueError("Unknown metadata table")
        return ".".join(_identifier(v) for v in (self.catalog, self.schema, table))

    def _authorize(self):
        me = self.client.current_user.me()
        username = _get(me, "user_name", _get(me, "userName"))
        if _get(me, "id") != self.user_id or not username:
            raise PermissionError("Client must authenticate as the requested user")
        if (self.client.config.host.rstrip("/").lower() != self.workspace_id
                or _get(self.client.catalogs.get(self.catalog), "owner") != username
                or _get(self.client.schemas.get(self.catalog + "." + self.schema), "owner") != username):
            raise PermissionError("Workspace, catalog or schema ownership mismatch")
        return username

    def _verify_table(self, table, username):
        self._name(table)
        info = self.client.tables.get(".".join((self.catalog, self.schema, table)))
        fmt = _get(info, "data_source_format")
        if _get(info, "owner") != username or _get(fmt, "value", fmt) != "DELTA":
            raise PermissionError("Metadata table must be user-owned Delta")
        columns = {c.name: (c.type_text.upper(), c.nullable) for c in info.columns or []}
        if columns != {k: (v, False) for k, v in COLUMNS.items()}:
            raise ValueError("Existing metadata table schema is incompatible")

    def _execute(self, sql, values=None):
        parameters = [StatementParameterListItem(name=k, value=str(v), type="STRING")
                      for k, v in (values or {}).items()]
        statement_id = None
        try:
            response = self.client.statement_execution.execute_statement(
                statement=sql, warehouse_id=self.warehouse, parameters=parameters,
                disposition=Disposition.INLINE, format=Format.JSON_ARRAY,
                wait_timeout="50s", on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
                byte_limit=4 * 1024 * 1024)
            statement_id = _get(response, "statement_id")
            deadline = time.monotonic() + 180
            while _get(_get(response, "status"), "state") is not None:
                state = _get(_get(response, "status"), "state")
                state = _get(state, "value", state)
                if state == "SUCCEEDED":
                    break
                if state not in {"PENDING", "RUNNING"} or time.monotonic() >= deadline:
                    error = _get(_get(response, "status"), "error")
                    raise DeltaStatementError(error or RuntimeError("Delta statement failed or timed out"),
                                               statement_id=statement_id)
                # Bound polling, never busy-wait or expose provider error text.
                time.sleep(0.25)
                response = self.client.statement_execution.get_statement(response.statement_id)
            else:
                raise RuntimeError("Missing Delta statement status")
            manifest = _get(response, "manifest")
            result = _get(response, "result")
            if _get(manifest, "truncated", False) or _get(result, "next_chunk_index") is not None:
                raise RuntimeError("Statement result exceeded bounded inline response")
            return _get(result, "data_array", []) or []
        except DeltaStatementError:
            raise
        except Exception as error:
            # SDK exception messages can contain SQL values or credential URLs.
            raise DeltaStatementError(error, statement_id=statement_id) from None

    def ensure_tables(self):
        """Create only missing tables in an already-owned catalog/schema."""
        username = self._authorize()
        definition = ", ".join(_identifier(k) + " " + v + " NOT NULL" for k, v in COLUMNS.items())
        for table in TABLES:
            try:
                self._verify_table(table, username)
            except NotFound:
                self._execute(f"CREATE TABLE IF NOT EXISTS {self._name(table)} ({definition}) USING DELTA")
                self._verify_table(table, username)
        return list(TABLES)

    def make_record(self, table, *, project_id, package_hash, context_id,
                    source_path, source_table, source_key, payload, **keys):
        self._name(table)
        _safe(payload)
        if set(keys) - set(COLUMNS) or set(keys) & {"workspace_id", "user_id", "record_id", "content_hash", "migration_id"}:
            raise ValueError("Unknown or reserved metadata column")
        row = {k: (0 if t == "BIGINT" else "") for k, t in COLUMNS.items()}
        row.update(workspace_id=self.workspace_id, user_id=self.user_id,
                   project_id=_text(project_id), package_hash=_text(package_hash),
                   context_id=_text(context_id), source_path=_text(source_path),
                   source_table=_text(source_table), source_key=_text(source_key),
                   payload=_json(payload), **keys)
        row["record_id"] = _hash([table, self.workspace_id, self.user_id, project_id,
                                   package_hash, context_id, source_path, source_table, source_key])
        row["content_hash"] = _content_hash(row)
        return row

    def upsert_records(self, table, records, *, migration_id):
        """Append idempotent revisions in bounded multirow parameterized MERGEs.

        Returns submitted unique revision count, not a claim of inserted count.
        App-owned writes serialize per physical table in this single replica.
        Delta has no enforced unique constraint or cross-process writer lock.
        """
        username = self._authorize()
        self._verify_table(table, username)
        _text(migration_id)
        batch, size, submitted = [], 0, 0
        seen = set()

        def flush():
            values, tuples = {}, []
            for i, row in enumerate(batch):
                expressions = []
                for key, typ in COLUMNS.items():
                    parameter = f"r{i}_{key}"
                    values[parameter] = row[key]
                    expressions.append(f"CAST(:{parameter} AS {typ})")
                tuples.append("(" + ",".join(expressions) + ")")
            names = ",".join(_identifier(k) for k in COLUMNS)
            identity_keys = ("workspace_id", "user_id", "project_id", "package_hash",
                             "context_id", "record_id", "content_hash", "migration_id")
            # Source equalities alone can register WHOLE_TABLE_READ. Constant
            # target predicates constrain the read set even for mixed-scope batches.
            target_scope = " OR ".join("(" + " AND ".join(
                f"t.{_identifier(k)}=:r{i}_{k}" for k in identity_keys) + ")"
                for i in range(len(batch)))
            sql = (f"MERGE INTO {self._name(table)} AS t USING (SELECT * FROM VALUES "
                     + ",".join(tuples) + f" AS source_rows({names})) AS s ON "
                   + "(" + target_scope + ") AND "
                   + " AND ".join(f"t.{_identifier(k)}=s.{_identifier(k)}" for k in identity_keys)
                   + f" WHEN NOT MATCHED THEN INSERT ({names}) VALUES ("
                   + ",".join("s." + _identifier(k) for k in COLUMNS) + ")")
            # Different operation/user locks still share jobs/audit files. Hold
            # this lock through Statement API completion, not just submission.
            with _table_writer((self.workspace_id, self.catalog, self.schema, table)):
                self._execute(sql, values)

        for original in records:
            row = dict(original, migration_id=migration_id)
            if set(row) != set(COLUMNS) or row["workspace_id"] != self.workspace_id or row["user_id"] != self.user_id:
                raise PermissionError("Cross-user or invalid record envelope")
            if any(type(row[k]) is not (int if t == "BIGINT" else str) for k, t in COLUMNS.items()):
                raise ValueError("Invalid typed metadata value")
            _safe(row)
            if len(row["payload"].encode()) > MAX_PAYLOAD or row["content_hash"] != _content_hash(row):
                raise ValueError("Oversize or corrupt metadata record")
            identity = (row["record_id"], row["content_hash"])
            if identity in seen:
                continue
            seen.add(identity)
            row_size = len(_json(row).encode())
            if batch and (len(batch) >= BATCH_ROWS or size + row_size > BATCH_BYTES):
                flush()
                batch, size = [], 0
            batch.append(row)
            size += row_size
            submitted += 1
        if batch:
            flush()
        return submitted

    def read_table(self, table, *, project_id, package_hash, context_id,
                   migration_id=None, after=None, limit=50):
        """Exact-scope bounded keyset read; returns JSON payload strings intact."""
        username = self._authorize()
        self._verify_table(table, username)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Read limit must be 1..50")
        values = {"workspace_id": self.workspace_id, "user_id": self.user_id,
                  "project_id": _text(project_id), "package_hash": _text(package_hash),
                  "context_id": _text(context_id)}
        where = " AND ".join(_identifier(k) + "=:" + k for k in values)
        if migration_id is not None:
            values["migration_id"] = _text(migration_id)
            where += " AND migration_id=:migration_id"
        if after is not None:
            if not isinstance(after, (tuple, list)) or len(after) != 3:
                raise ValueError("Invalid keyset cursor")
            values.update(cursor_id=_text(after[0]), cursor_hash=_text(after[1]), cursor_migration=_text(after[2]))
            where += " AND (record_id,content_hash,migration_id) > (:cursor_id,:cursor_hash,:cursor_migration)"
        names = ",".join(_identifier(k) for k in COLUMNS)
        rows = self._execute(f"SELECT {names} FROM {self._name(table)} WHERE {where} ORDER BY record_id,content_hash,migration_id LIMIT {limit}", values)
        return [{k: int(v) if COLUMNS[k] == "BIGINT" else v for k, v in zip(COLUMNS, row, strict=True)} for row in rows]

    def migration_count(self, table, migration_id):
        """Independent count also detects unexpected or empty-inventory scopes."""
        username = self._authorize()
        self._verify_table(table, username)
        rows = self._execute(
            f"SELECT count(*) FROM {self._name(table)} WHERE workspace_id=:workspace_id AND user_id=:user_id AND migration_id=:migration_id",
            {"workspace_id": self.workspace_id, "user_id": self.user_id, "migration_id": _text(migration_id)})
        return int(rows[0][0])


class VolumeArtifacts:
    """Immutable, checksum-verified objects under the same owned UC schema."""

    def __init__(self, store, volume="artifacts"):
        _identifier(volume)
        self.store, self.volume = store, volume
        namespace = _hash([store.workspace_id, store.user_id])
        self.root = f"/Volumes/{store.catalog}/{store.schema}/{volume}/delta-state/users/{namespace}"

    def authorize(self):
        username = self.store._authorize()
        info = self.store.client.volumes.read(f"{self.store.catalog}.{self.store.schema}.{self.volume}")
        if _get(info, "owner") != username:
            raise PermissionError("Artifact Volume must be owned by the requesting user")

    def _path(self, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid artifact checksum")
        return self.root + "/blobs/" + digest

    def put(self, data):
        _safe(data)
        self.authorize()
        return self._put_verified(data)

    def _put_verified(self, data, *, verify_upload=False, create_directory=True):
        """Internal upload after the caller has verified this fixed client."""
        digest = sha256(data).hexdigest()
        path = self._path(digest)
        if create_directory:
            self.store.client.files.create_directory(path.rsplit("/", 1)[0])
        # A retry must not PUT an already-present large immutable body. Some
        # workspace gateways reset that request instead of returning conflict.
        # Only a genuine NotFound permits upload, all other read errors fail.
        try:
            existing = self._get_verified(digest, max_bytes=len(data))
        except NotFound:
            pass
        else:
            if existing != data:
                raise ValueError("Existing immutable artifact hash mismatch")
            return {"volume_path": path, "sha256": digest, "byte_size": len(data)}
        try:
            self.store.client.files.upload(path, io.BytesIO(data), overwrite=False)
        except AlreadyExists:
            if self._get_verified(digest, max_bytes=len(data)) != data:
                raise ValueError("Existing immutable artifact hash mismatch") from None
        else:
            if verify_upload and self._get_verified(digest, max_bytes=len(data)) != data:
                raise ValueError("Uploaded artifact checksum mismatch")
        return {"volume_path": path, "sha256": digest, "byte_size": len(data)}

    def put_many(self, paths, *, progress=None):
        """Bounded parallel migration upload, with no cross-action auth cache.

        Verify exact principal and all namespace owners before AND after each
        batch, pin the same client/host throughout, and independently download
        every object. The service still authorizes each individual HTTP request.
        No deletion, overwrite, identity fallback or swallowed upload failure.
        """
        paths = list(paths)
        client, host = self.store.client, self.store.workspace_id
        completed = 0

        def upload(path):
            data = path.read_bytes()
            try:
                _safe(data)
                if (self.store.client is not client
                        or client.config.host.rstrip("/").lower() != host):
                    raise PermissionError("Migration client changed during verified batch")
                if sha256(data).hexdigest() != path.name:
                    raise ValueError("Staged artifact hash mismatch")
                reference = self._put_verified(data, verify_upload=True, create_directory=False)
                return reference
            except Exception as error:
                _emit(progress, "ARTIFACT_FAILED", sha256=path.name, byte_size=len(data),
                      kind=_blob_kind(data), **safe_error(error))
                raise

        for offset in range(0, len(paths), UPLOAD_BATCH):
            self.authorize()
            self.store.client.files.create_directory(self.root + "/blobs")
            with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
                futures = [pool.submit(upload, path) for path in paths[offset:offset + UPLOAD_BATCH]]
                for future in as_completed(futures):
                    future.result()
                    completed += 1
            self.authorize()
            _emit(progress, "ARTIFACTS_VERIFIED", completed=completed, total=len(paths))
        return completed

    def get(self, digest, *, max_bytes=1024 * 1024 * 1024):
        self.authorize()
        return self._get_verified(digest, max_bytes=max_bytes)

    def _get_verified(self, digest, *, max_bytes):
        response = self.store.client.files.download(self._path(digest))
        with closing(response.contents) as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes or sha256(data).hexdigest() != digest:
            raise ValueError("Artifact exceeds bound or checksum mismatch")
        return data


@dataclass
class Snapshot:
    root: Path
    files: list


def snapshot_local(source_root, destination):
    """Read-only SQLite online backups, then operate exclusively on the copies.

    Stop workers before capture for cross-file consistency. SQLite backup gives
    per-database consistency only. Reject symlinks, never follow local paths in
    stored metadata. Destination must be outside the source tree and empty.
    """
    source, destination = Path(source_root).resolve(), Path(destination).resolve()
    if not source.is_dir() or destination == source or source in destination.parents:
        raise ValueError("Snapshot destination must be outside a valid source root")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Snapshot destination must be empty")
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are forbidden in migration input")
        relative = path.relative_to(source)
        if any(part.startswith(".") for part in relative.parts) or path.name.endswith(("-wal", "-shm", ".lock")) or not path.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {".sqlite", ".sqlite3", ".db"}:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db, closing(sqlite3.connect(target)) as backup:
                db.backup(backup)
        else:
            # bounded memory and a stable byte copy, never os.replace on source
            with path.open("rb") as inp, target.open("xb") as out:
                while chunk := inp.read(1024 * 1024):
                    out.write(chunk)
        raw = target.read_bytes()
        _safe(raw)
        files.append({"path": relative.as_posix(), "sha256": sha256(raw).hexdigest(), "byte_size": len(raw)})
    return Snapshot(destination, files)


def _scope(relative, data=None):
    parts = Path(relative).parts
    project, package, context = "__legacy__", "__none__", "legacy"
    if "projects" in parts:
        i = parts.index("projects")
        if len(parts) > i + 2 and parts[i + 1] != "projects.sqlite":
            project = parts[i + 1]
            context = "project:" + project
    if "package-contexts" in parts:
        i = parts.index("package-contexts")
        package = parts[i + 1]
        context = "package:" + project + ":" + package
    elif "packages" in parts:
        i = parts.index("packages")
        package = parts[i + 1]
    elif "generated-drafts" in parts:
        i = parts.index("generated-drafts")
        package = parts[i + 1]
    if isinstance(data, dict):
        row_project = str(data.get("project_id") or project)
        if project != "__legacy__" and project != row_project:
            raise ValueError("Source row disagrees with project-context namespace")
        project = row_project
        found = data.get("package_hash") or data.get("sha256") or data.get("hash")
        if found and re.fullmatch(r"[a-f0-9]{64}", str(found)):
            if package != "__none__" and package != found:
                raise ValueError("Source row disagrees with package-context namespace")
            package = found
    return {"project_id": project, "package_hash": package, "context_id": context}


def _keys(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ("workflow_id", "proposal_id", "run_id", "role", "kind", "status", "actor", "created_at"):
        if isinstance(data.get(key), str):
            result[key] = data[key]
    if "status" not in result and isinstance(data.get("state"), str):
        result["status"] = data["state"]
    for key in ("version", "sequence"):
        if type(data.get(key)) is int:
            result[key] = data[key]
    entity = data.get("id", data.get("document_id", data.get("proposal_id", data.get("run_id", ""))))
    result["entity_id"] = str(entity)
    return result


def _unpack_row(row):
    # Keep every raw source column; expose parsed JSON separately, without ever
    # rewriting legacy actor or actor_authenticated fields.
    result = dict(row)
    for key in ("payload", "record", "result", "input_hashes"):
        if isinstance(row.get(key), str):
            try:
                result[key + "_decoded"] = json.loads(row[key])
            except ValueError:
                pass
    return result


def _externalize(value, artifacts):
    _safe(value)
    if isinstance(value, bytes):
        return {"$artifact": artifacts.put(value), "encoding": "binary"}
    if isinstance(value, str) and len(value.encode()) > 8192:
        return {"$artifact": artifacts.put(value.encode()), "encoding": "utf-8"}
    if isinstance(value, dict):
        value = {k: _externalize(v, artifacts) for k, v in value.items()}
    elif isinstance(value, list):
        value = [_externalize(v, artifacts) for v in value]
    if len(_json(value).encode()) > MAX_PAYLOAD:
        return {"$artifact": artifacts.put(_json(value).encode()), "encoding": "json"}
    return value


def build_records(store, snapshot, artifacts):
    """Exact inventory: every SQLite user row and every non-database file.

    Every file also gets an artifact_manifest entry. Unknown SQLite tables fail
    closed rather than disappearing from the inventory. Checkpoint tables are
    serialized losslessly, including binary cells, never unpickled or executed.
    """
    records = {t: [] for t in TABLES}

    def add(table, path, source_table, key, payload, scope=None, typed=None):
        _safe(payload)
        records[table].append(store.make_record(
            table, **(scope or _scope(path)), source_path=path,
            source_table=source_table, source_key=_json(key),
            payload=_externalize(payload, artifacts), **(typed or {})))

    for item in snapshot.files:
        relative = item["path"]
        path = snapshot.root / relative
        source_xml = path.name == "source.xml" or ("staged" in path.parts and path.suffix.lower() == ".xml")
        if source_xml and callable(getattr(artifacts, "put_file", None)):
            reference = artifacts.put_file(path, item)
        else:
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Snapshot changed after capture")
            reference = artifacts.put(raw)
        add("artifact_manifest", relative, "file", relative, reference,
            typed={"artifact_path": reference["volume_path"], "artifact_sha256": reference["sha256"], "byte_size": reference["byte_size"]})
        if path.suffix in {".sqlite", ".sqlite3", ".db"}:
            checkpoint = path.name.startswith("checkpoint_")
            mapping = SQLITE_MAP.get(path.name, {})
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for table in tables:
                    _identifier(table)
                    if checkpoint:
                        target = "agent_checkpoints"
                    elif table in mapping:
                        target = mapping[table]
                    else:
                        raise ValueError("Unrecognized SQLite table; migration inventory must be extended explicitly")
                    columns = db.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
                    primary = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]
                    # All known metadata tables have a primary key. Refuse an
                    # unstable key instead of silently collapsing duplicate rows.
                    if not primary:
                        raise ValueError("SQLite table has no stable primary key")
                    for row in db.execute(f"SELECT * FROM {_identifier(table)}"):
                        payload = _unpack_row(dict(row))
                        decoded = payload.get("payload_decoded", payload.get("record_decoded", {}))
                        merged = {**(decoded if isinstance(decoded, dict) else {}), **dict(row)}
                        scope = _scope(relative, merged)
                        if table == "projects":
                            scope["project_id"] = str(row["id"])
                        typed = _keys(merged)
                        if checkpoint:
                            typed["role"] = path.stem.removeprefix("checkpoint_")
                        add(target, relative, table, [row[k] for k in primary], payload, scope, typed)
        elif path.suffix == ".json" and "generated-drafts" not in Path(relative).parts:
            if len(raw) > MAX_JSON:
                raise ValueError("JSON metadata exceeds parsing bound; classify explicitly as an artifact")
            data = json.loads(raw)
            _safe(data)
            if path.name == "model-routing.json":
                # Never place another user's routes into the authenticated user's
                # namespace, even when present in a shared legacy document.
                for identity, entry in data.get("identities", {}).items():
                    if entry.get("user_id") != store.user_id or entry.get("workspace", "").rstrip("/").lower() != store.workspace_id:
                        raise PermissionError("Model routes contain a different workspace/user")
                    add("model_routes", relative, "json", identity, entry, typed=_keys(entry))
            elif path.name.startswith("proposals-") and isinstance(data, list):
                for index, proposal in enumerate(data):
                    add("proposal_versions", relative, "json", index, proposal, _scope(relative, proposal), _keys(proposal))
            else:
                parts = path.parts
                table = ("jobs" if "jobs" in parts else "agent_runs" if "agent-runs" in parts
                         else "documents" if "documents" in parts else "packages" if path.name == "manifest.json"
                         else "settings")
                add(table, relative, "json", relative, data, _scope(relative, data), _keys(data))
    return records


def _inventory(records):
    return {table: [{"record_id": row["record_id"], "content_hash": row["content_hash"],
                     "project_id": row["project_id"], "package_hash": row["package_hash"], "context_id": row["context_id"]}
                    for row in sorted(rows, key=lambda r: (r["record_id"], r["content_hash"]))]
            for table, rows in records.items() if table != "migration_receipts"}


def validate_live(store, inventory, migration_id, *, progress=None):
    """Independent live paged reads, exact counts, identities and row hashes."""
    report = {}
    for table, expected in inventory.items():
        expected_pairs = [(r["record_id"], r["content_hash"]) for r in expected]
        actual = []
        scopes = sorted({(r["project_id"], r["package_hash"], r["context_id"]) for r in expected})
        for project, package, context in scopes:
            cursor = None
            while True:
                page = store.read_table(table, project_id=project, package_hash=package,
                                        context_id=context, migration_id=migration_id, after=cursor)
                for row in page:
                    if row["content_hash"] != _content_hash(row):
                        raise ValueError("Live Delta row hash mismatch")
                    actual.append((row["record_id"], row["content_hash"]))
                if len(page) < 50:
                    break
                last = page[-1]
                new_cursor = (last["record_id"], last["content_hash"], last["migration_id"])
                if cursor is not None and new_cursor <= cursor:
                    raise ValueError("Non-advancing live validation cursor")
                cursor = new_cursor
        live_count = store.migration_count(table, migration_id)
        if sorted(actual) != sorted(expected_pairs) or live_count != len(expected_pairs):
            raise ValueError("Live Delta inventory count or identity mismatch")
        report[table] = {"expected": len(expected_pairs), "actual": len(actual), "hashes_verified": True}
        _emit(progress, "DELTA_VERIFIED", table=table, rows=len(actual))
    return report


def migrate(store, snapshot, artifacts, *, progress=None):
    """Explicit remote writes only. A receipt is published after live validation."""
    # Preflight the complete inventory before any remote writes.
    with tempfile.TemporaryDirectory(prefix="workbench-delta-preflight-") as temp:
        class StagedArtifacts:
            def put(self, data):
                _safe(data)
                digest = sha256(data).hexdigest()
                target = Path(temp) / digest
                if not target.exists():
                    target.write_bytes(data)
                return {"volume_path": artifacts._path(digest), "sha256": digest, "byte_size": len(data)}

        _emit(progress, "PREFLIGHT_BEGIN", files=len(snapshot.files))
        records = build_records(store, snapshot, StagedArtifacts())
        objects = [{"sha256": path.name, "byte_size": path.stat().st_size}
                   for path in sorted(Path(temp).iterdir())]
        _emit(progress, "PREFLIGHT_OK", files=len(snapshot.files), objects=len(objects),
              rows=sum(map(len, records.values())), byte_size=sum(o["byte_size"] for o in objects))
        store.ensure_tables()
        _emit(progress, "TABLES_ENSURED", count=len(TABLES))
        artifacts.put_many(sorted(Path(temp).iterdir()), progress=progress)
    inventory = _inventory(records)
    plan = {"format": 1, "workspace_id": store.workspace_id, "user_id": store.user_id,
            "files": snapshot.files, "objects": objects, "inventory": inventory}
    migration_id = _hash(plan)
    plan_reference = artifacts.put(_json(plan).encode())
    if artifacts.get(plan_reference["sha256"], max_bytes=plan_reference["byte_size"]) != _json(plan).encode():
        raise ValueError("Migration plan checksum mismatch")
    _emit(progress, "PLAN_VERIFIED", migration_id=migration_id, plan_sha256=plan_reference["sha256"])
    for table, rows in records.items():
        if rows:
            _emit(progress, "DELTA_WRITE_BEGIN", table=table, rows=len(rows))
            store.upsert_records(table, rows, migration_id=migration_id)
            _emit(progress, "DELTA_WRITE_OK", table=table, rows=len(rows))
    verification = validate_live(store, inventory, migration_id, progress=progress)
    receipt = store.make_record(
        "migration_receipts", project_id="__migration__", package_hash="__none__", context_id="migration",
        source_path="migration", source_table="migration", source_key=migration_id,
        payload={"migration_id": migration_id, "plan": plan_reference, "verification": verification,
                 "legacy_actors_authenticated": False}, status="verified",
        artifact_path=plan_reference["volume_path"], artifact_sha256=plan_reference["sha256"], byte_size=plan_reference["byte_size"])
    store.upsert_records("migration_receipts", [receipt], migration_id=migration_id)
    validate_live(store, {"migration_receipts": _inventory({"receipt": [receipt]})["receipt"]}, migration_id, progress=progress)
    _emit(progress, "RECEIPT_VERIFIED", migration_id=migration_id)
    return {"migration_id": migration_id, "plan": plan_reference, "tables": verification,
            "hosted_authority_integrated": False}


def restore_snapshot(artifacts, plan, destination):
    """Reconstruct isolated files, including original SQLite backups, never open them.

    Destination must be empty. This exports recovery snapshots, not a fabricated
    SQL-to-SQLite reconstruction of later live Delta changes.
    """
    if plan.get("workspace_id") != artifacts.store.workspace_id or plan.get("user_id") != artifacts.store.user_id:
        raise PermissionError("Cross-user snapshot export forbidden")
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Export destination must be empty")
    for item in plan["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or any(p in {".", ".."} or ":" in p or "\\" in p for p in relative.parts):
            raise ValueError("Unsafe recovery path")
        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise ValueError("Unsafe recovery path")
        data = artifacts.get(item["sha256"], max_bytes=item["byte_size"])
        if len(data) != item["byte_size"]:
            raise ValueError("Snapshot recovery size mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return len(plan["files"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "ensure", "migrate", "validate", "export"))
    parser.add_argument("--source", default="output/workbench")
    parser.add_argument("--destination")
    parser.add_argument("--profile", default="labs")
    parser.add_argument("--warehouse")
    parser.add_argument("--catalog")
    parser.add_argument("--schema", default="workbench")
    parser.add_argument("--volume", default="artifacts")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            # Truly offline: no WorkspaceClient, OAuth resolution or remote calls.
            with tempfile.TemporaryDirectory(prefix="workbench-delta-inventory-") as temp:
                snapshot = snapshot_local(args.source, temp)
                print(_json({"files": snapshot.files, "count": len(snapshot.files), "remote_writes": False}))
            return 0
        if not args.warehouse or not args.catalog:
            raise ValueError("An explicit user-authorized warehouse and catalog are required")
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient(profile=args.profile)
        store = DeltaStateStore(client, args.warehouse, args.catalog, args.schema, client.current_user.me().id)
        artifacts = VolumeArtifacts(store, args.volume)
        if args.command == "ensure":
            result = {"tables": store.ensure_tables()}
        elif args.command == "migrate":
            with tempfile.TemporaryDirectory(prefix="workbench-delta-migration-") as temp:
                result = migrate(store, snapshot_local(args.source, temp), artifacts)
        else:
            if not args.plan_sha256:
                raise ValueError("Plan checksum is required")
            raw = artifacts.get(args.plan_sha256, max_bytes=64 * 1024 * 1024)
            plan = json.loads(raw)
            if plan.get("workspace_id") != store.workspace_id or plan.get("user_id") != store.user_id:
                raise PermissionError("Plan belongs to a different workspace/user")
            if args.command == "validate":
                result = {"migration_id": _hash(plan), "tables": validate_live(store, plan["inventory"], _hash(plan))}
                # Verify all backup/source bytes independently as well as rows.
                for item in plan["objects"]:
                    if len(artifacts.get(item["sha256"], max_bytes=item["byte_size"])) != item["byte_size"]:
                        raise ValueError("Artifact size mismatch")
                result["artifact_hashes_verified"] = len(plan["objects"])
            else:
                if not args.destination:
                    raise ValueError("Empty export destination is required")
                result = {"exported_files": restore_snapshot(artifacts, plan, args.destination)}
        print(_json(result))
        return 0
    except Exception:
        print("Delta state operation failed. No credentials or provider error details are printed. Inspect input and workspace query history securely.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())