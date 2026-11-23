"""Offline contracts for the opt-in async operation API, with real Delta envelopes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import uuid4

from databricks.sdk.errors import Unauthenticated
import pytest

from workbench import delta_state as ds
from workbench.databricks_models import AuthenticationUnavailable
from workbench.delta_runtime import RequestStateStore
from workbench.hosted_operations import API, MAX_RESULT_BYTES, RECOVERY, HostedOperations, _progress, _valid_progress, current_progress
from workbench.hosted_storage import provision
from workbench.tests.test_hosted import (
    Backend, HOST, TOKEN_A, TOKEN_A2, TOKEN_B, browser, create,
    offline,  # noqa: F401: install the existing no-network/no-CLI fixture
)


@pytest.fixture
def estate():
    backend = Backend()
    # Seed tables through actual browser project creation, not ensure_tables in
    # the operation path. Reuse the production resolver/provisioning contracts.
    with browser(backend) as client:
        project = create(client)
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    service = HostedOperations("warehouse", "process-original")
    yield SimpleNamespace(backend=backend, project=project, identity=identity, remote=remote, service=service)
    service.close(wait=True)


def request(estate, operation=None, **changes):
    return {"id": operation or uuid4().hex, "method": "POST",
            "path": f"/api/projects/{estate.project}/analyze", "body": {}, **changes}


def post(estate, body, execute=lambda *args: (200, {"ok": True}), *, headers=None, service=None):
    return (service or estate.service).handle(
        estate.identity, estate.remote, "POST", API, "",
        headers or {"x-forwarded-access-token": TOKEN_A}, body, execute)


def get(estate, operation, *, service=None, identity=None, remote=None):
    return (service or estate.service).handle(
        identity or estate.identity, remote or estate.remote,
        "GET", API + "/" + operation, "", {}, b"", None)


def rows(estate, operation):
    return [row for name, items in estate.backend.rows.items() if name.endswith(".jobs")
            for row in items if row["context_id"] == "hosted-operation:" + operation]


def test_running_progress_producer_and_strict_unknown_total_contract():
    canonical = _progress("running")
    assert canonical == {"stage": "running", "completed": 0, "total": None, "unit": None}
    assert _valid_progress("running", canonical)
    assert _valid_progress("running", _progress("running", stage="audit_restore"))
    for invalid in ({**canonical, "completed": 1}, {**canonical, "completed": True},
                    {**canonical, "unit": "bytes"}, {**canonical, "stage": "untrusted"},
                    {**canonical, "total": -1}, {**canonical, "extra": "value"}):
        assert not _valid_progress("running", invalid)


def test_spark_draft_route_uses_existing_operation_journal_without_execution(estate):
    body = request(estate, path='/api/redesigns/' + 'a' * 64 + '/spark-drafts',
                   body={'version': 1, 'consent': True})
    calls = []
    status, _ = post(estate, body, lambda *args: (calls.append(args) or (202, {'status': 'queued'})))
    assert status == 202
    estate.service.close(wait=True)
    assert len(calls) == 1 and calls[0][1] == body['path']
    assert get(estate, body['id'])[1]['status'] == 'completed'


def test_cohort_design_journal_requires_exact_id_and_never_replays(estate):
    operation = uuid4().hex
    body = request(estate, operation, path='/api/cohorts/' + 'a' * 32 + '/workflows/' + 'b' * 64 + '/design',
                   body={'operation_id': operation, 'consent': True})
    calls = []
    assert post(estate, {**body, 'body': {'operation_id': 'c' * 32, 'consent': True}})[0] == 400
    status, _ = post(estate, body, lambda *args: (calls.append(args) or (202, {'id': 'run'})))
    assert status == 202
    estate.service.close(wait=True)
    assert post(estate, body, lambda *args: pytest.fail('must not replay'))[0] == 200
    assert len(calls) == 1 and get(estate, operation)[1]['status'] == 'completed'


def test_cohort_server_worker_commits_each_step_and_resumes_without_replay(estate):
    from workbench.cohort_review import operation_id
    package, cohort = 'a' * 64, 'b' * 32
    workflows = ['c' * 64, 'd' * 64]
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'workflows': [
        {'workflow_id': wid, 'operation_id': operation_id(package, cohort, wid),
         'status': 'awaiting_individual_review'} for wid in workflows]}
    calls = []
    def execute(method, path, query, headers, body):
        if method == 'GET':
            return 200, deepcopy(saved)
        wid = path.split('/')[-2]
        calls.append(wid)
        item = next(row for row in saved['workflows'] if row['workflow_id'] == wid)
        item.update(status='design_ready', agent_run_id=('1' if wid == workflows[0] else '2') * 32)
        return 202, {'id': item['agent_run_id'], 'status': 'queued'}
    store = estate.service._store(estate.identity, estate.remote)
    request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/design', 'query': '', 'body': {'consent': True}}
    parent = {**request, 'id': uuid4().hex}
    assert post(estate, parent, execute)[0] == 202
    estate.service.close(wait=True)
    result = get(estate, parent['id'])[1]['result']
    assert result['status'] == 'designs_ready_for_review'
    assert calls == workflows
    for wid in workflows:
        assert get(estate, operation_id(package, cohort, wid))[1]['status'] == 'completed'
    restarted = HostedOperations('warehouse', 'process-restarted')
    try:
        assert restarted._cohort_work(store, request, {}, (), execute)[1]['status'] == 'designs_ready_for_review'
        assert calls == workflows
    finally:
        restarted.close()


def test_cohort_worker_uncertain_claim_and_auth_failure_are_not_replayed(estate):
    from workbench.cohort_review import operation_id
    package, cohort, wid = 'a' * 64, 'b' * 32, 'c' * 64
    op = operation_id(package, cohort, wid)
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'workflows': [
        {'workflow_id': wid, 'operation_id': op, 'status': 'awaiting_individual_review'}]}
    store = estate.service._store(estate.identity, estate.remote)
    request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/design', 'query': '', 'body': {'consent': True}}
    calls = []
    def denied(method, path, query, headers, body):
        if method == 'GET':
            return 200, deepcopy(saved)
        calls.append(path)
        return 401, {'detail': 'denied'}
    with pytest.raises(AuthenticationUnavailable):
        estate.service._cohort_work(store, request, {}, (), denied)
    assert get(estate, op)[1]['status'] == 'authentication_required'
    restarted = HostedOperations('warehouse', 'process-restarted')
    try:
        result = restarted._cohort_work(store, request, {}, (), denied)[1]
        assert result['status'] == 'recovery_required' and len(calls) == 1
    finally:
        restarted.close()
    for consent in (False, 1, 'true'):
        body = {'id': uuid4().hex, 'method': 'POST', 'path': request['path'], 'body': {'consent': consent}}
        assert post(estate, body, denied)[0] == 400


def test_cohort_drafting_requires_approval_and_never_replays_published_steps(estate):
    package, cohort, wid, design_id = 'a' * 64, 'b' * 32, 'c' * 64, 'd' * 64
    job_id, bundle_id = 'e' * 32, 'f' * 64
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'request': {'scenario': 'bronze'},
             'workflows': [{'workflow_id': wid, 'status': 'design_ready',
                            'redesign_refs': [{'id': design_id, 'version': 1}]}]}
    design = {'id': design_id, 'version': 1, 'package_hash': package, 'workflow_id': wid,
              'scenario': 'bronze', 'stale': False, 'outline_drafting_allowed': False}
    job = {'id': job_id, 'package_hash': package, 'workflow_id': wid, 'design_id': design_id,
           'design_version': 1, 'status': 'completed', 'output': {'status': 'generation_complete'}}
    verification = {'code': 200, 'value': {'bundle_id': bundle_id, 'status': 'draft_unverified',
        'receipt': {'job_id': job_id, 'package_hash': package, 'workflow_id': wid,
                    'design_id': design_id, 'design_version': 1, 'workspace_root': '/Workspace/Users/test'},
        'semantic_parity': 'not_established', 'execution_allowed': False, 'deployment_allowed': False}}
    calls = []
    def execute(method, path, query, headers, body):
        if method == 'GET':
            if path.startswith('/api/cohorts/'): return 200, deepcopy(saved)
            if path.startswith('/api/redesigns/'): return 200, deepcopy(design)
            if path == '/api/jobs/' + job_id: return 200, deepcopy(job)
            if path == '/api/jobs/' + job_id + '/native-bundles/' + bundle_id:
                return verification['code'], deepcopy(verification['value'])
        calls.append((path, body))
        if path.endswith('/spark-drafts'): return 202, {'id': job_id, 'status': 'queued'}
        if path.endswith('/native-bundles'): return 200, {'bundle_id': bundle_id}
        pytest.fail('unexpected request')
    store = estate.service._store(estate.identity, estate.remote)
    request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/draft', 'query': '',
               'body': {'consent': True, 'workspace_root': '/Workspace/Users/test'}}
    assert estate.service._cohort_draft(store, request, {}, (), execute)[1]['status'] == 'review_required'
    assert not calls
    design['outline_drafting_allowed'] = True
    result = estate.service._cohort_draft(store, request, {}, (), execute)[1]
    assert result['status'] == 'drafts_published_unverified' and len(calls) == 2
    assert result['completed'] == [{'workflow_id': wid, 'job_id': job_id, 'bundle_id': bundle_id}]
    assert result['semantic_parity'] == 'not_established' and not result['deployment_allowed']
    restarted = HostedOperations('warehouse', 'process-restarted')
    try:
        assert restarted._cohort_draft(store, request, {}, (), execute)[1] == result
        assert len(calls) == 2
        verification['code'] = 409
        with pytest.raises(ValueError, match='Drafting evidence is unavailable'):
            restarted._cohort_draft(store, request, {}, (), execute)
        verification['code'] = 200
        for field, wrong in [('job_id', '0' * 32), ('package_hash', '0' * 64),
                             ('workflow_id', '0' * 64), ('design_id', '0' * 64),
                             ('design_version', 2), ('workspace_root', '/Workspace/other')]:
            original = verification['value']['receipt'][field]
            verification['value']['receipt'][field] = wrong
            with pytest.raises(ValueError, match='scope or integrity'):
                restarted._cohort_draft(store, request, {}, (), execute)
            verification['value']['receipt'][field] = original
        assert len(calls) == 2
    finally:
        restarted.close()
    invalid = {'id': uuid4().hex, 'method': 'POST', 'path': request['path'],
               'body': {'consent': True, 'workspace_root': '/Workspace/../escape'}}
    assert post(estate, invalid, execute)[0] == 400


@pytest.mark.parametrize('bad', ['unavailable', 'wrong_id', 'wrong_status', 'missing_receipt',
                                 'parity_claim', 'execution_allowed', 'deployment_allowed'])
def test_cohort_draft_rechecks_committed_bundle_before_reporting_success(estate, bad):
    package, cohort, wid, design_id = 'a' * 64, 'b' * 32, 'c' * 64, 'd' * 64
    job_id, bundle_id = 'e' * 32, 'f' * 64
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'request': {'scenario': 'bronze'},
             'workflows': [{'workflow_id': wid, 'status': 'design_ready',
                            'redesign_refs': [{'id': design_id, 'version': 1}]}]}
    design = {'id': design_id, 'version': 1, 'package_hash': package, 'workflow_id': wid,
              'scenario': 'bronze', 'stale': False, 'outline_drafting_allowed': True}
    job = {'id': job_id, 'package_hash': package, 'workflow_id': wid, 'design_id': design_id,
           'design_version': 1, 'status': 'completed', 'output': {'status': 'generation_complete'}}
    verified = {'bundle_id': bundle_id, 'status': 'draft_unverified',
                'receipt': {'job_id': job_id, 'package_hash': package, 'workflow_id': wid,
                            'design_id': design_id, 'design_version': 1, 'workspace_root': '/Workspace/Users/test'},
                'semantic_parity': 'not_established', 'execution_allowed': False, 'deployment_allowed': False}
    if bad == 'wrong_id': verified['bundle_id'] = '0' * 64
    if bad == 'wrong_status': verified['status'] = 'needs_reconciliation'
    if bad == 'missing_receipt': verified.pop('receipt')
    if bad == 'parity_claim': verified['semantic_parity'] = 'verified'
    if bad == 'execution_allowed': verified['execution_allowed'] = True
    if bad == 'deployment_allowed': verified['deployment_allowed'] = True
    calls = []

    def execute(method, path, query, headers, body):
        if method == 'GET':
            if path == '/api/cohorts/' + cohort: return 200, deepcopy(saved)
            if path == '/api/redesigns/' + design_id: return 200, deepcopy(design)
            if path == '/api/jobs/' + job_id: return 200, deepcopy(job)
            if path == '/api/jobs/' + job_id + '/native-bundles/' + bundle_id:
                return (409, {'detail': 'Unavailable'}) if bad == 'unavailable' else (200, deepcopy(verified))
        calls.append(path)
        if path.endswith('/spark-drafts'): return 202, {'id': job_id}
        if path.endswith('/native-bundles'): return 200, {'bundle_id': bundle_id}
        pytest.fail('unexpected request')

    request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/draft', 'query': '',
               'body': {'consent': True, 'workspace_root': '/Workspace/Users/test'}}
    store = estate.service._store(estate.identity, estate.remote)
    for _ in range(2):
        with pytest.raises(ValueError):
            estate.service._cohort_draft(store, request, {}, (), execute)
    assert len(calls) == 2  # Neither writer nor publication is replayed.


def test_cohort_restart_does_not_replay_running_claim(estate):
    from workbench.cohort_review import operation_id
    package, cohort, wid = 'a' * 64, 'b' * 32, 'c' * 64
    op = operation_id(package, cohort, wid)
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'workflows': [
        {'workflow_id': wid, 'operation_id': op, 'status': 'awaiting_individual_review'}]}
    store = estate.service._store(estate.identity, estate.remote)
    path = f'/api/cohorts/{cohort}/workflows/{wid}/design'
    _, _, fingerprint = estate.service._request({'id': op, 'method': 'POST', 'path': path,
        'body': {'operation_id': op, 'consent': True}}, ())
    queued = {'operation_id': op, 'process_id': estate.service.process_id, 'fingerprint': fingerprint,
              'status': 'queued', 'progress': _progress('queued')}
    running = {**queued, 'status': 'running', 'progress': _progress('running')}
    estate.service._append(store, op, [], queued)
    estate.service._append(store, op, [queued], running)
    restarted = HostedOperations('warehouse', 'process-restarted')
    try:
        def execute(method, path, query, headers, body):
            if method == 'GET': return 200, deepcopy(saved)
            pytest.fail('A claimed workflow must not be replayed')
        request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/design', 'query': '', 'body': {'consent': True}}
        assert restarted._cohort_work(store, request, {}, (), execute)[1]['status'] == 'recovery_required'
        assert get(estate, op, service=restarted)[1]['status'] == 'interrupted'
    finally:
        restarted.close()


def test_cohort_worker_stops_if_inventory_changes_after_claim(estate):
    from workbench.cohort_review import operation_id
    package, cohort = 'a' * 64, 'b' * 32
    ids = ['c' * 64, 'd' * 64]
    saved = {'id': cohort, 'package_hash': package, 'stale': False, 'workflows': [
        {'workflow_id': wid, 'operation_id': operation_id(package, cohort, wid),
         'status': 'awaiting_individual_review'} for wid in ids]}
    calls = []
    def execute(method, path, query, headers, body):
        if method == 'GET':
            return 200, deepcopy(saved)
        calls.append(path)
        saved['workflows'][0].update(status='design_ready', agent_run_id='1' * 32)
        saved['workflows'].pop()
        return 202, {'id': '1' * 32}
    store = estate.service._store(estate.identity, estate.remote)
    request = {'method': 'POST', 'path': '/api/cohorts/' + cohort + '/design', 'query': '', 'body': {'consent': True}}
    outcome = estate.service._cohort_work(store, request, {}, (), execute)[1]
    assert outcome['status'] == 'recovery_required' and len(calls) == 1
    assert get(estate, operation_id(package, cohort, ids[0]))[1]['status'] == 'failed'
    assert get(estate, operation_id(package, cohort, ids[1]))[0] == 404


def test_native_bundle_publication_is_journaled_and_not_replayed(estate):
    body = request(estate, path='/api/jobs/' + 'a' * 32 + '/native-bundles',
                   body={'workspace_root': '/Workspace/Users/test', 'consent': True})
    calls = []
    assert post(estate, body, lambda *args: (calls.append(args) or (200, {'bundle_id': 'b' * 64})))[0] == 202
    estate.service.close(wait=True)
    assert post(estate, body, lambda *args: pytest.fail('must not replay'))[0] == 200
    assert len(calls) == 1 and get(estate, body['id'])[1]['result']['bundle_id'] == 'b' * 64
    assert all(row['user_id'] == estate.identity.user_id for row in rows(estate, body['id']))


def test_overlapping_object_reads_preserve_immutable_progress_and_verified_results(estate):
    rendezvous = Barrier(2)
    stages = ['metadata_restore', 'preparing_package', 'querying_inventory']

    def execute(method, path, query, headers, body):
        assert method == 'GET'
        rendezvous.wait(timeout=10)
        reporter = current_progress()
        assert callable(reporter)
        for stage in stages:
            reporter(stage)
        return 200, {'node': {'id': path.rsplit('/', 1)[-1]}}

    requests = [request(estate, method='GET', path='/api/object/' + name)
                for name in ['WIL_NET_DIS', 'WIL_NET_INS']]
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(lambda body: post(estate, body, execute), requests))
    assert all(status == 202 for status, _ in accepted)
    estate.service.close(wait=True)
    for body in requests:
        code, completed = get(estate, body['id'])
        assert code == 200 and completed['status'] == 'completed'
        assert completed['result']['node']['id'] == body['path'].rsplit('/', 1)[-1]
        history = sorted(rows(estate, body['id']), key=lambda row: row['sequence'])
        assert [json.loads(row['payload'])['progress']['stage'] for row in history] == [
            'queued', 'running', *stages, 'committed']
        assert [row['sequence'] for row in history] == list(range(1, 7))
        assert all(row['user_id'] == estate.identity.user_id for row in history)


def test_async_returns_before_work_finishes_and_poll_does_not_wait_for_execute(estate):
    entered, release = Event(), Event()
    body = request(estate)
    calls = []

    def execute(method, path, query, headers, payload):
        calls.append((method, path, query, headers, payload))
        entered.set()
        assert release.wait(15)
        return 201, {"job": {"id": "public-job"}, "items": [1, True, None]}

    try:
        status, accepted = post(estate, body, execute)
        assert status == 202
        assert accepted == {"operation_id": body["id"], "process_id": "process-original", "status": "queued",
                            "progress": {"stage": "queued", "completed": 0, "total": None, "unit": None}}
        assert entered.wait(10)
        # A separate thread with a bounded wait proves GET is not behind execute.
        with ThreadPoolExecutor(max_workers=1) as pool:
            status, running = pool.submit(get, estate, body["id"]).result(timeout=3)
        assert status == 200 and running["status"] == "running"
        assert running["progress"] == {"stage": "running", "completed": 0, "total": None, "unit": None}
        assert len(rows(estate, body["id"])) == 2
    finally:
        release.set()
        estate.service.close(wait=True)
    status, completed = get(estate, body["id"])
    assert status == 200 and completed["status"] == "completed"
    assert completed["progress"] == {"stage": "committed", "completed": 1, "total": 1, "unit": "operation"}
    assert completed["status_code"] == 201
    assert completed["result"] == {"job": {"id": "public-job"}, "items": [1, True, None]}
    assert calls == [("POST", body["path"], "", {}, {})]
    assert len(rows(estate, body["id"])) == 3


def test_duplicate_id_concurrent_requests_execute_once_and_different_request_conflicts(estate):
    entered, release = Event(), Event()
    counter, guard = [], Lock()
    body = request(estate)

    def execute(*args):
        with guard:
            counter.append(1)
        entered.set()
        assert release.wait(15)
        return 200, {"ok": True}

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: post(estate, body, execute), range(6)))
        assert all(status == 202 for status, _ in responses)
        assert entered.wait(10)
        assert post(estate, {**body, "body": {"different": True}}, execute)[0] == 409
        assert counter == [1]
    finally:
        release.set()
        estate.service.close()
    assert post(estate, body, execute)[0] == 200
    assert counter == [1]
    assert len(rows(estate, body["id"])) == 3


def test_wrong_user_and_remote_namespace_cannot_read_or_reuse_payload(estate):
    body = request(estate)
    assert post(estate, body, lambda *args: (200, {"alice_only": True}))[0] == 202
    estate.service.close()
    with browser(estate.backend) as client:
        create(client, "Bob", TOKEN_B)
    bob = estate.backend.resolve(HOST, TOKEN_B, trusted_proxy=True, deployment_host=HOST)
    remote = provision(bob, "orion_dev_")
    assert get(estate, body["id"], identity=bob, remote=remote) == (404, {"detail": "Operation not found."})
    wrong = get(estate, body["id"], identity=bob)
    assert wrong[0] == 503 and "alice_only" not in json.dumps(wrong)
    # Even a claimed Alice user with Bob's authenticated client fails closed.
    impostor = SimpleNamespace(mode="obo", host=HOST, user_id=estate.identity.user_id, client=bob.client)
    assert get(estate, body["id"], identity=impostor)[0] == 503


@pytest.mark.parametrize("state", ["queued", "running"])
def test_process_restart_returns_observed_interruption_without_writing_or_replaying(estate, state):
    body = request(estate)
    store = estate.service._store(estate.identity, estate.remote)
    _, _, fingerprint = estate.service._request(body, ())
    accepted = {"operation_id": body["id"], "process_id": "process-original", "fingerprint": fingerprint,
                "status": "queued", "progress": {"stage": "queued", "completed": 0, "total": None, "unit": None}}
    estate.service._append(store, body["id"], [], accepted)
    if state == "running":
        estate.service._append(store, body["id"], [accepted], {
            **accepted, "status": "running", "progress": {"stage": "running", "completed": 0, "total": None, "unit": None}})
    snapshot = deepcopy(estate.backend.rows)
    restarted = HostedOperations("warehouse", "process-new")
    try:
        code, observed = get(estate, body["id"], service=restarted)
        assert code == 200 and observed["status"] == "interrupted"
        assert observed["process_id"] == "process-original" and observed["recovery"] == RECOVERY
        assert post(estate, body, lambda *a: pytest.fail("No replay"), service=restarted) == (200, observed)
        assert estate.backend.rows == snapshot
    finally:
        restarted.close()


def test_completed_result_survives_restart_and_refreshed_identity(estate):
    body = request(estate)
    post(estate, body, lambda *a: (202, {"run_id": "existing-public-run"}))
    estate.service.close()
    refreshed = estate.backend.resolve(HOST, TOKEN_A2, trusted_proxy=True, deployment_host=HOST)
    restarted = HostedOperations("warehouse", "new-process")
    try:
        code, public = get(estate, body["id"], service=restarted, identity=refreshed)
        assert code == 200 and public["status"] == "completed"
        assert public["result"] == {"run_id": "existing-public-run"}
        assert TOKEN_A not in json.dumps(public) and TOKEN_A2 not in json.dumps(public)
    finally:
        restarted.close()


@pytest.mark.parametrize("corruption", ["checksum", "record_id", "envelope", "duplicate", "gap", "overflow", "process", "payload"])
def test_corrupt_rows_fail_closed(estate, corruption):
    body = request(estate)
    post(estate, body)
    estate.service.close()
    journal = rows(estate, body["id"])
    latest = max(journal, key=lambda row: row["sequence"])
    table = next(items for name, items in estate.backend.rows.items() if name.endswith(".jobs") and latest in items)
    if corruption == "checksum":
        latest["content_hash"] = "0" * 64
    elif corruption == "record_id":
        latest["record_id"] = "0" * 64
        latest["content_hash"] = ds._content_hash(latest)
    elif corruption == "envelope":
        latest["source_path"] = "wrong"
        latest["content_hash"] = ds._content_hash(latest)
    elif corruption == "duplicate":
        table.append(deepcopy(latest))
    elif corruption == "gap":
        table.remove(next(row for row in journal if row["sequence"] == 2))
    elif corruption == "overflow":
        table.extend(deepcopy(latest) for _ in range(20))
    else:
        payload = json.loads(latest["payload"])
        if corruption == "process":
            payload["process_id"] = "different-process"
        else:
            payload["result"] = {"access_token": "never-public"}
        latest["payload"] = ds._json(payload)
        latest["content_hash"] = ds._content_hash(latest)
    status, public = get(estate, body["id"])
    assert status == 503
    assert "never-public" not in json.dumps(public)


@pytest.mark.parametrize("mode", ["dropped", "corrupt", "exception"])
def test_accepted_append_requires_verified_readback_before_submit(estate, monkeypatch, mode):
    original = RequestStateStore.upsert_records

    def write(store, table, records, **kwargs):
        if records[0]["source_table"] != "operations":
            return original(store, table, records, **kwargs)
        if mode == "exception":
            raise RuntimeError("secret upstream " + TOKEN_A)
        if mode == "dropped":
            return 1
        result = original(store, table, records, **kwargs)
        for row in rows(estate, json.loads(records[0]["payload"])["operation_id"]):
            row["content_hash"] = "0" * 64
        return result

    monkeypatch.setattr(RequestStateStore, "upsert_records", write)
    body = request(estate)
    status, public = post(estate, body, lambda *a: pytest.fail("Unverified acceptance must not execute"))
    assert status == 503 and TOKEN_A not in json.dumps(public)
    # Capacity is returned even on uncertain persistence.
    assert estate.service._slots.acquire(blocking=False)
    estate.service._slots.release()


@pytest.mark.parametrize("action", ["raise", "http", "token", "opaque", "oversize", "nan"])
def test_safe_failure_never_persists_or_returns_credentials(estate, action):
    body = request(estate)

    def execute(*args):
        if action == "raise":
            raise RuntimeError("sensitive upstream " + TOKEN_A)
        if action == "http":
            return 500, {"detail": TOKEN_A}
        if action == "token":
            return 200, {"token": "credential-value"}
        if action == "opaque":
            return 200, {"detail": TOKEN_A}
        if action == "oversize":
            return 200, {"detail": "a" * MAX_RESULT_BYTES}
        return 200, {"value": float("nan")}

    assert post(estate, body, execute, headers={"X-Workbench-Request": "1"})[0] == 202
    estate.service.close()
    code, public = get(estate, body["id"])
    assert code == 200 and public["status"] == "failed"
    assert public["error"]["detail"] == "Hosted operation failed."
    assert TOKEN_A not in json.dumps(public)
    for row in rows(estate, body["id"]):
        ds._safe(row)
        assert TOKEN_A not in ds._json(row)


@pytest.mark.parametrize("failure", ["custom", "sdk", "status"])
def test_authentication_required_never_falls_back(estate, failure):
    body = request(estate)

    def execute(*args):
        if failure == "custom":
            raise AuthenticationUnavailable(TOKEN_A)
        if failure == "sdk":
            raise Unauthenticated(TOKEN_A)
        return 401, {"detail": TOKEN_A}

    post(estate, body, execute)
    estate.service.close()
    status, public = get(estate, body["id"])
    assert status == 200 and public["status"] == "authentication_required"
    assert TOKEN_A not in json.dumps(public)


def test_capacity_precedes_durable_acceptance_and_close_drains_queued_work(estate):
    entered, release = Event(), Event()
    calls = []

    def execute(*args):
        entered.set()
        assert release.wait(15)
        calls.append(1)
        return 200, {"ok": True}

    try:
        for _ in range(8):
            assert post(estate, request(estate), execute)[0] == 202
        assert entered.wait(10)
        denied = request(estate)
        assert post(estate, denied, execute)[0] == 429
        assert not rows(estate, denied["id"])
    finally:
        release.set()
        estate.service.close(wait=True)
    assert len(calls) == 8
    assert post(estate, request(estate))[0] == 503


@pytest.mark.parametrize("path", ["/api/agents/run", "/api/loops/run", "project"])
def test_real_action_allowlist_query_and_json_bytes(estate, path):
    path = f"/api/projects/{estate.project}/analyze" if path == "project" else path
    body = request(estate, path=path, query="project_id=abc&flag=1", body={"workflow_id": "W"})
    observed = []
    status, _ = post(estate, json.dumps(body).encode(), lambda *args: (observed.append(args) or (200, {"ok": True})))
    assert status == 202
    estate.service.close()
    assert observed == [("POST", path, body["query"], {}, body["body"])]


@pytest.mark.parametrize("change", [
    {"id": "invalid"}, {"method": "GET"}, {"path": "/api/agent-loops/run"},
    {"path": "/api/projects/../analyze"}, {"path": "/api/agents/run?x=1"},
    {"body": []}, {"extra": True}, {"body": {"password": "hidden"}},
    {"query": "access_token=hidden"}, {"query": "access_token=hidden&access_token="},
    {"query": "x=" + TOKEN_A}, {"query": "?x=1"}, {"query": "x=\n"},
    {"body": {"opaque": TOKEN_A}}, {"body": {"large": "a" * 33000}},
])
def test_invalid_or_unsafe_request_has_no_durable_acceptance(estate, change):
    snapshot = deepcopy(estate.backend.rows)
    status, public = post(estate, request(estate, **change), lambda *a: pytest.fail("Invalid action executed"))
    assert status == 400
    assert TOKEN_A not in json.dumps(public)
    assert estate.backend.rows == snapshot


@pytest.mark.parametrize("body", [b'{"id":"a","id":"b"}', b'{"value":NaN}', b"not json", b"x" * 33000])
def test_strict_bounded_json(estate, body):
    assert post(estate, body)[0] == 400


def test_journal_reads_are_bounded_and_do_not_create_tables(estate, monkeypatch):
    limits = []
    original = RequestStateStore.read_table

    def read(store, *args, **kwargs):
        limits.append(kwargs["limit"])
        return original(store, *args, **kwargs)

    monkeypatch.setattr(RequestStateStore, "read_table", read)
    monkeypatch.setattr(RequestStateStore, "ensure_tables", lambda *a: pytest.fail("No table provisioning"))
    estate.backend.calls.clear()
    body = request(estate)
    post(estate, body)
    estate.service.close()
    assert get(estate, body["id"])[0] == 200
    assert limits and set(limits) == {50}
    assert not any(action == "sql" and sql.startswith("CREATE TABLE") for _, action, sql in estate.backend.calls)
    for row in rows(estate, body["id"]):
        assert row["migration_id"] == row["context_id"] == "hosted-operation:" + body["id"]
        assert row["project_id"] == "__operations__" and row["package_hash"] == "__none__"
        assert row["kind"] == "process-original" and row["source_key"] == str(row["sequence"])


def test_unknown_routes_and_missing_operation(estate):
    for method, path in [("GET", API), ("POST", API + "/abc"), ("GET", "/api/health"), ("POST", "/api/agents/run")]:
        assert estate.service.handle(None, None, method, path, "", {}, b"", None) is None
    assert get(estate, uuid4().hex)[0] == 404


def test_executor_submission_failure_is_durably_failed_and_never_executed(estate, monkeypatch):
    def submit(*args):
        raise RuntimeError(TOKEN_A)

    monkeypatch.setattr(estate.service._executor, "submit", submit)
    body = request(estate)
    status, public = post(estate, body, lambda *a: pytest.fail("Submission failed"))
    assert status == 503 and public["status"] == "failed"
    assert TOKEN_A not in json.dumps(public)
    assert get(estate, body["id"])[1]["status"] == "failed"


def test_mutating_original_body_does_not_change_worker_arguments(estate):
    entered, release = Event(), Event()
    observed = []

    def blocking(*args):
        entered.set()
        assert release.wait(15)
        return 200, {}

    try:
        # Occupy both workers so a third request is definitely queued.
        post(estate, request(estate), blocking)
        post(estate, request(estate), blocking)
        assert entered.wait(10)
        body = request(estate, body={"nested": ["original"]})
        assert post(estate, body, lambda *args: (observed.append(args[-1]) or (200, {})))[0] == 202
        body["body"]["nested"][0] = "mutated"
    finally:
        release.set()
        estate.service.close()
    assert observed == [{"nested": ["original"]}]


def test_running_write_failure_prevents_action_execution(estate, monkeypatch):
    original = RequestStateStore.upsert_records

    def write(store, table, records, **kwargs):
        if records[0]["source_table"] == "operations" and records[0]["status"] == "running":
            raise RuntimeError(TOKEN_A)
        return original(store, table, records, **kwargs)

    monkeypatch.setattr(RequestStateStore, "upsert_records", write)
    body = request(estate)
    assert post(estate, body, lambda *a: pytest.fail("Running must be durable before execute"))[0] == 202
    estate.service.close()
    status, observed = get(estate, body["id"])
    assert status == 200 and observed["status"] == "queued"
    assert len(rows(estate, body["id"])) == 1


def test_failed_completion_write_does_not_claim_committed_result(estate, monkeypatch):
    original = RequestStateStore.upsert_records
    calls = []

    def write(store, table, records, **kwargs):
        if records[0]["source_table"] == "operations" and records[0]["status"] == "completed":
            raise RuntimeError(TOKEN_A)
        return original(store, table, records, **kwargs)

    monkeypatch.setattr(RequestStateStore, "upsert_records", write)
    body = request(estate)
    post(estate, body, lambda *a: (calls.append(1) or (200, {"public_result": True})))
    estate.service.close()
    status, observed = get(estate, body["id"])
    assert status == 200 and observed["status"] == "running"
    assert "result" not in observed and calls == [1]
    assert post(estate, body, lambda *a: pytest.fail("Never replay uncertain work"))[0] == 202


def test_request_authentication_failure_is_safe_and_does_not_execute(estate, monkeypatch):
    def expired():
        raise Unauthenticated(TOKEN_A)

    monkeypatch.setattr(estate.identity.client.current_user, "me", expired)
    status, public = post(estate, request(estate), lambda *a: pytest.fail("No auth fallback"))
    assert status == 401 and public["status"] == "authentication_required"
    assert TOKEN_A not in json.dumps(public)


def test_duplicate_registry_does_not_retain_clients_requests_results_or_futures(estate):
    body = request(estate, body={"workflow_id": "only-worker-closure"})
    post(estate, body, lambda *a: (200, {"public_response": True}))
    estate.service.close()
    # Service lifetime state contains configuration, synchronization and executor
    # only. There is deliberately no in-memory auth/payload/result registry.
    assert set(vars(estate.service)) == {"warehouse", "process_id", "_executor", "_slots", "_lifecycle", "_closed"}
    import workbench.hosted_operations as operations
    for (workspace, user), locks in operations._LOCKS.items():
        assert isinstance(workspace, str) and isinstance(user, str)
        assert all(isinstance(operation, str) and hasattr(lock, "acquire") for operation, lock in locks.items())