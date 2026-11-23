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


def writer_skill_capacity():
    """Bound installed reads plus the registry's fixed maximum of 200 approved skills."""
    from workbench.official_skill_library import SKILLS
    writer_root = Path(__file__).with_name("skills") / "spark_codegen"
    local_skills = sum(1 for path in (writer_root / "library").glob("**/SKILL.md") if path.is_file())
    return sum(len(names) for names in SKILLS.values()) + local_skills + 202


def writer_skill_receipt(value):
    """Project bounded writer access receipts without inferring historical access."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None
    loaded, reads = value.get("loaded"), value.get("tool_reads")
    if not isinstance(loaded, list) or not isinstance(reads, list):
        return None
    try:
        capacity = writer_skill_capacity()
    except Exception:
        return None
    if len(loaded) > capacity or len(reads) > capacity:
        return None
    result = {"schema_version": 1, "reasoning_use": "not_established", "loaded": [], "tool_reads": []}
    for item in loaded:
        if not isinstance(item, dict):
            return None
        path, digest = item.get("path"), item.get("content_sha256")
        if ((path != CONTRACT and safe_skill_path(path) != path) or not isinstance(path, str) or len(path) > 240
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or item.get("source") != "system_prompt"):
            return None
        result["loaded"].append({"path": path, "content_sha256": digest, "source": "system_prompt"})
    for item in reads:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or len(item["path"]) > 240
                or safe_skill_path(item["path"]) != item["path"]):
            return None
        if item["path"].startswith("/library/official/"):
            try:
                from workbench.official_skill_library import SKILLS
                parts = item["path"].split("/")
                if (len(parts) != 6 or parts[3] not in SKILLS or parts[4] not in SKILLS[parts[3]]
                        or parts[5] != "SKILL.md"):
                    return None
            except Exception:
                return None
        elif item["path"].startswith("/library/approved/"):
            parts = item["path"].split("/")
            if (len(parts) != 5 or parts[2] != "approved"
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", parts[3])
                    or parts[4] != "SKILL.md"):
                return None
        count = item.get("count")
        if type(count) is not int or count < 1:
            return None
        result["tool_reads"].append({"path": item["path"], "count": count})
    return result


def skill_activity(events):
    """Project scope-verified events only. Old hash-only audits stay unavailable."""
    try:
        from workbench.official_skill_library import SKILLS
    except Exception:
        # Optional official-skill telemetry cannot invalidate historical reads.
        SKILLS = {}

    def official_path(path):
        if not isinstance(path, str):
            return False
        parts = path.split("/")
        if (len(parts) == 6 and parts[:3] == ["", "library", "official"]
                and parts[3] in SKILLS and parts[4] in SKILLS[parts[3]] and parts[5] == "SKILL.md"):
            return True
        return (len(parts) == 5 and parts[:3] == ["", "library", "approved"]
                and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", parts[3]) is not None
                and parts[4] == "SKILL.md")

    rows = {role: {"role": role, "loaded": {}, "reads": {}} for role in ROLES}
    pending = {}
    official_reads = False
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
                    and ((payload.get("tool_name") == "read_file" and path is not None
                          and safe_skill_path(path) == path and start == (role, "read_file", path)
                          and (not path.startswith("/library/official/") or official_path(path)))
                         or (payload.get("tool_name") == "read_official_skill" and official_path(path)
                             and start == (role, "read_official_skill", path)))):
                row["reads"][path] = row["reads"].get(path, 0) + 1
                official_reads = official_reads or payload.get("tool_name") == "read_official_skill"
    per_role = []
    for row in rows.values():
        available = bool(row["loaded"] or row["reads"])
        per_role.append({"role": row["role"], "status": "recorded" if available else "unavailable",
            "loaded_count": len(row["loaded"]) if row["loaded"] else None,
            "tool_read_count": sum(row["reads"].values()) if row["loaded"] or row["reads"] else None,
            "loaded": list(row["loaded"].values()),
            "tool_reads": [{"path": path, "count": count} for path, count in sorted(row["reads"].items())]})
    return {"status": "recorded" if any(r["status"] == "recorded" for r in per_role) else "unavailable",
            "basis": ("recorded_system_prompt_loads_and_successful_skill_read_calls" if official_reads
                      else "recorded_system_prompt_loads_and_successful_read_file_calls"),
            "read_meaning": "A recorded official skill read is a successful nonempty SKILL.md page, not necessarily the entire file; it does not establish reasoning use.",
            "reasoning_use": "not_established", "per_role": per_role}