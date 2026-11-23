"""Offline real-app regressions for the original estate-only capability cards."""
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / 'static'
NAV = ['projects', 'estate', 'explorer', 'agents', 'proposals', 'evidence',
    'validate', 'readiness', 'release', 'logs', 'evolution']
PAGES = {
    'estate': 'Estate',
    'proposals': 'Agentic Migration Proposals',
    'explorer': 'Pipeline explorer',
    'evolution': 'Evolution roadmap',
}


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def app(browser):
    context = browser.new_context(offline=True, service_workers='block')
    page = context.new_page()
    errors, unexpected, requested = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    markup = (STATIC / 'index.html').read_text(encoding='utf-8')
    # Keep real application/navigation, isolate unrelated extensions/assets.
    # Retain any rejected include so accidental restoration cannot be hidden.
    markup = re.sub(r'<script\b[^>]*src="([^"]+)"[^>]*>\s*</script>',
                    lambda match: match[0] if match[1] in {
                        '/static/app.js', '/static/capability-status.js'} else '', markup)
    markup = re.sub(r'<link\b[^>]*>|<img\b[^>]*>', '', markup)
    markup = markup.replace('</head>', '<link rel="stylesheet" href="/static/app.css"></head>')
    response = {'status': 200, 'data': {'ready': False, 'status': 'empty'}, 'health': {'status': 'ok'},
                'nodes': [], 'held_objects': [], 'hold_objects': False}

    def route_request(route):
        path = urlsplit(route.request.url).path
        requested.append(path)
        if path == '/':
            route.fulfill(content_type='text/html', body=markup)
        elif path in {'/static/app.js', '/static/app.css'}:
            route.fulfill(path=str(STATIC / path.rsplit('/', 1)[-1]))
        elif path == '/api/health':
            route.fulfill(json=response['health'])
        elif path == '/api/overview':
            route.fulfill(status=response['status'], json=response['data'])
        elif path == '/api/projects':
            route.fulfill(json={'items': []})
        elif path == '/api/input-packages':
            route.fulfill(json={'items': []})
        elif path == '/api/objects':
            route.fulfill(json={'items': response['nodes'], 'total': len(response['nodes'])})
        elif path.startswith('/api/object/'):
            node = next(node for node in response['nodes'] if node['id'] == path.rsplit('/', 1)[-1])
            model = {'node': node, 'graph': {'nodes': response['nodes'], 'edges': []}}
            if response['hold_objects']:
                response['held_objects'].append((route, model))
            else:
                route.fulfill(json=model)
        elif path == '/api/agents/runs':
            route.fulfill(json={'items': []})
        elif path.startswith('/api/workflows/') and path.endswith('/migration-context'):
            route.fulfill(status=404, json={'detail': 'No canonical package context in capability fixture'})
        else:
            unexpected.append(path)
            route.abort()

    context.route('**/*', route_request)
    try:
        yield page, response, requested
    finally:
        for route, _ in response['held_objects']:
            try:
                route.abort()
            except Exception:
                pass
        context.close()
        assert not unexpected, unexpected
        assert not errors, errors


def assert_original_ui_and_navigation(page):
    expect(page.locator('#implementation-status')).to_have_count(0)
    expect(page.get_by_role('heading', name='Implementation status and remaining work')).to_have_count(0)
    cards = page.locator('#capabilities')
    expect(cards).to_have_count(1)
    assert cards.evaluate('node => node.closest(".page").id') == 'page-estate'
    heading = page.get_by_role('heading', name=re.compile(r'^ORION Capabilities & unsupported features'), include_hidden=True)
    expect(heading).to_have_count(1)
    assert heading.evaluate('node => node.closest("article").querySelector("#capabilities") !== null')
    assert page.locator('.navigation button[data-page]').evaluate_all(
        'nodes => nodes.map(node => node.dataset.page)') == NAV
    # This fixture deliberately has no selected package. The prerequisite gate
    # must keep package-dependent tabs disabled rather than opening empty/error
    # views. Capability content remains in its one estate-only DOM location.
    expect(cards).to_have_count(1)
    for destination in ('estate', 'explorer', 'agents', 'proposals', 'evidence', 'validate', 'readiness', 'release'):
        expect(page.locator(f'.navigation button[data-page={destination}]')).to_be_disabled()
        if destination != 'estate':
            expect(page.locator(f'#page-{destination} #capabilities')).to_have_count(0)
    # Capability rendering does not authorize Estate without a ready package.
    # Selected completed packages retain their separately tested progress view.
    expect(page.locator('#page-agents #capabilities')).to_have_count(0)
    expect(page.locator('#agent-submit')).to_be_disabled()
    for destination in ('projects', 'logs', 'evolution'):
        expect(page.locator(f'.navigation button[data-page={destination}]')).to_be_enabled()
    expect(page.locator('#implementation-status')).to_have_count(0)


