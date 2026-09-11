from __future__ import annotations

import io
import json
import time
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
    FrameHistorySampler,
    OpenAICompatibleVisionClient,
    PlannerReplyError,
    VisionRateLimitedError,
    VlmPlannerPolicy,
    build_instruction,
    encode_frame_png,
    parse_planner_reply,
    parse_planner_sequence,
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
        action, x, y, quest, step = parse_planner_reply('{"action":"tap","x":0.59,"y":0.64}')

        self.assertEqual((action, x, y, quest, step), ("tap", 0.59, 0.64, None, None))

    def test_wait_reply(self) -> None:
        self.assertEqual(
            parse_planner_reply('{"action":"wait"}'), ("wait", None, None, None, None)
        )

    def test_fenced_reply(self) -> None:
        action, x, y, quest, step = parse_planner_reply(
            '```json\n{"action":"tap","x":0.5,"y":0.5}\n```'
        )

        self.assertEqual((action, x, y, quest, step), ("tap", 0.5, 0.5, None, None))

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

        action, x, y, quest, step = parse_planner_reply(reply)

        self.assertEqual((action, x, y, quest, step), ("tap", 0.6, 0.7, None, None))

    def test_trailing_text_after_json_is_tolerated(self) -> None:
        reply = '画面上有按钮。 {"action":"wait"} 补充说明文字'

        self.assertEqual(parse_planner_reply(reply), ("wait", None, None, None, None))

    def test_reply_with_only_invalid_objects_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('描述 {"a":1} 另一段 {"b":2}')

    def test_action_sequence_parsed_oldest_first(self) -> None:
        reply = (
            '推进对话。 {"actions":[{"action":"tap","x":0.5,"y":0.6},'
            '{"action":"tap","x":0.5,"y":0.7},{"action":"wait"}]}'
        )

        sequence = parse_planner_sequence(reply)

        self.assertEqual(len(sequence), 3)
        self.assertEqual(sequence[0][0], "tap")
        self.assertEqual((sequence[0][1], sequence[0][2]), (0.5, 0.6))
        self.assertEqual((sequence[1][1], sequence[1][2]), (0.5, 0.7))
        self.assertEqual(sequence[2][0], "wait")

    def test_action_sequence_overflow_is_truncated(self) -> None:
        steps = ",".join('{"action":"tap","x":0.1,"y":0.1}' for _ in range(9))
        reply = '{"actions":[' + steps + "]}"

        sequence = parse_planner_sequence(reply)

        self.assertEqual(len(sequence), 4)

    def test_action_sequence_empty_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_sequence('{"actions":[]}')

    def test_sequence_quest_field_per_step(self) -> None:
        reply = '{"actions":[{"action":"tap","x":0.2,"y":0.3,"quest":"与桃夭对话"}]}'

        sequence = parse_planner_sequence(reply)

        self.assertEqual(sequence[0][3], "与桃夭对话")

    def test_quest_field_is_reported(self) -> None:
        action, x, y, quest, step = parse_planner_reply(
            '选择捏脸数据。 {"action":"tap","x":0.2,"y":0.3,"quest":"与桃夭对话"}'
        )

        self.assertEqual((action, x, y, quest, step), ("tap", 0.2, 0.3, "与桃夭对话", None))

    def test_step_field_is_reported(self) -> None:
        action, x, y, quest, step = parse_planner_reply(
            '灵宠界面。 {"action":"tap","x":0.4,"y":0.5,"step":"打开灵宠界面"}'
        )

        self.assertEqual((action, x, y, quest, step), ("tap", 0.4, 0.5, None, "打开灵宠界面"))

    def test_press_action_with_button(self) -> None:
        action, x, y, quest, step = parse_planner_reply(
            '剧情对话。 {"action":"press","button":"confirm"}'
        )

        self.assertEqual((action, x, y, quest, step), ("press", None, None, None, "confirm"))

    def test_press_without_button_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"press"}')

    def test_press_unknown_button_rejected_at_dispatch(self) -> None:
        client = _FakeClient(['{"action":"press","button":"does_not_exist"}'])
        policy = _policy(client)

        with self.assertRaises(PlannerReplyError):
            policy.infer(PolicyContext("obs-1", UGATime(100), (), None))

    def test_drag_action_decoded_with_endpoints(self) -> None:
        action, x, y, quest, tail = parse_planner_reply(
            '摇杆移动。 {"action":"drag","x1":0.14,"y1":0.78,"x2":0.16,"y2":0.30}'
        )

        self.assertEqual((action, x, y, quest), ("drag", 0.14, 0.78, None))
        self.assertEqual(tail, "0.1600,0.3000")

    def test_drag_missing_coordinates_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"drag","x1":0.1,"y1":0.2}')

    def test_drag_out_of_range_rejected(self) -> None:
        with self.assertRaises(PlannerReplyError):
            parse_planner_reply('{"action":"drag","x1":1.4,"y1":0.2,"x2":0.3,"y2":0.4}')


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

    def test_instruction_describes_multi_frame_bundle(self) -> None:
        instruction = build_instruction("推进主线", None, None, None)

        self.assertIn("时间先后", instruction)
        self.assertIn("最后一张是当前画面", instruction)


