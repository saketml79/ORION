"""Production Proposals rendering under self-only CSP, offline Chromium only."""
import json
from hashlib import sha256

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui, prepare_sql_draft
from workbench.tests.test_proposal_primary_ui import saved_code
from workbench.tests.test_native_task_view_ui import native_bundle, open_bundle
from workbench.tests.test_workflow_artifacts_api import workflow_ui, select_bronze, generate_workflow


PYTHON = '# café 未変更\r\nfrom pyspark.sql import functions as F\r\ndef load():\r\n    return print("<img src=x onerror=alert(1)>", 42)\r\n'
SQL = "-- exact SQL\r\nSELECT 'it''s <script>alert(1)</script>', 42 FROM source WHERE id IS NOT NULL;\r\n"
YAML = 'resources:\r\n  jobs:\r\n    sample:\r\n      name: "<svg onload=alert(1)>"\r\n      enabled: true\r\n      retries: 2 # exact comment\r\n'


def assert_tokens(pre, text, language, kinds):
    assert pre.text_content() == text
    expect(pre).to_have_attribute('data-language', language)
    expect(pre.locator('script, img, svg, a, iframe')).to_have_count(0)
    for kind in kinds:
        assert pre.locator('.syntax-' + kind).count() > 0
        assert pre.locator('.syntax-' + kind).first.evaluate(
            'span => getComputedStyle(span).color !== getComputedStyle(span.parentElement).color')


def file_pre(page, path):
    return page.locator('.review-code-file').filter(
        has=page.get_by_role('button', name='Download ' + path, exact=True)).locator('pre.proposal-code')


def reload_saved_code(page):
    page.locator('#redesign-refresh').click()
    expect(page.locator('#redesign-status')).to_contain_text('Stored v2')
    page.get_by_role('tab', name='Notebook code & Job YAML', exact=True).click()
    page.locator('#spark-draft-find').click()


def replace_saved_artifacts(page, manifest, artifacts):
    notebook = json.loads(artifacts['notebooks/load.ipynb'])
    notebook['cells'][1]['source'] = [PYTHON]
    notebook['cells'].extend([
        {'cell_type': 'code', 'metadata': {'language': 'sql'}, 'source': [SQL]},
        {'cell_type': 'code', 'metadata': {'language': 'yaml'}, 'source': [YAML]},
        {'cell_type': 'code', 'metadata': {'language': 'unknown'}, 'source': [PYTHON]},
    ])
    artifacts.update({'notebooks/load.ipynb': json.dumps(notebook, ensure_ascii=False),
                      'modules/shared.py': PYTHON, 'sql/load.sql': SQL, 'job.yml': YAML})
    manifest['artifact_hashes'] = {path: sha256(text.encode()).hexdigest() for path, text in artifacts.items()}
    manifest['tasks'][0]['artifact_paths'] = list(artifacts)
    reload_saved_code(page)
    expect(file_pre(page, 'notebooks/load.ipynb')).to_have_count(5)


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_saved_artifacts_exact_text_colored_languages_and_download_bytes(saved_code, theme):
    page, state, _, _, manifest, artifacts = saved_code
    replace_saved_artifacts(page, manifest, artifacts)
    page.locator('html').evaluate('(element, theme) => element.dataset.theme = theme', theme)
    for path, text, language, kinds in [
        ('modules/shared.py', PYTHON, 'python', ['comment', 'keyword', 'function', 'string', 'number']),
        ('sql/load.sql', SQL, 'sql', ['comment', 'keyword', 'string', 'number']),
        ('job.yml', YAML, 'yaml', ['key', 'string', 'keyword', 'number', 'comment']),
    ]:
        assert_tokens(file_pre(page, path), text, language, kinds)
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Download ' + path, exact=True).click()
        assert downloaded.value.path().read_bytes() == text.encode('utf-8')
    cells = file_pre(page, 'notebooks/load.ipynb')
    for index, text, language, kinds in [
        (1, PYTHON, 'python', ['keyword', 'string']), (2, SQL, 'sql', ['keyword']),
        (3, YAML, 'yaml', ['key']), (4, PYTHON, 'unknown', []),
    ]:
        assert_tokens(cells.nth(index), text, language, kinds)
    expect(cells.nth(0).locator('span')).to_have_count(0)  # Markdown is not inferred as Python.
    expect(cells.nth(4).locator('span')).to_have_count(0)
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download notebooks/load.ipynb', exact=True).click()
    assert downloaded.value.path().read_bytes() == artifacts['notebooks/load.ipynb'].encode()
    assert json.loads(downloaded.value.path().read_bytes())['cells'][1]['metadata']['id'] == 'existing-code'
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_saved_task_clicks_and_refresh_rebuild_highlighted_exact_source(saved_code):
    page, state, _, _, manifest, artifacts = saved_code
    replace_saved_artifacts(page, manifest, artifacts)
    for _ in range(2):
        page.get_by_role('tab', name='Notebook code & Job YAML', exact=True).click()
        view = page.locator('#native-job-task-view')
        view.locator('[data-task-key="ingest"]').click()
        pre = view.locator('.workflow-task-inspector pre.proposal-code')
        assert_tokens(pre, PYTHON, 'python', ['keyword', 'function', 'string'])
        view.locator('[data-task-key="check"]').press('Enter')
        expect(view.locator('.workflow-task-inspector pre.proposal-code')).to_have_count(0)
        view.locator('[data-task-key="ingest"]').press('Enter')
        assert_tokens(pre, PYTHON, 'python', ['keyword', 'string'])
        reload_saved_code(page)
        expect(file_pre(page, 'modules/shared.py')).to_have_count(1)
    assert all(request['method'] == 'GET' for request in state['requests'])


