"""
Visual Workflow Rendering.

Convert workflow DAGs to Mermaid diagram markup for documentation and review.
Produces human-readable dependency graphs showing task nodes and dependencies.
"""
import re
from typing import Any


def _node_key(name: str) -> str:
    return (name or "unknown").replace("-", "_").replace(" ", "_").lower()


def _extract_called_workflows(workflow_ir: dict, workflow_name_map: dict[str, str]) -> set[str]:
    """Extract explicit cross-workflow calls from command task names/attributes."""
    called: set[str] = set()
    pattern = re.compile(r"(wkf_[A-Za-z0-9_]+)", re.IGNORECASE)

    for node in workflow_ir.get("nodes", []):
        task_type = (node.get("task_type") or "").lower()
        if task_type != "command":
            continue

        texts: list[str] = []
        node_name = node.get("name")
        if isinstance(node_name, str):
            texts.append(node_name)

        attrs = node.get("attrs") or {}
        if isinstance(attrs, dict):
            for value in attrs.values():
                if isinstance(value, str):
                    texts.append(value)

        for text in texts:
            for match in pattern.finditer(text):
                candidate = match.group(1)
                canonical = workflow_name_map.get(candidate.lower())
                if canonical:
                    called.add(canonical)

    return called


def render_workflow_mermaid(workflow_name: str, workflow_ir: dict) -> str:
    """
    Convert workflow IR to Mermaid flowchart syntax.
    
    Produces markdown that renders as interactive DAG diagram.
    """
    lines = [
        "```mermaid",
        "%%{init: {'theme': 'base', 'flowchart': {'curve': 'basis', 'nodeSpacing': 40, 'rankSpacing': 60}}}%%",
        "flowchart TD",
        f"    subgraph wf[\"Workflow: {workflow_name}\"]",
    ]
    
    nodes = workflow_ir.get("nodes", [])
    edges = workflow_ir.get("edges", [])
    
    node_classes = []
    
    # Add nodes with labels
    for node in nodes:
        node_name = node.get("name", "")
        node_key = _node_key(node_name)
        task_type = node.get("task_type", "unknown")
        role = node.get("role", "unknown")
        enabled = node.get("enabled", True)
        
        enabled_marker = "" if enabled else " (DISABLED)"
        label = f"{node_name}\\n[{task_type}::{role}]{enabled_marker}"

        if task_type.lower() == "start":
            lines.append(f"        {node_key}((\"{label}\"))")
            node_classes.append((node_key, "start"))
        else:
            lines.append(f"        {node_key}[\"{label}\"]")
            if not enabled:
                node_classes.append((node_key, "disabled"))
            elif role == "session":
                node_classes.append((node_key, "session"))
            elif role == "gate":
                node_classes.append((node_key, "gate"))
            elif role == "control":
                node_classes.append((node_key, "control"))
            elif role == "orchestrator":
                node_classes.append((node_key, "orchestrator"))
            else:
                node_classes.append((node_key, "defaultNode"))
    
    # Add edges with conditions
    for edge in edges:
        source = _node_key(edge.get("source", ""))
        target = _node_key(edge.get("target", ""))
        condition = edge.get("condition")
        
        if condition:
            edge_label = f"on: {condition}"
            lines.append(f"        {source} -->|{edge_label}| {target}")
        else:
            lines.append(f"        {source} --> {target}")

    lines.append("    end")
    lines.append("")
    lines.append("    classDef start fill:#1f2937,color:#ffffff,stroke:#111827,stroke-width:2px;")
    lines.append("    classDef session fill:#d1fae5,color:#064e3b,stroke:#10b981,stroke-width:1.5px;")
    lines.append("    classDef gate fill:#fef3c7,color:#78350f,stroke:#f59e0b,stroke-width:1.5px;")
    lines.append("    classDef control fill:#dbeafe,color:#1e3a8a,stroke:#3b82f6,stroke-width:1.5px;")
    lines.append("    classDef orchestrator fill:#e5e7eb,color:#374151,stroke:#6b7280,stroke-width:1.5px;")
    lines.append("    classDef disabled fill:#fee2e2,color:#7f1d1d,stroke:#dc2626,stroke-width:1.5px,stroke-dasharray: 5 3;")
    lines.append("    classDef defaultNode fill:#f3f4f6,color:#111827,stroke:#9ca3af,stroke-width:1px;")

    for node_key, class_name in node_classes:
        lines.append(f"    class {node_key} {class_name};")
    
    lines.append("```")
    
    return "\n".join(lines)


