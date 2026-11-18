"""Persistent local single-user projects, not a production tenancy boundary.

Selection is request-scoped: a child service never changes package in response
to a read. Batch ingestion uses short-lived children and one bounded worker.
Only two idle/request contexts are retained; busy agent contexts are pinned.
No dotenv, external calls, implicit ingestion, or client-supplied local paths.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from threading import BoundedSemaphore, RLock
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

MAX_FILES = 10
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_BATCH_BYTES = 160 * 1024 * 1024
PROJECT_ID = re.compile(r"[a-f0-9]{32}\Z")
PACKAGE_ID = re.compile(r"[a-f0-9]{64}\Z")


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)

    @field_validator("name", "description")
    @classmethod
    def bounded_text(cls, value):
        if any(ord(c) < 32 and c not in "\n\t" for c in value) or "\x7f" in value:
            raise ValueError("Project text contains control characters")
        return value.strip()

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value:
            raise ValueError("Project name is required")
        return value


class SampleBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    names: list[str] = Field(min_length=1, max_length=MAX_FILES)


class ProjectRegistry:
    """Small JSON records in SQLite, independent connections per transaction."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "projects.sqlite"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS packages(project_id TEXT, hash TEXT, record TEXT NOT NULL,
                    PRIMARY KEY(project_id, hash));
                CREATE TABLE IF NOT EXISTS jobs(project_id TEXT, id TEXT, record TEXT NOT NULL,
                    PRIMARY KEY(project_id, id));
            """)

    def connect(self):
        return sqlite3.connect(self.database, timeout=30)

    def create(self, body, *, project_id=None, extra=None):
        from workbench.app import _now
        record = {"id": project_id or uuid4().hex, **body.model_dump(), **(extra or {}), "created_at": _now(),
                  "local_only": True, "authentication": "not_implemented"}
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO projects VALUES (?,?)", (record["id"], json.dumps(record)))
        return record

    def rename(self, project_id, body):
        from workbench.app import _now
        self.project(project_id)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            record = json.loads(db.execute("SELECT record FROM projects WHERE id=?", (project_id,)).fetchone()[0])
            record.update(**body.model_dump(), updated_at=_now())
            db.execute("UPDATE projects SET record=? WHERE id=?", (json.dumps(record), project_id))
        return record

    def project(self, project_id):
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(404, "Unknown project ID")
        with closing(self.connect()) as db:
            row = db.execute("SELECT record FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown project ID")
        return json.loads(row[0])

    def projects(self):
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM projects ORDER BY rowid DESC")]

    def records(self, table, project_id):
        assert table in {"packages", "jobs"}
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute(
                f"SELECT record FROM {table} WHERE project_id=? ORDER BY rowid DESC", (project_id,))]

    def save(self, table, project_id, record):
        assert table in {"packages", "jobs"}
        key = record["sha256"] if table == "packages" else record["id"]
        with closing(self.connect()) as db, db:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (project_id, key, json.dumps(record)))

    def member(self, project_id, package_hash, *, ready=True):
        if not PACKAGE_ID.fullmatch(package_hash):
            raise HTTPException(403, "Package is not a ready member of this project")
        with closing(self.connect()) as db:
            row = db.execute("SELECT record FROM packages WHERE project_id=? AND hash=?", (project_id, package_hash)).fetchone()
        if row is None or (ready and json.loads(row[0])["status"] != "completed"):
            raise HTTPException(403, "Package is not a ready member of this project")
        return json.loads(row[0])

    def job(self, project_id, job_id):
        self.project(project_id)
        if not PROJECT_ID.fullmatch(job_id):
            raise HTTPException(404, "Unknown project job ID")
        with closing(self.connect()) as db:
            row = db.execute("SELECT record FROM jobs WHERE project_id=? AND id=?", (project_id, job_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown project job ID")
        return json.loads(row[0])


class ProjectManager:
    def __init__(self, root, factory, sample, input_root, *, executor_factory=ThreadPoolExecutor, source_publisher=None):
        self.root = Path(root) / "projects"
        self.registry = ProjectRegistry(self.root)
        self.factory, self.sample = factory, sample
        self.input_root = Path(input_root).resolve()
        self.uploads = self.root / ".uploads"
        self.uploads.mkdir(exist_ok=True)
        self.source_publisher = source_publisher
        self.executor = executor_factory(max_workers=1, thread_name_prefix="workbench-project-batch")
        self.slot = BoundedSemaphore(1)
        self.lock = RLock()
        self.contexts = {}
        self.futures = {}

    def start(self):
        from workbench.app import _now
        for project in self.registry.projects():
            for record in self.registry.records("jobs", project["id"]):
                if record["status"] in {"queued", "running"}:
                    for item in record["items"]:
                        if item["status"] in {"queued", "running"}:
                            item.update(status="failed", error="Process interrupted; not automatically resumed.")
                            if item.get("sha256"):
                                self.registry.save("packages", project["id"], item)
                    record.update(status="failed", error="Process interrupted; not automatically resumed.", finished_at=_now())
                    self.registry.save("jobs", project["id"], record)
        for path in self.uploads.glob("*.xml"):
            path.unlink(missing_ok=True)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
        with self.lock:
            for entry in self.contexts.values():
                entry["app"].state.service.close()
                entry["app"].state.service.active = None
            self.contexts.clear()

    def context_root(self, project_id, package_hash):
        # Both values are registry-validated before reaching this function.
        return self.root / project_id / "package-contexts" / package_hash

    def acquire(self, project_id, package_hash):
        self.registry.member(project_id, package_hash)
        key = (project_id, package_hash)
        with self.lock:
            entry = self.contexts.get(key)
            if entry is None:
                if len(self.contexts) >= 2:
                    for old_key, old in list(self.contexts.items()):
                        service = old["app"].state.service
                        if old["leases"] == 0 and service.agent_slot.acquire(blocking=False):
                            service.agent_slot.release()
                            service.close()
                            service.active = None
                            del self.contexts[old_key]
                            break
                    else:
                        raise HTTPException(409, "Package contexts are busy; retry after current requests or agent runs finish")
                child = self.factory(self.context_root(*key), self.sample)
                try:
                    child.state.service.start()
                    index = child.state.service.require()
                    if index.model["package"]["sha256"] != package_hash:
                        raise HTTPException(409, "Persisted project package does not match selection")
                except Exception:
                    child.state.service.close()
                    raise
                entry = {"app": child, "leases": 0}
                self.contexts[key] = entry
            entry["leases"] += 1
            return entry["app"]

    def release(self, project_id, package_hash):
        with self.lock:
            self.contexts[(project_id, package_hash)]["leases"] -= 1

    def inputs(self):
        from workbench.app import _filename
        items = []
        for path in sorted(self.input_root.iterdir() if self.input_root.is_dir() else [], key=lambda p: p.name.casefold()):
            if path.suffix.lower() != ".xml" or path.is_symlink() or not path.is_file():
                continue
            try:
                _filename(path.name, {".xml"})
            except HTTPException:
                continue
            items.append({"name": path.name, "size_bytes": path.stat().st_size})
        return items

    def reserve(self, project_id):
        self.registry.project(project_id)
        if not self.slot.acquire(blocking=False):
            raise HTTPException(409, "A project batch or upload is already in progress; no batches are queued")

    def stage(self, project_id, files):
        """Retain hash-addressed XML snapshots without parsing or starting jobs."""
        from workbench.app import _digest, _now
        pending = {p["sha256"]: p["size_bytes"] for p in self.registry.records("packages", project_id)
                   if p["status"] == "staged"}
        for _, _, digest, size in files:
            try:
                self.registry.member(project_id, digest)
            except HTTPException:
                pending[digest] = size
        from workbench.intake_limits import max_batch_bytes
        batch_limit = max_batch_bytes() if self.source_publisher is not None else MAX_BATCH_BYTES
        if len(pending) > MAX_FILES or sum(pending.values()) > batch_limit:
            raise HTTPException(413, "Staged packages exceed the ten-file or aggregate batch limit")
        if self.source_publisher is not None:
            for path, name, digest, _ in files:
                self.source_publisher(path, name, digest)
        directory = self.root / project_id / "staged"
        directory.mkdir(parents=True, exist_ok=True)
        items = []
        for path, name, digest, size in files:
            destination = directory / (digest + ".xml")
            if destination.exists():
                if destination.is_symlink() or _digest(destination) != digest:
                    raise HTTPException(409, "Staged snapshot checksum mismatch")
            else:
                os.replace(path, destination)
            try:
                previous = self.registry.member(project_id, digest, ready=False)
            except HTTPException:
                previous = None
            if previous and previous["status"] == "completed":
                items.append({**previous, "reused": True})
                continue
            record = {"name": name, "sha256": digest, "size_bytes": size, "status": "staged", "created_at": _now()}
            self.registry.save("packages", project_id, record)
            items.append(record)
        return {"status": "staged", "project_id": project_id, "items": items,
                "total": len(items), "analysis_started": False, "job_id": None}

    def analyze(self, project_id):
        from workbench.app import _digest
        self.reserve(project_id)
        files, submitted, total = [], False, 0
        try:
            staged = sorted((p for p in self.registry.records("packages", project_id) if p["status"] == "staged"),
                            key=lambda p: p["sha256"])
            if not staged:
                raise HTTPException(409, "No staged packages to analyze")
            if len(staged) > MAX_FILES:
                raise HTTPException(413, "At most ten staged packages may be analyzed in one batch")
            for record in staged:
                digest = record["sha256"]
                if not PACKAGE_ID.fullmatch(digest):
                    raise HTTPException(409, "Invalid staged package record")
                source = self.root / project_id / "staged" / (digest + ".xml")
                if source.is_symlink() or not source.is_file() or _digest(source) != digest:
                    raise HTTPException(409, "Staged snapshot checksum mismatch")
                from workbench.intake_limits import max_xml_bytes, max_batch_bytes
                item = _snapshot(source, self.uploads, record["name"], total,
                                 max_file=max_xml_bytes() if self.source_publisher is not None else MAX_FILE_BYTES,
                                 max_batch=max_batch_bytes() if self.source_publisher is not None else MAX_BATCH_BYTES)
                files.append(item)
                total += item[3]
                if item[2] != digest:
                    raise HTTPException(409, "Staged snapshot checksum mismatch")
            result = self.submit(project_id, files)
            submitted = True
            return result
        finally:
            if not submitted:
                for path, *_ in files:
                    path.unlink(missing_ok=True)
                self.slot.release()

    def adopt_current(self, service, body):
        """Isolate legacy artifacts and a transactional SQLite backup, never start it.

        Publication happens only after the snapshot is complete. Names are display
        metadata, never paths. Existing immutable files may be hard-linked; the
        mutable review database is always backed up into an independent file.
        """
        from workbench.app import PackageIndex, _atomic_json, _digest, _now
        if not self.slot.acquire(blocking=False):
            raise HTTPException(409, "A project batch or upload is already in progress")
        held = []
        context = None
        published = False
        try:
            for slot in (service.package_slot, service.agent_slot):
                if not slot.acquire(blocking=False):
                    raise HTTPException(409, "Legacy package or agent work is running; retry after it finishes")
                held.append(slot)
            with service.lock:
                index = service.require()
                digest = index.model["package"]["sha256"]
                PackageIndex.load(index.directory)
                if _digest(index.directory / "source.xml") != digest:
                    raise HTTPException(409, "Legacy source artifact checksum mismatch")
                for project in self.registry.projects():
                    if project.get("legacy_snapshot_sha256") == digest:
                        member = self.registry.member(project["id"], digest)
                        return {**project, "package": member, "reused": True}
                project_id = uuid4().hex
                context = self.context_root(project_id, digest)
                destination = context / "packages" / digest
                destination.parent.mkdir(parents=True)
                if index.directory.is_symlink() or any(p.is_symlink() for p in index.directory.rglob("*")):
                    raise ValueError("Symlink artifacts are not supported")
                shutil.copytree(index.directory, destination, copy_function=_link_or_copy)
                PackageIndex.load(destination)
                state = context / "state"
                state.mkdir()
                with closing(sqlite3.connect(service.store.db_path)) as source, closing(sqlite3.connect(state / "reviews.sqlite")) as backup:
                    source.backup(backup)
                _atomic_json(state / "active.json", {"sha256": digest, "activated_at": _now()})
                manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
                member = {"name": manifest["filename"], "sha256": digest, "package_hash": digest,
                          "size_bytes": manifest["size_bytes"], "status": "completed", "imported": True,
                          "counts": manifest["counts"], "artifacts": manifest["artifacts"],
                          "node_count": manifest["node_count"], "edge_count": manifest["edge_count"],
                          "created_at": _now()}
                # One transaction publishes project membership and the server marker.
                project = {"id": project_id, **body.model_dump(), "created_at": _now(),
                           "local_only": True, "authentication": "not_implemented", "legacy_snapshot_sha256": digest}
                _atomic_json(self.root / project_id / "active.json", {"sha256": digest, "activated_at": _now()})
                with closing(self.registry.connect()) as db, db:
                    db.execute("INSERT INTO projects VALUES (?,?)", (project_id, json.dumps(project)))
                    db.execute("INSERT INTO packages VALUES (?,?,?)", (project_id, digest, json.dumps(member)))
                published = True
                return {**project, "package": member, "reused": False}
        except (OSError, ValueError, KeyError, sqlite3.Error):
            raise HTTPException(409, "Legacy artifact verification or snapshot failed; originals are unchanged") from None
        finally:
            if context is not None and not published:
                shutil.rmtree(context.parent.parent, ignore_errors=True)
            for slot in reversed(held):
                slot.release()
            self.slot.release()

    def submit(self, project_id, files):
        from workbench.app import _now
        if self.source_publisher is not None:
            for path, name, digest, _ in files:
                self.source_publisher(path, name, digest)
        record = {"id": uuid4().hex, "project_id": project_id, "kind": "package_batch", "status": "queued",
                  "created_at": _now(), "total": len(files), "processed": 0,
                  "items": [{"name": name, "sha256": digest, "size_bytes": size, "status": "queued"}
                            for _, name, digest, size in files]}
        self.registry.save("jobs", project_id, record)
        with self.lock:
            # Retain only the current completion handle, not an unbounded queue.
            self.futures = {record["id"]: self.executor.submit(self.run_batch, record, files)}
        return record

    def run_batch(self, initial, files):
        from workbench.app import _atomic_json, _now
        record = json.loads(json.dumps(initial))
        project_id = record["project_id"]

        def log_failure(item):
            audit = getattr(self, "audit", None)
            if audit is None:
                return
            diagnostic = item.get("error_diagnostic", {})
            try:
                audit.append({"event": "project_package_import_failed", "level": "ERROR", "role": None,
                    "run_id": record["id"], "message": "Package import failed during "
                    + diagnostic.get("stage", "project_batch") + ": " + diagnostic.get("error_type", "Exception") + ".",
                    "diagnostic": diagnostic})
            except Exception:
                item["audit_status"] = "write_failed"

        try:
            record.update(status="running", started_at=_now())
            self.registry.save("jobs", project_id, record)
            for position, (path, name, digest, size) in enumerate(files):
                item = record["items"][position]
                child = None
                try:
                    try:
                        member = self.registry.member(project_id, digest)
                    except HTTPException:
                        member = None
                    if member:
                        item.update(status="skipped", reason="duplicate_sha256", package_hash=digest)
                    else:
                        item.update(status="running", created_at=_now())
                        self.registry.save("packages", project_id, item)
                        self.registry.save("jobs", project_id, record)
                        child = self.factory(self.context_root(project_id, digest), self.sample)
                        service = child.state.service
                        # New context, with no background reader or competing job.
                        service.start()
                        service.package_slot.acquire()
                        worker = {"id": uuid4().hex, "status": "queued", "kind": "package", "filename": name, "created_at": _now()}
                        item["worker_job_id"] = worker["id"]
                        self.registry.save("jobs", project_id, record)
                        service._ingest(worker, path, name, False)
                        result = service.record(worker["id"])
                        item.update(status=result["status"], worker_job_id=worker["id"], finished_at=_now())
                        if result["status"] == "completed":
                            if result["package_hash"] != digest:
                                raise ValueError("Snapshot checksum mismatch")
                            manifest = json.loads((service.require().directory / "manifest.json").read_text(encoding="utf-8"))
                            item.update(package_hash=digest, counts=manifest["counts"],
                                        artifacts=manifest["artifacts"], node_count=result["node_count"], edge_count=result["edge_count"])
                            self.attach_documents(project_id, service, digest)
                            _atomic_json(self.root / project_id / "active.json", {"sha256": digest, "activated_at": _now()})
                        else:
                            item["error"] = result["error"]
                            if result.get("error_diagnostic"):
                                item["error_diagnostic"] = result["error_diagnostic"]
                            if result.get("audit_status"):
                                item["worker_audit_status"] = result["audit_status"]
                            log_failure(item)
                        self.registry.save("packages", project_id, item)
                except Exception as exc:
                    kind = type(exc).__name__
                    item.update(status="failed", error="Package ingestion or persistence failed.", finished_at=_now(),
                                error_diagnostic={"stage": "project_batch", "error_type": kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", kind) else "Exception"})
                    log_failure(item)
                    self.registry.save("packages", project_id, item)
                finally:
                    if child is not None:
                        child.state.service.close()
                        child.state.service.active = None
                    path.unlink(missing_ok=True)
                record["processed"] = position + 1
                self.registry.save("jobs", project_id, record)
            failures = sum(item["status"] == "failed" for item in record["items"])
            record.update(status="failed" if failures == len(files) else "partial" if failures else "completed",
                          completed_count=sum(i["status"] == "completed" for i in record["items"]),
                          skipped_count=sum(i["status"] == "skipped" for i in record["items"]), failed_count=failures)
        except Exception as exc:
            kind = type(exc).__name__
            record.update(status="failed", error="Batch persistence failed; inspect package membership before retrying.",
                          error_diagnostic={"stage": "batch_persistence", "error_type": kind if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", kind) else "Exception"})
            log_failure(record)
        finally:
            for path, *_ in files:
                path.unlink(missing_ok=True)
            record["finished_at"] = _now()
            try:
                self.registry.save("jobs", project_id, record)
            finally:
                self.slot.release()

    def documents(self, project_id):
        self.registry.project(project_id)
        directory = self.root / project_id / "documents"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]

    def attach_documents(self, project_id, service, digest):
        for document in self.documents(project_id):
            service.store.save_document(digest, document["name"], document["text"], document["metadata"])


def _link_or_copy(source, destination):
    if Path(source).is_symlink():
        raise ValueError("Symlink artifacts are not supported")
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)
    return destination


def _snapshot(source, directory, name, total, *, max_file=None, max_batch=None):
    """Stream a stable local snapshot with the same limits as an upload."""
    # Resolve defaults at call time so deployment/runtime configuration and
    # narrowly-scoped test overrides are honored consistently.
    max_file = MAX_FILE_BYTES if max_file is None else max_file
    max_batch = MAX_BATCH_BYTES if max_batch is None else max_batch
    descriptor, destination = tempfile.mkstemp(suffix=".xml", dir=directory)
    path = Path(destination)
    digest, size = sha256(), 0
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > max_file or total + size > max_batch:
                    raise HTTPException(413, "Package or aggregate batch size limit exceeded")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "Empty files are not supported")
        return path, name, digest.hexdigest(), size
    except Exception:
        path.unlink(missing_ok=True)
        raise


async def _uploads(request, manager):
    from workbench.app import _filename
    files, total = [], 0
    form = await request.form(max_files=MAX_FILES, max_fields=0, max_part_size=16384)
    try:
        uploads = form.getlist("files")
        if set(form) != {"files"} or not 1 <= len(uploads) <= MAX_FILES:
            raise HTTPException(400, "Supply one to ten XML files using multipart field files")
        for upload in uploads:
            if not hasattr(upload, "filename") or not hasattr(upload, "read"):
                raise HTTPException(400, "XML file uploads are required")
            original = re.search(r'filename\s*=\s*(?:"([^"]*)"|([^;]*))', upload.headers.get("content-disposition", ""), re.I)
            if original:
                _filename(original.group(1) if original.group(1) is not None else original.group(2), {".xml"})
            name = _filename(upload.filename, {".xml"})
            descriptor, destination = tempfile.mkstemp(suffix=".xml", dir=manager.uploads)
            path, size, digest = Path(destination), 0, sha256()
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES or total + size > MAX_BATCH_BYTES:
                            raise HTTPException(413, "Package or aggregate batch size limit exceeded")
                        digest.update(chunk)
                        await run_in_threadpool(stream.write, chunk)
                if not size:
                    raise HTTPException(400, "Empty files are not supported")
                files.append((path, name, digest.hexdigest(), size))
                total += size
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return files
    except Exception:
        for path, *_ in files:
            path.unlink(missing_ok=True)
        raise
    finally:
        await form.close()


class ProjectDispatch:
    def __init__(self, app, manager):
        from workbench.app import _LocalBoundary
        self.app, self.manager = app, manager
        # Outermost security also covers direct child dispatch, before parsing.
        self.boundary = _LocalBoundary(self.dispatch)

    async def __call__(self, scope, receive, send):
        return await self.boundary(scope, receive, send)

    async def dispatch(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v.decode("latin1") for k, v in scope["headers"]}
        project_id, digest = headers.get(b"x-workbench-project"), headers.get(b"x-workbench-package")
        management = (scope["path"] in {"/api/projects", "/api/input-packages", "/api/databricks/volumes"}
                  or scope["path"].startswith(("/api/projects/", "/api/databricks/volumes/")))
        try:
            if scope["path"] == "/api/models/workspace" and scope["method"] == "GET":
                # Deployment/profile metadata has no project data or membership
                # dependency. Do not acquire a package or read its SQLite state.
                return await self.app(scope, receive, send)
            if management:
                if project_id:
                    await run_in_threadpool(self.manager.registry.project, project_id)
                    parts = scope["path"].split("/")
                    if len(parts) > 3 and parts[2] == "projects" and parts[3] not in {project_id, "adopt-current"}:
                        raise HTTPException(403, "Project header and URL do not match")
                return await self.app(scope, receive, send)
            if not project_id:
                if digest:
                    raise HTTPException(400, "X-Workbench-Package requires X-Workbench-Project")
                return await self.app(scope, receive, send)
            project = await run_in_threadpool(self.manager.registry.project, project_id)
            if not digest and scope["path"] in {"/api/models/status", "/api/models/connect", "/api/models/select"}:
                # Without a package, configure the user/root route, not a
                # fabricated package context. Membership is still checked above.
                return await self.app(scope, receive, send)
            if scope["path"].startswith("/api/packages/"):
                raise HTTPException(409, "Use the project batch sample/upload endpoints to preserve membership")
            if not digest:
                if scope["path"] == "/api/overview" and scope["method"] == "GET":
                    response = JSONResponse({"ready": False, "status": "package_selection_required", "package": None,
                                             "project": project, "generation_allowed": False, "deployment_allowed": False})
                    return await response(scope, receive, send)
                raise HTTPException(409, "Select a project package with X-Workbench-Package")
            from urllib.parse import parse_qs
            query = parse_qs(scope.get("query_string", b"").decode("latin1"))
            status_path = re.fullmatch(r"/api/agents/runs/([a-f0-9]{32})", scope["path"])
            status_list = scope["path"] == "/api/agents/runs"
            if (scope["method"] == "GET" and (status_path or status_list)
                    and query.get("status_only") == ["true"]):
                # Never acquire/start a child context just to observe progress.
                # Membership remains a fresh registry read, not a cached lease.
                await run_in_threadpool(self.manager.registry.member, project_id, digest)
                from workbench.app import _stored_agent_runs, _stored_agent_status
                root = self.manager.context_root(project_id, digest)
                if status_list:
                    try:
                        offset = int(query.get("offset", ["0"])[0])
                        limit = int(query.get("limit", ["50"])[0])
                        if len(query.get("offset", ["0"])) != 1 or len(query.get("limit", ["50"])) != 1:
                            raise ValueError
                    except ValueError:
                        raise HTTPException(422, "Invalid saved-run pagination") from None
                    result = await run_in_threadpool(_stored_agent_runs, root, offset, limit, digest)
                else:
                    result = await run_in_threadpool(_stored_agent_status, root, status_path[1], digest)
                result["project_id"] = project["id"]
                return await JSONResponse(result)(scope, receive, send)
            child = await run_in_threadpool(self.manager.acquire, project_id, digest)
            try:
                return await child(scope, receive, send)
            finally:
                await run_in_threadpool(self.manager.release, project_id, digest)
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)


def install_projects(app, factory, input_root, *, executor_factory=ThreadPoolExecutor, source_publisher=None):
    service = app.state.service
    manager = ProjectManager(service.root, factory, service.sample, input_root,
                             executor_factory=executor_factory, source_publisher=source_publisher)
    manager.audit = getattr(app.state, "audit", None)
    app.state.projects = manager
    app.add_middleware(ProjectDispatch, manager=manager)

    @app.get("/api/projects")
    def projects():
        items = []
        for project in manager.registry.projects():
            packages = manager.registry.records("packages", project["id"])
            items.append({**project, "package_count": len(packages),
                          "ready_package_count": sum(p["status"] == "completed" for p in packages)})
        return {"items": items}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectBody):
        return manager.registry.create(body)

    @app.post("/api/projects/adopt-current", status_code=201)
    def adopt_current(body: ProjectBody):
        return manager.adopt_current(service, body)

    @app.patch("/api/projects/{project_id}")
    def rename_project(project_id: str, body: ProjectBody):
        return manager.registry.rename(project_id, body)

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: str):
        project = manager.registry.project(project_id)
        packages = manager.registry.records("packages", project_id)
        jobs = manager.registry.records("jobs", project_id)
        return {**project, "packages": packages, "jobs": jobs, "package_count": len(packages),
                "ready_package_count": sum(p["status"] == "completed" for p in packages)}

    @app.get("/api/input-packages")
    def input_packages():
        return {"items": manager.inputs()}

    @app.get("/api/projects/{project_id}/jobs/{job_id}")
    def job(project_id: str, job_id: str):
        return manager.registry.job(project_id, job_id)

    @app.get("/api/projects/{project_id}/jobs/{job_id}/logs")
    def job_logs(project_id: str, job_id: str, level: str = "", role: str = "", run_id: str = "",
                 offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        manager.registry.job(project_id, job_id)
        audit = getattr(manager, "audit", None)
        if audit is None:
            raise HTTPException(503, "Project job audit log is unavailable")
        rows, valid = audit.records()
        rows = [row for row in rows if row.get("run_id") == job_id
                and (not level or row.get("level") == level.upper())
                and (not role or row.get("role") == role)]
        rows.reverse()
        return {"items": rows[offset:offset + limit], "total": len(rows), "chain_valid": valid,
                "storage": "local project import audit", "offset": offset, "limit": limit}

    @app.post("/api/projects/{project_id}/analyze", status_code=202)
    def analyze(project_id: str):
        return manager.analyze(project_id)

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        from workbench.app import _document_display
        items = manager.documents(project_id)
        return {"items": [_document_display(d) for d in items[offset:offset + limit]], "total": len(items),
                "offset": offset, "limit": limit, "truncated": offset + limit < len(items)}

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    async def upload_document(project_id: str, request: Request):
        from workbench.app import MAX_DOCUMENT_BYTES, _atomic_json, _digest, _document_display, _document_text, _now, _upload
        from workbench.domain import _hash, _redact
        manager.registry.project(project_id)
        temp, name, metadata = await _upload(request, service, {".txt", ".md", ".pdf"}, MAX_DOCUMENT_BYTES, ".evidence")
        try:
            if metadata["workflow_id"]:
                raise HTTPException(422, "Project evidence is project-wide; workflow scope requires a selected package")
            text = await run_in_threadpool(_document_text, temp, name)
            if not text.strip():
                raise HTTPException(422, "Document has no extractable text")
            digest = await run_in_threadpool(_digest, temp)
            directory = manager.root / project_id / "documents"
            directory.mkdir(parents=True, exist_ok=True)
            original = directory / (digest + Path(name).suffix.lower())
            document_id = _hash([digest, name, metadata])
            record_path = directory / (document_id + ".json")
            with manager.lock:
                if not original.exists():
                    os.replace(temp, original)
                if record_path.exists():
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                else:
                    record = _redact({"id": document_id, "name": name, "filename": name, "text": text, "created_at": _now(),
                                      "metadata": {**metadata, "sha256": digest, "stored_name": original.name,
                                                   "project_id": project_id, "scope": "project_wide",
                                                   "verification_status": "unverified", "actor_authenticated": False,
                                                   "raw_bytes_sensitive": True}})
                    _atomic_json(record_path, record)
            return {**_document_display(record), "semantic_review_started": False,
                    "attachment_policy": "project_wide_snapshot_on_successful_analysis"}
        finally:
            temp.unlink(missing_ok=True)

    @app.post("/api/projects/{project_id}/packages/sample", status_code=202)
    def samples(project_id: str, body: SampleBody, stage_only: bool = False):
        from workbench.app import _filename
        manager.reserve(project_id)
        files, submitted = [], False
        try:
            available = {item["name"] for item in manager.inputs()}
            total = 0
            for name in body.names:
                _filename(name, {".xml"})
                if name not in available:
                    raise HTTPException(404, "Unknown trusted input XML basename")
                source = manager.input_root / name
                if source.is_symlink() or source.resolve().parent != manager.input_root:
                    raise HTTPException(400, "Input must be a regular trusted XML file")
                item = _snapshot(source, manager.uploads, name, total)
                files.append(item)
                total += item[3]
            if stage_only:
                return manager.stage(project_id, files)
            record = manager.submit(project_id, files)
            submitted = True
            return record
        finally:
            if not submitted:
                for path, *_ in files:
                    path.unlink(missing_ok=True)
                manager.slot.release()

    @app.post("/api/projects/{project_id}/packages/upload", status_code=202)
    async def upload(project_id: str, request: Request, stage_only: bool = False):
        await run_in_threadpool(manager.reserve, project_id)
        files, submitted = [], False
        try:
            files = await _uploads(request, manager)
            if stage_only:
                return await run_in_threadpool(manager.stage, project_id, files)
            record = await run_in_threadpool(manager.submit, project_id, files)
            submitted = True
            return record
        finally:
            if not submitted:
                for path, *_ in files:
                    path.unlink(missing_ok=True)
                manager.slot.release()