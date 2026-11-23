"""Real app + guide, offline Chromium, held overview GETs and real abort signals.

No backend, auth changes, mutations, live models, or wall-clock 110-second wait.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from test_hosted_startup import startup
from test_project_package_autoload import (
    STATIC, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B, PACKAGE_C,
    browser, member, settle,
)


@pytest.fixture
def loading(startup):
    def start(members=None):
        page, state, _ = startup(projects={
            PROJECT_A: members if members is not None else [member()],
            PROJECT_B: [member(PACKAGE_C)],
        })
        expect(page.locator(f'#project-select option[value="{PROJECT_A}"]')).to_have_count(1)
        page.add_script_tag(path=str(STATIC / 'workflow-guide.js'))
        page.clock.install()
        page.evaluate("""() => {
          window.overviewSignals = []; window.otherSignals = [];
          window.progressScrolls = 0;
          const fetchOriginal = window.fetch.bind(window);
          window.fetch = (path, options) => {
            const signals = path === '/api/overview' ? overviewSignals : otherSignals;
            signals.push({path, signal: options.signal});
            return fetchOriginal(path, options);
          };
          const scrollOriginal = Element.prototype.scrollIntoView;
          Element.prototype.scrollIntoView = function(...args) {
            if (this.id === 'package-understanding-progress') progressScrolls++;
            return scrollOriginal.apply(this, args);
          };
        }""")
        pending = []

        def overview(route):
            headers = route.request.all_headers()
            pending.append((route, headers.get('x-workbench-package', '')))

        page.route('**/api/overview', overview)
        page.locator('#project-select').select_option(PROJECT_A)
        return page, state, pending

    return start


def wait_pending(page, pending, size=1):
    page.wait_for_function('overviewSignals.length >= ' + str(size))
    settle(page)
    assert len(pending) >= size


def respond(pending, index=0, *, status=200, ready=True, detail=None):
    route, digest = pending[index]
    data = {'ready': ready, 'status': 'package_selection_required',
            'package': {'name': f'{digest[:1]}.xml', 'sha256': digest} if ready else None,
            'summary': f'Understanding for {digest}',
            'counts': {'workflow': 1} if ready else {},
            'workflows': [{'id': f'wf/{digest}', 'name': 'Actual workflow'}] if ready else [],
            'agent': {'available': True}}
    route.fulfill(status=status, content_type='application/json',
                  body=json.dumps({'detail': detail} if detail else data))


def selected_not_ready(page, digest=PACKAGE_A):
    expect(page.locator('#package-name')).to_have_text(f'{digest[:1]}.xml')
    expect(page.locator('#estate-package-select')).to_have_value(digest)
    expect(page.locator('#project-package')).to_have_value(digest)
    expect(page.locator('#package-ready')).not_to_have_text('Package ready')
    expect(page.locator('#show-package-graph')).to_be_disabled()
    expect(page.locator('#agent-submit')).to_be_disabled()
    expect(page.locator('#agent-consent')).to_be_checked()
    expect(page.locator('#agent-workflow option[value^="wf/"]')).to_have_count(0)
    expect(page.locator('.intake-step').nth(1)).to_have_class(re.compile(r'\bis-recorded\b'))
    expect(page.locator('.intake-step').nth(1)).not_to_have_attribute('aria-current', 'step')
    expect(page.locator('.intake-step').nth(1)).to_contain_text('upload optional')
    expect(page.locator('.intake-step').nth(2)).to_have_attribute('aria-current', 'step')
    expect(page.locator('.intake-current')).to_contain_text('No new upload is required', ignore_case=True)
    expect(page.locator('.current-project-name')).to_have_text('1')


def test_slow_overview_survives_110_seconds_with_metadata_and_real_indeterminate_progress(loading):
    page, _, pending = loading()
    wait_pending(page, pending)
    selected_not_ready(page)
    expect(page.locator('#package-ready')).to_have_text('Loading package understanding')
    expect(page.locator('.intake-current')).to_contain_text('Loading existing package understanding: a.xml')
    progress = page.locator('#package-understanding-progress')
    expect(progress).not_to_have_attribute('hidden', '')
    expect(progress).to_have_attribute('role', 'status')
    expect(progress).to_have_attribute('aria-busy', 'true')
    expect(progress.locator('progress')).not_to_have_attribute('value', re.compile('.+'))
    page.clock.fast_forward(110_001)
    settle(page)
    assert page.evaluate('overviewSignals.every(item => !item.signal.aborted)')
    assert page.evaluate('progressScrolls') == 0
    selected_not_ready(page)
    expect(progress).not_to_have_attribute('hidden', '')
    page.get_by_role('button', name='View package loading progress').click()
    expect(progress).to_be_focused()
    assert page.evaluate('progressScrolls') == 1
    respond(pending)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#show-package-graph')).to_be_enabled()
    expect(progress).to_be_hidden()
    expect(progress).to_have_attribute('aria-busy', 'false')
    expect(page.get_by_role('button', name='View package loading progress')).to_have_count(0)
    expect(page.locator('.intake-current')).to_contain_text('inspect package understanding')


@pytest.mark.parametrize('failure', ['http', 'network'])
def test_failed_overview_keeps_xml_and_guide_context_and_explicit_retry(loading, failure):
    page, _, pending = loading()
    wait_pending(page, pending)
    if failure == 'http':
        respond(pending, status=503, detail='Selected package hydration unavailable')
    else:
        pending[0][0].abort('failed')
    expect(page.locator('#package-ready')).to_contain_text('Loading interrupted')
    if failure == 'http':
        expect(page.locator('#package-ready')).to_contain_text('HTTP 503: Selected package hydration unavailable')
    selected_not_ready(page)
    expect(page.locator('#package-understanding-progress')).to_be_hidden()
    expect(page.locator('.intake-step').nth(2)).to_have_class(re.compile(r'\bis-failed\b'))
    assert page.evaluate('progressScrolls') == 0
    page.locator('#refresh').click()
    wait_pending(page, pending, 2)
    selected_not_ready(page)
    expect(page.locator('#package-ready')).to_have_text('Loading package understanding')
    respond(pending, 1)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#package-name')).to_have_text('a.xml')


def test_nonready_service_response_does_not_erase_completed_selection_or_fake_progress(loading):
    page, _, pending = loading()
    wait_pending(page, pending)
    respond(pending, ready=False)
    expect(page.locator('#package-ready')).to_have_text('Selected package understanding not loaded')
    selected_not_ready(page)
    expect(page.locator('#package-understanding-progress')).to_be_hidden()
    expect(page.locator('.intake-current')).not_to_have_class(re.compile(r'\bis-running\b'))
    expect(page.locator('#overview-summary')).to_contain_text('uploading another package is optional')


@pytest.mark.parametrize('late_failure', [False, True])
def test_switch_package_ignores_old_success_or_failure_with_ineffective_cancellation(loading, late_failure):
    page, state, pending = loading([member(), member(PACKAGE_B)])
    wait_pending(page, pending)
    respond(pending, ready=False)
    expect(page.locator('#project-package')).to_have_attribute('aria-busy', 'false')
    page.locator('#project-package').select_option(PACKAGE_A)
    wait_pending(page, pending, 2)
    selected_not_ready(page)
    page.locator('#project-package').select_option(PACKAGE_B)
    wait_pending(page, pending, 3)
    selected_not_ready(page, PACKAGE_B)
    respond(pending, 2)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    respond(pending, 1, status=503 if late_failure else 200,
            detail='Old selection failure' if late_failure else None)
    settle(page)
    expect(page.locator('#package-name')).to_have_text('b.xml')
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#overview-summary')).to_contain_text(PACKAGE_B)
    expect(page.locator('#notice')).to_be_hidden()
    expect(page.locator('#package-understanding-progress')).to_be_hidden()
    read_ids = [request['headers']['xworkbenchreadid'] for request in state['requests']
                if request['path'] == '/api/overview']
    assert len(read_ids) == len(set(read_ids))


def test_switch_project_ignores_old_overview_while_new_request_alone_owns_progress(loading):
    page, _, pending = loading()
    wait_pending(page, pending)
    page.locator('#project-select').select_option(PROJECT_B)
    wait_pending(page, pending, 2)
    expect(page.locator('#package-name')).to_have_text('c.xml')
    respond(pending, 0)
    settle(page)
    expect(page.locator('#package-name')).to_have_text('c.xml')
    expect(page.locator('#package-ready')).to_have_text('Loading package understanding')
    expect(page.locator('#package-understanding-progress')).not_to_have_attribute('hidden', '')
    expect(page.locator('.current-project-name')).to_have_text('2')
    respond(pending, 1)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#package-name')).to_have_text('c.xml')
    expect(page.locator('#package-understanding-progress')).to_be_hidden()


def test_slow_reads_remain_pending_without_client_deadline_and_upload_is_optional(loading):
    page, _, pending = loading()
    wait_pending(page, pending)
    held = []
    page.route('**/api/projects', lambda route: held.append(route))
    page.locator('#refresh-projects').click()
    page.wait_for_function("otherSignals.some(item => item.path === '/api/projects')")
    page.clock.fast_forward(110_001)
    expect(page.locator('#project-summary')).not_to_contain_text('110 seconds')
    assert page.evaluate("otherSignals.some(item => item.path === '/api/projects' && !item.signal.aborted)")
    assert page.evaluate('overviewSignals.every(item => !item.signal.aborted)')
    expect(page.locator('.intake-step').nth(1)).to_contain_text('only if needed')
    expect(page.locator('#project-upload-form button')).to_be_disabled()
    assert held
    # Refresh projects legitimately starts a newer overview ticket while the
    # original is pending. Resolve both, leaving ticket guards authoritative.
    for index in range(len(pending)):
        respond(pending, index)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#project-upload-form button')).to_be_enabled()