"""Explicit versioned Java evidence enrichment over immutable saved understanding.

Canonical XML/model/manifests, IDs, reviews and prior input pins are not rewritten.
Only captured embedded_code sidecars are joined by exact canonical provenance.
No GET parses source or publishes evidence. Hosted POST uses normal full restore
and commit-last publication, never an independent authority or hidden reupload.
"""
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from workbench import delta_state as ds

FORMAT = "source-analysis-java-v1"
HASH = r"^[a-f0-9]{64}$"


def _digest(path):
    with Path(path).open("rb") as stream:
        return __import__("hashlib").file_digest(stream, "sha256").hexdigest()


def _version(document):
    return sha256(ds._json(document).encode()).hexdigest()


def _property_hash(record):
    return _version({k: v for k, v in record.items() if k != "embedded_code"})


def analysis_pointer(directory):
    path = Path(directory) / "source-analysis.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError("Invalid source analysis pointer")
    pointer = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(pointer, dict) or set(pointer) != {"format", "package_hash", "base_understanding_sha256",
            "analysis_version", "artifact_sha256"} or pointer["format"] != FORMAT
            or any(not isinstance(pointer[k], str) or not re.fullmatch(HASH, pointer[k])
                   for k in set(pointer) - {"format"})):
        raise ValueError("Invalid source analysis pointer")
    return pointer


def enrich(model, directory, manifest, pointer):
    """Verify then detach enriched nodes. Never mutate the cached base model."""
    if pointer is None:
        return model
    if (pointer["package_hash"] != manifest["sha256"]
            or pointer["base_understanding_sha256"] != manifest["understanding_sha256"]):
        raise ValueError("Stale source analysis base")
    path = Path(directory) / "analysis" / (pointer["analysis_version"] + ".json.gz")
    if path.is_symlink() or _digest(path) != pointer["artifact_sha256"]:
        raise ValueError("Source analysis artifact checksum mismatch")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(ds.MAX_JSON + 1)
    if len(raw) > ds.MAX_JSON:
        raise ValueError("Source analysis artifact exceeds bound")
    ds._safe(raw)
    document = json.loads(raw)
    if (not isinstance(document, dict) or set(document) != {
            "format", "package_hash", "base_understanding_sha256", "analyzer_hashes", "evidence"}
            or _version(document) != pointer["analysis_version"]
            or any(document[k] != pointer[k] for k in ("format", "package_hash", "base_understanding_sha256"))
            or not isinstance(document["evidence"], dict)):
        raise ValueError("Invalid source analysis document")
    nodes = {n["id"]: n for n in model["nodes"]}
    replacements = {}
    for identifier, item in document["evidence"].items():
        node = nodes.get(identifier)
        if (node is None or set(item) != {"kind", "parent_id", "evidence", "properties"}
                or any(item[k] != node.get(k) for k in ("kind", "parent_id", "evidence"))
                or not isinstance(item["properties"], list)):
            raise ValueError("Source analysis canonical provenance mismatch")
        properties = list(node.get("properties", []))
        seen = set()
        for record in item["properties"]:
            if (not isinstance(record, dict) or set(record) != {"index", "property_sha256", "embedded_code"}
                    or type(record["index"]) is not int or not 0 <= record["index"] < len(properties)
                    or record["index"] in seen or not isinstance(record["embedded_code"], dict)
                    or _property_hash(properties[record["index"]]) != record["property_sha256"]):
                raise ValueError("Source analysis property provenance mismatch")
            seen.add(record["index"])
            properties[record["index"]] = dict(properties[record["index"]], embedded_code=deepcopy(record["embedded_code"]))
        replacements[identifier] = dict(node, properties=properties)
    current = dict(model)
    current["nodes"] = [replacements.get(n["id"], n) for n in model["nodes"]]
    current["source_analysis_version"] = pointer["analysis_version"]
    return current


class RebuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    package_hash: str = Field(pattern=HASH)
    expected_understanding_sha256: str = Field(pattern=HASH)
    expected_analysis_version: str | None
    consent: bool


