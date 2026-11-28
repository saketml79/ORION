"""Focused offline release gate. No model calls, hosted writes or test_*.py."""
import argparse
import ast
import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys
from threading import Lock
from types import ModuleType, SimpleNamespace

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def section(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def function(source, name):
    match = re.search(r"^  (?:async )?function " + name + r"\(", source, re.M)
    if match is None:
        raise AssertionError(f"Missing production function: {name}")
    end = re.search(r"^  (?:async )?function |^  (?:const|let) ", source[match.end():], re.M)
    if end is None:
        raise AssertionError(f"Missing function boundary: {name}")
    return source[match.start():match.end() + end.start()]


def check_cancel():
    tree = ast.parse((ROOT / "workbench" / "hosted.py").read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "_early_response")
    module = ModuleType("workbench.external_models")
    module.PREFIX = "/api/external/"
    module.cookie_token = lambda headers: None
    previous = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module

    async def in_thread(call, *args):
        return call(*args)

    namespace = {"re": re, "monotonic": lambda: 1, "CancelScope": lambda **kw: nullcontext(),
                 "run_in_threadpool": in_thread}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<production-cancel>", "exec"), namespace)

    async def exercise():
        calls = []
        fail = False

        async def app(scope, receive, send):
            calls.append(scope)
            if fail:
                raise RuntimeError("controlled dispatch failure")

        session = SimpleNamespace(app=app, discarded=False, live_scope=("p", "h"), leases=1)
        sessions = SimpleNamespace(lock=Lock(), sessions={"owner": session})

        def release(entry):
            entry.leases -= 1

        sessions.release = release
        runtime = SimpleNamespace(sessions=sessions)
        path = "/api/agents/runs/" + "a" * 32 + "/cancel"

        async def invoke(owner="owner", project="p", package="h", method="POST", route=path):
            return await namespace["_early_response"](runtime, identity=None, owner=owner,
                path=route, scope={"method": method},
                headers={"x-workbench-project": project, "x-workbench-package": package},
                forwarded=None, external_key=None, clean_scope={"checked": True},
                receive=None, secure_send=None)

        assert await invoke() is True
        assert len(calls) == 1 and session.leases == 1
        for args in ({"owner": "other"}, {"project": "other"}, {"package": "other"},
                     {"method": "GET"}, {"route": path.replace("a" * 32, "invalid")}):
            assert await invoke(**args) is False
        assert len(calls) == 1 and session.leases == 1
        session.discarded = True
        assert await invoke() is False
        session.discarded = False
        fail = True
        try:
            await invoke()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Dispatch error was swallowed")
        assert session.leases == 1

    try:
        asyncio.run(exercise())
    finally:
        if previous is None:
            sys.modules.pop(module.__name__)
        else:
            sys.modules[module.__name__] = previous
    print("PASS: same-owner/live-scope cancellation dispatch; wrong scope, method, stale session and error cleanup")


HELPERS = """
const $ = id => document.getElementById(id);
const el = (tag, cls = '', value = '') => {
  const n=document.createElement(tag); n.className=cls; n.textContent=value; return n;
};
const clear = n => { n.replaceChildren(); return n; };
const array = v => Array.isArray(v) ? v : [];
const text = v => String(v ?? '');
const count = v => v.toLocaleString('en-US');
const human = v => String(v ?? '').replaceAll('_',' ');
const icon = v => { const n=el('span','ui-icon',v); n.dataset.icon=v; return n; };
const paragraph = (n,v,cls) => n.append(el('p',cls,v));
const message = (n,v) => { clear(n); paragraph(n,v); };
const formStatus = (n,v,cls) => { const p=n.querySelector('.form-status');p.textContent=v;p.className='form-status '+(cls||''); };
const title = (n,v) => { n.title=v; return n; };
const historyTime = v => v || 'Not recorded';
const action = (v,cb,cls) => { const n=el('button',cls,v); n.onclick=cb; return n; };
const state = {epoch:1,runTicket:1,runId:'a'.repeat(32),page:'agents',projectId:'p',memberHash:'h',
  ready:true,hosted:true,inventoryPreload:'ready',readinessPreload:'ready',runtimeWarmStatus:'ready',
  runtimeWarmKey:'1:p:h',overview:{package:{sha256:'h'}},projectJobs:[],requests:0,
  pagePreload:'failed',pagePreloadError:'documents',agentRunning:true,agentSubmitting:false};
const selectedCompletedPackage = () => ({name:'fixture'});
let initializingNavigation=false,legacyJobStatus='',hostedActivity=null;
const requestActivity=new Map(),recordedRunStates=new Map(),agentEvaluationSnapshots=new Map();
const activeRunStatuses=new Set(['queued','running','cancelling']);
const activeWriter = r => activeRunStatuses.has(r?.candidate_writer?.status);
const activeRun = r => activeRunStatuses.has(r?.status) || activeWriter(r);
const runState = (r,epoch) => ({...r,epoch});
const terminal=new Set(['completed','failed','cancelled']);
const savedAgentObservation = () => null;
const recordAgentEvaluationSnapshot = () => {};
const workspaceRestoreDiagnostic = () => null;
const operationProgressText = () => '';
const loadingDiagnosticLabel = () => '';
const updateAgentTurnClocks = () => {};
const navigate = () => {};
const updateControls = () => updateActivity();
const loadRuns = () => {};
const overviewProgress=$('overview-progress'),overviewProgressHeading=$('overview-progress-heading');
const overviewProgressDescription=$('overview-progress-description'),overviewProgressBar=$('overview-progress-bar');
const inventoryRetry=$('inventory-retry'),readinessRetry=$('readiness-retry'),warmRetry=$('warm-retry');
const loadingDiagnosticText=$('loading-diagnostic-text');
let liveRunObservation=null,liveRunClock=null,latestRunObservation=null,hostedRunTiming=null;
let finalRunStatus=null,renderedRunId=null,renderedRunSignature=null,displayedRunDocsConsentId=null;
let response=null,rejectStatus=false,reads=0,timers=0,clears=0;
window.setTimeout = cb => { window.reconnect=cb; return 1; };
window.setInterval = () => { timers++; return 1; };
window.clearInterval = () => { clears++; };
const api = async path => {
  reads++;
  if(rejectStatus) throw new Error('HTTP 503 fixture');
  return {...response};
};
"""


MARKUP = """<!doctype html><html><body><main>
<form id="agent-form"><select id="agent-workflow"><option value="w">Fixture workflow</option></select><p class="form-status"></p></form>
<div id="agent-arena"></div><div id="runs-list" data-offset="0"></div><div id="run-detail"></div>
<div id="overview-progress"><h3 id="overview-progress-heading"></h3>
<p id="overview-progress-description"></p><progress id="overview-progress-bar"></progress></div>
<button id="inventory-retry"></button><button id="readiness-retry"></button><button id="warm-retry"></button>
<span id="loading-diagnostic-text"></span><div id="activity-status"></div><div id="operation-banner"></div>
</main></body></html>"""


def check_browser(baseline=None):
    source = (ROOT / "workbench" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "workbench" / "static" / "app.css").read_text(encoding="utf-8")
    arena = (ROOT / "workbench" / "static" / "agent-arena.js").read_text(encoding="utf-8")
    code = HELPERS
    for name in ("semanticStatus", "latestPackageRead", "updateActivity", "navigationPrerequisite",
                 "baseAssessmentReady", "hostedWarmReady", "agentObservationReceipt", "agentProgressTime", "renderLiveRunStatus",
                 "updateLiveRunClock", "formatClockDuration", "setClockTime", "runHistoryRow",
                 "showObservedActiveRun"):
        code += function(source, name)
    prefix = section(source, "  async function pollRun(", "      void loadModelListPrices();")
    timing = section(source, "      if (completed) {\n        clearInterval(liveRunClock);",
                     "      const phaseLabels =")
    catch = section(source, "    } catch (error) {\n      if (epoch !== state.epoch || ticket !== state.runTicket) return;\n      const previous =",
                    "\n  const settingNames =")
    code += prefix + timing + "\n      updateControls(); updateLiveRunClock(); return run;\n" + catch
    code += """
window.probe = {state,recordedRunStates,
  nav: navigationPrerequisite, activity:updateActivity,
  poll:()=>pollRun(1), progress:agentProgressTime,
  tick:updateLiveRunClock, history:showObservedActiveRun,
  setResponse:r=>{response=r;rejectStatus=false;},fail:()=>rejectStatus=true,
  observations:()=>({timers,clears,reads,liveRunObservation}),
  age:s=>{liveRunObservation.progressAt=Date.now()-s*1000;updateLiveRunClock();}};
"""
    run = {"id": "a" * 32, "status": "running", "workflow_id": "w", "package_hash": "h",
           "started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), "live_activity": {
               "public_run_id": "a" * 32, "status": "verified", "counts": {},
               "active_calls": [{"role": "semantics", "kind": "model", "endpoint": "fixture"}],
               "skill_stats": {"per_role": [{"role": "semantics", "loaded": [
                   {"path": "/library/semantics/SKILL.md"}]}]},
               "last_event": {"created_at": "2020-01-01T00:00:00Z", "event": "model_started"}}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.abort())
        page.set_content(MARKUP)
        page.add_style_tag(content=css)
        page.evaluate("() => {\n" + code + "\n}")
        page.evaluate((ROOT / "workbench" / "static" / "redaction.js").read_text(encoding="utf-8"))
        page.evaluate(arena)
        if baseline:
            before = (baseline / "workbench" / "static" / "app.js").read_text(encoding="utf-8")
            page.evaluate("""() => { const state=probe.state;
                          const selectedCompletedPackage=()=>({name:'fixture'});
                          let initializingNavigation=false,legacyJobStatus='';
                          """ + function(before, "navigationPrerequisite")
                          + function(before, "baseAssessmentReady") + function(before, "hostedWarmReady")
                          + "if(!navigationPrerequisite('agents')) throw new Error('Baseline did not reproduce lock'); }")
            print("REPRODUCED: baseline optional-document failure blocks ready-package Agent navigation")
        assert page.evaluate("probe.nav('agents')") == ""
        for preload in ("not_started", "loading", "failed"):
            assert page.evaluate("v=>{probe.state.pagePreload=v;return probe.nav('explorer')}", preload) == ""
        for field, value in (("inventoryPreload", "loading"), ("readinessPreload", "failed"),
                             ("runtimeWarmStatus", "unavailable"), ("memberHash", "different")):
            assert page.evaluate("""([k,v])=>{const old=probe.state[k];probe.state[k]=v;
              const blocked=!!probe.nav('agents');probe.state[k]=old;return blocked;}""", [field, value])
        page.evaluate("probe.state.pagePreload='failed';probe.activity()")
        assert page.locator("#overview-progress").is_visible()
        assert page.locator("#overview-progress-bar").is_hidden()
        assert "documents" in page.locator("#overview-progress-description").inner_text()
        page.evaluate("probe.state.pagePreload='loading';probe.activity()")
        assert page.locator("#overview-progress-bar").is_visible()
        page.evaluate("probe.state.pagePreload='ready';probe.state.pagePreloadError=''")
        page.evaluate("r=>probe.setResponse(r)", run)
        result = page.evaluate("probe.poll()")
        assert result is not None, page.locator(".agent-status-read-error").inner_text()
        assert page.locator(".agent-status-progress-age").inner_text() != "Not available"
        assert "successful status check does not prove execution progress" in page.locator(".agent-status-note").inner_text()
        assert page.locator(".agent-arena").get_attribute("data-active") == "false"
        assert page.locator('[data-in-flight="true"]').count() == 0
        assert page.locator(".arena-current-skill[data-active=true]").count() == 0
        assert "No recent" in page.locator("#activity-status").inner_text()
        page.evaluate("probe.poll()")
        assert "No agent progress" in page.locator(".agent-status-note").inner_text()
        assert page.locator(".agent-status-age").inner_text() == "00:00:00"
        page.evaluate("""r=>{r.live_activity.last_event.created_at=new Date().toISOString();
          probe.setResponse(r)}""", run)
        page.evaluate("probe.poll()")
        assert page.locator(".agent-arena").get_attribute("data-active") == "true"
        assert page.locator('[data-in-flight="true"]').count() == 1
        page.evaluate("probe.age(299)")
        assert page.locator(".agent-arena").get_attribute("data-active") == "true"
        page.evaluate("probe.age(300)")
        assert page.locator(".agent-arena").get_attribute("data-active") == "false"
        page.evaluate("probe.fail();probe.poll()")
        assert "Current status unconfirmed" in page.locator(".agent-status-note").inner_text()
        assert page.evaluate("probe.observations().liveRunObservation") is None
        assert page.locator(".agent-arena").get_attribute("data-active") == "false"
        page.evaluate("""r=>{r.live_activity.last_event.created_at=new Date().toISOString();
          probe.setResponse(r)}""", run)
        page.evaluate("probe.poll()")
        assert page.locator(".agent-arena").get_attribute("data-active") == "true"
        before_timers = page.evaluate("probe.observations().timers")
        before_clears = page.evaluate("probe.observations().clears")
        page.evaluate("probe.fail();probe.poll()")
        assert page.evaluate("probe.observations().timers") == before_timers
        assert page.evaluate("probe.observations().clears") > before_clears
        page.evaluate("""r=>{r.live_activity.last_event.created_at=new Date().toISOString();
          probe.setResponse(r)}""", run)
        page.evaluate("probe.poll()")
        page.evaluate("""r=>{probe.state.agentSubmitting=true;probe.setResponse({...r,status:'completed'});
          }""", run)
        page.evaluate("probe.poll()")
        assert "Saving workspace state" in page.locator(".agent-status-note").inner_text()
        assert "not observed" not in page.locator(".arena-workspace-elapsed").inner_text()
        page.evaluate("probe.fail();probe.poll()")
        assert "Status unconfirmed" in page.locator(".arena-badge").inner_text()
        assert "not observed" not in page.locator(".arena-workspace-elapsed").inner_text()
        page.evaluate("probe.state.agentSubmitting=false")
        for status in ("failed", "completed"):
            page.evaluate("([r,s])=>probe.setResponse({...r,status:s,finished_at:new Date().toISOString()})", [run, status])
            page.evaluate("probe.poll()")
            assert page.evaluate("probe.observations().liveRunObservation") is None
            assert page.locator(".agent-arena").get_attribute("data-active") == "false"
        assert page.locator("#runs-list > button").count() == 1
        page.evaluate("probe.history({id:'b'.repeat(32),status:'running'})")
        assert page.locator("#runs-list > button").count() == 2
        page.evaluate("document.getElementById('runs-list').dataset.offset='50';probe.history({id:'c'.repeat(32),status:'running'})")
        assert page.locator("#runs-list > button").count() == 2
        page.evaluate("""()=>{const list=document.getElementById('runs-list');list.dataset.offset='0';
          probe.history({id:'c'.repeat(32),status:'running'});
          probe.history({id:'d'.repeat(32),status:'running'});}""")
        geometry = page.locator("#runs-list").evaluate("""n=>{
          const list=n.getBoundingClientRect(),rows=[...n.children].map(x=>x.getBoundingClientRect());
          return {height:n.clientHeight,heights:rows.map(r=>r.height),
            thirdBottom:rows[2].bottom-list.top,fourthTop:rows[3].top-list.top};}""")
        assert geometry["height"] == 376 and geometry["heights"] == [120] * 4, geometry
        assert geometry["thirdBottom"] <= 376 and geometry["fourthTop"] >= 376, geometry
        page.evaluate("""()=>{document.getElementById('agent-form').insertAdjacentHTML('beforeend',
          '<section id="agent-scope-host"><p>Target review</p><div class="detail-list" id="scope-output"></div></section>'
          +'<p id="agent-workflow-session-status">Workflow status</p><div class="detail-list" id="unrelated-list"></div>');}""")
        assert page.locator("#scope-output").evaluate("n=>getComputedStyle(n).display") == "none"
        assert page.locator("#unrelated-list").evaluate("n=>getComputedStyle(n).display") != "none"
        page.evaluate("document.getElementById('scope-output').append(document.createElement('span'))")
        assert page.locator("#scope-output").evaluate("n=>getComputedStyle(n).display") != "none"
        for theme in ("light", "dark"):
            for width in (1440, 800, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.evaluate("v=>document.documentElement.dataset.theme=v", theme)
                assert page.locator(".agent-live-status").evaluate(
                    "n=>n.scrollWidth<=n.clientWidth+1"), (theme, width)
        assert not errors, errors
        browser.close()
    print("PASS: optional preload navigation, safety gates, error/loading distinction, unchanged-progress heartbeat")
    print("PASS: status failure/recovery, threshold crossing, terminal events, frozen timers and activity consumers")
    print("PASS: observed history insertion/deduplication/pagination and six live-status layouts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    if args.source_root:
        ROOT = args.source_root.resolve()
    check_cancel()
    check_browser(args.baseline)
