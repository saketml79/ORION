"""Explicit read-only UC fixture observations, never production bindings.

Only SCIM identity, schema and table metadata APIs are used. No XML reparsing,
SQL, data access, writes, model calls or persisted/global verification state.
"""
from collections import deque
from datetime import datetime, timezone
import re
from typing import Literal

from databricks.sdk.errors import NotFound
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from workbench.domain import _RELATIONS
from workbench.source_fixtures import fixture_namespace, identifier, mock_type


class FixtureMetadataBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str = Field(min_length=1, max_length=512)
    catalog: str = Field(min_length=1, max_length=255)
    profile: Literal["labs"] | None = "labs"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _column(column):
    return {"name": column.name, "type": column.type_text, "nullable": column.nullable}


def _signature(columns):
    return [(c["name"].casefold(), re.sub(r"\s+", "", c["type"]).upper(), c["nullable"])
            for c in columns]


def observe(index, workflow_id, catalog, identity):
    identifier(catalog)
    if index.nodes.get(workflow_id, {}).get("kind") != "workflow":
        raise ValueError("workflow_id must identify a canonical workflow definition")
    reached, queue = {workflow_id}, deque([workflow_id])
    while queue:
        for edge in index.out.get(queue.popleft(), ()):
            if (edge["kind"] in _RELATIONS and edge["target"] not in reached
                and index.nodes[edge["target"]]["kind"] != "workflow"):
                reached.add(edge["target"])
                queue.append(edge["target"])
    sources = [index.nodes[n] for n in sorted(reached) if index.nodes[n]["kind"] == "source"]
    if len(sources) > 200:
        raise ValueError("Selected workflow exceeds the 200-source metadata request bound")
    package_hash = index.model["package"]["sha256"]
    records, schemas, names, plans = [], {}, [], []
    # Plan every canonical source before reads. Never choose a bare-name match.
    for source in sources:
        attrs, scope = source["attributes"], source["scope"]
        xml_identity = [scope.get("repository", ""), scope.get("folder", ""),
                        attrs.get("DATABASETYPE", ""), attrs.get("DBDNAME", ""),
                        attrs.get("OWNERNAME", ""), attrs["NAME"]]
        record = {"source_id": source["id"], "xml_identity": xml_identity,
                  "binding_status": "fixture_only_unapproved", "approved": False,
                  "exists": None, "schema_exists": None, "status": "unobserved",
                  "expected_full_name": None, "actual_full_name": None,
                  "expected_columns": [], "columns": [], "observed_at": None}
        records.append(record)
        try:
            schema, table = fixture_namespace(xml_identity)
            name = ".".join((catalog, schema, table))
            names.append(name)
            record["expected_full_name"] = name
            fields = [f["attributes"] for f in source["fields"] if f["kind"] == "sourcefield"]
            if not fields or len(fields) > 1000:
                raise ValueError("Missing or excessive canonical source fields")
            columns = [{"name": f["NAME"], "type": mock_type(f, attrs.get("DATABASETYPE", "")),
                        "nullable": f.get("NULLABLE") != "NOTNULL"} for f in fields]
            if len({c["name"].casefold() for c in columns}) != len(columns):
                raise ValueError("Duplicate source columns")
            record["expected_columns"] = columns
            plans.append((record, schema, name))
        except (ValueError, KeyError, TypeError):
            record.update(status="mapping_error", error="Canonical fixture mapping is incomplete or invalid")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate complete source identity or case-insensitive fixture collision")
    for record, schema, name in plans:
        try:
            schema_name = catalog + "." + schema
            if schema_name not in schemas:
                try:
                    metadata = identity.client.schemas.get(schema_name)
                    if metadata.full_name != schema_name:
                        raise ValueError("Schema identity mismatch")
                    schemas[schema_name] = True
                except NotFound:
                    schemas[schema_name] = False
            record["schema_exists"] = schemas[schema_name]
            if not schemas[schema_name]:
                record.update(exists=False, status="schema_not_found")
                continue
            table = identity.client.tables.get(name)
            if table.full_name != name or len(table.columns or []) > 1000:
                raise ValueError("Table identity or metadata bound mismatch")
            columns = [_column(c) for c in table.columns or []]
            properties = table.properties or {}
            provenance = {key: properties.get(key) for key in ("orion.fixture", "orion.package_sha256")}
            fixture_match = provenance["orion.fixture"] == "xml_source_schema_only"
            package_match = provenance["orion.package_sha256"] == package_hash
            column_match = _signature(columns) == _signature(record["expected_columns"])
            fmt = getattr(table.data_source_format, "value", table.data_source_format)
            record.update(exists=True, actual_full_name=table.full_name, columns=columns,
                          data_source_format=fmt, fixture_provenance=provenance,
                          fixture_provenance_matches=fixture_match, package_hash_matches=package_match,
                          column_schema_matches=column_match,
                          status="metadata_verified_fixture_only" if fixture_match and package_match
                          and column_match and fmt == "DELTA" else "metadata_mismatch")
        except NotFound:
            record.update(exists=False, status="table_not_found")
        except Exception as error:
            # Never turn access/auth/network failures into missing tables, nor
            # expose upstream bodies, credentials or SQL in public evidence.
            record.update(status="metadata_error", error=type(error).__name__)
        finally:
            record["observed_at"] = _now()
    unresolved = [n for n in sorted(reached) if index.nodes[n]["kind"] == "unresolved_reference"]
    from workbench.reference_diagnostics import reference_report
    diagnostics = reference_report(index.nodes, reached)
    source_blockers = diagnostics["summary"]["source_definition_blockers"]
    return {"workflow_id": workflow_id, "package_hash": package_hash, "catalog": catalog,
            "identity": {"mode": identity.mode, "host": identity.host, "user_id": identity.user_id},
            "observed_at": _now(), "sources": records, "unresolved_reference_ids": unresolved,
            "targets": [{"target_id": n, "binding_status": "unresolved", "approved": False}
                        for n in sorted(reached) if index.nodes[n]["kind"] == "target"],
            "binding_status": ("fixture_only_unapproved" if sources else
                               "no_resolved_sources" if source_blockers else "no_direct_sources"),
            "reference_diagnostics": diagnostics,
            "source_scope": "selected_workflow_definition_closure_only_excludes_called_workflows",
            "direct_source_count": len(sources), "direct_target_count": sum(
                index.nodes[n]["kind"] == "target" for n in reached),
            "child_metadata_reads": False, "approved": False,
            "production_ready": False, "generation_allowed": False, "deployment_allowed": False,
            "notice": "Metadata observations only. Empty source fixtures are not production bindings, source connectivity, data parity or target approval."}


def install_fixture_metadata(app):
    from workbench.databricks_models import AuthenticationUnavailable, RoutingError, profile_identity

    # Hosted installation replaces this with a fresh ContextVar OBO provider.
    app.state.fixture_identity_provider = lambda profile: profile_identity(profile=profile or "labs")

    @app.post("/api/workflows/fixture-metadata")
    def read_metadata(body: FixtureMetadataBody, request: Request):
        index = app.state.service.require()
        app.state.service.workflow(index, body.workflow_id)
        identifier(body.catalog)
        try:
            identity = app.state.fixture_identity_provider(body.profile)
        except AuthenticationUnavailable:
            raise HTTPException(401, "Workspace authentication failed; no alternate identity used") from None
        except RoutingError:
            raise HTTPException(422, "Unsupported workspace profile") from None
        result = observe(index, body.workflow_id, body.catalog, identity)
        return {**result, "project_id": request.headers.get("X-Workbench-Project"),
                "profile": "labs" if identity.mode == "profile" else None}