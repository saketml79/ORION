"""Authored regressions, not executed during contract implementation.

Commit-path cases use the existing offline scripted writer only when main runs
these tests. No provider, generated artifact, job or application runs at import.
"""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_spark_codegen import context, payload, retrieved, script
from workbench.spark_codegen import (
    ContractError, GenerationArtifactSet, _translation_assessment,
    _validate, draft_approved_design,
)


ROOT = Path(__file__).resolve().parents[1]


def covered_payload(record, evidence):
    value = payload(record)
    rows = {row['key']: row for row in evidence.rows['shared'] + evidence.sql['shared']}
    # Inventory annotations are claims, not independently proven translations.
    value['translation_coverage'] = {'items': [
        {'task_id': 'shared', 'kind': 'sql_phase' if 'original_sql' in row else 'transformation',
         'source_record_keys': [key], 'evidence_ids': row['evidence_ids'],
         'artifact_paths': ['modules/clean.py'], 'status': 'translated',
         'explanation': 'Static source-to-artifact accounting, runtime proof still required',
         'blockers': []} for key, row in rows.items()]}
    implementation = next(a for a in value['artifacts'] if a['relative_path'] == 'modules/clean.py')
    implementation['evidence_ids'] = sorted({i for row in rows.values() for i in row['evidence_ids']})
    return value


