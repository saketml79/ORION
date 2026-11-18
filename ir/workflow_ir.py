
def workflow_ir(name, taskinstances, workflowlinks):
    nodes = []
    for task_name, attrs in taskinstances.items():
        nodes.append(
            {
                "name": task_name,
                "task_type": attrs.get("TASKTYPE", "Unknown"),
                "enabled": attrs.get("ISENABLED", "YES") == "YES",
                "attrs": attrs,
            }
        )

    edges = []
    for link in workflowlinks:
        source = link.get("FROMTASK") or link.get("TASKFROM") or link.get("FROMTASKINSTANCE")
        target = link.get("TOTASK") or link.get("TASKTO") or link.get("TOTASKINSTANCE")
        if source and target:
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "condition": link.get("CONDITION") or link.get("LINKCONDITION"),
                    "attrs": link,
                }
            )

    return {
        "type": "workflow",
        "name": name,
        "nodes": nodes,
        "edges": edges,
    }
