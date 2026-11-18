"""Source-port SQL graph compiler for the reviewed WIL unit, no execution.

The caller pins the complete canonical unit. SQL here is a review candidate,
never a runtime parity certificate. Missing ports/operators fail locally, rather
than substituting NULL, an empty frame, a fabricated key or a procedure name.
"""
from collections import defaultdict
from hashlib import sha256
import re

import networkx as nx
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class Unsupported(ValueError):
    pass


def ident(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise Unsupported("Unsupported identifier")
    return "`" + name + "`"


def properties(node):
    return {p["attributes"]["NAME"]: p["attributes"].get("VALUE", "")
            for p in node.get("properties", []) if "NAME" in p["attributes"]}


def fields(node):
    return [f["attributes"] for f in node.get("fields", [])]


def sql_candidate(text, *, query=False):
    """Bind scalar parameters, retain whole-table identity and date assumptions.

    No string substitution using user values. The resulting table tokens are
    explicit unresolved identifiers, not guessed catalog/schema names.
    """
    if not text or "[REDACTED]" in text:
        raise Unsupported("Missing or redacted SQL/expression")
    if re.search(r":(?:SP|LKP)\.|\bNEXTVAL\b", text, re.I):
        raise Unsupported("External procedure/lookup call or Oracle sequence allocation")
    notes = []
    if "[:alnum:]" in text:
        notes.append("ORACLE_POSIX_ALNUM_REGEX_REQUIRES_EQUIVALENCE_CONTRACT")
    if re.search(r"\bSYSDATE\b|\bTO_DATE\b", text, re.I):
        notes.append("ORACLE_CLOCK_DATE_EMPTY_STRING_AND_TIMEZONE_CONTRACT_REQUIRED")
    params = sorted(set(re.findall(r"\$\$([A-Za-z_][A-Za-z0-9_]*)", text)))
    # Schema variables must not become scalar arguments. Record exact table
    # spellings on the AST below, then require explicit whole-table bindings.
    text = re.sub(r"\$\$([A-Za-z_][A-Za-z0-9_]*)\.", r"infa_\1.", text)
    text = re.sub(r"TO_DATE\(\s*'?\$\$JOB_RUN_DATE'?\s*,\s*'mmddyyyy'\s*\)",
                  "CAST(:p_JOB_RUN_DATE AS DATE)", text, flags=re.I)
    text = re.sub(r"TRUNC\(\s*SYSDATE\s*\)", "CURRENT_DATE", text, flags=re.I)
    text = re.sub(r"\bSYSDATE\b", "CURRENT_TIMESTAMP", text, flags=re.I)
    text = re.sub(r"'\$\$([A-Za-z_][A-Za-z0-9_]*)'", r":p_\1", text)
    text = re.sub(r"\$\$([A-Za-z_][A-Za-z0-9_]*)", r":p_\1", text)
    if "$" in text:
        raise Unsupported("Unresolved non-scalar parameter or interpolation inside literal")
    # Informatica row expressions are not arbitrary Oracle function calls.
    text = re.sub(r"\bIIF\s*\(", "IF(", text, flags=re.I)
    text = re.sub(r"\bISNULL\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
                  r"(\1 IS NULL)", text, flags=re.I)
    try:
        trees = sqlglot.parse(text, read="oracle")
        if len(trees) != 1 or trees[0] is None:
            raise Unsupported("Exactly one statement/expression required")
        tree = trees[0]
    except ParseError as error:
        raise Unsupported("Source SQL cannot be parsed") from error
    if query and not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise Unsupported("Source must be a SELECT/set expression")
    if isinstance(tree, (exp.Commit, exp.Rollback, exp.Command)):
        raise Unsupported("Oracle transaction/opaque command cannot become an equivalent Delta statement")
    for call in tree.find_all(exp.Anonymous):
        if call.name.upper() not in {"IF"}:
            raise Unsupported("Unsupported function: " + call.name)
    tables = {}
    for table in list(tree.find_all(exp.Table)):
        original = table.sql(dialect="oracle")
        if table.name.upper() == "DUAL" and table.db.upper() in {"", "SYS"}:
            table.replace(exp.Subquery(this=exp.select(exp.Literal.number(1).as_("dummy")),
                                      alias=table.args.get("alias")))
            continue
        key = re.sub(r"\binfa_([A-Za-z_][A-Za-z0-9_]*)\.", r"$$\1.", original)
        # Aliases do not belong to a physical identity.
        key = key.split(" AS ")[0]
        if table.args.get("alias"):
            key = exp.Table(this=table.this, db=table.args.get("db"),
                            catalog=table.args.get("catalog")).sql(dialect="oracle")
            key = re.sub(r"\binfa_([A-Za-z_][A-Za-z0-9_]*)\.", r"$$\1.", key)
        token = "binding_" + sha256(key.encode()).hexdigest()[:16]
        tables[token] = key
        # Preserve unaliased source column qualification after replacing table.
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(token), alias=alias))
    scalar = [p for p in params if ":p_" + p in tree.sql(dialect="databricks")]
    return {"sql": tree.sql(dialect="databricks"), "tables": tables,
            "parameters": scalar, "semantic_requirements": sorted(set(notes))}