def rebuild(service, body):
    from workbench.app import PackageIndex, _atomic_json
    from workbench.understanding import analyze_package
    if body.consent is not True:
        raise HTTPException(403, "Explicit source-analysis rebuild consent:true is required")
    with service.lock:
        index = service.require()
        directory = index.directory
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        pointer = analysis_pointer(directory)
        if (body.package_hash != manifest["sha256"]
                or body.expected_understanding_sha256 != manifest["understanding_sha256"]
                or body.expected_analysis_version != (pointer["analysis_version"] if pointer else None)):
            raise HTTPException(409, "Source-analysis selection or version changed; reload before explicit rebuild")
        if not service.package_slot.acquire(blocking=False):
            raise HTTPException(409, "A package job is active; source analysis was not changed")
        if not service.agent_slot.acquire(blocking=False):
            service.package_slot.release()
            raise HTTPException(409, "An agent or draft run is active; source analysis was not changed")
        try:
            source = directory / "source.xml"
            if source.is_symlink() or _digest(source) != manifest["sha256"]:
                raise HTTPException(409, "Saved source checksum mismatch; analysis was not changed")
            fresh = analyze_package(source)
            if fresh["package"]["sha256"] != manifest["sha256"]:
                raise HTTPException(409, "Saved source changed during analysis")
            evidence = {}
            for node in fresh["nodes"]:
                captured = [(i, p) for i, p in enumerate(node.get("properties", [])) if "embedded_code" in p]
                if not captured:
                    continue
                old = index.nodes.get(node["id"])
                if old is None or any(old.get(k) != node.get(k) for k in ("kind", "parent_id", "evidence")):
                    raise HTTPException(409, "New analysis does not match saved canonical provenance; reviewed migration required")
                records = []
                for position, prop in captured:
                    previous = old.get("properties", [])
                    if position >= len(previous) or _property_hash(previous[position]) != _property_hash(prop):
                        raise HTTPException(409, "Java property provenance changed; reviewed migration required")
                    records.append({"index": position, "property_sha256": _property_hash(prop),
                                    "embedded_code": deepcopy(prop["embedded_code"])})
                evidence[node["id"]] = {**{k: deepcopy(node[k]) for k in ("kind", "parent_id", "evidence")},
                                        "properties": records}
            document = {"format": FORMAT, "package_hash": manifest["sha256"],
                        "base_understanding_sha256": manifest["understanding_sha256"],
                        "analyzer_hashes": {name: _digest(Path(__file__).with_name(name))
                                            for name in ("understanding.py", "source_evidence.py")},
                        "evidence": evidence}
            raw = ds._json(document).encode()
            ds._safe(raw)
            if len(raw) > ds.MAX_JSON:
                raise HTTPException(413, "Source analysis exceeds metadata bound")
            version = _version(document)
            destination = directory / "analysis" / (version + ".json.gz")
            compressed = gzip.compress(raw, mtime=0)
            destination.parent.mkdir(exist_ok=True)
            if destination.exists():
                if destination.is_symlink() or destination.read_bytes() != compressed:
                    raise ValueError("Immutable source analysis version conflict")
            else:
                with destination.open("xb") as stream:
                    stream.write(compressed)
            updated = {"format": FORMAT, "package_hash": manifest["sha256"],
                       "base_understanding_sha256": manifest["understanding_sha256"],
                       "analysis_version": version, "artifact_sha256": sha256(compressed).hexdigest()}
            # Validate the new joined model before advancing its explicit pointer.
            enrich(index.model, directory, manifest, updated)
            _atomic_json(directory / "source-analysis.json", updated)
            service.active = PackageIndex.load(directory, cache_scope=service.package_cache_scope)
            return {**updated, "embedded_code_records": len(evidence),
                    "canonical_artifacts_unchanged": True, "historical_reviews_preserved": True,
                    "generation_allowed": False, "deployment_allowed": False,
                    "notice": "Versioned Java evidence refreshed from saved source. Historical approvals are not renewed; output parity and execution remain unverified."}
        finally:
            service.agent_slot.release()
            service.package_slot.release()


def install_source_analysis(app, service):
    @app.get("/api/source-analysis")
    def status():
        index = service.require()
        manifest = json.loads((index.directory / "manifest.json").read_text(encoding="utf-8"))
        pointer = analysis_pointer(index.directory)
        state = "not_rebuilt"
        if pointer:
            path = index.directory / "analysis" / (pointer["analysis_version"] + ".json.gz")
            if _digest(path) != pointer["artifact_sha256"]:
                raise ValueError("Source analysis artifact checksum mismatch")
            with gzip.open(path, "rb") as stream:
                raw = stream.read(ds.MAX_JSON + 1)
            if len(raw) > ds.MAX_JSON:
                raise ValueError("Source analysis artifact exceeds bound")
            document = json.loads(raw)
            if _version(document) != pointer["analysis_version"]:
                raise ValueError("Source analysis version mismatch")
            current_hashes = {name: _digest(Path(__file__).with_name(name))
                              for name in ("understanding.py", "source_evidence.py")}
            state = "current" if document["analyzer_hashes"] == current_hashes else "stale_analyzer"
        return {"package_hash": manifest["sha256"], "understanding_sha256": manifest["understanding_sha256"],
                "analysis_version": pointer["analysis_version"] if pointer else None,
                "analysis_state": state, "explicit_rebuild_required": state != "current",
                "source_reparsed": False}

    @app.post("/api/source-analysis/rebuild")
    def refresh(body: RebuildBody):
        return rebuild(service, body)