"""Offline-only federation metadata tests; no real Databricks calls."""
import json
from types import SimpleNamespace as NS

from databricks.sdk.service.catalog import CatalogsAPI, SchemasAPI, TablesAPI
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from workbench.databricks_models import AuthenticationUnavailable
from workbench.oracle_federation import PATH, discover, install_oracle_federation


SECRET = "very-private-connection-secret"
TOKEN = "forwarded-user-token-private"
HOST = "https://workspace.example.com"
ORIGIN = "https://workbench.example.com"


class Api:
    """Real SDK metadata iterators backed by a strictly GET-only fake API."""

    def __init__(self):
        self._cfg = NS(workspace_id=None)
        self.calls = []
        self.fail = None
        self.empty_pages = False

    def do(self, method, path, **kwargs):
        assert method == "GET"
        self.calls.append((method, path, dict(kwargs.get("query", {}))))
        if self.fail:
            raise self.fail
        query = kwargs.get("query", {})
        if path.endswith("/catalogs"):
            if self.empty_pages:
                return {"catalogs": [], "next_page_token": "never-ending"}
            if not query.get("page_token"):
                return {"catalogs": [{"name": "internal", "catalog_type": "MANAGED_CATALOG"},
                                     {"name": "foreign", "catalog_type": "FOREIGN_CATALOG",
                                      "connection_name": SECRET, "options": {"password": SECRET}}],
                        "next_page_token": "next"}
            return {"catalogs": [{"name": "unknown"}]}
        if path.endswith("/catalogs/foreign"):
            return {"name": "foreign", "catalog_type": "FOREIGN_CATALOG",
                    "connection_name": SECRET, "options": {"password": SECRET}}
        if path.endswith("/catalogs/unknown"):
            return {"name": "unknown"}
        if path.endswith("/catalogs/internal"):
            return {"name": "internal", "catalog_type": "MANAGED_CATALOG"}
        if path.endswith("/schemas"):
            return {"schemas": [{"name": "app", "catalog_name": "foreign", "full_name": "foreign.app"}]}
        if path.endswith("/tables"):
            return {"tables": [self.table(columns=False), self.table(name="unclassified", fmt=None, columns=False)]}
        if path.endswith("/tables/foreign.app.orders"):
            return self.table()
        raise AssertionError("Unexpected API operation")

    @staticmethod
    def table(name="orders", fmt="ORACLE_FORMAT", columns=True):
        row = {"name": name, "catalog_name": "foreign", "schema_name": "app",
               "full_name": "foreign.app." + name, "data_source_format": fmt,
               "table_type": "FOREIGN", "properties": {"password": SECRET},
               "storage_location": "https://user:" + SECRET + "@host", "view_definition": SECRET}
        if columns:
            row["columns"] = [{"name": "ID", "type_text": "DECIMAL(10,0)", "nullable": False,
                               "position": 0, "comment": SECRET}]
        return row


def identity(api, mode="obo"):
    client = NS(catalogs=CatalogsAPI(api), schemas=SchemasAPI(api), tables=TablesAPI(api))
    return NS(mode=mode, host=HOST, user_id="user", client=client)


def request(identity_value, query):
    return discover(identity_value, query.encode("ascii"))


def test_catalog_windows_preserve_unknown_and_do_not_list_connections():
    api = Api()
    user = identity(api)
    first = request(user, "operation=catalogs&limit=1")
    assert first["items"] == [] and first["has_more"] and first["next_offset"] == 1
    second = request(user, "operation=catalogs&limit=1&offset=1")
    assert second["items"][0]["foreign"] is True
    assert second["items"][0]["connection_type"] is None
    third = request(user, "operation=catalogs&limit=1&offset=2")
    assert third["items"][0]["foreign"] is None and not third["has_more"]
    assert SECRET not in json.dumps([first, second, third])
    assert all(call[1].endswith("/catalogs") for call in api.calls)


def test_foreign_catalog_schema_table_and_caller_selected_context():
    api = Api()
    user = identity(api)
    workflow, source = "a" * 64, "b" * 64
    context = f"&workflow_id={workflow}&source_id={source}"
    schemas = request(user, "operation=schemas&catalog=foreign" + context)
    assert schemas["status"] == "observed" and schemas["items"][0]["full_name"] == "foreign.app"
    assert schemas["source_context"] == {"workflow_id": workflow, "source_id": source,
                                         "status": "caller_selected_not_verified_or_bound"}
    tables = request(user, "operation=tables&catalog=foreign&schema=app")
    assert [row["oracle_format"] for row in tables["items"]] == [True, None]
    observed = request(user, "operation=table&catalog=foreign&schema=app&table=orders")
    assert observed["exists"] is True and observed["items"][0]["columns"][0]["name"] == "ID"
    assert observed["binding_approved"] is False and observed["execution_allowed"] is False
    assert SECRET not in json.dumps([schemas, tables, observed])
    assert all(call[0] == "GET" for call in api.calls)


