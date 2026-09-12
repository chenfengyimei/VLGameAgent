from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from uga.agent.closed_loop import (
    ClosedLoopSupervisor,
    DecisionDisposition,
    GoalVerifier,
    TerminalStatus,
)
from uga.control.lease import ControlMode
from uga.environment.profile import PerceptionProfile
from uga.gui.schema import GuiActionKind
from uga.perception.schema import (
    ActionRisk,
    DecisionKind,
    GoalStatus,
    GroundedAction,
    NormalizedBox,
    PerceptionSnapshot,
    PlannerOutcome,
    WaitReason,
)
from uga.time.clock import ManualClock, UGATime


def snapshot(
    number: int,
    timestamp_ns: int,
    *,
    signature: str = "same-state",
    generation: int = 1,
) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        f"snapshot-{number}",
        f"frame-{number}",
        number,
        UGATime(timestamp_ns),
        identity(generation=generation),
        1,
        1,
        ControlMode.GUI,
        (),
        (),
        (("goal", "settings"),),
        signature,
        0.95,
    )


def outcome(
    number: int,
    *,
    kind: DecisionKind = DecisionKind.ACT,
    confidence: float = 0.95,
    label: str = "settings",
    risk: ActionRisk = ActionRisk.NORMAL,
    generation: int = 1,
) -> PlannerOutcome:
    action = None
    wait_reason = None
    status = GoalStatus.IN_PROGRESS
    if kind == DecisionKind.ACT:
        action = GroundedAction(
            GuiActionKind.CLICK,
            label,
            NormalizedBox(0.1, 0.1, 0.9, 0.9),
            "target page opens",
            confidence,
            risk,
        )
    elif kind == DecisionKind.WAIT:
        wait_reason = WaitReason.LOADING
    elif kind == DecisionKind.DONE:
        status = GoalStatus.SUCCEEDED
    return PlannerOutcome(
        f"decision-{number}",
        f"frame-{number}",
        number,
        generation,
        1,
        1,
        kind,
        "Android settings",
        ("settings",),
        status,
        confidence,
        action,
        wait_reason,
    )


class GoalVerifierTests(unittest.TestCase):
    def test_done_requires_two_fresh_frames_separated_by_stability_window(self) -> None:
        verifier = GoalVerifier(confirmation_ns=500_000_000)

        self.assertFalse(verifier.consider(outcome(1, kind=DecisionKind.DONE), snapshot(1, 0)))
        self.assertFalse(
            verifier.consider(
                outcome(2, kind=DecisionKind.DONE), snapshot(2, 499_999_999)
            )
        )
        self.assertTrue(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE), snapshot(3, 500_000_000)
            )
        )

    def test_non_done_resets_goal_confirmation(self) -> None:
        verifier = GoalVerifier(confirmation_ns=500_000_000)
        self.assertFalse(verifier.consider(outcome(1, kind=DecisionKind.DONE), snapshot(1, 0)))
        self.assertFalse(verifier.consider(outcome(2, kind=DecisionKind.WAIT), snapshot(2, 1)))
        self.assertFalse(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE), snapshot(3, 600_000_000)
            )
        )


class ClosedLoopSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(0)
        self.profile = PerceptionProfile(
            page_stable_ms=500,
            action_effect_timeout_ms=1000,
        )
        self.supervisor = ClosedLoopSupervisor(self.clock, self.profile)

    def test_wait_never_becomes_an_executable_action(self) -> None:
        current = snapshot(1, 0)
        decision = self.supervisor.assess(
            outcome(1, kind=DecisionKind.WAIT),
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.WAIT)
        self.assertIsNone(decision.outcome.action)

    def test_high_confidence_action_is_grounded_at_box_center(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        decision = self.supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        action = self.supervisor.to_gui_action(proposal, lambda _: None)
        self.assertEqual((action.x, action.y), (0.5, 0.5))

    def test_medium_confidence_without_verifier_reobserves_then_blocks(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1, confidence=0.7)

        first = self.supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )
        second = self.supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(second.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(self.supervisor.status, TerminalStatus.BLOCKED)

    def test_critical_action_requires_explicit_matching_goal(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1, label="delete", risk=ActionRisk.CRITICAL)

        rejected = self.supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )

        self.assertEqual(rejected.disposition, DecisionDisposition.REOBSERVE)

        supervisor = ClosedLoopSupervisor(self.clock, self.profile)
        accepted = supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "delete",
        )
        self.assertEqual(accepted.disposition, DecisionDisposition.EXECUTE)

    def test_stale_window_generation_is_never_executed(self) -> None:
        decided = snapshot(1, 0)
        fresh = snapshot(2, 1, generation=2)

        decision = self.supervisor.assess(
            outcome(1), decided, fresh, frame(1, 0), frame(2, 1), "open settings"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)

    def test_action_effect_must_change_semantic_or_target_state(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))

        waiting = self.supervisor.observe(snapshot(2, 250_000_000), frame(1, 250_000_000))
        changed = self.supervisor.observe(
            snapshot(3, 300_000_000, signature="new-state"),
            frame(2, 300_000_000),
        )

        self.assertTrue(waiting.pending)
        self.assertTrue(changed.effect_observed)

    def test_full_screen_signature_noise_alone_is_not_action_effect(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))

        noisy = self.supervisor.observe(
            snapshot(2, 300_000_000, signature="animation-only"),
            frame(1, 300_000_000),
        )

        self.assertTrue(noisy.pending)
        self.assertIsNone(noisy.effect_observed)

    def test_unchanged_action_is_recorded_as_ineffective_after_timeout(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))

        effect = self.supervisor.observe(
            snapshot(2, 1_000_000_000), frame(1, 1_000_000_000)
        )

        self.assertFalse(effect.pending)
        self.assertFalse(effect.effect_observed)

    def test_done_transitions_to_success_only_after_confirmation(self) -> None:
        first = snapshot(1, 0)
        second = snapshot(2, 500_000_000)

        pending = self.supervisor.assess(
            outcome(1, kind=DecisionKind.DONE),
            first,
            first,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )
        completed = self.supervisor.assess(
            outcome(2, kind=DecisionKind.DONE),
            second,
            second,
            frame(2, 500_000_000),
            frame(2, 500_000_000),
            "open settings",
        )

        self.assertEqual(pending.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(completed.disposition, DecisionDisposition.TERMINATE)
        self.assertEqual(self.supervisor.status, TerminalStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
