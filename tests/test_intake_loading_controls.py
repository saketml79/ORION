"""Offline Chromium contracts for the real intake scripts and computed CSS.

Only explicit Volume-connection setup may POST, and that POST is fulfilled in
memory. Held project/overview reads never contact a server or write project data.
"""
import json
import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import (
    STATIC, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_C, browser, member, settle,
)


LOCAL_BUTTONS = (
    '#project-upload-form button', '#project-sample-form button', '#refresh-inputs',
)
VOLUME_CONTROLS = (
    '#connect-workspace-volume', '#workspace-volume-import', '#workspace-volume-back',
    '#workspace-volume-catalog', '#workspace-volume-schema', '#workspace-volume-volume',
    '#workspace-volume-consent',
)


@pytest.fixture
def intake(browser):
    context = browser.new_context(offline=True, service_workers='block',
                                  reduced_motion='no-preference')
    # Stale-completion guards, not cancellation, must own current loading state.
    context.add_init_script("""window.AbortController = class extends AbortController { abort() {} };
      window.intakeEvents = [];
      document.addEventListener('orion:intake-busy', () =>
        intakeEvents.push(document.getElementById('page-projects').dataset.intakeBusy));""")
    page = context.new_page()
    state = {'requests': [], 'unexpected': [], 'errors': [], 'pending': [],
             'hold': None, 'allow_connect': False, 'permission': True}
    page.on('pageerror', lambda error: state['errors'].append(str(error)))
    markup = (STATIC / 'index.html').read_text(encoding='utf-8')
    markup = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>',
                    '', markup, flags=re.S)
    assets = ['app.js', 'context-picker.js', 'workspace-access.js']
    markup = markup.replace('</head>', '<link rel="stylesheet" href="/static/app.css">' +
                            ''.join(f'<script src="/static/{asset}" defer></script>'
                                    for asset in assets) + '</head>')

    def fulfill(route, data, status=200):
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    def handler(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == '/':
            route.fulfill(content_type='text/html', body=markup)
            return
        if path in [f'/static/{asset}' for asset in [*assets, 'app.css']]:
            route.fulfill(path=str(STATIC / path.rsplit('/', 1)[1]),
                          content_type='text/css' if path.endswith('.css') else 'text/javascript')
            return
        entry = {'path': path, 'method': request.method, 'headers': request.all_headers()}
        state['requests'].append(entry)
        if request.method != 'GET':
            if path == '/api/databricks/volumes/connect' and state['allow_connect']:
                fulfill(route, {'connected': True} if state['permission'] else
                        {'detail': 'Volume permission denied'}, 200 if state['permission'] else 403)
            else:
                state['unexpected'].append(entry)
                route.abort()
            return
        if path == '/api/health':
            data = {'status': 'ok', 'authentication': 'databricks_obo'}
        elif path.startswith('/api/hosted/loading/'):
            route.fulfill(status=404, content_type='application/json', body='{"detail":"No active loading diagnostic in this fixture"}')
            return
        elif path == '/api/projects':
            data = {'items': [{'id': project, 'name': project[:1], 'ready_package_count': 1}
                              for project in [PROJECT_A, PROJECT_B]]}
        elif path in [f'/api/projects/{project}' for project in [PROJECT_A, PROJECT_B]]:
            project = path.rsplit('/', 1)[1]
            data = {'id': project, 'name': project[:1], 'ready_package_count': 1,
                    'packages': [member(PACKAGE_A if project == PROJECT_A else PACKAGE_C)], 'jobs': []}
        elif path == '/api/overview':
            digest = entry['headers'].get('x-workbench-package', '')
            data = {'ready': bool(digest), 'package': {'sha256': digest, 'name': f'{digest[:1]}.xml'},
                    'summary': f'Understanding for {digest}', 'counts': {'workflow': 1},
                    'workflows': [], 'agent': {'available': False}}
        elif path == '/api/input-packages':
            data = {'items': [{'name': 'sample.xml', 'size_bytes': 123}]}
        elif path == '/api/models/status':
            data = {'inference_ready': False}
        elif path == '/api/models/workspace':
            data = {'workspace': 'https://workspace.example.com', 'mode': 'obo'}
        elif path.startswith('/api/databricks/volumes/'):
            kind = path.rsplit('/', 1)[1]
            if kind in ['catalogs', 'schemas', 'volumes']:
                data = {'items': [{'name': {'catalogs': 'catalog', 'schemas': 'schema',
                                          'volumes': 'volume'}[kind]}]}
            elif kind == 'files':
                data = {'directory': 'child', 'volume_path': '/Volumes/catalog/schema/volume',
                        'items': [{'name': 'sample.xml', 'path': 'child/sample.xml',
                                   'is_directory': False, 'size_bytes': 123},
                                  {'name': 'nested', 'path': 'child/nested', 'is_directory': True}]}
            else:
                state['unexpected'].append(entry)
                route.abort()
                return
        else:
            state['unexpected'].append(entry)
            route.abort()
            return
        if state['hold'] and state['hold'](entry):
            state['pending'].append((route, data))
        else:
            fulfill(route, data)

    context.route('**/*', handler)
    page.goto('http://intake-loading.invalid/')
    expect(page.locator(f'#project-select option[value="{PROJECT_A}"]')).to_have_count(1)
    expect(page.locator('#workspace-volume-panel')).to_be_visible()
    yield page, state, fulfill
    context.close()
    assert not state['errors'], state['errors']
    assert not state['unexpected'], state['unexpected']


def choose_project(page, project=PROJECT_A):
    # Exercise the visible real context picker, not hidden native-select geometry.
    index = 1 if project == PROJECT_A else 2
    page.locator('#project-select-roller [role="option"]').nth(index).click()


def busy(page):
    expect(page.locator('#page-projects')).to_have_attribute('data-intake-busy', 'true')
    expect(page.locator('#estate-package-select')).to_have_attribute('aria-busy', 'true')
    for selector in (*LOCAL_BUTTONS, *VOLUME_CONTROLS):
        expect(page.locator(selector)).to_be_disabled()
    settle(page)
    assert page.evaluate('intakeEvents.at(-1)') == 'true'


def idle(page, connected=False):
    expect(page.locator('#page-projects')).to_have_attribute('data-intake-busy', 'false')
    expect(page.locator('#project-package')).to_have_attribute('aria-busy', 'false')
    expect(page.locator('#estate-package-select')).to_have_attribute('aria-busy', 'false')
    for selector in (*LOCAL_BUTTONS, '#connect-workspace-volume', '#workspace-volume-consent'):
        expect(page.locator(selector)).to_be_enabled()
    for selector in VOLUME_CONTROLS[1:6]:
        if connected:
            expect(page.locator(selector)).to_be_enabled()
        else:
            expect(page.locator(selector)).to_be_disabled()
    settle(page)
    assert page.evaluate('intakeEvents.at(-1)') == 'false'


def click_disabled_without_posts(page, state):
    before = len(state['requests'])
    # Native click() is a real disabled-control no-op. Playwright click would wait
    # for enabled controls, while dispatchEvent would bypass the browser contract.
    for selector in (*LOCAL_BUTTONS, *VOLUME_CONTROLS[:3]):
        page.locator(selector).evaluate('(element) => element.click()')
    settle(page)
    assert len(state['requests']) == before


def release(state, fulfill, *, status=200, index=0):
    route, data = state['pending'].pop(index)
    fulfill(route, data if status == 200 else {'detail': 'Fixture loading failure'}, status)


def connect_volume(page, state):
    state['allow_connect'] = True
    page.locator('#connect-workspace-volume').click()
    if not state['permission']:
        expect(page.locator('#workspace-volume-status')).to_contain_text('HTTP 403')
    else:
        page.locator('#workspace-volume-catalog').select_option('catalog')
        page.locator('#workspace-volume-schema').select_option('schema')
        page.locator('#workspace-volume-volume').select_option('volume')
        page.locator('#workspace-volume-files input').check()
        page.locator('#workspace-volume-consent').check()
        expect(page.locator('#workspace-volume-import')).to_be_enabled()
    state['allow_connect'] = False


@pytest.mark.parametrize('phase', ['detail', 'overview'])
@pytest.mark.parametrize('status', [200, 503])
def test_initial_loading_disables_intake_and_recovers_on_ready_or_error(intake, phase, status):
    page, state, fulfill = intake
    path = f'/api/projects/{PROJECT_A}' if phase == 'detail' else '/api/overview'
    state['hold'] = lambda entry: entry['path'] == path
    choose_project(page)
    busy(page)
    assert len(state['pending']) == 1
    if phase == 'detail':
        assert not any(entry['path'] == '/api/overview' for entry in state['requests'])
    else:
        expect(page.locator('#package-ready')).to_have_text('Loading package understanding')
    click_disabled_without_posts(page, state)
    state['hold'] = None
    release(state, fulfill, status=status)
    idle(page)
    if status == 200:
        expect(page.locator('#package-ready')).to_have_text('Package ready')
    elif phase == 'detail':
        expect(page.locator('#project-summary')).to_contain_text('Project packages could not be loaded')
        expect(page.locator('#project-package')).to_be_disabled()
    else:
        expect(page.locator('#package-ready')).to_contain_text('Loading interrupted')


def test_package_refresh_disables_explore_and_package_tabs_until_exact_overview_returns(intake):
    page, state, fulfill = intake
    choose_project(page)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('[data-go="explorer"]')).to_be_enabled()
    page.locator('.navigation [data-page="estate"]').click()
    state['hold'] = lambda entry: entry['path'] == '/api/overview'
    page.locator('#refresh').click()
    expect(page.locator('#package-understanding-progress')).to_be_visible()
    expect(page.locator('#package-understanding-progress')).to_contain_text('Loading selected XML understanding')
    for selector in ('[data-go="explorer"]', '#show-package-graph',
                     '.navigation [data-page="explorer"]', '.navigation [data-page="readiness"]',
                     '.navigation [data-page="proposals"]', '#refresh'):
        expect(page.locator(selector)).to_be_disabled()
    for page_name in ('explorer', 'readiness', 'proposals', 'evidence', 'validate', 'release'):
        expect(page.locator(f'#page-{page_name}')).to_have_attribute('inert', '')
        expect(page.locator(f'#page-{page_name}')).to_have_attribute('aria-busy', 'true')
    expect(page.locator('#agent-form')).to_have_attribute('inert', '')
    expect(page.locator('#workspace-model-panel')).not_to_have_attribute('inert', '')
    for export in page.locator('[data-export]').all():
        expect(export).to_have_attribute('aria-disabled', 'true')
    # Independent project/model setup remains usable while package reads are pending.
    expect(page.locator('.navigation [data-page="projects"]')).to_be_enabled()
    expect(page.locator('.navigation [data-page="agents"]')).to_be_enabled()
    assert len(state['pending']) == 1
    before = len(state['requests'])
    page.locator('[data-go="explorer"]').evaluate('(button) => button.click()')
    page.locator('#show-package-graph').evaluate('(button) => button.click()')
    assert len(state['requests']) == before
    state['hold'] = None
    release(state, fulfill)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('[data-go="explorer"]')).to_be_enabled()
    expect(page.locator('#show-package-graph')).to_be_enabled()
    expect(page.locator('#refresh')).to_be_enabled()
    for page_name in ('explorer', 'readiness', 'proposals', 'evidence', 'validate', 'release'):
        expect(page.locator(f'#page-{page_name}')).not_to_have_attribute('inert', '')
        expect(page.locator(f'#page-{page_name}')).to_have_attribute('aria-busy', 'false')
    for export in page.locator('[data-export]').all():
        expect(export).to_have_attribute('aria-disabled', 'false')


