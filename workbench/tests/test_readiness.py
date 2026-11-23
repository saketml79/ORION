"""Offline actual service/API evidence, no models or business execution."""
from copy import deepcopy
import json

import pytest

from workbench.readiness import assess, current_versions, local_evidence
from workbench.tests.test_app import ready, small_xml, HEADERS
from workbench.redesign import store_for


def test_empty_tracking_is_unknown_not_converted_and_get_is_read_only(ready):
    client, app = ready
    service = app.state.service
    before = {str(p): p.read_bytes() for p in service.state.rglob('*') if p.is_file()}
    response = client.get('/api/readiness?limit=1')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['counts']['total_workflows'] == 2
    assert result['counts']['structurally_analyzed'] == 2
    assert result['counts']['logical_draft'] == 0
    for metric in ('executable', 'runtime_validated', 'output_parity_approved'):
        assert result['counts'][metric] is None
    assert result['release_gate']['status'] == 'BLOCKED'
    assert not result['production_ready'] and not result['execution_allowed']
    assert result['workflows']['truncated'] and len(result['workflows']['items']) == 1
    assert all(a['latest_status'] == 'not_assessed' and a['model_accuracy'] is None for a in result['agents'])
    # Existing HTTP audit is permitted. Readiness must not touch review/job/agent stores.
    after = {str(p): p.read_bytes() for p in service.state.rglob('*') if p.is_file()}
    assert {k: v for k, v in before.items() if 'audit.sqlite' not in k} == {k: v for k, v in after.items() if 'audit.sqlite' not in k}


def test_actual_snapshot_freshness_design_and_generated_receipts(ready):
    client, app = ready
    service, index = app.state.service, app.state.service.require()
    package = index.model['package']['sha256']
    workflow = index.kinds['workflow'][0]['id']
    inputs = service.agent_inputs(index, workflow, 'original')
    run = {'id': 'a' * 32, 'workflow_id': workflow, 'scenario': 'original', 'package_hash': package,
           'input_version': inputs['input_version'], 'status': 'completed', 'created_at': '2026-11-06',
           'output': {'findings': [{'role': 'semantics', 'summary': 'Actual stored fixture handoff', 'status': 'needs_information',
                                    'evidence_ids': [workflow], 'blockers': ['BINDING_REQUIRED']}],
                      'usage': {'total_tokens': None, 'known_total_tokens': 12}}}
    service.update_record(run, agent=True)
    source = {'record_id': run['id'], 'runtime_run_id': 'r', 'submission_index': 0, 'kind': 'agent', 'input_version': inputs['input_version']}
    design = store_for(service).append(package, workflow, 'original',
        {'design': {'blockers': []}, 'role': 'architecture', 'retrieved_evidence_ids': [workflow]}, source, 0)
    service.update_record({'id': 'b' * 32, 'kind': 'spark_draft', 'workflow_id': workflow, 'package_hash': package,
        'scenario': 'original', 'input_version': inputs['input_version'], 'status': 'completed',
        'design_id': design['id'], 'design_version': 1, 'manifest_sha256': 'c' * 64})
    evidence, complete = local_evidence(service, package, '__legacy__')
    assert complete and current_versions(index, evidence)[workflow, 'original'] == inputs['input_version']
    result = client.get('/api/readiness').json()
    assert result['counts']['logical_draft'] == result['counts']['generated_draft_receipts'] == result['counts']['agent_assessed'] == 1
    assert sum(result['stage_counts'].values()) == 2
    semantics = next(a for a in result['agents'] if a['role'] == 'semantics')
    assert semantics['finding_count'] == 1 and semantics['freshness'] == 'current'
    assert semantics['reported_usage']['total_tokens'] is None
    # A changed analysis invalidates the exact advisory hash, not just package membership.
    changed = deepcopy(index.model)
    changed['source_analysis_version'] = 'd' * 64
    original = index.model
    index.model = changed
    try:
        stale = assess(index, '__legacy__', evidence, True)
        assert stale['counts']['logical_draft'] == stale['counts']['generated_draft_receipts'] == 0
        assert next(w for w in stale['workflows']['items'] if w['workflow_id'] == workflow)['design_freshness'] == 'stale'
    finally:
        index.model = original
    bounded = assess(index, '__legacy__', evidence, False)
    assert bounded['counts']['logical_draft'] is None
    assert any(r['code'] == 'EVIDENCE_BOUND_REACHED' for r in bounded['reasons'])


