"""Offline Chromium contracts for the standalone hosted intake integration API.

No parent scripts, live server, Databricks credentials or network are needed.
Only the three-second poll delay is accelerated, and its requested value is checked.
"""
from pathlib import Path
import hashlib
import json
import re

import pytest
from playwright.sync_api import expect, sync_playwright


SCRIPT = (Path(__file__).resolve().parents[1] / "static" / "hosted-intake.js").read_text(encoding="utf-8")
CSS = (Path(__file__).resolve().parents[1] / "static" / "app.css").read_text(encoding="utf-8")
PROJECT = "a" * 32
CHUNK = 4 * 1024 * 1024
STORE = "orion.hosted-intake.v1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context()
    page = context.new_page()
    state = {"requests": [], "uploads": {}, "operations": {}, "errors": [], "held": [],
             "authentication": "databricks_obo", "hold": None, "chunk_failure": None,
             "stage_status": "completed", "analysis_result": {"status": "completed", "items": []},
             "post_error": None, "health_error": False, "file_limit": None,
             "analysis_status": "completed", "hold_after_analysis_running": False}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))

    def public(upload):
        chunks = upload["chunks"]
        ready = len(chunks) == upload["total_chunks"]
        return {key: value for key, value in upload.items() if key != "chunks"} | {
            "chunks": list(chunks.values()), "durable_bytes": sum(c["size_bytes"] for c in chunks.values()),
            "status": "ready_for_finalize" if ready else "uploading", "ready_for_finalize": ready,
        }

    def respond(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def handler(route):
        request = route.request
        path = request.url.split(".invalid", 1)[-1]
        if path == "/":
            route.fulfill(content_type="text/html", body='<link rel="stylesheet" href="/static/app.css"><header class="topbar"><button>Theme</button></header><div class="app-shell"><nav><button>Navigate</button></nav><main id="main"><form id="upload"><input type="file" multiple><span class="spinner">Existing spinner</span></form></main></div>')
            return
        if path == "/static/app.css":
            route.fulfill(content_type="text/css", body=CSS)
            return
        body = request.post_data_json if request.method == "POST" and request.post_data else None
        state["requests"].append({"method": request.method, "path": path, "body": body,
                                  "headers": request.all_headers()})
        if state["hold"] and state["hold"] in path:
            state["held"].append(route)
            return
        if path == "/api/health":
            respond(route, {"authentication": state["authentication"]}, 503 if state["health_error"] else 200)
        elif path == "/api/hosted/uploads" and request.method == "POST":
            if state["file_limit"] and body["size_bytes"] > state["file_limit"]:
                respond(route, {"detail": "Configured hosted file limit exceeded."}, 413)
                return
            upload = {**body, "chunk_bytes": CHUNK, "total_chunks": (body["size_bytes"] + CHUNK - 1) // CHUNK, "chunks": {}}
            state["uploads"][body["id"]] = upload
            respond(route, public(upload), 201)
        elif re.fullmatch(r"/api/hosted/uploads/[a-f0-9]{32}(?:/complete|/chunks/\d+)?", path):
            upload = state["uploads"].get(path.split("/")[4])
            if upload is None:
                respond(route, {"detail": "Upload not found."}, 404)
                return
            if "/chunks/" in path:
                index = int(path.rsplit("/", 1)[-1])
                content = request.post_data_buffer
                ack = {"index": index, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                if state["chunk_failure"] == "durable":
                    upload["chunks"][index] = ack
                if state["chunk_failure"]:
                    respond(route, {"detail": "Chunk receipt response lost."}, 503)
                else:
                    upload["chunks"][index] = ack
                    respond(route, ack)
            else:
                respond(route, public(upload))
        elif path == "/api/hosted/operations" and request.method == "POST":
            state["operations"][body["id"]] = body
            if state["post_error"]:
                route.abort("failed")
            else:
                respond(route, {"operation_id": body["id"], "status": "queued"}, 202)
        elif path.startswith("/api/hosted/operations/"):
            identifier = path.rsplit("/", 1)[-1]
            operation = state["operations"][identifier]
            stage = operation["path"].endswith("/finalize")
            status = state["stage_status"] if stage else state["analysis_status"]
            result = {"status": "staged", "items": [{"name": "source.xml", "status": "staged"}]} if stage else state["analysis_result"]
            if not stage and state["hold_after_analysis_running"]:
                state["hold"] = "/api/hosted/operations/"
            respond(route, {"operation_id": identifier, "status": status, "result": result,
                            "error": {"detail": "Recorded stage failure."}, "recovery": "Original process ended. Inspect state."})
        else:
            pytest.fail(f"Unexpected request: {request.method} {path}")

    context.route("**/*", handler)
    page.goto("https://hosted-intake.invalid/")
    page.evaluate("""() => {
      window.pollDelays = []; window.refreshes = []; window.current = true;
      const original = window.setTimeout.bind(window);
      window.setTimeout = (fn, delay, ...args) => {
        if (delay === 3000) window.pollDelays.push(delay);
        return original(fn, delay === 3000 ? 5 : delay, ...args);
      };
      window.options = {isCurrent: () => window.current,
        refresh: async (...args) => { window.refreshes.push(args); }};
      window.makeFile = (name, size) => ({name, size,
        slice: (start, end) => { window.sliceSizes.push(end - start); return new Blob([new Uint8Array(end - start)]); },
        arrayBuffer: () => { throw new Error('Full file arrayBuffer forbidden'); }});
      window.sliceSizes = [];
    }""")
    page.add_script_tag(content=SCRIPT)
    yield page, state
    # Resolve deliberately held requests before closing their context.
    for route in state["held"]:
        try:
            route.abort()
        except Exception:
            pass
    context.close()
    assert not state["errors"], state["errors"]


def start(page, files=None):
    page.evaluate("""({project, files}) => {
      window.done = false; window.failure = null;
      window.work = orionHostedIntake.upload(document.querySelector('form'),
        files.map(([name, size]) => makeFile(name, size)), project, options)
        .then(value => { window.handled = value; window.done = true; })
        .catch(error => { window.failure = error.message; window.done = true; });
    }""", {"project": PROJECT, "files": files or [["source.xml", CHUNK + 3]]})


def finished(page):
    page.wait_for_function("window.done === true")
    assert page.evaluate("window.failure") is None
    assert page.evaluate("window.handled") is True


def test_reconciled_commit_remains_failed_and_never_resubmits_or_claims_operation_success(ui):
    page, state = ui
    def observe(route):
        identifier = route.request.url.rsplit("/", 1)[-1]
        route.fulfill(content_type="application/json", body=json.dumps({
            "operation_id": identifier, "status": "failed", "progress": {"stage": "failed"},
            "observation": {"observation": "committed_with_ack_failure"},
            "recovery": "Committed analysis independently verified after acknowledgement failure. "
                        "Original execution remains failed. Do not replay the rebuild."}))
    page.route("**/api/hosted/operations/*", observe)
    page.evaluate("""() => {
      window.done = false; window.resolved = false;
      orionHostedIntake.submit('/api/source-analysis/rebuild', {}, {'X-Workbench-Project': 'a'.repeat(32)})
        .then(() => { window.resolved = true; window.done = true; })
        .catch(error => { window.failure = error.message; window.done = true; });
    }""")
    page.wait_for_function("window.done === true")
    assert not page.evaluate("window.resolved")
    failure = page.evaluate("window.failure")
    assert "failed" in failure and "independently verified" in failure and "Do not replay" in failure
    expect(page.locator("#hosted-activity-message")).to_contain_text("acknowledgement failure")
    expect(page.locator("#hosted-activity-message")).not_to_contain_text("Operation completed")
    posts = [r for r in state["requests"] if r["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["path"] == "/api/hosted/operations"


def posts(state, path=None):
    return [r for r in state["requests"] if r["method"] == "POST" and (path is None or r["path"] == path)]


def test_large_hosted_file_bounded_chunks_and_committed_autoanalysis(ui):
    page, state = ui
    size = 81 * 1024 * 1024
    start(page, [["large.XML", size]])
    finished(page)
    manifest = posts(state, "/api/hosted/uploads")[0]["body"]
    assert manifest["size_bytes"] == size
    assert re.fullmatch(r"[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}", manifest["id"])
    assert max(page.evaluate("window.sliceSizes")) == CHUNK
    assert sum(page.evaluate("window.sliceSizes")) == size
    operations = posts(state, "/api/hosted/operations")
    assert [r["body"]["path"] for r in operations] == [
        f'/api/hosted/uploads/{manifest["id"]}/finalize', f"/api/projects/{PROJECT}/analyze"]
    assert all(r["body"]["method"] == "POST" and r["body"]["body"] == {} and r["body"]["query"] == "" for r in operations)
    assert all(r["headers"]["x-workbench-request"] == "1" for r in state["requests"] if r["method"] in {"POST", "PUT"})
    assert page.evaluate("window.pollDelays") == [3000, 3000]
    assert len(page.evaluate("window.refreshes")) == 2
    expect(page.get_by_label("Transfer: durable server-acknowledged bytes", exact=True)).to_have_attribute("value", str(size))
    expect(page.locator(".spinner")).to_have_text("Existing spinner")
    expect(page.locator("form + section[role=status][aria-live=polite]")).to_be_visible()
    # Navigation may hide the form and adjacent page, but not the activity summary.
    page.locator("form").evaluate("form => form.hidden = true")
    expect(page.locator("#orion-hosted-intake-activity")).to_be_visible()


@pytest.mark.parametrize("mode,expected", [("missing", 0), ("durable", CHUNK)])
def test_failed_chunk_recovers_ack_without_automatic_retry(ui, mode, expected):
    page, state = ui
    state["chunk_failure"] = mode
    start(page)
    finished(page)
    expect(page.get_by_label("Transfer: durable server-acknowledged bytes", exact=True)).to_have_attribute("value", str(expected))
    assert len([r for r in state["requests"] if r["method"] == "PUT"]) == 1
    assert not posts(state, "/api/hosted/operations")
    expect(page.locator(".hosted-intake-status")).to_contain_text("Reselect the same file")
    assert len([r for r in state["requests"] if r["method"] == "GET" and "/uploads/" in r["path"]]) == 1
    # File reselection offers an explicit resume, but does not send anything yet.
    state["chunk_failure"] = None
    start(page)
    finished(page)
    assert len([r for r in state["requests"] if r["method"] == "PUT"]) == 1
    page.get_by_role("button", name="Resume selected file").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("Recorded result: completed")
    assert len(posts(state, "/api/hosted/uploads")) == 1
    assert len([r for r in state["requests"] if r["method"] == "PUT"]) == (3 if mode == "missing" else 2)


def test_transport_does_not_invent_durable_bytes_and_analysis_is_indeterminate(ui):
    page, state = ui
    # Intercepted network requests do not emit native upload progress. Drive the
    # XHR transport callback explicitly, independently of its later server ACK.
    page.evaluate("""() => {
      window.XMLHttpRequest = class {
        constructor() { this.upload = {}; }
        open() {}
        setRequestHeader() {}
        send() {
          window.pendingChunk = this;
          this.upload.onprogress({loaded: 2});
        }
      };
    }""")
    start(page, [["source.xml", 3]])
    page.wait_for_function("document.querySelector('progress[aria-label^=Transport]')?.value > 0")
    expect(page.get_by_label("Transfer: durable server-acknowledged bytes", exact=True)).to_have_attribute("value", "0")
    expect(page.get_by_label("Active hosted file: durable acknowledged bytes", exact=True)).to_have_attribute("value", "0")
    expect(page.locator("#orion-hosted-intake-activity")).to_have_class(re.compile(r"\bis-busy\b"))
    expect(page.locator("#orion-hosted-intake-activity .hosted-intake-spinner")).to_be_visible()
    expect(page.locator("#orion-hosted-intake-activity")).to_contain_text("Running.")
    upload = next(iter(state["uploads"].values()))
    ack = {"index": 0, "size_bytes": 3, "sha256": hashlib.sha256(bytes(3)).hexdigest()}
    upload["chunks"][0] = ack
    state["hold"] = "/api/hosted/operations/"
    page.evaluate("""ack => {
      pendingChunk.status = 200; pendingChunk.responseText = JSON.stringify(ack);
      pendingChunk.onload();
    }""", ack)
    page.wait_for_function("document.querySelector('progress[aria-label^=Transfer]')?.value > 0")
    expect(page.get_by_label("Active hosted file: durable acknowledged bytes", exact=True)).to_have_attribute("value", "3")
    expect(page.get_by_label("Staging: progress unknown", exact=True)).not_to_have_attribute("value", re.compile(".+"))
    expect(page.get_by_label("Analysis: progress unknown", exact=True)).not_to_have_attribute("value", re.compile(".+"))


def test_stale_selection_never_refreshes_or_submits_analysis(ui):
    page, state = ui
    state["hold"] = "/api/hosted/operations/"
    start(page, [["source.xml", 3]])
    page.wait_for_function("window.pollDelays.length === 1")
    expect(page.locator(".hosted-intake-status")).to_contain_text("Staging: queued")
    # Dispatch the timer/held GET before changing the captured selection.
    page.wait_for_timeout(30)
    assert len(state["held"]) == 1
    operation_id = state["held"][0].request.url.rsplit("/", 1)[-1]
    page.evaluate("window.current = false")
    state["held"].pop().fulfill(content_type="application/json", body=json.dumps({
        "operation_id": operation_id, "status": "completed", "result": {"status": "staged"}}))
    finished(page)
    assert page.evaluate("window.refreshes") == []
    assert len(posts(state, "/api/hosted/operations")) == 1
    expect(page.locator(".hosted-intake-status")).to_contain_text("Selection changed")


def test_local_probe_returns_false_without_mutation_or_ui(ui):
    page, state = ui
    state["authentication"] = "not_implemented"
    start(page)
    page.wait_for_function("window.done")
    assert page.evaluate("window.handled") is False
    assert not posts(state)
    assert page.locator(".hosted-intake-status").count() == 0


def test_probe_caches_only_positive_obo_and_failure_never_falls_back(ui):
    page, state = ui
    state["authentication"] = "local"
    assert page.evaluate("orionHostedIntake.probe()") is False
    state["authentication"] = "databricks_obo"
    assert page.evaluate("orionHostedIntake.probe()") is True
    state["health_error"] = True
    assert page.evaluate("orionHostedIntake.probe()") is True
    assert len(state["requests"]) == 2


def test_unknown_health_does_not_start_local_or_hosted_upload(ui):
    page, state = ui
    state["health_error"] = True
    start(page)
    page.wait_for_function("window.done")
    assert "HTTP 503" in page.evaluate("window.failure")
    assert not posts(state)


@pytest.mark.parametrize("status", ["failed", "authentication_required", "interrupted"])
def test_terminal_stage_failures_never_analyze_or_replay(ui, status):
    page, state = ui
    state["stage_status"] = status
    start(page, [["source.xml", 3]])
    finished(page)
    expect(page.locator(".hosted-intake-status")).to_contain_text(status)
    assert len(posts(state, "/api/hosted/operations")) == 1
    page.get_by_role("button", name="Reconnect to recorded operation").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("No operation was replayed")
    assert len(posts(state, "/api/hosted/operations")) == 1


def test_partial_analysis_and_every_file_outcome_survive(ui):
    page, state = ui
    state["file_limit"] = 10
    state["analysis_result"] = {"status": "partial", "items": [
        {"name": "valid.xml", "status": "completed"},
        {"name": "bad.xml", "status": "failed", "error": "Invalid XML"}]}
    start(page, [["too-large.xml", 11], ["valid.xml", 3], ["not-xml.txt", 3]])
    finished(page)
    assert page.locator(".hosted-intake-status article").count() == 3
    expect(page.locator(".hosted-intake-status")).to_contain_text("Configured hosted file limit exceeded")
    expect(page.locator(".hosted-intake-status")).to_contain_text("bad.xml: failed (Invalid XML)")
    expect(page.locator(".hosted-intake-status")).to_contain_text("nonempty XML file")
    expect(page.get_by_label("Analysis: progress unknown", exact=True).nth(1)).not_to_have_attribute("value", re.compile(".+"))


def test_lost_operation_post_response_reconnects_by_get_only_and_metadata_is_safe(ui):
    page, state = ui
    state["post_error"] = True
    start(page, [["source.xml", 3]])
    finished(page)
    stored = page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", STORE)
    assert len(stored) == 1
    assert set(stored[0]) == {"id", "project_id", "name", "size_bytes", "stage_operation_id"}
    assert stored[0]["stage_operation_id"] in state["operations"]
    state["post_error"] = None
    page.get_by_role("button", name="Reconnect to recorded operation").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("No analysis operation recorded")
    assert len(posts(state, "/api/hosted/operations")) == 1


def test_submit_helper_uses_hosted_operation_and_does_not_store_headers_or_body(ui):
    page, state = ui
    result = page.evaluate("""project => orionHostedIntake.submit(`/api/projects/${project}/analyze`,
      {label: 'not-persisted'}, {'X-Review': 'not-persisted-either'})""", PROJECT)
    assert result["status"] == "completed"
    operation = posts(state, "/api/hosted/operations")[0]
    assert operation["body"]["body"] == {"label": "not-persisted"}
    assert operation["headers"]["x-review"] == "not-persisted-either"
    assert "not-persisted" not in page.evaluate("key => sessionStorage.getItem(key)", STORE)


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_close_completed_notification_preserves_history_and_explicit_reconnect(ui, key):
    page, state = ui
    start(page, [["source.xml", 3]])
    finished(page)
    panel = page.locator("#orion-hosted-intake-activity")
    stored = page.evaluate("key => sessionStorage.getItem(key)", STORE)
    count = len(state["requests"])
    close = panel.get_by_role("button", name="Close operation notification", exact=True)
    close.focus()
    close.press(key)
    expect(panel).to_be_hidden()
    # An ordinary render/navigation must not resurrect the notification.
    page.locator("form").evaluate("form => form.hidden = true")
    expect(panel).to_be_hidden()
    assert page.evaluate("key => sessionStorage.getItem(key)", STORE) == stored
    assert len(state["requests"]) == count
    page.get_by_role("button", name="Reconnect to recorded operation", exact=True).click()
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("No operation was replayed")
    assert all(r["method"] == "GET" for r in state["requests"][count:])
    assert page.evaluate("key => sessionStorage.getItem(key)", STORE) == stored


@pytest.mark.parametrize("method", ["submit"])
def test_close_active_notification_does_not_cancel_poll_or_reopen_on_completion(ui, method):
    page, state = ui
    state["hold"] = "/api/hosted/operations/"
    page.evaluate("""method => {
      window.notificationDone = false;
      orionHostedIntake[method](method === 'read' ? '/api/overview' : '/api/generic-action')
        .then(result => { window.notificationResult = result; window.notificationDone = true; });
    }""", method)
    panel = page.locator("#orion-hosted-intake-activity")
    expect(panel).to_have_attribute("aria-busy", "true")
    page.wait_for_function("window.pollDelays.length === 1")
    page.wait_for_timeout(30)
    assert len(state["held"]) == 1
    stored = page.evaluate("key => sessionStorage.getItem(key)", STORE)
    count = len(state["requests"])
    panel.get_by_role("button", name="Minimize operation progress", exact=True).click()
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    assert len(state["requests"]) == count
    route = state["held"].pop()
    identifier = route.request.url.rsplit("/", 1)[-1]
    # A later running poll update must also respect dismissal.
    route.fulfill(json={"operation_id": identifier, "status": "running"})
    page.wait_for_function("window.pollDelays.length === 2")
    page.wait_for_timeout(30)
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    state["held"].pop().fulfill(json={
        "operation_id": identifier, "status": "completed", "result": {"status": "completed"}})
    page.wait_for_function("window.notificationDone")
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    expect(panel).to_have_attribute("aria-busy", "false")
    assert page.evaluate("window.notificationResult") == {"status": "completed"}
    assert page.evaluate("key => sessionStorage.getItem(key)", STORE) == stored
    assert len(posts(state)) == 1
    state["hold"] = None
    page.evaluate("orionHostedIntake.submit('/api/generic-action')")
    expect(panel).to_be_visible()
    assert len(posts(state)) == 2


def test_analysis_running_has_no_percent_and_polls_never_overlap(ui):
        page, state = ui
        state["analysis_status"] = "running"
        state["hold_after_analysis_running"] = True
        start(page, [["source.xml", 3]])
        expect(page.locator(".hosted-intake-status")).to_contain_text("Analysis: running, progress unknown")
        page.wait_for_timeout(50)
        assert len(state["held"]) == 1
        count = len(state["requests"])
        page.wait_for_timeout(50)
        assert len(state["requests"]) == count
        expect(page.get_by_label("Analysis: progress unknown", exact=True)).not_to_have_attribute("value", re.compile(".+"))
        assert page.evaluate("window.done") is False


def test_chunk_timeout_is_110_seconds_and_recovers_without_resending(ui):
        page, state = ui
        page.evaluate("""() => {
            window.XMLHttpRequest = class {
                constructor() { this.upload = {}; }
                open() {}
                setRequestHeader() {}
                send(blob) {
                    window.chunkTimeout = this.timeout;
                    this.upload.onprogress({loaded: blob.size});
                    queueMicrotask(() => this.ontimeout());
                }
            };
        }""")
        start(page, [["source.xml", 3]])
        finished(page)
        assert page.evaluate("window.chunkTimeout") == 110000
        expect(page.locator(".hosted-intake-status")).to_contain_text("timed out after 110 seconds")
        expect(page.get_by_label("Transfer: durable server-acknowledged bytes", exact=True)).to_have_attribute("value", "0")
        assert not posts(state, "/api/hosted/operations")
        assert len([r for r in state["requests"] if r["method"] == "GET" and "/uploads/" in r["path"]]) == 1


def seed_resume(page, state, index=1):
    identifier = "b" * 32
    size = CHUNK + 3
    chunk_size = CHUNK if index == 0 else 3
    record = {"id": identifier, "project_id": PROJECT, "name": "source.xml", "size_bytes": size}
    state["uploads"][identifier] = {**record, "chunk_bytes": CHUNK, "total_chunks": 2,
        "chunks": {index: {"index": index, "size_bytes": chunk_size,
                           "sha256": hashlib.sha256(bytes(chunk_size)).hexdigest()}}}
    page.evaluate("({key, record}) => sessionStorage.setItem(key, JSON.stringify([record]))",
                  {"key": STORE, "record": record})
    return identifier


@pytest.mark.parametrize("index", [0, 1])
def test_reselected_same_name_and_size_with_altered_content_blocks_all_mutations(ui, index):
    page, state = ui
    seed_resume(page, state, index)
    page.evaluate("""() => {
      window.makeFile = (name, size) => ({name, size,
        slice: (start, end) => {
          window.sliceSizes.push(end - start);
          return new Blob([new Uint8Array(end - start).fill(1)]);
        }, arrayBuffer: () => { throw new Error('Full file read forbidden'); }});
    }""")
    start(page)
    finished(page)
    assert not posts(state)
    page.get_by_role("button", name="Resume selected file").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("Selected file content does not match durable chunk")
    expect(page.locator(".hosted-intake-status")).to_contain_text("Resume attempt finished")
    assert not posts(state)
    assert not [r for r in state["requests"] if r["method"] == "PUT"]
    assert max(page.evaluate("window.sliceSizes")) <= CHUNK


@pytest.mark.parametrize("index", [0, 1])
def test_identical_reselected_content_verifies_bounded_slices_then_sends_only_missing_chunks(ui, index):
    page, state = ui
    identifier = seed_resume(page, state, index)
    start(page)
    finished(page)
    assert page.evaluate("window.sliceSizes") == []
    page.get_by_role("button", name="Resume selected file").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("Recorded result: completed")
    puts = [r for r in state["requests"] if r["method"] == "PUT"]
    assert [r["path"] for r in puts] == [f"/api/hosted/uploads/{identifier}/chunks/{1 - index}"]
    assert page.evaluate("window.sliceSizes") == ([CHUNK, 3] if index == 0 else [3, CHUNK])
    assert len(posts(state, "/api/hosted/operations")) == 2
    assert not posts(state, "/api/hosted/uploads")


def test_crypto_unavailable_blocks_skipping_existing_chunks_without_fallback(ui):
    page, state = ui
    seed_resume(page, state)
    page.evaluate("Object.defineProperty(crypto, 'subtle', {value: undefined})")
    start(page)
    finished(page)
    page.get_by_role("button", name="Resume selected file").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("SHA-256 verification requires crypto.subtle")
    expect(page.locator(".hosted-intake-status")).to_contain_text("Resume attempt finished")
    assert page.evaluate("window.sliceSizes") == []
    assert not posts(state)
    assert not [r for r in state["requests"] if r["method"] == "PUT"]


@pytest.mark.parametrize("digest", [None, "not-a-hash", "0" * 63, "g" * 64, ["0" * 64]])
def test_resume_requires_valid_server_chunk_digest_before_mutation(ui, digest):
    page, state = ui
    identifier = seed_resume(page, state)
    chunk = state["uploads"][identifier]["chunks"][1]
    if digest is None:
        chunk.pop("sha256")
    else:
        chunk["sha256"] = digest
    start(page)
    finished(page)
    page.get_by_role("button", name="Resume selected file").click()
    expect(page.locator(".hosted-intake-status")).to_contain_text("Invalid durable chunk SHA-256 receipt")
    expect(page.locator(".hosted-intake-status")).to_contain_text("Resume attempt finished")
    assert not posts(state)
    assert not [r for r in state["requests"] if r["method"] == "PUT"]


def test_generic_submit_has_standalone_activity_project_header_and_committed_queued_result(ui):
    page, state = ui
    state["hold"] = "/api/hosted/operations/"
    state["analysis_result"] = {"status": "queued"}
    page.evaluate("""project => {
      window.genericDone = false;
      window.genericWork = orionHostedIntake.submit('/api/generic-action',
        {private: 'never-record'}, {'X-Workbench-Project': project})
        .then(result => { window.genericResult = result; window.genericDone = true; });
    }""", PROJECT)
    panel = page.locator("#orion-hosted-intake-activity")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Waiting.")
    expect(panel).not_to_contain_text("Running.")
    # This acknowledgement has only a queued status, not measured progress.
    expect(panel).to_contain_text("Queued for workspace processing.")
    expect(panel).to_have_class(re.compile(r"\bis-busy\b"))
    expect(panel).to_have_attribute("aria-busy", "true")
    expect(panel.locator(".hosted-intake-spinner")).to_be_visible()
    expect(panel.get_by_label("Queued for workspace processing (current phase only; count not reported)", exact=True)).not_to_have_attribute("value", re.compile(".+"))
    expect(page.get_by_label("Active hosted file: durable acknowledged bytes", exact=True)).to_be_hidden()
    stored = page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", STORE)
    assert stored[0]["project_id"] == PROJECT
    identifier = stored[0]["stage_operation_id"]
    expect(panel.locator('#hosted-activity-message')).not_to_contain_text(identifier)
    expect(panel.locator('details p')).to_contain_text(identifier)
    expect(panel.locator('details p')).to_be_hidden()
    assert "never-record" not in page.evaluate("key => sessionStorage.getItem(key)", STORE)
    expect(panel.get_by_role("button", name="Reconnect to last recorded operation")).to_be_disabled()
    page.wait_for_function("window.pollDelays.length === 1")
    page.wait_for_timeout(30)
    state["held"].pop().fulfill(content_type="application/json", body=json.dumps({
        "operation_id": identifier, "status": "completed", "result": {"status": "queued"}}))
    page.wait_for_function("window.genericDone")
    expect(panel).to_contain_text("completed. Returned result: queued")
    expect(panel).to_have_attribute("aria-busy", "false")
    expect(panel.locator(".hosted-intake-spinner")).to_be_hidden()
    assert page.evaluate("window.genericResult") == {"status": "queued"}
    assert len(posts(state)) == 1


def test_generic_lost_response_and_reload_reconnect_last_record_without_resubmit(ui):
    page, state = ui
    state["post_error"] = True
    page.evaluate("""project => orionHostedIntake.submit('/api/generic-action', {},
      {'X-Workbench-Project': project}).catch(error => { window.genericFailure = error.message; })""", PROJECT)
    panel = page.locator("#orion-hosted-intake-activity")
    expect(panel).to_contain_text("Reconnect to inspect recorded state")
    identifier = next(iter(state["operations"]))
    state["post_error"] = None
    count = len(state["requests"])
    page.reload()
    page.add_script_tag(content=SCRIPT)
    expect(panel.locator('#hosted-activity-message')).not_to_contain_text(identifier)
    expect(panel.locator('details p')).to_contain_text(identifier)
    expect(panel).to_contain_text("Recorded operation available")
    # Loading the script renders reconnect metadata only, not a GET or POST.
    assert len(state["requests"]) == count
    state["hold"] = "/api/hosted/operations/"
    page.get_by_role("button", name="Reconnect to last recorded operation").click()
    expect(panel).to_contain_text("Connecting. Operation: reconnecting to recorded state.")
    expect(panel).not_to_contain_text("Running.")
    page.wait_for_timeout(30)
    state["held"].pop().fulfill(json={
        "operation_id": identifier, "status": "completed", "result": {"status": "completed"}})
    expect(panel).to_contain_text("No operation was replayed")
    assert len(posts(state)) == 1
    assert [r["path"] for r in state["requests"][count:]] == [f"/api/hosted/operations/{identifier}"]


@pytest.mark.parametrize("header", ["invalid", "", "0" * 31])
def test_generic_submit_invalid_project_header_uses_project_path(ui, header):
    page, state = ui
    page.evaluate("""({project, header}) => orionHostedIntake.submit(`/api/projects/${project}/analyze`,
      {}, {'X-Workbench-Project': header})""", {"project": PROJECT, "header": header})
    stored = page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", STORE)
    assert stored[-1]["project_id"] == PROJECT
    assert len(posts(state)) == 1


def test_new_upload_without_crypto_still_uses_server_receipts(ui):
        page, state = ui
        page.evaluate("Object.defineProperty(crypto, 'subtle', {value: undefined})")
        start(page, [["source.xml", 3]])
        finished(page)
        expect(page.locator(".hosted-intake-status")).to_contain_text("Recorded result: completed")
        puts = [r for r in state["requests"] if r["method"] == "PUT"]
        assert len(puts) == 1
        assert "x-chunk-sha256" not in puts[0]["headers"]
        upload = next(iter(state["uploads"].values()))
        assert upload["chunks"][0]["sha256"] == hashlib.sha256(bytes(3)).hexdigest()
        assert len(posts(state, "/api/hosted/operations")) == 2


@pytest.mark.parametrize("status", ["failed", "authentication_required", "interrupted"])
def test_generic_terminal_failure_reconnect_is_observational(ui, status):
        page, state = ui
        state["analysis_status"] = status
        page.evaluate("""() => orionHostedIntake.submit('/api/generic-action')
            .catch(error => { window.genericFailure = error.message; })""")
        panel = page.locator("#orion-hosted-intake-activity")
        expect(panel).to_contain_text(status)
        expect(panel).to_have_attribute("aria-busy", "false")
        page.get_by_role("button", name="Reconnect to last recorded operation").click()
        expect(panel).to_contain_text("No operation was replayed")
        assert len(posts(state)) == 1


@pytest.mark.parametrize("status", ["completed", "failed", "authentication_required", "interrupted"])
def test_inline_activity_keyboard_collapse_and_terminal_cleanup(ui, status):
    page, state = ui
    page.add_style_tag(content=(Path(__file__).resolve().parents[1] / "static" / "app.css").read_text(encoding="utf-8"))
    page.get_by_role("button", name="Navigate", exact=True).focus()
    state["hold"] = "/api/hosted/operations/"
    page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}); }")
    panel = page.locator("#orion-hosted-intake-activity")
    expect(panel).to_have_attribute("role", "status")
    expect(panel).not_to_have_attribute("aria-modal", "true")
    expect(page.locator(".app-shell")).not_to_have_attribute("inert", "")
    expect(page.locator(".topbar")).not_to_have_attribute("inert", "")
    assert page.locator(".app-shell").evaluate("node => getComputedStyle(node).filter") == "none"
    expect(page.get_by_role("button", name="Navigate", exact=True)).to_be_focused()
    page.get_by_role("button", name="Minimize operation progress").press("Enter")
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    expect(page.locator(".app-shell")).not_to_have_attribute("inert", "")
    show = page.get_by_role("button", name="Expand operation progress")
    expect(show).to_be_focused()
    show.press("Space")
    expect(panel).to_be_visible()
    page.wait_for_function("window.pollDelays.length === 1")
    # Wait for the actual held request, not an assumed timer completion.
    page.wait_for_timeout(30)
    route = state["held"].pop()
    identifier = route.request.url.rsplit("/", 1)[-1]
    route.fulfill(json={"operation_id": identifier, "status": status,
                        "result": {"status": "completed"}, "recovery": "Inspect state"})
    expect(page.locator(".app-shell")).not_to_have_attribute("inert", "")
    expect(page.locator(".topbar")).not_to_have_attribute("inert", "")
    expect(page.locator("#orion-hosted-operation-backdrop")).to_have_count(0)
    expect(panel).to_have_attribute("role", "status")
    expect(panel).not_to_have_attribute("aria-modal", "true")
    expect(page.get_by_role("button", name="Navigate", exact=True)).to_be_enabled()


def test_overlapping_mutation_is_rejected_without_overwriting_activity_or_prior_inert(ui):
    page, state = ui
    state["hold"] = "/api/hosted/operations/"
    page.locator(".topbar").evaluate("node => node.inert = true")
    page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}); orionHostedIntake.submit('/api/other-action').catch(error => { window.overlapFailure = error.message; }); }")
    page.wait_for_function("window.pollDelays.length === 1 && window.overlapFailure")
    page.wait_for_timeout(30)
    assert len(state["held"]) == 1 and len(posts(state)) == 1
    assert 'already active' in page.evaluate('window.overlapFailure')
    route = state["held"].pop()
    identifier = route.request.url.rsplit("/", 1)[-1]
    expect(page.locator('#orion-hosted-intake-activity details p')).to_contain_text(identifier)
    route.fulfill(json={"operation_id": identifier, "status": "completed", "result": {}})
    expect(page.locator(".app-shell")).not_to_have_attribute("inert", "")
    expect(page.locator(".topbar")).to_have_attribute("inert", "")


