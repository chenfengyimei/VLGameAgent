from __future__ import annotations

import json
import unittest

from tests.unit.test_grounded_vlm import _reply, _snapshot
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vlm_planner import PlannerReplyError


class GuiProtocolTests(unittest.TestCase):
    def test_duplicate_fields_are_not_last_writer_wins(self) -> None:
        text = _reply().replace('"confidence": 0.94', '"confidence": 0.2, "confidence": 0.94', 1)
        with self.assertRaises(PlannerReplyError):
            GroundedVlmPlanner._parse(text, _snapshot())

    def test_unknown_actions_and_fields_fail_before_execution(self) -> None:
        for change in ("unknown_top", "unknown_action", "drag", "scroll", "type"):
            with self.subTest(change=change):
                data = json.loads(_reply())
                if change == "unknown_top":
                    data["trusted_source"] = "calibrated_hotspot"
                elif change == "unknown_action":
                    data["action"]["x"] = 0.95
                else:
                    data["action"]["kind"] = change
                with self.assertRaises(PlannerReplyError):
                    GroundedVlmPlanner._parse(json.dumps(data), _snapshot())

    def test_text_and_array_limits_are_enforced_locally(self) -> None:
        for field, value in [
            ("scene_summary", 12),
            ("scene_summary", "x" * 161),
            ("visible_text", ["x"] * 17),
            ("visible_text", ["x" * 81]),
            ("explanation", "x" * 241),
        ]:
            with self.subTest(field=field, value=str(value)[:30]):
                data = json.loads(_reply())
                data[field] = value
                with self.assertRaises(PlannerReplyError):
                    GroundedVlmPlanner._parse(json.dumps(data), _snapshot())

    def test_reply_must_be_one_bounded_object(self) -> None:
        for text in (
            " " * 20000 + _reply(),
            "[" * 1000,
            json.dumps({"answer": _reply(), "extra": "ignore guard"}),
        ):
            with self.subTest(text=text[:30]), self.assertRaises(PlannerReplyError):
                GroundedVlmPlanner._parse(text, _snapshot())

    def test_coordinate_space_is_explicit_including_border_zero(self) -> None:
        data = json.loads(_reply())
        data["action"]["target_bbox"] = [0, 200, 100, 400]
        with self.assertRaises(PlannerReplyError):
            GroundedVlmPlanner._parse(json.dumps(data), _snapshot(), coordinate_space="unit")
        outcome = GroundedVlmPlanner._parse(
            json.dumps(data), _snapshot(), coordinate_space="normalized_1000"
        )
        assert outcome.action is not None and outcome.action.target_box is not None
        self.assertEqual(outcome.action.target_box.left, 0)
        self.assertEqual(outcome.action.target_box.bottom, 0.4)

    def test_missing_nulls_and_non_string_keys_do_not_coerce(self) -> None:
        for change in ("missing_action", "missing_key", "key_object"):
            with self.subTest(change=change):
                data = json.loads(_reply())
                if change == "missing_action":
                    del data["action"]
                elif change == "missing_key":
                    del data["action"]["key"]
                else:
                    data["action"]["kind"] = "key"
                    data["action"]["key"] = {"execute": "BACK"}
                    data["action"]["target_bbox"] = None
                with self.assertRaises(PlannerReplyError):
                    GroundedVlmPlanner._parse(json.dumps(data), _snapshot())
