"""Production graph and real builder, offline HTTP only. No live Jobs or model."""
import json
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from workbench.draft_bundle import publish
from workbench.native_job_draft import build, graph_metadata
from workbench.tests.test_native_job_draft_conditions import conditional_fixture
from workbench.tests.test_proposal_primary_ui import saved_code
from workbench.tests.test_redesign_ui import browser, ui, STATIC
from workbench.tests.test_workflow_artifacts_api import workflow_ui, select_bronze, generate_workflow


@pytest.fixture
def native_bundle(saved_code, tmp_path):
    page, state, fulfill, job, _, _ = saved_code
    design, manifest, artifacts = conditional_fixture()
    manifest['run_id'] = job['output']['run_id']
    # Two distinct committed notebooks share a basename. The native destination
    # must resolve by the full manifest path, never a basename search.
    original = artifacts.pop('notebooks/above.ipynb')
    notebook = json.loads(original)
    notebook['cells'][0]['metadata']['id'] = 'kept-original-cell'
    notebook['cells'][0]['source'] = ['print("Exact selected notebook")\r\n']
    original = json.dumps(notebook, ensure_ascii=False)
    artifacts['notebooks/selected/above.ipynb'] = original
    artifacts['notebooks/unrelated/above.ipynb'] = original.replace('Exact selected notebook', 'WRONG NOTEBOOK')
    manifest['tasks'][2]['artifact_paths'] = ['notebooks/selected/above.ipynb']
    root = tmp_path / 'generated-drafts' / job['package_hash'] / job['design_id'] / str(job['design_version']) / manifest['run_id']
    root.mkdir(parents=True)
    bundle = publish(SimpleNamespace(root=tmp_path), job, {'design': design},
                     {'manifest': manifest, 'artifacts': artifacts}, '/Workspace/Users/test')
    path = '/api/jobs/' + job['id'] + '/native-bundles/' + bundle['bundle_id']
    requests = []
    def read(route):
        requests.append(route.request.method)
        fulfill(route, bundle)
    page.route('**' + path, read)
    return page, state, fulfill, job, bundle, requests


def open_bundle(page, bundle):
    page.locator('#native-bundle-id').fill(bundle['bundle_id'])
    page.locator('#native-bundle-read').click()


def test_real_saved_native_graph_primary_exact_notebook_keyboard_zoom_download_and_scope(native_bundle, tmp_path):
    page, _, _, _, bundle, requests = native_bundle
    open_bundle(page, bundle)
    view = page.locator('#native-job-task-view')
    expect(view.locator('.workflow-native-card')).to_have_count(4)
    expect(view).to_be_hidden()
    notebook_tab = page.get_by_role('tab', name='Notebook code & Job YAML', exact=True)
    notebook_tab.click()
    expect(notebook_tab).to_have_attribute('aria-selected', 'true')
    expect(view).to_be_visible()
    assert view.evaluate("node => !node.closest('details')?.hidden")
    assert view.evaluate("node => !!(node.compareDocumentPosition(document.querySelector('#spark-draft-output .review-code-file')) & Node.DOCUMENT_POSITION_FOLLOWING)")
    resource = json.loads(bundle['artifacts']['migration-job.yaml'])['resources']['jobs']['migration_draft']
    assert resource['performance_target'] == 'STANDARD'
    assert 'job_clusters' not in resource
    assert all(not ({'job_cluster_key', 'existing_cluster_id', 'new_cluster'} & task.keys())
               for task in resource['tasks'])
    assert bundle['native_jobs'][0]['tasks'] == resource['tasks']
    expect(view).to_contain_text('not uploaded, executed, approved for release or proof of parity')
    expect(view).to_contain_text('/Workspace/Users/test/notebooks/selected/above')
    expect(view).to_contain_text('row_count GREATER_THAN_OR_EQUAL 35')
    expect(view.locator('[data-task-key="check_threshold"]')).to_contain_text('condition_task')
    expect(view).to_contain_text('True')
    expect(view).to_contain_text('False')
    svg = view.locator('svg')
    expect(svg).to_have_attribute('width', '1100')
    view.get_by_role('button', name='+ Zoom in', exact=True).click()
    expect(svg).to_have_attribute('width', '1375')
    view.get_by_role('button', name='Reset zoom', exact=True).click()
    selected = view.locator('[data-task-key="above"]')
    selected.focus()
    selected.press('Enter')
    inspector = view.locator('.workflow-task-inspector')
    expect(inspector).to_contain_text('Cell 1 · python')
    expect(inspector).to_contain_text('Exact selected notebook')
    expect(inspector).not_to_contain_text('WRONG NOTEBOOK')
    expect(inspector).not_to_contain_text('kept-original-cell')
    # The fixture serves the production stylesheet through same-origin CSP.
    assert view.locator('.workflow-native-card').first.evaluate(
        'node => getComputedStyle(node).display') != 'none'
    view.screenshot(path=str(tmp_path / 'native-task-view.png'))
    inspector.get_by_role('button', name='Close task details').click()
    expect(selected).to_be_focused()
    view.locator('[data-task-key="check_threshold"]').click()
    expect(inspector).to_contain_text('GREATER_THAN_OR_EQUAL')
    expect(inspector).to_contain_text('above')
    expect(inspector).to_contain_text('below')
    path = 'notebooks/selected/above.ipynb'
    page.get_by_role('tab', name='Lakeflow Job DAG', exact=True).click()
    page.get_by_role('button', name='View notebooks & downloads', exact=True).click()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='Download ' + path, exact=True).click()
    assert downloaded.value.path().read_bytes() == bundle['artifacts'][path].encode()
    assert json.loads(downloaded.value.path().read_bytes())['cells'][0]['metadata'] == {
        'language': 'python', 'id': 'kept-original-cell'}
    assert requests == ['GET']
    page.locator('#redesign-workflow').select_option('wf/two')
    expect(page.locator('.workflow-native-card')).to_have_count(0)
    expect(page.locator('.workflow-task-inspector')).to_have_count(0)


