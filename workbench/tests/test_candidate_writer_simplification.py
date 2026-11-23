"""Focused offline candidate writer regressions. No provider, cloud or history edits."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
import socket

import pytest
from langchain_core.messages import AIMessage

from workbench.draft_artifact_reads import read_result
from workbench.selected_job_deploy import _owned_notebooks
from workbench.spark_codegen import (
    ContractError, _CandidateRepairs, _GenerationReceipts,
    _StagedArtifacts, _translation_assessment, _validate, draft_agent_design,
    create_spark_codegen,
)

FIX = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests' / 'test_spark_codegen.py'))


def test_writer_guidance_scopes_demo_and_child_job_behavior():
    skills = Path(__file__).resolve().parents[1] / 'skills' / 'spark_codegen'
    implementation = (skills / 'library' / 'implementation' / 'SKILL.md').read_text(encoding='utf-8')
    context = (skills / 'CONTEXT.md').read_text(encoding='utf-8')
    demo_section = implementation.index('## User-authorized synthetic demo bindings and translations')
    general_guidance = implementation[:demo_section]
    explicit_demo_guidance = implementation[demo_section:]

    assert 'supplied synthetic UC source data' not in general_guidance
    assert 'a single-workflow design stays single-workflow' in general_guidance
    assert 'not a Databricks child Job' in general_guidance
    assert 'Do not invent `child_job_id`' in general_guidance
    assert 'demo_mode' not in general_guidance
    assert 'only when the caller explicitly authorizes a synthetic UC demo' in explicit_demo_guidance
    assert 'For an ordinary single-workflow conversion, ignore' in explicit_demo_guidance
    assert 'supplied synthetic UC source data' in explicit_demo_guidance
    assert 'demo_mode' in explicit_demo_guidance
    assert 'UC tables, bronze/silver/gold, or candidate-code mode do not imply synthetic-demo authorization' in explicit_demo_guidance
    assert 'no external call' in explicit_demo_guidance
    assert 'Non-demo mode must require the actual child job/notification binding' in explicit_demo_guidance
    assert 'candidate-code mode do not imply synthetic-demo authorization' in context


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden in candidate writer regression')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return FIX['context'].__wrapped__(tmp_path)


def receipts(ctx, *, candidate=True):
    _, record, model, _ = ctx
    evidence = FIX['retrieved'](model, record)
    staged = _StagedArtifacts(record)
    metadata = _GenerationReceipts(record, staged, evidence, 'offline-new-writer', candidate=candidate)
    file = staged.stage('notebooks/load.ipynb', 'notebook', FIX['notebook']())
    metadata.binding(file['relative_path'], ['shared'], ['mapping'], file['content_sha256'])
    return metadata, staged, evidence


def contradictory(metadata, evidence):
    task = FIX['payload'](metadata.record)['tasks'][0]
    task.update(status='blocked', blockers=['Missing actual target write contract'],
                artifact_paths=['notebooks/load.ipynb'], evidence_ids=['mapping'], obligations=[])
    metadata.task(task)
    rows = evidence.rows['shared'] + evidence.sql['shared']
    item = dict(task_id='shared', kind='transformation',
        source_record_keys=[row['key'] for row in rows],
        evidence_ids=sorted({identifier for row in rows for identifier in row['evidence_ids']}),
        artifact_paths=['notebooks/load.ipynb'], status='translated',
        explanation='Contradictory model-authored translation claim',
        blockers=['Actual translation still needs repair'])
    # Cite each record in the binding so the EXACT historical class of status
    # contradiction, not a different missing-citation failure, is reached.
    old = metadata.bindings['notebooks/load.ipynb']['receipt_sha256']
    metadata.binding('notebooks/load.ipynb', ['shared'], item['evidence_ids'],
        metadata.staged.files['notebooks/load.ipynb']['content_sha256'], old)
    metadata.translation(item)
    return item


def test_exact_contradiction_is_review_issue_not_save_rejection(ctx):
    metadata, staged, evidence = receipts(ctx)
    original = contradictory(metadata, evidence)
    before = deepcopy(metadata.translations)
    authored_tasks = deepcopy(metadata.tasks)
    submitted = deepcopy(metadata.submitted_metadata)
    authored = staged.hydrate(dict(**metadata.envelope,
        tasks=[row['value'] for row in metadata.tasks.values()],
        artifacts=[row['value'] for row in metadata.bindings.values()],
        translation_coverage={'items': [original]}))
    with pytest.raises(ContractError, match='Translated items require drafted task-owned implementation artifacts without blockers'):
        _translation_assessment(authored, evidence)
    assert metadata.missing() == []  # no basis or obligation bookkeeping needed
    value = metadata.assemble()
    _, bodies = _validate(value, metadata.record, evidence, candidate=True)
    assert bodies['notebooks/load.ipynb'] == staged.files['notebooks/load.ipynb']['content']
    assert value.translation_coverage is None and value.tasks[0].status == 'drafted'
    assert value.tasks[0].blockers == []  # generated owned code, not translation certification
    assert _translation_assessment(value, evidence)['status'] == 'unverified'
    assert metadata.translations == before
    assert metadata.tasks == authored_tasks and metadata.submitted_metadata == submitted
    assert metadata.recovery['submitted_translation_claims'] == [original]
    assert any(issue['kind'] == 'task_behavior_unverified' and
        issue['task_id'] == 'shared' and issue['reasons'] == authored_tasks['shared']['value']['blockers']
        for issue in metadata.recovery['review_issues'])
    assert any(issue['kind'] == 'translation_claim_pending_review' and issue['submitted'] == original
        for issue in metadata.recovery['review_issues'])
    assert any(issue['kind'] == 'translation_metadata_inconsistent' and
        issue['reason'] == 'Translated items require drafted task-owned implementation artifacts without blockers'
        for issue in metadata.recovery['review_issues'])


@pytest.mark.parametrize('relative_path', ['notebooks/load.ipynb', 'load.ipynb'])
def test_plan_projection_without_authored_tasks_or_translations(ctx, relative_path):
    metadata, staged, evidence = receipts(ctx)
    metadata.record['design']['artifact_plan'][0]['relative_path'] = relative_path
    metadata.envelope['design_sha256'] = FIX['_digest'](metadata.record['design'])
    metadata.bindings.clear()
    metadata.bind_planned_file('notebooks/load.ipynb')
    assert set(metadata.bindings) == {'notebooks/load.ipynb'}
    value = metadata.assemble()
    assert value.tasks[0].artifact_paths == ['notebooks/load.ipynb']
    assert value.tasks[0].status == 'drafted' and value.translation_coverage is None
    assert value.tasks[0].blockers == []
    assert _translation_assessment(value, evidence)['status'] == 'unverified'
    assert any(item['kind'] == 'translation_metadata_missing' for item in metadata.recovery['review_issues'])


def test_plan_never_guesses_unretrieved_or_unplanned_ownership(ctx):
    metadata, staged, evidence = receipts(ctx)
    metadata.bindings.clear()
    evidence.fetched['shared'].clear()
    evidence.sql_fetched['shared'].clear()
    metadata.bind_planned_file('notebooks/load.ipynb')
    assert not metadata.bindings
    staged.stage('notebooks/unplanned.ipynb', 'notebook', FIX['notebook']())
    metadata.bind_planned_file('notebooks/unplanned.ipynb')
    assert not metadata.bindings


@pytest.mark.parametrize('change', ['hash', 'bytes', 'path', 'owner', 'citation', 'input'])
def test_candidate_projection_preserves_safety_guards(ctx, change):
    metadata, staged, evidence = receipts(ctx)
    ref = metadata.bindings['notebooks/load.ipynb']['value']
    if change == 'hash':
        ref['content_sha256'] = 'f' * 64
    elif change == 'bytes':
        staged.files['notebooks/load.ipynb']['content'] += ' '
    elif change == 'path':
        ref['relative_path'] = 'notebooks/../escape.ipynb'
    elif change == 'owner':
        ref['task_ids'] = ['outside']
    elif change == 'citation':
        ref['evidence_ids'] = ['outside']
    else:
        metadata.envelope['input_version'] = 'f' * 64
    with pytest.raises(ContractError):
        metadata.assemble()


def run_script(ctx, actions, *, candidate=True):
    service, record, model, _ = ctx
    evidence = FIX['retrieved'](model, record)
    fetches = []
    for sql in (False, True):
        rows = evidence.sql['shared'] if sql else evidence.rows['shared']
        for offset in range(0, max(1, len(rows)), 12):
            fetches.append(('fetch_sql_phases' if sql else 'fetch_source_task', dict(task_id='shared', offset=offset)))
    responses = [FIX['call'](name, args, number) for number, (name, args) in enumerate(fetches + actions)]
    responses.append(AIMessage(content='No more work requested'))
    route = FIX['ScriptedChat'](responses=responses, custom_get_token_ids=lambda text: list(range(len(text) // 4)))
    service.runtime.role_models = {'artifact': route}
    public = dict(id='source-run', consent=True, generate_code=True, status='running',
        **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
    service.record = lambda *args, **kwargs: deepcopy(public)
    service.update_record = lambda value, **kwargs: public.update(value)
    before = (service.state / 'redesigns.sqlite').read_bytes()
    if candidate:
        report = draft_agent_design(service, record, agent_run_id='source-run', consent=True)
    else:
        agent = create_spark_codegen(service, record)
        # Current public factory defaults to candidate mode even for reviewed
        # designs. Exercise the actual retained noncandidate branch explicitly.
        agent.require_review = True
        agent.candidate = False
        report = agent.draft(consent=True)
    assert (service.state / 'redesigns.sqlite').read_bytes() == before
    assert record['generation_allowed'] is False
    assert report['artifact_approved'] is False
    assert report['execution_allowed'] is False and report['deployment_allowed'] is False
    return report, route


def assert_unreviewed_candidate(report, status):
    """Generated inventory is independent of translation/review certification."""
    assert report['status'] == status and report['failure'] is None
    availability = report['candidate_availability']
    assert availability['generation_status'] == status
    assert availability['generation_complete'] is (status == 'generation_complete')
    assert availability['semantic_parity'] == 'unverified'
    assert availability['execution_allowed'] is False and availability['deployment_allowed'] is False
    assert report['translation_coverage'] is None
    assert report['translation_assessment']['status'] == 'unverified'
    review = report['generated_code_review']
    assert review['status'] == 'needs_information' and review['ai_reviewed'] is False
    assert review['runtime_verified'] is False
    assert review['execution_allowed'] is False and review['deployment_allowed'] is False
    assert any(issue['kind'] == 'review_unavailable' for issue in review['issues'])


def saved(ctx, report):
    service, record, _, _ = ctx
    raw = (service.root / report['relative_directory'] / 'manifest.json').read_bytes()
    job = dict(kind='spark_draft', status='completed', package_hash=record['package_hash'],
        design_id=record['id'], design_version=record['version'], input_version=record['input_version'],
        output={'run_id': report['run_id']}, manifest_sha256=sha256(raw).hexdigest())
    return read_result(service.root, job, 'f' * 32)


def test_compiled_commit_exact_contradiction_immediate_and_saved_access(ctx):
    _, record, model, _ = ctx
    metadata, _, evidence = receipts(ctx)
    original = contradictory(metadata, evidence)
    task = metadata.tasks['shared']['value']
    path = 'notebooks/candidate.ipynb'
    task['artifact_paths'] = [path]
    original['artifact_paths'] = [path]
    content = FIX['notebook']()
    expected = _StagedArtifacts(record)
    digest = expected.stage(path, 'notebook', content)['content_sha256']
    report, route = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=path, kind='notebook', content=content)),
        ('stage_generation_task', dict(task=task)),
        ('stage_generation_artifact_binding', dict(relative_path=path, task_ids=['shared'],
            evidence_ids=original['evidence_ids'], content_sha256=digest)),
        ('stage_generation_translation', dict(item=original)),
        ('commit_generation_receipts', {}),
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    assert report['translation_coverage'] is None
    assert report['candidate_availability']['tasks'][0]['code_available']
    result = saved(ctx, report)
    assert result['artifacts'][path] == expected.files[path]['content']
    owned, files, hashes = _owned_notebooks(record['design'], result['manifest'], result)
    assert owned == {'shared': path}
    assert hashes[path] == digest
    messages = [message for call in route._calls for message in call if getattr(message, 'type', '') == 'tool']
    immediate = next(json.loads(message.content) for message in messages if message.name == 'stage_generation_artifact')
    assert immediate['candidate_availability']['code_available']
    assert not immediate['candidate_availability']['tasks'][0]['code_available']  # unbound, no ownership guessing
    assert any(item['kind'] == 'translation_metadata_inconsistent' and 'without blockers' in item['reason']
               for item in report['candidate_review_issues'])
    recovery = report['writer_recovery_evidence']
    assert recovery['submitted_translation_claims'] == [original]
    assert any(row['submitted'] == task for row in recovery['submitted_metadata'])
    assert any(item['kind'] == 'translation_claim_pending_review' and item['submitted'] == original
               for item in report['candidate_review_issues'])
    assert report['tasks'][0]['status'] == 'drafted' and report['tasks'][0]['blockers'] == []
    assert not report['execution_allowed'] and not report['deployment_allowed']


@pytest.mark.parametrize('kind,path,bad,good', [
    ('module', 'modules/code.py', 'def broken(:\n', 'value = 1\n'),
    ('sql', 'sql/code.sql', 'SELECT (', 'SELECT 1'),
    ('notebook', 'notebooks/load.ipynb', '{invalid JSON', FIX['notebook']()),
    ('notebook', 'notebooks/load.ipynb', FIX['notebook']().replace('def transform(value):', 'def transform(:'), FIX['notebook']()),
])
def test_actual_syntax_requires_targeted_repair_before_acceptance(ctx, kind, path, bad, good):
    _, record, _, _ = ctx
    evidence = FIX['retrieved'](ctx[2], record)
    staged = _StagedArtifacts(record)
    good_receipt = staged.stage(path, kind, good)
    report, route = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=path, kind=kind, content=bad)),
        ('commit_generation_receipts', {}),
        ('stage_generation_artifact', dict(relative_path=path, kind=kind, content=good)),
        *([('stage_generation_artifact_binding', dict(relative_path=path, task_ids=['shared'],
            evidence_ids=['mapping'], content_sha256=good_receipt['content_sha256']))] if kind != 'notebook' else []),
        ('commit_generation_receipts', {}),
    ])
    # The pinned task requires a notebook. A module/SQL file alone is saved,
    # but cannot satisfy notebook_task generation merely by having valid syntax.
    assert_unreviewed_candidate(report, 'generation_complete' if kind == 'notebook' else 'drafts_saved')
    assert report['artifact_hashes'][path] == good_receipt['content_sha256']
    assert not any(issue['kind'] == 'candidate_repair_required' for issue in report['candidate_review_issues'])
    assert report['writer_repair_evidence'][0]['content'] == bad
    assert any(event.get('repair', {}).get('remaining_attempts') == 2 for event in report['events'])
    assert any(event.get('error_code') == 'candidate_repair_required' for event in report['events'])


def test_irreparable_syntax_stops_after_three_failures_preserves_valid_candidate(ctx):
    actions = [('stage_generation_artifact', dict(relative_path='notebooks/load.ipynb', kind='notebook', content=FIX['notebook']()))]
    actions += [('stage_generation_artifact', dict(relative_path='modules/broken.py', kind='module', content='def broken(:\n'))] * 10
    report, route = run_script(ctx, actions)
    assert report['status'] == 'blocked' and report['failure'] == 'candidate_repair_exhausted'
    assert report['validation']['static'] == 'not_accepted' and not report['artifact_hashes']
    assert len([event for event in report['events'] if event.get('repair')]) == 3
    assert len(route._calls) < 12
    assert report['writer_repair_evidence'][0]['content'] == 'def broken(:\n'
    result = saved(ctx, report)
    assert 'notebooks/load.ipynb' in result['staged_artifacts']
    assert report['candidate_availability']['tasks'][0]['code_available']
    assert 'modules/broken.py' not in result['staged_artifacts']


@pytest.mark.parametrize('finish', ['commit', 'empty_legacy', 'prose'])
def test_plan_owned_candidate_immediate_and_final_without_bookkeeping(ctx, finish):
    ending = ([('commit_generation_receipts', {})] if finish == 'commit' else
              [('finalize_generation_artifact_set', {})] if finish == 'empty_legacy' else [])
    report, route = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path='notebooks/load.ipynb', kind='notebook', content=FIX['notebook']())),
        *ending,
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    assert report['tasks'][0]['artifact_paths'] == ['notebooks/load.ipynb']
    assert report['tasks'][0]['status'] == 'drafted' and report['tasks'][0]['blockers'] == []
    assert report['translation_coverage'] is None
    result = saved(ctx, report)
    assert result['artifacts']['notebooks/load.ipynb'] == FIX['_notebook'](
        FIX['notebook'](), ctx[1]['id'] + ':' + str(ctx[1]['version']) + ':notebooks/load.ipynb')
    assert result['staged_artifacts'] == {}  # completed inventory is served as committed artifacts
    messages = [message for turn in route._calls for message in turn if getattr(message, 'type', '') == 'tool']
    immediate = next(json.loads(message.content) for message in messages if message.name == 'stage_generation_artifact')
    assert immediate['candidate_availability']['tasks'][0]['code_available']


def test_invalid_optional_bookkeeping_does_not_block_candidate_commit(ctx):
    report, _ = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path='notebooks/load.ipynb', kind='notebook', content=FIX['notebook']())),
        ('stage_generation_translation', dict(item={'task_id': 'shared', 'status': 'translated'})),
        ('commit_generation_receipts', {}),
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    assert any(issue['kind'] == 'metadata_review_issue' for issue in report['candidate_review_issues'])
    assert report['translation_coverage'] is None


def test_no_budget_candidate_cannot_loop_forever(ctx):
    report, route = run_script(ctx, [('fetch_source_task', dict(task_id='shared'))] * 30)
    assert report['failure'] == 'candidate_repair_exhausted'
    evidence = FIX['retrieved'](ctx[2], ctx[1])
    retrieval_calls = sum(len(range(0, max(1, len(rows)), 12))
                          for rows in (evidence.rows['shared'], evidence.sql['shared']))
    assert report['budget_used']['model_calls'] == retrieval_calls + 4
    assert len(route._calls) == retrieval_calls + 4
    assert report['status'] == 'blocked'


def test_real_progress_is_not_truncated_at_arbitrary_call_ceiling(ctx):
    actions = [('stage_generation_artifact', dict(relative_path=f'modules/code_{number}.py',
        kind='module', content=f'value = {number}\n')) for number in range(15)]
    actions += [('stage_generation_artifact_binding', dict(relative_path=f'modules/code_{number}.py',
        task_ids=['shared'], evidence_ids=['mapping'],
        content_sha256=sha256(f'value = {number}\n'.encode()).hexdigest())) for number in range(15)]
    actions.append(('commit_generation_receipts', {}))
    report, route = run_script(ctx, actions)
    assert len(route._calls) > 12
    assert_unreviewed_candidate(report, 'drafts_saved')
    assert len(report['staged_drafts']) == 15


def test_structural_task_feedback_repair_and_no_infinite_loop(ctx):
    task = FIX['payload'](ctx[1])['tasks'][0]
    task.update(artifact_paths=['notebooks/load.ipynb'], obligations=[])
    malformed = {**task, 'implementation_kind': 'bogus_yaml_task_type'}
    report, _ = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path='notebooks/load.ipynb', kind='notebook', content=FIX['notebook']())),
        ('stage_generation_task', dict(task=malformed)),
        ('stage_generation_task', dict(task=task)),
        ('commit_generation_receipts', {}),
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    repair = next(event['repair'] for event in report['events'] if event.get('repair'))
    assert any(field['field'] == 'implementation_kind' for field in repair['invalid_fields'])
    assert not any(issue['kind'] == 'candidate_repair_required' for issue in report['candidate_review_issues'])


def test_approved_path_keeps_rejecting_missing_obligations(ctx):
    value = FIX['payload'](ctx[1])
    value['tasks'][0]['obligations'] = []
    with pytest.raises(ContractError, match='all eight semantic obligation'):
        _validate(value, ctx[1], FIX['retrieved'](ctx[2], ctx[1]), candidate=False)


def test_approved_generation_unchanged(ctx):
    report, _ = run_script(ctx, [('submit_generation_artifact_set', dict(artifact_set=FIX['payload'](ctx[1])))], candidate=False)
    assert report['status'] == 'generation_complete' and report['failure'] is None
    assert len(report['artifact_hashes']) == 4


@pytest.mark.parametrize('submission', ['full', 'staged'])
def test_fully_validated_authored_candidate_preserves_success(ctx, submission):
    value = FIX['payload'](ctx[1])
    if submission == 'full':
        actions = [('submit_generation_artifact_set', dict(artifact_set=value))]
    else:
        # Staging the pinned notebook automatically supplies its exact plan
        # binding. Repeating that receipt is valid; changing its citations
        # would require the runtime invocation's replacement receipt hash.
        value['artifacts'][0]['evidence_ids'] = ctx[1]['design']['artifact_plan'][0]['evidence_ids']
        staged = _StagedArtifacts(ctx[1])
        actions = []
        for artifact in value['artifacts']:
            file = staged.stage(artifact['relative_path'], artifact['kind'], artifact['content'])
            actions.extend([
                ('stage_generation_artifact', {key: artifact[key] for key in ('relative_path', 'kind', 'content')}),
                ('stage_generation_artifact_binding', dict(relative_path=file['relative_path'],
                    task_ids=artifact['task_ids'], evidence_ids=artifact['evidence_ids'],
                    content_sha256=file['content_sha256'])),
            ])
        actions.extend([('stage_generation_task', dict(task=value['tasks'][0])),
                        ('commit_generation_receipts', {})])
    report, _ = run_script(ctx, actions)
    assert_unreviewed_candidate(report, 'generation_complete')
    assert report['tasks'][0]['status'] == 'drafted'
    expected = FIX['GenerationArtifactSet'].model_validate(value).tasks[0].model_dump()
    assert report['tasks'][0]['obligations'] == expected['obligations']
    assert report['translation_coverage'] is None
    assert report['translation_assessment']['status'] == 'unverified'
    assert len(report['artifact_hashes']) == 4
    assert not report['execution_allowed'] and not report['deployment_allowed']


@pytest.mark.parametrize('defect', ['citation', 'python'])
def test_full_candidate_replacement_repairs_its_own_rejection(ctx, defect):
    good = FIX['payload'](ctx[1])
    bad = deepcopy(good)
    if defect == 'citation':
        bad['artifacts'][0]['evidence_ids'] = ['invented']
    else:
        bad['artifacts'][1]['content'] = 'def broken(:\n'
    report, route = run_script(ctx, [
        ('submit_generation_artifact_set', dict(artifact_set=bad)),
        ('submit_generation_artifact_set', dict(artifact_set=good)),
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    assert report['budget_used']['submission_attempts'] == 2
    assert not any(issue['kind'] == 'candidate_repair_required' for issue in report['candidate_review_issues'])
    assert any(message.type == 'tool' and message.status == 'error'
               for turn in route._calls for message in turn)


def test_full_candidate_cannot_clear_an_unrelated_source_repair(ctx):
    report, _ = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path='modules/broken.py', kind='module', content='def broken(:\n')),
        ('submit_generation_artifact_set', dict(artifact_set=FIX['payload'](ctx[1]))),
    ])
    assert report['status'] == 'blocked' and not report['artifact_hashes']
    assert any(issue['kind'] == 'candidate_repair_required' and issue['operation'] == 'stage_generation_artifact'
               for issue in report['candidate_review_issues'])


def test_unsafe_rejected_source_not_retained():
    repair = _CandidateRepairs()
    args = dict(relative_path='modules/code.py', kind='module', content='password = "dont-save-this"\n')
    repair.reject('stage_generation_artifact', args, 'Credential-bearing artifacts are prohibited', [])
    assert repair.rejected == {}


@pytest.mark.parametrize('source,expected', [
    pytest.param('import time\ntime.sleep(0)\n', {}, id='timer-no-tables'),
    pytest.param('dbutils.widgets.text("log_message", "review complete")\n'
        'import logging\nlogging.getLogger("candidate").info(dbutils.widgets.get("log_message"))\n',
        {'log_message': 'review complete'}, id='logging-no-tables'),
    pytest.param('dbutils.widgets.text("source_table", "main.raw.events")\n'
        'source_table = dbutils.widgets.get("source_table")\n'
        'spark.sql(f"SELECT COUNT(*) AS event_count FROM {source_table}").show()\n',
        {'source_table': 'main.raw.events'}, id='aggregate-source-only'),
    pytest.param('dbutils.widgets.text("target_table", "main.review.events")\n'
        'target_table = dbutils.widgets.get("target_table")\n'
        'spark.sql(f"INSERT INTO {target_table} (id, amount) VALUES (1, 7)")\n',
        {'target_table': 'main.review.events'}, id='insert-target-only'),
    pytest.param('dbutils.widgets.text("target_table", "main.review.events")\n'
        'target_table = dbutils.widgets.get("target_table")\n'
        'spark.sql(f"MERGE INTO {target_table} AS t USING (SELECT 1 AS id, 7 AS amount) AS s "\n'
        '          "ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = s.amount "\n'
        '          "WHEN NOT MATCHED THEN INSERT (id, amount) VALUES (s.id, s.amount)")\n',
        {'target_table': 'main.review.events'}, id='merge-target-only'),
    pytest.param('from pyspark.sql import functions as F\n'
        'rows = spark.createDataFrame([(1, None), (2, 7)], "id int, amount int")\n'
        'rows.select("id", F.coalesce("amount", F.lit(0)).alias("amount")).show()\n',
        {}, id='transform-no-tables-or-parameters'),
])
def test_actual_behavior_candidate_saves_and_previews_only_declared_widgets(ctx, source, expected, monkeypatch):
    from workbench import selected_job_deploy as deploy
    from workbench import knowledge_store
    from workbench.spark_codegen import _digest

    service, record, _, _ = ctx
    # Supply the staging format, including stable IDs, so exact bytes after
    # staging and unchanged business code are the contract being exercised.
    content = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'markdown', 'metadata': {'language': 'markdown', 'id': 'intro'},
         'source': '# Actual behavior candidate'},
        {'cell_type': 'code', 'metadata': {'language': 'python', 'id': 'behavior'},
         'source': source, 'execution_count': None, 'outputs': []}]})
    path = 'notebooks/load.ipynb'
    staging = _StagedArtifacts(record)
    staging.stage(path, 'notebook', content)
    content = staging.files[path]['content']
    assert json.loads(content)['cells'][1]['source'] == source
    report, _ = run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=path, kind='notebook', content=content)),
        ('commit_generation_receipts', {}),
    ])
    assert_unreviewed_candidate(report, 'generation_complete')
    assert report['candidate_availability']['tasks'][0]['code_available']
    assert not report['execution_allowed'] and not report['deployment_allowed']
    result = saved(ctx, report)
    assert result['artifacts'][path].encode('utf-8') == content.encode('utf-8')
    assert json.loads(result['artifacts'][path])['cells'][1]['source'] == source
    assert report['artifact_hashes'][path] == sha256(content.encode('utf-8')).hexdigest()

    job_id, project_id = 'f' * 32, 'd' * 32
    manifest_raw = (service.root / report['relative_directory'] / 'manifest.json').read_bytes()
    job = dict(id=job_id, kind='spark_draft', status='completed',
        package_hash=record['package_hash'], workflow_id=record['workflow_id'], scenario=record['scenario'],
        design_id=record['id'], design_version=record['version'], input_version=record['input_version'],
        output={'run_id': report['run_id']}, manifest_sha256=sha256(manifest_raw).hexdigest())
    service.record = lambda requested: deepcopy(job) if requested == job_id else {}
    # The writer fixture has no project directory registration. Supply that
    # identity only, retaining the real package gate, reader, Design and preview.
    def project_for_root(root):
        assert root == service.root
        return project_id
    monkeypatch.setattr(knowledge_store, 'project_for_root', project_for_root)
    import workbench.databricks_models as models
    def forbidden(*args, **kwargs):
        pytest.fail('Candidate save/preview must not authenticate or invoke a cloud model')
    monkeypatch.setattr(models, 'profile_identity', forbidden)
    selection = deploy.SelectedJobSelection(project_id=project_id, package_hash=record['package_hash'],
        workflow_id=record['workflow_id'], scenario=record['scenario'], design_id=record['id'],
        design_version=record['version'], design_sha256=_digest(record['design']),
        input_version=record['input_version'], manifest_sha256=job['manifest_sha256'])
    before = {file: file.read_bytes() for file in service.root.rglob('*') if file.is_file()}
    plan = deploy.preview(service, job_id, selection)
    assert plan['artifacts'] == {path: content}
    assert plan['artifact_mapping'] == {'shared': path}
    native = next(iter(plan['bundle']['resources']['jobs'].values()))
    assert native.get('parameters', []) == [
        {'name': name, 'default': default} for name, default in sorted(expected.items())]
    if not expected:
        assert 'parameters' not in native
    assert native['tasks'] == [{'task_key': 'shared', 'notebook_task': {
        'notebook_path': './' + path, 'source': 'WORKSPACE'}}]
    assert before == {file: file.read_bytes() for file in service.root.rglob('*') if file.is_file()}