def _sampler_frame(tag: int) -> Frame:
    row_bytes = 64 * 4
    payload = bytes([tag]) * (row_bytes * 32)
    return Frame(
        frame_id=f"frame-{tag}",
        capture_timestamp=UGATime(tag),
        present_estimate=None,
        window_identity=identity(),
        width=64,
        height=32,
        stride_bytes=row_bytes,
        pixel_format=PixelFormat.BGRA8,
        physical_rect=Rect(0, 0, 64, 32),
        client_rect=Rect(0, 0, 64, 32),
        source_backend="fixture",
        buffer_handle=BufferHandle(
            handle_id="buf",
            kind=BufferKind.CPU_BYTES,
            size_bytes=len(payload),
            payload=payload,
        ),
    )


class FrameHistorySamplerTests(unittest.TestCase):
    def _sampler_with_history(self, stamps: list[int]) -> FrameHistorySampler:
        sampler = FrameHistorySampler(capture=lambda: _sampler_frame(0))
        tagged = [(float(t), _sampler_frame(t)) for t in stamps]
        with sampler._lock:
            sampler._history.extend(tagged)
        return sampler

    def test_empty_history_returns_empty_bundle(self) -> None:
        sampler = FrameHistorySampler(capture=lambda: _sampler_frame(0))

        self.assertEqual(sampler.select_bundle(None), [])

    def test_fewer_than_max_frames_returns_all_in_time_order(self) -> None:
        sampler = self._sampler_with_history([7, 8, 9, 10])

        bundle = sampler.select_bundle(5.0)

        self.assertEqual(
            [frame.frame_id for frame in bundle],
            ["frame-7", "frame-8", "frame-9", "frame-10"],
        )

    def test_bundle_anchored_at_first_frame_after_boundary(self) -> None:
        # Decision #1 ran at t=1 and finished by t=10; decision #2 at t=11
        # must cover the whole gap (frames 2..11), oldest first, newest last.
        sampler = self._sampler_with_history([2, 3, 4, 5, 6, 7, 8, 9, 10, 11])

        bundle = sampler.select_bundle(1.0)

        self.assertEqual(len(bundle), 5)
        self.assertEqual(bundle[0].frame_id, "frame-2")
        self.assertEqual(bundle[-1].frame_id, "frame-11")

    def test_bundle_middle_frames_are_evenly_spaced(self) -> None:
        sampler = self._sampler_with_history([2, 3, 4, 5, 6, 7, 8, 9, 10, 11])

        bundle = sampler.select_bundle(1.0)

        stamps = [frame.frame_id for frame in bundle]
        self.assertEqual(
            stamps, ["frame-2", "frame-3", "frame-5", "frame-8", "frame-11"]
        )

    def test_frames_at_or_before_boundary_are_excluded(self) -> None:
        sampler = self._sampler_with_history([1, 2, 8, 9, 10, 11])

        bundle = sampler.select_bundle(1.0)

        self.assertNotIn("frame-1", [frame.frame_id for frame in bundle])
        self.assertEqual(bundle[0].frame_id, "frame-2")

    def test_no_frames_after_boundary_falls_back_to_newest(self) -> None:
        sampler = self._sampler_with_history([1, 2, 3])

        bundle = sampler.select_bundle(5.0)

        self.assertEqual([frame.frame_id for frame in bundle], ["frame-1", "frame-2", "frame-3"])

    def test_policy_passes_freshest_frame_last(self) -> None:
        client = _FakeClient(['{"action":"wait"}'])
        sampler = self._sampler_with_history([1, 2, 3])
        policy = _policy(client)
        policy._sampler = sampler

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))

        self.assertGreaterEqual(len(client.last_images), 1)
        self.assertLessEqual(len(client.last_images), 5)


