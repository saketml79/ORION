"""Local, single-package review services over the canonical understanding model.

SQLite is durable on local disk, not production authentication, tenant isolation,
or a deployment service. Actor names are audit labels, not authenticated users.
Documents and comments are nonexecuting evidence. No model, network, generation,
secret lookup, or environment-file access is performed by this module.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from workbench.understanding import mapping_fingerprints
from workbench.redaction import CREDENTIAL_KEY as _SENSITIVE_KEY, SECRET_TEXT as _SECRET_TEXT, is_secret_key, redact_text


_RELATIONS = {
    "contains", "task_definition", "session_mapping", "instance_of",
    "shortcut_reference", "session_instance", "instance_reference",
    "config_reference", "component_reference", "connection_reference",
    "schedule_reference",
}
_BASE_BLOCKERS = [
    "Lifecycle and operational activity are unknown; owner evidence is required.",
    "Migration scope and behavior disposition require explicit verification.",
    "Source/target connections and environment bindings are unverified.",
    "Transformation, write and control semantics have not been verified.",
]
_SAFE_USAGE_COUNTERS = {"input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens",
                        "known_input_tokens", "known_output_tokens", "known_total_tokens"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _redact(value):
    if isinstance(value, dict):
        return {str(k): (v if str(k) in _SAFE_USAGE_COUNTERS and (v is None or type(v) is int and v >= 0)
                         else "[REDACTED]" if str(k) in _SAFE_USAGE_COUNTERS or is_secret_key(k) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _required(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(f"{label} must be a nonempty string of at most 1024 characters")
    if _SECRET_TEXT.search(value):
        raise ValueError(f"{label} must not contain credentials")
    return value


class _Graph:
    """Forward containment only, plus verified canonical definition references."""

    def __init__(self, model):
        self.nodes = {n["id"]: n for n in model["nodes"]}
        self.edges = model["edges"]
        self.out = defaultdict(list)
        for edge in self.edges:
            if edge["kind"] in _RELATIONS:
                self.out[edge["source"]].append(edge)
        for edges in self.out.values():
            edges.sort(key=lambda e: e["id"])

    def walk(self, start):
        parents = {start: None}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for edge in self.out[current]:
                target = edge["target"]
                if target not in parents:
                    parents[target] = edge
                    queue.append(target)
        return parents

    @staticmethod
    def path(parents, target):
        result = []
        while parents[target] is not None:
            edge = parents[target]
            result.append({k: edge[k] for k in ("id", "source", "target", "kind", "evidence")})
            target = edge["source"]
        return list(reversed(result))

    def workflows(self):
        return sorted((n for n in self.nodes.values() if n["kind"] == "workflow"), key=lambda n: n["id"])

    def workflow(self, workflow_id):
        if self.nodes.get(workflow_id, {}).get("kind") != "workflow":
            raise ValueError("workflow_id must identify a canonical workflow definition")


def _logical(node, layer):
    slug = re.sub(r"[^a-z0-9_]+", "_", node["name"].lower()).strip("_") or "dataset"
    return f"{layer}.{slug}_{_hash(node['id'])[:10]}"


def build_proposals(model: dict, scenario: str = "original") -> list[dict]:
    """Build deterministic provisional workflow proposals, never executable plans.

    Scenarios are 'original' systems or already-landed 'bronze'. IDs include the
    package hash, scenario and workflow identity. Medallion names are suggestions,
    never verified three-part table bindings. Identical metadata is only a reuse
    candidate, not semantic equivalence, retirement advice or safe merge evidence.
    """
    if scenario not in {"original", "bronze"}:
        raise ValueError("scenario must be original or bronze")
    graph = _Graph(model)
    fingerprints = mapping_fingerprints(model)
    proposals = []
    for workflow in graph.workflows():
        reached = graph.walk(workflow["id"])
        members = [graph.nodes[i] for i in sorted(reached)]
        mappings = [n["id"] for n in members if n["kind"] == "mapping"]
        unresolved = [n["id"] for n in members if n["kind"] == "unresolved_reference"
                      or n.get("attributes", {}).get("status") in {"unresolved", "ambiguous"}]
        def endpoints(kind):
            return [{"definition_id": n["id"], "name": n["name"],
                     "scope": deepcopy(n["scope"]), "evidence": deepcopy(n["evidence"]),
                     "suggested_logical_name": _logical(n, "bronze" if kind == "source" else "silver"),
                     "binding_status": "unbound", "bound_table": None,
                     "origin": "original_system" if kind == "source" and scenario == "original"
                     else "suggested_databricks_dataset",
                     "medallion_layer": "bronze" if kind == "source" else "silver"}
                    for n in members if n["kind"] == kind]
        sources, targets = endpoints("source"), endpoints("target")
        blockers = list(_BASE_BLOCKERS)
        if unresolved:
            blockers.append(f"{len(unresolved)} unresolved or ambiguous references/fields require resolution.")
        if not mappings:
            blockers.append("No verified mapping binding was discovered for this workflow.")
        if not sources or not targets:
            blockers.append("Source-to-target coverage is incomplete or absent.")
        if model.get("coverage", {}).get("unsupported_tags"):
            blockers.append("Unsupported package tags require scoped behavior review.")
        proposal = {
            "id": _hash([model["package"]["sha256"], workflow["id"], scenario]),
            "workflow_id": workflow["id"], "name": workflow["name"], "scenario": scenario,
            "version": 1, "status": "provisional", "blockers": blockers,
            "rationale": "Keep the original workflow boundary until operational and semantic evidence supports a change.",
            "alternatives": [
                {"kind": "retain_boundary", "status": "candidate_only",
                 "rationale": "Preserve dependencies, disabled tasks and conditional controls pending verification."},
                {"kind": "decomposition", "status": "candidate_only", "mapping_ids": mappings,
                 "rationale": "Inspect mapping boundaries for possible separation; ordering, transactions and side effects may prohibit it."},
            ] + [{"kind": "identical_metadata", **deepcopy(group), "safe_merge": False,
                  "rationale": "Matching fingerprints are candidates only; connection, scope and semantic verification remain mandatory."}
                 for group in fingerprints if set(group["mapping_ids"]) & set(mappings)],
            "sources": sources, "targets": targets, "mapping_ids": mappings,
            "evidence": [{"object_id": n["id"], **deepcopy(n["evidence"])} for n in members],
            "coverage": {"scoped_objects": len(members), "mapping_count": len(mappings),
                         "unresolved_ids": unresolved, "semantic_status": "unverified",
                         "lifecycle": "unknown", "scope_status": "unverified",
                         "connection_status": "unverified", "behavior_disposition": "pending",
                         "structural_only": True},
            "graph": {"nodes": deepcopy(members), "edges": [deepcopy(e) for e in graph.edges
                       if e["source"] in reached and e["target"] in reached]},
            "summary": (f"Workflow {workflow['name']} references {len(mappings)} mapping definitions, "
                        f"{len(sources)} sources and {len(targets)} targets. "
                        f"Scenario: {scenario}. Bronze inputs and silver outputs are suggested logical names, "
                        "not bound tables. Gold placement requires business-grain evidence. "
                        "Lifecycle is unknown. This is a conservative structural proposal, not an optimization "
                        "or verified translation; external side effects must not be silently replaced."),
            "generation_allowed": False, "deployment_allowed": False,
        }
        proposals.append(_redact(proposal))
    return proposals


def impact_analysis(model: dict, workflow_id: str) -> dict:
    """Report bounded, evidence-backed impact with full category counts.

    Direct means an explicit forward workflow-definition path. Shared means an
    exact mapping/source/target definition identity, not a matching name. Transitive
    means callers of affected workflows through directed references. Shared-resource
    overlap is not recursively flooded across the estate. Paths contain canonical
    edges; shared paths include an explicit reverse leg back to the related workflow.
    Unknown includes unresolved references, never guessed command-text calls.
    """
    graph = _Graph(model)
    graph.workflow(workflow_id)
    closures = {n["id"]: graph.walk(n["id"]) for n in graph.workflows()}
    selected = closures[workflow_id]
    definitions = {i for i in selected if graph.nodes[i]["kind"] in {"mapping", "source", "target"}}
    groups = {k: [] for k in ("direct", "shared", "transitive", "unknown")}
    affected = {workflow_id}
    for other, reached in closures.items():
        if other == workflow_id:
            continue
        common = sorted(definitions & reached.keys())
        if other in selected:
            groups["direct"].append({"workflow_id": other, "name": graph.nodes[other]["name"],
                                      "path": graph.path(selected, other)[:80],
                                      "path_edge_count": len(graph.path(selected, other))})
            affected.add(other)
        elif common:
            via = common[0]
            left, right = graph.path(selected, via), graph.path(reached, via)
            groups["shared"].append({"workflow_id": other, "name": graph.nodes[other]["name"],
                                     "shared_definition_ids": common[:50], "shared_definition_count": len(common),
                                     "path": {"forward": left[:80], "reverse": list(reversed(right))[:80]},
                                     "path_edge_count": len(left) + len(right),
                                     "direction": "selected_to_definition_then_reverse_to_related"})
            affected.add(other)
    # Each closure already follows nested workflow/worklet definition references.
    # This catches callers, without treating all overlaps as directional dataflow.
    for other, reached in closures.items():
        if other in affected:
            continue
        called = sorted(affected & reached.keys())
        if called:
            path = graph.path(reached, called[0])
            groups["transitive"].append({"workflow_id": other, "name": graph.nodes[other]["name"],
                                         "via_workflow_id": called[0], "direction": "caller_to_affected",
                                         "path": path[:80], "path_edge_count": len(path)})
    for i in sorted(selected):
        node = graph.nodes[i]
        if node["kind"] == "unresolved_reference" or node.get("attributes", {}).get("status") in {"unresolved", "ambiguous"}:
            groups["unknown"].append({"object_id": i, "name": node["name"],
                                       "reason": node.get("attributes", {}).get("status", "unresolved"),
                                       "path": graph.path(selected, i)[:80]})
    counts = {k: len(v) for k, v in groups.items()}
    return _redact({"workflow_id": workflow_id, **{k: v[:50] for k, v in groups.items()},
                    "counts": counts, "total_related_workflows": sum(counts[k] for k in ("direct", "shared", "transitive")),
                    "scoped_object_count": len(selected), "workflow_count": len(closures),
                    "limit_per_category": 50, "path_edge_limit": 80,
                    "truncated": any(v > 50 for v in counts.values()),
                    "limitations": ["Lifecycle and runtime impact are unknown.",
                                    "Command-text workflow calls and equal physical table names are not verified references.",
                                    "Shared definitions indicate possible impact, not proven dataflow or safe merging."]})


def common_error_candidates(model: dict, workflow_id: str) -> list[dict]:
    """Suggest shared-mapping review only when exact definition bindings exist.

    No error is asserted to exist and no change is applied. Similar names,
    source/target overlap and unresolved mapping candidates do not qualify.
    """
    graph = _Graph(model)
    graph.workflow(workflow_id)
    closures = {n["id"]: graph.walk(n["id"]) for n in graph.workflows()}
    result = []
    for mapping_id in sorted(closures[workflow_id]):
        if graph.nodes[mapping_id]["kind"] != "mapping":
            continue
        consumers = sorted(w for w, reached in closures.items() if mapping_id in reached)
        if len(consumers) < 2:
            continue
        result.append({"id": _hash([workflow_id, mapping_id, "common_error_review"]),
                       "mapping_id": mapping_id, "workflow_ids": consumers,
                       "status": "candidate_only", "auto_change": False,
                       "applicability": "Only errors independently confirmed in this exact shared mapping definition may affect these consumers; session overrides may differ.",
                       "summary": "Review shared-mapping fixes across verified consumers; no common error has been established.",
                       "evidence": [{"workflow_id": w, "path": graph.path(closures[w], mapping_id)} for w in consumers]})
    return _redact(result)


class Store:
    """Per-method SQLite connections and transactional local review persistence.

    Package hashes namespace a local workbench, not authenticated projects. Use a
    persistent file, not ':memory:'. Proposal snapshots and events are append-only.
    Review versions advance on every action, including comments. Documents and
    settings never satisfy gates. No method grants generation/deployment authority.
    """

    def __init__(self, db_path):
        """Initialize a local database file and immutable-history schema."""
        if str(db_path) == ":memory:":
            raise ValueError("Store requires a durable SQLite file")
        self.db_path = str(Path(db_path).resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                    package_hash TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(package_hash, proposal_id, version));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, package_hash TEXT NOT NULL,
                    proposal_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    package_hash TEXT NOT NULL, document_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(package_hash, document_id));
                CREATE TABLE IF NOT EXISTS settings (
                    package_hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS proposals_no_update BEFORE UPDATE ON proposals
                    BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS proposals_no_delete BEFORE DELETE ON proposals
                    BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
            """)

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, package_hash, proposal_id, kind, payload):
        record = _redact({**payload, "created_at": datetime.now(timezone.utc).isoformat()})
        cursor = db.execute("INSERT INTO events(package_hash,proposal_id,kind,payload) VALUES(?,?,?,?)",
                            (package_hash, proposal_id, kind, _json(record)))
        return {"sequence": cursor.lastrowid, "package_hash": package_hash,
                "proposal_id": proposal_id, "kind": kind, **record}

    def save_proposals(self, package_hash: str, proposals: list[dict]) -> list[dict]:
        """Register version-one drafts atomically; identical retries are idempotent.

        Existing originals cannot be overwritten, reset or used to clear gates.
        All imported drafts retain mandatory local gates regardless of input flags.
        """
        _required(package_hash, "package_hash")
        with self._connection(write=True) as db:
            for original in proposals:
                proposal = _redact(deepcopy(original))
                proposal_id = _required(proposal["id"], "proposal id")
                if proposal.get("version") != 1 or proposal.get("status") != "provisional":
                    raise ValueError("Only provisional version-one proposals can be registered")
                if proposal.get("scenario") not in {"original", "bronze"}:
                    raise ValueError("Invalid proposal scenario")
                proposal["blockers"] = list(dict.fromkeys(_BASE_BLOCKERS + proposal.get("blockers", [])))
                proposal.update(generation_allowed=False, deployment_allowed=False)
                existing = db.execute("SELECT payload FROM proposals WHERE package_hash=? AND proposal_id=? AND version=1",
                                      (package_hash, proposal_id)).fetchone()
                if existing:
                    if existing["payload"] != _json(proposal):
                        raise ValueError("Original proposal is immutable")
                    continue
                db.execute("INSERT INTO proposals VALUES(?,?,?,?)", (package_hash, proposal_id, 1, _json(proposal)))
                self._event(db, package_hash, proposal_id, "proposal_registered", {"version": 1})
        return self.load_state(package_hash)["proposals"]

    def load_state(self, package_hash: str) -> dict:
        """Return a consistent snapshot of effective proposals and immutable history."""
        _required(package_hash, "package_hash")
        with self._connection() as db:
            revisions = [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM proposals WHERE package_hash=? ORDER BY proposal_id,version", (package_hash,))]
            current = {p["id"]: p for p in revisions}
            events = self._events(db, package_hash)
            documents = [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM documents WHERE package_hash=? ORDER BY document_id", (package_hash,))]
            settings = db.execute("SELECT payload FROM settings WHERE package_hash=?", (package_hash,)).fetchone()
            return {"package_hash": package_hash, "scope": "local_single_package_not_production_auth",
                    "proposals": list(current.values()), "history": revisions, "events": events,
                    "reviews": [e for e in events if e["kind"] == "review"], "documents": documents,
                    "settings": json.loads(settings["payload"]) if settings else {},
                    "generation_allowed": False, "deployment_allowed": False}

    @staticmethod
    def _events(db, package_hash):
        return [{"sequence": row["sequence"], "package_hash": package_hash,
                 "proposal_id": row["proposal_id"], "kind": row["kind"], **json.loads(row["payload"])}
                for row in db.execute("SELECT * FROM events WHERE package_hash=? ORDER BY sequence", (package_hash,))]

    def save_review(self, package_hash: str, proposal_id: str, expected_version: int,
                    action: str, comment: str, actor: str = "local-reviewer", anchor=None) -> dict:
        """Append a version-checked review and new immutable effective snapshot.

        Actions: comment, request_changes, reject, request_rework, approve. Rework
        creates a draft revision only, with unchanged evidence and blockers, never
        a fabricated agent result. Approve fails while gates remain; this local
        structural-only service has no evidence-verification gate-clearing API.
        Reviewer text/anchors are inert and cannot authorize code generation.
        """
        _required(package_hash, "package_hash")
        _required(proposal_id, "proposal_id")
        _required(actor, "actor")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        if action not in {"comment", "request_changes", "reject", "request_rework", "approve"}:
            raise ValueError("Unknown review action")
        if not isinstance(comment, str):
            raise ValueError("comment must be text")
        if anchor is not None and not isinstance(anchor, (str, dict)):
            raise ValueError("anchor must be text or evidence metadata")
        with self._connection(write=True) as db:
            row = db.execute("SELECT payload FROM proposals WHERE package_hash=? AND proposal_id=? ORDER BY version DESC LIMIT 1",
                             (package_hash, proposal_id)).fetchone()
            if row is None:
                raise ValueError("Unknown proposal in this package")
            proposal = json.loads(row["payload"])
            if proposal["version"] != expected_version:
                raise ValueError(f"Version conflict: expected {expected_version}, effective {proposal['version']}")
            if action == "approve":
                raise ValueError("Approval blocked: lifecycle, scope, connection and semantic gates cannot be cleared through review text")
            proposal["version"] += 1
            proposal["status"] = {"request_changes": "changes_requested", "reject": "rejected",
                                   "request_rework": "draft"}.get(action, proposal["status"])
            if action == "request_rework":
                proposal["revision_of"] = expected_version
                proposal["rework_status"] = "requested_not_executed"
            proposal.update(generation_allowed=False, deployment_allowed=False)
            event = self._event(db, package_hash, proposal_id, "review",
                                {"action": action, "comment": comment, "actor": actor, "anchor": anchor,
                                 "expected_version": expected_version, "version": proposal["version"],
                                 "status": proposal["status"], "nonexecuting": True})
            db.execute("INSERT INTO proposals VALUES(?,?,?,?)",
                       (package_hash, proposal_id, proposal["version"], _json(proposal)))
            return {**proposal, "review": event}

    def get_reviews(self, package_hash: str) -> list:
        """Return ordered immutable reviewer events for this local package."""
        _required(package_hash, "package_hash")
        with self._connection() as db:
            return [e for e in self._events(db, package_hash) if e["kind"] == "review"]

    def save_document(self, package_hash: str, filename: str, text: str, metadata: dict) -> dict:
        """Persist redacted, inert supporting evidence; never read the named file.

        Only a display basename is accepted. Redaction is regex-based defense in
        depth, not a guarantee of detecting unlabeled secrets. Do not upload secrets.
        Content-addressed records preserve prior versions and are idempotent.
        """
        _required(package_hash, "package_hash")
        _required(filename, "filename")
        if filename in {".", ".."} or any(c in filename for c in '/\\:\x00'):
            raise ValueError("filename must be a display basename")
        if not isinstance(text, str) or not isinstance(metadata, dict):
            raise ValueError("document requires text and metadata dict")
        safe = _redact({"filename": filename, "text": text, "metadata": metadata})
        document = {"id": _hash(safe), "package_hash": package_hash, **safe, "nonexecuting": True}
        with self._connection(write=True) as db:
            cursor = db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?)", (package_hash, document["id"], _json(document)))
            if cursor.rowcount:
                self._event(db, package_hash, None, "document_saved", {"document_id": document["id"], "filename": safe["filename"]})
        return document

    def documents(self, package_hash: str) -> list:
        """Return redacted supporting documents in stable content-ID order."""
        _required(package_hash, "package_hash")
        with self._connection() as db:
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM documents WHERE package_hash=? ORDER BY document_id", (package_hash,))]

    def settings(self, package_hash: str, values: dict | None = None) -> dict:
        """Read or patch nonsecret repository, branch and host display settings.

        Hosts are HTTPS origins without userinfo, path, query or fragment. These
        values never initiate connectivity. Unknown keys and credential material
        are rejected rather than stored. Empty strings leave settings unset.
        Authentication belongs outside this MVP. Legacy host remains supported.
        """
        _required(package_hash, "package_hash")
        if values is not None:
            host_keys = {"host", "dev_host", "test_host", "prod_host"}
            supported_keys = {"github_owner", "github_repo", "default_branch"} | host_keys
            if not isinstance(values, dict) or set(values) - supported_keys:
                raise ValueError("Only github_owner, github_repo, default_branch, host, dev_host, test_host and prod_host settings are accepted; no secrets")
            for key, value in values.items():
                if value == "":
                    continue
                _required(value, key)
                if value != value.strip():
                    raise ValueError("Settings cannot contain surrounding whitespace")
                if key == "github_owner" and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value):
                    raise ValueError("Invalid GitHub owner")
                if key == "github_owner" and "--" in value:
                    raise ValueError("Invalid GitHub owner")
                if key == "github_repo" and (not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) or value in {".", ".."}):
                    raise ValueError("Invalid GitHub repository")
                if key == "default_branch":
                    if (value == "@" or value.startswith("-") or ".." in value or "@{" in value
                            or re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\]", value)
                            or any(not part or part.startswith(".") or part.endswith((".", ".lock"))
                                   for part in value.split("/"))):
                        raise ValueError("Invalid Git branch")
                if key in host_keys:
                    try:
                        parsed = urlsplit(value)
                        port = parsed.port
                        valid = (parsed.scheme == "https" and parsed.hostname and "@" not in parsed.netloc
                                 and parsed.path in {"", "/"} and "?" not in value and "#" not in value
                                 and not re.search(r"[\s\x00-\x1f\x7f\\%]", value)
                                 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parsed.hostname)
                                 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                         for label in parsed.hostname.split("."))
                                 and not parsed.netloc.endswith(":")
                                 and (port is None or 1 <= port <= 65535))
                    except ValueError:
                        valid = False
                    if not valid:
                        raise ValueError(f"{key} must be a credential-free HTTPS origin")
        with self._connection(write=values is not None) as db:
            row = db.execute("SELECT payload FROM settings WHERE package_hash=?", (package_hash,)).fetchone()
            result = json.loads(row["payload"]) if row else {}
            if values is not None:
                result.update(values)
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(package_hash) DO UPDATE SET payload=excluded.payload",
                           (package_hash, _json(result)))
                self._event(db, package_hash, None, "settings_saved", {"settings": result})
            return result