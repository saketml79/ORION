"""Offline Chromium integration, real markup and script, deliberately fake API.

No model, workbench server, Databricks, credentials, or deployment is contacted.
"""
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"
HASH = "a" * 64
CURRENT = "b" * 64


def design_record(version=2, scenario="original", **updates):
    value = {
        "id": "real-redesign", "package_hash": HASH, "workflow_id": "wf/one",
        "scenario": scenario, "version": version, "review_version": 3,
        "input_version": CURRENT, "current_input_version": CURRENT,
        "stale": False, "status": "draft", "outline_drafting_allowed": False,
        "advisory_only": True, "approved": False, "generation_allowed": False,
        "deployment_allowed": False, "author_role": "architecture",
        "source": {"record_id": "actual-run", "kind": "agent"},
        "retrieved_evidence_ids": ["original/task", "edge/evidence"], "reviews": [],
        "design": {
            "schema_version": 1, "title": "Actual submitted outline",
            "rationale": "Separate recovery boundaries based on original/task",
            "tasks": [
                {"id": "ingest", "label": "Ingest actual", "implementation_kind": "notebook_task",
                 "purpose": "Capture source", "evidence_ids": ["original/task"]},
                {"id": "check", "label": "Check actual", "implementation_kind": "condition_task",
                 "purpose": "Check boundary", "evidence_ids": ["edge/evidence"]}],
            "edges": [{"source": "ingest", "target": "check", "condition": "captured condition",
                       "outcome": None, "run_if": "NONE_FAILED", "evidence_ids": ["edge/evidence"]}],
            "alternatives": [{"label": "Retain", "rationale": "Less change", "tradeoffs": ["More duplication"], "evidence_ids": ["original/task"]}],
            "coverage": [{"original_task_id": "original/task", "proposed_task_ids": ["ingest", "check"],
                          "disposition": "mapped", "rationale": "Preserve behavior", "evidence_ids": ["original/task"]}],
            "parameters": [{"name": "batch", "purpose": "Recovery key", "required": True, "value_type": "string"}],
            "reuse_groups": [{"id": "shared-check", "proposed_task_ids": ["check"], "rationale": "Shared validation", "evidence_ids": ["edge/evidence"]}],
            "proposed_constraints": ["No external side effects"], "blockers": [],
            "evidence_ids": ["original/task", "edge/evidence"],
        },
    }
    value.update(updates)
    return value


