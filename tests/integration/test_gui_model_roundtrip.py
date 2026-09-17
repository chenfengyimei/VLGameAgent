"""GUI round trips use actual parser/controller/scheduler and fake HTTP + DryRun I/O.

This validates protocol and state transitions, not either model's live accuracy.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from tests.helpers import frame
from tests.integration.test_review_followup import make_loop
from uga.capture.frame import BufferHandle
from uga.control.physical import AbsolutePointerAction, MouseButtonAction
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import NormalizedBox, TextRegion
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vlm_planner import OpenAICompatibleVisionClient
from uga.time.clock import ManualClock
from uga.windows.coordinates import Rect


class GuiModelRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_providers_parse_execute_observe_and_feed_back(self) -> None:
        for model in ("Qwen/Qwen3-VL-4B-Instruct", "glm-4.6v"):
            for space in ("unit", "normalized_1000"):
                with self.subTest(model=model, space=space):
                    clock = ManualClock(100)
                    client = OpenAICompatibleVisionClient(
                        base_url="http://127.0.0.1:1234/v1", model=model,
                        disable_thinking=True, max_output_tokens=1024,
                    )
                    planner = GroundedVlmPlanner(
                        client, compact_output=True, coordinate_space=space,
                        max_image_width=640, max_temporal_frames=1, max_target_crops=1,
                        enable_rule_fast_paths=False,
                    )
                    loop, backend, supervisor, _, frames = make_loop(planner, clock)

                    class Scene:
                        stage = 0
                        number = 0
                        available = True

                        async def capture_once(self, clock=clock, frames=frames):
                            self.number += 1
                            original = frame(self.number, clock.now().value_ns)
                            pixels = bytes([80 + self.stage * 80, 60, 40, 255]) * (640 * 360)
                            source = replace(
                                original, width=640, height=360, stride_bytes=640 * 4,
                                client_rect=Rect(0, 0, 640, 360),
                                physical_rect=Rect(-1280, 100, 0, 820),
                                buffer_handle=BufferHandle(
                                    original.buffer_handle.handle_id, original.buffer_handle.kind,
                                    len(pixels), pixels,
                                ),
                            )
                            return frames.publish(source)

                        def recognize(self, source):
                            return (TextRegion("Sound" if self.stage else "Settings",
                                               NormalizedBox(.2, .3, .4, .5), .99),)

                    scene = Scene()
                    loop._capture = scene
                    loop._perception_builder = PerceptionBuilder(scene)
                    calls = []

                    def send(request, calls=calls, space=space, **kwargs):
                        payload = json.loads(request.data)
                        calls.append(payload)
                        reply = {"kind": "wait", "confidence": .95, "action": None,
                                 "wait_reason": "animation"}
                        if len(calls) == 1:
                            reply.update(kind="act", wait_reason=None, action={
                                "kind": "click", "target_label": "Settings", "key": None,
                                "confidence": .95, "scroll_delta": None,
                                "target_bbox": ([200, 300, 400, 500] if space == "normalized_1000"
                                                else [.2, .3, .4, .5]),
                                "effect": {"kind": "text_appears", "text": "Sound"},
                            })
                        response = MagicMock()
                        response.__enter__.return_value = response
                        response.read.return_value = json.dumps({"choices": [{
                            "finish_reason": "stop", "message": {"content": json.dumps(reply)}
                        }]}).encode()
                        return response

                    with patch("uga.policy.vlm_planner.open_vision_request", side_effect=send):
                        first = await loop.step()
                        self.assertTrue(first.gui_submission.decision.accepted)
                        pointer = next(
                            a for a in backend.actions if isinstance(a, AbsolutePointerAction)
                        )
                        self.assertEqual((pointer.x, pointer.y), (-896, 388))
                        self.assertEqual(len(backend.actions), 3)
                        self.assertEqual(sum(isinstance(a, MouseButtonAction) and a.is_down
                                             for a in backend.actions), 1)
                        scene.stage = 1
                        clock.set(300_000_100)
                        await loop.step()
                        self.assertEqual(len(calls), 1, "pending effect must prevent replanning")
                        clock.set(900_000_100)
                        await loop.step()
                        self.assertTrue(supervisor.last_effect_observed)
                        self.assertEqual(len(calls), 2)
                        self.assertEqual(
                            len(backend.actions), 3, "WAIT must not duplicate the click"
                        )
                        instruction = calls[-1]["messages"][0]["content"][-1]["text"]
                        state = json.loads(instruction.split("SCENE_DATA_JSON:\n")[1])
                        feedback = json.loads(state["recent_runtime_feedback"])
                        self.assertEqual(feedback["recent_actions"][-1]["status"], "verified")
                        self.assertIn("Sound", feedback["recent_actions"][-1]["reason"])
                        image_map = state["image_map"]
                        self.assertEqual(image_map[0]["role"], "current_overview")
                        first_prompt = calls[0]["messages"][0]["content"][-1]["text"]
                        first_state = json.loads(first_prompt.split("SCENE_DATA_JSON:\n")[1])
                        self.assertEqual(first_state["image_map"][-1]["role"], "detail_read_only")
                        self.assertEqual(len(image_map), 1, "unrelated old crops must not persist")
