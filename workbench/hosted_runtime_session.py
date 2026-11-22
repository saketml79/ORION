"""Credential-free coordination primitive for a future retained hosted session.

This module does not authenticate, own a service, or publish a Delta generation.
Callers must hold the same owner lease for every use of a retained service, check
fresh authority inside it, and discard a service whose recorded epoch differs.
A verified successful commit may retag the service with its returned new epoch.
The generation is a caller-computed SHA-256 fingerprint of verified authority,
not an authorization decision. This is a process-local lock, not distributed CAS.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import re
from threading import Lock

from anyio import Lock as AsyncLock, get_current_task


_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError("An opaque SHA-256 identity is required")
    return value


class _Owner:
    def __init__(self):
        self.lock = AsyncLock()
        self.users = 0  # Includes waiters, so eviction cannot create a second lock.
        self.generation = None
        self.epoch = 0


class SessionLease:
    """One exclusive action, never a container for credentials or services."""

    def __init__(self, owner):
        self._owner = owner
        self._task_id = get_current_task().id
        self._open = True
        self._observed = False
        self._dirty = False

    def _require_open(self):
        if not self._open:
            raise RuntimeError("Session lease is no longer active")
        if get_current_task().id != self._task_id:
            raise RuntimeError("Session lease belongs to another task")

    def observe(self, generation):
        """Record freshly verified authority before accessing retained state.

        Returns (epoch, changed). A changed observation requires disposal of
        an older service before use, including after an earlier failed action.
        """
        self._require_open()
        if self._observed:
            raise RuntimeError("Generation already observed for this action")
        generation = _digest(generation)
        changed = self._owner.generation != generation
        if changed:
            self._owner.generation = generation
            self._owner.epoch += 1
        self._observed = True
        return self._owner.epoch, changed

    def dirty(self):
        """Mark before any retained-state mutation, including local writes."""
        self._require_open()
        if not self._observed or self._dirty:
            raise RuntimeError("Observe authority before marking a mutation")
        self._dirty = True

    def committed(self, generation):
        """Accept only the caller's independently verified commit fingerprint."""
        self._require_open()
        if not self._dirty:
            raise RuntimeError("No pending mutation to commit")
        generation = _digest(generation)
        if generation == self._owner.generation:
            raise ValueError("A mutation requires a new committed generation")
        self._owner.generation = generation
        self._owner.epoch += 1
        self._dirty = False
        return self._owner.epoch

    def invalidate(self):
        """Require retained state to be discarded and authority re-observed."""
        self._require_open()
        self._owner.generation = None
        self._owner.epoch += 1
        self._observed = False
        self._dirty = False


class SessionGate:
    """Bounded per-owner read/write exclusion and generation bookkeeping.

    Owner keys must be opaque digests of the verified workspace and user. This
    gate stores only those digests, lock state and generation fingerprints. It
    intentionally neither retains nor closes application services or clients.
    """

    def __init__(self, max_owners=64):
        if type(max_owners) is not int or max_owners < 1:
            raise ValueError("A positive owner limit is required")
        self._max_owners = max_owners
        self._guard = Lock()
        self._owners = {}

    @asynccontextmanager
    async def hold(self, owner):
        owner = _digest(owner)
        with self._guard:
            state = self._owners.get(owner)
            if state is None:
                if len(self._owners) >= self._max_owners:
                    raise RuntimeError("Session owner capacity reached")
                state = _Owner()
                self._owners[owner] = state
            state.users += 1
        acquired = False
        lease = None
        try:
            await state.lock.acquire()
            acquired = True
            lease = SessionLease(state)
            try:
                yield lease
            except BaseException:
                lease.invalidate()
                raise
            finally:
                if lease._dirty or not lease._observed:
                    # A lost commit acknowledgement is ambiguous: discard local
                    # state, then consult fresh durable authority on the next use.
                    if state.generation is not None:
                        lease.invalidate()
                lease._open = False
        finally:
            if acquired:
                state.lock.release()
            with self._guard:
                state.users -= 1

    def evict(self, owner):
        """Forget an idle owner's bookkeeping; never evict a holder or waiter.

        The integrator must dispose any corresponding retained service before
        allowing new actions; this primitive owns no executor or service.
        """
        owner = _digest(owner)
        with self._guard:
            state = self._owners.get(owner)
            if state is None:
                return True
            if state.users:
                return False
            del self._owners[owner]
            return True