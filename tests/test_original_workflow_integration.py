"""Real source button, main navigation owner and Explorer, offline HTTP only.

Saved designs are fixtures, not a model or runtime parity result. Every current
production script and stylesheet is served by the strict navigation fixture.
"""
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from playwright.sync_api import expect

from test_navigation_prerequisites import navigation, tab, choose_project
from test_project_package_autoload import (
    browser, settle, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, PACKAGE_C,
)
from workbench.tests.test_redesign_ui import design_record


WORKFLOWS = [str(n) * 64 for n in (4, 5, 6)]


@pytest.fixture
def source_ui(navigation):
    def start(standalone=False):
        page, state, fulfill = navigation(fragment='projects', legacy=standalone)
        state['workflows'] = [{'id': wid, 'name': f'Source workflow {i + 1}'}
                              for i, wid in enumerate(WORKFLOWS)]
        state['objects'] = [{**row, 'kind': 'workflow'} for row in state['workflows']]
        reads = []

        def scoped(route):
            request = route.request
            path = urlsplit(request.url).path
            query = parse_qs(urlsplit(request.url).query)
            operation = None
            if path == '/api/hosted/operations' and request.method == 'POST':
                envelope = request.post_data_json
                if envelope['method'] != 'GET':
                    route.fallback()
                    return
                operation, path = envelope['id'], envelope['path']
                query = parse_qs(envelope.get('query', ''))
            if not (path.startswith('/api/object/') or path.startswith('/api/redesigns')
                    or path.endswith('/migration-context')):
                route.fallback()
                return
            reads.append({'path': path, 'headers': request.all_headers(),
                          'method': 'GET' if operation else request.method})
            package = PACKAGE_C if standalone else PACKAGE_A
            scenario = query.get('scenario', ['original'])[0]
            workflow = query.get('workflow_id', [WORKFLOWS[0]])[0]
            if path.startswith('/api/object/'):
                wid = unquote(path.removeprefix('/api/object/'))
                root = {'id': wid, 'kind': 'workflow', 'name': 'Exact original ' + wid[0]}
                child = {'id': 'source/' + wid, 'kind': 'source', 'name': 'Original source',
                         'fields': [{'attributes': {'NAME': 'EXACT_SOURCE_FIELD'}}]}
                result = {'node': root, 'children': [], 'graph': {
                    'nodes': [root, child], 'edges': [{'source': wid, 'target': child['id'], 'kind': 'contains'}]}}
            elif path.endswith('/migration-context'):
                wid = unquote(path.split('/')[3])
                result = {'workflow_id': wid, 'package_hash': package,
                          'cohort': {'items': [{'workflow_id': row['id'], 'name': row['name']}
                                              for row in state['workflows']],
                                     'total': 3, 'has_more': False},
                          'invocations': {'items': [], 'has_more': False}}
            elif path == '/api/redesigns':
                record = design_record(id=workflow + '-' + scenario, workflow_id=workflow,
                                       package_hash=package, scenario=scenario)
                result = {'items': [record], 'total': 1, 'offset': 0, 'limit': 50}
            else:
                # Like the real API, immutable record identity pins detail scope;
                # detail requests do not repeat list workflow/scenario filters.
                identifier = path.removeprefix('/api/redesigns/')
                workflow, scenario = identifier.rsplit('-', 1)
                record = design_record(id=identifier, workflow_id=workflow,
                                       package_hash=package, scenario=scenario)
                result = {**record, 'latest_version': record['version'], 'draft_jobs': []}
            fulfill(route, {'operation_id': operation, 'status': 'completed', 'result': result}
                    if operation else result)

        page.route('**/api/**', scoped)
        if standalone:
            page.locator('#refresh').click()
        else:
            choose_project(page)
        expect(page.locator('#package-ready')).to_have_text('Package ready')
        expect(page.locator('#agent-workflow option[value="' + WORKFLOWS[2] + '"]')).to_have_count(1)
        tab(page, 'proposals').click()
        expect(page.locator('#redesign-detail h3').first).to_have_text('Actual submitted outline')
        settle(page)
        return page, state, reads

    return start


