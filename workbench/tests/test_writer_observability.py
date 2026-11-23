import json
from types import SimpleNamespace

import pytest

from workbench import spark_codegen as writer
from workbench.app import _candidate_writer_status


@pytest.fixture(autouse=True)
def isolated_logs(monkeypatch):
    monkeypatch.setattr(writer, '_WRITER_LOGS', {})


def service_for(root, job, identity=''):
    return SimpleNamespace(root=root, runtime=SimpleNamespace(identity_scope=identity),
                           record=lambda job_id: job if job_id == job['id'] else {})


def make_job(root, job_id='1' * 32, package='a' * 64, parent='2' * 32):
    job = {'id': job_id, 'kind': 'spark_draft', 'status': 'running',
           'package_hash': package, 'agent_run_id': parent, 'created_at': '2026-01-01'}
    directory = root / 'state' / 'jobs'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (job_id + '.json')).write_text(json.dumps(job), encoding='utf-8')
    return job


def test_additive_history_correlates_nested_validation_and_tool(tmp_path):
    job = make_job(tmp_path)
    service = service_for(tmp_path, job)
    tool_call, model_call = '3' * 32, '4' * 32
    with writer.writer_observation(service, job['id'], job['package_hash']):
        writer.writer_status_event('writer', 'started')
        writer.writer_status_event('tool', 'started', call_id=tool_call, tool_name='check_generation_code')
        writer.writer_status_event('validation', 'started', call_id=model_call,
                                   role='validation', endpoint='review-model')
        writer.writer_status_event('validation', 'completed', call_id=model_call,
                                   role='validation', endpoint='review-model')
        writer.writer_status_event('tool', 'completed', call_id=tool_call, tool_name='check_generation_code')
    result = _candidate_writer_status(tmp_path, job['agent_run_id'], job['package_hash'])
    history = result['writer_recent_events']
    assert [row['status'] for row in history['events']] == [
        'started', 'started', 'started', 'completed', 'completed']
    assert history['events'][1]['call_id'] == history['events'][4]['call_id'] == tool_call
    assert history['events'][2]['call_id'] == history['events'][3]['call_id'] == model_call
    assert all(row['timestamp'] for row in history['events'])
    assert result['status'] == 'running'
    assert json.loads((tmp_path / 'state' / 'jobs' / (job['id'] + '.json')).read_text()) == job


def test_isolation_rejects_other_roots_packages_jobs_and_obo(tmp_path):
    job = make_job(tmp_path)
    service = service_for(tmp_path, job)
    with writer.writer_observation(service, job['id'], job['package_hash']):
        writer.writer_status_event('writer', 'started')
    assert writer.writer_recent_events(tmp_path / 'other', job['package_hash'], job['id']) is None
    assert writer.writer_recent_events(tmp_path, 'b' * 64, job['id']) is None
    assert writer.writer_recent_events(tmp_path, job['package_hash'], '5' * 32) is None
    with writer.writer_observation(service_for(tmp_path, job, 'workspace:user'), job['id'], job['package_hash']):
        writer.writer_status_event('writer', 'failed')
    assert len(writer.writer_recent_events(tmp_path, job['package_hash'], job['id'])['events']) == 1
    with writer.writer_observation(service, '5' * 32, job['package_hash']):
        writer.writer_status_event('writer', 'started')
    assert writer.writer_recent_events(tmp_path, job['package_hash'], '5' * 32) is None


def test_fail_closed_fields_and_bounded_append_only_log(tmp_path, monkeypatch):
    job = make_job(tmp_path)
    service = service_for(tmp_path, job)
    monkeypatch.setattr(writer, '_WRITER_LOG_MAX_EVENTS', 2)
    secret = 'token=super-secret-code-or-prompt'
    with writer.writer_observation(service, job['id'], job['package_hash']):
        writer.writer_status_event('model', 'started', call_id=secret, role='artifact', endpoint=secret,
                                   tool_name=secret)
        writer.writer_status_event('model', 'failed', role='artifact', failure=ValueError(secret))
        writer.writer_status_event('writer', 'completed')
    result = writer.writer_recent_events(tmp_path, job['package_hash'], job['id'])
    assert result['retained'] == 2 and result['omitted'] == 1
    assert result['events'][1]['failure_class'] == 'ValueError'
    assert secret not in json.dumps(result)
    assert set(result['events'][0]) == {'timestamp', 'stage', 'status', 'role'}
    result['events'][0]['status'] = 'corrupted'
    assert writer.writer_recent_events(tmp_path, job['package_hash'], job['id'])['events'][0]['status'] == 'started'


def test_status_picks_exact_latest_job_not_older_history(tmp_path):
    older = make_job(tmp_path)
    with writer.writer_observation(service_for(tmp_path, older), older['id'], older['package_hash']):
        writer.writer_status_event('writer', 'started')
    latest = make_job(tmp_path, job_id='f' * 32)
    result = _candidate_writer_status(tmp_path, older['agent_run_id'], older['package_hash'])
    assert result['id'] == latest['id']
    assert 'writer_recent_events' not in result