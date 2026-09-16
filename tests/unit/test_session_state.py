from __future__ import annotations

import unittest

from tests.helpers import identity
from uga.agent.session_state import (
    ActionTrace,
    GameSessionState,
    ScreenType,
    find_xiuxian_path_quest_line,
    mumu_close_dialog_cancel,
    page_anchor_signature,
    page_level_value,
    quest_level_target,
    quest_page_keyword,
    stable_anchor_tokens,
    xiuxian_path_objective_goto,
)
from uga.control.lease import ControlMode
from uga.perception.schema import NormalizedBox, PerceptionSnapshot, TextRegion
from uga.time.clock import UGATime


def snapshot(
    number: int,
    timestamp_ns: int,
    *,
    visible_text: tuple[tuple[str, tuple[float, float, float, float], float], ...] = (),
    generation: int = 1,
) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        f"snapshot-{number}",
        f"frame-{number}",
        number,
        UGATime(timestamp_ns),
        identity(generation=generation),
        1,
        1,
        ControlMode.GUI,
        tuple(
            TextRegion(text, NormalizedBox(*box), confidence)
            for text, box, confidence in visible_text
        ),
        (),
        (),
        f"signature-{number}",
        0.95,
    )


def quest_frame(
    number: int,
    timestamp_ns: int,
    quest: str,
    *,
    quest_box: tuple[float, float, float, float] = (0.06, 0.24, 0.22, 0.30),
    quest_confidence: float = 0.99,
) -> PerceptionSnapshot:
    return snapshot(
        number,
        timestamp_ns,
        visible_text=(
            ("主线", (0.02, 0.12, 0.10, 0.18), 0.97),
            (quest, quest_box, quest_confidence),
        ),
    )


class QuestMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = GameSessionState()

    def test_first_task_is_adopted_immediately(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "桃夭"), 0)

        task = self.state.latest_main_task
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.raw_text, "桃夭")
        self.assertEqual(task.canonical_text, "桃夭")
        self.assertEqual(task.generation, 0)

    def test_task_without_main_header_is_not_collected(self) -> None:
        self.state.observe_snapshot(
            snapshot(
                1,
                0,
                visible_text=(("桃夭", (0.06, 0.24, 0.22, 0.30), 0.99),),
            ),
            0,
        )

        self.assertIsNone(self.state.latest_main_task)

    def test_low_confidence_task_under_header_is_not_collected(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "桃夭", quest_confidence=0.6), 0)

        self.assertIsNone(self.state.latest_main_task)

    def test_single_character_ocr_jitter_keeps_the_same_generation(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)
        self.state.observe_snapshot(quest_frame(2, 500_000_000, "终于来到桃天"), 500_000_000)

        task = self.state.latest_main_task
        assert task is not None
        self.assertEqual(task.canonical_text, "终于来到桃夭")
        self.assertEqual(task.raw_text, "终于来到桃天")
        self.assertEqual(task.generation, 0)

    def test_new_task_text_needs_two_consecutive_frames_to_advance_generation(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)
        # One isolated flicker of different text must not flip the quest.
        self.state.observe_snapshot(quest_frame(2, 500_000_000, "获取灵宠"), 500_000_000)
        self.state.observe_snapshot(quest_frame(3, 1_000_000_000, "终于来到桃夭"), 1_000_000_000)

        task = self.state.latest_main_task
        assert task is not None
        self.assertEqual(task.canonical_text, "终于来到桃夭")
        self.assertEqual(task.generation, 0)

        # The same new text on two consecutive observations advances it.
        self.state.observe_snapshot(quest_frame(4, 1_500_000_000, "获取灵宠"), 1_500_000_000)
        self.state.observe_snapshot(quest_frame(5, 2_000_000_000, "获取灵宠"), 2_000_000_000)

        task = self.state.latest_main_task
        assert task is not None
        self.assertEqual(task.canonical_text, "获取灵宠")
        self.assertEqual(task.generation, 1)

    def test_hidden_tracker_keeps_the_last_known_quest(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)
        self.state.observe_snapshot(
            snapshot(
                2,
                500_000_000,
                visible_text=(("确定", (0.4, 0.5, 0.6, 0.6), 0.99),),
            ),
            500_000_000,
        )

        task = self.state.latest_main_task
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.canonical_text, "终于来到桃夭")


class ScreenClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = GameSessionState()

    def test_dialogue_cue_is_detected(self) -> None:
        self.state.observe_snapshot(
            snapshot(
                1,
                0,
                visible_text=(
                    ("3秒后自动继续", (0.4, 0.9, 0.6, 0.95), 0.99),
                    ("师姐", (0.1, 0.8, 0.2, 0.85), 0.99),
                ),
            ),
            0,
        )

        self.assertEqual(self.state.screen_type, ScreenType.DIALOGUE)
        self.assertTrue(self.state.dialogue_active)

    def test_world_screen_needs_the_main_header(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)

        self.assertEqual(self.state.screen_type, ScreenType.WORLD)

    def test_feature_page_is_detected_from_the_title_band(self) -> None:
        self.state.observe_snapshot(
            snapshot(
                1,
                0,
                visible_text=(
                    ("灵宠", (0.03, 0.03, 0.11, 0.09), 0.99),
                    ("主战位", (0.26, 0.16, 0.37, 0.22), 0.99),
                ),
            ),
            0,
        )

        self.assertEqual(self.state.screen_type, ScreenType.FEATURE)
        self.assertEqual(self.state.feature_page, "灵宠")

    def test_loading_cue_wins_over_other_screen_types(self) -> None:
        self.state.observe_snapshot(
            snapshot(
                1,
                0,
                visible_text=(
                    ("灵宠", (0.03, 0.03, 0.11, 0.09), 0.99),
                    ("加载中", (0.45, 0.45, 0.55, 0.55), 0.99),
                ),
            ),
            0,
        )

        self.assertEqual(self.state.screen_type, ScreenType.LOADING)


class ContextSummaryTests(unittest.TestCase):
    def test_summary_includes_quest_page_and_recent_actions(self) -> None:
        state = GameSessionState()
        state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)
        state.record_trace(
            ActionTrace(
                action_id="a-1",
                source="model",
                proposed_label="获取灵宠",
                proposed_box=None,
                final_label="获取灵宠",
                final_box=None,
                point=(0.7, 0.9),
                effect="ineffective",
                created_at_ns=1,
                updated_at_ns=2,
            )
        )
        state.record_trace(
            ActionTrace(
                action_id="a-2",
                source="ocr_task_panel_fast",
                proposed_label="终于来到桃夭",
                proposed_box=None,
                final_label="终于来到桃夭",
                final_box=None,
                point=(0.1, 0.27),
                effect="verified",
                created_at_ns=3,
                updated_at_ns=4,
            )
        )

        summary = state.context_summary()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("终于来到桃夭", summary)
        self.assertIn("获取灵宠→无效果", summary)
        self.assertIn("已生效", summary)

    def test_summary_without_quest_returns_page_only(self) -> None:
        state = GameSessionState()
        state.observe_snapshot(
            snapshot(1, 0, visible_text=(("灵宠", (0.03, 0.03, 0.11, 0.09), 0.99),)),
            0,
        )

        summary = state.context_summary()
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("功能页", summary)
        self.assertIn("灵宠", summary)
        self.assertNotIn("最近真实动作", summary)


