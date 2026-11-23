"""Whole-evidence partition contracts with a local fake callback, never a provider.

These tests deliberately use no app runtime, model clients, source execution,
CLI, saved history writes or browser harness. Running them is separately gated
by the caller's permission.
"""
from copy import deepcopy
from hashlib import sha256
import json
import socket
from types import SimpleNamespace

import pytest

from workbench.generated_code_review import (
    GeneratedCodeReviewer, PARTITION_SYSTEM, SYSTEM, _digest, _json,
    _partition_manifest, _verify_partition_coverage,
)


class Evidence:
    def __init__(self):
        self.rows = {'load': [dict(key=f'row:{i:03}', evidence_ids=['mapping'],
            value={'ordinal': i, 'original': f'whole-row-{i}:' + 'r' * 3000})
            for i in range(9)]}
        self.sql = {'load': [dict(key=f'sql:{i:03}', evidence_ids=['mapping'],
            original_sql=f'SELECT {i} /* ' + 'q' * 3000 + ' */', phase=phase)
            for i, phase in enumerate(('pre', 'main', 'post'))]}
        self.fetched = {'load': {r['key'] for r in self.rows['load']}}
        self.sql_fetched = {'load': {r['key'] for r in self.sql['load']}}

    def complete(self, tid):
        return (self.fetched[tid] == {r['key'] for r in self.rows[tid]}
                and self.sql_fetched[tid] == {r['key'] for r in self.sql[tid]})


class FakeReview:
    def __init__(self, *, budget=100, alter=None, issue_count=0):
        self.budget = budget
        self.alter = alter
        self.issue_count = issue_count
        self.attempts = []
        self.calls = []

    def __call__(self, messages, size):
        request = json.loads(messages[-1].content)
        assert size == sum(len(m.content) for m in messages)
        self.attempts.append((deepcopy(request), size))
        if len(self.calls) >= self.budget:
            return None
        self.calls.append((deepcopy(request), size))
        if 'phase' not in request:
            return SimpleNamespace(content='{"status":"review_ready","issues":[]}', tool_calls=[])
        result = {k: request[k] for k in ('task_id', 'scope_sha256', 'request_sha256',
            'phase', 'partition_index', 'partition_count')}
        result.update(status='review_ready', issues=[],
                      reviewed_content_sha256=request['coverage_sha256'])
        if self.issue_count and request['phase'] == 'evidence_partition':
            result.update(status='needs_correction', issues=[dict(kind='semantic_bug',
                task_id=request['task_id'], relative_path='modules/load.py',
                detail=f'Partition {request["partition_index"]} semantic correction {i}')
                for i in range(self.issue_count)])
        if self.alter:
            override = self.alter(request, result, messages)
            if override is not None:
                return override
        return SimpleNamespace(content=_json(result), tool_calls=[])


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')

    def forbidden(*args, **kwargs):
        raise AssertionError('Network is forbidden in partition coverage tests')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    design = dict(tasks=[dict(id='load', purpose='Preserve captured SQL phases and all rows')],
        edges=[], parameters=[dict(name='unused_parameter', purpose='Retain full selected Design')])
    record = dict(design=design, workflow_id='workflow', scenario='original')
    value = SimpleNamespace(tasks=[SimpleNamespace(task_id='load')],
        artifacts=[SimpleNamespace(relative_path='modules/load.py', kind='module',
            task_ids=['load'], evidence_ids=['mapping'])],
        design_id='design', design_version=7, design_sha256=_digest(design),
        input_version='input', package_hash='package')
    bodies = {'modules/load.py': 'def load():\n    return 1\n'}
    evidence = Evidence()
    return value, bodies, record, evidence


def run(case, fake=None, *, bound=None, reviewer=None, **kwargs):
    if reviewer is None:
        reviewer = GeneratedCodeReviewer(fake, max_characters=bound or len(PARTITION_SYSTEM) + 15_000)
    return reviewer.review(*case, **kwargs), reviewer


def partition_calls(fake):
    return [body for body, _ in fake.calls if body.get('phase') == 'evidence_partition']


