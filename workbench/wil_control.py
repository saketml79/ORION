"""Exact exported link predicate evaluation, no job submission or task execution."""
import re


def link_matches(condition, outcomes):
    if not condition.strip():
        return True
    match = re.fullmatch(r"\s*\$([A-Za-z_]\w*)\.(Status|TgtSuccessRows)\s*(>=|<=|>|<|=)\s*(SUCCEEDED|\d+)\s*",
                         condition, re.I)
    if match is None:
        raise ValueError("Unsupported exported link predicate")
    task, field, operator, expected = match.groups()
    outcome = outcomes.get(task)
    if outcome is None:
        return False
    if field.lower() == "status":
        if operator != "=" or expected.upper() != "SUCCEEDED":
            raise ValueError("Unsupported status predicate")
        return outcome.get("status") == "SUCCEEDED"
    # Never treat a rejected/failed/no-run session as a zero-row success.
    value = outcome.get("target_success_rows")
    if outcome.get("status") != "SUCCEEDED" or type(value) is not int or value < 0:
        return False
    expected = int(expected)
    return {"=": value == expected, ">": value > expected, "<": value < expected,
            ">=": value >= expected, "<=": value <= expected}[operator]


def eligibility(task, links, outcomes):
    incoming = [link for link in links if link["TOTASK"] == task["name"]]
    if task["attributes"].get("ISENABLED") != "YES":
        return False
    if not incoming:
        return task["attributes"].get("TASKTYPE") == "Start"
    values = [outcomes.get(link["FROMTASK"], {}).get("status") not in {None, "SKIPPED"}
              and link_matches(link.get("CONDITION", ""), outcomes) for link in incoming]
    return all(values) if task["attributes"].get("TREAT_INPUTLINK_AS_AND", "YES") == "YES" else any(values)


def preview_trace(tasks, links, session_outcomes):
    """Offline logical routing only. No success outcome is fabricated for a load.

    Decisions with no business expression are structural successful OR merges.
    Email remains a side-effect requirement even if the logical path reaches it.
    """
    import networkx as nx
    by_name = {t["name"]: t for t in tasks}
    graph = nx.DiGraph()
    graph.add_nodes_from(by_name)
    graph.add_edges_from((l["FROMTASK"], l["TOTASK"]) for l in links)
    if set(graph) != set(by_name) or not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Dangling or cyclic workflow graph")
    outcomes = {}
    for name in nx.lexicographical_topological_sort(graph):
        task = by_name[name]
        if not eligibility(task, links, outcomes):
            outcomes[name] = {"status": "SKIPPED"}
        elif task["attributes"]["TASKTYPE"] in {"Start", "Decision"}:
            outcomes[name] = {"status": "SUCCEEDED"}
        else:
            outcomes[name] = dict(session_outcomes.get(name, {"status": "NOT_EXECUTED"}))
    return outcomes