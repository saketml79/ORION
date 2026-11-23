"""Direct offline metadata protocol checks. No suite, service restart or live provider.

Run with python -B -m tests.check_spark_codegen_metadata. Socket access is forbidden.
All compiled graph/checkpoint/artifact outputs live in cleaned temporary directories.
"""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from tests.check_spark_codegen_staging import (
    FIXTURES, checkpoint_receipts, context, rejects, main as legacy_checks,
)
from workbench.native_job_draft import build, graph_metadata
from workbench.redesign import Review, store_for
from workbench.spark_codegen import (
    OBLIGATIONS, TaskDraft, _Evidence, _GenerationReceipts, _StagedArtifacts, _digest,
    draft_agent_design, draft_approved_design,
)


def four_task_context(root):
    service, previous, model, inputs = context(root)
    design = deepcopy(previous['design'])
    ids = ['counter', 'condition', 'child', 'notify']
    design['tasks'] = [{**deepcopy(design['tasks'][0]), 'id': task_id,
        'label': task_id, 'implementation_kind': 'condition_task' if task_id == 'condition' else 'notebook_task'}
        for task_id in ids]
    design['tasks'][1]['condition_task'] = {
        'left': {'kind': 'task_value', 'task_id': 'counter', 'key': 'row_count'},
        'op': 'LESS_THAN', 'right': {'kind': 'literal', 'value': '35'}}
    design['edges'] = [dict(source='counter', target='condition', evidence_ids=['a']),
        dict(source='condition', target='child', outcome='true', evidence_ids=['a']),
        dict(source='condition', target='notify', outcome='false', evidence_ids=['b'])]
    for row in design['coverage']:
        if row['disposition'] == 'mapped':
            row['proposed_task_ids'] = ids
    design['artifact_plan'] = [{**deepcopy(design['artifact_plan'][0]),
        'relative_path': f'notebooks/{task_id}.ipynb', 'proposed_task_ids': [task_id]} for task_id in ids]
    store = store_for(service)
    record = store.append(previous['package_hash'], 'wf', 'original',
        {'design': design, 'role': 'architecture', 'retrieved_evidence_ids': ['a', 'b', 'disabled', 'mapping']},
        {**previous['source'], 'record_id': 'offline-four-role-run'}, previous['version'])
    store.review(record['id'], record['package_hash'], Review(action='approve_for_drafting',
        comment='Offline metadata protocol only, no execution', expected_version=record['version'],
        expected_input_version=inputs['input_version'], consent=True), inputs['input_version'], {'actor': 'offline'})
    return service, store.get(record['id'], record['package_hash']), model, inputs


def notebook(code):
    return json.dumps(dict(nbformat=4, nbformat_minor=5, metadata={}, cells=[
        dict(cell_type='code', metadata={'language': 'python'}, source=code, execution_count=None, outputs=[])]))


