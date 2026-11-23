"""Canonical classifier, execution and Readiness contracts, no remote writes."""
from copy import deepcopy
import json

import pytest

from workbench import delta_state as ds
from workbench.package_context import PackageContext
from workbench.validation_contracts import SourceContracts, target_role, VERSION, pack_source_contract, unpack_source_contract
from workbench.validation_readiness import workflow_readiness
from workbench.validation_metrics import campaign_metrics
from workbench.validation_campaigns import CampaignBody, build_campaign, input_pins
from workbench.app import PackageIndex
from workbench.readiness import assess, FAMILIES
from workbench.tests.test_migration_planning import parse, definitions, mapping, workflow, identity


def business_model(tmp_path, name="W"):
    endpoints = definitions().replace('<TARGET NAME="T"', '<TARGET NAME="T" DATABASETYPE="Oracle"').replace(
        '<TARGETFIELD NAME="id" DATATYPE="integer"/>',
        '<TARGETFIELD NAME="id" DATATYPE="number" PRECISION="10" SCALE="0" NULLABLE="NOTNULL"/>')
    return parse(tmp_path, '<FOLDER NAME="F">' + endpoints + mapping() +
        '<SESSION NAME="session" MAPPINGNAME="M" COMMITINTERVAL="1000"/>' + workflow(name) + '</FOLDER>')


def report_index(tmp_path):
    model = business_model(tmp_path)
    directory = tmp_path / 'canonical'; directory.mkdir()
    (directory / 'manifest.json').write_text(json.dumps({'understanding_sha256': 'a' * 64}), encoding='utf-8')
    index = PackageIndex(model, directory)
    pins = input_pins(index)
    body = CampaignBody(consent=True, workflow_id=identity(model, 'workflow', 'W'), scenario='original',
        expected_package_hash=pins['package_hash'], expected_understanding_sha256=pins['understanding_sha256'],
        source_fixture_catalog='existing_source')
    report = build_campaign(index, body, project_id='P', workspace_id='local', user_id='U', user_label='U', campaign_id='b' * 32)
    return index, report


@pytest.mark.parametrize('name', ['W', 'wkf_AE2E_DUMMY_CALL_NOT_A_WRAPPER', 'Business Name'])
def test_business_selection_is_wired_evidence_not_workflow_label(tmp_path, name):
    model = business_model(tmp_path, name)
    context = PackageContext(model)
    wid = identity(model, 'workflow', name)
    rows = workflow_readiness(context, wid)
    assert len(rows) == 1
    contract = rows[0]['source_execution_contract']
    assert contract['classification'] == 'business_definition_candidate'
    assert contract['business_target_ids'] == [identity(model, 'target', 'T')]
    task = next(t for t in contract['tasks'] if t['task_type'] == 'Session')
    assert task['session_ids'] == [identity(model, 'session', 'session')]
    assert task['mapping_ids'] == [identity(model, 'mapping', 'M')]
    assert contract['mappings'][0]['data_flow_edge_ids']
    assert contract['control_links']
    assert contract['contract_sha256'] == ds._hash({k: v for k, v in contract.items() if k != 'contract_sha256'})
    assert not contract['execution_allowed'] and not contract['output_parity'] and not contract['approved']


def test_counter_launch_excluded_even_when_workflow_has_business_name(tmp_path):
    body = '''<FOLDER NAME="F">
      <SOURCE NAME="S"><SOURCEFIELD NAME="id" DATATYPE="integer"/></SOURCE>
      <TARGET NAME="AE2E_JOB_DEP_CHK_COUNT" DATABASETYPE="Oracle">
        <TARGETFIELD NAME="TEMPLATE_ID"/><TARGETFIELD NAME="COUNTER"/></TARGET>'''
    model = parse(tmp_path, body + mapping(target='AE2E_JOB_DEP_CHK_COUNT') + '''
      <SESSION NAME="session" MAPPINGNAME="M"/>
      <WORKFLOW NAME="RealBusiness"><TASK NAME="call" TYPE="Command">
        <ATTRIBUTE NAME="Command" VALUE="pmcmd startworkflow -f F -nowait Child"/></TASK>
        <TASKINSTANCE NAME="call" TASKNAME="call" TASKTYPE="Command"/>
        <TASKINSTANCE NAME="run" TASKNAME="session" TASKTYPE="Session"/></WORKFLOW>
      <WORKFLOW NAME="Child"/></FOLDER>''')
    context = PackageContext(model)
    wid = identity(model, 'workflow', 'RealBusiness')
    contract = SourceContracts(context).workflow(wid)
    assert contract['classification'] == 'launch_wrapper'
    assert contract['invocations'][0]['candidate_workflow_ids'] == [identity(model, 'workflow', 'Child')]
    assert not contract['invocations'][0]['completion_dependency_established']
    assert workflow_readiness(context, wid) == []


