from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from tests.unit.test_closed_loop_supervisor import outcome as supervisor_outcome
from uga.agent.session_state import (
    ActionTrace,
    GameSessionState,
    ScreenType,
    artifact_result_close_control,
    basic_onboarding_complete,
    black_clad_leader_combat_active,
    blessing_feed_page_visible,
    blessing_onboarding_control,
    demon_sect_disciple_combat_active,
    demonized_spirit_combat_active,
    equipment_dungeon_active,
    equipment_dungeon_control,
    equipment_recruiting_visible,
    equipment_reward_popup_visible,
    find_xiuxian_path_quest_line,
    heroic_rescue_combat_active,
    master_message_event_control,
    mumu_close_dialog_cancel,
    notice_board_event_stage,
    page_anchor_signature,
    page_level_value,
    peach_talisman_barrier_active,
    peach_tree_spirit_combat_active,
    pet_companion_current_slot_ready,
    pet_companion_deployment_complete,
    pet_companion_taotian_control,
    pet_information_tab_control,
    pet_training_entry_control,
    pet_upgrade_control,
    pet_upgrade_information_tab_control,
    quest_is_pet_companion_task,
    quest_is_pet_upgrade_task,
    quest_level_target,
    quest_page_keyword,
    raging_tree_spirit_combat_active,
    realm_breakthrough_animation_active,
    realm_breakthrough_control,
    realm_breakthrough_entry_control,
    realm_breakthrough_success,
    realm_promotion_completed,
    red_dust_auto_enable_ready,
    romance_ad_visible,
    settings_page_visible,
    stable_anchor_tokens,
    summon_bell_interaction_active,
    summon_once_control,
    summon_result_close_control,
    summon_world_control,
    welfare_page_visible,
    world_chat_send_control,
    world_chat_sent_visible,
    xiuxian_path_objective_goto,
    xiuxian_path_recorded_objective_control,
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
    def test_recorded_notice_board_event_advances_left_then_right(self) -> None:
        prompt = TextRegion(
            "告示牌上有许多消息",
            NormalizedBox(0.40, 0.88, 0.62, 0.95),
            0.99,
        )
        self.assertEqual(notice_board_event_stage((prompt,)), "left")

        clue = TextRegion(
            "问道大会最高奖赏洛神泪",
            NormalizedBox(0.08, 0.78, 0.34, 0.88),
            0.99,
        )
        self.assertEqual(notice_board_event_stage((prompt, clue)), "right")
        self.assertIsNone(notice_board_event_stage((clue,)))

    def test_recorded_master_message_and_artifact_close_controls_are_narrow(
        self,
    ) -> None:
        letter = TextRegion(
            "桃源居传音",
            NormalizedBox(0.42, 0.18, 0.58, 0.25),
            0.99,
        )
        event = TextRegion(
            "河洛城即将召开问道大会",
            NormalizedBox(0.45, 0.29, 0.70, 0.39),
            0.99,
        )
        signature = TextRegion(
            "——师父",
            NormalizedBox(0.56, 0.72, 0.68, 0.82),
            0.99,
        )
        self.assertEqual(
            master_message_event_control((letter, event, signature)),
            signature,
        )
        self.assertIsNone(master_message_event_control((event, signature)))

        artifact = TextRegion(
            "承影仙剑",
            NormalizedBox(0.45, 0.30, 0.62, 0.39),
            0.99,
        )
        close = TextRegion(
            "点击任意处关闭(30秒)",
            NormalizedBox(0.39, 0.88, 0.61, 0.95),
            0.99,
        )
        self.assertEqual(
            artifact_result_close_control((artifact, close)),
            close,
        )
        self.assertIsNone(artifact_result_close_control((close,)))

    def test_recorded_pet_companion_controls_replace_xiaoqinglong_with_taotian(
        self,
    ) -> None:
        self.assertTrue(quest_is_pet_companion_task("桃天伴随 上阵桃天，协助战斗 0/1"))
        self.assertFalse(quest_is_pet_companion_task("小龙升级"))

        title = TextRegion("灵宠", NormalizedBox(0.05, 0.05, 0.15, 0.13), 0.99)
        main_slot = TextRegion("主战位", NormalizedBox(0.26, 0.17, 0.38, 0.24), 0.99)
        formation = TextRegion(
            "布阵总修为:1540",
            NormalizedBox(0.65, 0.35, 0.88, 0.42),
            0.99,
        )
        rest = TextRegion(
            "让小青龙歇息一下",
            NormalizedBox(0.36, 0.45, 0.63, 0.55),
            0.99,
        )
        current = (title, main_slot, formation, rest)
        self.assertTrue(
            pet_companion_current_slot_ready(current, task_active=True)
        )
        self.assertFalse(
            pet_companion_current_slot_ready(current, task_active=False)
        )

        picker = TextRegion(
            "选择主战位灵宠",
            NormalizedBox(0.68, 0.14, 0.88, 0.22),
            0.99,
        )
        taotian = TextRegion("桃天", NormalizedBox(0.70, 0.23, 0.82, 0.34), 0.99)
        xiaoqinglong = TextRegion(
            "小青龙",
            NormalizedBox(0.70, 0.38, 0.82, 0.49),
            0.99,
        )
        self.assertEqual(
            pet_companion_taotian_control(
                (title, picker, taotian, xiaoqinglong),
                task_active=True,
            ),
            taotian,
        )
        self.assertIsNone(
            pet_companion_taotian_control(
                (title, picker, xiaoqinglong),
                task_active=True,
            )
        )

        complete = (
            title,
            main_slot,
            TextRegion(
                "布阵总修为:1814",
                NormalizedBox(0.65, 0.35, 0.88, 0.42),
                0.99,
            ),
            TextRegion("仙", NormalizedBox(0.06, 0.23, 0.09, 0.28), 0.99),
            TextRegion("1级", NormalizedBox(0.08, 0.23, 0.13, 0.28), 0.99),
        )
        self.assertTrue(
            pet_companion_deployment_complete(complete, task_active=True)
        )
        self.assertFalse(
            pet_companion_deployment_complete(
                complete + (picker,),
                task_active=True,
            )
        )

    def test_recorded_contract_bell_controls_are_narrow_and_ordered(self) -> None:
        quest = TextRegion(
            "使用契铃唤醒桃天 0/1",
            NormalizedBox(0.04, 0.27, 0.30, 0.34),
            0.99,
        )
        menu = TextRegion("菜单", NormalizedBox(0.93, 0.29, 0.99, 0.39), 0.99)
        entry = TextRegion("铃唤", NormalizedBox(0.88, 0.42, 0.96, 0.54), 0.99)

        collapsed = summon_world_control((quest, menu))
        assert collapsed is not None
        self.assertEqual(collapsed, ("menu", menu))

        expanded = summon_world_control((quest, menu, entry))
        assert expanded is not None
        self.assertEqual(expanded, ("entry", entry))
        self.assertIsNone(summon_world_control((menu, entry)))

        title = TextRegion("召唤", NormalizedBox(0.04, 0.05, 0.16, 0.13), 0.99)
        once = TextRegion("铃唤一次", NormalizedBox(0.30, 0.80, 0.52, 0.91), 0.99)
        ten = TextRegion("铃唤十次", NormalizedBox(0.62, 0.80, 0.80, 0.91), 0.99)
        self.assertEqual(summon_once_control((title, once, ten)), once)
        self.assertIsNone(summon_once_control((once, ten)))

        bell_title = TextRegion("铃唤", NormalizedBox(0.04, 0.05, 0.16, 0.13), 0.99)
        instruction = TextRegion(
            "滑动手指摇动铃铛召唤灵宠",
            NormalizedBox(0.34, 0.90, 0.66, 0.96),
            0.99,
        )
        self.assertTrue(summon_bell_interaction_active((bell_title, instruction)))
        self.assertFalse(summon_bell_interaction_active((instruction,)))

        pet_name = TextRegion("桃天", NormalizedBox(0.88, 0.20, 0.96, 0.40), 0.99)
        close = TextRegion(
            "点击空白区域关闭",
            NormalizedBox(0.67, 0.87, 0.89, 0.94),
            0.99,
        )
        self.assertEqual(summon_result_close_control((pet_name, close)), close)
        self.assertIsNone(summon_result_close_control((close,)))

    def test_recorded_pet_upgrade_controls_require_their_page_anchors(self) -> None:
        entry_regions = (
            TextRegion("小龙升级", NormalizedBox(0.04, 0.22, 0.18, 0.27), 0.99),
            TextRegion("灵宠", NormalizedBox(0.92, 0.40, 0.99, 0.52), 0.99),
        )
        self.assertIsNotNone(pet_training_entry_control(entry_regions))

        formation_regions = (
            TextRegion("灵宠", NormalizedBox(0.05, 0.06, 0.14, 0.12), 0.99),
            TextRegion("布阵目标", NormalizedBox(0.70, 0.17, 0.82, 0.23), 0.99),
            TextRegion("信息", NormalizedBox(0.93, 0.30, 0.99, 0.42), 0.99),
        )
        self.assertIsNotNone(pet_information_tab_control(formation_regions))

        training_regions = (
            TextRegion("灵宠", NormalizedBox(0.05, 0.06, 0.14, 0.12), 0.99),
            TextRegion("等级 1/40", NormalizedBox(0.62, 0.23, 0.75, 0.29), 0.99),
            TextRegion("基础属性", NormalizedBox(0.62, 0.39, 0.76, 0.45), 0.99),
            TextRegion("升2级", NormalizedBox(0.83, 0.23, 0.92, 0.30), 0.99),
        )
        self.assertIsNotNone(pet_upgrade_control(training_regions, 2))
        self.assertIsNone(pet_upgrade_control(training_regions, 1))

        self.assertTrue(quest_is_pet_upgrade_task("拥有1只灵宠达到10级0/1仙途礼"))
        self.assertFalse(quest_is_pet_upgrade_task("有1只灵宠达到4星"))
        wrong_subpage = (
            TextRegion("灵宠", NormalizedBox(0.05, 0.06, 0.14, 0.12), 0.99),
            TextRegion("信息", NormalizedBox(0.93, 0.30, 0.99, 0.42), 0.99),
            TextRegion("升星", NormalizedBox(0.93, 0.43, 0.99, 0.55), 0.99),
            TextRegion("技能升级", NormalizedBox(0.63, 0.39, 0.76, 0.45), 0.99),
            TextRegion("成长率", NormalizedBox(0.63, 0.56, 0.73, 0.62), 0.99),
        )
        self.assertIsNotNone(
            pet_upgrade_information_tab_control(wrong_subpage, task_active=True)
        )
        self.assertIsNone(
            pet_upgrade_information_tab_control(wrong_subpage, task_active=False)
        )

    def test_recorded_red_dust_and_combat_anchors_are_narrow(self) -> None:
        def regions(*labels: str) -> tuple[TextRegion, ...]:
            return tuple(
                TextRegion(label, NormalizedBox(0.04, 0.22, 0.30, 0.32), 0.99)
                for label in labels
            )

        self.assertTrue(red_dust_auto_enable_ready(regions("红尘入世", "与师姐一起下山")))
        self.assertFalse(red_dust_auto_enable_ready(regions("红尘入世", "似乎有人呼救")))
        demonized = regions("魔化精怪", "制服魔化妖灵") + (
            TextRegion("魔化猪猪", NormalizedBox(0.40, 0.30, 0.55, 0.38), 0.99),
        )
        peach_tree = regions("暴虐精怪", "制服桃木精") + (
            TextRegion("桃木精", NormalizedBox(0.40, 0.30, 0.55, 0.38), 0.99),
        )
        self.assertTrue(demonized_spirit_combat_active(demonized))
        self.assertTrue(peach_tree_spirit_combat_active(peach_tree))
        raging_tree = regions("狂暴树精", "制服狂暴的树精") + (
            TextRegion("千年桃木精 Lv.7", NormalizedBox(0.35, 0.08, 0.60, 0.14), 0.99),
        )
        self.assertTrue(raging_tree_spirit_combat_active(raging_tree))
        demon_sect = regions("孤身应战", "击败魔宗门徒 0/3") + (
            TextRegion("魔宗门徒", NormalizedBox(0.40, 0.22, 0.55, 0.30), 0.99),
        )
        self.assertTrue(demon_sect_disciple_combat_active(demon_sect))
        self.assertFalse(demon_sect_disciple_combat_active(regions("孤身应战")))

        boss = regions("幕后黑手", "独自对抗幕后黑手") + (
            TextRegion(
                "黑衣人头目 Lv.20",
                NormalizedBox(0.35, 0.07, 0.62, 0.14),
                0.99,
            ),
        )
        self.assertTrue(black_clad_leader_combat_active(boss))
        self.assertFalse(black_clad_leader_combat_active(regions("幕后黑手")))

        heroic_rescue = regions("英雄救美", "击败池早和姚九 0/1") + (
            TextRegion("姚九", NormalizedBox(0.52, 0.22, 0.61, 0.30), 0.99),
        )
        self.assertTrue(heroic_rescue_combat_active(heroic_rescue))
        self.assertFalse(heroic_rescue_combat_active(regions("英雄救美")))
        self.assertFalse(
            heroic_rescue_combat_active(
                (TextRegion("姚九", NormalizedBox(0.52, 0.22, 0.61, 0.30), 0.99),)
            )
        )

    def test_peach_talisman_barrier_requires_instruction_and_countdown(self) -> None:
        instruction = TextRegion(
            "用桃天符印开启结界",
            NormalizedBox(0.40, 0.86, 0.62, 0.92),
            0.99,
        )
        countdown = TextRegion(
            "27秒后将自动完成",
            NormalizedBox(0.42, 0.92, 0.61, 0.98),
            0.99,
        )
        self.assertTrue(peach_talisman_barrier_active((instruction, countdown)))
        self.assertFalse(peach_talisman_barrier_active((instruction,)))
        self.assertFalse(
            peach_talisman_barrier_active(
                (
                    instruction,
                    TextRegion(
                        "27秒后将自动完成",
                        NormalizedBox(0.42, 0.30, 0.61, 0.36),
                        0.99,
                    ),
                )
            )
        )

    def test_realm_breakthrough_controls_are_ordered_and_narrow(self) -> None:
        entry = (
            TextRegion(
                "境界突破 境界达到炼气前期 0/1",
                NormalizedBox(0.03, 0.23, 0.30, 0.34),
                0.99,
            ),
            TextRegion("变强", NormalizedBox(0.18, 0.07, 0.27, 0.16), 0.99),
        )
        self.assertIsNotNone(realm_breakthrough_entry_control(entry))
        self.assertIsNone(realm_breakthrough_entry_control(entry[1:]))

        page = (
            TextRegion("境界", NormalizedBox(0.05, 0.05, 0.18, 0.13), 0.99),
            TextRegion(
                "直面天劫突破自身",
                NormalizedBox(0.61, 0.23, 0.82, 0.32),
                0.99,
            ),
            TextRegion("提交", NormalizedBox(0.79, 0.29, 0.91, 0.38), 0.99),
            TextRegion("领取", NormalizedBox(0.69, 0.86, 0.82, 0.95), 0.99),
        )
        control = realm_breakthrough_control(page)
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control[0], "submit")

        claimed = page[:2] + (
            TextRegion("已完成", NormalizedBox(0.79, 0.29, 0.91, 0.38), 0.99),
            page[-1],
        )
        control = realm_breakthrough_control(claimed)
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control[0], "claim")

        animation = claimed + (
            TextRegion("突破瓶颈", NormalizedBox(0.25, 0.40, 0.42, 0.59), 0.99),
            TextRegion("已领取", NormalizedBox(0.70, 0.86, 0.82, 0.95), 0.99),
        )
        self.assertTrue(realm_breakthrough_animation_active(animation))
        self.assertTrue(
            realm_breakthrough_success(
                (
                    TextRegion(
                        "突破成功",
                        NormalizedBox(0.38, 0.18, 0.62, 0.28),
                        0.99,
                    ),
                )
            )
        )

        promoted = (
            TextRegion("境界", NormalizedBox(0.05, 0.05, 0.18, 0.13), 0.99),
            TextRegion("境界目标", NormalizedBox(0.68, 0.15, 0.83, 0.21), 0.99),
            TextRegion("上阵两只灵宠", NormalizedBox(0.61, 0.24, 0.78, 0.30), 0.99),
            TextRegion("1/2", NormalizedBox(0.61, 0.30, 0.67, 0.35), 0.99),
            TextRegion("去完成", NormalizedBox(0.81, 0.30, 0.91, 0.36), 0.99),
            TextRegion("悬铃塔通关第2层", NormalizedBox(0.61, 0.39, 0.80, 0.45), 0.99),
            TextRegion("0/2", NormalizedBox(0.61, 0.46, 0.67, 0.51), 0.99),
            TextRegion("去完成", NormalizedBox(0.81, 0.46, 0.91, 0.52), 0.99),
            TextRegion("修为达到10000", NormalizedBox(0.61, 0.55, 0.78, 0.61), 0.99),
            TextRegion("11665/10000", NormalizedBox(0.61, 0.62, 0.75, 0.67), 0.99),
            TextRegion("已完成", NormalizedBox(0.82, 0.62, 0.90, 0.67), 0.99),
            TextRegion("晋升奖励：攻击+50", NormalizedBox(0.25, 0.82, 0.50, 0.88), 0.99),
        )
        self.assertTrue(realm_promotion_completed(promoted))
        self.assertFalse(realm_promotion_completed(promoted[:4]))
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

    def test_welfare_page_is_feature_even_though_welfare_is_not_a_quest(self) -> None:
        regions = (
            TextRegion("福利", NormalizedBox(0.06, 0.05, 0.16, 0.12), 0.99),
            TextRegion("在线奖励", NormalizedBox(0.02, 0.17, 0.14, 0.25), 0.99),
            TextRegion("每日签到", NormalizedBox(0.17, 0.16, 0.34, 0.27), 0.99),
        )
        self.assertTrue(welfare_page_visible(regions))
        self.state.observe_snapshot(
            snapshot(
                1,
                0,
                visible_text=tuple(
                    (r.text, (r.box.left, r.box.top, r.box.right, r.box.bottom), r.confidence)
                    for r in regions
                ),
            ),
            0,
        )
        self.assertEqual(self.state.screen_type, ScreenType.FEATURE)
        self.assertEqual(self.state.feature_page, "福利")

    def test_settings_title_is_a_feature_but_world_entry_is_not(self) -> None:
        title = TextRegion("设置", NormalizedBox(0.05, 0.04, 0.15, 0.12), 0.99)
        world_entry = TextRegion("设置", NormalizedBox(0.90, 0.88, 0.99, 0.98), 0.99)
        self.assertTrue(settings_page_visible((title,)))
        self.assertFalse(settings_page_visible((world_entry,)))

    def test_feature_pages_survive_missing_decorative_title_ocr(self) -> None:
        welfare_body = (
            TextRegion("在线奖励", NormalizedBox(0.01, 0.18, 0.14, 0.25), 0.95),
            TextRegion("每日签到", NormalizedBox(0.18, 0.17, 0.36, 0.27), 0.95),
            TextRegion("礼包码兑换", NormalizedBox(0.01, 0.35, 0.15, 0.44), 0.95),
        )
        settings_body = (
            TextRegion("切换角色", NormalizedBox(0.73, 0.16, 0.82, 0.28), 0.95),
            TextRegion("返回登录", NormalizedBox(0.84, 0.16, 0.94, 0.28), 0.95),
            TextRegion("音频设置", NormalizedBox(0.03, 0.36, 0.16, 0.44), 0.95),
        )
        self.assertTrue(welfare_page_visible(welfare_body))
        self.assertTrue(settings_page_visible(settings_body))

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