@pytest.mark.parametrize('width', [1440, 390])
def test_inline_progress_geometry_details_and_untrapped_navigation(ui, width):
        page, state = ui
        page.set_viewport_size({'width': width, 'height': 850})
        page.add_style_tag(content=(Path(__file__).resolve().parents[1] / 'static' / 'app.css').read_text(encoding='utf-8'))
        state['hold'] = '/api/hosted/operations/'
        page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}); }")
        panel = page.locator('#orion-hosted-intake-activity')
        expect(panel).to_have_attribute('role', 'status')
        assert panel.evaluate("element => element.closest('main') !== null")
        assert panel.evaluate("element => getComputedStyle(element).position") == 'relative'
        geometry = panel.evaluate('''element => {
            const box = element.getBoundingClientRect();
            return {left: box.left, right: box.right, width: box.width,
                overflow: element.scrollWidth > element.clientWidth,
                buttonsInside: Array.from(element.querySelectorAll('button')).every(button => {
                    const b = button.getBoundingClientRect(); return b.left >= box.left && b.right <= box.right;
                })};
        }''')
        assert geometry['left'] >= 0 and geometry['right'] <= width
        assert not geometry['overflow'] and geometry['buttonsInside']
        if width == 1440:
                assert geometry['width'] >= 600
        expect(panel.locator('details')).not_to_have_attribute('open', '')
        close = panel.get_by_role('button', name='Minimize operation progress')
        expect(close).to_have_text('−')
        expect(close).to_have_attribute('title', 'Collapse activity details. Work continues; this does not cancel it.')
        expect(panel.locator('.hosted-activity-header button')).to_have_count(2)
        expect(panel.get_by_role('button', name='Collapse', exact=True)).to_have_count(0)
        expect(panel.get_by_role('button', name='Expand', exact=True)).to_have_count(0)
        details = panel.locator('details')
        summary = details.locator('summary')
        summary.click()
        expect(details).to_have_attribute('open', '')
        expect(details.locator('p')).to_be_visible()
        summary.click()
        expect(details).not_to_have_attribute('open', '')
        page.get_by_role('button', name='Navigate', exact=True).focus()
        expect(page.get_by_role('button', name='Navigate', exact=True)).to_be_focused()
        close.click()
        show = page.get_by_role('button', name='Expand operation progress')
        expect(panel.locator('.hosted-activity-content')).to_be_hidden()
        show.click()
        expect(panel).not_to_have_attribute('aria-modal', 'true')
        expect(panel.locator('.hosted-activity-content')).to_be_visible()
        expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('viewport', [(1440, 850), (390, 850), (667, 320)])
