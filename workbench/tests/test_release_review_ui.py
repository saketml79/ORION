"""Real release script, offline browser and explicit export action."""
import json

from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, STATIC  # noqa: F401


def test_release_preview_exact_pins_context_and_no_pageview_execution(browser):
    context = browser.new_context(offline=True)
    page = context.new_page()
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('http://release.test/', lambda route: route.fulfill(content_type='text/html', body='''
      <select id="project-select"><option value="one">one</option><option value="two">two</option></select>
      <select id="project-package"><option value="package">package</option></select>
      <select id="scenario"><option value="bronze">bronze</option></select>
      <section id="page-release"></section>'''))
    def respond(route):
        calls.append((route.request.method, route.request.post_data_json, route.request.headers))
        route.fulfill(content_type='application/json', body=json.dumps({'report': {
            'exported_member_count': 1, 'member_count': 2,
            'members': [{'workflow_id': 'e' * 64, 'status': 'exact_reviewed_draft_exported_not_executed'},
                        {'workflow_id': '9' * 64, 'status': 'missing_exact_reviewed_bundle', 'action': 'Review and draft this member.'}],
            'blockers': ['Separate execution approval required.']}, 'artifacts': {'databricks.yml': '{"resources": {}}'}}))
    page.route('**/api/releases/preview', respond)
    page.goto('http://release.test/')
    page.add_script_tag(content=(STATIC / 'release-review.js').read_text(encoding='utf-8'))
    assert calls == []
    pin = {'project': '1' * 32, 'package': 'a' * 64, 'scenario': 'bronze', 'workflow_id': 'e' * 64,
           'design_id': 'b' * 64, 'design_version': 3, 'job_id': 'd' * 32, 'bundle_id': 'f' * 64,
           'name': 'Actual selected workflow', 'cohort_id': 'c' * 32}
    page.evaluate("pin => document.dispatchEvent(new CustomEvent('orion:reviewed-bundle', {detail: pin}))", pin)
    preview = page.get_by_role('button', name='Preview exact DAB & member report')
    expect(preview).to_be_enabled()
    expect(page.locator('#release-export-consent')).to_have_count(0)
    preview.click()
    expect(page.locator('#reviewed-release-export')).to_contain_text('1 of 2 chosen members')
    expect(page.locator('#reviewed-release-export')).to_contain_text('missing exact reviewed bundle')
    assert calls[0][0] == 'POST'
    assert calls[0][1] == {'bundles': [{'job_id': pin['job_id'], 'bundle_id': pin['bundle_id']}],
                          'cohort_id': pin['cohort_id'], 'consent': True}
    assert calls[0][2]['x-workbench-package'] == pin['package']
    page.locator('#project-select').select_option('two')
    expect(preview).to_be_disabled()
    expect(page.locator('#reviewed-release-export')).not_to_contain_text('Actual selected workflow')
    assert len(calls) == 1 and not errors
    context.close()