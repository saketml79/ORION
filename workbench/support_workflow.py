"""User-submitted Support reviews and vetted skills, staged through OBO Volumes."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
from uuid import uuid4

from workbench.support_storage import (
    ADMIN_EMAIL, APPROVED_VOLUME, SUBMISSIONS_VOLUME, current_identity, download, is_initial_approver,
    list_files, metadata_bytes, upload, validate_skill,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(identity) -> dict:
    user = identity.client.current_user.me()
    name = getattr(user, "user_name", None) or getattr(user, "userName", None)
    if not isinstance(name, str) or not name:
        raise PermissionError("Authenticated workspace username is unavailable")
    owner = sha256((identity.host + "\n" + identity.user_id).encode("utf-8")).hexdigest()
    return {"email": name, "owner_hash": owner}


def _safe_note(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError("Review note exceeds its limit")
    from workbench.redaction import redact_text
    if redact_text(value) != value:
        raise ValueError("Remove credential-like text before saving this review note")
    return value


def _private_stage(identity, relative_path: str, data: bytes, *, overwrite: bool = False) -> dict:
    from workbench.hosted_storage import provision
    import os
    root = provision(identity, os.environ.get("WORKBENCH_CATALOG_PREFIX", "orion_dev_"), initialize=False)
    path = root + "/support/" + relative_path
    identity.client.files.create_directory(path.rsplit("/", 1)[0])
    identity.client.files.upload(path, __import__("io").BytesIO(data), overwrite=overwrite)
    response = identity.client.files.download(path)
    if response.contents is None:
        raise ValueError("Private Volume staging readback is unavailable")
    from contextlib import closing
    with closing(response.contents) as stream:
        actual = stream.read(len(data) + 1)
    if actual != data:
        raise ValueError("Private Volume staging checksum mismatch")
    return {"path": path, "sha256": sha256(actual).hexdigest(), "byte_size": len(actual)}


def submit_skill(identity, filename: str, content: bytes) -> dict:
    validated = validate_skill(filename, content)
    actor = _actor(identity)
    request_id = uuid4().hex
    private = _private_stage(identity, f"skill-submissions/{request_id}.md", content)
    content_path = f"skills/{request_id}.md"
    meta_path = f"skills/{request_id}.json"
    metadata = {"schema_version": 1, "kind": "skill_submission", "id": request_id,
                "status": "pending", "created_at": _now(), "requestor": actor,
                "filename": filename, "slug": validated["slug"], "content_sha256": validated["sha256"],
                "byte_size": validated["byte_size"], "private_staging_sha256": private["sha256"],
                "content_path": content_path, "reviewed_by": None, "promoted_path": None}
    # The private copy is retained if the shared queue lacks its necessary Volume grant.
    try:
        shared = upload(identity, SUBMISSIONS_VOLUME, content_path, content)
        upload(identity, SUBMISSIONS_VOLUME, meta_path, metadata_bytes(metadata))
    except Exception:
        return {"status": "private_staged_not_queued", "request_id": request_id,
                "content_sha256": validated["sha256"], "staged_in_user_volume": True,
                "shared_queue_path": None, "approved": False,
                "detail": "Private staging succeeded, but shared queue access is not granted; the administrator was not notified."}
    return {"status": "pending", "request_id": request_id, "content_sha256": validated["sha256"],
            "staged_in_user_volume": True, "shared_queue_path": meta_path,
            "shared_content_sha256": shared["sha256"], "approved": False}


def submit_human_review(service, identity, run_id: str, package_hash: str, pricing: dict | None = None) -> dict:
    actor = _actor(identity)
    from workbench.support_review import _review_context
    context = _review_context(service, run_id, package_hash, pricing or {})
    request_id = uuid4().hex
    body = {"schema_version": 1, "kind": "human_review_request", "id": request_id,
            "status": "pending", "created_at": _now(), "requestor": actor,
            "run_id": run_id, "context": context, "review": None}
    raw = metadata_bytes(body)
    private = _private_stage(identity, f"review-requests/{request_id}.json", raw)
    shared_path = f"reviews/{request_id}.json"
    try:
        uploaded = upload(identity, SUBMISSIONS_VOLUME, shared_path, raw)
    except Exception:
        return {"status": "private_staged_not_queued", "request_id": request_id,
                "private_staging_sha256": private["sha256"], "shared_queue_path": None,
                "detail": "Private staging succeeded, but shared queue access is not granted; the administrator was not notified."}
    return {"status": "pending", "request_id": request_id,
            "private_staging_sha256": private["sha256"], "shared_queue_path": shared_path,
            "shared_sha256": uploaded["sha256"], "reviewer": ADMIN_EMAIL}


def pending_reviews(identity) -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can open the Support queue")
    items = []
    for row in list_files(identity, SUBMISSIONS_VOLUME, "reviews"):
        if not str(row.get("path", "")).endswith(".json"):
            continue
        try:
            item = json.loads(download(identity, SUBMISSIONS_VOLUME, row["path"]))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if (isinstance(item, dict) and item.get("kind") == "human_review_request"
            and item.get("schema_version") == 1
            and item.get("status") in {"pending", "reviewed", "needs_information", "declined"}):
            items.append({key: item.get(key) for key in ("id", "status", "created_at", "requestor", "run_id", "review")})
    items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return {"items": items[:200], "total": len(items), "reviewer": ADMIN_EMAIL}


def get_human_review(identity, request_id: str) -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can inspect Support review details")
    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("Invalid Support review ID")
    record = json.loads(download(identity, SUBMISSIONS_VOLUME, f"reviews/{request_id}.json"))
    if record.get("kind") != "human_review_request" or record.get("id") != request_id:
        raise ValueError("Unknown Support review request")
    return record


def decide_human_review(identity, request_id: str, decision: str, comment: str) -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can complete a Support review")
    if not re.fullmatch(r"[a-f0-9]{32}", request_id) or decision not in {"reviewed", "needs_information", "declined"}:
        raise ValueError("Invalid Support review decision")
    comment = _safe_note(comment)
    path = f"reviews/{request_id}.json"
    record = json.loads(download(identity, SUBMISSIONS_VOLUME, path))
    if record.get("kind") != "human_review_request" or record.get("id") != request_id or record.get("status") != "pending":
        raise ValueError("Unknown Support review request")
    stamp = _now()
    event_id = uuid4().hex
    event = {"schema_version": 1, "kind": "human_review_decision", "event_id": event_id,
             "request_id": request_id, "previous_status": "pending", "decision": decision,
             "reviewer": ADMIN_EMAIL, "reviewed_at": stamp, "comment": comment, "advisory_only": True}
    upload(identity, SUBMISSIONS_VOLUME, f"reviews/events/{event_id}.json", metadata_bytes(event))
    record.update(status=decision, review={"event_id": event_id, "reviewer": ADMIN_EMAIL,
                                           "reviewed_at": stamp, "comment": comment,
                                           "advisory_only": True})
    upload(identity, SUBMISSIONS_VOLUME, path, metadata_bytes(record), overwrite=True)
    return {"request_id": request_id, "decision_event_id": event_id, "status": decision,
            "reviewed_at": record["review"]["reviewed_at"]}


def pending_skills(identity) -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can inspect pending skills")
    items = []
    for row in list_files(identity, SUBMISSIONS_VOLUME, "skills"):
        if not str(row.get("path", "")).endswith(".json"):
            continue
        try:
            item = json.loads(download(identity, SUBMISSIONS_VOLUME, row["path"]))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if (isinstance(item, dict) and item.get("kind") == "skill_submission"
                and item.get("schema_version") == 1 and item.get("status") == "pending"):
            items.append({key: item.get(key) for key in
                          ("id", "status", "created_at", "requestor", "filename", "slug", "content_sha256", "byte_size")})
    items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return {"items": items[:200], "total": len(items), "reviewer": ADMIN_EMAIL}


def get_pending_skill(identity, request_id: str) -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can inspect submitted skills")
    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("Invalid skill submission ID")
    metadata = json.loads(download(identity, SUBMISSIONS_VOLUME, f"skills/{request_id}.json"))
    if metadata.get("kind") != "skill_submission" or metadata.get("id") != request_id or metadata.get("status") != "pending":
        raise ValueError("Skill submission is not pending")
    content = download(identity, SUBMISSIONS_VOLUME, metadata["content_path"])
    if sha256(content).hexdigest() != metadata.get("content_sha256"):
        raise ValueError("Skill content changed after submission")
    metadata["content"] = content.decode("utf-8", errors="strict")
    return metadata


def promote_skill(identity, request_id: str, approved: bool, review_note: str = "") -> dict:
    if not is_initial_approver(identity):
        raise PermissionError("Only the designated approver can promote skills")
    if not re.fullmatch(r"[a-f0-9]{32}", request_id) or approved is not True:
        raise ValueError("Explicit administrator approval is required")
    review_note = _safe_note(review_note)
    metadata_path = f"skills/{request_id}.json"
    metadata = json.loads(download(identity, SUBMISSIONS_VOLUME, metadata_path))
    if metadata.get("kind") != "skill_submission" or metadata.get("id") != request_id or metadata.get("status") != "pending":
        raise ValueError("Skill submission is not pending or its receipt is invalid")
    content = download(identity, SUBMISSIONS_VOLUME, metadata["content_path"])
    validated = validate_skill(metadata["filename"], content)
    if validated["sha256"] != metadata.get("content_sha256"):
        raise ValueError("Skill content changed after submission")
    skills_path = "approved/skills/" + validated["slug"] + "/SKILL.md"
    index_path = "approved/skills/index.json"
    try:
        index = json.loads(download(identity, APPROVED_VOLUME, index_path))
    except Exception as error:
        from databricks.sdk.errors import NotFound
        if not isinstance(error, NotFound):
            raise
        index = {"schema_version": 1, "skills": []}
    if index.get("schema_version") != 1 or not isinstance(index.get("skills"), list):
        raise ValueError("Approved skill registry is invalid")
    if any(item.get("slug") == validated["slug"] for item in index["skills"] if isinstance(item, dict)):
        raise ValueError("A skill with this name is already approved")
    uploaded = upload(identity, APPROVED_VOLUME, skills_path, content)
    entry = {"slug": validated["slug"], "name": validated["slug"], "path": skills_path,
             "sha256": uploaded["sha256"], "byte_size": uploaded["byte_size"],
             "approved_by": ADMIN_EMAIL, "approved_at": _now(), "submission_id": request_id,
             "review_note": review_note}
    index["skills"].append(entry)
    upload(identity, APPROVED_VOLUME, index_path, metadata_bytes(index), overwrite=True)
    event = {"schema_version": 1, "kind": "skill_promotion", "event_id": uuid4().hex,
             "submission_id": request_id, "slug": validated["slug"], "content_sha256": uploaded["sha256"],
             "approved_by": ADMIN_EMAIL, "approved_at": entry["approved_at"],
             "review_note": review_note, "advisory_only": True}
    upload(identity, APPROVED_VOLUME, f"approved/skills/events/{event['event_id']}.json", metadata_bytes(event))
    metadata.update(status="approved", reviewed_by=ADMIN_EMAIL, reviewed_at=entry["approved_at"],
                    promoted_path=skills_path, review_note=review_note)
    upload(identity, SUBMISSIONS_VOLUME, metadata_path, metadata_bytes(metadata), overwrite=True)
    return {"status": "approved", "skill": entry, "available_to_all": False,
            "availability_note": "The Volume is owner-controlled until an approved Unity Catalog group receives READ VOLUME."}


def list_approved_skills(identity) -> dict:
    try:
        index = json.loads(download(identity, APPROVED_VOLUME, "approved/skills/index.json"))
    except Exception as error:
        from databricks.sdk.errors import NotFound
        if isinstance(error, NotFound):
            return {"status": "available", "items": []}
        return {"status": "unavailable", "items": []}
    if index.get("schema_version") != 1 or not isinstance(index.get("skills"), list):
        raise ValueError("Approved skill registry is invalid")
    items = []
    for entry in index["skills"][:200]:
        if not isinstance(entry, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", str(entry.get("slug", ""))):
            continue
        items.append({key: entry.get(key) for key in ("slug", "name", "path", "sha256", "byte_size", "approved_at")})
    return {"status": "available", "items": items}


def read_approved_skill(identity, slug: str, offset: int = 0, limit: int = 16000) -> dict:
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", slug):
        raise ValueError("Invalid approved skill name")
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 16000:
        raise ValueError("Invalid approved skill page bounds")
    listing = list_approved_skills(identity)
    if listing.get("status") != "available":
        raise PermissionError("Approved skill Volume access is unavailable")
    entry = next((item for item in listing["items"] if item["slug"] == slug), None)
    if entry is None:
        raise LookupError("Approved skill not found")
    content = download(identity, APPROVED_VOLUME, entry["path"])
    digest = sha256(content).hexdigest()
    if digest != entry["sha256"] or len(content) != entry["byte_size"]:
        raise ValueError("Approved skill integrity check failed")
    text = content.decode("utf-8", errors="strict")
    end = min(offset + limit, len(text))
    return {**entry, "provider": "orion-approved", "skill": slug, "path": "SKILL.md",
            "offset": offset, "next_offset": end if end < len(text) else None,
            "limit": limit, "total_characters": len(text), "content": text[offset:end],
            "status": "verified"}
