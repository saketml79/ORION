
def workflow_graph(workflow_ir):
    nodes = [
        {
            "id": n["name"],
            "task_type": n.get("task_type"),
            "enabled": n.get("enabled", True),
        }
        for n in workflow_ir["nodes"]
    ]
    edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "condition": e.get("condition"),
        }
        for e in workflow_ir["edges"]
    ]
    return {"nodes": nodes, "edges": edges}