@pytest.mark.parametrize('terminal', ['completed', 'failed'])
def test_inline_icons_real_css_long_messages_and_terminal(ui, theme, viewport, terminal, tmp_path):
    page, state = ui
    page.set_viewport_size({'width': viewport[0], 'height': viewport[1]})
    # Match the hosted style policy: only the actual same-origin stylesheet,
    # with no inline geometry overrides or injected replacement styles.
    page.evaluate('''theme => {
        document.documentElement.dataset.theme = theme;
        const policy = document.createElement('meta');
        policy.httpEquiv = 'Content-Security-Policy';
        policy.content = "style-src 'self'";
        document.head.append(policy);
    }''', theme)
    state['hold'] = '/api/hosted/operations/'
    page.get_by_role('button', name='Navigate', exact=True).focus()
    page.evaluate('''() => {
        window.windowDone = false;
        orionHostedIntake.submit('/api/generic-action')
            .catch(() => {}).finally(() => { window.windowDone = true; });
    }''')
    panel = page.locator('#orion-hosted-intake-activity')
    control = panel.get_by_role('button', name='Minimize operation progress', exact=True)
    expect(page.get_by_role('button', name='Navigate', exact=True)).to_be_focused()
    expect(control).to_have_text('−')
    expect(control.locator('span')).to_have_attribute('aria-hidden', 'true')
    expect(panel.get_by_role('button', name=re.compile('Collapse|Expand'))).to_have_count(0)

    def assert_borderless_focus(button):
        # Inspect the actual cascade, not stylesheet text. Keyboard modality
        # also proves the deliberate focus ring survives the border removal.
        page.mouse.move(0, 0)
        page.keyboard.press('Tab')
        button.focus()
        # Hover exit animates the background. Await actual CSS convergence,
        # rather than sampling a transient color immediately after mouse move.
        expect(button).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
        styles = button.evaluate('''element => {
            const style = getComputedStyle(element);
            return {borders: ['Top', 'Right', 'Bottom', 'Left'].map(side => style[`border${side}Width`]),
                shadow: style.boxShadow, background: style.backgroundColor,
                outline: style.outlineWidth, outlineStyle: style.outlineStyle,
                outlineOffset: style.outlineOffset, focusVisible: element.matches(':focus-visible')};
        }''')
        assert styles['borders'] == ['0px'] * 4, styles
        assert styles['shadow'] == 'none', styles
        assert styles['background'] == 'rgba(0, 0, 0, 0)', styles
        assert styles['focusVisible'] and styles['outline'] == '2px', styles
        assert styles['outlineStyle'] == 'solid' and styles['outlineOffset'] == '2px', styles

    assert_borderless_focus(control)
    measure = '''element => {
        const box = element.getBoundingClientRect();
        const header = element.querySelector('.hosted-activity-header').getBoundingClientRect();
        const button = element.querySelector('.hosted-activity-controls button:last-child').getBoundingClientRect();
        const content = element.querySelector('.hosted-activity-content');
        return {width: box.width, height: box.height, left: box.left, right: box.right,
            top: box.top, bottom: box.bottom, buttonWidth: button.width, buttonHeight: button.height,
            rightInset: box.right - button.right, topInset: button.top - box.top,
            aligned: Math.abs(button.right - header.right) < 1 && Math.abs(button.top - header.top) < 1,
            overflow: content.scrollHeight > content.clientHeight,
            innerScroll: getComputedStyle(content).overflowY,
            horizontalOverflow: element.scrollWidth > element.clientWidth};
    }'''
    initial = panel.evaluate(measure)
    page.screenshot(path=str(tmp_path / 'active-minus.png'))
    assert initial['buttonWidth'] == initial['buttonHeight'] == 36
    assert initial['aligned'] and not initial['horizontalOverflow']
    assert initial['left'] >= 0 and initial['right'] <= viewport[0]
    assert initial['top'] >= 0  # Normal document flow may extend below a short viewport.
    assert 18 <= initial['rightInset'] <= 25 and 18 <= initial['topInset'] <= 25
    page.wait_for_function('window.pollDelays.length === 1')
    page.wait_for_function('window.windowDone === false')
    page.wait_for_timeout(30)
    route = state['held'].pop()
    identifier = route.request.url.rsplit('/', 1)[-1]
    route.fulfill(json={'operation_id': identifier, 'status': 'running',
                        'progress': {'stage': 'artifact_files', 'completed': 1, 'total': 2,
                                     'unit': 'very-long-unbroken-package-name' * 100}})
    expect(panel).to_contain_text('very-long-unbroken-package-name')
    long = panel.evaluate(measure)
    assert long['width'] == initial['width']
    assert not long['horizontalOverflow']
    assert long['innerScroll'] == 'auto'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    control.click()
    show = page.get_by_role('button', name='Expand operation progress')
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    show.press('Enter')
    expect(panel.locator('.hosted-activity-content')).to_be_visible()
    page.wait_for_function('window.pollDelays.length === 2')
    page.wait_for_timeout(30)
    route = state['held'].pop()
    route.fulfill(json={'operation_id': identifier, 'status': terminal,
                        'result': {'status': 'completed', 'items': [{'name': 'Long saved package ' * 200}]},
                        'recovery': 'Inspect the recorded operation. ' * 200})
    page.wait_for_function('window.windowDone')
    close = panel.get_by_role('button', name='Close operation notification', exact=True)
    expect(close).to_have_text('×')
    expect(close).to_have_attribute('title', 'Close activity details only. This does not cancel the operation.')
    assert_borderless_focus(close)
    end = panel.evaluate(measure)
    page.screenshot(path=str(tmp_path / 'terminal-x.png'))
    assert end['width'] == initial['width']
    assert end['buttonWidth'] == end['buttonHeight'] == 36
    assert end['aligned'] and not end['horizontalOverflow']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    print(f'VISUAL {theme} {viewport[0]}x{viewport[1]} {terminal}: '
          f"card={initial['width']}x{initial['height']} control=36x36 "
          f"insets={initial['rightInset']},{initial['topInset']} stable; screenshots={tmp_path}")
    close.scroll_into_view_if_needed()
    expect(close).to_be_in_viewport()
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    close.click()
    expect(panel).to_be_hidden()
    assert len(posts(state)) == 1


