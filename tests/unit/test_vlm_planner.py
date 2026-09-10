from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from tests.helpers import identity
from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.policy.action_chunk import ActionButton
from uga.policy.fast_policy import PolicyContext
from uga.policy.vlm_planner import (
    MAX_CONSECUTIVE_FAILURES,
    OpenAICompatibleVisionClient,
    PlannerReplyError,
    VisionRateLimitedError,
    VlmPlannerPolicy,
    build_instruction,
    encode_frame_png,
    parse_planner_reply,
)
from uga.time.clock import ManualClock, UGATime
from uga.windows.coordinates import Rect


def _frame(width: int = 64, height: int = 32) -> Frame:
    row_bytes = width * 4
    payload = bytes([200]) * (row_bytes * height)
    return Frame(
        frame_id="frame-1",
        capture_timestamp=UGATime(100),
        present_estimate=None,
        window_identity=identity(),
        width=width,
        height=height,
        stride_bytes=row_bytes,
        pixel_format=PixelFormat.BGRA8,
        physical_rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        source_backend="fixture",
        buffer_handle=BufferHandle(
            handle_id="buffer-1",
            kind=BufferKind.CPU_BYTES,
            size_bytes=len(payload),
            payload=payload,
        ),
    )


def _context() -> PolicyContext:
    return PolicyContext("obs-1", UGATime(100), (), None)


class _FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def decide(self, *, images: list[bytes], instruction: str) -> str:
        self.calls += 1
        self.last_images = images
        self.last_instruction = instruction
        if not self.replies:
            raise BackendUnavailableError("fake client exhausted")
        return self.replies.pop(0)


def _policy(client: object) -> VlmPlannerPolicy:
    return VlmPlannerPolicy(
        client=client,  # type: ignore[arg-type]
        frame_source=_frame,
        client_rect=lambda: Rect(100, 200, 1100, 1320),
        goal="完成任务",
        decision_interval_s=600.0,
        failure_backoff_s=1.0,
    )


class EncodeFramePngTests(unittest.TestCase):
    def test_encodes_bgra_frame_as_png(self) -> None:
        encoded = encode_frame_png(_frame())

        self.assertTrue(encoded.startswith(b"\x89PNG"))

    def test_wide_frames_are_downscaled(self) -> None:
        encoded = encode_frame_png(_frame(width=1920, height=1088), max_width=960)

        self.assertTrue(encoded.startswith(b"\x89PNG"))
        self.assertLess(len(encoded), 400_000)

    def test_rejects_unsupported_pixel_format(self) -> None:
        frame = _frame()
        object.__setattr__(frame, "pixel_format", "rgb565")
        with self.assertRaises(ContractViolation):
            encode_frame_png(frame)


class ParsePlannerReplyTests(unittest.TestCase):
    def test_tap_reply(self) -> None:
        action, x, y, quest = parse_planner_reply('{"action":"tap","x":0.59,"y":0.64}')

        self.assertEqual((action, x, y, quest), ("tap", 0.59, 0.64, None))

    def test_wait_reply(self) -> None:
        self.assertEqual(
            parse_planner_reply('{"action":"wait"}'), ("wait", None, None, None)
        )

    def test_fenced_reply(self) -> None:
        action, x, y, quest = parse_planner_reply(
            '```json\n{"action":"tap","x":0.5,"y":0.5}\n```'
        )

        self.assertEqual((action, x, y, quest), ("tap", 0.5, 0.5, None))

    def test_garbage_reply_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply("画面上有一个按钮，我认为应该点击它")

    def test_out_of_range_coordinates_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"tap","x":1.5,"y":0.5}')

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"swipe","x":0.5,"y":0.5}')

    def test_missing_coordinates_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"tap"}')

    def test_newest_json_object_wins_when_reply_has_several(self) -> None:
        reply = (
            "画面是灵宠界面 {\"a\":1} 描述里混入了括号 "
            '{"action":"tap","x":0.1,"y":0.1} 旧的坐标 '
            '{"action":"tap","x":0.6,"y":0.7}'
        )

        action, x, y, quest = parse_planner_reply(reply)

        self.assertEqual((action, x, y, quest), ("tap", 0.6, 0.7, None))

    def test_trailing_text_after_json_is_tolerated(self) -> None:
        reply = '画面上有按钮。 {"action":"wait"} 补充说明文字'

        self.assertEqual(parse_planner_reply(reply), ("wait", None, None, None))

    def test_reply_with_only_invalid_objects_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('描述 {"a":1} 另一段 {"b":2}')

    def test_quest_field_is_reported(self) -> None:
        action, x, y, quest = parse_planner_reply(
            '选择捏脸数据。 {"action":"tap","x":0.2,"y":0.3,"quest":"与桃夭对话"}'
        )

        self.assertEqual((action, x, y, quest), ("tap", 0.2, 0.3, "与桃夭对话"))