def test_campaign_fixture_passes_do_not_establish_runtime_or_parity(ready):
    client, app = ready
    context = client.get('/api/validation/context').json()
    pins = context['pins']
    wid = app.state.service.require().kinds['workflow'][1]['id']
    report = client.post('/api/validation/campaigns', headers=HEADERS, json={
        'consent': True, 'workflow_id': wid, 'scenario': 'original',
        'expected_package_hash': pins['package_hash'], 'expected_understanding_sha256': pins['understanding_sha256'],
        'expected_analysis_version': pins['analysis_version'], 'source_fixture_catalog': 'existing_source'} )
    assert report.status_code == 201, report.text
    result = client.get('/api/readiness').json()
    assert result['campaigns']['current'] == 1
    assert result['campaigns']['check_statuses']['passing'] > 0
    assert result['counts']['runtime_validated'] is None and result['counts']['output_parity_approved'] is None
    assert result['release_gate']['verified_business_workflows'] == 0
    assert result['issue_counts']['PAIRED_REFERENCE_AND_CANDIDATE_SNAPSHOTS_REQUIRED'] == 1
    # Never include another project receipt even if a package hash happens to match.
    path = app.state.service.state / 'validation-campaigns' / pins['package_hash'] / (report.json()['id'] + '.json')
    # A valid historical inline receipt belongs to another project. Corrupting
    # a new manifest instead must fail closed, not silently omit corruption.
    from workbench import delta_state as ds
    value = report.json(); value['project_id'] = 'another-project'
    value['report_sha256'] = ds._hash({k: v for k, v in value.items() if k != 'report_sha256'})
    path.write_text(json.dumps(value), encoding='utf-8')
    assert client.get('/api/readiness').json()['campaigns']['current'] == 0


def test_missing_report_artifact_is_unavailable_not_partial_green(ready):
    from pathlib import Path
    client, app = ready
    pins = client.get('/api/validation/context').json()['pins']
    workflow = app.state.service.require().kinds['workflow'][1]['id']
    response = client.post('/api/validation/campaigns', headers=HEADERS, json={
        'consent': True, 'workflow_id': workflow, 'scenario': 'original',
        'expected_package_hash': pins['package_hash'], 'expected_understanding_sha256': pins['understanding_sha256'],
        'expected_analysis_version': pins['analysis_version'], 'source_fixture_catalog': 'existing_source'})
    assert response.status_code == 201
    report = response.json()
    path = app.state.service.state / 'validation-campaigns' / pins['package_hash'] / (report['id'] + '.json')
    manifest = json.loads(path.read_bytes())
    artifact = Path(manifest['artifact']['volume_path'])
    raw = artifact.read_bytes()
    artifact.write_bytes(b'X' + raw[1:])
    assert client.get('/api/validation/campaigns/' + report['id'] + '/export').status_code == 409
    artifact.unlink()
    result = client.get('/api/readiness')
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['counts']['campaign_business_candidates'] is None
    assert value['business_validation']['coverage_available'] is False
    assert value['campaigns']['check_statuses'] == {}
    assert value['campaigns']['receipts'][0]['artifact_availability'] == 'unavailable'
    assert any(r['code'] == 'CAMPAIGN_ARTIFACT_UNAVAILABLE' for r in value['reasons'])
    assert value['release_gate']['verified_business_workflows'] == 0
    assert not value['deployment_allowed']


@pytest.mark.parametrize('query', ['limit=101', 'limit=0', 'offset=-1', 'scenario=unknown'])
def test_invalid_paging_and_scenario(ready, query):
    client, _ = ready
    assert client.get('/api/readiness?' + query).status_code == 422


def test_stale_selection_rejected(ready):
    client, _ = ready
    # Existing project middleware rejects a package header without its project.
    assert client.get('/api/readiness', headers={'X-Workbench-Package': '0' * 64}).status_code == 400