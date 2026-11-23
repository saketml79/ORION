"""Actual native dialog and Estate hooks in offline Chromium, GET only."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import browser, STATIC, PROJECT_A, PACKAGE_A
from test_navigation_prerequisites import navigation, tab, choose_project


@pytest.fixture
def inventory_ui(browser):
    context = browser.new_context(offline=True)
    # Late responses deliberately ignore transport abort, guards must win.
    context.add_init_script('window.AbortController = class extends AbortController { abort() {} };')
    page = context.new_page()
    state = {'requests': [], 'pending': [], 'hold': False, 'fail': False, 'errors': []}
    page.on('pageerror', lambda e: state['errors'].append(str(e)))
    markup = '''<!doctype html><html data-theme="light"><head><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/inventory-dialog.css"><script src="/static/scoped-reads.js" defer></script><script src="/static/inventory-dialog.js" defer></script></head><body><main><button id="open">Targets 307</button><button id="refresh">Refresh</button></main><div style="height:2000px"></div></body></html>'''
    def payload(kind, identifier=''):
        return {'id': identifier or kind + '-0', 'name': 'Same name', 'kind': kind,
                'scope': {'repository': 'R', 'folder': 'A' if not identifier.endswith('-1') else 'B'},
                'attributes': {'DBDNAME': 'DB_A'}, 'counts': {'fields': 237, 'properties': 0, 'children': 0, 'usage': 0}}
    def fulfill(route, value):
        route.fulfill(content_type='application/json', body=json.dumps(value))
    def handler(route):
        req = route.request; url = urlsplit(req.url); path = url.path
        if path == '/':
            route.fulfill(content_type='text/html', body=markup); return
        if path.startswith('/static/'):
            route.fulfill(path=str(STATIC / path.rsplit('/', 1)[-1])); return
        assert req.method == 'GET'
        query = parse_qs(url.query); state['requests'].append((path, query, req.all_headers()))
        if state['fail']:
            route.fulfill(status=503, content_type='application/json', body='{"detail":"unavailable"}'); return
        offset = int(query.get('offset', [0])[0])
        if path == '/api/objects':
            kind = query['kind'][0]; total = 307 if kind == 'target' else 1
            if query.get('q') == ['missing']: total = 0
            value = {'items': [payload(kind, f'{kind}-{i}') for i in range(offset, min(offset + 100, total))],
                     'total': total, 'offset': offset, 'limit': 100, 'input_version': 'v1'}
        else:
            kind, identifier = path.split('/')[-2:]; section = query['section'][0]
            total = 237 if section == 'fields' else 0
            value = {'node': payload(kind, identifier), 'section': section, 'total': total, 'offset': offset, 'input_version': 'v1',
                     'pins': {'package_hash': PACKAGE_A}, 'counts': {},
                     'suggested_equivalent': {'label': 'Suggested Databricks equivalent', 'suggestion': 'Reviewed Delta table',
                                              'rationale': 'Requires contract review', 'limitations': 'Not generated or verified', 'source_evidence': {'id': identifier}},
                     'items': [{'attributes': {'NAME': f'column-{i}', 'DATATYPE': 'decimal', 'NULLABLE': 'NOTNULL'}} for i in range(offset, min(offset + 100, total))]}
        if state['hold']: state['pending'].append((route, value))
        else: fulfill(route, value)
    page.route('**/*', handler)
    page.goto('http://inventory.invalid/')
    page.evaluate('''() => {
      orionInventory.setContext({projectId: '11111111111111111111111111111111', packageHash: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ready: true});
      document.getElementById('open').onclick = () => orionInventory.open('target', 307, document.getElementById('open'));
    }''')
    yield page, state, fulfill
    assert not state['errors']
    context.close()


def test_all_307_auto_metadata_then_selected_full_fields_and_accessibility(inventory_ui):
    page, state, _ = inventory_ui
    page.locator('#open').click()
    expect(page.locator('.inventory-record')).to_have_count(307)
    assert [q['offset'] for p, q, h in state['requests'] if p == '/api/objects'] == [['0'], ['100'], ['200'], ['300']]
    assert all(h.get('x-workbench-package') == PACKAGE_A for p, q, h in state['requests'])
    assert not any('/api/inventory/' in p for p, q, h in state['requests'])
    expect(page.locator('#inventory-dialog-title')).to_have_text('Targets 307')
    page.locator('.inventory-record').first.click()
    expect(page.locator('#inventory-content')).to_contain_text('Suggested Databricks equivalent')
    page.locator('.inventory-record').first.click()
    assert sum('/api/inventory/' in p for p, q, h in state['requests']) == 1
    page.get_by_role('tab', name='Columns / ports').click()
    expect(page.locator('#inventory-content')).to_contain_text('100 of 237')
    page.get_by_role('button', name='Load remaining 137').click()
    expect(page.locator('#inventory-content')).to_contain_text('237 of 237')
    expect(page.locator('#inventory-content details')).to_have_count(237)
    page.locator('#inventory-content details').last.locator('summary').click()
    expect(page.locator('#inventory-content details').last).to_contain_text('column-236')
    expect(page.locator('#inventory-content details').last).to_contain_text('NOTNULL')
    assert page.evaluate('getComputedStyle(document.documentElement).overflow') == 'hidden'
    assert page.evaluate("document.querySelector('#inventory-dialog').matches(':modal')")
    page.keyboard.press('Escape')
    expect(page.locator('#inventory-dialog')).not_to_be_visible()
    expect(page.locator('#open')).to_be_focused()
    assert page.evaluate('getComputedStyle(document.documentElement).overflow') != 'hidden'


@pytest.mark.parametrize('kind', ['repository', 'folder', 'mapping', 'mapplet', 'workflow', 'worklet', 'session', 'source'])
def test_every_other_kind_is_type_specific_and_no_detail_until_selection(inventory_ui, kind):
    page, state, _ = inventory_ui
    page.evaluate('(kind) => orionInventory.open(kind, 1, document.getElementById("open"))', kind)
    expect(page.locator('.inventory-record')).to_have_count(1)
    assert state['requests'][0][1]['kind'] == [kind]
    assert len(state['requests']) == 1
    page.locator('.inventory-record').click()
    expect(page.locator('#inventory-content')).to_contain_text('Suggested Databricks equivalent')
    assert state['requests'][-1][0].startswith('/api/inventory/' + kind + '/')


def test_failed_read_is_unconfirmed_not_zero_and_search_empty_is_explicit(inventory_ui):
    page, state, _ = inventory_ui
    state['fail'] = True; page.locator('#open').click()
    expect(page.locator('.inventory-status')).to_contain_text('HTTP 503')
    expect(page.locator('#inventory-dialog-title')).to_have_text('Targets · Unconfirmed')
    state['fail'] = False
    page.get_by_role('searchbox').fill('missing'); page.get_by_role('button', name='Search', exact=True).click()
    expect(page.locator('#inventory-dialog-title')).to_have_text('Targets 0')
    expect(page.locator('.inventory-list')).to_contain_text('No matching records')


def test_explicit_pages_then_show_all_and_keyboard_tabs(inventory_ui):
    page, state, _ = inventory_ui
    page.locator('#open').click(); expect(page.locator('.inventory-record')).to_have_count(307)
    expect(page.get_by_role('button', name='Previous', exact=True)).to_be_disabled()
    page.get_by_role('button', name='100 per page', exact=True).click()
    expect(page.locator('.inventory-record')).to_have_count(100)
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('.inventory-status')).to_contain_text('offset 100')
    assert state['requests'][-1][1]['offset'] == ['100']
    page.get_by_role('button', name='Previous', exact=True).click()
    expect(page.locator('.inventory-status')).to_contain_text('offset 0')
    page.get_by_role('button', name='Show all metadata', exact=True).click()
    expect(page.locator('.inventory-record')).to_have_count(307)
    page.get_by_role('searchbox').fill('Same'); page.get_by_role('button', name='Search', exact=True).click()
    expect(page.locator('.inventory-record')).to_have_count(307)
    page.locator('.inventory-record').first.click()
    expect(page.get_by_role('tab', name='Overview')).to_have_attribute('aria-selected', 'true')
    page.get_by_role('tab', name='Overview').focus(); page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='Columns / ports')).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_role('tab', name='Columns / ports')).to_be_focused()
    expect(page.locator('#inventory-content')).to_contain_text('100 of 237')
    page.keyboard.press('Home')
    expect(page.get_by_role('tab', name='Overview')).to_have_attribute('aria-selected', 'true')


def test_late_selected_detail_cannot_replace_new_selection_or_scope(inventory_ui):
    page, state, fulfill = inventory_ui
    page.locator('#open').click(); expect(page.locator('.inventory-record')).to_have_count(307)
    state['hold'] = True
    page.locator('.inventory-record').first.click()
    expect(page.locator('.inventory-detail h3')).to_have_text('Same name')
    page.locator('.inventory-status').text_content(); assert state['pending']
    first = state['pending'].pop(0)
    state['hold'] = False
    page.locator('.inventory-record').nth(1).click()
    expect(page.locator('#inventory-content')).to_contain_text('Suggested Databricks equivalent')
    fulfill(*first)
    page.locator('#inventory-content summary').first.click()
    expect(page.locator('#inventory-content details').first).to_contain_text('target-1')
    expect(page.locator('#inventory-content details').first).not_to_contain_text('target-0')
    page.evaluate("orionInventory.setContext({projectId:'other',packageHash:'other',ready:true})")
    expect(page.locator('#inventory-dialog')).not_to_be_visible()
    expect(page.locator('.inventory-detail')).to_be_empty()


@pytest.mark.parametrize('action', ['close', 'context', 'refresh'])
def test_late_list_after_close_or_scope_change_never_appends(inventory_ui, action):
    page, state, fulfill = inventory_ui
    state['hold'] = True; page.locator('#open').click()
    expect(page.locator('#inventory-dialog')).to_be_visible()
    page.wait_for_function('document.querySelector("#inventory-dialog-title").textContent.includes("Loading")')
    # Browser request interception is pumped by a deterministic DOM operation.
    page.locator('.inventory-status').text_content()
    assert state['pending']
    if action == 'close': page.get_by_role('button', name='Close inventory').click()
    elif action == 'refresh': page.evaluate('document.getElementById("refresh").click()')
    else: page.evaluate("orionInventory.setContext({projectId:'other',packageHash:'other',ready:true})")
    for route, value in state['pending']: fulfill(route, value)
    expect(page.locator('#inventory-dialog')).not_to_be_visible()
    expect(page.locator('.inventory-record')).to_have_count(0)
    assert len(state['requests']) == 1


@pytest.mark.parametrize('width,height,theme', [(1440, 900, 'light'), (1024, 600, 'dark'), (390, 740, 'light')])
def test_responsive_native_dialog_independent_scrolls(inventory_ui, width, height, theme):
    page, state, _ = inventory_ui
    page.set_viewport_size({'width': width, 'height': height})
    page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
    page.locator('#open').click(); expect(page.locator('.inventory-record')).to_have_count(307)
    bounds = page.locator('#inventory-dialog').bounding_box()
    assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['width'] <= width and bounds['height'] <= height
    assert page.locator('.inventory-close').evaluate('n => getComputedStyle(n).borderTopWidth') == '0px'
    assert page.locator('.inventory-list').evaluate('n => n.scrollHeight > n.clientHeight')
    assert page.locator('.inventory-detail').evaluate('n => getComputedStyle(n).overflowY') == 'auto'


def test_actual_estate_hooks_do_not_turn_global_facts_into_inventory(navigation):
    page, state, _ = navigation(fragment='estate', legacy=True)
    expect(page.locator('#metrics [data-inventory-kind=workflow]')).to_have_attribute('role', 'button')
    expect(page.locator('#overview-summary [data-inventory-kind=workflow]')).to_have_attribute('aria-label', 'Browse 1 workflow')
    expect(page.locator('#overview-summary .inventory-total[data-inventory-kind]')).to_have_count(0)
    expect(page.locator('[data-inventory-kind]:not(#page-estate [data-inventory-kind])')).to_have_count(0)
    assert not any('/api/inventory/' in r['path'] for r in state['requests'])