class OpenAICompatibleVisionClientTests(unittest.TestCase):
    def test_posts_image_and_parses_content(self) -> None:
        reply_body = json.dumps(
            {"choices": [{"message": {"content": '{"action":"wait"}'}}]}
        ).encode("utf-8")

        class _Response:
            def read(self) -> bytes:
                return reply_body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        client = OpenAICompatibleVisionClient(
            base_url="http://127.0.0.1:1234/v1",
            model="gemma-3-4b-it",
            api_key="secret",
        )
        with mock.patch(
            "urllib.request.urlopen", return_value=_Response()
        ) as urlopen:
            reply = client.decide(images=[b"\x89PNGfake"], instruction="决定")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "http://127.0.0.1:1234/v1/chat/completions"
        )
        self.assertEqual(request.headers.get("Authorization"), "Bearer secret")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "gemma-3-4b-it")
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(
            content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        )
        self.assertEqual(content[1]["type"], "text")
        self.assertIn("决定", content[1]["text"])
        self.assertEqual(reply, '{"action":"wait"}')

    def test_endpoint_suffix_is_not_doubled(self) -> None:
        client = OpenAICompatibleVisionClient(
            base_url="https://api.example.com/v1/chat/completions",
            model="qwen-vl-max",
        )
        self.assertEqual(
            client._endpoint, "https://api.example.com/v1/chat/completions"
        )

    def test_extra_body_is_merged_into_the_payload(self) -> None:
        reply_body = json.dumps(
            {"choices": [{"message": {"content": '{"action":"wait"}'}}]}
        ).encode("utf-8")

        class _Response:
            def read(self) -> bytes:
                return reply_body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        client = OpenAICompatibleVisionClient(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.8-flash",
            extra_body={"enable_thinking": False},
        )
        with mock.patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
            client.decide(images=[b"x"], instruction="go")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["enable_thinking"], False)

    def test_disable_thinking_adds_the_switch_to_the_payload(self) -> None:
        reply_body = json.dumps(
            {"choices": [{"message": {"content": '{"action":"wait"}'}}]}
        ).encode("utf-8")

        class _Response:
            def read(self) -> bytes:
                return reply_body

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        client = OpenAICompatibleVisionClient(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.6v-flash",
            disable_thinking=True,
        )
        with mock.patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
            client.decide(images=[b"x"], instruction="go")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_http_error_maps_to_backend_unavailable(self) -> None:
        client = OpenAICompatibleVisionClient(
            base_url="http://127.0.0.1:1234/v1", model="m"
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 500, "boom", None, io.BytesIO(b"")  # type: ignore[arg-type]
            ),
        ), self.assertRaises(BackendUnavailableError):
            client.decide(images=[b"x"], instruction="go")


