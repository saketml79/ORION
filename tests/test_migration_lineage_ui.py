"""Actual SVG/markup/assets with offline canonical-only responses."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs

import pytest
from playwright.sync_api import expect

from test_project_package_autoload import browser, STATIC
from workbench.migration_lineage import Lineage
from workbench.tests.test_migration_lineage import index_for
from workbench.tests.test_package_context import parsed


@pytest.fixture
def lineage_ui(browser, tmp_path):
    model, ids, _ = parsed(tmp_path)
    lineage = Lineage(index_for(model, tmp_path / 'index'))
    lineage.selected = [ids['Parent'], ids['Child']]
    context = browser.new_context(offline=True, service_workers='block')
    page = context.new_page()
    errors, requests, pending = [], [], []
    state = {'hold': False, 'wrong': False, 'saved': None}
    page.on('pageerror', lambda e: errors.append(str(e)))
    markup = (STATIC / 'index.html').read_text(encoding='utf-8')
    markup = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>', '', markup, flags=re.S)
    markup = markup.replace('id="page-readiness" class="page" aria-labelledby="readiness-heading" hidden',
                            'id="page-readiness" class="page" aria-labelledby="readiness-heading"')
    markup = markup.replace('</head>', '<link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/migration-lineage.css"><script src="/static/migration-lineage.js" defer></script></head>')
    def handler(route):
        url = urlsplit(route.request.url)
        if url.path == '/':
            route.fulfill(content_type='text/html', body=markup)
        elif url.path.startswith('/static/'):
            route.fulfill(path=str(STATIC / url.path.rsplit('/', 1)[1]))
        elif url.path == '/api/readiness/lineage':
            assert route.request.method == 'GET'
            requests.append(route.request)
            query = parse_qs(url.query)
            result = lineage.response('another-project' if state['wrong'] else '__legacy__',
                wid=query.get('workflow_id', [None])[-1], root=query.get('root_id', [None])[-1],
                family=query.get('family', ['nodes'])[-1], offset=int(query.get('offset', ['0'])[-1]),
                limit=int(query.get('limit', ['100'])[-1]), expected=query.get('input_version', [None])[-1],
                saved=state['saved'], example=query.get('example', ['false'])[-1] == 'true')
            if state['hold']:
                pending.append((route, result))
            else:
                route.fulfill(content_type='application/json', body=json.dumps(result))
        else:
            pytest.fail('Unexpected request ' + url.path)
    page.route('**/*', handler)
    page.goto('http://lineage.test/')
    yield page, state, requests, pending, ids
    context.close()
    assert errors == []


@pytest.mark.parametrize('width', [1440, 390])
def test_interactive_canonical_trace_zoom_paging_and_no_mutation(lineage_ui, width):
    page, _, requests, _, ids = lineage_ui
    page.set_viewport_size({'width': width, 'height': 900})
    page.locator('#lineage-load').click()
    expect(page.locator('#lineage-status')).to_contain_text('Captured XML lineage')
    expect(page.locator('#lineage-graph')).to_contain_text('Not generated')
    expect(page.locator('#lineage-calls')).to_contain_text('nowait / literal candidate only')
    mapping_details = page.locator('#lineage-relations details').filter(has_text='Captured mappings and target definitions')
    mapping_details.locator('summary').click()
    expect(mapping_details).to_contain_text('No mapping definitions returned for this workflow')
    before = page.locator('#lineage-graph').get_attribute('viewBox')
    page.locator('#lineage-in').click()
    assert page.locator('#lineage-graph').get_attribute('viewBox') != before
    page.locator('#lineage-graph').focus(); page.keyboard.press('0')
    assert page.locator('#lineage-graph').get_attribute('viewBox') == before
    page.locator('#lineage-play').click()
    expect(page.locator('#migration-lineage')).to_have_class(re.compile('lineage-animate'))
    assert page.locator('#lineage-graph .trace-selected').count() > 0
    assert page.evaluate("[...document.querySelectorAll('#migration-lineage button')].every(b => !b.getAnimations().length)")
    page.locator('#lineage-play').click()
    expect(page.locator('#migration-lineage')).not_to_have_class(re.compile('lineage-animate'))
    page.locator('#lineage-calls [role=button]').last.click()
    expect(page.locator('#lineage-workflow')).to_have_value(ids['Child'])
    child_mappings = page.locator('#lineage-relations details').filter(has_text='Captured mappings and target definitions')
    child_mappings.locator('summary').click()
    expect(child_mappings).to_contain_text('Read')
    expect(child_mappings).to_contain_text('No target definitions returned')
    node = page.locator('#lineage-graph [role=button]').filter(has=page.locator('text', has_text='read')).first
    # Keyboard follows the same canonical expansion path as click.
    node.focus(); page.keyboard.press('Enter')
    expect(page.locator('#lineage-inspector')).to_contain_text('Filtered to the expanded object')
    page.locator('#lineage-family').select_option('properties')
    expect(page.locator('#lineage-inspector h3')).to_contain_text('Linked properties')
    page.locator('#lineage-home').click()
    expect(page.locator('#lineage-inspector')).to_contain_text('Entire invocation cohort')
    assert all(r.method == 'GET' for r in requests)
    assert not page.locator('dialog[open], [inert], [aria-modal=true]').count()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_reduced_motion_stops_existing_animation(lineage_ui):
    page, _, _, _, _ = lineage_ui
    page.locator('#lineage-load').click()
    expect(page.locator('#lineage-play')).to_be_enabled()
    page.locator('#lineage-play').click()
    page.emulate_media(reduced_motion='reduce')
    expect(page.locator('#lineage-play')).to_be_disabled()
    expect(page.locator('#migration-lineage')).not_to_have_class(re.compile('lineage-animate'))
    assert page.evaluate("[...document.querySelectorAll('#lineage-graph path')].every(e => !e.getAnimations().length)")


@pytest.mark.parametrize('case', ['wrong', 'late'])
def test_scope_mismatch_or_late_navigation_clears_all_source_graphs(lineage_ui, case):
    page, state, _, pending, _ = lineage_ui
    state['wrong'] = case == 'wrong'
    state['hold'] = case == 'late'
    page.locator('#lineage-load').click()
    if case == 'late':
        expect(page.locator('#lineage-status')).to_contain_text('Reading pinned')
        page.evaluate("document.getElementById('page-readiness').hidden = true")
        page.wait_for_function("document.getElementById('lineage-status').textContent.startsWith('Selection changed')")
        for route, result in pending:
            route.fulfill(content_type='application/json', body=json.dumps(result))
    else:
        expect(page.locator('#lineage-status')).to_contain_text('scope mismatch')
    expect(page.locator('#lineage-graph')).to_be_empty()
    expect(page.locator('#lineage-calls')).to_be_empty()


def test_missing_reference_is_red_not_unassessed_gray(lineage_ui):
    page, _, _, _, _ = lineage_ui
    page.locator('#lineage-load').click()
    expect(page.locator('#lineage-status')).to_contain_text('Captured XML lineage')
    page.locator('#lineage-family').select_option('obligations')
    assert '#dc2626' == page.evaluate("getComputedStyle(document.querySelector('.lineage-legend .stage-missing')).getPropertyValue('--stage').trim()")
    assert page.locator('.lineage-legend .stage-missing').inner_text().startswith('Red:')
    assert page.locator('#lineage-graph .stage-generated, #lineage-graph .stage-verified').count() == 0


@pytest.mark.parametrize('reduced', [False, True])
def test_explicit_trace_unmapped_accessible_details_clear_and_reference(lineage_ui, reduced):
    page, _, requests, _, ids = lineage_ui
    page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
    page.locator('#lineage-load').click()
    page.locator('#lineage-workflow').select_option(ids['Child'])
    page.locator('#lineage-trace').click()
    expect(page.locator('#lineage-trace-status')).to_contain_text('Execution result unknown')
    expect(page.locator('#lineage-graph')).to_contain_text('Not mapped')
    expect(page.locator('#lineage-trace-text')).to_contain_text('ReadSession')
    assert page.locator('#lineage-graph .lineage-correspondence').count() == 0
    row = page.locator('#lineage-graph [role=button]').last
    row.focus(); page.keyboard.press('Enter')
    expect(page.locator('#lineage-change-detail')).to_contain_text('Not mapped')
    if reduced:
        expect(page.locator('#lineage-play')).to_be_disabled()
        assert page.evaluate("[...document.querySelectorAll('#lineage-graph path')].every(e => !e.getAnimations().length)")
    page.locator('#lineage-example').check()
    expect(page.locator('#lineage-trace-status')).to_contain_text('Historical MOCK proof')
    expect(page.locator('#lineage-trace-status')).to_contain_text('not a live status feed')
    expect(page.get_by_role('link', name='Open runnable VYVX MOCK Job (Databricks sign-in required)')).to_have_attribute('href', 'https://adb-1866518241053589.9.azuredatabricks.net/jobs/878190924157330')
    expect(page.locator('#lineage-trace-text')).to_contain_text('11 tasks, no inferred source match')
    expect(page.locator('#lineage-graph')).to_contain_text('AE2E_BILL_TAMER_PREP')
    assert page.locator('#lineage-graph .lineage-correspondence').count() == 4
    page.locator('#lineage-graph [role=button]').last.click()
    expect(page.locator('#lineage-change-detail')).to_contain_text('VENDOR_TERM to ASR_TERM')
    expect(page.locator('#lineage-change-detail')).to_contain_text('No selected-user correlation')
    page.locator('#lineage-example').uncheck()
    expect(page.locator('#lineage-graph')).to_contain_text('Not mapped')
    expect(page.locator('#lineage-graph')).not_to_contain_text('VYVX')
    page.locator('#lineage-clear').click()
    expect(page.locator('#lineage-graph')).to_be_empty()
    expect(page.locator('#migration-lineage')).not_to_have_class(re.compile('lineage-animate'))
    expect(page.locator('#lineage-hierarchy')).to_contain_text('read')
    assert all(r.method == 'GET' for r in requests)


def test_current_explicit_saved_coverage_visual_and_no_runtime_claim(lineage_ui):
    from workbench.tests.test_migration_lineage import saved_trace
    page, state, _, _, ids = lineage_ui
    page.locator('#lineage-load').click()
    response = page.evaluate("async () => (await fetch('/api/readiness/lineage')).json()")
    child = page.evaluate(f"async () => (await fetch('/api/readiness/lineage?workflow_id={ids['Child']}')).json()")
    state['saved'] = saved_trace(response['scope'], ids['Child'], child['selected']['task_instance_ids'][0])
    page.locator('#lineage-workflow').select_option(ids['Child'])
    page.locator('#lineage-trace').click()
    expect(page.locator('#lineage-graph')).to_contain_text('Authored task')
    assert page.locator('#lineage-graph .lineage-correspondence').count() == 1
    page.locator('#lineage-graph [role=button]').last.click()
    expect(page.locator('#lineage-change-detail')).to_contain_text('notebooks/authored.ipynb')
    expect(page.locator('#lineage-trace-status')).to_contain_text('Execution result unknown')
    expect(page.locator('#migration-lineage')).to_have_class(re.compile('lineage-animate'))
    page.locator('#lineage-play').click()
    expect(page.locator('#migration-lineage')).not_to_have_class(re.compile('lineage-animate'))