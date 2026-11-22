import asyncio
import unittest

from workbench.hosted_runtime_session import SessionGate


ALICE = "a" * 64
BOB = "b" * 64
GENERATION_ONE = "1" * 64
GENERATION_TWO = "2" * 64


class SessionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_changes_are_scoped_to_owner(self):
        gate = SessionGate()
        async with gate.hold(ALICE) as lease:
            self.assertEqual(lease.observe(GENERATION_ONE), (1, True))
        async with gate.hold(ALICE) as lease:
            self.assertEqual(lease.observe(GENERATION_ONE), (1, False))
        async with gate.hold(BOB) as lease:
            self.assertEqual(lease.observe(GENERATION_ONE), (1, True))
        async with gate.hold(ALICE) as lease:
            self.assertEqual(lease.observe(GENERATION_TWO), (2, True))
        async with gate.hold(BOB) as lease:
            self.assertEqual(lease.observe(GENERATION_ONE), (1, False))

    async def test_same_owner_read_waits_for_write_but_other_owner_does_not(self):
        gate = SessionGate()
        waiting, entered = asyncio.Event(), asyncio.Event()

        async def read():
            waiting.set()
            async with gate.hold(ALICE) as lease:
                lease.observe(GENERATION_TWO)
                entered.set()

        async with gate.hold(ALICE) as lease:
            lease.observe(GENERATION_ONE)
            lease.dirty()
            task = asyncio.create_task(read())
            await waiting.wait()
            self.assertFalse(entered.is_set())
            self.assertFalse(gate.evict(ALICE))
            async with gate.hold(BOB) as other:
                other.observe(GENERATION_ONE)
            self.assertEqual(lease.committed(GENERATION_TWO), 2)
        await task
        self.assertTrue(entered.is_set())
        async with gate.hold(ALICE) as lease:
            self.assertEqual(lease.observe(GENERATION_TWO), (2, False))

    async def test_failed_or_unconfirmed_mutation_invalidates_even_if_generation_repeats(self):
        gate = SessionGate()
        async with gate.hold(ALICE) as lease:
            lease.observe(GENERATION_ONE)
            lease.dirty()
        async with gate.hold(ALICE) as lease:
            epoch, changed = lease.observe(GENERATION_ONE)
            self.assertTrue(changed)
            self.assertGreater(epoch, 1)
        with self.assertRaisesRegex(RuntimeError, "flush failed"):
            async with gate.hold(ALICE) as lease:
                lease.observe(GENERATION_ONE)
                lease.dirty()
                raise RuntimeError("flush failed")
        async with gate.hold(ALICE) as lease:
            next_epoch, changed = lease.observe(GENERATION_ONE)
            self.assertTrue(changed)
            self.assertGreater(next_epoch, epoch)

    async def test_exception_after_commit_still_invalidates_local_state(self):
        gate = SessionGate()
        with self.assertRaisesRegex(RuntimeError, "readback lost"):
            async with gate.hold(ALICE) as lease:
                lease.observe(GENERATION_ONE)
                lease.dirty()
                committed_epoch = lease.committed(GENERATION_TWO)
                raise RuntimeError("readback lost")
        async with gate.hold(ALICE) as lease:
            epoch, changed = lease.observe(GENERATION_TWO)
            self.assertTrue(changed)
            self.assertGreater(epoch, committed_epoch)

    async def test_unobserved_and_failed_authorization_actions_invalidate(self):
        gate = SessionGate()
        async with gate.hold(ALICE) as lease:
            lease.observe(GENERATION_ONE)
        async with gate.hold(ALICE):
            pass
        async with gate.hold(ALICE) as lease:
            epoch, changed = lease.observe(GENERATION_ONE)
            self.assertTrue(changed)
        with self.assertRaises(PermissionError):
            async with gate.hold(ALICE):
                raise PermissionError("authorization failed")
        async with gate.hold(ALICE) as lease:
            next_epoch, changed = lease.observe(GENERATION_ONE)
            self.assertTrue(changed)
            self.assertGreater(next_epoch, epoch)

    async def test_cancelled_waiter_cannot_evict_or_strand_owner(self):
        gate = SessionGate()
        waiting = asyncio.Event()

        async def wait_for_owner():
            waiting.set()
            async with gate.hold(ALICE):
                self.fail("Cancelled waiter acquired owner")

        async with gate.hold(ALICE) as lease:
            lease.observe(GENERATION_ONE)
            task = asyncio.create_task(wait_for_owner())
            await waiting.wait()
            self.assertFalse(gate.evict(ALICE))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(gate.evict(ALICE))
        self.assertTrue(gate.evict(ALICE))
        async with gate.hold(ALICE) as lease:
            self.assertEqual(lease.observe(GENERATION_ONE), (1, True))

    async def test_lease_is_bound_to_owner_task_and_closed_after_action(self):
        gate = SessionGate()
        async with gate.hold(ALICE) as lease:
            lease.observe(GENERATION_ONE)

            async def misuse():
                with self.assertRaisesRegex(RuntimeError, "another task"):
                    lease.dirty()

            await asyncio.create_task(misuse())
        with self.assertRaisesRegex(RuntimeError, "no longer active"):
            lease.dirty()

    async def test_validation_capacity_and_mutation_protocol_fail_closed(self):
        gate = SessionGate(max_owners=1)
        with self.assertRaises(ValueError):
            async with gate.hold("alice"):
                pass
        async with gate.hold(ALICE) as lease:
            with self.assertRaises(RuntimeError):
                lease.dirty()
            with self.assertRaises(ValueError):
                lease.observe("credential-not-a-digest")
            lease.observe(GENERATION_ONE)
            with self.assertRaises(RuntimeError):
                lease.committed(GENERATION_TWO)
            lease.dirty()
            with self.assertRaises(ValueError):
                lease.committed(GENERATION_ONE)
            with self.assertRaises(RuntimeError):
                async with gate.hold(BOB):
                    pass
        async with gate.hold(ALICE) as lease:
            _, changed = lease.observe(GENERATION_ONE)
            self.assertTrue(changed)
        self.assertTrue(gate.evict(ALICE))
        async with gate.hold(BOB) as lease:
            lease.observe(GENERATION_TWO)