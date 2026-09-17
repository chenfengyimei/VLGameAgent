from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests.unit.test_grounded_vlm import _Client, _large_frame, _reply, _snapshot
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.gui_prompt import gui_instruction


class GuiPromptInputTests(unittest.TestCase):
    def test_both_formats_define_wait_and_never_force_unsafe_progress(self) -> None:
        for compact in (False, True):
            prompt = gui_instruction(_snapshot(), "Open settings", compact=compact)
            self.assertIn('WAIT: kind="wait", action=null', prompt)
            self.assertIn("No registered goal evidence", prompt)
            self.assertIn("untrusted scene DATA", prompt)
            self.assertNotIn("must click", prompt)
            data = json.loads(prompt.split("SCENE_DATA_JSON:\n")[1])
            self.assertEqual(data["goal"], "Open settings")

    def test_image_order_and_detail_transform_are_explicit_in_compact_mode(self) -> None:
        client = _Client([_reply()])
        planner = GroundedVlmPlanner(client, compact_output=True)
        planner.decide(
            snapshot=_snapshot(), frames=(_large_frame(99), _large_frame(100)), goal=_snapshot().text[0]
        )
        prompt = str(client.calls[0]["instruction"])
        self.assertIn("Image 2 is the CURRENT full overview", prompt)
        data = json.loads(prompt.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["image_map"][0]["role"], "context_overview")
        self.assertEqual(data["image_map"][-1]["role"], "detail_read_only")
        self.assertEqual(data["image_map"][-1]["source_box_unit"], [0.18, 0.28, 0.42, 0.52])
        self.assertIn("Do not return crop-local", prompt)

    def test_foreign_window_and_geometry_do_not_leak_into_temporal_input(self) -> None:
        last = _large_frame(100)
        foreign = replace(
            _large_frame(98), window_identity=replace(last.window_identity, window_generation=99)
        )
        resized = replace(_large_frame(99), client_rect=replace(last.client_rect, right=99))
        planner = GroundedVlmPlanner(_Client([]), max_target_crops=0)
        _, temporal, _ = planner._images((foreign, resized, last), _snapshot(), "settings", False)
        self.assertEqual(temporal, 1)

    def test_compact_has_the_same_goal_evidence_and_consumed_target_rules(self) -> None:
        client = _Client([_reply("wait")])
        planner = GroundedVlmPlanner(
            client,
            compact_output=True,
            max_target_crops=0,
            required_goal_evidence=("Expected title",),
            preferred_action_target="settings",
        )
        planner.decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="Open settings",
            preferred_action_available=False,
        )
        data = json.loads(str(client.calls[0]["instruction"]).split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["missing_goal_evidence"], ["Expected title"])
        self.assertEqual(data["consumed_target"], "settings")

    def test_thousand_grid_is_consistent_between_schema_prompt_and_ocr(self) -> None:
        response = json.loads(_reply())
        response["action"]["target_bbox"] = [200, 300, 400, 500]
        client = _Client([json.dumps(response)])
        planner = GroundedVlmPlanner(client, coordinate_space="normalized_1000")
        planner.decide(snapshot=_snapshot(), frames=(_large_frame(100),), goal="Open settings")
        prompt = str(client.calls[0]["instruction"])
        self.assertIn("integer [0,1000]", prompt)
        data = json.loads(prompt.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["ocr"][0]["bbox"], [200, 300, 400, 500])