def sql_draft_response(workflow_id="wf/two"):
    return {
        "workflow_id": workflow_id, "deployment_allowed": False,
        "notice": "SQL syntax drafts only. Missing references block affected tasks, not the entire design.",
        "manifest": {
            "workflow_id": workflow_id, "status": "isolated_draft_incomplete",
            "release_ready": False, "execution_permitted": False,
            "sql_inventory": [
                {"id": "sql-query", "role": "source_query", "task_ids": ["session-ok"],
                 "status": "draft_syntax_only", "blocker_codes": [], "emitted_artifacts": ["query.py", "query.ipynb"]},
                {"id": "sql-post", "role": "target_post_sql", "task_ids": ["session-missing"],
                 "status": "blocked", "blocker_codes": ["UNRESOLVED_REFERENCE"], "emitted_artifacts": []}],
            "tasks": [{"node_id": "session-ok", "status": "partial_sql_draft"},
                      {"node_id": "session-missing", "status": "blocked"},
                      {"node_id": "email", "status": "unsupported"}],
            "blockers": [{"node_id": "session-missing", "code": "UNRESOLVED_REFERENCE"}]},
        "artifacts": {"query.py": "# Actual compiler draft\nsql = 'SELECT 1'\n",
                      "query.ipynb": json.dumps({"cells": [{"cell_type": "code", "metadata": {"language": "python"}, "source": ["sql = 'SELECT 1'"]}]}),
                      "graph.json": '{"nodes": ["session-ok", "session-missing", "email"]}'},
    }


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def ui(browser):
    context = browser.new_context(offline=True, service_workers="block", accept_downloads=True,
                                  viewport={"width": 1440, "height": 1100})
    # Deliberately defeat cancellation, proving response guards, not just aborts.
    context.add_init_script("window.AbortController = class extends AbortController { abort() {} };")
    page = context.new_page()
    state = {"records": [design_record(), design_record(1)], "requests": [],
             "unexpected": [], "errors": [], "pending": [], "hold": None, "review_status": 201,
             "sql_response": sql_draft_response(), "sql_status": 200, "selection": None, "cohort": None}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>", "", markup, flags=re.S)
    markup = markup.replace("</head>", '<link rel="stylesheet" href="/static/app.css">'
                            '<script src="/static/redaction.js" defer></script>'
                            '<script src="/harness.js" defer></script>'
                            '<script src="/static/redesign-review.js" defer></script></head>')
    harness = """
      const byId = id => document.getElementById(id);
      byId('agent-workflow').append(new Option('Overview workflow one', 'wf/one'), new Option('Overview workflow two', 'wf/two'));
    byId('agent-workflow').value = 'wf/one';
    const workflowRoller = document.createElement('div'); workflowRoller.id = 'agent-workflow-roller';
    byId('agent-workflow').parentElement.append(workflowRoller);
      byId('project-select').append(new Option('Project one', 'project-one'), new Option('Project two', 'project-two'));
      byId('project-package').append(new Option('Package one', 'package-one'), new Option('Package two', 'package-two'));
      byId('project-package').disabled = false;
      for (const nav of document.querySelectorAll('[data-page]')) nav.addEventListener('click', () => {
        for (const page of document.querySelectorAll('.page')) page.hidden = page.id !== `page-${nav.dataset.page}`;
      });
    """
    # Execute the production shared package renderer, not a second UI implementation.
    app_source = (STATIC / "app.js").read_text(encoding="utf-8")
    renderer = app_source[app_source.index("  function renderPackageMigrationContext("):
                          app_source.index("  async function loadPackageMigrationContext(")]
    harness += """
            const clear=p=>p.replaceChildren(), array=v=>Array.isArray(v)?v:[];
            const el=(tag,cls='',text='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=text;return e;};
            const heading=(p,t,tag)=>p.append(el(tag,'',t));
            const paragraph=(p,t,c)=>p.append(el('p',c,t));
            const human=v=>String(v).replaceAll('_',' ');
            const count=v=>typeof v==='number'?v.toLocaleString('en-US'):'Not supplied';
            const list=(p,values,empty='')=>{const ul=el('ul');for(const v of values)ul.append(el('li','',v));p.append(ul);if(!values.length&&empty)paragraph(p,empty);};
            const action=(t,fn)=>{const b=el('button','',t);b.onclick=fn;return b;};
            const jsonDetails=(p,t,v)=>{const d=el('details');d.append(el('summary','',t),el('pre','',JSON.stringify(v)));p.append(d);};
    """ + renderer

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def api(route):
        request = route.request
        path, query = urlsplit(request.url).path, parse_qs(urlsplit(request.url).query)
        state["requests"].append({"path": path, "query": query, "method": request.method,
                                  "headers": request.all_headers(), "body": request.post_data_json if request.post_data else None})
        if state["hold"] and state["hold"](path, query):
            state["pending"].append(route)
            return
        rows = [r for r in state["records"] if r["scenario"] == query.get("scenario", [r["scenario"]])[0]
                and r["workflow_id"] == query.get("workflow_id", [r["workflow_id"]])[0]]
        if path in {"/api/object/wf%2Ftwo", "/api/object/wf%2Fone"}:
            workflow_id = "wf/two" if "two" in path else "wf/one"
            fulfill(route, {"node": {"name": "Captured workflow"}, "behavior_explanation": {
            "basis": "canonical_xml_evidence", "semantic_parity": "unverified",
            "coverage": {"task_count": 2, "control_link_count": 1},
            "sections": [{"title": "Exact XML control flow", "items": ["Start → Session on success"]}],
            "evidence_ids": [workflow_id]}})
        elif path in {"/api/workflows/wf%2Ftwo/migration-context", "/api/workflows/wf%2Fone/migration-context"}:
            empty_page = {"items": [], "total": 0, "offset": 0, "limit": 40, "has_more": False, "truncated": False}
            workflow_id = 'wf/two' if 'two' in path else 'wf/one'
            fulfill(route, {"workflow_id": workflow_id, "package_hash": HASH,
                "selected": {"direct_sources": 0, "direct_targets": 0},
                "convert_together": "Review the entire invocation cohort, not one unrelated workflow.",
                "keep_external_explicitly": "Confirm missing child bindings and async completion contracts.",
                "cohort": {**empty_page, "total": 2, "direct_source_count_sum": 4, "direct_target_count_sum": 8,
                    "items": [{"workflow_id": workflow_id, "name": "Selected root", "scope": {"repository": "R", "folder": "F"},
                               "direct_sources": 0, "direct_targets": 0, "unresolved_references": 0},
                              {"workflow_id": 'wf/one' if workflow_id == 'wf/two' else 'wf/two', "name": "Actual child scope", "scope": {"repository": "R", "folder": "F"},
                               "direct_sources": 4, "direct_targets": 8, "unresolved_references": 0}]},
                "referenced_children": {**empty_page, "total": 1, "source_count_sum": 4, "target_count_sum": 8},
                "invocations": empty_page, "outside_references": empty_page, "cycles": empty_page,
                "potential_data_dependencies": empty_page,
                "blockers": ["Output parity not established"], "human_questions": ["Who owns the child?"]})
        elif path == "/api/workflows/wf%2Fone/target-review":
            scope = query.get("scope", ["workflow"])[0]
            fulfill(route, {"format": "source-target-review-v1", "workflow_id": "wf/one", "scope": scope,
                "input_version": CURRENT, "workflow_count": 2 if scope == "cohort" else 1,
                "assignment_persisted": False, "execution_allowed": False, "deployment_allowed": False,
                "targets": {"total": 1, "has_more": False, "items": [{"target_id": "d" * 64, "name": "Actual target",
                    "exported_identity": {"DATABASETYPE": "Oracle", "NAME": "Actual target"},
                    "source_scope": {"repository": "R", "folder": "F"}, "role": "business_target_candidate",
                    "workflow_ids": ["wf/one"], "file_disposition_required": False,
                    "columns": [{"NAME": "ID", "DATATYPE": "number"}]}]}})
        elif path == '/api/target-selections/wf%2Fone/' + 'd' * 64 and request.method == 'GET':
            selection = state['selection']
            fulfill(route, {'version': selection['version'] if selection else 0, 'selection': selection})
        elif path == '/api/target-selections/' + 'd' * 64 and request.method == 'POST':
            body = request.post_data_json
            state['selection'] = {**body, 'version': body['expected_version'] + 1,
                                  'qualified_name': '`cat`.`sch`.`tbl`', 'stale': False}
            fulfill(route, {'version': state['selection']['version'], 'selection': state['selection']})
        elif path == '/api/cohorts' and request.method == 'POST':
            body = request.post_data_json
            state['cohort'] = {'id': body['request_id'], 'stale': False,
                'request': body, 'package_hash': 'a' * 64,
                'workflows': [{'name': 'Actual child scope', 'workflow_id': 'f' * 64,
                               'operation_id': 'b' * 32, 'status': 'awaiting_individual_review',
                               'reason': 'A durable independent model worker is not configured.'}]}
            fulfill(route, state['cohort'], 201)
        elif path.endswith('/design') and request.method == 'POST':
            fulfill(route, {'id': 'e' * 32, 'status': 'queued'}, 202)
        elif path == '/api/agents/runs/' + 'e' * 32 and request.method == 'GET':
            fulfill(route, {'id': 'e' * 32, 'workflow_id': 'f' * 64, 'cohort_id': state['cohort']['id'],
                            'package_hash': 'a' * 64, 'scenario': state['cohort']['request']['scenario'],
                            'status': 'failed'})
        elif path.startswith('/api/cohorts/') and request.method == 'GET':
            fulfill(route, state['cohort'])
        elif path == '/api/redesigns/real-redesign/spark-drafts' and request.method == 'POST':
            record = max(state['records'], key=lambda r: r['version'])
            fulfill(route, {'id': 'e' * 32, 'kind': 'spark_draft', 'design_id': record['id'],
                'design_version': record['version'], 'package_hash': record['package_hash'],
                'workflow_id': record['workflow_id'], 'scenario': record['scenario'],
                'input_version': record['input_version'], 'status': 'queued'}, 202)
        elif path == '/api/proposals/sql-drafts' and request.method == 'POST':
            fulfill(route, state["sql_response"], state["sql_status"])
        elif path == "/api/redesigns":
            offset = int(query.get("offset", [0])[0])
            fulfill(route, {"items": rows[offset:offset + 50], "total": len(rows), "offset": offset,
                            "limit": 50, "truncated": offset + 50 < len(rows)})
        elif path == "/api/redesigns/real-redesign" and request.method == "GET":
            version = int(query.get("version", [max(r["version"] for r in state["records"])])[0])
            value = next(r for r in state["records"] if r["version"] == version)
            if "draft_jobs" in query:
                value = {**value, "draft_jobs": []}
            fulfill(route, value)
        elif path == "/api/redesigns/real-redesign/reviews" and request.method == "POST":
            if state["review_status"] != 201:
                fulfill(route, {"detail": "token=never-echo"}, state["review_status"])
                return
            latest = max(state["records"], key=lambda r: r["version"])
            body = request.post_data_json
            latest["review_version"] += 1
            latest["status"] = body["action"] if body["action"] != "comment" else latest["status"]
            latest["outline_drafting_allowed"] = body["action"] == "approve_for_drafting"
            latest["reviews"].append({"action": body["action"], "comment": body["comment"]})
            fulfill(route, {"event": latest["reviews"][-1], "redesign": latest}, 201)
        else:
            state["unexpected"].append(path)
            route.abort()

    def route_handler(route):
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html", body=markup, headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self'; img-src 'none'"})
        elif path == "/harness.js":
            route.fulfill(content_type="text/javascript", body=harness)
        elif path == "/static/redesign-review.js":
            route.fulfill(content_type="text/javascript", path=str(STATIC / "redesign-review.js"))
        elif path == "/static/redaction.js":
            route.fulfill(content_type="text/javascript", path=str(STATIC / "redaction.js"))
        elif path == "/static/app.css":
            route.fulfill(content_type="text/css", path=str(STATIC / "app.css"))
        elif path.startswith("/api/"):
            api(route)
        else:
            state["unexpected"].append(route.request.url)
            route.abort()

    context.route("**/*", route_handler)
    page.goto("http://redesign.invalid/")
    # These saved records are explicitly original-source fixtures, independent
    # of the production default scenario shown on a new page.
    page.locator('#scenario').select_option('original', force=True)
    yield page, state, fulfill
    assert not state["errors"]
    assert not state["unexpected"], state["unexpected"]
    context.close()


