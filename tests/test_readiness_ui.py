"""Real navigation and readiness assets in offline Chromium."""
import json
import re

import pytest
from playwright.sync_api import expect

from test_navigation_prerequisites import navigation, tab, shown, choose_project
from test_migration_lineage_ui import lineage_ui
from test_project_package_autoload import browser, settle, PROJECT_A, PROJECT_B, PACKAGE_A, STATIC


def payload(package=PACKAGE_A):
    return {'observed_at': '2026-11-06T00:00:00Z', 'scope': {'project_id': PROJECT_A,
        'pins': {'package_hash': package, 'understanding_sha256': 'b' * 64, 'analysis_version': None}, 'scenario': 'original', 'generation': 'generation-test'},
        'count_basis': 'Structural analysis is not conversion.', 'counts': {'total_workflows': 1, 'structurally_analyzed': 1, 'logical_draft': 0, 'runtime_validated': None},
        'stage_counts': {'structural_only': 1, 'logical_draft': 0}, 'evidence_complete': True,
        'release_gate': {'status': 'BLOCKED', 'verified_business_workflows': 0, 'minimum_business_workflows': 4},
        'production_ready': False, 'execution_allowed': False, 'deployment_allowed': False,
        'issue_counts': {}, 'issue_count_basis': 'Stored blocker occurrences only.', 'agent_run_outcomes': {},
        'agents': [{'role': 'semantics', 'latest_status': 'not_assessed', 'freshness': 'not_assessed', 'latest_run_id': None,
                    'finding_count': 0, 'typed_design_versions': 0, 'reported_usage': None, 'feedback': {}}],
        'human_feedback': {}, 'feedback_basis': 'Advisory outcomes do not clear gates.', 'evaluation_count': 0, 'evaluations': [],
        'reasons': [{'code': 'BUSINESS_RUNTIME_NOT_ASSESSED', 'impact': 'No business output proof.', 'next_step': 'Acquire separately approved paired snapshots.'}],
        'campaigns': {'current': 0, 'stale': 0, 'check_statuses': {}, 'basis': 'Controlled fixtures do not certify business parity.', 'receipts': []},
        'workflows': {'total': 1, 'offset': 0, 'limit': 25, 'truncated': False, 'items': [{'workflow_id': 'w' * 64,
             'name': 'Friendly workflow', 'stage': 'structural_only', 'design_version': None, 'design_freshness': 'not_assessed',
             'campaign_count': 0, 'reason': 'Runtime not assessed.', 'next_step': 'Request a review.', 'blockers': []}]}}


def start(navigation, *, hold=False, wrong=False):
    page, state, fulfill = navigation(fragment='projects')
    pending = []
    state['readiness_requests'] = []
    def read(route):
        state['readiness_requests'].append({'url': route.request.url, 'method': route.request.method,
                                            'headers': route.request.all_headers()})
        if hold:
            pending.append(route)
        else:
            route.fulfill(content_type='application/json', body=json.dumps(payload('c' * 64 if wrong else PACKAGE_A)))
    page.route('**/api/readiness?*', read)
    choose_project(page)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    # These legacy fixture payloads explicitly represent original sources.
    page.locator('#readiness-scenario').select_option('original', force=True)
    tab(page, 'readiness').click(); shown(page, 'readiness')
    return page, state, pending


def preload_setup(navigation, *, hold=False, status=200):
        page, state, _ = navigation(fragment='projects')
        choose_project(page)
        expect(page.locator('#package-ready')).to_have_text('Package ready')
        settle(page)
        requests, pending = [], []

        def read(route):
                requests.append({'url': route.request.url, 'method': route.request.method,
                                                 'headers': route.request.all_headers()})
                if hold:
                        pending.append(route)
                elif status != 200:
                        route.fulfill(status=status, body='{}')
                else:
                        result = payload()
                        result['scope']['scenario'] = 'bronze' if 'scenario=bronze' in route.request.url else 'original'
                        route.fulfill(content_type='application/json', body=json.dumps(result))

        page.route('**/api/readiness?*', read)
        page.evaluate('''() => {
            window.readinessEvents = [];
            window.stopReadinessEvents = window.orionReadiness.subscribe(event => window.readinessEvents.push(event));
            window.readinessScope = epoch => ({projectId: document.getElementById('project-select').value,
                packageHash: document.getElementById('estate-package-select').value,
                scenario: document.getElementById('readiness-scenario').value, epoch});
        }''')
        return page, state, requests, pending