class MappingCompiler:
    def __init__(self, snapshot, mapping_id, session_id):
        self.nodes = {n["id"]: n for n in snapshot["nodes"]}
        self.edges = snapshot["edges"]
        self.mapping_id = mapping_id
        self.session_id = session_id
        self.instances = {n["id"]: n for n in snapshot["nodes"]
                          if n["kind"] == "instance" and n.get("parent_id") == mapping_id}
        self.definitions = {}
        for iid in self.instances:
            targets = [e["target"] for e in self.edges if e["source"] == iid and e["kind"] == "instance_of"]
            if len(targets) != 1:
                raise Unsupported("Instance needs exactly one canonical definition: " + iid)
            self.definitions[iid] = self.nodes[targets[0]]
        self.flows = [e for e in self.edges if e["kind"] == "data_flow"
                      and e["source"] in self.instances and e["target"] in self.instances]
        self.overrides = defaultdict(list)
        for n in snapshot["nodes"]:
            if n["kind"] == "sesstransformationinst" and n.get("parent_id") == session_id:
                self.overrides[(n["attributes"].get("SINSTANCENAME"),
                                n["attributes"].get("TRANSFORMATIONTYPE"))].append(n)
        self.rows, self.relations, self.ctes = [], {}, []
        self.lookup_fusions = {}
        self.lookup_output_aliases = {}
        self.tables, self.parameters, self.requirements = {}, set(), set()

    def effective(self, instance):
        base = properties(self.definitions[instance["id"]])
        owners = {key: self.definitions[instance["id"]]["id"] for key in base}
        overrides = self.overrides[(instance["name"], instance["attributes"]["TRANSFORMATION_TYPE"])]
        if len(overrides) > 1:
            raise Unsupported("Ambiguous scoped session override")
        if overrides:
            # An explicitly empty override is evidence too, not fallback SQL.
            for key, value in properties(overrides[0]).items():
                base[key], owners[key] = value, overrides[0]["id"]
        return base, owners

    def convert(self, text, **kwargs):
        candidate = sql_candidate(text, **kwargs)
        self.tables.update(candidate["tables"])
        self.parameters.update(candidate["parameters"])
        self.requirements.update(candidate["semantic_requirements"])
        return candidate["sql"]

    def cte(self, name, sql):
        key = "n_" + sha256((self.session_id + name).encode()).hexdigest()[:20]
        self.ctes.append((key, sql))
        return key

    def input_sql(self, iid):
        incoming = [e for e in self.flows if e["target"] == iid]
        groups = defaultdict(list)
        seen = set()
        replacements = {}
        for e in incoming:
            fusion = self.lookup_fusions.get(e["source"])
            if fusion:
                parent, relation, ports = fusion
                replacements[parent] = (relation, ports)
        for e in incoming:
            a = e["attributes"]
            if a["TOFIELD"] in seen:
                raise Unsupported("Multiple connectors to the same input port: " + a["TOFIELD"])
            seen.add(a["TOFIELD"])
            relation, ports = self.relations.get((e["source"], a["FROMFIELD"]),
                                                replacements.get(e["source"], self.relations.get(e["source"], (None, set()))))
            if a["FROMFIELD"] not in ports:
                raise Unsupported("Upstream port not compiled: " + a["FROMFIELD"])
            storage_port = self.lookup_output_aliases.get((e["source"], a["FROMFIELD"]), a["FROMFIELD"])
            groups[relation].append((storage_port, a["TOFIELD"]))
        if not groups:
            raise Unsupported("No row input connectors")
        projections, sources = [], []
        for index, (relation, links) in enumerate(sorted(groups.items())):
            alias = "s" + str(index)
            sources.append(ident(relation) + " " + alias)
            projections.extend(alias + "." + ident(src) + " AS " + ident(dst) for src, dst in links)
        return "SELECT " + ", ".join(projections) + " FROM " + " CROSS JOIN ".join(sources), seen, len(groups)

    def compile_instance(self, iid):
        instance, definition = self.instances[iid], self.definitions[iid]
        typ = instance["attributes"]["TRANSFORMATION_TYPE"]
        props, owners = self.effective(instance)
        fs = fields(definition)
        row = {"instance_id": iid, "definition_id": definition["id"], "name": instance["name"],
               "type": typ, "property_owners": owners, "source_properties": props,
               "ports": fs, "status": "blocked", "sql": None, "blockers": []}
        if typ == "Expression":
            row["port_candidates"] = []
            for f in fs:
                if not f.get("EXPRESSION"):
                    continue
                port = {"name": f["NAME"], "source_expression": f["EXPRESSION"], "ready": False}
                try:
                    port["candidate"] = sql_candidate(f["EXPRESSION"])
                    tree = sqlglot.parse_one(port["candidate"]["sql"], read="databricks")
                    port["required_ports"] = sorted({c.name for c in tree.find_all(exp.Column)})
                    port["status"] = "compiled_expression_candidate_requires_ports"
                except Unsupported as error:
                    port.update(status="blocked", blocker=str(error))
                row["port_candidates"].append(port)
        self.rows.append(row)
        try:
            used = {e["attributes"]["FROMFIELD"] for e in self.flows if e["source"] == iid}
            if typ == "Lookup Procedure" and not any(e["target"] == iid for e in self.flows):
                called = any(re.search(r":LKP\." + re.escape(instance["name"]) + r"\s*\(",
                            f.get("EXPRESSION", ""), re.I)
                            for d in self.definitions.values() for f in fields(d))
                if not called:
                    row["status"] = "unused_exported_definition_not_on_row_path"
                    return
            if typ == "Source Definition":
                row["status"] = "definition_only_not_an_operator"
                return
            if typ == "Stored Procedure":
                raise Unsupported("Exact external body required: " + props.get("Stored Procedure Name", ""))
            if typ == "Custom Transformation":
                raise Unsupported("Java core is compiled separately; session integration not complete")
            if typ == "Source Qualifier":
                raw = props.get("Sql Query", "")
                sql = self.convert(raw, query=True)
                tree = sqlglot.parse_one(sql, read="databricks")
                # Informatica override projection follows connected SQ output
                # ports, in exported port order, not unused definition fields.
                selected_fields = [f for f in fs if f["NAME"] in used]
                row["query_port_order"] = [f["NAME"] for f in selected_fields]
                if not isinstance(tree, exp.Select) or len(tree.expressions) != len(selected_fields):
                    raise Unsupported("Source projection/port arity mismatch: " + str(len(tree.expressions))
                                      + " SQL columns versus " + str(len(selected_fields)) + " connected ports")
                if any(isinstance(e, exp.Star) for e in tree.expressions):
                    raise Unsupported("Wildcard source query has no proven positional port contract")
                tree.set("expressions", [exp.alias_(e.this if isinstance(e, exp.Alias) else e,
                          f["NAME"], quoted=True) for e, f in zip(tree.expressions, selected_fields)])
                sql, ports = tree.sql(dialect="databricks"), {f["NAME"] for f in selected_fields}
            else:
                sql, ports, sources = self.input_sql(iid)
                inp = self.cte(iid + ":input", sql)
                if typ == "Joiner":
                    if sources != 2 or props.get("Join Type") != "Normal Join":
                        raise Unsupported("Only source-proven two-stream Normal Join is implemented")
                    if props.get("Case Sensitive String Comparison") != "YES":
                        raise Unsupported("Case-insensitive join collation needs a contract")
                    condition = self.convert(props["Join Condition"])
                    self.check_columns(condition, ports)
                    sql = "SELECT * FROM " + ident(inp) + " WHERE " + condition
                elif typ == "Filter":
                    if sources != 1:
                        raise Unsupported("Filter needs one input stream")
                    condition = self.convert(props["Filter Condition"])
                    self.check_columns(condition, ports)
                    sql = "SELECT * FROM " + ident(inp) + " WHERE " + condition
                elif typ == "Expression":
                    if sources != 1:
                        raise Unsupported("Expression needs one input stream")
                    current = inp
                    for ordinal, f in enumerate(fs):
                        if f.get("PORTTYPE") == "INPUT":
                            if f["NAME"] not in ports:
                                raise Unsupported("Unbound input: " + f["NAME"])
                            continue
                        expression = f.get("EXPRESSION") or f["NAME"]
                        if (expression == f["NAME"] and f["NAME"] not in ports and f["NAME"] not in used
                                and not any(re.search(r"\b" + re.escape(f["NAME"]) + r"\b", other.get("EXPRESSION", ""))
                                            for other in fs if other is not f)):
                            continue  # Unwired, unused identity port only, never an effectful expression.
                        if expression == f["NAME"] and f["NAME"] in ports:
                            continue
                        expression = self.convert(expression)
                        self.check_columns(expression, ports)
                        if f["NAME"] in ports:
                            raise Unsupported("Reassignment of an existing port is not implemented")
                        current = self.cte(iid + ":port:" + str(ordinal), "SELECT *, " + expression
                                           + " AS " + ident(f["NAME"]) + " FROM " + ident(current))
                        ports.add(f["NAME"])
                    outputs = [f["NAME"] for f in fs if "OUTPUT" in f.get("PORTTYPE", "") and f["NAME"] in ports]
                    if not set(outputs) <= ports:
                        raise Unsupported("Output ports unresolved")
                    sql = "SELECT " + ", ".join(map(ident, outputs)) + " FROM " + ident(current)
                    ports = set(outputs)
                elif typ == "Router":
                    if sources != 1:
                        raise Unsupported("Router needs one input stream")
                    groups = [n for n in self.nodes.values() if n["kind"] == "group"
                              and n.get("parent_id") == definition["id"] and "OUTPUT" in n["attributes"].get("TYPE", "")]
                    conditions = []
                    for group in groups:
                        if "DEFAULT" not in group["attributes"]["TYPE"]:
                            condition = self.convert(group["attributes"]["EXPRESSION"])
                            self.check_columns(condition, ports)
                            conditions.append(condition)
                    for group in groups:
                        gfs = [f for f in fs if f.get("GROUP") == group["name"]]
                        if "DEFAULT" in group["attributes"]["TYPE"]:
                            condition = "NOT (" + " OR ".join("COALESCE((" + c + "), FALSE)" for c in conditions) + ")"
                        else:
                            condition = self.convert(group["attributes"]["EXPRESSION"])
                        if any(f.get("REF_FIELD") not in ports for f in gfs):
                            raise Unsupported("Router REF_FIELD absent")
                        projection = ", ".join(ident(f["REF_FIELD"]) + " AS " + ident(f["NAME"]) for f in gfs)
                        rel = self.cte(iid + ":group:" + group["name"], "SELECT " + projection
                                       + " FROM " + ident(inp) + " WHERE " + condition)
                        for f in gfs:
                            self.relations[(iid, f["NAME"])] = (rel, {x["NAME"] for x in gfs})
                    row["status"], row["router_groups"] = "compiled_sql_candidate", groups
                    row["sql"] = "Independent matching output groups, not exclusive CASE branches"
                    return
                elif typ == "Lookup Procedure":
                    if sources != 1 or props.get("Lookup cache persistent") != "NO" or props.get("Dynamic Lookup Cache") != "NO":
                        raise Unsupported("Persistent/dynamic lookup state requires an implementation contract")
                    pairs = re.findall(r"\b([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\b", props.get("Lookup condition", ""))
                    outs = [f for f in fs if "LOOKUP" in f.get("PORTTYPE", "")]
                    # These numeric equality-key lookups return only their keys.
                    # DISTINCT preserves ANY VALUE without choosing a differing
                    # payload or multiplying input rows on duplicate keys.
                    if (not pairs or {f["NAME"] for f in outs} != {a for a, _ in pairs}
                            or any(f.get("DATATYPE") != "decimal" for f in outs)
                            or any(b not in ports for _, b in pairs)
                            or props.get("Lookup policy on multiple match") != "Use Any Value"
                            or props.get("Lookup Sql Override")):
                        raise Unsupported("Lookup ANY payload/collation/override requires exact implementation")
                    token = "binding_" + sha256(props["Lookup table name"].encode()).hexdigest()[:16]
                    self.tables[token] = props["Lookup table name"]
                    lookup = self.cte(iid + ":lookup", "SELECT DISTINCT " + ", ".join(ident(a) for a, _ in pairs)
                                      + " FROM " + ident(token))
                    incoming = [e for e in self.flows if e["target"] == iid]
                    parents = {e["source"] for e in incoming}
                    if len(parents) != 1:
                        raise Unsupported("Connected lookup requires a single input row lineage")
                    parent = next(iter(parents))
                    parent_rel, parent_ports = self.relations[parent]
                    aliases = {e["attributes"]["TOFIELD"]: e["attributes"]["FROMFIELD"] for e in incoming}
                    # Fuse lookup and its original row before fan-out reconverges.
                    # Joining two independent copies would cross-multiply rows.
                    lookup_input = self.cte(iid + ":correlated_input", "SELECT *, " + ", ".join(
                        ident(src) + " AS " + ident(dst) for dst, src in aliases.items()) + " FROM " + ident(parent_rel))
                    storage_names = {a: "lookup_" + a for a, _ in pairs}
                    if set(storage_names.values()).intersection(parent_ports | ports):
                        raise Unsupported("Lookup internal storage port collision")
                    sql = "SELECT i.*, " + ", ".join("l." + ident(a) + " AS " + ident(storage_names[a]) for a, _ in pairs)
                    sql += " FROM " + ident(lookup_input) + " i LEFT JOIN " + ident(lookup) + " l ON "
                    sql += " AND ".join("l." + ident(a) + " = i." + ident(b) for a, b in pairs)
                    ports.update(parent_ports)
                    ports.update(a for a, _ in pairs)
                    rel = self.cte(iid, sql)
                    self.relations[iid] = (rel, ports)
                    self.lookup_fusions[iid] = (parent, rel, ports)
                    self.lookup_output_aliases.update({(iid, a): storage_names[a] for a, _ in pairs})
                    self.requirements.add("STATIC_LOOKUP_SNAPSHOT_AND_SOURCE_FRESHNESS_CONTRACT_REQUIRED")
                    row["status"], row["sql"], row["relation"] = "compiled_sql_candidate", sql, rel
                    return
                elif typ == "Target Definition":
                    if sources != 1:
                        raise Unsupported("Target input needs one proven stream")
                    # No silent NULL defaulting for unconnected target columns.
                    mapped = [f for f in fs if f["NAME"] in ports]
                    sql = "SELECT " + ", ".join(ident(f["NAME"]) for f in mapped) + " FROM " + ident(inp)
                    row["target_definition"] = definition["attributes"]
                    row["target_format_properties"] = definition.get("properties", [])
                    row["target_format_children"] = [n for n in self.nodes.values() if n.get("parent_id") == definition["id"]]
                    row["connected_columns"] = [f["NAME"] for f in mapped]
                    row["datatype_contract"] = mapped
                    row["unconnected_target_ports"] = [f for f in fs if f["NAME"] not in ports]
                    row["write_operation"] = "INSERT_EXPLICIT_CONNECTED_COLUMNS"
                    row["blockers"].append("APPROVED_TARGET_TYPES_DEFAULTS_REJECTS_AND_SESSION_WRITER_REQUIRED")
                    self.requirements.add("TARGET_DATATYPE_PRECISION_DEFAULT_AND_ERROR_CONTRACT_REQUIRED")
                else:
                    raise Unsupported("Unsupported operator: " + typ)
            rel = self.cte(iid, sql)
            self.relations[iid] = (rel, ports)
            row["status"], row["sql"], row["relation"] = "compiled_sql_candidate", sql, rel
        except (Unsupported, KeyError) as error:
            row["blockers"].append(str(error))

    @staticmethod
    def check_columns(sql, ports):
        tree = sqlglot.parse_one(sql, read="databricks")
        missing = {c.name for c in tree.find_all(exp.Column) if c.name not in ports}
        if missing:
            raise Unsupported("Expression references unbound ports: " + ", ".join(sorted(missing)))

    def compile(self):
        graph = nx.DiGraph()
        graph.add_nodes_from(self.instances)
        graph.add_edges_from((e["source"], e["target"]) for e in self.flows)
        if not nx.is_directed_acyclic_graph(graph):
            raise Unsupported("Mapping row-flow cycle")
        for iid in nx.lexicographical_topological_sort(graph):
            self.compile_instance(iid)
        ctes = ",\n".join(ident(key) + " AS (\n" + sql + "\n)" for key, sql in self.ctes)
        targets = []
        for row in self.rows:
            if row["type"] == "Target Definition" and row["sql"]:
                target = {"instance_id": row["instance_id"], "target": row["target_definition"],
                    "select_sql": "WITH " + ctes + "\n" + row["sql"],
                    "connected_columns": row["connected_columns"], "datatype_contract": row["datatype_contract"],
                    "target_format_properties": row["target_format_properties"],
                    "target_format_children": row["target_format_children"],
                    "unconnected_target_ports": row["unconnected_target_ports"],
                    "ready": False}
                name = self.instances[row["instance_id"]]["name"]
                writers = [n for n in self.nodes.values() if n["kind"] == "sessionextension"
                           and n.get("parent_id") == self.session_id and n["attributes"].get("TYPE") == "WRITER"
                           and n["attributes"].get("SINSTANCENAME") == name]
                target["writer_evidence"] = writers
                source_mode = properties(self.nodes[self.session_id]).get("Treat source rows as")
                target["source_row_mode"] = source_mode
                if len(writers) == 1 and properties(writers[0]).get("Insert") == "YES" and source_mode == "Insert":
                    token = "target_" + row["instance_id"][:20]
                    target["target_binding_token"] = token
                    self.tables[token] = "canonical_target:" + row["definition_id"] + ":" + row["instance_id"]
                    target["insert_sql"] = "INSERT INTO " + ident(token) + " (" + ", ".join(map(ident, row["connected_columns"]))
                    target["insert_sql"] += ")\n" + target["select_sql"]
                    target["write_status"] = "compiled_insert_candidate_not_executed"
                else:
                    target["write_status"] = "writer_or_file_output_contract_not_implemented"
                targets.append(target)
        return {"mapping_id": self.mapping_id, "session_id": self.session_id,
                "operators": self.rows, "target_candidates": targets, "table_tokens": self.tables,
                "required_scalar_parameters": sorted(self.parameters),
                "semantic_requirements": sorted(self.requirements), "ready": False,
                "output_parity": "not_established", "execution_status": "not_run"}