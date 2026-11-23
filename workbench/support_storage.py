"""Explicit, OBO-scoped Support upload and review storage helpers.

Pending material is untrusted and never executed. The shared Volumes are owner-only
until a valid Unity Catalog group grant is configured; callers must report access
failure rather than fall back to another identity or local/global storage.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import io
import json
from pathlib import PurePosixPath
import re

ADMIN_EMAIL = "ssuman@bpcs.com"
APPROVED_VOLUME = "/Volumes/orion_dev_ssuman/workbench/orion_support"
SUBMISSIONS_VOLUME = "/Volumes/orion_dev_ssuman/workbench/orion_support_submissions"
MAX_SKILL_BYTES = 128 * 1024
SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")


def current_identity():
    """Use the current hosted OBO identity, or explicit local labs profile only."""
    from workbench.hosted import _CURRENT
    identity = _CURRENT.get(None)
    if identity is not None:
        return identity
    from workbench.databricks_models import profile_identity
    return profile_identity("labs")


def _username(identity) -> str:
    user = identity.client.current_user.me()
    name = getattr(user, "user_name", None) or getattr(user, "userName", None)
    if not isinstance(name, str) or name.lower() != ADMIN_EMAIL:
        return name if isinstance(name, str) else ""
    return name


def is_initial_approver(identity) -> bool:
    return _username(identity).casefold() == ADMIN_EMAIL.casefold()


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("Invalid Support Volume path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Invalid Support Volume path")
    return str(path)


def upload(identity, volume_root: str, relative_path: str, content: bytes, *, overwrite: bool = False) -> dict:
    if volume_root not in {APPROVED_VOLUME, SUBMISSIONS_VOLUME}:
        raise ValueError("Unapproved Support Volume")
    path = volume_root + "/" + _safe_relative(relative_path)
    if not isinstance(content, bytes) or len(content) > MAX_SKILL_BYTES:
        raise ValueError("Support file exceeds its byte limit")
    client = identity.client
    client.files.create_directory(path.rsplit("/", 1)[0])
    client.files.upload(path, io.BytesIO(content), overwrite=overwrite)
    response = client.files.download(path)
    if response.contents is None:
        raise ValueError("Support upload readback is unavailable")
    with closing(response.contents) as stream:
        actual = stream.read(MAX_SKILL_BYTES + 1)
    if len(actual) > MAX_SKILL_BYTES or sha256(actual).digest() != sha256(content).digest():
        raise ValueError("Support upload checksum verification failed")
    return {"path": path, "sha256": sha256(actual).hexdigest(), "byte_size": len(actual)}


def download(identity, volume_root: str, relative_path: str, maximum: int = MAX_SKILL_BYTES) -> bytes:
    if volume_root not in {APPROVED_VOLUME, SUBMISSIONS_VOLUME} or type(maximum) is not int or not 1 <= maximum <= MAX_SKILL_BYTES:
        raise ValueError("Invalid Support Volume read request")
    path = volume_root + "/" + _safe_relative(relative_path)
    response = identity.client.files.download(path)
    if response.contents is None:
        raise ValueError("Support Volume file is unavailable")
    with closing(response.contents) as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Support Volume file exceeds its read bound")
    return content


def list_files(identity, volume_root: str, relative_directory: str) -> list[dict]:
    if volume_root not in {APPROVED_VOLUME, SUBMISSIONS_VOLUME}:
        raise ValueError("Unapproved Support Volume")
    path = volume_root + "/" + _safe_relative(relative_directory)
    rows = []
    for item in identity.client.files.list_directory_contents(path):
        item_path = getattr(item, "path", None)
        is_dir = getattr(item, "is_directory", None)
        if isinstance(item_path, str) and item_path.startswith(path + "/") and is_dir is False:
            relative = item_path[len(volume_root) + 1:]
            _safe_relative(relative)
            rows.append({"path": relative, "name": getattr(item, "name", None),
                         "file_size": getattr(item, "file_size", None)})
            if len(rows) >= 500:
                break
    return rows


def validate_skill(filename: str, content: bytes) -> dict:
    """Validate bounded Markdown shape and obvious secret material, not trustworthiness."""
    if not isinstance(filename, str) or not filename.lower().endswith(".md") or len(filename) > 100:
        raise ValueError("Upload one Markdown skill file")
    slug = filename[:-3].lower()
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Skill filename must use letters, digits, hyphens or underscores")
    if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_SKILL_BYTES:
        raise ValueError("Skill Markdown must be from 1 byte to 128 KiB")
    text = content.decode("utf-8", errors="strict")
    if "\x00" in text or not text.lstrip().startswith("---"):
        raise ValueError("Skill must be UTF-8 Markdown with a YAML frontmatter block")
    lines = text.lstrip().splitlines()
    try:
        closing = lines.index("---", 1)
    except ValueError:
        raise ValueError("Skill YAML frontmatter must be closed") from None
    header = "\n".join(lines[1:closing])
    if re.search(r"(?im)^(?:name|description)\s*:\s*\S", header) is None:
        raise ValueError("Skill frontmatter must include a name or description")
    from workbench.redaction import redact_text
    if redact_text(text) != text:
        raise ValueError("Potential credential or secret material must be removed before submission")
    return {"slug": slug, "byte_size": len(content), "sha256": sha256(content).hexdigest(),
            "content": text}


def metadata_bytes(record: dict) -> bytes:
    """Serialize metadata only; callers decide whether the record may be shared."""
    data = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) > 64 * 1024:
        raise ValueError("Support metadata exceeds its bound")
    return data