def test_original_api_capability_cards_remain_estate_only(app):
    page, response, requested = app
    response['data']['capabilities'] = {
        'xml_inventory': True,
        'semantic_parity': False,
        'advisory_agents': {'available': True, 'description': 'Configured advisory adapter.'},
        'physical_binding': {'supported': False, 'reason': 'Connection tests are not configured.'},
    }
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    cards = page.locator('#capabilities')
    expect(cards.locator('.capability')).to_have_count(4)
    expect(cards.locator('.capability-overview')).to_contain_text('of 4 capabilities reported available. Execution gates still apply.')
    expect(cards.locator('.capability-overview strong')).to_have_text('2')
    for name, available, explanation in (
        ('xml inventory', True, 'Availability reported by the service. Execution gates still apply.'),
        ('semantic parity', False, 'Unsupported or not configured.'),
        ('advisory agents', True, 'Configured advisory adapter.'),
        ('physical binding', False, 'Connection tests are not configured.'),
    ):
        card = cards.locator('.capability').filter(has=page.get_by_text(name, exact=True))
        expect(card).to_have_count(1)
        expect(card).to_have_class(re.compile('capability-available' if available else 'capability-unavailable'))
        expect(card).to_contain_text('Reported available' if available else 'Unavailable')
        expect(card).to_contain_text(explanation)
    expect(cards.get_by_text('Service capability record', exact=True)).to_have_count(2)
    assert_original_ui_and_navigation(page)
    expect(cards.locator('.capability')).to_have_count(4)
    assert '/api/overview' in requested


@pytest.mark.parametrize('overview_status', [200, 503])
def test_missing_or_failed_overview_does_not_create_global_panel(app, overview_status):
    page, response, requested = app
    response['status'] = overview_status
    if overview_status == 503:
        response['data'] = {'detail': 'Overview failed'}
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    if overview_status == 503:
        expect(page.locator('#notice')).to_contain_text('Cannot load overview')
        expect(page.locator('#capabilities')).to_have_text('Capability information has not been loaded.')
    else:
        expect(page.locator('#capabilities')).to_have_text('The API has not reported capabilities. Availability is unknown.')
    expect(page.locator('#capabilities .capability')).to_have_count(0)
    assert_original_ui_and_navigation(page)
    assert '/api/overview' in requested


def test_agent_capability_distinguishes_existing_roles_from_missing_model_route(app):
    page, response, _ = app
    response['data']['capabilities'] = {
        'agents': {'available': False, 'status': 'roles_configured_model_route_required',
                   'description': 'Six Deep Agents role definitions are installed. Select a hosted model endpoint.'}}
    response['data']['agent'] = {'available': False, 'roles': [
        'supervisor', 'discovery', 'semantics', 'architecture', 'validation', 'artifact'],
        'reason': 'No user-scoped model endpoint is selected.'}
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    card = page.locator('#capabilities .capability').filter(has=page.get_by_text('agents', exact=True))
    expect(card).to_contain_text('Model route required')
    expect(card).to_contain_text('Six Deep Agents role definitions are installed')
    expect(page.locator('#agent-roles')).to_contain_text('supervisor, discovery, semantics, architecture, validation, artifact')
    expect(page.locator('#agent-availability')).to_contain_text('No user-scoped model endpoint is selected')


def test_rejected_panel_script_is_not_loaded_by_index():
    markup = (STATIC / 'index.html').read_text(encoding='utf-8')
    assert '/static/capability-status.js' not in markup
    assert 'id="implementation-status"' not in markup
    assert markup.count('id="capabilities"') == 1
    assert re.findall(r'class="nav-item[^\"]*" type="button" data-page="([^"]+)"', markup) == NAV