@pytest.mark.parametrize('method', ['submit'])
def test_activity_labels_follow_submission_queue_and_reported_phase_counts(ui, method):
    page, state = ui
    state['hold'] = '/api/hosted/operations'
    page.evaluate('''method => {
        window.phaseDone = false;
        orionHostedIntake[method](method === 'read' ? '/api/overview' : '/api/generic-action')
            .then(() => { window.phaseDone = true; });
    }''', method)
    panel = page.locator('#orion-hosted-intake-activity')
    message = panel.locator('#hosted-activity-message')
    processing = panel.locator('.hosted-activity-content > div').last
    bar = processing.locator('progress')
    expect(message).to_contain_text('Waiting.')
    expect(message).to_contain_text('submitting')
    expect(message).not_to_contain_text('Running.')
    expect(processing).to_have_text('Hosted processing: current phase (count not reported)')
    page.wait_for_timeout(30)
    route = state['held'].pop()
    identifier = route.request.post_data_json['id']
    route.fulfill(json={'operation_id': identifier, 'status': 'queued'})
    expect(message).to_have_text('Waiting. Queued for workspace processing.')
    expect(bar).not_to_have_attribute('value', re.compile('.+'))

    phases = [
        ({'stage': 'artifact_files', 'completed': 0, 'total': 16, 'unit': 'files'},
         'Verifying committed package files · 0 of 16 files (current phase only)', '0'),
        ({'stage': 'artifact_files', 'completed': 7, 'total': 16, 'unit': 'files'},
         'Verifying committed package files · 7 of 16 files (current phase only)', '7'),
        ({'stage': 'metadata_restore'},
         'Restoring committed package metadata (current phase only; count not reported)', None),
        ({'stage': 'artifact_files', 'completed': -1, 'total': 16, 'unit': 'files'},
         'Verifying committed package files (current phase only; count not reported)', None),
        ({'stage': 'artifact_files', 'completed': 17, 'total': 16, 'unit': 'files'},
         'Verifying committed package files (current phase only; count not reported)', None),
    ]
    for index, (progress, caption, value) in enumerate(phases):
        page.wait_for_function('count => window.pollDelays.length === count', arg=index + 1)
        page.wait_for_timeout(30)
        state['held'].pop().fulfill(json={
            'operation_id': identifier, 'status': 'running', 'progress': progress})
        expect(message).to_contain_text('Running.')
        expect(processing).to_have_text(caption)
        expect(bar).to_have_attribute('aria-label', caption)
        if value is None:
            expect(bar).not_to_have_attribute('value', re.compile('.+'))
        else:
            expect(bar).to_have_attribute('value', value)
            expect(bar).to_have_attribute('max', '16')
        expect(processing).not_to_contain_text('progress unknown')
    page.wait_for_function('window.pollDelays.length === 6')
    page.wait_for_timeout(30)
    state['held'].pop().fulfill(json={
        'operation_id': identifier, 'status': 'completed', 'result': {}})
    page.wait_for_function('window.phaseDone')
    expect(processing).to_be_hidden()
    expect(panel.get_by_role('button', name='Close operation notification')).to_have_text('×')
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    assert len([r for r in state['requests'] if r['method'] == 'POST']) == 1


