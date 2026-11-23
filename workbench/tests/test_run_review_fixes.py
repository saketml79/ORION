"""Offline regressions for scenario interpretation, immutable review and exact handoff."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from workbench.agents import build_agents, normalize_design_submission
from workbench.diagnostic_relevance import project_diagnostic
from workbench.redesign import artifact_completeness, Design, projected_read, store_for
from workbench.spark_codegen import ContractError, draft_agent_design
from workbench.tests.test_redesign import HEADERS, model, design_for, chat, api, launch, wait


def diagnostic_model():
    value = model()
    value['nodes'].extend([
        {'id': 'conn', 'kind': 'unresolved_reference', 'name': '[REDACTED]',
         'parent_id': 'a', 'attributes': {'expected_kinds': ['connection'], 'status': 'unresolved'}},
        {'id': 'missing_mapping', 'kind': 'unresolved_reference', 'name': 'actual-missing',
         'parent_id': 'b', 'attributes': {'expected_kinds': ['mapping'], 'status': 'unresolved'}}])
    value['edges'].extend([
        {'id': 'c-edge', 'kind': 'connection_reference', 'source': 'a', 'target': 'conn'},
        {'id': 'm-edge', 'kind': 'session_mapping', 'source': 'b', 'target': 'missing_mapping'}])
    value['diagnostics'] = [
        {'code': 'UNRESOLVED_REFERENCE', 'object_id': 'a', 'message': 'old connection notice'},
        {'code': 'UNRESOLVED_REFERENCE', 'object_id': 'b', 'message': 'missing mapping'}]
    return value


def test_uc_source_connection_notice_is_nonblocking_but_missing_mapping_is_retained(tmp_path):
    source = diagnostic_model(); original = deepcopy(source)
    with build_agents(tmp_path, source, 'wf', 'bronze', chat_model=chat()) as bundle:
        graph = bundle.tools['discovery']['graph_slice'].invoke({'limit': 12})
        rows = graph['diagnostics']
        assert rows[0]['relevance']['impact'] == 'non_blocking_context'
        assert rows[0]['relevance']['references'][0]['expected_kinds'] == ['connection']
        assert 'historical informatica connection metadata' in rows[0]['relevance']['reason'].lower()
        assert rows[1]['relevance']['impact'] == 'affected_task_blocker'
        assert rows[1]['relevance']['references'][0]['status'] == 'unresolved'
        assert all(not row['relevance']['resolved'] for row in rows)
        assert source == original


@pytest.mark.parametrize('code', ['UNSUPPORTED_CONTROL_IMPLEMENTATION', 'UNSUPPORTED_MAPPING_IMPLEMENTATION'])
def test_adapter_limitation_not_missing_source(code):
    result = project_diagnostic({'code': code, 'node_id': 'a'}, {}, {}, 'bronze')
    assert result['category'] == 'application_capability'
    assert result['owner_id'] == 'a' and not result['resolved']


def test_new_diagnostic_blockers_need_exact_role_evidence_scope_and_reason(tmp_path):
    with build_agents(tmp_path, diagnostic_model(), 'wf', 'bronze', chat_model=chat()) as bundle:
        tools = bundle.tools['validation']
        rows = tools['graph_slice'].invoke({})['diagnostics']
        value = {'role': 'validation', 'summary': 'Scope needs review', 'status': 'unsupported',
                 'blockers': ['Missing evidence'], 'evidence_ids': [rows[1]['id']]}
        assert tools['submit_finding'].invoke({'finding': value})['recorded'] is False
        value['diagnostic_scopes'] = [{'diagnostic_id': rows[1]['id'], 'owner_id': 'b',
            'impact': 'affected_task_blocker', 'reason': 'Session b has no resolved mapping definition.'}]
        assert tools['submit_finding'].invoke({'finding': value})['recorded'] is True
        # A separate role cannot borrow the first role's citations.
        assert bundle.tools['discovery']['submit_finding'].invoke({'finding': {**value, 'role': 'discovery'}})['recorded'] is False
        value['evidence_ids'] = [rows[0]['id']]
        value['diagnostic_scopes'] = [{'diagnostic_id': rows[0]['id'], 'owner_id': 'a',
            'impact': 'non_blocking_context', 'reason': 'Historical Oracle connection metadata is not the selected UC source.'}]
        assert tools['submit_finding'].invoke({'finding': value})['recorded'] is False


def test_named_field_ambiguity_requires_a_matching_diagnostic_owner(tmp_path):
    with build_agents(tmp_path, diagnostic_model(), 'wf', 'bronze', chat_model=chat()) as bundle:
        tools = bundle.tools['validation']
        rows = tools['graph_slice'].invoke({})['diagnostics']
        diagnostic = rows[1]
        tools['workflow_explanation'].invoke({})
        value = {'role': 'validation', 'summary': 'Reference actual-missing is unresolved.',
                 'status': 'unsupported', 'blockers': ['Resolve the missing mapping.'],
                 'evidence_ids': [diagnostic['id']], 'diagnostic_scopes': [{
                     'diagnostic_id': diagnostic['id'], 'owner_id': 'b',
                     'impact': 'affected_task_blocker', 'reason': 'Exact mapping reference is unresolved.'}]}
        accepted = tools['submit_finding'].invoke({'finding': value})
        assert accepted['recorded'] is True

        unsupported = {**value, 'summary': "Fields 'TEMPLATE_ID' and 'COUNTER' are ambiguous."}
        rejected = tools['submit_finding'].invoke({'finding': unsupported})
        assert rejected['recorded'] is False
        assert rejected['error'] == 'finding_reference_claim_unverified'


def test_administrative_start_is_removed_and_successor_becomes_a_dag_root():
    value = model()
    value['nodes'].extend([
        {'id': 'start-task', 'kind': 'taskinstance', 'name': 'Start', 'parent_id': 'wf',
         'attributes': {'TASKTYPE': 'Start', 'ISENABLED': 'YES'}},
        {'id': 'load-task', 'kind': 'taskinstance', 'name': 'Load', 'parent_id': 'wf',
         'attributes': {'TASKTYPE': 'Session', 'ISENABLED': 'YES'}},
    ])
    design = {'status': 'draft', 'tasks': [
        {'id': 'start', 'implementation_kind': 'notebook_task'},
        {'id': 'load', 'implementation_kind': 'notebook_task'}],
        'edges': [{'source': 'start', 'target': 'load'}],
        'coverage': [
            {'original_task_id': 'start-task', 'proposed_task_ids': ['start'], 'disposition': 'mapped'},
            {'original_task_id': 'load-task', 'proposed_task_ids': ['load'], 'disposition': 'mapped'}],
        'artifact_plan': [{'proposed_task_ids': ['start'], 'relative_path': 'notebooks/start.ipynb'},
                          {'proposed_task_ids': ['load'], 'relative_path': 'notebooks/load.ipynb'}]}
    result = normalize_design_submission(design, value, 'wf')
    assert 'status' not in result
    assert [task['id'] for task in result['tasks']] == ['load']
    assert result['edges'] == []
    start_coverage = next(row for row in result['coverage'] if row['original_task_id'] == 'start-task')
    assert start_coverage['disposition'] == 'removed' and not start_coverage['proposed_task_ids']
    assert [item['relative_path'] for item in result['artifact_plan']] == ['notebooks/load.ipynb']


def test_idempotent_same_role_submission_preserves_different_evidence(tmp_path):
    with build_agents(tmp_path, model(), 'wf', 'original', chat_model=chat()) as bundle:
        tools = bundle.tools['discovery']; tools['graph_slice'].invoke({})
        value = {'role': 'discovery', 'summary': 'Evidence inspected', 'status': 'supported', 'evidence_ids': ['a']}
        first = tools['submit_finding'].invoke({'finding': value})
        repeated = tools['submit_finding'].invoke({'finding': value})
        assert first['recorded'] and repeated['duplicate'] and len(bundle.findings) == 1
        tools['submit_finding'].invoke({'finding': {**value, 'evidence_ids': ['b']}})
        other = bundle.tools['semantics']; other['graph_slice'].invoke({})
        other['submit_finding'].invoke({'finding': {**value, 'role': 'semantics'}})
        assert len(bundle.findings) == 3


def test_condition_and_notebook_completeness_does_not_invalidate_logical_history():
    value = design_for(model())
    value['tasks'][0].update(implementation_kind='condition_task', purpose='rowcount < 35')
    value['artifact_plan'] = [{'relative_path': 'notebooks/legacy.ipynb', 'artifact_kind': 'notebook',
        'proposed_task_ids': ['logical-load'], 'description': 'Logical responsibility', 'evidence_ids': ['a']}]
    accepted = Design.model_validate(value).model_dump()
    result = artifact_completeness(accepted)
    assert {i['code'] for i in result['issues']} == {'STRUCTURED_CONDITION_REQUIRED'}
    assert not result['execution_ready'] and not result['compiled']
    assert accepted['tasks'][0]['condition_task'] is None


def test_run_links_exact_persisted_version_and_history_unchanged(api):
    client, app, workflow = api
    run = launch(client, workflow)
    link = run['submitted_design_links'][0]
    assert link['id'] == run['redesign_refs'][0]['id'] and link['version'] == 1
    assert link['record_id'] == run['id'] and link['workflow_id'] == workflow
    assert link['scenario'] == run['scenario'] and link['package_hash'] == run['package_hash']
    raw_before = app.state.service.record(run['id'], agent=True)
    again = client.get('/api/agents/runs/' + run['id']).json()
    assert again['diagnostic_assessment']['historical_output_unchanged']
    assert app.state.service.record(run['id'], agent=True) == raw_before


def test_agent_action_can_start_unapproved_candidate_writer_only_for_its_exact_run(api):
    client, app, workflow = api
    run = launch(client, workflow)
    service = app.state.service
    design = store_for(service).get(run['redesign_refs'][0]['id'], run['package_hash'],
                                   run['redesign_refs'][0]['version'])
    source_run = service.record(run['id'], agent=True); source_run['generate_code'] = True
    service.update_record(source_run, agent=True)
    assert design['status'] == 'draft'
    assert not design['approved'] and design['generation_allowed'] is False
    with pytest.raises(PermissionError):
        draft_agent_design(service, design, agent_run_id=run['id'], consent=False)
    # Inference is not reached in this fixture because it deliberately provides
    # no artifact route; exact run/design scope is checked first.
    service.runtime.role_models.pop('artifact')
    with pytest.raises(ContractError, match='Existing runtime.role_models\\[artifact\\]'):
        draft_agent_design(service, design, agent_run_id=run['id'], consent=True)


def test_one_agent_action_records_candidate_code_receipt_without_execution(api, monkeypatch):
    client, app, workflow = api
    written = []

    def draft(service, design, *, agent_run_id, consent, budget=None):
        assert consent is True and agent_run_id
        run = service.record(agent_run_id, agent=True)
        assert run['generate_code'] is True and run['status'] == 'running'
        relative = f"generated-drafts/{design['package_hash']}/{design['id']}/{design['version']}/writer-run"
        path = service.root / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / 'manifest.json').write_text('{"status":"blocked"}', encoding='utf-8')
        written.append(design['id'])
        return {'run_id': 'a' * 32, 'relative_directory': relative, 'status': 'blocked',
            'tasks': [{'task_id': task['id'], 'status': 'blocked', 'blockers': ['fixture gap']}
                  for task in design['design']['tasks']]}

    monkeypatch.setattr('workbench.spark_codegen.draft_agent_design', draft)
    response = client.post('/api/agents/run', headers=HEADERS,
        json={'workflow_id': workflow, 'scenario': 'original', 'prompt': 'Redesign',
              'consent': True, 'generate_code': True})
    assert response.status_code == 202, response.text
    result = wait(client, '/api/agents/runs/' + response.json()['id'])
    assert result['status'] == 'partial'
    assert len(result['output']['candidate_code_drafts']) == 1
    receipt = result['output']['candidate_code_drafts'][0]
    assert receipt['status'] == 'completed' and receipt['manifest_status'] == 'blocked'
    job = client.get('/api/jobs/' + receipt['job_id']).json()
    assert job['approved'] is False and job['execution_allowed'] is False and job['deployment_allowed'] is False
    assert written == [result['redesign_refs'][0]['id']]


def test_projection_selects_exact_version_never_latest_and_requires_verified_loop_lessons(monkeypatch):
    from workbench.tests.test_redesign_ui import design_record
    one = design_record(1, id='d' * 64, created_at='2026-11-09T00:00:00Z')
    two = design_record(2, id='d' * 64, created_at='2026-11-09T01:00:00Z')
    index = SimpleNamespace(model={'package': {'sha256': one['package_hash']}},
        nodes={'wf/one': {'kind': 'workflow'}}, evidence_references=lambda refs: [])
    evidence = {'designs': [one, two], 'reviews': []}
    monkeypatch.setattr('workbench.readiness.current_versions', lambda *args: {('wf/one', 'original'): one['input_version']})
    page = projected_read(index, evidence, {})
    assert page['total'] == 2 and set(page['items'][0]['design']) == {'title'}
    selected = projected_read(index, evidence, {'version': ['1']}, one['id'])
    assert selected['version'] == 1 and not selected['generation_allowed']
    assert selected['artifact_completeness']['execution_ready'] is False
    with pytest.raises(Exception) as caught:
        projected_read(index, evidence, {'version': ['99']}, one['id'])
    assert caught.value.status_code == 404
    evidence['designs'][0]['source']['kind'] = 'loop'
    with pytest.raises(ValueError, match='lesson evidence unavailable'):
        projected_read(index, evidence, {})
    evidence['lessons'] = []
    from workbench.app import _hash
    result = projected_read(index, evidence, {'version': ['1']}, one['id'])
    assert result['current_input_version'] == _hash([one['input_version'], []])
    assert result['stale'] and not result['outline_drafting_allowed']