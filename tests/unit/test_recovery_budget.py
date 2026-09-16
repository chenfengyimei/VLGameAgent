"""D06 regression tests: the run-level recovery budget (F05/F06).

``--max-recoveries=0`` must disable EVERY recovery — including the visual
back exit that used to bypass the budget as an "ordinary state transition"
(EX03).  The budget is owned by the composition root: rebuilt supervisors
inherit it and can never reset the accounting.
"""

from __future__ import annotations

import unittest

from tests.helpers import identity
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor, TerminalStatus
from uga.agent.recovery_budget import RecoveryBudget, RecoveryKind
from uga.core.errors import ContractViolation
from uga.environment.profile import PerceptionProfile
from uga.time.clock import ManualClock


class RecoveryBudgetTests(unittest.TestCase):
    def test_zero_limit_refuses_every_recovery(self) -> None:
        budget = RecoveryBudget(0)
        self.assertTrue(budget.exhausted)
        self.assertFalse(budget.allow(RecoveryKind.BACK))
        self.assertFalse(budget.consume(RecoveryKind.BACK))
        self.assertEqual(budget.consumed, 0)

    def test_consumption_is_bounded_and_never_resets(self) -> None:
        budget = RecoveryBudget(2)
        self.assertTrue(budget.consume(RecoveryKind.HIGH_RESOLUTION))
        self.assertTrue(budget.consume(RecoveryKind.BACK))
        self.assertFalse(budget.consume(RecoveryKind.BACK))
        self.assertTrue(budget.exhausted)
        state = budget.state()
        self.assertEqual(state.limit, 2)
        self.assertEqual(state.consumed, 2)
        self.assertTrue(state.exhausted)

    def test_negative_limit_is_a_contract_violation(self) -> None:
        with self.assertRaises(ContractViolation):
            RecoveryBudget(-1)


class RecoveryBudgetSupervisorTests(unittest.TestCase):
    def test_zero_budget_blocks_every_recovery_including_back(self) -> None:
        # EX03 regression: max_recoveries=0 with a back-binding profile used
        # to still emit BACK recovery directives.
        supervisor = ClosedLoopSupervisor(
            ManualClock(0),
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
            max_recoveries=0,
        )

        supervisor._request_recovery("fixture failure")  # type: ignore[attr-defined]

        self.assertIsNone(supervisor._pending_recovery)  # type: ignore[attr-defined]
        self.assertIsNone(supervisor._recovery_in_progress)  # type: ignore[attr-defined]
        self.assertEqual(supervisor.status, TerminalStatus.BLOCKED)
        self.assertIn("recovery budget exhausted", supervisor.termination_reason or "")

    def test_budget_survives_supervisor_rebuild(self) -> None:
        # F06: continuous mode rebuilds supervisors; the run-level budget
        # must carry over so a rebuild cannot reset the accounting.
        budget = RecoveryBudget(1)
        first = ClosedLoopSupervisor(
            ManualClock(0),
            PerceptionProfile(action_effect_timeout_ms=1000),
            recovery_budget=budget,
        )

        first._request_recovery("first failure")  # type: ignore[attr-defined]

        self.assertTrue(budget.exhausted)
        second = ClosedLoopSupervisor(
            ManualClock(0),
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
            recovery_budget=budget,
        )

        second._request_recovery("second failure")  # type: ignore[attr-defined]

        self.assertEqual(second.status, TerminalStatus.BLOCKED)
        self.assertIn("recovery budget exhausted", second.termination_reason or "")
        self.assertEqual(budget.consumed, 1)

    def test_budget_exhaustion_surfaces_in_diagnostics(self) -> None:
        supervisor = ClosedLoopSupervisor(
            ManualClock(0),
            PerceptionProfile(action_effect_timeout_ms=1000),
            max_recoveries=1,
        )

        supervisor._request_recovery("first failure")  # type: ignore[attr-defined]

        diagnostics = supervisor.diagnostics()
        self.assertEqual(diagnostics["recovery_budget_consumed"], 1)
        self.assertEqual(diagnostics["recovery_budget_limit"], 1)
        self.assertTrue(diagnostics["recovery_budget_exhausted"])


class RecoveryBudgetPlumbingTests(unittest.TestCase):
    """outcome/snapshot helpers keep the supervisor tests self-consistent."""

    def test_helpers_exist(self) -> None:
        self.assertIsNotNone(outcome)
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(identity)


if __name__ == "__main__":
    unittest.main()