class QuestTargetTests(unittest.TestCase):
    def test_quest_level_target_parsing(self) -> None:
        self.assertEqual(quest_level_target("拥有1只灵宠达到10级0/1"), 10)
        self.assertEqual(quest_level_target("灵宠等级达到 25 级"), 25)
        self.assertIsNone(quest_level_target("通关兰若妖寺1/1"))
        self.assertIsNone(quest_level_target(None))

    def test_page_level_value_reads_the_pet_level_marker(self) -> None:
        regions = (
            TextRegion("等级 10/40", NormalizedBox(0.6, 0.2, 0.8, 0.3), 0.99),
            TextRegion("修为1814", NormalizedBox(0.1, 0.5, 0.3, 0.6), 0.99),
            TextRegion("上阵3只4星灵宠", NormalizedBox(0.6, 0.7, 0.9, 0.8), 0.99),
        )

        self.assertEqual(page_level_value(regions), 10)
        self.assertIsNone(page_level_value(()))

    def test_context_summary_includes_the_level_target_rule(self) -> None:
        state = GameSessionState()
        state.observe_snapshot(quest_frame(1, 0, "拥有1只灵宠达到10级0/1"), 0)

        summary = state.context_summary()
        assert summary is not None
        self.assertIn("等级达到10级", summary)
        self.assertIn("禁止继续点击升级类按钮", summary)

    def test_quest_page_keyword_extraction(self) -> None:
        self.assertEqual(quest_page_keyword("拥有1只灵宠达到10级0/1"), "灵宠")
        self.assertEqual(quest_page_keyword("强化坐骑到3阶"), "坐骑")
        self.assertIsNone(quest_page_keyword("通关兰若妖寺1/1"))
        self.assertIsNone(quest_page_keyword(None))

    def test_quest_memory_survives_a_restart_via_persistence(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_state.json"
            first = GameSessionState()
            first.set_persistence(path)
            first.observe_snapshot(quest_frame(1, 0, "拥有1只灵宠达到10级0/1"), 0)

            self.assertTrue(path.exists())
            restored = GameSessionState()
            restored.set_persistence(path)
            task = restored.latest_main_task
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task.raw_text, "拥有1只灵宠达到10级0/1")
            self.assertEqual(task.canonical_text, "拥有1只灵宠达到10级0/1")
            summary = restored.context_summary()
            assert summary is not None
            self.assertIn("拥有1只灵宠达到10级0/1", summary)
            self.assertIn("等级达到10级", summary)


