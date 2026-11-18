"""Canonical lineage completeness, scope, paging and exclusive artifact integrity."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from workbench import delta_state as ds
from workbench.migration_lineage import Lineage, FAMILIES, export, page
from workbench.tests.test_app import ready, small_xml
from workbench.tests.test_package_context import parsed


def index_for(model, directory):
    directory.mkdir(exist_ok=True)
    (directory / 'manifest.json').write_text(json.dumps({'understanding_sha256': ds._hash(model)}), encoding='utf-8')
    return SimpleNamespace(model=model, directory=directory)


@pytest.mark.parametrize('wait', ['wait', 'nowait'])
def test_exact_invocation_and_all_normal_form_records_preserved(tmp_path, wait):
    model, ids, _ = parsed(tmp_path, command=f'pmcmd startworkflow -f F -{wait} Child')
    before = deepcopy(model)
    lineage = Lineage(index_for(model, tmp_path / 'index'))
    unit = lineage.inventory(ids['Parent'])
    assert len(unit['invocations']) == 1
    assert unit['invocations'][0]['wait_mode'] == wait
    assert not unit['invocations'][0]['completion_dependency']
    cohort = set(unit['cohort_ids'])
    assert cohort == {ids['Parent'], ids['Child']} and ids['Sibling'] not in cohort
    by_id = {n['id']: n for n in unit['nodes']}
    original = {n['id']: n for n in model['nodes']}
    assert all(original[e['source']] and original[e['target']] for e in unit['edges'])
    assert all(e in model['edges'] for e in unit['edges'])
    for field in unit['fields']:
        assert field['record'] == original[field['owner_id']]['fields'][field['ordinal']]
    for prop in unit['properties']:
        assert prop['record'] == original[prop['owner_id']]['properties'][prop['ordinal']]
    assert model == before
    assert len(by_id) == len(unit['nodes'])
    for family in FAMILIES:
        all_rows, offset = [], 0
        while True:
            response = lineage.response('project', wid=ids['Parent'], family=family, offset=offset, limit=1)
            batch = response['page']; all_rows.extend(batch['items'])
            assert batch['total'] == len(unit[family])
            if not batch['has_more']:
                break
            assert batch['next_offset'] > offset
            offset = batch['next_offset']
        assert all_rows == unit[family]


def test_missing_dynamic_invocation_and_input_version_rejection(tmp_path):
    model, ids, _ = parsed(tmp_path, command='pmcmd startworkflow -f $$FOLDER -nowait Child')
    lineage = Lineage(index_for(model, tmp_path / 'index'))
    response = lineage.response('project', wid=ids['Parent'])
    call = response['invocation_neighborhood']['items'][0]
    assert call['candidate_workflow_ids'] == [] and call['resolution'] == 'unresolved_or_unsupported'
    assert response['coverage']['canonical_inventory_complete']
    assert not response['coverage']['semantic_lineage_complete']
    assert response['selected']['proposed_target']['name'] is None
    assert not response['execution_allowed'] and not response['deployment_allowed']
    with pytest.raises(HTTPException) as failure:
        lineage.response('project', wid=ids['Parent'], expected='stale')
    assert failure.value.status_code == 409
    with pytest.raises(ValueError):
        lineage.response('project', wid=ids['Parent'], root=ids['Sibling'])


def test_mapping_target_projection_uses_canonical_mapping_and_stays_unassigned(tmp_path):
        model, ids, _ = parsed(tmp_path)
        lineage = Lineage(index_for(model, tmp_path / 'index'))
        child = lineage.workflow(ids['Child'])
        assert [row['mapping_name'] for row in child['mapping_targets']] == ['Read']
        assert child['mapping_targets'][0]['targets'] == []
        assert not child['converted_complete'] and child['runtime_status'] == 'NOT_RUN'
        target_model, target_ids, _ = parsed(tmp_path, extra='''<WORKFLOW NAME="Writer">
            <TASKINSTANCE NAME="write" TASKTYPE="Session" TASKNAME="WriteSession"/></WORKFLOW>''')
        target_lineage = Lineage(index_for(target_model, tmp_path / 'target-index'))
        writer = target_lineage.workflow(target_ids['Writer'])
        assert writer['mapping_targets'][0]['mapping_name'] == 'Write'
        target = writer['mapping_targets'][0]['targets'][0]
        assert target['name'] == 'Data' and target['destination_status'] == 'unassigned'
        assert not target['approved'] and target['wired_source_instance_count'] == 0


def test_endpoint_observational_and_invalid_paging(ready):
    client, app = ready
    before = {str(p): p.read_bytes() for p in app.state.service.state.rglob('*') if p.is_file() and 'audit.sqlite' not in str(p)}
    assert client.get('/api/readiness/lineage?limit=1').status_code == 200
    wid = app.state.service.require().kinds['workflow'][0]['id']
    result = client.get('/api/readiness/lineage', params={'workflow_id': wid}).json()
    assert result['selected']['workflow_id'] == wid
    assert result['scope']['project_id'] == '__legacy__'
    for query in ('limit=201', 'offset=-1', 'family=unknown', 'root_id=unknown', 'input_version=wrong'):
        assert client.get('/api/readiness/lineage?workflow_id=' + wid + '&' + query).status_code in {409, 422}
    after = {str(p): p.read_bytes() for p in app.state.service.state.rglob('*') if p.is_file() and 'audit.sqlite' not in str(p)}
    assert before == after


def test_page_byte_bound_has_exact_continuation(monkeypatch):
    monkeypatch.setattr('workbench.migration_lineage.PAGE_BYTES', 30)
    rows = [{'id': '1234567890'}, {'id': 'abcdefghij'}]
    result = page(rows, 0, 100)
    assert result['items'] == rows[:1] and result['next_offset'] == 1
    assert page(rows, 1, 100)['items'] == rows[1:]


def test_exclusive_export_manifest_and_does_not_modify_inputs(ready, tmp_path):
    _, app = ready
    index = app.state.service.require()
    target = tmp_path / 'export'
    before = deepcopy(index.model)
    result = export(index, target)
    raw = (target / 'lineage.json').read_bytes()
    manifest = json.loads((target / 'manifest.json').read_bytes())
    assert manifest['artifacts']['lineage.json'] == {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
    report = json.loads(raw)
    assert report['export_complete'] and not result['semantic_lineage_complete']
    assert report['report_sha256'] == ds._hash({k: v for k, v in report.items() if k != 'report_sha256'})
    assert report['totals'] == {k: len(v) for k, v in report['linked_objects'].items()}
    with pytest.raises(FileExistsError):
        export(index, target)
    assert index.model == before


def test_real_saved_lumen_ten_targets_master19_and_field_connectors():
    from workbench.app import PackageIndex
    from workbench.validation_contracts import LEDGER_PRIORITY_IDS
    root = Path(__file__).resolve().parents[2]
    index = PackageIndex.load(root / 'output/workbench/packages/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba')
    lineage = Lineage(index)
    assert lineage.selected == list(LEDGER_PRIORITY_IDS)
    unit = lineage.inventory(lineage.selected[0])
    assert len(unit['cohort_ids']) == 32
    master = next(n['id'] for n in index.model['nodes'] if n['kind'] == 'workflow' and n['name'] == 'wkf_LOAD_AE2E_MASTER_PREP')
    calls = [r for r in unit['invocations'] if r['caller_workflow_id'] == master]
    assert len(calls) == 19 and all(r['wait_mode'] == 'nowait' for r in calls)
    assert master not in lineage.selected and master in unit['cohort_ids']
    assert any(e['kind'] == 'field_flow' for e in unit['edges'])
    assert any(e['kind'] == 'workflow_control' and e.get('attributes') for e in unit['edges'])
    assert any(r['status'] == 'missing_or_ambiguous' for r in unit['obligations'])
    for wid in lineage.selected:
        summary = lineage.workflow(wid)
        assert not summary['converted_complete'] and summary['runtime_status'] == 'NOT_RUN'
        assert all(t['destination_catalog'] is None for t in summary['target_assignments'])


def test_hosted_route_fresh_scoped_no_restore_or_jobs(backend, uploaded, tmp_path, monkeypatch):
    from workbench.delta_runtime import DeltaRuntime
    from workbench.tests.test_hosted_object_reads import full_index, selected
    from workbench.tests.test_hosted import browser
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    wid = index.kinds['workflow'][0]['id']
    before = deepcopy(backend.rows)
    monkeypatch.setattr(DeltaRuntime, 'restore', lambda *a, **k: pytest.fail('No full restore'))
    monkeypatch.setattr(DeltaRuntime, 'flush', lambda *a, **k: pytest.fail('No publication'))
    backend.calls.clear()
    with browser(backend) as client:
        for _ in range(2):
            result = client.get('/api/readiness/lineage', headers=selected(project, package), params={'workflow_id': wid, 'limit': 1})
            assert result.status_code == 200, result.text
            assert result.json()['scope']['project_id'] == project
        result = client.get('/api/readiness/lineage', headers=selected(project, package), params={'workflow_id': wid, 'input_version': 'stale'})
        assert result.status_code == 409
    assert backend.rows == before
    assert not any('.`jobs`' in text or text.startswith(('CREATE', 'MERGE')) for _, action, text in backend.calls if action == 'sql')


# Existing hosted fixtures retain their strict offline fresh-user behavior.
from workbench.tests.test_hosted import backend, offline
from workbench.tests.test_hosted_registry import uploaded


def saved_trace(scope, wid, tid):
    return {'scope': scope, 'workflows': {'items': [{'workflow_id': wid, 'proposed_state': {
        'design': {'id': 'design', 'version': 1, 'freshness': 'current', 'input_version': 'version',
            'current_input_version': 'version', 'design': {'title': 'Authored outline',
                'tasks': [{'id': 'authored', 'label': 'Authored task', 'implementation_kind': 'notebook_task'}],
                'coverage': [{'original_task_id': tid, 'proposed_task_ids': ['authored'], 'disposition': 'mapped',
                    'rationale': 'Explicit fixture correspondence, not a name match.'}]}},
        'draft_receipts': [{'design_id': 'design', 'design_version': 1, 'input_version': 'version',
            'artifact_hashes': {'notebooks/authored.ipynb': 'a' * 64},
            'task_statuses': [{'task_id': 'authored', 'status': 'drafted', 'artifact_paths': ['notebooks/authored.ipynb']}]}]
    }}]}}


@pytest.mark.parametrize('case', ['valid', 'no_mapping', 'cross_project', 'cross_package', 'stale', 'missing_target', 'unmapped', 'corrupt_hash', 'wrong_design'])
def test_trace_exact_bindings_and_fail_closed_evidence(tmp_path, case):
    model, ids, _ = parsed(tmp_path)
    view = Lineage(index_for(model, tmp_path / 'index'))
    source = view.response('project', wid=ids['Child'])
    tid = source['selected']['task_instance_ids'][0]
    saved = deepcopy(saved_trace(source['scope'], ids['Child'], tid))
    state = saved['workflows']['items'][0]['proposed_state']
    if case == 'no_mapping': state['design']['design']['coverage'] = []
    if case == 'cross_project': saved['scope']['project_id'] = 'other'
    if case == 'cross_package': saved['scope']['pins']['package_hash'] = 'b' * 64
    if case == 'stale': state['design']['current_input_version'] = 'new'
    if case == 'missing_target': state['design']['design']['coverage'][0]['proposed_task_ids'] = ['missing']
    if case == 'unmapped': state['design']['design']['coverage'][0]['disposition'] = 'removed'
    if case == 'corrupt_hash': state['draft_receipts'][0]['artifact_hashes']['notebooks/authored.ipynb'] = 'tampered'
    if case == 'wrong_design': state['draft_receipts'][0]['design_id'] = 'other'
    before = deepcopy(model)
    result = view.response('project', wid=ids['Child'], family='trace', saved=saved, limit=1)
    row = result['page']['items'][0]
    assert row['source']['id'] == tid and row['source']['sessions'][0]['name'] == 'ReadSession'
    assert row['source']['mappings'][0]['name'] == 'Read'
    assert row['execution_result'] == 'unknown' and not row['whole_workflow_converted']
    if case in {'valid', 'corrupt_hash', 'wrong_design'}:
        assert row['targets'][0]['task_key'] == 'authored'
        assert bool(row['targets'][0]['artifacts']) == (case == 'valid')
    else:
        assert row['targets'] == [] and row['status'] == 'not_mapped'
    assert not result['coverage']['entire_package_converted'] and model == before


def test_reference_uses_real_contract_excerpts_not_current_user_or_runtime():
    from workbench.lineage_example import reference
    root = Path(__file__).resolve().parents[2]
    contract_raw = (root / 'output/workbench/vyvx-mock-demo-20261107-v2/contract.json').read_bytes()
    contract = json.loads(contract_raw)
    manifest = json.loads((root / 'output/workbench/vyvx-mock-demo-20261107-v2/manifest.json').read_bytes())
    example = reference()
    assert example['contract_sha256'] == sha256(contract_raw).hexdigest() == manifest['artifacts']['contract.json']
    assert example['source_input_hash'] == contract['package_sha256']
    assert example['task_correspondences'] == [] and not example['current_user_correlation']
    assert example['execution_result'] == 'historical_mock_success'
    assert 'not a live status feed' in example['execution_note']
    evidence = example['execution_evidence']
    reports = root / 'output/workbench/vyvx-mock-demo-20261107-v2/reports'
    for label in ('fresh', 'resume'):
        proof_path = next(reports.glob(f'*-{label}-VERIFIED.json'))
        assert evidence[f'{label}_proof_sha256'] == sha256(proof_path.read_bytes()).hexdigest()
        submitted = json.loads(next(reports.glob(f'*-{label}-submitted.json')).read_bytes())
        assert evidence[f'{label}_run_id'] == str(submitted['run_id'])
    assert evidence['destinations_empty'] == evidence['sources_preserved'] == 7
    assert evidence['informatica_parity'] is False
    assert example['changes'].items() <= contract['mock_substitutions'].items()
    by_name = {t['name']: t for t in contract['tables'].values()}
    for row in example['table_correspondences']:
        original = by_name[row['target']]
        assert original['xml_identity'] and row['column_count'] == len(original['columns'])
        assert row['columns_excerpt'] == original['columns'][:4]
    packed = json.dumps(example)
    assert '@' not in packed and '/Workspace/Users/' not in packed
    assert evidence['job_url'].endswith('/jobs/878190924157330')


def test_reference_integrity_tampering_rejected(monkeypatch):
    from workbench.lineage_example import reference
    monkeypatch.setattr('workbench.lineage_example.REFERENCE_SHA256', '0' * 64)
    with pytest.raises(ValueError, match='integrity'):
        reference()


def test_reference_never_injected_without_opt_in_and_trace_pages(tmp_path):
    model, ids, _ = parsed(tmp_path)
    view = Lineage(index_for(model, tmp_path / 'index'))
    result = view.response('project', wid=ids['Child'], family='trace', example=True, limit=1)
    assert result['illustrative_example']['example_only']
    assert result['page']['items'][0]['targets'] == []
    assert 'illustrative_example' not in view.response('project', wid=ids['Child'], family='trace')
    assert result['transformation_trace']['source_package_hash'] != result['illustrative_example']['source_input_hash']


def test_local_trace_endpoint_read_only_with_optional_reference(ready):
    client, app = ready
    before = deepcopy(app.state.service.require().model)
    wid = app.state.service.require().kinds['workflow'][0]['id']
    result = client.get('/api/readiness/lineage', params={'family': 'trace', 'example': 'true', 'workflow_id': wid})
    assert result.status_code == 200, result.text
    assert result.json()['illustrative_example']['execution_result'] == 'historical_mock_success'
    assert result.json()['transformation_trace']['execution_result'] == 'unknown'
    assert app.state.service.require().model == before


def test_trace_paging_preserves_every_source_task_and_unknown_destination(tmp_path):
    model, ids, _ = parsed(tmp_path, extra='''<WORKFLOW NAME="Multi">
        <TASKINSTANCE NAME="one" TASKTYPE="Session" TASKNAME="ReadSession"/>
        <TASKINSTANCE NAME="two" TASKTYPE="Session" TASKNAME="ReadSession"/>
        <TASKINSTANCE NAME="three" TASKTYPE="Session" TASKNAME="WriteSession"/>
        </WORKFLOW>''')
    view = Lineage(index_for(model, tmp_path / 'index'))
    first = view.response('project', wid=ids['Multi'], family='trace', limit=1)
    assert first['page']['total'] == 3 and first['page']['next_offset'] == 1
    rows = [view.response('project', wid=ids['Multi'], family='trace', limit=1, offset=i)['page']['items'][0] for i in range(3)]
    assert len({r['id'] for r in rows}) == 3 and all(r['targets'] == [] for r in rows)


def test_hosted_trace_scoped_existing_state_only_no_restore_or_writes(backend, uploaded, tmp_path, monkeypatch):
    from workbench.delta_runtime import DeltaRuntime
    from workbench.tests.test_hosted_object_reads import full_index, selected
    from workbench.tests.test_hosted import browser
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    wid = index.kinds['workflow'][0]['id']
    before = deepcopy(backend.rows)
    monkeypatch.setattr(DeltaRuntime, 'restore', lambda *a, **k: pytest.fail('No full restore'))
    monkeypatch.setattr(DeltaRuntime, 'flush', lambda *a, **k: pytest.fail('No publication'))
    backend.calls.clear()
    with browser(backend) as client:
        result = client.get('/api/readiness/lineage', headers=selected(project, package),
            params={'workflow_id': wid, 'family': 'trace', 'example': 'true'})
        assert result.status_code == 200, result.text
        value = result.json()
        assert value['scope']['project_id'] == project and value['scope']['pins']['package_hash'] == package
        assert value['illustrative_example']['example_only'] and not value['illustrative_example']['current_user_correlation']
        assert value['transformation_trace']['execution_result'] == 'unknown'
    assert backend.rows == before
    assert not any(text.startswith(('CREATE', 'MERGE', 'INSERT', 'UPDATE', 'DELETE')) for _, action, text in backend.calls if action == 'sql')