@pytest.mark.parametrize('width,theme', [(1440, 'light'), (1024, 'dark'), (390, 'light')])
def test_versioned_inventory_count_dates_compact_cards_and_roadmap(app, width, theme):
    from workbench.capabilities import capability_inventory
    page, response, requested = app
    inventory = capability_inventory(True)
    response['health'].update(capability_inventory=inventory, capabilities={'deployment': False})
    response['data'].update(ready=True, package={'sha256': 'c' * 64}, workflows=[], counts={})
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
    page.locator('.navigation button[data-page=estate]').click()
    cards = page.locator('#capabilities .capability-scoped')
    expect(cards).to_have_count(len(inventory['items']))
    expect(page.locator('#capabilities .capability-overview strong')).to_have_text(str(len(inventory['items'])))
    expect(page.locator('#capabilities .capability-overview')).to_contain_text('16 implemented, 1 configured/unverified')
    expect(page.locator('#capability-assessed-at')).to_have_attribute('datetime', inventory['assessed_at'])
    expect(page.locator('#capability-assessed-at')).not_to_contain_text('28 October 2026')
    assert cards.evaluate_all('nodes => nodes.map(n => n.dataset.capabilityKey)') == [r['key'] for r in inventory['items']]
    assert cards.locator('details[open]').count() == 0
    assert cards.first.evaluate('node => getComputedStyle(node.querySelector("p")).fontSize') == '12px'
    for selector in ('#capabilities .capability-purpose', '#capability-roadmap p'):
        for paragraph_node in page.locator(selector).all():
            assert paragraph_node.evaluate('node => node.scrollHeight <= node.clientHeight + 1')
    assert cards.first.evaluate('node => getComputedStyle(node).borderTopWidth') == '0px'
    implemented = page.locator('#capabilities article[data-capability-key=xml_understanding]')
    assert 'capability-implemented' in implemented.locator('.status-pill').first.get_attribute('class')
    assert 'caution' not in implemented.locator('.status-pill').first.get_attribute('class')
    assert 'caution' in implemented.locator('.status-pill').nth(1).get_attribute('class')
    columns = page.locator('#capabilities').evaluate('node => getComputedStyle(node).gridTemplateColumns.split(" ").length')
    assert columns == (4 if width == 1440 else 3 if width == 1024 else 1)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    agents = page.locator('[data-capability-key=agents]')
    expect(agents).to_contain_text('Configured, unverified')
    agents.locator('summary').click()
    expect(agents).to_contain_text('not successful inference')
    auth = page.locator('[data-capability-key=authentication]')
    expect(auth).to_contain_text('Runtime: request verified')
    assert 'good' in auth.locator('.status-pill').nth(1).get_attribute('class')
    configured_runtime = page.locator('#capabilities article[data-capability-key=agents] .status-pill').nth(1).get_attribute('class')
    assert 'caution' in configured_runtime
    assert page.locator('#capabilities [data-capability-key=model_inference_authentication]').count() == 0
    page.locator('.navigation button[data-page=evolution]').click()
    expect(page.locator('#capability-roadmap [data-roadmap-key]')).to_have_count(len(inventory['roadmap']))
    expect(page.locator('#capability-roadmap')).to_contain_text('General behavior-preserving conversion')
    expect(page.locator('#capability-roadmap')).to_contain_text('Controlled customer release')
    for key in ('oracle_source_exploration', 'model_cost_estimates'):
        card = page.locator(f'#capabilities [data-capability-key="{key}"]')
        expect(card).to_be_visible()
        expect(card).to_contain_text('Runtime: request verified' if width != 390 else 'Runtime: local boundary only')
    for key in ('agent_handoffs', 'run_memory', 'model_routing', 'grounded_evaluation',
                'bounded_review_loop', 'cohort_evaluation', 'reproducible_agent_traces',
                'measured_efficiency', 'agent_governance'):
        expect(page.locator(f'#capability-roadmap [data-roadmap-key="{key}"]')).to_be_visible()
    assert not any('/api/generate' in path or '/api/deploy' in path for path in requested)


def test_inventory_is_implementation_not_runtime_or_authority():
    from datetime import datetime
    from workbench.capabilities import capability_inventory
    for hosted in (False, True):
        report = capability_inventory(hosted)
        assert datetime.fromisoformat(report['assessed_at']).tzinfo is not None
        assert report['version'] == 'capability-inventory-v1'
        assert len({r['key'] for r in report['items']}) == len(report['items']) == 17
        assert not report['generation_allowed'] and not report['deployment_allowed']
        assert all(not r['generation_allowed'] and not r['deployment_allowed'] for r in report['items'])
        assert not {r['key'] for r in report['items']} & {r['key'] for r in report['roadmap']}
        assert all(r['runtime_validation'] in {'not_verified', 'local_boundary_only'}
               for r in report['items'] if r['key'] != 'authentication')
        by_key = {row['key']: row for row in report['items']}
        for key in ('oracle_source_exploration', 'model_cost_estimates'):
            assert by_key[key]['runtime_validation'] == ('request_verified' if hosted else 'local_boundary_only')


