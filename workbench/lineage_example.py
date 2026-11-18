"""Opt-in, packaged reference only. Never reads output folders or user state."""
from pathlib import Path
import json
from hashlib import sha256

REFERENCE_SHA256 = "ffb6ffb399e00e7c97f838de4a8c774224834ce0031e2414419daec521ff389c"


def reference():
    path = Path(__file__).parent / "static" / "lineage-example.json"
    raw = path.read_bytes()
    if len(raw) > 32_000 or sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise ValueError("Illustrative reference integrity mismatch")
    value = json.loads(raw)
    if (value.get("format") != "orion-lineage-illustrative-reference-v1"
            or value.get("example_only") is not True
            or value.get("current_user_correlation") is not False
            or value.get("task_correspondences") != []):
        raise ValueError("Invalid illustrative reference")
    return value