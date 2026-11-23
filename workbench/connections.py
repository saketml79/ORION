"""Read-only connection evidence from the already parsed canonical XML model.

No discovery, physical binding inference, credentials, or live tests.
Canonical redactions are irreversible and are reported, never filled in.
"""
from __future__ import annotations

from fastapi import Query

from workbench.domain import _redact
from workbench.understanding import REDACTED, _CONNECTION_ENDPOINT, _INLINE_SECRET, _SENSITIVE, _safe_connection_alias


_CONNECTION_KEYS = {"NAME", "CNXREFNAME", "TYPE", "CONNECTIONTYPE",
                    "CONNECTIONSUBTYPE", "CONNECTIONNUMBER", "CONNECTIONNAME", "CONNECTIONVALUE"}
_COMPONENT_KEYS = {"NAME", "TYPE", "SUBTYPE", "SINSTANCENAME", "DSQINSTNAME",
                   "DSQINSTTYPE", "TRANSFORMATIONTYPE"}
NOTICE = ("Canonical XML configuration only. Physical bindings are unknown, not inferred. "
          "Live connections and connection testing are not implemented. "
          "Explicit test fixtures are separate, not automatic connections.")


def _text(value):
    value = str(value)
    cleaned = _redact(value)
    if _INLINE_SECRET.search(value) or _CONNECTION_ENDPOINT.search(value) or cleaned != value:
        return REDACTED
    return value[:1024]


def _metadata(node, allowed):
    """Retain field presence, but only expose schema-allowlisted metadata values."""
    attributes = node.get("attributes", {})
    result = []
    for key, value in sorted(attributes.items()):
        if key in {"status", "status_reason"}:
            continue
        if key in {"CONNECTIONNAME", "CONNECTIONVALUE"}:
            safe = _text(value) if key in allowed and _safe_connection_alias(str(value)) else REDACTED
        else:
            safe = _text(value) if key in allowed and not _SENSITIVE.search(key) else REDACTED
        result.append({"name": _text(key), "value": safe,
                       "status": "value_redacted" if safe == REDACTED else "captured"})
    # Property values have no trusted connection schema. Preserve presence only.
    for prop in node.get("properties", []):
        result.append({"name": _text(prop.get("attributes", {}).get("NAME", prop.get("kind", "property"))),
                       "value": REDACTED, "status": "value_redacted"})
    return result


def inventory(index, offset=0, limit=100):
    """One row per reference, unreferenced component, or declared connection.

    Associations use canonical containment and resolved definition edges only.
    Duplicate aliases never imply a shared physical connection.
    """
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid connection inventory bounds")
    nodes = index.nodes

    def ancestor(node, kind):
        visited = set()
        while node and node["id"] not in visited:
            visited.add(node["id"])
            if node["kind"] == kind:
                return node
            node = nodes.get(node.get("parent_id"))
        return None

    def brief(node):
        return {"id": node["id"], "name": _text(node["name"])} if node else None

    def related(node, relation):
        if not node:
            return []
        return [nodes[e["target"]] for e in index.out.get(node["id"], ())
                if e["kind"] == relation and e["target"] in nodes]

    references = index.kinds.get("connectionreference", ())
    referenced_components = set()
    for reference in references:
        component = ancestor(reference, "sessionextension")
        if component:
            referenced_components.add(component["id"])
    candidates = list(references)
    candidates.extend(n for n in index.kinds.get("sessionextension", ())
                      if n["id"] not in referenced_components)
    candidates.extend(index.kinds.get("connection", ()))
    candidates.sort(key=lambda n: (n.get("evidence", {}).get("locator", ""), n["id"]))
    records = []
    for node in candidates[offset:offset + limit]:
        component = ancestor(node, "sessionextension")
        session = ancestor(node, "session")
        role = (component or {}).get("attributes", {}).get("TYPE", "").upper()
        role = role.lower() if role in {"READER", "WRITER"} else "unknown"
        metadata = _metadata(node, _COMPONENT_KEYS if node["kind"] == "sessionextension" else _CONNECTION_KEYS)
        component_metadata = _metadata(component, _COMPONENT_KEYS) if component else []
        mappings = related(session, "session_mapping")
        instances = related(component, "session_instance")
        # A redacted field is evidence of captured configuration, not absence.
        physical = [m for m in metadata if m["name"] in {"CONNECTIONNAME", "CONNECTIONVALUE", "VALUE"}
                and m["status"] == "value_redacted"]
        schema = [m for m in metadata if "SCHEMA" in m["name"].upper()]
        aliases = {m["name"]: {"value": m["value"], "status": m["status"]}
                   for m in metadata if m["name"] in {"NAME", "CNXREFNAME", "CONNECTIONNAME", "CONNECTIONVALUE"}}
        definitions = related(node, "connection_reference")
        declared = [definition for definition in definitions if definition["kind"] == "connection"]
        records.append({
            "id": node["id"], "kind": node["kind"], "declared_aliases": aliases,
            "connection_definition": brief(declared[0]) if len(declared) == 1 else None,
            "reference_status": ("resolved" if len(declared) == 1 else
                                 "ambiguous" if definitions else node["attributes"].get("status", "unknown")),
            "status_reason": node["attributes"].get("status_reason"),
            "role": role, "source_target": {"reader": "source", "writer": "target"}.get(role, "unknown"),
            "session": brief(session), "component": brief(component),
            "component_metadata": component_metadata[:100],
            "mapping": brief(mappings[0]) if len(mappings) == 1 and mappings[0]["kind"] == "mapping" else None,
            "mapping_status": "captured" if len(mappings) == 1 and mappings[0]["kind"] == "mapping" else "unknown",
            "mapping_instance": brief(instances[0]) if len(instances) == 1 and instances[0]["kind"] == "instance" else None,
            "mapping_instance_status": "captured" if len(instances) == 1 and instances[0]["kind"] == "instance" else "unknown",
            "physical_binding": {"status": "value_redacted" if physical else "not_captured", "value": "unknown"},
            "source_schema": {"status": "value_redacted" if schema else "not_captured", "value": "unknown"},
            "metadata": metadata[:100], "metadata_total": len(metadata), "metadata_truncated": len(metadata) > 100,
            "component_metadata_total": len(component_metadata), "component_metadata_truncated": len(component_metadata) > 100,
            "evidence": {"locator": _text(node.get("evidence", {}).get("locator", "unknown"))},
        })
    return {"items": records, "total": len(candidates), "offset": offset, "limit": limit,
            "package_hash": index.model["package"]["sha256"], "source": "canonical_xml",
            "live_connections_implemented": False, "connection_testing_implemented": False,
            "automatic_connections": False, "notice": NOTICE}


def install_connections(app):
    @app.get("/api/connections")
    def connections(offset: int = Query(default=0, ge=0),
                    limit: int = Query(default=100, ge=1, le=100)):
        # Installed in every package child app: the existing project dispatcher
        # selects the service; never cache another request's package selection.
        return inventory(app.state.service.require(), offset, limit)