class TaskGenerationTests(unittest.TestCase):
    """F08: quest identity changes must advance the single task generation."""

    def setUp(self) -> None:
        self.state = GameSessionState()

    def test_progress_change_keeps_the_task_generation(self) -> None:
        self.state.observe_snapshot(
            quest_frame(1, 0, "通关兰若妖寺0/1"), 0
        )
        # The first adoption is itself an identity change (no task → task A).
        self.assertEqual(self.state.task_generation, 2)
        self.state.observe_snapshot(
            quest_frame(2, 500_000_000, "通关兰若妖寺1/1"), 500_000_000
        )
        # 0/1 → 1/1 is progress on the same task: the generation holds.
        self.assertEqual(self.state.task_generation, 2)

    def test_level_target_change_advances_the_task_generation(self) -> None:
        self.state.observe_snapshot(
            quest_frame(1, 0, "拥有1只灵宠达到10级"), 0
        )
        first_generation = self.state.task_generation

        # The same fuzzy-similar quest text with a DIFFERENT level target is
        # a new task identity: two consecutive frames advance the generation.
        self.state.observe_snapshot(
            quest_frame(2, 500_000_000, "拥有1只灵宠达到20级"), 500_000_000
        )
        self.assertEqual(self.state.task_generation, first_generation)
        self.state.observe_snapshot(
            quest_frame(3, 1_000_000_000, "拥有1只灵宠达到20级"), 1_000_000_000
        )
        self.assertEqual(self.state.task_generation, first_generation + 1)

    def test_object_change_advances_the_task_generation(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "击杀罢工的幻狐"), 0)
        first_generation = self.state.task_generation

        self.state.observe_snapshot(quest_frame(2, 500_000_000, "击杀罢工的牛魔"), 500_000_000)
        self.state.observe_snapshot(quest_frame(3, 1_000_000_000, "击杀罢工的牛魔"), 1_000_000_000)

        self.assertEqual(self.state.task_generation, first_generation + 1)

    def test_ocr_jitter_does_not_advance_the_task_generation(self) -> None:
        self.state.observe_snapshot(quest_frame(1, 0, "终于来到桃夭"), 0)
        first_generation = self.state.task_generation
        self.state.observe_snapshot(quest_frame(2, 500_000_000, "终于来到桃天"), 500_000_000)

        self.assertEqual(self.state.task_generation, first_generation)