def test_nonblocking_observation_and_completed_read_close_survive_reload(ui):
    page, state = ui
    state["hold"] = "/api/hosted/operations/"
    page.get_by_role('button', name='Navigate', exact=True).focus()
    page.evaluate("() => { window.readStatuses = []; orionHostedIntake.read('/api/agents/runs', {}, data => readStatuses.push(data)).then(() => { window.readDone = true; }); }")
    page.wait_for_function('window.readStatuses.length > 0')
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page.locator("#orion-hosted-intake-activity")).to_have_count(0)
    expect(page.locator(".app-shell")).not_to_have_attribute("inert", "")
    expect(page.get_by_role('button', name='Navigate', exact=True)).to_be_focused()
    page.wait_for_function("window.pollDelays.length === 1")
    page.wait_for_timeout(30)
    route = state["held"].pop()
    identifier = route.request.url.rsplit("/", 1)[-1]
    route.fulfill(json={"operation_id": identifier, "status": "completed", "result": {}})
    page.wait_for_function('window.readDone')
    before = page.evaluate("key => sessionStorage.getItem(key)", STORE)
    count = len(state["requests"])
    page.reload(); page.add_script_tag(content=SCRIPT)
    expect(page.locator('#orion-hosted-intake-activity')).to_have_count(0)
    assert len(state["requests"]) == count
    assert page.evaluate("key => sessionStorage.getItem(key)", STORE) == before