class VlmPlannerPolicyTests(unittest.TestCase):
    def test_tap_decision_maps_fractions_to_screen_coordinates(self) -> None:
        client = _FakeClient(['{"action":"tap","x":0.5,"y":0.75}'])
        policy = _policy(client)

        output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (int(ActionButton.INTERACT),))
        self.assertEqual(output.chunk.pointer_x, 600.0)
        self.assertEqual(output.chunk.pointer_y, 1040.0)
        self.assertTrue(client.last_images[-1].startswith(b"\x89PNG"))
        self.assertIn("完成任务", client.last_instruction)

    def test_wait_decision_holds_at_client_center(self) -> None:
        client = _FakeClient(['{"action":"wait"}'])
        policy = _policy(client)

        output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (0,) * output.chunk.horizon)
        self.assertEqual(output.chunk.pointer_x, 600.0)
        self.assertEqual(output.chunk.pointer_y, 760.0)

    def test_decisions_are_throttled_to_the_interval(self) -> None:
        client = _FakeClient(['{"action":"wait"}'])
        policy = _policy(client)
        policy.infer(_context())

        throttled = policy.infer(_context())

        self.assertEqual(client.calls, 1)
        self.assertEqual(throttled.chunk.buttons, (0,) * throttled.chunk.horizon)

    def test_unparseable_replies_back_off_then_fail_closed(self) -> None:
        clock = [0.0]
        client = _FakeClient(["not json"] * MAX_CONSECUTIVE_FAILURES)
        policy = _policy(client)

        with mock.patch(
            "uga.policy.vlm_planner.time.monotonic", lambda: clock[0]
        ), self.assertRaises(BackendUnavailableError):
            for _ in range(MAX_CONSECUTIVE_FAILURES):
                policy.infer(_context())
                clock[0] += 10.0

        self.assertEqual(client.calls, MAX_CONSECUTIVE_FAILURES)

    def test_unreachable_provider_counts_as_failure(self) -> None:
        client = _FakeClient([])
        policy = _policy(client)

        output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (0,) * output.chunk.horizon)

    def test_rate_limiting_backs_off_without_counting_failures(self) -> None:
        class _RateLimitedClient:
            def decide(self, *, images: list[bytes], instruction: str) -> str:
                raise VisionRateLimitedError("rate limited")

        clock = [0.0]
        policy = VlmPlannerPolicy(
            client=_RateLimitedClient(),  # type: ignore[arg-type]
            frame_source=_frame,
            client_rect=lambda: Rect(100, 200, 1100, 1320),
            goal="完成任务",
            decision_interval_s=600.0,
            failure_backoff_s=1.0,
        )

        with mock.patch("uga.policy.vlm_planner.time.monotonic", lambda: clock[0]):
            for _ in range(MAX_CONSECUTIVE_FAILURES + 3):
                output = policy.infer(_context())
                clock[0] += 1200.0
                self.assertEqual(output.chunk.buttons, (0,) * output.chunk.horizon)

    def test_empty_goal_rejected(self) -> None:
        with self.assertRaises(ContractViolation):
            VlmPlannerPolicy(
                client=_FakeClient([]),
                frame_source=_frame,
                client_rect=lambda: Rect(0, 0, 10, 10),
                goal="  ",
            )

    def test_chunks_are_stamped_at_decision_time_not_observation_time(self) -> None:
        clock = ManualClock(100_000_000_000)
        client = _FakeClient(['{"action":"tap","x":0.5,"y":0.5}'])
        policy = VlmPlannerPolicy(
            client=client,  # type: ignore[arg-type]
            frame_source=_frame,
            client_rect=lambda: Rect(100, 200, 1100, 1320),
            goal="完成任务",
            decision_interval_s=600.0,
            failure_backoff_s=1.0,
            clock=clock,
        )

        output = policy.infer(PolicyContext("obs-1", UGATime(100), (), None))

        self.assertEqual(output.chunk.generated_at.value_ns, 100_000_000_000)
        self.assertGreater(output.chunk.expires_at.value_ns, 100_000_000_000)


class BuildInstructionTests(unittest.TestCase):
    def test_instruction_contains_goal_and_json_contract(self) -> None:
        instruction = build_instruction(
            "进入游戏",
            last_action="tap(0.5,0.5)",
            quest="与桃夭对话",
            screen_changed=True,
        )

        self.assertIn("进入游戏", instruction)
        self.assertIn('"action":"tap"', instruction)
        self.assertIn("tap(0.5,0.5)", instruction)
        self.assertIn("与桃夭对话", instruction)
        self.assertIn("没有变化", instruction)


if __name__ == "__main__":
    unittest.main()