class TraceAndAnchorTests(unittest.TestCase):
    def test_record_trace_bounds_the_recent_ring(self) -> None:
        state = GameSessionState()
        for index in range(20):
            state.record_trace(
                ActionTrace(
                    action_id=f"a-{index}",
                    source="model",
                    proposed_label="x",
                    proposed_box=None,
                    final_label="x",
                    final_box=None,
                    point=None,
                    effect="ineffective",
                    created_at_ns=index,
                    updated_at_ns=index,
                )
            )

        self.assertEqual(len(state.recent_actions), 12)
        self.assertEqual(state.latest_trace().action_id, "a-19")

    def test_update_trace_marks_the_effect(self) -> None:
        state = GameSessionState()
        state.record_trace(
            ActionTrace(
                action_id="a-1",
                source="model",
                proposed_label="提交",
                proposed_box=None,
                final_label="提交",
                final_box=None,
                point=(0.8, 0.3),
                effect="pending",
                created_at_ns=1,
                updated_at_ns=1,
            )
        )
        state.update_trace("a-1", effect="verified", detail="page changed")

        trace = state.latest_trace()
        assert trace is not None
        self.assertEqual(trace.effect, "verified")
        self.assertEqual(state.last_verified_progress_at_ns, 1)

    def test_anchor_signature_ignores_numeric_jitter(self) -> None:
        before = page_anchor_signature(
            (
                TextRegion("修为1814", NormalizedBox(0.05, 0.03, 0.25, 0.10), 0.99),
                TextRegion("灵宠", NormalizedBox(0.05, 0.10, 0.15, 0.15), 0.99),
            )
        )
        after = page_anchor_signature(
            (
                TextRegion("修为770/15", NormalizedBox(0.05, 0.03, 0.25, 0.10), 0.99),
                TextRegion("灵宠", NormalizedBox(0.05, 0.10, 0.15, 0.15), 0.99),
            )
        )

        self.assertEqual(before, after)
        self.assertEqual(before, frozenset({"修为", "灵宠"}))

    def test_stable_anchor_tokens_drop_mostly_numeric_labels(self) -> None:
        tokens = stable_anchor_tokens(("770/15", "主战位", "1814", "3秒后自动继续"))

        self.assertEqual(tokens, frozenset({"主战位", "秒后自动继续"}))


    def test_expanded_panel_progress_line_is_adopted_below_the_old_band(self) -> None:
        # 展开的任务面板布局（实测）：主线任务行（带进度 0/2）中心 y=0.3805
        # —— 旧条带 0.38 恰好压线拒绝过它导致任务记忆过期。现在必须被采纳
        # （两帧规则推进 generation），且进度行优先于同竖列的更长干扰文本。
        state = GameSessionState()
        state.observe_snapshot(quest_frame(1, 0, "完成3次30级装备秘境"), 0)
        rows = (
            ("主线", (0.055, 0.189, 0.092, 0.223), 1.00),
            ("悬赏榜", (0.176, 0.221, 0.227, 0.253), 1.00),
            ("修仙之路", (0.080, 0.242, 0.147, 0.278), 1.00),
            ("完成3次30级装备秘境", (0.081, 0.278, 0.220, 0.307), 1.00),
            ("一段非常长的干扰文本没有任何进度计数也不是任务", (0.045, 0.320, 0.270, 0.355), 0.99),
            ("完成2个修仙之路目标0/2仙途轧缘仙遇", (0.043, 0.363, 0.293, 0.398), 0.96),
        )
        state.observe_snapshot(snapshot(2, 500_000_000, visible_text=rows), 500_000_000)

        # 单帧不动（新任务文本需两帧确认）。
        task = state.latest_main_task
        assert task is not None
        self.assertEqual(task.canonical_text, "完成3次30级装备秘境")

        state.observe_snapshot(snapshot(3, 1_000_000_000, visible_text=rows), 1_000_000_000)

        task = state.latest_main_task
        assert task is not None
        self.assertEqual(task.canonical_text, "完成2个修仙之路目标0/2仙途轧缘仙遇")
        self.assertEqual(task.generation, 1)