@pytest.mark.parametrize('read_status', ['running', 'completed', 'failed'])
def test_read_updates_never_own_or_expand_active_inline_mutation(ui, read_status):
    page, state = ui
    state['hold'] = '/api/hosted/operations/'
    page.evaluate("""() => {
      orionHostedIntake.submit('/api/generic-action').catch(() => {});
      orionHostedIntake.read('/api/overview').catch(error => { window.readFailure = error.message; });
      orionHostedIntake.read('/api/objects').catch(() => {});
    }""")
    page.wait_for_function('window.pollDelays.length === 3')
    page.wait_for_timeout(30)
    panel = page.locator('#orion-hosted-intake-activity')
    owner = page.evaluate("document.querySelector('#orion-hosted-intake-activity details p').textContent")
    message = page.locator('#hosted-activity-message').inner_text()
    page.get_by_role('button', name='Minimize operation progress').click()
    reads = [route for route in state['held'] if state['operations'][route.request.url.rsplit('/', 1)[-1]]['method'] == 'GET']
    assert len(reads) == 2
    for route in reads:
        state['held'].remove(route)
        identifier = route.request.url.rsplit('/', 1)[-1]
        route.fulfill(json={'operation_id': identifier, 'status': read_status, 'result': {}, 'recovery': 'Inspect recorded failure'})
    if read_status == 'running':
        page.wait_for_function('window.pollDelays.length === 5')
    elif read_status == 'failed':
        page.wait_for_function('window.readFailure')
        assert 'no request was replayed' in page.evaluate('window.readFailure')
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_hidden()
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    expect(page.locator('#hosted-activity-message')).to_have_text(message)
    expect(panel.locator('details p')).to_have_text(owner)
    expect(page.get_by_role('button', name='Expand operation progress')).to_be_focused()
    assert len(posts(state)) == 3


