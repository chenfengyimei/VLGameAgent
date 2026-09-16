"""D04 regression tests: terminal execution receipts and their consumers.

Arbiter acceptance only queues work; the receipt ring records what the input
executor actually did to the OS.  The supervisor must anchor its effect clock
at the real execution moment, resolve never-executed actions without ever
claiming an effect, and treat a partial click as anything but a completed one.
"""

from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_control_runtime import FakeIntegrity, FakeWindows
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.control.arbiter import ActionArbiter
from uga.control.execution_receipt import (
    ExecutionPrimitiveStatus,
    ExecutionReceipt,
    aggregate_receipts,
)
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.control.scheduler import ActionScheduler
from uga.environment.profile import PerceptionProfile
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import ManualClock, UGATime

_SUBMITTED = frozenset({"a:move", "a:down", "a:up"})


def _receipt(
    action_id: str,
    status: ExecutionPrimitiveStatus,
    at_ns: int = 100,
) -> ExecutionReceipt:
    return ExecutionReceipt(
        action_id=action_id,
        proposal_id="proposal-1",
        primitive="MouseButtonAction",
        status=status,
        at=UGATime(at_ns),
        target=identity(),
        lease_id="lease-1",
        lease_generation=1,
        failure_reason=None if status == ExecutionPrimitiveStatus.EXECUTED else "agent_disabled",
    )


class AggregateReceiptsTests(unittest.TestCase):
    def test_all_executed_is_executed(self) -> None:
        receipts = (
            _receipt("a:move", ExecutionPrimitiveStatus.EXECUTED),
            _receipt("a:down", ExecutionPrimitiveStatus.EXECUTED),
        )
        self.assertEqual(aggregate_receipts(receipts), "executed")

    def test_mixed_is_partial_never_completed(self) -> None:
        receipts = (
            _receipt("a:move", ExecutionPrimitiveStatus.EXECUTED),
            _receipt("a:down", ExecutionPrimitiveStatus.REJECTED),
            _receipt("a:up", ExecutionPrimitiveStatus.FLUSHED),
        )
        self.assertEqual(aggregate_receipts(receipts), "partial")

    def test_all_rejected_reports_the_dominant_failure(self) -> None:
        receipts = (
            _receipt("a:move", ExecutionPrimitiveStatus.REJECTED),
            _receipt("a:down", ExecutionPrimitiveStatus.REJECTED),
            _receipt("a:up", ExecutionPrimitiveStatus.REJECTED),
        )
        self.assertEqual(aggregate_receipts(receipts), "rejected")

    def test_empty_is_unknown(self) -> None:
        self.assertEqual(aggregate_receipts(()), "unknown")


class SchedulerReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(100)
        self.target = identity()
        self.leases = ControlLeaseManager(self.clock)
        self.backend = DryRunInputBackend()
        self.enabled = AgentEnableState(True)
        self.guard = FocusGuard(
            FakeWindows(self.target), FakeIntegrity(), self.leases, self.enabled
        )
        self.executor = InputExecutor(self.clock, self.backend, self.guard, self.leases)
        self.scheduler = ActionScheduler(self.clock, self.executor, self.leases)
        self.arbiter = ActionArbiter(self.clock, self.leases)
        self.lease = self.leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="test",
        )

    def _schedule(self, action_id: str, effective: int, expires: int) -> None:
        lifetime = ActionLifetime(UGATime(100), UGATime(effective), UGATime(expires))
        action = KeyboardAction(action_id, lifetime, 0x11, True)
        decision = self.arbiter.decide(
            # Build the proposal exactly like the production controller does.
            self._proposal((action,))
        )
        self.scheduler.schedule(decision, self.target, self.lease)

    def _proposal(self, actions: tuple[KeyboardAction, ...]):
        from uga.control.proposal import ActionProposal

        lifetime = ActionLifetime(UGATime(100), UGATime(100), UGATime(500))
        return ActionProposal(
            "proposal",
            "test",
            self.lease.owner,
            self.lease.mode,
            self.lease.lease_id,
            self.lease.generation,
            lifetime,
            actions,
        )

    def test_every_scheduled_primitive_gets_one_terminal_receipt(self) -> None:
        self._schedule("now", 100, 300)
        self._schedule("later", 150, 300)
        self.scheduler.tick()
        self.clock.set(150)
        self.scheduler.tick()
        receipts = self.scheduler.drain_receipts()
        self.assertEqual(
            [receipt.action_id for receipt in receipts], ["now", "later"]
        )
        self.assertTrue(all(receipt.executed for receipt in receipts))
        self.assertEqual(self.scheduler.drain_receipts(), ())

    def test_guard_rejection_receipt_and_count_conservation(self) -> None:
        self._schedule("now", 100, 300)
        self._schedule("later", 100, 300)
        self.enabled.set(False)
        stats = self.scheduler.tick()
        receipts = self.scheduler.drain_receipts()
        # First primitive refused by the guard, the rest flushed without input.
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0].status, ExecutionPrimitiveStatus.REJECTED)
        self.assertEqual(receipts[1].status, ExecutionPrimitiveStatus.FLUSHED)
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(
            stats.scheduled,
            stats.executed + stats.rejected + stats.expired + stats.flushed + stats.queued,
        )

    def test_expired_primitive_gets_expired_receipt(self) -> None:
        self._schedule("stale", 100, 110)
        self.clock.set(111)
        stats = self.scheduler.tick()
        receipts = self.scheduler.drain_receipts()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, ExecutionPrimitiveStatus.EXPIRED)
        self.assertEqual(stats.expired, 1)

    def test_flush_emits_receipt_for_every_cleared_primitive(self) -> None:
        self._schedule("future", 200, 400)
        count = self.scheduler.flush()
        receipts = self.scheduler.drain_receipts()
        self.assertEqual(count, 1)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, ExecutionPrimitiveStatus.FLUSHED)

    def test_receipt_ring_is_bounded(self) -> None:
        for index in range(1200):
            self._schedule(f"key-{index}", 100, 300)
        self.scheduler.flush()
        receipts = self.scheduler.drain_receipts()
        self.assertEqual(len(receipts), 1024)
        # The newest receipts survive; the oldest are dropped first.
        self.assertEqual(receipts[-1].action_id, "key-1199")


class SupervisorExecutionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(100)
        self.supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=1000)
        )

    def _start(self) -> None:
        self.supervisor.start_action(
            outcome(1),
            snapshot(1, 0),
            frame(1, 0),
            submitted_action_ids=_SUBMITTED,
            expected_primitives=3,
        )

    def test_rejected_execution_resolves_without_any_effect_claim(self) -> None:
        self._start()
        self.supervisor.record_execution_receipts(
            (
                _receipt("a:move", ExecutionPrimitiveStatus.REJECTED),
                _receipt("a:down", ExecutionPrimitiveStatus.REJECTED),
                _receipt("a:up", ExecutionPrimitiveStatus.REJECTED),
            )
        )
        observation = self.supervisor.observe(snapshot(2, 150), frame(2, 150))
        self.assertFalse(observation.pending)
        self.assertFalse(observation.effect_observed)
        diagnostics = self.supervisor.diagnostics()
        self.assertEqual(diagnostics["not_executed_actions"], 1)
        self.assertEqual(diagnostics["verified_effect_actions"], 0)
        self.assertEqual(diagnostics["ineffective_actions"], 0)
        self.assertFalse(self.supervisor.last_effect_observed)

    def test_partial_click_is_never_a_completed_click(self) -> None:
        self._start()
        self.supervisor.record_execution_receipts(
            (
                _receipt("a:move", ExecutionPrimitiveStatus.EXECUTED),
                _receipt("a:down", ExecutionPrimitiveStatus.REJECTED),
                _receipt("a:up", ExecutionPrimitiveStatus.FLUSHED),
            )
        )
        observation = self.supervisor.observe(snapshot(2, 150), frame(2, 150))
        self.assertFalse(observation.pending)
        self.assertFalse(observation.effect_observed)
        pending = self.supervisor._pending  # type: ignore[attr-defined]
        self.assertIsNone(pending)
        diagnostics = self.supervisor.diagnostics()
        self.assertEqual(diagnostics["verified_effect_actions"], 0)

    def test_effect_clock_starts_at_actual_execution_time(self) -> None:
        self._start()
        # The primitives reached the OS 400ms after the action was staged.
        self.supervisor.record_execution_receipts(
            (
                _receipt("a:move", ExecutionPrimitiveStatus.EXECUTED, 500_000_100),
                _receipt("a:down", ExecutionPrimitiveStatus.EXECUTED, 500_000_120),
                _receipt("a:up", ExecutionPrimitiveStatus.EXECUTED, 500_000_140),
            )
        )
        observation = self.supervisor.observe(
            snapshot(2, 550_000_000), frame(2, 550_000_000)
        )
        # 50ms after the LAST executed primitive: still inside the minimum
        # window.  An issue-time clock (450ms) would already run effect
        # checks here — the anchor must be the execution moment.
        self.assertTrue(observation.pending)
        self.assertIn("minimum action effect window", observation.detail)

    def test_unproven_execution_waits_then_fails_closed_at_the_deadline(self) -> None:
        self._start()
        waiting = self.supervisor.observe(snapshot(2, 150), frame(2, 150))
        self.assertTrue(waiting.pending)
        self.assertIn("terminal execution receipts", waiting.detail)
        resolved = self.supervisor.observe(
            snapshot(3, 1_100_000_000), frame(3, 1_100_000_000)
        )
        self.assertFalse(resolved.pending)
        self.assertFalse(resolved.effect_observed)
        diagnostics = self.supervisor.diagnostics()
        self.assertEqual(diagnostics["not_executed_actions"], 1)
        self.assertEqual(diagnostics["verified_effect_actions"], 0)

    def test_receipts_for_foreign_actions_are_ignored(self) -> None:
        self._start()
        self.supervisor.record_execution_receipts(
            (_receipt("other:move", ExecutionPrimitiveStatus.EXECUTED),)
        )
        waiting = self.supervisor.observe(snapshot(2, 150), frame(2, 150))
        self.assertTrue(waiting.pending)


if __name__ == "__main__":
    unittest.main()