@pytest.mark.parametrize('phase', ['detail', 'overview'])
@pytest.mark.parametrize('permission', [True, False])
@pytest.mark.parametrize('status', [200, 503])
def test_connected_or_permission_denied_volume_stays_gated_during_refresh(
        intake, phase, permission, status):
    page, state, fulfill = intake
    choose_project(page)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    state['permission'] = permission
    connect_volume(page, state)
    idle(page, connected=permission)
    path = f'/api/projects/{PROJECT_A}' if phase == 'detail' else '/api/overview'
    state['hold'] = lambda entry: entry['path'] == path
    page.locator('#refresh-projects' if phase == 'detail' else '#refresh').click()
    busy(page)
    click_disabled_without_posts(page, state)
    state['hold'] = None
    release(state, fulfill, status=status)
    idle(page, connected=permission)


@pytest.mark.parametrize('motion', ['no-preference', 'reduce'])
@pytest.mark.parametrize('phase', ['detail', 'overview'])
def test_whole_package_roller_computed_animation_respects_motion(intake, motion, phase):
    page, state, fulfill = intake
    page.emulate_media(reduced_motion=motion)
    path = f'/api/projects/{PROJECT_A}' if phase == 'detail' else '/api/overview'
    state['hold'] = lambda entry: entry['path'] == path
    choose_project(page)
    busy(page)
    roller = page.locator('#project-package-roller')
    expect(roller).to_have_attribute('aria-busy', 'true')
    expected = 'package-loading-pulse' if motion == 'no-preference' else 'none'
    assert roller.evaluate('(element) => getComputedStyle(element).animationName') == expected
    # The roller itself, not just its selected row, owns the loading animation.
    assert roller.locator('[role="option"]').evaluate_all(
        "elements => elements.every(element => getComputedStyle(element).animationName === 'none')")
    state['hold'] = None
    release(state, fulfill)
    idle(page)
    assert roller.evaluate('(element) => getComputedStyle(element).animationName') == 'none'


@pytest.mark.parametrize('late_status', [200, 503])
def test_stale_project_completion_cannot_unlock_current_loading(intake, late_status):
    page, state, fulfill = intake
    state['hold'] = lambda entry: entry['path'].startswith('/api/projects/')
    choose_project(page)
    busy(page)
    choose_project(page, PROJECT_B)
    busy(page)
    assert len(state['pending']) == 2
    events_before = page.evaluate('intakeEvents.length')
    release(state, fulfill, status=late_status)
    settle(page)
    busy(page)
    expect(page.locator('#project-select')).to_have_value(PROJECT_B)
    assert page.evaluate('intakeEvents.length') == events_before
    assert not any(entry['path'] == '/api/overview' for entry in state['requests'])
    click_disabled_without_posts(page, state)
    state['hold'] = None
    release(state, fulfill)
    idle(page)
    expect(page.locator('#package-name')).to_have_text('c.xml')