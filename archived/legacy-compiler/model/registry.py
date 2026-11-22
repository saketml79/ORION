
def build_registry(scan):
    definitions = scan["definitions"]
    execution = scan["execution"]
    orchestration = scan["orchestration"]

    session_to_mapping = {
        session_name: attrs.get("MAPPINGNAME")
        for session_name, attrs in execution["sessions"].items()
    }

    workflow_to_tasks = {
        workflow_name: list(item["taskinstances"].keys())
        for workflow_name, item in orchestration["workflows"].items()
    }

    return {
        "definitions": {
            "sources": len(definitions["sources"]),
            "targets": len(definitions["targets"]),
            "transformations": len(definitions["transformations"]),
            "transform_fields": len(definitions["transform_fields"]),
            "mappings": list(definitions["mappings"].keys()),
            "connectors": len(definitions["connectors"]),
        },
        "execution": {
            "sessions": list(execution["sessions"].keys()),
            "session_to_mapping": session_to_mapping,
            "sesstransformationinst": len(execution["sesstransformationinst"]),
            "sessionextensions": len(execution["sessionextensions"]),
        },
        "orchestration": {
            "workflows": list(orchestration["workflows"].keys()),
            "workflow_to_tasks": workflow_to_tasks,
            "workflow_links": len(orchestration["workflowlinks"]),
            "schedules": len(orchestration["schedules"]),
        },
        "sql_snippet_count": len(scan["sql_snippets"]),
        "raw_tag_counts": scan["stats"]["counts"],
    }
