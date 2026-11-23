"""Evidence-derived workflow explanations shared by the UI and agent tools.

No model invocation, code generation, lifecycle inference or name-based binding.
The caller supplies canonical indexes; traversal is bounded and disclosed.
"""
from collections import defaultdict, deque
import re

from sqlglot.errors import TokenError
from sqlglot.tokens import Tokenizer, TokenType

from workbench.domain import _RELATIONS, _redact
from workbench.source_evidence import _pmcmd


def explain_workflow(nodes, outgoing, workflow_id, *, limit=100):
    """Explain captured workflow behavior, never assert semantic equivalence.

    outgoing maps node IDs to canonical edge records, not just target IDs.
    Returned evidence_ids are actual canonical nodes/edges read by this tool.
    """
    root = nodes.get(workflow_id)
    if not root or root["kind"] != "workflow":
        raise ValueError("A canonical workflow ID is required")
    visited, queue = {workflow_id}, deque([workflow_id])
    while queue and len(visited) < 5000:
        for edge in outgoing.get(queue.popleft(), ()):
            target = edge["target"]
            if edge["kind"] not in _RELATIONS or target not in nodes or target in visited:
                continue
            if nodes[target]["kind"] == "workflow":
                continue  # Called workflows are separate units, not owned children.
            if len(visited) >= 5000:
                break
            visited.add(target)
            queue.append(target)
    evidence = {workflow_id}
    sections = []

    def section(title, rows):
        sections.append({"title": title, "items": rows})

    def properties(node):
        # Preserve evidence ordering; property names alone do not describe behavior.
        return [p.get("attributes", {}) for p in node.get("properties", [])]

    def describe_settings(attributes):
        labels = {"SCHEDULETYPE": "Launch mode", "STARTDATE": "Start date", "STARTTIME": "Start time",
                  "ENDTYPE": "End rule", "ENDAFTER": "Runs before ending", "RUNFOREVER": "Run indefinitely",
                  "INTERVAL": "Repeat interval", "TYPE": "Repeat unit", "RUNONCE": "Once per scheduled day",
                  "DAY": "Scheduled day", "DAYS": "Days", "HOURS": "Hours", "MINUTES": "Minutes",
                  "TIMERTYPE": "Wait rule"}
        return "; ".join(f'{labels.get(key, key.replace("_", " ").capitalize())}: {str(value).replace("_", " ")}'
                         for key, value in attributes.items() if value != "")

    # Follow the resolved scheduler, not every recurrence beneath the workflow:
    # task-relative timers are waits, not workflow launch frequencies.
    schedule_kinds = {"scheduleinfo", "scheduleoptions", "startoptions", "endoptions",
                      "recurring", "repeat", "dailyfrequency", "custom", "filter"}
    schedulers = [nodes[e["target"]] for e in outgoing.get(workflow_id, ())
                  if e["kind"] == "schedule_reference" and e["target"] in nodes]
    cadence_rows, cadence_records, cadence_seen = [], [], set()
    pending_schedule = deque(n["id"] for n in schedulers)
    while pending_schedule and len(cadence_seen) < 200:
        current = pending_schedule.popleft()
        if current in cadence_seen:
            continue
        cadence_seen.add(current)
        node = nodes[current]
        evidence.add(current)
        if node["kind"] == "unresolved_reference":
            cadence_rows.append("Referenced scheduler is unresolved. Do not infer cadence from its name.")
        else:
            attrs = node.get("attributes", {})
            cadence_records.append({"id": current, "kind": node["kind"], "attributes": attrs})
            if node["kind"] != "scheduler" and attrs:
                cadence_rows.append(describe_settings(attrs) + ".")
            for edge in outgoing.get(current, ()):
                if edge["kind"] == "contains" and edge["target"] in nodes and nodes[edge["target"]]["kind"] in schedule_kinds:
                    pending_schedule.append(edge["target"])
    if not schedulers:
        cadence_rows.append("No resolved workflow scheduler was returned. External/manual invocation remains possible.")
    schedule_types = sorted({r["attributes"]["SCHEDULETYPE"] for r in cadence_records
                             if r["attributes"].get("SCHEDULETYPE")})
    if "ONDEMAND" in schedule_types:
        cadence_rows.append("On-demand launch in the export. This does not exclude invocation by another workflow or an external scheduler.")
    cadence_rows.extend([
        "Task-relative timers are waits inside a workflow, not evidence that the workflow launches at that interval.",
        "Timezone, daylight-saving policy and current scheduler activation require explicit confirmation before generating an executable replacement schedule.",
        f'Workflow enabled in export: {root.get("attributes", {}).get("ISENABLED", "unspecified")}. This is not observed operational activity.',
    ])
    section("Pipeline launch cadence and scheduling evidence", cadence_rows)

    tasks = [nodes[e["target"]] for e in outgoing.get(workflow_id, ())
             if e["kind"] == "contains" and e["target"] in nodes
             and nodes[e["target"]]["kind"] == "taskinstance"]
    task_ids = {t["id"] for t in tasks}
    rows = []
    for task in tasks[:limit]:
        evidence.add(task["id"])
        attrs = task.get("attributes", {})
        rows.append(f'{task["name"]}: {attrs.get("TASKTYPE", "unspecified task type")}; '
                    f'enabled in export: {attrs.get("ISENABLED", "unspecified")}.')
    section("Tasks captured in the export (definition order, not execution order)", rows)

    links = [e for source in task_ids for e in outgoing.get(source, ())
             if e["kind"] == "workflow_control" and e["target"] in task_ids]
    links.sort(key=lambda e: (nodes[e["source"]]["name"], nodes[e["target"]]["name"], e["id"]))
    rows = []
    for edge in links[:limit]:
        evidence.update([edge["id"], edge["source"], edge["target"]])
        condition = edge.get("attributes", {}).get("CONDITION", "")
        rows.append(f'{nodes[edge["source"]]["name"]} → {nodes[edge["target"]]["name"]}: '
                    + (f'only when {condition}.' if condition else 'no condition text recorded on this link.'))
    section("Execution links and exact branch conditions", rows)

    # Lead with observable behavior rather than inferring business intent from names.
    overview = [f'{root["name"]} contains {len(tasks)} captured task instances and {len(links)} control links. '
                'The steps below describe the export, not observed production execution.']
    for task in tasks[:limit]:
        definitions = [nodes[e["target"]] for e in outgoing.get(task["id"], ())
                       if e["kind"] == "task_definition" and e["target"] in nodes]
        for definition in definitions[:4]:
            if definition["kind"] == "unresolved_reference":
                continue
            evidence.update((task["id"], definition["id"]))
            kind = attrs_type(task).lower()
            if kind == "session":
                mapping = definition.get("attributes", {}).get("MAPPINGNAME")
                if mapping:
                    overview.append(f'{task["name"]} runs the {mapping} mapping. Inspect its source, '
                                    'transformations, target and session overrides for the data-processing logic.')
            if kind == "command":
                for prop in properties(definition)[:40]:
                    command = prop.get("VALUE", "")
                    parsed = _pmcmd(command) if isinstance(command, str) else {}
                    if parsed:
                        mode = parsed["wait_mode"]
                        completion = ("without waiting for it to finish" if mode == "nowait" else
                                      "with a wait requested; runtime completion is not verified" if mode == "wait" else
                                      "with completion behavior not established")
                        overview.append(f'{task["name"]} requests {parsed["workflow_name"]} in folder '
                                        f'{parsed["folder"]}, {completion}. The called workflow is a separate scope.')
            if kind == "email":
                subject = next((p.get("VALUE") for p in properties(definition)
                                if p.get("NAME", "").lower() == "email subject" and p.get("VALUE")), None)
                if subject:
                    overview.append(f'{task["name"]} sends an email with the captured subject: {subject[:500]}. '
                                    'Use the message and branch condition, not the task name, to interpret its purpose.')
            if kind == "timer":
                pending = deque([definition["id"]]); seen = set()
                while pending and len(seen) < 30:
                    current = pending.popleft()
                    if current in seen:
                        continue
                    seen.add(current)
                    detail = nodes[current]
                    if detail["kind"] == "recurring":
                        values = detail.get("attributes", {})
                        parts = [f'{values[key]} {label}' for key, label in
                                 (("DAYS", "days"), ("HOURS", "hours"), ("MINUTES", "minutes"))
                                 if str(values.get(key, "")).isdigit() and int(values[key]) > 0]
                        if parts:
                            evidence.add(current)
                            overview.append(f'{task["name"]} has a captured wait of {", ".join(parts)}. '
                                            'This is a task wait, not a workflow schedule or proof of a polling loop.')
                    pending.extend(e["target"] for e in outgoing.get(current, ())
                                   if e["kind"] == "contains" and e["target"] in nodes)
    for edge in links[:limit]:
        condition = edge.get("attributes", {}).get("CONDITION", "")
        if not isinstance(condition, str):
            continue
        match = re.fullmatch(r'\s*\$([\w.]+)\.TgtSuccessRows\s*(<=|>=|<|>|=)\s*(\d+)\s*', condition, re.I)
        if match:
            relation = {"<": "fewer than", "<=": "at most", ">": "more than",
                        ">=": "at least", "=": "exactly"}[match.group(2)]
            overview.append(f'After {nodes[edge["source"]]["name"]}, {nodes[edge["target"]]["name"]} '
                            f'is selected when {match.group(1)} reports {relation} {match.group(3)} '
                            'successful target rows. This count is not automatically a source-readiness check.')
    sections.insert(0, {"title": "What this workflow does", "items": overview})

    code_rows = []
    descriptions = {"SELECT": "reads or calculates values", "WITH": "defines a query",
                    "INSERT": "inserts rows", "UPDATE": "updates rows", "DELETE": "deletes rows",
                    "MERGE": "merges rows", "CALL": "calls a procedure", "TRUNCATE": "truncates data",
                    "COMMIT": "commits changes"}
    for identifier in sorted(visited):
        node = nodes[identifier]
        for prop in node.get("properties", []):
            attrs = prop.get("attributes", {})
            name, value = attrs.get("NAME", ""), attrs.get("VALUE", "")
            if not isinstance(name, str) or not isinstance(value, str) or not value.strip():
                continue
            if "sql" in name.lower() and node["kind"] in {"transformation", "sesstransformationinst", "session"}:
                try:
                    tokens = Tokenizer().tokenize(value)
                    starts = [token.text.upper() for i, token in enumerate(tokens)
                              if i == 0 or tokens[i - 1].token_type == TokenType.SEMICOLON]
                    actions = [descriptions[start] for start in starts if start in descriptions]
                except TokenError:
                    actions = []
                action = ", then ".join(actions) if actions else "contains SQL whose operation needs review"
                scope = "session-specific setting" if node["kind"] in {"sesstransformationinst", "session"} else "mapping definition"
                code_rows.append(f'{node["name"]}: {name} in the {scope} {action}. '
                                 'This is a lexical summary, not execution or a complete procedural interpretation. '
                                 'Review the complete statement, predicates, parameters and any subsequent statements; '
                                 'a session setting can override the mapping definition.')
                evidence.add(identifier)
            if prop.get("embedded_code"):
                code_rows.append(f'{node["name"]}: {name} contains embedded code. Review its sanitized source '
                                 'and control flow; its behavior is not established by its presence alone.')
                evidence.add(identifier)
    if not code_rows:
        code_rows.append('No supported SQL or embedded-code properties were found in the inspected workflow scope. '
                         'Called workflows, external scripts and procedures require separate review.')
    sections.insert(1, {"title": "What the captured code does", "items": code_rows[:limit]})

    rows = []
    for task in tasks[:limit]:
        definitions = [nodes[e["target"]] for e in outgoing.get(task["id"], ())
                       if e["kind"] == "task_definition" and e["target"] in nodes]
        if not definitions:
            rows.append(f'{task["name"]}: task definition not resolved; implementation cannot be inferred.')
        for definition in definitions[:4]:
            evidence.add(definition["id"])
            if definition["kind"] == "unresolved_reference":
                rows.append(f'{task["name"]}: unresolved definition; additional export evidence is needed.')
                continue
            mapping = definition.get("attributes", {}).get("MAPPINGNAME")
            if mapping:
                rows.append(f'{task["name"]} references mapping {mapping}. Its source/target and transformation contracts must be inspected separately.')
            detail_nodes = [definition]
            # Timer settings and session components can be nested XML records.
            detail_nodes.extend(nodes[e["target"]] for e in outgoing.get(definition["id"], ())
                                if e["kind"] == "contains" and e["target"] in nodes)
            for detail in detail_nodes[:20]:
                if detail is not definition and detail["kind"] in {"timer", "scheduleoptions"}:
                    evidence.add(detail["id"])
                    rows.append(f'{task["name"]}: {describe_settings(detail.get("attributes", {}))}.')
                for prop in properties(detail)[:40]:
                    value = prop.get("VALUE", "")
                    name = prop.get("NAME", "Property")
                    if value and (attrs_type(task) in {"Command", "Email", "Timer"}
                                  or name in {"Parameter Filename", "Treat source rows as", "Commit Type", "Recovery Strategy"}):
                        evidence.add(detail["id"])
                        rows.append(f'{task["name"]} / {name}: {value[:1800]}')
    details_truncated = len(rows) > limit
    section("What the tasks actually reference or execute", rows[:limit])

    mapping_nodes = sorted((nodes[i] for i in visited if nodes[i]["kind"] == "mapping"), key=lambda n: n["id"])
    contracts = sorted((nodes[i] for i in visited if nodes[i]["kind"] in {"source", "target"}), key=lambda n: n["id"])
    rows = []
    for node in (mapping_nodes + contracts)[:limit]:
        evidence.add(node["id"])
        if node["kind"] == "mapping":
            rows.append(f'Mapping: {node["name"]}.')
        else:
            fields = node.get("fields", [])
            names = [f'{f.get("attributes", {}).get("NAME", "unnamed")} ({f.get("attributes", {}).get("DATATYPE", "type unspecified")})' for f in fields[:8]]
            rows.append(f'{node["kind"].capitalize()}: {node["name"]}; {len(fields)} captured fields. '
                        + ", ".join(names) + (". More fields available in canonical evidence." if len(fields) > 8 else "."))
    section("Referenced mappings and captured source/target field metadata", rows)
    section("How this informs a Lakeflow proposal, not generated code", [
        "Review dependency links, exact predicates, task failure/skip settings and timer behavior before selecting Lakeflow Jobs tasks.",
        "An Informatica command is not automatically a notebook. Inspect actual command text. A -nowait invocation must not silently become a blocking child-job dependency.",
        "Task names are labels, not business intent. Email contents and branch predicates can contradict names such as SUCCESS.",
        "Compare equivalent logic, parameter bindings, schedules, privileges and recovery boundaries before proposing shared code or parameterized jobs. Shared names or fingerprints do not prove equivalence.",
    ])
    unresolved_nodes = [nodes[i] for i in visited if nodes[i]["kind"] == "unresolved_reference"]
    redacted_connections = [n for n in unresolved_nodes
                            if n.get("attributes", {}).get("status_reason") == "connection_value_redacted"
                            and (n.get("attributes", {}).get("reference_name") == "[REDACTED]"
                                 or n.get("name") == "[REDACTED]")
                            and not any(value and value != "[REDACTED]" for value in (
                                nodes.get(n.get("parent_id"), {}).get("attributes", {}).get(key)
                                for key in ("CONNECTIONNAME", "CONNECTIONVALUE")))]
    other_unresolved = len(unresolved_nodes) - len(redacted_connections)
    redacted_reference_ids = {n.get("parent_id") or n["id"] for n in redacted_connections}
    redacted_reference_ids.update(i for i in visited if nodes[i]["kind"] == "connectionreference"
        and nodes[i].get("attributes", {}).get("status_reason") == "connection_value_redacted"
        and "[REDACTED]" in (nodes[i].get("attributes", {}).get("CONNECTIONNAME"),
                             nodes[i].get("attributes", {}).get("CONNECTIONVALUE"))
        and not any(value and value != "[REDACTED]" for value in (
            nodes[i].get("attributes", {}).get(key) for key in ("CONNECTIONNAME", "CONNECTIONVALUE"))))
    external_connections = [nodes[i] for i in visited if nodes[i]["kind"] == "connectionreference"
                            and nodes[i].get("attributes", {}).get("status_reason")
                            == "external_connection_definition_not_exported"]
    gap_rows = []
    if redacted_reference_ids:
        gap_rows.append(
            f"{len(redacted_reference_ids)} Informatica connection reference(s) are withheld in the selected "
            "saved canonical analysis. The saved value may have been an alias or sensitive connection "
            "information; this record cannot establish which, or what the original XML contained. "
            "Historical analysis is not automatically upgraded. This is not evidence that a Unity Catalog "
            "schema or table is missing; this local analysis did not query Unity Catalog."
        )
    if external_connections:
        gap_rows.append(
            f"{len(external_connections)} Informatica connection reference(s) have an external definition "
            "not captured in this analysis. An external alias is not a redaction or an approved physical binding."
        )
    if other_unresolved:
        gap_rows.append(
            f"{other_unresolved} other unresolved package reference(s) remain in the inspected closure; "
            "review their XML evidence before execution."
        )
    if not unresolved_nodes:
        gap_rows.append("No unresolved package references were found in the inspected closure.")
    gap_rows.extend([
        "XML field names and types are captured metadata, not verified live schemas or Databricks table bindings.",
        "Workflow containers normally have no column fields. Their child task links carry control flow; zero direct container flow edges does not mean the workflow does nothing.",
        "Provide parameter-file values without secrets, source/target DDL or bronze bindings, and operational requirements where absent. Upload project design documents under New Project, or package/workflow evidence under Legacy Data Integration Artifacts.",
        "This is a deterministic interpretation of captured metadata. Missing definitions are not resolved by inventing behavior. No notebooks, approved design, semantic parity or deployed Job is implied.",
    ])
    section("Gaps and required evidence", gap_rows)
    return _redact({"workflow_id": workflow_id, "name": root["name"],
                    "basis": "canonical_xml_evidence", "generator": "workflow_explanation_v1",
                    "sections": sections, "evidence_ids": sorted(evidence),
                    "cadence": {"schedule_types": schedule_types, "records": cadence_records,
                                "operational_activation": "unverified", "timezone": "unverified",
                                "truncated": bool(pending_schedule), "deployment_ready": False},
                    "coverage": {"visited_nodes": len(visited), "task_count": len(tasks), "control_link_count": len(links),
                                 "mapping_count": len(mapping_nodes), "source_target_count": len(contracts),
                                 "truncated": bool(queue) or len(tasks) > limit or len(links) > limit
                                 or details_truncated or len(code_rows) > limit or len(mapping_nodes) + len(contracts) > limit},
                    "semantic_parity": "unverified", "generation_allowed": False})


def attrs_type(task):
    return task.get("attributes", {}).get("TASKTYPE", "")


def model_indexes(model):
    """Build indexes only when a caller does not already own canonical indexes."""
    outgoing = defaultdict(list)
    for edge in model["edges"]:
        outgoing[edge["source"]].append(edge)
    return {n["id"]: n for n in model["nodes"]}, outgoing