@pytest.mark.parametrize('changed', ['workflow', 'scenario', 'package'])
def test_pending_native_bundle_cannot_repopulate_changed_scope(native_bundle, changed):
    page, _, fulfill, job, bundle, _ = native_bundle
    pending = []
    path = '/api/jobs/' + job['id'] + '/native-bundles/' + bundle['bundle_id']
    page.route('**' + path, lambda route: pending.append(route))
    open_bundle(page, bundle)
    expect(page.locator('#native-bundle-read')).to_be_disabled()
    assert len(pending) == 1
    if changed == 'workflow':
        page.locator('#redesign-workflow').select_option('wf/two')
    elif changed == 'scenario':
        page.locator('#scenario').select_option('bronze')
    else:
        page.locator('#page-proposals').evaluate("node => { node.dataset.packageHash = 'c'.repeat(64); }")
    expect(page.locator('#native-job-task-view')).to_have_count(0)
    fulfill(pending[0], bundle)
    expect(page.locator('.workflow-native-card')).to_have_count(0)
    # A fresh scope may mount an empty output container. It must not restore
    # the previous committed notebooks or graph after the held read completes.
    expect(page.locator('#redesign-detail')).not_to_contain_text('Exact selected notebook')
    expect(page.locator('#native-job-task-view')).to_have_count(0)


@pytest.mark.parametrize('bad', ['missing_metadata', 'different_tasks', 'different_root', 'wrong_artifact', 'invalid_yaml'])
def test_unavailable_or_mismatched_metadata_keeps_files_without_fake_graph(native_bundle, bad):
    page, _, _, _, bundle, _ = native_bundle
    if bad == 'missing_metadata':
        bundle.pop('native_jobs')
    elif bad == 'different_tasks':
        bundle['native_jobs'][0]['tasks'][0]['task_key'] = 'invented'
    elif bad == 'different_root':
        bundle['native_jobs'][0]['workspace_root'] = '/Workspace/Users/other'
    elif bad == 'wrong_artifact':
        bundle['native_jobs'][0]['artifact_mapping']['above']['notebook_path'] = 'notebooks/unrelated/above.ipynb'
    elif bad == 'invalid_yaml':
        bundle['artifacts']['migration-job.yaml'] = 'resources:\n  jobs: {}\n'
    open_bundle(page, bundle)
    expect(page.locator('#native-job-task-view')).to_contain_text('Task data unavailable')
    expect(page.locator('#native-job-task-view .workflow-native-card')).to_have_count(0)
    page.get_by_role('button', name='View notebooks & downloads', exact=True).click()
    expect(page.get_by_role('button', name='Download migration-job.yaml', exact=True)).to_be_visible()


def test_known_json_native_builder_result_reuses_graph_after_explicit_build(saved_code):
    page, _, fulfill, job, _, artifacts = saved_code
    design = {'tasks': [{'id': 'ingest', 'implementation_kind': 'notebook_task'}], 'edges': []}
    manifest = {'tasks': [{'task_id': 'ingest', 'status': 'drafted', 'artifact_paths': ['notebooks/load.ipynb']}]}
    root = '/Workspace/Users/test'
    document = build(design, manifest, artifacts, root)
    calls = []
    def draft(route):
        calls.append(route.request.post_data_json)
        fulfill(route, {'job_id': job['id'], 'design_id': job['design_id'], 'design_version': job['design_version'],
                        'yaml': document, 'native_jobs': graph_metadata(document, manifest, artifacts, root),
                        'execution_allowed': False, 'deployment_allowed': False})
    page.route('**/api/jobs/' + job['id'] + '/native-job-draft', draft)
    expect(page.locator('#native-job-task-view .workflow-native-card')).to_have_count(0)
    page.locator('#native-job-workspace-root').fill(root)
    page.locator('#native-job-build').click()
    view = page.locator('#native-job-task-view')
    expect(view.locator('.workflow-native-card')).to_have_count(1)
    view.locator('.workflow-native-card').click()
    expect(view.locator('.workflow-task-inspector')).to_contain_text('saved exact')
    assert calls == [{'workspace_root': root, 'consent': True}]


def test_logical_saved_outline_never_fabricates_native_tasks(ui):
    page, _, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#redesign-detail h3').first).to_have_text('Actual submitted outline')
    expect(page.locator('.workflow-native-card')).to_have_count(0)
    expect(page.locator('#workflow-artifact-show-example')).to_be_hidden()


def test_example_generated_output_has_single_primary_mount_and_explicit_return_button(workflow_ui):
    page, _, _ = workflow_ui
    select_bronze(page)
    generate_workflow(page)
    page.get_by_text('Examples and technical details', exact=True).click()
    output = page.locator('#workflow-artifact-output')
    expect(output).to_be_visible()
    expect(output.locator('.workflow-native-card')).to_have_count(5)
    assert output.evaluate("node => !node.closest('details')")
    page.get_by_role('button', name='Show example workflow & notebooks', exact=True).click()
    expect(output.locator('.workflow-native-card').first).to_be_focused()
    expect(page.locator('#workflow-artifact-consent')).to_have_count(0)
    expect(page.locator('#workflow-artifact-output')).to_have_count(1)
    page.locator('#redesign-workflow').select_option('wf/one')
    expect(output).to_be_empty()
    expect(page.locator('#workflow-artifact-show-example')).to_be_hidden()