"""Exercise production dispatch on both local and hosted task semantics."""
import asyncio
from contextlib import contextmanager
from hashlib import sha256

from anyio import Lock, fail_after
from databricks.sdk.errors import PermissionDenied
from fastapi import HTTPException
import httpx
import pytest

import workbench.hosted as hosted
from workbench.databricks_models import AuthenticationUnavailable
from workbench.tests.test_hosted import (
    backend, browser, create, headers, offline, HOST, ORIGIN, TOKEN_A, TOKEN_A2,
)


def test_two_actual_mutations_release_under_python311_wait_for_semantics(backend, monkeypatch):
    original = asyncio.wait_for

    async def python311_wait_for(awaitable, timeout):
        return await original(asyncio.ensure_future(awaitable), timeout)

    monkeypatch.setattr(asyncio, 'wait_for', python311_wait_for)
    with browser(backend) as client:
        first = create(client, 'First')
        second = create(client, 'Second', TOKEN_A2)
        assert first != second
        dispatcher = client.app.routes[-1].app
        assert not dispatcher.user_queues and not dispatcher.users
        assert len(client.get('/api/projects', headers=headers(TOKEN_A2)).json()['items']) == 2


@pytest.mark.parametrize('error,status', [
    (PermissionDenied('private upstream text'), 503),
    (AuthenticationUnavailable('expired'), 401),
    (HTTPException(403, 'Scope denied'), 403),
])
def test_failed_mutation_releases_before_next_actual_write(backend, monkeypatch, error, status):
    original = hosted.provision
    once = [True]

    def fail_once(*args, **kwargs):
        if once.pop() if once else False:
            raise error
        return original(*args, **kwargs)

    monkeypatch.setattr(hosted, 'provision', fail_once)
    with browser(backend) as client:
        response = client.post('/api/projects', headers=headers(), json={'name': 'Failure'})
        assert response.status_code == status
        assert not client.app.routes[-1].app.user_queues
        create(client, 'After failure', TOKEN_A2)
        assert not client.app.routes[-1].app.user_queues


@pytest.mark.parametrize('case', ['cancel_owner', 'cancel_waiter', 'timeout_waiter'])
def test_cancelled_and_timed_out_requests_do_not_strand_owner(backend, monkeypatch, case):
    async def exercise():
        dispatcher = hosted.HostedDispatcher(hosted.HostedConfig(HOST, ORIGIN, warehouse='warehouse'), backend.resolve)
        owner = sha256((HOST + '\n' + 'alice-id').encode()).hexdigest()
        entered = asyncio.Event()
        original_threadpool = hosted.run_in_threadpool

        async def block_provision(function, *args, **kwargs):
            if case == 'cancel_owner' and function is hosted.provision:
                entered.set()
                await asyncio.Event().wait()
            return await original_threadpool(function, *args, **kwargs)

        monkeypatch.setattr(hosted, 'run_in_threadpool', block_provision)
        if case != 'cancel_owner':
            lock = Lock()
            await lock.acquire()
            # Simulate an already accepted worker waiting behind the owner.
            dispatcher.user_queues[owner] = {'lock': lock, 'requests': 1, 'active': {'operation_id': 'a' * 32}}

        @contextmanager
        def short_timeout(seconds):
            with fail_after(0.02):
                yield

        if case == 'timeout_waiter':
            monkeypatch.setattr(hosted, 'fail_after', short_timeout)

        async def application(scope, receive, send):
            await dispatcher({**scope, 'workbench.operation_execution': case != 'cancel_owner'}, receive, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=ORIGIN) as client:
            request = asyncio.create_task(client.post('/api/projects', headers=headers(), json={'name': case}))
            if case == 'cancel_owner':
                await asyncio.wait_for(entered.wait(), 3)
            elif case == 'cancel_waiter':
                while dispatcher.user_queues[owner]['requests'] < 2:
                    await asyncio.sleep(0)
            if case == 'timeout_waiter':
                response = await request
                assert response.status_code == 409
            else:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            assert not dispatcher.users
            if case != 'cancel_owner':
                assert dispatcher.user_queues[owner]['requests'] == 1
                lock.release()  # Original owner still owns it, waiter did not steal it.
                dispatcher.user_queues.pop(owner)
            assert not dispatcher.user_queues
            monkeypatch.setattr(hosted, 'run_in_threadpool', original_threadpool)
            monkeypatch.setattr(hosted, 'fail_after', fail_after)
            response = await client.post('/api/projects', headers=headers(TOKEN_A2), json={'name': 'Next succeeds'})
            assert response.status_code == 201, response.text
            assert not dispatcher.user_queues and not dispatcher.users
        dispatcher.operations.close()

    asyncio.run(exercise())