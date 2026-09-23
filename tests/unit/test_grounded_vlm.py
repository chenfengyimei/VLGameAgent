from __future__ import annotations

import json
import struct
import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.capture.frame import BufferHandle
from uga.capture.ring_buffer import SequencedFrame
from uga.control.lease import ControlMode
from uga.core.errors import BackendUnavailableError
from uga.gui.schema import GuiActionKind
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import (
    DecisionKind,
    GoalStatus,
    NormalizedBox,
    TextRegion,
    WaitReason,
)
from uga.policy.grounded_vlm import (
    COMPACT_GROUNDING_RESPONSE_FORMAT,
    GROUNDING_RESPONSE_FORMAT,
    GroundedVlmPlanner,
    crop_frame,
)
from uga.windows.coordinates import Rect


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
    @staticmethod
    def _wide_frame(number: int):  # type: ignore[no-untyped-def]
        source = frame(number)
        width = 1600
        height = 1600
        payload = bytes([number % 256]) * (width * height * 4)
        return replace(
            source,
            width=width,
            height=height,
            stride_bytes=width * 4,
            physical_rect=Rect(-100, 20, 1500, 1520),
            client_rect=Rect(0, 0, width, height),
            buffer_handle=BufferHandle(
                source.buffer_handle.handle_id,
                source.buffer_handle.kind,
                len(payload),
                payload,
            ),
        )

    def test_high_resolution_retry_doubles_overview_width(self) -> None:
        # EX04 regression: the retry only widened crop padding, which changes
        # nothing when no crops are configured — the same pixels were re-sent
        # while a recovery slot was burned.
        wide = self._wide_frame(1)
        planner = GroundedVlmPlanner(
            _Client([]),
            max_image_width=640,
            max_temporal_frames=1,
            max_target_crops=0,
        )
        images, _, _ = planner._images((wide,), _snapshot(), "goal", False)
        normal_width = struct.unpack(">I", images[0][16:20])[0]
        self.assertEqual(normal_width, 640)

        retry_images, _, _ = planner._images((wide,), _snapshot(), "goal", True)
        retry_width = struct.unpack(">I", retry_images[0][16:20])[0]
        self.assertEqual(retry_width, 1280)
        self.assertTrue(planner.last_high_resolution_upgraded)

    def test_high_resolution_retry_at_cap_is_recorded_as_non_upgrade(self) -> None:
        wide = self._wide_frame(1)
        planner = GroundedVlmPlanner(
            _Client([]),
            max_image_width=1280,
            max_temporal_frames=1,
            max_target_crops=0,
        )
        images, _, _ = planner._images((wide,), _snapshot(), "goal", True)
        width = struct.unpack(">I", images[0][16:20])[0]
        self.assertEqual(width, 1280)
        # At the hard cap the retry is recorded as a non-upgrade instead of
        # silently re-sending identical pixels.
        self.assertFalse(planner.last_high_resolution_upgraded)

    def test_high_confidence_left_task_panel_bypasses_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        task_box = NormalizedBox(0.04, 0.25, 0.19, 0.30)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("主线", NormalizedBox(0.04, 0.17, 0.10, 0.20), 0.99),
                TextRegion("得赶紧回到桃源居", task_box, 0.98),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="点击左上角具体任务文字",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "得赶紧回到桃源居")  # type: ignore[union-attr]
        self.assertEqual(outcome.action.target_box, task_box)  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_mumu_close_confirmation_dialog_cancels_without_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(
                    "确定要关闭 “MuMu安卓设备-1” 吗?",
                    NormalizedBox(0.36, 0.38, 0.72, 0.43),
                    0.97,
                ),
                TextRegion("不再提示", NormalizedBox(0.38, 0.44, 0.46, 0.47), 0.95),
                TextRegion("确定", NormalizedBox(0.40, 0.55, 0.50, 0.60), 0.96),
                TextRegion("取消", NormalizedBox(0.53, 0.55, 0.64, 0.60), 0.96),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线任务",
        )

        # MuMu 的关闭确认框是模态拦截：规则层直接点取消（绝不点确定），
        # 不消耗一次 VLM 推理。
        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "取消")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_xiuxian_path_quest_line_clicks_without_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("主线", NormalizedBox(0.055, 0.189, 0.092, 0.223), 1.00),
                TextRegion("修仙之路", NormalizedBox(0.080, 0.242, 0.147, 0.278), 1.00),
                TextRegion(
                    "完成2个修仙之路目标0/2仙途轧缘仙遇",
                    NormalizedBox(0.043, 0.363, 0.293, 0.398),
                    0.96,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线任务",
        )

        # 修仙之路任务：点任务面板任务行本身会自动跳转到对应界面——规则层
        # 直接点真实任务行，绕开模型反复点"主线"栏目标题的死循环。
        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertIn("修仙之路目标", outcome.action.target_label)
        self.assertEqual(client.calls, [])

    def test_irrelevant_page_exit_is_cooldown_bounded_and_hands_off_to_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("悬赏任务", NormalizedBox(0.235, 0.294, 0.306, 0.767), 0.99),
                TextRegion(
                    "悬赏任务需要3~5人组队完成",
                    NormalizedBox(0.393, 0.294, 0.623, 0.330),
                    1.00,
                ),
                TextRegion("便捷组队", NormalizedBox(0.427, 0.719, 0.520, 0.766), 1.00),
                TextRegion("创建队伍", NormalizedBox(0.596, 0.719, 0.688, 0.766), 1.00),
            ),
        )
        planner = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        )

        first = planner.decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线任务",
            quest_text="完成3次30级装备秘境",
        )

        # 过期任务关键词（装备）不在本页：第一次先走校准返回热点退出。
        self.assertEqual(first.kind, DecisionKind.ACT)
        assert first.action is not None
        self.assertEqual(first.action.target_label, "ui_back")

        second = planner.decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线任务",
            quest_text="完成3次30级装备秘境",
        )

        # 冷却期内不再机械退出：控制权交还模型（VLM 被调用一次）。
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(second.kind, DecisionKind.WAIT)
        self.assertIsNone(second.action)

    def test_xiuxian_objective_goto_clicks_without_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("修仙之路", NormalizedBox(0.357, 0.146, 0.617, 0.282), 1.00),
                TextRegion(
                    "完成3次30级装备秘境",
                    NormalizedBox(0.423, 0.311, 0.558, 0.344),
                    1.00,
                ),
                TextRegion("前往", NormalizedBox(0.767, 0.328, 0.807, 0.367), 1.00),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线任务",
        )

        # 修仙之路界面：目标文字不可点，规则层直接点目标行的前往按钮。
        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "前往")
        self.assertEqual(client.calls, [])

    def test_left_side_function_tabs_are_not_treated_as_task_panel(self) -> None:
        compact_reply = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_reply])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", NormalizedBox(0.02, 0.05, 0.10, 0.10), 0.99),
                TextRegion("攻击", NormalizedBox(0.13, 0.28, 0.18, 0.34), 0.99),
                TextRegion("回退", NormalizedBox(0.11, 0.86, 0.18, 0.92), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_pet_attribute_text_is_not_treated_as_dialogue(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("修为1154", NormalizedBox(0.28, 0.90, 0.42, 0.95), 0.99),
                TextRegion("龙吟云溪1级", NormalizedBox(0.72, 0.82, 0.88, 0.87), 0.99),
                TextRegion("伤害", NormalizedBox(0.75, 0.88, 0.82, 0.93), 0.99),
                TextRegion("暴击抵抗0", NormalizedBox(0.70, 0.66, 0.82, 0.71), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_quest_level_target_met_exits_without_model(self) -> None:
        # 任务目标 10 级已达成：页面显示等级 10/40 → 规则层直接 ui_back 退出，
        # 不再让模型继续点击升级浪费材料，也不调用 VLM。
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.08, 0.05, 0.15, 0.11)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("等级 10/40", NormalizedBox(0.64, 0.24, 0.73, 0.29), 0.99),
                TextRegion("升级", NormalizedBox(0.70, 0.60, 0.80, 0.68), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
            quest_target_level=10,
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "ui_back")  # type: ignore[union-attr]
        self.assertEqual(outcome.explanation.split()[0], "ocr_quest_satisfied_back_fast")
        self.assertEqual(client.calls, [])

    def test_quest_irrelevant_page_exits_without_model(self) -> None:
        # 任务目标是灵宠，但当前功法升级页 OCR 中完全没有灵宠：规则层直接
        # ui_back 退出，不让模型在无关页面上继续消耗升级材料。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("功法升级", NormalizedBox(0.42, 0.12, 0.58, 0.18), 0.99),
                TextRegion("长生诀", NormalizedBox(0.10, 0.18, 0.22, 0.24), 0.99),
                TextRegion("功法修为：597", NormalizedBox(0.10, 0.22, 0.28, 0.28), 0.99),
                TextRegion("升级消耗 660", NormalizedBox(0.60, 0.70, 0.82, 0.76), 0.99),
                TextRegion("升级", NormalizedBox(0.68, 0.82, 0.80, 0.90), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
            quest_text="拥有1只灵宠达到10级0/1",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "ui_back")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_quest_relevant_page_keeps_model_in_charge(self) -> None:
        # 页面本身包含任务关键词（灵宠）：相关性守卫不得触发。
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.08, 0.05, 0.15, 0.11)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("等级 3/40", NormalizedBox(0.64, 0.24, 0.73, 0.29), 0.99),
                TextRegion("升级消耗 660", NormalizedBox(0.60, 0.70, 0.82, 0.76), 0.99),
                TextRegion("升级", NormalizedBox(0.68, 0.82, 0.80, 0.90), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
            quest_text="拥有1只灵宠达到10级0/1",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_restored_unverified_pet_page_exits_before_model_can_spend(self) -> None:
        client = _Client([_reply("act")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", NormalizedBox(0.05, 0.06, 0.14, 0.12), 0.99),
                TextRegion("技能升级", NormalizedBox(0.63, 0.39, 0.76, 0.45), 0.99),
                TextRegion("成长率", NormalizedBox(0.63, 0.56, 0.73, 0.62), 0.99),
                TextRegion("升星", NormalizedBox(0.70, 0.82, 0.84, 0.92), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
            restored_task_unverified=True,
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_back")
        self.assertEqual(client.calls, [])

    def test_welfare_and_settings_pages_exit_without_calling_model(self) -> None:
        pages = (
            (
                TextRegion("福利", NormalizedBox(0.06, 0.05, 0.16, 0.12), 0.99),
                TextRegion("在线奖励", NormalizedBox(0.02, 0.17, 0.14, 0.25), 0.99),
                TextRegion("每日签到", NormalizedBox(0.17, 0.16, 0.34, 0.27), 0.99),
            ),
            (
                TextRegion("切换角色", NormalizedBox(0.73, 0.16, 0.82, 0.28), 0.99),
                TextRegion("返回登录", NormalizedBox(0.84, 0.16, 0.94, 0.28), 0.99),
                TextRegion("音频设置", NormalizedBox(0.03, 0.36, 0.16, 0.44), 0.99),
            ),
        )
        for regions, source in zip(
            pages,
            ("ocr_welfare_page_back_fast", "ocr_settings_page_back_fast"),
            strict=True,
        ):
            with self.subTest(source=source):
                client = _Client([_reply("act")])
                planner = GroundedVlmPlanner(
                    client,
                    max_temporal_frames=1,
                    max_target_crops=0,
                    compact_output=True,
                    prefer_ocr_task_panel=True,
                    back_hotspot=(0.06, 0.08),
                )
                outcome = planner.decide(
                    snapshot=replace(_snapshot(), visible_text=regions),
                    frames=(_large_frame(100),),
                    goal="推进当前主线",
                )
                self.assertEqual(outcome.kind, DecisionKind.ACT)
                assert outcome.action is not None
                self.assertEqual(outcome.action.target_label, "ui_back")
                self.assertEqual(planner.last_decision_source, source)
                self.assertEqual(client.calls, [])

    def test_monetization_popup_is_closed_not_clicked(self) -> None:
        # 充值弹窗：禁止点击其中文字，规则层直接点 OCR 可见的 × 字形。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("限时特惠", NormalizedBox(0.35, 0.15, 0.65, 0.25), 0.99),
                TextRegion("充值", NormalizedBox(0.40, 0.40, 0.60, 0.50), 0.99),
                TextRegion("关闭×", NormalizedBox(0.86, 0.18, 0.92, 0.24), 0.95),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
            close_hotspot=(0.945, 0.075),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_close")
        self.assertEqual(outcome.action.target_box, NormalizedBox(0.86, 0.18, 0.92, 0.24))  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_monetization_popup_without_glyph_falls_to_model(self) -> None:
        # 无 × 字形时不强行退出：提示词已禁止点击促销文字，模型应输出 wait。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("首充大礼", NormalizedBox(0.35, 0.15, 0.65, 0.25), 0.99),
                TextRegion("充值6元", NormalizedBox(0.40, 0.40, 0.60, 0.50), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
            close_hotspot=(0.945, 0.075),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_realm_promotion_medallion_clicked_when_objectives_complete(self) -> None:
        # 境界页目标全部已完成：晋升奖章是图形控件 OCR 读不出，规则层直接
        # 点击校准热点，而不是让模型无限 wait。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("境界", NormalizedBox(0.08, 0.08, 0.16, 0.13), 1.00),
                TextRegion("境界目标", NormalizedBox(0.70, 0.16, 0.85, 0.21), 0.99),
                TextRegion("上阵两只灵宠", NormalizedBox(0.60, 0.25, 0.75, 0.30), 0.99),
                TextRegion("已完成", NormalizedBox(0.82, 0.30, 0.90, 0.35), 0.99),
                TextRegion("已完成", NormalizedBox(0.82, 0.44, 0.90, 0.49), 0.99),
                TextRegion("已完成", NormalizedBox(0.82, 0.58, 0.90, 0.63), 0.99),
                TextRegion("晋升奖励：攻击+50", NormalizedBox(0.28, 0.82, 0.50, 0.88), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
            promote_hotspot=(0.32, 0.60),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_promote")
        center = outcome.action.target_box.center  # type: ignore[union-attr]
        self.assertAlmostEqual(center.x, 0.32, places=6)
        self.assertAlmostEqual(center.y, 0.60, places=6)
        self.assertEqual(client.calls, [])

    def test_realm_page_without_completed_objectives_waits(self) -> None:
        # 目标未全部完成时不得乱点晋升。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("境界", NormalizedBox(0.08, 0.08, 0.16, 0.13), 1.00),
                TextRegion("境界目标", NormalizedBox(0.70, 0.16, 0.85, 0.21), 0.99),
                TextRegion("上阵两只灵宠", NormalizedBox(0.60, 0.25, 0.75, 0.30), 0.99),
                TextRegion("1/2", NormalizedBox(0.60, 0.30, 0.68, 0.35), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
            promote_hotspot=(0.32, 0.60),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_realm_promotion_result_exits_after_objectives_reset(self) -> None:
        # 晋升后的实机页面不会稳定显示“突破成功”，而是直接将下一境界的
        # 目标重置为 1/2、0/2。此时必须退出，不能把新目标当成本轮任务循环。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("境界", NormalizedBox(0.08, 0.08, 0.16, 0.13), 1.00),
                TextRegion("境界目标", NormalizedBox(0.70, 0.16, 0.85, 0.21), 0.99),
                TextRegion("上阵两只灵宠", NormalizedBox(0.60, 0.25, 0.75, 0.30), 0.99),
                TextRegion("1/2", NormalizedBox(0.60, 0.30, 0.68, 0.35), 0.99),
                TextRegion("去完成", NormalizedBox(0.82, 0.30, 0.91, 0.36), 0.99),
                TextRegion("悬铃塔通关第2层", NormalizedBox(0.60, 0.40, 0.78, 0.45), 0.99),
                TextRegion("0/2", NormalizedBox(0.60, 0.46, 0.68, 0.51), 0.99),
                TextRegion("去完成", NormalizedBox(0.82, 0.46, 0.91, 0.52), 0.99),
                TextRegion("11665/10000", NormalizedBox(0.60, 0.62, 0.76, 0.67), 0.99),
                TextRegion("已完成", NormalizedBox(0.82, 0.62, 0.90, 0.67), 0.99),
                TextRegion("晋升奖励：攻击+50", NormalizedBox(0.28, 0.82, 0.50, 0.88), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
            promote_hotspot=(0.32, 0.60),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_back")
        self.assertEqual(
            outcome.explanation.split()[0],
            "ocr_realm_promotion_complete_back_fast",
        )
        self.assertEqual(client.calls, [])

    def test_task_panel_click_cooldown_hands_control_back_to_model(self) -> None:
        # 追踪器点击生效后进入冷却：下一次决策交给模型（游戏高亮引导的
        # 市场按钮才是真正的下一步），不再无限重复点击追踪器。
        client = _Client([_reply("wait")])
        tracker_regions = (
            TextRegion("主线", NormalizedBox(0.02, 0.10, 0.12, 0.16), 0.99),
            TextRegion("出售一件商品", NormalizedBox(0.03, 0.24, 0.20, 0.30), 0.99),
            TextRegion("市场", NormalizedBox(0.66, 0.07, 0.73, 0.13), 0.99),
        )
        current = replace(_snapshot(), visible_text=tracker_regions)

        planner = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        )

        first = planner.decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="出售一件商品",
        )
        self.assertEqual(first.action.target_label, "出售一件商品")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

        second = planner.decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="出售一件商品",
        )
        # The second decision falls through to the model.
        self.assertEqual(second.kind, DecisionKind.WAIT)
        self.assertEqual(len(client.calls), 1)

    def test_task_panel_cooldown_resets_on_new_quest_text(self) -> None:
        client = _Client([_reply("wait")])
        planner = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        )

        first_regions = (
            TextRegion("主线", NormalizedBox(0.02, 0.10, 0.12, 0.16), 0.99),
            TextRegion("出售一件商品", NormalizedBox(0.03, 0.24, 0.20, 0.30), 0.99),
        )
        first = planner.decide(
            snapshot=replace(_snapshot(), visible_text=first_regions),
            frames=(_large_frame(100),),
            goal="出售一件商品",
        )
        self.assertEqual(first.action.target_label, "出售一件商品")  # type: ignore[union-attr]

        # A different quest text navigates immediately (no cooldown).
        second_regions = (
            TextRegion("主线", NormalizedBox(0.02, 0.10, 0.12, 0.16), 0.99),
            TextRegion("装备精炼", NormalizedBox(0.03, 0.24, 0.20, 0.30), 0.99),
        )
        second = planner.decide(
            snapshot=replace(_snapshot(), visible_text=second_regions),
            frames=(_large_frame(100),),
            goal="出售一件商品",
        )
        self.assertEqual(second.action.target_label, "装备精炼")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_market_task_clicks_market_entry_not_quest_panel(self) -> None:
        # 出售类任务：游戏高亮的市场入口才是正确目标，任务面板文字不是。
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("主线", NormalizedBox(0.02, 0.10, 0.12, 0.16), 0.99),
                TextRegion("出售一件商品", NormalizedBox(0.03, 0.24, 0.20, 0.30), 0.99),
                TextRegion("市场", NormalizedBox(0.66, 0.07, 0.73, 0.13), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="出售一件商品",
            quest_text="摆摊出售 出售一件商品",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "市场")
        self.assertEqual(client.calls, [])

    def test_active_level_up_quest_page_is_not_declared_complete(self) -> None:
        # 等级 3/40 during an active 达到10级 quest means the page must be
        # worked, not left: the fast path must not fire a ui_back here.
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.08, 0.05, 0.15, 0.11)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("等级 3/40", NormalizedBox(0.64, 0.24, 0.73, 0.29), 0.99),
                TextRegion("回退", NormalizedBox(0.11, 0.86, 0.18, 0.92), 0.99),
                TextRegion("暴击抵抗 0", NormalizedBox(0.70, 0.66, 0.82, 0.71), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertIsNone(outcome.action)
        self.assertEqual(len(client.calls), 1)

    def test_completed_pet_panel_without_back_hotspot_falls_back_to_model(self) -> None:
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.08, 0.05, 0.15, 0.11)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("等级 3/40", NormalizedBox(0.64, 0.24, 0.73, 0.29), 0.99),
                TextRegion("回退", NormalizedBox(0.11, 0.86, 0.18, 0.92), 0.99),
                TextRegion("暴击抵抗 0", NormalizedBox(0.70, 0.66, 0.82, 0.71), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertIsNone(outcome.action)
        self.assertEqual(len(client.calls), 1)

    def test_pet_evolution_limit_panel_exits_without_model(self) -> None:
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.08, 0.05, 0.15, 0.11)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("4星", NormalizedBox(0.73, 0.14, 0.79, 0.20), 0.99),
                TextRegion(
                    "该灵宠已培养至进化上限",
                    NormalizedBox(0.68, 0.72, 0.90, 0.78),
                    0.99,
                ),
                TextRegion("图鉴", NormalizedBox(0.52, 0.89, 0.58, 0.95), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="完成主线任务",
        )

        self.assertEqual(outcome.action.target_label, "ui_back")  # type: ignore[union-attr]
        self.assertEqual(outcome.action.pointer_offset_x, 0.0)  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_completed_pet_formation_panel_exits_without_model(self) -> None:
        client = _Client([_reply("wait")])
        title_box = NormalizedBox(0.03, 0.03, 0.11, 0.09)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", title_box, 0.99),
                TextRegion("主战位", NormalizedBox(0.26, 0.16, 0.37, 0.22), 0.99),
                TextRegion("辅助位", NormalizedBox(0.26, 0.69, 0.37, 0.75), 0.99),
                TextRegion("修为：1814", NormalizedBox(0.09, 0.60, 0.20, 0.66), 0.99),
                TextRegion("下阵", NormalizedBox(0.70, 0.87, 0.82, 0.94), 0.99),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            back_hotspot=(0.06, 0.08),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进主线",
        )

        self.assertEqual(outcome.action.target_label, "ui_back")  # type: ignore[union-attr]
        self.assertEqual(outcome.action.pointer_offset_x, 0.0)  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_dialogue_layout_without_review_cue_falls_back_to_model(self) -> None:
        compact_wait = json.dumps(
            {
                "kind": "wait",
                "confidence": 0.95,
                "wait_reason": "no_safe_action",
                "action": None,
            }
        )
        client = _Client([compact_wait])
        dialogue_box = NormalizedBox(0.11, 0.86, 0.33, 0.91)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("师姐", NormalizedBox(0.11, 0.80, 0.16, 0.84), 0.99),
                TextRegion("刚才那魔人有没有伤着你", dialogue_box, 0.96),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进剧情",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertIsNone(outcome.action)
        self.assertEqual(len(client.calls), 1)

    def test_submit_button_wins_over_passive_reward_text(self) -> None:
        client = _Client([_reply("wait")])
        submit_box = NormalizedBox(0.81, 0.28, 0.91, 0.35)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("境界目标", NormalizedBox(0.69, 0.15, 0.81, 0.21), 0.99),
                TextRegion("提交", submit_box, 0.99),
                TextRegion("今日境界奖励", NormalizedBox(0.61, 0.79, 0.75, 0.84), 0.99),
                TextRegion(
                    "突破瓶颈奖励：气血+2500",
                    NormalizedBox(0.17, 0.91, 0.46, 0.97),
                    0.99,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进任务",
        )

        self.assertEqual(outcome.action.target_label, "提交")  # type: ignore[union-attr]
        self.assertEqual(outcome.action.target_box, submit_box)  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_pet_panel_numbers_are_not_misclassified_as_dialogue_choices(self) -> None:
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("灵宠", NormalizedBox(0.03, 0.03, 0.11, 0.09), 0.99),
                TextRegion("主战位", NormalizedBox(0.25, 0.17, 0.36, 0.22), 0.99),
                TextRegion("辅助位", NormalizedBox(0.26, 0.70, 0.36, 0.75), 0.99),
                TextRegion("等级达28级", NormalizedBox(0.08, 0.79, 0.16, 0.87), 0.98),
                TextRegion("770", NormalizedBox(0.82, 0.50, 0.87, 0.55), 0.99),
                TextRegion(
                    "辅助位灵宠等级将临时提升至主战灵宠中的最低等级",
                    NormalizedBox(0.13, 0.90, 0.61, 0.95),
                    0.98,
                ),
            ),
        )

        self.assertIsNone(GroundedVlmPlanner._progress_control_candidate(current))

    def test_long_reward_broadcast_is_not_a_progress_control(self) -> None:
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(
                    "恭喜玩家领取首充礼包，获得玄光剑和丰厚奖励",
                    NormalizedBox(0.20, 0.03, 0.78, 0.08),
                    0.99,
                ),
            ),
        )

        self.assertIsNone(GroundedVlmPlanner._progress_control_candidate(current))

    def test_claimable_hud_label_is_not_a_generic_progress_control(self) -> None:
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("可领取", NormalizedBox(0.45, 0.82, 0.57, 0.90), 0.99),
            ),
        )

        self.assertIsNone(GroundedVlmPlanner._progress_control_candidate(current))

    def test_model_cannot_click_an_unscoped_claim_control(self) -> None:
        claim_box = NormalizedBox(0.45, 0.82, 0.57, 0.90)
        reply = json.dumps(
            {
                "kind": "act",
                "confidence": 0.95,
                "action": {
                    "kind": "click",
                    "target_label": "可领取",
                    "target_bbox": [
                        claim_box.left,
                        claim_box.top,
                        claim_box.right,
                        claim_box.bottom,
                    ],
                    "confidence": 0.95,
                    "key": None,
                },
                "wait_reason": None,
            }
        )
        client = _Client([reply])
        current = replace(
            _snapshot(),
            visible_text=(TextRegion("可领取", claim_box, 0.99),),
        )
        planner = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        )

        outcome = planner.decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进游戏",
        )

        self.assertEqual(outcome.kind, DecisionKind.ABSTAIN)
        self.assertIsNone(outcome.action)
        self.assertEqual(planner.last_decision_source, "unscoped_claim_blocked")
        self.assertEqual(len(client.calls), 1)

    def test_not_deployed_label_is_not_treated_as_deploy_button(self) -> None:
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("未上阵", NormalizedBox(0.08, 0.40, 0.19, 0.48), 0.99),
            ),
        )

        self.assertIsNone(GroundedVlmPlanner._progress_control_candidate(current))

    def test_model_target_is_snapped_to_matching_latest_ocr_box(self) -> None:
        loose_box = [0.68, 0.85, 0.85, 0.97]
        ocr_box = NormalizedBox(0.70, 0.87, 0.83, 0.95)
        reply = json.dumps(
            {
                "kind": "act",
                "confidence": 0.95,
                "action": {
                    "kind": "click",
                    "target_label": "获取灵宠",
                    "target_bbox": loose_box,
                    "confidence": 0.95,
                    "key": None,
                },
                "wait_reason": None,
            }
        )
        client = _Client([reply])
        current = replace(
            _snapshot(),
            visible_text=(TextRegion("获取灵宠", ocr_box, 0.99),),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进游戏",
        )

        self.assertEqual(outcome.action.target_box, ocr_box)  # type: ignore[union-attr]
        self.assertIn("snapped", outcome.explanation)
        self.assertEqual(len(client.calls), 1)

    def test_auto_continue_countdown_advances_with_space_without_model(self) -> None:
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(
                    "9秒后自动继续",
                    NormalizedBox(0.72, 0.68, 0.89, 0.74),
                    0.99,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进剧情",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.kind, GuiActionKind.CLICK)  # type: ignore[union-attr]
        assert outcome.action.target_box is not None  # type: ignore[union-attr]
        center = outcome.action.target_box.center
        self.assertAlmostEqual(center.x, 0.805, places=3)
        self.assertAlmostEqual(center.y, 0.71, places=3)
        self.assertEqual(client.calls, [])

    def test_review_story_cue_advances_using_the_configured_hotspot(self) -> None:
        # 回顾剧情 sidebar identifies the dialogue screen, but is never the
        # click target itself: the user-confirmed lower-right hotspot advances.
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("回顾剧情", NormalizedBox(0.02, 0.43, 0.05, 0.60), 0.99),
                TextRegion("师姐", NormalizedBox(0.11, 0.80, 0.16, 0.84), 0.99),
                TextRegion(
                    "刚才那魔人有没有伤着你",
                    NormalizedBox(0.11, 0.86, 0.33, 0.91),
                    0.96,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            dialogue_hotspot=(0.96, 0.915),
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进剧情",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "ui_dialogue_advance")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_little_dragon_rescue_choice_advances_without_review_story_label(self) -> None:
        client = _Client([_reply("wait")])
        choice_box = NormalizedBox(0.69, 0.69, 0.78, 0.74)
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion("拯救小龙", choice_box, 0.99),
                TextRegion("伊娇娅", NormalizedBox(0.09, 0.80, 0.18, 0.85), 0.99),
                TextRegion(
                    "这小龙受伤太重了，我得救他",
                    NormalizedBox(0.11, 0.86, 0.62, 0.91),
                    0.98,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进剧情",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "拯救小龙")  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_hold_prompt_uses_long_click(self) -> None:
        client = _Client([_reply("wait")])
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(
                    "传功疗伤",
                    NormalizedBox(0.76, 0.68, 0.86, 0.74),
                    0.99,
                ),
                TextRegion("伊娇娅", NormalizedBox(0.09, 0.80, 0.18, 0.85), 0.99),
                TextRegion(
                    "小龙伤得太重了",
                    NormalizedBox(0.11, 0.86, 0.40, 0.91),
                    0.98,
                ),
            ),
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
        ).decide(
            snapshot=current,
            frames=(_large_frame(100),),
            goal="持续推进剧情",
        )

        self.assertEqual(outcome.action.kind, GuiActionKind.LONG_CLICK)  # type: ignore[union-attr]
        self.assertEqual(client.calls, [])

    def test_compact_mode_parses_minimal_single_action_reply(self) -> None:
        compact_reply = json.dumps(
            {
                "kind": "act",
                "confidence": 0.96,
                "wait_reason": None,
                "action": {
                    "kind": "click",
                    "target_label": "主线任务",
                    "target_bbox": [0.1, 0.2, 0.3, 0.4],
                    "confidence": 0.96,
                    "key": None,
                },
            }
        )
        client = _Client([compact_reply])

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
        ).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="点击主线任务",
        )

        self.assertEqual(outcome.kind, DecisionKind.ACT)
        self.assertEqual(outcome.action.target_label, "主线任务")  # type: ignore[union-attr]
        self.assertEqual(outcome.action.risk.value, "normal")  # type: ignore[union-attr]
        self.assertEqual(
            client.calls[0]["response_format"], COMPACT_GROUNDING_RESPONSE_FORMAT
        )
        self.assertEqual(len(client.calls[0]["images"]), 1)  # type: ignore[arg-type]

    def test_compact_mode_normalizes_glm_1000_space_bbox(self) -> None:
        client = _Client(
            [
                json.dumps(
                    {
                        "kind": "act",
                        "confidence": 0.96,
                        "wait_reason": None,
                        "action": {
                            "kind": "click",
                            "target_label": "返回",
                            "target_bbox": [522, 925, 557, 975],
                            "confidence": 0.96,
                            "key": None,
                        },
                    }
                )
            ]
        )

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            coordinate_space="normalized_1000",
        ).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="返回上一页",
        )

        self.assertEqual(
            outcome.action.target_box,  # type: ignore[union-attr]
            NormalizedBox(0.522, 0.925, 0.557, 0.975),
        )

    def test_compact_mode_unwraps_json_answer_string(self) -> None:
        inner = {
            "kind": "wait",
            "confidence": 0.9,
            "action": None,
            "wait_reason": "animation",
        }
        client = _Client([json.dumps({"answer": json.dumps(inner)})])

        outcome = GroundedVlmPlanner(
            client,
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
        ).decide(
            snapshot=_snapshot(),
            frames=(_large_frame(100),),
            goal="等待动画",
        )

        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertEqual(outcome.wait_reason, WaitReason.ANIMATION)

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
        self.assertIn("No registered goal evidence: do not output DONE", instruction)
        self.assertIn("Continue only safe goal-relevant steps", instruction)

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
        self.assertIn('never key="return_button" or key="back"', instruction)
        self.assertIn("including visible Back/Return arrows", instruction)

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
        self.assertLess(
            instruction.index("goal completion evidence"), instruction.index("target_bbox")
        )
        self.assertIn("navigation row is not proof", instruction)
        self.assertIn("ALL required_goal_evidence", instruction)
        self.assertIn("目标页标题", instruction)
        self.assertIn("目标页事实", instruction)
        self.assertIn("visible clickable control", instruction)
        data = json.loads(instruction.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["missing_goal_evidence"], ["目标页标题", "目标页事实"])
        self.assertIn("never authority to click a disabled or missing target", instruction)

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
        data = json.loads(instruction.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["consumed_target"], "设置")
        self.assertIn("Do not reclick consumed_target", instruction)

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
        self.assertIn("stop with DONE", instruction)
        self.assertIn("do not click another option", instruction)
        data = json.loads(instruction.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(data["missing_goal_evidence"], [])

    def test_temporal_overviews_are_chronological_and_capped_at_three(self) -> None:
        client = _Client([_reply()])

        GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(),
            frames=tuple(_large_frame(value) for value in (1, 2, 3, 100)),
            goal="打开设置",
        )

        self.assertEqual(len(client.calls[0]["images"]), 4)  # type: ignore[arg-type]
        self.assertIn("Image 3 is the CURRENT full overview", str(client.calls[0]["instruction"]))

    def test_temporal_overviews_can_be_limited_for_local_inference(self) -> None:
        client = _Client([_reply()])

        GroundedVlmPlanner(client, max_temporal_frames=1).decide(
            snapshot=_snapshot(),
            frames=tuple(_large_frame(value) for value in (1, 2, 100)),
            goal="打开设置",
        )

        self.assertEqual(len(client.calls[0]["images"]), 2)  # type: ignore[arg-type]
        self.assertIn("Image 1 is the CURRENT full overview", str(client.calls[0]["instruction"]))

    def test_lightweight_mode_sends_exactly_one_current_image(self) -> None:
        client = _Client([_reply()])

        GroundedVlmPlanner(
            client,
            max_image_width=640,
            max_temporal_frames=1,
            max_target_crops=0,
        ).decide(
            snapshot=_snapshot(),
            frames=tuple(_large_frame(value) for value in (1, 2, 100)),
            goal="打开设置",
        )

        self.assertEqual(len(client.calls[0]["images"]), 1)  # type: ignore[arg-type]
        instruction = str(client.calls[0]["instruction"])
        self.assertIn("Image 1 is the CURRENT full overview", instruction)
        data = json.loads(instruction.split("SCENE_DATA_JSON:\n")[1])
        self.assertEqual(len(data["image_map"]), 1)

    def test_high_resolution_retry_still_honors_explicit_image_cap(self) -> None:
        client = _Client([_reply()])
        planner = GroundedVlmPlanner(
            client,
            max_image_width=640,
            max_temporal_frames=1,
        )
        from dataclasses import replace

        source = frame(100)
        large = replace(
            source,
            width=1000,
            height=100,
            stride_bytes=4000,
            buffer_handle=BufferHandle(
                source.buffer_handle.handle_id,
                source.buffer_handle.kind,
                1000 * 100 * 4,
                bytes([100]) * (1000 * 100 * 4),
            ),
        )

        planner.decide(
            snapshot=_snapshot(),
            frames=(large,),
            goal="打开设置",
            high_resolution_retry=True,
        )

        # F14: the retry now doubles the overview budget up to the 1280 hard
        # cap (min(2*640, 1280)); the 1000px frame sits under that budget, so
        # it travels at full width instead of the normal 640 cap.
        overview = client.calls[0]["images"][0]  # type: ignore[index]
        self.assertEqual(int.from_bytes(overview[16:20], "big"), 1000)
        self.assertTrue(planner.last_high_resolution_upgraded)

    def test_invalid_reply_gets_one_repair_then_abstains(self) -> None:
        client = _Client(["not json", "still not json"])

        outcome = GroundedVlmPlanner(client).decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(outcome.kind, DecisionKind.ABSTAIN)
        self.assertEqual(outcome.goal_status, GoalStatus.UNKNOWN)

    def test_valid_first_reply_updates_current_request_summary(self) -> None:
        # F13: the journal reply_head must show THIS decision's reply — the
        # normal first-success path updates last_raw_reply before parsing.
        reply_text = _reply()
        client = _Client([reply_text])
        planner = GroundedVlmPlanner(client)

        planner.decide(snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置")

        self.assertEqual(planner.last_raw_reply, reply_text)

    def test_previous_repair_reply_does_not_leak_to_next_decision(self) -> None:
        # F13: a repair reply from decision N must never surface as decision
        # N+1's summary — the fast-path reset and the fresh response both
        # overwrite the per-request state.
        client = _Client(["not json", "still not json", _reply()])
        planner = GroundedVlmPlanner(client)

        abstained = planner.decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )
        self.assertEqual(abstained.kind, DecisionKind.ABSTAIN)
        self.assertIn("not json", planner.last_raw_reply or "")

        resolved = planner.decide(
            snapshot=_snapshot(), frames=(_large_frame(100),), goal="打开设置"
        )
        self.assertEqual(resolved.kind, DecisionKind.ACT)
        self.assertEqual(planner.last_raw_reply, _reply())
        self.assertNotIn("not json", planner.last_raw_reply or "")

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
