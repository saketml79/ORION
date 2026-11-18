"""Direct temporary ASGI/compiled writer checks, not a pytest suite or live run."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import runpy
import socket
from tempfile import TemporaryDirectory
from threading import local
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from workbench.app import _create_package_app
from workbench.redesign import store_for
from workbench.spark_codegen import ContractError, draft_agent_design, draft_approved_design


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = runpy.run_path(str(ROOT / 'tests' / 'test_spark_codegen.py'))
PARENT = 'f' * 32


def forbidden(*args, **kwargs):
    raise AssertionError('No network or full analysis is permitted')


class HoldingExecutor:
    def __init__(self, **kwargs):
        self.pending = []

    def submit(self, function, *args):
        self.pending.append((function, args))

    def finish(self):
        function, args = self.pending.pop(0)
        function(*args)

    def shutdown(self, **kwargs):
        assert not self.pending


class FailingChat(FIXTURES['ScriptedChat']):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.i:
            raise PermissionError(13, 'private provider diagnostic')
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def model():
    return FailingChat(responses=[FIXTURES['call']('read_file', {'file_path': '/CONTEXT.md'}, 'read'),
                                  AIMessage(content='unused')])


def checks(root):
    runtime = SimpleNamespace(role_models={'artifact': model()}, run=forbidden,
                              status=lambda: {'available': True, 'authentication': 'model_provided'})
    app = _create_package_app(root, agent_runtime=runtime, executor_factory=HoldingExecutor,
                              record_audit_initialization=False)
    service = app.state.service
    canonical = FIXTURES['source_model']()
    index = SimpleNamespace(model=canonical, nodes={n['id']: n for n in canonical['nodes']},
                            evidence_references=lambda identifiers: [])
    service.active = index
    inputs = {'input_version': 'b' * 64, 'input_hashes': {'policy': 'c' * 64, 'skills': 'd' * 64},
              'input_versions': {'documents': 'e' * 64, 'reviews': 'e' * 64}, 'snapshot': '{}'}
    service.agent_inputs = lambda *args: deepcopy(inputs)
    submissions = [{'design': FIXTURES['design'](), 'role': 'supervisor',
                    'retrieved_evidence_ids': ['a', 'b', 'disabled', 'mapping']} for _ in range(7)]
    store = store_for(service)
    for number, submission in enumerate(submissions):
        record = store.append(canonical['package']['sha256'], 'wf', 'original', submission,
            {'record_id': PARENT, 'runtime_run_id': '1' * 32, 'submission_index': number, 'kind': 'agent',
             **{k: deepcopy(inputs[k]) for k in ('input_version', 'input_hashes', 'input_versions')}}, number)
    record = store.get(record['id'], record['package_hash'])
    parent = {'id': PARENT, 'status': 'partial', 'consent': True, 'generate_code': True,
              **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')},
              **{key: deepcopy(inputs[key]) for key in ('input_hashes', 'input_versions')},
              'redesign_refs': [{'id': record['id'], 'version': 6}, {'id': record['id'], 'version': 7}],
              'output': {'run_id': '1' * 32, 'redesigns': submissions,
                         'candidate_code_drafts': [{'status': 'failed', 'error': 'Original failure retained'}]}}
    service.update_record(parent, agent=True)
    protected_parent = service.record_path(PARENT, agent=True).read_bytes()
    protected_design = (service.state / 'redesigns.sqlite').read_bytes()
    body = {'consent': True, 'design_id': record['id'], 'version': 7,
            'current_input_version': inputs['input_version']}
    path = '/api/agents/runs/' + PARENT + '/candidate-code'
    client = TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000),
                        headers={'X-Workbench-Request': '1'})
    def post(value=None, expected=409, url=path):
        response = client.post(url, json=body if value is None else value)
        assert response.status_code == expected, (response.status_code, response.text)
        return response.json()
    try:
        # Fresh saved GET supplies the exact request pin. No implicit approval.
        response = client.get('/api/redesigns/' + record['id'] + '?version=7')
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved['version'] == saved['latest_version'] == 7 and saved['stale'] is False
        assert saved['current_input_version'] == body['current_input_version']
        assert saved['outline_drafting_allowed'] is False and saved['reviews'] == []
        post({**body, 'consent': False}, 403)
        post({**body, 'consent': 'true'}, 422)
        post({k: v for k, v in body.items() if k != 'consent'}, 422)
        post({**body, 'version': True}, 422)
        post({**body, 'unknown': True}, 422)
        post({**body, 'version': 6})
        post({**body, 'current_input_version': '0' * 64})
        post({**body, 'design_id': '0' * 64}, 404)
        for key, wrong in [('package_hash', '0' * 64), ('workflow_id', 'outside'),
                           ('scenario', 'bronze'), ('status', 'running'), ('generate_code', False),
                           ('redesign_refs', []), ('input_hashes', {})]:
            service.update_record({**parent, key: wrong}, agent=True)
            post(expected=404 if key == 'package_hash' else 409)
        forged = deepcopy(parent)
        forged['output']['redesigns'][6]['design']['title'] = 'Unrelated typed design'
        service.update_record(forged, agent=True)
        post()
        service.update_record(parent, agent=True)
        inputs['input_hashes']['skills'] = '0' * 64
        stale = post()['detail']
        assert stale['code'] == 'candidate_design_stale' and 'input_hashes' in stale['changed_pins']
        inputs['input_hashes']['skills'] = 'd' * 64
        inputs['input_version'] = '0' * 64
        assert post()['detail']['code'] == 'candidate_design_stale'
        inputs['input_version'] = 'b' * 64
        runtime.role_models = None
        post()
        runtime.role_models = {'artifact': model()}
        assert not service.agent_executor.pending and not list((service.state / 'jobs').glob('*.json'))
        assert service.agent_slot.acquire(blocking=False)
        post()
        service.agent_slot.release()
        # A consented current-design draft may be attempted without review, but
        # it remains unapproved and this scripted provider failure must stay blocked.
        ordinary = draft_approved_design(service, record, consent=True)
        assert ordinary['status'] == 'blocked' and ordinary['artifact_hashes'] == {}
        assert ordinary['execution_allowed'] is False and ordinary['deployment_allowed'] is False
        # A partial parent is never accepted as a running candidate authority.
        try:
            draft_agent_design(service, record, agent_run_id=PARENT, consent=True)
        except ContractError:
            pass
        else:
            raise AssertionError('Partial parent run was accepted as candidate authority')
        writer_ids, jobs = [], []
        for _ in range(2):
            runtime.role_models = {'artifact': model()}
            queued = post(expected=202)
            assert queued['approved'] is False and queued['agent_run_id'] == PARENT
            assert queued['status'] == 'queued' and queued['design_version'] == 7
            assert queued['execution_allowed'] is False and queued['deployment_allowed'] is False
            post()  # The same shared slot prevents a second candidate or agent run.
            service.agent_executor.finish()
            done = service.record(queued['id'])
            assert done['status'] == 'completed', done
            report = done['output']
            assert report['manifest_status'] == 'blocked' and report['artifact_approved'] is False
            assert report['failure_diagnostic']['exceptions'][0]['exception_class'] == 'PermissionError'
            assert 'private provider' not in json.dumps(done)
            manifest_path = root / report['relative_directory'] / 'manifest.json'
            manifest = json.loads(manifest_path.read_bytes())
            assert manifest['draft_authority'] == 'explicit_parent_candidate_retry'
            assert manifest['budget_used']['model_calls'] == 2
            assert manifest['approval_sha256'] is None and manifest['artifact_approved'] is False
            assert manifest_path.with_name('checkpoint_spark_codegen.sqlite').is_file()
            writer_ids.append(report['run_id'])
            jobs.append(queued['id'])
            assert service.record_path(PARENT, agent=True).read_bytes() == protected_parent
            assert (service.state / 'redesigns.sqlite').read_bytes() == protected_design
        assert len(set(writer_ids)) == len(set(jobs)) == 2
        assert all(identifier not in {PARENT, '1' * 32} for identifier in writer_ids + jobs)
        assert service.agent_slot.acquire(blocking=False)
        service.agent_slot.release()
        # Queue-to-worker input drift fails without any inference or parent mutation.
        runtime.role_models = {'artifact': model()}
        queued = post(expected=202)
        inputs['input_version'] = '0' * 64
        service.agent_executor.finish()
        assert service.record(queued['id'])['status'] == 'failed'
        assert not runtime.role_models['artifact']._calls
        assert service.record_path(PARENT, agent=True).read_bytes() == protected_parent
        print('PASS actual temporary ASGI: consent/schema/scope/typed-origin/latest/stale/route/busy gates')
        print('PASS real compiled fake writer twice: fresh jobs/checkpoints, safe diagnostics, no parent/review mutation')
        print('PASS ordinary approval and partial gates unchanged; queued freshness rechecked before inference')
    finally:
        client.close()
        service.close()


def main():
    for relative in ('workbench/app.py', 'workbench/spark_codegen.py', 'workbench/candidate_retry.py',
                     'tests/check_candidate_writer_retry.py'):
        ast.parse((ROOT / relative).read_text(encoding='utf-8'))
    # Windows asyncio creates an internal loopback socketpair. Permit only that
    # exact construction, not arbitrary localhost/model/provider connections.
    guard = local()
    original_pair, original_connect = socket.socketpair, socket.socket.connect
    def internal_pair(*args, **kwargs):
        guard.creating_pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            guard.creating_pair = False
    def guarded_connect(self, address):
        if getattr(guard, 'creating_pair', False) and address[0] in {'127.0.0.1', '::1'}:
            return original_connect(self, address)
        return forbidden()
    with patch.object(socket, 'socketpair', internal_pair), \
            patch.object(socket.socket, 'connect', guarded_connect), patch.object(socket, 'create_connection', forbidden), \
            patch.dict('os.environ', {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}):
        with TemporaryDirectory(prefix='candidate-writer-retry-') as name:
            checks(Path(name))
    print('DIRECT_CANDIDATE_WRITER_RETRY=PASS; no live inference/restart/cloud/suite')


if __name__ == '__main__':
    main()