class XiuxianPathQuestLineTests(unittest.TestCase):
    def test_progress_line_is_found(self) -> None:
        line = find_xiuxian_path_quest_line(
            (
                TextRegion("修仙之路", NormalizedBox(0.080, 0.242, 0.147, 0.278), 1.00),
                TextRegion(
                    "完成2个修仙之路目标0/2仙途轧缘仙遇",
                    NormalizedBox(0.043, 0.363, 0.293, 0.398),
                    0.96,
                ),
            )
        )

        assert line is not None
        self.assertEqual(line.text, "完成2个修仙之路目标0/2仙途轧缘仙遇")

    def test_bare_section_header_does_not_match(self) -> None:
        self.assertIsNone(
            find_xiuxian_path_quest_line(
                (TextRegion("修仙之路", NormalizedBox(0.080, 0.242, 0.147, 0.278), 1.00),)
            )
        )

    def test_low_confidence_or_out_of_area_lines_are_ignored(self) -> None:
        self.assertIsNone(
            find_xiuxian_path_quest_line(
                (
                    TextRegion(
                        "完成2个修仙之路目标0/2",
                        NormalizedBox(0.043, 0.363, 0.293, 0.398),
                        0.7,
                    ),
                )
            )
        )
        self.assertIsNone(
            find_xiuxian_path_quest_line(
                (
                    TextRegion(
                        "完成2个修仙之路目标0/2",
                        NormalizedBox(0.40, 0.363, 0.60, 0.398),
                        0.96,
                    ),
                )
            )
        )

    def test_objective_goto_button_found_on_interface(self) -> None:
        # 修仙之路界面（实测布局）：每个目标行的"前往"按钮在目标文字右下
        # 方 ~0.02；目标文字本身不可点。规则层返回最上方目标行的前往。
        button = xiuxian_path_objective_goto(
            (
                TextRegion("修仙之路", NormalizedBox(0.357, 0.146, 0.617, 0.282), 1.00),
                TextRegion("【装备】", NormalizedBox(0.340, 0.307, 0.399, 0.348), 1.00),
                TextRegion(
                    "完成3次30级装备秘境",
                    NormalizedBox(0.423, 0.311, 0.558, 0.344),
                    1.00,
                ),
                TextRegion("前往", NormalizedBox(0.767, 0.328, 0.807, 0.367), 1.00),
                TextRegion(
                    "完成5环悬赏任务",
                    NormalizedBox(0.423, 0.416, 0.532, 0.452),
                    1.00,
                ),
                TextRegion("前往", NormalizedBox(0.766, 0.432, 0.807, 0.472), 1.00),
            )
        )

        assert button is not None
        self.assertAlmostEqual(button.box.center.y, 0.3475, places=3)

    def test_objective_goto_requires_interface_title_and_button(self) -> None:
        rows = (
            TextRegion(
                "完成3次30级装备秘境",
                NormalizedBox(0.423, 0.311, 0.558, 0.344),
                1.00,
            ),
            TextRegion("前往", NormalizedBox(0.767, 0.328, 0.807, 0.367), 1.00),
        )
        self.assertIsNone(xiuxian_path_objective_goto(rows))
        self.assertIsNone(
            xiuxian_path_objective_goto(
                (
                    TextRegion(
                        "修仙之路",
                        NormalizedBox(0.357, 0.146, 0.617, 0.282),
                        1.00,
                    ),
                    TextRegion(
                        "完成3次30级装备秘境",
                        NormalizedBox(0.423, 0.311, 0.558, 0.344),
                        1.00,
                    ),
                )
            )
        )


class MumuCloseDialogTests(unittest.TestCase):
    def test_cancel_region_returned_for_mumu_confirm_dialog(self) -> None:
        cancel = mumu_close_dialog_cancel(
            (
                TextRegion(
                    "确定要关闭 “MuMu安卓设备-1” 吗?",
                    NormalizedBox(0.36, 0.38, 0.72, 0.43),
                    0.97,
                ),
                TextRegion("不再提示", NormalizedBox(0.38, 0.44, 0.46, 0.47), 0.95),
                TextRegion("确定", NormalizedBox(0.40, 0.55, 0.50, 0.60), 0.96),
                TextRegion("取消", NormalizedBox(0.53, 0.55, 0.64, 0.60), 0.96),
            )
        )

        assert cancel is not None
        self.assertEqual(cancel.text, "取消")

    def test_game_confirm_dialog_without_mumu_marker_is_ignored(self) -> None:
        cancel = mumu_close_dialog_cancel(
            (
                TextRegion("确定要放弃当前任务吗", NormalizedBox(0.36, 0.38, 0.72, 0.43), 0.97),
                TextRegion("确定", NormalizedBox(0.40, 0.55, 0.50, 0.60), 0.96),
                TextRegion("取消", NormalizedBox(0.53, 0.55, 0.64, 0.60), 0.96),
            )
        )

        self.assertIsNone(cancel)

    def test_device_name_in_title_bar_strip_alone_does_not_trigger(self) -> None:
        cancel = mumu_close_dialog_cancel(
            (
                TextRegion("MuMu安卓设备-1", NormalizedBox(0.02, 0.005, 0.20, 0.035), 0.99),
                TextRegion("取消", NormalizedBox(0.53, 0.55, 0.64, 0.60), 0.96),
            )
        )

        self.assertIsNone(cancel)


if __name__ == "__main__":
    unittest.main()
