"""Opt-in workspace serving routes. No application-wide authentication integration.

Factories explicitly resolve current_user.me(); constructing WorkspaceModels or
reading status does not authenticate. Discovery lists metadata, never invokes a
model. Declarations are NOT live tool probes. The caller must obtain consent for
discovery/inference and establish hosted proxy trust outside this local module.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import configparser
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from threading import Event, RLock
from typing import Any, Literal
from urllib.parse import urlsplit
import builtins

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from databricks.sdk.credentials_provider import CredentialsStrategy, databricks_cli
from langchain_openai import ChatOpenAI


class _DatabricksChatOpenAI(ChatOpenAI):
    """Normalize assistant/tool wire messages, never checkpoint/source messages.

    Databricks serving rejects top-level message ``name`` on assistant and tool
    messages. The assistant message name is framework bookkeeping, not content;
    actual function names remain in assistant ``tool_calls`` and tool replies
    retain ``tool_call_id``. Copy only changed dictionaries so a shared parent
    payload and the checkpoint/source messages remain unchanged.
    """

    def _get_request_payload(self, input_, *, stop=None, **kwargs) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" not in payload:
            return payload
        return {**payload, "messages": [
            {key: value for key, value in message.items() if key != "name"}
            if message.get("role") in {"assistant", "tool"} and "name" in message else message
            for message in payload["messages"]
        ]}


ROLE_PURPOSES = {
    "supervisor": "Coordinate advisory specialists and synthesize evidence-backed findings.",
    "discovery": "Discover source estate structure, dependencies and missing information.",
    "semantics": "Analyze mapping semantics and identify transformation parity risks.",
    "architecture": "Propose target architecture with explicit constraints and alternatives.",
    "validation": "Review evidence, verification gaps and proposed validation criteria.",
    "artifact": "Describe proposed artifacts without granting generation or deployment authority.",
}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}\Z")
_SECRET = re.compile(
    r"(?i)bearer\s+|\bdapi[a-z0-9]+|\beyJ[a-zA-Z0-9_-]+\.|"
    r"(?:token|secret|password|api[_ -]?key|authorization)\s*[:=]"
)
_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = RLock()


class AuthenticationUnavailable(RuntimeError):
    """Sanitized authentication failure. Never includes upstream exception text."""


class RoutingError(ValueError):
    """Invalid metadata, route or persistence document."""


class DiscoveryUnavailable(RuntimeError):
    """Sanitized inventory failure, including permission and authentication errors."""


def safe_workspace_path(value, *, workspace_root=None):
    """Return only a normalized in-workspace, noncredential path, otherwise None.

    Never return exception text, URLs, external basenames or raw path arguments.
    Resolution is read-only and fails closed, including inaccessible/symlink paths.
    """
    if not isinstance(value, (str, Path)):
        return None
    text = str(value)
    if (not text or len(text) > 4096 or _SECRET.search(text)
            or any(ord(char) < 32 for char in text) or '://' in text
            or re.search(r'(?i)(?:^|[/\\])[^/\\]*(?:secret|password|credential|token|api[_-]?key)[^/\\]*(?:[/\\]|$)', text)
            or re.search(r'(?i)(?:^|[/\\])(?:\.env[^/\\]*|\.databrickscfg|\.ssh|\.aws)(?:[/\\]|$)', text)):
        return None
    try:
        root = Path(workspace_root or Path(__file__).resolve().parents[1]).resolve()
        path = Path(text)
        path = (path if path.is_absolute() else root / path).resolve()
        relative = path.relative_to(root).as_posix()
        # Check the resolved spelling too, not just a possible symlink alias.
        if relative == '.' or _SECRET.search(relative) or re.search(
                r'(?i)secret|password|credential|token|api[_-]?key|(?:^|/)\.env|\.databrickscfg|(?:^|/)\.(?:ssh|aws)(?:/|$)', relative):
            return None
        return relative
    except (OSError, RuntimeError, ValueError):
        return None


def safe_failure_diagnostic(error, operation, *, workspace_root=None):
    """Fixed vocabulary, OS fields and frame locations only. No messages/locals.

    A serialized repr cannot recover an OSError filename. Inspect live fields and
    chained exceptions before they are lost, without guessing an operation/path.
    """
    operations = {'writer_model_progress_started', 'writer_model_handler',
                  'writer_model_progress_completed', 'writer_model_progress_failed',
                  'writer_tool', 'writer_graph', 'writer_invocation',
                  'writer_preflight_temp_directory', 'writer_preflight_write',
                  'writer_preflight_read', 'writer_preflight_rename',
                  'writer_preflight_sqlite', 'writer_preflight_cleanup'}
    result = {'schema_version': 1, 'operation': operation if operation in operations else 'unknown',
              'exceptions': []}
    seen = set()
    current = error
    for _ in range(4):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        cls = next((base.__name__ for base in type(current).__mro__
                    if getattr(builtins, base.__name__, None) is base
                    and issubclass(base, BaseException)), 'Exception')
        item = {'exception_class': cls, 'errno': None, 'winerror': None,
                'filename': None, 'filename2': None, 'stack': []}
        if isinstance(current, OSError):
            for key in ('errno', 'winerror'):
                value = getattr(current, key, None)
                if type(value) is int:
                    item[key] = value
            for key in ('filename', 'filename2'):
                item[key] = safe_workspace_path(getattr(current, key, None), workspace_root=workspace_root)
        frame = current.__traceback__
        frames = []
        while frame is not None:
            code = frame.tb_frame.f_code
            path = safe_workspace_path(code.co_filename, workspace_root=workspace_root)
            function = code.co_name
            if path and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,100}|<module>', function):
                frames.append({'file': path, 'function': function, 'line': frame.tb_lineno})
            frame = frame.tb_next
        item['stack'] = frames[-24:]
        result['exceptions'].append(item)
        # Even a sanitized `raise ... from None` retains its original context.
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return result


def retry_databricks_429(operation, model, cancelled: Event, on_progress=None):
    """Retry Databricks serving HTTP 429/503 until success or manual cancellation.

    429 throttling and 503 service unavailability are transient serving responses.
    The SDK's broad retries remain disabled. Never publish exception text, response
    bodies or headers. Retry-After is honored without an overall elapsed cutoff.
    """
    # LangChain wraps tool-enabled chat models in a Runnable binding. That
    # wrapper forwards openai_api_base but is not itself a ChatOpenAI instance.
    # Gate on the exact Databricks serving endpoint so transient retries still
    # work after bind_tools without broadening them to other providers.
    base_url = str(getattr(model, "openai_api_base", "") or "")
    enabled = urlsplit(base_url).path.rstrip("/") == "/serving-endpoints"
    retry_count = 0
    last_status_code = 429

    def progress(state, seconds=0.0, status_code=429):
        if on_progress is not None:
            try:
                on_progress({"event": state, "status_code": status_code,
                             "retry_count": retry_count, "retry_wait_seconds": seconds})
            except Exception:
                # A best-effort progress bridge must not change provider retry.
                pass

    while True:
        if cancelled.is_set():
            from workbench.agents import AgentRunCancelled
            raise AgentRunCancelled("Model retry cancelled by user")
        try:
            result = operation()
        except Exception as exc:
            status_code = getattr(exc, 'status_code', None)
            if not enabled or status_code not in {429, 503}:
                raise
            last_status_code = status_code
            retry_count += 1
            headers = getattr(getattr(exc, "response", None), "headers", {})
            delay = None
            value = headers.get("retry-after")
            if value is not None:
                try:
                    delay = float(value)
                except (TypeError, ValueError):
                    try:
                        target = parsedate_to_datetime(value)
                        if target.tzinfo is None:
                            target = target.replace(tzinfo=timezone.utc)
                        delay = max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
            if delay is None:
                try:
                    delay = float(headers.get("retry-after-ms")) / 1000.0
                except (TypeError, ValueError):
                    pass
            if delay is None or not math.isfinite(delay) or delay < 0:
                delay = float(min(60, 2 ** min(retry_count, 6)))
            progress("model_retry_wait", delay, status_code)
            if cancelled.wait(delay):
                from workbench.agents import AgentRunCancelled
                raise AgentRunCancelled("Model retry cancelled by user")
            progress("model_retry_resuming", status_code=status_code)
        else:
            if retry_count:
                progress("model_retry_succeeded", status_code=last_status_code)
            return result


def _host(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise RoutingError("A trusted HTTPS workspace origin is required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise RoutingError("A trusted HTTPS workspace origin is required") from None
    if (parsed.scheme != "https" or not hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or port not in (None, 443)
            or not re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", hostname)
            or any(c.isspace() for c in value) or "\\" in value):
        raise RoutingError("A trusted HTTPS workspace origin is required")
    return "https://" + hostname.lower()


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise RoutingError("Use a single serving endpoint name, not a URL, path or Unity Gateway name")
    return value


def _auth_message(mode: str) -> str:
    return "Reauthenticate labs" if mode == "profile" else "Forwarded user authentication unavailable"


@dataclass(frozen=True)
class WorkspaceIdentity:
    mode: Literal["profile", "obo"]
    host: str
    user_id: str
    client: Any = field(repr=False, compare=False)

    def __post_init__(self):
        if self.mode not in ("profile", "obo") or self.host != _host(self.host):
            raise RoutingError("Invalid workspace identity")
        if not isinstance(self.user_id, str) or not self.user_id or len(self.user_id) > 256:
            raise RoutingError("A resolved user identity is required")
        if _SECRET.search(self.user_id) or any(ord(c) < 32 for c in self.user_id):
            raise RoutingError("Invalid user identity")


class _ExplicitConfig(Config):
    """SDK 0.157 adapter: no environment, default profile or eager metadata HTTP.

    attributes() delegates explicitly because SDK Config introspects class-local
    descriptors. WorkspaceClient.copy() preserves this subclass and strategy.
    """

    @classmethod
    def attributes(cls):
        return Config.attributes()

    def _load_from_env(self):
        pass

    def _known_file_config_loader(self):
        pass

    def _resolve_host_metadata(self):
        pass


class _ProfileCredentials(CredentialsStrategy):
    def __init__(self):
        self._lock = RLock()
        self._provider = None

    def auth_type(self):
        return "databricks-cli"

    def __call__(self, cfg):
        def headers():
            try:
                with self._lock:
                    # SDK CLI version detection can fall back to --host, which
                    # is ambiguous with multiple cached profiles. Never do so.
                    executable = shutil.which("databricks")
                    if cfg.profile != "labs" or not executable:
                        raise AuthenticationUnavailable("Reauthenticate labs")
                    result = subprocess.run(
                        [executable, "auth", "token", "--profile", "labs", "--output", "json"],
                        capture_output=True, encoding="utf-8", timeout=30, check=True,
                        env={k: v for k, v in os.environ.items()
                             if not k.upper().startswith("DATABRICKS_")
                             or k.upper() == "DATABRICKS_CONFIG_FILE"},
                        cwd=tempfile.gettempdir())
                    payload = json.loads(result.stdout)
                    token = payload["access_token"]
                    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                        raise AuthenticationUnavailable("Reauthenticate labs")
                    return {"Authorization": "Bearer " + token}
            except Exception:
                raise AuthenticationUnavailable("Reauthenticate labs") from None
        return headers


class _ForwardedCredentials(CredentialsStrategy):
    def __init__(self, token: str):
        self._token = token

    def auth_type(self):
        # PAT here describes bearer-header transport, not the token's provenance.
        return "pat"

    def __call__(self, cfg):
        return lambda: {"Authorization": "Bearer " + self._token}


def _resolved(mode, host, client):
    try:
        if _host(client.config.host) != host:
            raise ValueError("Workspace mismatch")
        user = client.current_user.me()
        user_id = user.get("id") if isinstance(user, dict) else user.id
        return WorkspaceIdentity(mode, host, user_id, client)
    except Exception:
        raise AuthenticationUnavailable(_auth_message(mode)) from None


def _profile_host(profile="labs", *, path=None) -> str:
    """Read only the explicit labs host, without constructing credentials/clients."""
    if profile != "labs":
        raise RoutingError("Only the explicitly approved labs profile is supported")
    try:
        path = path or Path(os.environ.get("DATABRICKS_CONFIG_FILE", "~/.databrickscfg")).expanduser()
        parser = configparser.ConfigParser(interpolation=None)
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
        # Never inherit a host from DEFAULT or use an ambient SDK host.
        return _host(parser._sections[profile]["host"])
    except Exception:
        raise AuthenticationUnavailable("Configured labs workspace is unavailable") from None


def profile_identity(profile="labs", client_factory=None) -> WorkspaceIdentity:
    """Resolve only the named labs CLI user; never use ambient host/auth/profile.

    DATABRICKS_CONFIG_FILE selects the file, not the profile or authentication.
    Construction of Config/WorkspaceClient is offline. me() is the first call.
    An injected factory receives the same explicit config as the real SDK.
    """
    if profile != "labs":
        raise RoutingError("Only the explicitly approved labs profile is supported")
    try:
        path = Path(os.environ.get("DATABRICKS_CONFIG_FILE", "~/.databrickscfg")).expanduser()
        host = _profile_host(profile, path=path)
        cfg = _ExplicitConfig(host=host, profile=profile, config_file=str(path),
                              auth_type="databricks-cli", credentials_strategy=_ProfileCredentials())
        client = (client_factory or WorkspaceClient)(profile="labs", config=cfg)
    except Exception:
        raise AuthenticationUnavailable("Reauthenticate labs") from None
    return _resolved("profile", host, client)


def obo_identity(host, token, trusted_proxy=False, deployment_host=None,
                 client_factory=None) -> WorkspaceIdentity:
    """Resolve an ephemeral forwarded user on a server-configured workspace only.

    trusted_proxy and deployment_host MUST come from server configuration, never
    request fields. No attempt is made to enable hosted auth in the local app.
    """
    if trusted_proxy is not True or not deployment_host:
        raise AuthenticationUnavailable("Hosted trusted proxy configuration is required")
    normalized = _host(host)
    if normalized != _host(deployment_host):
        raise AuthenticationUnavailable("Forwarded workspace does not match deployment")
    if (not isinstance(token, str) or not token or len(token) > 32768
            or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token)):
        raise AuthenticationUnavailable("Forwarded user token is required")
    try:
        cfg = _ExplicitConfig(host=normalized, token=token, auth_type="pat",
                              credentials_strategy=_ForwardedCredentials(token))
        client = (client_factory or WorkspaceClient)(host=normalized, token=token,
                                                    auth_type="pat", config=cfg)
    except Exception:
        raise AuthenticationUnavailable("Forwarded user authentication unavailable") from None
    return _resolved("obo", normalized, client)


def _namespace(identity):
    return sha256(json.dumps([identity.host, identity.user_id], separators=(",", ":")).encode()).hexdigest()


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _text(value):
    value = getattr(value, "value", value)
    return value if isinstance(value, str) else ""


def _metadata(endpoint):
    name = _endpoint(_get(endpoint, "name"))
    task = _text(_get(endpoint, "task"))
    if not task:
        task = _text(_get(_get(endpoint, "metadata", {}), "task"))
    # Never infer capability from endpoint/model names, tags or descriptions.
    tasks = {"llm/v1/chat": "chat", "llm/v1/embeddings": "embedding",
             "llm/v1/completions": "completions", "custom": "custom"}
    kind = tasks.get(task, "unknown")
    task = task if task in tasks else "unknown"
    ready = _text(_get(_get(endpoint, "state", {}), "ready"))
    return {"name": name, "state": ready if ready in ("READY", "NOT_READY") else "UNKNOWN",
            "task": task, "type": kind, "tool_capability": "unknown"}


def _evidence(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 1000
            or _SECRET.search(value) or any(ord(c) < 32 for c in value)):
        raise RoutingError("A short nonsecret capability evidence declaration is required")
    return value.strip()


def _routes(role_routes, endpoints):
    if not isinstance(role_routes, dict) or set(role_routes) != set(ROLE_PURPOSES):
        raise RoutingError("Explicit routes for all six roles are required")
    result = {}
    allowed = {"endpoint", "tool_support_evidence", "chat_compatible_evidence", "tool_calling_verified"}
    for role, purpose in ROLE_PURPOSES.items():
        route = role_routes[role]
        if not isinstance(route, dict) or set(route) - allowed:
            raise RoutingError("Unknown route fields are not permitted")
        name = _endpoint(route.get("endpoint"))
        endpoint = endpoints.get(name)
        if endpoint is None:
            raise RoutingError("Selected endpoint is absent from this user's workspace inventory")
        if endpoint["state"] != "READY":
            raise RoutingError("Selected endpoint must explicitly report READY")
        if endpoint["type"] in ("embedding", "completions"):
            raise RoutingError("Selected endpoint is not a chat endpoint")
        chat_evidence = route.get("chat_compatible_evidence")
        if endpoint["type"] != "chat":
            chat_evidence = _evidence(chat_evidence)
        elif chat_evidence is not None:
            chat_evidence = _evidence(chat_evidence)
        # No probe/verification authority exists in this module. A declaration
        # can enable an explicitly selected route but never assert live success.
        if route.get("tool_calling_verified", False) is not False:
            raise RoutingError("Runtime tool verification requires a separately approved live probe")
        result[role] = {"endpoint": name, "purpose": purpose,
                        "tool_support_evidence": _evidence(route.get("tool_support_evidence")),
                        "tool_calling_verified": False,
                        "tool_support_status": "declared_not_runtime_verified"}
        if chat_evidence is not None:
            result[role]["chat_compatible_evidence"] = chat_evidence
    return result


def _inputs(routes):
    """Validate persisted/output fields too, not only caller input."""
    if not isinstance(routes, dict) or set(routes) != set(ROLE_PURPOSES):
        raise RoutingError("Explicit routes for all six roles are required")
    result = {}
    for role, route in routes.items():
        allowed = {"endpoint", "purpose", "tool_support_evidence", "chat_compatible_evidence",
                   "tool_calling_verified", "tool_support_status"}
        if (not isinstance(route, dict) or set(route) - allowed
                or route.get("purpose") != ROLE_PURPOSES[role]
                or route.get("tool_support_status") != "declared_not_runtime_verified"
                or route.get("tool_calling_verified") is not False):
            raise RoutingError("Invalid stored route")
        result[role] = {k: v for k, v in route.items()
                        if k in ("endpoint", "tool_support_evidence", "chat_compatible_evidence",
                                 "tool_calling_verified")}
        _endpoint(result[role].get("endpoint"))
        _evidence(result[role].get("tool_support_evidence"))
        if "chat_compatible_evidence" in result[role]:
            _evidence(result[role]["chat_compatible_evidence"])
    return result


@contextmanager
def _file_lock(path):
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), RLock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+b") as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class WorkspaceModels:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "model-routing.json"
        self._inventories = {}
        self._lock = RLock()

    def discover(self, identity: WorkspaceIdentity) -> dict:
        key = _namespace(identity)
        with self._lock:
            self._inventories.pop(key, None)
        try:
            if _host(identity.client.config.host) != identity.host:
                raise ValueError("Workspace mismatch")
            endpoints = [_metadata(e) for e in identity.client.serving_endpoints.list()]
            if len({e["name"] for e in endpoints}) != len(endpoints):
                raise ValueError("Ambiguous inventory")
        except Exception:
            raise DiscoveryUnavailable("Endpoint discovery unavailable; " + _auth_message(identity.mode)) from None
        inventory = {"workspace": identity.host, "user_id": identity.user_id,
                     "endpoints": sorted(endpoints, key=lambda e: e["name"])}
        with self._lock:
            self._inventories[key] = (identity.client, deepcopy(inventory))
        return inventory

    def _document(self):
        if not self.path.exists():
            return {"version": 1, "identities": {}}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(document, dict) or set(document) != {"version", "identities"}
                    or document["version"] != 1 or not isinstance(document["identities"], dict)):
                raise ValueError("Invalid document")
            # Validate every namespace before rewriting the shared file.
            for key, entry in document["identities"].items():
                if (not re.fullmatch(r"[a-f0-9]{64}", key) or not isinstance(entry, dict)
                        or set(entry) != {"workspace", "user_id", "routes"}):
                    raise ValueError("Invalid entry")
                host = _host(entry["workspace"])
                ident = WorkspaceIdentity("profile", host, entry["user_id"], None)
                if key != _namespace(ident):
                    raise ValueError("Invalid namespace")
                _inputs(entry["routes"])
            return document
        except Exception:
            raise RoutingError("Invalid model routing document") from None

    def configure(self, identity: WorkspaceIdentity, role_routes: dict, inventory: dict) -> dict:
        key = _namespace(identity)
        with self._lock:
            snapshot = self._inventories.get(key)
            if snapshot is None or snapshot[0] is not identity.client or snapshot[1] != inventory:
                raise RoutingError("Discover an unmodified inventory with this user identity first")
            routes = _routes(role_routes, {e["name"]: e for e in snapshot[1]["endpoints"]})
        with _file_lock(self.path):
            document = self._document()
            document["identities"][key] = {"workspace": identity.host,
                                           "user_id": identity.user_id, "routes": routes}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".model-routing-", delete=False) as stream:
                    temporary = stream.name
                    json.dump(document, stream, sort_keys=True, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
        return deepcopy(routes)

    def load(self, identity: WorkspaceIdentity) -> dict:
        # Atomic replace means readers see a whole old or new document.
        entry = self._document()["identities"].get(_namespace(identity))
        return deepcopy(entry["routes"]) if entry else {}

    def validated_routes(self, identity: WorkspaceIdentity) -> dict:
        """Revalidate saved routing metadata without constructing model clients."""
        routes = self.load(identity)
        if not routes:
            raise RoutingError("Configure all six role routes before building models")
        inventory = self.discover(identity)
        return _routes(_inputs(routes), {e["name"]: e for e in inventory["endpoints"]})

    def chat_models(self, identity: WorkspaceIdentity, *,
                    request_timeout: float | None | dict[str, float | None] = None) -> dict[str, ChatOpenAI]:
        return build_chat_models(identity, self.validated_routes(identity),
                                 request_timeout=request_timeout)

    def app_chat_models(self, identity: WorkspaceIdentity) -> dict[str, ChatOpenAI]:
        """No application-imposed provider read timeout, including the reused writer route."""
        routes = self.validated_routes(identity)
        return build_chat_models(identity, routes, request_timeout=None)

    def status(self, identity: WorkspaceIdentity | None = None) -> dict:
        result = {"authentication": "not_resolved", "configured": False,
                  "runtime_verified": False, "roles": dict(ROLE_PURPOSES)}
        if identity is not None:
            result.update(authentication="identity_resolved", mode=identity.mode,
                          workspace=identity.host, configured=bool(self.load(identity)))
        return result


def build_chat_models(identity: WorkspaceIdentity, routes: dict, *,
                      request_timeout: float | None | dict[str, float | None] = None) -> dict[str, ChatOpenAI]:
    """Build clients only. routes must be configure()/chat_models() validated output.

    No token appears in model fields: the callback obtains fresh identity headers
    on each request. Do not attach tracing/logging that captures request headers.
    This function is not a readiness/permission check; chat_models re-discovers.
    request_timeout is seconds per provider request (not an overall run budget).
    None disables the client timeout. Explicit finite values from 1 to 180 opt
    into a request timeout; a partial role map leaves other roles unlimited.
    Broad SDK retries are disabled. The agent/writer retries only serving HTTP 429.
    """
    _inputs(routes)
    if isinstance(request_timeout, dict):
        if set(request_timeout) - set(ROLE_PURPOSES):
            raise RoutingError("Request timeout overrides must name existing roles")
        timeouts = {role: request_timeout.get(role) for role in ROLE_PURPOSES}
    else:
        timeouts = dict.fromkeys(ROLE_PURPOSES, request_timeout)
    if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value)
           or not 1 <= value <= 180) for value in timeouts.values()):
        raise RoutingError("Model request timeout must be None or finite numeric seconds from 1 to 180")
    if _host(identity.client.config.host) != identity.host:
        raise RoutingError("Client workspace does not match identity")

    def api_key():
        try:
            if _host(identity.client.config.host) != identity.host:
                raise ValueError("Workspace changed")
            value = identity.client.config.authenticate().get("Authorization")
            if not isinstance(value, str) or not value.startswith("Bearer "):
                raise ValueError("Bearer authorization is required")
            token = value[7:]
            if not token or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError("Invalid bearer token")
            return token
        except Exception:
            raise AuthenticationUnavailable(_auth_message(identity.mode)) from None

    return {role: _DatabricksChatOpenAI(model=route["endpoint"],
                            base_url=identity.host + "/serving-endpoints", api_key=api_key,
                            timeout=timeouts[role], max_retries=0, temperature=None,
                            use_responses_api=False, stream_usage=False,
                            disable_streaming="tool_calling",
                            organization="", openai_proxy="", profile={})
            for role, route in routes.items()}