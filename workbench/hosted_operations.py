"""Opt-in, process-local async actions with a verified append-only Delta journal.

Integration contract: call ``handle`` through run_in_threadpool AFTER trusted
OBO authentication and BEFORE the dispatcher's per-user action lock. ``execute``
must enter that existing lock and run the old synchronous hosted action, including
its durable commit. This module does not install routes or change the dispatcher.
Only its worker closure retains the initiating OAuth client and action arguments.
No replay, distributed lock, credential persistence, or background reauthentication.
One app replica is required. Close the independent executor on app shutdown.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
from contextvars import ContextVar
from hashlib import sha256
import json
import re
import tempfile
from threading import BoundedSemaphore, Lock
from urllib.parse import parse_qsl
from uuid import uuid4

from workbench import delta_state as ds
from workbench.databricks_models import AuthenticationUnavailable
from workbench.delta_runtime import RequestStateStore
from workbench.hosted_storage import volume_path

API = "/api/hosted/operations"
MAX_BODY = 32 * 1024
MAX_HISTORY = 48
RESULT_CHUNK_BYTES = 20 * 1024
MAX_RESULT_BYTES = 640 * 1024
RECOVERY = ("Original app process ended. No automatic replay. "
            "Verify committed state before retrying.")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PROJECT_ACTION = re.compile(r"/api/projects/[a-f0-9]{32}/analyze\Z")
_UPLOAD_ACTION = re.compile(r"/api/hosted/uploads/[a-f0-9]{32}/finalize\Z")
_SPARK_DRAFT_ACTION = re.compile(r"/api/redesigns/[a-f0-9]{64}/spark-drafts\Z")
_COHORT_DESIGN_ACTION = re.compile(r"/api/cohorts/[a-f0-9]{32}/workflows/[a-f0-9]{64}/design\Z")
_COHORT_BATCH_ACTION = re.compile(r"/api/cohorts/([a-f0-9]{32})/design\Z")
_COHORT_DRAFT_ACTION = re.compile(r"/api/cohorts/([a-f0-9]{32})/draft\Z")
_NATIVE_BUNDLE_ACTION = re.compile(r"/api/jobs/[a-f0-9]{32}/native-bundles\Z")
_READ_ACTIONS = {
    "/api/overview", "/api/objects", "/api/package-graph", "/api/proposals",
    "/api/redesigns", "/api/reviews", "/api/impact",
    "/api/readiness", "/api/architecture-knowledge", "/api/package/warm",
}
_OBJECT_READ = re.compile(r"/api/object/[A-Za-z0-9%._~-]{1,512}\Z")
_CONTEXT_READ = re.compile(r"/api/workflows/[a-f0-9]{64}/migration-context\Z")
_PROPOSAL_READ = re.compile(r"/api/proposals/[a-f0-9]{64}\Z")
_REDESIGN_READ = re.compile(r"/api/redesigns/[a-f0-9]{64}\Z")
_IMPACT_READ = re.compile(r"/api/impact/[a-f0-9]{64}\Z")
_JOB_READ = re.compile(r"/api/jobs/[a-f0-9]{32}(?:/spark-drafts)?\Z")
_AGENT_RUN_READ = re.compile(r"/api/agents/runs(?:/[a-f0-9]{32})?\Z")
_TERMINAL = {"completed", "failed", "authentication_required"}
_PROGRESS_STAGES = {"queued", "running", "starting", "provisioning", "audit_restore", "metadata_restore", "reading_history", "artifact_files",
                    "verifying_artifact", "decoding_nodes", "decoding_edges", "index_cache",
                    "indexing_nodes", "indexing_edges",
                    "preparing_package", "querying_inventory", "committing_delta", "responding", "committed",
                    "failed", "authentication_required", "interrupted", "route_validate", "route_commit", "route_readback"}
_PROGRESS_UNITS = {None, "files", "nodes", "edges", "objects", "bytes", "records"}
_CURRENT_PROGRESS = ContextVar("hosted_operation_progress", default=None)
_FAILURE_PHASES = {"action_execution", "action_status", "result_validation", "result_chunks",
                   "journal_readback"}
# Only namespace strings and locks, never clients, requests, results or Futures.
_LOCKS_GUARD = Lock()
_LOCKS: dict[tuple[str, str], dict[str, Lock]] = {}


def _lock(workspace, user, operation):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault((workspace, user), {}).setdefault(operation, Lock())


def current_progress():
    """Progress reporter for the current hosted operation worker, if any."""
    return _CURRENT_PROGRESS.get()


def _progress(status, *, stage=None, completed=None, total=None, unit=None):
    if status == "queued":
        return {"stage": "queued", "completed": 0, "total": None, "unit": None}
    if status == "completed":
        return {"stage": "committed", "completed": 1, "total": 1, "unit": "operation"}
    if status in {"failed", "authentication_required"}:
        return {"stage": status, "completed": 0, "total": None, "unit": None}
    if status == "running" and stage is None:
        return {"stage": "running", "completed": 0, "total": None, "unit": None}
    return {"stage": stage or "running", "completed": completed,
            "total": total, "unit": unit}


def _valid_progress(status, value):
    if not isinstance(value, dict) or set(value) != {"stage", "completed", "total", "unit"}:
        return False
    stage, completed, total, unit = (value[k] for k in ("stage", "completed", "total", "unit"))
    if status != "running":
        return value == _progress(status)
    if stage not in _PROGRESS_STAGES or unit not in _PROGRESS_UNITS:
        return False
    if completed is not None and (type(completed) is not int or completed < 0):
        return False
    if total is not None and (type(total) is not int or total < 0):
        return False
    if total is not None and (completed is None or completed > total):
        return False
    if total is None and (unit is not None or (completed is not None
            and not (stage == "running" and completed == 0))):
        return False
    return True


def _authentication(error):
    # Statement API errors deliberately strip messages, but preserve safe codes.
    return (isinstance(error, AuthenticationUnavailable)
            or type(error).__name__ == "Unauthenticated"
            or getattr(error, "error_code", None) == "UNAUTHENTICATED"
            or getattr(error, "status_code", None) == 401)


def _error(error):
    auth = _authentication(error)
    detail = "User authentication is required." if auth else "Hosted operation failed."
    message = error.args[0] if isinstance(error, RuntimeError) and len(error.args) == 1 else None
    if (not auth and isinstance(message, str)
            and re.fullmatch(r"Hosted action failed; diagnostic [a-f0-9]{32}(?: phase [A-Za-z][A-Za-z0-9_:.-]{0,80})?", message)):
        detail = message
    return {"detail": detail,
            **ds.safe_error(error)}


def _valid_error(error, status):
    if not isinstance(error, dict) or set(error) != {"detail", "error_type", "error_code"}:
        return False
    kind = error["error_type"]
    if not isinstance(kind, str):
        return False
    # Construct only an inert Exception type, never import/evaluate stored names.
    # Reuse the canonical safe_error allowlists rather than copying/drifting them.
    recorded = type(kind, (Exception,), {
        "error_code": error["error_code"],
        "status_code": 401 if status == "authentication_required" else None,
    })()
    expected = _error(recorded)
    if error == expected:
        return True
    return (status == "failed" and error.get("error_type") == expected["error_type"]
        and error.get("error_code") == expected["error_code"]
        and isinstance(error.get("detail"), str)
        and re.fullmatch(r"Hosted action failed; diagnostic [a-f0-9]{32}(?: phase [A-Za-z][A-Za-z0-9_:.-]{0,80})?",
                         error["detail"]) is not None)


def _json(raw):
    """Strict bounded JSON, including duplicate-key and non-finite rejection."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Non-finite JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _checked(value, forbidden=()):
    raw = ds._json(value).encode("utf-8")
    ds._safe(value)
    if any(secret and secret.encode("utf-8") in raw for secret in forbidden):
        raise ValueError("Credential material cannot be persisted")
    return raw