def open_proposals(page, scenario='original'):
    page.locator('#scenario').select_option(scenario, force=True)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#redesign-detail h3").first).to_have_text("Actual submitted outline")
    # Legacy evidence tests explicitly expand the retained optional tools.
    page.get_by_text('Advanced source details and compiler examples', exact=True).click()
    page.get_by_text('Canonical XML understanding and source evidence', exact=True).click()
    page.get_by_text('Advanced: target assignments and invocation cohort review', exact=True).click()
    page.get_by_text('Advanced: captured SQL drafts', exact=True).click()
    page.get_by_text('Example compiler (four reviewed wrappers)', exact=True).click()
    page.get_by_text('Design rationale, coverage, provenance and downloads', exact=True).click()


def test_run_handoff_opens_exact_submitted_bronze_version_not_latest_or_baseline(ui):
    page, state, fulfill = ui
    identifier = 'd' * 64
    one = design_record(1, 'bronze', id=identifier)
    two = design_record(2, 'bronze', id=identifier)
    state['records'] = [two, one]
    def exact_detail(route):
        query = parse_qs(urlsplit(route.request.url).query)
        version = int(query.get('version', ['2'])[0])
        state['requests'].append({'path': '/api/redesigns/' + identifier,
                                  'query': query, 'method': route.request.method})
        fulfill(route, one if version == 1 else two)
    page.route('**/api/redesigns/' + identifier + '*', exact_detail)
    page.locator('[data-page="agents"]').click()
    page.evaluate('''link => document.dispatchEvent(new CustomEvent('orion:review-submitted-design', {detail: link}))''',
        {'id': identifier, 'version': 1, 'workflow_id': 'wf/one', 'scenario': 'bronze',
         'package_hash': HASH, 'record_id': 'actual-run'})
    expect(page.locator('#page-proposals')).to_be_visible()
    expect(page.locator('#scenario')).to_have_value('bronze')
    expect(page.locator('#redesign-workflow')).to_have_value('wf/one')
    expect(page.locator('#redesign-status')).to_contain_text('Stored v1, latest v2')
    assert json.loads(page.locator('#redesign-version').input_value()) == [identifier, 1]
    assert not any(r['method'] != 'GET' for r in state['requests'])
    expect(page.locator('#redesign-consent')).to_have_count(0)


def test_run_handoff_refuses_mismatched_saved_source_without_selecting_latest(ui):
    page, state, fulfill = ui
    identifier = 'd' * 64
    value = design_record(1, 'bronze', id=identifier, source={'record_id': 'different-run', 'kind': 'agent'})
    state['records'] = [value]
    page.route('**/api/redesigns/' + identifier + '*', lambda route: fulfill(route, value))
    page.evaluate('''link => document.dispatchEvent(new CustomEvent('orion:review-submitted-design', {detail: link}))''',
        {'id': identifier, 'version': 1, 'workflow_id': 'wf/one', 'scenario': 'bronze',
         'package_hash': HASH, 'record_id': 'actual-run'})
    expect(page.locator('#redesign-status')).to_contain_text('does not match the selected run submission')
    expect(page.locator('#redesign-review-form')).to_have_count(0)
    assert not any(r['method'] != 'GET' for r in state['requests'])


def prepare_review(page):
    page.locator("#redesign-review-comment").fill("Reviewed evidence and recovery boundaries")


def test_failed_list_refresh_retains_choices_but_clears_review_authority(ui):
        page, state, _ = ui
        open_proposals(page)
        before = page.locator('#redesign-version').inner_html()
        page.route('**/api/redesigns?*', lambda route: route.fulfill(
                status=502, content_type='text/html', body='<html>Gateway failure, not JSON</html>'))
        page.locator('#redesign-refresh').click()
        expect(page.locator('#redesign-status')).to_contain_text('GET /api/redesigns: HTTP 502')
        expect(page.locator('#redesign-status')).to_contain_text('Previously listed choices are unconfirmed')
        assert page.locator('#redesign-version').inner_html() == before
        expect(page.locator('#redesign-review-form')).to_have_count(0)
        assert 'No agent-submitted design' not in page.locator('#redesign-status').inner_text()
        assert all(r['method'] == 'GET' for r in state['requests'])


def test_hosted_redesign_list_and_exact_handoff_use_background_read_helper(ui):
        page, state, _ = ui
        identifier = 'd' * 64
        records = [design_record(2, id=identifier), design_record(1, id=identifier)]
        page.evaluate('''rows => {
            window.savedReadCalls = [];
            expect(page.locator('#redesign-consent')).to_have_count(0)
                probe: async () => true,
                read: async (path, headers, onStatus) => {
                    savedReadCalls.push({path, headers});
                    onStatus({status: 'running'});
                    const url = new URL(path, location.origin);
                    if (url.pathname === '/api/redesigns') return {items: rows.map(r => ({...r, design: {title: r.design.title}})), total: 2};
                    return rows.find(r => r.version === Number(url.searchParams.get('version') || 2));
                }
            };
        }''', records)
        page.evaluate('''link => document.dispatchEvent(new CustomEvent('orion:review-submitted-design', {detail: link}))''',
                {'id': identifier, 'version': 1, 'workflow_id': 'wf/one', 'scenario': 'original',
                 'package_hash': HASH, 'record_id': 'actual-run'})
        expect(page.locator('#redesign-status')).to_contain_text('Stored v1, latest v2')
        calls = page.evaluate('savedReadCalls')
        assert any('/api/redesigns?' in call['path'] and 'workflow_id=wf%2Fone' in call['path'] for call in calls)
        assert any(call['path'] == '/api/redesigns/' + identifier + '?version=1' for call in calls)
        assert any(call['path'] == '/api/redesigns/' + identifier for call in calls)
        assert not any(r['path'].startswith('/api/redesigns') for r in state['requests'])
        expect(page.locator('#redesign-consent')).to_have_count(0)
        before = page.locator('#redesign-version').inner_html()
        page.evaluate("() => { orionHostedIntake.read = async () => { throw new Error('Recorded read operation failed: HTTP 503'); }; }")
        page.locator('#redesign-refresh').click()
        expect(page.locator('#redesign-status')).to_contain_text('Recorded read operation failed: HTTP 503')
        assert page.locator('#redesign-version').inner_html() == before
        expect(page.locator('#redesign-review-form')).to_have_count(0)
        assert not any(r['path'].startswith('/api/redesigns') for r in state['requests'])