def render_estate_mermaid(workflows: list[dict]) -> str:
    """
    Render cross-workflow dependency graph.
    
    Shows all workflows and their session-level dependencies.
    """
    lines = [
        "```mermaid",
        "%%{init: {'theme': 'base', 'flowchart': {'curve': 'linear', 'nodeSpacing': 60, 'rankSpacing': 80}}}%%",
        "flowchart TB",
        "    subgraph WorkflowDependencies[\"Workflow-Level Dependencies\"]",
    ]
    
    # Collect all data-load sessions and their workflows
    session_to_workflows = {}
    for wf in workflows:
        wf_name = wf.get("name", "unknown")
        for session in wf.get("data_load_sessions", []):
            if session not in session_to_workflows:
                session_to_workflows[session] = []
            session_to_workflows[session].append(wf_name)
    
    # Add nodes for each workflow
    for wf in workflows:
        wf_name = wf.get("name", "")
        wf_key = _node_key(wf_name)
        is_dummy = wf.get("is_dummy_workflow", False)
        is_gate = wf.get("is_gate_workflow", False)
        
        marker = "(DUMMY)" if is_dummy else "(GATE)" if is_gate else ""
        label = f"{wf_name} {marker}"
        task_count = len(wf.get("nodes", []))
        session_count = len(wf.get("data_load_sessions", []))
        
        lines.append(f"        {wf_key}[\"{label}\\n({task_count} tasks, {session_count} sessions)\"]")
    
    # Add dependency edges based on shared sessions
    added_edges = set()
    for wf1 in workflows:
        wf1_name = wf1.get("name", "")
        wf1_sessions = set(wf1.get("data_load_sessions", []))
        
        for wf2 in workflows:
            if wf1_name == wf2.get("name"):
                continue
            wf2_name = wf2.get("name", "")
            wf2_sessions = set(wf2.get("data_load_sessions", []))
            
            # If they share a session, there's a potential dependency
            shared = wf1_sessions & wf2_sessions
            if shared:
                edge_key = tuple(sorted([wf1_name, wf2_name]))
                if edge_key not in added_edges:
                    wf1_key = _node_key(wf1_name)
                    wf2_key = _node_key(wf2_name)
                    shared_label = ", ".join(sorted(shared))
                    lines.append(f"        {wf1_key} -->|{shared_label}| {wf2_key}")
                    added_edges.add(edge_key)
    
    lines.append("    end")
    lines.append("    classDef wf fill:#eef2ff,color:#312e81,stroke:#4f46e5,stroke-width:1.5px;")
    for wf in workflows:
        lines.append(f"    class {_node_key(wf.get('name', ''))} wf;")
    lines.append("```")
    
    return "\n".join(lines)