class SessionPersistenceTests(unittest.TestCase):
    """F15: scoped, atomic, honest session-state persistence."""

    def _persisted_state(self, tmp: str, *, profile_id: str | None = None):
        from pathlib import Path

        state = GameSessionState()
        state.set_persistence(Path(tmp) / "session_state.json", profile_id=profile_id)
        return state

    def test_quest_memory_survives_a_restart_via_persistence(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            first = self._persisted_state(tmp)
            first.observe_snapshot(quest_frame(1, 0, "拥有1只灵宠达到10级0/1"), 0)

            restored = self._persisted_state(tmp)
            task = restored.latest_main_task
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task.raw_text, "拥有1只灵宠达到10级0/1")
            self.assertTrue(task.restored)
            self.assertTrue(restored.restored_from_disk)
            self.assertTrue(restored.restored_task_unverified)
            summary = restored.context_summary()
            assert summary is not None
            self.assertIn("未在本次运行中证实", summary)

    def test_auto_combat_once_flag_persists_and_new_character_rearms_it(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            first = self._persisted_state(tmp, profile_id="mumu-xianyu")
            first.mark_auto_combat_enabled()
            first.mark_peach_talisman_barrier_dragged()
            self.assertTrue(first.auto_combat_enabled)
            self.assertTrue(first.peach_talisman_barrier_dragged)

            restored = self._persisted_state(tmp, profile_id="mumu-xianyu")
            self.assertTrue(restored.auto_combat_enabled)
            self.assertTrue(restored.peach_talisman_barrier_dragged)
            restored.observe_snapshot(
                snapshot(
                    1,
                    100,
                    visible_text=(("创角", (0.05, 0.06, 0.15, 0.12), 0.99),),
                ),
                100,
            )
            self.assertFalse(restored.auto_combat_enabled)
            self.assertFalse(restored.peach_talisman_barrier_dragged)

            reloaded = self._persisted_state(tmp, profile_id="mumu-xianyu")
            self.assertFalse(reloaded.auto_combat_enabled)
            self.assertFalse(reloaded.peach_talisman_barrier_dragged)

    def test_restored_task_verifies_after_two_fresh_frames(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            first = self._persisted_state(tmp)
            first.observe_snapshot(quest_frame(1, 0, "通关兰若妖寺0/1"), 0)

            restored = self._persisted_state(tmp)
            self.assertTrue(restored.restored_task_unverified)
            restored.observe_snapshot(quest_frame(2, 100, "通关兰若妖寺0/1"), 100)
            self.assertTrue(restored.restored_task_unverified)
            restored.observe_snapshot(quest_frame(3, 200, "通关兰若妖寺0/1"), 200)
            self.assertFalse(restored.restored_task_unverified)

    def test_profile_namespace_isolation(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            mumu = self._persisted_state(tmp, profile_id="mumu-xianyu")
            mumu.observe_snapshot(quest_frame(1, 0, "击杀罢工的幻狐"), 0)

            other = self._persisted_state(tmp, profile_id="other-game")
            self.assertIsNone(other.latest_main_task)
            self.assertIn("belongs to profile", other.last_persistence_error or "")

            same_profile = self._persisted_state(tmp, profile_id="mumu-xianyu")
            self.assertIsNotNone(same_profile.latest_main_task)

    def test_corrupt_state_is_quarantined_and_session_starts_empty(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_state.json"
            path.write_text("{not json at all", encoding="utf-8")

            state = self._persisted_state(tmp)
            self.assertIsNone(state.latest_main_task)
            self.assertIsNotNone(state.last_persistence_error)
            self.assertIn("corrupt state file", state.last_persistence_error or "")
            quarantined = list(Path(tmp).glob("session_state.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            # The quarantined payload is preserved as diagnostic evidence.
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "{not json at all")
            self.assertFalse(path.exists())
            del json

    def test_oversized_state_is_quarantined(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_state.json"
            path.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

            state = self._persisted_state(tmp)
            self.assertIsNone(state.latest_main_task)
            self.assertIn("size cap", state.last_persistence_error or "")
            self.assertEqual(len(list(Path(tmp).glob("*.corrupt-*"))), 1)

    def test_atomic_write_leaves_no_torn_state(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = self._persisted_state(tmp)
            state.observe_snapshot(quest_frame(1, 0, "击杀罢工的幻狐"), 0)

            path = Path(tmp) / "session_state.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "uga.session-state/1")
            self.assertEqual(payload["profile_id"], None)
            # The atomic replace consumed the temp file: no torn copies.
            self.assertEqual(list(Path(tmp).glob("*.tmp-*")), [])

    def test_failed_save_is_observable_not_silent(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = self._persisted_state(tmp)
            # A FILE where the state directory should be: the mkdir fails.
            blocked = Path(tmp) / "blocked"
            blocked.write_text("", encoding="utf-8")
            state.persistence_path = blocked / "state.json"
            state.observe_snapshot(quest_frame(1, 0, "击杀罢工的幻狐"), 0)

            self.assertIsNotNone(state.last_persistence_error)
            self.assertIn("save failed", state.last_persistence_error or "")

    def test_stale_outcome_after_task_bump_is_discarded(self) -> None:
        # F08 end-to-end at the supervisor boundary: an outcome stamped with
        # the pre-bump task generation is discarded by the consistency check
        # once the quest identity change advanced the generation.
        from dataclasses import replace as dc_replace

        from uga.agent.closed_loop import ActionValidator
        from uga.environment.profile import PerceptionProfile
        from uga.time.clock import ManualClock

        clock = ManualClock(0)
        state = GameSessionState()
        # Adopt quest A (two frames) → the first adoption bumps to 2.
        state.observe_snapshot(quest_frame(1, 0, "击杀罢工的幻狐"), 0)
        state.observe_snapshot(quest_frame(2, 100, "击杀罢工的幻狐"), 100)
        generation_after_a = state.task_generation
        # Quest identity change: quest B confirmed on two fresh frames.
        state.observe_snapshot(quest_frame(3, 200, "击杀罢工的牛魔"), 200)
        state.observe_snapshot(quest_frame(4, 300, "击杀罢工的牛魔"), 300)
        self.assertEqual(state.task_generation, generation_after_a + 1)

        decided = dc_replace(snapshot(5, 400), task_generation=generation_after_a)
        fresh = dc_replace(snapshot(6, 500), task_generation=state.task_generation)
        stale_outcome = dc_replace(
            supervisor_outcome(1),
            request_frame_id="frame-5",
            request_frame_sequence=5,
            task_generation=generation_after_a,
        )
        validator = ActionValidator(
            PerceptionProfile(action_effect_timeout_ms=1000)
        )
        valid, reason = validator.validate(
            stale_outcome, decided, fresh, frame(5, 400), frame(6, 500), "open settings"
        )
        del clock
        self.assertFalse(valid)
        self.assertIn("stale", reason)


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


class RecordedCultivationPathTests(unittest.TestCase):
    @staticmethod
    def _region(
        text: str,
        box: tuple[float, float, float, float],
        confidence: float = 1.0,
    ) -> TextRegion:
        return TextRegion(text, NormalizedBox(*box), confidence)

    def test_world_chat_row_precedes_topmost_generic_objective(self) -> None:
        regions = (
            self._region("修仙之路", (0.35, 0.14, 0.62, 0.28)),
            self._region("完成3次30级装备秘境", (0.40, 0.31, 0.60, 0.35)),
            self._region("前往", (0.75, 0.32, 0.82, 0.37)),
            self._region("世界频道发言1次", (0.40, 0.53, 0.60, 0.57)),
            self._region("0/1", (0.40, 0.58, 0.45, 0.61)),
            self._region("前往", (0.75, 0.54, 0.82, 0.59)),
        )

        result = xiuxian_path_recorded_objective_control(regions)

        assert result is not None
        self.assertEqual(result[0], "world_chat_goto")
        self.assertAlmostEqual(result[1].box.center.y, 0.565)

    def test_completed_recorded_objectives_select_claim(self) -> None:
        world = xiuxian_path_recorded_objective_control(
            (
                self._region("修仙之路", (0.35, 0.14, 0.62, 0.28)),
                self._region("世界频道发言1次", (0.40, 0.30, 0.60, 0.34)),
                self._region("1/1", (0.40, 0.35, 0.45, 0.38)),
                self._region("领取", (0.75, 0.31, 0.82, 0.36)),
            )
        )
        equipment = xiuxian_path_recorded_objective_control(
            (
                self._region("修仙之路", (0.35, 0.14, 0.62, 0.28)),
                self._region("完成3次30级装备秘境", (0.40, 0.30, 0.60, 0.34)),
                self._region("3/3", (0.40, 0.35, 0.45, 0.38)),
                self._region("领取", (0.75, 0.31, 0.82, 0.36)),
            )
        )

        self.assertEqual(world and world[0], "world_chat_claim")
        self.assertEqual(equipment and equipment[0], "equipment_dungeon_claim")

    def test_world_chat_send_and_collapse_require_recorded_text(self) -> None:
        send_regions = (
            self._region("世界", (0.01, 0.10, 0.09, 0.20)),
            self._region("仙遇有你，一路同行", (0.15, 0.90, 0.45, 0.97)),
            self._region("发送", (0.40, 0.90, 0.51, 0.97)),
        )
        sent_regions = (
            self._region("世界", (0.01, 0.10, 0.09, 0.20)),
            self._region("想你的风还是吹到了仙遇", (0.40, 0.40, 0.70, 0.46)),
            self._region("9秒", (0.40, 0.90, 0.51, 0.97)),
        )

        self.assertEqual(world_chat_send_control(send_regions).text, "发送")  # type: ignore[union-attr]
        self.assertTrue(world_chat_sent_visible(sent_regions))

    def test_equipment_dungeon_controls_and_wait_states(self) -> None:
        npc = equipment_dungeon_control(
            (
                self._region("秘境使者", (0.10, 0.75, 0.28, 0.83)),
                self._region("装备秘境", (0.68, 0.69, 0.92, 0.79)),
            )
        )
        group = equipment_dungeon_control(
            (
                self._region("装备秘境", (0.02, 0.04, 0.20, 0.12)),
                self._region("盘丝妖窟", (0.18, 0.25, 0.35, 0.35)),
                self._region("组队", (0.65, 0.86, 0.80, 0.95)),
            )
        )
        recruiting = (
            self._region("我的队伍", (0.02, 0.04, 0.20, 0.12)),
            self._region("装备秘境-盘丝妖窟", (0.02, 0.15, 0.30, 0.22)),
            self._region("招募中", (0.70, 0.86, 0.82, 0.95)),
            self._region("前往副本", (0.84, 0.86, 0.98, 0.95)),
        )
        battle = (
            self._region("盘丝妖窟", (0.05, 0.15, 0.20, 0.22)),
            self._region("击杀夜叉兽0/8", (0.04, 0.30, 0.22, 0.36)),
        )

        self.assertEqual(npc and npc[0], "npc_choice")
        self.assertEqual(group and group[0], "group")
        self.assertIsNone(equipment_dungeon_control(recruiting))
        self.assertTrue(equipment_recruiting_visible(recruiting))
        self.assertTrue(equipment_dungeon_active(battle))

    def test_blessing_ad_and_terminal_anchors_are_narrow(self) -> None:
        select = blessing_onboarding_control(
            (
                self._region("选择一个灵佑", (0.35, 0.10, 0.65, 0.20)),
                self._region("呦呦", (0.24, 0.30, 0.37, 0.38)),
                self._region("啾啾", (0.65, 0.30, 0.78, 0.38)),
            )
        )
        feed = (
            self._region("喂养", (0.03, 0.05, 0.15, 0.12)),
            self._region("呦呦", (0.45, 0.08, 0.56, 0.14)),
            self._region("剩余孵化时间：46小时", (0.38, 0.68, 0.64, 0.74)),
        )
        ad = (
            self._region("倩女幽魂", (0.20, 0.18, 0.50, 0.75)),
            self._region("立即前往", (0.42, 0.75, 0.60, 0.85)),
        )
        final = (
            self._region("洛神大街", (0.08, 0.30, 0.20, 0.36)),
            self._region("修仙之路", (0.04, 0.20, 0.14, 0.24)),
            self._region("点击前往领取奖励", (0.04, 0.24, 0.30, 0.28)),
        )

        self.assertEqual(select and select[0], "select_yoyo")
        self.assertTrue(blessing_feed_page_visible(feed))
        self.assertTrue(romance_ad_visible(ad))
        self.assertTrue(basic_onboarding_complete(final))
        self.assertTrue(
            equipment_reward_popup_visible(
                (
                    self._region("仙路漫漫", (0.05, 0.30, 0.20, 0.36)),
                    self._region("30级", (0.60, 0.50, 0.68, 0.56)),
                    self._region("使用", (0.60, 0.62, 0.70, 0.68)),
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
