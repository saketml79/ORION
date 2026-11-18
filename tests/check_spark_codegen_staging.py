"""Direct offline checks, not a suite. Run as a module from the repository root.

Uses real compiled Deep Agents tools/checkpoints with a scripted local model.
All artifacts are temporary. Sockets are forbidden. No running service is touched.
"""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import runpy
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from workbench.agents import AgentRunCancelled
from workbench.native_job_draft import build, graph_metadata
from workbench.redesign import Review, store_for
from workbench.spark_codegen import (
    ContractError, MAX_CONTENT, _StagedArtifacts, _validate, draft_agent_design,
    draft_approved_design,
)


FIXTURES = runpy.run_path(str(Path(__file__).with_name('test_spark_codegen.py')))


def rejects(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError('Expected strict rejection')


def stage_script(model, record, *, truncated=False, chunked=False):
    original = FIXTURES['payload'](record)
    # More than one notebook proves the final tool does not depend on one body.
    second = deepcopy(original['artifacts'][0])
    second['relative_path'] = 'notebooks/second.ipynb'
    second['content'] = second['content'].replace('Draft, no parity claim', 'Second complete notebook')
    original['artifacts'].append(second)
    has_second_task = any(task['id'] == 'second' for task in record['design']['tasks'])
    if has_second_task:
        second['task_ids'] = ['second']
        original['tasks'].append({**deepcopy(original['tasks'][0]), 'task_id': 'second',
            'artifact_paths': [second['relative_path'], 'tests/test_clean.py']})
        original['artifacts'][2]['task_ids'].append('second')
    else:
        original['tasks'][0]['artifact_paths'].append(second['relative_path'])
    evidence = FIXTURES['retrieved'](model, record)
    ids = sorted(evidence.seen)
    for artifact in original['artifacts']:
        artifact['evidence_ids'] = ids
    original['translation_coverage'] = {'items': [{
        'task_id': 'shared', 'kind': 'transformation',
        'source_record_keys': sorted({row['key'] for row in evidence.rows['shared'] + evidence.sql['shared']}),
        'evidence_ids': ids, 'artifact_paths': [a['relative_path'] for a in original['artifacts']
                              if 'shared' in a['task_ids']],
        'status': 'translated', 'explanation': 'Offline synthetic inventory, not parity proof', 'blockers': [],
    }]}
    if has_second_task:
        second_evidence = FIXTURES['_Evidence'](model, record)
        for sql in (False, True):
            rows = second_evidence.sql['second'] if sql else second_evidence.rows['second']
            for offset in range(0, max(1, len(rows)), 12):
                second_evidence.page('second', offset, sql=sql)
        original['translation_coverage']['items'].append({
            **deepcopy(original['translation_coverage']['items'][0]), 'task_id': 'second',
            'source_record_keys': sorted({row['key'] for row in second_evidence.rows['second'] + second_evidence.sql['second']}),
            'evidence_ids': sorted(second_evidence.seen),
            'artifact_paths': ['notebooks/second.ipynb', 'tests/test_clean.py'],
        })
    original.update(input_data_basis='synthetic', behavior_basis='source_preserving')
    responses = FIXTURES['script'](model, record).responses[:-2]
    call = FIXTURES['call']
    if has_second_task:
        for sql in (False, True):
            rows = second_evidence.sql['second'] if sql else second_evidence.rows['second']
            for offset in range(0, max(1, len(rows)), 12):
                responses.append(call('fetch_sql_phases' if sql else 'fetch_source_task',
                    {'task_id': 'second', 'offset': offset}, f'second-{sql}-{offset}'))
    if truncated:
        bad = call('submit_generation_artifact_set', {}, 'length')
        bad.response_metadata = {'finish_reason': 'length'}
        bad.usage_metadata = {'input_tokens': 100, 'output_tokens': 8192, 'total_tokens': 8292}
        responses.append(bad)
        # A later parseable monolith is forbidden after the truncation signal too.
        responses.append(call('submit_generation_artifact_set', {'artifact_set': original}, 'repeat'))
    stager = _StagedArtifacts(record)
    manifest = deepcopy(original)
    for number, artifact in enumerate(original['artifacts']):
        args = {key: artifact[key] for key in ('relative_path', 'kind', 'content')}
        if chunked and artifact['kind'] == 'module':
            args['content'] = '# literal unicode: λ\r\n' + args['content']
            parts = [args['content'][:22], args['content'][22:]]
            offset = 0
            for index, content in enumerate(parts):
                responses.append(call('stage_generation_file_chunk', {
                    **args, 'content': content, 'offset': offset, 'final': index == len(parts) - 1,
                }, f'chunk-{index}'))
                offset += len(content.encode('utf-8'))
        else:
            responses.append(call('stage_generation_artifact', args, f'stage-{number}'))
        receipt = stager.stage(**args)
        manifest['artifacts'][number] = {
            **{key: artifact[key] for key in ('relative_path', 'kind', 'task_ids', 'evidence_ids')},
            'content_sha256': receipt['content_sha256'],
        }
    responses.append(call('finalize_generation_artifact_set', {'artifact_set': manifest}, 'final'))
    responses.append(AIMessage(content='Must not execute another model call after acceptance'))
    route = FIXTURES['ScriptedChat'](responses=responses,
        custom_get_token_ids=lambda text: list(range(len(text) // 4)))
    return route, manifest, stager


def context(root):
    return FIXTURES['context'].__wrapped__(root)


def check_stager(root):
    _, record, model, _ = context(root)
    store = _StagedArtifacts(record)
    args = dict(relative_path='modules/exact.py', kind='module', content='x = "λ"\r\n')
    receipt = store.stage(**args)
    assert receipt['content_sha256'] == sha256(args['content'].encode('utf-8')).hexdigest()
    assert store.stage(**args) == receipt
    rejects(lambda: store.stage(**{**args, 'content': 'x = 2\n'}))
    revised = store.stage(**{**args, 'content': 'x = 2\n'}, replace_sha256=receipt['content_sha256'])
    assert revised['content_sha256'] != receipt['content_sha256']
    rejects(lambda: store.stage(**{**args, 'relative_path': 'modules/EXACT.py'}))
    rejects(lambda: store.stage(**{**args, 'relative_path': '../outside.py'}))
    rejects(lambda: store.stage(**{**args, 'content': 'def broken(:'}))
    rejects(lambda: store.stage(**{**args, 'content': 'x' * (MAX_CONTENT + 1)}))
    rejects(lambda: store.stage(**{**args, 'content': 'password = "real_password_value_123"'}))
    rejects(lambda: store.stage('tests/test_bad.py', 'test', 'def test_bad():\n    assert True\n'))
    rejects(lambda: store.stage('notebooks/bad.ipynb', 'notebook', '{"cells":['))
    invalid = json.loads(FIXTURES['notebook']())
    invalid['cells'][1]['metadata']['language'] = 'bash'
    rejects(lambda: store.stage('notebooks/bad.ipynb', 'notebook', json.dumps(invalid)))
    rejects(lambda: store.stage('sql/bad.sql', 'sql', 'SELECT ('))
    assert len(store.files) == 1 and not store.chunks
    # Chunk prefixes are not files, final invalid content is not retained.
    prefix = store.chunk('modules/chunk.py', 'module', 'def value():\n', final=False)
    assert 'modules/chunk.py' not in store.files
    rejects(lambda: store.chunk('modules/chunk.py', 'module', 'broken', offset=1))
    rejects(lambda: store.chunk('modules/chunk.py', 'module', '    return (',
                                 offset=prefix['next_offset'], final=True))
    complete = store.chunk('modules/chunk.py', 'module', '    return 7\n',
                           offset=prefix['next_offset'], final=True)
    assert complete['staged'] and not store.chunks
    route, manifest, staged = stage_script(model, record)
    hydrated = staged.hydrate(manifest)
    _, bodies = _validate(hydrated, record, FIXTURES['retrieved'](model, record))
    assert all(bodies[path] == item['content'] for path, item in staged.files.items())
    for mutation in ('hash', 'path', 'content', 'kind', 'version', 'evidence', 'tasks', 'native'):
        bad = deepcopy(manifest)
        if mutation == 'hash':
            bad['artifacts'][0]['content_sha256'] = '0' * 64
        elif mutation == 'path':
            bad['artifacts'][0]['relative_path'] = 'notebooks/external.ipynb'
        elif mutation == 'content':
            bad['artifacts'][0]['content'] = 'not a reference'
        elif mutation == 'kind':
            bad['artifacts'][0]['kind'] = 'module'
        elif mutation == 'version':
            bad['design_version'] += 1
        elif mutation == 'evidence':
            bad['artifacts'][0]['evidence_ids'] = ['unretrieved']
        elif mutation == 'tasks':
            bad['tasks'] = []
        else:
            bad['tasks'][0]['implementation_kind'] = 'spark_python_task'
        rejects(lambda: _validate(staged.hydrate(bad), record, FIXTURES['retrieved'](model, record)))
    rejects(lambda: _StagedArtifacts(record).hydrate(manifest))
    assert len(route.responses) > 1
    print('PASS staging: exact bytes, strict formats, paths, secrets, bounds, revisions, chunks, typed hydration and provenance')


def check_compiled(root, *, truncated=False, chunked=False, candidate=False):
    service, record, model, inputs = context(root)
    two = deepcopy(record['design'])
    two['tasks'].append({**deepcopy(two['tasks'][0]), 'id': 'second'})
    two['coverage'][0]['proposed_task_ids'].append('second')
    two['artifact_plan'].append({**deepcopy(two['artifact_plan'][0]),
        'relative_path': 'notebooks/second.ipynb', 'proposed_task_ids': ['second']})
    record = store_for(service).append(record['package_hash'], 'wf', 'original',
        {'design': two, 'role': 'architecture', 'retrieved_evidence_ids': ['a', 'b', 'disabled', 'mapping']},
        {**record['source'], 'record_id': 'two-task-run'}, record['version'])
    store_for(service).review(record['id'], record['package_hash'], Review(
        action='approve_for_drafting', comment='Authorize two-task offline draft only',
        expected_version=record['version'], expected_input_version=inputs['input_version'], consent=True),
        inputs['input_version'], {'actor': 'offline-check'})
    record = store_for(service).get(record['id'], record['package_hash'])
    route, manifest, staged = stage_script(model, record, truncated=truncated, chunked=chunked)
    service.runtime.role_models = {'artifact': route}
    if candidate:
        run_id = record['source']['record_id']
        run = dict(id=run_id, consent=True, generate_code=True, status='running',
            **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
        service.record = lambda identifier, **kwargs: run
        report = draft_agent_design(service, record, agent_run_id=run_id, consent=True)
        assert report['draft_authority'] == 'explicit_agent_run_candidate'
    else:
        report = draft_approved_design(service, record, consent=True)
    assert report['status'] == 'generation_complete', report
    assert report['failure'] is None
    assert not any(report[key] for key in ('artifact_approved', 'execution_allowed', 'deployment_allowed'))
    assert report['translation_coverage'] == manifest['translation_coverage']
    assert report['translation_assessment']['status'] == 'static_inventory_accounted'
    directory = service.root / report['relative_directory']
    persisted = json.loads((directory / 'manifest.json').read_bytes())
    bodies = {}
    for path, digest in persisted['artifact_hashes'].items():
        actual = (directory / path).read_bytes()
        assert sha256(actual).hexdigest() == digest == staged.files[path.casefold()]['content_sha256']
        assert actual == staged.files[path.casefold()]['content'].encode('utf-8')
        bodies[path] = actual.decode('utf-8')
        if path.endswith('.ipynb'):
            assert all('language' in cell['metadata'] and cell['metadata']['id'] == cell['id']
                       for cell in json.loads(actual)['cells'])
    assert len([path for path in bodies if path.endswith('.ipynb')]) == 2
    document = build(record['design'], report, bodies, '/Workspace/Users/offline')
    graph = graph_metadata(document, report, bodies, '/Workspace/Users/offline')
    assert graph[0]['tasks'][0]['notebook_task']['notebook_path'].endswith('/notebooks/load')
    assert graph[0]['tasks'][0]['task_key'] == 'shared'
    assert graph[0]['tasks'][1]['task_key'] == 'second'
    assert graph[0]['tasks'][1]['notebook_task']['notebook_path'].endswith('/notebooks/second')
    assert len(route._calls) == len(route.responses) - 1
    assert {'stage_generation_artifact', 'stage_generation_file_chunk', 'finalize_generation_artifact_set',
            'stage_generation_translation_page', 'submit_generation_artifact_set'} <= route._bound_names
    first_prompt = json.dumps([message.model_dump() for message in route._calls[0]], default=str)
    assert 'stage_generation_translation_page' in first_prompt
    assert 'translation_page_group' in first_prompt
    assert 'Do not use stage_generation_translation for inventory coverage or manually copy citation IDs' in first_prompt
    final_calls = [call for message in route.responses
                   for call in getattr(message, 'tool_calls', []) if call['name'] == 'finalize_generation_artifact_set']
    assert final_calls and all('content' not in item for item in final_calls[-1]['args']['artifact_set']['artifacts'])
    serializer = JsonPlusSerializer()
    with closing(sqlite3.connect(directory / 'checkpoint_spark_codegen.sqlite')) as db:
        tools = []
        for kind, blob in db.execute("SELECT type,value FROM writes WHERE channel='messages'"):
            value = serializer.loads_typed((kind, blob))
            tools.extend(m for m in (value if isinstance(value, list) else [value]) if getattr(m, 'type', None) == 'tool')
    receipts = [json.loads(m.content) for m in tools if m.name == 'stage_generation_artifact' and m.status != 'error']
    assert receipts and all('content_sha256' in r and 'content' not in r and not r['committed'] for r in receipts)
    if truncated:
        errors = [json.loads(m.content) for m in tools if m.status == 'error']
        assert any(e.get('error') == 'incomplete_model_tool_payload' for e in errors)
        assert any('Stage files individually' in e.get('error', '') for e in errors)
        assert any(event.get('usage', {}).get('output_tokens') == 8192 for event in report['events']
                   if isinstance(event.get('usage'), dict))
        assert report['budget_used']['submission_attempts'] == 3
    print(f'PASS compiled graph: truncated={truncated}, chunked={chunked}, candidate={candidate}, '
          f'{len(bodies)} exact committed files, metadata-only finalization, native Jobs draft, checkpoint hash receipts')


def check_no_partial(root):
    service, record, model, _ = context(root)
    route, manifest, _ = stage_script(model, record)
    responses = route.responses[:-2]
    manifest['artifacts'][0]['content_sha256'] = '0' * 64
    responses += [FIXTURES['call']('finalize_generation_artifact_set', {'artifact_set': manifest}, 'bad-final')]
    responses += [AIMessage(content='No valid finalization'), AIMessage(content='Still no valid finalization')]
    service.runtime.role_models = {'artifact': FIXTURES['ScriptedChat'](responses=responses)}
    report = draft_approved_design(service, record, consent=True)
    assert report['status'] == 'blocked' and report['artifact_hashes'] == {}
    directory = service.root / report['relative_directory']
    assert not (directory / 'notebooks').exists() and not (directory / 'modules').exists()
    assert list(directory.glob('*.json')) == [directory / 'manifest.json']
    print('PASS invalid finalization: no staged artifact was committed')


def check_cancelled(root):
    service, record, model, _ = context(root)
    run_id = record['source']['record_id']
    cancelled = Event()
    service.agent_cancel_events = {run_id: cancelled}
    service.record = lambda identifier, **kwargs: dict(id=run_id, consent=True, generate_code=True,
        status='running', **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
    route, _, _ = stage_script(model, record)
    service.runtime.role_models = {'artifact': route}
    original = type(route)._generate
    def cancel_after_response(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        # Cancel when finalization has been requested, before tool acceptance.
        if any(call['name'] == 'finalize_generation_artifact_set' for call in response.generations[0].message.tool_calls):
            cancelled.set()
        return response
    with patch.object(type(route), '_generate', cancel_after_response):
        try:
            draft_agent_design(service, record, agent_run_id=run_id, consent=True)
        except AgentRunCancelled:
            pass
        else:
            raise AssertionError('Cancellation was not propagated')
    assert not list(service.root.rglob('manifest.json'))
    audit_paths = list(service.root.rglob('staged-artifacts/receipts/*.json'))
    assert audit_paths
    assert all(json.loads(path.read_bytes())['status'] == 'draft_not_complete' for path in audit_paths)
    assert all('staged-artifacts' in path.parts for path in service.root.rglob('*.ipynb'))
    print('PASS manual cancellation: no manifest/accepted artifacts; validated audit drafts survive')


def checkpoint_receipts(directory):
    serializer = JsonPlusSerializer()
    with closing(sqlite3.connect(directory / 'checkpoint_spark_codegen.sqlite')) as db:
        for kind, blob in db.execute("SELECT type,value FROM writes WHERE channel='messages'"):
            value = serializer.loads_typed((kind, blob))
            for message in value if isinstance(value, list) else [value]:
                if getattr(message, 'type', None) == 'tool':
                    yield json.loads(message.content)


def check_empty_finalization(root, *, recover=False, progress=False, duplicate=False):
    service, record, model, _ = context(root)
    route, manifest, staged = stage_script(model, record)
    call = FIXTURES['call']
    responses = route.responses[:-2]
    responses.append(call('finalize_generation_artifact_set', {}, 'empty-first'))
    if duplicate:
        artifact = FIXTURES['payload'](record)['artifacts'][1]
        responses.append(call('stage_generation_artifact', {key: artifact[key] for key in
            ('relative_path', 'kind', 'content')}, 'identical-restage'))
    if progress:
        responses.append(call('stage_generation_artifact', {
            'relative_path': 'modules/new_progress.py', 'kind': 'module', 'content': 'new_value = 7\n'}, 'progress'))
        responses.append(call('finalize_generation_artifact_set', {}, 'new-progress-empty'))
    if recover:
        responses.append(call('finalize_generation_artifact_set', {'artifact_set': json.dumps(manifest)}, 'corrected'))
    else:
        # A long script reproduces the reported unbounded loop, without actually making 989 calls.
        responses += [call('finalize_generation_artifact_set', {}, f'empty-{i}') for i in range(989)]
    route = FIXTURES['ScriptedChat'](responses=responses)
    service.runtime.role_models = {'artifact': route}
    schemas = {}
    original_bind = type(route).bind_tools
    def capture(self, tools, **kwargs):
        for tool in tools:
            if getattr(tool, 'name', '') in {'finalize_generation_artifact_set', 'submit_generation_artifact_set'}:
                schemas[tool.name] = convert_to_openai_tool(tool)
        return original_bind(self, tools, **kwargs)
    with patch.object(type(route), 'bind_tools', capture):
        report = draft_approved_design(service, record, consent=True)
    assert len(schemas) == 2
    for schema in schemas.values():
        params = schema['function']['parameters']
        assert params['required'] == ['artifact_set']
        assert len(json.dumps(params)) < 400 and '$defs' not in params
        assert {item['type'] for item in params['properties']['artifact_set']['anyOf']} == {'object', 'string'}
    directory = service.root / report['relative_directory']
    corrections = [r for r in checkpoint_receipts(directory) if r.get('error') == 'empty_finalization_arguments']
    assert len(corrections) == (2 if progress else 1)
    assert corrections[0]['required_envelope']['design_id'] == record['id']
    assert len(corrections[0]['staged_receipts']) == len(staged.files)
    assert 'task_ids' in corrections[0]['instruction'] and 'not a timeout' in corrections[0]['instruction']
    assert all('content' not in item for item in corrections[0]['staged_receipts'])
    if recover:
        assert report['status'] == 'generation_complete' and report['failure'] is None
        assert len(route._calls) == len(responses)
    else:
        assert report['status'] == 'blocked' and report['failure'] == 'writer_protocol_no_progress'
        assert report['artifact_hashes'] == {} and not (directory / 'notebooks').exists()
        assert len(route._calls) == len(route.responses) - 988
        failures = [e for e in report['events'] if e.get('error_code') == 'writer_protocol_no_progress']
        assert len(failures) == 1 and failures[0]['timeout'] is False
        assert not report['artifact_approved'] and not report['execution_allowed']
    assert report['staged_drafts']
    for audit in report['staged_drafts']:
        content = (directory / audit['audit_relative_path']).read_bytes()
        assert sha256(content).hexdigest() == audit['content_sha256']
        assert audit['status'] == 'draft_not_complete' and not audit['committed']
    print(f'PASS empty finalization: recover={recover}, progress={progress}, identical_restage={duplicate}; '
          'actual compact provider schemas, checkpoint correction, explicit protocol failure or valid JSON-string recovery, durable audit bytes')


def check_retry_progress(root, *, fail=False, unknown=False):
    service, record, model, _ = context(root)
    route, _, _ = stage_script(model, record)
    service.runtime.role_models = {'artifact': route}
    run_id = record['source']['record_id']
    run = dict(id=run_id, consent=True, generate_code=True, status='running',
               model_retry={'event': 'old_wait'},
               **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
    updates = []
    service.record = lambda identifier, **kwargs: run
    service.update_record = lambda value, **kwargs: updates.append(deepcopy(value))
    attempts = []
    def retry(operation, model, cancelled, on_progress):
        if unknown:
            run['status'] = 'unknown'
        if not attempts:
            on_progress({'event': 'model_retry_wait', 'retry_count': 1, 'retry_wait_seconds': 12, 'status_code': 429})
            on_progress({'event': 'model_retry_resuming', 'retry_count': 1, 'retry_wait_seconds': 0, 'status_code': 429})
        attempts.append(True)
        if fail:
            raise RuntimeError('Offline simulated provider failure, never published')
        result = operation()
        if len(attempts) == 1:
            on_progress({'event': 'model_retry_succeeded', 'retry_count': 1, 'retry_wait_seconds': 0, 'status_code': 429})
        return result
    with patch('workbench.databricks_models.retry_databricks_429', retry):
        report = draft_agent_design(service, record, agent_run_id=run_id, consent=True)
    assert updates[0]['phase'] == 'writer_model_started' and 'model_retry' not in updates[0]
    if unknown:
        assert len(updates) == 1 and run['status'] == 'unknown'
    else:
        assert updates[-1]['phase'] == ('writer_model_failed' if fail else 'writer_model_completed')
        assert 'model_retry' not in updates[-1]
        waits = [u for u in updates if u.get('model_retry', {}).get('event') == 'model_retry_wait']
        assert waits and waits[0]['model_retry']['operation'] == 'spark_codegen'
        assert all(u['status'] == 'running' for u in updates)
    assert report['status'] == ('blocked' if fail else 'generation_complete')
    print(f'PASS retry progress: fail={fail}, unknown_operation={unknown}; clear stale wait, started/completed/failed, never overwrite run status')


def main():
    def no_network(*args, **kwargs):
        raise AssertionError('Network access forbidden during direct checks')
    with patch.object(socket.socket, 'connect', no_network), TemporaryDirectory(prefix='writer-staging-check-') as temporary:
        root = Path(temporary)
        cases = [
            ('stage', check_stager),
            ('compiled', check_compiled),
            ('truncated', lambda p: check_compiled(p, truncated=True, chunked=True)),
            ('candidate', lambda p: check_compiled(p, candidate=True)),
            ('invalid-final', check_no_partial),
            ('cancelled', check_cancelled),
            ('empty-no-progress', check_empty_finalization),
            ('empty-corrected', lambda p: check_empty_finalization(p, recover=True)),
            ('empty-semantic-progress', lambda p: check_empty_finalization(p, recover=True, progress=True)),
            ('empty-identical-restage', lambda p: check_empty_finalization(p, duplicate=True)),
            ('retry-progress', check_retry_progress),
            ('retry-failed', lambda p: check_retry_progress(p, fail=True)),
            ('retry-unknown', lambda p: check_retry_progress(p, unknown=True)),
        ]
        for name, check in cases:
            location = root / name
            location.mkdir()
            check(location)
        for name in ('test_real_compiled_graph_uses_artifact_route_and_persists_immutable_code',
                     'test_recoverable_validation_feedback_is_not_a_provider_retry',
                     'test_call_budget_and_no_submission_are_explicitly_blocked'):
            location = root / name
            location.mkdir()
            FIXTURES[name](context(location))
            print('PASS compatibility: ' + name)
    print('PASS all direct checks; temporary artifacts cleaned; no cloud, live model, service restart or suite')


if __name__ == '__main__':
    main()