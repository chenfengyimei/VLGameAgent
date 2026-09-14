from __future__ import annotations

import json
import unittest

from tests.helpers import frame
from uga.capture.frame import BufferHandle
from uga.capture.ring_buffer import SequencedFrame
from uga.control.lease import ControlMode
from uga.core.errors import BackendUnavailableError
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import (
    DecisionKind,
    GoalStatus,
    NormalizedBox,
    TextRegion,
    WaitReason,
)
from uga.policy.grounded_vlm import (
    GROUNDING_RESPONSE_FORMAT,
    GroundedVlmPlanner,
    crop_frame,
)


class _Provider:
    @property
    def available(self) -> bool:
        return True

    def recognize(self, source):  # type: ignore[no-untyped-def]
        del source
        return (TextRegion("设置", NormalizedBox(0.2, 0.3, 0.4, 0.5), 0.99),)


class _Client:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

    def decide(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _snapshot():  # type: ignore[no-untyped-def]
    return PerceptionBuilder(_Provider()).build(
        SequencedFrame(2, _large_frame(100)),
        ControlMode.GUI,
        geometry_generation=4,
        task_generation=5,
    )


def _reply(kind: str = "act") -> str:
    action = (
        {
            "kind": "click",
            "target_label": "设置",
            "target_bbox": [0.2, 0.3, 0.4, 0.5],
            "expected_effect": "打开设置页",
            "confidence": 0.94,
            "risk": "low",
            "key": None,
        }
        if kind == "act"
        else None
    )
    return json.dumps(
        {
            "kind": kind,
            "scene_summary": "Android 主页面",
            "visible_text": ["设置"],
            "goal_status": "in_progress" if kind != "done" else "succeeded",
            "confidence": 0.94,
            "action": action,
            "wait_reason": "loading" if kind == "wait" else None,
            "explanation": "可靠决策",
        }
    )


class GroundedVlmTests(unittest.TestCase):
    def test_schema_constrains_every_bbox_coordinate_to_normalized_range(self) -> None:
        encoded = json.dumps(GROUNDING_RESPONSE_FORMAT)
        self.assertIn('"minimum": 0', encoded)
        self.assertIn('"maximum": 1', encoded)

    def test_schema_bounds_free_text_generation(self) -> None:
        schema = GROUNDING_RESPONSE_FORMAT["json_schema"]["schema"]
        properties = schema["properties"]
        self.assertEqual(properties["scene_summary"]["maxLength"], 160)
        self.assertEqual(properties["visible_text"]["maxItems"], 16)
        self.assertEqual(properties["explanation"]["maxLength"], 240)

    def test_schema_and_prompt_distinguish_blocked_from_done(self) -> None:
        client = _Client([_reply("wait")])

        GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="页面没有安全可用操作时停止并报告无法继续",
        )

        schema = GROUNDING_RESPONSE_FORMAT["json_schema"]["schema"]
        self.assertIn("not DONE", schema["properties"]["kind"]["description"])
        instruction = str(client.calls[0]["instruction"])
        self.assertIn("不代表该外部目标成功", instruction)
        self.assertIn("wait_reason=no_safe_action", instruction)
        self.assertIn("绝对禁止 DONE", instruction)

    def test_schema_and_prompt_require_null_key_for_back_button_click(self) -> None:
        client = _Client([_reply()])

        GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="返回上一页，不要点击不可用的继续按钮",
        )

        schema = GROUNDING_RESPONSE_FORMAT["json_schema"]["schema"]
        action_schema = schema["properties"]["action"]["anyOf"][1]
        self.assertIn("including", action_schema["properties"]["key"]["description"])
        instruction = str(client.calls[0]["instruction"])
        self.assertIn("return_button、back", instruction)
        self.assertIn("应 ACT 点击该返回控件", instruction)

    def test_uses_clean_overview_and_grounded_target_crop(self) -> None:
        client = _Client([_reply()])
        snapshot = _snapshot()

        outcome = GroundedVlmPlanner(
            client,
            required_goal_evidence=("目标页标题", "目标页事实"),
            preferred_action_target="设置",
        ).decide(
            snapshot=snapshot,
            frames=(_large_frame(100),),
            goal="打开设置",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertAlmostEqual(
            outcome.action.target_box.center.x, 0.3  # type: ignore[union-attr]
        )
        self.assertEqual(len(client.calls[0]["images"]), 2)  # type: ignore[arg-type]
        self.assertEqual(client.calls[0]["response_format"], GROUNDING_RESPONSE_FORMAT)
        instruction = str(client.calls[0]["instruction"])
        self.assertLess(instruction.index("完成证据"), instruction.index("normalized target_bbox"))
        self.assertIn("目标按钮因上一步成功而消失", instruction)
        self.assertIn("最新 OCR 中的字面文字", instruction)
        self.assertIn("目标页标题", instruction)
        self.assertIn("目标页事实", instruction)
        self.assertIn("可点击行在最新帧可见", instruction)
        self.assertIn("当前缺失证据：目标页标题, 目标页事实", instruction)
        self.assertIn("当前帧必须输出 ACT", instruction)

    def test_wait_is_a_non_action_with_reason(self) -> None:
        outcome = GroundedVlmPlanner(_Client([_reply("wait")])).decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="等待加载"
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(outcome.wait_reason, WaitReason.LOADING)
        self.assertIsNone(outcome.action)

    def test_consumed_single_step_target_is_explicitly_forbidden(self) -> None:
        client = _Client([_reply("wait")])
        planner = GroundedVlmPlanner(client, preferred_action_target="设置")

        planner.decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="打开设置",
            preferred_action_available=False,
        )

        instruction = str(client.calls[0]["instruction"])
        self.assertIn("已执行且已观测到界面效果", instruction)
        self.assertIn("禁止再次点击", instruction)

    def test_visible_required_evidence_makes_act_explicitly_forbidden(self) -> None:
        client = _Client([_reply("done")])

        GroundedVlmPlanner(
            client,
            required_goal_evidence=("设置",),
            preferred_action_target="设置",
        ).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="打开设置",
        )

        instruction = str(client.calls[0]["instruction"])
        self.assertIn("当前帧必须输出 DONE", instruction)
        self.assertIn("绝对禁止 ACT", instruction)
        self.assertIn("不得点击目标页内的其他选项", instruction)

    def test_temporal_overviews_are_chronological_and_capped_at_three(self) -> None:
        client = _Client([_reply()])

        GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(),
            frames=tuple(_large_frame(value) for value in (1, 2, 3, 100)),
            goal="打开设置",
        )

        self.assertEqual(len(client.calls[0]["images"]), 4)  # type: ignore[arg-type]
        self.assertIn("3 张按时间先后", str(client.calls[0]["instruction"]))

    def test_invalid_reply_gets_one_repair_then_abstains(self) -> None:
        client = _Client(["not json", "still not json"])

        outcome = GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(outcome.kind, DecisionKind.ABSTAIN)
        self.assertEqual(outcome.goal_status, GoalStatus.UNKNOWN)

    def test_repair_prompt_includes_the_cross_field_validation_error(self) -> None:
        invalid = json.loads(_reply())
        invalid["wait_reason"] = "no_safe_action"
        client = _Client([json.dumps(invalid), _reply()])

        outcome = GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertIn(
            "only WAIT outcomes may carry a wait reason",
            str(client.calls[1]["instruction"]),
        )

    def test_unsupported_schema_is_probed_once_and_falls_back(self) -> None:
        client = _Client(
            [BackendUnavailableError("structured output unsupported: response_format"), _reply()]
        )
        planner = GroundedVlmPlanner(client)

        first = planner.decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )

        self.assertEqual(first.kind, DecisionKind.ACT)
        self.assertIn("response_format", client.calls[0])
        self.assertNotIn("response_format", client.calls[1])

    def test_crop_copies_only_target_pixels(self) -> None:
        source = _large_frame(1)
        cropped = crop_frame(source, NormalizedBox(0.1, 0.2, 0.5, 0.6), padding=0)

        self.assertEqual((cropped.width, cropped.height), (40, 40))
        self.assertEqual(cropped.buffer_handle.size_bytes, 40 * 40 * 4)


def _large_frame(number: int):  # type: ignore[no-untyped-def]
    source = frame(number)
    payload = bytes([number % 256]) * (100 * 100 * 4)
    return replace_frame_buffer(source, payload)


def replace_frame_buffer(source, payload: bytes):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(
        source,
        width=100,
        height=100,
        stride_bytes=400,
        buffer_handle=BufferHandle(
            source.buffer_handle.handle_id,
            source.buffer_handle.kind,
            len(payload),
            payload,
        ),
    )


if __name__ == "__main__":
    unittest.main()
