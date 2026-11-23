"""Offline actual writer-graph review/correction tests, no generated code execution."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
import socket

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
import pytest

from workbench.generated_code_review import GeneratedCodeReviewer, _task_design
from workbench.redesign import store_for
from workbench.spark_codegen import DraftBudget, GenerationArtifactSet, _Evidence, create_spark_codegen


FIX = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests' / 'test_spark_codegen.py'))
USAGE = dict(input_tokens=11, output_tokens=7, total_tokens=18)


class Reviewer(FIX['ScriptedChat']):
    mode: str = 'nulls'

    def bind_tools(self, tools, **kwargs):
        raise AssertionError('Reviewer must never receive tools')

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append(messages)
        scope = json.loads(messages[-1].content)
        tid = scope['task_id']
        files = scope['artifacts']
        if self.mode == 'failure':
            raise RuntimeError('Provider failure must not erase saved code')
        if self.mode == 'invalid':
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='not JSON', usage_metadata=USAGE))])
        if self.mode == 'tools':
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', usage_metadata=USAGE,
                tool_calls=[dict(name='run_sql', args={}, id='forbidden')]))])
        if self.mode == 'outside':
            result = dict(status='needs_correction', issues=[dict(kind='semantic_bug',
                task_id='outside', relative_path='modules/outside.py', detail='Outside scope')])
        elif self.mode == 'unknown':
            commented = any('Missing business rule: target retention' in a['content'] for a in files)
            result = dict(status='needs_information', issues=[dict(kind='missing_information', task_id=tid,
                relative_path=files[0]['relative_path'], detail=('Target retention still unknown; comment recorded'
                    if commented else 'Add explicit comment: Missing business rule: target retention. Do not invent a table.'))])
        elif self.mode == 'always_bug' or any('return None' in a['content'] for a in files):
            result = dict(status='needs_correction', issues=[dict(kind='semantic_bug', task_id=tid,
                relative_path=next((a['relative_path'] for a in files if a['kind'] == 'module'), files[0]['relative_path']),
                detail='XML IIF(ISNULL(amount_in), 0, amount_in) requires null to zero and preserves nonnull values; repair the affected code.')])
        else:
            result = dict(status='review_ready', issues=[])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(result), usage_metadata=USAGE))])


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden in generated-code review regression')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return FIX['context'].__wrapped__(tmp_path)


def bad_payload(record):
    value = FIX['payload'](record)
    value['artifacts'][1]['content'] = 'def null_safe(value):\n    return None\n'
    return value


def run_graph(ctx, actions, reviewer=None, *, budget=None, same_writer=False, candidate=True):
    service, record, model, _ = ctx
    evidence = _Evidence(model, record)
    fetches = []
    for tid in evidence.rows:
        for sql in (False, True):
            rows = evidence.sql[tid] if sql else evidence.rows[tid]
            fetches.extend(('fetch_sql_phases' if sql else 'fetch_source_task', dict(task_id=tid, offset=offset))
                           for offset in range(0, max(1, len(rows)), 12))
    responses = [FIX['call'](name, args, i).model_copy(update={'usage_metadata': USAGE})
                 for i, (name, args) in enumerate(fetches + actions)]
    responses.append(AIMessage(content='Stop without inventing anything', usage_metadata=USAGE))
    writer = FIX['ScriptedChat'](responses=responses, custom_get_token_ids=lambda text: list(range(len(text) // 4)))
    service.runtime.role_models = {'artifact': writer}
    if reviewer is not None or same_writer:
        service.runtime.role_models['validation'] = writer if same_writer else reviewer
    before = (service.state / 'redesigns.sqlite').read_bytes()
    agent = create_spark_codegen(service, record, budget=budget)
    if not candidate:
        agent.candidate = False
        agent.require_review = True
    report = agent.draft(consent=True)
    assert (service.state / 'redesigns.sqlite').read_bytes() == before
    root = service.root / report['relative_directory']
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['generated_code_review'] == report['generated_code_review'] if candidate else 'generated_code_review' not in manifest
    for path, digest in report['artifact_hashes'].items():
        assert sha256((root / path).read_bytes()).hexdigest() == digest
    assert record['generation_allowed'] is False
    assert report['artifact_approved'] is False
    assert report['execution_allowed'] is False and report['deployment_allowed'] is False
    return report, writer


def submit(value):
    return 'submit_generation_artifact_set', dict(artifact_set=value)


def review_model(mode='nulls'):
    return Reviewer(mode=mode, responses=[AIMessage(content='unused')])


def assert_counts(report, writer, reviewer):
    calls = report['budget_used']
    assert calls['artifact_model_calls'] == len(writer._calls)
    assert calls['validation_model_calls'] == len(reviewer._calls)
    assert calls['model_calls'] == len(writer._calls) + len(reviewer._calls)
    events = [e for e in report['events'] if e['kind'] == 'model']
    assert len(events) == calls['model_calls']
    assert sorted(e['ordinal'] for e in events) == list(range(1, calls['model_calls'] + 1))
    successful = sum(e.get('status') == 'completed' for e in events)
    for name, amount in USAGE.items():
        assert calls[name] == amount * successful
    assert all(e['route'] == 'runtime.role_models.' + e['role'] for e in events)


def test_main_graph_semantic_bug_corrected_and_exact_saved_digest_reviewed(ctx):
    reviewer = review_model()
    bad, good = bad_payload(ctx[1]), FIX['payload'](ctx[1])
    report, writer = run_graph(ctx, [submit(bad), submit(good)], reviewer,
                              budget=DraftBudget(model_calls=8, input_characters=1_000_000))
    receipt = report['generated_code_review']
    assert report['status'] == 'generation_complete' and report['failure'] is None
    assert report['translation_coverage'] is None and report['translation_assessment']['status'] == 'unverified'
    assert receipt['status'] == 'review_ready' and receipt['ai_reviewed']
    assert receipt['assurance'] == 'AI-reviewed not runtime verified'
    assert len(reviewer._calls) == 2
    scopes = [json.loads(m[-1].content) for m in reviewer._calls]
    assert any('return None' in a['content'] for a in scopes[0]['artifacts'])
    assert any('return 0 if value is None else value' in a['content'] for a in scopes[1]['artifacts'])
    assert receipt['tasks'][0]['artifact_hashes'] == report['artifact_hashes']
    source = json.dumps(scopes[-1]['canonical_source_xml_rows'])
    assert 'IIF(ISNULL(amount_in), 0, amount_in)' in source and 'port-connector' in source
    assert [r['original_sql'] for r in scopes[-1]['sql_phases']] == ['SELECT 1', 'SELECT 2']
    assert 'outside' not in source
    assert scopes[-1]['selected_design'] == _task_design(ctx[1]['design'], 'shared', dict(
        artifacts=scopes[-1]['artifacts'], canonical_source_xml_rows=scopes[-1]['canonical_source_xml_rows'],
        sql_phases=scopes[-1]['sql_phases']))
    assert scopes[-1]['identity']['design_sha256'] == FIX['_digest'](ctx[1]['design'])
    feedback = [json.loads(m.content) for call in writer._calls for m in call
                if m.type == 'tool' and m.name == 'submit_generation_artifact_set']
    assert any(f.get('error') == 'generated_code_review_feedback' and f['code_saved'] for f in feedback)
    assert report['candidate_availability']['tasks'][0]['code_available']
    assert report['candidate_availability']['tasks'][0]['generated_code_review'] == receipt['tasks'][0]
    assert not receipt['runtime_verified'] and not report['execution_allowed']
    assert_counts(report, writer, reviewer)


def test_identical_repeated_commit_does_not_review_or_loop(ctx):
    reviewer = review_model('always_bug')
    bad = bad_payload(ctx[1])
    report, writer = run_graph(ctx, [submit(bad)] * 12, reviewer)
    assert len(reviewer._calls) == 1
    assert report['status'] == 'generation_complete'
    assert report['generated_code_review']['status'] == 'needs_correction'
    assert report['failure'] is None and report['artifact_hashes']
    assert_counts(report, writer, reviewer)


def test_actual_staged_commit_feedback_rebinds_corrected_bytes(ctx, monkeypatch):
    reviewer = review_model()
    path = 'notebooks/load.ipynb'
    bad = FIX['notebook']().replace('return null_safe(value)', 'return None')
    good = FIX['notebook']()
    namespace = ctx[1]['id'] + ':' + str(ctx[1]['version']) + ':' + path
    old_hash = sha256(FIX['_notebook'](bad, namespace).encode()).hexdigest()
    new_hash = sha256(FIX['_notebook'](good, namespace).encode()).hexdigest()
    original = FIX['ScriptedChat']._generate
    def consume_current_feedback(self, messages, **kwargs):
        result = original(self, messages, **kwargs)
        message = result.generations[0].message
        for call in message.tool_calls:
            if call['args'].get('replace_sha256') == 'current-feedback-binding':
                feedback = next(json.loads(m.content) for m in reversed(messages)
                    if m.type == 'tool' and m.name == 'commit_generation_receipts')
                assert feedback['error'] == 'generated_code_review_feedback'
                call['args']['replace_sha256'] = feedback['file_receipts'][0]['binding_replace_sha256']
        return result
    monkeypatch.setattr(FIX['ScriptedChat'], '_generate', consume_current_feedback)
    report, writer = run_graph(ctx, [
        ('stage_generation_artifact', dict(relative_path=path, kind='notebook', content=bad)),
        ('commit_generation_receipts', {}),
        ('stage_generation_artifact', dict(relative_path=path, kind='notebook', content=good, replace_sha256=old_hash)),
        ('stage_generation_artifact_binding', dict(relative_path=path, task_ids=['shared'],
            evidence_ids=['mapping'], content_sha256=new_hash, replace_sha256='current-feedback-binding')),
        ('commit_generation_receipts', {}),
    ], reviewer)
    assert report['failure'] is None and report['generated_code_review']['status'] == 'review_ready'
    assert report['artifact_hashes'][path] == new_hash and len(reviewer._calls) == 2
    assert report['generated_code_review']['tasks'][0]['artifact_hashes'] == {path: new_hash}
    assert_counts(report, writer, reviewer)


def test_identical_content_reordered_metadata_does_not_re_review(ctx):
    reviewer = review_model('always_bug')
    value = bad_payload(ctx[1])
    reordered = deepcopy(value)
    reordered['artifacts'].reverse()
    reordered['tasks'][0]['blockers'] = ['Bookkeeping changed, same actual code']
    reordered['tasks'][0]['status'] = 'blocked'
    report, writer = run_graph(ctx, [submit(value), submit(reordered)], reviewer)
    assert len(reviewer._calls) == 1 and report['generated_code_review']['status'] == 'needs_correction'
    assert_counts(report, writer, reviewer)


@pytest.mark.parametrize('mode', ['absent', 'failure', 'invalid', 'tools', 'outside', 'same_writer'])
def test_unavailable_review_preserves_valid_code_with_explicit_issue(ctx, mode):
    reviewer = None if mode in {'absent', 'same_writer'} else review_model(mode)
    report, writer = run_graph(ctx, [submit(FIX['payload'](ctx[1]))], reviewer, same_writer=mode == 'same_writer')
    receipt = report['generated_code_review']
    assert report['status'] == 'generation_complete' and report['failure'] is None
    assert report['translation_coverage'] is None and report['translation_assessment']['status'] == 'unverified'
    assert not receipt['ai_reviewed'] and receipt['status'] == 'needs_information'
    assert receipt['issues'][0]['kind'] == 'review_unavailable'
    assert report['artifact_hashes'] and report['validation']['static'] == 'passed'
    assert not report['validation']['ai_reviewed'] and not report['validation']['runtime_verified']
    assert report['candidate_availability']['tasks'][0]['code_available']
    assert any(i['kind'] == 'review_unavailable' for i in report['candidate_availability']['tasks'][0]['unresolved_items'])
    if reviewer is not None:
        assert_counts(report, writer, reviewer)
    else:
        assert report['budget_used']['validation_model_calls'] == 0


def test_unknown_business_feedback_adds_comments_not_fake_code(ctx):
    reviewer = review_model('unknown')
    value = FIX['payload'](ctx[1])
    corrected = deepcopy(value)
    notebook = json.loads(corrected['artifacts'][0]['content'])
    notebook['cells'][1]['source'] += '# Missing business rule: target retention\n'
    corrected['artifacts'][0]['content'] = json.dumps(notebook)
    report, writer = run_graph(ctx, [submit(value), submit(corrected), submit(corrected)], reviewer)
    assert len(reviewer._calls) == 2
    assert report['generated_code_review']['status'] == 'needs_information'
    assert report['generated_code_review']['issues'][0]['kind'] == 'missing_information'
    assert 'retention' in report['generated_code_review']['issues'][0]['detail']
    assert all('saveAsTable' not in a['content'] for a in corrected['artifacts'])
    assert_counts(report, writer, reviewer)


def test_no_table_timer_uses_only_actual_source_no_invented_bindings(ctx):
    service, record, model, inputs = ctx
    model['edges'] = [e for e in model['edges'] if e['source'] == 'wf']
    model['nodes'] = [n for n in model['nodes'] if n['kind'] not in {'mapping', 'transformation'}]
    for node in model['nodes']:
        if node['id'] in {'a', 'b'}:
            node['attributes'].update(TYPE='Timer', **{'Timer Value': '10'})
    design = deepcopy(record['design'])
    design['tasks'][0]['purpose'] = 'Preserve exported timer delay, no tables'
    design['artifact_plan'][0].update(mapping_ids=[], evidence_ids=['a'])
    new = store_for(service).append(record['package_hash'], 'wf', 'original',
        dict(design=design, role='architecture', retrieved_evidence_ids=['a', 'b']),
        {**deepcopy(record['source']), 'record_id': 'new-timer-source'}, record['version'])
    value = FIX['payload'](new)
    value['artifacts'] = [dict(relative_path='notebooks/load.ipynb', kind='notebook',
        content=FIX['notebook']().replace('from modules.clean import null_safe\\ndef transform(value):\\n    return null_safe(value)\\n',
                                       '# Exported timer delay: 10 seconds\\nimport time\\ntime.sleep(10)\\n'),
        task_ids=['shared'], evidence_ids=['a'])]
    value['tasks'][0].update(artifact_paths=['notebooks/load.ipynb'], evidence_ids=['a'], obligations=[],
                            status='blocked', blockers=['Timer draft, not runtime verified'])
    reviewer = review_model('timer')
    report, writer = run_graph((service, new, model, inputs), [submit(value)], reviewer)
    scope = json.loads(reviewer._calls[0][-1].content)
    assert scope['sql_phases'] == [] and scope['source_complete']
    assert 'Timer Value' in json.dumps(scope['canonical_source_xml_rows'])
    assert all('saveAsTable' not in a['content'] and 'spark.table' not in a['content'] for a in scope['artifacts'])
    assert report['generated_code_review']['status'] == 'review_ready'
    assert_counts(report, writer, reviewer)


def test_six_review_calls_finite_shared_budget_and_saved_unverified(ctx):
    reviewer = review_model('always_bug')
    values = []
    for i in range(12):
        value = bad_payload(ctx[1])
        value['artifacts'][1]['content'] += f'# revision {i}\n'
        values.append(submit(value))
    report, writer = run_graph(ctx, values, reviewer)
    assert len(reviewer._calls) == 6
    assert report['generated_code_review']['issues'][0]['kind'] == 'review_unavailable'
    assert report['artifact_hashes'] and not report['generated_code_review']['ai_reviewed']
    assert_counts(report, writer, reviewer)


def test_overall_model_budget_counts_review_not_just_writer(ctx):
    reviewer = review_model('always_bug')
    report, writer = run_graph(ctx, [submit(bad_payload(ctx[1]))] * 10, reviewer,
                              budget=DraftBudget(model_calls=5))
    assert report['budget_used']['model_calls'] == 5
    assert len(reviewer._calls) == 1
    assert report['artifact_hashes'] and report['generated_code_review']['status'] == 'needs_correction'
    assert_counts(report, writer, reviewer)


def test_bounded_context_never_truncates_or_claims_review(ctx):
    value = FIX['payload'](ctx[1])
    # The writer configures 160,000 characters, unlike the direct helper's
    # 64,000 default. Exceed the actual writer bound with a complete valid file.
    value['artifacts'][1]['content'] += '# ' + 'x' * 161_000 + '\n'
    reviewer = review_model()
    report, writer = run_graph(ctx, [submit(value)], reviewer)
    assert not reviewer._calls and report['artifact_hashes']
    assert report['status'] == 'generation_complete' and report['failure'] is None
    _, expected_bodies = FIX['_validate'](value, ctx[1], FIX['retrieved'](ctx[2], ctx[1]), candidate=True)
    expected_hashes = {path: sha256(content.encode('utf-8')).hexdigest()
                       for path, content in expected_bodies.items()}
    assert report['artifact_hashes'] == expected_hashes
    root = ctx[0].root / report['relative_directory']
    assert {path: (root / path).read_bytes() for path in expected_bodies} == {
        path: content.encode('utf-8') for path, content in expected_bodies.items()}
    receipt = report['generated_code_review']
    assert receipt['status'] == 'needs_information' and receipt['ai_reviewed'] is False
    assert receipt['runtime_verified'] is False
    assert receipt['tasks'][0]['artifact_hashes'] == expected_hashes
    assert receipt['issues'][0]['kind'] == 'review_unavailable'
    assert 'bounded review context' in receipt['issues'][0]['detail']
    assert 'no truncated review claimed' in receipt['issues'][0]['detail']
    assert report['translation_coverage'] is None and report['translation_assessment']['status'] == 'unverified'
    assert_counts(report, writer, reviewer)


def test_partial_source_cannot_claim_full_review_ready(ctx):
    _, record, model, _ = ctx
    evidence = _Evidence(model, record)
    evidence.page('shared', 0)
    value = GenerationArtifactSet.model_validate(FIX['payload'](record))
    reviewer = review_model()
    helper = GeneratedCodeReviewer(lambda messages, size: reviewer.invoke(messages))
    receipt = helper.review(value, {a.relative_path: a.content for a in value.artifacts}, record, evidence)
    assert receipt['status'] == 'needs_information'
    assert receipt['issues'][0]['kind'] == 'missing_information'
    assert json.loads(reviewer._calls[0][-1].content)['missing_record_keys']


def test_approved_legacy_does_not_invoke_candidate_reviewer(ctx):
    reviewer = review_model('failure')
    report, writer = run_graph(ctx, [submit(FIX['payload'](ctx[1]))], reviewer, candidate=False)
    assert report['status'] == 'generation_complete'
    assert not reviewer._calls and 'generated_code_review' not in report


def direct_review(ctx, response, *, safe_text=lambda text: True):
    _, record, model, _ = ctx
    evidence = _Evidence(model, record)
    for tid in evidence.rows:
        for sql in (False, True):
            rows = evidence.sql[tid] if sql else evidence.rows[tid]
            for offset in range(0, max(1, len(rows)), 12):
                evidence.page(tid, offset, sql=sql)
    value = GenerationArtifactSet.model_validate(FIX['payload'](record))
    calls = []
    def invoke(messages, size):
        calls.append((messages, size))
        if isinstance(response, Exception):
            raise response
        return AIMessage(content=response)
    receipt = GeneratedCodeReviewer(invoke, safe_text=safe_text).review(
        value, {a.relative_path: a.content for a in value.artifacts}, record, evidence)
    return receipt, calls


@pytest.mark.parametrize('response', [
    '{"status":"review_ready","issues":[]}',
    ' \n```json\n{"status":"review_ready","issues":[]}\n```\n ',
    '```\n{"status":"review_ready","issues":[]}\n```',
    [{'type': 'text', 'text': '{"status":"review_ready","issues":[]}'}],
    [{'type': 'text', 'text': '```json\n{"status":'},
     {'type': 'text', 'text': '"review_ready","issues":[]}\n```'}],
    [{'type': 'reasoning', 'reasoning': 'Untrusted private reasoning'},
     {'type': 'text', 'text': '{"status":"review_ready","issues":[]}'}],
])
def test_complete_standard_review_responses(ctx, response):
    receipt, calls = direct_review(ctx, response)
    assert receipt['ai_reviewed'] and receipt['status'] == 'review_ready'
    assert len(calls) == 1
    assert calls[0][1] == sum(len(m.content) for m in calls[0][0])


@pytest.mark.parametrize('response,stage,cls,locations', [
    ('prefix {"status":"review_ready","issues":[]}', 'json_parse', 'JSONDecodeError', []),
    ('```json\n{"status":"review_ready","issues":[]}\n``` trailing', 'json_parse', 'JSONDecodeError', []),
    ([{'type': 'reasoning', 'text': '{"status":"review_ready","issues":[]}'}], 'response_text', 'ValueError', []),
    ('{"status":"review_ready","issues":[],"password=PRIVATE":"PRIVATE"}', 'schema_validation', 'ValidationError', [['<unknown>']]),
    ('{"status":"review_ready","issues":"PRIVATE"}', 'schema_validation', 'ValidationError', [['issues']]),
    (json.dumps(dict(status='needs_correction', issues=[dict(kind='semantic_bug', task_id='shared',
        relative_path='modules/clean.py', detail='PRIVATE' * 86)])),
        'schema_validation', 'ValidationError', [['issues', 0, 'detail']]),
    (json.dumps(dict(status='needs_correction', issues=[dict(kind='semantic_bug', task_id='outside',
        relative_path='modules/clean.py', detail='PRIVATE')])), 'scope_validation', 'ValueError', []),
    (json.dumps(dict(status='needs_correction', issues=[dict(kind='semantic_bug', task_id='shared',
        relative_path='outside.py', detail='PRIVATE')])), 'scope_validation', 'ValueError', []),
    ('{"status":"needs_correction","issues":[]}', 'disposition_validation', 'ValueError', []),
    (RuntimeError('password=PRIVATE upstream body'), 'provider', 'RuntimeError', []),
])
def test_invalid_review_safe_stage_diagnostics_and_scope_intact(ctx, response, stage, cls, locations):
    receipt, calls = direct_review(ctx, response)
    task = receipt['tasks'][0]
    assert not receipt['ai_reviewed'] and task['artifact_hashes']
    assert task['diagnostic'] == dict(exception_class=cls, stage=stage, schema_locations=locations)
    assert 'PRIVATE' not in json.dumps(receipt)
    assert len(calls) == 1 and not receipt['execution_allowed']


def test_unsafe_response_stays_unreviewed(ctx):
    receipt, _ = direct_review(ctx, '{"status":"review_ready","issues":[]}', safe_text=lambda text: False)
    assert not receipt['ai_reviewed']
    assert receipt['tasks'][0]['diagnostic']['stage'] == 'response_text'


def test_generic_design_scope_keeps_ancestors_children_coverage_and_exact_parameters():
    design = dict(tasks=[dict(id=tid, purpose='Use relevant_parameter' if tid == 'own' else tid)
                         for tid in ('grandparent', 'parent', 'own', 'child', 'unrelated')],
        edges=[dict(source=a, target=b) for a, b in
               [('grandparent', 'parent'), ('parent', 'own'), ('own', 'child'), ('parent', 'unrelated')]],
        coverage=[dict(proposed_task_ids=[tid], original_task_id=tid) for tid in ('own', 'child', 'unrelated')],
        artifact_plan=[dict(proposed_task_ids=[tid], relative_path=tid) for tid in ('own', 'unrelated')],
        scoped_unresolved=[dict(proposed_task_ids=['child'], detail='Exact missing behavior')],
        parameters=[dict(name=name, purpose='Preserve whole declaration') for name in
                    ('relevant_parameter', 'source_parameter', 'unrelated_parameter')],
        cadence=dict(kind='manual'), blockers=['Global constraint'])
    before = deepcopy(design)
    scoped = _task_design(design, 'own', dict(source='Exact source_parameter literal'))
    assert [t['id'] for t in scoped['tasks']] == ['grandparent', 'parent', 'own', 'child']
    assert len(scoped['edges']) == 3
    assert [r['original_task_id'] for r in scoped['coverage']] == ['own', 'child']
    assert [p['name'] for p in scoped['parameters']] == ['relevant_parameter', 'source_parameter']
    assert scoped['scoped_unresolved'] == design['scoped_unresolved']
    assert scoped['blockers'] == design['blockers'] and design == before


def test_unrelated_large_design_not_sent_but_complete_source_and_code_preserved(ctx):
    _, record, model, _ = ctx
    record['design']['tasks'].append(dict(id='unrelated', purpose='x' * 65_000))
    # Direct helper value covers the original task, not a writer submission.
    receipt, calls = direct_review(ctx, '{"status":"review_ready","issues":[]}')
    scope = json.loads(calls[0][0][-1].content)
    assert receipt['ai_reviewed'] and calls[0][1] < 64_000
    assert [t['id'] for t in scope['selected_design']['tasks']] == ['shared']
    evidence = _Evidence(model, record)
    assert scope['canonical_source_xml_rows'] == sorted(evidence.rows['shared'], key=lambda r: r['key'])
    assert scope['sql_phases'] == sorted(evidence.sql['shared'], key=lambda r: r['key'])
    payload = FIX['payload'](record)
    assert {a['relative_path']: a['content'] for a in scope['artifacts']} == {
        a['relative_path']: a['content'] for a in payload['artifacts']}