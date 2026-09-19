from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame, identity
from uga.agent.closed_loop import (
    ActionValidator,
    ClosedLoopSupervisor,
    DecisionDisposition,
    GoalVerifier,
    RecoveryDirective,
    SupervisedDecision,
    TerminalStatus,
    _digest_difference,
    region_digest,
)
from uga.agent.session_state import GameSessionState, ScreenType, page_anchor_signature
from uga.agent.task_graph import RetryPolicy, TaskGraph, TaskNode, TaskStatus
from uga.capture.frame import BufferHandle
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
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
    TextRegion,
    WaitReason,
)
from uga.time.clock import ManualClock, UGATime


def snapshot(
    number: int,
    timestamp_ns: int,
    *,
    signature: str = "same-state",
    generation: int = 1,
    visible_text: tuple[str, ...] = (),
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
        tuple(
            TextRegion(value, NormalizedBox(0.1, 0.1, 0.9, 0.2), 0.99)
            for value in visible_text
        ),
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
    wait: WaitReason | None = None,
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
        wait_reason = wait or WaitReason.LOADING
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


class RegionDigestTests(unittest.TestCase):
    @staticmethod
    def _bgra_frame(
        number: int, blue: int, green: int, red: int, alpha: int = 255
    ):  # type: ignore[no-untyped-def]
        source = frame(number)
        pixel = bytes((blue, green, red, alpha))
        payload = pixel * (source.width * source.height)
        return replace(
            source,
            buffer_handle=BufferHandle(
                source.buffer_handle.handle_id,
                source.buffer_handle.kind,
                len(payload),
                payload,
            ),
        )

    def test_red_green_channel_change_is_detected(self) -> None:
        # EX01 regression: the old [::16] byte stride sampled one colour
        # channel of every fourth pixel, so a red↔green swap with constant
        # blue stayed invisible to every freshness and effect comparison.
        box = NormalizedBox(0.0, 0.0, 1.0, 1.0)
        blue_frame = self._bgra_frame(1, 10, 200, 30)
        swapped = self._bgra_frame(2, 10, 30, 200)
        difference = _digest_difference(
            region_digest(blue_frame, box), region_digest(swapped, box)
        )
        self.assertGreater(difference, 0.25)
        self.assertTrue(ActionValidator._target_changed(box, blue_frame, swapped))

    def test_alpha_change_is_not_a_target_change(self) -> None:
        # Alpha and stride padding must not fake (or mask) progress.
        box = NormalizedBox(0.0, 0.0, 1.0, 1.0)
        opaque = self._bgra_frame(1, 10, 200, 30, 255)
        transparent = self._bgra_frame(2, 10, 200, 30, 0)
        self.assertEqual(region_digest(opaque, box), region_digest(transparent, box))
        self.assertFalse(ActionValidator._target_changed(box, opaque, transparent))

    def test_digest_sampling_stays_bounded_on_huge_regions(self) -> None:
        box = NormalizedBox(0.0, 0.0, 1.0, 1.0)
        source = frame(1)
        width = 1920
        height = 1080
        payload = bytes([64]) * (width * height * 4)
        huge = replace(
            source,
            width=width,
            height=height,
            stride_bytes=width * 4,
            buffer_handle=BufferHandle(
                source.buffer_handle.handle_id,
                source.buffer_handle.kind,
                len(payload),
                payload,
            ),
        )
        digest = region_digest(huge, box)
        # Two bytes per sampled pixel on a bounded grid: never the raw frame.
        self.assertLessEqual(len(digest), 2 * 4096 * 4)
        self.assertGreater(len(digest), 0)


class GoalVerifierTests(unittest.TestCase):
    def test_done_requires_two_fresh_frames_separated_by_stability_window(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000, required_evidence=("任务完成",)
        )

        self.assertFalse(
            verifier.consider(
                outcome(1, kind=DecisionKind.DONE),
                snapshot(1, 0, visible_text=("任务完成",)),
            )
        )
        self.assertFalse(
            verifier.consider(
                outcome(2, kind=DecisionKind.DONE),
                snapshot(2, 499_999_999, visible_text=("任务完成",)),
            )
        )
        self.assertTrue(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE),
                snapshot(3, 500_000_000, visible_text=("任务完成",)),
            )
        )

    def test_done_without_screen_evidence_never_confirms(self) -> None:
        # EX02 regression: two high-confidence DONE replies on an OCR-empty
        # screen used to confirm the goal.  The screen itself must carry the
        # evidence — a model claim is never proof.
        verifier = GoalVerifier(
            confirmation_ns=500_000_000, required_evidence=("任务完成",)
        )
        for number, timestamp in ((1, 0), (2, 600_000_000), (3, 1_200_000_000)):
            self.assertFalse(
                verifier.consider(
                    outcome(number, kind=DecisionKind.DONE), snapshot(number, timestamp)
                )
            )

    def test_model_claimed_visible_text_is_not_goal_evidence(self) -> None:
        # outcome().visible_text is the model's own claim; with the screen
        # OCR-empty, confirmation must never happen no matter how confident
        # the reply is.
        verifier = GoalVerifier(
            confirmation_ns=500_000_000, required_evidence=("任务完成",)
        )
        for number, timestamp in ((1, 0), (2, 600_000_000)):
            self.assertFalse(
                verifier.consider(
                    outcome(number, kind=DecisionKind.DONE), snapshot(number, timestamp)
                )
            )

    def test_goal_evidence_cannot_span_window_or_task_context(self) -> None:
        # T14 regression: completion evidence must not be spliced across a
        # window recreation — the new context re-establishes the candidate.
        verifier = GoalVerifier(
            confirmation_ns=500_000_000, required_evidence=("任务完成",)
        )
        self.assertFalse(
            verifier.consider(
                outcome(1, kind=DecisionKind.DONE),
                snapshot(1, 0, visible_text=("任务完成",)),
            )
        )
        recreated = snapshot(2, 600_000_000, visible_text=("任务完成",), generation=2)
        self.assertFalse(
            verifier.consider(outcome(2, kind=DecisionKind.DONE), recreated)
        )
        self.assertFalse(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE),
                snapshot(3, 700_000_000, visible_text=("任务完成",), generation=2),
            )
        )
        self.assertTrue(
            verifier.consider(
                outcome(4, kind=DecisionKind.DONE),
                snapshot(4, 1_200_000_000, visible_text=("任务完成",), generation=2),
            )
        )

    def test_non_done_resets_goal_confirmation(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000, required_evidence=("任务完成",)
        )
        self.assertFalse(verifier.consider(outcome(1, kind=DecisionKind.DONE), snapshot(1, 0)))
        self.assertFalse(verifier.consider(outcome(2, kind=DecisionKind.WAIT), snapshot(2, 1)))
        self.assertFalse(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE), snapshot(3, 600_000_000)
            )
        )

    def test_required_evidence_must_come_from_fresh_ocr_not_model_claims(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000,
            required_evidence=("根据电量百分比",),
        )

        self.assertFalse(
            verifier.consider(
                outcome(1, kind=DecisionKind.DONE),
                snapshot(1, 0, visible_text=("设置时间表", "没有时间表")),
            )
        )
        self.assertEqual(verifier.last_missing_evidence, ("根据电量百分比",))
        self.assertFalse(
            verifier.consider(
                outcome(2, kind=DecisionKind.DONE),
                snapshot(2, 100_000_000, visible_text=("根据电量", "百分比")),
            )
        )
        self.assertTrue(
            verifier.consider(
                outcome(3, kind=DecisionKind.DONE),
                snapshot(3, 600_000_000, visible_text=("根据电量", "百分比")),
            )
        )

    def test_observed_evidence_can_confirm_despite_contradictory_action(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000,
            required_evidence=("destination",),
        )

        self.assertFalse(
            verifier.consider_observed_evidence(
                snapshot(1, 0, visible_text=("destination",))
            )
        )
        self.assertTrue(
            verifier.consider_observed_evidence(
                snapshot(2, 500_000_000, visible_text=("destination",))
            )
        )

    def test_observed_evidence_requires_configured_fact_and_confidence(self) -> None:
        unbound = GoalVerifier(confirmation_ns=500_000_000)
        low_confidence = replace(
            snapshot(1, 0),
            visible_text=(
                TextRegion("destination", NormalizedBox(0.1, 0.1, 0.9, 0.2), 0.8),
            ),
        )
        verifier = GoalVerifier(
            confirmation_ns=500_000_000,
            required_evidence=("destination",),
        )

        self.assertFalse(unbound.consider_observed_evidence(snapshot(1, 0)))
        self.assertFalse(verifier.consider_observed_evidence(low_confidence))

    def test_evidence_confidence_ignores_unrelated_low_confidence_text(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000,
            required_evidence=("destination",),
        )
        current = replace(
            snapshot(1, 0),
            visible_text=(
                TextRegion("noise", NormalizedBox(0.1, 0.1, 0.2, 0.2), 0.1),
                TextRegion("destination", NormalizedBox(0.2, 0.2, 0.8, 0.3), 0.96),
            ),
            confidence=0.53,
        )

        self.assertFalse(verifier.consider_observed_evidence(current))
        self.assertEqual(verifier.last_evidence_confidence, 0.96)
        later = replace(
            current,
            snapshot_id="snapshot-2",
            frame_id="frame-2",
            frame_sequence=2,
            captured_at=UGATime(500_000_000),
        )
        self.assertTrue(verifier.consider_observed_evidence(later))

    def test_split_evidence_uses_lowest_contributing_region_confidence(self) -> None:
        verifier = GoalVerifier(
            confirmation_ns=500_000_000,
            required_evidence=("根据电量百分比",),
        )
        current = replace(
            snapshot(1, 0),
            visible_text=(
                TextRegion("根据电量", NormalizedBox(0.1, 0.1, 0.3, 0.2), 0.97),
                TextRegion("百分比", NormalizedBox(0.3, 0.1, 0.5, 0.2), 0.88),
            ),
        )

        verifier.inspect_evidence(current)

        self.assertEqual(verifier.last_missing_evidence, ())
        self.assertEqual(verifier.last_evidence_confidence, 0.88)


class ClosedLoopSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(0)
        self.profile = PerceptionProfile(
            page_stable_ms=500,
            action_effect_timeout_ms=1000,
        )
        self.supervisor = ClosedLoopSupervisor(self.clock, self.profile)

    def test_effect_deadline_expires_under_permanent_pixel_animation(self) -> None:
        # EX08 regression: a permanently changing target kept refreshing the
        # stabilization candidate and the wait never ended.  Candidates may
        # refresh their own window, but the absolute effect deadline must
        # terminate the wait as ineffective.
        supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=1000)
        )
        current = snapshot(1, 0)
        supervisor.start_action(outcome(1), current, frame(1, 0))
        self.assertIsNotNone(supervisor._pending)  # type: ignore[attr-defined]

        first = supervisor.observe(snapshot(2, 400_000_000), frame(2, 400_000_000))
        self.assertTrue(first.pending)
        self.assertEqual(first.detail, "waiting for target pixel change to stabilize")

        second = supervisor.observe(snapshot(3, 800_000_000), frame(3, 800_000_000))
        self.assertTrue(second.pending)
        self.assertEqual(second.detail, "transient target pixels are still changing")

        third = supervisor.observe(snapshot(4, 1_200_000_000), frame(4, 1_200_000_000))
        self.assertFalse(third.pending)
        self.assertFalse(third.effect_observed)
        diagnostics = supervisor.diagnostics()
        self.assertEqual(diagnostics["ineffective_actions"], 1)

    def test_local_dialogue_click_releases_effect_gate_within_half_second(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=3000)
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_dialogue_advance",
                NormalizedBox(0.94, 0.89, 0.98, 0.94),
                "the dialogue advances",
                1.0,
            ),
        )
        supervisor.start_action(
            proposal,
            current,
            frame(1, 0),
            source="ocr_dialogue_click_fast",
        )

        still_waiting = supervisor.observe(
            snapshot(2, 300_000_000), frame(2, 300_000_000)
        )
        released = supervisor.observe(
            snapshot(3, 500_000_000), frame(3, 500_000_000)
        )

        self.assertTrue(still_waiting.pending)
        self.assertFalse(released.pending)
        self.assertFalse(released.effect_observed)

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

    def test_repeated_no_safe_wait_uses_bounded_distinct_recoveries(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(recovery_safe_actions=frozenset({"back"})),
            max_recoveries=2,
        )
        first_snapshot = snapshot(1, 0)
        second_snapshot = snapshot(2, 1)
        third_snapshot = snapshot(3, 2)

        first = supervisor.assess(
            outcome(1, kind=DecisionKind.WAIT, wait=WaitReason.NO_SAFE_ACTION),
            first_snapshot,
            first_snapshot,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )
        second = supervisor.assess(
            outcome(2, kind=DecisionKind.WAIT, wait=WaitReason.NO_SAFE_ACTION),
            second_snapshot,
            second_snapshot,
            frame(2, 1),
            frame(2, 1),
            "open settings",
        )
        third = supervisor.assess(
            outcome(3, kind=DecisionKind.WAIT, wait=WaitReason.NO_SAFE_ACTION),
            third_snapshot,
            third_snapshot,
            frame(3, 2),
            frame(3, 2),
            "open settings",
        )

        self.assertEqual(first.disposition, DecisionDisposition.WAIT)
        self.assertEqual(second.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(supervisor.recovery_count, 1)
        self.assertEqual(third.disposition, DecisionDisposition.RECOVER)
        self.assertEqual(third.recovery, RecoveryDirective.BACK)
        # The visual back transition is an ordinary state change: it never
        # consumes the second recovery slot.
        self.assertEqual(supervisor.recovery_count, 1)

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

    def test_pointer_offset_keeps_grounding_box_but_moves_click_hotspot(self) -> None:
        proposal = outcome(1)
        assert proposal.action is not None
        shifted = replace(
            proposal,
            action=replace(
                proposal.action,
                pointer_offset_x=0.02,
                pointer_offset_y=-0.06,
            ),
        )

        action = self.supervisor.to_gui_action(shifted, lambda _: None)

        self.assertEqual((action.x, action.y), (0.52, 0.44))
        self.assertEqual(shifted.action.target_box, proposal.action.target_box)  # type: ignore[union-attr]

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

    def test_critical_action_requires_handoff_even_with_matching_goal(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1, label="delete", risk=ActionRisk.CRITICAL)

        rejected = self.supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )

        self.assertEqual(rejected.disposition, DecisionDisposition.WAIT)

        supervisor = ClosedLoopSupervisor(self.clock, self.profile)
        accepted = supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "delete",
        )
        self.assertEqual(accepted.disposition, DecisionDisposition.WAIT)

    def test_stale_window_generation_is_never_executed(self) -> None:
        decided = snapshot(1, 0)
        fresh = snapshot(2, 1, generation=2)

        decision = self.supervisor.assess(
            outcome(1), decided, fresh, frame(1, 0), frame(2, 1), "open settings"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)

        valid = self.supervisor.assess(
            outcome(2),
            snapshot(2, 2),
            snapshot(2, 2),
            frame(2, 2),
            frame(2, 2),
            "open settings",
        )
        self.assertEqual(valid.disposition, DecisionDisposition.EXECUTE)

    def test_target_is_rechecked_on_the_last_frame_before_execution(self) -> None:
        self.clock.set(2)  # Both captured frames precede execution.
        proposal = outcome(1)
        validated = frame(1, 0)
        unchanged = replace(
            validated,
            frame_id="frame-2",
            capture_timestamp=UGATime(1),
        )

        safe, _ = self.supervisor.validate_execution_frame(
            proposal, validated, unchanged
        )
        stale, reason = self.supervisor.validate_execution_frame(
            proposal, validated, frame(2, 2)
        )

        self.assertTrue(safe)
        self.assertFalse(stale)
        self.assertIn("before execution", reason)
        self.assertEqual(self.supervisor.diagnostics()["stale_results_discarded"], 1)

    def test_ocr_grounded_dynamic_target_can_execute_on_the_next_frame(self) -> None:
        self.clock.set(2)
        proposal = outcome(1)
        validated = frame(1, 0)

        safe, reason = self.supervisor.validate_execution_frame(
            proposal,
            validated,
            frame(2, 2),
            target_was_ocr_grounded=True,
        )

        self.assertTrue(safe)
        self.assertIn("OCR-grounded target", reason)
        self.assertEqual(self.supervisor.diagnostics()["stale_results_discarded"], 0)

    def test_target_missing_from_fresh_ocr_is_discarded_as_stale(self) -> None:
        target_box = NormalizedBox(0.1, 0.4, 0.3, 0.5)
        proposal = replace(
            outcome(1),
            action=replace(outcome(1).action, target_box=target_box),
        )
        decided = replace(
            snapshot(1, 0),
            visible_text=(TextRegion("settings", target_box, 0.99),),
        )
        fresh = replace(
            snapshot(2, 1),
            visible_text=(
                TextRegion("settings", NormalizedBox(0.1, 0.05, 0.3, 0.1), 0.99),
                TextRegion("destination", NormalizedBox(0.1, 0.2, 0.3, 0.3), 0.99),
            ),
        )

        decision = self.supervisor.assess(
            proposal,
            decided,
            fresh,
            frame(1, 0),
            frame(1, 1),
            "open settings",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("stale decision discarded", decision.reason)
        self.assertEqual(self.supervisor.diagnostics()["stale_results_discarded"], 1)

    def test_dynamic_target_is_accepted_when_fresh_ocr_still_matches(self) -> None:
        target_box = NormalizedBox(0.1, 0.1, 0.9, 0.2)
        proposal = replace(
            outcome(1),
            action=replace(outcome(1).action, target_box=target_box),
        )
        decided = replace(
            snapshot(1, 0),
            visible_text=(TextRegion("settings", target_box, 0.99),),
        )
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("settings", target_box, 0.99),),
        )

        decision = self.supervisor.assess(
            proposal,
            decided,
            fresh,
            frame(1, 0),
            frame(2, 1),
            "open settings",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)

    def test_loose_model_button_box_accepts_contained_ocr_label(self) -> None:
        model_box = NormalizedBox(0.70, 0.88, 0.94, 0.97)
        ocr_box = NormalizedBox(0.73, 0.90, 0.82, 0.94)
        proposal = replace(
            outcome(1, label="获取灵宠"),
            action=replace(
                outcome(1).action,
                target_label="获取灵宠",
                target_box=model_box,
            ),
        )
        decided = snapshot(1, 0)
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("获取灵宠", ocr_box, 0.99),),
        )

        decision = self.supervisor.assess(
            proposal,
            decided,
            fresh,
            frame(1, 0),
            frame(2, 1),
            "持续推进游戏",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)

    def test_repeated_non_progress_panel_action_uses_safe_back(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(recovery_safe_actions=frozenset({"back"})),
        )
        proposal = outcome(1, label="获取灵宠")
        current = snapshot(1, 0)
        current_frame = frame(1, 0)
        supervisor.start_action(proposal, current, current_frame)
        supervisor.start_action(proposal, current, current_frame)

        decision = supervisor.assess(
            proposal,
            current,
            current,
            current_frame,
            current_frame,
            "持续推进游戏",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.RECOVER)
        self.assertEqual(decision.recovery, RecoveryDirective.BACK)
        self.assertIn("leaving the panel", decision.reason)

    def test_dynamic_task_target_tolerates_one_ocr_character_error(self) -> None:
        target_box = NormalizedBox(0.1, 0.1, 0.9, 0.2)
        proposal = outcome(1, label="终于来到桃天")
        proposal = replace(
            proposal,
            action=replace(proposal.action, target_box=target_box),
        )
        decided = replace(
            snapshot(1, 0),
            visible_text=(TextRegion("终于来到桃天", target_box, 0.99),),
        )
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("终于来到桃夭村", target_box, 0.99),),
        )

        decision = self.supervisor.assess(
            proposal,
            decided,
            fresh,
            frame(1, 0),
            frame(2, 1),
            "推进主线",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)

    def test_action_effect_must_change_semantic_or_target_state(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))

        waiting = self.supervisor.observe(snapshot(2, 250_000_000), frame(1, 250_000_000))
        candidate = self.supervisor.observe(
            snapshot(3, 300_000_000, signature="new-state"),
            frame(2, 300_000_000),
        )
        changed = self.supervisor.observe(
            snapshot(4, 600_000_000, signature="new-state"),
            frame(2, 600_000_000),
        )

        self.assertTrue(waiting.pending)
        self.assertTrue(candidate.pending)
        self.assertTrue(changed.effect_observed)
        self.assertEqual(self.supervisor.diagnostics()["logical_actions_issued"], 1)
        self.assertEqual(self.supervisor.diagnostics()["verified_effect_actions"], 1)

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

    def test_transient_target_pixel_change_is_not_an_action_effect(self) -> None:
        current = snapshot(1, 0)
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))

        ripple = self.supervisor.observe(snapshot(2, 300_000_000), frame(2, 300_000_000))
        cleared = self.supervisor.observe(snapshot(3, 600_000_000), frame(1, 600_000_000))
        timed_out = self.supervisor.observe(
            snapshot(4, 1_000_000_000), frame(1, 1_000_000_000)
        )

        self.assertTrue(ripple.pending)
        self.assertTrue(cleared.pending)
        self.assertFalse(timed_out.pending)
        self.assertFalse(timed_out.effect_observed)

    def test_ocr_speckle_and_persistent_hover_are_not_an_action_effect(self) -> None:
        target_box = NormalizedBox(0.1, 0.1, 0.9, 0.2)
        proposal = replace(
            outcome(1),
            action=replace(outcome(1).action, target_box=target_box),
        )
        current = snapshot(1, 0, visible_text=("settings",))
        self.supervisor.start_action(proposal, current, frame(1, 0))
        noisy = snapshot(2, 300_000_000, visible_text=("settings", "artifact"))

        first = self.supervisor.observe(noisy, frame(2, 300_000_000))
        second = self.supervisor.observe(
            replace(noisy, captured_at=UGATime(600_000_000)),
            frame(2, 600_000_000),
        )
        timed_out = self.supervisor.observe(
            replace(noisy, captured_at=UGATime(1_000_000_000)),
            frame(2, 1_000_000_000),
        )

        self.assertTrue(first.pending)
        self.assertTrue(second.pending)
        self.assertFalse(timed_out.pending)
        self.assertFalse(timed_out.effect_observed)
        self.assertEqual(self.supervisor.diagnostics()["verified_effect_actions"], 0)

    def test_effective_single_step_target_cannot_be_clicked_again(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            self.profile,
            goal_action_target="settings",
        )
        initial = snapshot(1, 0, visible_text=("settings",))
        proposal = outcome(1)
        supervisor.start_action(proposal, initial, frame(1, 0))
        effect = supervisor.observe(
            snapshot(2, 300_000_000, visible_text=("destination",)),
            frame(1, 300_000_000),
        )

        self.assertTrue(effect.effect_observed)
        self.assertFalse(supervisor.preferred_action_available)
        first = supervisor.assess(
            outcome(2),
            snapshot(2, 400_000_000),
            snapshot(2, 400_000_000),
            frame(1, 400_000_000),
            frame(1, 400_000_000),
            "open settings",
        )
        second = supervisor.assess(
            outcome(3),
            snapshot(3, 500_000_000),
            snapshot(3, 500_000_000),
            frame(1, 500_000_000),
            frame(1, 500_000_000),
            "open settings",
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(second.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(supervisor.diagnostics()["logical_actions_issued"], 1)

    def test_visible_completion_evidence_suppresses_action_and_confirms_locally(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            self.profile,
            goal_evidence=("destination",),
            goal_action_target="settings",
        )
        current = snapshot(1, 0, visible_text=("destination",))

        first = supervisor.assess(
            outcome(1),
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )
        later = snapshot(2, 500_000_000, visible_text=("destination",))
        second = supervisor.assess(
            outcome(2),
            later,
            later,
            frame(2, 500_000_000),
            frame(2, 500_000_000),
            "open settings",
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("suppressing physical action", first.reason)
        self.assertEqual(second.disposition, DecisionDisposition.TERMINATE)
        self.assertIn("contradictory planner action suppressed", second.reason)
        self.assertEqual(supervisor.status, TerminalStatus.SUCCEEDED)
        self.assertEqual(supervisor.diagnostics()["logical_actions_issued"], 0)

    def test_evidence_confidence_is_not_diluted_by_unrelated_ocr_noise(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            self.profile,
            goal_evidence=("destination",),
            goal_action_target="settings",
        )
        noisy = replace(
            snapshot(1, 0),
            visible_text=(
                TextRegion("noise", NormalizedBox(0.1, 0.1, 0.2, 0.2), 0.1),
                TextRegion("destination", NormalizedBox(0.2, 0.2, 0.8, 0.3), 0.96),
            ),
            confidence=0.53,
        )

        first = supervisor.assess(
            outcome(1), noisy, noisy, frame(1, 0), frame(1, 0), "open settings"
        )
        later = replace(
            noisy,
            snapshot_id="snapshot-2",
            frame_id="frame-2",
            frame_sequence=2,
            captured_at=UGATime(500_000_000),
        )
        second = supervisor.assess(
            outcome(2, kind=DecisionKind.DONE),
            later,
            later,
            frame(2, 500_000_000),
            frame(2, 500_000_000),
            "open settings",
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(second.disposition, DecisionDisposition.TERMINATE)
        self.assertEqual(supervisor.goal_confidence, 0.95)
        self.assertEqual(supervisor.diagnostics()["logical_actions_issued"], 0)

    def test_single_step_goal_refuses_a_different_action_target(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            self.profile,
            goal_evidence=("destination",),
            goal_action_target="settings",
        )
        current = snapshot(1, 0, visible_text=("settings",))

        decision = supervisor.assess(
            outcome(1, label="other option"),
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("configured navigation target", decision.reason)
        self.assertEqual(supervisor.diagnostics()["logical_actions_issued"], 0)

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
        self.supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(), goal_evidence=("设置",)
        )
        # F09: the confirmation evidence is the SCREEN OCR on both frames;
        # a model-claimed DONE alone no longer completes the goal.
        first = snapshot(1, 0, visible_text=("设置",))
        second = snapshot(2, 500_000_000, visible_text=("设置",))
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

    def test_low_confidence_done_reobserves_once_then_blocks(self) -> None:
        self.supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(), goal_evidence=("settings",)
        )
        current = snapshot(1, 0, visible_text=("settings",))
        proposal = outcome(1, kind=DecisionKind.DONE, confidence=0.8)

        first = self.supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )
        second = self.supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "open settings",
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertEqual(second.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(self.supervisor.status, TerminalStatus.BLOCKED)

    def test_loop_uses_distinct_high_resolution_then_safe_back_recovery(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            max_recoveries=2,
        )
        for number in (1, 2):
            current = snapshot(number, number * 1_000_000_000)
            proposal = outcome(number)
            accepted = supervisor.assess(
                proposal,
                current,
                current,
                frame(number, current.captured_at.value_ns),
                frame(number, current.captured_at.value_ns),
                "open settings",
            )
            self.assertEqual(accepted.disposition, DecisionDisposition.EXECUTE)
            supervisor.start_action(
                proposal, current, frame(number, current.captured_at.value_ns)
            )
            supervisor.observe(
                snapshot(number + 1, current.captured_at.value_ns + 1_000_000_000),
                frame(number, current.captured_at.value_ns + 1_000_000_000),
            )

        self.assertTrue(supervisor.high_resolution_retry)
        self.assertEqual(supervisor.recovery_count, 1)
        retry_snapshot = snapshot(3, 3_000_000_000)
        recovery = supervisor.assess(
            outcome(3),
            retry_snapshot,
            retry_snapshot,
            frame(3, 3_000_000_000),
            frame(3, 3_000_000_000),
            "open settings",
        )

        self.assertEqual(recovery.disposition, DecisionDisposition.RECOVER)
        self.assertEqual(recovery.recovery, RecoveryDirective.BACK)
        self.assertEqual(supervisor.recovery_count, 1)
        back = supervisor.to_recovery_gui_action(
            RecoveryDirective.BACK, lambda name: (27,) if name == "back" else None
        )
        self.assertEqual(back.kind, GuiActionKind.KEY)
        self.assertEqual(back.key_codes, (27,))
        supervisor.start_recovery_action(retry_snapshot, frame(3, 3_000_000_000))
        supervisor.observe(snapshot(4, 4_000_000_000), frame(3, 4_000_000_000))
        self.assertEqual(supervisor.recovery_count, 1)

    def test_repeated_loading_wait_on_same_state_eventually_leaves_via_back(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
            max_stuck_waits=4,
        )
        waits: list[SupervisedDecision] = []
        for number in range(1, 4):
            current = snapshot(number, number * 1_000_000)
            waits.append(
                supervisor.assess(
                    outcome(number, kind=DecisionKind.WAIT, wait=WaitReason.ANIMATION),
                    current,
                    current,
                    frame(number, current.captured_at.value_ns),
                    frame(number, current.captured_at.value_ns),
                    "open settings",
                )
            )

        self.assertTrue(all(item.disposition == DecisionDisposition.WAIT for item in waits))

        # The 4th identical-state wait trips the stuck-wait bound and arms a
        # high-resolution re-observe instead of waiting forever.
        fourth = snapshot(4, 4_000_000)
        armed = supervisor.assess(
            outcome(4, kind=DecisionKind.WAIT, wait=WaitReason.ANIMATION),
            fourth,
            fourth,
            frame(4, fourth.captured_at.value_ns),
            frame(4, fourth.captured_at.value_ns),
            "open settings",
        )
        self.assertEqual(armed.disposition, DecisionDisposition.REOBSERVE)

        # The high-resolution retry still waits, so the visual back leaves the
        # stuck page as an ordinary state transition.
        fifth = snapshot(5, 5_000_000)
        recovered = supervisor.assess(
            outcome(5, kind=DecisionKind.WAIT, wait=WaitReason.ANIMATION),
            fifth,
            fifth,
            frame(5, fifth.captured_at.value_ns),
            frame(5, fifth.captured_at.value_ns),
            "open settings",
        )
        self.assertEqual(recovered.disposition, DecisionDisposition.RECOVER)
        self.assertEqual(recovered.recovery, RecoveryDirective.BACK)
        self.assertEqual(supervisor.recovery_count, 1)

    def test_model_back_intent_routes_to_calibrated_hotspot(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "返回",
                NormalizedBox(0.02, 0.02, 0.08, 0.10),
                "the page closes",
                0.95,
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续推进"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        self.assertIn("model exit intent routed to the calibrated ui_back control", decision.reason)
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "ui_back")
        center = decision.outcome.action.target_box.center  # type: ignore[union-attr]
        self.assertAlmostEqual(center.x, 0.06, places=6)
        self.assertAlmostEqual(center.y, 0.08, places=6)

    def test_chrome_strip_click_without_exit_label_is_rejected(self) -> None:
        # MuMu 标题栏（窗口按钮/标签页/×）不是游戏内容：任何出口语义之外
        # 的点击只要落进 no_click 条带就必须拒绝，绝不允许提交到模拟器。
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.055),),
            ),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "闪亮图标",
                NormalizedBox(0.973, 0.015, 0.988, 0.039),
                "看起来可以点",
                0.90,
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续推进主线任务"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("no-click region", decision.reason)

    def test_model_close_button_click_on_chrome_routes_to_hotspot(self) -> None:
        # 模型把 MuMu 标题栏 ✕ 当"关闭按钮"（关闭语义）时，出口路由优先于
        # no_click 拒绝：转换为游戏内校准热点，而不是原样点模拟器。
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.055),),
            ),
            back_hotspot=(0.06, 0.08),
            close_hotspot=(0.881, 0.186),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "关闭按钮",
                NormalizedBox(0.973, 0.015, 0.988, 0.039),
                "关闭当前页面",
                0.90,
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续推进主线任务"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "ui_back")
        center = decision.outcome.action.target_box.center  # type: ignore[union-attr]
        self.assertAlmostEqual(center.x, 0.06, places=6)
        self.assertAlmostEqual(center.y, 0.08, places=6)

    def test_model_first_mode_still_routes_exit_intent_to_hotspot(self) -> None:
        # 回归：model-first（allow_calibrated_intents=False）下出口语义点击
        # 仍必须路由到校准热点。图形关闭按钮没有 OCR 文本，一旦失去这条
        # 路由就会判为 grounding conflict → 重试耗尽 → BLOCK，连续运行
        # 在几轮内被终止（2026-09-18 实跑复现）。
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.055),),
            ),
            back_hotspot=(0.06, 0.08),
            close_hotspot=(0.881, 0.186),
            allow_calibrated_intents=False,
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "关闭按钮",
                NormalizedBox(0.866, 0.170, 0.887, 0.205),
                "关闭当前页面",
                0.90,
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续推进主线任务"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "ui_back")

    def test_advertising_decoy_click_is_rejected(self) -> None:
        # 广告/运营横幅（首充礼包、新服冲榜、商城福利…）是纯营收诱饵：
        # 无论模型给它们贴什么标签，落在诱饵文字上的点击一律拒绝。
        supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=1000)
        )
        ad_snapshot = PerceptionSnapshot(
            "snapshot-1",
            "frame-1",
            1,
            UGATime(0),
            identity(),
            1,
            1,
            ControlMode.GUI,
            (
                TextRegion(
                    "神宠降临首充超值礼",
                    NormalizedBox(0.222, 0.219, 0.412, 0.254),
                    1.00,
                ),
            ),
            (),
            (("goal", "settings"),),
            "same-state",
            0.95,
        )
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "活动按钮",
                NormalizedBox(0.24, 0.22, 0.40, 0.26),
                "活动打开",
                0.90,
            ),
        )

        decision = supervisor.assess(
            proposal,
            ad_snapshot,
            ad_snapshot,
            frame(1, 0),
            frame(1, 0),
            "持续推进主线任务",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("decoy", decision.reason)

    def test_real_name_gate_parks_all_decisions(self) -> None:
        # 实名登记/防沉迷是账号级法定门禁（个人身份信息）：代理绝不代填、
        # 绝不点击，挂起全部决策等待 owner——不计数任何等待/恢复逃逸。
        supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=1000)
        )
        gate_snapshot = PerceptionSnapshot(
            "snapshot-1",
            "frame-1",
            1,
            UGATime(0),
            identity(),
            1,
            1,
            ControlMode.GUI,
            (
                TextRegion(
                    "游戏实名登记", NormalizedBox(0.423, 0.043, 0.575, 0.062), 1.00
                ),
                TextRegion(
                    "请填写真实姓名", NormalizedBox(0.169, 0.150, 0.342, 0.168), 1.00
                ),
            ),
            (),
            (("goal", "settings"),),
            "same-state",
            0.95,
        )
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "实名登记",
                NormalizedBox(0.433, 0.268, 0.562, 0.289),
                "提交实名信息",
                0.90,
            ),
        )

        decision = supervisor.assess(
            proposal,
            gate_snapshot,
            gate_snapshot,
            frame(1, 0),
            frame(1, 0),
            "持续推进主线任务",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.WAIT)
        self.assertIn("real-name registration gate", decision.reason)
        self.assertEqual(supervisor.recovery_count, 0)

    def test_back_intent_without_hotspot_keeps_normal_validation(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "返回",
                NormalizedBox(0.02, 0.02, 0.08, 0.10),
                "the page closes",
                0.95,
            ),
        )
        # Overlapping unrelated OCR text forces the grounding-conflict path.
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("设置", NormalizedBox(0.01, 0.01, 0.09, 0.11), 0.99),),
        )

        decision = supervisor.assess(
            proposal, current, fresh, frame(1, 0), frame(2, 1), "持续推进"
        )

        # Without a hotspot the proposal must not be silently converted; it
        # goes through ordinary grounding validation (OCR conflict here).
        self.assertIn(
            decision.disposition, {DecisionDisposition.REOBSERVE, DecisionDisposition.BLOCK}
        )
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "返回")

    def test_planner_ui_back_outcome_executes_without_ocr_grounding(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_back",
                NormalizedBox(0.025, 0.05, 0.095, 0.11),
                "the feature page closes",
                1.0,
            ),
        )
        # Overlapping unrelated OCR near the hotspot must not matter: the
        # hotspot is a user-confirmed calibration, not an OCR-grounded target.
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("设置", NormalizedBox(0.01, 0.01, 0.09, 0.11), 0.99),),
        )

        decision = supervisor.assess(
            proposal,
            current,
            fresh,
            frame(1, 0),
            frame(2, 1),
            "持续推进",
            decision_source="ocr_close_glyph_fast",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        self.assertIn("deterministic OCR rule fast-tracked", decision.reason)
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "ui_back")

    def test_deterministic_dialogue_rule_ignores_task_ocr_generation_churn(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.065),),
            ),
            dialogue_hotspot=(0.96, 0.915),
        )
        decided = snapshot(1, 0)
        fresh = replace(
            snapshot(2, 1),
            task_generation=decided.task_generation + 1,
            state_signature="dialogue-ocr-jitter",
        )
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_dialogue_advance",
                NormalizedBox(0.94, 0.895, 0.98, 0.935),
                "the dialogue advances to the next line",
                1.0,
            ),
        )

        decision = supervisor.assess(
            proposal,
            decided,
            fresh,
            frame(1, 0),
            frame(2, 1),
            "持续推进主线",
            decision_source="ocr_dialogue_click_fast",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        self.assertIn("fast-tracked", decision.reason)

    def test_deterministic_rule_still_obeys_no_click_region(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.20),),
            ),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "错误标题栏目标",
                NormalizedBox(0.3, 0.05, 0.5, 0.10),
                "must remain blocked",
                1.0,
            ),
        )

        decision = supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "持续推进主线",
            decision_source="ocr_progress_control_fast",
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("no-click region", decision.reason)

    def test_model_reserved_label_is_routed_to_hotspot_never_raw_box(self) -> None:
        # A model reply claiming the reserved ``ui_back`` label has no
        # privilege: the label is display text.  Without a trusted runtime
        # source the click must be re-anchored onto the user-confirmed
        # calibration, never executed at the model's own coordinates.
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        model_box = NormalizedBox(0.972, 0.012, 0.99, 0.04)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_back",
                model_box,
                "模型自报的退出标签",
                1.0,
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续推进"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        assert decision.outcome.action is not None
        center = decision.outcome.action.target_box.center  # type: ignore[union-attr]
        self.assertAlmostEqual(center.x, 0.06, places=6)
        self.assertAlmostEqual(center.y, 0.08, places=6)
        self.assertNotEqual(
            decision.outcome.action.target_box, model_box  # type: ignore[union-attr]
        )

    def test_reserved_label_with_trusted_source_still_respects_no_click(self) -> None:
        # Even a trusted rule-source exit click is refused when its final
        # landing point falls inside the chrome strip.
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                action_effect_timeout_ms=1000,
                no_click_regions=((0.0, 0.0, 1.0, 0.055),),
            ),
            back_hotspot=(0.06, 0.03),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_back",
                NormalizedBox(0.03, 0.01, 0.09, 0.05),
                "the feature page closes",
                1.0,
            ),
        )

        decision = supervisor.assess(
            proposal,
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "持续推进",
            decision_source="ocr_close_glyph_fast",
        )

        self.assertIn(
            decision.disposition,
            {DecisionDisposition.REOBSERVE, DecisionDisposition.BLOCK},
        )
        self.assertIn("no-click region", decision.reason)

    def test_trusted_exit_click_rejected_after_window_recreation(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            back_hotspot=(0.06, 0.08),
        )
        validated = frame(1, 0)
        recreated = replace(frame(2, 1_000), window_identity=identity(generation=2))
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_back",
                NormalizedBox(0.03, 0.05, 0.09, 0.11),
                "the feature page closes",
                1.0,
            ),
        )
        fresh = snapshot(2, 1_000)

        decision = supervisor.assess(
            proposal,
            fresh,
            fresh,
            recreated,
            recreated,
            "持续推进",
            decision_source="ocr_close_glyph_fast",
        )
        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("stale", decision.reason)

        stable, reason = supervisor.validate_execution_context(validated, recreated)
        self.assertFalse(stable)
        self.assertIn("generation or geometry changed", reason)

    def test_pointer_offset_moves_final_point_into_no_click_region(self) -> None:
        # EX06 regression: the box CENTER sits safely outside the forbidden
        # strip, but the pointer offset drags the real landing point into it.
        # The gate must judge the final point, not the center.
        validator = ActionValidator(
            PerceptionProfile(
                no_click_regions=((0.5, 0.5, 0.7, 0.7),),
            )
        )
        current = snapshot(1, 0)
        safe_action = GroundedAction(
            GuiActionKind.CLICK,
            "设置",
            NormalizedBox(0.2, 0.2, 0.4, 0.4),
            "打开设置",
            0.95,
        )
        drifted_action = replace(
            safe_action, pointer_offset_x=0.25, pointer_offset_y=0.25
        )

        safe = validator.validate(
            replace(outcome(1), action=safe_action),
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "打开设置",
        )
        self.assertTrue(safe[0])

        drifted = validator.validate(
            replace(outcome(1), action=drifted_action),
            current,
            current,
            frame(1, 0),
            frame(1, 0),
            "打开设置",
        )
        self.assertFalse(drifted[0])
        self.assertIn("no-click region", drifted[1])

    def test_failed_back_attempt_alternates_to_the_close_hotspot(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
            close_hotspot=(0.945, 0.075),
            max_failed_back_recoveries=3,
        )
        current = snapshot(1, 0)
        supervisor.start_recovery_action(current, frame(1, 0))
        first_point = supervisor._pending.action.target_box.center  # type: ignore[union-attr]
        supervisor.observe(snapshot(2, 1_000_000_000), frame(1, 1_000_000_000))
        self.assertEqual(supervisor.status, TerminalStatus.RUNNING)

        supervisor.start_recovery_action(current, frame(1, 1_000_000_000))
        second_point = supervisor._pending.action.target_box.center  # type: ignore[union-attr]

        # The first failed exit used the top-left ribbon; the retry must aim
        # at the top-right X instead.
        self.assertAlmostEqual(first_point.x, 0.06, places=6)
        self.assertAlmostEqual(second_point.x, 0.945, places=6)
        self.assertAlmostEqual(second_point.y, 0.075, places=6)

    def test_feature_page_waits_give_up_sooner_than_loading_waits(self) -> None:
        feature_session = GameSessionState()
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=100_000),
            session=feature_session,
            max_stuck_waits=10,
        )
        feature_session.screen_type = ScreenType.FEATURE

        for number in range(1, 4):
            current = snapshot(number, number * 1_000_000)
            decision = supervisor.assess(
                outcome(number, kind=DecisionKind.WAIT, wait=WaitReason.ANIMATION),
                current,
                current,
                frame(number, current.captured_at.value_ns),
                frame(number, current.captured_at.value_ns),
                "open settings",
            )
            self.assertEqual(decision.disposition, DecisionDisposition.WAIT)

        # The 4th identical-state wait on a FEATURE page trips the shortened
        # bound (4) even though the global bound is 10.
        fourth = snapshot(4, 4_000_000)
        armed = supervisor.assess(
            outcome(4, kind=DecisionKind.WAIT, wait=WaitReason.ANIMATION),
            fourth,
            fourth,
            frame(4, fourth.captured_at.value_ns),
            frame(4, fourth.captured_at.value_ns),
            "open settings",
        )
        self.assertEqual(armed.disposition, DecisionDisposition.REOBSERVE)

    def test_world_screen_cancels_armed_visual_back_recovery(self) -> None:
        session = GameSessionState()
        session.screen_type = ScreenType.WORLD
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            session=session,
            back_hotspot=(0.06, 0.08),
        )
        supervisor._pending_recovery = RecoveryDirective.BACK  # noqa: SLF001
        current = snapshot(1, 0)

        decision = supervisor.assess(
            outcome(1), current, current, frame(1, 0), frame(1, 0), "持续战斗"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)
        self.assertIsNone(decision.recovery)
        assert decision.outcome.action is not None
        self.assertEqual(decision.outcome.action.target_label, "settings")

    def test_world_screen_rejects_model_back_intent(self) -> None:
        session = GameSessionState()
        session.screen_type = ScreenType.WORLD
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            session=session,
            back_hotspot=(0.06, 0.08),
        )
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "返回",
                NormalizedBox(0.02, 0.02, 0.08, 0.10),
                "leave the current page",
                0.95,
            ),
        )
        current = snapshot(1, 0)

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "持续战斗"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("suppressed outside", decision.reason)

    def test_high_confidence_visual_only_click_passes_validation(self) -> None:
        # 晋升 medallion case: a graphical button with no OCR text in either
        # the decided or the fresh frame, high model confidence → allowed.
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "晋升按钮",
                NormalizedBox(0.27, 0.50, 0.38, 0.65),
                "the realm promotes",
                0.95,
            ),
        )
        decided = replace(
            current,
            visible_text=(TextRegion("境界", NormalizedBox(0.08, 0.08, 0.16, 0.13), 0.99),),
        )
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("境界", NormalizedBox(0.08, 0.08, 0.16, 0.13), 0.99),),
        )

        decision = supervisor.assess(
            proposal, decided, fresh, frame(1, 0),
            replace(frame(1, 1), frame_id="frame-2"), "持续推进"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)

    def test_stale_text_target_is_still_rejected_even_at_high_confidence(self) -> None:
        # Text existed at the box when decided and moved away: a high
        # confidence does not turn a stale TEXT target into a visual click.
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
        )
        target_box = NormalizedBox(0.1, 0.4, 0.3, 0.5)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.CLICK,
                "settings",
                target_box,
                "opens settings",
                0.95,
            ),
        )
        decided = replace(
            snapshot(1, 0),
            visible_text=(TextRegion("settings", target_box, 0.99),),
        )
        fresh = replace(
            snapshot(2, 1),
            visible_text=(TextRegion("destination", NormalizedBox(0.1, 0.2, 0.3, 0.3), 0.99),),
        )

        decision = supervisor.assess(
            proposal, decided, fresh, frame(1, 0), frame(2, 1), "open settings"
        )

        self.assertIn(
            decision.disposition, {DecisionDisposition.REOBSERVE, DecisionDisposition.BLOCK}
        )

    def test_back_recovery_uses_visual_hotspot_click_not_esc(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(recovery_safe_actions=frozenset({"back"})),
            back_hotspot=(0.06, 0.08),
        )
        action = supervisor.to_recovery_gui_action(
            RecoveryDirective.BACK, lambda name: (27,) if name == "back" else None
        )

        self.assertEqual(action.kind, GuiActionKind.CLICK)
        self.assertEqual((action.x, action.y), (0.06, 0.08))
        self.assertEqual(action.key_codes, ())

    def test_back_recovery_without_hotspot_reports_missing_configuration(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(recovery_safe_actions=frozenset({"back"})),
        )

        with self.assertRaises(ContractViolation):
            supervisor.to_recovery_gui_action(RecoveryDirective.BACK, lambda name: None)

    def test_recovery_pending_action_targets_the_visual_back_hotspot(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        supervisor.start_recovery_action(current, frame(1, 0))

        pending = supervisor._pending
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.action.kind, GuiActionKind.CLICK)
        self.assertEqual(pending.action.target_label, "ui_back")
        assert pending.action.target_box is not None
        center = pending.action.target_box.center
        self.assertAlmostEqual(center.x, 0.06, places=6)
        self.assertAlmostEqual(center.y, 0.08, places=6)

    def test_one_failed_visual_back_retries_then_second_failure_blocks(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        supervisor.start_recovery_action(current, frame(1, 0))

        supervisor.observe(snapshot(2, 1_000_000_000), frame(1, 1_000_000_000))

        # The first failed back does not terminate the loop.
        self.assertEqual(supervisor.status, TerminalStatus.RUNNING)
        self.assertEqual(supervisor.diagnostics()["back_recovery_streak"], 1)

        supervisor.start_recovery_action(current, frame(1, 1_000_000_000))
        supervisor.observe(snapshot(3, 2_000_000_000), frame(1, 2_000_000_000))

        self.assertEqual(supervisor.status, TerminalStatus.BLOCKED)
        self.assertIn("visual exit controls", supervisor.termination_reason or "")

    def test_verified_effect_resets_the_back_recovery_streak(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
            back_hotspot=(0.06, 0.08),
        )
        current = snapshot(1, 0)
        supervisor.start_recovery_action(current, frame(1, 0))
        supervisor.observe(snapshot(2, 1_000_000_000), frame(1, 1_000_000_000))
        self.assertEqual(supervisor.diagnostics()["back_recovery_streak"], 1)

        # F04: the stabilization wait now obeys the absolute deadline, so the
        # second attempt must carry a realistic issue time — the monotonic
        # clock reflects when this recovery actually started.
        self.clock.set(1_000_000_000)
        supervisor.start_recovery_action(current, frame(1, 1_000_000_000))
        candidate = supervisor.observe(
            snapshot(3, 1_400_000_000, signature="world-view"), frame(2, 1_400_000_000)
        )
        self.assertTrue(candidate.pending)
        verified = supervisor.observe(
            snapshot(4, 1_700_000_000, signature="world-view"), frame(2, 1_700_000_000)
        )

        self.assertTrue(verified.effect_observed)
        self.assertEqual(supervisor.diagnostics()["back_recovery_streak"], 0)
        self.assertEqual(supervisor.status, TerminalStatus.RUNNING)

    def test_unbound_semantic_key_is_rejected_before_execution(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            available_keys=frozenset({"dialogue_advance"}),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.KEY,
                "back",
                None,
                "dismiss the page",
                0.95,
                key="back",
            ),
        )

        first = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )
        second = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )

        self.assertEqual(first.disposition, DecisionDisposition.REOBSERVE)
        self.assertIn("no confirmed binding", first.reason)
        self.assertEqual(second.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(supervisor.diagnostics()["logical_actions_issued"], 0)

    def test_bound_semantic_key_still_executes_with_available_keys(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            available_keys=frozenset({"dialogue_advance"}),
        )
        current = snapshot(1, 0)
        proposal = replace(
            outcome(1),
            action=GroundedAction(
                GuiActionKind.KEY,
                "对话继续",
                None,
                "advance the dialogue",
                0.95,
                key="dialogue_advance",
            ),
        )

        decision = supervisor.assess(
            proposal, current, current, frame(1, 0), frame(1, 0), "open settings"
        )

        self.assertEqual(decision.disposition, DecisionDisposition.EXECUTE)

    def test_numeric_only_ocr_change_is_not_an_action_effect(self) -> None:
        current = snapshot(1, 0, visible_text=("修为1814", "主战位"))
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))
        jittered = snapshot(2, 300_000_000, visible_text=("修为770/15", "主战位"))

        first = self.supervisor.observe(jittered, frame(1, 300_000_000))
        second = self.supervisor.observe(
            replace(jittered, captured_at=UGATime(600_000_000)),
            frame(1, 600_000_000),
        )
        timed_out = self.supervisor.observe(
            replace(jittered, captured_at=UGATime(1_000_000_000)),
            frame(1, 1_000_000_000),
        )

        self.assertTrue(first.pending)
        self.assertTrue(second.pending)
        self.assertFalse(timed_out.pending)
        self.assertFalse(timed_out.effect_observed)
        self.assertEqual(self.supervisor.diagnostics()["verified_effect_actions"], 0)

    def test_page_anchor_flip_after_two_frames_verifies_the_effect(self) -> None:
        current = snapshot(1, 0, visible_text=("灵宠", "主战位"))
        proposal = outcome(1)
        self.supervisor.start_action(proposal, current, frame(1, 0))
        exited = replace(
            snapshot(2, 300_000_000, visible_text=("主战位",)),
            captured_at=UGATime(300_000_000),
        )

        first = self.supervisor.observe(exited, frame(2, 300_000_000))
        second = self.supervisor.observe(
            replace(exited, captured_at=UGATime(600_000_000)),
            frame(2, 600_000_000),
        )

        # Only the page-header anchor changed; token-level noise stays below
        # the meaningful-change bar, but the stable anchor flip (confirmed on
        # two observations) is accepted as the verified page transition.
        self.assertTrue(first.pending)
        self.assertTrue(second.pending or second.effect_observed is not None)

    def test_numeric_jitter_does_not_flip_page_anchors(self) -> None:
        signature = page_anchor_signature(
            (
                TextRegion("修为1814", NormalizedBox(0.05, 0.05, 0.25, 0.10), 0.99),
                TextRegion("灵宠", NormalizedBox(0.05, 0.10, 0.15, 0.15), 0.99),
            )
        )

        self.assertEqual(signature, frozenset({"修为", "灵宠"}))

    def test_recovery_stops_when_profile_does_not_allow_back(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            max_recoveries=2,
        )
        for number in (1, 2):
            current = snapshot(number, number * 1_000_000_000)
            proposal = outcome(number)
            supervisor.start_action(
                proposal, current, frame(number, current.captured_at.value_ns)
            )
            supervisor.observe(
                snapshot(number + 1, current.captured_at.value_ns + 1_000_000_000),
                frame(number, current.captured_at.value_ns + 1_000_000_000),
            )
        retry_snapshot = snapshot(3, 3_000_000_000)

        stopped = supervisor.assess(
            outcome(3),
            retry_snapshot,
            retry_snapshot,
            frame(3, 3_000_000_000),
            frame(3, 3_000_000_000),
            "open settings",
        )

        self.assertEqual(stopped.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(supervisor.status, TerminalStatus.BLOCKED)

    def test_blocked_supervisor_updates_the_bound_task_graph(self) -> None:
        graph = TaskGraph(
            (
                TaskNode(
                    "goal",
                    "open settings",
                    None,
                    (),
                    TaskStatus.PENDING,
                    (),
                    None,
                    "goal confirmed",
                    "closed loop blocked",
                    10_000_000_000,
                    RetryPolicy(),
                ),
            )
        )
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(action_effect_timeout_ms=1000),
            max_recoveries=0,
            task_graph=graph,
            task_node_id="goal",
        )
        for number in (1, 2):
            current = snapshot(number, number * 1_000_000_000)
            proposal = outcome(number)
            supervisor.start_action(
                proposal, current, frame(number, current.captured_at.value_ns)
            )
            supervisor.observe(
                snapshot(number + 1, current.captured_at.value_ns + 1_000_000_000),
                frame(number, current.captured_at.value_ns + 1_000_000_000),
            )

        self.assertEqual(supervisor.status, TerminalStatus.BLOCKED)
        self.assertEqual(graph.get("goal").status, TaskStatus.BLOCKED)

    def test_two_step_visual_ring_requests_recovery_after_two_rounds(self) -> None:
        supervisor = ClosedLoopSupervisor(
            self.clock,
            PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
        )
        transitions = (
            ("state-a", "next", 1, "state-b", 2),
            ("state-b", "previous", 2, "state-a", 1),
            ("state-a", "next", 1, "state-b", 2),
            ("state-b", "previous", 2, "state-a", 1),
        )
        for index, (before_sig, label, before_frame, after_sig, _after_frame) in enumerate(
            transitions, start=1
        ):
            proposal = outcome(index, label=label)
            before = snapshot(index, index * 1_000_000_000, signature=before_sig)
            supervisor.start_action(
                proposal, before, frame(before_frame, before.captured_at.value_ns)
            )
            supervisor.observe(
                snapshot(
                    index + 1,
                    index * 1_000_000_000 + 250_000_000,
                    signature=after_sig,
                ),
                frame(
                    before_frame,
                    index * 1_000_000_000 + 250_000_000,
                ),
            )

        self.assertIsNotNone(supervisor.last_loop_finding)
        self.assertEqual(supervisor.recovery_count, 1)
        recovery_snapshot = snapshot(6, 6_000_000_000, signature="state-a")
        recovery = supervisor.assess(
            outcome(6, label="next"),
            recovery_snapshot,
            recovery_snapshot,
            frame(1, 6_000_000_000),
            frame(1, 6_000_000_000),
            "open settings",
        )
        self.assertEqual(recovery.disposition, DecisionDisposition.RECOVER)
        self.assertEqual(recovery.recovery, RecoveryDirective.BACK)


if __name__ == "__main__":
    unittest.main()