def test_scoped_capability_display_preserves_general_and_release_boundaries(app):
    from workbench.workflow_artifacts import supported_workflows
    page, response, _ = app
    response['data']['capabilities'] = {
        'generation': {'available': True, 'status': 'bounded_reviewed_workflows',
                       'supported_workflows': supported_workflows(),
                       'reason': 'Four fingerprint-pinned bronze workflows only. Separate approval gates apply.'},
        'general_code_generation': False,
        'authentication': {'available': True, 'status': 'request_obo_authenticated',
                           'scope': 'hosted_request_proxy_obo',
                           'reason': 'Fresh request OBO is verified, not model inference.'},
        'model_inference_authentication': {'available': None, 'status': 'inference_unverified'},
        'hosted_application': {'available': True, 'status': 'app_hosted'},
        'deployment': False,
        'semantic_translation': {'available': False, 'status': 'not_implemented'},
    }
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    cards = page.locator('#capabilities')
    expect(cards.locator('.capability')).to_have_count(7)
    expect(cards.locator('.capability-overview strong')).to_have_text('3')
    generation = cards.locator('.capability').filter(has=page.get_by_text('generation', exact=True))
    expect(generation).to_contain_text('Available: four reviewed bronze workflows')
    expect(generation.locator('li')).to_have_count(4)
    for member in supported_workflows():
        expect(generation.locator('li').filter(has_text=member['workflow_name'])).to_have_count(1)
    expect(cards).to_contain_text('General approved-design code generation for arbitrary workflows is not implemented')
    auth = cards.locator('.capability').filter(has=page.get_by_text('Request/proxy authentication', exact=True))
    expect(auth).to_contain_text('Hosted request/OBO authenticated')
    expect(auth).not_to_contain_text('Unavailable')
    expect(cards).to_contain_text('Inference unverified')
    expect(cards).to_contain_text('App hosted')
    jobs = cards.locator('.capability').filter(has=page.get_by_text('Generated Jobs deployment', exact=True))
    expect(jobs).to_contain_text('Unavailable')
    expect(jobs).to_contain_text('no release or deployment gate is cleared')
    expect(page.locator('.navigation button[data-page=release]')).to_be_disabled()
    assert_original_ui_and_navigation(page)


@pytest.mark.parametrize('hosted', [False, True])
def test_live_health_scope_overrides_stale_boolean_overview_without_enabling_gates(app, hosted):
    from workbench.app import _scope_capabilities
    page, response, _ = app
    response['health']['capabilities'] = _scope_capabilities(hosted)
    response['data']['capabilities'] = {'authentication': False, 'generation': False, 'deployment': False}
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    cards = page.locator('#capabilities')
    expect(cards).to_contain_text('Available: four reviewed bronze workflows')
    expect(cards.locator('li')).to_have_count(4)
    expect(cards).to_contain_text('Hosted request/OBO authenticated' if hosted else 'Local loopback only, not authenticated')
    expect(cards).to_contain_text('App hosted' if hosted else 'Local app, not hosted')
    expect(cards).to_contain_text('Inference unverified')
    jobs = cards.locator('.capability').filter(has=page.get_by_text('Generated Jobs deployment', exact=True))
    expect(jobs).to_contain_text('Unavailable')
    expect(page.locator('#agent-submit')).to_be_disabled()
    expect(page.locator('.navigation button[data-page=release]')).to_be_disabled()


def test_health_scope_support_is_not_execution_authority():
    from workbench.app import _scope_capabilities
    from workbench.workflow_artifacts import supported_workflows
    for hosted in (False, True):
        caps = _scope_capabilities(hosted)
        assert caps['generation']['supported_workflows'] == supported_workflows()
        assert len(caps['generation']['supported_workflows']) == 4
        assert caps['generation']['scenario'] == 'bronze'
        assert caps['generation']['general_codegen_available'] is False
        assert caps['generation']['generation_allowed'] is False
        assert caps['generation']['deployment_allowed'] is False
        assert caps['general_code_generation'] is False and caps['deployment'] is False
        assert caps['model_inference_authentication']['inference_verified'] is False
        assert caps['authentication']['available'] is (True if hosted else None)


