from __future__ import annotations

import unittest

from tests.helpers import identity
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.evaluation.grounding import GroundingEvaluator, GroundingPrediction, GroundingSample
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
from uga.time.clock import UGATime


class GroundedLoopContractTests(unittest.TestCase):
    def test_pointer_action_is_derived_from_target_box(self) -> None:
        box = NormalizedBox(0.2, 0.3, 0.6, 0.7)
        action = GroundedAction(
            GuiActionKind.CLICK,
            "设置",
            box,
            "打开设置页面",
            0.94,
            ActionRisk.LOW,
        )
        outcome = PlannerOutcome(
            "decision-1",
            "frame-1",
            3,
            1,
            4,
            2,
            DecisionKind.ACT,
            "Android 主页面",
            ("设置",),
            GoalStatus.IN_PROGRESS,
            0.94,
            action,
        )

        self.assertEqual(box.center.x, 0.4)
        self.assertEqual(box.center.y, 0.5)
        self.assertEqual(outcome.action, action)

    def test_wait_done_and_abstain_cannot_carry_physical_action(self) -> None:
        wait = PlannerOutcome(
            "wait-1",
            "frame-1",
            1,
            1,
            1,
            1,
            DecisionKind.WAIT,
            "页面加载中",
            ("加载中",),
            GoalStatus.IN_PROGRESS,
            0.91,
            wait_reason=WaitReason.LOADING,
        )
        self.assertIsNone(wait.action)
        with self.assertRaisesRegex(ContractViolation, "only ACT"):
            PlannerOutcome(
                "bad",
                "frame-1",
                1,
                1,
                1,
                1,
                DecisionKind.ABSTAIN,
                "不确定",
                (),
                GoalStatus.UNKNOWN,
                0.2,
                GroundedAction(
                    GuiActionKind.CLICK,
                    "未知按钮",
                    NormalizedBox(0.1, 0.1, 0.2, 0.2),
                    "未知",
                    0.2,
                ),
            )

    def test_done_requires_succeeded_goal(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "DONE"):
            PlannerOutcome(
                "done-1",
                "frame-1",
                1,
                1,
                1,
                1,
                DecisionKind.DONE,
                "设置页面",
                ("网络和互联网",),
                GoalStatus.UNKNOWN,
                0.9,
            )

    def test_perception_snapshot_exposes_ocr_text(self) -> None:
        snapshot = PerceptionSnapshot(
            "snapshot-1",
            "frame-1",
            7,
            UGATime(100),
            identity(generation=2),
            3,
            1,
            ControlMode.GUI,
            (TextRegion("网络和互联网", NormalizedBox(0.1, 0.2, 0.5, 0.3), 0.98),),
            (),
            (("goal_visible", "true"),),
            "signature",
            0.96,
        )

        self.assertEqual(snapshot.text, ("网络和互联网",))


class GroundingEvaluatorTests(unittest.TestCase):
    def test_reports_action_text_box_and_false_act_metrics(self) -> None:
        target = NormalizedBox(0.2, 0.2, 0.4, 0.4)
        samples = (
            GroundingSample("act", DecisionKind.ACT, ("open settings",), target),
            GroundingSample(
                "done",
                DecisionKind.DONE,
                ("settings",),
                None,
                frozenset({DecisionKind.ACT}),
            ),
        )
        predictions = (
            GroundingPrediction(
                DecisionKind.ACT,
                ("open settings",),
                NormalizedBox(0.22, 0.22, 0.38, 0.38),
            ),
            GroundingPrediction(DecisionKind.ACT, ("settings",), target),
        )

        metrics = GroundingEvaluator().evaluate(samples, predictions)

        self.assertEqual(metrics.samples, 2)
        self.assertEqual(metrics.schema_valid_rate, 1.0)
        self.assertEqual(metrics.decision_kind_accuracy, 0.5)
        self.assertEqual(metrics.action_kind_accuracy, 0.5)
        self.assertEqual(metrics.box_hit_rate, 1.0)
        self.assertEqual(metrics.false_act_rate, 1.0)
        self.assertEqual(metrics.forbidden_action_count, 1)
        self.assertEqual(metrics.wrong_window_count, 0)

    def test_normalized_text_metric_handles_chinese_without_spaces(self) -> None:
        samples = (
            GroundingSample("cn", DecisionKind.DONE, ("网络和互联网",), None),
        )
        predictions = (
            GroundingPrediction(DecisionKind.DONE, ("网络 和 互联网",), None),
        )

        metrics = GroundingEvaluator().evaluate(samples, predictions)

        self.assertEqual(metrics.text_f1, 1.0)


if __name__ == "__main__":
    unittest.main()