@pytest.mark.parametrize('change', ['connector', 'fields', 'session'])
def test_incomplete_wiring_schema_or_session_does_not_qualify(tmp_path, change):
    model = business_model(tmp_path)
    if change == 'connector':
        model['edges'] = [e for e in model['edges'] if e['kind'] != 'data_flow']
    elif change == 'fields':
        next(n for n in model['nodes'] if n['kind'] == 'target')['fields'] = []
    else:
        model['edges'] = [e for e in model['edges'] if e['kind'] != 'task_definition']
    context = PackageContext(model)
    assert workflow_readiness(context, identity(model, 'workflow', 'W')) == []


def test_known_control_name_with_business_schema_is_unknown_not_name_blacklist():
    node = {'name': 'AE2E_JOB_DEP_CHK_COUNT', 'attributes': {'DATABASETYPE': 'Oracle'},
            'fields': [{'attributes': {'NAME': 'CUSTOMER_ID'}}]}
    assert target_role(node) == 'unclassified'
    node['name'] = 'CUSTOMER'
    assert target_role(node) == 'business_target_candidate'


def test_source_contract_hash_changes_with_session_override_no_mutation(tmp_path):
    model = business_model(tmp_path)
    before = deepcopy(model)
    wid = identity(model, 'workflow', 'W')
    first = SourceContracts(PackageContext(model)).workflow(wid)
    assert model == before
    next(n for n in model['nodes'] if n['kind'] == 'session')['attributes']['COMMITINTERVAL'] = '2000'
    second = SourceContracts(PackageContext(model)).workflow(wid)
    assert first['contract_sha256'] != second['contract_sha256']
    assert [t['contract_sha256'] for t in first['tasks']] != [t['contract_sha256'] for t in second['tasks']]
    assert all(o['physical_binding'] is None and o['snapshot_id'] is None for o in second['runtime_obligations'])


def test_readiness_projects_current_hash_version_obligations_not_boolean_parity(tmp_path):
    index, report = report_index(tmp_path)
    evidence = {k: [] for k in FAMILIES}; evidence['campaigns'] = [report]
    result = assess(index, 'P', evidence, True)
    assert result['counts']['campaign_business_candidates'] == 1
    assert result['counts']['campaign_runtime_not_run'] == 1
    assert result['counts']['campaign_unassigned_targets'] == 1
    assert result['counts']['runtime_validated'] is None
    assert result['counts']['output_parity_approved'] is None
    observed = result['workflows']['items'][0]['business_validation_evidence']
    assert observed['report_sha256'] == report['report_sha256'] and observed['contract_version'] == VERSION
    assert 'NOT_RUN' in result['workflows']['items'][0]['reason']
    assert any(r['code'] == 'TARGET_LAYERS_UNASSIGNED' for r in result['reasons'])
    assert len(report['pinned_wrapper_support']['members']) == 4
    assert not report['pinned_wrapper_support']['qualifies_for_release_gate']
    assert all(t['destination_layer'] is None and not t['approved'] for t in report['target_assignment_contract']['targets'])
    duplicate = deepcopy(report); duplicate['id'] = 'c' * 32
    duplicate['workflow_candidates'][0]['business_outputs_verified'] = True
    duplicate['report_sha256'] = ds._hash({k: v for k, v in duplicate.items() if k != 'report_sha256'})
    evidence['campaigns'].append(duplicate)
    again = assess(index, 'P', evidence, True)
    assert again['counts']['campaign_business_candidates'] == 1
    assert again['release_gate']['verified_business_workflows'] == 0 and not again['business_validation']['output_parity']


