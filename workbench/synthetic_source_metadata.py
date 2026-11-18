"""Separate, conservative original-XML evidence extraction for synthetic fixtures.

No changes to source planning or canonical analysis. CONNECTOR endpoints are
traced only through provable same-port/bare-column passthroughs. SQL is evidence,
never executable input. Raw SQL, Java, parameters and credentials are not saved.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import re

from defusedxml.ElementTree import iterparse
import sqlglot
from sqlglot import exp


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def sensitive(name):
    return bool(re.search(r"password|passwd|secret|token|credential|\buname\b|authorization", name, re.I))


def extract_metadata(path, tables):
    """Return key hints, unambiguous literal observations, joins and settings.

    Complete source identities remain authoritative. Ambiguous source names,
    shortcuts, computed expressions and unresolved parameters are withheld.
    Confidence measures evidence strength, not probability of business parity.
    """
    source_index = defaultdict(list)
    columns = {}
    for index, table in enumerate(tables):
        identity = table["xml_identity"]
        source_index[(identity[0], identity[1], identity[-1].casefold())].append(index)
        for column in table["columns"]:
            columns[(index, column["name"].casefold())] = column
    result = {"keys": [], "relationships": [], "literals": [], "counter_rows": [],
              "dependency_settings": [], "withheld": defaultdict(int)}
    stack = []
    retained = {"SOURCE", "MAPPING", "SESSION"}

    def scope():
        return {kind: attrs.get("NAME", "") for kind, attrs in stack
                if kind in {"REPOSITORY", "FOLDER", "WORKFLOW", "MAPPING", "SESSION"}}

    def resolve(name, context, owner="", db=""):
        candidates = source_index.get((context.get("REPOSITORY", ""), context.get("FOLDER", ""), name.casefold()), [])
        if owner:
            candidates = [i for i in candidates if tables[i]["xml_identity"][4].casefold() == owner.casefold()]
        if db:
            candidates = [i for i in candidates if tables[i]["xml_identity"][3].casefold() == db.casefold()]
        return candidates[0] if len(candidates) == 1 else None

    def query_evidence(text, context, label):
        if not text or len(text) > 200000:
            return
        fingerprint = sha256(text.encode()).hexdigest()
        # SQL with parameters is not rewritten into fake physical bindings.
        # A simple parameterized FROM can still identify a unique XML table;
        # only SELECT literal aliases are captured in that restricted case.
        parameterized = "$" in text
        parse_text = re.sub(r"\$\$[A-Za-z_][A-Za-z0-9_]*\.", "", text) if parameterized else text
        try:
            trees = sqlglot.parse(parse_text, read="oracle", error_level="RAISE")
        except Exception:
            result["withheld"]["unparsed_sql"] += 1
            return
        for tree in trees:
            if not isinstance(tree, exp.Select):
                continue
            references = {}
            for reference in tree.find_all(exp.Table):
                index = resolve(reference.name, context, owner=reference.db if not parameterized else "")
                if index is not None:
                    references[reference.alias_or_name.casefold()] = index
                    references[reference.name.casefold()] = index
            evidence = {"scope": context, "object": label, "sql_sha256": fingerprint}
            # WIL counter values come from SELECT aliases in actual overrides,
            # never from the destructive post-SQL or a made-up threshold.
            literal_row = {}
            for selection in tree.expressions:
                if isinstance(selection, exp.Alias) and isinstance(selection.this, exp.Literal):
                    literal_row[selection.alias.casefold()] = selection.this.this
            if {"template_id", "counter"} <= literal_row.keys():
                for index in sorted(set(references.values())):
                    if tables[index]["table"] == "ae2e_job_dep_chk_count":
                        result["counter_rows"].append({"table_index": index, "values": literal_row,
                            "confidence": 1.0, "evidence": evidence})
            if parameterized:
                result["withheld"]["parameterized_status_sql"] += 1
                continue
            for comparison in tree.find_all(exp.EQ, exp.In):
                column = comparison.this
                if not isinstance(column, exp.Column) or sensitive(column.name):
                    continue
                if column.table:
                    index = references.get(column.table.casefold())
                else:
                    candidates = {i for i in references.values() if (i, column.name.casefold()) in columns}
                    index = next(iter(candidates)) if len(candidates) == 1 else None
                if index is None or (index, column.name.casefold()) not in columns:
                    continue
                values = comparison.expressions if isinstance(comparison, exp.In) else [comparison.expression]
                for literal in values:
                    if isinstance(literal, exp.Literal) and re.fullmatch(r"[A-Za-z0-9_ .:/+-]{1,100}", literal.this):
                        result["literals"].append({"table_index": index, "column": column.name.casefold(),
                            "value": literal.this, "confidence": 0.85, "evidence": evidence})

    def mapping_evidence(element, context):
        instances = {c.attrib["NAME"]: c.attrib for c in element if tag(c) == "INSTANCE"}
        transforms = {c.attrib["NAME"]: c for c in element if tag(c) == "TRANSFORMATION"}
        incoming = defaultdict(list)
        for child in element:
            if tag(child) == "CONNECTOR":
                a = child.attrib
                incoming[(a["TOINSTANCE"], a["TOFIELD"].casefold())].append((a["FROMINSTANCE"], a["FROMFIELD"].casefold()))

        def origins(instance, field, visited=frozenset()):
            endpoint = (instance, field)
            if endpoint in visited:
                return set()
            attrs = instances.get(instance, {})
            if attrs.get("TYPE") == "SOURCE":
                index = resolve(attrs.get("TRANSFORMATION_NAME", ""), context, db=attrs.get("DBDNAME", ""))
                return {(index, field)} if (index, field) in columns else set()
            transformation = transforms.get(attrs.get("TRANSFORMATION_NAME", instance))
            if transformation is None:
                return set()
            properties = {c.attrib.get("NAME", ""): c.attrib.get("VALUE", "")
                          for c in transformation if tag(c) == "TABLEATTRIBUTE"}
            if properties.get("Sql Query", "").strip():
                return set()
            fields = {c.attrib.get("NAME", "").casefold(): c.attrib
                      for c in transformation if tag(c) == "TRANSFORMFIELD"}
            port = fields.get(field, {})
            expression = port.get("EXPRESSION", "").strip()
            if expression:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
                    return set()
                field = expression.casefold()
            elif attrs.get("TRANSFORMATION_TYPE") not in {"Source Qualifier", "Joiner", "Lookup Procedure", "Expression", "Filter", "Router", "Sorter"}:
                return set()
            values = set()
            for upstream in incoming.get((instance, field), []):
                values.update(origins(upstream[0], upstream[1], visited=visited | {endpoint}))
            return values

        for instance, attrs in instances.items():
            transformation = transforms.get(attrs.get("TRANSFORMATION_NAME", instance))
            if transformation is None:
                continue
            for child in transformation:
                if tag(child) != "TABLEATTRIBUTE":
                    continue
                name, value = child.attrib.get("NAME", ""), child.attrib.get("VALUE", "")
                if name in {"Sql Query", "Lookup Sql Override"}:
                    query_evidence(value, context, instance + ":" + name)
                if name not in {"Join Condition", "User Defined Join", "Lookup condition"} or not value:
                    continue
                try:
                    predicate = sqlglot.parse_one(value, read="oracle")
                except Exception:
                    result["withheld"]["unparsed_join"] += 1
                    continue
                for equality in predicate.find_all(exp.EQ):
                    if not isinstance(equality.this, exp.Column) or not isinstance(equality.expression, exp.Column):
                        continue
                    left = origins(instance, equality.this.name.casefold())
                    right = origins(instance, equality.expression.name.casefold())
                    if len(left) == len(right) == 1 and left != right:
                        result["relationships"].append({"left": list(next(iter(left))), "right": list(next(iter(right))),
                            "kind": "connector_traced_equality", "confidence": 0.8,
                            "key_aliases": [equality.this.name, equality.expression.name],
                            "evidence": {"scope": context, "instance": instance,
                                         "predicate_sha256": sha256(value.encode()).hexdigest()}})
                    else:
                        result["withheld"]["ambiguous_or_computed_join"] += 1

    for event, element in iterparse(path, events=("start", "end"), forbid_entities=True, forbid_external=True):
        kind = tag(element)
        if event == "start":
            stack.append((kind, dict(element.attrib)))
            continue
        context = scope()
        if kind == "SOURCE":
            index = resolve(element.attrib["NAME"], context, owner=element.attrib.get("OWNERNAME", ""),
                            db=element.attrib.get("DBDNAME", ""))
            if index is not None:
                for field in element:
                    if tag(field) == "SOURCEFIELD" and field.attrib.get("KEYTYPE", "NOT A KEY") != "NOT A KEY":
                        result["keys"].append({"table_index": index, "column": field.attrib["NAME"].casefold(),
                            "xml_keytype": field.attrib["KEYTYPE"], "confidence": 0.6})
        elif kind == "MAPPING":
            mapping_evidence(element, context)
            for setting in result["dependency_settings"]:
                if setting["scope"] != context:
                    continue
                setting["max_wait_count_literals"] = sorted({int(c.attrib["EXPRESSION"])
                    for c in element.iter() if tag(c) == "TRANSFORMFIELD"
                    and c.attrib.get("NAME") == "MAX_WAIT_COUNT"
                    and re.fullmatch(r"[0-9]{1,6}", c.attrib.get("EXPRESSION", ""))})
                setting["unresolved_parameter_names"] = sorted({parameter
                    for c in element.iter() if tag(c) == "TRANSFORMFIELD" and not sensitive(c.attrib.get("NAME", ""))
                    for parameter in re.findall(r"\$\$[A-Za-z_][A-Za-z0-9_]*", c.attrib.get("EXPRESSION", ""))
                    if not sensitive(parameter) and parameter not in {"$$HOST", "$$PORT_NUMBER", "$$SNAME"}})
        elif kind == "ATTRIBUTE" and element.attrib.get("NAME") in {"Sql Query", "Source Filter"}:
            query_evidence(element.attrib.get("VALUE", ""), context, context.get("SESSION", "") + ":override")
        elif kind == "METADATAEXTENSION" and element.attrib.get("NAME") == "OnInputRow_Method_Snippet":
            value = element.attrib.get("VALUE", "")
            if "depends_on_template_id" in value.lower():
                names = sorted(set(re.findall(r"\bae2e_[a-z_]+\b", value.lower())))
                # Store only safe literal table/column tokens and hashes. Never
                # save the JDBC text, host, user, password or executable Java.
                names = [name for name in names if name in {"ae2e_template_job_dependency", "ae2e_template", "ae2e_unix_job_log"}]
                result["dependency_settings"].append({"scope": context, "java_sha256": sha256(value.encode()).hexdigest(),
                    "table_names": names, "column_tokens": sorted(set(re.findall(
                        r"\b(?:depends_on_template_id|template_id|template_name|run_date|end_time)\b", value.lower()))),
                    "sleep_milliseconds_literals": sorted({int(n) for n in re.findall(
                        r"Thread\.sleep\s*\(\s*([0-9]+)\s*\)", value)}),
                    "resolved_fixture_indices": {name: resolve(name, context) for name in names},
                    "parameters_resolved": False, "parity": "not_established"})
        if not any(parent in retained for parent, _ in stack[:-1]):
            element.clear()
        stack.pop()
    result["withheld"] = dict(sorted(result["withheld"].items()))
    return result