@pytest.mark.parametrize('standalone', [False, True])
@pytest.mark.parametrize('workflow', WORKFLOWS)
@pytest.mark.parametrize('mode', ['workflow', 'cohort'])
@pytest.mark.parametrize('scenario', ['original', 'bronze'])
def test_source_click_opens_exact_original_graph_in_current_scope(source_ui, standalone, workflow, mode, scenario):
    page, state, reads = source_ui(standalone)
    page.locator('#scenario').select_option(scenario)
    page.locator('#redesign-workflow').select_option(workflow)
    page.locator('#source-target-scope').select_option(mode)
    expect(page.locator('#redesign-open-original')).to_be_enabled()
    page.locator('#redesign-open-original').click()
    expect(page.locator('#page-explorer')).to_be_visible()
    expect(page.locator('#object-title')).to_have_text('Exact original ' + workflow[0])
    expect(page.locator('#tab-graph')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#graph-area .graph-svg')).to_have_count(1)
    assert json.loads(page.locator('#object-json').text_content())['node']['id'] == workflow
    expect(page.locator('#agent-workflow')).to_have_value(workflow)
    expect(page.locator('#agent-scenario')).to_have_value(scenario)
    expect(page.locator('#source-target-scope')).to_have_value(mode)
    exact = [read for read in reads if read['path'].startswith('/api/object/')]
    assert len(exact) == 1
    assert unquote(exact[0]['path']) == '/api/object/' + workflow
    if standalone:
        assert 'x-workbench-project' not in exact[0]['headers']
        assert 'x-workbench-package' not in exact[0]['headers']
        expect(page.locator('#page-proposals')).to_have_attribute('data-package-hash', PACKAGE_C)
    else:
        assert exact[0]['headers']['x-workbench-project'] == PROJECT_A
        assert exact[0]['headers']['x-workbench-package'] == PACKAGE_A
    # A deep source inspection must not change the root opened on return.
    page.locator('#graph-area [data-node-id="source/' + workflow + '"]').click()
    expect(page.locator('#fields-content')).to_contain_text('EXACT_SOURCE_FIELD')
    tab(page, 'proposals').click()
    page.locator('#redesign-open-original').click()
    expect(page.locator('#tab-graph')).to_have_attribute('aria-selected', 'true')
    assert json.loads(page.locator('#object-json').text_content())['node']['id'] == workflow
    settle(page)
    assert len([read for read in reads if read['path'].startswith('/api/object/')]) == 1
    assert all(read['method'] == 'GET' for read in reads)
    assert all(read['method'] == 'GET' or read['path'] == '/api/hosted/heartbeat'
               for read in state['requests'])


@pytest.mark.parametrize('standalone', [False, True])
@pytest.mark.parametrize('invalid', ['project', 'package', 'unknown_workflow', 'partial_id', 'scenario'])
def test_main_helper_rejects_mismatched_scope_synchronously_without_get(source_ui, standalone, invalid):
    page, state, reads = source_ui(standalone)
    pins = {'workflowId': WORKFLOWS[0], 'projectId': '' if standalone else PROJECT_A,
            'packageHash': PACKAGE_C if standalone else PACKAGE_A, 'scenario': 'original'}
    pins[{'project': 'projectId', 'package': 'packageHash', 'unknown_workflow': 'workflowId',
          'partial_id': 'workflowId', 'scenario': 'scenario'}[invalid]] = {
              'project': PROJECT_B, 'package': PACKAGE_B, 'unknown_workflow': 'f' * 64,
              'partial_id': WORKFLOWS[0][:12], 'scenario': 'unsupported'}[invalid]
    before = len(reads), len(state['requests'])
    assert page.evaluate('pins => window.orionOpenWorkflow(pins)', pins) is False
    settle(page)
    expect(page.locator('#page-proposals')).to_be_visible()
    assert (len(reads), len(state['requests'])) == before