@pytest.mark.parametrize('change', ['understanding', 'analysis', 'classifier', 'missing', 'bound'])
def test_readiness_missing_or_stale_evidence_remains_unknown(tmp_path, change):
    index, report = report_index(tmp_path)
    evidence = {k: [] for k in FAMILIES}; evidence['campaigns'] = [report]
    if change == 'understanding': report['pins']['understanding_sha256'] = 'd' * 64
    if change == 'analysis': report['pins']['analysis_version'] = 'd' * 64
    if change == 'classifier': report['semantic_fingerprints']['validation_contracts.py'] = 'd' * 64
    if change == 'missing': evidence['campaigns'] = []
    report['report_sha256'] = ds._hash({k: v for k, v in report.items() if k != 'report_sha256'})
    result = assess(index, 'P', evidence, change != 'bound')
    assert result['counts']['campaign_business_candidates'] is None
    assert result['counts']['runtime_validated'] is None and not result['deployment_allowed']
    assert any(r['code'] == 'CURRENT_SOURCE_EXECUTION_CONTRACTS_UNAVAILABLE' for r in result['reasons'])


def test_nested_contract_corruption_rejected_even_if_outer_hash_recomputed(tmp_path):
    index, report = report_index(tmp_path)
    packed = report['workflow_candidates'][0]['source_execution_contract']
    packed['decoded_sha256'] = 'f' * 64
    report['report_sha256'] = ds._hash({k: v for k, v in report.items() if k != 'report_sha256'})
    with pytest.raises(ValueError, match='[Ss]ource execution contract mismatch'):
        campaign_metrics([report], {identity(index.model, 'workflow', 'W')}, complete=True)


@pytest.mark.parametrize('alias,reason', [
    ('external-prod', 'external_connection_definition_not_exported'),
    ('jdbc:oracle://private-host', 'connection_value_redacted'),
    ('', 'connection_value_redacted'),
    (None, 'connection_identifier_not_captured')])
def test_external_connections_not_unresolved_but_still_block_physical_binding(tmp_path, alias, reason):
    from workbench.migration_planning import PlanningIndex
    model = parse(tmp_path, '<FOLDER NAME="F">' + definitions() + mapping() +
        '<SESSION NAME="session" MAPPINGNAME="M"><SESSIONEXTENSION NAME="writer" SINSTANCENAME="dst" TRANSFORMATIONTYPE="Target Definition">' +
        ('<CONNECTIONREFERENCE CONNECTIONTYPE="Relational"/>' if alias is None else f'<CONNECTIONREFERENCE CONNECTIONNAME="{alias}" CONNECTIONTYPE="Relational"/>') +
        '</SESSIONEXTENSION></SESSION>' + workflow('W') + '</FOLDER>')
    result = PlanningIndex(model).workflow_contract(identity(model, 'workflow', 'W'))
    assert result['coverage']['unresolved_references'] == 0
    gap = result['external_connection_metadata']
    assert gap['total'] == 1 and gap['items'][0]['attributes']['value']['status_reason'] == reason
    assert 'external_connection_metadata_required' in result['readiness']['blockers']
    assert result['targets']['items'][0]['schema']['status'] == 'captured_unverified'
    assert not result['readiness']['execution_ready']
    assert 'private-host' not in json.dumps(result)


def test_exported_connection_definition_resolves_without_invented_physical_binding(tmp_path):
    from workbench.migration_planning import PlanningIndex
    model = parse(tmp_path, '<FOLDER NAME="F">' + definitions() + mapping() +
        '<CONNECTION NAME="external-prod" TYPE="Relational"/>'
        '<SESSION NAME="session" MAPPINGNAME="M"><SESSIONEXTENSION NAME="writer" SINSTANCENAME="dst" TRANSFORMATIONTYPE="Target Definition">'
        '<CONNECTIONREFERENCE CONNECTIONNAME="external-prod" CONNECTIONTYPE="Relational"/>'
        '</SESSIONEXTENSION></SESSION>' + workflow('W') + '</FOLDER>')
    result = PlanningIndex(model).workflow_contract(identity(model, 'workflow', 'W'))
    assert result['external_connection_metadata']['total'] == 0
    assert result['coverage']['unresolved_references'] == 0
    assert any(e['kind'] == 'connection_reference' for e in result['definition_links']['items'])
    assert result['targets']['items'][0]['physical_binding'] is None
    assert not result['readiness']['execution_ready']


