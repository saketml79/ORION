"""Read-only inventory metadata and complete, independently paged source evidence.

No source reparse, execution, destination lookup, model call or persistence.
Only canonical, already-sanitized records are presented. Suggestions are not
generated artifacts, approved mappings, or executable equivalence claims.
"""
import json

from fastapi import HTTPException, Query

KINDS = frozenset(("repository", "folder", "mapping", "mapplet", "workflow",
                   "worklet", "session", "source", "target", "connectionreference"))
SECTIONS = frozenset(("overview", "fields", "properties", "children", "usage", "diagnostics", "evidence"))


def _validate(kind, offset, limit, q=""):
    if kind not in KINDS or offset < 0 or not 1 <= limit <= 100 or len(q) > 200:
        raise HTTPException(422, "Invalid inventory kind, search or pagination")


def _version(index, expected=None):
    from hashlib import sha256
    from workbench.validation_campaigns import input_pins
    pins = input_pins(index)
    version = sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()
    if expected is not None and version != expected:
        raise HTTPException(409, "Inventory source changed. Close and reopen the current package inventory.")
    return pins, version


def brief(index, node):
    from workbench.app import _brief
    return {**_brief(node), "attributes": {k: v for k, v in node.get("attributes", {}).items()
            if k in {"DBDNAME", "DATABASETYPE", "OWNERNAME"}},
            "counts": {"fields": len(node.get("fields", ())),
                       "properties": len(node.get("properties", ())),
                       "children": len(index.children.get(node["id"], ())),
                       "usage": len(index.adjacency.get(node["id"], ()))}}


def inventory_response(index, kind, q="", offset=0, limit=100, input_version=None):
    _validate(kind, offset, limit, q)
    pins, version = _version(index, input_version)
    term = q.casefold()
    # IDs disambiguate equal names in sibling folders/databases. Sorting is
    # inventory-only, so existing Explorer order and callers remain unchanged.
    nodes = sorted((n for n in index.kinds.get(kind, ()) if not term or term in n["name"].casefold()),
                   key=lambda n: (n["name"].casefold(), n["id"]))
    return {"items": [brief(index, n) for n in nodes[offset:offset + limit]],
            "total": len(nodes), "offset": offset, "limit": limit,
            "truncated": offset + limit < len(nodes), "pins": pins, "input_version": version}


def inventory_preload(index, offset=0, input_version=None):
    """Return bounded, version-pinned inventory metadata for initial loading."""
    if offset < 0:
        raise HTTPException(422, "Invalid inventory offset")
    pins, version = _version(index, input_version)
    ordered = [node for kind in sorted(KINDS) for node in sorted(
        index.kinds.get(kind, ()), key=lambda n: (n["name"].casefold(), n["id"]))]
    total = len(ordered)
    if total > 20000:
        raise HTTPException(413, "Inventory exceeds the initial loading limit; open a category to load its pages.")
    if offset > total:
        raise HTTPException(422, "Inventory offset exceeds the total")
    items, size = [], 0
    for node in ordered[offset:offset + 1000]:
        row = brief(index, node)
        # ASCII escaping is a conservative bound for transports that encode
        # non-ASCII characters before applying their own response-size limit.
        row_size = len(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) + 1
        if size + row_size > 320 * 1024:
            if not items:
                raise HTTPException(413, "An inventory item exceeds the initial loading limit.")
            break
        items.append(row)
        size += row_size
    return {"items": items, "offset": offset, "total": total,
            "input_version": version, "pins": pins}


