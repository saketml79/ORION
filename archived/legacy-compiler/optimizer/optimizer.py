
def _node_kind(node):
    task_type = (node.get("task_type") or "").lower()
    name = (node.get("name") or "").lower()
    if task_type == "session":
        return "session"
    if task_type in {"start", "decision", "command", "control"}:
        return "control"
    if any(token in name for token in ["check", "gate", "dummy", "retry", "wait", "log"]):
        return "gate"
    return "orchestrator"


def _find_cycles(adj):
    visited = set()
    in_stack = set()
    stack = []
    cycles = []

    def dfs(node):
        visited.add(node)
        in_stack.add(node)
        stack.append(node)

        for nxt in adj.get(node, []):
            if nxt not in visited:
                dfs(nxt)
            elif nxt in in_stack:
                if nxt in stack:
                    idx = stack.index(nxt)
                    cycle = stack[idx:] + [nxt]
                    if cycle not in cycles:
                        cycles.append(cycle)

        stack.pop()
        in_stack.remove(node)

    for n in adj:
        if n not in visited:
            dfs(n)
    return cycles


def optimize_workflow(workflow_ir):
    node_by_name = {n["name"]: n for n in workflow_ir["nodes"]}
    adjacency = {n["name"]: [] for n in workflow_ir["nodes"]}
    indegree = {n["name"]: 0 for n in workflow_ir["nodes"]}

    for edge in workflow_ir["edges"]:
        source = edge["source"]
        target = edge["target"]
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)
            indegree[target] += 1

    raw_cycles = _find_cycles(adjacency)
    cycle_info = []
    for cycle in raw_cycles:
        kinds = {_node_kind(node_by_name.get(name, {})) for name in cycle}
        cycle_type = "logical"
        if kinds.issubset({"control", "gate", "orchestrator"}):
            cycle_type = "control"
        cycle_info.append({"type": cycle_type, "nodes": cycle})

    entry_nodes = [name for name, degree in indegree.items() if degree == 0]
    terminal_nodes = [name for name, next_nodes in adjacency.items() if not next_nodes]

    normalized_nodes = []
    for node in workflow_ir["nodes"]:
        role = _node_kind(node)
        normalized_nodes.append({**node, "role": role})

    return {
        **workflow_ir,
        "entry_nodes": entry_nodes,
        "terminal_nodes": terminal_nodes,
        "cycles": cycle_info,
        "normalized_nodes": normalized_nodes,
        "optimized": True,
    }