def test_workflow_inventory_selection_is_immediate_and_graph_click_keyboard_persist(app):
    page, response, _ = app
    response['data'] = {'ready': True, 'status': 'ready', 'package': {'sha256': 'c' * 64},
                        'counts': {}, 'workflows': []}
    response['nodes'] = [{'id': 'a' * 64, 'name': 'Workflow A', 'kind': 'workflow', 'scope': {}},
                         {'id': 'b' * 64, 'name': 'Workflow B', 'kind': 'workflow', 'scope': {}}]
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.locator('.navigation button[data-page=explorer]').click()
    first = page.locator('#object-list [data-object-id]').nth(0)
    second = page.locator('#object-list [data-object-id]').nth(1)
    response['hold_objects'] = True
    first.click()
    expect(first).to_have_attribute('aria-pressed', 'true')
    expect(first).to_have_class(re.compile(r'\bselected\b'))
    expect(second).to_have_attribute('aria-pressed', 'false')
    expect(first).to_have_attribute('aria-busy', 'true')
    expect(first).to_have_attribute('data-load-state', 'loading')
    expect(page.locator('#object-summary')).to_contain_text('availability is not yet known')
    expect(page.get_by_role('button', name='Retry selected object')).to_have_count(0)
    page.wait_for_timeout(30)
    route, model = response['held_objects'].pop()
    route.fulfill(json=model)
    expect(page.locator('#object-title')).to_have_text('Workflow A')
    expect(first).to_have_attribute('aria-busy', 'false')
    a = page.locator('#graph-area [data-node-id]').nth(0)
    b = page.locator('#graph-area [data-node-id]').nth(1)
    expect(a).to_have_attribute('aria-pressed', 'true')
    expect(a).to_have_attribute('data-assessment-status', 'neutral')
    expect(a.locator('.node-platform')).to_have_count(0)
    expect(a).not_to_contain_text('Scoped checks passed')
    expect(page.locator('#inspector-content')).to_contain_text('No completed agent or service assessment is recorded')
    expect(page.locator('#inspector-content')).not_to_contain_text('Unassessed; no service assessment supplied')
    b.click()
    expect(b).to_have_attribute('aria-pressed', 'true')
    expect(a).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#inspector-content')).to_contain_text('Workflow B')
    expect(page.locator('#tab-fields')).to_be_focused()
    expect(page.locator('#model-fields')).to_be_visible()
    page.locator('#tab-graph').click()
    a.focus(); a.press('Space')
    expect(a).to_have_attribute('aria-pressed', 'true')
    expect(b).to_have_attribute('aria-pressed', 'false')
    assert a.locator('rect').evaluate('node => getComputedStyle(node).strokeDasharray') == '5px, 2px'
    expect(first).to_have_attribute('aria-pressed', 'true')
    second.click()
    expect(second).to_have_attribute('aria-pressed', 'true')
    expect(first).to_have_attribute('aria-pressed', 'false')
    page.wait_for_timeout(30)
    route, model = response['held_objects'].pop()
    route.fulfill(json=model)
    expect(page.locator('#object-title')).to_have_text('Workflow B')


def test_failed_selected_object_retains_highlight_and_explicit_retry(app):
    page, response, _ = app
    response['data'] = {'ready': True, 'package': {'sha256': 'c' * 64}}
    response['nodes'] = [{'id': 'a' * 64, 'name': 'Workflow A', 'kind': 'workflow', 'scope': {}}]
    response['hold_objects'] = True
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.locator('.navigation button[data-page=explorer]').click()
    item = page.locator('#object-list [data-object-id]')
    item.click()
    page.wait_for_function("document.querySelector('#object-list [data-load-state=loading]') !== null")
    page.wait_for_timeout(30)
    route, _ = response['held_objects'].pop()
    route.fulfill(status=503, json={'detail': 'Controlled object load failure'})
    expect(item).to_have_attribute('aria-pressed', 'true')
    expect(item).to_have_attribute('data-load-state', 'failed')
    expect(page.locator('#object-title')).to_have_text('Object loading failed')
    retry = page.get_by_role('button', name='Retry selected object')
    expect(retry).to_be_visible()
    response['hold_objects'] = False
    retry.click()
    expect(page.locator('#object-title')).to_have_text('Workflow A')
    expect(item).to_have_attribute('data-load-state', 'loaded')