def test_hidden_startup_preload_navigation_reuses_inflight_and_completed_evidence(navigation):
        page, state, requests, pending = preload_setup(navigation, hold=True)
        assert page.locator('#page-readiness').is_hidden()
        page.evaluate('''() => {
            window.firstPreload = window.orionReadiness.preload(window.readinessScope(10));
            window.samePreload = window.orionReadiness.preload(window.readinessScope(10));
        }''')
        page.wait_for_function("window.readinessEvents.some(event => event.status === 'loading')")
        settle(page)
        assert len(pending) == 1
        assert page.evaluate('window.firstPreload === window.samePreload')
        tab(page, 'readiness').click(); shown(page, 'readiness')
        assert len(requests) == 1
        tab(page, 'estate').click(); shown(page, 'estate')
        result = payload()
        result['scope']['scenario'] = page.locator('#readiness-scenario').input_value()
        pending[0].fulfill(content_type='application/json', body=json.dumps(result))
        assert page.evaluate('window.firstPreload.then(result => result.status)') == 'ready'
        tab(page, 'readiness').click(); shown(page, 'readiness')
        expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
        snapshot = page.evaluate('window.orionReadiness.getStatus()')
        assert snapshot == {'status': 'ready', 'scope': {'projectId': PROJECT_A, 'packageHash': PACKAGE_A,
                                             'scenario': result['scope']['scenario']}, 'epoch': 10,
                                             'message': page.locator('#readiness-status').text_content(),
                                             'observedAt': result['observed_at']}
        assert [r['status'] for r in page.evaluate('window.readinessEvents')] == ['loading', 'ready']
        tab(page, 'estate').click(); tab(page, 'readiness').click(); shown(page, 'readiness')
        settle(page)
        assert len(requests) == 1
        assert requests[0]['method'] == 'GET'
        assert requests[0]['headers']['x-workbench-project'] == PROJECT_A
        assert requests[0]['headers']['x-workbench-package'] == PACKAGE_A
        assert not [r for r in state['requests'] if r['method'] != 'GET' and r['path'] != '/api/hosted/heartbeat']


def test_completed_hidden_preload_opens_without_a_navigation_read(navigation):
        page, _, requests, _ = preload_setup(navigation)
        result = page.evaluate('window.orionReadiness.preload(window.readinessScope(1))')
        assert result['status'] == 'ready'
        assert page.locator('#page-readiness').is_hidden()
        assert len(requests) == 1
        assert page.evaluate('window.orionReadiness.preload(window.readinessScope(1))')['status'] == 'ready'
        tab(page, 'readiness').click(); shown(page, 'readiness')
        expect(page.locator('#readiness-content')).to_contain_text('Friendly workflow')
        settle(page)
        assert len(requests) == 1


def test_legacy_navigation_reloads_without_preload(navigation):
    page, state, _ = start(navigation)
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    assert len(state['readiness_requests']) == 1
    tab(page, 'estate').click(); shown(page, 'estate')
    expect(page.locator('#readiness-content')).to_be_empty()
    tab(page, 'readiness').click(); shown(page, 'readiness')
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    assert len(state['readiness_requests']) == 2


def test_rejected_preload_does_not_disable_legacy_navigation_read(navigation):
    page, _, requests, _ = preload_setup(navigation)
    assert page.evaluate('''async () => {
        const request = window.readinessScope(1); request.packageHash = 'wrong';
        try { await window.orionReadiness.preload(request); return false; }
        catch (error) { return error.message.includes('current selection'); }
    }''')
    assert not requests
    tab(page, 'readiness').click(); shown(page, 'readiness')
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    assert len(requests) == 1


def test_preload_selection_change_discards_old_result_and_navigation_never_retries(navigation):
        page, _, requests, pending = preload_setup(navigation, hold=True)
        original = page.locator('#readiness-scenario').input_value()
        changed = 'original' if original == 'bronze' else 'bronze'
        page.evaluate('''() => { window.oldPreload = window.orionReadiness.preload(window.readinessScope(1)); }''')
        page.wait_for_function("window.readinessEvents.some(event => event.status === 'loading')")
        settle(page)
        assert len(pending) == 1
        page.locator('#readiness-scenario').evaluate('''(select, value) => {
            select.value = value; select.dispatchEvent(new Event('change', {bubbles: true}));
        }''', changed)
        assert page.evaluate('window.orionReadiness.getStatus().status') == 'invalidated'
        tab(page, 'readiness').click(); shown(page, 'readiness')
        assert len(requests) == 1
        expect(page.locator('#readiness-content')).to_be_empty()
        assert page.evaluate('''async () => {
            try { await window.orionReadiness.preload(window.readinessScope(1)); return false; }
            catch (error) { return error.message.includes('epoch'); }
        }''')
        page.evaluate('''() => { window.newPreload = window.orionReadiness.preload(window.readinessScope(2)); }''')
        page.wait_for_function("window.orionReadiness.getStatus().status === 'loading'")
        settle(page)
        assert len(pending) == 2
        result = payload()
        result['scope']['scenario'] = changed
        pending[1].fulfill(content_type='application/json', body=json.dumps(result))
        assert page.evaluate('window.newPreload.then(result => result.status)') == 'ready'
        result['scope']['scenario'] = original
        pending[0].fulfill(content_type='application/json', body=json.dumps(result))
        assert page.evaluate('window.oldPreload.then(result => result.status)') == 'invalidated'
        assert json.loads(page.locator('.readiness-technical pre').text_content())['scope']['scenario'] == changed
        assert page.evaluate('window.orionReadiness.getStatus().epoch') == 2
        assert len(requests) == 2