def metadata_script(record, model, *, missing=False, recover=False, schema_repair=False):
    responses, payloads, bindings, translations = [], [], [], []
    call = FIXTURES['call']
    def emit(name, args):
        responses.append(call(name, args, len(responses)))
    evidence = _Evidence(model, record)
    staged = _StagedArtifacts(record)
    contents = dict(counter='row_count = 34\ndbutils.jobs.taskValues.set(key="row_count", value=row_count)\n',
        condition='def below_threshold(value):\n    return value < 35\n',
        child='message = "OFFLINE FIXTURE, NO CHILD SUBMISSION"\n',
        notify='message = "OFFLINE FIXTURE, NO EMAIL DELIVERY"\n')
    for task in record['design']['tasks']:
        task_id = task['id']
        for sql in (False, True):
            rows = evidence.sql[task_id] if sql else evidence.rows[task_id]
            for offset in range(0, max(1, len(rows)), 12):
                emit('fetch_sql_phases' if sql else 'fetch_source_task', dict(task_id=task_id, offset=offset))
                evidence.page(task_id, offset, sql=sql)
        owned_rows = {row['key']: row for row in evidence.rows[task_id] + evidence.sql[task_id]}
        ids = sorted({identifier for row in owned_rows.values() for identifier in row['evidence_ids']})
        path = f'notebooks/{task_id}.ipynb'
        body = notebook(contents[task_id])
        receipt = staged.stage(path, 'notebook', body)
        emit('stage_generation_artifact', dict(relative_path=path, kind='notebook', content=body))
        bindings.append(dict(relative_path=path, task_ids=[task_id], evidence_ids=ids,
                             content_sha256=receipt['content_sha256']))
        obligations = [dict(category=category, status='not_applicable',
            explanation='Constructed offline protocol fixture, not business parity evidence', evidence_ids=ids)
            for category in sorted(OBLIGATIONS)]
        obligations[0].update(status='test_required', test_path='tests/test_threshold.py', test_function='test_threshold')
        payloads.append(dict(task_id=task_id, implementation_kind=task['implementation_kind'],
            status='drafted', artifact_paths=[path, 'tests/test_threshold.py'], evidence_ids=ids,
            blockers=[], obligations=obligations))
        # One record per item proves incremental translation coverage without a giant list.
        for key, row in owned_rows.items():
            translations.append(dict(task_id=task_id, kind='control', source_record_keys=[key],
                evidence_ids=row['evidence_ids'], artifact_paths=[path], status='translated',
                explanation='Constructed canonical inventory coverage, no semantic equivalence claim', blockers=[]))
    test_body = 'def test_threshold():\n    values = [34, 35, 36]\n    assert [v < 35 for v in values] == [True, False, False]\n'
    receipt = staged.stage('tests/test_threshold.py', 'test', test_body)
    emit('stage_generation_artifact', dict(relative_path='tests/test_threshold.py', kind='test', content=test_body))
    bindings.append(dict(relative_path='tests/test_threshold.py', task_ids=[task['task_id'] for task in payloads],
        evidence_ids=sorted(evidence.seen), content_sha256=receipt['content_sha256']))
    if schema_repair:
        emit('stage_generation_task', {'task': {'task_id': 'counter'}})
    if missing:
        emit('commit_generation_receipts', {})
        if not recover:
            emit('inspect_generation_receipts', {})
            emit('commit_generation_receipts', {})
            responses.append(AIMessage(content='Must not continue after identical invalid state'))
            return responses, staged, payloads, bindings, translations
    for task in payloads:
        # Exercise individual obligations for every role without a giant nested call.
        emit('stage_generation_task', {'task': {**task, 'obligations': []}})
        for item in task['obligations']:
            emit('stage_generation_obligation', dict(task_id=task['task_id'], item=item))
    for binding in bindings:
        emit('stage_generation_artifact_binding', binding)
    for item in translations:
        emit('stage_generation_translation', {'item': item})
    emit('stage_generation_basis', dict(input_data_basis='synthetic', behavior_basis='substituted'))
    emit('inspect_generation_receipts', {})
    emit('commit_generation_receipts', {})
    responses.append(AIMessage(content='Must not be called after valid commit'))
    return responses, staged, payloads, bindings, translations