def render_dependency_table(workflows: list[dict]) -> str:
    """
    Render workflow dependencies as markdown table.
    
    Shows: Workflow | Task Count | Sessions | Entry Nodes | Terminal Nodes
    """
    workflow_names = [wf.get("name", "") for wf in workflows if wf.get("name")]
    workflow_name_map = {name.lower(): name for name in workflow_names}

    depends_on_map: dict[str, set[str]] = {name: set() for name in workflow_names}
    referenced_by_map: dict[str, set[str]] = {name: set() for name in workflow_names}

    for wf in workflows:
        wf_name = wf.get("name", "")
        if not wf_name:
            continue

        called = _extract_called_workflows(wf, workflow_name_map)
        called.discard(wf_name)
        depends_on_map[wf_name] = called
        for target in called:
            referenced_by_map.setdefault(target, set()).add(wf_name)

    lines = [
        "## Workflow Dependency Summary",
        "",
        "### Column Guide",
        "- Serial No: Row number in this summary table.",
        "- Workflow: Informatica workflow name.",
        "- Tasks: Total number of tasks/nodes in the workflow DAG.",
        "- Sessions: Number of session-type tasks in the workflow.",
        "- Session Names: Names of all session-type tasks.",
        "- Depends On Workflows: Explicit workflow calls made by this workflow (from command tasks).",
        "- Referenced By Workflows: Other workflows that explicitly call this workflow.",
        "- Entry Nodes: Tasks with no incoming dependency edges.",
        "- Terminal Nodes: Tasks with no outgoing dependency edges.",
        "- Has Cycles: Whether dependency cycle(s) were detected.",
        "- All Tasks: Names of all tasks in the workflow (disabled tasks are marked).",
        "",
        "| Serial No | Workflow | Tasks | Sessions | Session Names | Depends On Workflows | Referenced By Workflows | Entry Nodes | Terminal Nodes | Has Cycles | All Tasks |",
        "|-----------|----------|-------|----------|---------------|----------------------|-------------------------|-------------|----------------|------------|-----------|",
    ]
    
    for idx, wf in enumerate(workflows, start=1):
        name = wf.get("name", "")
        nodes = wf.get("nodes", [])
        task_count = len(nodes)
        session_nodes = [
            n.get("name", "")
            for n in nodes
            if (n.get("task_type") or "").lower() == "session" and n.get("name")
        ]
        session_count = len(session_nodes)
        depends_on = sorted(depends_on_map.get(name, set()))
        referenced_by = sorted(referenced_by_map.get(name, set()))
        entry = ", ".join(wf.get("entry_nodes", []))
        terminal = ", ".join(wf.get("terminal_nodes", []))
        has_cycles = wf.get("has_cycles", False)

        all_tasks = []
        for node in nodes:
            node_name = node.get("name", "")
            if not node_name:
                continue
            if not node.get("enabled", True):
                node_name = f"{node_name} (DISABLED)"
            all_tasks.append(node_name)

        task_list = "<br>".join(all_tasks)
        session_list = "<br>".join(session_nodes)
        depends_on_list = "<br>".join(depends_on) if depends_on else "-"
        referenced_by_list = "<br>".join(referenced_by) if referenced_by else "-"
        entry_display = entry.replace("|", "/")
        terminal_display = terminal.replace("|", "/")
        session_list_display = session_list.replace("|", "/")
        depends_on_display = depends_on_list.replace("|", "/")
        referenced_by_display = referenced_by_list.replace("|", "/")
        task_list_display = task_list.replace("|", "/")
        
        lines.append(
            f"| {idx} | {name} | {task_count} | {session_count} | {session_list_display} | {depends_on_display} | {referenced_by_display} | {entry_display} | {terminal_display} | {has_cycles} | {task_list_display} |"
        )
    
    return "\n".join(lines)


def render_mermaid_html(title: str, mermaid_block: str) -> str:
        """Wrap Mermaid markup into standalone HTML for reliable visual rendering."""
        content = mermaid_block.strip()
        if content.startswith("```mermaid"):
                content = content[len("```mermaid"):].strip()
        if content.endswith("```"):
                content = content[:-3].strip()

        return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>{title}</title>
    <style>
        body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f8fafc; color: #0f172a; }}
        .header {{ padding: 16px 20px; background: #0f172a; color: #f8fafc; font-weight: 600; }}
        .hint {{ padding: 8px 20px; color: #334155; font-size: 13px; }}
        .panel {{ margin: 12px 20px 20px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; overflow: auto; }}
        .mermaid {{ min-width: 1200px; padding: 16px; }}
    </style>
</head>
<body>
    <div class=\"header\">{title}</div>
    <div class=\"hint\">Tip: use browser zoom (Ctrl + / Ctrl -) and scroll to inspect large graphs.</div>
    <div class=\"panel\">
        <pre class=\"mermaid\">{content}</pre>
    </div>
    <script type=\"module\">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({{ startOnLoad: true, securityLevel: 'loose' }});
    </script>
</body>
</html>
"""
