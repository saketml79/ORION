"""Real loopback HTTP/browser journey with fresh XML and a scripted provider.

All mutable state is pytest temporary state. No real endpoint, hosted app, Job,
catalog mutation, production approval, or semantic parity is exercised here.
"""
import json
import socket
from threading import Event, Thread
from time import monotonic

import httpx
from langchain_core.messages import AIMessage
from playwright.sync_api import expect
import pytest
import uvicorn

from workbench.agents import build_agents
from workbench.app import _create_package_app
from workbench.redesign import Design, build_wil_net_dis_candidate, cited_ids
from workbench.tests.test_hosted import offline  # noqa: F401
from workbench.tests.test_redesign import chat, design_for, tool_call
from workbench.tests.test_redesign_ui import browser  # noqa: F401
from workbench.tests.test_review_actual_workflows import canonical, NAMES, ROOT  # noqa: F401
from workbench.tests.test_runtime_planning import actual_xml  # noqa: F401


class ScriptedSourceProvider:
    """Compiled production tools, deterministic fake model responses only."""

    def __init__(self, index, directory):
        self.index, self.directory = index, directory
        self.calls = []

    def status(self):
        return {"available": True, "authentication": "model_provided",
                "provider": "scripted-offline-test", "model_routes": {}}

    def run(self, model, workflow, scenario, prompt, documents, reviews):
        self.calls.append((workflow, scenario))
        node = self.index.nodes[workflow]
        design = (build_wil_net_dis_candidate(model, workflow, scenario=scenario)
                  if node["name"] == NAMES[0] else design_for(model, workflow))
        refs = sorted(cited_ids(Design.model_validate(design)))
        route = chat([
            AIMessage(content="", tool_calls=[
                {"id": f"lookup-{position}", "name": "lookup_evidence",
                 "args": {"evidence_id": ref}} for position, ref in enumerate(refs)]),
            tool_call("submit_redesign", {"design": design}, "submit-design"),
            AIMessage(content="Scripted source-backed outline, not live inference or parity."),
        ])
        with build_agents(self.directory / workflow, model, workflow, scenario,
                          documents=documents, feedback=reviews, chat_model=route) as bundle:
            bundle.specialists["architecture"].invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                {"configurable": {"thread_id": "local-http-" + workflow},
                 "recursion_limit": 200})
            assert len(bundle.redesigns) == 1
            return {"status": "advisory", "run_id": bundle.run_id,
                    "redesigns": bundle.redesigns, "input_hashes": bundle.input_hashes,
                    "simulation": True}


@pytest.fixture
def isolated_http(canonical, tmp_path, monkeypatch):
    monkeypatch.setattr("langchain.chat_models.init_chat_model",
                        lambda *a, **k: pytest.fail("Live inference forbidden"))
    provider = ScriptedSourceProvider(canonical, tmp_path / "compiled")
    app = _create_package_app(tmp_path / "state", ROOT / "input/LumenExample.XML", provider)
    app.state.service.active = canonical
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="error", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 30
        while not server.started and thread.is_alive() and monotonic() < deadline:
            Event().wait(.02)
        assert server.started, "Isolated loopback server did not start"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60,
                          trust_env=False) as client:
            yield client, provider, app
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        assert not thread.is_alive(), "Owned test server failed to stop"


def terminal(client, path):
    deadline = monotonic() + 120
    while monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in {"completed", "failed"}:
            return record
        Event().wait(.05)
    pytest.fail("Isolated scripted run did not finish; no automatic resubmission")


@pytest.mark.parametrize("name", NAMES)
def test_real_http_source_scripted_agent_saved_design_and_browser_gates(
        isolated_http, browser, name):
    client, provider, app = isolated_http
    index = provider.index
    workflow = next(n["id"] for n in index.kinds["workflow"] if n["name"] == name)
    body = {"workflow_id": workflow, "scenario": "bronze",
            "prompt": "Review this exact source workflow using scripted offline responses.",
            "consent": True}
    headers = {"X-Workbench-Request": "1"}
    denied = client.post("/api/agents/run", headers=headers, json={**body, "consent": False})
    assert denied.status_code == 403 and provider.calls == []
    response = client.post("/api/agents/run", headers=headers, json=body)
    assert response.status_code == 202, response.text
    record = terminal(client, "/api/agents/runs/" + response.json()["id"])
    assert record["status"] == "completed", record
    assert provider.calls == [(workflow, "bronze")]
    assert len(record["redesign_refs"]) == 1
    ref = record["redesign_refs"][0]
    saved = client.get(f"/api/redesigns/{ref['id']}?version={ref['version']}").json()
    assert saved["workflow_id"] == workflow and saved["scenario"] == "bronze"
    assert saved["design"]["tasks"] and not saved["stale"]
    assert not saved["deployment_allowed"] and not saved["outline_drafting_allowed"]
    context = browser.new_context(service_workers="block")
    page = context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(error.message))
    page.on("request", lambda request: requests.append((request.method(), request.url)))
    # Test browser can access only its owned loopback HTTP server.
    origin = str(client.base_url).rstrip("/")
    context.route("**/*", lambda route: route.continue_()
                  if route.request.url.startswith(origin + "/") else route.abort())
    try:
        page.goto(origin + "/#proposals")
        expect(page.locator("#redesign-workflow option[value='" + workflow + "']")).to_have_count(1)
        page.locator("#scenario").select_option("bronze")
        page.locator("#redesign-workflow").select_option(workflow)
        expect(page.locator("#redesign-detail")).to_contain_text(name)
        expect(page.locator("#redesign-detail svg g[role='button']")).to_have_count(len(saved["design"]["tasks"]))
        page.locator("#redesign-open-original").click()
        expect(page.locator("#object-title")).to_have_text(name)
        assert json.loads(page.locator("#object-json").text_content())["node"]["id"] == workflow
        assert not errors
        assert provider.calls == [(workflow, "bronze")], "Browsing must not invoke again"
        assert all(method == "GET" or url.endswith("/api/hosted/heartbeat")
               for method, url in requests)
        assert app.state.service.root != ROOT / "output/workbench"
    finally:
        context.close()