"""Tiny offline writer-only checks. No suite, live model, cloud or app restart."""
import ast
from contextlib import closing
import json
from pathlib import Path
import runpy
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from workbench.databricks_models import safe_failure_diagnostic, safe_workspace_path, retry_databricks_429
from workbench.spark_codegen import draft_approved_design, draft_agent_design


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = runpy.run_path(str(ROOT / 'tests' / 'test_spark_codegen.py'))


def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in direct writer diagnostics check')


def check_fields():
    original = ROOT / 'workbench' / 'spark_codegen.py'
    try:
        error = PermissionError(13, 'password=DO_NOT_PUBLISH', str(original))
        error.winerror = 5
        raise error
    except PermissionError as error:
        result = safe_failure_diagnostic(error, 'writer_model_handler')
        item = result['exceptions'][0]
        assert item['exception_class'] == 'PermissionError'
        assert (item['errno'], item['winerror']) == (13, 5)
        assert item['filename'] == 'workbench/spark_codegen.py'
        assert item['stack'][-1]['file'] == 'tests/check_writer_failure_diagnostics.py'
        assert set(item['stack'][-1]) == {'file', 'function', 'line'}
        assert str(original) not in json.dumps(result) and 'DO_NOT_PUBLISH' not in json.dumps(result)
    rename_error = PermissionError(13, 'hidden', str(original), 5, str(ROOT / 'workbench' / 'app.py'))
    assert safe_failure_diagnostic(rename_error, 'writer_graph')['exceptions'][0]['filename2'] == 'workbench/app.py'
    for path in (ROOT / '.env', ROOT / 'secrets' / 'value', ROOT / '.databrickscfg',
                 ROOT / '.ssh' / 'id_rsa', ROOT.parent / 'outside.sqlite',
                 'https://user:password@example.org/key', ROOT / 'logs' / 'token=private'):
        assert safe_workspace_path(path) is None
        assert safe_failure_diagnostic(PermissionError(13, 'hidden', str(path)), 'bad')['exceptions'][0]['filename'] is None
    # OSError repr drops filename even when the live exception has one.
    assert str(original) not in repr(PermissionError(13, 'denied', str(original)))
    try:
        try:
            raise PermissionError(13, 'secret text', str(original))
        except PermissionError:
            raise RuntimeError('private provider text') from None
    except RuntimeError as error:
        result = safe_failure_diagnostic(error, 'writer_model_handler')
        assert [x['exception_class'] for x in result['exceptions']] == ['RuntimeError', 'PermissionError']
        assert 'private provider' not in json.dumps(result)
    assert safe_failure_diagnostic(PermissionError(13, 'denied'), 'writer_graph')['exceptions'][0]['filename'] is None
    attempts = []
    def fail():
        attempts.append(1)
        raise PermissionError(13, 'denied')
    try:
        retry_databricks_429(fail, object(), Event())
    except PermissionError:
        pass
    assert len(attempts) == 1
    # Databricks serving 503 is transient, honors Retry-After: 0, and is
    # cancellable/unbounded rather than being converted into a failed draft.
    class ServiceUnavailable(Exception):
        status_code = 503
        response = type('Response', (), {'headers': {'retry-after': '0'}})()
    model = ChatOpenAI(base_url='https://adb.example/serving-endpoints',
                       api_key='offline-placeholder', max_retries=0)
    attempts = []
    progress = []
    def recover_503():
        attempts.append(1)
        if len(attempts) < 3:
            raise ServiceUnavailable('private provider text')
        return 'ok'
    assert retry_databricks_429(recover_503, model, Event(), progress.append) == 'ok'
    assert len(attempts) == 3
    assert [item['status_code'] for item in progress] == [503] * 5
    assert [item['event'] for item in progress] == [
        'model_retry_wait', 'model_retry_resuming', 'model_retry_wait',
        'model_retry_resuming', 'model_retry_succeeded']
    # Real Windows directory-open denial, scratch only. This is diagnostic
    # validation, NOT a reproduction of the historical writer's unknown cause.
    with TemporaryDirectory(prefix='writer-os-error-') as name:
        try:
            with Path(name).open('rb'):
                raise AssertionError('Directory unexpectedly opened as a file')
        except PermissionError as error:
            native = safe_failure_diagnostic(error, 'writer_tool')
            assert native['exceptions'][0]['errno'] == 13
            assert native['exceptions'][0]['filename'] is None
    print('PASS fields: OS filename/errno/winerror, traceback, chains, repr loss, redaction, no PermissionError retry')


