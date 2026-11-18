"""Real current markup/scripts/CSS in offline Chromium, no model or business writes.

Navigation uses the actual app, including verified package gates and hosted read
envelopes. DOM-only state changes below explicitly exercise presentation updates,
not fake user input or a replacement navigation/guidance implementation.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from test_navigation_prerequisites import (
    navigation, tab, shown, blocked, choose_project, PROTECTED,
)
from test_project_package_autoload import (
    browser, member, settle, PROJECT_A, PACKAGE_A, PACKAGE_B,
)
from test_explorer_graph_ui import explorer, selected_object, loaded, CHILD_ID


PULSE = '.orion-next-action'
CASES = [
    ('projects', 'New or existing project', '#refresh-projects'),
    ('estate', 'Estate', '[data-go="explorer"]'),
    ('readiness', 'Readiness', '#readiness-refresh'),
    ('explorer', 'Pipeline explorer', '#object-list button[data-object-id="wf/selected"]'),
    ('proposals', 'Agentic Migration Proposals', '#redesign-refresh'),
    ('evidence', 'Legacy Data Integration Artifacts', '#connections-refresh'),
    ('agents', 'Agent activity', '#refresh-runs'),
    ('validate', 'Validate', '#validation-context'),
    ('release', 'CI CD (DAB + Github)', '#page-release [href="/api/export/understanding"]'),
    ('logs', 'Logs', '#logs-refresh'),
    ('evolution', 'Evolution roadmap', '#evidence-design-open'),
]


def workspace(navigation, **kwargs):
    page, state, fulfill = navigation(fragment='projects', **kwargs)
    state['objects'] = [{'id': 'wf/selected', 'name': 'Selected workflow', 'kind': 'workflow'}]
    choose_project(page)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    return page, state, fulfill


def suggested(page, selector):
    expect(page.locator(PULSE)).to_have_count(1)
    expect(page.locator(selector)).to_have_class(re.compile(r'\borion-next-action\b'))
    expect(page.locator(selector)).to_be_visible()
    expect(page.locator(selector)).to_be_enabled()
    assert page.locator(selector).evaluate("el => !el.closest('[hidden], [inert], [aria-disabled=true]')")
    hint = page.locator('.next-action-hint:not([hidden])')
    expect(hint).to_have_count(1)
    expect(hint).to_contain_text('Suggested next step:')
    assert hint.get_attribute('aria-live') is None
    assert hint.get_attribute('role') is None
    assert hint.get_attribute('id') in page.locator(selector).get_attribute('aria-describedby').split()


@pytest.mark.parametrize('name,label,selector', CASES)
def test_real_page_entry_has_one_safe_suggestion_with_actual_navigation_label(navigation, name, label, selector):
    page, state, _ = workspace(navigation)
    tab(page, 'estate').click()
    if name != 'estate':
        tab(page, name).click()
    shown(page, name)
    expect(tab(page, name)).to_contain_text(label)
    suggested(page, selector)
    assert page.locator('.page[hidden] ' + PULSE).count() == 0
    assert not page.locator('dialog[open], [inert]').count()
    assert page.locator(selector).evaluate('el => document.activeElement !== el')
    # Existing heartbeat POSTs report actual browser activity. The fixture
    # normalizes durable read envelopes to GET and rejects all business writes.
    assert not [r for r in state['requests']
                if r['method'] != 'GET' and r['path'] != '/api/hosted/heartbeat']


@pytest.mark.parametrize('interaction', ['click', 'input', 'keydown'])
def test_unrelated_interaction_does_not_permanently_stop_action_guidance(navigation, interaction):
    page, _, _ = navigation(fragment='logs')
    suggested(page, '#logs-refresh')
    if interaction == 'click':
        page.locator('#logs-level').click()
    elif interaction == 'input':
        page.locator('#logs-run').fill('review')
    else:
        page.keyboard.press('Tab')
    if interaction == 'input':
        expect(page.locator(PULSE)).to_have_count(0)
    page.evaluate("""() => {
      document.getElementById('logs-summary').textContent = 'Routine recorded read update';
      document.dispatchEvent(new CustomEvent('orion:navigate', {detail: {page: 'logs'}}));
      document.getElementById('logs-refresh').disabled = true;
      document.getElementById('logs-refresh').disabled = false;
    }""")
    settle(page)
    suggested(page, '#logs-refresh')
    tab(page, 'logs').click()  # Same-page navigation is not a new visit.
    settle(page)
    suggested(page, '#logs-refresh')
    tab(page, 'evolution').click()
    suggested(page, '#evidence-design-open')
    tab(page, 'logs').click()
    suggested(page, '#logs-refresh')


def test_programmatic_updates_do_not_dismiss_or_restart_existing_animation(navigation):
    page, _, _ = navigation(fragment='logs')
    suggested(page, '#logs-refresh')
    page.evaluate("""() => {
      window.originalPulse = document.getElementById('logs-refresh').getAnimations()
        .find(a => a.animationName === 'orion-next-step');
      document.getElementById('logs-run').dispatchEvent(new Event('input', {bubbles: true}));
      document.getElementById('theme-toggle').click();
      document.getElementById('logs-summary').textContent = 'Routine read rerender';
    }""")
    settle(page)
    suggested(page, '#logs-refresh')
    assert page.evaluate("document.getElementById('logs-refresh').getAnimations().includes(originalPulse)")
    page.keyboard.press('Shift')
    suggested(page, '#logs-refresh')


def test_disabled_hidden_inert_and_busy_controls_are_never_suggested(navigation):
    page, _, _ = workspace(navigation)
    tab(page, 'evidence').click()
    suggested(page, '#connections-refresh')
    page.evaluate("document.getElementById('connections-refresh').disabled = true")
    suggested(page, '#refresh-documents')
    page.evaluate("document.getElementById('refresh-documents').hidden = true")
    expect(page.locator(PULSE)).to_have_count(0)
    page.evaluate("document.getElementById('connections-refresh').disabled = false")
    suggested(page, '#connections-refresh')
    page.evaluate("document.querySelector('.app-shell').inert = true")
    expect(page.locator(PULSE)).to_have_count(0)
    page.evaluate("document.querySelector('.app-shell').inert = false")
    suggested(page, '#connections-refresh')
    page.evaluate("document.getElementById('connections-card').setAttribute('aria-busy', 'true')")
    expect(page.locator(PULSE)).to_have_count(0)
    page.evaluate("document.getElementById('connections-card').removeAttribute('aria-busy')")
    suggested(page, '#connections-refresh')
    page.locator('#document-description').fill('Owner context')
    page.evaluate("document.getElementById('connections-refresh').disabled = true")
    page.evaluate("document.getElementById('connections-refresh').disabled = false")
    settle(page)
    suggested(page, '#connections-refresh')


def test_initial_package_understanding_waits_for_exact_readiness_not_enabled_cta(navigation):
    page, state, fulfill = navigation(fragment='projects', hold=True)
    choose_project(page)
    expect(tab(page, 'estate')).to_be_enabled()
    tab(page, 'estate').click()
    shown(page, 'estate')
    expect(page.locator('#package-ready')).to_have_text('Loading package understanding')
    blocked(page, PROTECTED, estate_enabled=True)
    expect(page.locator('[data-go="explorer"]')).to_be_disabled()
    settle(page)
    expect(page.locator(PULSE)).to_have_count(0)
    # Initial understanding deliberately locks package-dependent sections, not
    # the whole browser shell. That existing safety gate is not a read modal.
    expect(page.locator('.app-shell[inert], dialog[open], .hosted-operation-inert')).to_have_count(0)
    expect(page.locator('#page-explorer')).to_have_attribute('inert', '')
    expect(page.locator('#page-estate')).not_to_have_attribute('inert', '')
    route, data = state['pending'].pop()
    state['hold'] = False
    fulfill(route, data)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    suggested(page, '[data-go="explorer"]')


def test_mismatched_package_cannot_recommend_normal_but_guarded_explore_button(navigation):
    page, _, _ = navigation(fragment='estate', overview_hash=PACKAGE_B,
                            saved={'project': PROJECT_A, 'package': PACKAGE_A})
    shown(page, 'estate')
    blocked(page, PROTECTED, estate_enabled=True)
    settle(page)
    expect(page.locator(PULSE)).to_have_count(0)


def test_queued_upload_member_does_not_unlock_package_or_model_work(navigation):
        page, state, _ = navigation(fragment='projects', members=[member(status='queued')])
        choose_project(page)
        expect(page.locator('#project-package')).to_be_disabled()
        blocked(page)
        expect(page.locator('#agent-submit')).to_be_disabled()
        assert not any(r['path'].startswith(('/api/agents/', '/api/models/connect')) for r in state['requests'])


def test_current_proposal_review_handoff_precedes_legacy_candidates_and_mutations(navigation):
        page, state, _ = workspace(navigation)
        state['proposals'] = [{
                'id': 'd' * 64, 'workflow_id': 'wf/selected', 'name': 'Current candidate',
                'scenario': 'original', 'version': 1, 'status': 'draft', 'blockers': [],
            'graph': {'nodes': [], 'edges': []},
        }, {
            'id': 'e' * 64, 'workflow_id': 'wf/selected', 'name': 'Another current candidate',
            'scenario': 'original', 'version': 1, 'status': 'draft', 'blockers': [],
            'graph': {'nodes': [], 'edges': []},
        }]
        tab(page, 'proposals').click()
        page.locator('#redesign-workflow').select_option('wf/selected')
        expect(page.locator('#redesign-status')).to_contain_text('No agent-submitted design; structural baseline separate')
        expect(page.locator('#redesign-generate')).to_be_enabled()
        suggested(page, '#redesign-generate')
        page.evaluate("document.getElementById('redesign-generate').disabled = true")
        # The enabled exact-workflow target read precedes legacy candidates.
        # Test that real next step before making it unavailable in this fixture.
        suggested(page, '#source-target-read')
        page.evaluate("document.getElementById('source-target-read').disabled = true")
        page.get_by_text('Structural proposal baseline and original graph', exact=True).click()
        selected = page.locator('#proposal-list button[aria-pressed="true"]')
        expect(selected).to_have_count(1)
        expect(selected).not_to_have_class(re.compile(r'\borion-next-action\b'))
        # Legacy migration candidates remain available beneath the primary
        # exact-workflow handoff. Only a different, unselected candidate is next.
        suggested(page, '#proposal-list button[aria-pressed="false"]')
        page.locator('#proposal-list button[aria-pressed="false"]').evaluate('(button) => { button.disabled = true; }')
        suggested(page, '#redesign-refresh')
        assert not page.locator('#sql-draft-submit' + PULSE + ', #workflow-artifact-submit' + PULSE).count()


@pytest.mark.parametrize('excluded', ['hidden', 'visibility', 'opacity', 'aria-disabled', 'fieldset'])
def test_unavailable_primary_action_selects_only_an_available_fallback(navigation, excluded):
        page, _, _ = navigation(fragment='evolution')
        suggested(page, '#evidence-design-open')
        page.evaluate("""excluded => {
            const el = document.getElementById('evidence-design-open');
            if (excluded === 'hidden') el.hidden = true;
            if (excluded === 'visibility') el.style.visibility = 'hidden';
            if (excluded === 'opacity') el.style.opacity = '0';
            if (excluded === 'aria-disabled') el.setAttribute('aria-disabled', 'true');
            if (excluded === 'fieldset') {
                const fieldset = document.createElement('fieldset'); fieldset.disabled = true;
                el.before(fieldset); fieldset.append(el);
            }
        }""", excluded)
        suggested(page, '#agent-design-open')


@pytest.mark.parametrize('dismiss_first', [False, True])
def test_async_inventory_recommends_after_completion_even_after_unrelated_interaction(navigation, dismiss_first):
    page, _, _ = workspace(navigation)
    pending = []

    def hold_inventory(route):
        body = route.request.post_data_json
        # The real adapter sends pathname and query as separate envelope fields.
        if body['method'] == 'GET' and body['path'] == '/api/objects':
            pending.append((route, body['id']))
        else:
            route.fallback()

    page.route('**/api/hosted/operations', hold_inventory)
    tab(page, 'explorer').click()
    shown(page, 'explorer')
    expect(page.locator('#object-list')).to_contain_text('Loading')
    settle(page)
    expect(page.locator(PULSE)).to_have_count(0)
    # Routine inventory reads do not lock navigation or open a modal.
    for name in ('projects', 'estate', 'agents', 'logs', 'evolution'):
        expect(tab(page, name)).to_be_enabled()
    assert not page.locator('[inert], dialog[open]').count()
    if dismiss_first:
        page.keyboard.press('Tab')
    assert len(pending) == 1
    route, identifier = pending.pop()
    route.fulfill(content_type='application/json', body=json.dumps({
        'operation_id': identifier, 'status': 'completed', 'result': {
            'items': [{'id': 'wf/selected', 'name': 'Selected workflow', 'kind': 'workflow'}], 'total': 1,
        },
    }))
    expect(page.locator('#object-list button')).to_have_count(1)
    suggested(page, '#object-list button[data-object-id="wf/selected"]')


def test_missing_model_route_recommends_connect_never_disabled_run(navigation):
    page, state, _ = workspace(navigation, agent_available=False)
    tab(page, 'agents').click()
    suggested(page, '#connect-workspace-models')
    expect(page.locator('#agent-submit')).to_be_disabled()
    assert not page.locator('#agent-submit' + PULSE).count()
    assert not any(r['path'] in ('/api/agents/run', '/api/models/connect') for r in state['requests'])


def test_old_overview_readiness_cannot_suggest_invoke_before_fresh_picker_status(navigation):
        page, state, fulfill = workspace(navigation, hold_models=True)
        page.evaluate("""() => {
            document.getElementById('agent-workflow').value = 'wf/selected';
        }""")
        tab(page, 'agents').click()
        expect(page.locator('#agent-availability')).to_have_class(re.compile('success'))
        expect(page.locator('#agent-submit')).to_be_enabled()
        suggested(page, '#connect-workspace-models')
        assert not page.locator('#agent-submit' + PULSE).count()
        assert state['pending_models']
        for route, data in state['pending_models']:
                fulfill(route, data)
        suggested(page, '#agent-submit')
        assert not any(r['path'] == '/api/agents/run' for r in state['requests'])


def test_run_recommendation_requires_configured_model_valid_workflow_and_enabled_run(navigation):
    page, state, _ = workspace(navigation)
    page.evaluate("""() => {
      document.getElementById('agent-workflow').value = 'wf/selected';
    }""")
    tab(page, 'agents').click()
    expect(page.locator('#agent-submit')).to_be_enabled()
    suggested(page, '#agent-submit')
    page.evaluate("document.getElementById('agent-submit').disabled = true")
    suggested(page, '#refresh-runs')
    expect(page.locator('#agent-consent')).to_have_count(0)
    assert not any(r['path'] == '/api/agents/run' for r in state['requests'])


def test_reduced_motion_retains_static_cue_without_live_announcements_or_focus(navigation):
    page, _, _ = navigation(fragment='evolution')
    page.emulate_media(reduced_motion='reduce')
    suggested(page, '#evidence-design-open')
    cue = page.locator('#evidence-design-open')
    assert cue.evaluate('el => getComputedStyle(el).animationName') == 'none'
    assert cue.evaluate('el => getComputedStyle(el).boxShadow') != 'none'
    assert cue.evaluate("el => !el.getAnimations().some(a => a.animationName === 'orion-next-step')")
    assert not cue.evaluate('el => document.activeElement === el')


def test_background_visibility_pauses_same_animation_without_restarting_it(navigation):
    page, _, _ = navigation(fragment='evolution')
    suggested(page, '#evidence-design-open')
    page.evaluate("""() => {
      window.pulseBeforeHide = document.getElementById('evidence-design-open').getAnimations()
        .find(a => a.animationName === 'orion-next-step');
      Object.defineProperty(document, 'hidden', {configurable: true, value: true});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    assert page.locator('#evidence-design-open').evaluate('el => getComputedStyle(el).animationPlayState') == 'paused'
    page.evaluate("""() => {
      Object.defineProperty(document, 'hidden', {configurable: true, value: false});
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    settle(page)
    assert page.evaluate("document.getElementById('evidence-design-open').getAnimations().includes(pulseBeforeHide)")
    page.keyboard.press('Tab')
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    settle(page)
    suggested(page, '#evidence-design-open')


def test_description_tokens_preserved_and_explicit_dialog_advances_after_close(navigation):
    page, _, _ = navigation(fragment='evolution')
    suggested(page, '#evidence-design-open')
    page.evaluate("document.getElementById('evidence-design-open').setAttribute('aria-describedby', 'existing-help next-action-evolution')")
    page.locator('#evidence-design-open').click()
    expect(page.locator('#evidence-design-dialog')).to_be_visible()
    expect(page.locator(PULSE)).to_have_count(0)
    expect(page.locator('#evidence-design-open')).to_have_attribute('aria-describedby', 'existing-help')
    page.locator('#evidence-design-close').click()
    settle(page)
    suggested(page, '#agent-design-open')


def test_clicked_action_advances_once_and_busy_rerenders_do_not_reinstruct_it(navigation):
    page, _, _ = navigation(fragment='logs')
    page.route('**/api/logs/export?*', lambda route: route.fulfill(content_type='text/csv', body='level,message\n'))
    suggested(page, '#logs-refresh')
    page.locator('#logs-refresh').click()
    suggested(page, '#logs-export')
    page.evaluate("document.getElementById('logs-refresh').disabled = true")
    page.evaluate("document.getElementById('logs-refresh').disabled = false")
    settle(page)
    suggested(page, '#logs-export')
    page.locator('#logs-export').click()
    expect(page.locator(PULSE)).to_have_count(0)
    page.evaluate("document.getElementById('logs-summary').textContent = 'Routine update'")
    settle(page)
    expect(page.locator(PULSE)).to_have_count(0)
    page.locator('#logs-level').select_option('warning')
    suggested(page, '#logs-refresh')


@pytest.mark.parametrize('hosted', [False, True])
def test_actual_explorer_object_tab_and_graph_node_progression(explorer, hosted):
    page, state, _, wire = explorer(hosted=hosted)
    suggested(page, '#object-list button[data-object-id]')
    selected_object(page).click()
    loaded(page)
    suggested(page, '#tab-english')
    page.locator('#tab-english').click()
    suggested(page, '#tab-fields')
    page.locator('#tab-fields').click()
    suggested(page, '#tab-graph')
    page.locator('#tab-graph').click()
    expect(page.locator(PULSE)).to_have_count(0)
    before = len(wire)
    page.locator(f'#graph-area [data-node-id="{CHILD_ID}"]').click()
    expect(page.locator('#tab-fields')).to_have_attribute('aria-selected', 'true')
    suggested(page, '#tab-graph')
    assert wire[before:] == []
    assert not page.locator('#download-object' + PULSE).count()
    page.locator('#tab-graph').click()
    suggested(page, '#tab-english')
    page.locator('#tab-json').click()
    expect(page.locator(PULSE)).to_have_count(0)


def test_continuous_typing_suspends_without_animation_flashing_then_resumes(navigation):
    page, _, _ = navigation(fragment='logs')
    suggested(page, '#logs-refresh')
    page.locator('#logs-run').click()
    expect(page.locator(PULSE)).to_have_count(0)
    page.locator('#logs-run').press_sequentially('selected-run', delay=100)
    expect(page.locator(PULSE)).to_have_count(0)
    suggested(page, '#logs-refresh')
    page.locator('#logs-refresh').click()
    suggested(page, '#logs-export')


def test_readiness_click_waits_for_enabled_trace_and_keeps_completed_refresh_dismissed(navigation):
    page, _, _ = workspace(navigation)
    tab(page, 'readiness').click()
    suggested(page, '#readiness-refresh')
    page.evaluate("document.getElementById('lineage-load').disabled = true")
    page.locator('#readiness-refresh').click()
    expect(page.locator(PULSE)).to_have_count(0)
    # Only the real enabled control gate permits the next action.
    page.evaluate("document.getElementById('lineage-trace').disabled = false")
    suggested(page, '#lineage-trace')
    page.evaluate("document.getElementById('readiness-refresh').disabled = true")
    page.evaluate("document.getElementById('readiness-refresh').disabled = false")
    suggested(page, '#lineage-trace')
    tab(page, 'evolution').click()
    suggested(page, '#evidence-design-open')
    assert not page.locator('#page-readiness ' + PULSE).count()


def test_validation_async_context_advances_only_with_valid_inputs(navigation):
    page, state, _ = workspace(navigation)
    pending = []
    page.route('**/api/validation/context', lambda route: pending.append(route))
    tab(page, 'validate').click()
    suggested(page, '#validation-context')
    page.locator('#validation-context').click()
    expect(page.locator('#validation-context')).to_be_disabled()
    expect(page.locator(PULSE)).to_have_count(0)
    assert len(pending) == 1
    pending.pop().fulfill(content_type='application/json', body=json.dumps({
        'pins': {'package_hash': PACKAGE_A, 'understanding_sha256': 'b' * 64, 'analysis_version': None},
        'hosted_obo': False,
    }))
    expect(page.locator('#validation-create')).to_be_enabled()
    expect(page.locator(PULSE)).to_have_count(0)
    page.locator('#validation-workflow').select_option('wf/selected')
    page.route('**/api/databricks/volumes/status', lambda route: route.fulfill(json={'connected': True}))
    page.route('**/api/databricks/volumes/catalogs', lambda route: route.fulfill(json={
        'items': [{'name': 'fixture_catalog'}], 'truncated': False}))
    page.locator('#validation-load-catalogs').click()
    expect(page.locator('#validation-source-catalog option')).to_have_count(2)
    page.locator('#validation-source-catalog').select_option('fixture_catalog')
    # The real input handlers invalidate context. Explicitly reload it.
    page.locator('#validation-context').click()
    assert len(pending) == 1
    pending.pop().fulfill(content_type='application/json', body=json.dumps({
        'pins': {'package_hash': PACKAGE_A}, 'hosted_obo': False,
    }))
    suggested(page, '#validation-create')
    expect(page.locator('#validation-consent')).to_have_count(0)
    expect(page.locator(PULSE)).to_have_count(1)
    assert not any(r['path'] == '/api/validation/campaigns' for r in state['requests'])


def test_reduced_motion_progresses_with_static_hint_after_action(navigation):
    page, _, _ = navigation(fragment='logs')
    page.emulate_media(reduced_motion='reduce')
    page.locator('#logs-refresh').click()
    suggested(page, '#logs-export')
    assert page.locator('#logs-export').evaluate('el => getComputedStyle(el).animationName') == 'none'