def check_compiled_metadata(root, *, candidate=False, missing=False, recover=False, schema_repair=False):
    service, record, model, _ = four_task_context(root)
    responses, staged, tasks, bindings, translations = metadata_script(record, model,
        missing=missing, recover=recover, schema_repair=schema_repair)
    route = FIXTURES['ScriptedChat'](responses=responses)
    service.runtime.role_models = {'artifact': route}
    schemas = {}
    original_bind = type(route).bind_tools
    def capture(self, tools, **kwargs):
        for tool in tools:
            if getattr(tool, 'name', '').startswith(('stage_generation_', 'commit_generation_', 'inspect_generation_')):
                schemas[tool.name] = convert_to_openai_tool(tool)['function']['parameters']
        return original_bind(self, tools, **kwargs)
    with patch.object(type(route), 'bind_tools', capture):
        if candidate:
            run_id = record['source']['record_id']
            service.record = lambda identifier, **kwargs: dict(id=run_id, consent=True, generate_code=True,
                status='running', **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
            report = draft_agent_design(service, record, agent_run_id=run_id, consent=True)
        else:
            report = draft_approved_design(service, record, consent=True)
    directory = service.root / report['relative_directory']
    receipts = list(checkpoint_receipts(directory))
    for name, schema in schemas.items():
        assert '$defs' not in schema and len(json.dumps(schema)) < 1500, (name, schema)
    assert not schemas['commit_generation_receipts'].get('properties')
    assert all(len(json.dumps(row).encode()) < 2000 for row in receipts if 'next_offset' in row and 'state_sha256' in row)
    if missing:
        errors = [row for row in receipts if row.get('error') == 'generation_metadata_missing']
        assert len(errors) == 1 and errors[0]['accepted'] is False
        assert any('stage_generation_task' in field for field in errors[0]['missing'])
    if missing and not recover:
        assert report['status'] == 'blocked' and report['artifact_hashes'] == {}
        assert report['failure'] == 'writer_protocol_no_progress'
        assert report['translation_coverage'] is None
        assert not (directory / 'notebooks').exists()
        assert len(report['staged_drafts']) == 5
        assert any(event.get('timeout') is False for event in report['events'] if event.get('error_code') == 'writer_protocol_no_progress')
    else:
        assert report['status'] == 'generation_complete', report
        assert report['failure'] is None
        assert report['tasks'] == [TaskDraft.model_validate(task).model_dump() for task in tasks]
        assert report['translation_coverage']['items'] == translations
        assert report['translation_assessment']['status'] == 'static_inventory_accounted'
        assert not any(report[key] for key in ('execution_allowed', 'deployment_allowed', 'artifact_approved'))
        bodies = {}
        for path, digest in report['artifact_hashes'].items():
            raw = (directory / path).read_bytes()
            assert sha256(raw).hexdigest() == digest == staged.files[path.casefold()]['content_sha256']
            assert raw == staged.files[path.casefold()]['content'].encode('utf-8')
            bodies[path] = raw.decode('utf-8')
        document = build(record['design'], report, bodies, '/Workspace/Users/offline')
        graph = graph_metadata(document, report, bodies, '/Workspace/Users/offline')[0]
        native = {item['task_key']: item for item in graph['tasks']}
        assert native['condition']['condition_task'] == dict(
            left='{{tasks.counter.values.row_count}}', op='LESS_THAN', right='35')
        assert native['child']['depends_on'] == [dict(task_key='condition', outcome='true')]
        assert native['notify']['depends_on'] == [dict(task_key='condition', outcome='false')]
        assert 'notebook_task' not in native['condition']
        assert len([path for path in bodies if path.endswith('.ipynb')]) == 4
        hashes = report['generation_metadata_receipts']
        assert hashes['invocation_id'] == report['run_id']
        assert len(hashes['tasks']) == 4 and len(hashes['bindings']) == 5
        assert len(hashes['obligations']) == 32 and len(hashes['translations']) == len(translations)
        assert len(route._calls) == len(responses) - 1
        assert 'commit_generation_receipts()' in str(route._calls[0])
    if schema_repair:
        errors = [row for row in receipts if row.get('invalid_fields')]
        assert any(field['field'] == 'implementation_kind' and field['code'] == 'missing'
                   for row in errors for field in row['invalid_fields'])
        assert 'input' not in json.dumps(errors)
    for audit in report['staged_drafts']:
        assert sha256((directory / audit['audit_relative_path']).read_bytes()).hexdigest() == audit['content_sha256']
        assert audit['status'] == 'draft_not_complete' and audit['committed'] is False
    assert not any(call['name'] in {'submit_generation_artifact_set', 'finalize_generation_artifact_set'}
                   for message in responses for call in getattr(message, 'tool_calls', []))
    print(f'PASS compiled metadata candidate={candidate} missing={missing} recover={recover} schema_repair={schema_repair}: '
          f'{len(route._calls)} model calls, 4 task roles, 5 exact files, {len(translations)} individual translation items, '
          f'status={report["status"]}, no monolithic manifest or fake completion')


def check_strict_rows(root):
    _, record, model, _ = four_task_context(root)
    _, staged, tasks, bindings, translations = metadata_script(record, model)
    evidence = _Evidence(model, record)
    for task_id in evidence.rows:
        for sql in (False, True):
            rows = evidence.sql[task_id] if sql else evidence.rows[task_id]
            for offset in range(0, max(1, len(rows)), 12):
                evidence.page(task_id, offset, sql=sql)
    store = _GenerationReceipts(record, staged, evidence, 'invocation-a')
    task = tasks[0]
    first = store.task(task)
    assert store.task(task) == first
    rejects(lambda: store.task({**task, 'unexpected': True}))
    rejects(lambda: store.task({**task, 'task_id': 'outside'}))
    rejects(lambda: store.task({**task, 'evidence_ids': ['outside']}))
    rejects(lambda: store.task({**task, 'status': 'blocked', 'blockers': ['Explicit missing behavior']}))
    revised = store.task({**task, 'status': 'blocked', 'blockers': ['Explicit missing behavior']}, first['receipt_sha256'])
    assert revised['receipt_sha256'] != first['receipt_sha256']
    other = _GenerationReceipts(record, staged, evidence, 'invocation-b')
    assert other.task(task)['receipt_sha256'] != first['receipt_sha256']
    rejects(lambda: other.task({**task, 'status': 'blocked', 'blockers': ['Missing']}, first['receipt_sha256']))
    empty = _GenerationReceipts(record, _StagedArtifacts(record), evidence, 'invocation-empty')
    rejects(lambda: empty.binding(**bindings[0]))
    binding = store.binding(**bindings[0])
    rejects(lambda: store.binding(**{**bindings[0], 'content_sha256': '0' * 64}))
    staged.stage('notebooks/counter.ipynb', 'notebook', notebook('row_count = 36\n'), bindings[0]['content_sha256'])
    assert any('stale file hash' in field for field in store.missing())
    new_binding = {**bindings[0], 'content_sha256': staged.files['notebooks/counter.ipynb']['content_sha256']}
    rejects(lambda: store.binding(**new_binding))
    store.binding(**new_binding, replace_sha256=binding['receipt_sha256'])
    item = translations[0]
    receipt = store.translation(item)
    rejects(lambda: store.translation({**item, 'source_record_keys': ['outside']}))
    rejects(lambda: store.translation({**item, 'evidence_ids': ['outside']}))
    rejects(lambda: store.translation({**item, 'explanation': 'Changed explanation'}))
    store.translation({**item, 'explanation': 'Changed explanation'}, receipt['receipt_sha256'])
    assert len(json.dumps(store.inventory()).encode()) < 2000
    print('PASS strict rows: task/evidence ownership, exact hashes, cross-invocation isolation, explicit revisions, stale binding rejection, bounded inventory')


def check_final_metadata_gate(root):
    service, record, model, _ = four_task_context(root)
    responses, _, _, _, _ = metadata_script(record, model)
    for message in responses:
        for call in getattr(message, 'tool_calls', []):
            if (call['name'] == 'stage_generation_translation'
                    and call['args']['item']['task_id'] == 'counter'):
                # Valid strict shape and citations, but violates reciprocal artifact ownership.
                call['args']['item']['artifact_paths'] = ['notebooks/notify.ipynb']
    responses[-1:] = [FIXTURES['call']('commit_generation_receipts', {}, 'repeat-invalid-commit')]
    route = FIXTURES['ScriptedChat'](responses=responses)
    service.runtime.role_models = {'artifact': route}
    report = draft_approved_design(service, record, consent=True)
    directory = service.root / report['relative_directory']
    assert report['failure'] == 'writer_protocol_no_progress'
    assert report['status'] == 'blocked' and report['artifact_hashes'] == {}
    assert not (directory / 'notebooks').exists()
    assert report['translation_coverage'] is None
    assert len(report['staged_drafts']) == 5
    assert not report['execution_allowed'] and not report['deployment_allowed']
    assert any(row.get('accepted') is False for row in checkpoint_receipts(directory))
    print('PASS final metadata gate: complete row inventory with forged reciprocal artifact ownership refused, no committed notebooks or fake completion')


def check_candidate_translation_coverage_gate(root):
    service, record, model, _ = four_task_context(root)
    responses, _, _, _, _ = metadata_script(record, model)
    removed = False
    kept = []
    for message in responses:
        calls = getattr(message, 'tool_calls', [])
        if (not removed and calls and calls[0]['name'] == 'stage_generation_translation'
                and calls[0]['args']['item']['task_id'] == 'counter'):
            removed = True
            continue
        kept.append(message)
    assert removed
    kept[-1:] = [FIXTURES['call']('commit_generation_receipts', {}, 'repeat-incomplete-commit')]
    route = FIXTURES['ScriptedChat'](responses=kept)
    service.runtime.role_models = {'artifact': route}
    report = draft_approved_design(service, record, consent=True)
    assert report['status'] == 'blocked' and report['failure'] == 'writer_protocol_no_progress'
    assert report['artifact_hashes'] == {} and len(report['staged_drafts']) == 5
    assert not report['execution_allowed'] and not report['deployment_allowed']
    print('PASS candidate translation gate: one missing canonical receipt blocks commit; identical retry stops without extra inference')


def check_real_counter_snapshot():
    """Validate actual canonical citations, not historical recovery or business completion."""
    root = Path(__file__).resolve().parents[1] / 'output/workbench/projects/1dd254871d0b4fb9970ff9d215f12dc1/package-contexts/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba'
    public_path = root / 'state/agent-runs/740e131bb2bf426fb18bd6194b716439.json'
    manifest_path = root / 'generated-drafts/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba/3a654380c729d744a475e0ced3a49db3031161bfdac1d24f4498d34f7b4c6fc3/5/0c6041c832794bf99dcf16597716d0b3/manifest.json'
    if not public_path.exists() or not manifest_path.exists():
        print('UNAVAILABLE optional exact historical counter snapshot; constructed checks still run')
        return
    checkpoint_path = manifest_path.with_name('checkpoint_spark_codegen.sqlite')
    protected = [public_path, manifest_path, checkpoint_path]
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in protected}
    manifest = json.loads(manifest_path.read_bytes())
    public = json.loads(public_path.read_bytes())
    design = public['output']['redesigns'][0]['design']
    assert _digest(design) == manifest['design_sha256']
    record = dict(id=manifest['design_id'], version=manifest['design_version'], design=design,
                  input_version=manifest['input_version'], package_hash=manifest['package_hash'])
    rows, sql, final_calls = {}, {}, {}
    serializer = JsonPlusSerializer()
    with closing(sqlite3.connect(checkpoint_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        for kind, blob in db.execute("SELECT type,value FROM writes WHERE channel='messages'"):
            value = serializer.loads_typed((kind, blob))
            for message in value if isinstance(value, list) else [value]:
                if getattr(message, 'type', None) == 'ai':
                    for call in getattr(message, 'tool_calls', []):
                        if call['name'] == 'finalize_generation_artifact_set':
                            final_calls[call['id']] = (call['args'], message.response_metadata.get('finish_reason'))
                if (getattr(message, 'type', None) != 'tool' or getattr(message, 'status', None) == 'error'
                        or getattr(message, 'name', None) not in {'fetch_source_task', 'fetch_sql_phases'}):
                    continue
                page = json.loads(message.content)
                if page.get('task_id') != 't_counter':
                    continue
                target = sql if message.name == 'fetch_sql_phases' else rows
                for row in page['items']:
                    if row['key'] in target:
                        assert row == target[row['key']]
                    target[row['key']] = row
    inventory = {**rows, **sql}
    assert len(inventory) == 174
    source_key = '42ef3ef9d9f47b605648ebed4935045e19e0b8b522b8ac622660fdb920d685a5:property:0'
    source = inventory[source_key]
    assert source['role'] == 'source_query' and 'SELECT 18300 AS template_id' in source['original_sql']
    assert 'FROM AE2E.AE2E_JOB_DEP_CHK_COUNT' in source['original_sql']
    post_key = 'c99c1ac31ceca569b17d23e8dba9d2af1d98b3082725badef58a14a64f93afa7:property:1'
    assert inventory[post_key]['role'] == 'post_sql' and 'where 60 <=' in inventory[post_key]['original_sql']
    evidence = SimpleNamespace(rows={'t_counter': list(rows.values())}, sql={'t_counter': list(sql.values())},
        fetched={'t_counter': set(rows)}, sql_fetched={'t_counter': set(sql)},
        reads={key: _digest(row) for key, row in inventory.items()})
    store = _GenerationReceipts(record, _StagedArtifacts(record), evidence, 'offline-canonical-check-not-recovery')
    for row in inventory.values():
        store.translation(dict(task_id='t_counter', kind='control', source_record_keys=[row['key']],
            evidence_ids=row['evidence_ids'], artifact_paths=[], status='blocked', blockers=['Citation check only, no implementation accepted'],
            explanation='Exact historical canonical citation validation only, not recovered metadata or business translation'))
    assert len(store.translations) == 174
    forged = dict(task_id='t_counter', kind='sql_phase', source_record_keys=[source_key],
        evidence_ids=['fabricated-citation'], artifact_paths=[], status='blocked', blockers=['Not implemented'], explanation='Negative check')
    rejects(lambda: store.translation(forged))
    pages, offset, visible = 0, 0, []
    while True:
        page = store.inventory(offset)
        assert len(json.dumps(page).encode()) < 2000
        pages += 1
        visible += page['items']
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert len(visible) == 174 and len({row['key'] for row in visible}) == 174
    assert store.expected.keys() - store.tasks.keys()  # No fabricated task metadata or completion.
    assert manifest['status'] == 'blocked' and public['status'] == 'partial'
    assert len(final_calls) == 13 and sum(reason == 'length' for _, reason in final_calls.values()) == 11
    assert all(args == {} for args, _ in final_calls.values())
    for audit in manifest['staged_drafts']:
        path = manifest_path.parent / audit['audit_relative_path']
        assert sha256(path.read_bytes()).hexdigest() == audit['content_sha256']
    assert sum(audit['bytes'] for audit in manifest['staged_drafts']) == 47704
    assert hashes == {path: sha256(path.read_bytes()).hexdigest() for path in protected}
    print(f'PASS exact real counter snapshot: 174 explicit record receipts, {pages} inventory pages under 2KB, '
          'exact session sourceSQL and post-SQL keys, forged citation refused; '
          '13 empty legacy finalizations/11 length, 47704 preserved bytes; originals unchanged, no recovery')


def main():
    def no_network(*args, **kwargs):
        raise AssertionError('Network forbidden in direct metadata checker')
    with patch.object(socket.socket, 'connect', no_network), TemporaryDirectory(prefix='writer-metadata-') as temporary:
        cases = [('strict', check_strict_rows), ('compiled', check_compiled_metadata),
            ('final-ownership', check_final_metadata_gate),
            ('candidate-coverage', check_candidate_translation_coverage_gate),
            ('candidate', lambda root: check_compiled_metadata(root, candidate=True)),
            ('missing', lambda root: check_compiled_metadata(root, missing=True)),
            ('recover', lambda root: check_compiled_metadata(root, missing=True, recover=True)),
            ('schema', lambda root: check_compiled_metadata(root, schema_repair=True))]
        for name, check in cases:
            root = Path(temporary) / name
            root.mkdir()
            check(root)
        check_real_counter_snapshot()
        # Existing direct checker only, not pytest or any suite.
        legacy_checks()
    print('PASS metadata + existing direct staging checks; all temporary outputs cleaned; no cloud/live inference/restart/suite')


if __name__ == '__main__':
    main()