def test_new_epoch_same_scope_starts_fresh_read_and_old_result_cannot_win(navigation):
        page, _, requests, pending = preload_setup(navigation, hold=True)
        page.evaluate('''() => { window.oldPreload = window.orionReadiness.preload(window.readinessScope(10)); }''')
        page.wait_for_function("window.orionReadiness.getStatus().status === 'loading'")
        settle(page)
        page.evaluate('''() => { window.newPreload = window.orionReadiness.preload(window.readinessScope(11)); }''')
        page.wait_for_function("window.orionReadiness.getStatus().epoch === 11")
        settle(page)
        assert len(pending) == 2
        newest = payload(); newest['scope']['scenario'] = page.locator('#readiness-scenario').input_value()
        newest['observed_at'] = '2026-11-19T12:00:00Z'
        pending[1].fulfill(content_type='application/json', body=json.dumps(newest))
        assert page.evaluate('window.newPreload.then(result => result.status)') == 'ready'
        older = payload(); older['scope']['scenario'] = newest['scope']['scenario']
        pending[0].fulfill(content_type='application/json', body=json.dumps(older))
        old = page.evaluate('window.oldPreload')
        assert old['status'] == 'invalidated' and old['epoch'] == 10
        assert page.evaluate('window.orionReadiness.getStatus().observedAt') == newest['observed_at']
        assert len(requests) == 2


def test_hidden_preload_rejects_mismatched_response_without_navigation_retry(navigation):
        page, _, requests, _ = preload_setup(navigation)
        wrong = payload('c' * 64)
        wrong['scope']['scenario'] = page.locator('#readiness-scenario').input_value()
        def read_wrong(route):
            requests.append({'method': route.request.method})
            route.fulfill(content_type='application/json', body=json.dumps(wrong))
        page.route('**/api/readiness?*', read_wrong)
        result = page.evaluate('window.orionReadiness.preload(window.readinessScope(1))')
        assert result['status'] == 'failed' and 'does not match' in result['message']
        tab(page, 'readiness').click(); shown(page, 'readiness')
        expect(page.locator('#readiness-content')).to_be_empty()
        settle(page)
        assert len(requests) == 1


def test_preload_failure_and_workspace_invalidation_require_explicit_refresh(navigation):
        page, _, requests, _ = preload_setup(navigation, status=503)
        outcome = page.evaluate('window.orionReadiness.preload(window.readinessScope(3))')
        assert outcome['status'] == 'failed' and 'HTTP 503' in outcome['message']
        tab(page, 'readiness').click(); shown(page, 'readiness')
        settle(page)
        assert len(requests) == 1
        expect(page.locator('#readiness-content')).to_be_empty()
        expect(page.locator('#readiness-status')).to_contain_text('HTTP 503')
        result = payload(); result['scope']['scenario'] = page.locator('#readiness-scenario').input_value()
        page.route('**/api/readiness?*', lambda route: (requests.append({'method': route.request.method}),
                route.fulfill(content_type='application/json', body=json.dumps(result))))
        page.locator('#readiness-refresh').click()
        expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
        assert len(requests) == 2
        tab(page, 'estate').click()
        page.evaluate("document.dispatchEvent(new CustomEvent('orion:workspace-changed'))")
        assert page.evaluate('window.orionReadiness.getStatus().status') == 'invalidated'
        tab(page, 'readiness').click(); shown(page, 'readiness')
        settle(page)
        assert len(requests) == 2
        expect(page.locator('#readiness-content')).to_be_empty()
        page.locator('#readiness-refresh').click()
        expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
        assert len(requests) == 3


