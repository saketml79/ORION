"""Authored offline regression tests. No live model, generated-code execution or deploy.

The compiled-graph tests use a scripted local BaseChatModel, not a provider.
Nothing in this module invokes a model during import/collection.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
import pytest

from workbench.agents import ROLES
from workbench.redesign import Review, store_for
from workbench.spark_codegen import (
    ContractError, DraftBudget, GenerationArtifactSet, OBLIGATIONS, TranslationPageGroup,
    _Evidence, _GenerationReceipts, _StagedArtifacts, _digest, _notebook, _path, _python, _sql, _validate,
    _translation_assessment, create_spark_codegen, draft_approved_design,
)


class ScriptedChat(FakeMessagesListChatModel):
    _calls: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._bound_names = {getattr(t, 'name', str(t)) for t in tools}
        return self

    _bound_names: set = PrivateAttr(default_factory=set)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def call(name, args, number):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': f'call-{number}'}])


def source_model():
    def node(identifier, kind, parent=None, **attributes):
        return {'id': identifier, 'kind': kind, 'name': identifier, 'parent_id': parent,
                'attributes': attributes, 'fields': [], 'properties': [],
                'scope': {}, 'evidence': {'locator': '/' + identifier}}
    nodes = [node('wf', 'workflow'), node('a', 'taskinstance', 'wf', ISENABLED='YES'),
             node('b', 'taskinstance', 'wf', ISENABLED='YES'),
             node('disabled', 'taskinstance', 'wf', ISENABLED='NO'),
             node('mapping', 'mapping'), node('expression', 'transformation', 'mapping', TYPE='Expression'),
             node('outside', 'workflow')]
    nodes[5]['fields'] = [{'kind': 'transformfield', 'attributes': {
        'NAME': 'amount', 'EXPRESSION': 'IIF(ISNULL(amount_in), 0, amount_in)',
        'DATATYPE': 'decimal', 'PRECISION': '18', 'SCALE': '2', 'PORTTYPE': 'OUTPUT'}}]
    nodes[4]['properties'] = [{'kind': 'tableattribute', 'attributes': {'NAME': 'Pre SQL', 'VALUE': 'SELECT 1'}},
                              {'kind': 'tableattribute', 'attributes': {'NAME': 'Post SQL', 'VALUE': 'SELECT 2'}}]
    def edge(identifier, start, end, kind):
        return {'id': identifier, 'source': start, 'target': end, 'kind': kind,
                'attributes': {}, 'evidence': {'locator': '/' + identifier}}
    edges = [edge('wf-' + n, 'wf', n, 'contains') for n in ('a', 'b', 'disabled')]
    edges += [edge('a-map', 'a', 'mapping', 'contains'), edge('b-map', 'b', 'mapping', 'contains'),
              edge('map-expr', 'mapping', 'expression', 'contains'),
              edge('port-connector', 'mapping', 'expression', 'field_flow')]
    return {'schema_version': '1.0', 'package': {'sha256': 'a' * 64},
            'nodes': nodes, 'edges': edges, 'diagnostics': [], 'coverage': {}}


def design():
    return {'title': 'Approved shared transformation, not one notebook per mapping',
        'tasks': [{'id': 'shared', 'label': 'Shared transformation', 'implementation_kind': 'notebook_task',
                   'purpose': 'Preserve both consumers through a shared component', 'evidence_ids': ['a', 'b']}],
        'edges': [], 'rationale': 'Reuse the transformation without inventing operational lifecycle',
        'alternatives': [{'label': 'Keep separate', 'rationale': 'Less sharing',
                          'tradeoffs': ['Duplicated transformation'], 'evidence_ids': ['a', 'b']}],
        'coverage': [{'original_task_id': t, 'proposed_task_ids': ['shared'] if t != 'disabled' else [],
                      'disposition': 'disabled' if t == 'disabled' else 'mapped',
                      'rationale': 'Preserve exact exported disposition', 'evidence_ids': [t]}
                     for t in ('a', 'b', 'disabled')],
        'evidence_ids': ['a', 'b', 'disabled'], 'reuse_groups': [], 'parameters': [],
        'proposed_constraints': ['Independent semantic verification before any execution'], 'blockers': [],
        'artifact_plan': [{'relative_path': 'notebooks/load.ipynb', 'artifact_kind': 'notebook',
                           'proposed_task_ids': ['shared'], 'mapping_ids': ['mapping'],
                           'description': 'Thin task entrypoint using shared transformation', 'evidence_ids': ['mapping']}],
        'scoped_unresolved': []}


@pytest.fixture
def context(tmp_path):
    model = source_model()
    index = SimpleNamespace(model=model)
    inputs = {'input_version': 'b' * 64, 'input_hashes': {'policy': 'c' * 64},
              'input_versions': {'documents': 'd' * 64, 'reviews': 'e' * 64},
              'snapshot': json.dumps({'package_hash': model['package']['sha256']})}
    service = SimpleNamespace(root=tmp_path, state=tmp_path / 'state', lock=RLock(),
                              runtime=SimpleNamespace(role_models=None))
    service.require = lambda: index
    service.agent_inputs = lambda *args: deepcopy(inputs)
    store = store_for(service)
    record = store.append(model['package']['sha256'], 'wf', 'original',
        {'design': design(), 'role': 'architecture', 'retrieved_evidence_ids': ['a', 'b', 'disabled', 'mapping']},
        {'record_id': 'source-run', 'runtime_run_id': '1' * 32, 'submission_index': 0,
         'kind': 'agent', 'input_version': inputs['input_version'],
         'input_hashes': inputs['input_hashes'], 'input_versions': inputs['input_versions']}, 0)
    body = Review(action='approve_for_drafting', comment='Authorize drafting only', expected_version=1,
                  expected_input_version=inputs['input_version'], consent=True)
    store.review(record['id'], record['package_hash'], body, inputs['input_version'],
                 {'actor': 'local-reviewer', 'actor_authenticated': False})
    record = store.get(record['id'], record['package_hash'])
    return service, record, model, inputs


def notebook():
    return json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'markdown', 'metadata': {'language': 'markdown'}, 'source': '# Draft, no parity claim'},
        {'cell_type': 'code', 'metadata': {'language': 'python'},
         'source': 'from modules.clean import null_safe\ndef transform(value):\n    return null_safe(value)\n',
         'execution_count': None, 'outputs': []}]})


def payload(record):
    artifacts: list[dict[str, Any]] = [
        {'relative_path': 'notebooks/load.ipynb', 'kind': 'notebook', 'content': notebook()},
        {'relative_path': 'modules/clean.py', 'kind': 'module',
         'content': 'def null_safe(value):\n    return 0 if value is None else value\n'},
        {'relative_path': 'tests/test_clean.py', 'kind': 'test', 'content':
         'from modules.clean import null_safe\ndef test_null_values():\n    assert null_safe(None) == 0\n    assert null_safe(7) == 7\n'},
        {'relative_path': 'sql/query.sql', 'kind': 'sql', 'content': 'SELECT CAST(1 AS DECIMAL(18, 2)) AS amount'},
    ]
    for artifact in artifacts:
        artifact.update(task_ids=['shared'], evidence_ids=['expression'])
    obligations = [{'category': category, 'status': 'not_applicable',
                    'explanation': 'Synthetic pure transformation fixture has no external effect in this category',
                    'evidence_ids': ['expression']} for category in sorted(OBLIGATIONS)]
    nulls = next(o for o in obligations if o['category'] == 'nulls')
    nulls.update(status='test_required', explanation='Null becomes zero and nonnull values are preserved',
                 test_path='tests/test_clean.py', test_function='test_null_values')
    return {'design_id': record['id'], 'design_version': record['version'],
            'design_sha256': _digest(record['design']), 'input_version': record['input_version'],
            'package_hash': record['package_hash'],
            'tasks': [{'task_id': 'shared', 'implementation_kind': 'notebook_task', 'status': 'drafted',
                       'artifact_paths': [a['relative_path'] for a in artifacts], 'evidence_ids': ['expression'],
                       'blockers': [], 'obligations': obligations}], 'artifacts': artifacts}


def retrieved(model, record):
    evidence = _Evidence(model, record)
    for sql in (False, True):
        offset = 0
        while True:
            page = evidence.page('shared', offset, sql=sql)
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
    return evidence


def script(model, record, value=None, bad_first=None):
    evidence = _Evidence(model, record)
    responses = []
    for sql in (False, True):
        rows = evidence.sql['shared'] if sql else evidence.rows['shared']
        for offset in range(0, max(1, len(rows)), 12):
            responses.append(call('fetch_sql_phases' if sql else 'fetch_source_task',
                                  {'task_id': 'shared', 'offset': offset}, len(responses)))
    if bad_first is not None:
        responses.append(call('submit_generation_artifact_set', {'artifact_set': bad_first}, len(responses)))
    responses.append(call('submit_generation_artifact_set', {'artifact_set': value or payload(record)}, len(responses)))
    # Successful submission should terminate before this additional model call.
    responses.append(AIMessage(content='This must not be needed to persist code'))
    return ScriptedChat(responses=responses, custom_get_token_ids=lambda text: list(range(len(text) // 4)))


def test_factory_and_consent_do_not_invoke_model_or_create_drafts(context):
    service, record, _, _ = context
    agent = create_spark_codegen(service, record)
    assert agent.graph is None
    assert not (service.root / 'generated-drafts').exists()
    with pytest.raises(PermissionError):
        agent.draft()
    assert not (service.root / 'generated-drafts').exists()


@pytest.mark.parametrize('change', ['caller_boolean', 'reject', 'new_version', 'input', 'hashes', 'design', 'package'])
def test_repository_gate_cannot_be_bypassed(context, change):
    service, record, _, inputs = context
    expected = deepcopy(record)
    if change in {'caller_boolean', 'reject'}:
        store_for(service).review(record['id'], record['package_hash'], Review(
            action='reject', comment='Do not draft', expected_version=1, expected_review_version=1,
            expected_input_version=inputs['input_version'], consent=True), inputs['input_version'], {'actor': 'reviewer'})
        expected['outline_drafting_allowed'] = True
        expected['approved'] = True
    elif change == 'new_version':
        store_for(service).append(record['package_hash'], 'wf', 'original',
            {'design': record['design'], 'role': 'architecture', 'retrieved_evidence_ids': ['a']},
            {**record['source'], 'record_id': 'next-source'}, 1)
    elif change == 'input':
        inputs['input_version'] = 'f' * 64
    elif change == 'hashes':
        inputs['input_hashes']['policy'] = 'f' * 64
    elif change == 'design':
        expected['design']['title'] = 'Caller tampered content'
    else:
        expected['package_hash'] = 'f' * 64
    with pytest.raises(ContractError):
        draft_approved_design(service, expected, consent=True)
    assert not (service.root / 'generated-drafts').exists()


def test_exact_source_expressions_connectors_and_sql_phases_are_retrieved(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    assert evidence.complete('shared')
    source = json.dumps(evidence.rows['shared'])
    assert 'IIF(ISNULL(amount_in), 0, amount_in)' in source
    assert 'port-connector' in source and 'PRECISION' in source
    assert {row['role'] for row in evidence.sql['shared']} == {'pre_sql', 'post_sql'}
    assert [row['original_sql'] for row in evidence.sql['shared']] == ['SELECT 1', 'SELECT 2']
    assert 'outside' not in evidence.seen
    with pytest.raises(ContractError, match='not an evidence ID.*shared'):
        evidence.page('outside', 0)


def test_translation_page_binds_canonical_citations_and_rejects_incomplete_coverage(context):
    _, record, model, _ = context
    evidence = _Evidence(model, record)
    page = evidence.page('shared', 0, sql=False)
    assert page['items'] and not page['incomplete']
    keys = [row['key'] for row in page['items']]
    indexes = list(range(len(keys)))
    split = len(indexes) // 2
    groups = [
        TranslationPageGroup(record_indexes=indexes[:split], kind='transformation',
            artifact_paths=['notebooks/load.ipynb'], status='blocked',
            explanation='Fixture requires semantic implementation before claiming translation.',
            blockers=['Semantic implementation not established']),
        TranslationPageGroup(record_indexes=indexes[split:], kind='control',
            artifact_paths=[], status='blocked',
            explanation='Fixture behavior remains unimplemented.', blockers=['Unimplemented behavior']),
    ]
    receipts = _GenerationReceipts(record, _StagedArtifacts(record), evidence, 'offline-page-check')
    result = receipts.translation_page('shared', 0, False, groups)
    assert result['record_count'] == len(keys)
    assert result['group_count'] == 2
    expected_citations = {identifier for row in page['items'] for identifier in row['evidence_ids']}
    actual_citations = {identifier for item in receipts.translations.values()
                        for identifier in item['value']['evidence_ids']}
    assert actual_citations == expected_citations
    before = deepcopy(receipts.translations)
    with pytest.raises(ContractError, match='missing=1'):
        receipts.translation_page('shared', 0, False, [groups[0].model_copy(update={
            'record_indexes': indexes[:-1],
        })])
    assert receipts.translations == before


def test_translation_group_does_not_require_artifact_to_repeat_every_group_citation(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    rows = evidence.rows['shared'] + evidence.sql['shared']
    # Model a source record backed by multiple exact canonical facts, as occurs
    # for real connected transformations and control links.
    extra_citation = 'f' * 64
    rows[0]['evidence_ids'].append(extra_citation)
    evidence.seen.add(extra_citation)
    exact_group_citations = sorted({identifier for row in rows for identifier in row['evidence_ids']})
    per_record_citations = sorted({row['evidence_ids'][0] for row in rows})
    assert len(per_record_citations) < len(exact_group_citations)
    value = payload(record)
    notebook_artifact = next(item for item in value['artifacts']
                             if item['relative_path'] == 'notebooks/load.ipynb')
    notebook_artifact['evidence_ids'] = per_record_citations
    value['translation_coverage'] = {'items': [{
        'task_id': 'shared', 'kind': 'transformation',
        'source_record_keys': [row['key'] for row in rows],
        'evidence_ids': exact_group_citations,
        'artifact_paths': ['notebooks/load.ipynb'], 'status': 'translated',
        'explanation': 'Every inventory record is explicitly classified; the implementation notebook cites each record once.',
        'blockers': [],
    }]}
    artifact_set = GenerationArtifactSet.model_validate(value)
    assessment = _translation_assessment(artifact_set, evidence)
    assert assessment['status'] == 'static_inventory_accounted'


@pytest.mark.parametrize('path,kind', [('../x.py', 'module'), ('modules/../../x.py', 'module'),
    ('C:/modules/x.py', 'module'), ('modules/x.py:evil', 'module'), ('modules/CON.py', 'module'),
    ('modules//x.py', 'module'), ('modules/x.py.', 'module'), ('notebooks/x.py', 'notebook'),
    ('tests/check.py', 'test'), ('/sql/query.sql', 'sql')])
def test_artifact_paths_are_closed_allowlist(path, kind):
    with pytest.raises(ContractError):
        _path(path, kind)


def test_notebook_schema_ids_python_and_sql_are_static_only():
    first = _notebook(notebook(), 'namespace')
    assert _notebook(first, 'namespace') == first
    cells = json.loads(first)['cells']
    assert all(c['metadata']['id'] == c['id'] for c in cells)
    bad = json.loads(notebook())
    bad['cells'][1]['metadata']['language'] = 'bash'
    with pytest.raises(ContractError):
        _notebook(json.dumps(bad), 'namespace')
    with pytest.raises(ContractError):
        _python('def broken(:')
    with pytest.raises(ContractError):
        _sql('SELECT (')
    with pytest.raises(ContractError):
        _python('spark.sql("SELECT (")')
    assert _python('raise RuntimeError("Not executed by validator")') is not None


def test_class_based_pytest_methods_with_nonconstant_assertions_are_valid(context):
    _, record, model, _ = context
    content = (
        'class TestBehavior:\n'
        '    def test_computed_value(self):\n'
        '        actual = sum([2, 3])\n'
        '        assert actual == 5\n'
    )
    staged = _StagedArtifacts(record)
    receipt = staged.stage('tests/test_class_behavior.py', 'test', content)
    assert receipt['staged']
    value = payload(record)
    value['artifacts'][2]['content'] = content
    next(item for item in value['tasks'][0]['obligations']
         if item['category'] == 'nulls')['test_function'] = 'test_computed_value'
    artifact_set = GenerationArtifactSet.model_validate(value)
    evidence = retrieved(model, record)
    _, bodies = _validate(artifact_set, record, evidence)
    assert bodies['tests/test_clean.py'] == content


@pytest.mark.parametrize('mutation', ['unretrieved', 'version', 'task', 'missing_coverage', 'case_collision',
    'test_assertion', 'test_ref', 'obligation', 'placeholder', 'plan_path'])
def test_typed_validator_rejects_false_coverage_and_provenance(context, mutation):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = payload(record)
    if mutation == 'unretrieved':
        value['artifacts'][0]['evidence_ids'] = ['outside']
    elif mutation == 'version':
        value['design_version'] += 1
    elif mutation == 'task':
        value['artifacts'][0]['task_ids'] = ['invented']
    elif mutation == 'missing_coverage':
        value['tasks'] = []
    elif mutation == 'case_collision':
        duplicate = deepcopy(value['artifacts'][1])
        duplicate['relative_path'] = 'modules/CLEAN.py'
        value['artifacts'].append(duplicate)
    elif mutation == 'test_assertion':
        value['artifacts'][2]['content'] = 'def test_null_values():\n    assert True\n'
    elif mutation == 'test_ref':
        next(o for o in value['tasks'][0]['obligations'] if o['status'] == 'test_required')['test_function'] = 'invented'
    elif mutation == 'obligation':
        value['tasks'][0]['obligations'].pop()
    elif mutation == 'placeholder':
        value['artifacts'][1]['content'] = 'def null_safe(value):\n    pass\n'
    else:
        value['artifacts'][0]['relative_path'] = 'notebooks/renamed.ipynb'
        value['tasks'][0]['artifact_paths'][0] = 'notebooks/renamed.ipynb'
    with pytest.raises(ValueError):
        _validate(value, record, evidence)


def test_missing_source_and_scoped_unknowns_block_only_affected_task(context):
    _, record, model, _ = context
    value = payload(record)
    with pytest.raises(ContractError):
        _validate(value, record, _Evidence(model, record))
    evidence = retrieved(model, record)
    changed = deepcopy(record)
    changed['design']['scoped_unresolved'] = [{'proposed_task_ids': ['shared'], 'category': 'mapping',
        'impact': 'execution_blocker', 'description': 'Missing source join predicate', 'evidence_ids': ['a']}]
    value['design_sha256'] = _digest(changed['design'])
    with pytest.raises(ContractError, match='affected task'):
        _validate(value, changed, evidence)
    value['tasks'][0].update(status='blocked', blockers=['Missing source join predicate'])
    parsed, _ = _validate(value, changed, evidence)
    assert parsed.tasks[0].status == 'blocked'


def test_generated_native_lakeflow_task_kind_cannot_downgrade(context):
    _, record, model, _ = context
    evidence = retrieved(model, record)
    value = payload(record)
    value['tasks'][0]['implementation_kind'] = 'spark_python_task'
    with pytest.raises(ContractError, match='exactly match the approved design'):
        _validate(value, record, evidence)


def test_missing_uc_binding_is_human_review_not_a_draft_blocker(context):
    service, record, model, inputs = context
    design_value = deepcopy(record['design'])
    design_value['scoped_unresolved'] = [{'proposed_task_ids': ['shared'], 'category': 'connection',
        'impact': 'human_review', 'description': 'Bind the approved bronze source table or external connection before execution.',
        'evidence_ids': ['a']}]
    source = {**record['source'], 'record_id': 'connection-review-run', 'runtime_run_id': '2' * 32}
    reviewed = store_for(service).append(record['package_hash'], 'wf', 'original',
        {'design': design_value, 'role': 'architecture', 'retrieved_evidence_ids': ['a', 'b', 'disabled', 'mapping']},
        source, record['version'])
    store_for(service).review(reviewed['id'], reviewed['package_hash'], Review(
        action='approve_for_drafting', comment='Draft with source binding left for human review',
        expected_version=reviewed['version'], expected_input_version=inputs['input_version'], consent=True),
        inputs['input_version'], {'actor': 'local-reviewer', 'actor_authenticated': False})
    reviewed = store_for(service).get(reviewed['id'], reviewed['package_hash'])
    route = script(model, reviewed)
    service.runtime.role_models = {'artifact': route}
    report = draft_approved_design(service, reviewed, consent=True)
    assert report['status'] == 'generation_complete'
    assert report['workflow_readiness'] == 'drafted_pending_human_review'
    assert report['human_review_items'] == [dict(design_value['scoped_unresolved'][0],
        status='pending', execution_allowed=False)]
    assert report['tasks'][0]['status'] == 'drafted'
    assert not report['execution_allowed'] and not report['deployment_allowed']


def test_overbound_exact_record_is_not_silently_truncated(context):
    _, record, model, _ = context
    model['nodes'][5]['fields'][0]['attributes']['EXPRESSION'] = 'x' * 70_000
    evidence = retrieved(model, record)
    assert not evidence.complete('shared')
    assert 'expression:fields:0' not in evidence.reads


def test_real_compiled_graph_uses_artifact_route_and_persists_immutable_code(context):
    service, record, model, _ = context
    original_roles = tuple(ROLES)
    route = script(model, record)
    service.runtime.role_models = {'artifact': route}
    report = draft_approved_design(service, record, consent=True)
    assert report['status'] == 'generation_complete'
    assert tuple(ROLES) == original_roles and 'spark_codegen' not in ROLES
    assert report['route_reused'] == 'runtime.role_models.artifact'
    assert report['validation']['tests'] == 'authored_not_run'
    assert report['validation']['semantic_parity'].startswith('blocked_')
    assert not report['execution_allowed'] and not report['deployment_allowed']
    directory = service.root / report['relative_directory']
    persisted = json.loads((directory / 'manifest.json').read_text())
    for path, digest in persisted['artifact_hashes'].items():
        assert sha256((directory / path).read_bytes()).hexdigest() == digest
    assert json.loads((directory / 'notebooks/load.ipynb').read_text())['cells'][1]['metadata']['language'] == 'python'
    assert len(report['original_coverage']) == 3 and len(report['tasks']) == 1
    assert len(route._calls) == report['budget_used']['model_calls']
    shared_path = Path(__file__).resolve().parents[1].joinpath(
        'workbench/skills/production-conversion-contract.md')
    shared = shared_path.read_text(encoding='utf-8')
    system = route._calls[0][0].text
    assert shared in system
    assert 'complete Java control and data behavior' in system
    assert 'procedure bodies and transitive calls' in system
    assert 'Missing coverage remains unverified' in system
    for requirement in ('{artifact_set: object}', 'content (a string',
                        'exactly the eight documented obligation categories once',
                        'sql_phases as not_applicable', 'Masked Java literals',
                        'markdown cells have neither', 'lower validators'):
        assert requirement in system
    assert report['production_contract_sha256'] == sha256(shared_path.read_bytes()).hexdigest()
    assert persisted['production_contract_sha256'] == report['production_contract_sha256']
    assert report['translation_coverage'] is None
    assert report['translation_assessment']['status'] == 'unverified'
    assert report['input_data_basis'] == report['behavior_basis'] == 'unknown'
    assert 'execute' not in route._bound_names and 'task' not in route._bound_names
    assert not {'write_file', 'edit_file', 'delete_file'} & route._bound_names
    service.runtime.role_models = {'artifact': script(model, record)}
    second = draft_approved_design(service, record, consent=True)
    assert second['run_id'] != report['run_id']
    assert (directory / 'manifest.json').read_text() == (json.dumps(persisted, sort_keys=True,
        ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n')


def test_recoverable_validation_feedback_is_not_a_provider_retry(context):
    service, record, model, _ = context
    invalid = payload(record)
    invalid['artifacts'][0]['evidence_ids'] = ['invented']
    route = script(model, record, bad_first=invalid)
    service.runtime.role_models = {'artifact': route}
    result = draft_approved_design(service, record, consent=True, budget=DraftBudget(model_calls=12))
    assert result['status'] == 'generation_complete'
    assert result['budget_used']['submission_attempts'] == 2
    assert any(m.type == 'tool' and m.status == 'error' for messages in route._calls for m in messages)


def test_provider_failure_has_no_fallback_or_retry(context, monkeypatch):
    service, record, _, _ = context
    route = ScriptedChat(responses=[AIMessage(content='unused')])
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('private token/provider detail')
    monkeypatch.setattr(ScriptedChat, '_generate', fail)
    service.runtime.role_models = {'artifact': route}
    result = draft_approved_design(service, record, consent=True)
    assert len(calls) == 1 and result['status'] == 'blocked'
    assert result['artifact_hashes'] == {}
    assert 'private token' not in json.dumps(result)


def test_call_budget_and_no_submission_are_explicitly_blocked(context):
    service, record, _, _ = context
    route = ScriptedChat(responses=[call('fetch_source_task', {'task_id': 'shared'}, 0)])
    service.runtime.role_models = {'artifact': route}
    result = draft_approved_design(service, record, consent=True, budget=DraftBudget(model_calls=1))
    assert result['status'] == 'blocked' and result['failure'] == 'budget_exhausted'
    assert len(route._calls) == 1
    assert result['artifact_hashes'] == {}


def test_no_route_is_inferred_from_environment(context, monkeypatch):
    service, record, _, _ = context
    monkeypatch.setenv('WORKBENCH_MODEL', 'openai:some-model')
    with pytest.raises(ContractError, match='no inferred provider'):
        draft_approved_design(service, record, consent=True)


def test_freshness_change_during_model_call_prevents_artifact_commit(context, monkeypatch):
    service, record, model, inputs = context
    original = ScriptedChat._generate
    def change_after_call(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        inputs['input_version'] = 'f' * 64
        return result
    monkeypatch.setattr(ScriptedChat, '_generate', change_after_call)
    service.runtime.role_models = {'artifact': script(model, record)}
    with pytest.raises(ContractError, match='stale'):
        draft_approved_design(service, record, consent=True)
    assert list((service.root / 'generated-drafts').rglob('manifest.json')) == []


def test_typed_schema_is_closed_and_content_bounded(context):
    _, record, _, _ = context
    value = payload(record)
    value['approved'] = True
    with pytest.raises(ValueError):
        GenerationArtifactSet.model_validate(value)
    value.pop('approved')
    value['artifacts'][1]['content'] = 'x' * 256_001
    with pytest.raises(ValueError):
        GenerationArtifactSet.model_validate(value)


def test_schema_invalid_submissions_consume_bounded_revision_attempts(context):
    service, record, _, _ = context
    invalid = [call('submit_generation_artifact_set', {'artifact_set': {'design_id': record['id']}}, number)
               for number in range(3)]
    route = ScriptedChat(responses=invalid)
    service.runtime.role_models = {'artifact': route}
    result = draft_approved_design(service, record, consent=True, budget=DraftBudget(model_calls=8))
    assert result['status'] == 'blocked'
    assert result['budget_used']['submission_attempts'] == 3
    assert len(route._calls) <= 8  # Explicit model-call budget, including bounded correction turns.
    for messages in route._calls:
        for message in messages:
            if message.type == 'tool' and message.status == 'error':
                assert len(str(message.content)) < 1000


def test_unresolved_canonical_port_cannot_be_declared_implemented(context):
    _, record, model, _ = context
    field = {'id': 'missing-port', 'kind': 'instance_field', 'name': 'missing',
             'parent_id': 'mapping', 'attributes': {'status': 'unresolved'}, 'fields': [], 'properties': []}
    model['nodes'].append(field)
    model['edges'].append({'id': 'missing-edge', 'kind': 'contains', 'source': 'mapping', 'target': 'missing-port'})
    with pytest.raises(ContractError, match='ports block'):
        _validate(payload(record), record, retrieved(model, record))


def test_scoped_blocker_does_not_discard_independent_implemented_task(context):
    _, record, model, _ = context
    record['design']['tasks'].append({**record['design']['tasks'][0], 'id': 'needs-owner'})
    record['design']['coverage'][0]['proposed_task_ids'].append('needs-owner')
    record['design']['scoped_unresolved'] = [{'proposed_task_ids': ['needs-owner'], 'category': 'control',
        'impact': 'execution_blocker', 'description': 'Unknown required side effect', 'evidence_ids': ['a']}]
    value = payload(record)
    value['tasks'].append({'task_id': 'needs-owner', 'implementation_kind': 'notebook_task',
                          'status': 'blocked', 'artifact_paths': [],
                          'evidence_ids': ['a'], 'blockers': ['Unknown required side effect'], 'obligations': []})
    parsed, bodies = _validate(value, record, retrieved(model, record))
    assert [t.status for t in parsed.tasks] == ['drafted', 'blocked']
    assert 'notebooks/load.ipynb' in bodies


def test_loop_approval_rechecks_actual_approved_lessons_without_constructing_loop_service(context):
    import sqlite3
    from workbench.agents import _hash
    from workbench.spark_codegen import _approved
    service, record, _, inputs = context
    path = service.state / 'loops.sqlite'
    db = sqlite3.connect(path)
    try:
        db.execute('CREATE TABLE lessons (package_hash TEXT, workflow_id TEXT, payload TEXT)')
        db.commit()
        hashes = {**inputs['input_hashes'], 'approved_lessons': _hash([])}
        loop_version = _hash([inputs['input_version'], []])
        loop_record = store_for(service).append(record['package_hash'], 'wf', 'original',
            {'design': record['design'], 'role': 'supervisor', 'retrieved_evidence_ids': ['a', 'b']},
            {**record['source'], 'record_id': 'loop-source', 'kind': 'loop',
             'input_hashes': hashes, 'input_version': loop_version}, 1)
        store_for(service).review(loop_record['id'], loop_record['package_hash'], Review(
            action='approve_for_drafting', comment='Current loop input snapshot', expected_version=2,
            expected_input_version=loop_version, consent=True), loop_version, {'actor': 'reviewer'})
        assert _approved(service, loop_record)[1]['version'] == 2
        db.execute('INSERT INTO lessons VALUES (?,?,?)', (record['package_hash'], 'wf',
            json.dumps({'id': 'new-lesson', 'status': 'approved', 'lesson': 'Material new evidence'})))
        db.commit()
        with pytest.raises(ContractError, match='stale'):
            _approved(service, loop_record)
    finally:
        db.close()