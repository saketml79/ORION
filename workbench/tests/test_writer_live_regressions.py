"""Offline runtime reproductions of the patched live writer failures.

Real DeepAgents graphs and HTTP workers, scripted local models only. The
multi-ID case expands the capture seam because real capture groups one scope.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import socket

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from workbench.redesign import capture_run, store_for
from workbench.spark_codegen import _StagedArtifacts, draft_agent_design
from workbench.tests.test_run_review_fixes import api, HEADERS, wait
from workbench.tests.test_redesign import chat, design_for, scripted_runtime, tool_call


FIX = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests' / 'test_spark_codegen.py'))


def test_default_agent_prompt_is_generic_and_requests_one_final_artifact_set():
    from html.parser import HTMLParser

    class Prompt(HTMLParser):
        capturing = False
        text = ''

        def handle_starttag(self, tag, attrs):
            if tag == 'textarea' and dict(attrs).get('id') == 'agent-prompt':
                self.capturing = True

        def handle_endtag(self, tag):
            if tag == 'textarea':
                self.capturing = False

        def handle_data(self, data):
            if self.capturing:
                self.text += data

    parser = Prompt()
    parser.feed((Path(__file__).resolve().parents[1] / 'static/index.html').read_text(encoding='utf-8'))
    assert parser.text.startswith('Understand and convert the selected Informatica workflow')
    assert 'application selections' in parser.text
    assert 'one saved artifact set' in parser.text
    assert 'superseded design revisions' in parser.text
    assert 'validation-agent feedback' in parser.text
    for supplied_answer in ('WIL_NET_DIS', 'AE2E_JOB_DEP_CHK_COUNT', '18300', '18537',
                            'infotodbxmig_src_dev_ssuman', 'threshold34/35', 'B_AP_INVOICELOG'):
        assert supplied_answer not in parser.text
    assert len(parser.text) < 12000


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ('LANGSMITH_TRACING', 'LANGCHAIN_TRACING_V2'):
        monkeypatch.setenv(key, 'false')
    monkeypatch.setenv('WORKBENCH_LANGSMITH_ENABLED', '0')

    def forbidden(*args, **kwargs):
        pytest.fail('Provider/cloud/network access forbidden in live-bug regressions')

    connect = socket.socket.connect
    create_connection = socket.create_connection

    def local_connect(sock, address):
        # Windows asyncio creates its self-pipe with a loopback socket pair.
        # TestClient also needs that event loop, not an external HTTP server.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return connect(sock, address)
        return forbidden()

    def local_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return create_connection(address, *args, **kwargs)
        return forbidden()

    monkeypatch.setattr(socket.socket, 'connect', local_connect)
    monkeypatch.setattr(socket, 'create_connection', local_connection)
    monkeypatch.setattr('langchain.chat_models.init_chat_model', forbidden)


@pytest.mark.parametrize('staging', ['stage_generation_artifact', 'stage_generation_file_chunk'])
def test_compiled_candidate_reads_plain_text_skill_then_saves_code(tmp_path, staging):
    service, record, model, _ = FIX['context'].__wrapped__(tmp_path)
    content = FIX['notebook']()
    path = 'notebooks/load.ipynb'
    staged = _StagedArtifacts(record)
    expected = staged.stage(path, 'notebook', content)
    actions = [('read_file', {'file_path': '/library/implementation/SKILL.md'})]
    evidence = FIX['retrieved'](model, record)
    for sql in (False, True):
        rows = evidence.sql['shared'] if sql else evidence.rows['shared']
        for offset in range(0, max(1, len(rows)), 12):
            actions.append(('fetch_sql_phases' if sql else 'fetch_source_task',
                            {'task_id': 'shared', 'offset': offset}))
    args = {'relative_path': path, 'kind': 'notebook', 'content': content}
    if staging == 'stage_generation_file_chunk':
        split = len(content) // 2
        actions.append((staging, {**args, 'content': content[:split], 'offset': 0, 'final': False}))
        args.update(content=content[split:], offset=len(content[:split].encode('utf-8')), final=True)
    actions.extend([(staging, args), ('commit_generation_receipts', {})])
    route = FIX['ScriptedChat'](
        responses=[FIX['call'](name, args, i) for i, (name, args) in enumerate(actions)]
                  + [AIMessage(content='Unexpected extra model invocation')],
        custom_get_token_ids=lambda text: list(range(len(text) // 4)))
    service.runtime.role_models = {'artifact': route}
    public = dict(id='source-run', consent=True, generate_code=True, status='running',
                  **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')})
    service.record = lambda *args, **kwargs: deepcopy(public)
    service.update_record = lambda value, **kwargs: public.update(value)
    history_before = (service.state / 'redesigns.sqlite').read_bytes()

    report = draft_agent_design(service, record, agent_run_id='source-run', consent=True)

    assert report['failure'] is None, report
    assert report['status'] == 'drafts_saved', report
    assert len(route._calls) == len(actions)  # no retry or post-commit provider call
    skill_messages = [message for message in route._calls[1]
                      if isinstance(message, ToolMessage) and message.name == 'read_file']
    assert len(skill_messages) == 1
    skill = skill_messages[0]
    assert skill.status == 'success' and isinstance(skill.content, str)
    assert 'implementation' in skill.content.lower()
    with pytest.raises(json.JSONDecodeError):
        json.loads(skill.content)  # prove the real filesystem result is not JSON
    # Emitted tool events prove the actual graph executed each action, including commit.
    tool_events = [event for event in report['events'] if event.get('kind') == 'tool']
    assert [event['name'] for event in tool_events] == [name for name, _ in actions]
    assert all(event['status'] == 'completed' for event in tool_events), tool_events
    assert report['artifact_hashes'][path] == expected['content_sha256']
    directory = service.root / report['relative_directory']
    assert (directory / path).read_text(encoding='utf-8') == staged.files[path]['content']
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['artifact_hashes'] == report['artifact_hashes']
    assert report['candidate_availability']['tasks'][0]['code_available']
    assert not report['execution_allowed'] and not report['deployment_allowed']
    assert (service.state / 'redesigns.sqlite').read_bytes() == history_before


def configure_two_submissions(app, workflow, tmp_path):
    index = app.state.service.active
    first = design_for(index.model, workflow)
    final = deepcopy(first)
    final['title'] = 'Final evidence-backed revision'
    runtime, _ = scripted_runtime(tmp_path / 'runtime-two', index.model, workflow, first)
    refs = first['evidence_ids']
    actions = [tool_call('lookup_evidence', {'evidence_id': ref}, f'ref-{i}')
               for i, ref in enumerate(refs)]
    actions += [tool_call('submit_redesign', {'design': first}, 'first'),
                tool_call('submit_redesign', {'design': final}, 'final'), AIMessage(content='Done.')]
    runtime.role_models['architecture'] = chat(actions)
    app.state.service.runtime = runtime
    return first, final


def writer_spy(monkeypatch, invocations):
    def draft(service, design, *, agent_run_id, consent, budget=None):
        assert consent is True and budget is None
        run = service.record(agent_run_id, agent=True)
        assert run['generate_code'] and run['status'] == 'running'
        exact = store_for(service).get(design['id'], design['package_hash'], design['version'])
        assert exact['design'] == design['design']
        invocations.append(deepcopy(design))
        relative = f"generated-drafts/{design['id']}/{design['version']}/offline-writer"
        directory = service.root / relative
        directory.mkdir(parents=True)
        (directory / 'manifest.json').write_text('{"status":"drafts_saved"}', encoding='utf-8')
        return {'run_id': 'a' * 32, 'relative_directory': relative, 'status': 'drafts_saved',
                'tasks': [], 'failure': None}
    monkeypatch.setattr('workbench.spark_codegen.draft_agent_design', draft)


def run_http(client, workflow):
    response = client.post('/api/agents/run', headers=HEADERS, json={
        'workflow_id': workflow, 'scenario': 'original', 'prompt': 'Propose a redesign',
        'consent': True, 'generate_code': True})
    assert response.status_code == 202, response.text
    result = wait(client, '/api/agents/runs/' + response.json()['id'])
    assert result['status'] in {'completed', 'partial'}, result
    assert all(row['status'] == 'completed' for row in result['output']['candidate_code_drafts']), result
    return result


def test_http_worker_real_capture_writes_only_final_revision_retains_history(api, tmp_path, monkeypatch):
    client, app, workflow = api
    first, final = configure_two_submissions(app, workflow, tmp_path)
    invocations = []
    writer_spy(monkeypatch, invocations)

    result = run_http(client, workflow)

    refs = result['redesign_refs']
    assert len(refs) == 2 and refs[0]['id'] == refs[1]['id']
    assert [ref['version'] for ref in refs] == [1, 2]
    assert [(row['id'], row['version']) for row in invocations] == [(refs[1]['id'], 2)]
    store = store_for(app.state.service)
    assert store.get(refs[0]['id'], result['package_hash'], 1)['design']['title'] == first['title']
    assert store.get(refs[1]['id'], result['package_hash'], 2)['design']['title'] == final['title']
    assert app.state.service.record(result['id'], agent=True)['redesign_refs'] == refs
    assert len(result['output']['candidate_code_drafts']) == 1
    job = client.get('/api/jobs/' + result['output']['candidate_code_drafts'][0]['job_id']).json()
    assert job['design_version'] == 2 and job['design_id'] == refs[1]['id']
    assert not job['execution_allowed'] and not job['deployment_allowed']


def test_http_worker_ignores_other_scenario_and_writes_selected_final_revision_once(api, tmp_path, monkeypatch):
    client, app, workflow = api
    configure_two_submissions(app, workflow, tmp_path)
    captured = []

    def multi_scope_capture(service, index, record, output):
        # The normal capture is single-scope and thus one ID. Persist genuine
        # versions in two fixture scopes to exercise the worker's multi-ID seam.
        original = capture_run(service, index, record, output)
        other = capture_run(service, index, {**record, 'scenario': 'bronze'}, output)
        captured.extend([original[1], other[0], original[0], other[1]])
        return captured  # deliberately unsorted: select max, not last/first

    monkeypatch.setattr('workbench.redesign.capture_run', multi_scope_capture)
    invocations = []
    writer_spy(monkeypatch, invocations)

    result = run_http(client, workflow)

    expected = {(row['id'], 2) for row in captured if row['scenario'] == 'original'}
    assert len(expected) == 1
    assert len(invocations) == 1
    assert {(row['id'], row['version']) for row in invocations} == expected
    assert result['redesign_refs'] == [{'id': row['id'], 'version': row['version']} for row in captured]
    drafts = result['output']['candidate_code_drafts']
    assert len(drafts) == 1
    assert {(row['design_id'], row['design_version']) for row in drafts} == expected
    for row in captured:
        assert store_for(app.state.service).get(row['id'], result['package_hash'], row['version'])['design'] == row['design']