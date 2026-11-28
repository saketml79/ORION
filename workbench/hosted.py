"""Databricks Apps only. Retained per-owner services with fresh OBO per action.

Do not expose this entrypoint outside the trusted Apps reverse proxy. Forwarded
headers are not independently verifiable JWTs. Fixed workspace and public origin
are deployment configuration, never supplied by a browser. Local entrypoints and
their loopback boundary remain unchanged.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from hashlib import sha256
import logging
import os
import json
from pathlib import Path
import re
import tempfile
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from anyio import CancelScope, Lock as AsyncLock, WouldBlock, fail_after
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from langchain_core.language_models import BaseChatModel
from starlette.concurrency import run_in_threadpool

from workbench.agents import AgentRuntime
from workbench.app import STATIC, _create_package_app
from workbench.databricks_models import (
    AuthenticationUnavailable, DiscoveryUnavailable, ROLE_PURPOSES, RoutingError,
    WorkspaceModels, _host, obo_identity,
)
from workbench.hosted_storage import provision
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_diagnostics import HostedDiagnostics, owner_key
from workbench.model_access import RouteSelection, workspace_ready
from workbench.projects import install_projects
from workbench.volume_intake import VolumeIntake, install_volume_intake

_CURRENT = ContextVar("workbench_hosted_identity")
_CURRENT_DELTA_RUNTIME: ContextVar[DeltaRuntime | None] = ContextVar(
    "workbench_hosted_delta_runtime", default=None)
_CURRENT_MUTATION = ContextVar("workbench_hosted_mutation", default=False)
_CURRENT_AGENT_RUNTIMES: ContextVar[dict[str, Any] | None] = ContextVar(
    "workbench_hosted_agent_runtimes", default=None)
_CURRENT_ACTION_RUNTIMES: ContextVar[list[Any] | None] = ContextVar(
    "workbench_hosted_action_runtimes", default=None)
HOSTED_DEFAULT_CHAT_ENDPOINT = "OpenAI-gpt-4o-mini"
HOSTED_DEFAULT_TOOL_EVIDENCE = (
    "OpenAI-gpt-4o-mini completed tool-call interactions in a prior authorized workspace trial; "
    "that trial did not produce a valid redesign, and current inference remains unverified."
)


def current_identity():
    try:
        return _CURRENT.get()
    except LookupError:
        raise AuthenticationUnavailable("No active user action; no alternate identity is available") from None


def current_delta_runtime():
    runtime = _CURRENT_DELTA_RUNTIME.get()
    if runtime is None:
        raise AuthenticationUnavailable("No active Delta request; no retained authorization is available")
    return runtime


def _verify_notebook_attachment(app, project, package, job_id, result):
    from workbench.notebook_edits import _SESSION_FIELDS

    if not isinstance(result, dict) or result.get("status") != "attached":
        raise ValueError("Invalid notebook attachment response")
    if not project and not package:
        editor = app.state.notebook_editor
    else:
        manager = app.state.projects
        with manager.lock:
            selected = manager.contexts.get((project, package))
        if selected is None:
            raise ValueError("Notebook attachment package is unavailable")
        editor = selected["app"].state.notebook_editor
    identity, user = editor.identity(result.get("workspace"))
    attachments = editor.attachments(job_id, identity, user)
    if attachments is None:
        raise ValueError("Incremental notebook attachment storage is unavailable")
    session = {key: result[key] for key in _SESSION_FIELDS}
    if "folder_object_id" in result:
        session["folder_object_id"] = result["folder_object_id"]
    if attachments.read(session["session_id"]).get(session["session_id"]) != session:
        raise ValueError("Notebook attachment was not durably verified")


async def _finish_action_before_cancellation(coroutine):
    """Keep the action and its worker threads alive until cleanup is complete."""
    task = asyncio.create_task(coroutine)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                result = task.result()
                cancelled = True
                break
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def _close_request_runtime(runtime):
    """Close model resources and drop every request-only client reference."""
    seen = set()
    nested_runtime = getattr(runtime, "_runtime", None)
    candidates = [runtime, nested_runtime]
    for candidate in tuple(candidates):
        if candidate is not None:
            candidates.extend(getattr(candidate, "role_models", {}).values()
                             if isinstance(getattr(candidate, "role_models", None), dict) else ())
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        close = getattr(candidate, "close", None)
        wrapper_closes_nested = candidate is nested_runtime and callable(getattr(type(runtime), "close", None))
        if callable(close) and not wrapper_closes_nested:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
            except Exception:
                logging.getLogger(__name__).warning("Hosted request model resource cleanup failed")
        for name in ("root_client", "root_async_client", "http_client", "http_async_client", "_client", "_async_client"):
            client = getattr(candidate, name, None)
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            for method_name in ("close", "aclose"):
                method = getattr(client, method_name, None)
                if callable(method):
                    try:
                        result = method()
                        if asyncio.iscoroutine(result):
                            asyncio.run(result)
                    except Exception:
                        logging.getLogger(__name__).warning("Hosted request model client cleanup failed")
                    break


def _meaningful_hosted_read(method, path, query):
    if method != "GET":
        return False
    return bool(
        re.fullmatch(r"/api/redesigns(?:/[a-f0-9]{64})?", path)
        or re.fullmatch(r"/api/proposals(?:/[a-f0-9]{64})?", path)
        or re.fullmatch(r"/api/jobs/[a-f0-9]{32}/spark-drafts(?:/download)?", path)
        or re.fullmatch(r"/api/jobs/[a-f0-9]{32}/native-bundles(?:/[a-f0-9]{32})?", path)
        or re.fullmatch(r"/api/workflows/[a-f0-9]{64}/migration-context", path)
        or (re.fullmatch(r"/api/agents/runs/[a-f0-9]{32}", path)
            and query.get("status_only") != ["true"])
        or path in {"/api/reviews", "/api/documents", "/api/export/understanding", "/api/export/english"}
    )


class HostedRuntimeProxy:
    """Resolve an AgentRuntime from the current request, never from app/session state."""
    def __init__(self, root):
        object.__setattr__(self, "_root", str(Path(root).resolve()))

    def _runtime(self):
        runtimes = _CURRENT_AGENT_RUNTIMES.get()
        runtime = runtimes.get(self._root) if isinstance(runtimes, dict) else None
        if runtime is None:
            raise AuthenticationUnavailable("No model runtime is active for this request")
        return runtime

    def __getattr__(self, name):
        return getattr(self._runtime(), name)

    def __setattr__(self, name, value):
        if name == "_root":
            object.__setattr__(self, name, value)
        else:
            setattr(self._runtime(), name, value)


@dataclass
class HostedSession:
    """Only nonsecret local application state is retained between OBO actions."""
    owner: str
    temporary: object
    root: Path
    gate: AsyncLock = field(default_factory=AsyncLock)
    app: FastAPI | None = None
    remote: str | None = None
    marker: tuple | None = None
    generation: str | None = None
    sequence: int = 0
    parent_inventory: dict | None = None
    copy_parent_generation: str | None = None
    copy_parent_records: dict | None = None
    copy_file_fingerprints: tuple | None = None
    copy_verified_blobs: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    committed_sources: set = field(default_factory=set)
    leases: int = 0
    discarded: bool = False
    touched: float = field(default_factory=monotonic)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    live_scope: tuple[str, str] | None = None
    save_read_scope: tuple[str, str] | None = None
    live_readers: int = 0
    live_readers_done: asyncio.Event = field(default_factory=asyncio.Event)
    audit_sequences: dict[str, int] = field(default_factory=dict)
    audit_loaded: bool = False


def _retain_copy_state(session, runtime):
    from workbench import delta_state as ds
    from workbench.delta_runtime import _GENERATION_RECORD_CACHE_MAX_BYTES

    session.copy_parent_generation = None
    session.copy_parent_records = None
    session.copy_file_fingerprints = None
    session.copy_verified_blobs = {}
    if runtime.generation is None:
        return
    records = runtime._parent_records
    if records is None:
        raise ValueError("Verified copy-on-write parent records are unavailable")
    size = 0
    for rows in records.values():
        for row in rows:
            size += len(ds._json(row).encode("utf-8"))
            if size > _GENERATION_RECORD_CACHE_MAX_BYTES:
                logging.getLogger(__name__).warning(
                    "Copy-on-write parent exceeds the retained session limit; next action will restore it")
                return
    fingerprints = runtime._parent_file_fingerprints
    if fingerprints is not None and fingerprints[0] != runtime.generation:
        raise ValueError("Copy-on-write fingerprints do not match the committed parent")
    session.copy_parent_generation = runtime.generation
    session.copy_parent_records = records
    session.copy_file_fingerprints = fingerprints
    session.copy_verified_blobs = dict(runtime._verified_blob_sizes)


def _restore_copy_state(session, runtime):
    if session.generation is None:
        return
    if session.copy_parent_generation != session.generation or session.copy_parent_records is None:
        raise ValueError("Verified copy-on-write session state is unavailable")
    runtime._parent_records = session.copy_parent_records
    runtime._parent_file_fingerprints = session.copy_file_fingerprints
    runtime._verified_blob_sizes = dict(session.copy_verified_blobs)


class HostedSessionManager:
    """Bounded, process-local app/index retention, isolated by verified owner."""
    def __init__(self, limit):
        self.limit = limit
        self.lock = Lock()
        self.sessions = {}
        self.closed = False

    @staticmethod
    def _dispose(session):
        app = session.app
        try:
            if app is not None:
                try:
                    app.state.projects.close()
                finally:
                    app.state.service.close()
        finally:
            session.app = None
            session.temporary.cleanup()

    @staticmethod
    def _idle(session):
        if session.leases:
            return False
        app = session.app
        if app is None:
            return True
        return app.state.service.idle() and app.state.projects.idle()

    def _new(self, owner):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-session-" + owner[:12] + "-")
        return HostedSession(owner, temporary, Path(temporary.name))

    @staticmethod
    def notify(session):
        previous = session.changed
        session.changed = asyncio.Event()
        previous.set()

    async def enter(self, owner, read_scope=None, save_observation=False, needs_context=False, on_wait=None):
        waiting_reported = False
        while True:
            evicted = None
            with self.lock:
                if self.closed:
                    raise HTTPException(503, "Hosted service is shutting down")
                session = self.sessions.get(owner)
                if session is not None and session.discarded and session.leases == 0:
                    self.sessions.pop(owner, None)
                    evicted = session
                    session = None
                if session is None:
                    if len(self.sessions) >= self.limit:
                        candidates = [entry for entry in self.sessions.values() if self._idle(entry)]
                        if not candidates:
                            raise HTTPException(429, "Hosted session capacity reached; all retained owners are active")
                        evicted = min(candidates, key=lambda entry: entry.touched)
                        self.sessions.pop(evicted.owner, None)
                    session = self._new(owner)
                    self.sessions[owner] = session
                session.leases += 1
                session.touched = monotonic()
            try:
                if evicted is not None:
                    await run_in_threadpool(self._dispose, evicted)
                while True:
                    changed = session.changed
                    try:
                        session.gate.acquire_nowait()
                        borrowed = False
                        break
                    except WouldBlock:
                        save_read = (save_observation and session.save_read_scope == read_scope)
                        if save_read and needs_context and session.app is not None:
                            manager = session.app.state.projects
                            with manager.lock:
                                save_read = read_scope in manager.contexts
                        if (read_scope is not None and (session.live_scope == read_scope or save_read)
                                and not session.discarded and session.app is not None):
                            session.live_readers += 1
                            session.live_readers_done.clear()
                            borrowed = True
                            break
                        if on_wait is not None and not waiting_reported:
                            await on_wait()
                            waiting_reported = True
                        await changed.wait()
            except BaseException:
                with CancelScope(shield=True):
                    await run_in_threadpool(self.release, session)
                raise
            with self.lock:
                if not session.discarded:
                    return session, borrowed
                if borrowed:
                    session.live_readers -= 1
                    if not session.live_readers:
                        session.live_readers_done.set()
                else:
                    session.gate.release()
                    self.notify(session)
            await run_in_threadpool(self.release, session)

    def invalidate(self, session):
        with self.lock:
            session.discarded = True

    def reset(self, session):
        """Replace all local state before a cold restore, while its owner gate is held."""
        self._dispose(session)
        replacement = self._new(session.owner)
        session.temporary, session.root = replacement.temporary, replacement.root
        session.app, session.remote, session.marker = None, None, None
        session.generation, session.sequence = None, 0
        session.parent_inventory = None
        session.copy_parent_generation = None
        session.copy_parent_records = None
        session.copy_file_fingerprints = None
        session.copy_verified_blobs = {}
        session.sources, session.committed_sources = {}, set()
        session.audit_sequences = {}
        session.audit_loaded = False
        session.discarded = False
        session.touched = monotonic()

    def release(self, session):
        dispose = False
        with self.lock:
            session.leases = max(0, session.leases - 1)
            session.touched = monotonic()
            if session.discarded and session.leases == 0:
                if self.sessions.get(session.owner) is session:
                    self.sessions.pop(session.owner, None)
                dispose = True
        if dispose:
            self._dispose(session)

    def close_all(self):
        with self.lock:
            self.closed = True
            sessions = tuple(self.sessions.values())
            for session in sessions:
                session.discarded = True
            idle = [session for session in sessions if not session.leases]
            for session in idle:
                self.sessions.pop(session.owner, None)
        for session in idle:
            self._dispose(session)


@contextmanager
def _preparation_trace(phase):
    """Fixed-label timings only; never log arguments, exception text or credentials."""
    started = monotonic()
    logger = logging.getLogger(__name__)
    logger.info("Hosted preparation phase=%s state=started", phase)
    try:
        yield
    except BaseException as error:
        kind = type(error)
        safe_class = kind.__name__ if kind in {ValueError, TypeError, KeyError, OSError,
                                              PermissionError, TimeoutError, RuntimeError,
                                              AssertionError} else "Exception"
        logger.info("Hosted preparation phase=%s state=failed elapsed_seconds=%.3f exception_class=%s",
                    phase, monotonic() - started, safe_class)
        raise
    else:
        logger.info("Hosted preparation phase=%s state=completed elapsed_seconds=%.3f",
                    phase, monotonic() - started)


class ActionExecutor(ThreadPoolExecutor):
    """Copy only the initiating request context, never a global last-user token."""
    def submit(self, fn, /, *args, **kwargs):
        return super().submit(copy_context().run, fn, *args, **kwargs)


@dataclass(frozen=True)
class HostedConfig:
    workspace_host: str
    app_origin: str
    catalog_prefix: str = "orion_dev_"
    max_active_users: int = 2
    warehouse: str = ""
    copy_on_write: bool = True

    def __post_init__(self):
        object.__setattr__(self, "workspace_host", _host(self.workspace_host))
        object.__setattr__(self, "app_origin", _host(self.app_origin))
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.catalog_prefix)
                or type(self.max_active_users) is not int or not 1 <= self.max_active_users <= 4):
            raise ValueError("Invalid hosted naming prefix or concurrency limit")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.warehouse):
            raise ValueError("An explicit hosted SQL warehouse is required")
        if type(self.copy_on_write) is not bool:
            raise ValueError("Invalid copy-on-write configuration")

    @classmethod
    def environment(cls):
        if os.environ.get("WORKBENCH_TRUST_DATABRICKS_PROXY") != "1":
            raise ValueError("Explicit Databricks Apps proxy trust is required")
        host = os.environ["WORKBENCH_DATABRICKS_HOST"]
        runtime_host = os.environ.get("DATABRICKS_HOST", "")
        # Databricks Apps can inject a bare DNS hostname, unlike SDK origins.
        # Normalize only a strict bare hostname, then enforce the same workspace.
        if re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", runtime_host):
            runtime_host = "https://" + runtime_host
        if _host(runtime_host) != _host(host):
            raise ValueError("Databricks runtime workspace differs from configured workspace")
        return cls(host, os.environ["WORKBENCH_APP_ORIGIN"],
                   os.environ.get("WORKBENCH_CATALOG_PREFIX", "orion_dev_"),
                   warehouse=os.environ.get("WORKBENCH_SQL_WAREHOUSE_ID", ""))


class HostedRequestFlow:
    """Per-request progress and response metadata, never retained by a session."""
    def __init__(self, dispatcher, scope, send):
        self.dispatcher, self.scope, self.send = dispatcher, scope, send
        self.event_loop = asyncio.get_running_loop()
        self.operation_progress = scope.get("workbench.operation_progress")
        self.phase, self.response_status = "authenticating", None
        self.diagnostic_id = self.diagnostic_owner = None
        self.traced_index_buckets, self.trace_lock = set(), Lock()
        self.operation_progress_buckets, self.operation_progress_lock = {}, Lock()
        self.activity_owner = self.activity_request_id = None
        self.activity_last = None
        self.activity_lock = Lock()

    def bind_activity(self, owner, request_id):
        if self.scope["method"] == "GET" and self.scope["path"] in {
                "/api/input-packages", "/api/overview", "/api/objects", "/api/package/warm"}:
            self.activity_owner, self.activity_request_id = owner, request_id

    def bind_diagnostic(self, owner, diagnostic_id):
        self.diagnostic_owner, self.diagnostic_id = owner, diagnostic_id

    def loading_progress(self, phase, **counters):
        self.phase = phase
        if self.diagnostic_id is not None:
            self.dispatcher.loading.update(self.diagnostic_owner, self.diagnostic_id, phase, **counters)
        if self.activity_owner is not None:
            values = self.operation_progress_values(counters)
            completed, total = values.get("completed"), values.get("total")
            bucket = (min(20, completed * 20 // total)
                      if type(completed) is int and type(total) is int and 0 <= completed <= total and total > 0 else None)
            observation = (phase, bucket)
            with self.activity_lock:
                if observation != self.activity_last:
                    self.activity_last = observation
                    event = self.dispatcher.diagnostics.loading_activity(
                        self.activity_owner, request_id=self.activity_request_id,
                        operation_id=self.scope.get("workbench.active_operation_id"),
                        path=self.scope["path"], phase=phase, **values)
                    logging.getLogger("uvicorn.error").info("Hosted loading activity %s", json.dumps(event, sort_keys=True))

    @staticmethod
    def operation_progress_values(counters):
        if "verified_files" in counters or "total_files" in counters:
            return {"completed": counters.get("verified_files"),
                    "total": counters.get("total_files"), "unit": "files"}
        return {k: counters[k] for k in ("completed", "total", "unit") if k in counters}

    async def progress(self, phase, **counters):
        self.loading_progress(phase, **counters)
        if callable(self.operation_progress):
            if phase == "preparing_package":
                with _preparation_trace("preparing_package_reporter"):
                    await run_in_threadpool(self.operation_progress, phase,
                                            **self.operation_progress_values(counters))
            else:
                await run_in_threadpool(self.operation_progress, phase,
                                        **self.operation_progress_values(counters))

    def worker_progress(self, phase, **counters):
        self.loading_progress(phase, **counters)
        if not callable(self.operation_progress):
            return
        if phase in {"indexing_nodes", "indexing_edges"}:
            completed, total = counters.get("completed"), counters.get("total")
            if type(completed) is int and type(total) is int and total > 0:
                bucket = min(20, completed * 20 // total)
                progress_key = (phase, total)
                with self.operation_progress_lock:
                    previous = self.operation_progress_buckets.get(progress_key, -1)
                    if bucket == previous and completed != total:
                        return
                    self.operation_progress_buckets[progress_key] = bucket

        def report():
            future = asyncio.run_coroutine_threadsafe(run_in_threadpool(
                self.operation_progress, phase, **self.operation_progress_values(counters)), self.event_loop)
            future.result()

        trace_report = False
        if phase in {"verifying_artifact", "decoding_nodes", "decoding_edges",
                     "indexing_nodes", "indexing_edges", "index_cache"}:
            completed, total = counters.get("completed"), counters.get("total")
            bucket = (min(4, completed * 4 // total)
                      if type(completed) is int and type(total) is int and total > 0 else None)
            with self.trace_lock:
                trace_key = (phase, bucket)
                if trace_key not in self.traced_index_buckets:
                    self.traced_index_buckets.add(trace_key)
                    trace_report = True
        if trace_report:
            with _preparation_trace("index_progress_reporter_" + phase):
                report()
        else:
            report()

    def fail_loading(self, error):
        if self.diagnostic_id is not None:
            self.dispatcher.loading.finish(self.diagnostic_owner, self.diagnostic_id,
                                           exception_class=type(error).__name__)

    def error_body(self, detail, error=None):
        return {"detail": detail, "failure_phase": getattr(error, "diagnostic_hosted_phase", None) or self.phase,
            **({"diagnostic_id": self.diagnostic_id} if self.diagnostic_id else {})}

    async def secure_send(self, message):
        if message["type"] == "http.response.start":
            self.response_status = message.get("status")
            message["headers"] = list(message.get("headers", [])) + [
                (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
            ]
        await self.send(message)
        if (self.activity_owner is not None and message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and self.response_status is not None and 200 <= self.response_status < 300):
            try:
                self.loading_progress("response_sent")
            except Exception:
                logging.getLogger(__name__).exception("Could not record completed loading response")


class HostedVolumes(VolumeIntake):
    def status(self):
        identity = current_identity()
        return {"mode": "obo", "profile": None, "configured": True, "connected": True,
                "auth_check": "verified", "host": identity.host, "user_id": identity.user_id,
                "local_only": False, "production_remote_enabled": True}

    def connect(self):
        return self.status()

    @contextmanager
    def _client(self):
        try:
            yield current_identity().client
        except HTTPException:
            raise
        except Exception as error:
            self._failure(error)


def install_hosted_models(app):
    """Install request-scoped model resolution on an app retained by one owner."""
    service = app.state.service
    app.state.fixture_identity_provider = lambda profile: current_identity()
    previous_runtime = service.runtime
    if not isinstance(previous_runtime, HostedRuntimeProxy):
        close = getattr(previous_runtime, "close", None)
        if callable(close):
            close()
        service.runtime = HostedRuntimeProxy(service.root)
    app.state.hosted_request_authenticated = True

    def execution_request(method, path):
        return (method == "POST" and (path.startswith(("/api/agents/", "/api/loops/"))
            or path == "/api/support/ai-review"
                or "/design" in path or "/draft" in path))

    @app.middleware("http")
    async def request_model_runtime(request, call_next):
        identity = current_identity()
        runtime = None
        models: dict[str, BaseChatModel] = {}
        registered = False
        root = service.root.resolve()
        parts = root.parts
        project_context = (len(parts) >= 4 and parts[-4] == "projects"
                           and parts[-2] == "package-contexts")
        project = parts[-3] if project_context else None
        package = parts[-1] if project_context else None
        parent_dispatch = bool(request.headers.get("x-workbench-project")
                               and request.headers.get("x-workbench-package") and not project_context)
        should_execute = execution_request(request.method, request.url.path)
        try:
            if parent_dispatch and should_execute:
                # The outer app only dispatches this request. The selected child
                # app performs the authoritative overlay and runtime construction.
                runtime = AgentRuntime(root / "agents", identity_scope=identity.host + ":" + identity.user_id,
                                       uc_identity_provider=current_identity)
            else:
                from workbench.external_models import Runtime as ExternalRuntime, current_for_root
                external = current_for_root(root) if should_execute else None
                if external is not None:
                    store, external_key, session_token = external
                    runtime = ExternalRuntime(store, external_key, session_token, root,
                                              uc_identity_provider=current_identity, cache=False)
                else:
                    knowledge_loader = None
                    if project:
                        from workbench.knowledge_store import KnowledgeStore
                        knowledge_loader = lambda: KnowledgeStore(
                            current_delta_runtime().store, current_delta_runtime().artifacts).load(project)
                    routes = {}
                    if should_execute:
                        from workbench.hosted_model_reads import model_status
                        model_status(current_delta_runtime(), identity, project, package, validate_ready=False)
                        router = WorkspaceModels(root / "models")
                        routes = router.load(identity)
                    if should_execute and routes:
                        router = WorkspaceModels(root / "models")
                        models = {role: model for role, model in router.app_chat_models(identity).items()}
                    runtime = AgentRuntime(root / "agents", role_models=models or None,
                        identity_scope=identity.host + ":" + identity.user_id,
                        knowledge_loader=knowledge_loader, uc_identity_provider=current_identity)
            active = _CURRENT_AGENT_RUNTIMES.get()
            runtimes = dict(active) if isinstance(active, dict) else {}
            runtimes[str(root)] = runtime
            token = _CURRENT_AGENT_RUNTIMES.set(runtimes)
            action_runtimes = _CURRENT_ACTION_RUNTIMES.get()
            if isinstance(action_runtimes, list):
                action_runtimes.append(runtime)
                registered = True
            try:
                return await call_next(request)
            finally:
                _CURRENT_AGENT_RUNTIMES.reset(token)
        except BaseException:
            if runtime is not None and not registered:
                _close_request_runtime(runtime)
            elif runtime is None:
                for model in models.values():
                    _close_request_runtime(model)
            raise

    @app.get("/api/models/status")
    def status():
        identity = current_identity()
        routes = WorkspaceModels(service.root / "models").load(identity)
        endpoints = {route.get("endpoint") for route in routes.values() if isinstance(route, dict)}
        return {"connected": True, "profile": None, "mode": "obo", "roles": ROLE_PURPOSES,
            "workspace": identity.host,
            "active_endpoint": next(iter(endpoints)) if len(endpoints) == 1 else None,
            "configured_roles": sorted(routes), "tool_calling_verified": False,
                "inference_ready": workspace_ready(service.runtime),
                "reason": "Request-scoped Databricks user authorization; no service-principal fallback."}

    @app.post("/api/models/connect")
    def connect():
        identity = current_identity()
        router = WorkspaceModels(service.root / "models")
        inventory = router.discover(identity)
        existing = router.load(identity)
        selected = next(iter(existing.values()), {}).get("endpoint") if existing else None
        return {"connected": True, **inventory,
                "selected_endpoint": selected, "default_route_configured": False,
                "route_source": "existing_user_selection" if existing else None,
                "selection_basis": "Discovery only. Choose an endpoint and explicitly Apply to save a route.",
                "data_egress_consent_required_for_each_run": True,
                "runtime_verified": False}

    @app.post("/api/models/select")
    def select(body: RouteSelection):
        if not body.tool_support_confirmed:
            raise HTTPException(422, "Confirm endpoint chat/tool-call compatibility before routing agent tools.")
        fresh = current_identity()
        router = WorkspaceModels(service.root / "models")
        inventory = router.discover(fresh)
        routes = {role: {"endpoint": body.endpoint,
                         "tool_support_evidence": "User declared chat and tool-call compatibility in model picker."}
                  for role in ROLE_PURPOSES}
        router.configure(fresh, routes, inventory)
        return {"selected_endpoint": body.endpoint, "roles": list(ROLE_PURPOSES),
            "inference_ready": True, "runtime_verified": False,
            "notice": "User-scoped route metadata configured; inference has not been verified."}


def user_app(root, snapshots, *, read_only=False, execution_models=True):
    identity = current_identity()
    package_cache_scope = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()

    def publish_source(*args, **kwargs):
        return current_delta_runtime().publish_source(*args, **kwargs)

    def register_reference(*args, **kwargs):
        return current_delta_runtime().reference(*args, **kwargs)

    def install_models(app):
        with _preparation_trace("install_hosted_models"):
            install_hosted_models(app)
        from workbench.hosted_writer_checkpoints import writer_checkpoint_factory
        app.state.service.writer_checkpoint_factory = lambda **kwargs: writer_checkpoint_factory(
            current_delta_runtime())(**kwargs)

    def factory(data_dir, sample=None):
        factory_scope = "root" if Path(data_dir) == Path(root) else "project_context"
        with _preparation_trace("create_package_app_" + factory_scope):
            app = _create_package_app(data_dir, root / ".no-bundled-sample.xml",
                                       executor_factory=ActionExecutor,
                                       source_publisher=publish_source,
                                       model_installer=install_models,
                                       record_audit_initialization=False,
                                       package_cache_scope=package_cache_scope)
        from workbench.validation_campaigns import CampaignStore
        app.state.validation_store_provider = lambda: CampaignStore(
            delta=current_delta_runtime().store, artifacts=current_delta_runtime().artifacts)
        from workbench.knowledge_store import KnowledgeStore, project_for_root
        app.state.knowledge_store_provider = lambda: KnowledgeStore(
            current_delta_runtime().store, current_delta_runtime().artifacts)
        project = project_for_root(app.state.service.root)
        if project:
            app.state.service.knowledge_loader = lambda: app.state.knowledge_store_provider().load(project)
        return app
    app = factory(root)
    with _preparation_trace("install_projects"):
        install_projects(app, factory, root / ".no-bundled-input",
                         executor_factory=ActionExecutor, source_publisher=publish_source)
    with _preparation_trace("install_volume_intake"):
        install_volume_intake(app, service=HostedVolumes(app.state.projects, current_identity,
                                                       reference_registrar=register_reference))
    if not read_only:
        with _preparation_trace("append_local_audit"):
            app.state.audit.append({"event": "hosted_user_action", "level": "INFO",
                                    "actor": identity.user_id, "actor_authenticated": True,
                                    "workspace": identity.host, "authentication": "databricks_obo",
                                    "message": "Request-scoped user authorization resolved; no alternate identity.",
                                    "role": None, "run_id": None})
    return app


class HostedDispatcher:
    def __init__(self, config, identity_resolver=obo_identity):
        self.config, self.identity_resolver = config, identity_resolver
        from workbench.external_models import Sessions
        self.external_models = Sessions()
        from workbench.model_price_rates import ModelPriceRates
        self.model_price_rates = ModelPriceRates()
        self.guard, self.users = Lock(), {}
        self.user_queues = {}
        self.capacity = BoundedSemaphore(config.max_active_users)
        self.sessions = HostedSessionManager(config.max_active_users)
        self.static = StaticFiles(directory=STATIC)
        from workbench.hosted_operations import HostedOperations
        from workbench.hosted_uploads import HostedUploads
        from workbench.intake_limits import max_xml_bytes, max_batch_bytes
        self.diagnostics = HostedDiagnostics()
        self.operations = HostedOperations(config.warehouse, diagnostics=self.diagnostics)
        self.uploads = HostedUploads(config.warehouse, max_file_bytes=max_xml_bytes(),
                         max_batch_bytes=max_batch_bytes())
        from workbench.heartbeat import Heartbeats
        self.heartbeats = Heartbeats.from_env(config.warehouse)
        from workbench.hosted_loading import HostedLoading
        self.loading = HostedLoading()

    def authorize_external_scope(self, identity, forwarded, external_key):
        """Fresh committed membership only, no route settings or audit writes."""
        from workbench.hosted_registry import _registry
        remote = provision(identity, self.config.catalog_prefix, initialize=False)
        with tempfile.TemporaryDirectory(prefix="workbench-external-scope-") as temporary:
            snapshots = DeltaRuntime(current_identity, remote, temporary,
                                     warehouse=self.config.warehouse, forbidden_token=forwarded)
            _, registry = _registry(snapshots, tables=("projects", "packages"))
            if not any(owner == external_key[1] for owner, _ in registry["projects"]):
                raise HTTPException(404, "Unknown project ID")
            if not any(owner == external_key[1] and row["sha256"] == external_key[2]
                       and row["status"] == "completed" for owner, row in registry["packages"]):
                raise HTTPException(403, "Package is not a ready member of this project")

    async def execute_operation(self, original_scope, method, path, query, headers, body, progress_reporter=None):
        """Re-enter the tested commit boundary, not a second execution identity."""
        raw = json.dumps(body).encode()
        selected = {k.lower(): v for k, v in headers.items()
                    if k.lower() in {"x-workbench-project", "x-workbench-package"}}
        original_headers = [(k, v) for k, v in original_scope["headers"]
                            if k.lower() not in {b"content-length", b"content-type",
                                                 b"x-workbench-project", b"x-workbench-package"}]
        original_headers.extend((k.encode(), v.encode()) for k, v in selected.items())
        original_headers.extend([(b"content-type", b"application/json"),
                                 (b"content-length", str(len(raw)).encode())])
        inner = {**original_scope, "method": method, "path": path,
                 "raw_path": path.encode(), "query_string": query.encode(),
                 "headers": original_headers, "workbench.operation_execution": True,
                 "workbench.operation_progress": progress_reporter}
        messages, sent = [], False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        await self(inner, receive, send)
        start = next(m for m in messages if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return start["status"], json.loads(data)

    async def _run_retained_action(self, *, owner, identity, forwarded, mutation, path, scope, headers,
                                   bounded_receive, secure_send, progress, worker_progress,
                                   diagnostic_id, diagnostic_owner, operation_progress):
        project, package = headers.get("x-workbench-project", ""), headers.get("x-workbench-package", "")
        query = parse_qs(scope.get("query_string", b"").decode("latin1"))
        audit_read = _meaningful_hosted_read(scope["method"], path, query)
        read_scope = ((project, package) if not mutation and scope["method"] == "GET"
                      and not audit_read
                      and re.fullmatch(r"[a-f0-9]{32}", project)
                      and re.fullmatch(r"[a-f0-9]{64}", package) else None)
        status_read = (query.get("status_only") == ["true"] and
                       (path == "/api/agents/runs" or
                        re.fullmatch(r"/api/agents/runs/[a-f0-9]{32}", path) is not None))
        project_logs = re.fullmatch(r"/api/projects/([a-f0-9]{32})/jobs/[a-f0-9]{32}/logs", path)
        save_observation = bool(read_scope and (status_read or path in {"/api/logs", "/api/logs/export"}
                                or (project_logs and project_logs[1] == project)))
        loading_read = scope["method"] == "GET" and path in {
            "/api/input-packages", "/api/overview", "/api/objects", "/api/package/warm"}
        session, borrowed = await self.sessions.enter(owner, read_scope, save_observation,
            path in {"/api/logs", "/api/logs/export"},
            on_wait=(lambda: progress("waiting_for_session")) if loading_read else None)
        save_observation_borrowed = borrowed and session.save_read_scope == read_scope
        delta_tokens, mutation_tokens = [], []
        action_runtimes = []
        accepted_run_id = None
        action_started = monotonic()
        read_dispatch_started = False
        read_audit_attempted = False
        action_token = _CURRENT_ACTION_RUNTIMES.set(action_runtimes)
        remote = None
        snapshots = None
        app = None
        primary_failure = None
        reused_service = session.app is not None
        response_complete = False
        original_send = secure_send

        def report_snapshot_failure(error):
            try:
                import traceback
                from workbench.hosted_diagnostics import token_expiry_observation
                expiry, remaining = token_expiry_observation(forwarded)
                frames = [(Path(frame.filename).name, frame.name, frame.lineno)
                          for frame in traceback.extract_tb(error.__traceback__)]
                event = self.diagnostics.record(
                    owner, method=scope["method"], path=path, status=503,
                    error=error, frames=frames, phase="committing_delta",
                    operation_id=scope.get("workbench.active_operation_id"),
                    run_id=accepted_run_id, event_type="hosted_snapshot_failure_observed",
                    token_expiry=expiry, token_remaining=remaining,
                    elapsed_ms=int((monotonic() - action_started) * 1000))
                logging.getLogger(__name__).error(
                    "Hosted snapshot failure observed before recovery %s",
                    json.dumps(event, sort_keys=True))
            except Exception as diagnostic_error:
                logging.getLogger(__name__).error(
                    "Could not record initial snapshot failure: %s", type(diagnostic_error).__name__)
            else:
                try:
                    error.diagnostic_failure_recorded = True
                except (AttributeError, TypeError):
                    pass

        async def secure_send(message):
            nonlocal response_complete
            if save_observation_borrowed and message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) +
                           [(b"x-workbench-observation", b"live-uncommitted")]}
            await original_send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        def bind(runtime, is_mutation):
            delta_tokens.append(_CURRENT_DELTA_RUNTIME.set(runtime))
            mutation_tokens.append(_CURRENT_MUTATION.set(is_mutation))

        async def publish_read_audit(status, correlation):
            if app is None or snapshots is None:
                raise RuntimeError("Hosted audit runtime is unavailable")
            audit = app.state.audit
            manager = app.state.projects
            with manager.lock:
                selected = manager.contexts.get((project, package))
                if selected is not None:
                    audit = selected["app"].state.audit
            await run_in_threadpool(audit.append, {
                "event": "http_request_completed",
                "level": "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO",
                "role": None, "run_id": None, "package_hash": package or None,
                "actor": identity.user_id, "actor_authenticated": True,
                "correlation_id": correlation,
                "message": f"GET {path[:240]} returned HTTP {status}",
                "status_code": status, "duration_ms": round((monotonic() - action_started) * 1000, 2),
            })
            await run_in_threadpool(snapshots.flush_audit)
            session.audit_sequences = await run_in_threadpool(snapshots.audit_sequences)

        copy_on_write = getattr(self.config, "copy_on_write", False)
        runtime_options = {"copy_on_write": True} if copy_on_write else {}

        async def cold_restore(initialize):
            runtime = DeltaRuntime(current_identity, remote, session.root, warehouse=self.config.warehouse,
                                   forbidden_token=forwarded, **runtime_options)
            bind(runtime, initialize)
            restore_options = {"initialize": initialize}
            if loading_read or diagnostic_id is not None or callable(operation_progress):
                restore_options["progress"] = worker_progress
            await run_in_threadpool(runtime.restore, **restore_options)
            if mutation or audit_read or path in {"/api/logs", "/api/logs/export"}:
                await progress("audit_restore")
                await run_in_threadpool(runtime.restore_audit)
                session.audit_loaded = True
            await progress("preparing_package")
            retained_app = await run_in_threadpool(user_app, session.root, runtime,
                                                   read_only=True, execution_models=False)
            session.app = retained_app
            await run_in_threadpool(retained_app.state.service.start,
                worker_progress if loading_read or diagnostic_id is not None or callable(operation_progress) else None)
            await run_in_threadpool(retained_app.state.projects.start)
            session.remote, session.marker = remote, runtime.receipt_marker
            session.generation, session.sequence = runtime.generation, runtime.sequence
            session.parent_inventory = runtime._parent_inventory
            if copy_on_write:
                await run_in_threadpool(_retain_copy_state, session, runtime)
            session.sources = dict(runtime.sources)
            session.committed_sources = set(runtime._committed_sources)
            session.audit_sequences = dict(runtime._audit_sequences)
            return runtime, retained_app

        try:
            warm_session = session.app is not None and isinstance(session.remote, str) and bool(session.remote)
            if (warm_session and copy_on_write and session.generation is not None
                    and (session.copy_parent_generation != session.generation
                         or session.copy_parent_records is None)):
                if borrowed:
                    raise HTTPException(503, "Retained state is unavailable during the active operation")
                self.sessions.reset(session)
                warm_session = False
                reused_service = False
            if warm_session:
                # The owner key is derived from this freshly resolved OBO host/user.
                # Reuse only that owner's already verified storage root and app;
                # ordinary activity must not reopen UC metadata or rehydrate the
                # committed XML/index. Mutations still publish through Delta below.
                remote = session.remote
            else:
                await progress("provisioning")
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix,
                                                  initialize=bool(mutation and session.app is None))
            if not warm_session:
                snapshots, app = await cold_restore(mutation)
            else:
                snapshots = DeltaRuntime(current_identity, remote, session.root, warehouse=self.config.warehouse,
                                         forbidden_token=forwarded, **runtime_options)
                # A warm retained session is the current process's working copy.
                # OBO has been resolved for this request, and the per-user gate
                # serializes writes. Do not query Delta/Volume or reload artifacts
                # just to start an agent against an unchanged selected package.
                snapshots.ready = True
                snapshots._materialized = True
                snapshots.generation, snapshots.sequence = session.generation, session.sequence
                snapshots._parent_inventory = session.parent_inventory
                if copy_on_write:
                    _restore_copy_state(session, snapshots)
                snapshots.receipt_marker = session.marker
                snapshots.sources = dict(session.sources)
                snapshots._committed_sources = set(session.committed_sources)
                snapshots._audit_sequences = dict(session.audit_sequences)
                bind(snapshots, mutation)
                app = session.app
                if (mutation or audit_read) and not session.audit_loaded:
                    await progress("audit_restore")
                    await run_in_threadpool(snapshots.restore_audit)
                    session.audit_sequences = dict(snapshots._audit_sequences)
                    session.audit_loaded = True

            if app is None or snapshots is None:
                raise RuntimeError("Hosted workspace runtime is unavailable")

            if path == "/api/readiness" and scope["method"] == "GET" and read_scope is not None:
                query = parse_qs(scope.get("query_string", b"").decode("latin1"))
                try:
                    offset = int(query.get("offset", ["0"])[-1])
                    limit = int(query.get("limit", ["25"])[-1])
                    scenario = query.get("scenario", ["original"])[-1]
                    workflow_id = query.get("workflow_id", [None])[-1]
                    if (offset < 0 or not 1 <= limit <= 100 or scenario not in {"original", "bronze"}
                            or (workflow_id is not None and not re.fullmatch(r"[a-f0-9]{64}", workflow_id))):
                        raise ValueError()
                except (TypeError, ValueError):
                    raise HTTPException(422, "Invalid readiness scope or pagination") from None

                marker, manifest = await run_in_threadpool(snapshots.session_authority)
                if marker != session.marker or manifest is None:
                    raise HTTPException(409, "Saved workspace changed; refresh the selected package")
                child = await run_in_threadpool(app.state.projects.acquire, project, package)
                try:
                    index = child.state.service.require()
                    if index.model["package"]["sha256"] != package:
                        raise HTTPException(409, "Loaded package does not match the selected XML")
                    from workbench.readiness import assess, hosted_evidence
                    from workbench.knowledge_store import KnowledgeStore
                    from workbench.architecture_knowledge import unavailable
                    selected_scope = {"project_id": project, "package_hash": package,
                                      "context_id": "package:" + project + ":" + package}
                    evidence, complete = await run_in_threadpool(
                        hosted_evidence, snapshots, manifest["inventory"], session.generation, selected_scope)
                    knowledge = await run_in_threadpool(KnowledgeStore(snapshots.store, snapshots.artifacts).load, project)
                    result = await run_in_threadpool(assess, index, project, evidence, complete,
                        offset=offset, limit=limit, scenario=scenario, generation=session.generation,
                        workflow_id=workflow_id, knowledge=knowledge.status() if knowledge else unavailable())
                finally:
                    await run_in_threadpool(app.state.projects.release, project, package)
                await progress("responding")
                return await JSONResponse(result)(scope, bounded_receive, secure_send)

            if path == "/api/package/warm" and scope["method"] == "GET":
                project, package = headers.get("x-workbench-project", ""), headers.get("x-workbench-package", "")
                if not re.fullmatch(r"[a-f0-9]{32}", project) or not re.fullmatch(r"[a-f0-9]{64}", package):
                    raise HTTPException(403, "An explicit project and ready package are required")
                child = await run_in_threadpool(app.state.projects.acquire, project, package)
                try:
                    index = child.state.service.require()
                    if index.model["package"]["sha256"] != package:
                        raise HTTPException(409, "Loaded package does not match the selected XML")
                finally:
                    app.state.projects.release(project, package)
                await progress("responding")
                return await JSONResponse({
                    "ready": True, "cache_ready": True, "cache_hit": reused_service,
                    "cache_reason": "retained_service_ready", "cache_memory_diagnostic": {},
                    "source_cache_ready": True, "source_cache_reason": "retained_loaded_package",
                    "generation": session.generation, "retained_service": True,
                    "generation_allowed": False, "deployment_allowed": False,
                })(scope, bounded_receive, secure_send)

            response_start: dict[str, Any] | None = None
            if mutation:
                app.state.audit.append({"event": "hosted_user_action", "level": "INFO",
                    "actor": identity.user_id, "actor_authenticated": True, "workspace": identity.host,
                    "authentication": "databricks_obo",
                    "message": "Request-scoped user authorization resolved; no alternate identity.",
                    "role": None, "run_id": None})
            if callable(operation_progress) and path in {"/api/overview", "/api/objects"}:
                await progress("querying_inventory")
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as response:
                async def capture(message):
                    nonlocal response_start
                    if message["type"] == "http.response.start":
                        response_start = message
                    elif message["type"] == "http.response.body":
                        response.write(message.get("body", b""))

                finalize = re.fullmatch(r"/api/hosted/uploads/([a-f0-9]{32})/finalize", path)
                try:
                    if finalize and scope.get("workbench.operation_execution"):
                        source, name, project, digest, size = await run_in_threadpool(
                            self.uploads.materialize, identity, remote, finalize[1], session.root)
                        await run_in_threadpool(app.state.projects.reserve, project)
                        try:
                            staged = await run_in_threadpool(app.state.projects.stage, project,
                                                             [(source, name, digest, size)])
                        finally:
                            app.state.projects.slot.release()
                        await JSONResponse(staged, status_code=202)(scope, bounded_receive, capture)
                    else:
                        with _preparation_trace("app_dispatch"):
                            read_dispatch_started = audit_read
                            await app(scope, bounded_receive, capture)
                except BaseException:
                    if mutation:
                        self.sessions.invalidate(session)
                    raise

                missing_notebook = False
                attachment_path = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/spark-drafts/attach", path)
                incremental_notebook = bool(mutation and response_start is not None and attachment_path)
                if (mutation and response_start is not None and attachment_path
                        and response_start.get("status") == 200):
                    response.seek(0)
                    result = json.load(response)
                    await run_in_threadpool(_verify_notebook_attachment, app, project, package,
                                            attachment_path[1], result)
                if (mutation and response_start is not None and response_start.get("status") == 409
                        and attachment_path):
                    from workbench.notebook_edits import MISSING_NOTEBOOK_CONFLICT
                    response.seek(0)
                    try:
                        missing_notebook = json.load(response) == {"detail": MISSING_NOTEBOOK_CONFLICT}
                    except (ValueError, UnicodeDecodeError):
                        missing_notebook = False
                if missing_notebook or incremental_notebook:
                    await run_in_threadpool(app.state.projects.drain)
                    await run_in_threadpool(app.state.service.drain)
                    await progress("audit_restore")
                    await run_in_threadpool(snapshots.flush_audit)
                    session.audit_sequences = await run_in_threadpool(snapshots.audit_sequences)

                if mutation and not (missing_notebook or incremental_notebook):
                    if (path == "/api/agents/run" and response_start is not None
                            and response_start.get("status") == 202 and project and package):
                        session.live_readers_done.set()
                        session.live_scope = (project, package)
                        self.sessions.notify(session)
                        observer = getattr(operation_progress, "accepted_agent", None)
                        # The 202 is an in-process acceptance, not a Delta commit.
                        try:
                            response.seek(0)
                            accepted = json.load(response)
                            candidate = accepted.get("id") if isinstance(accepted, dict) else None
                            if isinstance(candidate, str) and re.fullmatch(r"[a-f0-9]{32}", candidate):
                                accepted_run_id = candidate
                            if callable(observer):
                                observer(accepted)
                        except Exception:
                            logging.getLogger(__name__).warning("Could not expose accepted agent status")
                    try:
                        await run_in_threadpool(app.state.projects.drain)
                        await run_in_threadpool(app.state.service.drain)
                    finally:
                        session.live_scope = None
                        self.sessions.notify(session)
                        if session.live_readers:
                            await session.live_readers_done.wait()
                    observer = getattr(operation_progress, "completed_agent", None)
                    if accepted_run_id and callable(observer):
                        try:
                            from workbench.app import _stored_agent_status
                            root = app.state.projects.context_root(project, package)
                            observation = await run_in_threadpool(
                                _stored_agent_status, root, accepted_run_id, package)
                            final_status = {key: observation.get(key) for key in
                                            ("id", "status", "phase", "finished_at")}
                            writer = observation.get("candidate_writer")
                            if isinstance(writer, dict):
                                final_status["candidate_writer"] = {
                                    key: writer.get(key) for key in
                                    ("id", "status", "phase", "created_at", "started_at",
                                     "finished_at", "manifest_status", "failure_stage",
                                     "exception_class")}
                            observer(final_status)
                        except Exception as error:
                            logging.getLogger(__name__).warning(
                                "Final agent observation unavailable: %s", type(error).__name__)
                    await progress("committing_delta")
                    try:
                        if accepted_run_id:
                            session.save_read_scope = (project, package)
                            self.sessions.notify(session)
                        try:
                            snapshot_progress = getattr(operation_progress, "snapshot", None)
                            if callable(snapshot_progress):
                                def snapshot_observer(stage, *, completed=None, total=None):
                                    snapshot_progress(stage, completed=completed, total=total)
                                snapshot_observer.failure = report_snapshot_failure
                                await run_in_threadpool(snapshots.flush, prime_cache=False,
                                                        progress=snapshot_observer)
                            else:
                                await run_in_threadpool(snapshots.flush, prime_cache=False)
                        finally:
                            session.save_read_scope = None
                            self.sessions.notify(session)
                            if session.live_readers:
                                await session.live_readers_done.wait()
                    except Exception as error:
                        # Keep the actual failing stage even if recovery later
                        # changes the request progress indicator.
                        try:
                            error.diagnostic_hosted_phase = "committing_delta"
                        except Exception:
                            pass
                        raise
                    session.marker = snapshots.receipt_marker
                    session.generation, session.sequence = snapshots.generation, snapshots.sequence
                    session.parent_inventory = snapshots._parent_inventory
                    if copy_on_write:
                        await run_in_threadpool(_retain_copy_state, session, snapshots)
                    session.sources = dict(snapshots.sources)
                    session.committed_sources = set(snapshots._committed_sources)
                    session.audit_sequences = await run_in_threadpool(snapshots.audit_sequences)
                    if finalize and scope.get("workbench.operation_execution"):
                        await run_in_threadpool(self.uploads.mark_committed, identity, remote, finalize[1], staged)
                    if response_start is None or response_start.get("status", 500) >= 400:
                        for request_runtime in action_runtimes:
                            await run_in_threadpool(_close_request_runtime, request_runtime)
                        action_runtimes.clear()
                        self.sessions.reset(session)
                        snapshots, app = await cold_restore(False)

                if response_start is None:
                    self.sessions.invalidate(session)
                    raise ValueError("Missing application response")
                if audit_read:
                    status = response_start["status"]
                    correlation = uuid4().hex
                    try:
                        read_audit_attempted = True
                        await publish_read_audit(status, correlation)
                    except BaseException:
                        self.sessions.invalidate(session)
                        raise
                    response_start = {**response_start, "headers": list(response_start.get("headers", [])) +
                                      [(b"x-correlation-id", correlation.encode())]}
                if save_observation_borrowed and response_start["status"] == 200 and path != "/api/logs/export":
                    response.seek(0)
                    observation = json.load(response)
                    if not isinstance(observation, dict) or "hosted_save_state" in observation:
                        raise ValueError("Invalid live observation response")
                    observation["hosted_save_state"] = "live_uncommitted"
                    encoded = json.dumps(observation).encode("utf-8")
                    response.seek(0)
                    response.truncate()
                    response.write(encoded)
                    response_start = {**response_start, "headers": [
                        (name, value) for name, value in response_start.get("headers", [])
                        if name.lower() != b"content-length"
                    ] + [(b"content-length", str(len(encoded)).encode("ascii"))]}
                if path == "/api/overview" and response_start["status"] == 200:
                    response.seek(0)
                    overview = json.load(response)
                    if (overview.get("ready") is True and headers.get("x-workbench-package")
                            and overview.get("package", {}).get("sha256") == headers["x-workbench-package"]):
                        overview["retained_service_ready"] = True
                        overview["retained_generation"] = session.generation
                        encoded = json.dumps(overview).encode("utf-8")
                        response.seek(0)
                        response.truncate()
                        response.write(encoded)
                        response_start = {**response_start, "headers": [
                            (name, value) for name, value in response_start.get("headers", [])
                            if name.lower() != b"content-length"
                        ] + [(b"content-length", str(len(encoded)).encode("ascii"))]}
                await progress("responding")
                await secure_send(response_start)
                response.seek(0)
                while chunk := response.read(1024 * 1024):
                    await secure_send({"type": "http.response.body", "body": chunk, "more_body": True})
                await secure_send({"type": "http.response.body", "body": b"", "more_body": False})
            if diagnostic_id is not None:
                self.loading.finish(diagnostic_owner, diagnostic_id,
                                    exception_class="HTTPException" if response_start["status"] >= 400 else None)
        except BaseException as primary_error:
            primary_failure = primary_error
            if (audit_read and read_dispatch_started and not read_audit_attempted
                    and isinstance(primary_error, Exception)):
                try:
                    status = (primary_error.status_code if isinstance(primary_error, HTTPException)
                              else 401 if isinstance(primary_error, AuthenticationUnavailable) else 503)
                    await publish_read_audit(status, uuid4().hex)
                except Exception as audit_error:
                    try:
                        setattr(primary_error, "diagnostic_recovery_error_type", type(audit_error).__name__)
                        setattr(primary_error, "diagnostic_recovery_stage", "audit_journal")
                    except (AttributeError, TypeError):
                        pass
            if accepted_run_id:
                try:
                    primary_error.diagnostic_run_id = accepted_run_id
                except Exception:
                    pass
            if (isinstance(primary_error, Exception)
                    and getattr(primary_error, "diagnostic_hosted_phase", None) == "committing_delta"
                    and not getattr(primary_error, "diagnostic_failure_recorded", False)):
                report_snapshot_failure(primary_error)
            if mutation and snapshots is not None:
                recovery_stage = "drain"
                try:
                    if session.app is not None:
                        await run_in_threadpool(session.app.state.projects.drain)
                        await run_in_threadpool(session.app.state.service.drain)
                    recovery_stage = "audit_journal"
                    if session.app is not None:
                        audit = session.app.state.audit
                        manager = session.app.state.projects
                        with manager.lock:
                            selected = manager.contexts.get((project, package))
                            if selected is not None:
                                audit = selected["app"].state.audit
                        status = (primary_error.status_code if isinstance(primary_error, HTTPException)
                                  else 401 if isinstance(primary_error, AuthenticationUnavailable) else 503)
                        await run_in_threadpool(audit.append, {
                            "event": "hosted_request_failed", "level": "ERROR" if status >= 500 else "WARNING",
                            "role": None, "run_id": accepted_run_id,
                            "package_hash": package or None, "actor": identity.user_id,
                            "message": f"{scope['method']} {path[:240]} failed; hosted completion is unconfirmed",
                            "status_code": status, "failure_phase": getattr(
                                primary_error, "diagnostic_hosted_phase", None),
                            "error_type": type(primary_error).__name__,
                        })
                    await run_in_threadpool(snapshots.flush_audit)
                    for request_runtime in action_runtimes:
                        await run_in_threadpool(_close_request_runtime, request_runtime)
                    action_runtimes.clear()
                    recovery_stage = "reset_session"
                    self.sessions.reset(session)
                    recovery_stage = "provision"
                    remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix,
                                                     initialize=False)
                    recovery_stage = "restore"
                    await cold_restore(False)
                except BaseException as recovery_error:
                    # Preserve the original failure; record only safe recovery
                    # metadata, never exception messages or file contents.
                    try:
                        import traceback
                        primary_error.diagnostic_recovery_error_type = type(recovery_error).__name__
                        primary_error.diagnostic_recovery_stage = recovery_stage
                        primary_error.diagnostic_recovery_error_code = getattr(recovery_error, "error_code", None)
                        primary_error.diagnostic_recovery_sql_state = getattr(recovery_error, "sql_state", None)
                        primary_error.diagnostic_recovery_statement_id = getattr(recovery_error, "statement_id", None)
                        primary_error.diagnostic_recovery_frames = [
                            (Path(frame.filename).name, frame.name, frame.lineno)
                            for frame in traceback.extract_tb(recovery_error.__traceback__)[-12:]
                        ]
                    except Exception:
                        pass
                    try:
                        self.sessions.invalidate(session)
                    except BaseException as invalidate_error:
                        try:
                            primary_error.diagnostic_cleanup_error_type = type(invalidate_error).__name__
                        except Exception:
                            pass
            elif not borrowed:
                try:
                    self.sessions.invalidate(session)
                except BaseException as invalidate_error:
                    try:
                        primary_error.diagnostic_cleanup_error_type = type(invalidate_error).__name__
                    except Exception:
                        pass
            raise
        finally:
            cleanup_failure = None
            cleanup_cancellation = None

            def note_cleanup_failure(error):
                nonlocal cleanup_failure, cleanup_cancellation
                if isinstance(error, asyncio.CancelledError):
                    if cleanup_cancellation is None:
                        cleanup_cancellation = error
                elif cleanup_failure is None:
                    cleanup_failure = error

            def cleanup_call(function, *args):
                try:
                    function(*args)
                except BaseException as error:
                    note_cleanup_failure(error)

            async def cleanup_thread(function, *args):
                task = asyncio.create_task(run_in_threadpool(function, *args))
                while True:
                    try:
                        await asyncio.shield(task)
                        break
                    except asyncio.CancelledError as error:
                        note_cleanup_failure(error)
                        if task.done():
                            if not task.cancelled():
                                try:
                                    task.result()
                                except BaseException as task_error:
                                    note_cleanup_failure(task_error)
                            break
                    except BaseException as error:
                        note_cleanup_failure(error)
                        break

            # Tokens and the gate belong to this task. Shield level cancellation
            # here and finish each worker even under direct asyncio cancellation.
            with CancelScope(shield=True):
                if not borrowed and session.discarded and session.app is not None:
                    await cleanup_thread(lambda: session.app.state.projects.drain())
                    await cleanup_thread(lambda: session.app.state.service.drain())
                for runtime in action_runtimes:
                    await cleanup_thread(_close_request_runtime, runtime)
                cleanup_call(_CURRENT_ACTION_RUNTIMES.reset, action_token)
                for token in reversed(mutation_tokens):
                    cleanup_call(_CURRENT_MUTATION.reset, token)
                for token in reversed(delta_tokens):
                    cleanup_call(_CURRENT_DELTA_RUNTIME.reset, token)
                if borrowed:
                    def release_reader():
                        session.live_readers -= 1
                    cleanup_call(release_reader)
                    if not session.live_readers:
                        cleanup_call(session.live_readers_done.set)
                else:
                    cleanup_call(session.gate.release)
                    cleanup_call(self.sessions.notify, session)
                await cleanup_thread(self.sessions.release, session)
            if primary_failure is not None:
                # Preserve an earlier cleanup failure recorded during recovery.
                if cleanup_failure is not None:
                    try:
                        if not getattr(primary_failure, "diagnostic_cleanup_error_type", None):
                            primary_failure.diagnostic_cleanup_error_type = type(cleanup_failure).__name__
                    except Exception:
                        pass
            elif cleanup_cancellation is not None:
                if cleanup_failure is not None:
                    logging.getLogger(__name__).warning("Hosted request cleanup failed during cancellation")
                raise cleanup_cancellation
            elif cleanup_failure is not None:
                if not response_complete or not isinstance(cleanup_failure, Exception):
                    raise cleanup_failure
                logging.getLogger(__name__).warning("Hosted request cleanup failed after response completion")

    async def _run_special_read(self, *, identity, owner, forwarded, path, scope, headers,
                                bounded_receive, secure_send, progress, worker_progress,
                                diagnostic_id, diagnostic_owner, package_warm_read,
                                artifact_read, history_read, selected_object_read, artifact_body):
        if path.startswith("/api/hosted/logs/"):
            diagnostic_id = path.removeprefix("/api/hosted/logs/")
            item = self.diagnostics.find(owner, diagnostic_id)
            if item is None:
                raise HTTPException(404, "No matching diagnostic is retained by this app instance")
            await JSONResponse({"item": item,
                "storage": "bounded per-user in-memory diagnostics; cleared when this app instance restarts"})(
                    scope, bounded_receive, secure_send)
            return
        remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix, initialize=False)
        with tempfile.TemporaryDirectory(prefix="workbench-special-" + owner[:12] + "-") as temporary:
            snapshots = DeltaRuntime(current_identity, remote, temporary, warehouse=self.config.warehouse,
                                     forbidden_token=forwarded)
            if package_warm_read:
                from workbench.hosted_registry import _registry, committed_generation
                project, package = headers["x-workbench-project"], headers["x-workbench-package"]
                if not re.fullmatch(r"[a-f0-9]{32}", project):
                    raise HTTPException(404, "Unknown project ID")
                if not re.fullmatch(r"[a-f0-9]{64}", package):
                    raise HTTPException(403, "Package is not a ready member of this project")
                before = committed_generation(snapshots)
                if before[0] is None:
                    raise HTTPException(409, "No committed package workspace is available to prepare")
                _, registry = _registry(snapshots, tables=("projects", "packages"), committed=before)
                if not any(owner_id == project for owner_id, _ in registry["projects"]):
                    raise HTTPException(404, "Unknown project ID")
                member = next((record for owner_id, record in registry["packages"]
                               if owner_id == project and record.get("sha256") == package), None)
                if member is None or member.get("status") != "completed":
                    raise HTTPException(403, "Package is not a ready member of this project")
                await run_in_threadpool(snapshots.restore, initialize=False, progress=worker_progress,
                                        materialize=False)
                if snapshots.generation != before[0]["migration_id"]:
                    raise HTTPException(409, "Committed workspace changed during package preparation; retry against the current selection")
                result = {"ready": snapshots.ready, "cache_ready": snapshots.working_snapshot_cache_ready,
                    "cache_hit": snapshots.working_snapshot_cache_hit,
                    "cache_reason": snapshots.working_snapshot_cache_reason,
                    "cache_memory_diagnostic": snapshots.working_snapshot_memory_diagnostic,
                    "source_cache_ready": snapshots.source_cache_ready,
                    "source_cache_reason": snapshots.source_cache_reason, "generation": snapshots.generation,
                    "generation_allowed": False, "deployment_allowed": False}
                return await JSONResponse(result)(scope, bounded_receive, secure_send)
            if artifact_read:
                from workbench.hosted_object_reads import workflow_artifact_response
                result = await run_in_threadpool(workflow_artifact_response, snapshots, path,
                    headers["x-workbench-project"], headers["x-workbench-package"], artifact_body, worker_progress)
                await progress("responding")
                response = result if isinstance(result, Response) else JSONResponse(result)
                return await response(scope, bounded_receive, secure_send)
            if path == "/api/models/select" and scope["method"] == "POST":
                raw = bytearray()
                while True:
                    message = await bounded_receive()
                    if message["type"] == "http.disconnect":
                        raise HTTPException(400, "Model selection body disconnected")
                    raw.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
                from workbench.hosted_model_writes import select_model
                result = await run_in_threadpool(select_model, snapshots, identity, json.loads(raw),
                    headers.get("x-workbench-project"), headers.get("x-workbench-package"), worker_progress)
                return await JSONResponse(result)(scope, bounded_receive, secure_send)
            if history_read:
                from workbench.hosted_agent_reads import history_response
                result = await run_in_threadpool(history_response, snapshots, identity, path,
                    headers.get("x-workbench-project"), headers.get("x-workbench-package"),
                    parse_qs(scope.get("query_string", b"").decode()), worker_progress)
                await progress("responding")
                await JSONResponse(result)(scope, bounded_receive, secure_send)
                if diagnostic_id is not None:
                    self.loading.finish(diagnostic_owner, diagnostic_id)
                return
            if selected_object_read:
                from workbench.hosted_object_reads import object_read_response
                result = await run_in_threadpool(object_read_response, snapshots, path,
                    headers["x-workbench-project"], headers["x-workbench-package"],
                    parse_qs(scope.get("query_string", b"").decode()), worker_progress)
                await progress("responding")
                response = result if isinstance(result, Response) else JSONResponse(result)
                await response(scope, bounded_receive, secure_send)
                if diagnostic_id is not None:
                    self.loading.finish(diagnostic_owner, diagnostic_id)
                return

    async def _early_response(self, *, identity, owner, path, scope, headers, forwarded,
                              external_key, clean_scope, receive, secure_send):
        from workbench.external_models import PREFIX as external_prefix, cookie_token

        async def send_response(response):
            await response(clean_scope, receive, secure_send)
            return True

        if (scope["method"] == "POST"
                and re.fullmatch(r"/api/agents/runs/[a-f0-9]{32}/cancel", path)):
            project, package = headers.get("x-workbench-project"), headers.get("x-workbench-package")
            with self.sessions.lock:
                session = self.sessions.sessions.get(owner)
                live_cancel = (session is not None and not session.discarded and session.app is not None
                    and project is not None and package is not None
                    and session.live_scope == (project, package))
                if live_cancel:
                    session.leases += 1
                    session.touched = monotonic()
            if live_cancel:
                # Stop must reach the existing worker, not wait behind the
                # mutation draining that worker. Its normal route rechecks
                # membership and the run's cancellation capability.
                try:
                    await session.app(clean_scope, receive, secure_send)
                    return True
                finally:
                    with CancelScope(shield=True):
                        await run_in_threadpool(self.sessions.release, session)

        if path.startswith(external_prefix):
            from workbench.external_models import handle as external_handle
            if external_key is None:
                response = JSONResponse({"detail": "Select an explicit project and ready package"}, status_code=409)
                return await send_response(response)
            try:
                self.external_models.rate(external_key[0])
                try:
                    length = int(headers.get("content-length", "0"))
                except ValueError:
                    raise HTTPException(400, "Invalid request size") from None
                if not 0 <= length <= 8192:
                    raise HTTPException(413, "External model request exceeds size limit")
                if scope.get("query_string"):
                    raise HTTPException(400, "External model requests do not accept query parameters")
                await run_in_threadpool(self.authorize_external_scope, identity, forwarded, external_key)
                with self.guard:
                    if (scope["method"] == "POST"
                            and self.user_queues.get(owner, {}).get("requests", 0)):
                        raise HTTPException(409, "Wait for all active model jobs before changing provider")
                await external_handle(self.external_models, external_key, headers,
                    {**clean_scope, "scheme": "https"}, receive, secure_send, rate_checked=True)
                return True
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
                return await send_response(response)
            except Exception:
                response = JSONResponse({"detail": "External model session scope unavailable"}, status_code=503)
                return await send_response(response)

        if external_key is not None and self.external_models.active(external_key, cookie_token(headers)):
            status = self.external_models.status(external_key, cookie_token(headers))
            if path in {"/api/models/connect", "/api/models/select"}:
                raise HTTPException(409, "Disconnect external models before changing Databricks controls")
            if path == "/api/models/status" and scope["method"] == "GET":
                await run_in_threadpool(self.authorize_external_scope, identity, forwarded, external_key)
                response = JSONResponse({**status, "roles": ROLE_PURPOSES, "connected": status["configured"]})
                return await send_response(response)
            from workbench.external_models import model_action
            if model_action(scope["method"], path) and not status["configured"]:
                raise HTTPException(409, "External model session expired; reconnect or disconnect explicitly")

        if path == "/":
            markup = await run_in_threadpool((STATIC / "index.html").read_text, encoding="utf-8")
            return await send_response(Response(markup, media_type="text/html", headers={"Cache-Control": "no-store"}))
        if path.startswith("/static/"):
            static_scope = {**clean_scope, "path": path[len("/static"):], "root_path": ""}
            await self.static(static_scope, receive, secure_send)
            return True
        if path == "/api/health":
            from workbench.app import _scope_capabilities
            from workbench.capabilities import capability_inventory
            response = JSONResponse({"status": "ok", "local_only": False, "authentication": "databricks_obo",
                "heartbeat_enabled": self.heartbeats.enabled, "capabilities": _scope_capabilities(True),
                "capability_inventory": capability_inventory(True), "generation_allowed": False,
                "deployment_allowed": False})
            return await send_response(response)
        if path == "/api/application-log-analytics/process" and scope["method"] == "GET":
            from workbench.application_log_analytics import process_sample
            return await send_response(JSONResponse(process_sample(), headers={"Cache-Control": "no-store"}))
        if path == "/api/models/workspace" and scope["method"] == "GET":
            return await send_response(JSONResponse({"workspace": identity.host, "mode": "obo", "profile": None,
                "auth_check": "verified", "connected": False}))
        if path == "/api/databricks/volumes/status" and scope["method"] == "GET":
            response = JSONResponse({"mode": "obo", "profile": None, "configured": True, "connected": True,
                "auth_check": "verified", "authentication": "databricks_obo", "host": identity.host,
                "user_id": identity.user_id, "local_only": False, "production_remote_enabled": True})
            return await send_response(response)
        if path == "/api/oracle-federation/metadata" and scope["method"] == "GET":
            from workbench.oracle_federation import discover
            result = await run_in_threadpool(discover, identity, scope.get("query_string", b""))
            return await send_response(JSONResponse(result))
        if path == "/api/model-pricing/list" and scope["method"] == "GET":
            result = await run_in_threadpool(self.model_price_rates.get)
            return await send_response(JSONResponse(result))
        if path == "/api/hosted/logs" and scope["method"] == "GET":
            try:
                limit = int(parse_qs(scope.get("query_string", b"").decode()).get("limit", ["100"])[0])
            except (TypeError, ValueError):
                raise HTTPException(422, "Diagnostic log limit must be an integer") from None
            result = {"items": self.diagnostics.list(owner_key(identity.host, identity.user_id), limit=limit),
                "storage": "bounded per-user in-memory diagnostics; cleared when this app instance restarts",
                "sql_text_logged": False, "credentials_logged": False}
            return await send_response(JSONResponse(result))
        if path.startswith("/api/hosted/logs/") and scope["method"] == "GET":
            diagnostic_id = path.removeprefix("/api/hosted/logs/")
            result = self.diagnostics.find(owner_key(identity.host, identity.user_id), diagnostic_id)
            if result is None:
                raise HTTPException(404, "No matching diagnostic is retained by this app instance")
            return await send_response(JSONResponse({"item": result,
                "storage": "bounded per-user in-memory diagnostics; cleared when this app instance restarts"}))
        if path.startswith("/api/hosted/loading/") and scope["method"] == "GET":
            from workbench.hosted_loading import warehouse_status
            result = self.loading.snapshot(owner, path[len("/api/hosted/loading/"):])
            result["warehouse"] = await run_in_threadpool(warehouse_status, identity, self.config.warehouse)
            result.update(self.loading.snapshot(owner, result["diagnostic_id"]))
            return await send_response(JSONResponse(result))
        if path == "/api/hosted/heartbeat" and scope["method"] == "POST":
            result = await run_in_threadpool(self.heartbeats.touch, identity)
            return await send_response(JSONResponse(result))
        return False

    async def _bounded_request(self, *, path, scope, headers, receive, artifact_read):
        limit = (256 * 1024 + 4096 if path == "/api/support/skills" and scope["method"] == "POST" else
                 80 * 1024 * 1024 + 65536 if path == "/api/packages/upload" else
                 160 * 1024 * 1024 + 65536 if path.endswith("/packages/upload") else
                 10 * 1024 * 1024 + 65536 if path.endswith("/documents") else 65536)
        if path.startswith("/api/hosted/uploads/") and scope["method"] == "PUT":
            limit = self.uploads.chunk_bytes
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if length < 0 or length > limit:
            raise HTTPException(413, "Request exceeds hosted body limit")
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, "Request exceeds hosted body limit")
            return message

        artifact_body = None
        if artifact_read:
            from workbench.app import WorkflowArtifactsBody
            media = headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
            if media != "application/json" and not (media.startswith("application/") and media.endswith("+json")):
                raise HTTPException(422, "Invalid workflow artifact request body")
            raw = bytearray()
            while True:
                message = await bounded_receive()
                if message["type"] == "http.disconnect":
                    raise HTTPException(400, "Workflow artifact body disconnected")
                raw.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            try:
                artifact_body = WorkflowArtifactsBody.model_validate_json(bytes(raw))
            except ValueError:
                raise HTTPException(422, "Invalid workflow artifact request body") from None
            if artifact_body.consent is not True:
                raise HTTPException(403, "Explicit workflow artifact generation consent:true is required")
            if not headers.get("x-workbench-project"):
                raise HTTPException(400, "Workflow artifact reads require an explicit project")
            if not headers.get("x-workbench-package"):
                raise HTTPException(409, "No package is ready; select an explicit project package")
        return length, bounded_receive, artifact_body

    async def _early_data_read(self, *, path, scope, headers, identity, forwarded,
                               clean_scope, receive, secure_send, authentication_seconds):
        if path == "/api/migration-activity" and scope["method"] == "GET":
            from workbench.migration_activity import hosted_activity
            query = parse_qs(scope.get("query_string", b"").decode("latin1"))
            project = query.get("project_id", [""])[-1]
            package = query.get("package_hash", [""])[-1]
            if (not re.fullmatch(r"[a-f0-9]{32}", project) or not re.fullmatch(r"[a-f0-9]{64}", package)
                    or any(len(values) != 1 for values in query.values())):
                raise HTTPException(422, "Invalid activity editing scope")
            if (headers.get("x-workbench-project") != project
                    or headers.get("x-workbench-package") != package):
                raise HTTPException(409, "Selected editing scope changed")
            remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix, initialize=False)
            with tempfile.TemporaryDirectory(prefix="workbench-activity-") as temporary:
                snapshots = DeltaRuntime(current_identity, remote, temporary,
                    warehouse=self.config.warehouse, forbidden_token=forwarded)
                result = await run_in_threadpool(hosted_activity, snapshots, project, package)
            await JSONResponse(result)(clean_scope, receive, secure_send)
            return True
        if scope["method"] == "GET" and (path == "/api/projects" or re.fullmatch(
                r"/api/projects/[a-f0-9]{32}(?:/jobs/[a-f0-9]{32})?", path)):
            from workbench.hosted_registry import registry_response
            remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix)
            with tempfile.TemporaryDirectory(prefix="workbench-registry-") as temporary:
                snapshots = DeltaRuntime(current_identity, remote, temporary,
                    warehouse=self.config.warehouse, forbidden_token=forwarded)
                result = await run_in_threadpool(registry_response, snapshots, path)
            await JSONResponse(result)(clean_scope, receive, secure_send)
            return True
        if not ((path == "/api/models/status" and scope["method"] == "GET")
                or (path == "/api/models/connect" and scope["method"] == "POST")):
            return False
        with tempfile.TemporaryDirectory(prefix="workbench-models-") as temporary:
            if path.endswith("/connect"):
                listing_started = monotonic()
                inventory = await run_in_threadpool(WorkspaceModels(Path(temporary)).discover, identity)
                result = {"connected": True, **inventory, "mode": "obo", "default_route_configured": False,
                    "timings": {"authentication_seconds": round(authentication_seconds, 3),
                                "endpoint_list_seconds": round(monotonic() - listing_started, 3)},
                    "runtime_verified": False, "data_egress_consent_required_for_each_run": True}
            else:
                from workbench.hosted_model_reads import model_status
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix, initialize=False)
                snapshots = DeltaRuntime(current_identity, remote, temporary, warehouse=self.config.warehouse,
                                         forbidden_token=forwarded)
                result = await run_in_threadpool(model_status, snapshots, identity,
                    headers.get("x-workbench-project"), headers.get("x-workbench-package"))
        await JSONResponse(result)(clean_scope, receive, secure_send)
        return True

    async def _hosted_intake_action(self, *, path, scope, headers, identity, external_key,
                                    clean_scope, receive, bounded_receive, secure_send):
        if (scope.get("workbench.operation_execution")
                or not (path.startswith(("/api/hosted/uploads", "/api/hosted/operations"))
                        or (path == "/api/models/select" and scope["method"] == "POST"))):
            return False, None
        maximum = self.uploads.chunk_bytes if scope["method"] == "PUT" else 32768
        raw = bytearray()
        try:
            length = int(next((value.decode("latin1") for name, value in scope["headers"]
                               if name.lower() == b"content-length"), "0"))
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if length > maximum:
            raise HTTPException(413, "Hosted intake request exceeds bounded chunk or metadata limit")
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise HTTPException(400, "Transfer disconnected before acknowledgement")
                raw.extend(message.get("body", b""))
                if len(raw) > maximum:
                    raise HTTPException(413, "Hosted intake request exceeds bounded chunk or metadata limit")
                if not message.get("more_body", False):
                    break
        remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix,
                                         initialize=path != "/api/models/select")
        loop = asyncio.get_running_loop()
        execution_operation_id = None

        def execute(method, action_path, query, action_headers, body):
            from workbench.hosted_operations import current_progress
            try:
                return asyncio.run_coroutine_threadsafe(self.execute_operation(
                    {**scope, "workbench.active_operation_id": execution_operation_id}, method, action_path,
                    query, action_headers, body, current_progress()), loop).result()
            finally:
                if external_key is not None and execution_operation_id:
                    self.external_models.end(external_key, execution_operation_id)

        handler = self.uploads if path.startswith("/api/hosted/uploads") else self.operations
        action_path, action_body = path, bytes(raw)
        if path == "/api/models/select":
            try:
                selection = json.loads(raw)
                operation_id = selection.pop("operation_id")
            except (ValueError, KeyError, TypeError, AttributeError):
                raise HTTPException(422, "Model save requires a recorded operation_id and expected_route_version") from None
            action_path = "/api/hosted/operations"
            action_body = {"id": operation_id, "method": "POST", "path": path, "body": selection}
        reservation = None
        if action_path == "/api/hosted/operations" and scope["method"] == "POST":
            try:
                submitted = action_body if isinstance(action_body, dict) else json.loads(action_body)
                execution_operation_id = submitted.get("id") if isinstance(submitted, dict) else None
            except (ValueError, TypeError):
                submitted = None
            from workbench.external_models import cookie_token, model_action
            if (external_key is not None and isinstance(execution_operation_id, str)
                    and re.fullmatch(r"[a-f0-9]{32}", execution_operation_id)
                    and isinstance(submitted, dict)
                    and model_action(submitted.get("method"), str(submitted.get("path", "")))):
                with self.guard:
                    if self.external_models.active(external_key, cookie_token(headers)):
                        self.external_models.credential(external_key, cookie_token(headers))
                    if self.external_models.begin(external_key, execution_operation_id):
                        reservation = external_key, execution_operation_id
        result = await run_in_threadpool(handler.handle, identity, remote, scope["method"], action_path,
            scope.get("query_string", b"").decode(), headers, action_body, execute)
        if result is None:
            raise HTTPException(404, "Unknown hosted intake operation")
        status, payload = result
        if (status == 200 and scope["method"] == "GET"
                and re.fullmatch(r"/api/hosted/operations/[a-f0-9]{32}", path)):
            payload = self.loading.observe_waiting_operation(owner_key(identity.host, identity.user_id), payload)
        if status == 202:
            reservation = None
        if self.heartbeats.enabled and path.startswith("/api/hosted/uploads") and scope["method"] not in {
                "GET", "HEAD", "OPTIONS"}:
            await run_in_threadpool(self.heartbeats.touch, identity, "active_work")
        await JSONResponse(payload, status_code=status)(clean_scope, bounded_receive, secure_send)
        return True, reservation

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path == "/healthz" and scope["method"] == "GET":
            return await JSONResponse({"status": "ok"})(scope, receive, send)
        key, acquired, context, identity = None, False, None, None
        request_id, request_started, queue_wait_seconds = uuid4().hex, monotonic(), 0.0
        flow = HostedRequestFlow(self, scope, send)
        queue_entry, queue_acquired = None, False
        heartbeat_task, heartbeat_stop = None, None
        diagnostic_id, diagnostic_owner = None, None
        external_context, model_reservation = None, None
        operation_progress = flow.operation_progress
        progress, worker_progress, secure_send = flow.progress, flow.worker_progress, flow.secure_send

        def record_failure(error, status):
            if identity is None:
                return None
            import traceback
            frames = [(Path(frame.filename).name, frame.name, frame.lineno)
                      for frame in traceback.extract_tb(error.__traceback__)]
            event = self.diagnostics.record(owner_key(identity.host, identity.user_id),
                method=scope["method"], path=path, status=status, error=error, frames=frames,
                phase=getattr(error, "diagnostic_hosted_phase", None) or flow.phase,
                request_id=request_id, operation_id=scope.get("workbench.active_operation_id"),
                elapsed_ms=int((monotonic() - request_started) * 1000), forbidden_token=forwarded)
            import logging
            logging.getLogger(__name__).error("Hosted request diagnostic %s", json.dumps(event, sort_keys=True))
            return event

        try:
            headers = {}
            for name, value in scope["headers"]:
                name = name.decode("latin1").lower()
                if name in headers and name in {"x-forwarded-access-token", "host", "x-forwarded-host", "origin", "content-length", "xworkbenchreadid", "content-type", "cookie", "x-workbench-request", "x-workbench-project", "x-workbench-package"}:
                    raise HTTPException(400, "Duplicate security-sensitive header")
                headers[name] = value.decode("latin1")
            # Apps terminates TLS and can replace Host with its internal route.
            # Only the trusted deployment proxy's public host is authoritative.
            public_host = headers.get("x-forwarded-host", headers.get("host", "")).lower()
            if public_host != urlsplit(self.config.app_origin).netloc:
                raise HTTPException(400, "Unexpected application host")
            fixture_read = (scope["method"] == "POST" and (path == "/api/workflows/fixture-metadata"
                            or re.fullmatch(r"/api/target-selections/[a-f0-9]{64}/verify", path)))
            artifact_read = scope["method"] == "POST" and path in {
                "/api/proposals/workflow-artifacts", "/api/proposals/workflow-artifacts/archive"}
            notebook_preview_read = (scope["method"] == "POST" and re.fullmatch(
                r"/api/jobs/[a-f0-9]{32}/spark-drafts/(?:edit-preview|edit-discard-preview)", path))
            temporary_job_read = (scope["method"] == "POST" and re.fullmatch(
                r"/api/jobs/[a-f0-9]{32}/temporary-job/(?:capabilities|status|preview|sessions|saved-settings)", path))
            mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not (
                fixture_read or artifact_read or notebook_preview_read or temporary_job_read)
            if mutation or fixture_read or artifact_read or notebook_preview_read or temporary_job_read:
                if (headers.get("x-workbench-request") != "1" or headers.get("sec-fetch-site") == "cross-site"
                        or headers.get("origin", self.config.app_origin) != self.config.app_origin):
                    raise HTTPException(403, "Same-origin mutations require X-Workbench-Request: 1")
            forwarded = headers.get("x-forwarded-access-token", "")
            if not forwarded:
                raise HTTPException(401, "Databricks forwarded user authorization is required")
            authentication_started = monotonic()
            identity = await run_in_threadpool(self.identity_resolver, self.config.workspace_host, forwarded,
                                               trusted_proxy=True, deployment_host=self.config.workspace_host)
            authentication_seconds = monotonic() - authentication_started
            if identity.mode != "obo" or identity.host != self.config.workspace_host:
                raise HTTPException(401, "Forwarded identity workspace mismatch")
            if path == "/api/package/warm" and not scope.get("workbench.operation_execution"):
                raise HTTPException(404, "Unknown hosted route")
            if self.identity_resolver is obo_identity:
                from workbench.hosted_volumes import with_sql_volumes
                identity = with_sql_volumes(identity, self.config.warehouse)
            context = _CURRENT.set(identity)
            from workbench.external_models import CURRENT as EXTERNAL_CURRENT, cookie_token, scope_key
            external_key = None
            if (re.fullmatch(r"[a-f0-9]{32}", headers.get("x-workbench-project", ""))
                    and re.fullmatch(r"[a-f0-9]{64}", headers.get("x-workbench-package", ""))):
                external_key = scope_key(identity.host + "\n" + identity.user_id,
                    headers["x-workbench-project"], headers["x-workbench-package"])
                external_context = EXTERNAL_CURRENT.set((self.external_models, external_key, cookie_token(headers)))
            flow.phase = "authenticated"
            from workbench.hosted_loading import read_id, warehouse_status
            if "xworkbenchreadid" in headers:
                read_id(headers["xworkbenchreadid"])
            # Remove bearer credentials before entering existing application code.
            clean_scope = {**scope, "workbench.local_boundary_checked": True,
                           "workbench.hosted_read_only": not mutation,
                           "headers": [(k, v) for k, v in scope["headers"]
                                       if k.lower() not in {b"x-forwarded-access-token", b"authorization"}]}
            key = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
            flow.bind_activity(key, request_id)
            if await self._early_response(identity=identity, owner=key, path=path, scope=scope,
                headers=headers, forwarded=forwarded, external_key=external_key,
                clean_scope=clean_scope, receive=receive, secure_send=secure_send):
                return
            length, bounded_receive, artifact_body = await self._bounded_request(
                path=path, scope=scope, headers=headers, receive=receive, artifact_read=artifact_read)
            if path == "/api/support/official-search" and scope["method"] == "POST":
                # This global, ephemeral search has no project data or durable
                # state. Fresh OBO and the same-origin mutation checks ran above.
                if headers.get("x-workbench-project") or headers.get("x-workbench-package"):
                    raise HTTPException(400, "Official search does not accept project context")
                from starlette.requests import Request
                from workbench.support_search import official_search_response
                response = await official_search_response(Request(clean_scope, bounded_receive))
                return await response(clean_scope, bounded_receive, secure_send)
            if await self._early_data_read(path=path, scope=scope, headers=headers, identity=identity,
                forwarded=forwarded, clean_scope=clean_scope, receive=receive, secure_send=secure_send,
                authentication_seconds=authentication_seconds):
                return

            handled, model_reservation = await self._hosted_intake_action(path=path, scope=scope,
                headers=headers, identity=identity, external_key=external_key, clean_scope=clean_scope,
                receive=receive, bounded_receive=bounded_receive, secure_send=secure_send)
            if handled:
                return

            key = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
            from workbench.external_models import model_action
            if external_key is not None and model_action(scope["method"], path):
                action = scope.get("workbench.active_operation_id") or request_id
                with self.guard:
                    fresh_reservation = self.external_models.begin(external_key, action)
                if fresh_reservation:
                    model_reservation = external_key, action
            if scope["method"] == "GET" and "xworkbenchreadid" in headers:
                diagnostic_owner = key
                candidate_id = headers["xworkbenchreadid"]
                try:
                    self.loading.begin(key, candidate_id, path=path)
                    diagnostic_id = candidate_id
                    flow.bind_diagnostic(key, candidate_id)
                except HTTPException as error:
                    # Diagnostics are observational only; collisions or a full
                    # diagnostic buffer must not reject the underlying read.
                    if error.status_code not in {409, 429}:
                        raise
                    logging.getLogger(__name__).warning(
                        "Hosted loading diagnostic unavailable (HTTP %s); proceeding without it",
                        error.status_code)
            # Read-only actions restore an immutable committed generation and
            # publish only their independently serialized audit event. They may
            # safely overlap same-user reads and must not queue behind a long
            # upload/review mutation. Mutations remain serialized per user.
            if mutation:
                with self.guard:
                    queue_entry = self.user_queues.setdefault(key, {"lock": AsyncLock(), "requests": 0,
                                                                  "active": None})
                    queued_before = queue_entry["requests"]
                    queue_entry["requests"] += 1
                    active_write = queue_entry["active"]
                durable_execution = bool(scope.get("workbench.operation_execution"))
                if queued_before and (not durable_execution or queued_before >= 8):
                    # The key includes the verified workspace and actor. Never
                    # reveal another user's operation, and never wait 90s on a
                    # proxy-owned request merely to report writer contention.
                    return await JSONResponse({"detail": "An authenticated write is active. Inspect its receipt and saved state before submitting another write.",
                        "active_write": active_write, "pending_writes": queued_before,
                        "change_applied": False}, status_code=409)(clean_scope, receive, secure_send)
                if queued_before:
                    logging.getLogger(__name__).info("Hosted mutation queued %s", json.dumps({
                        "request_id": request_id, "owner": key[:12], "method": scope["method"],
                        "path": re.sub(r"/[0-9a-f]{32,64}(?=/|$)", "/{id}", path, flags=re.I),
                        "pending_before": queued_before}))
                try:
                    # AnyIO locks are task-owned. Python 3.11 wait_for() creates
                    # a child task, so releasing in this dispatcher task fails
                    # and strands the lock. A cancel scope keeps the owner and
                    # releaser identical on every supported Python runtime.
                    with fail_after(90 if durable_execution else 1):
                        await queue_entry["lock"].acquire()
                except TimeoutError:
                    logging.getLogger(__name__).warning("Hosted mutation queue timed out %s", json.dumps({
                        "request_id": request_id, "owner": key[:12], "method": scope["method"],
                        "path": re.sub(r"/[0-9a-f]{32,64}(?=/|$)", "/{id}", path, flags=re.I),
                        "pending_before": queued_before, "wait_seconds": round(monotonic() - request_started, 3)}))
                    raise HTTPException(409, "Hosted write queue wait expired; no change was applied. Inspect the active operation before a new submission.") from None
                queue_acquired = True
                with self.guard:
                    queue_entry["active"] = {"request_id": request_id,
                        "operation_id": scope.get("workbench.active_operation_id"),
                        "path": re.sub(r"/[0-9a-f]{32,64}(?=/|$)", "/{id}", path, flags=re.I)}
                queue_wait_seconds = monotonic() - request_started
            else:
                queued_before = 0
            with self.guard:
                active_for_user = self.users.get(key, 0)
                # The limit is active authenticated users, not requests. A
                # browser routinely issues several independent read-only GETs
                # for one user (overview, logs, status and model capability).
                # Do not let those same-user reads consume all user slots.
                if active_for_user == 0 and not self.capacity.acquire(blocking=False):
                    raise HTTPException(429, "Hosted active-user capacity reached; retry after another user's action completes")
                self.users[key] = active_for_user + 1
                acquired = True
            flow.phase = "processing"
            if path.startswith("/api/"):
                logging.getLogger(__name__).info("Hosted request started %s", json.dumps({
                    "request_id": request_id, "owner": key[:12], "method": scope["method"],
                    "path": re.sub(r"/[0-9a-f]{32,64}(?=/|$)", "/{id}", path, flags=re.I),
                    "read_only": not mutation, "queue_wait_seconds": round(queue_wait_seconds, 3),
                    "pending_mutations": queued_before}))
            if self.heartbeats.enabled and scope.get("workbench.operation_execution"):
                heartbeat_stop = asyncio.Event()

                async def active_work_heartbeat():
                    while not heartbeat_stop.is_set():
                        await run_in_threadpool(self.heartbeats.touch, identity, "active_work")
                        try:
                            await asyncio.wait_for(heartbeat_stop.wait(), timeout=300)
                        except asyncio.TimeoutError:
                            continue

                heartbeat_task = asyncio.create_task(active_work_heartbeat())
            await progress("starting")
            from workbench.hosted_object_reads import ROUTE as OBJECT_READ_ROUTE, object_read_response
            from workbench.hosted_agent_reads import PATHS as HISTORY_PATHS, history_response
            diagnostic_read = (scope["method"] == "GET"
                and re.fullmatch(r"/api/hosted/logs/[a-f0-9]{32}", path) is not None)
            history_read = (diagnostic_read or scope["method"] == "GET" and path in HISTORY_PATHS
                           and (headers.get("x-workbench-package")
                               or (path == "/api/tracing/status" and not headers.get("x-workbench-project"))))
            if history_read and path == "/api/agents/runs":
                with self.sessions.lock:
                    active_session = self.sessions.sessions.get(key)
                    if (active_session is not None and not active_session.discarded
                            and (active_session.live_scope == (headers.get("x-workbench-project"),
                                                               headers.get("x-workbench-package"))
                                 or (active_session.save_read_scope == (headers.get("x-workbench-project"),
                                                                        headers.get("x-workbench-package"))
                                     and parse_qs(scope.get("query_string", b"").decode("latin1")).get("status_only") == ["true"]))):
                        history_read = False
            selected_object_read = (scope["method"] == "GET" and OBJECT_READ_ROUTE.fullmatch(path)
                                    and headers.get("x-workbench-project") and headers.get("x-workbench-package"))
            package_warm_read = (scope["method"] == "GET" and path == "/api/package/warm"
                                 and headers.get("x-workbench-project") and headers.get("x-workbench-package"))
            if path == "/api/hosted/status":
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix, initialize=False)
                return await JSONResponse({"authentication": "databricks_obo", "user_id": identity.user_id,
                                           "volume_root": remote, "mutations_serialized_per_user": True,
                                           "read_only_actions_may_run_concurrently": False,
                                           "retained_owner_sessions": True,
                                           "state_max_bytes": 1024 ** 3, "state_max_files": 20000,
                                           "authority": "committed_delta_metadata_and_volume_artifacts",
                                           "sqlite": "ephemeral_execution_cache_only",
                                           "warehouse_id": self.config.warehouse,
                                           "durability": "commit_last_generation_not_distributed_transaction",
                                           "generation_allowed": False, "deployment_allowed": False})(clean_scope, receive, secure_send)
            # The selected package's ordinary UI reads and preparation use the
            # same retained service as Invoke. Projection-only readers remain
            # for independent history and consented artifact actions.
            special_read = bool(artifact_read or history_read or path == "/api/models/select")
            if special_read:
                await self._run_special_read(identity=identity, owner=key, forwarded=forwarded,
                    path=path, scope=clean_scope, headers=headers, bounded_receive=bounded_receive,
                    secure_send=secure_send, progress=progress, worker_progress=worker_progress,
                    diagnostic_id=diagnostic_id, diagnostic_owner=diagnostic_owner,
                    package_warm_read=package_warm_read, artifact_read=artifact_read,
                    history_read=history_read, selected_object_read=selected_object_read,
                    artifact_body=artifact_body)
            else:
                action = self._run_retained_action(owner=key, identity=identity, forwarded=forwarded,
                        mutation=mutation, path=path, scope=clean_scope, headers=headers,
                        bounded_receive=bounded_receive, secure_send=secure_send, progress=progress,
                        worker_progress=worker_progress, diagnostic_id=diagnostic_id,
                        diagnostic_owner=diagnostic_owner, operation_progress=operation_progress)
                if not mutation or path == "/api/agents/run":
                    await _finish_action_before_cancellation(action)
                else:
                    with CancelScope(shield=True):
                        await action
        except HTTPException as error:
            if flow.response_status is not None:
                raise
            flow.fail_loading(error)
            event = record_failure(error, error.status_code) if error.status_code >= 400 else None
            detail = error.detail
            if event:
                detail = f"{detail} (diagnostic {event['id']}; see Logs)"
            await JSONResponse({**flow.error_body(detail, error), **({"failure_id": event["id"]} if event else {})},
                               status_code=error.status_code)(scope, receive, secure_send)
        except AuthenticationUnavailable as error:
            if flow.response_status is not None:
                raise
            flow.fail_loading(error)
            event = record_failure(error, 401)
            await JSONResponse({**flow.error_body("User authorization expired or unavailable; reopen the app. No alternate identity was used.", error),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=401)(scope, receive, secure_send)
        except (DiscoveryUnavailable, RoutingError) as error:
            if flow.response_status is not None:
                raise
            flow.fail_loading(error)
            event = record_failure(error, 403)
            await JSONResponse({**flow.error_body("User model discovery or routing unavailable; check consent, scopes and endpoint permissions.", error),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=403)(scope, receive, secure_send)
        except Exception as error:
            if flow.response_status is not None:
                raise
            flow.fail_loading(error)
            workspace_denied = (getattr(error, "diagnostic_workspace_operation", None) == "mkdirs"
                                and type(error).__name__ == "PermissionDenied")
            status = 403 if workspace_denied else 503
            event = record_failure(error, status)
            detail = ("Databricks denied creation of the temporary notebook folder. No notebook was uploaded; inspect Logs for the provider's reason."
                      if workspace_denied else
                      "Hosted action or Delta generation completion is unconfirmed. Inspect committed state and Logs before retrying. No local fallback was used.")
            await JSONResponse({**flow.error_body(detail, error),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=status)(scope, receive, secure_send)
        finally:
            if model_reservation is not None:
                self.external_models.end(*model_reservation)
            if external_context is not None:
                from workbench.external_models import CURRENT as EXTERNAL_CURRENT
                EXTERNAL_CURRENT.reset(external_context)
            if diagnostic_id is not None:
                # Cancellation or an early response must not leave a running diagnostic forever.
                self.loading.finish(diagnostic_owner, diagnostic_id, exception_class="IncompleteRead")
            if identity is not None and path.startswith("/api/"):
                logging.getLogger(__name__).info("Hosted request finished %s", json.dumps({
                    "request_id": request_id, "owner": key[:12] if key else None,
                    "method": scope["method"],
                    "path": re.sub(r"/[0-9a-f]{32,64}(?=/|$)", "/{id}", path, flags=re.I),
                    "status": flow.response_status, "phase": flow.phase,
                    "elapsed_seconds": round(monotonic() - request_started, 3),
                    "queue_wait_seconds": round(queue_wait_seconds, 3)}))
            try:
                if heartbeat_task is not None:
                    heartbeat_stop.set()
                    with CancelScope(shield=True):
                        await heartbeat_task
            finally:
                if context is not None:
                    _CURRENT.reset(context)
                if acquired:
                    with self.guard:
                        count = self.users.get(key, 0)
                        if count <= 1:
                            self.users.pop(key, None)
                            self.capacity.release()
                        else:
                            self.users[key] = count - 1
                if queue_entry is not None:
                    try:
                        if queue_acquired:
                            queue_entry["lock"].release()
                    finally:
                        with self.guard:
                            if queue_acquired:
                                queue_entry["active"] = None
                            queue_entry["requests"] -= 1
                            if queue_entry["requests"] == 0:
                                self.user_queues.pop(key, None)


def create_hosted_app(config=None, *, identity_resolver=obo_identity):
    if os.environ.get("WORKBENCH_MODEL", "").strip():
        raise ValueError("Hosted mode requires user-scoped model selection, not WORKBENCH_MODEL")
    config = config or HostedConfig.environment()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    dispatcher = HostedDispatcher(config, identity_resolver)
    app.mount("/", dispatcher)

    @app.on_event("shutdown")
    async def close_hosted_sessions():
        await run_in_threadpool(dispatcher.sessions.close_all)

    return app


def main():
    import uvicorn
    # Exactly one process/replica. No reload, ambient credential client, or dotenv.
    uvicorn.run(create_hosted_app(), host="0.0.0.0", port=int(os.environ["DATABRICKS_APP_PORT"]),
                workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    from workbench.hosted import main as hosted_main
    hosted_main()