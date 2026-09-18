from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_execution_receipt import _receipt
from tests.unit.test_grounded_vlm import _reply, _snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.control.execution_receipt import ExecutionPrimitiveStatus
from uga.environment.profile import PerceptionProfile
from uga.perception.schema import GuiEffect
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vlm_planner import PlannerReplyError
from uga.time.clock import ManualClock


class GuiEffectFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(0)
        self.supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=2000)
        )

    def start(
        self, effect: GuiEffect | None = None, *, texts: tuple[str, ...] = ("settings",)
    ) -> None:
        proposed = outcome(1)
        proposed = replace(proposed, action=replace(proposed.action, effect=effect))
        self.supervisor.start_action(proposed, snapshot(1, 0, visible_text=texts), frame(1, 0))

    def test_explicit_text_must_be_new_and_confirmed_on_two_fresh_frames(self) -> None:
        self.start(GuiEffect("text_appears", "Sound"))
        first = snapshot(2, 300_000_000, visible_text=("Sound",))
        self.assertIsNone(self.supervisor.observe(first, frame(2, 300_000_000)).effect_observed)
        self.clock.set(800_000_000)
        self.assertIsNone(self.supervisor.observe(first, frame(2, 300_000_000)).effect_observed)
        confirmed = self.supervisor.observe(
            snapshot(3, 900_000_000, visible_text=("Sound",)), frame(3, 900_000_000)
        )
        self.assertTrue(confirmed.effect_observed)
        self.assertIn("Sound", confirmed.detail)

    def test_unrelated_notification_is_not_expected_effect(self) -> None:
        self.start(GuiEffect("text_appears", "Sound"))
        for number, timestamp in [(2, 300_000_000), (3, 700_000_000)]:
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=("settings", "New reward")),
                frame(number, timestamp),
            )
            self.assertIsNone(result.effect_observed)
        expired = self.supervisor.observe(
            snapshot(4, 2_100_000_000, visible_text=("New reward",)), frame(4, 2_100_000_000)
        )
        self.assertFalse(expired.effect_observed)
        feedback = json.loads(self.supervisor.planner_feedback(1))["recent_actions"][-1]
        self.assertEqual(feedback["status"], "ineffective")

    def test_existing_text_cannot_be_reused_as_new_effect(self) -> None:
        self.start(GuiEffect("text_appears", "settings"))
        for number, timestamp in [(2, 300_000_000), (3, 900_000_000), (4, 2_100_000_000)]:
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=("settings",)), frame(number, timestamp)
            )
            self.assertIsNot(result.effect_observed, True)

    def test_empty_ocr_does_not_prove_text_disappeared(self) -> None:
        self.start(GuiEffect("text_disappears", "settings"))
        for number, timestamp in [(2, 300_000_000), (3, 900_000_000), (4, 2_100_000_000)]:
            result = self.supervisor.observe(snapshot(number, timestamp), frame(number, timestamp))
            self.assertIsNot(result.effect_observed, True)

    def test_effect_frame_must_follow_last_not_first_primitive_receipt(self) -> None:
        proposed = outcome(1)
        self.supervisor.start_action(
            proposed,
            snapshot(1, 0),
            frame(1, 0),
            submitted_action_ids=frozenset({"down", "up"}),
            expected_primitives=2,
        )
        self.supervisor.record_execution_receipts(
            [
                _receipt("down", ExecutionPrimitiveStatus.EXECUTED, 100_000_000),
                _receipt("up", ExecutionPrimitiveStatus.EXECUTED, 800_000_000),
            ]
        )
        self.clock.set(1_200_000_000)
        observed = self.supervisor.observe(
            snapshot(2, 500_000_000, visible_text=("Sound",)), frame(2, 500_000_000)
        )
        self.assertIsNone(observed.effect_observed)

    def test_effect_schema_and_local_parser_agree_and_reject_extra_commands(self) -> None:
        data = json.loads(_reply())
        data["action"]["effect"] = {"kind": "text_appears", "text": "Sound"}
        actual = GroundedVlmPlanner._parse(json.dumps(data), _snapshot())
        self.assertEqual(actual.action.effect, GuiEffect("text_appears", "Sound"))
        for bad in (
            {"kind": "text_appears", "text": ""},
            {"kind": "scene_changes", "text": "Sound"},
            {"kind": "text_appears", "text": "Sound", "command": "click"},
        ):
            data["action"]["effect"] = bad
            with self.assertRaises(PlannerReplyError):
                GroundedVlmPlanner._parse(json.dumps(data), _snapshot())

    def test_feedback_is_task_scoped_without_a_game_specific_session(self) -> None:
        self.start()
        self.supervisor.observe(snapshot(2, 2_100_000_000), frame(1, 2_100_000_000))
        self.assertTrue(json.loads(self.supervisor.planner_feedback(1))["recent_actions"])
        self.assertEqual(json.loads(self.supervisor.planner_feedback(2))["recent_actions"], [])