def test_preload_rejects_mismatched_selection_and_old_epoch_without_network(navigation):
        page, _, requests, _ = preload_setup(navigation)
        assert page.evaluate('''async () => {
            const request = window.readinessScope(7); request.packageHash = 'wrong';
            try { await window.orionReadiness.preload(request); return false; }
            catch (error) { return error.message.includes('current selection'); }
        }''')
        assert not requests
        assert page.evaluate('window.orionReadiness.preload(window.readinessScope(7))')['status'] == 'ready'
        assert page.evaluate('''async () => {
            try { await window.orionReadiness.preload(window.readinessScope(6)); return false; }
            catch (error) { return error.message.includes('epoch'); }
        }''')
        assert len(requests) == 1
        page.evaluate('window.stopReadinessEvents()')
        count = page.evaluate('window.readinessEvents.length')
        page.evaluate("document.dispatchEvent(new CustomEvent('orion:agent-run', {detail: {status: 'completed'}}))")
        assert page.evaluate('window.orionReadiness.getStatus().status') == 'invalidated'
        assert page.evaluate('window.readinessEvents.length') == count


def test_project_change_invalidates_hidden_preload_and_rejects_late_evidence(navigation):
    page, _, requests, pending = preload_setup(navigation, hold=True)
    page.evaluate('''() => { window.oldPreload = window.orionReadiness.preload(window.readinessScope(4)); }''')
    page.wait_for_function("window.orionReadiness.getStatus().status === 'loading'")
    settle(page)
    assert len(pending) == 1
    tab(page, 'projects').click()
    page.locator('#project-select').select_option(PROJECT_B)
    assert page.evaluate('window.orionReadiness.getStatus().status') == 'invalidated'
    old = payload(); old['scope']['scenario'] = page.locator('#readiness-scenario').input_value()
    pending[0].fulfill(content_type='application/json', body=json.dumps(old))
    assert page.evaluate('window.oldPreload')['status'] == 'invalidated'
    expect(page.locator('#readiness-content')).to_be_empty()
    assert len(requests) == 1


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 1024, 390])
def test_empty_evidence_three_accessible_charts_and_nonmodal_refresh(navigation, width, theme):
    page, state, _ = start(navigation)
    page.set_viewport_size({'width': width, 'height': 900})
    page.evaluate('(theme) => { document.documentElement.dataset.theme = theme; }', theme)
    expect(page.locator('.readiness-gate')).to_have_text('Not ready for production: 0/4 business workflows verified.')
    expect(page.locator('#readiness-content')).to_contain_text('No execution results recorded yet.')
    expect(page.locator('#readiness-content')).to_contain_text('Friendly workflow')
    expect(page.locator('#readiness-content svg[role=img]')).to_have_count(3)
    assert all(page.locator('#readiness-content svg').nth(i).get_attribute('aria-label') for i in range(3))
    expect(page.locator('#readiness-content summary', has_text='Chart data and legend')).to_have_count(3)
    text = page.locator('#readiness-content').inner_text()
    assert not re.search(r'not[ _]?tracked|campaign|\bN/A\b|\d+%', text, re.I)
    assert page.locator('.readiness-metrics article').count() <= 4
    expect(page.locator('.readiness-metrics [data-metric="runtime_validated"]')).to_have_count(0)
    assert page.locator('.readiness-charts').bounding_box()['y'] < page.locator('.readiness-metrics').bounding_box()['y']
    assert page.locator('.readiness-charts').bounding_box()['y'] < page.locator('.readiness-reference').bounding_box()['y']
    for chart in page.locator('.readiness-chart').all():
        box = chart.bounding_box()
        assert 0 < box['height'] < 390
        assert box['width'] <= width
    if width == 1440:
        boxes = [chart.bounding_box() for chart in page.locator('.readiness-chart').all()]
        assert max(box['y'] for box in boxes) - min(box['y'] for box in boxes) < 2
        assert max(box['y'] + box['height'] for box in boxes) < 900
    expect(page.locator('.readiness-reference')).not_to_have_attribute('open', '')
    expect(page.locator('.readiness-evidence-gaps')).not_to_have_attribute('open', '')
    expect(page.locator('.readiness-no-data')).to_have_count(1)
    expect(page.locator('#readiness-content')).to_contain_text('No agent reviews recorded yet.')
    expect(page.locator('.readiness-metrics strong')).to_have_text(['1', '0', '1'])
    page.locator('#readiness-refresh').click()
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    assert not page.locator('dialog[open], [inert], [aria-modal=true]').count()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert all(r['method'] == 'GET' for r in state['readiness_requests'])
    assert all(r['headers']['x-workbench-project'] == PROJECT_A for r in state['readiness_requests'])
    assert all(r['headers']['x-workbench-package'] == PACKAGE_A for r in state['readiness_requests'])