def test_unknown_or_nonforeign_catalog_never_inspected_or_called_oracle():
    api = Api()
    user = identity(api)
    unknown = request(user, "operation=schemas&catalog=unknown")
    internal = request(user, "operation=schemas&catalog=internal")
    assert unknown["status"] == "unknown" and unknown["catalog"]["foreign"] is None
    assert internal["status"] == "not_foreign_catalog" and internal["exists"] is None
    assert all("/schemas" not in call[1] for call in api.calls)


def test_permission_failure_unknown_vs_specific_table_absence():
    class Failure(Exception):
        def __init__(self, code):
            super().__init__(SECRET)
            self.error_code = code

    api = Api()
    user = identity(api)
    api.fail = Failure("PERMISSION_DENIED")
    denied = request(user, "operation=table&catalog=foreign&schema=app&table=orders")
    assert denied["status"] == "unknown" and denied["exists"] is None
    api.fail = None
    original = api.do

    def fail_table(method, path, **kwargs):
        if path.endswith("/tables/foreign.app.orders"):
            raise Failure("TABLE_DOES_NOT_EXIST")
        return original(method, path, **kwargs)

    api.do = fail_table
    absent = request(user, "operation=table&catalog=foreign&schema=app&table=orders")
    assert absent["status"] == "table_not_found" and absent["exists"] is False
    assert SECRET not in json.dumps([denied, absent])


def test_empty_sdk_pages_are_bounded_and_not_misreported_as_absence():
    api = Api()
    api.empty_pages = True
    result = request(identity(api), "operation=catalogs")
    assert len(api.calls) == 20
    assert result["status"] == "unknown" and result["exists"] is None


@pytest.mark.parametrize("query", [
    "operation=catalogs&limit=51", "operation=catalogs&offset=1000",
    "operation=catalogs&operation=table", "operation=table&catalog=foreign&schema=app&table=a.b",
    "operation=schemas&catalog=foreign&source_id=" + "a" * 64,
    "operation=catalogs&password=" + SECRET,
])
def test_invalid_requests_do_not_call_auth_or_echo_input(query):
    app = FastAPI()
    app.state.fixture_identity_provider = lambda _: pytest.fail("unexpected authentication")
    install_oracle_federation(app)
    with TestClient(app) as client:
        response = client.get(PATH + "?" + query)
    assert response.status_code == 422 and SECRET not in response.text


def test_local_route_uses_injected_user_provider_and_fails_closed():
    app = FastAPI()
    calls = []
    api = Api()
    app.state.fixture_identity_provider = lambda profile: calls.append(profile) or identity(api, "profile")
    install_oracle_federation(app)
    with TestClient(app) as client:
        response = client.get(PATH, params={"operation": "catalogs"})
        assert response.status_code == 200 and calls == [None]
        def unavailable(_):
            raise AuthenticationUnavailable(SECRET)
        app.state.fixture_identity_provider = unavailable
        denied = client.get(PATH, params={"operation": "catalogs"})
        assert denied.status_code == 401 and SECRET not in denied.text


def test_real_local_package_app_registers_metadata_route(tmp_path):
    from workbench.app import _create_package_app

    api = Api()
    app = _create_package_app(tmp_path)
    app.state.fixture_identity_provider = lambda _: identity(api, "profile")
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        response = client.get(PATH, params={"operation": "catalogs"})
    assert response.status_code == 200 and response.json()["status"] == "observed"
    assert SECRET not in response.text


def test_hosted_route_uses_fresh_obo_without_provision_or_sql(monkeypatch):
    from workbench.hosted import HostedConfig, create_hosted_app
    import workbench.hosted as hosted

    api = Api()
    seen = []
    def resolve(host, token, **kwargs):
        seen.append((host, token, kwargs))
        return identity(api)
    monkeypatch.setattr(hosted, "provision", lambda *a, **k: pytest.fail("provision forbidden"))
    app = create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="unused"), identity_resolver=resolve)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        assert client.get(PATH, params={"operation": "catalogs"}).status_code == 401
        response = client.get(PATH, params={"operation": "table", "catalog": "foreign",
                                           "schema": "app", "table": "orders"},
                              headers={"x-forwarded-access-token": TOKEN})
    assert response.status_code == 200 and response.json()["items"][0]["oracle_format"] is True
    assert seen == [(HOST, TOKEN, {"trusted_proxy": True, "deployment_host": HOST})]
    assert TOKEN not in response.text and SECRET not in response.text
    assert all(call[0] == "GET" for call in api.calls)


def test_hosted_route_rejects_non_obo_identity():
    from workbench.hosted import HostedConfig, create_hosted_app

    api = Api()
    app = create_hosted_app(HostedConfig(HOST, ORIGIN, warehouse="unused"),
                            identity_resolver=lambda *a, **k: identity(api, "profile"))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(PATH, params={"operation": "catalogs"},
                              headers={"x-forwarded-access-token": TOKEN})
    assert response.status_code == 401 and not api.calls