def suggested_equivalent(node):
    kind, attrs = node["kind"], node.get("attributes", {})
    database = str(attrs.get("DATABASETYPE", "")).casefold()
    file_based = "flat" in database or "file" in database
    suggestions = {
        "repository": ("Unity Catalog governance/catalog and workspace project", "Separate governance from source-code administration; there is no one-to-one repository conversion."),
        "folder": ("Unity Catalog schema and/or workspace code folder", "Choose by data ownership, security and administrative boundaries, not folder name alone."),
        "mapping": ("SQL / PySpark transformations or Lakeflow Declarative Pipelines", "Choose by dependencies, state, transformation support and batch/streaming requirements; not always a notebook."),
        "mapplet": ("Reusable Python module or SQL function", "Review transformation composition, parameters and supported function semantics."),
        "workflow": ("Lakeflow Jobs tasks, conditions and reviewed run_job hierarchy", "Preserve dependencies, conditions and invocation semantics only for reviewed scopes."),
        "worklet": ("Reusable task grouping or child Job", "There is no native worklet clone; review parameter, invocation and failure propagation contracts."),
        "session": ("Configured Lakeflow task with data bindings", "Preserve write modes, commit/recovery and pre/post SQL order. Session SQL overrides append/override according to source phase, not interchangeable alternatives."),
        "source": ("Unity Catalog Volume / file or API ingestion contract" if file_based else "Managed/external Unity Catalog table, Volume or API ingestion", "Use captured DATABASETYPE and connection metadata. Preserve column types, keys and nullability; actual external schema and access are unverified."),
        "target": ("Unity Catalog Volume / file delivery contract" if file_based else "Managed Delta table (subject to target contract review)", "A flat-file delivery is not automatically semantically equivalent to Delta. Preserve types, keys, nullability, write mode and delivery contract."),
        "connectionreference": ("Reviewed Unity Catalog connection / credential binding", "Only sanitized source aliases are shown. Authentication and endpoint configuration require separate authorized review."),
    }
    title, rationale = suggestions[kind]
    return {"label": "Suggested Databricks equivalent", "suggestion": title, "rationale": rationale,
            "limitations": "Requires review. No DDL, artifact, Job or destination mapping generated; runtime and output parity are not verified.",
            "source_evidence": {"id": node["id"], "kind": kind, "attributes": attrs,
                                "evidence": node.get("evidence", {})}}


def inventory_detail(index, kind, object_id, section="overview", offset=0, limit=100, input_version=None):
    _validate(kind, offset, limit)
    if section not in SECTIONS:
        raise HTTPException(422, "Unknown inventory detail section")
    pins, version = _version(index, input_version)
    node = index.nodes.get(object_id)
    if node is None or node["kind"] != kind:
        raise HTTPException(404, "Unknown canonical inventory object for this kind")
    # Every canonical node key is reachable, without bounding strings, nested
    # attributes or field records. Large collections are retrieved explicitly.
    metadata = {k: v for k, v in node.items() if k not in {"fields", "properties"}}
    if section == "overview":
        rows = []
    elif section in {"fields", "properties"}:
        rows = node.get(section, ())
    elif section == "children":
        rows = index.children.get(object_id, ())
    elif section == "usage":
        rows = index.adjacency.get(object_id, ())
    elif section == "evidence":
        # Explicit technical evidence expansion, not metadata enumeration.
        # Traverse only containment, never a shared mapping's unrelated users.
        rows, pending, seen = [], [node], set()
        while pending:
            child = pending.pop()
            if child["id"] in seen:
                continue
            seen.add(child["id"])
            rows.append(child)
            pending.extend(reversed(index.children.get(child["id"], ())))
    else:
        rows = index.diagnostics.get(object_id, ())
    chosen = rows[offset:offset + limit]
    if section == "usage":
        chosen = [{**edge, "source_object": brief(index, index.nodes[edge["source"]]) if edge["source"] in index.nodes else None,
                   "target_object": brief(index, index.nodes[edge["target"]]) if edge["target"] in index.nodes else None}
                  for edge in chosen]
    return {"node": metadata, "counts": brief(index, node)["counts"],
            "suggested_equivalent": suggested_equivalent(node), "section": section,
            "items": chosen, "total": len(rows), "offset": offset, "limit": limit,
            "truncated": offset + len(chosen) < len(rows), "pins": pins, "input_version": version}


def install_inventory_browser(app, service):
    @app.get("/api/inventory-preload")
    def preload(offset: int = Query(default=0, ge=0), input_version: str | None = None):
        return inventory_preload(service.require(), offset, input_version)

    @app.get("/api/inventory/{kind}/{object_id}")
    def detail(kind: str, object_id: str, section: str = "overview",
               offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
               input_version: str | None = None):
        return inventory_detail(service.require(), kind, object_id, section, offset, limit, input_version)