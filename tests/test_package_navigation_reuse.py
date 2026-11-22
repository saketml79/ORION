"""Browser checks for selected package navigation and background history loading."""
from playwright.sync_api import expect

from test_navigation_prerequisites import navigation, tab, choose_project
from test_project_package_autoload import browser, settle


def test_selecting_ready_project_opens_estate_and_prefetches_history(navigation):
    page, state, _ = navigation(fragment='projects')
    choose_project(page)
    expect(page.locator('#package-ready')).to_have_text('Package ready')
    expect(page.locator('#page-estate')).to_be_visible()
    expect(tab(page, 'estate')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#agent-submit')).to_be_disabled()
    page.wait_for_function("() => document.querySelector('#runs-list').textContent.includes('No agent runs recorded')")
    assert any(request['path'] == '/api/agents/runs' for request in state['requests'])
    assert not any(request['path'] == '/api/agents/run' for request in state['requests'])


def test_revisiting_evidence_retains_loaded_documents_and_connections(navigation):
    page, state, _ = navigation(fragment='projects')
    choose_project(page)
    expect(page.locator('#page-estate')).to_be_visible()
    tab(page, 'evidence').click()
    expect(page.locator('#documents-list')).to_contain_text('No supporting documents')
    expect(page.locator('#connections-status')).to_contain_text('No connection references')
    count = lambda path: sum(request['path'] == path for request in state['requests'])
    before = (count('/api/documents'), count('/api/connections'))
    tab(page, 'estate').click()
    tab(page, 'evidence').click()
    settle(page)
    assert (count('/api/documents'), count('/api/connections')) == before


def test_readiness_reuses_display_but_invalidates_after_workspace_change(navigation):
    import json
    from test_readiness_ui import payload

    page, state, _ = navigation(fragment='projects')
    result = payload()
    result['scope']['scenario'] = 'bronze'
    requests = []

    def serve(route):
        requests.append(route.request.url)
        route.fulfill(content_type='application/json', body=json.dumps(result))

    page.route('**/api/readiness?*', serve)
    choose_project(page)
    expect(page.locator('#page-estate')).to_be_visible()
    tab(page, 'readiness').click()
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    tab(page, 'estate').click()
    tab(page, 'readiness').click()
    settle(page)
    assert len(requests) == 1
    tab(page, 'estate').click()
    page.evaluate("document.dispatchEvent(new CustomEvent('orion:workspace-changed', {detail: {path: '/api/validation/campaigns', method: 'POST'}}))")
    tab(page, 'readiness').click()
    expect(page.locator('#readiness-status')).to_contain_text('Saved evidence observed')
    assert len(requests) == 2
