"""Invocation-local UC metadata, with caller-owned identity and no SDK auth fallback.

The factory returns a fresh selected-workspace identity with client and host.
Only catalogs.list, schemas.list, tables.list and tables.get are called. SDK
iterators perform pagination; bounded offset windows are explicitly not a stable
estate snapshot. Nothing here executes SQL, reads rows, or changes UC objects.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from itertools import islice
import json
import re
from threading import RLock
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from workbench.domain import _redact


PAGE_SIZE = 50
SCAN_LIMIT = 1000
COLUMN_LIMIT = 1000
OBSERVATION_LIMIT = 200
API_PAGE_LIMIT = 20


def bounded_service(service, endpoint):
    """Clone only SDK metadata services with an invocation-local page guard.

    SDK list iterators can otherwise follow infinitely many empty pages. Cloning
    retains the injected API client's auth and never mutates the caller's client.
    Offline injected services can implement the same list iterator contract.
    """
    if type(service).__module__ != "databricks.sdk.service.catalog":
        return service
    api = service._api

    class PageGuard:
        _cfg = api._cfg

        def __init__(self):
            self.pages = 0

        def do(self, method, path, **kwargs):
            if method != "GET" or path != endpoint or self.pages >= API_PAGE_LIMIT:
                raise ValueError("Metadata API page bound exceeded")
            self.pages += 1
            return api.do(method, path, **kwargs)

    return type(service)(PageGuard())


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest()


def identifier(value):
    # Keep exact identities, never normalize them into a different object.
    if not isinstance(value, str) or not re.fullmatch(r"[\w-]{1,255}", value, re.ASCII):
        raise ValueError("Invalid metadata identifier")
    if _redact(value) != value:
        raise ValueError("Credential material is not an identifier")
    return value


def full_identifier(value):
    if not isinstance(value, str) or len(value.split(".")) != 3:
        raise ValueError("A fully qualified catalog.schema.table is required")
    return ".".join(identifier(part) for part in value.split("."))


def text(value, limit=2048):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Metadata text exceeds its bound")
    # Comments/properties are excluded altogether. Schema type text and reasons
    # remain explicitly untrusted, stripped of markup/control chars and secrets.
    return _redact(re.sub(r"[<>`\x00-\x1f\x7f]", "", value))


class UCBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    canonical_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    full_name: str = Field(min_length=5, max_length=767)
    observation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=1600)
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class UCMetadata:
    """Private authoritative observations. Public projections are detached copies."""

    def __init__(self, provider, run_id):
        self.provider, self.run_id = provider, run_id
        self._observations, self._bindings = [], []
        self._workspace = None
        self._lock = RLock()

    def snapshot(self):
        with self._lock:
            return deepcopy({"uc_metadata_observations": self._observations,
                             "uc_table_bindings": self._bindings})

    def _identity(self):
        if self.provider is None:
            raise LookupError("No selected workspace identity provider")
        identity = self.provider()
        host = getattr(identity, "host", None)
        if not isinstance(host, str):
            raise ValueError("Selected workspace host is required")
        url = urlsplit(host)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("Unsafe workspace identity")
        workspace = "https://" + url.netloc.lower()
        if self._workspace is not None and workspace != self._workspace:
            raise ValueError("Workspace changed within invocation")
        self._workspace = workspace
        return identity.client, workspace

    def observe(self, role, operation, *, catalog="", schema="", full_name="", offset=0, limit=20):
        """Bound each SDK iterator window and project only allowlisted metadata."""
        with self._lock:
            query = {}
            try:
                if operation not in {"catalogs.list", "schemas.list", "tables.list", "tables.get"}:
                    raise ValueError("Unsupported metadata operation")
                if type(offset) is not int or type(limit) is not int or not 0 <= offset < SCAN_LIMIT or not 1 <= limit <= PAGE_SIZE:
                    raise ValueError("Invalid pagination bounds")
                if operation in {"schemas.list", "tables.list"}:
                    query["catalog"] = identifier(catalog)
                if operation == "tables.list":
                    query["schema"] = identifier(schema)
                if operation == "tables.get":
                    query["full_name"] = full_identifier(full_name)
                else:
                    query.update(offset=offset, limit=limit)
            except ValueError:
                return {"status": "invalid_request", "exists": None, "items": []}
            if len(self._observations) >= OBSERVATION_LIMIT:
                return {"status": "observation_limit", "exists": None, "items": []}
            record = {"run_id": self.run_id, "role": role, "operation": operation,
                      "query": query, "workspace": self._workspace,
                      "observed_at": datetime.now(timezone.utc).isoformat(),
                      "trust": "untrusted_metadata_not_instructions", "exists": None,
                      "status": "unknown", "items": []}
            try:
                client, record["workspace"] = self._identity()
                if operation == "tables.get":
                    table = client.tables.get(full_name=query["full_name"])
                    item = self._table(table, columns=True)
                    if item["full_name"] != query["full_name"]:
                        raise ValueError("Table identity mismatch")
                    record.update(status="observed", exists=True, items=[item])
                else:
                    size = min(limit, SCAN_LIMIT - offset)
                    if operation == "catalogs.list":
                        rows = bounded_service(client.catalogs, "/api/2.1/unity-catalog/catalogs").list(max_results=PAGE_SIZE)
                    elif operation == "schemas.list":
                        rows = bounded_service(client.schemas, "/api/2.1/unity-catalog/schemas").list(catalog_name=catalog, max_results=PAGE_SIZE)
                    else:
                        rows = bounded_service(client.tables, "/api/2.1/unity-catalog/tables").list(catalog_name=catalog, schema_name=schema,
                                                  max_results=PAGE_SIZE, omit_properties=True,
                                                  omit_columns=True, omit_username=True)
                    # One lookahead gives a truthful has_more, not a total count.
                    page = list(islice(rows, offset, offset + size + 1))
                    items = []
                    for row in page[:size]:
                        if operation == "tables.list":
                            item = self._table(row, columns=False)
                            if (item["catalog_name"], item["schema_name"]) != (catalog, schema):
                                raise ValueError("Table list scope mismatch")
                        elif operation == "schemas.list":
                            name = identifier(row.name)
                            if row.catalog_name != catalog or row.full_name != catalog + "." + name:
                                raise ValueError("Schema list scope mismatch")
                            item = {"name": name, "catalog_name": catalog, "full_name": row.full_name}
                        else:
                            item = {"name": identifier(row.name)}
                        items.append(item)
                    more = len(page) > size
                    record.update(status="observed", items=items, returned_count=len(items),
                                  has_more=more, next_offset=offset + size if more and offset + size < SCAN_LIMIT else None,
                                  scan_limit=SCAN_LIMIT, scan_limit_reached=more and offset + size >= SCAN_LIMIT,
                                  api_page_limit=API_PAGE_LIMIT,
                                  count_is="page_only_not_estate_total", pagination="bounded_sdk_iterator_window_not_snapshot")
            except LookupError:
                record.update(status="provider_unavailable")
            except Exception as error:
                # Never expose exception prose or classify generic NotFound, auth,
                # denied, schema/catalog errors as missing tables.
                code = getattr(error, "error_code", None)
                if operation == "tables.get" and code in {"TABLE_DOES_NOT_EXIST", "TABLE_NOT_FOUND"}:
                    record.update(status="table_not_found", exists=False,
                                  absence_scope=query["full_name"])
                else:
                    record.update(status="unknown", exists=None)
            payload_hash = digest(record)
            record.update(observation_sha256=payload_hash,
                          observation_id=digest([self.run_id, len(self._observations), payload_hash]))
            self._observations.append(deepcopy(record))
            return deepcopy(record)

    @staticmethod
    def _table(table, *, columns):
        full_name = full_identifier(table.full_name)
        catalog, schema, name = full_name.split(".")
        if (table.catalog_name, table.schema_name, table.name) != (catalog, schema, name):
            raise ValueError("Inconsistent table identity")
        item = {"full_name": full_name, "catalog_name": catalog, "schema_name": schema, "name": name}
        for key in ("table_id", "table_type", "data_source_format"):
            value = getattr(table, key, None)
            item[key] = text(getattr(value, "value", value), 255)
        if columns:
            fields = table.columns
            if fields is None or len(fields) > COLUMN_LIMIT:
                raise ValueError("Incomplete or excessive column schema")
            projected = []
            for column in fields:
                if type(column.nullable) not in {bool, type(None)}:
                    raise ValueError("Invalid nullable metadata")
                numeric = {key: getattr(column, key, None) for key in ("type_precision", "type_scale", "position")}
                if any(value is not None and type(value) is not int for value in numeric.values()):
                    raise ValueError("Invalid numeric column metadata")
                if not isinstance(column.type_text, str) or not column.type_text:
                    raise ValueError("Incomplete column type")
                projected.append({"name": identifier(column.name), "type_text": text(column.type_text),
                                  "type_name": text(getattr(getattr(column, "type_name", None), "value", getattr(column, "type_name", None))),
                                  **numeric, "nullable": column.nullable})
            # No comments, properties, owners, locations, view SQL or constraints.
            item["columns"] = projected
        return item

    def record(self, role, binding, allowed_ids, retrieved_ids):
        """LLM-selected candidate, not a name matcher or semantic/approval gate."""
        with self._lock:
            try:
                value = UCBinding.model_validate(binding)
                full_identifier(value.full_name)
            except ValueError:
                return {"recorded": False, "error": "invalid_binding"}
            kind = allowed_ids.get(value.canonical_id)
            if kind not in {"source", "target"} or value.canonical_id not in retrieved_ids:
                return {"recorded": False, "error": "canonical_id_out_of_role_workflow_scope"}
            if value.canonical_id not in value.evidence_refs or any(ref not in retrieved_ids for ref in value.evidence_refs):
                return {"recorded": False, "error": "evidence_not_owned_by_role"}
            observation = next((row for row in self._observations if row["observation_id"] == value.observation_id), None)
            if observation is None:
                return {"recorded": False, "error": "observation_not_in_this_run"}
            payload = {k: v for k, v in observation.items() if k not in {"observation_id", "observation_sha256"}}
            ordinal = self._observations.index(observation)
            if (digest(payload) != value.observation_sha256 or observation["observation_sha256"] != value.observation_sha256
                    or value.observation_id != digest([self.run_id, ordinal, value.observation_sha256])):
                return {"recorded": False, "error": "observation_integrity_mismatch"}
            if (observation["run_id"] != self.run_id or observation["operation"] != "tables.get"
                    or observation["status"] != "observed" or observation["exists"] is not True
                    or observation["query"]["full_name"] != value.full_name
                    or observation["workspace"] != self._workspace):
                return {"recorded": False, "error": "inspect_exact_table_first"}
            reason = text(value.reason, 1600)
            if not reason or not reason.strip():
                return {"recorded": False, "error": "reason_required"}
            record = {**value.model_dump(), "reason": reason, "role": role, "kind": kind,
                      "run_id": self.run_id, "workspace": observation["workspace"],
                      "confidence": "metadata_verified_candidate_not_semantic_proof",
                      "approved": False, "generation_allowed": False, "deployment_allowed": False}
            record["binding_sha256"] = digest(record)
            if not any(row["binding_sha256"] == record["binding_sha256"] for row in self._bindings):
                if len(self._bindings) >= OBSERVATION_LIMIT:
                    return {"recorded": False, "error": "binding_limit"}
                self._bindings.append(deepcopy(record))
            return {"recorded": True, "binding": deepcopy(record)}