def test_saved_credential_guard_still_withholds_files_and_redacts_task_source(saved_code):
    page, _, _, _, manifest, artifacts = saved_code
    replace_saved_artifacts(page, manifest, artifacts)
    secret = 'password=never_echo_this\n'
    artifacts['modules/shared.py'] = PYTHON + secret
    reload_saved_code(page)
    expect(page.locator('#spark-draft-output')).to_contain_text('credential-shaped content requires correction at source')
    expect(page.get_by_role('button', name='Download modules/shared.py', exact=True)).to_have_count(0)
    page.get_by_role('tab', name='Notebook code & Job YAML', exact=True).click()
    page.locator('#native-job-task-view [data-task-key="ingest"]').click()
    pre = page.locator('#native-job-task-view .workflow-task-inspector pre.proposal-code')
    assert pre.text_content() == PYTHON + '[REDACTED]\n'
    expect(page.locator('#page-proposals')).not_to_contain_text('never_echo_this')


def test_saved_native_task_notebook_and_yaml_are_highlighted(native_bundle):
    page, state, _, _, bundle, requests = native_bundle
    open_bundle(page, bundle)
    page.get_by_role('tab', name='Notebook code & Job YAML', exact=True).click()
    view = page.locator('#native-job-task-view')
    expect(view.locator('.workflow-native-card')).to_have_count(4)
    view.locator('[data-task-key="above"]').press('Enter')
    pre = view.locator('.workflow-task-inspector pre.proposal-code').first
    assert_tokens(pre, 'print("Exact selected notebook")\r\n', 'python', ['function', 'string'])
    assert_tokens(file_pre(page, 'migration-job.yaml'), bundle['artifacts']['migration-job.yaml'], 'yaml', ['key'])
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download notebooks/selected/above.ipynb', exact=True).click()
    assert downloaded.value.path().read_bytes() == bundle['artifacts']['notebooks/selected/above.ipynb'].encode()
    assert requests == ['GET']
    assert all(request['method'] == 'GET' for request in state['requests'])


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_deterministic_examples_task_cells_yaml_sql_and_mobile_overflow(workflow_ui, theme):
    page, state, _ = workflow_ui
    artifacts = state['workflow_response']['artifacts']
    notebook = json.loads(artifacts['notebooks/wait.ipynb'])
    exact = PYTHON + "password = dbutils.secrets.get(scope='approved', key='smtp')\r\n"
    notebook['cells'][1]['source'] = [exact]
    notebook['cells'].extend([
        {'cell_type': 'code', 'metadata': {'language': 'sql'}, 'source': [SQL]},
        {'cell_type': 'code', 'metadata': {'language': 'yaml'}, 'source': [YAML]},
    ])
    artifacts.update({'notebooks/wait.ipynb': json.dumps(notebook, ensure_ascii=False),
                      'notebooks/wait.py': exact, 'databricks.yml': YAML, 'query.sql': SQL,
                      'plain.txt': PYTHON, 'long.py': 'value = "' + 'x' * 600 + '"\r\n'})
    select_bronze(page)
    generate_workflow(page)
    expect(file_pre(page, 'notebooks/wait.ipynb')).to_have_count(4)
    page.locator('html').evaluate('(element, theme) => element.dataset.theme = theme', theme)
    assert_tokens(file_pre(page, 'databricks.yml'), YAML, 'yaml', ['key', 'string', 'keyword'])
    assert_tokens(file_pre(page, 'query.sql'), SQL, 'sql', ['keyword', 'string'])
    assert_tokens(file_pre(page, 'plain.txt'), PYTHON, 'unknown', [])
    expect(file_pre(page, 'plain.txt').locator('span')).to_have_count(0)
    output = page.locator('#workflow-artifact-output')
    for _ in range(2):
        output.locator('[data-task-key="wait"]').click()
        cells = output.locator('.workflow-task-inspector pre.proposal-code')
        assert_tokens(cells.nth(1), exact, 'python', ['keyword', 'string', 'function'])
        assert_tokens(cells.nth(2), SQL, 'sql', ['keyword', 'string'])
        assert_tokens(cells.nth(3), YAML, 'yaml', ['key', 'string'])
        assert_tokens(cells.nth(4), exact, 'python', ['keyword', 'string'])
        output.locator('[data-task-key="threshold"]').click()
        expect(output.locator('.workflow-task-inspector pre.proposal-code')).to_have_count(0)
    with page.expect_download() as downloaded:
        output.get_by_role('button', name='Download notebooks/wait.py', exact=True).click()
    assert downloaded.value.path().read_bytes() == exact.encode()
    page.set_viewport_size({'width': 390, 'height': 844})
    long = file_pre(page, 'long.py')
    long.locator('..').locator('summary').click()
    long.focus()
    geometry = long.evaluate('''pre => ({width: pre.getBoundingClientRect().width,
        scroll: pre.scrollWidth, client: pre.clientWidth, overflow: getComputedStyle(pre).overflowX,
        wrap: getComputedStyle(pre).whiteSpace, font: parseFloat(getComputedStyle(pre).fontSize)})''')
    assert geometry['width'] <= 390
    assert geometry['scroll'] > geometry['client']
    assert geometry['overflow'] == 'auto' and geometry['wrap'] == 'pre' and geometry['font'] >= 13
    long.press('ArrowRight')
    expect(long).to_be_focused()
    # A second explicit render creates fresh highlighted nodes, no observer or execution needed.
    generate_workflow(page)
    expect(file_pre(page, 'notebooks/wait.ipynb')).to_have_count(4)
    assert_tokens(file_pre(page, 'notebooks/wait.ipynb').nth(1), exact, 'python', ['keyword'])
    assert len(state['workflow_requests']) == 2
    assert not state['archive_requests']


def test_captured_sql_extension_rendering_preserves_escaping_and_redaction(ui):
    page, state, _ = ui
    state['sql_response']['artifacts'].update({'source.sql': SQL, 'job.yaml': YAML,
                                             'source.py': PYTHON + 'token=never_echo_this\n'})
    prepare_sql_draft(page)
    page.locator('#sql-draft-submit').click()
    output = page.locator('#sql-draft-output')
    expect(output).to_contain_text('SQL-only drafts')
    for path, text, language, kinds in [('source.sql', SQL, 'sql', ['keyword', 'string']),
                                        ('job.yaml', YAML, 'yaml', ['key', 'string'])]:
        block = output.locator('details').filter(has=page.get_by_text(path, exact=True))
        assert_tokens(block.locator('pre'), text, language, kinds)
    block = output.locator('details').filter(has=page.get_by_text('source.py', exact=True))
    assert_tokens(block.locator('pre'), PYTHON + '[REDACTED]\n', 'python', ['keyword', 'string'])
    expect(output).not_to_contain_text('never_echo_this')