def test_cross_package_response_never_displayed(navigation):
    page, _, _ = start(navigation, wrong=True)
    expect(page.locator('#readiness-status')).to_contain_text('does not match')
    expect(page.locator('#readiness-content')).to_be_empty()


def test_delayed_response_after_navigation_is_discarded(navigation):
    page, _, pending = start(navigation, hold=True)
    expect(page.locator('#readiness-status')).to_contain_text('Reading authoritative')
    tab(page, 'estate').click()
    for route in pending:
        route.fulfill(content_type='application/json', body=json.dumps(payload()))
    settle(page)
    expect(page.locator('#readiness-content')).to_be_empty()


def test_readiness_prerequisite_locked_before_package_selection(navigation):
    page, _, _ = navigation(fragment='projects')
    expect(tab(page, 'readiness')).to_be_disabled()


def test_knowledge_six_pillars_are_not_synthetic_validation_success(navigation):
    from workbench.architecture_knowledge import PILLARS
    page, _, _ = start(navigation)
    result = payload()
    result['architecture_knowledge'] = {'status': 'available', 'version': 'd' * 64,
        'feature_count': 6, 'fresh_ga_count': 4, 'expired_feature_count': 0,
        'earliest_expiry': '2026-11-23T00:00:00Z', 'data_availability': None,
        'pillars': {p: {'knowledge_count': 1, 'validated': False} for p in PILLARS}}
    refresh_result(page, result)
    page.locator('.readiness-reference > summary').click()
    expect(page.locator('#readiness-content')).to_contain_text('4 fresh GA candidates')
    expect(page.locator('#readiness-content')).to_contain_text('Knowledge is not validation')
    expect(page.locator('#readiness-content')).to_contain_text('Earliest expiry: 2026-11-23')
    expect(page.get_by_text('Not established by knowledge retrieval', exact=True)).to_have_count(6)
    expect(page.locator('.readiness-gate')).to_contain_text('Not ready for production: 0/4')


