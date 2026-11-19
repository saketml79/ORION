"""Hosted authority: committed Delta generations and immutable Volume artifacts.

SQLite is an empty, disposable execution cache reconstructed from Delta source
columns. Database artifacts contain schema DDL, NEVER a database row snapshot.
Commit-last visibility requires one process and one action per user. It is not a
multi-table cloud transaction, distributed lock, or durable background worker.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from hashlib import sha256
import gc
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
from workbench.hosted_storage import MAX_BYTES, MAX_FILES, VolumeSnapshots
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


def _container_memory_state():
    """Return current/limit from the Linux cgroup memory controller if exposed."""
    for current_path, limit_path in (
            ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
            ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
             "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            current = int(Path(current_path).read_text(encoding="ascii").strip())
            raw_limit = Path(limit_path).read_text(encoding="ascii").strip()
            if raw_limit == "max":
                return None
            limit = int(raw_limit)
            if 0 < current <= limit < (1 << 60):
                return current, limit
        except (OSError, ValueError):
            continue
    return None


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
        self.ready = False
        self._audit_sequences = {}

    def _checked(self, data):
        ds._safe(data)
        if self.forbidden and self.forbidden in data:
            raise ValueError("Credential material cannot be persisted")
        return data

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
        raw = self.artifacts.get(digest, max_bytes=size)
        if len(raw) != size or sha256(raw).hexdigest() != digest:
            raise ValueError("Artifact size or checksum mismatch")
        return self._checked(raw)

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
        if not self.ready:
            raise ValueError("Authoritative restore must succeed before audit commit")
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

    def restore(self, *, initialize=True, progress=None):
        """Restore authority, initializing missing Delta tables on mutations only.

        Hosted observational requests pass initialize=False. They still authorize
        this fresh user's namespace, verify each table actually used and execute
        real OBO SQL. An empty generation needs only receipts and the audit journal,
        not schema checks for the other nineteen metadata families. No schema or
        privilege verdict is cached across requests; missing tables fail closed.
        """
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
                self.ready = True
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
        records = self._records(manifest["inventory"], generation)
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
        # Build outside the cache, publish locally only after complete validation.
        with tempfile.TemporaryDirectory(prefix="workbench-hydrate-") as temporary:
            staging = Path(temporary)
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
                    self._verify_source(reference, target)
                    self.reference(reference["sha256"], reference["volume_path"])
                    verified += 1
                    if progress is not None:
                        progress("artifact_files", verified_files=verified, total_files=len(files))
                    continue
                if target.suffix in DATABASE_SUFFIXES:
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
            # Copy only after validation, without cross-directory rename locks
            # on Windows. The private cache is never observable until return.
            shutil.copytree(staging, self.local, dirs_exist_ok=True)
        self.generation, self.sequence = generation, receipt["sequence"]
        self.ready = True
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

    def flush(self):
        if not self.ready:
            raise ValueError("Authoritative restore must succeed before commit")
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
        self._records(manifest["inventory"], generation)
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
        return generation