"""Real production viewer, offline HTTP fixtures. No live model or parity claim."""
import json
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui, design_record, HASH, CURRENT
from workbench.draft_artifact_reads import matching_jobs
from workbench.draft_bundle import publish
from workbench.tests.test_native_job_draft_conditions import conditional_fixture


def test_primary_outputs_examples_collapsed_and_navigation_retained(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-heading')).to_have_text('Proposed workflow & code')
    expect(page.locator('#redesign-detail h3').first).to_have_text('Actual submitted outline')
    examples = page.locator('details').filter(has=page.get_by_text('Examples and technical details', exact=True)).first
    assert not examples.get_attribute('open')
    expect(page.locator('#workflow-artifact-submit')).to_be_hidden()
    expect(page.locator('#redesign-detail')).to_contain_text('Saved logical design')
    assert all(request['method'] == 'GET' for request in state['requests'])
    nav = page.locator('.navigation [data-page]').evaluate_all('(nodes) => nodes.map(n => n.dataset.page)')
    assert nav.index('agents') < nav.index('proposals')


def test_cohort_mode_saved_member_cards_and_no_inference(ui):
    page, state, fulfill = ui
    state['records'].append(design_record(workflow_id='wf/two', id='child-design'))
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.locator('#redesign-workflow').select_option('wf/one')
    page.locator('#source-target-scope').select_option('cohort')
    expect(page.locator('#proposal-saved-outputs')).to_contain_text('2 workflow scopes')
    expect(page.locator('#proposal-saved-outputs')).to_contain_text('Actual child scope')
    expect(page.locator('#agent-review-scope')).to_have_value('cohort')
    page.locator('[data-page="agents"]').click()
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#source-target-scope')).to_have_value('cohort')
    page.locator('#scenario').select_option('bronze')
    expect(page.locator('#source-target-scope')).to_have_value('workflow')
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.mark.parametrize('workflow_id', ['wf/one', 'wf/two', 'wf/three'])
@pytest.mark.parametrize('mode', ['workflow', 'cohort'])
@pytest.mark.parametrize('scenario', ['original', 'bronze'])
def test_original_workflow_contract_pins_three_overview_ids_and_preserves_design_scope(ui, workflow_id, mode, scenario):
    """Boundary contract only. Main integration covers the real Explorer handler."""
    page, state, fulfill = ui
    state['records'] = [design_record(workflow_id=workflow_id, scenario=scenario)]
    page.evaluate('''hash => {
        document.getElementById('agent-workflow').append(new Option('Overview workflow three', 'wf/three'));
        document.getElementById('page-proposals').dataset.packageHash = hash;
        window.originalWorkflowCalls = [];
        window.orionOpenWorkflow = pins => { originalWorkflowCalls.push(pins); return true; };
    }''', HASH)
    if workflow_id == 'wf/three':
        page.route('**/api/workflows/wf%2Fthree/migration-context?*', lambda route: fulfill(route, {
            'workflow_id': workflow_id, 'package_hash': HASH,
            'cohort': {'items': [{'workflow_id': workflow_id, 'name': 'Overview workflow three'},
                                 {'workflow_id': 'wf/one', 'name': 'Related workflow one'}],
                       'total': 2, 'has_more': False},
            'invocations': {'items': [], 'has_more': False}}))
    page.locator('[data-page="proposals"]').click()
    page.locator('#scenario').select_option(scenario)
    page.locator('#redesign-workflow').select_option(workflow_id)
    expect(page.locator('#redesign-open-original')).to_be_enabled()
    expect(page.locator('#source-target-scope')).to_be_enabled()
    page.locator('#source-target-scope').select_option(mode)
    expect(page.locator('#agent-review-scope')).to_have_value(mode)
    page.locator('#redesign-open-original').click()
    assert page.evaluate('originalWorkflowCalls') == [{
        'workflowId': workflow_id, 'projectId': '', 'packageHash': HASH, 'scenario': scenario}]
    expect(page.locator('#source-target-scope')).to_have_value(mode)
    assert not any(request['path'].startswith('/api/object/') for request in state['requests'])
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.mark.parametrize('invalid', ['missing_handler', 'pending_package', 'disabled_inventory', 'missing_package', 'unknown_workflow'])
def test_original_workflow_button_rejects_unavailable_or_unscoped_context(ui, invalid):
    page, state, _ = ui
    page.evaluate('''() => {
        window.originalWorkflowCalls = [];
        window.orionOpenWorkflow = pins => { originalWorkflowCalls.push(pins); return true; };
    }''')
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.evaluate('''invalid => {
        const byId = id => document.getElementById(id);
        if (invalid === 'missing_handler') delete window.orionOpenWorkflow;
        if (invalid === 'pending_package') byId('page-proposals').dataset.packageReady = 'false';
        if (invalid === 'disabled_inventory') byId('agent-workflow').disabled = true;
        if (invalid === 'missing_package') {
            byId('project-select').value = 'project-one'; byId('project-package').value = '';
        }
        if (invalid === 'unknown_workflow') {
            byId('redesign-workflow').append(new Option('Not in current overview', 'foreign-workflow'));
            byId('redesign-workflow').value = 'foreign-workflow';
        }
        byId('redesign-workflow').dispatchEvent(new Event('change', {bubbles: true}));
    }''', invalid)
    expect(page.locator('#redesign-open-original')).to_be_disabled()
    assert page.evaluate('originalWorkflowCalls') == []
    assert not any(request['path'].startswith('/api/object/') for request in state['requests'])
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.mark.parametrize('opened', [True, False])
def test_original_workflow_project_package_pins_and_declined_navigation(ui, opened):
    page, state, _ = ui
    page.evaluate('''({hash, opened}) => {
        const byId = id => document.getElementById(id);
        byId('project-select').value = 'project-one';
        const packages = byId('estate-package-select') || byId('project-package');
        packages.append(new Option('Exact current package', hash)); packages.value = hash;
        byId('project-select').dispatchEvent(new Event('change', {bubbles: true}));
        window.originalWorkflowCalls = [];
        window.orionOpenWorkflow = pins => { originalWorkflowCalls.push(pins); return opened; };
    }''', {'hash': HASH, 'opened': opened})
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.locator('#redesign-workflow').select_option('wf/one')
    expect(page.locator('#source-target-scope')).to_be_enabled()
    page.locator('#redesign-open-original').click()
    assert page.evaluate('originalWorkflowCalls') == [{
        'workflowId': 'wf/one', 'projectId': 'project-one', 'packageHash': HASH, 'scenario': 'original'}]
    if not opened:
        expect(page.locator('#redesign-status')).to_contain_text('could not be opened for the current package')
    expect(page.locator('#source-target-scope')).to_have_value('workflow')
    assert not any(request['path'].startswith('/api/object/') for request in state['requests'])
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_primary_scope_copy_reconnect_disclosure_and_related_worker_are_explicit(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    expect(page.locator('#source-target-scope option')).to_have_text([
        'Individual workflow', 'Optimize related workflows together'])
    expect(page.locator('.review-scope-controls')).to_contain_text(
        'Related workflows reviewed as one design scope, not automatic consolidation nor completion order')
    expect(page.locator('#proposal-scope-reconnect')).to_be_hidden()
    page.locator('#source-target-scope').select_option('cohort')
    expect(page.locator('#proposal-scope-reconnect')).to_be_visible()
    expect(page.locator('#cohort-review-id')).to_be_hidden()
    expect(page.locator('#proposal-saved-outputs')).to_contain_text('Related workflows: design & code')
    expect(page.locator('#redesign-generate')).to_be_enabled()
    page.locator('#redesign-generate').click()
    expect(page.locator('#redesign-status')).to_contain_text('Review the selected scope before starting')
    expect(page.locator('#cohort-agent-start-dialog')).to_be_visible()
    expect(page.locator('#cohort-agent-confirm')).to_be_enabled()
    page.locator('#cohort-agent-cancel').click()
    expect(page.locator('#page-proposals')).to_be_visible()
    page.locator('#proposal-scope-reconnect summary').click()
    expect(page.locator('#cohort-review-id')).to_be_visible()
    expect(page.locator('#cohort-review-read')).to_be_visible()
    assert len([request for request in state['requests'] if request['path'].endswith('/target-review')]) == 1
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_primary_cohort_confirm_starts_existing_worker_and_reconnects_exact_receipt(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.locator('#source-target-scope').select_option('cohort')
    expect(page.locator('#redesign-generate')).to_be_enabled()
    page.locator('#redesign-generate').click()
    expect(page.locator('#cohort-agent-start-dialog')).to_be_visible()
    assert all(r['method'] == 'GET' for r in state['requests'])
    page.locator('#cohort-agent-confirm').click()
    expect(page.locator('.review-scope-controls')).to_contain_text('Stopped:')
    expect(page.locator('#cohort-review-id')).to_have_value(state['cohort']['id'])
    designs = [r for r in state['requests'] if r['path'].endswith('/design')]
    assert len(designs) == 1
    assert designs[0]['path'] == f"/api/cohorts/{state['cohort']['id']}/workflows/" + 'f' * 64 + '/design'
    assert designs[0]['body'] == {'operation_id': 'b' * 32, 'consent': True}
    page.locator('.draft-progress-dialog').get_by_role('button', name='Close', exact=True).click()
    expect(page.locator('#redesign-generate')).to_be_enabled()
    page.locator('#redesign-generate').click()
    expect(page.locator('#cohort-agent-start-dialog')).to_be_visible()
    page.locator('#cohort-agent-cancel').click()
    assert len([r for r in state['requests'] if r['path'] == '/api/cohorts' and r['method'] == 'POST']) == 1
    assert any(r['path'] == '/api/cohorts/' + state['cohort']['id'] and r['method'] == 'GET' for r in state['requests'])
    assert len([r for r in state['requests'] if r['path'].endswith('/design')]) == 1


def test_primary_cohort_scope_change_closes_start_dialog_without_mutation(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.locator('#source-target-scope').select_option('cohort')
    expect(page.locator('#redesign-generate')).to_be_enabled()
    page.locator('#redesign-generate').click()
    expect(page.locator('#cohort-agent-start-dialog')).to_be_visible()
    page.locator('#scenario').select_option('bronze', force=True)
    expect(page.locator('#cohort-agent-start-dialog')).to_have_count(0)
    assert all(r['method'] == 'GET' for r in state['requests'])


def test_primary_cohort_hosted_worker_uses_saved_scope_after_start_action(ui):
    page, state, _ = ui
    page.evaluate('''() => {
        window.hostedWorkerSubmissions = [];
        window.orionHostedIntake = {
            probe: async () => true,
            submit: async (path, body, headers) => {
                hostedWorkerSubmissions.push({path, body, headers});
                return {status: 'completed', reason: 'Offline worker receipt'};
            }
        };
    }''')
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.locator('#source-target-scope').select_option('cohort')
    expect(page.locator('#redesign-generate')).to_be_enabled()
    page.locator('#redesign-generate').click()
    expect(page.locator('#cohort-agent-start-dialog')).to_be_visible()
    assert page.evaluate('hostedWorkerSubmissions') == []
    page.locator('#cohort-agent-confirm').click()
    expect(page.locator('.review-scope-controls')).to_contain_text('Server worker: completed')
    assert page.evaluate('hostedWorkerSubmissions') == [{
        'path': '/api/cohorts/' + state['cohort']['id'] + '/design', 'body': {'consent': True}, 'headers': {}}]
    assert not any(r['path'].endswith('/design') for r in state['requests'])
    assert any(r['path'] == '/api/cohorts/' + state['cohort']['id'] and r['method'] == 'GET' for r in state['requests'])


def test_summary_uses_only_loaded_source_counts_and_opens_same_notebook_output(ui):
    page, state, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    expect(page.locator('#proposal-source-comparison')).to_contain_text('Source task count not loaded')
    expect(page.locator('#proposal-source-comparison')).to_contain_text('2 proposed logical tasks')
    assert not any(request['path'].startswith('/api/object/') for request in state['requests'])
    page.get_by_text('Examples and technical details', exact=True).click()
    page.get_by_text('Canonical XML understanding and source evidence', exact=True).click()
    expect(page.locator('#proposal-source-comparison')).to_contain_text('2 captured source task instances')
    page.locator('#redesign-detail svg g[role="button"]').first.click()
    expect(page.get_by_role('region', name='Selected proposed task details')).to_contain_text('Capture source')
    expect(page.get_by_role('region', name='Selected proposed task details')).to_contain_text('No committed notebook is attached')
    page.locator('#redesign-view-notebooks').click()
    expect(page.locator('#saved-design-tab-1')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#spark-draft-find')).to_be_visible()
    page.get_by_text('Canonical XML understanding and source evidence', exact=True).click()
    page.locator('#redesign-workflow').select_option('wf/two')
    expect(page.locator('#proposal-source-comparison')).to_contain_text('Source task count not loaded')
    expect(page.locator('#proposal-source-comparison')).not_to_contain_text('2 captured source task instances')
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_same_context_navigation_retains_output_without_duplicate_design_reads(ui):
    page, state, _ = ui
    page.locator('#page-proposals').evaluate('(page, hash) => { page.dataset.packageHash = hash; }', HASH)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    before = len([r for r in state['requests'] if r['path'] == '/api/redesigns'])
    page.locator('[data-page="agents"]').click()
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    assert len([r for r in state['requests'] if r['path'] == '/api/redesigns']) == before
    page.locator('#redesign-refresh').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    assert len([r for r in state['requests'] if r['path'] == '/api/redesigns']) == before + 1


def test_same_context_refresh_replaces_pending_exact_version_even_if_abort_is_ignored(ui):
    page, state, fulfill = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    state['hold'] = lambda path, query: path.endswith('/real-redesign') and query.get('version') == ['1']
    with page.expect_request('**/api/redesigns/real-redesign?version=1'):
        page.locator('#redesign-version').select_option('["real-redesign",1]')
    expect(page.locator('#redesign-status')).to_contain_text('Reading stored design')
    page.wait_for_function("() => true")
    assert len(state['pending']) == 1
    old = state['pending'].pop()
    # Global refresh remains enabled even while the local exact-version picker
    # is busy. It must create a new presenter generation in the same scope.
    with page.expect_request('**/api/redesigns/real-redesign?version=1'):
        page.locator('#refresh').click()
    page.wait_for_function("() => true")
    expect(page.locator('#redesign-status')).to_contain_text('Reading stored design')
    assert len(state['pending']) == 1
    fresh = state['pending'].pop()
    state['hold'] = None
    current = design_record(1, latest_version=2)
    current['design']['title'] = 'Fresh exact version after refresh'
    fulfill(fresh, current)
    expect(page.locator('#redesign-detail h3').first).to_have_text('Fresh exact version after refresh')
    stale = design_record(1, latest_version=2)
    stale['design']['title'] = 'Late superseded response'
    fulfill(old, stale)
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('#redesign-detail h3').first).to_have_text('Fresh exact version after refresh')
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.mark.parametrize('pending_read', ['list', 'detail'])
def test_standalone_hash_change_invalidates_pending_reads_and_saved_selection(ui, pending_read):
    page, state, fulfill = ui
    page.locator('#page-proposals').evaluate('(page, hash) => { page.dataset.packageHash = hash; }', HASH)
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    old_records = list(state['records'])
    if pending_read == 'list':
        state['hold'] = lambda path, query: path == '/api/redesigns'
        page.locator('#redesign-refresh').click()
        expect(page.locator('#redesign-status')).to_contain_text('Reading actual')
    else:
        state['hold'] = lambda path, query: path.endswith('/real-redesign') and query.get('version') == ['1']
        page.locator('#redesign-version').select_option('["real-redesign",1]')
        expect(page.locator('#redesign-status')).to_contain_text('Reading stored design')
    page.locator('[data-page="agents"]').click()
    assert state['pending']
    before = len(state['requests'])
    page.locator('#page-proposals').evaluate('(page, hash) => { page.dataset.packageHash = hash; }', 'c' * 64)
    expect(page.locator('#redesign-version option')).to_have_count(1)
    expect(page.locator('#redesign-detail')).to_be_empty()
    assert len(state['requests']) == before
    state['hold'] = None
    for route in state['pending']:
        fulfill(route, {'items': old_records, 'total': 2} if pending_read == 'list' else old_records[1])
    state['pending'].clear()
    expect(page.locator('#redesign-detail')).to_be_empty()
    state['records'] = [design_record(package_hash='c' * 64)]
    state['records'][0]['design']['title'] = 'New standalone package design'
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_have_text('New standalone package design')
    assert all('x-workbench-project' not in request['headers'] and 'x-workbench-package' not in request['headers']
               for request in state['requests'])


@pytest.mark.parametrize('failed_read', ['list', 'detail'])
@pytest.mark.parametrize('status_code', [402, 409, 429, 503])
def test_failed_explicit_refresh_retains_only_actual_confirmed_graph_without_authority(ui, failed_read, status_code):
    page, state, fulfill = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    page.evaluate("() => {window.confirmedGraph = document.querySelector('#saved-design-panel-0 svg');}")
    state['hold'] = lambda path, query: (path == '/api/redesigns' if failed_read == 'list'
        else path.endswith('/real-redesign') and query.get('version') == ['2'])
    pattern = '**/api/redesigns?*' if failed_read == 'list' else '**/api/redesigns/real-redesign?version=2'
    with page.expect_request(pattern):
        page.locator('#redesign-refresh').click()
    expect(page.locator('#redesign-detail')).to_have_attribute('data-refresh-unconfirmed', 'true')
    assert page.evaluate("confirmedGraph === document.querySelector('#saved-design-panel-0 svg')")
    expect(page.locator('#redesign-review-form')).to_have_count(0)
    expect(page.locator('#spark-draft-submit')).to_have_count(0)
    expect(page.locator('#redesign-view-notebooks')).to_be_disabled()
    assert page.locator('#saved-design-panel-0').get_attribute('aria-labelledby') is None
    assert len(state['pending']) == 1
    state['hold'] = None
    fulfill(state['pending'].pop(), {'detail': 'Actual refresh failure'}, status_code)
    expect(page.locator('#redesign-status')).to_contain_text(f'HTTP {status_code}')
    expect(page.locator('#redesign-detail')).to_contain_text('Refresh is unconfirmed')
    assert page.evaluate("confirmedGraph === document.querySelector('#saved-design-panel-0 svg')")
    page.locator('#redesign-refresh').click()
    expect(page.locator('#redesign-detail h3').first).to_have_text('Actual submitted outline')
    assert not page.evaluate("confirmedGraph === document.querySelector('#saved-design-panel-0 svg')")
    expect(page.locator('#redesign-consent')).to_have_count(0)
    assert page.locator('#redesign-detail').get_attribute('data-refresh-unconfirmed') is None
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.fixture
def saved_code(ui):
    page, state, fulfill = ui
    identifier = 'e' * 32
    for row in state['records']:
        row['id'] = 'd' * 64
    record = state['records'][0]
    notebook = json.dumps({'cells': [
        {'cell_type': 'markdown', 'metadata': {'id': 'existing-markdown', 'language': 'markdown'}, 'source': ['# Real saved draft']},
        {'cell_type': 'code', 'metadata': {'id': 'existing-code', 'language': 'python'}, 'source': ['print("saved exact")\r\n'],
         'outputs': [], 'execution_count': None}], 'nbformat': 4, 'nbformat_minor': 5,
        'metadata': {'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'},
                     'custom': {'preserve': ['metadata', 1, True]}}})
    artifacts = {'notebooks/load.ipynb': notebook, 'modules/shared.py': '# exact source\r\n',
                 'sql/load.sql': 'SELECT 1;\n', 'tests/test_load.py': 'def test_load():\n    assert True\n'}
    manifest = {'schema_version': 1, 'run_id': 'f' * 32, 'package_hash': HASH,
                'design_id': record['id'], 'design_version': 2, 'input_version': CURRENT,
                'artifact_hashes': {path: sha256(content.encode()).hexdigest() for path, content in artifacts.items()},
                'tasks': [{'task_id': 'ingest', 'implementation_kind': 'notebook_task', 'status': 'drafted',
                           'artifact_paths': list(artifacts), 'evidence_ids': ['original/task'], 'blockers': []}],
                'human_review_items': ['Runtime parity unverified'], 'execution_allowed': False,
                'deployment_allowed': False, 'workflow_readiness': 'drafted_pending_human_review'}
    job = {'id': identifier, 'kind': 'spark_draft', 'design_id': record['id'], 'design_version': 2,
           'package_hash': HASH, 'workflow_id': 'wf/one', 'scenario': 'original', 'input_version': CURRENT,
           'status': 'completed', 'output': {'run_id': 'f' * 32},
           'manifest_sha256': sha256(json.dumps(manifest).encode()).hexdigest()}
    def detail(route):
        query = parse_qs(urlsplit(route.request.url).query)
        fulfill(route, {**record, 'latest_version': 2, **({'draft_jobs': [job]} if 'draft_jobs' in query else {})})
    page.route('**/api/redesigns/' + record['id'] + '*', detail)
    page.route('**/api/jobs/' + identifier, lambda route: fulfill(route, job))
    page.route('**/api/jobs/' + identifier + '/spark-drafts', lambda route: fulfill(route, {
        'job_id': identifier, 'manifest': manifest, 'artifacts': artifacts}))
    # This isolated writer receipt has no published native bundles. Preserve
    # strict rejection of every other unregistered route in the shared fixture.
    page.route('**/api/jobs/' + identifier + '/native-bundles', lambda route: fulfill(route, {
        'job_id': identifier, 'bundles': []}) if route.request.method == 'GET' else route.fallback())
    page.route('**/api/jobs/' + identifier + '/completeness', lambda route: fulfill(route, {'status': 'blocked', 'blockers': ['Runtime parity unverified']}))
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_be_visible()
    expect(page.locator('#saved-design-tab-1')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#spark-draft-output')).to_contain_text('Runtime parity unverified')
    expect(page.locator('#native-job-task-view .workflow-native-card')).to_have_count(2)
    return page, state, fulfill, job, manifest, artifacts


def test_saved_code_receipt_opens_valid_writer_paths_and_preserves_notebook_metadata(saved_code):
    page, state, _, _, _, artifacts = saved_code
    expect(page.locator('#proposal-saved-outputs')).to_contain_text('Design v2: saved code receipts completed')
    for path in artifacts:
        expect(page.get_by_role('button', name='Download ' + path, exact=True)).to_be_visible()
    expect(page.locator('#spark-draft-output')).to_contain_text('Notebook cell 1 (markdown)')
    expect(page.locator('#spark-draft-output')).not_to_contain_text('existing-code')
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download notebooks/load.ipynb', exact=True).click()
    assert downloaded.value.path().read_bytes() == artifacts['notebooks/load.ipynb'].encode()
    page.get_by_role('button', name='SQL & configs', exact=True).click()
    expect(page.get_by_role('button', name='Download sql/load.sql', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Download modules/shared.py', exact=True)).to_be_hidden()
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_complete_returned_manifest_display_and_serialized_download_match_object(saved_code):
    page, _, _, _, manifest, _ = saved_code
    details = page.locator('#spark-draft-output details').filter(
        has=page.get_by_text('Complete returned manifest JSON', exact=True)).first
    assert details.get_attribute('open') is None
    details.locator('summary').first.click()
    assert json.loads(details.locator('pre').text_content()) == manifest
    expect(details).to_contain_text('not the original manifest file bytes')
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download manifest JSON (serialized)', exact=True).click()
    assert json.loads(downloaded.value.path().read_bytes()) == manifest


def test_saved_native_bundle_yaml_uses_real_builder_and_native_endpoint(saved_code, tmp_path):
    page, _, fulfill, job, _, _ = saved_code
    design, manifest, artifacts = conditional_fixture()
    manifest['run_id'] = job['output']['run_id']
    root = tmp_path / 'generated-drafts' / job['package_hash'] / job['design_id'] / str(job['design_version']) / manifest['run_id']
    root.mkdir(parents=True)
    bundle = publish(SimpleNamespace(root=tmp_path), job, {'design': design},
                     {'manifest': manifest, 'artifacts': artifacts}, '/Workspace/Users/test')
    path = '/api/jobs/' + job['id'] + '/native-bundles/' + bundle['bundle_id']
    requests = []
    def native_read(route):
        requests.append((urlsplit(route.request.url).path, route.request.method))
        fulfill(route, bundle)
    page.route('**' + path, native_read)
    page.locator('#native-bundle-id').fill(bundle['bundle_id'])
    page.locator('#native-bundle-read').click()
    native = page.locator('#native-job-task-view')
    expect(native.locator('.workflow-native-card')).to_have_count(4)
    expect(native).to_be_visible()
    for task in ('count_rows', 'above', 'below'):
        native.locator(f'.workflow-native-card[data-task-key="{task}"]').click()
        inspector = native.locator('.workflow-task-inspector')
        expect(inspector).to_be_visible()
        notebook = json.loads(bundle['artifacts'][f'notebooks/{task}.ipynb'])
        for cell in notebook['cells']:
            expect(inspector).to_contain_text(''.join(cell['source']))
        expect(inspector).not_to_contain_text('No notebook JSON supplied')
    page.get_by_role('button', name='View notebooks & downloads', exact=True).click()
    expect(page.get_by_role('button', name='Download migration-job.yaml', exact=True)).to_be_visible()
    page.get_by_role('button', name='YAML', exact=True).last.click()
    expect(page.get_by_role('button', name='Download migration-job.yaml', exact=True)).to_be_visible()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download migration-job.yaml', exact=True).click()
    assert downloaded.value.path().read_bytes() == bundle['artifacts']['migration-job.yaml'].encode()
    assert requests == [(path, 'GET')]


@pytest.mark.parametrize('changed', ['package_hash', 'workflow_id', 'scenario', 'input_version', 'design_id', 'design_version'])
def test_saved_code_inventory_rejects_cross_context_receipts(changed):
    record = design_record()
    job = {key: record[key] for key in ('package_hash', 'workflow_id', 'scenario', 'input_version')}
    job.update(id='a' * 32, kind='spark_draft', status='completed', design_id=record['id'], design_version=record['version'])
    assert len(matching_jobs(record, [job])) == 1
    job[changed] = 999 if changed == 'design_version' else 'different'
    assert matching_jobs(record, [job]) == []


def test_saved_code_inventory_does_not_invent_missing_provenance_or_read_heavy_fields():
    record = design_record(input_version=None)

    class MetadataOnlyJob(dict):
        def get(self, key, default=None):
            assert key != 'output', 'Inventory must not inspect heavy writer outputs'
            return super().get(key, default)

        def __getitem__(self, key):
            assert key != 'output', 'Inventory must not inspect heavy writer outputs'
            return super().__getitem__(key)

    job = MetadataOnlyJob({key: record[key] for key in ('package_hash', 'workflow_id', 'scenario')})
    job.update(id='a' * 32, kind='spark_draft', status='completed', design_id=record['id'],
               design_version=record['version'], output=object())
    projected, = matching_jobs(record, [job])
    assert 'input_version' not in projected and 'output' not in projected
    job['input_version'] = None
    projected, = matching_jobs(record, [job])
    assert projected['input_version'] is None
    assert set(projected) == {'id', 'status', 'created_at', 'design_id', 'design_version',
                              'kind', 'package_hash', 'workflow_id', 'scenario', 'input_version'}