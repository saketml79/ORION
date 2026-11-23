"""Production read coordinator in offline Chromium, no model/service writes."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / 'workbench' / 'static'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as runtime:
        instance = runtime.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(offline=True, service_workers='block')
    page = context.new_page()
    page.route('http://reads.invalid/', lambda route: route.fulfill(content_type='text/html', body='<button id="refresh">Refresh</button>'))
    page.goto('http://reads.invalid/')
    page.add_script_tag(path=str(STATIC / 'scoped-reads.js'))
    yield page
    context.close()


def test_identical_cross_module_reads_share_body_not_mutable_result(page):
    held = []
    page.route('**/api/object/*', lambda route: held.append(route))
    page.evaluate('''() => {
      const read = path => orionScopedReads.fetch(path, {headers: {'X-Workbench-Project':'p', 'X-Workbench-Package':'a'}}).then(r => r.json());
      window.done = false;
      Promise.all([read('/api/object/one?b=2&a=1'), read('/api/object/one?a=1&b=2')]).then(values => {
        values[0].nodes.push('changed'); window.result = values; window.done = true;
      });
    }''')
    page.wait_for_function('() => true')
    assert len(held) == 1
    held.pop().fulfill(content_type='application/json', body='{"nodes":[]}')
    page.wait_for_function('window.done')
    assert page.evaluate('window.result') == [{'nodes': ['changed']}, {'nodes': []}]


@pytest.mark.parametrize('difference', ['package', 'scenario', 'version', 'query', 'refresh', 'agent-completed', 'loop-completed'])
def test_distinct_scopes_queries_versions_and_refresh_never_join(page, difference):
    held = []
    page.route('**/api/redesigns*', lambda route: held.append(route))
    page.evaluate('''difference => {
      const headers = {'X-Workbench-Project':'p', 'X-Workbench-Package':'a'};
      window.results = [];
      const first = orionScopedReads.fetch('/api/redesigns?scenario=bronze&version=1', {headers});
      let path = '/api/redesigns?scenario=bronze&version=1';
      if (difference === 'package') headers['X-Workbench-Package'] = 'b';
      if (difference === 'scenario') path = '/api/redesigns?scenario=original&version=1';
      if (difference === 'version') path = '/api/redesigns?scenario=bronze&version=2';
      if (difference === 'query') path += '&draft_jobs=1';
      if (difference === 'refresh') document.getElementById('refresh').click();
      if (difference === 'agent-completed' || difference === 'loop-completed')
        document.dispatchEvent(new CustomEvent(difference === 'agent-completed' ? 'orion:agent-run' : 'orion:loop-run', {detail:{status:'completed'}}));
      Promise.all([first, orionScopedReads.fetch(path, {headers})]).then(async values => {
        window.results = await Promise.all(values.map(r => r.json()));
      });
    }''', difference)
    page.wait_for_function('() => true')
    assert len(held) == 2
    for route in held:
        route.fulfill(content_type='application/json', body='{}')
    page.wait_for_function('window.results.length === 2')


def test_cancelled_subscriber_does_not_cancel_other_view(page):
    held = []
    page.route('**/api/object/*', lambda route: held.append(route))
    page.evaluate('''() => {
      window.controller = new AbortController(); window.result = null;
      orionScopedReads.fetch('/api/object/one', {signal: controller.signal}).catch(e => {window.cancelled = e.name;});
      orionScopedReads.fetch('/api/object/one').then(r => r.json()).then(value => {window.result = value;});
    }''')
    page.wait_for_function('() => true')
    assert len(held) == 1
    page.evaluate('controller.abort()')
    held[0].fulfill(content_type='application/json', body='{"ok":true}')
    page.wait_for_function('window.result !== null')
    assert page.evaluate('window.cancelled') == 'AbortError'
    assert page.evaluate('window.result') == {'ok': True}


def test_different_credential_policies_never_share_an_authenticated_read(page):
    held = []
    page.route('**/api/object/*', lambda route: held.append(route))
    page.evaluate('''() => {
      window.done = false;
      Promise.all([
        orionScopedReads.fetch('/api/object/one', {credentials:'omit'}),
        orionScopedReads.fetch('/api/object/one', {credentials:'same-origin'})
      ]).then(() => {window.done = true;});
    }''')
    page.wait_for_function('() => true')
    assert len(held) == 2
    for route in held:
        route.fulfill(content_type='application/json', body='{}')
    page.wait_for_function('window.done')


@pytest.mark.parametrize('path', ['/api/agents/runs', '/api/agents/runs/' + 'a'*32,
  '/api/jobs/' + 'a'*32, '/api/loops', '/api/hosted/operations/' + 'b'*32,
  '/api/projects/' + 'a'*32 + '/jobs/' + 'b'*32, '/api/hosted/uploads/' + 'b'*32])
def test_live_status_reads_are_never_joined_or_cached(page, path):
    held = []
    page.route('**/api/**', lambda route: held.append(route))
    page.evaluate('''path => {
      window.done = false;
      Promise.all([orionScopedReads.fetch(path), orionScopedReads.fetch(path)]).then(() => {window.done = true;});
    }''', path)
    page.wait_for_function('() => true')
    assert len(held) == 2
    for route in held:
        route.fulfill(content_type='application/json', body='{"status":"running"}')
    page.wait_for_function('window.done')


@pytest.mark.parametrize('status', [200, 401, 402, 403, 409, 429, 503])
def test_completed_and_failed_reads_always_observe_again(page, status):
    requests = []
    def answer(route):
        requests.append(route.request.url)
        route.fulfill(status=status, content_type='application/json', body='{}')
    page.route('**/api/object/*', answer)
    page.evaluate('''async () => {
      await orionScopedReads.fetch('/api/object/one');
      await orionScopedReads.fetch('/api/object/one');
    }''')
    assert len(requests) == 2


def test_durable_read_has_one_owner_without_elapsed_cutoff_and_retains_operation_id(page):
    page.clock.install()
    page.evaluate('''() => {
      window.starts = 0; window.messages = []; window.observations = []; window.controller = new AbortController();
      const loader = (signal, report) => {
        starts++; report({operation_id:'abc123', status:'running'});
        return new Promise(() => {});
      };
      const read = () => orionScopedReads.read('/api/redesigns?version=1', {}, loader, {transport:'hosted', signal: controller.signal, onStatus: value => observations.push(value)})
        .catch(e => {messages.push(e.message);});
      read(); read();
    }''')
    assert page.evaluate('window.starts') == 1
    page.clock.fast_forward(3600001)
    assert page.evaluate('window.messages') == []
    assert all(value['operation_id'] == 'abc123' for value in page.evaluate('window.observations'))
    page.evaluate('controller.abort()')
    page.wait_for_function('window.messages.length === 2')
    assert page.evaluate('window.messages') == ['Selection changed', 'Selection changed']
    assert page.evaluate('window.starts') == 1


def test_post_is_never_coalesced_and_invalidates_read_ownership(page):
    requests = []
    page.route('**/api/**', lambda route: (requests.append(route.request.method),
        route.fulfill(content_type='application/json', body='{}')))
    page.evaluate('''async () => {
      await Promise.all([orionScopedReads.fetch('/api/reviews', {method:'POST', body:'{}'}),
        orionScopedReads.fetch('/api/reviews', {method:'POST', body:'{}'})]);
    }''')
    assert requests == ['POST', 'POST']


def test_mutation_completion_invalidates_a_read_started_during_write(page):
    page.evaluate('''async () => {
      window.loads = 0;
      const read = () => orionScopedReads.read('/api/redesigns', {}, () => {
        loads++; return new Promise(() => {});
      }).catch(() => {});
      window.fetch = () => { throw new Error('Coordinator must retain captured native transport'); };
      window.readDuringWrite = read;
    }''')
    held = []
    page.route('**/api/reviews', lambda route: held.append(route))
    page.evaluate("() => {window.write = orionScopedReads.fetch('/api/reviews', {method:'POST', body:'{}'});}")
    page.wait_for_function('() => true')
    assert len(held) == 1
    page.evaluate('() => {readDuringWrite();}')
    assert page.evaluate('loads') == 1
    held[0].fulfill(content_type='application/json', body='{}')
    page.evaluate('async () => {await write; readDuringWrite();}')
    assert page.evaluate('loads') == 2


def test_last_subscriber_cancellation_evicts_even_if_loader_ignores_abort(page):
    page.evaluate('''async () => {
      const controller = new AbortController(); window.loads = 0; window.values = [];
      const loader = () => { loads++; return new Promise(resolve => {window.releaseOld = resolve;}); };
      orionScopedReads.read('/api/object/one', {}, loader, {signal:controller.signal})
        .then(value => values.push(value), error => values.push(error.name));
      await Promise.resolve(); controller.abort();
      orionScopedReads.read('/api/object/one', {}, async () => {loads++; return 'new';})
        .then(value => values.push(value));
      releaseOld('late old value');
    }''')
    page.wait_for_function('window.values.length === 2')
    assert page.evaluate('loads') == 2
    assert page.evaluate('values') == ['AbortError', 'new']


def test_real_and_identical_progress_do_not_impose_elapsed_deadlines(page):
    page.clock.install()
    page.evaluate('''() => {
      window.failure = '';
      orionScopedReads.read('/api/redesigns', {}, (signal, report) => {
        window.report = report; report({operation_id:'progress', status:'running', progress:{stage:'metadata_restore'}});
        return new Promise(() => {});
      }).catch(e => {window.failure = e.message;});
    }''')
    page.clock.fast_forward(90000)
    page.evaluate("report({operation_id:'progress', status:'running', progress:{stage:'preparing_package'}})")
    page.clock.fast_forward(90000)
    assert page.evaluate('window.failure') == ''
    page.evaluate("report({operation_id:'progress', status:'running', progress:{stage:'preparing_package', updated_at:'new-clock'}})")
    page.clock.fast_forward(3600001)
    assert page.evaluate('window.failure') == ''


def test_actual_hosted_helper_shares_one_durable_read_without_activity_modal(page):
    requests = []
    def answer(route):
        request = route.request
        requests.append((request.method, request.url))
        if request.url.endswith('/api/health'):
            value = {'authentication': 'databricks_obo'}
        else:
            envelope = request.post_data_json
            assert envelope['method'] == 'GET'
            value = {'operation_id': envelope['id'], 'status': 'completed', 'result': {'items': []}}
        route.fulfill(content_type='application/json', body=json.dumps(value))
    page.route('**/api/**', answer)
    page.add_script_tag(path=str(STATIC / 'hosted-intake.js'))
    result = page.evaluate('''async () => {
      const first = orionHostedIntake.read('/api/redesigns?scenario=bronze', {'X-Workbench-Project':'p'});
      const second = orionHostedIntake.read('/api/redesigns?scenario=bronze', {'X-Workbench-Project':'p', 'X-Workbench-Request':'1'});
      return await Promise.all([first, second]);
    }''')
    assert result == [{'items': []}, {'items': []}]
    assert len([item for item in requests if item[0] == 'POST']) == 1
    assert page.locator('#hosted-operation-activity').count() == 0


def test_hosted_shared_owner_survives_originating_view_cancellation_and_captures_scope(page):
    held = []
    def answer(route):
        if route.request.url.endswith('/api/health'):
            route.fulfill(content_type='application/json', body='{"authentication":"databricks_obo"}')
        else:
            held.append(route)
    page.route('**/api/**', answer)
    page.add_script_tag(path=str(STATIC / 'hosted-intake.js'))
    with page.expect_request('**/api/hosted/operations'):
        page.evaluate('''() => {
          window.firstCurrent = true; window.controller = new AbortController(); window.values = [];
          const headers = {'X-Workbench-Project':'a'.repeat(32), 'X-Workbench-Package':'a'.repeat(64)};
          orionHostedIntake.read('/api/redesigns', headers, () => {},
            {signal:controller.signal, isCurrent:() => firstCurrent}).catch(error => values.push(error.name));
          orionHostedIntake.read('/api/redesigns', headers).then(value => values.push(value));
          headers['X-Workbench-Package'] = 'b'.repeat(64);
        }''')
    page.wait_for_function('() => true')
    assert len(held) == 1
    request = held[0].request
    assert request.all_headers()['x-workbench-package'] == 'a' * 64
    operation = request.post_data_json['id']
    page.evaluate('() => {firstCurrent = false; controller.abort();}')
    held[0].fulfill(content_type='application/json', body=json.dumps({
        'operation_id': operation, 'status': 'completed', 'result': {'items': ['current second view']}}))
    page.wait_for_function('window.values.length === 2')
    assert page.evaluate('values') == ['AbortError', {'items': ['current second view']}]