def test_historical_sets_default_unknown_and_missing_coverage_is_unverified(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    parsed, _ = _validate(payload(record), record, evidence)
    assert parsed.translation_coverage is None
    assert parsed.input_data_basis == parsed.behavior_basis == 'unknown'
    assessment = _translation_assessment(parsed, evidence)
    assert assessment['status'] == 'unverified'
    assert 'expression:fields:0' in assessment['missing_items'][0]['source_record_keys']
    assert not assessment['production_ready'] and not assessment['execution_allowed']


@pytest.mark.parametrize('behavior', ['unknown', 'source_preserving', 'substituted'])
def test_synthetic_input_does_not_infer_behavior_substitution(context, behavior):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = covered_payload(record, evidence)
    value.update(input_data_basis='synthetic', behavior_basis=behavior)
    parsed, _ = _validate(value, record, evidence)
    assessment = _translation_assessment(parsed, evidence)
    assert assessment['input_data_basis'] == 'synthetic'
    assert assessment['behavior_basis'] == behavior
    assert assessment['status'] == 'static_inventory_accounted'
    assert assessment['semantic_parity'] == 'unverified'
    assert not assessment['execution_allowed'] and not assessment['production_ready']


def test_partial_coverage_cannot_hide_missing_sql_or_expression_records(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = covered_payload(record, evidence)
    value['translation_coverage']['items'] = [item for item in value['translation_coverage']['items']
        if item['source_record_keys'] == ['expression:header']]
    parsed, _ = _validate(value, record, evidence)
    assessment = _translation_assessment(parsed, evidence)
    assert assessment['status'] == 'unverified'
    missing = set(assessment['missing_items'][0]['source_record_keys'])
    assert {'expression:fields:0', 'mapping:property:0', 'mapping:property:1'} <= missing


@pytest.mark.parametrize('mutation', [
    'invented_record', 'unretrieved_record', 'invented_citation', 'wrong_record_citation',
    'invented_task', 'invented_artifact', 'artifact_citations', 'test_only',
    'duplicate_item', 'duplicate_key', 'blocked_claim', 'translated_blocker',
])
def test_invalid_translation_ownership_and_claims_are_rejected(context, mutation):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = covered_payload(record, evidence)
    item = next(i for i in value['translation_coverage']['items']
                if i['source_record_keys'] == ['expression:fields:0'])
    if mutation == 'invented_record':
        item['source_record_keys'] = ['invented:fields:0']
    elif mutation == 'unretrieved_record':
        # A blocked task permits partial retrieval, but its coverage still may
        # not cite a row whose actual bytes were never returned by its tool.
        value['tasks'][0].update(status='blocked', blockers=['Partial source retrieval'])
        item.update(status='blocked', blockers=['Partial source retrieval'])
        value['translation_coverage']['items'] = [item]
        evidence.fetched['shared'].remove('expression:fields:0')
    elif mutation == 'invented_citation':
        item['evidence_ids'] = ['invented']
    elif mutation == 'wrong_record_citation':
        item['evidence_ids'] = ['mapping']  # Real and retrieved, wrong owner.
    elif mutation == 'invented_task':
        item['task_id'] = 'outside'
    elif mutation == 'invented_artifact':
        item['artifact_paths'] = ['modules/absent.py']
    elif mutation == 'artifact_citations':
        next(a for a in value['artifacts'] if a['relative_path'] == 'modules/clean.py')['evidence_ids'] = ['mapping']
    elif mutation == 'test_only':
        item['artifact_paths'] = ['tests/test_clean.py']
    elif mutation == 'duplicate_item':
        value['translation_coverage']['items'].append(deepcopy(item))
    elif mutation == 'duplicate_key':
        item['source_record_keys'] *= 2
    elif mutation == 'blocked_claim':
        item.update(status='blocked', blockers=['Missing procedure body'])
    else:
        item['blockers'] = ['Missing Java exception behavior']
    with pytest.raises(ContractError):
        _validate(value, record, evidence)


@pytest.mark.parametrize('kind', ['java', 'procedure', 'sql_phase'])
def test_explicit_blocked_behavior_is_preserved_in_assessment(context, kind):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = payload(record)
    value['tasks'][0].update(status='blocked', blockers=['Required behavior unavailable'])
    value['translation_coverage'] = {'items': [
        {'task_id': 'shared', 'kind': kind, 'source_record_keys': ['mapping:properties:0'],
         'evidence_ids': ['mapping'], 'artifact_paths': [], 'status': 'blocked',
         'explanation': 'Captured call is not a complete implementation',
         'blockers': ['Required behavior unavailable']}]}
    parsed, _ = _validate(value, record, evidence)
    assessment = _translation_assessment(parsed, evidence)
    assert assessment['status'] == 'blocked'
    assert assessment['blocked_items'][0]['kind'] == kind
    assert assessment['blocked_items'][0]['blockers'] == ['Required behavior unavailable']
    assert assessment['missing_items']
    value['translation_coverage']['items'][0]['blockers'] = []
    with pytest.raises(ContractError, match='explicit blockers'):
        _validate(value, record, evidence)


@pytest.mark.parametrize('borrowed', ['record', 'citation', 'artifact'])
def test_globally_retrieved_evidence_cannot_be_borrowed_from_another_task(context, borrowed):
    _, record, model, _ = context
    model['nodes'].append({'id': 'private', 'kind': 'taskinstance', 'parent_id': 'wf',
        'name': 'private', 'attributes': {'ISENABLED': 'YES'}, 'fields': [], 'properties': []})
    model['edges'].append({'id': 'wf-private', 'kind': 'contains', 'source': 'wf', 'target': 'private'})
    record['design']['tasks'].append({**record['design']['tasks'][0], 'id': 'other'})
    record['design']['coverage'].append({'original_task_id': 'private', 'proposed_task_ids': ['other'],
        'disposition': 'mapped', 'rationale': 'Separate operational owner', 'evidence_ids': ['private']})
    evidence = retrieved(model, record)
    for sql in (False, True):
        offset = 0
        while True:
            page = evidence.page('other', offset, sql=sql)
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
    assert 'private' in evidence.seen
    value = covered_payload(record, evidence)
    value['tasks'].append({'task_id': 'other', 'implementation_kind': 'notebook_task',
        'status': 'blocked', 'artifact_paths': [], 'evidence_ids': ['private'],
        'blockers': ['Separate side effect remains unimplemented'], 'obligations': []})
    item = value['translation_coverage']['items'][0]
    if borrowed == 'record':
        item.update(source_record_keys=['private:header'], evidence_ids=['private'])
    elif borrowed == 'citation':
        item['evidence_ids'].append('private')
    else:
        value['artifacts'].append({'relative_path': 'modules/other.py', 'kind': 'module',
            'content': 'def other():\n    return 17\n', 'task_ids': ['other'],
            'evidence_ids': item['evidence_ids']})
        value['tasks'][-1]['artifact_paths'] = ['modules/other.py']
        item['artifact_paths'] = ['modules/other.py']
    with pytest.raises(ContractError):
        _validate(value, record, evidence)


def test_coverage_never_bypasses_original_evidence_or_obligation_validators(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = covered_payload(record, evidence)
    value['tasks'][0]['obligations'].pop()
    with pytest.raises(ContractError, match='all eight'):
        _validate(value, record, evidence)
    value = covered_payload(record, evidence)
    evidence.fetched['shared'].remove('expression:fields:0')
    with pytest.raises(ContractError, match='complete exact source'):
        _validate(value, record, evidence)


@pytest.mark.parametrize('coverage_mode', ['historical', 'partial', 'full', 'blocked'])
def test_real_commit_path_persists_assessment_and_keeps_gates_closed(context, coverage_mode):
    service, record, model, _ = context
    evidence = retrieved(model, record)
    value = payload(record) if coverage_mode == 'historical' else covered_payload(record, evidence)
    if coverage_mode == 'partial':
        value['translation_coverage']['items'].pop()
    elif coverage_mode == 'blocked':
        value['tasks'][0].update(status='blocked', blockers=['Missing procedure implementation'])
        for item in value['translation_coverage']['items']:
            item.update(status='blocked', blockers=['Missing procedure implementation'])
    if coverage_mode != 'historical':
        value.update(input_data_basis='synthetic', behavior_basis='source_preserving')
    service.runtime.role_models = {'artifact': script(model, record, value=value)}
    report = draft_approved_design(service, record, consent=True)
    persisted = json.loads((service.root / report['relative_directory'] / 'manifest.json').read_text(encoding='utf-8'))
    assert persisted['translation_coverage'] == value.get('translation_coverage')
    assert persisted['translation_assessment'] == report['translation_assessment']
    expected_status = {'historical': 'unverified', 'partial': 'unverified',
                       'full': 'static_inventory_accounted', 'blocked': 'blocked'}[coverage_mode]
    assert persisted['translation_assessment']['status'] == expected_status
    assert persisted['input_data_basis'] == ('unknown' if coverage_mode == 'historical' else 'synthetic')
    assert persisted['behavior_basis'] == ('unknown' if coverage_mode == 'historical' else 'source_preserving')
    assert persisted['validation']['tests'] == 'authored_not_run'
    assert persisted['validation']['semantic_parity'].startswith('blocked_')
    assert not persisted['execution_allowed'] and not persisted['deployment_allowed']
    assert not persisted['artifact_approved'] and not persisted['translation_assessment']['production_ready']
    assert bool(persisted['translation_assessment']['blocked_items']) == (coverage_mode == 'blocked')


def test_translation_schema_is_closed_and_does_not_accept_approval_claims(context):
    _, record, model, _ = context
    value = covered_payload(record, retrieved(model, record))
    value['translation_coverage']['production_ready'] = True
    with pytest.raises(ValueError):
        GenerationArtifactSet.model_validate(value)


def test_normal_agent_path_no_longer_calls_the_reference_compiler_automatically():
    tree = ast.parse((ROOT / 'workbench/agents.py').read_text(encoding='utf-8'))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {'submit_wil_net_dis_outline', 'deterministic_outline'} <= functions.keys()
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'deterministic_outline']
    for name in ('compile_role', 'output_instruction', 'completion_instruction'):
        strings = '\n'.join(n.value for n in ast.walk(functions[name])
                            if isinstance(n, ast.Constant) and isinstance(n.value, str))
        assert 'Call submit_wil_net_dis_outline' not in strings
        assert 'python_wheel_task logical adapter' not in strings
    reference = ast.get_source_segment((ROOT / 'workbench/agents.py').read_text(encoding='utf-8'),
                                      functions['submit_wil_net_dis_outline'])
    assert reference is not None
    assert 'submit_redesign.invoke' in reference
    assert 'deterministic_workflow_compiler' in reference


def test_shared_contract_is_injected_into_both_real_runtime_prompt_builders():
    for relative in ('workbench/agents.py', 'workbench/spark_codegen.py'):
        tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
        reads = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'read_text'
                 and 'production-conversion-contract.md' in ast.unparse(n.func.value)]
        assert reads, relative
    shared = (ROOT / 'workbench/skills/production-conversion-contract.md').read_text(encoding='utf-8')
    assert 'Synthetic input' in shared and 'behavior_basis' in shared
    assert 'independent' in shared and 'procedure bodies' in shared
    assert 'Static checks alone cannot establish production readiness' in shared