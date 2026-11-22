import json
from pathlib import Path
from visual.mermaid_renderer import render_mermaid_html


def safe_name(name: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return "".join(ch if ch in allowed else "_" for ch in name)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    vis_dir = root / "output" / "visual"
    vis_dir.mkdir(parents=True, exist_ok=True)

    # 1) Master orchestrator Mermaid view
    master_path = root / "output" / "databricks" / "workflows" / "master_orchestrator.json"
    master = json.loads(master_path.read_text(encoding="utf-8"))

    lines = [
        "```mermaid",
        "%%{init: {'theme': 'base', 'flowchart': {'curve': 'basis', 'nodeSpacing': 35, 'rankSpacing': 60}}}%%",
        "flowchart TD",
    ]
    for task in master.get("tasks", []):
        key = task["task_key"]
        label = key.replace("run__", "")
        lines.append(f"    {key}[\"{label}\"]")
    for task in master.get("tasks", []):
        child = task["task_key"]
        for dep in task.get("depends_on", []):
            parent = dep["task_key"]
            lines.append(f"    {parent} --> {child}")
    lines.append("```")

    master_md = vis_dir / "master_orchestrator.md"
    master_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    master_html = vis_dir / "master_orchestrator.html"
    master_html.write_text(
        render_mermaid_html("Master Orchestrator Dependency DAG", "\n".join(lines)),
        encoding="utf-8",
    )

    # 2) Session->Session edge replication validation
    opt_dir = root / "output" / "optimized"
    db_dir = root / "output" / "databricks" / "workflows"

    issues: list[tuple[str, str]] = []
    checked = 0

    for wf_file in sorted(db_dir.glob("workflow_*.json")):
        wf = json.loads(wf_file.read_text(encoding="utf-8"))
        wf_name = wf.get("name")
        if not wf_name:
            continue

        opt_file = opt_dir / f"workflow_{wf_name}.json"
        if not opt_file.exists():
            issues.append((wf_name, "missing optimized workflow JSON"))
            continue

        opt = json.loads(opt_file.read_text(encoding="utf-8"))

        # Generated Databricks tasks keyed by session task suffix
        generated_suffix_to_key: dict[str, str] = {}
        for task in wf.get("tasks", []):
            suffix = task["task_key"].split("__", 1)[-1]
            generated_suffix_to_key[suffix] = task["task_key"]

        # Map both TASKNAME and instance NAME to generated task_key
        session_name_to_task_key: dict[str, str] = {}
        for node in opt.get("nodes", []):
            if node.get("task_type") != "Session":
                continue
            task_name = node.get("name")
            instance_name = (node.get("attrs") or {}).get("NAME")

            candidates = []
            if isinstance(task_name, str):
                candidates.extend([task_name, safe_name(task_name)])
            if isinstance(instance_name, str):
                candidates.extend([instance_name, safe_name(instance_name)])

            for candidate in candidates:
                mapped_key = generated_suffix_to_key.get(candidate)
                if mapped_key:
                    session_name_to_task_key[candidate] = mapped_key

            if isinstance(task_name, str):
                for candidate in candidates:
                    mapped_key = generated_suffix_to_key.get(candidate)
                    if mapped_key:
                        session_name_to_task_key[task_name] = mapped_key
                        break
            if isinstance(instance_name, str):
                for candidate in candidates:
                    mapped_key = generated_suffix_to_key.get(candidate)
                    if mapped_key:
                        session_name_to_task_key[instance_name] = mapped_key
                        break

        expected_edges: set[tuple[str, str]] = set()
        for edge in opt.get("edges", []):
            source = session_name_to_task_key.get(edge.get("source"))
            target = session_name_to_task_key.get(edge.get("target"))
            if source and target:
                expected_edges.add((source, target))

        actual_edges: set[tuple[str, str]] = set()
        for task in wf.get("tasks", []):
            child = task["task_key"]
            for parent in task.get("depends_on", []):
                actual_edges.add((parent, child))

        checked += 1
        missing = expected_edges - actual_edges
        extra = actual_edges - expected_edges
        if missing or extra:
            issues.append((wf_name, f"missing={len(missing)}, extra={len(extra)}"))

    report_lines = [
        "# Workflow Replica Validation",
        "",
        "Compares optimized workflow Session->Session edges against generated Databricks task `depends_on` edges.",
        "",
        f"- Workflows checked: {checked}",
        f"- Workflows with mismatches: {len(issues)}",
    ]

    if issues:
        report_lines.extend(["", "## Mismatches"])
        for wf_name, detail in issues[:200]:
            report_lines.append(f"- {wf_name}: {detail}")
    else:
        report_lines.extend(["", "All workflows match for Session->Session dependency replication."])

    report_path = vis_dir / "workflow_replica_validation.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Wrote {master_md.relative_to(root)}")
    print(f"Wrote {master_html.relative_to(root)}")
    print(f"Wrote {report_path.relative_to(root)}")
    print(f"Checked {checked} workflows; mismatches={len(issues)}")


if __name__ == "__main__":
    main()
