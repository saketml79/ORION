"""Authored nonnetwork pause regressions. Synchronization uses events, not sleeps."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from workbench.agents import AgentRunCancelled, build_agents
from workbench.run_control import RunControl
from workbench.tests.test_agents import fake, sample_model


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setenv("WORKBENCH_LANGSMITH_ENABLED", "0")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def forbidden(*args, **kwargs):
        raise AssertionError("Network/provider access forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("langchain.chat_models.init_chat_model", forbidden)


def observed_control():
    observed = Event()
    control = RunControl(Event())
    control._on_change = lambda: observed.set() if control.snapshot()["paused"] else None
    return control, observed


def test_requested_is_not_observed_and_duplicates_do_not_retime():
    control, _ = observed_control()
    first = control.request_pause()
    assert first["pause_requested"] and first["pause_requested_at"]
    assert not first["paused"] and first["paused_at"] is None
    assert first["pause_boundary"] is None
    assert control.request_pause() == first
    for _ in range(2):
        resumed = control.resume()
        assert not resumed["pause_requested"] and not resumed["paused"]
        assert resumed["pause_requested_at"] is resumed["paused_at"] is None
    assert control.wait("analysis:model")


@pytest.mark.parametrize("boundary", ["analysis:model", "analysis:tool", "candidate_writer:model", "candidate_writer:tool"])
def test_inflight_finishes_but_next_boundary_waits(boundary):
    control, observed = observed_control()
    inflight, release, completed, admitted = Event(), Event(), Event(), Event()

    def work():
        assert control.wait(boundary)
        inflight.set()
        assert release.wait(5)
        completed.set()
        if control.wait(boundary):
            admitted.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work)
        try:
            assert inflight.wait(5)
            control.request_pause()
            assert not control.snapshot()["paused"]
            release.set()
            assert observed.wait(5)
            assert completed.is_set() and not admitted.is_set()
            assert control.snapshot()["pause_boundary"] == boundary
            assert control.snapshot()["paused_at"]
            control.resume()
            future.result(timeout=5)
            assert admitted.is_set() and not control.snapshot()["paused"]
        finally:
            release.set()
            control.cancel()


@pytest.mark.parametrize("direct_event", [False, True])
def test_cancel_wakes_wait_without_resuming_admission(direct_event):
    control, observed = observed_control()
    control.request_pause()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(control.wait, "analysis:tool")
        try:
            assert observed.wait(5)
            if direct_event:
                control.cancellation_event.set()  # Existing cancellation callers still work.
            else:
                control.cancel()  # HTTP path notifies immediately.
            assert future.result(timeout=5) is False
            assert not control.snapshot()["paused"]
            assert not control.snapshot()["pause_requested"]
            control.request_pause()
            assert not control.snapshot()["pause_requested"]
            assert control.wait("analysis:model") is False
        finally:
            control.cancel()


def test_parallel_waiters_all_resume_and_observer_can_take_service_lock():
    from threading import RLock
    service_lock = RLock()
    both_waiting = Event()
    control = RunControl(Event())

    def changed():
        with service_lock:
            if control.snapshot()["paused_boundary_count"] == 2:
                both_waiting.set()

    control._on_change = changed
    control.request_pause()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(control.wait, "analysis:tool") for _ in range(2)]
        try:
            assert both_waiting.wait(5)
            assert service_lock.acquire(timeout=1)
            try:
                assert control.snapshot()["paused_boundary_count"] == 2
                control.resume()
            finally:
                service_lock.release()
            assert all(f.result(timeout=5) for f in futures)
        finally:
            control.cancel()


def test_terminal_cleanup_wakes_waiters_without_inventing_cancellation():
    control, observed = observed_control()
    control.request_pause()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(control.wait, "analysis:model")
        try:
            assert observed.wait(5)
            control.finish()
            assert future.result(timeout=5) is False
            assert not control.cancellation_event.is_set()
            assert not control.snapshot()["pause_requested"]
            assert control.wait("analysis:tool") is False
        finally:
            control.cancel()


@pytest.mark.parametrize("kind", ["model", "tool"])
@pytest.mark.parametrize("cancel", [False, True])
def test_real_analysis_middleware_gates_before_call_and_preserves_no_pause_default(tmp_path, kind, cancel):
    control, observed = observed_control()
    with build_agents(tmp_path, sample_model(), "wf", "original", chat_model=fake(),
                      pause_control=control) as bundle:
        assert bundle.cancelled is control.cancellation_event
        budget = bundle.contexts["discovery"]["budget"]
        called = Event()

        def model_handler(request):
            called.set()
            return SimpleNamespace(result=[AIMessage(content="Nonnetwork handler receipt")])

        def tool_handler(request):
            called.set()
            return ToolMessage(content='{}', name="package_summary", tool_call_id="test")

        request = SimpleNamespace(model=fake(), system_message=None, messages=[],
                                  tool_call={"name": "package_summary", "args": {}, "id": "test"})
        invoke = (lambda: budget.wrap_model_call(request, model_handler)) if kind == "model" else (
            lambda: budget.wrap_tool_call(request, tool_handler))
        control.request_pause()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke)
            try:
                assert observed.wait(5)
                assert not called.is_set()
                if cancel:
                    bundle.cancel()
                    with pytest.raises(AgentRunCancelled):
                        future.result(timeout=5)
                    assert not called.is_set()
                else:
                    control.resume()
                    future.result(timeout=5)
                    assert called.is_set()
            finally:
                control.cancel()


def test_analysis_rejects_mismatched_cancellation_events(tmp_path):
    with pytest.raises(ValueError, match="share the run cancellation event"):
        build_agents(tmp_path, sample_model(), "wf", "original", chat_model=fake(),
                     cancellation_event=Event(), pause_control=RunControl(Event()))


def test_analysis_default_without_optional_hook_is_unchanged(tmp_path):
    with build_agents(tmp_path, sample_model(), "wf", "original", chat_model=fake()) as bundle:
        assert bundle.pause_control is None
        budget = bundle.contexts["discovery"]["budget"]
        called = Event()
        request = SimpleNamespace(tool_call={"name": "package_summary", "args": {}, "id": "default"})

        def handler(_request):
            called.set()
            return ToolMessage(content="{}", name="package_summary", tool_call_id="default")

        budget.wrap_tool_call(request, handler)
        assert called.is_set()


@pytest.mark.parametrize("kind", ["model", "tool", "validation_model"])
@pytest.mark.parametrize("cancel", [False, True])
def test_actual_candidate_writer_admission_hooks(tmp_path, monkeypatch, kind, cancel):
    """Capture real writer middleware, without streaming/invoking any chat model."""
    from copy import deepcopy
    from pathlib import Path
    import runpy
    import deepagents
    from workbench.spark_codegen import draft_agent_design

    fixtures = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tests/test_spark_codegen.py"))
    service, design, _, _ = fixtures["context"].__wrapped__(tmp_path)
    model = fixtures["ScriptedChat"](responses=[AIMessage(content="Not invoked")])
    validator = fixtures["ScriptedChat"](responses=[AIMessage(content="Not invoked")])
    service.runtime.role_models = {"artifact": model, "validation": validator}
    public = dict(id="source-run", status="running", consent=True, generate_code=True,
                  **{k: design[k] for k in ("workflow_id", "scenario", "package_hash", "input_version")})
    service.record = lambda *a, **k: deepcopy(public)
    service.update_record = lambda value, **k: public.update(value)
    control, observed = observed_control()
    service.agent_pause_controls = {"source-run": control}
    service.agent_cancel_events = {"source-run": control.cancellation_event}
    captured = {}

    class Captured(Exception):
        pass

    def capture_graph(**kwargs):
        captured["middleware"] = kwargs["middleware"][-1]
        raise Captured()

    class Reviewer:
        def __init__(self, invoke, **kwargs):
            captured["validation"] = invoke

    monkeypatch.setattr(deepagents, "create_deep_agent", capture_graph)
    monkeypatch.setattr("workbench.spark_codegen.GeneratedCodeReviewer", Reviewer)
    with pytest.raises(Captured):
        draft_agent_design(service, design, agent_run_id="source-run", consent=True)
    called = Event()

    def model_handler(request):
        called.set()
        return SimpleNamespace(result=[])

    def tool_handler(request):
        called.set()
        return ToolMessage(content="{}", name="fetch_source_task", tool_call_id="test")

    def validator_invoke(messages):
        called.set()
        return AIMessage(content="Nonnetwork validation receipt")

    # Patch only invocation, never synthesize a model/provider authentication.
    monkeypatch.setattr(type(validator), "invoke", lambda self, messages: validator_invoke(messages))
    budget = captured["middleware"]
    request = SimpleNamespace(model=model, messages=[], system_message=None,
                              tool_call={"name": "fetch_source_task", "id": "test", "args": {}})
    if kind == "validation_model":
        invoke = lambda: captured["validation"]([], 0)
    elif kind == "model":
        invoke = lambda: budget.wrap_model_call(request, model_handler)
    else:
        invoke = lambda: budget.wrap_tool_call(request, tool_handler)
    control.request_pause()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        try:
            assert observed.wait(5)
            assert not called.is_set()
            assert service.lock.acquire(timeout=1)
            service.lock.release()
            if cancel:
                control.cancel()
                with pytest.raises(AgentRunCancelled):
                    future.result(timeout=5)
                assert not called.is_set()
            else:
                control.resume()
                future.result(timeout=5)
                assert called.is_set()
            assert not model._calls and not validator._calls
        finally:
            control.cancel()