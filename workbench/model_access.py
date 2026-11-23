"""Explicit local labs model discovery and routing, never implicit inference."""
from threading import RLock
from time import monotonic

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from workbench.agents import AgentRuntime
from workbench.databricks_models import (
    AuthenticationUnavailable, DiscoveryUnavailable, RoutingError,
    ROLE_PURPOSES, WorkspaceModels, _profile_host, profile_identity,
)


class RouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str
    tool_support_confirmed: bool = False


def _local_uc_identity_provider(host):
    """Resolve labs afresh per metadata call, pinned to the model workspace."""
    def resolve():
        identity = profile_identity('labs')
        if identity.host != host:
            raise AuthenticationUnavailable('Selected model workspace changed; reconnect labs')
        return identity
    return resolve


def workspace_ready(runtime):
    status = runtime.status()
    from workbench.agents import _configured_model
    return status.get("available") is True and (
        status.get("authentication") == "model_provided" or _configured_model() is not None)


def install_model_access(app, identity_factory=profile_identity):
    service = app.state.service
    from workbench.external_models import LocalAccess
    app.add_middleware(LocalAccess, service=service)
    router = WorkspaceModels(service.root / "models")
    lock, connection = RLock(), {}

    @app.get("/api/models/workspace")
    def workspace():
        try:
            host = _profile_host()
        except AuthenticationUnavailable:
            host = None
        return {"workspace": host, "mode": "local_user_profile", "profile": "labs",
                "auth_check": "not_checked", "connected": False}

    @app.get("/api/models/status")
    def status():
        external = service.runtime.status()
        if external.get("mode") == "external":
            return {**external, "roles": ROLE_PURPOSES, "connected": external["configured"],
                    "reason": "Session-only external API configuration; inference remains unverified."}
        routes = service.runtime.status().get("model_routes", {})
        selected = set(routes.values())
        return {"connected": bool(connection), "profile": "labs", "mode": "local_user_profile",
            "workspace": workspace()["workspace"],
            "active_endpoint": next(iter(selected)) if len(selected) == 1 else None,
            "model_routes": routes,
                "inference_ready": workspace_ready(service.runtime),
                "reason": "Connect your Databricks workspace and select a chat endpoint. Local labs authentication is not hosted OBO.",
                "roles": ROLE_PURPOSES}

    @app.post("/api/models/connect")
    def connect():
        with lock:
            connection.clear()
            try:
                started = monotonic()
                identity = identity_factory()
                authenticated = monotonic()
                inventory = router.discover(identity)
                listed = monotonic()
            except AuthenticationUnavailable:
                raise HTTPException(401, "The labs sign-in has expired. Reauthenticate the labs profile, then reconnect.") from None
            except DiscoveryUnavailable:
                raise HTTPException(403, "Cannot list workspace endpoints. Check your labs sign-in and serving permissions.") from None
            connection.update(identity=identity, inventory=inventory)
            eligible = [e for e in inventory["endpoints"] if e["type"] == "chat" and e["state"] == "READY"]
            return {"connected": True, **inventory, "mode": "local_user_profile", "profile": "labs",
                    "timings": {"authentication_seconds": round(authenticated - started, 3),
                        "endpoint_list_seconds": round(listed - authenticated, 3)},
                    "default_endpoint": eligible[0]["name"] if eligible else None,
                    "selection_basis": "First ready endpoint explicitly identified as chat; not a model-quality ranking.",
                    "runtime_verified": False}

    @app.post("/api/models/select")
    def select(body: RouteSelection):
        if not body.tool_support_confirmed:
            raise HTTPException(422, "Confirm endpoint chat/tool-call compatibility before routing agent tools.")
        with lock:
            if not connection:
                raise HTTPException(409, "Connect to the workspace first")
            if not service.agent_slot.acquire(blocking=False):
                raise HTTPException(409, "Wait for the current agent run before changing models")
            try:
                identity, inventory = connection["identity"], connection["inventory"]
                routes = {role: {"endpoint": body.endpoint,
                                  "tool_support_evidence": "User declared chat and tool-call compatibility in model picker."}
                          for role in ROLE_PURPOSES}
                router.configure(identity, routes, inventory)
                models = router.app_chat_models(identity)
                service.runtime = AgentRuntime(service.root / "agents", role_models=models,
                                               identity_scope=identity.host + ":" + identity.user_id,
                                               uc_identity_provider=_local_uc_identity_provider(identity.host))
                return {"selected_endpoint": body.endpoint, "roles": list(models),
                        "inference_ready": workspace_ready(service.runtime), "runtime_verified": False,
                        "notice": "Model clients configured. No inference has been executed or verified."}
            except (AuthenticationUnavailable, DiscoveryUnavailable):
                raise HTTPException(401, "Workspace authorization failed. Reconnect labs; no alternate identity was used.") from None
            except RoutingError as exc:
                raise HTTPException(422, str(exc)) from None
            finally:
                service.agent_slot.release()