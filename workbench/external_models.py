"""Session-only API providers. No discovery, credential verification or persistence.

Keys belong only to this bounded process store. SDKs receive an inert placeholder;
the transport resolves the session immediately before an official API request.
The session cookie is an opaque capability, never an identity or OBO substitute.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
import json
import re
from secrets import compare_digest, token_urlsafe
from threading import RLock, Timer
from time import monotonic
from typing import Any

from fastapi import HTTPException
from pydantic import SecretStr

COOKIE = "orion_external_session"
PREFIX = "/api/models/external/"
PATHS = {PREFIX + "status": "GET", PREFIX + "connect": "POST", PREFIX + "disconnect": "POST"}
TTL = 3600
MAX_BODY = 8192
CURRENT: ContextVar[Any] = ContextVar("orion_external_models", default=None)
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]{16,512}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def selection(raw):
    """Never return parser/validation diagnostics containing submitted input."""
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError
            value[key] = item
        return value
    try:
        body = json.loads(raw, object_pairs_hook=pairs)
        if (not isinstance(body, dict) or set(body) != {"provider", "model", "api_key"}
                or body["provider"] not in ("openai", "anthropic")
                or not isinstance(body["model"], str) or not _MODEL.fullmatch(body["model"])
                or not isinstance(body["api_key"], str) or not _KEY.fullmatch(body["api_key"])
                or body["model"].lower().startswith(("sk-", "dapi"))
                or body["api_key"] in body["model"]):
            raise ValueError
        return body
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise HTTPException(422, "Invalid external model configuration") from None


def cookie_token(headers):
    try:
        cookie = SimpleCookie()
        cookie.load(headers.get("cookie", ""))
        value = cookie[COOKIE].value if COOKIE in cookie else ""
        return value if _TOKEN.fullmatch(value) else ""
    except Exception:
        return ""


def scope_key(owner, project, package):
    if (not isinstance(project, str) or not re.fullmatch(r"[a-f0-9]{32}", project)
            or not isinstance(package, str) or not re.fullmatch(r"[a-f0-9]{64}", package)):
        raise HTTPException(409, "Select an explicit project and ready package")
    return owner, project, package


def model_action(method, path):
    return method == "POST" and (path.startswith(("/api/agents/", "/api/loops/"))
                                 or "/design" in path or "/draft" in path)


def current_for_root(root):
    binding = CURRENT.get()
    if binding is None:
        return None
    store, key, token = binding
    if tuple(root.parts[-4:]) != ("projects", key[1], "package-contexts", key[2]):
        return None
    return binding if store.active(key, token) else None


@dataclass(repr=False)
class _Session:
    token: str
    provider: str | None = None
    model: str | None = None
    secret: bytearray = field(default_factory=bytearray, repr=False)
    expires: float = 0
    external: bool = False
    runtime: object = field(default=None, repr=False)
    timer: Any = field(default=None, repr=False)

    def forget(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.secret[:] = b"\x00" * len(self.secret)
        self.secret.clear()
        self.runtime = None


class Sessions:
    """Sticky expired tombstones prevent accidental Databricks fallback.

    Entries are not evicted while the process lives: at capacity new sessions
    fail closed rather than losing an expired external selection. Restart clears
    this session-only feature. Disconnect explicitly restores Databricks mode.
    """
    def __init__(self, *, clock=monotonic, ttl=TTL, capacity=1024):
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.lock = RLock()
        self.entries, self.activity, self.rates = {}, {}, {}

    def _expire(self):
        for entry in self.entries.values():
            if entry.external and entry.expires <= self.clock():
                entry.forget()

    def _entry(self, key, token, *, required=False):
        self._expire()
        entry = self.entries.get(key)
        if entry is not None and entry.external and not compare_digest(entry.token, token):
            raise HTTPException(401, "External model session is unavailable; use its original browser session")
        if required and (entry is None or not entry.external or not entry.secret):
            raise HTTPException(409, "External model session expired or is not configured; reconnect or disconnect explicitly")
        return entry

    def active(self, key, token=""):
        with self.lock:
            self._expire()
            entry = self.entries.get(key)
            unknown_cookie = bool(token and not any(compare_digest(item.token, token)
                                                   for item in self.entries.values()))
            return bool((entry and entry.external) or unknown_cookie)

    def rate(self, owner):
        with self.lock:
            now = self.clock()
            self.rates = {k: v for k, v in self.rates.items() if now - v[0] < 60}
            start, count = self.rates.get(owner, (now, 0))
            if count >= 30 or (owner not in self.rates and len(self.rates) >= self.capacity):
                raise HTTPException(429, "External model request limit reached")
            self.rates[owner] = start, count + 1

    def begin(self, key, action):
        with self.lock:
            if key not in self.activity and len(self.activity) >= self.capacity:
                raise HTTPException(429, "Model activity capacity reached")
            actions = self.activity.setdefault(key, set())
            fresh = action not in actions
            actions.add(action)
            return fresh

    def end(self, key, action):
        with self.lock:
            actions = self.activity.get(key, set())
            actions.discard(action)
            if not actions:
                self.activity.pop(key, None)

    def status(self, key, token):
        with self.lock:
            entry = self._entry(key, token)
            external = self.active(key, token)
            configured = bool(entry is not None and external and entry.secret)
            return {"mode": "external" if external else "databricks", "storage": "session_memory",
                    "status": "configured" if configured else "expired" if external else "default",
                    "configured": configured, "verified": False, "runtime_verified": False,
                    "provider": entry.provider if entry is not None and external else None,
                    "model": entry.model if entry is not None and external else None,
                    "expires_in_seconds": max(0, int(entry.expires - self.clock())) if entry is not None and external else None,
                    "databricks_controls_disabled": external, "inference_ready": configured,
                    "notice": "Configuration only, no inference or credential verification performed."}

    def connect(self, key, token, body):
        with self.lock:
            entry = self._entry(key, token)
            if self.activity.get(key):
                raise HTTPException(409, "Wait for all active model jobs before changing provider")
            if entry is None and len(self.entries) >= self.capacity:
                raise HTTPException(429, "External model session capacity reached")
            if entry:
                entry.forget()
            # Only reuse a cookie already minted by this process, never an
            # attacker-supplied initial session identifier.
            known = token and any(compare_digest(item.token, token) for item in self.entries.values())
            entry = _Session(token if known else token_urlsafe(32), body["provider"], body["model"],
                             bytearray(body["api_key"], "ascii"), self.clock() + self.ttl, True)
            self.entries[key] = entry
            def expire():
                with self.lock:
                    if self.entries.get(key) is entry and entry.expires <= self.clock():
                        entry.forget()
            entry.timer = Timer(self.ttl, expire)
            entry.timer.daemon = True
            entry.timer.start()
            return self.status(key, entry.token), entry.token

    def disconnect(self, key, token):
        with self.lock:
            entry = self._entry(key, token)
            if self.activity.get(key):
                raise HTTPException(409, "Wait for all active model jobs before changing provider")
            if entry:
                entry.forget()
                entry.external, entry.provider, entry.model = False, None, None
            elif token:
                if len(self.entries) >= self.capacity:
                    raise HTTPException(429, "External model session capacity reached")
                # Explicit acknowledgement of a lost process session restores
                # DB mode. Cookie presence alone must never silently do so.
                self.entries[key] = _Session(token)
            return self.status(key, token)

    def credential(self, key, token):
        with self.lock:
            entry = self._entry(key, token, required=True)
            assert entry is not None
            return entry.provider, bytes(entry.secret)

    def runtime(self, key, token, root, *, uc_identity_provider=None, knowledge_loader=None):
        with self.lock:
            entry = self._entry(key, token, required=True)
            assert entry is not None
            # Hosted roots are disposable. Never retain their clients or fresh
            # OBO metadata identity beyond the initiating action.
            return build_runtime(self, key, token, root, entry.provider, entry.model,
                                 uc_identity_provider=uc_identity_provider,
                                 knowledge_loader=knowledge_loader)


LOCAL = Sessions()


def build_runtime(store, key, token, root, provider, model, **options):
    """Reuse AgentRuntime, its six static roles, callbacks and independent writer."""
    from workbench.agents import AgentRuntime
    from workbench.databricks_models import ROLE_PURPOSES
    models = {}
    try:
        import httpx2 as httpx
        from workbench.external_model_transport import Transport, AsyncTransport
        for role in ROLE_PURPOSES:
            sync = httpx.Client(transport=Transport(store, key, token), trust_env=False,
                                follow_redirects=False, timeout=60)
            asynchronous = httpx.AsyncClient(transport=AsyncTransport(store, key, token), trust_env=False,
                                            follow_redirects=False, timeout=60)
            if provider == "openai":
                import openai
                from langchain_openai import ChatOpenAI
                sdk = openai.OpenAI(api_key="session-managed-placeholder", organization="", project="",
                    base_url="https://api.openai.com/v1", max_retries=0, timeout=60, http_client=sync)
                async_sdk = openai.AsyncOpenAI(api_key="session-managed-placeholder", organization="", project="",
                    base_url="https://api.openai.com/v1", max_retries=0, timeout=60, http_client=asynchronous)
                chat = ChatOpenAI(model=model, api_key=SecretStr("session-managed-placeholder"),
                                  base_url="https://api.openai.com/v1", organization="",
                                  openai_proxy="", max_retries=0, timeout=60,
                                  http_client=sync, http_async_client=asynchronous,
                                  client=sdk.chat.completions, root_client=sdk,
                                  async_client=async_sdk.chat.completions, root_async_client=async_sdk,
                                  use_responses_api=False, disable_streaming=True)
            else:
                import anthropic
                from langchain_anthropic import ChatAnthropic
                configuration: dict[str, Any] = dict(model=model, api_key="session-managed-placeholder",
                    base_url="https://api.anthropic.com", anthropic_proxy="", max_retries=0,
                    timeout=60, disable_streaming=True)
                chat = ChatAnthropic(**configuration)
                # Avoid the integration's process-wide cached HTTP clients.
                object.__getattribute__(chat, "__dict__")["_client"] = anthropic.Client(api_key="session-managed-placeholder",
                    auth_token="", base_url="https://api.anthropic.com", max_retries=0,
                    timeout=60, http_client=sync)
                object.__getattribute__(chat, "__dict__")["_async_client"] = anthropic.AsyncClient(api_key="session-managed-placeholder",
                    auth_token="", base_url="https://api.anthropic.com", max_retries=0,
                    timeout=60, http_client=asynchronous)
            models[role] = chat
        return AgentRuntime(root / "agents", role_models=models,
                            identity_scope="external:" + provider + ":" + model + ":" + ":".join(key), **options)
    except Exception:
        raise HTTPException(503, "External provider SDK configuration unavailable") from None


class Runtime:
    """Nonsecret service seam; resolved clients live only in session memory."""
    def __init__(self, store, key, token, root, *, uc_identity_provider=None, cache=True):
        self.store, self.key, self.token, self.root = store, key, token, root
        self.uc_identity_provider = uc_identity_provider
        self.knowledge_loader = None
        self.cache, self._runtime = cache, None

    def _resolve(self):
        with self.store.lock:
            entry = self.store._entry(self.key, self.token, required=True)
            assert entry is not None
            if not self.cache:
                if self._runtime is None:
                    self._runtime = self.store.runtime(self.key, self.token, self.root,
                        uc_identity_provider=self.uc_identity_provider, knowledge_loader=self.knowledge_loader)
                return self._runtime
            if entry.runtime is None:
                entry.runtime = self.store.runtime(self.key, self.token, self.root,
                    uc_identity_provider=self.uc_identity_provider, knowledge_loader=self.knowledge_loader)
            return entry.runtime

    def status(self):
        status = self.store.status(self.key, self.token)
        from workbench.databricks_models import ROLE_PURPOSES
        return {**status, "available": status["configured"], "roles": list(ROLE_PURPOSES),
                "authentication": "model_provided", "model_routes": {
                    role: status["model"] for role in ROLE_PURPOSES} if status["configured"] else {}}

    def run(self, *args, **kwargs):
        return self._resolve().run(*args, **kwargs)

    def close(self):
        runtime, self._runtime = self._runtime, None
        close = getattr(runtime, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


async def handle(store, key, headers, scope, receive, send, *, rate_checked=False):
    """Already authorized boundary, no FastAPI input-error/body serialization."""
    from starlette.responses import JSONResponse
    raw, body = bytearray(), None
    try:
        if scope.get("query_string") or scope["method"] != PATHS.get(scope["path"]):
            raise HTTPException(400, "Invalid external model request")
        if not rate_checked:
            store.rate(key[0])
        token = cookie_token(headers)
        if scope["method"] == "GET":
            result = store.status(key, token)
            issued = None
        else:
            if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise HTTPException(415, "External model requests require application/json")
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                raise HTTPException(400, "Invalid request size") from None
            if not 0 <= length <= MAX_BODY:
                raise HTTPException(413, "External model request exceeds size limit")
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    raise HTTPException(400, "External model request disconnected")
                raw.extend(message.get("body", b""))
                if len(raw) > MAX_BODY:
                    raise HTTPException(413, "External model request exceeds size limit")
                if not message.get("more_body", False):
                    break
            if scope["path"] == PREFIX + "connect":
                body = selection(raw)
                result, issued = store.connect(key, token, body)
                body.clear()
            else:
                if bytes(raw).strip() != b"{}":
                    raise HTTPException(422, "Disconnect requires an empty JSON object")
                result, issued = store.disconnect(key, token), None
        response = JSONResponse(result, headers={"cache-control": "no-store"})
        if issued:
            response.set_cookie(COOKIE, issued, httponly=True, secure=scope["scheme"] == "https",
                                samesite="strict", path="/")
        return await response(scope, receive, send)
    except HTTPException as error:
        return await JSONResponse({"detail": error.detail}, status_code=error.status_code,
                                  headers={"cache-control": "no-store"})(scope, receive, send)
    except Exception:
        return await JSONResponse({"detail": "External model configuration unavailable"}, status_code=503,
                                  headers={"cache-control": "no-store"})(scope, receive, send)
    finally:
        raw[:] = b"\x00" * len(raw)
        raw.clear()
        if body is not None:
            body.clear()


class LocalAccess:
    """Runs behind (or explicitly through) the existing loopback boundary.

    Local mode is intentionally single-user, scoped to the registry-validated
    package root. The cookie prevents another browser session taking over that
    package's external credential slot. Hosted mode never installs this class.
    """
    def __init__(self, app, *, service):
        from workbench.app import _LocalBoundary
        self.app, self.service = app, service
        self.boundary = _LocalBoundary(self.dispatch)
        self.databricks_runtime = None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(PREFIX):
            return await self.boundary(scope, receive, send)
        return await self.dispatch(scope, receive, send)

    async def dispatch(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        project, package = headers.get("x-workbench-project"), headers.get("x-workbench-package")
        if scope["path"] == "/api/models/workspace":
            return await self.app(scope, receive, send)
        if not (project and package):
            if scope["path"].startswith(PREFIX):
                return await JSONResponse({"detail": "Select an explicit project and ready package"},
                                          status_code=409)(scope, receive, send)
            return await self.app(scope, receive, send)
        if not (re.fullmatch(r"[a-f0-9]{32}", project) and re.fullmatch(r"[a-f0-9]{64}", package)):
            return await self.app(scope, receive, send)
        root = self.service.root.resolve()
        owner = root.parents[3] if tuple(root.parts[-4:]) == ("projects", project, "package-contexts", package) else root
        key = scope_key(str(owner), project, package)
        token = cookie_token(headers)
        action = None
        try:
            if scope["path"].startswith(PREFIX):
                if scope["method"] == "POST":
                    if not self.service.agent_slot.acquire(blocking=False):
                        raise HTTPException(409, "Wait for all active model jobs before changing provider")
                    try:
                        if not isinstance(self.service.runtime, Runtime):
                            self.databricks_runtime = self.service.runtime
                        await handle(LOCAL, key, headers, scope, receive, send)
                        if not LOCAL.active(key, token) and self.databricks_runtime is not None:
                            self.service.runtime = self.databricks_runtime
                    finally:
                        self.service.agent_slot.release()
                    return
                return await handle(LOCAL, key, headers, scope, receive, send)
            with LOCAL.lock:
                if LOCAL.active(key, token):
                    LOCAL.status(key, token)
                    if scope["path"] in {"/api/models/connect", "/api/models/select"}:
                        raise HTTPException(409, "Disconnect external models before changing Databricks controls")
                    if self.databricks_runtime is None:
                        self.databricks_runtime = self.service.runtime
                    self.service.runtime = Runtime(LOCAL, key, token, self.service.root,
                        uc_identity_provider=getattr(self.service.runtime, "uc_identity_provider", None))
                if model_action(scope["method"], scope["path"]):
                    from uuid import uuid4
                    action = uuid4().hex
                    LOCAL.begin(key, action)
            return await self.app(scope, receive, send)
        except HTTPException as error:
            return await JSONResponse({"detail": error.detail}, status_code=error.status_code)(scope, receive, send)
        finally:
            if action is not None:
                LOCAL.end(key, action)