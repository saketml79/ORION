"""Evidence of injected skill context and successful reads, never reasoning use."""
from pathlib import Path
import re

ROLES = ("supervisor", "discovery", "semantics", "architecture", "validation", "artifact")
CONTRACT = "/production-conversion-contract.md"


def safe_skill_path(value, root=None):
    """Only canonical virtual markdown paths inside a role's read-only root."""
    if not isinstance(value, str) or not re.fullmatch(
            r"/(?:CONTEXT\.md|library/(?:[A-Za-z0-9_-]+/)+SKILL\.md)", value):
        return None
    if root is not None:
        root = Path(root).resolve()
        target = (root / value.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
    return value


def skill_activity(events):
    """Project scope-verified events only. Old hash-only audits stay unavailable."""
    rows = {role: {"role": role, "loaded": {}, "reads": {}} for role in ROLES}
    pending = {}
    for role, call_id, event, payload, _created in events:
        if role not in rows:
            continue
        row = rows[role]
        if event == "skill_loaded":
            path, digest = payload.get("skill_path"), payload.get("content_sha256")
            if (path != CONTRACT and safe_skill_path(path) != path) or not isinstance(path, str):
                raise ValueError("Invalid loaded skill path")
            if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or payload.get("source") != "system_prompt"):
                raise ValueError("Invalid loaded skill receipt")
            row["loaded"][path] = {"path": path, "content_sha256": digest, "source": "system_prompt"}
        elif event == "tool_started":
            pending[call_id] = (role, payload.get("tool_name"), payload.get("skill_path"))
        elif event in {"tool_completed", "tool_failed", "tool_rejected"}:
            start = pending.pop(call_id, None)
            path = payload.get("skill_path")
            if (event == "tool_completed" and payload.get("success") is True
                    and payload.get("skill_read_success") is True
                    and payload.get("tool_name") == "read_file" and path is not None
                    and safe_skill_path(path) == path and start == (role, "read_file", path)):
                row["reads"][path] = row["reads"].get(path, 0) + 1
    per_role = []
    for row in rows.values():
        available = bool(row["loaded"] or row["reads"])
        per_role.append({"role": row["role"], "status": "recorded" if available else "unavailable",
            "loaded_count": len(row["loaded"]) if row["loaded"] else None,
            "tool_read_count": sum(row["reads"].values()) if row["loaded"] or row["reads"] else None,
            "loaded": list(row["loaded"].values()),
            "tool_reads": [{"path": path, "count": count} for path, count in sorted(row["reads"].items())]})
    return {"status": "recorded" if any(r["status"] == "recorded" for r in per_role) else "unavailable",
            "basis": "recorded_system_prompt_loads_and_successful_read_file_calls",
            "reasoning_use": "not_established", "per_role": per_role}