def test_target_review_requires_click_and_keeps_mappings_unapproved(ui):
    page, state, _ = ui
    open_proposals(page, scenario='bronze')
    page.locator('#redesign-workflow').select_option('wf/one')
    expect(page.locator('#source-target-read')).to_be_enabled()
    assert not any(r['path'].endswith('/target-review') for r in state['requests'])
    page.locator('#source-target-scope').select_option('cohort')
    assert not any(r['path'].endswith('/target-review') for r in state['requests'])
    page.locator('#source-target-read').click()
    expect(page.locator('#source-target-review')).to_contain_text('Actual target')
    expect(page.locator('#source-target-review')).to_contain_text('unassigned and not approved')
    read = [r for r in state['requests'] if r['path'].endswith('/target-review')]
    assert len(read) == 1 and read[0]['query']['scope'] == ['cohort']
    assert all(r['method'] == 'GET' for r in state['requests'])


def test_target_selection_and_cohort_are_explicit_and_unapproved(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator('#redesign-workflow').select_option('wf/one')
    page.locator('#source-target-scope').select_option('cohort')
    page.locator('#source-target-read').click()
    target = 'd' * 64
    page.locator('#target-load-' + target).click()
    page.locator('#target-catalog-' + target).fill('cat')
    page.locator('#target-schema-' + target).fill('sch')
    page.locator('#target-table-' + target).fill('tbl')
    page.locator('#target-save-' + target).click()
    expect(page.locator('#source-target-review')).to_contain_text('Unapproved; UC metadata and schema are unverified')
    page.locator('#cohort-review-create').click()
    expect(page.locator('.review-scope-controls')).to_contain_text('No model action is started by opening this review')
    assert state['selection']['expected_version'] == 0
    assert state['cohort']['workflows'][0]['status'] == 'awaiting_individual_review'


def test_cohort_design_requires_click_and_stops_on_failed_run(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator('#redesign-workflow').select_option('wf/one')
    page.locator('#source-target-scope').select_option('cohort')
    page.locator('#source-target-read').click()
    expect(page.locator('#cohort-review-create')).to_be_enabled()
    page.locator('#cohort-review-create').click()
    expect(page.locator('#cohort-design-start')).to_be_visible()
    action_posts = lambda: [r for r in state['requests'] if r['method'] == 'POST' and r['path'].endswith('/design')]
    assert not action_posts()
    expect(page.locator('#cohort-design-start')).to_be_enabled()
    page.locator('#cohort-design-start').click()
    expect(page.get_by_role('dialog')).to_contain_text('Sequence stopped')
    assert len(action_posts()) == 1 and action_posts()[0]['body']['consent'] is True
    assert any(r['method'] == 'GET' and r['path'] == '/api/agents/runs/' + 'e' * 32 for r in state['requests'])
    assert not any(r['method'] == 'POST' and r['path'] == '/api/agents/run' for r in state['requests'])
    assert not any(r['path'] == '/api/agents/run' for r in state['requests'])


def test_local_conversion_modal_minimize_does_not_cancel(ui):
    page, state, fulfill = ui
    latest = state['records'][0]
    latest.update(status='approve_for_drafting', stale=False, outline_drafting_allowed=True,
                  reviews=[{'action': 'approve_for_drafting', 'consent': True,
                            'version': latest['version'], 'input_version': latest['input_version']}])
    state['hold'] = lambda path, query: path == '/api/jobs/' + 'e' * 32
    open_proposals(page)
    page.locator('#redesign-workflow').select_option('wf/one')
    page.locator('#saved-design-tab-1').click()
    expect(page.locator('#spark-draft-consent')).to_have_count(0)
    expect(page.locator('#spark-draft-egress-consent')).to_have_count(0)
    page.locator('#spark-draft-submit').click()
    expect(page.locator('.draft-progress-dialog[open]')).to_be_visible()
    page.locator('.draft-progress-dialog button').click()
    expect(page.locator('.draft-progress-restore')).to_be_visible()
    page.locator('.draft-progress-restore').click()
    expect(page.locator('.draft-progress-dialog[open]')).to_be_visible()
    page.wait_for_timeout(100)
    assert len([r for r in state['requests'] if r['path'].endswith('/spark-drafts') and r['method'] == 'POST']) == 1
    record = latest
    for route in state['pending']:
        fulfill(route, {'id': 'e' * 32, 'kind': 'spark_draft', 'design_id': record['id'],
            'design_version': record['version'], 'package_hash': record['package_hash'],
            'workflow_id': record['workflow_id'], 'scenario': record['scenario'],
            'input_version': record['input_version'], 'status': 'failed', 'error': 'Draft blocked'})
    state['pending'].clear()
    expect(page.locator('.draft-progress-dialog')).to_contain_text('failed')
    page.locator('.draft-progress-dialog button').click()


def test_actual_list_version_graph_and_all_design_fields(ui):
    page, state, _ = ui
    open_proposals(page)
    expect(page.locator("#redesign-version option")).to_have_count(3)
    expect(page.get_by_role("img", name="Proposed Lakeflow task grid")).to_have_count(1)
    expect(page.locator("#redesign-detail .workflow-native-card")).to_have_count(2)
    expect(page.locator("#redesign-detail svg > path")).to_have_count(1)
    for text in ["Separate recovery boundaries", "More duplication", "original/task", "batch", "Recovery key",
                 "shared-check", "No external side effects", "captured condition", "NONE_FAILED", "actual-run"]:
        expect(page.locator("#redesign-detail")).to_contain_text(text)
    assert any(r["query"].get("version") == ["2"] for r in state["requests"])
    assert any(r["path"].endswith("real-redesign") and not r["query"] for r in state["requests"])
    assert all(r["method"] == "GET" for r in state["requests"])
    expect(page.locator("#redesign-detail")).to_contain_text("Proposal YAML preview")
    expect(page.locator("#redesign-detail")).to_contain_text('kind: "LakeflowJobProposal"')
    expect(page.locator("#redesign-detail")).to_contain_text("The logical design has no notebook path")
    expect(page.locator("#redesign-detail .proposal-task-table tbody tr")).to_have_count(2)
    proposal_yaml = page.locator("#proposal-yaml-preview pre").text_content()
    parsed_yaml = yaml.safe_load(proposal_yaml)
    assert parsed_yaml is not None, repr(proposal_yaml[:500])
    assert parsed_yaml["kind"] == "LakeflowJobProposal"
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_review_objects_are_readable_cards_not_json_bullets(ui):
    page, _, _ = ui
    open_proposals(page)
    section = page.locator('.proposal-review-section').filter(
        has=page.get_by_role('heading', name='Logical tasks and cited source objects', exact=True))
    expect(section.locator('.proposal-review-card')).to_have_count(2)
    expect(section.locator('h4').first).to_have_text('Ingest actual')
    expect(section).to_contain_text('Databricks task type')
    expect(section).to_contain_text('What this task does')
    expect(section).to_contain_text('Source evidence')
    assert '"evidence_ids":' not in section.inner_text()
    assert '"implementation_kind":' not in section.inner_text()
    assert '.proposal-review-fields' in (STATIC / 'app.css').read_text(encoding='utf-8')
    expect(page.get_by_text('Technical provenance and canonical IDs', exact=True)).to_be_visible()
    assert all('Input version:' not in text for text in page.locator('#redesign-detail > p').all_text_contents())


def test_workflow_selection_shows_deterministic_xml_explanation(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/two")
    expect(page.locator("#workflow-understanding")).to_contain_text("Captured Informatica workflow understanding")
    expect(page.locator("#workflow-understanding")).to_contain_text("Exact XML control flow")
    expect(page.locator("#workflow-understanding")).to_contain_text("Start → Session on success")
    expect(page.locator("#workflow-understanding")).to_contain_text("Not checked in this selection yet")
    assert any(request["path"] == "/api/object/wf%2Ftwo" for request in state["requests"])


def test_observational_workflow_reads_have_no_deadline_or_modal(ui):
        page, state, fulfill = ui
        open_proposals(page)
        page.evaluate("""() => {
            window.requestDeadlines = [];
            const original = window.setTimeout.bind(window);
            window.setTimeout = (fn, delay, ...args) => {
                requestDeadlines.push(delay); return original(fn, delay, ...args);
            };
        }""")
        state['hold'] = lambda path, query: path.startswith('/api/object/')
        page.locator('#redesign-workflow').select_option('wf/two')
        expect(page.locator('#workflow-understanding')).to_contain_text('Loading workflow evidence')
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('[inert]')).to_have_count(0)
        assert 30000 not in page.evaluate('window.requestDeadlines')
        assert 110000 not in page.evaluate('window.requestDeadlines')
        page.wait_for_timeout(30)
        route = state['pending'].pop()
        fulfill(route, {'node': {'name': 'Slow verified workflow'}, 'behavior_explanation': {
                'basis': 'canonical_xml_evidence', 'sections': [], 'coverage': {}, 'evidence_ids': []}})
        expect(page.locator('#workflow-understanding')).to_contain_text('Slow verified workflow')
        assert all(r['method'] == 'GET' for r in state['requests'])


def test_workflow_read_failure_is_cleared_on_tab_exit_and_late_error_ignored(ui):
        page, state, fulfill = ui
        open_proposals(page)
        state['hold'] = lambda path, query: path.startswith('/api/object/')
        page.locator('#redesign-workflow').select_option('wf/one')
        page.wait_for_timeout(30)
        fulfill(state['pending'].pop(), {'detail': 'Unavailable'}, 503)
        expect(page.locator('#workflow-understanding')).to_contain_text('Workflow evidence unavailable')
        page.locator('#redesign-workflow').select_option('wf/two')
        expect(page.locator('#workflow-understanding')).not_to_contain_text('Workflow evidence unavailable')
        page.wait_for_timeout(30)
        page.locator('[data-page="explorer"]').click()
        fulfill(state['pending'].pop(), {'detail': 'Late previous scope failure'}, 503)
        expect(page.locator('#workflow-understanding')).to_contain_text('Previous selection cleared')
        expect(page.locator('#workflow-understanding')).not_to_contain_text('Workflow evidence unavailable')
        expect(page.locator('#workflow-understanding')).not_to_contain_text('aborted without reason')
        state['hold'] = None
        page.locator('[data-page="proposals"]').click()
        expect(page.locator('#workflow-understanding')).to_contain_text('Exact XML control flow')
        assert all(r['method'] == 'GET' for r in state['requests'])


def test_proposals_show_package_contract_and_keep_technical_evidence_collapsed(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/two")
    context = page.locator("#proposal-package-context")
    expect(context).to_contain_text("Convert together")
    expect(context).to_contain_text("Keep external explicitly")
    expect(context).to_contain_text("Actual child scope")
    expect(context).to_contain_text("Package risk not assessed")
    assert '"direct_source_count_sum"' not in context.inner_text()
    assert context.locator("details[open]").count() == 0
    assert not any(r["path"] == "/api/workflows/fixture-metadata" for r in state["requests"])
    assert not any(r["method"] == "POST" for r in state["requests"])


def test_workflow_understanding_checks_real_scoped_fixture_metadata(ui):
    page, state, fulfill = ui
    calls = []
    def metadata(route):
        calls.append(route.request.post_data_json)
        fulfill(route, {"workflow_id": "wf/two", "observed_at": "2026-11-04T00:00:00Z", "sources": [{
            "expected_full_name": "fixture_catalog.fixture_schema.counter", "exists": True, "schema_exists": True,
            "status": "metadata_verified_fixture_only", "columns": [{"name": "COUNTER", "type": "decimal(5,0)"}],
            "column_schema_matches": True, "package_hash_matches": True, "fixture_provenance_matches": True,
            "binding_status": "fixture_only_unapproved", "approved": False}], "targets": [{"binding_status": "unresolved"}]})
    page.route("**/api/workflows/fixture-metadata", metadata)
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/two")
    panel = page.locator("#workflow-understanding")
    panel.get_by_label("Fixture catalog").fill("fixture_catalog")
    panel.get_by_role("button", name="Check existing UC source fixtures").click()
    expect(panel).to_contain_text("fixture_catalog.fixture_schema.counter")
    expect(panel).to_contain_text("metadata_verified_fixture_only")
    expect(panel).to_contain_text("fixture_only_unapproved")
    expect(panel).to_contain_text("decimal(5,0)")
    assert calls == [{"workflow_id": "wf/two", "catalog": "fixture_catalog"}]
    page.locator("#redesign-workflow").select_option("wf/one")
    expect(panel).not_to_contain_text("fixture_catalog.fixture_schema.counter")


@pytest.mark.parametrize("final", ["wf/one", ""])
@pytest.mark.parametrize("late_status", [200, 503])
def test_stale_package_context_success_or_error_cannot_restore_previous_selection(ui, final, late_status):
    page, state, fulfill = ui
    open_proposals(page)
    state["hold"] = lambda path, query: path == "/api/workflows/wf%2Ftwo/migration-context"
    page.locator("#redesign-workflow").select_option("wf/two")
    expect(page.locator("#proposal-package-context")).to_contain_text("Loading package invocation context")
    page.wait_for_function("document.querySelector('#proposal-package-context') !== null")
    # Flush browser/network callbacks without a live service or weakened cancellation guards.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert len(state["pending"]) == 1
    old_route = state["pending"].pop()
    state["hold"] = None
    page.locator("#redesign-workflow").select_option(final)
    if final:
        expect(page.locator("#proposal-package-context")).to_contain_text("Convert together")
    else:
        expect(page.locator("#proposal-package-context")).to_have_count(0)
    before = page.locator("#workflow-understanding").text_content()
    fulfill(old_route, {"detail": "Stale context failure"} if late_status != 200 else {}, late_status)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.locator("#workflow-understanding").text_content() == before
    expect(page.locator("#workflow-understanding")).not_to_contain_text("Package context unavailable")
    expect(page.locator("#redesign-workflow")).to_have_value(final)
    assert all(r["method"] == "GET" for r in state["requests"])


def test_command_only_metadata_has_no_missing_or_unapproved_source_cards(ui):
    page, state, fulfill = ui
    calls = []

    def metadata(route):
        calls.append(route.request.post_data_json)
        fulfill(route, {"workflow_id": "wf/two", "observed_at": "2026-11-04T00:00:00Z",
                        "sources": [], "targets": [], "direct_source_count": 0,
                        "binding_status": "no_direct_sources", "child_metadata_reads": False})

    page.route("**/api/workflows/fixture-metadata", metadata)
    open_proposals(page)
    page.locator("#redesign-workflow").select_option("wf/two")
    panel = page.locator("#workflow-understanding")
    expect(page.locator("#proposal-package-context")).to_contain_text("0 direct sources and 0 direct targets")
    expect(panel).to_contain_text("Exact XML control flow")
    # Opening proposals legitimately reads its initially selected wf/one.
    # Only the metadata action must not add child-scope requests.
    before_metadata = len(state["requests"])
    panel.get_by_role("button", name="Check existing UC source fixtures").click()
    expect(panel).to_contain_text("No direct sources captured")
    expect(panel).to_contain_text("Called child sources are separate scopes")
    fixture_panel = panel.locator(".proposal-review-section").filter(
        has=page.get_by_role("heading", name="Live UC source fixture metadata", exact=True))
    expect(fixture_panel.locator(".proposal-review-card")).to_have_count(0)
    expect(panel).not_to_contain_text("Not approved")
    expect(panel).not_to_contain_text("fixture_only_unapproved")
    expect(panel.locator(".status-amber, [data-assessment-status=amber]")).to_have_count(0)
    assert calls == [{"workflow_id": "wf/two", "catalog": "infotodbxmig_src_dev_ssuman"}]
    assert not any("wf%2Fone" in r["path"] for r in state["requests"][before_metadata:])


def test_empty_state_and_generate_handoff_prepares_selected_workflow_without_invoking(ui):
    page, state, _ = ui
    state["records"] = []
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#redesign-status")).to_contain_text("No saved designs on this page")
    page.locator("#redesign-workflow").select_option("wf/two")
    page.locator("#redesign-generate").click()
    expect(page.locator("#page-agents")).to_be_visible()
    expect(page.locator("#agent-workflow")).to_have_value("wf/two")
    expect(page.locator("#agent-prompt")).to_have_value(re.compile(r"wf/two.*submit_redesign", re.S))
    expect(page.locator("#agent-prompt")).to_have_value(re.compile(r"current workflow-scoped diagnostic"))
    expect(page.locator("#agent-prompt")).to_have_value(re.compile(r"Missing references block only affected tasks, not the whole redesign"))
    expect(page.locator("#agent-prompt")).to_have_value(re.compile(r"selected workflow"))
    expect(page.locator("#agent-consent")).to_have_count(0)
    expect(page.locator("#agent-submit")).to_be_disabled()
    assert all(r["method"] == "GET" for r in state["requests"])
    assert not page.locator("#redesign-detail svg").count()


def test_proposal_provenance_empty_state_and_action_spacing(ui):
    page, state, _ = ui
    state["records"] = []
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-status')).to_contain_text('No saved designs on this page')
    expect(page.locator('#redesign-generate')).to_have_text('Open Agent activity')
    expect(page.locator('#workflow-artifacts')).to_be_hidden()
    expect(page.locator('#proposal-saved-outputs')).to_contain_text('No saved design for this workflow appears on this page')
    gap = page.locator('.review-action-help').evaluate(
        'el => el.getBoundingClientRect().top - el.previousElementSibling.getBoundingClientRect().bottom')
    assert gap >= 12
    page.get_by_text('Advanced source details and compiler examples', exact=True).click()
    page.get_by_text('Example compiler (four reviewed wrappers)', exact=True).click()
    expect(page.locator('#workflow-artifacts')).to_contain_text('not agent-generated')
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_selected_baseline_handoff_uses_canonical_overview_option(ui):
    page, state, _ = ui
    open_proposals(page)
    page.locator("#proposal-detail").evaluate("""host => {
      const row = document.createElement('div'); row.className = 'detail-row';
      const key = document.createElement('span'); key.className = 'detail-key'; key.textContent = 'workflow id';
      const value = document.createElement('pre'); value.className = 'detail-value'; value.textContent = 'wf/two';
      row.append(key, value); host.replaceChildren(row);
    document.dispatchEvent(new CustomEvent('orion:proposal-selected', {detail: {workflowId: 'wf/two'}}));
    }""")
    expect(page.locator("#redesign-workflow")).to_have_value("wf/two")
    page.locator("#redesign-generate").click()
    expect(page.locator("#agent-workflow")).to_have_value("wf/two")
    assert all(r["method"] == "GET" for r in state["requests"])


@pytest.mark.parametrize("action", ["comment", "request_changes", "reject", "approve_for_drafting"])
def test_exact_backend_review_schema_and_outline_only_gate(ui, action):
    page, state, _ = ui
    open_proposals(page)
    target = page.locator(f"#redesign-{action}")
    expect(target).to_be_disabled()
    prepare_review(page)
    expect(target).to_be_enabled()
    target.click()
    expect(page.locator("#redesign-status")).to_contain_text("Review recorded by service")
    posts = [r for r in state["requests"] if r["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["body"] == {"action": action, "comment": "Reviewed evidence and recovery boundaries",
                                "expected_version": 2, "expected_review_version": 3,
                                "expected_input_version": CURRENT, "consent": True}
    assert posts[0]["headers"]["x-workbench-request"] == "1"
    expect(page.locator("#redesign-consent")).to_have_count(0)
    expect(page.locator("#redesign-detail")).to_contain_text("Deployment: not authorized")
    expect(page.locator("#redesign-detail")).to_contain_text("Executable generation: not authorized")


@pytest.mark.parametrize("kind", ["stale", "blockers", "historical"])
def test_stale_blocked_and_historical_approval_is_disabled(ui, kind):
    page, state, _ = ui
    if kind == "stale":
        state["records"][0].update(stale=True, input_version=HASH)
    elif kind == "blockers":
        state["records"][0]["design"]["blockers"] = ["Unknown source contract"]
    open_proposals(page)
    if kind == "historical":
        page.locator("#redesign-version").select_option(json.dumps(["real-redesign", 1], separators=(",", ":")))
        expect(page.locator("#redesign-detail")).to_contain_text("Historical version, read-only")
    prepare_review(page)
    expect(page.locator("#redesign-approve_for_drafting")).to_be_disabled()
    if kind == "historical":
        expect(page.locator("#redesign-review-comment")).to_be_visible()
        expect(page.locator("#redesign-review-comment")).to_be_editable()
        expect(page.locator("button#redesign-comment")).to_be_disabled()
    assert all(r["method"] == "GET" for r in state["requests"])


def test_context_headers_scenario_and_no_package_fallback(ui):
    page, state, _ = ui
    page.locator("#project-select").select_option("project-one", force=True)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#redesign-refresh")).to_be_disabled()
    assert not state["requests"]
    page.locator("#project-package").select_option("package-one", force=True)
    expect(page.locator("#redesign-detail h3").first).to_have_text("Actual submitted outline")
    prepare_review(page)
    page.locator("#scenario").select_option("bronze")
    expect(page.locator("#redesign-status")).to_contain_text("No agent-submitted design")
    expect(page.locator("#redesign-consent")).to_have_count(0)
    for r in state["requests"]:
        assert r["headers"]["x-workbench-project"] == "project-one"
        assert r["headers"]["x-workbench-package"] == "package-one"
    assert state["requests"][-1]["query"]["scenario"] == ["bronze"]


def test_conflict_requires_refresh_without_echo_or_false_success(ui):
    page, state, _ = ui
    state["review_status"] = 409
    open_proposals(page)
    prepare_review(page)
    page.locator("#redesign-reject").click()
    expect(page.locator("#redesign-status")).to_contain_text("Conflict (HTTP 409)")
    expect(page.locator("#redesign-status")).not_to_contain_text("never-echo")
    expect(page.locator("#redesign-consent")).to_have_count(0)
    prepare_review(page)
    expect(page.locator("#redesign-reject")).to_be_disabled()
    page.locator("#redesign-refresh").click()
    expect(page.locator("#redesign-detail h3").first).to_have_text("Actual submitted outline")
    expect(page.locator("#redesign-consent")).to_have_count(0)


def test_late_list_response_cannot_restore_previous_scenario(ui):
    page, state, fulfill = ui
    state["hold"] = lambda path, query: path == "/api/redesigns" and query.get("scenario") == ["original"]
    page.locator('[data-page="proposals"]').click()
    page.wait_for_function("document.getElementById('redesign-status').textContent.includes('Reading actual')")
    # A browser round-trip ensures the held request has been routed.
    page.locator("#scenario").select_option("bronze")
    expect(page.locator("#redesign-status")).to_contain_text("No agent-submitted design")
    assert state["pending"]
    for route in state["pending"]:
        fulfill(route, {"items": state["records"], "total": 2})
    expect(page.locator("#redesign-detail svg")).to_have_count(0)
    expect(page.locator("#redesign-version option")).to_have_count(1)


def test_late_detail_response_cannot_replace_new_selection(ui):
    page, state, fulfill = ui
    open_proposals(page)
    state["hold"] = lambda path, query: path.endswith("real-redesign") and query.get("version") == ["1"]
    page.locator("#redesign-version").select_option('["real-redesign",1]')
    expect(page.locator("#redesign-status")).to_contain_text("Reading stored design")
    page.locator("#redesign-version").select_option('["real-redesign",2]', force=True)
    expect(page.locator("#redesign-detail")).to_contain_text("Version: 2")
    assert state["pending"]
    for route in state["pending"]:
        fulfill(route, state["records"][1])
    expect(page.locator("#redesign-detail")).to_contain_text("Version: 2")
    expect(page.locator("#redesign-detail")).not_to_contain_text("Historical version, read-only")


def test_actual_graph_download_is_not_a_mock_jobs_dag(ui):
    page, state, _ = ui
    open_proposals(page)
    for target, expected in [("#redesign-download", state["records"][0]),
                             ("#redesign-download-graph", {k: state["records"][0]["design"][k] for k in ("tasks", "edges")})]:
        with page.expect_download() as downloaded:
            page.locator(target).click()
        assert json.loads(Path(downloaded.value.path()).read_text(encoding="utf-8")) == expected


def test_pagination_reads_real_versions_without_generation(ui):
    page, state, _ = ui
    state["records"] = [design_record(v) for v in range(55, 0, -1)]
    open_proposals(page)
    expect(page.locator("#redesign-version option")).to_have_count(51)
    expect(page.locator("#redesign-next")).to_be_enabled()
    page.locator("#redesign-next").click()
    expect(page.locator("#redesign-version option")).to_have_count(6)
    expect(page.locator("#redesign-detail")).to_contain_text("Version: 5")
    expect(page.locator("#redesign-detail")).to_contain_text("Historical version, read-only")
    expect(page.locator("#redesign-next")).to_be_disabled()
    assert any(r["query"].get("offset") == ["50"] for r in state["requests"])


def test_saved_design_card_shows_three_latest_versions_and_collapses_older(ui):
    page, state, _ = ui
    state["records"] = [design_record(version) for version in range(7, 0, -1)]
    page.locator('#scenario').select_option('original', force=True)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#redesign-detail h3").first).to_have_text("Actual submitted outline")
    primary = page.locator("#proposal-saved-outputs .saved-design-versions").first
    expect(primary.locator(":scope > li")).to_have_count(3)
    older = page.locator("#proposal-saved-outputs .saved-design-older").first
    expect(older).to_be_visible()
    assert not older.evaluate("node => node.open")
    expect(older.locator("summary")).to_have_text("Show 4 older versions")
    older.locator("summary").click()
    expect(older.locator(".saved-design-versions-older > li")).to_have_count(4)
    expect(older.locator(".saved-design-versions-older")).to_contain_text("Version 4")


def test_bronze_is_separate_and_handoff_preserves_scenario(ui):
    page, state, _ = ui
    state["records"] = [design_record(scenario="bronze")]
    page.locator("#scenario").select_option("bronze", force=True)
    open_proposals(page)
    expect(page.locator("#redesign-detail")).to_contain_text("Scenario: bronze")
    page.locator("#redesign-workflow").select_option("wf/one")
    page.locator("#redesign-generate").click()
    expect(page.locator("#agent-scenario")).to_have_value("bronze")
    expect(page.locator("#agent-prompt")).to_have_value(re.compile("scenario bronze"))
    expect(page.locator("#agent-consent")).to_have_count(0)
    assert all(r["method"] == "GET" for r in state["requests"])


def test_late_review_cannot_restore_previous_project_or_action_state(ui):
    page, state, fulfill = ui
    open_proposals(page)
    state["hold"] = lambda path, query: path.endswith("/reviews")
    prepare_review(page)
    page.locator("#redesign-reject").click()
    expect(page.locator("#redesign-status")).to_contain_text("Recording a version-checked")
    page.locator("#project-select").select_option("project-two", force=True)
    expect(page.locator("#redesign-detail")).to_be_empty()
    assert state["pending"]
    for route in state["pending"]:
        fulfill(route, {"redesign": design_record(status="reject")}, 201)
    expect(page.locator("#redesign-status")).not_to_contain_text("Review recorded by service")
    expect(page.locator("#redesign-consent")).to_have_count(0)
    expect(page.locator("#redesign-detail")).to_be_empty()


def test_current_input_version_is_sent_for_nonapproval_stale_review(ui):
    page, state, _ = ui
    state["records"][0].update(stale=True, input_version=HASH)
    open_proposals(page)
    prepare_review(page)
    page.locator("#redesign-request_changes").click()
    expect(page.locator("#redesign-status")).to_contain_text("Review recorded by service")
    post = next(r for r in state["requests"] if r["method"] == "POST")
    assert post["body"]["expected_input_version"] == CURRENT
    assert post["body"]["expected_input_version"] != HASH


def test_text_only_rendering_credentials_blocked_and_no_persistence(ui):
    page, state, _ = ui
    state["records"][0]["design"]["rationale"] = '<img src=x onerror=alert(1)> token=never-display'
    open_proposals(page)
    expect(page.locator("#redesign-detail")).to_contain_text("<img src=x")
    expect(page.locator("#redesign-detail img")).to_have_count(0)
    expect(page.locator("#redesign-detail")).not_to_contain_text("never-display")
    page.locator("#redesign-download").click()
    expect(page.locator("#redesign-status")).to_contain_text("Download blocked")
    prepare_review(page)
    page.locator("#redesign-review-comment").fill("token=do-not-send")
    page.locator("#redesign-reject").click()
    expect(page.locator("#redesign-status")).to_contain_text("Remove credential-shaped material")
    assert all(r["method"] == "GET" for r in state["requests"])
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_index_is_additive_and_client_has_no_execution_or_private_graph_helper():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.count('src="/static/redesign-review.js"') == 1
    assert html.count('src="/static/conversion-demo.js"') == 1
    script = (STATIC / "redesign-review.js").read_text(encoding="utf-8")
    for forbidden in ["innerHTML", "localStorage", "renderGraph(", "setInterval("]:
        assert forbidden not in script
    assert 'orion-review-scope-v1' in script
    assert 'sessionStorage.setItem(scopeStorageKey(), targetScope.value)' in script
    # GET /api/agents/runs/{id} is observation; it is not POST /api/agents/run.
    assert "request('/api/agents/run'," not in script
    assert 'request("/api/agents/run",' not in script
    assert "expected_input_version: value.current_input_version" in script


def prepare_sql_draft(page):
    page.locator('[data-page="proposals"]').click()
    page.locator("#redesign-workflow").select_option("wf/two")
    expect(page.locator("#redesign-status")).to_contain_text("No saved designs on this page")
    page.get_by_text('Advanced source details and compiler examples', exact=True).click()
    page.get_by_text('Advanced: captured SQL drafts', exact=True).click()


def test_sql_draft_button_exact_payload_headers_and_actual_artifacts(ui):
    page, state, _ = ui
    page.locator("#project-select").select_option("project-one", force=True)
    page.locator("#project-package").select_option("package-one", force=True)
    prepare_sql_draft(page)
    expect(page.locator("#sql-draft-submit")).to_be_enabled()
    assert not any(r["method"] == "POST" for r in state["requests"])
    page.get_by_role("button", name="Draft captured SQL artifacts", exact=True).click()
    expect(page.locator("#sql-draft-status")).to_contain_text("SQL draft response received")
    posts = [r for r in state["requests"] if r["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["path"] == "/api/proposals/sql-drafts"
    assert posts[0]["body"] == {"workflow_id": "wf/two", "consent": True}
    for header, value in {"x-workbench-request": "1", "x-workbench-project": "project-one",
                          "x-workbench-package": "package-one", "content-type": "application/json"}.items():
        assert posts[0]["headers"][header] == value
    output = page.locator("#sql-draft-output")
    for text in ["source_query", "target_post_sql", "draft_syntax_only", "UNRESOLVED_REFERENCE",
                 "session-missing", "partial_sql_draft", "unsupported", "isolated_draft_incomplete",
                 "not a completed workflow", "Non-SQL implementations"]:
        expect(output).to_contain_text(text)
    for name, content in state["sql_response"]["artifacts"].items():
        block = output.locator("details").filter(has=page.get_by_text(name, exact=True))
        expect(block).not_to_have_attribute("open", "")
        block.locator("summary").click()
        assert block.locator("pre").text_content() == content
    expect(page.locator("#sql-draft-consent")).to_have_count(0)
    expect(page.locator("#sql-draft-submit")).to_be_disabled()
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("scope", ["workflow", "scenario", "project", "package", "closed"])
def test_sql_draft_late_response_cannot_restore_stale_scope_or_output(ui, scope):
    page, state, fulfill = ui
    page.locator("#project-select").select_option("project-one", force=True)
    page.locator("#project-package").select_option("package-one", force=True)
    prepare_sql_draft(page)
    state["hold"] = lambda path, query: path == "/api/proposals/sql-drafts"
    page.locator("#sql-draft-submit").click()
    expect(page.locator("#sql-draft-status")).to_contain_text("Drafting captured SQL only")
    expect(page.locator("#sql-draft-submit")).to_be_disabled()
    if scope == "closed":
        page.locator('[data-page="agents"]').click()
    else:
        selector, value = {"workflow": ("#redesign-workflow", "wf/one"),
                           "scenario": ("#scenario", "bronze"),
                           "project": ("#project-select", "project-two"),
                           "package": ("#project-package", "package-two")}[scope]
        page.locator(selector).select_option(value, force=True)
    expect(page.locator("#sql-draft-status")).to_contain_text("previous output cleared")
    assert state["pending"]
    for route in state["pending"]:
        fulfill(route, state["sql_response"])
    # Flush resolved fetch and JSON continuations with a browser round-trip.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("#sql-draft-output")).to_be_empty()
    expect(page.locator("#sql-draft-status")).not_to_contain_text("response received")
    expect(page.locator("#sql-draft-consent")).to_have_count(0)
    assert len([r for r in state["requests"] if r["method"] == "POST"]) == 1


@pytest.mark.parametrize("invalid", ["workflow", "manifest", "deployment", "artifact", "http"])
def test_sql_draft_rejects_mismatched_response_and_requires_new_button_action(ui, invalid):
    page, state, _ = ui
    prepare_sql_draft(page)
    if invalid == "workflow":
        state["sql_response"]["workflow_id"] = "wf/one"
    elif invalid == "manifest":
        state["sql_response"]["manifest"]["workflow_id"] = "wf/one"
    elif invalid == "deployment":
        state["sql_response"]["deployment_allowed"] = True
    elif invalid == "artifact":
        state["sql_response"]["artifacts"]["query.py"] = {"not": "text"}
    else:
        state.update(sql_status=422, sql_response={"detail": "token=never-echo"})
    page.locator("#sql-draft-submit").click()
    expect(page.locator("#sql-draft-status")).to_contain_text("HTTP 422" if invalid == "http" else "mismatch")
    expect(page.locator("#sql-draft-status")).not_to_contain_text("never-echo")
    expect(page.locator("#sql-draft-output")).to_be_empty()
    expect(page.locator("#sql-draft-consent")).to_have_count(0)
    expect(page.locator("#sql-draft-submit")).to_be_disabled()