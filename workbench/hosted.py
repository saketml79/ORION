"""Databricks Apps only. Fresh OBO credentials and isolated runtime per action.

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
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
import json
from pathlib import Path
import re
import tempfile
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from anyio import CancelScope, Lock as AsyncLock, fail_after
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
    """Persist nonsecret selections only. Rebuild clients on every HTTP action."""
    service = app.state.service
    # Only the fresh requesting user's OBO identity may observe UC metadata.
    app.state.fixture_identity_provider = lambda profile: current_identity()
    router = WorkspaceModels(service.root / "models")
    from workbench.external_models import Runtime as ExternalRuntime, current_for_root
    external = current_for_root(service.root)
    if external is not None:
        store, external_key, session_token = external
        service.runtime = ExternalRuntime(store, external_key, session_token, service.root,
                                         uc_identity_provider=current_identity, cache=False)
    def configure_runtime():
        if external is not None:
            return
        identity = current_identity()
        if router.load(identity):
            models: dict[str, BaseChatModel] = {role: model for role, model in router.app_chat_models(identity).items()}
            service.runtime = AgentRuntime(service.root / "agents", role_models=models,
                                           identity_scope=identity.host + ":" + identity.user_id,
                                           knowledge_loader=getattr(service, "knowledge_loader", None),
                                           uc_identity_provider=current_identity)

    # Only execution actions need model clients. Metadata saves/discovery must
    # never construct six clients before reaching their actual route handler.
    if getattr(app.state, "hosted_execution_models", False):
        configure_runtime()
    # Set only inside the fresh, verified OBO action, never from an env flag.
    app.state.hosted_request_authenticated = current_identity().mode == "obo"

    @app.get("/api/models/status")
    def status():
        routes = router.load(current_identity())
        endpoints = {route.get("endpoint") for route in routes.values() if isinstance(route, dict)}
        return {"connected": True, "profile": None, "mode": "obo", "roles": ROLE_PURPOSES,
            "workspace": current_identity().host,
            "active_endpoint": next(iter(endpoints)) if len(endpoints) == 1 else None,
            "configured_roles": sorted(routes), "tool_calling_verified": False,
                "inference_ready": workspace_ready(service.runtime),
                "reason": "Request-scoped Databricks user authorization; no service-principal fallback."}

    @app.post("/api/models/connect")
    def connect():
        identity = current_identity()
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
    def install_models(app):
        app.state.hosted_execution_models = execution_models
        install_hosted_models(app)
    def factory(data_dir, sample=None):
        from workbench.external_models import current_for_root
        if execution_models and current_for_root(Path(data_dir)) is None:
            # Overlay separate settings authority before any client is built.
            from workbench.hosted_model_reads import model_status
            relative = Path(data_dir).relative_to(root).parts
            project = (relative[1] if len(relative) >= 2 and relative[0] == "projects"
                       else getattr(snapshots, "model_project", None))
            package = relative[3] if len(relative) >= 4 and relative[2] == "package-contexts" else None
            model_status(snapshots, identity, project, package, validate_ready=False)
        app = _create_package_app(data_dir, root / ".no-bundled-sample.xml",
                                   executor_factory=ActionExecutor,
                                   source_publisher=snapshots.publish_source,
                                   model_installer=install_models,
                                   record_audit_initialization=not read_only,
                                   package_cache_scope=package_cache_scope)
        from workbench.validation_campaigns import CampaignStore
        app.state.validation_store_provider = lambda: CampaignStore(delta=snapshots.store, artifacts=snapshots.artifacts)
        from workbench.knowledge_store import KnowledgeStore, project_for_root
        app.state.knowledge_store_provider = lambda: KnowledgeStore(snapshots.store, snapshots.artifacts)
        project = project_for_root(app.state.service.root)
        if project:
            # Created within this fresh OBO action only. No clients or graphs survive it.
            app.state.service.knowledge_loader = lambda: app.state.knowledge_store_provider().load(project)
            app.state.service.runtime.knowledge_loader = app.state.service.knowledge_loader
        return app
    app = factory(root)
    install_projects(app, factory, root / ".no-bundled-input",
                     executor_factory=ActionExecutor, source_publisher=snapshots.publish_source)
    install_volume_intake(app, service=HostedVolumes(app.state.projects, current_identity,
                                                   reference_registrar=snapshots.reference))
    if not read_only:
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
        self.guard, self.users = Lock(), {}
        self.user_queues = {}
        self.capacity = BoundedSemaphore(config.max_active_users)
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

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path == "/healthz" and scope["method"] == "GET":
            return await JSONResponse({"status": "ok"})(scope, receive, send)
        event_loop = asyncio.get_running_loop()
        key, acquired, context, identity = None, False, None, None
        request_id, request_started, queue_wait_seconds = uuid4().hex, monotonic(), 0.0
        request_phase, response_status = "authenticating", None
        queue_entry, queue_acquired = None, False
        heartbeat_task, heartbeat_stop = None, None
        diagnostic_id, diagnostic_owner = None, None
        external_context, model_reservation = None, None

        operation_progress = scope.get("workbench.operation_progress")

        def loading_progress(phase, **counters):
            nonlocal request_phase
            request_phase = phase
            if diagnostic_id is not None:
                self.loading.update(diagnostic_owner, diagnostic_id, phase, **counters)

        def operation_progress_values(counters):
            if "verified_files" in counters or "total_files" in counters:
                return {"completed": counters.get("verified_files"),
                        "total": counters.get("total_files"), "unit": "files"}
            return {k: counters[k] for k in ("completed", "total", "unit") if k in counters}

        async def progress(phase, **counters):
            loading_progress(phase, **counters)
            if callable(operation_progress):
                await run_in_threadpool(operation_progress, phase, **operation_progress_values(counters))

        def worker_progress(phase, **counters):
            """Bridge restore/index counters into the durable operation journal."""
            loading_progress(phase, **counters)
            if callable(operation_progress):
                future = asyncio.run_coroutine_threadsafe(
                    run_in_threadpool(operation_progress, phase, **operation_progress_values(counters)), event_loop)
                future.result()

        def fail_loading(error):
            if diagnostic_id is not None:
                self.loading.finish(diagnostic_owner, diagnostic_id, exception_class=type(error).__name__)

        def error_body(detail):
            return {"detail": detail, **({"diagnostic_id": diagnostic_id} if diagnostic_id else {})}

        def record_failure(error, status):
            if identity is None:
                return None
            import traceback
            frames = [(Path(frame.filename).name, frame.name, frame.lineno)
                      for frame in traceback.extract_tb(error.__traceback__)]
            event = self.diagnostics.record(owner_key(identity.host, identity.user_id),
                method=scope["method"], path=path, status=status, error=error, frames=frames)
            import logging
            logging.getLogger(__name__).error("Hosted request diagnostic %s", json.dumps(event, sort_keys=True))
            return event

        async def secure_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status")
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ]
            await send(message)

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
            if self.identity_resolver is obo_identity:
                from workbench.hosted_volumes import with_sql_volumes
                identity = with_sql_volumes(identity, self.config.warehouse)
            context = _CURRENT.set(identity)
            from workbench.external_models import CURRENT as EXTERNAL_CURRENT, PREFIX as EXTERNAL_PREFIX, cookie_token, scope_key
            external_key = None
            if (re.fullmatch(r"[a-f0-9]{32}", headers.get("x-workbench-project", ""))
                    and re.fullmatch(r"[a-f0-9]{64}", headers.get("x-workbench-package", ""))):
                external_key = scope_key(identity.host + "\n" + identity.user_id,
                    headers["x-workbench-project"], headers["x-workbench-package"])
                external_context = EXTERNAL_CURRENT.set((self.external_models, external_key, cookie_token(headers)))
            request_phase = "authenticated"
            from workbench.hosted_loading import read_id, warehouse_status
            if "xworkbenchreadid" in headers:
                read_id(headers["xworkbenchreadid"])
            # Remove bearer credentials before entering existing application code.
            clean_scope = {**scope, "workbench.local_boundary_checked": True,
                           "workbench.hosted_read_only": not mutation,
                           "headers": [(k, v) for k, v in scope["headers"]
                                       if k.lower() not in {b"x-forwarded-access-token", b"authorization"}]}
            if path.startswith(EXTERNAL_PREFIX):
                # Secret-bearing routes NEVER enter envelopes, audit, restore,
                # diagnostics or full runtime persistence. Identity above is fresh.
                from workbench.external_models import handle as external_handle
                if external_key is None:
                    return await JSONResponse({"detail": "Select an explicit project and ready package"},
                        status_code=409)(clean_scope, receive, secure_send)
                try:
                    self.external_models.rate(external_key[0])
                    try:
                        external_length = int(headers.get("content-length", "0"))
                    except ValueError:
                        raise HTTPException(400, "Invalid request size") from None
                    if not 0 <= external_length <= 8192:
                        raise HTTPException(413, "External model request exceeds size limit")
                    if scope.get("query_string"):
                        raise HTTPException(400, "External model requests do not accept query parameters")
                    # Authorize current membership from committed metadata only.
                    await run_in_threadpool(self.authorize_external_scope, identity, forwarded, external_key)
                    # A reservation includes accepted, not-yet-started operations.
                    # Existing per-user queue also covers legacy Databricks jobs.
                    with self.guard:
                        owner = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
                        if scope["method"] == "POST" and self.user_queues.get(owner, {}).get("requests", 0):
                            raise HTTPException(409, "Wait for all active model jobs before changing provider")
                    return await external_handle(self.external_models, external_key, headers,
                        {**clean_scope, "scheme": "https"}, receive, secure_send, rate_checked=True)
                except HTTPException as error:
                    return await JSONResponse({"detail": error.detail}, status_code=error.status_code)(clean_scope, receive, secure_send)
                except Exception:
                    return await JSONResponse({"detail": "External model session scope unavailable"},
                        status_code=503)(clean_scope, receive, secure_send)
            if external_key is not None and self.external_models.active(external_key, cookie_token(headers)):
                external_status = self.external_models.status(external_key, cookie_token(headers))
                if path in {"/api/models/connect", "/api/models/select"}:
                    raise HTTPException(409, "Disconnect external models before changing Databricks controls")
                if path == "/api/models/status" and scope["method"] == "GET":
                    await run_in_threadpool(self.authorize_external_scope, identity, forwarded, external_key)
                    return await JSONResponse({**external_status, "roles": ROLE_PURPOSES,
                        "connected": external_status["configured"]})(clean_scope, receive, secure_send)
                from workbench.external_models import model_action
                if model_action(scope["method"], path) and not external_status["configured"]:
                    raise HTTPException(409, "External model session expired; reconnect or disconnect explicitly")
            if path == "/":
                return await FileResponse(STATIC / "index.html")(clean_scope, receive, secure_send)
            if path.startswith("/static/"):
                static_scope = {**clean_scope, "path": path[len("/static"):], "root_path": ""}
                return await self.static(static_scope, receive, secure_send)
            if path == "/api/health":
                from workbench.app import _scope_capabilities
                from workbench.capabilities import capability_inventory
                return await JSONResponse({"status": "ok", "local_only": False, "authentication": "databricks_obo",
                                           "heartbeat_enabled": self.heartbeats.enabled,
                                           "capabilities": _scope_capabilities(True),
                                           "capability_inventory": capability_inventory(True),
                                           "generation_allowed": False, "deployment_allowed": False})(clean_scope, receive, secure_send)
            if path == "/api/models/workspace" and scope["method"] == "GET":
                # Fresh identity above is the authority, never the app origin or
                # browser input. No Delta, SQLite, provisioning or model inventory.
                return await JSONResponse({"workspace": identity.host, "mode": "obo", "profile": None,
                    "auth_check": "verified", "connected": False})(clean_scope, receive, secure_send)
            if path == "/api/databricks/volumes/status" and scope["method"] == "GET":
                # This status is derived solely from the already-verified OBO
                # identity. It neither reads project state nor contacts UC, so
                # do not hydrate a full Delta/Volume execution cache for it.
                return await JSONResponse({"mode": "obo", "profile": None, "configured": True,
                    "connected": True, "auth_check": "verified", "authentication": "databricks_obo", "host": identity.host,
                    "user_id": identity.user_id, "local_only": False,
                    "production_remote_enabled": True})(clean_scope, receive, secure_send)
            if path == "/api/hosted/logs" and scope["method"] == "GET":
                try:
                    limit = int(parse_qs(scope.get("query_string", b"").decode()).get("limit", ["100"])[0])
                except (TypeError, ValueError):
                    raise HTTPException(422, "Diagnostic log limit must be an integer") from None
                return await JSONResponse({"items": self.diagnostics.list(
                    owner_key(identity.host, identity.user_id), limit=limit),
                    "storage": "bounded per-user in-memory diagnostics; cleared when this app instance restarts",
                    "sql_text_logged": False, "credentials_logged": False})(clean_scope, receive, secure_send)
            if path.startswith("/api/hosted/loading/") and scope["method"] == "GET":
                owner = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
                result = self.loading.snapshot(owner, path[len("/api/hosted/loading/"):])
                result["warehouse"] = await run_in_threadpool(warehouse_status, identity, self.config.warehouse)
                # Refresh phase after the remote warehouse observation, which can take time.
                result.update(self.loading.snapshot(owner, result["diagnostic_id"]))
                return await JSONResponse(result)(clean_scope, receive, secure_send)
            if path == "/api/hosted/heartbeat" and scope["method"] == "POST":
                result = await run_in_threadpool(self.heartbeats.touch, identity)
                return await JSONResponse(result)(clean_scope, receive, secure_send)
            limit = 80 * 1024 * 1024 + 65536 if path == "/api/packages/upload" else (
                160 * 1024 * 1024 + 65536 if path.endswith("/packages/upload") else (
                    10 * 1024 * 1024 + 65536 if path.endswith("/documents") else 65536))
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

            registry_read = scope["method"] == "GET" and (
                path == "/api/projects" or re.fullmatch(
                    r"/api/projects/[a-f0-9]{32}(?:/jobs/[a-f0-9]{32})?", path))
            if registry_read:
                # Metadata screens read a pinned committed generation directly.
                # They must not wait behind a running mutation or hydrate XML,
                # models and checkpoints merely to discover package membership.
                from workbench.hosted_registry import registry_response
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix)
                with tempfile.TemporaryDirectory(prefix="workbench-registry-") as temporary:
                    snapshots = DeltaRuntime(current_identity, remote, temporary,
                                             warehouse=self.config.warehouse, forbidden_token=forwarded)
                    result = await run_in_threadpool(registry_response, snapshots, path)
                return await JSONResponse(result)(clean_scope, receive, secure_send)

            if ((path == "/api/models/status" and scope["method"] == "GET")
                    or (path == "/api/models/connect" and scope["method"] == "POST")):
                # Identity was freshly verified above. Discovery is metadata
                # only, with no route save, runtime restore or mutation queue.
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
                        remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix,
                                                         initialize=False)
                        snapshots = DeltaRuntime(current_identity, remote, temporary,
                                                 warehouse=self.config.warehouse, forbidden_token=forwarded)
                        result = await run_in_threadpool(model_status, snapshots, identity,
                            headers.get("x-workbench-project"), headers.get("x-workbench-package"))
                return await JSONResponse(result)(clean_scope, receive, secure_send)

            if (not scope.get("workbench.operation_execution")
                    and (path.startswith("/api/hosted/uploads") or path.startswith("/api/hosted/operations")
                     or (path == "/api/models/select" and scope["method"] == "POST"))):
                maximum = self.uploads.chunk_bytes if scope["method"] == "PUT" else 32768
                raw = bytearray()
                if length > maximum:
                    raise HTTPException(413, "Hosted intake request exceeds bounded chunk or metadata limit")
                if mutation:
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

                def execute(method, action_path, query, action_headers, body):
                    from workbench.hosted_operations import current_progress
                    try:
                        return asyncio.run_coroutine_threadsafe(self.execute_operation(
                            {**scope, "workbench.active_operation_id": execution_operation_id},
                            method, action_path, query, action_headers, body,
                            current_progress()), loop).result()
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
                execution_operation_id = None
                if action_path == "/api/hosted/operations" and scope["method"] == "POST":
                    try:
                        submitted = action_body if isinstance(action_body, dict) else json.loads(action_body)
                        execution_operation_id = submitted.get("id") if isinstance(submitted, dict) else None
                    except (ValueError, TypeError):
                        pass  # The journal's strict validator returns the actual request error.
                    from workbench.external_models import model_action
                    if (external_key is not None and isinstance(execution_operation_id, str)
                            and re.fullmatch(r"[a-f0-9]{32}", execution_operation_id)
                            and isinstance(submitted, dict)
                            and model_action(submitted.get("method"), str(submitted.get("path", "")))):
                        with self.guard:
                            if self.external_models.active(external_key, cookie_token(headers)):
                                self.external_models.credential(external_key, cookie_token(headers))
                            fresh_reservation = self.external_models.begin(external_key, execution_operation_id)
                        if fresh_reservation:
                            model_reservation = external_key, execution_operation_id
                result = await run_in_threadpool(handler.handle, identity, remote, scope["method"], action_path,
                                                scope.get("query_string", b"").decode(), headers, action_body, execute)
                if result is None:
                    raise HTTPException(404, "Unknown hosted intake operation")
                status, payload = result
                if status == 202:
                    # Worker owns the reservation until the complete action ends.
                    model_reservation = None
                if self.heartbeats.enabled and path.startswith("/api/hosted/uploads") and mutation:
                    await run_in_threadpool(self.heartbeats.touch, identity, "active_work")
                return await JSONResponse(payload, status_code=status)(clean_scope, receive, secure_send)

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
            request_phase = "processing"
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
            await progress("provisioning")
            from workbench.hosted_object_reads import ROUTE as OBJECT_READ_ROUTE, object_read_response
            from workbench.hosted_agent_reads import PATHS as HISTORY_PATHS, history_response
            history_read = (scope["method"] == "GET" and path in HISTORY_PATHS
                           and (headers.get("x-workbench-package")
                               or (path == "/api/tracing/status" and not headers.get("x-workbench-project"))))
            selected_object_read = (scope["method"] == "GET" and OBJECT_READ_ROUTE.fullmatch(path)
                                    and headers.get("x-workbench-project") and headers.get("x-workbench-package"))
            if fixture_read or artifact_read or selected_object_read or history_read or path == "/api/models/select":
                # POST carries explicit read consent/scope, not a state change.
                # Missing hosted authority is a blocker, never provision it.
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix, initialize=False)
            else:
                remote = await run_in_threadpool(provision, identity, self.config.catalog_prefix)
            if path == "/api/hosted/status":
                return await JSONResponse({"authentication": "databricks_obo", "user_id": identity.user_id,
                                           "volume_root": remote, "mutations_serialized_per_user": True,
                                           "read_only_actions_may_run_concurrently": True,
                                           "state_max_bytes": 1024 ** 3, "state_max_files": 20000,
                                           "authority": "committed_delta_metadata_and_volume_artifacts",
                                           "sqlite": "ephemeral_execution_cache_only",
                                           "warehouse_id": self.config.warehouse,
                                           "durability": "commit_last_generation_not_distributed_transaction",
                                           "generation_allowed": False, "deployment_allowed": False})(clean_scope, receive, secure_send)
            # No cached application or identity survives this action. SQLite is
            # rebuilt from committed Delta rows, never from a SQLite backup/HEAD.
            with CancelScope(shield=True), tempfile.TemporaryDirectory(prefix="workbench-" + key[:12] + "-") as temporary:
                snapshots = DeltaRuntime(current_identity, remote, temporary, warehouse=self.config.warehouse,
                                         forbidden_token=forwarded)
                if artifact_read:
                    from workbench.hosted_object_reads import workflow_artifact_response
                    result = await run_in_threadpool(workflow_artifact_response, snapshots, path,
                        headers["x-workbench-project"], headers["x-workbench-package"], artifact_body,
                        worker_progress)
                    await progress("responding")
                    return await (result if isinstance(result, Response) else JSONResponse(result))(
                        clean_scope, bounded_receive, secure_send)
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
                    result = await run_in_threadpool(select_model, snapshots, identity,
                        json.loads(raw), headers.get("x-workbench-project"),
                        headers.get("x-workbench-package"), worker_progress)
                    return await JSONResponse(result)(clean_scope, bounded_receive, secure_send)
                if history_read:
                    result = await run_in_threadpool(history_response, snapshots, identity, path,
                        headers.get("x-workbench-project"), headers.get("x-workbench-package"),
                        parse_qs(scope.get("query_string", b"").decode()), worker_progress)
                    await progress("responding")
                    await JSONResponse(result)(clean_scope, bounded_receive, secure_send)
                    if diagnostic_id is not None:
                        self.loading.finish(diagnostic_owner, diagnostic_id)
                    return
                if selected_object_read:
                    result = await run_in_threadpool(
                        object_read_response, snapshots, path, headers["x-workbench-project"],
                        headers["x-workbench-package"], parse_qs(scope.get("query_string", b"").decode()),
                        worker_progress)
                    await progress("responding")
                    await (result if isinstance(result, Response) else JSONResponse(result))(clean_scope, bounded_receive, secure_send)
                    if diagnostic_id is not None:
                        self.loading.finish(diagnostic_owner, diagnostic_id)
                    return
                # Only mutations may initialize the full metadata schema. Reads
                # authorize/query existing tables lazily, never checking all 21
                # families merely to discover an empty committed runtime scope.
                restore_options = {"initialize": mutation}
                if diagnostic_id is not None or callable(operation_progress):
                    restore_options["progress"] = worker_progress
                await run_in_threadpool(snapshots.restore, **restore_options)
                # The hash-chained local audit is needed for mutation safety and
                # for explicit Logs reads. Inventory/overview/API status reads do
                # not consume it; restoring a long audit tail for each such read
                # can dominate the request. Safe request metadata is logged to
                # stdout instead.
                if mutation or path in {"/api/logs", "/api/logs/export"}:
                    await progress("audit_restore")
                    await run_in_threadpool(snapshots.restore_audit)
                await progress("preparing_package")
                snapshots.model_project = headers.get("x-workbench-project")
                app = await run_in_threadpool(user_app, Path(temporary), snapshots, read_only=not mutation,
                    execution_models=mutation and (path.startswith("/api/agents/")
                        or path.startswith("/api/loops/") or "/design" in path or "/draft" in path))
                started = True
                try:
                    await run_in_threadpool(app.state.service.start, worker_progress if callable(operation_progress) else None)
                    await run_in_threadpool(app.state.projects.start)
                    if callable(operation_progress) and path in {"/api/overview", "/api/objects"}:
                        await progress("querying_inventory")
                    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as response:
                        response_start = None

                        async def capture(message):
                            nonlocal response_start
                            if message["type"] == "http.response.start":
                                response_start = message
                            elif message["type"] == "http.response.body":
                                response.write(message.get("body", b""))

                        finalize = re.fullmatch(r"/api/hosted/uploads/([a-f0-9]{32})/finalize", path)
                        if finalize and scope.get("workbench.operation_execution"):
                            source, name, project, digest, size = await run_in_threadpool(
                                self.uploads.materialize, identity, remote, finalize[1], temporary)
                            manager = app.state.projects
                            await run_in_threadpool(manager.reserve, project)
                            try:
                                staged = await run_in_threadpool(manager.stage, project,
                                                                [(source, name, digest, size)])
                            finally:
                                manager.slot.release()
                            await JSONResponse(staged, status_code=202)(clean_scope, bounded_receive, capture)
                        else:
                            await app(clean_scope, bounded_receive, capture)
                        # Drain package and agent workers while the initiating user
                        # context is alive. Never acknowledge unflushed mutations.
                        await run_in_threadpool(app.state.projects.close)
                        await run_in_threadpool(app.state.service.close)
                        started = False
                        if mutation:
                            await run_in_threadpool(snapshots.flush)
                            if finalize and scope.get("workbench.operation_execution"):
                                await run_in_threadpool(self.uploads.mark_committed, identity, remote,
                                                        finalize[1], staged)
                        else:
                            # Read-only requests never append to the disposable
                            # SQLite hash chain and never issue a Delta audit MERGE.
                            # Their request ID, route, status, phase and timings are
                            # recorded in app stdout by the dispatcher. This keeps
                            # concurrent observations from racing on a mutable
                            # sequence or waiting on audit publication to respond.
                            pass
                        if response_start is None:
                            raise ValueError("Missing application response")
                        await progress("responding")
                        await secure_send(response_start)
                        response.seek(0)
                        while chunk := response.read(1024 * 1024):
                            await secure_send({"type": "http.response.body", "body": chunk, "more_body": True})
                        await secure_send({"type": "http.response.body", "body": b"", "more_body": False})
                finally:
                    if started:
                        await run_in_threadpool(app.state.projects.close)
                        await run_in_threadpool(app.state.service.close)
                    app.state.service.active = None
                    app.state.service.runtime = None
                if diagnostic_id is not None:
                    self.loading.finish(diagnostic_owner, diagnostic_id,
                                        exception_class="HTTPException" if response_start["status"] >= 400 else None)
        except HTTPException as error:
            fail_loading(error)
            event = record_failure(error, error.status_code) if error.status_code >= 400 else None
            detail = error.detail
            if event:
                detail = f"{detail} (diagnostic {event['id']}; see Logs)"
            await JSONResponse({**error_body(detail), **({"failure_id": event["id"]} if event else {})},
                               status_code=error.status_code)(scope, receive, secure_send)
        except AuthenticationUnavailable as error:
            fail_loading(error)
            event = record_failure(error, 401)
            await JSONResponse({**error_body("User authorization expired or unavailable; reopen the app. No alternate identity was used."),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=401)(scope, receive, secure_send)
        except (DiscoveryUnavailable, RoutingError) as error:
            fail_loading(error)
            event = record_failure(error, 403)
            await JSONResponse({**error_body("User model discovery or routing unavailable; check consent, scopes and endpoint permissions."),
                                **({"failure_id": event["id"]} if event else {})},
                               status_code=403)(scope, receive, secure_send)
        except Exception as error:
            fail_loading(error)
            event = record_failure(error, 503)
            await JSONResponse({**error_body("Hosted action or Delta generation completion is unconfirmed. Inspect committed state and Logs before retrying. No local fallback was used."),
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
                    "status": response_status, "phase": request_phase,
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
    app.mount("/", HostedDispatcher(config, identity_resolver))
    return app


def main():
    import uvicorn
    # Exactly one process/replica. No reload, ambient credential client, or dotenv.
    uvicorn.run(create_hosted_app(), host="0.0.0.0", port=int(os.environ["DATABRICKS_APP_PORT"]),
                workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()