@pytest.mark.parametrize('dismiss', ['click', 'Escape'])
@pytest.mark.parametrize('terminal', ['completed', 'failed', 'authentication_required', 'interrupted'])
def test_close_details_keeps_compact_real_progress_polling_and_explicit_restore(ui, dismiss, terminal):
    page, state = ui
    state['hold'] = '/api/hosted/operations/'
    page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}).finally(() => window.compactDone = true); }")
    panel = page.locator('#orion-hosted-intake-activity')
    compact = page.locator('#orion-hosted-intake-summary')
    close = panel.get_by_role('button', name='Close operation notification', exact=True)
    expect(close).to_have_text('×')
    expect(panel.get_by_role('button', name='Minimize operation progress')).to_have_text('−')
    page.wait_for_function('window.pollDelays.length === 1')
    page.wait_for_timeout(30)
    before = len(state['requests'])
    stored = page.evaluate('key => sessionStorage.getItem(key)', STORE)
    close.focus()
    if dismiss == 'Escape':
        close.press('Escape')
    else:
        close.click()
    expect(panel).to_be_hidden()
    expect(compact).to_be_visible()
    restore = compact.get_by_role('button', name='Show activity details')
    expect(restore).to_be_focused()
    assert compact.evaluate("element => element.closest('main') !== null && getComputedStyle(element).position === 'relative'")
    expect(compact.locator('.hosted-intake-spinner')).to_be_visible()
    assert compact.locator('.hosted-intake-spinner').evaluate('element => element.getAnimations().length') > 0
    assert len(state['requests']) == before
    page.wait_for_timeout(1050)
    expect(compact).to_contain_text('1s elapsed')
    route = state['held'].pop()
    identifier = route.request.url.rsplit('/', 1)[-1]
    route.fulfill(json={'operation_id': identifier, 'status': 'running',
                        'progress': {'stage': 'artifact_files', 'completed': 7, 'total': 16, 'unit': 'files'}})
    expect(compact).to_contain_text('7 of 16 files')
    expect(compact).to_contain_text('Verifying committed package files')
    expect(panel).to_be_hidden()
    expect(restore).to_be_focused()
    page.wait_for_function('window.pollDelays.length === 2')
    page.wait_for_timeout(30)
    state['held'].pop().fulfill(json={'operation_id': identifier, 'status': terminal,
                                    'result': {'status': 'completed'}, 'recovery': 'Inspect recorded state.'})
    page.wait_for_function('window.compactDone')
    expect(panel).to_be_hidden()
    expect(compact).to_be_visible()
    expect(compact).to_have_attribute('aria-busy', 'false')
    expect(compact.locator('.hosted-intake-spinner')).to_be_hidden()
    expect(compact).to_contain_text('completed' if terminal == 'completed' else terminal)
    expect(compact).not_to_contain_text('cancelled')
    assert page.evaluate('key => sessionStorage.getItem(key)', STORE) == stored
    assert len(posts(state)) == 1
    before_restore = len(state['requests'])
    restore.press('Enter')
    expect(panel).to_be_visible()
    expect(panel.locator('.hosted-activity-content')).to_be_visible()
    expect(close).to_be_focused()
    expect(compact).to_be_hidden()
    expect(panel).not_to_have_attribute('aria-modal', 'true')
    assert len(state['requests']) == before_restore
    close.press('Tab')
    expect(close).not_to_be_focused()
    close.click()
    expect(compact).to_be_visible()
    page.get_by_role('button', name='Navigate', exact=True).focus()
    page.keyboard.press('Escape')
    expect(page.get_by_role('button', name='Navigate', exact=True)).to_be_focused()


