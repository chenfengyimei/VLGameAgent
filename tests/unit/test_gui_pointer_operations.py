from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests.integration.test_review_followup import GroundedClickPlanner, make_loop
from tests.unit.test_grounded_vlm import _large_frame, _reply, _snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.control.lifetime import ActionLifetime
from uga.control.physical import MouseButton, MouseButtonAction, WheelAction
from uga.core.agent_loop import RealtimeAgentLoop
from uga.environment.profile import PerceptionProfile
from uga.gui.control_bridge import GuiControlBridge
from uga.gui.schema import GuiAction, GuiActionKind
from uga.perception.schema import NormalizedBox
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vlm_planner import PlannerReplyError
from uga.time.clock import ManualClock, UGATime


class GuiPointerOperationTests(unittest.TestCase):
    def test_supported_operations_reach_the_physical_bridge(self) -> None:
        source = _large_frame(100)
        supervisor = ClosedLoopSupervisor(ManualClock(100), PerceptionProfile())
        for kind, downs in (("click", 1), ("double_click", 2), ("right_click", 1),
                            ("long_click", 1), ("scroll", 0)):
            with self.subTest(kind=kind):
                data = json.loads(_reply())
                data["action"].update(kind=kind, scroll_delta=-120 if kind == "scroll" else None)
                outcome = GroundedVlmPlanner._parse(json.dumps(data), _snapshot())
                gui = supervisor.to_gui_action(outcome, lambda _: None)
                actions = GuiControlBridge().translate(
                    gui, RealtimeAgentLoop._coordinate_transform(source)
                )
                buttons = [action for action in actions if isinstance(action, MouseButtonAction)]
                self.assertEqual(sum(action.is_down for action in buttons), downs)
                if kind == "right_click":
                    self.assertTrue(all(action.button == MouseButton.RIGHT for action in buttons))
                if kind == "scroll":
                    wheel = actions[-1]
                    self.assertIsInstance(wheel, WheelAction)
                    self.assertEqual(wheel.delta, -120)
                if kind == "long_click":
                    self.assertEqual(
                        actions[-1].lifetime.effective_from.value_ns
                        - actions[0].lifetime.effective_from.value_ns, 700_000_000
                    )

    def test_unsafe_scroll_values_and_panel_clicks_are_rejected(self) -> None:
        for delta in (True, "120", 0, 1, 121, 600, -600, 120.0):
            with self.subTest(delta=delta), self.assertRaises(PlannerReplyError):
                data = json.loads(_reply())
                data["action"].update(kind="scroll", scroll_delta=delta)
                GroundedVlmPlanner._parse(json.dumps(data), _snapshot())
        data = json.loads(_reply())
        data["action"]["target_bbox"] = [0, 0, 1, 1]
        with self.assertRaises(PlannerReplyError):
            GroundedVlmPlanner._parse(json.dumps(data), _snapshot())

    def test_drag_holds_then_moves_and_releases(self) -> None:
        action = GuiAction(
            "joystick-drag",
            GuiActionKind.DRAG,
            ActionLifetime(UGATime(0), UGATime(0), UGATime(1_000_000_000)),
            x=0.165,
            y=0.800,
            end_x=0.165,
            end_y=0.680,
        )
        actions = GuiControlBridge().translate(
            action, RealtimeAgentLoop._coordinate_transform(_large_frame(100))
        )
        buttons = [item for item in actions if isinstance(item, MouseButtonAction)]
        self.assertEqual([item.is_down for item in buttons], [True, False])
        self.assertEqual(actions[2].lifetime.effective_from.value_ns, 450_000_000)
        self.assertEqual(actions[3].lifetime.effective_from.value_ns, 700_000_000)

    def test_final_guard_is_local_to_target_not_background_animation(self) -> None:
        source = _large_frame(100)
        clock = ManualClock(source.capture_timestamp.value_ns + 100)
        loop, _, _, _, frames = make_loop(GroundedClickPlanner(), clock)
        box = NormalizedBox(0.2, 0.3, 0.4, 0.5)
        for inside in (False, True):
            payload = bytearray(source.buffer_handle.payload)
            for y in range(source.height):
                for x in range(source.width):
                    in_target = 0.2 <= x / source.width <= 0.4 and 0.3 <= y / source.height <= 0.5
                    if in_target == inside:
                        offset = y * source.stride_bytes + x * 4
                        payload[offset:offset + 3] = bytes([255, 0, 0])
            current = replace(source, frame_id=f"changed-{inside}",
                              buffer_handle=replace(source.buffer_handle, payload=bytes(payload)))
            frames.publish(current)
            self.assertEqual(loop._execution_is_current(
                None, source, 5, visual_stability=True, target_box=box
            ), not inside)