def refresh_result(page, result):
    """Return only after the exact response is rendered, not an earlier refresh."""
    page.route('**/api/readiness?*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps(result)))
    page.locator('#readiness-refresh').click()
    # Locator polling observes route completion without depending on animation
    # frames, which can be suspended while this long page scrolls to Refresh.
    expect(page.locator('.readiness-technical pre')).to_have_text(
        json.dumps(result, indent=2, ensure_ascii=False))


def test_unknown_counts_are_not_zero_or_metadata_conversion(navigation):
    page, _, _ = start(navigation)
    result = payload()
    result['counts'] = {'total_workflows': 243, 'structurally_analyzed': 243,
                        'agent_assessed': None, 'logical_draft': None,
                        'generated_draft_receipts': None, 'runtime_validated': None,
                        'executable': None, 'output_parity_approved': None}
    result['stage_counts'] = {'structural_only': None, 'logical_draft': None}
    result['evidence_complete'] = False
    refresh_result(page, result)
    expect(page.locator('.readiness-metrics strong')).to_have_text(['243', '243'])
    expect(page.locator('.readiness-chart .readiness-bar')).to_have_count(0)
    expect(page.locator('.readiness-no-data')).to_have_count(3)
    assert '243 designs' not in page.locator('.readiness-overview').inner_text()
    page.locator('.readiness-evidence-gaps > summary').click()
    expect(page.locator('.readiness-evidence-gaps')).to_contain_text('unknown, not zero')
    expect(page.locator('.readiness-evidence-gaps')).to_contain_text('History is bounded')
    assert json.loads(page.locator('.readiness-technical pre').text_content()) == result


def test_recorded_zero_has_zero_width_not_unknown_or_fake_pass(navigation):
    page, _, _ = start(navigation)
    result = payload()
    result['counts'].update(agent_assessed=0, generated_draft_receipts=0, executable=None)
    refresh_result(page, result)
    expect(page.locator('.readiness-metrics strong')).to_have_text(['1', '0', '0', '0'])
    review = page.locator('.readiness-chart').nth(1)
    expect(review.locator('.readiness-bar')).to_have_count(3)
    assert [bar.get_attribute('width') for bar in review.locator('.readiness-bar').all()] == ['0'] * 3
    expect(review).to_have_attribute('data-empty', 'true')
    execution = page.locator('.readiness-chart').nth(2)
    expect(execution.locator('.readiness-bar')).to_have_count(0)
    assert 'pass: 0' not in execution.inner_text().lower()
    expect(page.locator('.readiness-metrics [data-metric="executable"]')).to_have_count(0)


@pytest.mark.parametrize('matches_total', [True, False])
def test_real_stage_distribution_and_overlapping_counts_are_not_percentages(navigation, matches_total):
    page, _, _ = start(navigation)
    result = payload()
    result['counts'].update(total_workflows=10, structurally_analyzed=10,
                            agent_assessed=8, logical_draft=3, generated_draft_receipts=2)
    result['stage_counts'] = {'structural_only': 7 if matches_total else 8, 'logical_draft': 3}
    result['campaigns']['check_statuses'] = {'PASS': 2, 'BLOCKED': 1, 'NOT_RUN': 4}
    refresh_result(page, result)
    stages, reviews, execution = page.locator('.readiness-chart').all()
    assert [n.text_content() for n in stages.locator('.readiness-chart-count').all()] == (['7', '3'] if matches_total else ['8', '3'])
    assert [n.text_content() for n in reviews.locator('.readiness-chart-count').all()] == ['8', '3', '2']
    assert [n.text_content() for n in execution.locator('.readiness-chart-count').all()] == ['2', '1', '4']
    # Real CSS must preserve the chart palette rather than a generic SVG fill.
    colors = execution.locator('.readiness-bar').evaluate_all(
        '(bars) => bars.map(bar => getComputedStyle(bar).fill)')
    assert len(set(colors)) == 3
    assert all(color not in ('none', 'rgba(0, 0, 0, 0)') for color in colors)
    assert len(page.locator('.readiness-metrics article').all()) == 4
    assert ('exclusive recorded stages' in stages.inner_text()) is matches_total
    if not matches_total:
        expect(stages).to_contain_text('Coverage is incomplete')
    expect(reviews).to_contain_text('can overlap')
    assert not re.search(r'\d+%|accuracy:.*\d|converted:.*10', page.locator('#readiness-content').inner_text(), re.I)
    expect(page.locator('.readiness-gate')).to_have_text('Not ready for production: 0/4 business workflows verified.')
    for chart in (stages, reviews, execution):
        assert chart.locator('svg > title').text_content() == chart.locator('svg').get_attribute('aria-label')
        chart.locator('summary').click()
        expect(chart).to_contain_text('not readiness or accuracy')


def test_top_three_issues_keep_all_missing_definitions_and_receipts(navigation):
    page, _, _ = start(navigation)
    result = payload()
    result['reasons'] += [
        {'code': 'CAMPAIGN_ARTIFACT_UNAVAILABLE', 'impact': 'Campaign report missing.', 'next_step': 'Inspect campaign receipt.'},
        {'code': 'TARGET_LAYERS_UNASSIGNED', 'impact': 'Target placement unknown.', 'next_step': 'Review target.'},
        {'code': 'MISSING_PROCEDURE_DEFINITION', 'impact': 'GET_PRODUCT_KEY_ID body missing.', 'next_step': 'Supply the original procedure definition.'},
        {'code': 'MISSING_MAPPING_DEFINITION', 'impact': 'Source mapping definition missing.', 'next_step': 'Supply the original mapping definition.'}]
    result['campaigns']['receipts'] = [{'id': 'pin-only', 'controlled_fixture': {'mock': True}, 'pins': {'package_hash': PACKAGE_A}}]
    refresh_result(page, result)
    expect(page.locator('.readiness-attention > .readiness-reason')).to_have_count(3)
    assert 'campaign' not in page.locator('#readiness-content').inner_text().lower()
    all_issues = page.locator('.readiness-attention > details')
    expect(all_issues).not_to_have_attribute('open', '')
    all_issues.locator('summary').click()
    expect(all_issues.locator('.readiness-reason')).to_have_count(5)
    expect(all_issues).to_contain_text('GET_PRODUCT_KEY_ID body missing')
    expect(all_issues).to_contain_text('Source mapping definition missing')
    expect(page.locator('.readiness-attention')).to_contain_text('not original-data execution results')
    assert json.loads(page.locator('.readiness-technical pre').text_content()) == result


def test_agent_rows_only_show_recorded_roles_and_preserve_usage_and_findings(navigation):
    page, _, _ = start(navigation)
    result = payload()
    unassessed = result['agents'][0]
    result['agents'] = [{**unassessed, 'role': role} for role in ['supervisor', 'discovery', 'validation', 'artifact']]
    result['agents'].append({**unassessed, 'role': 'semantics', 'latest_run_id': 'actual-saved-run',
        'latest_status': 'completed', 'freshness': 'current', 'finding_count': 2,
        'typed_design_versions': 1, 'reported_usage': {'total_tokens': 1234},
        'latest_findings': [{'status': 'blocked', 'summary': 'External definition needed', 'citation_count': 2, 'blockers': ['procedure body']}],
        'feedback': {'request_changes': 1}})
    refresh_result(page, result)
    agents = page.locator('#readiness-content > article').filter(has=page.get_by_role('heading', name='Agent review and human feedback', exact=True))
    expect(agents.locator('tbody tr')).to_have_count(1)
    expect(agents).to_contain_text('semantics')
    assert 'Not tracked' not in agents.inner_text()
    agents.locator('summary').click()
    expect(agents).to_contain_text('actual-saved-run')
    expect(agents).to_contain_text('1234')
    expect(agents).to_contain_text('External definition needed')
    expect(agents).to_contain_text('not role attribution')


@pytest.mark.parametrize('field', ['project', 'scenario'])
def test_wrong_project_or_scenario_response_fails_closed(navigation, field):
    page, _, _ = start(navigation)
    result = payload()
    result['scope']['project_id' if field == 'project' else 'scenario'] = PROJECT_B if field == 'project' else 'bronze'
    page.route('**/api/readiness?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(result)))
    page.locator('#readiness-refresh').click()
    expect(page.locator('#readiness-status')).to_contain_text('does not match')
    expect(page.locator('#readiness-content')).to_be_empty()


def test_old_scenario_response_cannot_overwrite_new_selected_scenario(navigation):
    page, _, pending = start(navigation, hold=True)
    expect(page.locator('#readiness-status')).to_contain_text('Reading authoritative')
    page.locator('#readiness-scenario').select_option('bronze')
    page.wait_for_function('document.getElementById("readiness-status").textContent.startsWith("Reading authoritative")')
    # Route callbacks are serviced while Chromium settles. Both responses ignore cancellation.
    settle(page)
    assert len(pending) == 2
    result = payload()
    result['scope']['scenario'] = 'bronze'
    pending[1].fulfill(content_type='application/json', body=json.dumps(result))
    expect(page.locator('.readiness-context')).to_contain_text('Source data already in Unity Catalog')
    pending[0].fulfill(content_type='application/json', body=json.dumps(payload()))
    settle(page)
    expect(page.locator('.readiness-context')).to_contain_text('Source data already in Unity Catalog')
    assert json.loads(page.locator('.readiness-technical pre').text_content())['scope']['scenario'] == 'bronze'


@pytest.mark.parametrize('status', [401, 403, 503])
def test_refresh_authorization_or_service_failure_never_reuses_previous_evidence(navigation, status):
    page, _, _ = start(navigation)
    expect(page.locator('.readiness-metrics')).to_be_visible()
    page.route('**/api/readiness?*', lambda route: route.fulfill(status=status, body='{}'))
    page.locator('#readiness-refresh').click()
    expect(page.locator('#readiness-status')).to_contain_text(f'HTTP {status}')
    expect(page.locator('#readiness-content')).to_be_empty()
    expect(page.locator('#readiness-refresh')).to_be_enabled()


def test_next_step_clicks_real_navigation_without_invoking_review(navigation):
    page, state, _ = start(navigation)
    prompt = page.locator('#agent-prompt').input_value()
    workflow = page.locator('#agent-workflow').input_value()
    expect(page.locator('.readiness-actions button')).to_have_text('Review a workflow')
    page.locator('.readiness-actions button').click()
    shown(page, 'agents')
    assert page.locator('#agent-prompt').input_value() == prompt
    assert page.locator('#agent-workflow').input_value() == workflow
    assert not [r for r in state['requests'] if r['method'] != 'GET' and r['path'] != '/api/hosted/heartbeat']


def test_next_step_respects_disabled_navigation_and_keeps_context(navigation):
    page, state, _ = start(navigation)
    expect(page.locator('.readiness-actions button')).to_be_visible()
    tab(page, 'agents').evaluate('(button) => { button.disabled = true; button.setAttribute("aria-disabled", "true"); }')
    page.locator('.readiness-actions button').click()
    shown(page, 'readiness')
    expect(page.locator('#project-select')).to_have_value(PROJECT_A)
    result = payload()
    result['counts']['logical_draft'] = 1
    result['stage_counts'] = {'structural_only': 0, 'logical_draft': 1}
    refresh_result(page, result)
    expect(page.locator('.readiness-actions button')).to_have_text('Review designs and code')
    page.locator('.readiness-actions button').click()
    shown(page, 'proposals')
    expect(page.locator('#project-select')).to_have_value(PROJECT_A)
    assert not [r for r in state['requests'] if r['method'] != 'GET' and r['path'] != '/api/hosted/heartbeat']


@pytest.mark.parametrize('has_design', [False, True])
def test_next_step_preserves_selected_bronze_scenario(navigation, has_design):
    page, _, _ = start(navigation)
    result = payload()
    result['scope']['scenario'] = 'bronze'
    if has_design:
        result['counts']['logical_draft'] = 1
        result['stage_counts'] = {'structural_only': 0, 'logical_draft': 1}
    page.route('**/api/readiness?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(result)))
    page.locator('#readiness-scenario').select_option('bronze')
    expect(page.locator('.readiness-context')).to_contain_text('Source data already in Unity Catalog')
    page.locator('.readiness-actions button').click()
    shown(page, 'proposals' if has_design else 'agents')
    expect(page.locator('#scenario' if has_design else '#agent-scenario')).to_have_value('bronze')
    expect(page.locator('#project-select')).to_have_value(PROJECT_A)


def test_delayed_response_after_project_change_is_discarded(navigation):
    page, _, pending = start(navigation, hold=True)
    expect(page.locator('#readiness-status')).to_contain_text('Reading authoritative')
    tab(page, 'projects').click()
    page.locator('#project-select').select_option(PROJECT_B)
    for route in pending:
        route.fulfill(content_type='application/json', body=json.dumps(payload()))
    settle(page)
    expect(page.locator('#readiness-content')).to_be_empty()
    expect(page.locator('#project-select')).to_have_value(PROJECT_B)


def test_real_lineage_source_control_and_readiness_refresh_preserve_external_dom(lineage_ui):
    page, _, requests, _, ids = lineage_ui
    page.locator('#readiness-scenario').select_option('original')
    page.locator('#lineage-load').click()
    expect(page.locator('#lineage-status')).to_contain_text('Captured XML lineage')
    result = payload()
    result['scope']['project_id'] = '__legacy__'
    result['workflows']['items'][0]['workflow_id'] = ids['Child']
    page.route('**/api/readiness?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(result)))
    page.add_style_tag(url='/static/readiness.css')
    page.add_script_tag(url='/static/readiness.js')
    page.locator('#readiness-refresh').click()
    expect(page.locator('[data-readiness-workflow]')).to_be_visible()
    before = len(requests)
    page.locator('[data-readiness-workflow]').click()
    expect(page.locator('#lineage-workflow')).to_have_value(ids['Child'])
    expect(page.locator('#lineage-status')).to_contain_text('Captured XML lineage')
    assert len(requests) == before + 1
    assert f'workflow_id={ids["Child"]}' in requests[-1].url
    page.evaluate('window.readinessLineageIdentity = document.getElementById("migration-lineage")')
    lineage_before = page.locator('#lineage-graph').inner_html()
    refresh_result(page, result)
    assert page.evaluate('window.readinessLineageIdentity === document.getElementById("migration-lineage")')
    assert page.locator('#lineage-graph').inner_html() == lineage_before
    expect(page.locator('#lineage-workflow')).to_have_value(ids['Child'])
    assert len(requests) == before + 1
    page.locator('#lineage-workflow').evaluate('(select) => { select.disabled = true; }')
    expect(page.locator('[data-readiness-workflow]')).to_be_hidden()


def test_unavailable_source_control_has_no_unsupported_handoff(navigation):
    page, _, _ = start(navigation)
    expect(page.locator('[data-readiness-workflow]')).to_be_hidden()
    source = (STATIC / 'readiness.js').read_text(encoding='utf-8')
    assert 'orion:review-workflow' not in source
    assert 'orionOpenWorkflow' not in source
    assert 'new CustomEvent' not in source


def test_reduced_motion_dashboard_does_not_add_animations(navigation):
    page, _, _ = start(navigation)
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('.readiness-charts').evaluate('(root) => root.getAnimations({subtree: true}).length') == 0
    expect(page.locator('#readiness-content svg[role="img"] title')).to_have_count(3)


def test_readiness_navigation_position_after_validate_before_cicd(navigation):
    # Main owns the markup change. This contract joins its consolidated merge gate.
    page, _, _ = navigation(fragment='projects')
    order = page.locator('.navigation [data-page]').evaluate_all('(buttons) => buttons.map(button => button.dataset.page)')
    assert order.index('validate') < order.index('readiness') < order.index('release')