def test_legacy_cleanup_releases_only_recorded_roots_and_preserves_package_inert(ui):
    page, state = ui
    # Test-only visibility of the retained rollback function, no production hook.
    page.reload()
    page.add_script_tag(content=SCRIPT.replace('window.orionHostedIntake = Object.freeze',
                                             'window.testLegacy = legacyBlockingState; window.orionHostedIntake = Object.freeze'))
    state['hold'] = '/api/hosted/operations/'
    page.evaluate("() => { orionHostedIntake.submit('/api/generic-action').catch(() => {}); }")
    panel = page.locator('#orion-hosted-intake-activity')
    expect(panel).to_be_visible()
    page.evaluate('''() => {
        const section = document.createElement('section'); section.id = 'package-loading';
        section.inert = true; section.setAttribute('aria-busy', 'true'); document.querySelector('main').append(section);
        document.querySelector('.topbar').inert = true;
        testLegacy();
    }''')
    expect(page.locator('.app-shell')).to_have_attribute('inert', '')
    panel.locator('button[aria-controls="hosted-activity-content"]').evaluate('button => button.click()')
    expect(page.locator('.app-shell')).not_to_have_attribute('inert', '')
    expect(page.locator('.topbar')).to_have_attribute('inert', '')
    expect(page.locator('#package-loading')).to_have_attribute('inert', '')
    expect(page.locator('.hosted-operation-inert')).to_have_count(0)
    backdrop = page.locator('#orion-hosted-operation-backdrop')
    expect(backdrop).to_be_hidden()
    expect(backdrop).not_to_have_attribute('aria-modal', 'true')
    expect(backdrop).not_to_have_attribute('role', 'dialog')
    expect(panel).not_to_have_attribute('aria-modal', 'true')
    expect(panel).not_to_have_class(re.compile('hosted-operation-modal'))