def test_complete_exact_rows_sql_code_design_and_hashes_once(case):
    fake = FakeReview()
    before = deepcopy(case)
    report, reviewer = run(case, fake)
    task = report['tasks'][0]
    parts = partition_calls(fake)
    assert len(parts) > 1
    assert len(fake.calls) == len(parts) + 1 <= 6
    assert all(size <= reviewer.max_characters for _, size in fake.calls)
    assert report['ai_reviewed'] and report['status'] == 'review_ready'
    assert task['partition_review_complete'] and task['synthesis_reviewed']
    assert 'end-to-end equivalence not established' in report['assurance']
    for collection, expected in (('canonical_source_xml_rows', case[3].rows['load']),
                                 ('sql_phases', case[3].sql['load'])):
        assert [row for part in parts for row in part[collection]] == expected
    expected_artifact = dict(relative_path='modules/load.py', kind='module', task_ids=['load'],
        evidence_ids=['mapping'], content=case[1]['modules/load.py'],
        content_sha256=sha256(case[1]['modules/load.py'].encode()).hexdigest())
    for part in parts:
        assert part['selected_design'] == case[2]['design']
        assert part['artifacts'] == [expected_artifact]
        assert part['identity']['design_sha256'] == case[0].design_sha256
        assert part['scope_sha256'] == task['scope_sha256']
        assert part['request_sha256'] == _digest({k: v for k, v in part.items() if k != 'request_sha256'})
        assert part['coverage_sha256'] == _digest(part['coverage_records'])
        for item in part['coverage_records']:
            family = 'rows' if item['collection'] == 'canonical_source_xml_rows' else 'sql'
            row = getattr(case[3], family)['load'][item['index']]
            assert item['key'] == row['key'] and item['content_sha256'] == _digest(row)
    manifest = task['coverage_manifest']
    assert manifest['source_sha256'] == _digest(dict(
        canonical_source_xml_rows=case[3].rows['load'], sql_phases=case[3].sql['load']))
    assert manifest['artifacts'][0]['content_sha256'] == expected_artifact['content_sha256']
    assert task['coverage_manifest_sha256'] == _digest(manifest)
    synthesis = fake.calls[-1][0]
    assert synthesis['phase'] == 'final_synthesis'
    assert synthesis['selected_design'] == case[2]['design']
    assert synthesis['artifacts'] == [expected_artifact]
    assert synthesis['coverage_manifest'] == manifest
    assert synthesis['partition_reviews'] == task['partition_reviews'][:-1]
    assert synthesis['coverage_sha256'] == _digest(synthesis['partition_reviews'])
    for receipt in task['partition_reviews']:
        assert receipt['receipt_sha256'] == _digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
    assert case[1:3] == before[1:3]
    assert case[3].rows == before[3].rows and case[3].sql == before[3].sql
    assert report['runtime_verified'] is False
    assert report['execution_allowed'] is False and report['deployment_allowed'] is False


@pytest.mark.parametrize('mutation', ['drop', 'modify', 'duplicate', 'reorder', 'artifact', 'source'])
def test_manifest_verifier_rejects_modified_or_dropped_coverage(case, mutation):
    scope = dict(canonical_source_xml_rows=deepcopy(case[3].rows['load']),
        sql_phases=deepcopy(case[3].sql['load']), source_complete=True,
        selected_design=case[2]['design'], condition_upstream_artifacts=[])
    content = case[1]['modules/load.py']
    artifacts = [dict(relative_path='modules/load.py', kind='module', task_ids=['load'],
        evidence_ids=['mapping'], content=content, content_sha256=sha256(content.encode()).hexdigest())]
    scope['artifacts'] = deepcopy(artifacts)
    manifest = _partition_manifest(scope, artifacts)
    parts = [deepcopy(manifest['records'][:6]), deepcopy(manifest['records'][6:])]
    if mutation == 'drop':
        parts.pop()
    elif mutation == 'modify':
        parts[0][0]['content_sha256'] = '0' * 64
    elif mutation == 'duplicate':
        parts.append(deepcopy(parts[0]))
    elif mutation == 'reorder':
        parts.reverse()
    elif mutation == 'artifact':
        artifacts[0]['content'] += '# modified\n'
    else:
        scope['sql_phases'][-1]['original_sql'] += ' changed'
    with pytest.raises(ValueError):
        _verify_partition_coverage(scope, artifacts, manifest, parts)


@pytest.mark.parametrize('field', ['task_id', 'scope_sha256', 'request_sha256', 'phase',
                                  'partition_index', 'partition_count', 'reviewed_content_sha256'])
def test_provider_partition_binding_or_coverage_mismatch_fails_closed(case, field):
    def alter(request, result, messages):
        result[field] = 999 if field in ('partition_index', 'partition_count') else (
            'final_synthesis' if field == 'phase' else 'wrong')
    fake = FakeReview(alter=alter)
    report, _ = run(case, fake)
    assert len(fake.calls) == 1
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'
    assert not report['tasks'][0]['partition_review_complete']
    assert report['tasks'][0]['diagnostic']['stage'] == 'scope_validation'


