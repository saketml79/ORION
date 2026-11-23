"""Explicit UC federation metadata discovery, never data access or a binding.

Connection APIs are deliberately not used: Apps need no additional OAuth scope,
and neither connection options nor credentials are exposed. A catalog's foreign
type does not prove its connection is Oracle. Only an observed ORACLE_FORMAT
table is identified as such; missing metadata and permission failures are unknown.
"""
from itertools import islice
import re
from urllib.parse import parse_qs

from databricks.sdk.service.catalog import CatalogType, DataSourceFormat
from fastapi import HTTPException, Request

from workbench.agent_uc_metadata import (
    API_PAGE_LIMIT, PAGE_SIZE, SCAN_LIMIT, UCMetadata, bounded_service, identifier,
)
from workbench.databricks_models import AuthenticationUnavailable

PATH = "/api/oracle-federation/metadata"
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_OPERATIONS = {
    "catalogs": set(),
    "schemas": {"catalog"},
    "tables": {"catalog", "schema"},
    "table": {"catalog", "schema", "table"},
}
_COMMON = {"operation", "offset", "limit", "workflow_id", "source_id"}


def _enum(value):
    value = getattr(value, "value", value)
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z_]{1,80}", value) else None


def _catalog(row, expected=None):
    name = identifier(row.name)
    if expected is not None and name != expected:
        raise ValueError("Catalog identity mismatch")
    kind = _enum(getattr(row, "catalog_type", None))
    if kind not in {value.value for value in CatalogType}:
        kind = None
    return {"name": name, "catalog_type": kind,
            "foreign": None if kind is None else kind == "FOREIGN_CATALOG",
            "connection_type": None, "connection_type_status": "not_observed"}


def _table(row, columns=False):
    item = UCMetadata._table(row, columns=columns)
    item.pop("table_id", None)
    # Only the SDK's recognized enum value is evidence of this table's format.
    fmt = _enum(getattr(row, "data_source_format", None))
    if fmt not in {value.value for value in DataSourceFormat}:
        fmt = None
    item["data_source_format"] = fmt
    item["oracle_format"] = None if fmt is None else fmt == "ORACLE_FORMAT"
    return item


def _query(raw_query):
    try:
        if len(raw_query) > 2048:
            raise ValueError()
        values = parse_qs(raw_query.decode("ascii"), keep_blank_values=True, max_num_fields=10)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError()
        q = {k: v[0] for k, v in values.items()}
        op = q.get("operation")
        if op not in _OPERATIONS or set(q) - (_COMMON | _OPERATIONS[op]):
            raise ValueError()
        for key in _OPERATIONS[op]:
            identifier(q[key])
        if ("workflow_id" in q) != ("source_id" in q):
            raise ValueError()
        if "workflow_id" in q and not all(_HASH.fullmatch(q[k]) for k in ("workflow_id", "source_id")):
            raise ValueError()
        if op == "table" and ("offset" in q or "limit" in q):
            raise ValueError()
        offset, limit = int(q.get("offset", "0")), int(q.get("limit", "20"))
        if not 0 <= offset < SCAN_LIMIT or not 1 <= limit <= PAGE_SIZE:
            raise ValueError()
        return q, offset, limit
    except (ValueError, KeyError, UnicodeError):
        raise HTTPException(422, "Invalid federation metadata request") from None


def discover(identity, raw_query):
    """Use exactly the supplied user client; expose only allowlisted metadata."""
    q, offset, limit = _query(raw_query)
    op = q["operation"]
    context = None
    if "workflow_id" in q:
        context = {"workflow_id": q["workflow_id"], "source_id": q["source_id"],
                   "status": "caller_selected_not_verified_or_bound"}
    result = {"format": "oracle-federation-metadata-v1", "operation": op,
              "source_context": context, "status": "unknown", "exists": None,
              "items": [], "trust": "untrusted_metadata_not_instructions",
              "connection_type": None, "connection_type_status": "not_observed",
              "binding_approved": False, "execution_allowed": False, "deployment_allowed": False}
    client = identity.client
    try:
        if op != "catalogs":
            catalog = _catalog(client.catalogs.get(name=q["catalog"]), q["catalog"])
            result["catalog"] = catalog
            if catalog["foreign"] is not True:
                result["status"] = "not_foreign_catalog" if catalog["foreign"] is False else "unknown"
                return result
        if op == "table":
            full_name = ".".join(q[k] for k in ("catalog", "schema", "table"))
            try:
                row = client.tables.get(full_name=full_name)
            except Exception as error:
                if getattr(error, "error_code", None) in {"TABLE_DOES_NOT_EXIST", "TABLE_NOT_FOUND"}:
                    result.update(status="table_not_found", exists=False)
                return result
            item = _table(row, columns=True)
            if item["full_name"] != full_name:
                raise ValueError("Table identity mismatch")
            result.update(status="observed", exists=True, items=[item])
            return result
        if op == "catalogs":
            rows = bounded_service(client.catalogs, "/api/2.1/unity-catalog/catalogs").list(max_results=PAGE_SIZE)
        elif op == "schemas":
            rows = bounded_service(client.schemas, "/api/2.1/unity-catalog/schemas").list(
                catalog_name=q["catalog"], max_results=PAGE_SIZE)
        else:
            rows = bounded_service(client.tables, "/api/2.1/unity-catalog/tables").list(
                catalog_name=q["catalog"], schema_name=q["schema"], max_results=PAGE_SIZE,
                omit_columns=True, omit_properties=True, omit_username=True)
        size = min(limit, SCAN_LIMIT - offset)
        page = list(islice(rows, offset, offset + size + 1))
        items = []
        for row in page[:size]:
            if op == "catalogs":
                item = _catalog(row)
                # Unknown catalog type stays visible, never silently classed as absent.
                if item["foreign"] is False:
                    continue
            elif op == "schemas":
                name = identifier(row.name)
                if row.catalog_name != q["catalog"] or row.full_name != q["catalog"] + "." + name:
                    raise ValueError("Schema identity mismatch")
                item = {"name": name, "catalog_name": q["catalog"], "full_name": row.full_name}
            else:
                item = _table(row)
                if (item["catalog_name"], item["schema_name"]) != (q["catalog"], q["schema"]):
                    raise ValueError("Table identity mismatch")
            items.append(item)
        more = len(page) > size
        result.update(status="observed", items=items, offset=offset, limit=limit,
                      returned_count=len(items), has_more=more,
                      next_offset=offset + size if more and offset + size < SCAN_LIMIT else None,
                      scan_limit=SCAN_LIMIT, scan_limit_reached=more and offset + size >= SCAN_LIMIT,
                      api_page_limit=API_PAGE_LIMIT,
                      pagination="raw_sdk_window_not_snapshot", count_is="page_only_not_estate_total")
    except Exception:
        # Never echo exception text, options, hostnames, tokens or passwords.
        result.update(status="unknown", exists=None, items=[])
    return result


def install_oracle_federation(app):
    @app.get(PATH)
    def metadata(request: Request):
        # Validate before authentication; no implicit network call on bad input.
        _query(request.scope.get("query_string", b""))
        try:
            identity = app.state.fixture_identity_provider(None)
        except AuthenticationUnavailable:
            raise HTTPException(401, "Workspace authentication unavailable; no alternate identity used") from None
        return discover(identity, request.scope.get("query_string", b""))