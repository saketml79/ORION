"""Explicit, local single-user Volume metadata browsing and XML staging.

HTTP contracts (all paths under /api/databricks/volumes):
* GET /status and POST /connect: mode, profile, configured, connected,
  auth_check, host, user_id, local_only, production_remote_enabled.
  Status is offline; configured means a successfully connected identity.
* GET /catalogs, /schemas?catalog=, /volumes?catalog=&schema=: items [{name}],
  limit:200, truncated:bool. GET /files additionally takes volume and directory
  (relative, default empty), returns volume_path, directory and items containing
  path (volume-relative), name, size_bytes (nullable), is_directory.
  At most 200 raw SDK entries are inspected, including filtered entries.
  truncated means the cap was reached, not a proof of another entry.
* POST /import: {project_id,catalog,schema,volume,paths:[relative XML],consent:true}.
  Returns ProjectManager.stage's exact contract: status, project_id, items,
  total, analysis_started:false, job_id:null. Items include name, sha256,
  size_bytes, status, created_at (and reused:true for completed duplicates).
  No analysis, model inference, execution, or deployment is initiated.
* Errors: {detail:sanitized message}, 400 invalid selection/path, 401 reconnect,
  403 consent/access/project mismatch, 404 missing resource/project, 413 limits,
  415 non-XML, 422 invalid body, 501 unsupported hosted auth, 502 SDK failure.

Identities are ephemeral and never persisted. Only server code can inject an
identity factory. Request headers/body cannot select a host, profile, or token.
The existing loopback/CSRF/request-size boundary remains mandatory.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from itertools import islice
import os
from pathlib import Path
import re
import tempfile
from threading import RLock

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from workbench.projects import MAX_BATCH_BYTES, MAX_FILE_BYTES, MAX_FILES

PREFIX = "/api/databricks/volumes"
LIST_LIMIT = 200
CHUNK_BYTES = 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")


def _identifier(value):
    if not isinstance(value, str) or len(value) > 255 or not _IDENTIFIER.fullmatch(value):
        raise HTTPException(400, "Use a single safe catalog, schema or volume identifier")
    return value


def _relative(value, *, xml=False):
    if not isinstance(value, str) or len(value) > 4096:
        raise HTTPException(400, "A bounded volume-relative path is required")
    if value == "" and not xml:
        return value
    parts = value.split("/")
    if (any(p in {"", ".", ".."} or len(p) > 200 or p != p.strip() for p in parts)
            or any(c in value for c in ("%", "\\", ":", "?", "#"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise HTTPException(400, "Use relative path segments within the selected volume")
    if xml:
        from workbench.app import _filename
        _filename(parts[-1], {".xml"})
    return value


def _root(catalog, schema, volume):
    return "/Volumes/" + "/".join(_identifier(v) for v in (catalog, schema, volume))


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def build_obo_identity_factory(*, deployment_host=None, token_provider=None, trusted_proxy=False):
    """Optional server-only adapter, not an HTTP or global hosted-auth switch.

    The host, trust assertion and zero-argument token provider must be supplied
    by trusted deployment code. The explicit environment opt-in alone does not
    establish trust or relax the local boundary. No forwarded headers are read.
    """
    def factory():
        if (os.environ.get("WORKBENCH_VOLUME_HOSTED_AUTH") != "1"
                or trusted_proxy is not True or not deployment_host or not callable(token_provider)):
            raise HTTPException(501, "Hosted Volume authentication is not configured")
        from workbench.databricks_models import obo_identity
        return obo_identity(deployment_host, token_provider(), trusted_proxy=True,
                            deployment_host=deployment_host)
    return factory


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=1, max_length=32)
    catalog: str = Field(min_length=1, max_length=255)
    schema: str = Field(min_length=1, max_length=255)
    volume: str = Field(min_length=1, max_length=255)
    paths: list[str] = Field(min_length=1, max_length=MAX_FILES)
    consent: bool = False


class VolumeIntake:
    def __init__(self, manager, identity_factory=None, *, reference_registrar=None):
        self.manager = manager
        self.identity_factory = identity_factory
        self.reference_registrar = reference_registrar
        self.identity = None
        self.auth_check = "not_checked"
        self.lock = RLock()

    def status(self):
        with self.lock:
            identity = self.identity
            return {"mode": identity.mode if identity else "profile",
                    "profile": "labs" if not identity or identity.mode == "profile" else None,
                    "configured": identity is not None, "connected": identity is not None,
                    "auth_check": self.auth_check, "host": identity.host if identity else None,
                    "user_id": identity.user_id if identity else None,
                    "local_only": True, "production_remote_enabled": False}

    def _failure(self, error):
        from databricks.sdk.errors import NotFound, PermissionDenied, Unauthenticated
        from workbench.databricks_models import AuthenticationUnavailable
        if isinstance(error, (Unauthenticated, AuthenticationUnavailable)):
            self.identity = None
            self.auth_check = "reconnect_required"
            raise HTTPException(401, "Reconnect Databricks Volume authentication (local profile: labs)") from None
        if isinstance(error, PermissionDenied):
            raise HTTPException(403, "Databricks Volume access denied") from None
        if isinstance(error, NotFound):
            raise HTTPException(404, "Databricks Volume resource not found") from None
        raise HTTPException(502, "Databricks Volume request failed") from None

    def connect(self):
        with self.lock:
            self.identity = None
            self.auth_check = "reconnect_required"
            try:
                from workbench.databricks_models import WorkspaceIdentity, profile_identity, _host
                identity = self.identity_factory() if self.identity_factory else profile_identity()
                # An injected/prebuilt identity must also be freshly resolved.
                if not isinstance(identity, WorkspaceIdentity):
                    raise ValueError("Invalid identity factory result")
                user = identity.client.current_user.me()
                if (_get(user, "id") != identity.user_id
                        or _host(identity.client.config.host) != identity.host):
                    from workbench.databricks_models import AuthenticationUnavailable
                    raise AuthenticationUnavailable("Identity changed")
                self.identity = identity
                self.auth_check = "verified"
                return self.status()
            except HTTPException:
                raise
            except ImportError:
                raise HTTPException(501, "Databricks authentication dependencies are unavailable") from None
            except Exception as error:
                self._failure(error)

    @contextmanager
    def _client(self):
        with self.lock:
            if self.identity is None:
                raise HTTPException(401, "Connect Databricks Volume authentication first (local profile: labs)")
            try:
                yield self.identity.client
            except HTTPException:
                raise
            except Exception as error:
                self._failure(error)

    def names(self, kind, catalog=None, schema=None):
        if catalog is not None:
            _identifier(catalog)
        if schema is not None:
            _identifier(schema)
        with self._client() as client:
            if kind == "catalogs":
                iterator = client.catalogs.list(max_results=LIST_LIMIT)
            elif kind == "schemas":
                iterator = client.schemas.list(catalog_name=catalog, max_results=LIST_LIMIT)
            else:
                iterator = client.volumes.list(catalog_name=catalog, schema_name=schema, max_results=LIST_LIMIT)
            raw = list(islice(iterator, LIST_LIMIT))
            # Invalid remote metadata is not reflected into the browser.
            items = []
            for entry in raw:
                try:
                    items.append({"name": _identifier(_get(entry, "name"))})
                except HTTPException:
                    continue
            return {"items": items, "limit": LIST_LIMIT, "truncated": len(raw) == LIST_LIMIT}

    def files(self, catalog, schema, volume, directory):
        root = _root(catalog, schema, volume)
        directory = _relative(directory)
        selected = root + ("/" + directory if directory else "")
        with self._client() as client:
            raw = list(islice(client.files.list_directory_contents(selected, page_size=LIST_LIMIT), LIST_LIMIT))
            items = []
            for entry in raw:
                path = _get(entry, "path")
                is_directory = _get(entry, "is_directory")
                if not isinstance(path, str) or not path.startswith(selected + "/") or type(is_directory) is not bool:
                    continue
                # Only immediate children of this exact directory are reflected.
                # The Files API returns directories with one trailing slash.
                if is_directory and path.endswith("/"):
                    path = path[:-1]
                name = path[len(selected) + 1:]
                if "/" in name:
                    continue
                relative = (directory + "/" if directory else "") + name
                try:
                    _relative(relative, xml=not is_directory)
                except HTTPException:
                    continue
                size = _get(entry, "file_size")
                items.append({"path": relative, "name": name, "is_directory": is_directory,
                              "size_bytes": size if type(size) is int and size >= 0 else None})
            return {"items": items, "directory": directory, "volume_path": root,
                    "limit": LIST_LIMIT, "truncated": len(raw) == LIST_LIMIT}

    def import_xml(self, body, project_header=None):
        if body.consent is not True:
            raise HTTPException(403, "Explicit consent:true is required to download and stage XML")
        if project_header and project_header != body.project_id:
            raise HTTPException(403, "Project header and import project do not match")
        self.manager.registry.project(body.project_id)
        root = _root(body.catalog, body.schema, body.volume)
        paths = [_relative(path, xml=True) for path in body.paths]
        if len(set(paths)) != len(paths):
            raise HTTPException(400, "Select distinct XML paths")
        # Acquire auth before the project lease; neither stage nor reserve does
        # credential work. The lock prevents reconnect switching workspace mid-import.
        with self._client() as client:
            self.manager.reserve(body.project_id)
            files, temporary, total = [], [], 0
            try:
                for relative in paths:
                    response = client.files.download(root + "/" + relative)
                    contents = response.contents
                    if contents is None:
                        raise ValueError("Missing binary contents")
                    try:
                        declared = response.content_length
                        if type(declared) is int and (declared > MAX_FILE_BYTES or total + declared > MAX_BATCH_BYTES):
                            raise HTTPException(413, "XML or aggregate download size limit exceeded")
                        fd, filename = tempfile.mkstemp(suffix=".xml", dir=self.manager.uploads)
                        path = Path(filename)
                        temporary.append(path)
                        size, digest = 0, sha256()
                        with os.fdopen(fd, "wb") as target:
                            while True:
                                chunk = contents.read(CHUNK_BYTES)
                                if not isinstance(chunk, bytes):
                                    raise ValueError("Expected binary stream")
                                if not chunk:
                                    break
                                size += len(chunk)
                                if size > MAX_FILE_BYTES or total + size > MAX_BATCH_BYTES:
                                    raise HTTPException(413, "XML or aggregate download size limit exceeded")
                                target.write(chunk)
                                digest.update(chunk)
                        if not size:
                            raise HTTPException(400, "Empty XML files are not supported")
                        if type(declared) is int and declared != size:
                            raise ValueError("Incomplete download")
                        files.append((path, relative.rsplit("/", 1)[-1], digest.hexdigest(), size))
                        if self.reference_registrar is not None:
                            self.reference_registrar(digest.hexdigest(), root + "/" + relative)
                        total += size
                    finally:
                        contents.close()
                return self.manager.stage(body.project_id, files)
            finally:
                try:
                    for path in temporary:
                        path.unlink(missing_ok=True)
                finally:
                    self.manager.slot.release()


def install_volume_intake(app, identity_factory=None, *, service=None):
    """Install on the root app after projects; injection is server-only."""
    if getattr(app.state, "volume_intake", None) is not None:
        if identity_factory is not None:
            raise ValueError("Volume intake is already installed; inject via its server-side state before use")
        return app.state.volume_intake
    service = service or VolumeIntake(app.state.projects, identity_factory)
    app.state.volume_intake = service
    router = APIRouter(prefix=PREFIX)

    @router.get("/status")
    def status():
        return service.status()

    @router.post("/connect")
    def connect(body: ConnectBody = Body(default=ConnectBody())):
        return service.connect()

    @router.get("/catalogs")
    def catalogs():
        return service.names("catalogs")

    @router.get("/schemas")
    def schemas(catalog: str):
        return service.names("schemas", catalog)

    @router.get("/volumes")
    def volumes(catalog: str, schema: str):
        return service.names("volumes", catalog, schema)

    @router.get("/files")
    def files(catalog: str, schema: str, volume: str, directory: str = ""):
        return service.files(catalog, schema, volume, directory)

    @router.post("/import")
    def import_xml(body: ImportBody, request: Request):
        return service.import_xml(body, request.headers.get("X-Workbench-Project"))

    app.include_router(router)
    return service