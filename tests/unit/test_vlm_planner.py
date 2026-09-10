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

    def decide(self, *, image_png: bytes, instruction: str) -> str:
        self.calls += 1
        self.last_image = image_png
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
        action, x, y = parse_planner_reply('{"action":"tap","x":0.59,"y":0.64}')

        self.assertEqual((action, x, y), ("tap", 0.59, 0.64))

    def test_wait_reply(self) -> None:
        self.assertEqual(parse_planner_reply('{"action":"wait"}'), ("wait", None, None))

    def test_fenced_reply(self) -> None:
        action, x, y = parse_planner_reply(
            '```json\n{"action":"tap","x":0.5,"y":0.5}\n```'
        )

        self.assertEqual((action, x, y), ("tap", 0.5, 0.5))

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
            reply = client.decide(image_png=b"\x89PNGfake", instruction="决定")

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
            client.decide(image_png=b"x", instruction="go")


class VlmPlannerPolicyTests(unittest.TestCase):
    def test_tap_decision_maps_fractions_to_screen_coordinates(self) -> None:
        client = _FakeClient(['{"action":"tap","x":0.5,"y":0.75}'])
        policy = _policy(client)

        output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (int(ActionButton.INTERACT),))
        self.assertEqual(output.chunk.pointer_x, 600.0)
        self.assertEqual(output.chunk.pointer_y, 1040.0)
        self.assertTrue(client.last_image.startswith(b"\x89PNG"))
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
        instruction = build_instruction("进入游戏", last_action="tap(0.5,0.5)")

        self.assertIn("进入游戏", instruction)
        self.assertIn('"action":"tap"', instruction)
        self.assertIn("tap(0.5,0.5)", instruction)


if __name__ == "__main__":
    unittest.main()