@pytest.mark.parametrize('budget', [0, 1, 2])
def test_partial_existing_budget_stops_without_synthesis_or_optimism(case, budget):
    fake = FakeReview(budget=budget)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert len(fake.calls) == budget and len(fake.attempts) == budget + 1
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'
    assert not task['partition_review_complete'] and not task['synthesis_reviewed']
    assert len(task['partition_reviews']) == budget
    assert not any(b['phase'] == 'final_synthesis' for b, _ in fake.attempts)


def test_missing_source_has_entire_source_manifest_but_zero_inference(case):
    # Leave enough fetched evidence to select the oversized path.
    case[3].sql_fetched['load'].remove('sql:002')
    fake = FakeReview()
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert fake.calls == [] and fake.attempts == []
    assert report['status'] == 'needs_information' and not report['ai_reviewed']
    assert task['issues'][0]['kind'] == 'missing_information'
    assert len(task['coverage_manifest']['records']) == 12
    assert task['coverage_manifest']['source_complete'] is False
    assert task['coverage_manifest']['records'][-1]['content_sha256'] == _digest(case[3].sql['load'][-1])


def test_all_semantic_findings_retained_beyond_public_twelve_and_synthesis_cannot_erase(case):
    fake = FakeReview(issue_count=12)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    parts = partition_calls(fake)
    assert task['synthesis_reviewed'] and report['ai_reviewed']
    assert task['partition_reviews'][-1]['status'] == 'review_ready'
    assert report['status'] == 'needs_correction'
    assert len(task['issues']) == 12
    assert len(task['critical_issue_references']) == len(parts) * 12 > 12
    for ref in task['critical_issue_references']:
        receipt = task['partition_reviews'][ref['partition_index']]
        assert ref['request_sha256'] == receipt['request_sha256']
        assert receipt['issues'][ref['issue_index']]['kind'] == 'semantic_bug'
    assert len(fake.calls[-1][0]['partition_reviews']) == len(parts)
    assert all(len(r['issues']) == 12 for r in fake.calls[-1][0]['partition_reviews'])


@pytest.mark.parametrize('mode', ['budget', 'invalid', 'unavailable', 'missing_information'])
def test_final_synthesis_is_required_for_readiness(case, mode):
    def alter(request, result, messages):
        if request['phase'] != 'final_synthesis':
            return None
        if mode == 'budget':
            raise RuntimeError('Private provider error must not escape')
        if mode == 'invalid':
            result['request_sha256'] = 'stale'
        elif mode == 'unavailable':
            return SimpleNamespace(content='not JSON', tool_calls=[])
        elif mode == 'missing_information':
            result.update(status='needs_information', issues=[dict(kind='missing_information',
                task_id='load', relative_path=None, detail='Cross-partition behavior remains unknown')])
    fake = FakeReview(alter=alter)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert task['partition_review_complete'] and report['status'] == 'needs_information'
    assert task['synthesis_reviewed'] is (mode == 'missing_information')
    assert report['ai_reviewed'] is (mode == 'missing_information')
    assert 'Private provider error' not in _json(report)


def test_callback_budget_can_expire_exactly_before_synthesis(case):
    probe = FakeReview()
    _, _ = run(case, probe)
    fake = FakeReview(budget=len(partition_calls(probe)))
    report, _ = run(case, fake)
    assert report['tasks'][0]['partition_review_complete']
    assert not report['tasks'][0]['synthesis_reviewed']
    assert report['status'] == 'needs_information' and not report['ai_reviewed']
    assert fake.attempts[-1][0]['phase'] == 'final_synthesis'


@pytest.mark.parametrize('issue_count', [0, 1])
def test_six_call_callback_budget_is_shared_across_tasks_without_new_allowance(case, issue_count):
    case[0].tasks.append(SimpleNamespace(task_id='other'))
    case[0].artifacts[0].task_ids.append('other')
    case[2]['design']['tasks'].append(dict(id='other', purpose='Second complete scope'))
    for collection in ('rows', 'sql', 'fetched', 'sql_fetched'):
        data = getattr(case[3], collection)
        data['other'] = deepcopy(data['load'])
    fake = FakeReview(budget=6, issue_count=issue_count)
    report, _ = run(case, fake)
    assert len(fake.calls) == 6 and len(fake.attempts) == 7
    assert report['tasks'][0]['status'] == ('needs_correction' if issue_count else 'review_ready')
    assert report['tasks'][1]['status'] == 'needs_information'
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'