def test_exclusive_export_hashes_and_comparison_are_not_business_parity(tmp_path, monkeypatch):
    from hashlib import sha256
    from workbench.validation_campaigns import export_local_evidence
    import workbench.wil_parent_child as external
    monkeypatch.setattr(external, 'discover_external_evidence', lambda root: {'status': 'not_acquired'})
    index, previous = report_index(tmp_path)
    baseline = tmp_path / 'previous.json'; baseline.write_text(ds._json(previous), encoding='utf-8')
    before = baseline.read_bytes()
    output = tmp_path / 'new-export'
    result = export_local_evidence(tmp_path / 'planning.xml', output,
        workflow_id=previous['workflow_id'], source_fixture_catalog='existing_source', compare_campaign=baseline)
    assert result['manifest_verified'] and not result['deployment_allowed']
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert set(manifest) == {'campaign.json', 'proof.json', 'shortlist.json', 'source-execution-contracts.json', 'target-assignments.json'}
    for name, expected in manifest.items():
        raw = (output / name).read_bytes()
        assert sha256(raw).hexdigest() == expected['sha256'] and len(raw) == expected['bytes']
    shortlist = json.loads((output / 'shortlist.json').read_bytes())
    assert shortlist['comparison']['previous_business_definitions'] == 1
    assert shortlist['comparison']['previous_nonbusiness_definitions'] == 0
    assert not shortlist['comparison']['output_parity'] and not shortlist['output_parity']
    assert baseline.read_bytes() == before
    before_export = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        export_local_evidence(tmp_path / 'planning.xml', output,
            workflow_id=previous['workflow_id'], source_fixture_catalog='existing_source')
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before_export


def test_lossless_source_contract_encoding_deterministic_and_bounded(tmp_path):
    from workbench.validation_contracts import MAX_CONTRACT_BYTES
    model = business_model(tmp_path)
    original = SourceContracts(PackageContext(model)).workflow(identity(model, 'workflow', 'W'))
    packed = pack_source_contract(original)
    assert packed == pack_source_contract(original)
    assert unpack_source_contract(packed) == original
    for change in ({'decoded_bytes': True}, {'decoded_bytes': MAX_CONTRACT_BYTES + 1},
                   {'decoded_bytes': packed['decoded_bytes'] - 1}, {'decoded_sha256': 'f' * 64},
                   {'workflow_id': 'f' * 64}):
        with pytest.raises(ValueError):
            unpack_source_contract({**packed, **change})


def test_nested_evidence_budget_is_unknown_not_partial_success(tmp_path):
    index, report = report_index(tmp_path)
    report['workflow_candidates'][0]['source_execution_contract']['decoded_bytes'] = 9 * 1024 * 1024
    report['report_sha256'] = ds._hash({k: v for k, v in report.items() if k != 'report_sha256'})
    result = campaign_metrics([report], {identity(index.model, 'workflow', 'W')}, complete=True)
    assert not result['decoded_evidence_complete'] and not result['coverage_available']
    assert result['counts']['campaign_business_candidates'] is None
    assert result['semantic_stale_campaigns'] == 0 and not result['output_parity']


def test_canonical_reference_preserves_all_source_evidence_and_fails_stale_materialization(tmp_path):
    from workbench.validation_contracts import campaign_source_contract, verify_materialized_contract
    index, report = report_index(tmp_path)
    wid = identity(index.model, 'workflow', 'W')
    full = SourceContracts(PackageContext(index.model)).workflow(wid)
    packet = report['workflow_candidates'][0]['source_execution_contract']
    assert verify_materialized_contract(packet, full, report['pins']) == full
    compact = unpack_source_contract(packet)
    refs = compact['canonical_evidence_reference']
    assert refs['materialized_contract_sha256'] == full['contract_sha256']
    assert refs['runtime_obligations_sha256'] == ds._hash(full['runtime_obligations'])
    assert refs['runtime_obligation_count'] == len(full['runtime_obligations'])
    assert compact['tasks'] == full['tasks']
    assert compact['mappings'][0]['data_flow_edge_count'] == len(full['mappings'][0]['data_flow_edge_ids'])
    assert packet == campaign_source_contract(full, report['pins'])
    with pytest.raises(ValueError, match='materialization'):
        verify_materialized_contract(packet, full, {**report['pins'], 'understanding_sha256': 'f' * 64})
    changed = deepcopy(full); changed['runtime_obligations'][0]['metadata_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='materialization'):
        verify_materialized_contract(packet, changed, report['pins'])