def test_object_renderer_explicit_show_all_returned_nodes_preserves_evidence(app):
    page, response, requested = app
    response['data'] = {'ready': True, 'package': {'sha256': 'c' * 64}}
    response['nodes'] = [{'id': f'{i:064x}', 'name': f'Workflow {i}', 'kind': 'workflow',
                          'scope': {}, 'evidence': {'locator': f'/WORKFLOW[{i}]'}} for i in range(114)]
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.locator('.navigation button[data-page=explorer]').click()
    page.locator('#object-list [data-object-id]').first.click()
    expect(page.locator('#graph-area g.node')).to_have_count(100)
    evidence = page.locator('#object-json').text_content()
    before = len(requested)
    page.get_by_role('button', name='Show all returned nodes and relationships', exact=True).click()
    expect(page.locator('#graph-area g.node')).to_have_count(114)
    expect(page.locator('#graph-area .graph-toolbar')).to_contain_text('Showing 114 of 114')
    expect(page.get_by_role('button', name='Zoom graph in', exact=True)).to_have_count(1)
    page.get_by_role('button', name='Zoom graph in', exact=True).click()
    assert page.locator('#object-json').text_content() == evidence
    assert requested[before:] == []
    assert set(page.locator('#graph-area g.node').evaluate_all('nodes => nodes.map(n => n.dataset.nodeId)')) == {n['id'] for n in response['nodes']}


@pytest.mark.parametrize('status,expected', [('amber', 'neutral'), ('red', 'red')])
def test_source_only_graph_is_neutral_but_canonical_errors_stay_red(app, status, expected):
    page, response, _ = app
    response['data'] = {'ready': True, 'package': {'sha256': 'c' * 64}}
    response['nodes'] = [{'id': 'a' * 64, 'name': 'Command-only workflow', 'kind': 'workflow',
                         'scope': {}, 'assessment': {'basis': 'structural_unverified',
                                                    'status': status, 'reason': 'Captured XML evidence'}}]
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.locator('.navigation button[data-page=explorer]').click()
    page.locator('#object-list [data-object-id]').click()
    graph_node = page.locator('#graph-area [data-node-id]')
    expect(graph_node).to_have_attribute('data-assessment-status', expected)
    if expected == 'neutral':
        expect(graph_node.locator('.node-platform')).to_have_count(0)
    else:
        expect(graph_node.locator('.node-platform')).to_have_text('Issue recorded')
    expect(graph_node).not_to_contain_text('Scoped checks passed')
    expect(page.locator('#agent-submit')).to_be_disabled()


@pytest.mark.parametrize('late_status', [200, 503])
def test_explorer_reselection_clears_context_error_and_ignores_old_response(app, late_status):
    page, response, requested = app
    a, b = 'a' * 64, 'b' * 64
    response['data'] = {'ready': True, 'package': {'sha256': 'c' * 64}}
    response['nodes'] = [{'id': node_id, 'name': name, 'kind': 'workflow', 'scope': {}}
                         for node_id, name in [(a, 'Old workflow'), (b, 'Current workflow')]]
    pending = []

    def context(route):
        if a in urlsplit(route.request.url).path:
            pending.append(route)
        else:
            route.fulfill(status=404, json={'detail': 'Unavailable current context'})

    page.route('**/api/workflows/*/migration-context?*', context)
    page.goto('https://capabilities.invalid/#estate', wait_until='domcontentloaded')
    page.locator('.navigation button[data-page=explorer]').click()
    page.locator('#object-list [data-object-id]').nth(0).click()
    page.locator('[data-view=english]').click()
    expect(page.locator('#explorer-package-context')).to_contain_text('Loading package invocation context')
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert len(pending) == 1
    page.locator('#object-list [data-object-id]').nth(1).click()
    expect(page.locator('#object-title')).to_have_text('Current workflow')
    expect(page.locator('#explorer-package-context')).to_contain_text('HTTP 404')
    before = page.locator('#explorer-package-context').text_content()
    pending.pop().fulfill(status=late_status, json={} if late_status == 200 else {'detail': 'Obsolete failure'})
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.locator('#explorer-package-context').text_content() == before
    # A previous failure must disappear as soon as the next selection starts.
    page.locator('#object-list [data-object-id]').nth(0).click()
    expect(page.locator('#explorer-package-context')).to_contain_text('Loading package invocation context')
    expect(page.locator('#english-content')).not_to_contain_text('HTTP 404')
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert len(pending) == 1
    pending.pop().fulfill(status=404, json={'detail': 'Unavailable selected context'})
    expect(page.locator('#explorer-package-context')).to_contain_text('HTTP 404')
    assert '/api/workflows/fixture-metadata' not in requested