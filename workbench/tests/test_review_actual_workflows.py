"""Fresh real XML plus offline fake-provider compiled tools, never live inference.

These are not remote conversion, parity or independent model-quality proofs.
The 32-member cohort comes from canonical invocation evidence, not cloned rows.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
import pytest

from workbench.agents import build_agents
from workbench.app import PackageIndex, _create_package_app, _write_model
from workbench.native_job_draft import build
from workbench.package_context import PackageContext
from workbench.redesign import Design, build_wil_net_dis_candidate, cited_ids
from workbench.spark_codegen import ContractError
from workbench.target_review import target_review
from workbench.tests.test_cohort_design_actions import notebook
from workbench.tests.test_hosted import offline  # noqa: F401
from workbench.tests.test_redesign import chat, design_for, tool_call
from workbench.tests.test_runtime_planning import actual_xml  # noqa: F401

NAMES = ('wkf_AE2E_DUMMY_CALL_WIL_NET_DIS', 'wkf_AE2E_DUMMY_CALL_WIL_NET_INS',
         'wkf_Load_AE2E_VYVX_HIST_PREP_PULL')
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def canonical(actual_xml, tmp_path_factory):
    model = actual_xml
    source = ROOT / 'input/LumenExample.XML'
    assert model['package']['sha256'] == sha256(source.read_bytes()).hexdigest()
    directory = tmp_path_factory.mktemp('actual-review') / model['package']['sha256']
    directory.mkdir()
    _write_model(directory / 'understanding.json.gz', model)
    manifest = {'sha256': model['package']['sha256'],
        'understanding_sha256': sha256((directory / 'understanding.json.gz').read_bytes()).hexdigest()}
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return PackageIndex(model, directory)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('mode', ['workflow', 'cohort'])
@pytest.mark.parametrize('scenario', ['original', 'bronze'])
def test_actual_individual_and_32_member_cohort_fake_provider_tool_pipeline(
    canonical, tmp_path, monkeypatch, name, mode, scenario):
    monkeypatch.setattr('langchain.chat_models.init_chat_model',
                        lambda *a, **k: pytest.fail('A fake provider is mandatory for this offline test'))
    index = canonical
    ids = {row['name']: row['id'] for row in index.kinds['workflow']}
    workflow = ids[name]
    root = workflow if mode == 'workflow' else ids['wkf_LOAD_AE2E_MASTER_PREP']
    context = PackageContext(index.model)
    before = context.contract(workflow, limit=200)
    assert before == context.contract(workflow, limit=200)
    inventory = target_review(index, root, mode, limit=100)
    assert inventory['workflow_count'] == (1 if mode == 'workflow' else 32)
    assert workflow in inventory['workflow_ids']
    if mode == 'cohort':
        assert {ids[item] for item in NAMES} <= set(inventory['workflow_ids'])
    app = _create_package_app(tmp_path / 'review', ROOT / 'input/LumenExample.XML')
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345)) as client:
        app.state.service.active = index
        response = client.post('/api/cohorts', headers={'X-Workbench-Request': '1'}, json={
            'request_id': 'c' * 32, 'workflow_id': root, 'scope': mode, 'scenario': scenario,
            'input_version': inventory['input_version'], 'consent': True})
        assert response.status_code == 201, response.text
        saved = response.json()
        assert len(saved['workflows']) == (1 if mode == 'workflow' else 32)
        assert not saved['autonomous_worker_available']
        assert not saved['execution_allowed'] and not saved['deployment_allowed']
        assert all(row['status'] == 'awaiting_individual_review' for row in saved['workflows'])
        assert client.get('/api/cohorts/' + saved['id']).json() == saved
    # Real compiled specialist graph and tool validation. The fake provider
    # scripts retrieval before submission; no mocked tool or fabricated receipt.
    design = (build_wil_net_dis_candidate(index.model, workflow, scenario=scenario)
              if name == 'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS' else design_for(index.model, workflow))
    refs = sorted(cited_ids(Design.model_validate(design)))
    assert refs
    # Retrieve distinct evidence in one provider response. One response per
    # original task would exceed the real six-call role budget on large XML.
    route = chat([AIMessage(content='', tool_calls=[
        {'id': f'lookup-{position}', 'name': 'lookup_evidence', 'args': {'evidence_id': ref}}
        for position, ref in enumerate(refs)])] + [
        tool_call('submit_redesign', {'design': design}, 'submit-design'),
        AIMessage(content='Offline source-backed logical outline only.')])
    with build_agents(tmp_path / 'compiled', index.model, workflow, scenario, chat_model=route) as bundle:
        # A direct invocation has no surrounding provider message; the model
        # route below performs real tool-mediated retrieval before submitting.
        result = bundle.specialists['architecture'].invoke(
            {'messages': [{'role': 'user', 'content': 'Review this exact workflow, not an entire cohort.'}]},
            {'configurable': {'thread_id': 'offline-' + scenario + '-' + mode + '-' + workflow}, 'recursion_limit': 200})
        assert route._seen
        submissions = [json.loads(message.content) for message in result['messages']
                       if message.type == 'tool' and message.name == 'submit_redesign']
        assert submissions and submissions[-1].get('recorded') is not False, submissions
        assert len(bundle.redesigns) == 1
        submitted = bundle.redesigns[0]
        assert set(refs) <= set(submitted['retrieved_evidence_ids'])
        assert submitted['design']['tasks']
        other_role = bundle.tools['supervisor']['submit_redesign'].invoke({'design': design})
        assert other_role['recorded'] is False and other_role['error'] == 'design_evidence_not_retrieved'
    assert context.contract(workflow, limit=200) == before
    assert target_review(index, root, mode, limit=100) == inventory


def test_actual_source_binding_and_old_analysis_diagnostics_remain_separate(canonical):
    from workbench.fixture_metadata import observe
    from types import SimpleNamespace
    from unittest.mock import Mock
    index = canonical
    ids = {row['name']: row['id'] for row in index.kinds['workflow']}
    api = SimpleNamespace(schemas=Mock(), tables=Mock())
    identity = SimpleNamespace(client=api, mode='obo', host='https://workspace.example', user_id='offline')
    # The master genuinely has no directly resolved source tables. It must not
    # inherit a child's fixture lookup or turn it into a missing UC-table claim.
    report = observe(index, ids['wkf_LOAD_AE2E_MASTER_PREP'], 'explicit_user_fixtures', identity)
    assert report['binding_status'] == 'no_direct_sources'
    assert report['sources'] == [] and report['child_metadata_reads'] is False
    api.schemas.get.assert_not_called()
    api.tables.get.assert_not_called()
    # Compare current canonical evidence deterministically, never erase older
    # package diagnostics or claim that an old stored analysis was activated.
    assert index.model.get('source_analysis_version') is None
    assert target_review(index, ids['wkf_LOAD_AE2E_MASTER_PREP'], 'cohort')['workflow_count'] == 32


def test_null_condition_and_unsupported_tasks_never_generate_green_native_yaml():
    design = {'tasks': [{'id': 'gate', 'implementation_kind': 'condition_task', 'condition_task': None},
                        {'id': 'load', 'implementation_kind': 'notebook_task'}],
        'edges': [{'source': 'gate', 'target': 'load', 'outcome': 'true', 'evidence_ids': ['captured'],
                   'run_if': 'ALL_SUCCESS'}]}
    manifest = {'tasks': [{'task_id': task['id'], 'implementation_kind': task['implementation_kind'],
                           'status': 'drafted', 'artifact_paths': ['notebooks/load.ipynb']}
                          for task in design['tasks']]}
    artifacts = {'notebooks/load.ipynb': notebook()}
    with pytest.raises(ContractError, match='explicit validated operands'):
        build(design, manifest, artifacts, '/Workspace/Users/offline/review')
    unsupported = deepcopy(design)
    unsupported['tasks'][0]['implementation_kind'] = 'pipeline_task'
    unsupported['edges'] = []
    manifest['tasks'][0]['implementation_kind'] = 'pipeline_task'
    with pytest.raises(ContractError, match='explicitly implemented condition tasks'):
        build(unsupported, manifest, artifacts, '/Workspace/Users/offline/review')