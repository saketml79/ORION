"""Static completeness diagnostics, never execution or semantic-parity proof."""
import json

from workbench.spark_codegen import ContractError, _notebook, _python, _sql


def assess(design, manifest, artifacts, *, staged_artifacts=None, stale=None):
    tasks = {item["id"]: item for item in design.get("tasks", [])}
    rows = manifest.get("tasks", [])
    staged_artifacts = staged_artifacts or {}
    available = {**staged_artifacts, **artifacts}
    reasons = []
    if len(rows) != len(tasks) or {row.get("task_id") for row in rows} != set(tasks):
        reasons.append("Task coverage does not match the saved design")
    for row in rows:
        task = tasks.get(row.get("task_id"))
        if not task:
            continue
        paths = row.get("artifact_paths", [])
        if not isinstance(paths, list):
            paths = []
        notebook_paths = [path for path in available if path.endswith(".ipynb")
                          and path.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold() == row["task_id"].casefold()]
        has_notebook = any(path in available and path.endswith(".ipynb") for path in paths) or bool(notebook_paths)
        if task.get("implementation_kind") == "notebook_task" and not has_notebook:
            reasons.append(f"No notebook file was saved for task {row['task_id']}")
        missing_paths = [path for path in paths if path not in available]
        if missing_paths:
            reasons.append(f"Task {row['task_id']} references files that were not saved: {', '.join(missing_paths)}")
        if row.get("status") != "drafted":
            blocker_text = ", ".join(str(item) for item in row.get("blockers", []) if item)
            suffix = " A notebook file is saved, but task metadata was not completed." if has_notebook else ""
            reasons.append(f"Writer did not finish task {row['task_id']} ({blocker_text or 'status not drafted'}).{suffix}")
    for path, content in available.items():
        try:
            if path.endswith(".ipynb"):
                _notebook(content, path)
            elif path.endswith(".py"):
                _python(content)
            elif path.endswith(".sql"):
                _sql(content)
        except (ContractError, ValueError, TypeError, json.JSONDecodeError):
            reasons.append(f"Artifact failed static validation: {path}")
    # The current writer accepts notebook/module/SQL/test artifacts only. A
    # logical graph or YAML preview is not a generated native Jobs resource.
    jobs = [path for path in available if path.endswith((".yaml", ".yml"))]
    if not jobs:
        reasons.append("Native Jobs YAML was not produced because task completion metadata is incomplete")
    else:
        # No Jobs schema/semantic validator is registered yet. Never promote
        # arbitrary YAML merely because a file exists.
        reasons.append("Native Jobs YAML requires independent schema and dependency validation")
    if stale is not False:
        reasons.append("Current design and input freshness has not been verified" if stale is None else "Design is stale")
    staged_count = len(staged_artifacts)
    committed_count = len(artifacts)
    return {"status": "incomplete" if reasons else "generated_unverified", "blockers": reasons,
        "committed_file_count": committed_count, "saved_draft_file_count": staged_count,
        "semantic_parity": "not_established", "execution_allowed": False,
        "deployment_allowed": False, "freshness": "unknown" if stale is None else ("stale" if stale else "current")}