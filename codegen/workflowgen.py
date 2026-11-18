
from typing import Any


def generate_workflow(name: str, notebooks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "tasks": [
            {
                "task_key": notebook["task_key"],
                "notebook_path": notebook["notebook_path"],
                "depends_on": notebook.get("depends_on", []),
            }
            for notebook in notebooks
        ],
    }
