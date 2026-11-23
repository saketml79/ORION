"""Real browser helper contract, fake network only, no live model calls."""
from pathlib import Path
import json

import pytest
from playwright.sync_api import sync_playwright

SCRIPT = Path(__file__).resolve().parents[1] / 'static' / 'model-route-save.js'
VERSION = 'a' * 64


@pytest.mark.parametrize('case', ['completed', 'proxy_html', 'unknown', 'wrong_scope', 'changed_context',
                                'settings_revision', 'bad_settings_authority', 'bad_settings_parent'])
def test_route_save_durable_receipt_recovery_without_replay(case):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        calls, pending = [], []
        def route(r):
            request = r.request
            if request.url.endswith('/shell'):
                r.fulfill(body='<html></html>', content_type='text/html'); return
            calls.append((request.method, request.url))
            if request.method == 'POST':
                body = request.post_data_json
                pending.append(body['operation_id'])
                if case in {'proxy_html', 'unknown'}:
                    r.fulfill(status=502, body='<html>proxy failure</html>', content_type='text/html'); return
                r.fulfill(json={'operation_id': pending[0], 'status': 'queued'}); return
            if case == 'unknown':
                r.fulfill(status=404, json={'detail': 'Operation not found'}); return
            result = {'selected_endpoint': 'chat', 'project_id': 'p', 'package_hash': 'q',
                      'previous_route_version': VERSION, 'route_version': 'b' * 64,
                      'committed_generation': 'c' * 32}
            if case == 'wrong_scope': result['project_id'] = 'another-project'
            if case in {'settings_revision', 'bad_settings_authority', 'bad_settings_parent'}:
                result.update(settings_revision='d' * 32, settings_authority='hosted-model-settings-v1',
                              parent_generation='c' * 32)
            if case == 'bad_settings_authority': result['settings_authority'] = 'untrusted'
            if case == 'bad_settings_parent': result['parent_generation'] = 'e' * 32
            r.fulfill(json={'operation_id': pending[0], 'status': 'completed', 'result': result})
        page.route('**/*', route)
        page.goto('https://test.invalid/shell')
        page.add_script_tag(content=SCRIPT.read_text(encoding='utf-8'))
        value = page.evaluate('''async ({version, changed}) => {
          const scope = {project:'p', package:'q'}, workspace = 'https://workspace.example.com';
          const options = {scope, workspace, body:{endpoint:'chat', tool_support_confirmed:true,
            expected_route_version:version}, isCurrent:() => !changed};
          let result, error;
          try { result = await window.orionModelRouteSave.save(options); } catch (e) { error = e.message; }
          const recorded = window.orionModelRouteSave.pending(scope, workspace);
          if (recorded) {
            try { await window.orionModelRouteSave.save(options); } catch (_) {}
          }
          return {result, error, recorded};
        }''', {'version': VERSION, 'changed': case == 'changed_context'})
        assert len([c for c in calls if c[0] == 'POST']) == 1
        if case in {'completed', 'proxy_html', 'settings_revision'}:
            assert value['result']['committed_generation'] == 'c' * 32
            assert value['recorded'] is None
        else:
            assert value['error'] and value['recorded']['id'] == pending[0]
        browser.close()