def test_budget_exhaustion_preserves_known_critical_refs_but_not_ready_status(case):
    fake = FakeReview(budget=1, issue_count=12)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert report['status'] == 'needs_information' and not report['ai_reviewed']
    assert len(task['critical_issue_references']) == 12
    assert len(task['partition_reviews'][0]['issues']) == 12


def test_full_findings_too_large_for_synthesis_are_not_truncated_or_sent(case):
    def alter(request, result, messages):
        for issue in result['issues']:
            issue['detail'] = 'Actionable semantic correction: '.ljust(600, 'b')
    fake = FakeReview(issue_count=12, alter=alter)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert task['partition_review_complete'] and not task['synthesis_reviewed']
    assert report['status'] == 'needs_information' and not report['ai_reviewed']
    assert len(task['critical_issue_references']) > 12
    assert all(body['phase'] == 'evidence_partition' for body, _ in fake.attempts)
    assert all(len(issue['detail']) == 600 for receipt in task['partition_reviews']
               for issue in receipt['issues'])


def test_final_synthesis_can_find_cross_partition_semantic_bug(case):
    def alter(request, result, messages):
        if request['phase'] == 'final_synthesis':
            result.update(status='needs_correction', issues=[dict(kind='semantic_bug',
                task_id='load', relative_path='modules/load.py',
                detail='Post-SQL changes a value consumed by another partition; preserve phase order')])
    fake = FakeReview(alter=alter)
    report, _ = run(case, fake)
    task = report['tasks'][0]
    assert all(r['status'] == 'review_ready' for r in task['partition_reviews'][:-1])
    assert task['synthesis_reviewed'] and report['ai_reviewed']
    assert report['status'] == 'needs_correction'
    ref = task['critical_issue_references'][0]
    assert ref['partition_index'] == task['partition_count']
    assert task['partition_reviews'][-1]['issues'][ref['issue_index']]['kind'] == 'semantic_bug'


def test_source_complete_false_even_without_missing_keys_cannot_be_reviewed(case):
    case[3].complete = lambda tid: False
    fake = FakeReview()
    report, _ = run(case, fake)
    assert fake.attempts == []
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'


@pytest.mark.parametrize('kind', ['row', 'sql', 'code', 'design', 'upstream'])
def test_indivisible_record_or_common_code_too_large_never_calls(case, kind):
    huge = 'x' * 170_000
    if kind == 'row':
        case[3].rows['load'][-1]['value']['original'] = huge
    elif kind == 'sql':
        case[3].sql['load'][-1]['original_sql'] = huge
    elif kind == 'code':
        case[1]['modules/load.py'] += '# ' + huge + '\n'
    elif kind == 'design':
        case[2]['design']['tasks'][0]['purpose'] = huge
    else:
        case[2]['design']['tasks'][0].update(implementation_kind='condition_task',
            condition_task=dict(left=dict(kind='task_value', task_id='producer')))
        case[0].artifacts.append(SimpleNamespace(relative_path='modules/upstream.py', kind='module',
            task_ids=['producer'], evidence_ids=['mapping']))
        case[1]['modules/upstream.py'] = '# ' + huge + '\n'
    fake = FakeReview()
    report, _ = run(case, fake, bound=160_000)
    assert fake.calls == [] and fake.attempts == []
    assert report['status'] == 'needs_information' and report['ai_reviewed'] is False
    assert 'bounded review context' in report['issues'][0]['detail']
    assert 'no truncated review claimed' in report['issues'][0]['detail']


def test_condition_upstream_and_unowned_artifact_hashes_preserved(case):
    case[2]['design']['tasks'][0].update(implementation_kind='condition_task',
        condition_task=dict(left=dict(kind='task_value', task_id='producer')))
    for tid in ('producer', 'unrelated'):
        path = f'modules/{tid}.py'
        case[0].artifacts.append(SimpleNamespace(relative_path=path, kind='module',
            task_ids=[tid], evidence_ids=['mapping']))
        case[1][path] = 'def publish():\n    return 7\n'
    fake = FakeReview()
    report, _ = run(case, fake)
    manifest = report['tasks'][0]['coverage_manifest']
    assert len(manifest['artifacts']) == 3
    for body, _ in fake.calls:
        assert [a['relative_path'] for a in body['condition_upstream_artifacts']] == ['modules/producer.py']
        assert body['condition_upstream_artifacts'][0]['content'] == case[1]['modules/producer.py']
        assert set(body['related_artifact_hashes']) == set(case[1])


