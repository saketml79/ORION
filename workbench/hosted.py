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
_CURRENT_DELTA_RUNTIME = ContextVar("workbench_hosted_delta_runtime", default=None)
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
    sources: dict = field(default_factory=dict)
    committed_sources: set = field(default_factory=set)
    leases: int = 0
    discarded: bool = False
    touched: float = field(default_factory=monotonic)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    live_scope: tuple[str, str] | None = None
    live_readers: int = 0
    live_readers_done: asyncio.Event = field(default_factory=asyncio.Event)


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

    async def enter(self, owner, read_scope=None):
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
                        if (read_scope is not None and session.live_scope == read_scope
                                and not session.discarded and session.app is not None):
                            session.live_readers += 1
                            session.live_readers_done.clear()
                            borrowed = True
                            break
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
        session.sources, session.committed_sources = {}, set()
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

    def __post_init__(self):
        object.__setattr__(self, "workspace_host", _host(self.workspace_host))
        object.__setattr__(self, "app_origin", _host(self.app_origin))
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.catalog_prefix)
                or type(self.max_active_users) is not int or not 1 <= self.max_active_users <= 4):
            raise ValueError("Invalid hosted naming prefix or concurrency limit")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.warehouse):
            raise ValueError("An explicit hosted SQL warehouse is required")

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

    def bind_diagnostic(self, owner, diagnostic_id):
        self.diagnostic_owner, self.diagnostic_id = owner, diagnostic_id

    def loading_progress(self, phase, **counters):
        self.phase = phase
        if self.diagnostic_id is not None:
            self.dispatcher.loading.update(self.diagnostic_owner, self.diagnostic_id, phase, **counters)

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

    def error_body(self, detail):
        return {"detail": detail, "failure_phase": self.phase,
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
        self.operations = HostedOperations(config.warehouse)
        self.uploads = HostedUploads(config.warehouse, max_file_bytes=max_xml_bytes(),
                         max_batch_bytes=max_batch_bytes())
        from workbench.heartbeat import Heartbeats
        self.heartbeats = Heartbeats.from_env(config.warehouse)
        from workbench.hosted_loading import HostedLoading
        self.loading = HostedLoading()
        self.diagnostics = HostedDiagnostics()

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
        read_scope = ((project, package) if not mutation and scope["method"] == "GET"
                      and re.fullmatch(r"[a-f0-9]{32}", project)
                      and re.fullmatch(r"[a-f0-9]{64}", package) else None)
        session, borrowed = await self.sessions.enter(owner, read_scope)
        delta_tokens, mutation_tokens = [], []
        action_runtimes = []
        action_token = _CURRENT_ACTION_RUNTIMES.set(action_runtimes)
        remote = None
        snapshots = None
        app = None
        reused_service = session.app is not None

        def bind(runtime, is_mutation):
            delta_tokens.append(_CURRENT_DELTA_RUNTIME.set(runtime))
            mutation_tokens.append(_CURRENT_MUTATION.set(is_mutation))

        async def cold_restore(initialize):
            runtime = DeltaRuntime(current_identity, remote, session.root, warehouse=self.config.warehouse,
                                   forbidden_token=forwarded)
            bind(runtime, initialize)
            restore_options = {"initialize": initialize}
            if diagnostic_id is not None or callable(operation_progress):
                restore_options["progress"] = worker_progress
            await run_in_threadpool(runtime.restore, **restore_options)
            if mutation or path in {"/api/logs", "/api/logs/export"}:
                await progress("audit_restore")
                await run_in_threadpool(runtime.restore_audit)
            await progress("preparing_package")
            retained_app = await run_in_threadpool(user_app, session.root, runtime,
                                                   read_only=True, execution_models=False)
            session.app = retained_app
            await run_in_threadpool(retained_app.state.service.start,
                worker_progress if callable(operation_progress) else None)
            await run_in_threadpool(retained_app.state.projects.start)
            session.remote, session.marker = remote, runtime.receipt_marker
            session.generation, session.sequence = runtime.generation, runtime.sequence
            session.sources = dict(runtime.sources)
            session.committed_sources = set(runtime._committed_sources)
            return runtime, retained_app

        try:
            warm_session = session.app is not None and isinstance(session.remote, str) and bool(session.remote)
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
                                         forbidden_token=forwarded)
                # A warm retained session is the current process's working copy.
                # OBO has been resolved for this request, and the per-user gate
                # serializes writes. Do not query Delta/Volume or reload artifacts
                # just to start an agent against an unchanged selected package.
                snapshots.ready = True
                snapshots._materialized = True
                snapshots.generation, snapshots.sequence = session.generation, session.sequence
                snapshots.receipt_marker = session.marker
                snapshots.sources = dict(session.sources)
                snapshots._committed_sources = set(session.committed_sources)
                bind(snapshots, mutation)
                app = session.app

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
                            await app(scope, bounded_receive, capture)
                except BaseException:
                    if mutation:
                        self.sessions.invalidate(session)
                    raise

                if mutation:
                    if (path == "/api/agents/run" and response_start is not None
                            and response_start.get("status") == 202 and project and package):
                        session.live_readers_done.set()
                        session.live_scope = (project, package)
                        self.sessions.notify(session)
                        observer = getattr(operation_progress, "accepted_agent", None)
                        if callable(observer):
                            # The 202 is an in-process acceptance, not a Delta commit.
                            # Never let an optional live observer affect the run.
                            try:
                                response.seek(0)
                                observer(json.load(response))
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
                    await progress("committing_delta")
                    await run_in_threadpool(snapshots.flush, prime_cache=False)
                    session.marker = snapshots.receipt_marker
                    session.generation, session.sequence = snapshots.generation, snapshots.sequence
                    session.sources = dict(snapshots.sources)
                    session.committed_sources = set(snapshots._committed_sources)
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
        except BaseException:
            if mutation and snapshots is not None:
                try:
                    if session.app is not None:
                        await run_in_threadpool(session.app.state.projects.drain)
                        await run_in_threadpool(session.app.state.service.drain)
                    for request_runtime in action_runtimes:
                        await run_in_threadpool(_close_request_runtime, request_runtime)
                    action_runtimes.clear()
                    self.sessions.reset(session)
                    remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix,
                                                     initialize=False)
                    await cold_restore(False)
                except BaseException:
                    self.sessions.invalidate(session)
            elif not borrowed:
                self.sessions.invalidate(session)
            raise
        finally:
            if not borrowed and session.discarded and session.app is not None:
                try:
                    await run_in_threadpool(session.app.state.projects.drain)
                    await run_in_threadpool(session.app.state.service.drain)
                except Exception:
                    logging.getLogger(__name__).warning("Hosted invalidated session drain was incomplete")
            for runtime in action_runtimes:
                await run_in_threadpool(_close_request_runtime, runtime)
            _CURRENT_ACTION_RUNTIMES.reset(action_token)
            for token in reversed(mutation_tokens):
                _CURRENT_MUTATION.reset(token)
            for token in reversed(delta_tokens):
                _CURRENT_DELTA_RUNTIME.reset(token)
            if borrowed:
                session.live_readers -= 1
                if not session.live_readers:
                    session.live_readers_done.set()
            else:
                session.gate.release()
                self.sessions.notify(session)
            await run_in_threadpool(self.sessions.release, session)

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
        limit = (80 * 1024 * 1024 + 65536 if path == "/api/packages/upload" else
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
                phase=flow.phase)
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
            mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not (fixture_read or artifact_read)
            if mutation or fixture_read or artifact_read:
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
            if await self._early_response(identity=identity, owner=key, path=path, scope=scope,
                headers=headers, forwarded=forwarded, external_key=external_key,
                clean_scope=clean_scope, receive=receive, secure_send=secure_send):
                return
            length, bounded_receive, artifact_body = await self._bounded_request(
                path=path, scope=scope, headers=headers, receive=receive, artifact_read=artifact_read)
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
                    self.loading.begin(key, candidate_id)
                    diagnostic_id = candidate_id
                    flow.bind_diagnostic(key, candidate_id)
                except HTTPException as error:
                    # Diagnostics are observational only. A repeated/invalid ID
                    # must never reject the actual package-understanding request.
                    if error.status_code != 409:
                        raise
                    logging.getLogger(__name__).warning(
                        "Hosted loading diagnostic id collision; proceeding without this diagnostic")
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
                            and active_session.live_scope == (headers.get("x-workbench-project"),
                                                              headers.get("x-workbench-package"))):
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
            flow.fail_loading(error)
            event = record_failure(error, error.status_code) if error.status_code >= 400 else None
            detail = error.detail
            if event:
                detail = f"{detail} (diagnostic {event['id']}; see Logs)"
            await JSONResponse({**flow.error_body(detail), **({"failure_id": event["id"]} if event else {})},
                               status_code=error.status_code)(scope, receive, secure_send)
        except AuthenticationUnavailable as error:
            flow.fail_loading(error)
            event = record_failure(error, 401)
            await JSONResponse({**flow.error_body("User authorization expired or unavailable; reopen the app. No alternate identity was used."),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=401)(scope, receive, secure_send)
        except (DiscoveryUnavailable, RoutingError) as error:
            flow.fail_loading(error)
            event = record_failure(error, 403)
            await JSONResponse({**flow.error_body("User model discovery or routing unavailable; check consent, scopes and endpoint permissions."),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=403)(scope, receive, secure_send)
        except Exception as error:
            flow.fail_loading(error)
            event = record_failure(error, 503)
            detail = ("The agent worker finished, but Databricks denied access while verifying a Unity Catalog metadata table during the final Delta save. The run result may remain only in this app instance. Inspect Logs and committed run state before retrying."
                if type(error).__name__ == "PermissionDenied" and flow.phase == "committing_delta"
                else "Hosted action or Delta generation completion is unconfirmed. Inspect committed state and Logs before retrying. No local fallback was used.")
            await JSONResponse({**flow.error_body(detail),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=503)(scope, receive, secure_send)
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
    main()