class HostedOperations:
    """Bounded worker pool; durable state is always read using the polling user."""

    def __init__(self, warehouse, process_id=None):
        self.warehouse = ds._text(warehouse)
        self.process_id = ds._text(process_id if process_id is not None else uuid4().hex)
        ds._safe(self.process_id)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hosted-operation")
        self._slots = BoundedSemaphore(8)
        self._lifecycle = Lock()
        self._closed = False
        self._live_lock = Lock()
        self._live_agents = {}

    def close(self, wait=True):
        """Drain accepted work, including queued jobs; never silently cancel it."""
        with self._lifecycle:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _store(self, identity, remote):
        if identity.mode != "obo":
            raise AuthenticationUnavailable("OBO identity required")
        parts = volume_path(remote + "/HEAD.json").split("/")
        namespace = sha256((identity.host + "\n" + identity.user_id).encode()).hexdigest()
        if parts[5:] != ["users", namespace, "HEAD.json"]:
            raise PermissionError("Operation namespace mismatch")
        store = RequestStateStore(identity.client, self.warehouse, parts[2], parts[3], identity.user_id)
        if store.workspace_id != identity.host:
            raise PermissionError("Operation workspace mismatch")
        store._authorize()
        # Already created by project creation. Never run full ensure_tables here.
        store._verify_table("jobs", store._authorize())
        return store

    @staticmethod
    def _scope(operation):
        return dict(project_id="__operations__", package_hash="__none__",
                    context_id="hosted-operation:" + operation)

    def _record(self, store, operation, sequence, payload):
        context = self._scope(operation)
        return dict(store.make_record(
            "jobs", **context, source_path="hosted-operations", source_table="operations",
            source_key=str(sequence), payload=payload, sequence=sequence,
            kind=payload["process_id"], status=payload["status"]),
            migration_id=context["context_id"])

    def _read(self, store, operation):
        # At most 20 immutable stages. A 50-row page also detects overflow rather
        # than trusting a truncated tail or selecting an ambiguous MAX(sequence).
        rows = store.read_table("jobs", **self._scope(operation), limit=50)
        if len(rows) > MAX_HISTORY:
            raise ValueError("Operation history limit exceeded")
        stages = {}
        fingerprint, process = None, None
        for row in rows:
            if (set(row) != set(ds.COLUMNS)
                    or any(type(row[k]) is not (int if t == "BIGINT" else str)
                           for k, t in ds.COLUMNS.items())):
                raise ValueError("Invalid operation envelope")
            sequence = row["sequence"]
            if sequence in stages or not 1 <= sequence <= MAX_HISTORY:
                raise ValueError("Ambiguous operation sequence")
            payload = _json(row["payload"])
            if not isinstance(payload, dict):
                raise ValueError("Invalid operation payload")
            status = payload.get("status")
            expected = {"operation_id", "process_id", "fingerprint", "status", "progress"}
            if status == "completed":
                if ("result" in payload) == ("result_chunks" in payload):
                    raise ValueError("Invalid completed operation result representation")
                expected |= {"status_code", "result" if "result" in payload else "result_chunks"}
            elif status in {"failed", "authentication_required"}:
                expected |= {"error"}
                if "failure_phase" in payload:
                    expected.add("failure_phase")
                    if payload["failure_phase"] not in _FAILURE_PHASES:
                        raise ValueError("Invalid operation failure phase")
                if "action_status" in payload:
                    expected.add("action_status")
                    if (payload.get("failure_phase") != "action_status"
                            or type(payload["action_status"]) is not int
                            or not 100 <= payload["action_status"] <= 599
                            or 200 <= payload["action_status"] < 300):
                        raise ValueError("Invalid failed action status")
            elif status not in {"queued", "running"}:
                raise ValueError("Invalid operation state")
            if status == "running" and "result_chunk" in payload:
                expected.add("result_chunk")
            if (set(payload) != expected or payload["operation_id"] != operation
                    or not isinstance(payload["fingerprint"], str)
                    or not _HASH.fullmatch(payload["fingerprint"])
                    or not isinstance(payload["process_id"], str)
                    or not payload["process_id"]
                    or not _valid_progress(status, payload["progress"])):
                raise ValueError("Invalid operation state envelope")
            if status == "completed" and (type(payload["status_code"]) is not int
                                          or not 200 <= payload["status_code"] < 300):
                raise ValueError("Invalid completion code")
            if "result_chunk" in payload:
                chunk = payload["result_chunk"]
                if (not isinstance(chunk, dict) or set(chunk) != {"index", "count", "sha256", "data"}
                        or type(chunk["index"]) is not int or type(chunk["count"]) is not int
                        or not 0 <= chunk["index"] < chunk["count"] <= MAX_HISTORY - 3
                        or not isinstance(chunk["sha256"], str) or not _HASH.fullmatch(chunk["sha256"])
                        or not isinstance(chunk["data"], str) or len(chunk["data"]) > 4 * RESULT_CHUNK_BYTES):
                    raise ValueError("Invalid hosted operation result chunk")
                base64.b64decode(chunk["data"], validate=True)
            if "result_chunks" in payload:
                receipt = payload["result_chunks"]
                if (not isinstance(receipt, dict) or set(receipt) != {"count", "sha256"}
                        or type(receipt["count"]) is not int or not 1 <= receipt["count"] <= MAX_HISTORY - 3
                        or not isinstance(receipt["sha256"], str) or not _HASH.fullmatch(receipt["sha256"])):
                    raise ValueError("Invalid hosted operation result chunk receipt")
            if status in {"failed", "authentication_required"}:
                error = payload["error"]
                if not _valid_error(error, status):
                    raise ValueError("Invalid safe error")
            _checked(payload)
            if row != self._record(store, operation, sequence, payload):
                raise ValueError("Operation checksum or exact envelope mismatch")
            if fingerprint is not None and (fingerprint, process) != (payload["fingerprint"], payload["process_id"]):
                raise ValueError("Operation identity changed")
            fingerprint, process = payload["fingerprint"], payload["process_id"]
            stages[sequence] = payload
        ordered = [stages[n] for n in sorted(stages)]
        if sorted(stages) != list(range(1, len(stages) + 1)):
            raise ValueError("Operation history has gaps")
        for index, payload in enumerate(ordered):
            previous = ordered[index - 1]["status"] if index else None
            if not ((previous is None and payload["status"] == "queued")
                    or (previous == "queued" and payload["status"] in {"running", "failed", "authentication_required"})
                    or (previous == "running" and payload["status"] == "running")
                    or (previous == "running" and payload["status"] in _TERMINAL)):
                raise ValueError("Invalid operation transition")
            completed = ordered[-1] if ordered and ordered[-1]["status"] == "completed" else None
            if completed and "result_chunks" in completed:
                receipt = completed["result_chunks"]
                chunks = [item["result_chunk"] for item in ordered if "result_chunk" in item]
                if (len(chunks) != receipt["count"]
                    or sorted(chunk["index"] for chunk in chunks) != list(range(receipt["count"]))
                    or any(chunk["count"] != receipt["count"] or chunk["sha256"] != receipt["sha256"]
                       for chunk in chunks)):
                        raise ValueError("Incomplete hosted operation result chunks")
        return ordered

    def _append(self, store, operation, history, payload):
        # Freeze JSON values before publication. Validation/readback must compare
        # the same value, not a caller-owned result or progress dictionary.
        payload = _json(_checked(payload))
        history = _json(_checked(history))
        if not _valid_progress(payload["status"], payload["progress"]):
            raise ValueError("Invalid operation progress before publication")
        sequence = len(history) + 1
        if sequence > MAX_HISTORY:
            raise ValueError("Operation history limit exceeded")
        row = self._record(store, operation, sequence, payload)
        store.upsert_records("jobs", [row], migration_id=row["migration_id"])
        if self._read(store, operation) != [*history, payload]:
            error = RuntimeError("Operation append readback failed")
            error.hosted_failure_phase = "journal_readback"
            raise error

    def _observation(self, store, operation):
        from workbench.operation_reconciliation import read_observation
        return read_observation(self, store, operation)

    def _observed_public(self, store, payload, history):
        public = self._public(payload, history)
        if payload["status"] == "failed":
            observation = self._observation(store, payload["operation_id"])
            if observation is not None:
                if observation["request_fingerprint"] != payload["fingerprint"]:
                    raise ValueError("Reconciliation request mismatch")
                public["observation"] = observation
                public["recovery"] = ("Committed analysis independently verified after acknowledgement failure. "
                                      "Original execution remains failed. Do not replay the rebuild.")
        return public

    def _public(self, payload, history=None):
        public = {k: v for k, v in payload.items() if k != "fingerprint"}
        if payload.get("status") == "completed" and "result_chunks" in payload:
            chunks = [item["result_chunk"] for item in (history or []) if "result_chunk" in item]
            chunks.sort(key=lambda item: item["index"])
            raw = b"".join(base64.b64decode(item["data"], validate=True) for item in chunks)
            if (sha256(raw).hexdigest() != payload["result_chunks"]["sha256"]
                    or len(raw) > MAX_RESULT_BYTES):
                raise ValueError("Hosted operation result checksum or size mismatch")
            public["result"] = _json(raw)
            public.pop("result_chunks", None)
        if payload["process_id"] != self.process_id and payload["status"] in {"queued", "running"}:
            public = {**public, "status": "interrupted", "progress": _progress("interrupted"),
                      "recovery": RECOVERY}
        return public

    @staticmethod
    def _request(body, forbidden):
        if isinstance(body, (bytes, str)):
            if len(body if isinstance(body, bytes) else body.encode("utf-8")) > MAX_BODY:
                raise ValueError("Operation request too large")
            body = _json(body)
        if (not isinstance(body, dict) or set(body) - {"id", "method", "path", "query", "body"}
                or not {"id", "method", "path", "body"} <= set(body)
                or not isinstance(body["id"], str) or not _ID.fullmatch(body["id"])
                or body["method"] not in {"POST", "GET"} or not isinstance(body["path"], str)
                or not ((body["method"] == "POST" and (body["path"] in {"/api/agents/run", "/api/loops/run", "/api/source-analysis/rebuild", "/api/architecture-knowledge/curate", "/api/models/select"}
                    or _PROJECT_ACTION.fullmatch(body["path"]) or _UPLOAD_ACTION.fullmatch(body["path"])
                    or _SPARK_DRAFT_ACTION.fullmatch(body["path"]) or _COHORT_DESIGN_ACTION.fullmatch(body["path"])
                    or _COHORT_BATCH_ACTION.fullmatch(body["path"])
                    or _COHORT_DRAFT_ACTION.fullmatch(body["path"])
                    or _NATIVE_BUNDLE_ACTION.fullmatch(body["path"])))
                    or (body["method"] == "GET" and (body["path"] in _READ_ACTIONS
                    or _OBJECT_READ.fullmatch(body["path"]) or _CONTEXT_READ.fullmatch(body["path"]) or _PROPOSAL_READ.fullmatch(body["path"])
                    or _REDESIGN_READ.fullmatch(body["path"]) or _IMPACT_READ.fullmatch(body["path"])
                    or _JOB_READ.fullmatch(body["path"]) or _AGENT_RUN_READ.fullmatch(body["path"]))))
                or not isinstance(body["body"], dict)):
            raise ValueError("Invalid operation request")
        if body["method"] == "GET" and body["body"]:
            raise ValueError("Read operations cannot contain a request body")
        if body["path"] == "/api/models/select":
            selection = body["body"]
            if (set(selection) != {"endpoint", "tool_support_confirmed", "expected_route_version"}
                    or selection.get("tool_support_confirmed") is not True
                    or not isinstance(selection.get("endpoint"), str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", selection["endpoint"])
                    or not isinstance(selection.get("expected_route_version"), str)
                    or not _HASH.fullmatch(selection["expected_route_version"])):
                raise ValueError("Invalid model selection metadata")
        if _COHORT_DESIGN_ACTION.fullmatch(body["path"]) and body["body"].get("operation_id") != body["id"]:
            raise ValueError("Cohort action must use its recorded operation ID")
        if (_COHORT_BATCH_ACTION.fullmatch(body["path"])
            and (set(body["body"]) != {"consent"} or body["body"]["consent"] is not True)):
            raise ValueError("Cohort worker requires explicit model consent")
        if _COHORT_DRAFT_ACTION.fullmatch(body["path"]):
            draft = body["body"]
            root = draft.get("workspace_root")
            if (set(draft) != {"consent", "workspace_root"} or draft.get("consent") is not True
                or not isinstance(root, str) or not re.fullmatch(r"/Workspace/[A-Za-z0-9_./-]{1,200}", root)
                or any(part in ("", ".", "..") for part in root.split("/")[1:])):
                raise ValueError("Cohort drafting requires consent and a safe workspace destination")
        query = body.get("query", "")
        if (not isinstance(query, str) or len(query) > 4096
                or any(ord(c) < 32 for c in query) or query.startswith("?")):
            raise ValueError("Invalid operation query")
        if (_COHORT_BATCH_ACTION.fullmatch(body["path"]) or _COHORT_DRAFT_ACTION.fullmatch(body["path"])) and query:
            raise ValueError("Cohort worker does not accept query parameters")
        for key, value in parse_qsl(query, keep_blank_values=True, max_num_fields=100):
            ds._safe({key: value})
            _checked(value, forbidden)
        if len(_checked(body, forbidden)) > MAX_BODY:
            raise ValueError("Operation request too large")
        # Deep copy to prevent a caller mutating the request after acceptance.
        request = _json(ds._json({"method": body["method"], "path": body["path"],
                                 "query": query, "body": body["body"]}))
        return body["id"], request, ds._hash(request)

    def handle(self, identity, remote, method, path, query, headers, body, execute):
        """Return (HTTP status, JSON payload), or None for an unrecognized route.

        POST accepts JSON bytes or a decoded dict. Query arguments are URL query
        strings without '?'. Worker headers have credentials removed. Results are
        actual public action JSON, subject to strict secret and payload limits.
        """
        match = re.fullmatch(re.escape(API) + r"/([a-f0-9]{32})", path)
        reconcile = re.fullmatch(re.escape(API) + r"/([a-f0-9]{32})/reconcile", path)
        if not ((method == "POST" and (path == API or reconcile is not None))
            or (method == "GET" and match is not None)):
            return None
        try:
            store = self._store(identity, remote)
            if reconcile is not None:
                if query:
                    return 400, {"detail": "Reconciliation does not accept query arguments."}
                from workbench.delta_runtime import DeltaRuntime
                from workbench.operation_reconciliation import reconcile_analysis
                # This endpoint verifies an existing commit. It never calls
                # execute(), restore(), flush(), source parsing or a model.
                with _lock(store.workspace_id, store.user_id, reconcile[1]):
                    with tempfile.TemporaryDirectory(prefix="workbench-reconcile-") as temporary:
                        runtime = DeltaRuntime(lambda: identity, remote, temporary, warehouse=self.warehouse)
                        observation = reconcile_analysis(self, store, runtime, reconcile[1], body, headers)
                return 200, {"status": "failed", "observation": observation,
                             "original_history_preserved": True, "mutation_replayed": False}
            if method == "GET":
                assert match is not None
                operation = match[1]
                with _lock(store.workspace_id, store.user_id, operation):
                    history = self._read(store, operation)
                if not history:
                    return 404, {"detail": "Operation not found."}
                public = self._observed_public(store, history[-1], history)
                if public["status"] == "running" and public["process_id"] == self.process_id:
                    with self._live_lock:
                        live = self._live_agents.get((store.workspace_id, store.user_id, operation))
                        if live is not None:
                            public["live_agent"] = dict(live)
                return 200, public
            if query:
                return 400, {"detail": "Operation endpoint does not accept query arguments."}
            forbidden, clean = [], {}
            for name, value in headers.items():
                if any(word in name.lower() for word in ("authorization", "token", "cookie", "secret", "credential", "api-key")):
                    if isinstance(value, str) and value:
                        forbidden.extend((value, value.removeprefix("Bearer ")))
                else:
                    clean[name] = value
            # Include opaque initiating credentials even if the caller has already
            # stripped proxy headers. Never retain these on the service or journal.
            for value in identity.client.config.authenticate().values():
                if isinstance(value, str) and value:
                    forbidden.extend((value, value.removeprefix("Bearer ")))
            try:
                operation, request, fingerprint = self._request(body, forbidden)
                if request["path"] == "/api/models/select":
                    if not _HASH.fullmatch(str(request["body"].get("expected_route_version", ""))):
                        raise ValueError("Model selection requires an exact saved route version")
                    fingerprint = ds._hash({"request": request, "scope": {
                        key: headers.get(key, "") for key in ("x-workbench-project", "x-workbench-package")}})
            except (ValueError, TypeError, RecursionError, UnicodeError):
                return 400, {"detail": "Invalid or unsafe hosted operation request."}
            with _lock(store.workspace_id, store.user_id, operation):
                history = self._read(store, operation)
                if history:
                    if history[-1]["fingerprint"] != fingerprint:
                        return 409, {"detail": "Operation ID already has a different request."}
                    public = self._observed_public(store, history[-1], history)
                    return (202 if public["status"] in {"queued", "running"} else 200), public
                with self._lifecycle:
                    if self._closed:
                        return 503, {"detail": "Hosted operations are closed."}
                if not self._slots.acquire(blocking=False):
                    return 429, {"detail": "Hosted operation capacity reached."}
                submitted = False
                try:
                    accepted = {"operation_id": operation, "process_id": self.process_id,
                                "fingerprint": fingerprint, "status": "queued", "progress": _progress("queued")}
                    self._append(store, operation, [], accepted)

                    def work():
                        # The only retained identity/client is in this short-lived
                        # closure. execute owns the existing per-user dispatcher lock.
                        try:
                            self._work(identity, remote, operation, request, clean, forbidden, execute)
                        finally:
                            self._slots.release()

                    try:
                        with self._lifecycle:
                            if self._closed:
                                raise RuntimeError("Executor closed")
                            self._executor.submit(work)
                            submitted = True
                    except Exception as error:
                        failed = {**accepted, "status": "failed", "progress": _progress("failed"), "error": _error(error)}
                        self._append(store, operation, [accepted], failed)
                        return 503, self._public(failed)
                    return 202, self._public(accepted)
                finally:
                    if not submitted:
                        self._slots.release()
        except Exception as error:
            auth = _authentication(error)
            return (401 if auth else 503), {"status": "authentication_required" if auth else "unavailable",
                                           "error": _error(error)}

    def _cohort_work(self, store, request, headers, forbidden, execute, progress=None):
        """One authenticated worker, with independent journal and commit per workflow.

        The initiating OBO client is never persisted. A new user-authenticated
        batch can continue only after all previous claims have terminal evidence.
        """
        match = _COHORT_BATCH_ACTION.fullmatch(request["path"])
        if match is None:
            raise ValueError("Invalid cohort worker path")
        cohort = match[1]
        read_path = "/api/cohorts/" + cohort

        def read_scope():
            status, value = execute("GET", read_path, "", headers, {})
            if status == 401:
                raise AuthenticationUnavailable("User authentication required")
            if status != 200 or not isinstance(value, dict) or value.get("id") != cohort:
                raise ValueError("Cohort scope cannot be verified")
            if value.get("stale") is not False or not _HASH.fullmatch(value.get("package_hash", "")):
                raise ValueError("Cohort evidence is stale or invalid")
            if not isinstance(value.get("workflows"), list) or len(value["workflows"]) > 200:
                raise ValueError("Invalid cohort inventory")
            return value

        initial = read_scope()
        package = initial["package_hash"]
        from workbench.cohort_review import operation_id
        workflow_ids = []
        for item in initial["workflows"]:
            wid = item.get("workflow_id") if isinstance(item, dict) else None
            if (not isinstance(wid, str) or not _HASH.fullmatch(wid) or wid in workflow_ids
                    or item.get("operation_id") != operation_id(package, cohort, wid)):
                raise ValueError("Invalid cohort workflow identity")
            workflow_ids.append(wid)
        for position, wid in enumerate(workflow_ids):
            if progress:
                progress('running', completed=position, total=len(workflow_ids), unit='objects')
            current = read_scope()
            if current["package_hash"] != package:
                raise ValueError("Cohort package changed")
            if [row.get("workflow_id") for row in current["workflows"]] != workflow_ids:
                raise ValueError("Cohort membership changed during processing")
            matches = [item for item in current["workflows"] if item.get("workflow_id") == wid]
            if len(matches) != 1:
                raise ValueError("Cohort workflow changed")
            item = matches[0]
            op = operation_id(package, cohort, wid)
            if item.get("operation_id") != op:
                raise ValueError("Cohort operation changed")
            child_path = f"/api/cohorts/{cohort}/workflows/{wid}/design"
            _, child, fingerprint = self._request({"id": op, "method": "POST", "path": child_path,
                                                   "body": {"operation_id": op, "consent": True}}, forbidden)
            with _lock(store.workspace_id, store.user_id, op):
                history = self._read(store, op)
                if history:
                    if history[-1]["fingerprint"] != fingerprint:
                        raise ValueError("Workflow operation identity conflict")
                    if history[-1]["status"] != "completed":
                        return 200, {"status": "recovery_required", "cohort_id": cohort,
                                     "workflow_id": wid, "operation_id": op,
                                     "reason": "Existing workflow claim is uncertain or failed; inspect before retrying."}
                    if item.get("status") != "design_ready":
                        return 200, {"status": "review_required", "cohort_id": cohort,
                                     "workflow_id": wid, "operation_id": op}
                    continue
                if item.get("status") != "awaiting_individual_review":
                    return 200, {"status": "recovery_required", "cohort_id": cohort,
                                 "workflow_id": wid, "operation_id": op,
                                 "reason": "Workflow state has no matching completed operation."}
                accepted = {"operation_id": op, "process_id": self.process_id, "fingerprint": fingerprint,
                            "status": "queued", "progress": _progress("queued")}
                self._append(store, op, [], accepted)
                running = {**accepted, "status": "running", "progress": _progress("running")}
                self._append(store, op, [accepted], running)
                try:
                    status, result = execute("POST", child["path"], "", headers, child["body"])
                    if status == 401:
                        raise AuthenticationUnavailable("User authentication required")
                    if status != 202 or not isinstance(result, dict) or not _ID.fullmatch(result.get("id", "")):
                        raise ValueError("Workflow action did not return a valid receipt")
                    updated = read_scope()
                    matching = [row for row in updated["workflows"] if row.get("workflow_id") == wid]
                    if (updated["package_hash"] != package
                            or [row.get("workflow_id") for row in updated["workflows"]] != workflow_ids
                            or len(matching) != 1
                            or matching[0].get("agent_run_id") != result["id"]
                            or matching[0].get("status") not in {"design_ready", "needs_information", "failed"}):
                        raise ValueError("Committed workflow outcome is unconfirmed")
                    final = {**running, "status": "completed", "progress": _progress("completed"),
                             "status_code": status, "result": result}
                    _checked(final, forbidden)
                    self._append(store, op, [accepted, running], final)
                    if matching[0]["status"] != "design_ready":
                        return 200, {"status": "review_required", "cohort_id": cohort,
                                     "workflow_id": wid, "operation_id": op}
                except Exception as error:
                    state = "authentication_required" if _authentication(error) else "failed"
                    final = {**running, "status": state, "progress": _progress(state), "error": _error(error)}
                    self._append(store, op, [accepted, running], final)
                    if state == "authentication_required":
                        raise
                    return 200, {"status": "recovery_required", "cohort_id": cohort,
                                 "workflow_id": wid, "operation_id": op,
                                 "reason": "Workflow completion is unconfirmed; inspect committed state before retrying."}
        if progress and workflow_ids:
            progress('running', completed=len(workflow_ids), total=len(workflow_ids), unit='objects')
        return 200, {"status": "designs_ready_for_review", "cohort_id": cohort,
                     "workflow_count": len(workflow_ids), "execution_allowed": False,
                     "deployment_allowed": False}

    def _cohort_draft(self, store, request, headers, forbidden, execute, progress=None):
        match = _COHORT_DRAFT_ACTION.fullmatch(request['path'])
        if match is None:
            raise ValueError('Invalid cohort drafting path')
        cohort = match[1]

        def read(path):
            code, value = execute('GET', path, '', headers, {})
            if code == 401:
                raise AuthenticationUnavailable('User authentication required')
            if code != 200 or not isinstance(value, dict):
                raise ValueError('Drafting evidence is unavailable')
            return value

        saved = read('/api/cohorts/' + cohort)
        package = saved.get('package_hash')
        if (saved.get('id') != cohort or saved.get('stale') is not False
                or not isinstance(package, str) or not _HASH.fullmatch(package)
                or not isinstance(saved.get('workflows'), list) or len(saved['workflows']) > 200):
            raise ValueError('Invalid or stale cohort')
        seen = set()
        workflow_ids = [item.get('workflow_id') if isinstance(item, dict) else None
                for item in saved['workflows']]

        def child(op, path, body, expected_status):
            _, subrequest, fingerprint = self._request({'id': op, 'method': 'POST', 'path': path, 'body': body}, forbidden)
            with _lock(store.workspace_id, store.user_id, op):
                history = self._read(store, op)
                if history:
                    if history[-1]['fingerprint'] != fingerprint:
                        raise ValueError('Drafting operation identity conflict')
                    if history[-1]['status'] != 'completed':
                        return None
                    return self._public(history[-1], history)['result']
                accepted = {'operation_id': op, 'process_id': self.process_id, 'fingerprint': fingerprint,
                            'status': 'queued', 'progress': _progress('queued')}
                self._append(store, op, [], accepted)
                running = {**accepted, 'status': 'running', 'progress': _progress('running')}
                self._append(store, op, [accepted], running)
                try:
                    code, result = execute('POST', subrequest['path'], '', headers, subrequest['body'])
                    if code == 401:
                        raise AuthenticationUnavailable('User authentication required')
                    if code != expected_status or not isinstance(result, dict):
                        raise ValueError('Drafting action completion is unconfirmed')
                    final = {**running, 'status': 'completed', 'progress': _progress('completed'),
                             'status_code': code, 'result': result}
                    _checked(final, forbidden)
                    self._append(store, op, [accepted, running], final)
                    return result
                except Exception as error:
                    state = 'authentication_required' if _authentication(error) else 'failed'
                    self._append(store, op, [accepted, running],
                                 {**running, 'status': state, 'progress': _progress(state), 'error': _error(error)})
                    if state == 'authentication_required':
                        raise
                    return None

        completed = []
        for position, item in enumerate(saved['workflows']):
            if progress:
                progress('running', completed=position, total=len(workflow_ids), unit='objects')
            current = read('/api/cohorts/' + cohort)
            if (current.get('id') != cohort or current.get('package_hash') != package
                    or current.get('stale') is not False or current.get('request') != saved.get('request')
                    or not isinstance(current.get('workflows'), list)
                    or [row.get('workflow_id') if isinstance(row, dict) else None
                        for row in current['workflows']] != workflow_ids
                    or current['workflows'][position] != item):
                raise ValueError('Cohort membership or workflow changed during drafting')
            wid = item.get('workflow_id') if isinstance(item, dict) else None
            if not isinstance(wid, str) or not _HASH.fullmatch(wid) or wid in seen:
                raise ValueError('Invalid cohort workflow')
            seen.add(wid)
            refs = item.get('redesign_refs')
            if item.get('status') != 'design_ready' or not isinstance(refs, list) or len(refs) != 1:
                return 200, {'status': 'review_required', 'cohort_id': cohort, 'workflow_id': wid, 'completed': completed}
            ref = refs[0]
            if (not isinstance(ref, dict) or not isinstance(ref.get('id'), str) or not _HASH.fullmatch(ref['id'])
                    or type(ref.get('version')) is not int or ref['version'] < 1):
                raise ValueError('Invalid design reference')
            design = read('/api/redesigns/' + ref['id'])
            if (design.get('id') != ref['id'] or design.get('version') != ref['version']
                    or design.get('package_hash') != package or design.get('workflow_id') != wid
                    or design.get('scenario') != saved.get('request', {}).get('scenario')
                    or design.get('stale') is not False or design.get('outline_drafting_allowed') is not True):
                return 200, {'status': 'review_required', 'cohort_id': cohort, 'workflow_id': wid, 'completed': completed}
            op = sha256(f"cohort-draft-v1:{package}:{cohort}:{wid}:{ref['id']}:{ref['version']}".encode()).hexdigest()[:32]
            result = child(op, '/api/redesigns/' + ref['id'] + '/spark-drafts',
                           {'version': ref['version'], 'consent': True}, 202)
            if result is None:
                return 200, {'status': 'recovery_required', 'cohort_id': cohort, 'workflow_id': wid, 'operation_id': op, 'completed': completed}
            job_id = result.get('id')
            if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
                raise ValueError('Invalid writer job receipt')
            job = read('/api/jobs/' + job_id)
            if (job.get('id') != job_id or job.get('package_hash') != package or job.get('workflow_id') != wid
                    or job.get('design_id') != ref['id'] or job.get('design_version') != ref['version']):
                raise ValueError('Writer job scope mismatch')
            if job.get('status') != 'completed' or job.get('output', {}).get('status') != 'generation_complete':
                return 200, {'status': 'review_required', 'cohort_id': cohort, 'workflow_id': wid, 'job_id': job_id, 'completed': completed}
            root = request['body']['workspace_root']
            bundle_op = sha256(f'cohort-bundle-v1:{package}:{cohort}:{wid}:{job_id}:{root}'.encode()).hexdigest()[:32]
            bundle = child(bundle_op, '/api/jobs/' + job_id + '/native-bundles',
                           {'workspace_root': root, 'consent': True}, 200)
            if bundle is None:
                return 200, {'status': 'recovery_required', 'cohort_id': cohort, 'workflow_id': wid, 'operation_id': bundle_op, 'completed': completed}
            bundle_id = bundle.get('bundle_id')
            if not isinstance(bundle_id, str) or not _HASH.fullmatch(bundle_id):
                raise ValueError('Invalid bundle receipt')
            verified = read('/api/jobs/' + job_id + '/native-bundles/' + bundle_id)
            receipt = verified.get('receipt')
            if (verified.get('bundle_id') != bundle_id or verified.get('status') != 'draft_unverified'
                    or not isinstance(receipt, dict) or receipt.get('job_id') != job_id
                    or receipt.get('package_hash') != package or receipt.get('workflow_id') != wid
                    or receipt.get('design_id') != ref['id'] or receipt.get('design_version') != ref['version']
                    or receipt.get('workspace_root') != root
                    or verified.get('semantic_parity') != 'not_established'
                    or verified.get('execution_allowed') is not False
                    or verified.get('deployment_allowed') is not False):
                raise ValueError('Committed draft bundle scope or integrity is unconfirmed')
            completed.append({'workflow_id': wid, 'job_id': job_id, 'bundle_id': bundle_id})
        if progress and workflow_ids:
            progress('running', completed=len(workflow_ids), total=len(workflow_ids), unit='objects')
        return 200, {'status': 'drafts_published_unverified', 'cohort_id': cohort, 'completed': completed,
                     'semantic_parity': 'not_established', 'execution_allowed': False, 'deployment_allowed': False}

    def _work(self, identity, remote, operation, request, headers, forbidden, execute):
        try:
            # Fresh verification instance for the worker, pinned to initiating OBO.
            store = self._store(identity, remote)
            lock = _lock(store.workspace_id, store.user_id, operation)
            with lock:
                history = self._read(store, operation)
                if not history or history[-1]["status"] != "queued" or history[-1]["process_id"] != self.process_id:
                    return
                running = {**history[-1], "status": "running", "progress": _progress("running")}
                self._append(store, operation, history, running)

            last_progress = running["progress"]
            last_bucket = -1

            def report_progress(stage, *, completed=None, total=None, unit=None):
                nonlocal last_progress, last_bucket
                candidate = _progress("running", stage=stage, completed=completed, total=total, unit=unit)
                if not _valid_progress("running", candidate):
                    raise ValueError("Invalid hosted operation progress")
                bucket = (min(4, completed * 4 // total)
                          if type(completed) is int and type(total) is int and total > 0 else None)
                if candidate["stage"] == last_progress["stage"] and (
                        bucket is None or bucket == last_bucket):
                    return
                with lock:
                    history = self._read(store, operation)
                    if not history or history[-1]["status"] != "running":
                        return
                    updated = {**history[-1], "progress": candidate}
                    self._append(store, operation, history, updated)
                last_progress = candidate
                if bucket is not None:
                    last_bucket = bucket

            def accepted_agent(record):
                project = headers.get("x-workbench-project", "")
                package = headers.get("x-workbench-package", "")
                workflow = request["body"].get("workflow_id")
                if (request["path"] != "/api/agents/run" or not isinstance(record, dict)
                        or not _ID.fullmatch(str(record.get("id", "")))
                        or not _ID.fullmatch(project) or not _HASH.fullmatch(package)
                        or not _HASH.fullmatch(str(workflow or ""))
                        or record.get("package_hash") != package or record.get("workflow_id") != workflow):
                    return
                live = {"run_id": record["id"], "project_id": project, "package_hash": package,
                        "workflow_id": workflow, "state": "accepted_in_process_not_committed"}
                with self._live_lock:
                    self._live_agents[(store.workspace_id, store.user_id, operation)] = live

            report_progress.accepted_agent = accepted_agent
            progress_token = _CURRENT_PROGRESS.set(report_progress)
            failure_phase = "action_execution"
            try:
                if _COHORT_BATCH_ACTION.fullmatch(request["path"]) or _COHORT_DRAFT_ACTION.fullmatch(request["path"]):
                    # Individual restores would otherwise exhaust the bounded
                    # outer progress journal for a large cohort.
                    paused = _CURRENT_PROGRESS.set(None)
                    try:
                        worker = self._cohort_work if _COHORT_BATCH_ACTION.fullmatch(request["path"]) else self._cohort_draft
                        status, result = worker(store, request, headers, forbidden, execute, report_progress)
                    finally:
                        _CURRENT_PROGRESS.reset(paused)
                else:
                    status, result = execute(request["method"], request["path"], request["query"], headers, request["body"])
                failure_phase = "action_status"
                if type(status) is not int or not 100 <= status <= 599:
                    raise ValueError("Invalid action status")
                if status == 401:
                    raise AuthenticationUnavailable("User authentication required")
                if not 200 <= status < 300:
                    error = RuntimeError("Hosted action failed")
                    diagnostic_id = result.get("failure_id") if isinstance(result, dict) else None
                    phase = result.get("failure_phase") if isinstance(result, dict) else None
                    if isinstance(diagnostic_id, str) and re.fullmatch(r"[a-f0-9]{32}", diagnostic_id):
                        detail = f"Hosted action failed; diagnostic {diagnostic_id}"
                        if isinstance(phase, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]{0,80}", phase):
                            detail += f" phase {phase}"
                        error.args = (detail,)
                    raise error
                # Normalize to actual JSON before validation/readback comparison.
                failure_phase = "result_validation"
                result_bytes = _checked(result, forbidden)
                if len(result_bytes) > MAX_RESULT_BYTES:
                    raise ValueError("Hosted operation result exceeds bounded read result size")
                if len(result_bytes) <= 24 * 1024:
                    result = _json(result_bytes)
                    final = {**running, "status": "completed", "progress": _progress("completed"),
                             "status_code": status, "result": result}
                    _checked(final, forbidden)
                else:
                    failure_phase = "result_chunks"
                    chunks = [result_bytes[i:i + RESULT_CHUNK_BYTES]
                              for i in range(0, len(result_bytes), RESULT_CHUNK_BYTES)]
                    if len(chunks) > MAX_HISTORY - 3:
                        raise ValueError("Hosted operation result exceeds bounded chunk count")
                    digest = sha256(result_bytes).hexdigest()
                    for index, chunk in enumerate(chunks):
                        end = min(len(result_bytes), (index + 1) * RESULT_CHUNK_BYTES)
                        chunk_record = {**running,
                            "progress": _progress("running", stage="responding", completed=end,
                                                  total=len(result_bytes), unit="bytes"),
                            "result_chunk": {"index": index, "count": len(chunks),
                                             "sha256": digest,
                                             "data": base64.b64encode(chunk).decode("ascii")}}
                        with lock:
                            history = self._read(store, operation)
                            self._append(store, operation, history, chunk_record)
                    final = {**running, "status": "completed", "progress": _progress("completed"),
                             "status_code": status,
                             "result_chunks": {"count": len(chunks), "sha256": digest}}
                    _checked(final, forbidden)
            except Exception as error:
                state = "authentication_required" if _authentication(error) else "failed"
                failure_phase = getattr(error, "hosted_failure_phase", failure_phase)
                final = {**running, "status": state, "progress": _progress(state), "error": _error(error),
                         "failure_phase": failure_phase}
                if failure_phase == "action_status" and type(status) is int and 100 <= status <= 599 and not 200 <= status < 300:
                    final["action_status"] = status
            finally:
                _CURRENT_PROGRESS.reset(progress_token)
            with lock:
                history = self._read(store, operation)
                self._append(store, operation, history, final)
        except Exception:
            # A failed/unverified journal write MUST NOT authorize execution or
            # report completion. GET reads durable evidence only. No raw logging.
            return
        finally:
            with self._live_lock:
                self._live_agents.pop((identity.host, identity.user_id, operation), None)