def test_exact_cache_reuses_but_any_source_sql_code_design_change_rebinds(case):
    fake = FakeReview()
    report, reviewer = run(case, fake)
    first_calls = len(fake.calls)
    repeated, _ = run(case, reviewer=reviewer)
    assert repeated == report and len(fake.calls) == first_calls
    old_hash = report['tasks'][0]['scope_sha256']
    for change in ('source', 'sql', 'code', 'design'):
        if change == 'source':
            case[3].rows['load'][0]['value']['original'] += ' changed'
        elif change == 'sql':
            case[3].sql['load'][0]['original_sql'] += ' changed'
        elif change == 'code':
            case[1]['modules/load.py'] += '# changed\n'
        else:
            case[2]['design']['parameters'][0]['purpose'] += ' changed'
        changed, _ = run(case, reviewer=reviewer)
        new_hash = changed['tasks'][0]['scope_sha256']
        assert new_hash != old_hash
        old_hash = new_hash


@pytest.mark.parametrize('mutation', ['drop', 'content', 'findings'])
def test_corrupt_cached_partition_cannot_claim_review_or_trigger_inference(case, mutation):
    fake = FakeReview()
    report, reviewer = run(case, fake)
    calls = len(fake.calls)
    task = report['tasks'][0]
    if mutation == 'drop':
        task['partition_plan'].pop()
    elif mutation == 'content':
        task['coverage_manifest']['records'][0]['content_sha256'] = 'changed'
    else:
        task['partition_reviews'].pop()
    repeated, _ = run(case, reviewer=reviewer)
    assert len(fake.calls) == calls
    assert repeated['status'] == 'needs_information' and not repeated['ai_reviewed']


def test_request_mutation_is_invalid_and_no_secret_body_is_exposed(case):
    secret = 'PRIVATE_PROVIDER_BODY'

    def alter(request, result, messages):
        messages[-1].content = secret
    fake = FakeReview(alter=alter)
    report, _ = run(case, fake)
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'
    assert report['tasks'][0]['diagnostic']['stage'] == 'coverage_validation'
    assert secret not in _json(report)


def test_unsafe_input_or_response_withheld_without_secret_receipt(case):
    secret = 'PRIVATE_SECRET_VALUE'
    fake = FakeReview()
    reviewer = GeneratedCodeReviewer(fake, max_characters=len(PARTITION_SYSTEM) + 15_000,
        safe_text=lambda text: secret not in text)
    case[3].rows['load'][0]['value']['original'] += secret
    report, _ = run(case, reviewer=reviewer)
    assert fake.attempts == [] and not report['ai_reviewed']
    assert secret not in _json(report)
    case[3].rows['load'][0]['value']['original'] = 'r' * 3000

    def alter(request, result, messages):
        result.update(status='needs_information', issues=[dict(kind='missing_information',
            task_id='load', relative_path=None, detail=secret)])
    fake.alter = alter
    report, _ = run(case, reviewer=reviewer)
    assert len(fake.calls) == 1 and not report['ai_reviewed']
    assert secret not in _json(report)


def test_allow_inference_false_and_no_provider_make_no_calls(case):
    fake = FakeReview()
    report, _ = run(case, fake, allow_inference=False)
    assert fake.attempts == [] and not report['ai_reviewed']
    report, _ = run(case)
    assert not report['ai_reviewed'] and report['status'] == 'needs_information'


def test_below_bound_keeps_exact_legacy_request_response_and_assurance(case):
    case[3].rows['load'] = case[3].rows['load'][:1]
    case[3].rows['load'][0]['value']['original'] = 'small exact record'
    case[3].fetched['load'] = {'row:000'}
    case[3].sql['load'] = []
    case[3].sql_fetched['load'] = set()
    fake = FakeReview()
    report, _ = run(case, fake, bound=160_000)
    assert len(fake.calls) == 1
    body, size = fake.calls[0]
    assert 'phase' not in body and 'coverage_records' not in body
    assert size == len(SYSTEM) + len(_json(body))
    assert body['canonical_source_xml_rows'] == case[3].rows['load']
    assert body['selected_design']['parameters'] == []
    assert report['status'] == 'review_ready' and report['ai_reviewed']
    assert report['assurance'] == 'AI-reviewed not runtime verified'
    assert 'partition_plan' not in report['tasks'][0]