"""Evidence-preserving structural understanding, not a transformation translator.

Only the XML parser's active ancestor chain is retained. The returned graph is
necessarily proportional to the captured metadata. Locators are ordinal XML
paths, never invented source lines. External DOCTYPE declarations are accepted,
but DTDs are not loaded and all entity declarations/references are forbidden.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from defusedxml.ElementTree import iterparse
from workbench.redaction import CREDENTIAL_KEY as _SENSITIVE, SECRET_TEXT as _INLINE_SECRET, is_secret_key, redact_text


FIELD_TAGS = {"SOURCEFIELD", "TARGETFIELD", "TRANSFORMFIELD"}
PROPERTY_TAGS = {"ATTRIBUTE", "TABLEATTRIBUTE", "INITPROP", "VALUEPAIR", "METADATAEXTENSION"}
KNOWN_TAGS = set("""POWERMART REPOSITORY FOLDER SOURCE TARGET MAPPING MAPPLET
TRANSFORMATION INSTANCE SESSION WORKFLOW WORKLET TASK TASKINSTANCE SHORTCUT
SESSIONEXTENSION CONNECTIONREFERENCE CONNECTION CONFIG CONFIGREFERENCE
SESSIONCOMPONENT SESSTRANSFORMATIONINST SESSTRANSFORMATIONGROUP SCHEDULER
SCHEDULEINFO SCHEDULEOPTIONS STARTOPTIONS ENDOPTIONS RECURRING REPEAT
DAILYFREQUENCY CUSTOM FILTER TIMER FLATFILE ERPINFO GROUP PARTITION KEYRANGE
MAPPINGVARIABLE WORKFLOWVARIABLE TARGETLOADORDER ASSOCIATED_SOURCE_INSTANCE
FIELDDEPENDENCY CONNECTOR WORKFLOWLINK""".split()) | FIELD_TAGS | PROPERTY_TAGS
REDACTED = "[REDACTED]"
_CONNECTION_ENDPOINT = re.compile(
    r"://|\b(?:jdbc|odbc):|\b(?:dsn|server|host|database|uid|user\s*id)\s*=", re.I
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _id(*parts):
    return sha256(_json(parts).encode()).hexdigest()


def _safe_connection_alias(value):
    """Only literal identifiers or Informatica parameters, never endpoint strings."""
    return bool(re.fullmatch(r"(?:\$\$)?[A-Za-z_][A-Za-z0-9_.-]*", value)) and value != REDACTED


def _clean(attributes, tag):
    """Redact secret-bearing values, not whole transformation/session objects."""
    label = attributes.get("NAME", "")
    # Preserve the existing withholding of physical connection values.
    physical_property = re.sub(r"[ _-]", "", label).lower() in {"connectionstring", "connectstring"}
    sensitive_property = is_secret_key(label) or physical_property
    result = {}
    for key, value in sorted(attributes.items()):
        alias = (tag in {"CONNECTION", "CONNECTIONREFERENCE"}
                 and key in {"CONNECTIONNAME", "CONNECTIONVALUE"})
        alias |= (tag in PROPERTY_TAGS and key in {"VALUE", "DEFAULTVALUE"}
                  and re.sub(r"[ _-]", "", label).upper() in {"CONNECTIONNAME", "CONNECTIONVALUE"})
        if alias:
            result[key] = value if _safe_connection_alias(value) else REDACTED
            continue
        connection_value = tag in {"CONNECTION", "CONNECTIONREFERENCE"} and key not in {
            "NAME", "TYPE", "CONNECTIONTYPE", "CONNECTIONSUBTYPE", "CONNECTIONNUMBER", "CNXREFNAME"
        }
        hidden = is_secret_key(key) or re.sub(r"[ _-]", "", key).lower() in {"connectionstring", "connectstring"}
        hidden |= tag in {"CONNECTION", "CONNECTIONREFERENCE"} and bool(_CONNECTION_ENDPOINT.search(value))
        hidden |= key in {"VALUE", "DEFAULTVALUE"} and sensitive_property
        hidden |= connection_value
        result[key] = REDACTED if hidden else redact_text(value)
    return result


def to_networkx(model: dict):
    """Return the complete canonical multigraph, including unresolved nodes."""
    import networkx as nx

    graph = nx.MultiDiGraph()
    for node in model["nodes"]:
        graph.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in model["edges"]:
        graph.add_edge(edge["source"], edge["target"], key=edge["id"],
                       **{k: v for k, v in edge.items() if k not in {"id", "source", "target"}})
    return graph


def cycle_report(model: dict, limit: int = 20, member_limit: int = 50) -> dict:
    """Bounded SCC report of executable instance flows, never enumerate cycles."""
    import networkx as nx

    if limit < 0 or member_limit < 1:
        raise ValueError("Invalid SCC report bounds")
    graph = nx.DiGraph()
    for edge in model["edges"]:
        if edge["kind"] in {"workflow_control", "data_flow"}:
            graph.add_edge(edge["source"], edge["target"])
    components = []
    total = 0
    for members in nx.strongly_connected_components(graph):
        if len(members) > 1 or any(graph.has_edge(n, n) for n in members):
            total += 1
            components.append({"members": sorted(members)[:member_limit],
                               "size": len(members), "members_truncated": len(members) > member_limit})
            components.sort(key=lambda c: c["members"])
            del components[limit:]
    return {"component_count": total, "components": components, "truncated": total > limit}


def analyze_package(path: Path) -> dict:
    """Stream an Informatica export into a deterministic JSON-compatible model.

    Resolution is exact and case sensitive: enclosing definition first, then the
    same folder. Cross-folder/repository shortcuts require explicit qualifiers.
    Duplicates remain distinct; ambiguous or missing references become nodes.
    Parsing/security exceptions deliberately propagate rather than return partial
    output. Lifecycle and observed runtime behavior cannot be inferred from XML.
    """
    path = Path(path)
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    nodes, edges, diagnostics = [], [], []
    by_id = {}
    index = defaultdict(list)
    stack = []
    pending = []
    connectors = []
    links = []
    unsupported = Counter()
    xml_elements = 0

    def edge(source, target, kind, attributes, evidence):
        record = {"source": source, "target": target, "kind": kind,
                  "attributes": attributes, "evidence": evidence}
        record["id"] = _id("edge", record)
        edges.append(record)

    def add_node(kind, name, parent, scope, attributes, locator):
        node_id = _id("node", parent, kind, name, attributes, locator)
        node = {"id": node_id, "kind": kind, "name": name, "scope": dict(scope),
                "parent_id": parent, "attributes": attributes, "fields": [],
                "properties": [], "evidence": {"locator": locator}, "summary": ""}
        nodes.append(node)
        by_id[node_id] = node
        if parent:
            edge(parent, node_id, "contains", {}, node["evidence"])
        index[(parent, kind, name)].append(node_id)
        return node

    with path.open("rb") as stream:
        for event, elem in iterparse(stream, events=("start", "end"),
                                     forbid_dtd=False, forbid_entities=True, forbid_external=True):
            tag = elem.tag.rsplit("}", 1)[-1]
            if event == "start":
                xml_elements += 1
                parent_frame = stack[-1] if stack else None
                if parent_frame:
                    parent_frame["ordinals"][tag] += 1
                    ordinal = parent_frame["ordinals"][tag]
                    locator = parent_frame["locator"] + f"/{tag}[{ordinal}]"
                    scope = dict(parent_frame["scope"])
                    parent = parent_frame["owner"]
                else:
                    locator, scope, parent = f"/{tag}[1]", {}, None
                attrs = _clean(dict(elem.attrib), tag)
                frame = {"element": elem, "locator": locator, "scope": scope,
                         "owner": parent, "ordinals": Counter(), "tag": tag}
                evidence = {"locator": locator}
                if tag in FIELD_TAGS | PROPERTY_TAGS:
                    if parent:
                        record = {"kind": tag.lower(), "attributes": attrs, "evidence": evidence}
                        if (tag == "METADATAEXTENSION"
                                and elem.attrib.get("DOMAINNAME") == "Java_Transform_Domain"
                                and elem.attrib.get("NAME", "").endswith("_Snippet")):
                            from workbench.source_evidence import embedded_java
                            record["embedded_code"] = embedded_java(elem.attrib.get("VALUE", ""))
                        collection = "fields" if tag in FIELD_TAGS else "properties"
                        by_id[parent][collection].append(record)
                        frame["record"] = record
                elif tag == "CONNECTOR":
                    connectors.append((parent, attrs, evidence))
                elif tag == "WORKFLOWLINK":
                    links.append((parent, attrs, evidence))
                else:
                    if tag not in KNOWN_TAGS:
                        unsupported[tag] += 1
                    node = add_node(tag.lower(), attrs.get("NAME", attrs.get("SINSTANCENAME", tag)),
                                    parent, scope, attrs, locator)
                    frame["owner"] = node["id"]
                    if tag in {"REPOSITORY", "FOLDER"}:
                        scope[tag.lower()] = node["name"]
                        scope[tag.lower() + "_id"] = node["id"]
                    if tag in {"MAPPING", "MAPPLET", "WORKFLOW", "WORKLET", "SESSION"}:
                        scope["container_id"] = node["id"]
                    frame["scope"] = scope
                    node["scope"] = dict(scope)
                    if tag in {"INSTANCE", "SESSION", "TASKINSTANCE", "SHORTCUT", "CONFIGREFERENCE",
                               "SESSIONEXTENSION", "SESSTRANSFORMATIONINST", "SESSIONCOMPONENT", "WORKFLOW",
                               "TARGETLOADORDER", "ASSOCIATED_SOURCE_INSTANCE"}:
                        pending.append(node["id"])
                stack.append(frame)
            else:
                frame = stack.pop()
                text = (elem.text or "").strip()
                if text:
                    # Keep noncredential text; historical canonical values are not rebuilt.
                    secret_text = (is_secret_key(frame["tag"]) or is_secret_key(elem.attrib.get("NAME", ""))
                                   or frame["tag"] in {"CONNECTION", "CONNECTIONREFERENCE"})
                    text = REDACTED if secret_text else redact_text(text)
                    if "record" in frame:
                        frame["record"]["text"] = text
                    elif frame["owner"]:
                        by_id[frame["owner"]]["attributes"]["_text"] = text
                elem.clear()
                if stack:
                    stack[-1]["element"].remove(elem)

    def candidates(parent, kinds, name):
        return [n for kind in kinds for n in index.get((parent, kind, name), ())]

    def qualified_match(node, key, value):
        attributes = node["attributes"]
        actual = attributes.get(key)
        if key == "TYPE" and node["kind"] == "instance" and actual is None:
            actual = {"Source Definition": "SOURCE", "Target Definition": "TARGET"}.get(
                attributes.get("TRANSFORMATION_TYPE"))
        return actual == value

    def resolve(owner, name, kinds, relation, parents, evidence=None, reference_attrs=None, qualifiers=None):
        evidence = evidence or owner["evidence"]
        matches = []
        for parent in dict.fromkeys(parents):
            matches = candidates(parent, kinds, name)
            if qualifiers:
                # Literal XML qualifiers are constraints, not ranking hints.
                # Never choose the first same-named definition or substitute a
                # physical mock-table name for Informatica object identity.
                matches = [m for m in matches if all(
                    qualified_match(by_id[m], key, value) for key, value in qualifiers.items())]
            if owner["kind"] == "taskinstance" and "task" in kinds:
                task_type = owner["attributes"].get("TASKTYPE", "").lower()
                matches = [m for m in matches if by_id[m]["kind"] == "shortcut"
                           or by_id[m]["attributes"].get("TYPE", "").lower() == task_type]
            if matches:
                break
        if len(matches) == 1:
            target = matches[0]
        else:
            status = "ambiguous" if matches else "unresolved"
            ref = {"reference_name": name, "expected_kinds": sorted(kinds), "status": status,
                   "candidate_ids": sorted(matches), **(reference_attrs or {})}
            if qualifiers:
                ref["required_qualifiers"] = qualifiers
            target = add_node("unresolved_reference", name, owner["id"], owner["scope"], ref,
                              evidence["locator"] + "/reference/" + relation)["id"]
            diagnostics.append({"severity": "warning", "code": status.upper() + "_REFERENCE",
                                "message": f"{relation}: {status} {','.join(kinds)} reference {name!r}.",
                                "object_id": owner["id"]})
        edge(owner["id"], target, relation, reference_attrs or {}, evidence)
        return target

    def literal_qualifiers(attributes, keys):
        # Parameter expressions cannot be resolved from export metadata alone.
        return {key: attributes[key] for key in keys if attributes.get(key)
                and attributes[key] != REDACTED and "$" not in attributes[key]}

    def instance_type_qualifier(type_name):
        if not type_name:
            return {}
        return {"TRANSFORMATION_TYPE": type_name}

    definitions = {}
    session_mappings = {}
    reference_targets = {}
    # Definitions and mappings must resolve before session extensions and field flows.
    for node_id in pending:
        n = by_id[node_id]
        a, scope = n["attributes"], n["scope"]
        folder = scope.get("folder_id")
        if n["kind"] == "instance":
            type_name = a.get("TRANSFORMATION_TYPE", a.get("TYPE", "")).lower()
            kind = {"source definition": "source", "target definition": "target",
                    "source": "source", "target": "target", "mapplet": "mapplet"}.get(type_name, "transformation")
            definitions[node_id] = resolve(n, a.get("TRANSFORMATION_NAME", n["name"]),
                                           [kind, "shortcut"], "instance_of", [n["parent_id"], folder],
                                           qualifiers=literal_qualifiers(a, ("DBDNAME", "OWNERNAME")))
        elif n["kind"] == "session":
            session_mappings[node_id] = resolve(n, a.get("MAPPINGNAME", ""), ["mapping", "shortcut"],
                                                "session_mapping", [folder])

    for node_id in pending:
        n = by_id[node_id]
        a, scope = n["attributes"], n["scope"]
        folder = scope.get("folder_id")
        kind = n["kind"]
        if kind == "taskinstance":
            task_type = a.get("TASKTYPE", "").lower()
            kinds = [task_type] if task_type in {"session", "worklet", "workflow"} else ["task"]
            reference_targets[node_id] = resolve(n, a.get("TASKNAME", n["name"]), kinds + ["shortcut"], "task_definition",
                [n["parent_id"], folder])
        elif kind == "shortcut":
            repo = a.get("REFREPOSITORYNAME", scope.get("repository"))
            folder_name = a.get("REFFOLDERNAME", scope.get("folder"))
            folders = [x["id"] for x in nodes if x["kind"] == "folder"
                       and x["name"] == folder_name and x["scope"].get("repository") == repo]
            # Multiple matching qualified folders must remain ambiguous, not first-match.
            refkind = a.get("OBJECTSUBTYPE", a.get("REFERENCETYPE", "")).lower().replace(" ", "")
            refkind = {"sourcedefinition": "source", "targetdefinition": "target"}.get(refkind, refkind)
            kinds = [refkind] if refkind in {"source", "target", "mapping", "mapplet", "transformation", "session", "worklet", "task"} else ["unknown_definition"]
            if len(folders) == 1:
                parents = folders
            else:
                parents = []
            reference_targets[node_id] = resolve(
                n, a.get("REFOBJECTNAME", ""), kinds, "shortcut_reference", parents,
                reference_attrs={"repository": repo, "folder": folder_name})
        elif kind == "configreference":
            resolve(n, a.get("REFOBJECTNAME", ""), ["config"], "config_reference", [folder])
        elif kind == "sessioncomponent" and a.get("REUSABLE", "").upper() == "YES":
            resolve(n, a.get("REFOBJECTNAME", ""), ["task"], "component_reference", [folder])
        elif kind == "workflow" and a.get("SCHEDULERNAME"):
            resolve(n, a["SCHEDULERNAME"], ["scheduler"], "schedule_reference", [node_id, folder])
        # Session overrides are resolved after all task/shortcut definitions,
        # including forward references. A TASKINSTANCE is not a container_id.
        elif kind in {"targetloadorder", "associated_source_instance"}:
            resolve(n, a.get("TARGETINSTANCE", n["name"]), ["instance"], "instance_reference",
                    [scope.get("container_id")], qualifiers={"TYPE": "TARGET" if kind == "targetloadorder" else "SOURCE"})

    def session_mapping_owner(node):
        owner = by_id.get(node.get("parent_id"))
        ancestors = set()
        while owner and owner["id"] not in ancestors:
            ancestors.add(owner["id"])
            if owner["kind"] in {"session", "taskinstance"}:
                current, seen = owner["id"], set()
                while current and current not in seen:
                    seen.add(current)
                    if current in session_mappings:
                        current = session_mappings[current]
                    elif current in reference_targets:
                        current = reference_targets[current]
                    else:
                        return current if by_id[current]["kind"] == "mapping" else None
                return None
            owner = by_id.get(owner.get("parent_id"))
        return None

    for node_id in pending:
        n = by_id[node_id]
        a = n["attributes"]
        if n["kind"] in {"sessionextension", "sesstransformationinst"} and a.get("SINSTANCENAME"):
            mapping = session_mapping_owner(n)
            resolve(n, a["SINSTANCENAME"], ["instance"], "session_instance", [mapping] if mapping else [],
                    qualifiers=instance_type_qualifier(a.get("TRANSFORMATIONTYPE")))

    # Exported aliases are object identity, not physical bindings. Missing external
    # definitions are metadata gaps, not broken references or missing schemas.
    for n in list(nodes):
        if n["kind"] == "connectionreference":
            a, scope = n["attributes"], n["scope"]
            values = [a[key] for key in ("CONNECTIONNAME", "CONNECTIONVALUE") if a.get(key)]
            name = next((value for value in values if _safe_connection_alias(value)), None)
            parents = [n["parent_id"], scope.get("container_id"),
                       scope.get("folder_id"), scope.get("repository_id")]
            if name and not name.startswith("$$") and any(
                    candidates(parent, ["connection"], name) for parent in parents):
                resolve(n, name, ["connection"], "connection_reference", parents)
            else:
                a["status"] = "external" if name else "unknown"
                a["status_reason"] = ("external_connection_definition_not_exported" if name else
                                      "connection_value_redacted" if REDACTED in values else
                                      "connection_identifier_not_captured")

    ports = {}
    shortcut_targets = {e["source"]: e["target"] for e in edges if e["kind"] == "shortcut_reference"}

    def port(instance_id, field_name, evidence):
        key = (instance_id, field_name)
        if key in ports:
            return ports[key]
        instance = by_id[instance_id]
        definition = by_id.get(definitions.get(instance_id), {})
        visited = set()
        while definition.get("id") in shortcut_targets and definition["id"] not in visited:
            visited.add(definition["id"])
            definition = by_id[shortcut_targets[definition["id"]]]
        fields = [f for f in definition.get("fields", []) if f["attributes"].get("NAME") == field_name]
        attributes = {"definition_id": definition.get("id"), "status": "resolved" if len(fields) == 1 else "unresolved"}
        if len(fields) == 1:
            attributes["field"] = fields[0]["attributes"]
            attributes["definition_evidence"] = fields[0]["evidence"]
        else:
            diagnostics.append({"severity": "warning", "code": "UNRESOLVED_FIELD",
                                "message": f"Field {field_name!r} has no unique resolved definition.",
                                "object_id": instance_id})
        field_node = add_node("instance_field", field_name, instance_id, instance["scope"], attributes,
                              instance["evidence"]["locator"] + "/field/" + _id(field_name))
        ports[key] = field_node["id"]
        return field_node["id"]

    for parent, attrs, evidence in connectors:
        owner = by_id[parent]
        source = resolve(owner, attrs.get("FROMINSTANCE", ""), ["instance"], "connector_source", [parent], evidence,
                 qualifiers=instance_type_qualifier(attrs.get("FROMINSTANCETYPE")))
        target = resolve(owner, attrs.get("TOINSTANCE", ""), ["instance"], "connector_target", [parent], evidence,
                 qualifiers=instance_type_qualifier(attrs.get("TOINSTANCETYPE")))
        edge(source, target, "data_flow", attrs, evidence)
        edge(port(source, attrs.get("FROMFIELD", ""), evidence),
             port(target, attrs.get("TOFIELD", ""), evidence), "field_flow", attrs, evidence)
    for parent, attrs, evidence in links:
        owner = by_id[parent]
        source = resolve(owner, attrs.get("FROMTASK", ""), ["taskinstance"], "control_source", [parent], evidence)
        target = resolve(owner, attrs.get("TOTASK", ""), ["taskinstance"], "control_target", [parent], evidence)
        edge(source, target, "workflow_control", attrs, evidence)

    outgoing = Counter(e["source"] for e in edges if e["kind"] in {"data_flow", "workflow_control", "field_flow"})
    for n in nodes:
        roles = Counter(p["attributes"].get("NAME", p["kind"]) for p in n["properties"])
        role_text = ", ".join(f"{name} ({count})" for name, count in sorted(roles.items()))
        qualified = "/".join(str(n["scope"].get(k, "")) for k in ("repository", "folder"))
        n["summary"] = (f"{n['kind']} {n['name']!r} in {qualified}: {len(n['fields'])} fields, "
                        f"{len(n['properties'])} properties, {outgoing[n['id']]} outgoing flow edges."
                        + (f" Property roles: {role_text}." if roles else "")
                        + " Lifecycle is unknown; export metadata is not runtime evidence.")
    counts = dict(sorted(Counter(n["kind"] for n in nodes).items()))
    # Parallel identical references can have the same content ID; canonicalize them.
    canonical_edges = {e["id"]: e for e in edges}
    model = {"schema_version": "1.0", "package": {"name": path.name, "sha256": digest.hexdigest(), "size_bytes": size},
             "counts": counts, "nodes": sorted(nodes, key=lambda n: n["id"]),
             "edges": sorted(canonical_edges.values(), key=lambda e: e["id"]),
             "diagnostics": sorted(diagnostics, key=lambda d: (d["object_id"], d["code"], d["message"])),
             "summary": (f"The complete export contains {xml_elements} XML elements across {counts.get('repository', 0)} "
                         f"repositories and {counts.get('folder', 0)} folders: {counts.get('source', 0)} sources, "
                         f"{counts.get('target', 0)} targets, {counts.get('mapping', 0)} mappings, "
                         f"{counts.get('mapplet', 0)} mapplets, {counts.get('session', 0)} sessions, "
                         f"{counts.get('workflow', 0)} workflows and {counts.get('worklet', 0)} worklets. "
                         "Definitions, instances, field flows, SQL/property roles, schedules and original conditional "
                         "control links are retained, including disabled instances. "
                         f"There are {len(diagnostics)} reference/field diagnostics. Sensitive values are redacted. "
                         "This is structural understanding, not verified transformation semantic translation. "
                         "Lifecycle, activity, execution, ownership validation and deprecation remain unknown."),
             "coverage": {"xml_elements": xml_elements, "captured_objects": len(nodes),
                          "unsupported_tags": dict(sorted(unsupported.items())),
                          "semantic_status": "structural_only_not_semantically_translated",
                          "lifecycle_dimensions": {k: "unknown" for k in
                                                   ("lifecycle", "activity", "execution", "ownership", "deprecation")}}}
    model["coverage"]["cycles"] = cycle_report(model)
    return model


def mapping_fingerprints(model: dict) -> list[dict]:
    """Exact metadata candidates only, not equivalence or automatic merge advice.

    Conservative fingerprints retain names, SQL roles, expressions and qualifiers.
    Mapping names, locators and graph IDs are excluded. Redacted or unresolved
    mapping subtrees are ineligible because equality would not be evidence.
    """
    children = defaultdict(list)
    for n in model["nodes"]:
        children[n["parent_id"]].append(n)
    flows = defaultdict(list)
    lookup = {n["id"]: n for n in model["nodes"]}
    for e in model["edges"]:
        if e["kind"] == "data_flow":
            flows[lookup[e["source"]]["parent_id"]].append(e["attributes"])

    def tree(n):
        return {"kind": n["kind"], "name": n["name"], "attributes": n["attributes"],
                "fields": [{"kind": f["kind"], "attributes": f["attributes"]} for f in n["fields"]],
                "properties": [{"kind": p["kind"], "attributes": p["attributes"]} for p in n["properties"]],
                "children": sorted((tree(c) for c in children[n["id"]]
                                    if c["kind"] != "instance_field"), key=_json)}

    groups = defaultdict(list)
    referenced = defaultdict(list)
    for e in model["edges"]:
        if e["kind"] in {"instance_of", "shortcut_reference"}:
            referenced[e["source"]].append(e["target"])
    for n in model["nodes"]:
        if n["kind"] != "mapping":
            continue
        payload = tree(n)
        payload.pop("name")
        payload["scope"] = {k: n["scope"].get(k) for k in ("repository", "folder")}
        payload["attributes"] = {k: v for k, v in payload["attributes"].items() if k != "NAME"}
        payload["flows"] = sorted(flows[n["id"]], key=_json)
        # Include referenced definitions, not merely instance type/name strings.
        definitions = []
        todo = [c["id"] for c in children[n["id"]]]
        seen = set()
        unsafe = False
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            obj = lookup[current]
            unsafe |= obj["kind"] == "unresolved_reference"
            if current in referenced:
                for target in referenced[current]:
                    definitions.append(tree(lookup[target]))
                    todo.append(target)
            todo.extend(c["id"] for c in children[current])
        payload["definitions"] = sorted(definitions, key=_json)
        encoded = _json(payload)
        if unsafe or REDACTED in encoded or '"unresolved"' in encoded:
            continue
        groups[sha256(encoded.encode()).hexdigest()].append(n["id"])
    return [{"fingerprint": key, "mapping_ids": sorted(ids), "status": "candidate_only_not_semantic_equivalence"}
            for key, ids in sorted(groups.items()) if len(ids) > 1]