class FailingChat(FIXTURES['ScriptedChat']):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.i:
            error = PermissionError(13, 'password=DO_NOT_PUBLISH', str(ROOT / 'workbench' / 'spark_codegen.py'))
            error.winerror = 5
            raise error
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def check_writer(root, *, candidate=False, progress_failure=False):
    service, record, _, _ = FIXTURES['context'].__wrapped__(root)
    route = FailingChat(responses=[FIXTURES['call']('read_file', {'file_path': '/CONTEXT.md'}, 'read'),
                                   AIMessage(content='unused')])
    service.runtime.role_models = {'artifact': route}
    public = {'id': 'source-run', 'consent': True, 'generate_code': True, 'status': 'running',
              **{key: record[key] for key in ('workflow_id', 'scenario', 'package_hash', 'input_version')}}
    service.record = lambda *args, **kwargs: public.copy()
    def update(value, **kwargs):
        if progress_failure and value.get('phase') == 'writer_model_failed':
            raise OSError(5, 'private progress write')
        public.update(value)
    service.update_record = update
    report = (draft_agent_design(service, record, agent_run_id='source-run', consent=True) if candidate
              else draft_approved_design(service, record, consent=True))
    assert report['status'] == 'blocked' and report['failure'] == 'draft_agent_failed'
    diagnostic = report['failure_diagnostic']
    assert diagnostic['operation'] == 'writer_model_handler'
    assert diagnostic['exceptions'][0]['exception_class'] == 'PermissionError'
    assert diagnostic['exceptions'][0]['filename'] == 'workbench/spark_codegen.py'
    assert diagnostic['last_safe_activity']['name'] == 'read_file'
    assert diagnostic['last_safe_activity']['workspace_relative_path'] == 'workbench/skills/spark_codegen/CONTEXT.md'
    assert report['budget_used']['model_calls'] == 2 and not report['artifact_hashes']
    if progress_failure:
        progress_diagnostic = (report['events'][-1].get('progress_write_diagnostic')
                               or report['events'][-1].get('progress_failure_diagnostic'))
        assert progress_diagnostic['exceptions'][0]['exception_class'] == 'OSError'
    manifest = root / report['relative_directory'] / 'manifest.json'
    assert json.loads(manifest.read_text())['failure_diagnostic'] == diagnostic
    assert 'DO_NOT_PUBLISH' not in manifest.read_text()
    checkpoint = manifest.with_name('checkpoint_spark_codegen.sqlite')
    with closing(sqlite3.connect(checkpoint.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        assert connection.execute("select count(*) from writes where channel='__error__'").fetchone()[0] == 1
    print('PASS compiled writer: candidate=' + str(candidate) + ', secondary_progress_failure=' + str(progress_failure))


def check_public_projection():
    tree = ast.parse((ROOT / 'workbench' / 'app.py').read_text(encoding='utf-8'))
    diagnostic = safe_failure_diagnostic(PermissionError(13, 'hidden'), 'writer_model_handler')
    draft = {'run_id': 'writer', 'relative_directory': 'generated-drafts/test', 'status': 'blocked',
             'failure': 'draft_agent_failed', 'failure_diagnostic': diagnostic}
    statement = next(node for node in ast.walk(tree) if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'draft_job'
        and any(arg.arg == 'manifest_sha256' for arg in node.value.keywords))
    job = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[statement], type_ignores=[])), '<public-projection>', 'exec'),
         {'draft_job': job, 'draft': draft, 'manifest_path': Path('unused'), '_digest': lambda path: 'hash'})
    assert job['output']['failure_diagnostic'] == diagnostic and job['output']['failure'] == 'draft_agent_failed'
    print('PASS exact app candidate-job projection retains safe failure diagnostics')


def check_success(root):
    service, record, model, _ = FIXTURES['context'].__wrapped__(root)
    service.runtime.role_models = {'artifact': FIXTURES['script'](model, record)}
    report = draft_approved_design(service, record, consent=True)
    assert report['status'] == 'generation_complete' and report['failure_diagnostic'] is None
    assert report['artifact_hashes'] and report['execution_allowed'] is False
    print('PASS successful compiled writer unchanged; no failure diagnostic or execution authority')


def main():
    with patch.object(socket.socket, 'connect', forbidden), patch.object(socket, 'create_connection', forbidden), \
            patch.dict('os.environ', {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}):
        check_fields()
        with TemporaryDirectory(prefix='writer-diagnostic-check-') as name:
            root = Path(name)
            for number, options in enumerate(({}, {'candidate': True}, {'candidate': True, 'progress_failure': True})):
                path = root / str(number)
                path.mkdir()
                check_writer(path, **options)
            success = root / 'success'
            success.mkdir()
            check_success(success)
        check_public_projection()
    print('DIRECT_WRITER_FAILURE_DIAGNOSTICS=PASS; temporary files cleaned; no live inference/cloud/restart')


if __name__ == '__main__':
    main()