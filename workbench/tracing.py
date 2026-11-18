"""Opt-in, payload-free LangSmith spans for actual LangChain callbacks.

Server configuration only: WORKBENCH_LANGSMITH_ENABLED=1,
WORKBENCH_LANGSMITH_PROJECT (a non-secret label), LANGSMITH_API_KEY (secret),
and optionally LANGSMITH_ENDPOINT. No dotenv reads or environment writes.
Only the exact approved US API origin is accepted. Additional regions require
an explicit code review, not a user-supplied URL. Never return the API key.

This is deliberately NOT a full LangChainTracer: serialized models, prompts,
SQL/XML, documents, checkpoint metadata and tool arguments/results are ignored.
Only generic span kinds, actual callback UUIDs, timestamps, numeric token counts
and a hashed owner scope leave the process. SDK runtime metadata is suppressed.
SDK writes are unbatched, single attempt, with a 1s connect / 2s read timeout
(not a hard DNS/process deadline). A bounded, payload-free per-run queue keeps
network I/O off graph/model callbacks. Finalization waits at most three seconds;
unfinished delivery is pending, not verified or eligible for feedback. The first
delivery failure stops further writes, never model execution.

Parent UI contract: GET /api/tracing/status is configuration-only, not a probe.
Runtime output.tracing and loop attempt.tracing describe actual delivery.
verified is always false: HTTP acknowledgment is not LangSmith UI readback.
No guessed trace URLs are emitted. Feedback is an advisory rubric, not parity,
approval, a model-quality evaluation dataset or a LangSmith experiment.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from queue import Queue
import re
from secrets import token_bytes
from threading import Event, RLock, Thread
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


APPROVED_ENDPOINTS = frozenset({"https://api.smith.langchain.com"})
DEFAULT_ENDPOINT = "https://api.smith.langchain.com"
MAX_SPANS = 256
CRITERIA = frozenset({
    "output_object", "finding_schema", "runtime_status", "six_required_roles",
    "known_scoped_citations", "supported_findings_cite_evidence", "blocker_schema",
    "no_blocking_unknowns", "runtime_completed", "authority_gates_closed",
})
_RECEIPT_KEY = token_bytes(32)  # process-local authenticity, not a user credential


def _configuration():
    enabled = os.environ.get("WORKBENCH_LANGSMITH_ENABLED") == "1"
    if not enabled:
        return {"enabled": False, "state": "disabled", "reason": "not_opted_in", "project": None}
    project = os.environ.get("WORKBENCH_LANGSMITH_PROJECT", "")
    endpoint = os.environ.get("LANGSMITH_ENDPOINT", DEFAULT_ENDPOINT)
    # Labels, not arbitrary strings that could carry URLs, headers or secrets.
    if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,62}", project)
            or re.search(r"(?i)secret|token|password|api.?key|^lsv2|^dapi", project)):
        reason = "explicit_project_required"
    elif endpoint not in APPROVED_ENDPOINTS:
        reason = "endpoint_not_approved"
    elif not os.environ.get("LANGSMITH_API_KEY", "").strip():
        reason = "server_key_required"
    else:
        return {"enabled": True, "state": "configured", "reason": "not_probed",
                "project": project, "endpoint": endpoint}
    return {"enabled": True, "state": "not_configured" if reason in {"server_key_required", "explicit_project_required"} else "configuration_error",
            "reason": reason, "project": None}


def tracing_status():
    """No client construction, network probe, raw errors or secret indicators."""
    return {**_configuration(), "backend": "langsmith", "verified": False,
            "payload_policy": "metadata_minimal", "evaluation_scope": "advisory_rubric_only"}


def _new_client(config):
    # Inspected against installed langsmith 0.14.14. Do not silently downgrade
    # unsupported privacy settings on older SDKs: initialization must fail closed.
    from langsmith import Client
    import requests
    from urllib3.util.retry import Retry
    from urllib3.util.timeout import Timeout
    from urllib.parse import urlsplit

    class Transport(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False  # no ambient proxy credentials/netrc
            self.acknowledged = 0

        def request(self, method, url, **kwargs):
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin != config["endpoint"] or parsed.query or parsed.fragment:
                raise ValueError("unapproved_trace_destination")
            if method not in {"POST", "PATCH"} or not (
                    parsed.path == "/runs" or re.fullmatch(r"/runs/[0-9a-f-]{36}", parsed.path)
                    or parsed.path == "/feedback"):
                raise ValueError("unapproved_trace_operation")
            kwargs.update(allow_redirects=False, timeout=Timeout(total=3, connect=1, read=2))
            response = super().request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                raise ValueError("trace_redirect_refused")
            if 200 <= response.status_code < 300:
                self.acknowledged += 1
            else:
                # Do not let the SDK's ignored-conflict path turn HTTP errors
                # into a successful submission/feedback eligibility receipt.
                response.raise_for_status()
            return response

    class SingleAttemptClient(Client):
        def request_with_retries(self, method, pathname, **kwargs):
            # create_feedback otherwise defaults to ten attempts.
            kwargs["stop_after_attempt"] = 1
            return super().request_with_retries(method, pathname, **kwargs)

    # The SDK permits LANGSMITH_RUNS_ENDPOINTS to override api_url. Refuse that
    # ambient multi-destination configuration instead of mutating the environment.
    if any(os.environ.get(prefix + option) for prefix in ("LANGSMITH_", "LANGCHAIN_")
           for option in ("RUNS_ENDPOINTS", "USE_PYO3_CLIENT")):
        raise ValueError("ambient_trace_destinations_refused")
    transport = Transport()
    try:
        client = SingleAttemptClient(
            api_key=os.environ["LANGSMITH_API_KEY"], api_url=config["endpoint"],
            hide_inputs=True, hide_outputs=True, hide_metadata=True,
            omit_traced_runtime_info=True, auto_batch_tracing=False,
            tracing_sampling_rate=1.0, tracing_mode="langsmith", otel_enabled=False,
            timeout_ms=(1000, 2000), retry_config=Retry(total=0, redirect=0),
            session=transport,
        )
        if client.api_url != config["endpoint"]:
            raise ValueError("unapproved_trace_destination")
        return client
    except Exception:
        transport.close()
        raise


def _owner(identity_scope):
    if not isinstance(identity_scope, str):
        raise ValueError("invalid_trace_scope")
    return sha256(identity_scope.encode("utf-8")).hexdigest()


def _seal(trace):
    fields = {k: trace[k] for k in (
        "root_id", "runtime_run_id", "owner_scope", "project", "endpoint",
        "root_submitted", "root_acknowledged", "root_completed")}
    return hmac.new(_RECEIPT_KEY, json.dumps(fields, sort_keys=True).encode(), sha256).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _usage(value):
    if not isinstance(value, dict):
        return {}
    return {k: value[k] for k in ("input_tokens", "output_tokens", "total_tokens")
            if type(value.get(k)) is int and 0 <= value[k] <= 10**12}


class _Callbacks(BaseCallbackHandler):
    """Ignore every callback payload except explicitly reported numeric usage."""
    run_inline = True

    def __init__(self, session):
        self.session = session

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self.session.start_span("chain", run_id, parent_run_id)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self.session.start_span("llm", run_id, parent_run_id)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        self.session.start_span("llm", run_id, parent_run_id)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self.session.start_span("tool", run_id, parent_run_id)

    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
        self.session.start_span("retriever", run_id, parent_run_id)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self.session.end_span(run_id)

    def on_tool_end(self, output, *, run_id, **kwargs):
        self.session.end_span(run_id)

    def on_retriever_end(self, documents, *, run_id, **kwargs):
        self.session.end_span(run_id)

    def on_llm_end(self, response, *, run_id, **kwargs):
        counts = {}
        # Do not inspect content, tool calls, response metadata or serialized kwargs.
        for generation in getattr(response, "generations", ()):
            for item in generation:
                values = _usage(getattr(getattr(item, "message", None), "usage_metadata", None))
                for key, value in values.items():
                    counts[key] = counts.get(key, 0) + value
        self.session.end_span(run_id, usage=counts)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self.session.end_span(run_id, failed=True)

    on_tool_error = on_chain_error
    on_llm_error = on_chain_error
    on_retriever_error = on_chain_error


class TraceSession:
    def __init__(self, run_id, identity_scope):
        self.config = tracing_status()
        self.client = None
        self.lock = RLock()
        self.spans = {}
        self.finished = False
        self.snapshot = None
        self.queue = Queue(maxsize=MAX_SPANS * 2 + 1)
        self.stop = Event()
        self.worker = None
        self.callback = _Callbacks(self)
        self.record = {**self.config, "root_id": None, "runtime_run_id": None,
                       "root_submitted": False, "root_acknowledged": False,
                       "root_completed": False, "submitted_spans": 0,
                       "http_acknowledged_writes": 0, "truncated": False}
        if self.config["state"] != "configured":
            return
        try:
            self.root = UUID(str(run_id))
            self.record.update(root_id=str(self.root), runtime_run_id=str(run_id),
                               owner_scope=_owner(identity_scope))
            self.client = _new_client(self.config)
            self.record.update(state="pending", reason="delivery_pending")
            self._create(self.root, "chain", None)
            self.worker = Thread(target=self._deliver, name="workbench-trace", daemon=True)
            self.worker.start()
        except Exception:
            self._failed("initialization_failed")
            if self.worker is not None and self.worker.ident is None:
                self.worker = None

    def _failed(self, reason):
        self.record.update(state="failed", reason=reason, verified=False)

    def _write(self, method, *args, **kwargs):
        try:
            method(*args, **kwargs)
            with self.lock:
                self.record.update(state="submitted", reason="sdk_submission_returned",
                    http_acknowledged_writes=getattr(self.client.session, "acknowledged", 0))
            return True
        except Exception:
            with self.lock:
                self._failed("delivery_failed")
            return False

    def _enqueue(self, operation, run_id, payload):
        # Payloads are constructed here from allowlisted fields, never callback
        # args/kwargs or LangChain objects. No blocking put on the model thread.
        try:
            self.queue.put_nowait((operation, run_id, payload))
        except Exception:
            self._failed("delivery_queue_full")
            self.stop.set()

    def _deliver(self):
        try:
            while True:
                item = self.queue.get()
                try:
                    if item is None:
                        break
                    operation, run_id, payload = item
                    with self.lock:
                        skip = self.stop.is_set() or self.record["state"] == "failed"
                    if skip:
                        continue
                    if operation == "create":
                        success = self._write(self.client.create_run, **payload)
                    else:
                        success = self._write(self.client.update_run, run_id, **payload)
                    if success:
                        with self.lock:
                            if operation == "create":
                                self.record["submitted_spans"] += 1
                                if run_id == self.root:
                                    self.record.update(root_submitted=True,
                                        root_acknowledged=self.record["http_acknowledged_writes"] == 1)
                            elif run_id == self.root:
                                self.record["root_completed"] = True
                finally:
                    self.queue.task_done()
        except Exception:
            with self.lock:
                self._failed("delivery_failed")
        finally:
            try:
                self.client.session.close()
            except Exception:
                pass

    def _create(self, run_id, kind, parent):
        start = _now()
        order = start.strftime("%Y%m%dT%H%M%S%fZ") + str(run_id)
        if parent is not None:
            order = self.spans[parent]["order"] + "." + order
        self.spans[run_id] = {"order": order, "ended": False}
        self._enqueue("create", run_id, dict(id=run_id, name="workbench." + kind,
            run_type=kind, inputs={}, outputs={}, serialized=None,
            start_time=start, parent_run_id=parent, trace_id=self.root,
            dotted_order=order, project_name=self.config["project"],
            extra={"owner_scope": self.record["owner_scope"]}))

    def start_span(self, kind, run_id, parent):
        with self.lock:
            if self.finished or self.record["state"] not in {"pending", "submitted"}:
                return
            if kind not in {"chain", "llm", "tool", "retriever"}:
                return
            if len(self.spans) >= MAX_SPANS:
                self.record["truncated"] = True
                return
            # IDs come from the callback manager, never checkpoint metadata.
            if not isinstance(run_id, UUID) or run_id in self.spans:
                return
            parent = self.root if parent is None else parent
            if parent not in self.spans:
                self.record["truncated"] = True
                return  # do not invent/reparent missing callback ancestry
            self._create(run_id, kind, parent)

    def end_span(self, run_id, failed=False, usage=None):
        with self.lock:
            span = self.spans.get(run_id)
            if self.record["state"] not in {"pending", "submitted"} or span is None or span["ended"]:
                return
            self._enqueue("update", run_id, dict(end_time=_now(),
                trace_id=self.root, dotted_order=span["order"],
                error="execution_failed" if failed else None,
                extra={"usage": _usage(usage)}))
            span["ended"] = True

    def callbacks(self):
        return [self.callback] if self.record["state"] in {"pending", "submitted"} and not self.finished else []

    def finish(self, failed=False, usage=None):
        with self.lock:
            if self.snapshot is not None:
                return dict(self.snapshot)
            # Missing end events are not fabricated as successful spans.
            if any(not s["ended"] for i, s in self.spans.items() if i != getattr(self, "root", None)):
                self.record["truncated"] = True
            if getattr(self, "root", None) in self.spans:
                self.end_span(self.root, failed, usage)
            self.finished = True
            if self.worker is not None:
                try:
                    self.queue.put_nowait(None)
                except Exception:
                    self.stop.set()
                    self._failed("delivery_queue_full")
        # Never hold the callback lock while waiting for the delivery worker.
        if self.worker is not None:
            self.worker.join(timeout=3)
        elif self.client is not None:
            try:
                self.client.session.close()
            except Exception:
                pass
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                self.stop.set()
                if self.record["state"] != "failed":
                    self.record.update(state="pending", reason="delivery_pending")
            if self.record["state"] == "submitted" and self.record["root_completed"]:
                self.record["receipt"] = _seal(self.record)
            self.snapshot = dict(self.record)
            return dict(self.snapshot)


@contextmanager
def trace_session(run_id, identity_scope):
    from langsmith import tracing_context
    # Context-local, never global env mutation. Prevent ambient full-payload
    # tracing even when the workbench opt-in is off. Explicit callbacks still run.
    with tracing_context(enabled=False, parent=False):
        session = TraceSession(run_id, identity_scope)
        try:
            yield session
        except BaseException:
            session.finish(failed=True)
            raise
        finally:
            session.finish()


def export_evaluation(trace, rubric, *, identity_scope, run_id):
    """Export only boolean deterministic checks for an authentic same-owner run.

    Receipts are process-local. A restart cannot retroactively export a persisted
    receipt; it returns not_submitted, never creates a replacement/fake trace.
    No remote lookup, dataset upload or model invocation is performed.
    """
    result = {"state": "not_submitted", "submitted_scores": 0, "verified": False,
              "scope": "advisory_rubric_only", "semantic_parity": "unproven"}
    config = tracing_status()
    if config["state"] != "configured":
        return {**result, "state": config["state"], "reason": config["reason"]}
    try:
        if (not isinstance(trace, dict) or trace.get("state") != "submitted"
                or trace.get("root_submitted") is not True
            or trace.get("root_acknowledged") is not True
            or trace.get("root_completed") is not True
                or trace.get("runtime_run_id") != run_id
                or trace.get("root_id") != str(UUID(str(run_id)))
                or trace.get("owner_scope") != _owner(identity_scope)
                or trace.get("project") != config["project"]
                or trace.get("endpoint") != config["endpoint"]
                or not isinstance(trace.get("receipt"), str)
                or not hmac.compare_digest(trace["receipt"], _seal(trace))):
            return {**result, "reason": "trace_not_owned_or_submitted"}
        checks = rubric.get("checks") if isinstance(rubric, dict) else None
        if (not isinstance(checks, list) or not 1 <= len(checks) <= len(CRITERIA)
                or any(not isinstance(c, dict) or c.get("criterion") not in CRITERIA
                       or type(c.get("passed")) is not bool for c in checks)
                or len({c["criterion"] for c in checks}) != len(checks)):
            return {**result, "reason": "invalid_advisory_rubric"}
        client = _new_client(config)
        try:
            for check in checks:
                client.create_feedback(run_id=UUID(trace["root_id"]),
                    key="advisory." + check["criterion"], score=check["passed"],
                    stop_after_attempt=1)
                result["submitted_scores"] += 1
            return {**result, "state": "submitted", "reason": "sdk_submission_returned",
                    "http_acknowledged_writes": getattr(client.session, "acknowledged", 0)}
        finally:
            client.session.close()
    except Exception:
        return {**result, "state": "failed", "reason": "evaluation_export_failed"}