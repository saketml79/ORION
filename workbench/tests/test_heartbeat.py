"""Isolated offline SQL and actual Chromium activity-contract regression tests."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout, StatementParameterListItem
from playwright.sync_api import sync_playwright

from workbench import heartbeat as hb
from workbench.databricks_models import WorkspaceIdentity


TABLE = "gap_demo_dev_app_monitoring.genie_workbench.app_heartbeat"
STAMP = "2026-10-30T12:34:56.123Z"


def response(state="SUCCEEDED", rows=None):
    return SimpleNamespace(status=SimpleNamespace(state=state), manifest=None,
                           result=SimpleNamespace(data_array=rows, next_chunk_index=None))


def identity(user="actual-user", mode="obo"):
    client = Mock()
    client.config.host = "https://workspace.example"
    client.current_user.me.return_value = SimpleNamespace(id=user)
    client.statement_execution.execute_statement.side_effect = lambda **kw: response(
        rows=[["ORION", user, STAMP]] if kw["statement"].startswith("SELECT") else [])
    return WorkspaceIdentity(mode, client.config.host, user, client)


def service():
    return hb.Heartbeats("existing-warehouse", TABLE, "ORION")


@pytest.mark.parametrize("table,app", [(None, None), (TABLE, None), (None, "ORION"), ("", "")])
def test_missing_config_is_disabled_without_auth(table, app):
    instance = hb.Heartbeats(None, table, app)
    assert instance.touch(object())["status"] == "disabled"
    assert not instance.status()["enabled"]


def test_explicit_environment_opt_in(monkeypatch):
    monkeypatch.delenv("WORKBENCH_HEARTBEAT_TABLE", raising=False)
    monkeypatch.delenv("WORKBENCH_HEARTBEAT_APP", raising=False)
    assert hb.Heartbeats.from_env("warehouse").status()["status"] == "disabled"
    monkeypatch.setenv("WORKBENCH_HEARTBEAT_TABLE", TABLE)
    monkeypatch.setenv("WORKBENCH_HEARTBEAT_APP", "ORION")
    assert hb.Heartbeats.from_env("warehouse").status()["enabled"]


@pytest.mark.parametrize("table", ["a.b", "a.b.c.d", "a.b.c;DROP", "`a`.b.c", "a..c", " a.b.c", 123])
def test_reject_unsafe_table(table):
    with pytest.raises(ValueError, match="three-part"):
        hb.Heartbeats("warehouse", table, "ORION")


@pytest.mark.parametrize("app", ["ORION'", "bad app", "x" * 65, "token=secret", 123])
def test_reject_unsafe_app(app):
    with pytest.raises(ValueError, match="application name"):
        hb.Heartbeats("warehouse", TABLE, app)


@pytest.mark.parametrize("warehouse", [None, "", "bad warehouse", "w;DROP"])
def test_enabled_requires_existing_warehouse_identifier(warehouse):
    with pytest.raises(ValueError, match="warehouse identifier"):
        hb.Heartbeats(warehouse, TABLE, "ORION")


def test_obo_only_fresh_me_even_during_throttle_no_fallback():
    instance, actor = service(), identity()
    assert instance.touch(identity(mode="profile"))["reason"] == "obo_required"
    assert instance.touch(actor)["written"]
    actor.client.current_user.me.return_value.id = "another-user"
    assert instance.touch(actor)["reason"] == "identity_mismatch"
    actor.client.current_user.me.side_effect = PermissionError("Bearer secret-never-echo")
    result = instance.touch(actor)
    assert result["error_type"] == "PermissionError"
    assert "secret" not in json.dumps(result)
    assert actor.client.statement_execution.execute_statement.call_count == 2
    assert set(actor.client.method_calls[i][0] for i in range(len(actor.client.method_calls))) <= {
        "current_user.me", "statement_execution.execute_statement"}


def test_parameterized_append_and_actual_timestamp_readback():
    instance, actor = service(), identity("user's actual ID")
    result = instance.touch(actor, "active_work")
    assert result["status"] == "written" and result["ts"] == STAMP
    calls = actor.client.statement_execution.execute_statement.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["statement"] == (
        "INSERT INTO `gap_demo_dev_app_monitoring`.`genie_workbench`.`app_heartbeat` "
        "(app, ts, `user`) VALUES (:app, current_timestamp(), :user)")
    assert "ORDER BY ts DESC LIMIT 1" in calls[1].kwargs["statement"]
    for call in calls:
        kw = call.kwargs
        assert kw["warehouse_id"] == "existing-warehouse"
        assert kw["wait_timeout"] == "5s"
        assert kw["on_wait_timeout"] == ExecuteStatementRequestOnWaitTimeout.CONTINUE
        assert all(isinstance(p, StatementParameterListItem) and p.type == "STRING" for p in kw["parameters"])
        assert {p.name: p.value for p in kw["parameters"]} == {"app": "ORION", "user": actor.user_id}
        assert actor.user_id not in kw["statement"] and "ORION" not in kw["statement"]


def test_monotonic_per_user_cooldown_starts_after_verification(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(hb.time, "monotonic", lambda: clock[0])
    instance, actor = service(), identity()
    assert instance.touch(actor)["written"]
    clock[0] = 309.9
    assert instance.touch(actor)["status"] == "throttled"
    assert instance.touch(identity("other-user"))["written"]
    clock[0] = 310.0
    assert instance.touch(actor)["written"]
    assert actor.client.current_user.me.call_count == 3


@pytest.mark.parametrize("phase,state", [("insert", "PENDING"), ("insert", "RUNNING"),
                                        ("insert", "FAILED"), ("insert", None),
                                        ("readback", "PENDING"), ("readback", "FAILED")])
def test_pending_or_failed_never_throttles_or_polls(phase, state):
    instance, actor = service(), identity()
    actor.client.statement_execution.execute_statement.side_effect = (
        [response(state)] if phase == "insert" else [response(), response(state)])
    result = instance.touch(actor)
    assert not result["written"]
    assert result["status"] == ("pending" if state in {"PENDING", "RUNNING"} else "error")
    actor.client.statement_execution.get_statement.assert_not_called()
    actor.client.statement_execution.execute_statement.side_effect = [response(), response(rows=[["ORION", actor.user_id, STAMP]])]
    assert instance.touch(actor)["written"]


@pytest.mark.parametrize("rows", [[], [["other", "actual-user", STAMP]],
                                  [["ORION", "wrong-user", STAMP]],
                                  [["ORION", "actual-user", None]],
                                  [["ORION", "actual-user", "not-a-timestamp"]]])
def test_readback_must_match_row_and_actual_timestamp(rows):
    actor, instance = identity(), service()
    actor.client.statement_execution.execute_statement.side_effect = [response(), response(rows=rows)]
    assert not instance.touch(actor)["written"]
    assert next(iter(instance._users.values())).verified_at is None


def test_safe_sql_errors_and_invalid_event():
    actor, instance = identity(), service()
    assert instance.touch(actor, "browser_active_work")["reason"] == "invalid_event"
    actor.client.current_user.me.assert_not_called()
    actor.client.statement_execution.execute_statement.side_effect = RuntimeError("token=top-secret")
    assert instance.touch(actor)["error_type"] == "RuntimeError"
    assert "top-secret" not in json.dumps(instance.touch(actor))


def test_workspace_mismatch_and_unknown_error_are_safe():
    actor, instance = identity(), service()
    actor.client.config.host = "https://different.example"
    assert instance.touch(actor)["reason"] == "workspace_mismatch"
    actor.client.current_user.me.assert_not_called()
    actor = identity()
    unsafe_type = type("BearerSecretException", (Exception,), {})
    actor.client.statement_execution.execute_statement.side_effect = unsafe_type("token=secret")
    assert instance.touch(actor)["error_type"] == "OperationError"


@pytest.mark.parametrize("kind", ["truncated", "chunk"])
def test_incomplete_readback_is_not_written(kind):
    actor, instance = identity(), service()
    readback = response(rows=[["ORION", actor.user_id, STAMP]])
    if kind == "truncated":
        readback.manifest = SimpleNamespace(truncated=True)
    else:
        readback.result.next_chunk_index = 1
    actor.client.statement_execution.execute_statement.side_effect = [response(), readback]
    assert instance.touch(actor)["reason"] == "unverified_row"
    assert next(iter(instance._users.values())).verified_at is None


def test_cooldown_begins_at_verified_completion_not_insert_start(monkeypatch):
    actor, instance = identity(), service()
    clock = [0.0]
    monkeypatch.setattr(hb.time, "monotonic", lambda: clock[0])

    def execute(**kw):
        clock[0] += 5
        return response(rows=[["ORION", actor.user_id, STAMP]])

    actor.client.statement_execution.execute_statement.side_effect = execute
    assert instance.touch(actor)["written"]
    assert next(iter(instance._users.values())).verified_at == 10
    clock[0] = 300
    assert instance.touch(actor)["status"] == "throttled"


def test_per_user_lock_does_not_block_another_user():
    actor, instance = identity(), service()
    entered, finish = Event(), Event()

    def execute(**kw):
        if kw["statement"].startswith("INSERT"):
            entered.set()
            assert finish.wait(10)
        return response(rows=[["ORION", actor.user_id, STAMP]])

    actor.client.statement_execution.execute_statement.side_effect = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(instance.touch, actor)
        try:
            assert entered.wait(10)
            assert instance.touch(actor)["reason"] == "user_in_flight"
            assert instance.touch(identity("other"))["written"]
        finally:
            finish.set()
        assert work.result(timeout=10)["written"]


def test_bounded_user_map_does_not_evict_recent_cooldowns(monkeypatch):
    assert hb.MAX_USERS == 1024
    monkeypatch.setattr(hb, "MAX_USERS", 2)
    clock = [0.0]
    monkeypatch.setattr(hb.time, "monotonic", lambda: clock[0])
    instance = service()
    assert instance.touch(identity("one"))["written"]
    assert instance.touch(identity("two"))["written"]
    assert instance.touch(identity("three"))["reason"] == "capacity"
    assert len(instance._users) == 2
    clock[0] = 300
    assert instance.touch(identity("three"))["written"]
    assert len(instance._users) == 2


@pytest.fixture
def browser_page(request):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(offline=True, service_workers="block")
        page = context.new_page()
        requests, errors = [], []
        health_requests = []
        health = getattr(request, 'param', {'authentication': 'databricks_obo', 'heartbeat_enabled': True})
        script = (Path(__file__).resolve().parents[1] / "static" / "heartbeat.js").read_text(encoding="utf-8")

        def route(request):
            if request.request.url.endswith('/api/health'):
                health_requests.append(request.request)
                request.fulfill(content_type='application/json', body=json.dumps(health))
            elif request.request.url.endswith("/api/hosted/heartbeat"):
                requests.append(request.request)
                request.fulfill(status=503, content_type="application/json", body='{"secret":"never read"}')
            elif request.request.url.endswith("/heartbeat.js"):
                request.fulfill(content_type="application/javascript", body=script)
            elif request.request.url == "https://heartbeat.test/":
                request.fulfill(content_type="text/html", body=(
                    '<input><div style="height:4000px"></div><script src="/heartbeat.js"></script>'))
            else:
                request.abort()

        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", route)
        page.clock.install()
        with page.expect_response('**/api/health') as health_response:
            page.goto("https://heartbeat.test/")
            page.clock.run_for(1)
        health_response.value.finished()
        page.clock.run_for(1)
        yield page, requests, errors
        assert len(health_requests) == 1 and health_requests[0].method == 'GET'
        context.close()
        browser.close()


def test_browser_trusted_only_no_startup_or_idle_writes(browser_page):
    page, requests, errors = browser_page
    page.clock.run_for(900001)
    assert not requests
    page.evaluate("""() => {
      for (const name of ['pointerdown', 'keydown', 'input', 'scroll'])
        document.dispatchEvent(new Event(name, {bubbles: true}));
    }""")
    assert not requests
    page.locator("input").click()
    page.wait_for_function("true")
    assert len(requests) == 1
    req = requests[0]
    assert req.method == "POST" and req.post_data is None
    assert req.headers["x-workbench-request"] == "1"
    assert "authorization" not in req.headers
    page.clock.run_for(1200000)
    assert len(requests) == 1 and not errors


def test_browser_programmatic_scroll_is_not_activity_but_real_wheel_is(browser_page):
    page, requests, errors = browser_page
    page.evaluate("scrollTo(0, 500)")
    page.clock.run_for(1001)
    assert not requests
    page.evaluate("document.dispatchEvent(new WheelEvent('wheel', {deltaY: 400}))")
    page.evaluate("scrollTo(0, 1000)")
    page.clock.run_for(1001)
    assert not requests
    page.mouse.move(400, 400)
    page.mouse.wheel(0, 500)
    page.wait_for_function("scrollY > 1000")
    page.wait_for_function("true")
    assert len(requests) == 1
    page.clock.run_for(900001)
    assert len(requests) == 1 and not errors


def test_browser_new_activity_throttled_hidden_stops_and_failure_quiet(browser_page):
    page, requests, errors = browser_page
    page.locator("input").click()
    page.wait_for_function("true")
    assert len(requests) == 1
    page.locator("input").fill("real human input")
    page.clock.run_for(299000)
    assert len(requests) == 1
    page.clock.run_for(1001)
    page.wait_for_function("true")
    assert len(requests) == 2
    page.locator("input").press("a")
    # Browser test override of visibility, not a production bypass of isTrusted.
    page.evaluate("""() => {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.locator("input").press("b")
    page.clock.run_for(600001)
    assert len(requests) == 2
    page.evaluate("""() => {
      Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.clock.run_for(600001)
    assert len(requests) == 2
    page.locator("input").press("c")
    page.wait_for_function("true")
    assert len(requests) == 3 and not errors


@pytest.mark.parametrize('browser_page', [
    {'authentication': 'not_implemented', 'local_only': True, 'heartbeat_enabled': False},
    {'authentication': 'databricks_obo', 'heartbeat_enabled': False},
    {'authentication': 'databricks_obo'},
    {'authentication': 'not_implemented', 'heartbeat_enabled': True},
], indirect=True)
def test_browser_local_disabled_or_unconfirmed_never_posts_or_polls(browser_page):
    page, requests, errors = browser_page
    page.locator('input').click()
    page.locator('input').fill('actual interaction')
    page.mouse.move(400, 400)
    page.mouse.wheel(0, 500)
    page.clock.run_for(1001)
    for visibility in ['hidden', 'visible']:
        page.evaluate('''value => {
            Object.defineProperty(document, 'visibilityState', {configurable: true, value});
            document.dispatchEvent(new Event('visibilitychange'));
        }''', visibility)
    page.locator('input').press('a')
    page.clock.run_for(1200000)
    assert not requests and not errors
