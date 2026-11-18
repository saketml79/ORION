"""Authored static-check regressions using existing compiled writer fakes.

No generated artifact execution, real provider, network or deployment is needed.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from workbench.spark_codegen import _digest, _StagedArtifacts
from workbench.tests import test_candidate_writer_simplification as writer_fakes


ctx = writer_fakes.ctx
FIX = writer_fakes.FIX
PATH = 'notebooks/load.ipynb'


def notebook(source):
    value = json.loads(FIX['notebook']())
    value['cells'][1]['source'] = source
    return json.dumps(value)


def messages(route, name='check_generation_code'):
    # A ToolMessage is present in subsequent turns, deduplicate its actual ID.
    found = {message.tool_call_id: message for turn in route._calls for message in turn
             if message.type == 'tool' and message.name == name}
    return [json.loads(message.content) for message in found.values()]


def snapshot(metadata):
    return deepcopy(dict(files=metadata.staged.files, chunks=metadata.staged.chunks,
        tasks=metadata.tasks, bindings=metadata.bindings, translations=metadata.translations,
        obligations=metadata.obligations, basis=metadata.basis,
        submitted_metadata=metadata.submitted_metadata, recovery=metadata.recovery,
        reads=metadata.evidence.reads, seen=metadata.evidence.seen,
        fetched=dict(metadata.evidence.fetched), sql_fetched=dict(metadata.evidence.sql_fetched)))


def assert_static_only(receipt):
    for flag in ('ai_reviewed', 'runtime_verified', 'code_executed', 'tests_executed',
                 'committed', 'artifact_approved', 'execution_allowed', 'deployment_allowed'):
        assert receipt[flag] is False
    assert receipt['semantic_equivalence'] == 'unverified'
    assert receipt['dynamic_sql'] == 'review_unverified'
    assert receipt['receipt_sha256'] == _digest({key: value for key, value in receipt.items()
                                               if key != 'receipt_sha256'})


def test_compiled_check_tool_receipt_telemetry_and_static_only_pass(ctx, tmp_path):
    marker = tmp_path / 'generated-code-must-not-run'
    content = notebook('dbutils.widgets.text("source_table", "")\n'
        'source_table = dbutils.widgets.get("source_table")\n'
        'spark.sql("SELECT 1")\n'
        f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
        'raise AssertionError("Generated notebook was executed")\n')
    staging = _StagedArtifacts(ctx[1])
    expected = staging.stage(PATH, 'notebook', content)
    actions = [('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=content)),
               ('check_generation_code', {}), ('commit_generation_receipts', {})]
    report, route = writer_fakes.run_script(ctx, actions)

    assert 'check_generation_code' in route._bound_names
    checks = messages(route)
    assert len(checks) == 1
    receipt = checks[0]
    assert_static_only(receipt)
    assert receipt['status'] == 'review_unverified'
    assert receipt['static_validation'] == 'passed' and not receipt['needs_correction']
    assert receipt['diagnostics'] == []
    artifact = receipt['artifacts'][0]
    assert artifact['relative_path'] == PATH and artifact['task_ids'] == ['shared']
    assert artifact['content_sha256'] == artifact['staged_sha256'] == expected['content_sha256']
    assert artifact['binding_replace_sha256']
    assert receipt['parameter_contract']['parameters'] == [{'name': 'source_table', 'default': ''}]
    event = next(event for event in report['events'] if event.get('name') == 'check_generation_code')
    assert event['activity'] == receipt['activity'] == 'enhance code if needed, syntax and sanity checks'
    assert event['status'] == 'completed' and event['check_status'] == 'review_unverified'
    assert event['receipt_sha256'] == receipt['receipt_sha256']
    assert report['failure'] is None and report['artifact_hashes'][PATH] == expected['content_sha256']
    assert not marker.exists()
    assert report['budget_used']['validation_model_calls'] == 0
    assert report['budget_used']['model_calls'] == len(route._calls)
    assert len(route._calls) == sum(event['kind'] == 'tool' for event in report['events'])
    assert 'Before final commit, call check_generation_code' in str(route._calls[0])
    assert 'never repeat an unchanged failing check' in str(route._calls[0])


@pytest.mark.parametrize('source,reason', [
    ('dbutils.widgets.get("missing")\n', 'read without'),
    ('for name in ["source_table"]:\n    dbutils.widgets.text(name, "")\n', 'nested'),
    ('dbutils.widgets.text("source_table", str(1))\n', 'explicit string literals'),
    ('dbutils.widgets.text("source_table", "a")\n'
     'dbutils.widgets.text("source_table", "b")\n', 'conflict'),
])
def test_compiled_invalid_widgets_return_path_hash_and_correction(ctx, source, reason):
    report, route = writer_fakes.run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=notebook(source))),
        ('check_generation_code', {}),
        ('inspect_generation_receipts', {}),
        ('commit_generation_receipts', {}),
    ])
    receipt = messages(route)[0]
    assert_static_only(receipt)
    assert receipt['needs_correction'] and receipt['status'] == 'needs_correction'
    assert receipt['static_validation'] == 'failed'
    diagnostic = next(row for row in receipt['diagnostics'] if reason in row['reason'])
    assert diagnostic['check'] == 'native_job_parameter_contract'
    assert diagnostic['relative_path'] == PATH and diagnostic['task_ids'] == ['shared']
    assert diagnostic['content_sha256'] == receipt['artifacts'][0]['content_sha256']
    assert report['budget_used']['validation_model_calls'] == 0


def test_compiled_widget_code_correction_rebinds_then_rechecks_same_thread(ctx, monkeypatch):
    bad = notebook('dbutils.widgets.get("source_table")\n')
    good = notebook('dbutils.widgets.text("source_table", "")\n'
                    'dbutils.widgets.get("source_table")\n')
    staging = _StagedArtifacts(ctx[1])
    old = staging.stage(PATH, 'notebook', bad)['content_sha256']
    new = staging.stage(PATH, 'notebook', good, old)['content_sha256']
    original = FIX['ScriptedChat']._generate

    def consume_receipt(self, messages, **kwargs):
        result = original(self, messages, **kwargs)
        for call in result.generations[0].message.tool_calls:
            if call['args'].get('replace_sha256') == 'exact-check-binding':
                receipt = next(json.loads(message.content) for message in reversed(messages)
                    if message.type == 'tool' and message.name == 'check_generation_code')
                call['args']['replace_sha256'] = receipt['artifacts'][0]['binding_replace_sha256']
        return result

    monkeypatch.setattr(FIX['ScriptedChat'], '_generate', consume_receipt)
    report, route = writer_fakes.run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=bad)),
        ('check_generation_code', {}),
        ('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=good, replace_sha256=old)),
        ('stage_generation_artifact_binding', dict(relative_path=PATH, task_ids=['shared'],
            evidence_ids=['mapping'], content_sha256=new, replace_sha256='exact-check-binding')),
        ('check_generation_code', {}), ('commit_generation_receipts', {}),
    ])
    checks = messages(route)
    assert [row['status'] for row in checks] == ['needs_correction', 'review_unverified']
    assert [row['artifacts'][0]['content_sha256'] for row in checks] == [old, new]
    assert len({row['invocation_id'] for row in checks}) == 1
    assert report['failure'] is None and report['artifact_hashes'][PATH] == new
    assert report['budget_used']['validation_model_calls'] == 0
    assert len(route._calls) == sum(event['kind'] == 'tool' for event in report['events'])


@pytest.mark.parametrize('content,reason', [
    ('{invalid notebook', 'valid constrained Jupyter JSON'),
    (notebook('def invalid(:\n'), 'Python source must parse'),
    (notebook('spark.sql("SELECT (")\n'), 'parse strictly'),
    (notebook('spark.sql("SELECT $$UNRESOLVED")\n'), 'Unresolved Informatica'),
    (notebook('value = "$$UNRESOLVED"\n'), 'Unresolved Informatica'),
])
def test_check_revalidates_exact_bytes_not_prior_staging_receipt(ctx, content, reason):
    metadata, staged, _ = writer_fakes.receipts(ctx)
    # Simulate changed in-memory candidate bytes, with matching hash claims, so
    # syntax checks cannot accidentally rely on the earlier valid staging pass.
    staged.files[PATH]['content'] = content
    digest = sha256(content.encode('utf-8')).hexdigest()
    staged.files[PATH]['content_sha256'] = digest
    metadata.bindings[PATH]['value']['content_sha256'] = digest
    before = snapshot(metadata)
    receipt = metadata.check_code()
    assert_static_only(receipt)
    assert receipt['needs_correction'] and receipt['static_validation'] == 'failed'
    assert any(reason in row['reason'] and row['relative_path'] == PATH
               and row['content_sha256'] == digest for row in receipt['diagnostics'])
    assert snapshot(metadata) == before


@pytest.mark.parametrize('bad_source', [
    'spark.sql("SELECT (")\n',
    'spark.sql("SELECT $$UNRESOLVED")\n',
    'value = "$$UNRESOLVED"\n',
])
def test_compiled_sql_and_tokens_are_rejected_then_corrected_without_execution(ctx, bad_source):
    report, route = writer_fakes.run_script(ctx, [
        ('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=notebook(bad_source))),
        ('check_generation_code', {}),
        ('stage_generation_artifact', dict(relative_path=PATH, kind='notebook', content=notebook('spark.sql("SELECT 1")\n'))),
        ('check_generation_code', {}), ('commit_generation_receipts', {}),
    ])
    checks = messages(route)
    assert [row['status'] for row in checks] == ['needs_correction', 'review_unverified']
    assert checks[0]['artifacts'] == []  # invalid source never became staged authority
    assert checks[1]['artifacts'][0]['content_sha256'] == report['artifact_hashes'][PATH]
    assert all(not row['code_executed'] for row in checks)
    assert any(event.get('error_code') == 'artifact_contract_rejected' and event.get('repair')
               for event in report['events'])
    assert report['failure'] is None and report['budget_used']['validation_model_calls'] == 0


@pytest.mark.parametrize('kind,path,bad', [
    ('module', 'modules/clean.py', 'def invalid(:\n'),
    ('sql', 'sql/query.sql', 'SELECT ('),
    ('sql', 'sql/query.sql', 'SELECT $$UNRESOLVED'),
    ('test', 'tests/test_clean.py', 'def test_invalid():\n    assert True\n'),
])
def test_check_revalidates_module_sql_and_assertion_artifacts(ctx, kind, path, bad):
    metadata, staged, _ = writer_fakes.receipts(ctx)
    artifact = next(row for row in FIX['payload'](ctx[1])['artifacts'] if row['kind'] == kind)
    item = staged.stage(path, kind, artifact['content'])
    metadata.binding(path, ['shared'], ['expression'], item['content_sha256'])
    digest = sha256(bad.encode('utf-8')).hexdigest()
    staged.files[path].update(content=bad, content_sha256=digest)
    metadata.bindings[path]['value']['content_sha256'] = digest
    receipt = metadata.check_code()
    assert receipt['needs_correction'] and receipt['static_validation'] == 'failed'
    assert any(row['check'] == 'artifact_syntax' and row['relative_path'] == path
               and row['content_sha256'] == digest for row in receipt['diagnostics'])
    assert_static_only(receipt)


@pytest.mark.parametrize('change', ['unbound', 'stale_binding', 'hash', 'owner', 'citation', 'chunk'])
def test_check_preserves_exact_ownership_and_staging_guards(ctx, change):
    metadata, staged, _ = writer_fakes.receipts(ctx)
    if change == 'unbound':
        staged.stage('modules/unbound.py', 'module', 'value = 1\n')
    elif change == 'stale_binding':
        metadata.bindings[PATH]['value']['content_sha256'] = 'f' * 64
    elif change == 'hash':
        staged.files[PATH]['content'] += ' '
    elif change == 'owner':
        metadata.bindings[PATH]['value']['task_ids'] = ['outside']
    elif change == 'citation':
        metadata.bindings[PATH]['value']['evidence_ids'] = ['outside']
    else:
        staged.chunk('modules/pending.py', 'module', 'value = ', final=False)
    before = snapshot(metadata)
    receipt = metadata.check_code()
    assert_static_only(receipt)
    assert receipt['needs_correction'] and receipt['diagnostics']
    assert snapshot(metadata) == before


def test_static_receipt_all_artifact_kinds_is_repeatable_and_read_only(ctx):
    metadata, staged, _ = writer_fakes.receipts(ctx)
    for artifact in FIX['payload'](ctx[1])['artifacts'][1:]:
        item = staged.stage(artifact['relative_path'], artifact['kind'], artifact['content'])
        metadata.binding(item['relative_path'], ['shared'], ['expression'], item['content_sha256'])
    metadata.recovery['review_issues'] = [{'kind': 'keep_existing_review_feedback'}]
    before = snapshot(metadata)
    files_before = {path: path.read_bytes() for path in ctx[0].root.rglob('*') if path.is_file()}
    first, second = metadata.check_code(), metadata.check_code()
    assert first == second and not first['needs_correction']
    assert {row['kind'] for row in first['artifacts']} == {'notebook', 'module', 'sql', 'test'}
    assert snapshot(metadata) == before
    assert files_before == {path: path.read_bytes() for path in ctx[0].root.rglob('*') if path.is_file()}
    assert_static_only(first)