class TapFreshnessGuardTests(unittest.TestCase):
    def test_stable_target_area_is_not_stale(self) -> None:
        policy = _policy(_FakeClient([]))

        self.assertFalse(policy._tap_target_stale(_frame(), _frame(), 0.5, 0.5))

    def test_changed_target_area_is_stale(self) -> None:
        changed = _frame()
        row_bytes = 64 * 4
        payload = bytes([90]) * (row_bytes * 32)
        changed = Frame(
            frame_id="frame-changed",
            capture_timestamp=UGATime(101),
            present_estimate=None,
            window_identity=identity(),
            width=64,
            height=32,
            stride_bytes=row_bytes,
            pixel_format=PixelFormat.BGRA8,
            physical_rect=Rect(0, 0, 64, 32),
            client_rect=Rect(0, 0, 64, 32),
            source_backend="fixture",
            buffer_handle=BufferHandle(
                handle_id="buf2",
                kind=BufferKind.CPU_BYTES,
                size_bytes=len(payload),
                payload=payload,
            ),
        )
        policy = _policy(_FakeClient([]))

        self.assertTrue(policy._tap_target_stale(_frame(), changed, 0.5, 0.5))

    def test_full_screen_change_elsewhere_does_not_invalidate_tap(self) -> None:
        # The tap box (±8% around 0.08, 0.9) must stay clean even when the
        # rest of the screen animates: only the corner pixel rows change.
        row_bytes = 64 * 4
        partial = bytearray(bytes([200]) * (row_bytes * 32))
        for y in range(0, 8):  # top rows only — far from the tap target
            partial[y * row_bytes : y * row_bytes + row_bytes] = bytes([1]) * row_bytes
        animated = Frame(
            frame_id="frame-animated",
            capture_timestamp=UGATime(101),
            present_estimate=None,
            window_identity=identity(),
            width=64,
            height=32,
            stride_bytes=row_bytes,
            pixel_format=PixelFormat.BGRA8,
            physical_rect=Rect(0, 0, 64, 32),
            client_rect=Rect(0, 0, 64, 32),
            source_backend="fixture",
            buffer_handle=BufferHandle(
                handle_id="buf3",
                kind=BufferKind.CPU_BYTES,
                size_bytes=len(partial),
                payload=bytes(partial),
            ),
        )
        policy = _policy(_FakeClient([]))

        self.assertFalse(policy._tap_target_stale(_frame(), animated, 0.5, 0.9))

    def test_discarded_tap_redecides_immediately(self) -> None:
        frames = [_frame(), _frame(40, 32)]  # second frame: different width
        policy = _policy(_FakeClient(['{"action":"tap","x":0.5,"y":0.5}'] * 2))
        policy._frame_source = lambda: frames.pop(0)

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))

        self.assertEqual(policy._stale_discards, 1)
        self.assertLessEqual(policy._next_decision_at - time.monotonic(), 0.1)


class HybridThinkingTests(unittest.TestCase):
    def _varied_frame(self, tag: int) -> Frame:
        row_bytes = 64 * 4
        payload = bytes([tag]) * (row_bytes * 32)
        return Frame(
            frame_id=f"frame-v{tag}",
            capture_timestamp=UGATime(tag),
            present_estimate=None,
            window_identity=identity(),
            width=64,
            height=32,
            stride_bytes=row_bytes,
            pixel_format=PixelFormat.BGRA8,
            physical_rect=Rect(0, 0, 64, 32),
            client_rect=Rect(0, 0, 64, 32),
            source_backend="fixture",
            buffer_handle=BufferHandle(
                handle_id=f"buf-v{tag}",
                kind=BufferKind.CPU_BYTES,
                size_bytes=len(payload),
                payload=payload,
            ),
        )

    def test_unchanged_screen_skips_inference(self) -> None:
        client = _FakeClient(['{"action":"wait"}'] * 3)
        policy = _policy(client)
        policy._decision_interval_s = 60.0
        policy._next_decision_at = 0.0

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        calls_after_first = client.calls
        policy._next_decision_at = 0.0  # cadence elapsed; screen unchanged
        policy.infer(PolicyContext("obs-2", UGATime(200), (), None))

        self.assertEqual(client.calls, calls_after_first)
        self.assertEqual(policy._static_holds, 1)

    def test_changed_screen_resumes_inference(self) -> None:
        client = _FakeClient(['{"action":"wait"}'] * 3)
        policy = _policy(client)
        policy._decision_interval_s = 60.0
        policy._next_decision_at = 0.0
        frames = iter([self._varied_frame(1), self._varied_frame(2), self._varied_frame(3)])
        policy._frame_source = lambda: next(frames)

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        policy._next_decision_at = 0.0
        policy.infer(PolicyContext("obs-2", UGATime(200), (), None))

        self.assertEqual(client.calls, 2)
        self.assertEqual(policy._static_holds, 0)

    def test_static_holds_bounded(self) -> None:
        client = _FakeClient(['{"action":"wait"}'] * 2)
        policy = _policy(client)
        policy._decision_interval_s = 0.001
        policy._next_decision_at = 0.0

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        for _ in range(30):
            time.sleep(0.002)
            policy.infer(PolicyContext("obs-n", UGATime(100), (), None))

        self.assertLessEqual(client.calls, 3)  # initial + bounded re-asks


