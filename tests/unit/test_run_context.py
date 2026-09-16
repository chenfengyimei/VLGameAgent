from __future__ import annotations

import unittest

from tests.helpers import identity
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease_manager import ControlLeaseManager
from uga.control.scheduler import ActionScheduler
from uga.core.run_context import RunContext
from uga.safety.emergency_stop import EmergencyStop
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.safety.shutdown import SafetyShutdown
from uga.time.clock import ManualClock


def _safety_stack() -> tuple[SafetyShutdown, AgentEnableState]:
    clock = ManualClock(100)
    target = identity()
    leases = ControlLeaseManager(clock)
    backend = DryRunInputBackend()
    enabled = AgentEnableState(True)
    guard = FocusGuard(FakeWindows(target), FakeIntegrity(), leases, enabled)
    executor = InputExecutor(clock, backend, guard, leases)
    scheduler = ActionScheduler(clock, executor, leases)
    return SafetyShutdown(clock, leases, scheduler, executor, enabled), enabled


class RunContextTests(unittest.TestCase):
    def test_stamp_is_live_until_cancelled(self) -> None:
        context = RunContext()
        stamp = context.stamp()
        self.assertTrue(context.is_live(stamp))
        context.cancel()
        self.assertFalse(context.is_live(stamp))
        self.assertFalse(context.is_live(context.stamp()))
        self.assertTrue(context.cancelled)
        self.assertTrue(context.should_stop())

    def test_cancel_is_latched_and_generation_monotonic(self) -> None:
        context = RunContext()
        before = context.stamp().generation
        context.cancel()
        context.cancel()
        self.assertGreater(context.stamp().generation, before)
        self.assertTrue(context.should_stop())

    def test_advance_generation_invalidates_outstanding_stamps(self) -> None:
        context = RunContext()
        old = context.stamp()
        context.advance_generation()
        self.assertFalse(context.is_live(old))
        self.assertTrue(context.is_live(context.stamp()))
        self.assertFalse(context.should_stop())

    def test_tripped_shutdown_kills_every_stamp(self) -> None:
        shutdown, _enabled = _safety_stack()
        context = RunContext(shutdown)
        stamp = context.stamp()
        self.assertTrue(context.is_live(stamp))
        EmergencyStop(shutdown).trigger()
        self.assertFalse(context.is_live(stamp))
        self.assertFalse(context.is_live(context.stamp()))
        self.assertTrue(context.should_stop())

    def test_distinct_runs_have_distinct_ids(self) -> None:
        first = RunContext()
        second = RunContext()
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertFalse(first.is_live(second.stamp()))


if __name__ == "__main__":
    unittest.main()