class ActionSequenceQueueTests(unittest.TestCase):
    @staticmethod
    def _varied_frame(tag: int) -> Frame:
        row_bytes = 64 * 4
        payload = bytes([tag]) * (row_bytes * 32)
        return Frame(
            frame_id=f"frame-v{tag}",
            capture_timestamp=UGATime(tag),
            present_estimate=None,
            window_identity=identity(),
            width=64,
            height=32,
            stride_bytes=row_bytes,
            pixel_format=PixelFormat.BGRA8,
            physical_rect=Rect(0, 0, 64, 32),
            client_rect=Rect(0, 0, 64, 32),
            source_backend="fixture",
            buffer_handle=BufferHandle(
                handle_id=f"buf-v{tag}",
                kind=BufferKind.CPU_BYTES,
                size_bytes=len(payload),
                payload=payload,
            ),
        )

    def test_clustered_ineffective_taps_trigger_auto_probe(self) -> None:
        # The model confidently taps one spot but keeps missing the button:
        # after three clustered no-effect taps the system probes a ring of
        # nearby offsets via the pending queue (no extra inference).
        client = _FakeClient(['{"action":"tap","x":0.50,"y":0.50}'] * 8)
        policy = _policy(client)
        policy._decision_interval_s = 60.0
        policy._frame_source = lambda: _frame()  # identical, static screen

        for index in range(6):
            policy._next_decision_at = 0.0
            policy.infer(PolicyContext(f"obs-{index}", UGATime(100), (), None))

        self.assertGreaterEqual(policy._stuck_taps, 3)
        self.assertGreaterEqual(len(policy._pending_actions), 7)  # probe ring
        self.assertEqual(policy._perturb_rounds, 1)

    def test_effective_tap_resets_stuck_cluster(self) -> None:
        client = _FakeClient(['{"action":"tap","x":0.50,"y":0.50}'] * 3)
        policy = _policy(client)
        policy._decision_interval_s = 60.0
        calls = {"n": 0}

        def frame_source() -> Frame:
            calls["n"] += 1
            # Two identical frames (decision + freshness probe), then the
            # world visibly changes — the first tap registered an effect.
            if calls["n"] <= 2:
                return _frame()
            return self._varied_frame(7)

        policy._frame_source = frame_source

        policy._next_decision_at = 0.0
        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        policy._next_decision_at = 0.0
        policy.infer(PolicyContext("obs-2", UGATime(200), (), None))

        self.assertEqual(policy._stuck_taps, 0)
        self.assertFalse(policy._pending_actions)

    def test_probe_rounds_bounded_per_cluster(self) -> None:
        client = _FakeClient(['{"action":"tap","x":0.50,"y":0.50}'] * 16)
        policy = _policy(client)
        policy._decision_interval_s = 60.0
        policy._frame_source = lambda: _frame()  # static screen throughout

        for index in range(14):
            policy._next_decision_at = 0.0
            policy.infer(PolicyContext(f"obs-{index}", UGATime(100), (), None))

        self.assertLessEqual(policy._perturb_rounds, 2)


    def test_sequence_executes_without_extra_inference(self) -> None:
        client = _FakeClient(
            ['{"actions":[{"action":"tap","x":0.3,"y":0.4},{"action":"tap","x":0.5,"y":0.6}]}']
        )
        policy = _policy(client)
        policy._decision_interval_s = 0.05
        policy._next_decision_at = 0.0

        first = policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        self.assertEqual(first.chunk.buttons, (int(ActionButton.INTERACT),))
        self.assertEqual(len(policy._pending_actions), 1)

        policy._next_decision_at = 0.0
        second = policy.infer(PolicyContext("obs-2", UGATime(200), (), None))

        self.assertEqual(client.calls, 1)  # second action: no new inference
        self.assertEqual(second.chunk.pointer_x, 100.0 + 1000 * 0.5)
        self.assertEqual(second.chunk.pointer_y, 200.0 + 1120 * 0.6)
        self.assertFalse(policy._pending_actions)

    def test_stale_guard_clears_pending_queue(self) -> None:
        client = _FakeClient(
            ['{"actions":[{"action":"tap","x":0.5,"y":0.5},{"action":"tap","x":0.5,"y":0.5}]}']
        )
        frames = [_frame(), _frame(40, 32), _frame(40, 32), _frame(40, 32)]
        policy = _policy(client)
        policy._decision_interval_s = 0.05
        policy._next_decision_at = 0.0
        policy._frame_source = lambda: frames.pop(0)

        policy.infer(PolicyContext("obs-1", UGATime(100), (), None))

        self.assertEqual(policy._stale_discards, 1)
        self.assertFalse(policy._pending_actions)


if __name__ == "__main__":
    unittest.main()
