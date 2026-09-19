"""D12 regression tests: game-strategy isolation.

A game profile's rule inventory is explicit data scoped to its game id; a
generic profile resolves to an EMPTY registry and none of the commercial-game
fast paths fire.  The inventory grants nothing by itself: every rule-proposed
action still passes the unified action safety gate (D03).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import identity
from tests.unit.test_grounded_vlm import _Client
from uga.agent.strategies import (
    StrategyRegistry,
    registry_for,
)
from uga.agent.strategies.flow_manifest import load_recorded_flow
from uga.agent.strategies.mumu_xianyu import REGISTRY as MUMU_REGISTRY
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.environment.profile import load_game_profile
from uga.gui.schema import GuiActionKind
from uga.perception.schema import (
    DecisionKind,
    NormalizedBox,
    PerceptionSnapshot,
    TextRegion,
)
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.time.clock import UGATime


class StrategyRegistryTests(unittest.TestCase):
    def test_recorded_flow_covers_all_evidence_and_registered_rules(self) -> None:
        root = Path(__file__).parents[2]
        flow = load_recorded_flow(root / "configs/flows/mumu-xianyu-onboarding.yaml")
        self.assertEqual(flow.game_id, MUMU_REGISTRY.game_id)
        self.assertEqual(len(flow.steps), 31)
        self.assertEqual(
            {Path(step.evidence).name for step in flow.steps},
            {f"{index:02d}-{name}" for index, name in enumerate((
                "character-create.png",
                "preset-selection.png",
                "name-confirmation.png",
                "joystick-forward-tutorial.png",
                "invasion-group-attack.png",
                "auto-navigation.png",
                "evil-spirit-group-attack.png",
                "refreshed-main-quest.png",
                "cutscene-skip.png",
                "senior-sister-task.png",
                "senior-sister-dialogue.png",
                "master-task.png",
                "master-dialogue.png",
                "spirit-task.png",
                "spirit-dialogue.png",
                "cutscene-skip.png",
                "little-dragon-task.png",
                "little-dragon-rescue-choice.png",
                "little-dragon-heal.png",
                "little-dragon-dialogue-task.png",
                "little-dragon-dialogue.png",
                "contract-continue.png",
                "little-dragon-bond-quest.png",
                "demonized-spirit-pet-group-attack.png",
                "demonized-spirit-dialogue.png",
                "red-dust-auto-once.png",
                "red-dust-cutscene-skip.png",
                "rescue-cry-quest.png",
                "rescue-cutscene-skip.png",
                "rescue-dialogue.png",
                "peach-tree-spirit-combat.png",
            ), start=1)},
        )
        for step in flow.steps:
            with self.subTest(step=step.step_id):
                self.assertTrue((root / step.evidence).is_file())
                self.assertTrue(MUMU_REGISTRY.allows(step.source))

    def test_recorded_hotspots_match_the_confirmed_game_profile(self) -> None:
        root = Path(__file__).parents[2]
        profile = load_game_profile(root / "configs/games/mumu-xianyu.yaml")
        flow = load_recorded_flow(root / "configs/flows/mumu-xianyu-onboarding.yaml")
        hotspot_actions = {
            step.action.removeprefix("click_hotspot:")
            for step in flow.steps
            if step.action.startswith("click_hotspot:")
        }
        for action in hotspot_actions:
            with self.subTest(action=action):
                binding = profile.binding(action)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertTrue(binding.confirmed)
                self.assertIsNotNone(binding.hotspot)
        dragon = profile.binding("ui_little_dragon_heal")
        assert dragon is not None and dragon.hotspot is not None
        self.assertAlmostEqual(dragon.hotspot[0], 0.568, places=3)
        self.assertAlmostEqual(dragon.hotspot[1], 0.588, places=3)
        auto = profile.binding("ui_auto_combat")
        assert auto is not None and auto.hotspot is not None
        self.assertAlmostEqual(auto.hotspot[0], 0.585, places=3)
        self.assertAlmostEqual(auto.hotspot[1], 0.803, places=3)

    def test_known_game_resolves_to_a_populated_registry(self) -> None:
        registry = registry_for("mumu-xianyu")
        assert registry is not None
        self.assertEqual(registry.game_id, "mumu-xianyu")
        self.assertTrue(registry.allows_fast_paths())
        self.assertGreaterEqual(len(registry.rules), 10)
        for rule in registry.rules:
            self.assertTrue(rule.name)
            self.assertTrue(rule.source)
            self.assertEqual(rule.game_id, "mumu-xianyu")
            self.assertTrue(rule.summary)
            self.assertTrue(rule.allowed_action)
            self.assertTrue(rule.risk)
            self.assertTrue(rule.effect)

    def test_generic_profile_resolves_to_an_empty_registry(self) -> None:
        registry = registry_for("some-other-game")
        assert registry is not None
        self.assertEqual(registry.game_id, "some-other-game")
        self.assertEqual(registry.rules, ())
        self.assertFalse(registry.allows_fast_paths())

    def test_sources_are_unique_within_a_registry(self) -> None:
        with self.assertRaises(ContractViolation):
            StrategyRegistry(
                game_id="mumu-xianyu",
                rules=(
                    MUMU_REGISTRY.rules[0],
                    MUMU_REGISTRY.rules[0],
                ),
            )

    def test_rules_cannot_belong_to_a_foreign_game(self) -> None:
        with self.assertRaises(ContractViolation):
            StrategyRegistry(
                game_id="other-game",
                rules=MUMU_REGISTRY.rules[:1],
            )

    def test_allow_lookup_is_source_exact(self) -> None:
        self.assertTrue(MUMU_REGISTRY.allows("ocr_mumu_dialog_cancel_fast"))
        self.assertFalse(MUMU_REGISTRY.allows("ocr_not_a_real_source"))
        self.assertIsNone(MUMU_REGISTRY.rule_for("ocr_not_a_real_source"))

    def test_every_planner_fast_path_source_is_inventoried(self) -> None:
        # The contract: a rule emitted by the planner must be declared in the
        # game's inventory, otherwise the planner would propose actions no
        # registered rule explains.
        known_sources = {rule.source for rule in MUMU_REGISTRY.rules}
        self.assertNotIn("ocr_login_agreement_fast", known_sources)
        for source in (
            "ocr_auto_navigation_wait",
            "ocr_invasion_task_navigate_fast",
            "ocr_invasion_group_attack_fast",
            "ocr_demonized_spirit_group_attack_fast",
            "ocr_peach_tree_spirit_group_attack_fast",
            "ocr_red_dust_auto_once_fast",
            "ocr_onboarding_joystick_forward_fast",
            "ocr_character_creation_customize_fast",
            "ocr_character_preset_start_fast",
            "ocr_character_name_confirm_fast",
            "ocr_mumu_dialog_cancel_fast",
            "ocr_dialogue_click_fast",
            "ocr_cutscene_skip_fast",
            "ocr_rescue_little_dragon_choice_fast",
            "ocr_little_dragon_heal_fast",
            "ocr_narrative_continue_fast",
            "ocr_close_glyph_fast",
            "ocr_task_panel_fast",
            "ocr_progress_control_fast",
            "ocr_quest_satisfied_back_fast",
            "ocr_completed_panel_back_fast",
            "ocr_quest_irrelevant_back_fast",
            "ocr_xiuxian_path_jump_fast",
            "ocr_xiuxian_objective_goto_fast",
            "ocr_stall_item_fast",
            "ocr_realm_promote_fast",
            "ocr_market_entry_fast",
        ):
            self.assertIn(source, known_sources)


def _mumu_dialog_snapshot() -> PerceptionSnapshot:
    """A frame carrying MuMu's own close-confirmation dialog markers."""
    return PerceptionSnapshot(
        "snapshot-1",
        "frame-1",
        1,
        UGATime(0),
        identity(),
        1,
        1,
        ControlMode.GUI,
        (
            TextRegion(
                "确定要关闭 MuMu安卓设备-1",
                NormalizedBox(0.3, 0.2, 0.7, 0.3),
                0.99,
            ),
            TextRegion("取消", NormalizedBox(0.55, 0.32, 0.68, 0.4), 0.95),
        ),
        (),
        (),
        "signature-1",
        0.95,
    )


def _onboarding_snapshot(*regions: TextRegion) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        "onboarding-snapshot",
        "onboarding-frame",
        1,
        UGATime(0),
        identity(),
        1,
        1,
        ControlMode.GUI,
        regions,
        (),
        (),
        "onboarding-signature",
        0.95,
    )


class PlannerStrategyScopingTests(unittest.TestCase):
    def test_generic_registry_disables_game_fast_paths(self) -> None:
        # test_generic_profile_never_runs_mumu_rules: an empty registry means
        # the MuMu close-dialog rule never fires; the decision falls to the
        # vision model instead.
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=StrategyRegistry(game_id="other-game", rules=()),
        )
        self.assertFalse(planner.strategy_allows_fast_paths)
        outcome = planner._ocr_fast_path(_mumu_dialog_snapshot())  # type: ignore[attr-defined]
        self.assertIsNone(outcome)

    def test_registered_game_still_fires_its_rule(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        self.assertTrue(planner.strategy_allows_fast_paths)
        outcome = planner._ocr_fast_path(_mumu_dialog_snapshot())  # type: ignore[attr-defined]
        assert outcome is not None
        self.assertEqual(outcome.kind, DecisionKind.ACT)
        assert outcome.action is not None
        self.assertEqual(outcome.action.target_label, "取消")
        self.assertEqual(
            planner._last_decision_source,  # type: ignore[attr-defined]
            "ocr_mumu_dialog_cancel_fast",
        )

    def test_mumu_close_dialog_wins_over_visible_game_tutorial_text(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion(
                "确定要关闭 MuMu安卓设备-1",
                NormalizedBox(0.30, 0.20, 0.70, 0.30),
                0.99,
            ),
            TextRegion("取消", NormalizedBox(0.55, 0.32, 0.68, 0.40), 0.99),
            TextRegion("滑动摇杆可以移动", NormalizedBox(0.08, 0.48, 0.30, 0.56), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.kind, GuiActionKind.CLICK)
        self.assertEqual(outcome.action.target_label, "取消")
        self.assertEqual(planner.last_decision_source, "ocr_mumu_dialog_cancel_fast")

    def test_recorded_character_creation_screens_use_exact_ocr_controls(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        cases = (
            (
                "customize",
                _onboarding_snapshot(
                    TextRegion("创角", NormalizedBox(0.06, 0.06, 0.16, 0.12), 0.99),
                    TextRegion("定制细节", NormalizedBox(0.78, 0.84, 0.96, 0.94), 0.99),
                ),
                "定制细节",
                "ocr_character_creation_customize_fast",
            ),
            (
                "preset",
                _onboarding_snapshot(
                    TextRegion("选择预设", NormalizedBox(0.06, 0.06, 0.18, 0.12), 0.99),
                    TextRegion("开启仙途", NormalizedBox(0.74, 0.84, 0.90, 0.94), 0.99),
                ),
                "开启仙途",
                "ocr_character_preset_start_fast",
            ),
            (
                "name",
                _onboarding_snapshot(
                    TextRegion("请输入名字", NormalizedBox(0.43, 0.30, 0.58, 0.36), 0.99),
                    TextRegion("尹禧燕", NormalizedBox(0.42, 0.46, 0.56, 0.52), 0.99),
                    TextRegion("3/7", NormalizedBox(0.58, 0.46, 0.62, 0.52), 0.99),
                    TextRegion("确定", NormalizedBox(0.52, 0.66, 0.66, 0.76), 0.99),
                ),
                "确定",
                "ocr_character_name_confirm_fast",
            ),
        )
        for name, snapshot, label, source in cases:
            with self.subTest(name=name):
                outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
                assert outcome is not None and outcome.action is not None
                self.assertEqual(outcome.kind, DecisionKind.ACT)
                self.assertEqual(outcome.action.target_label, label)
                self.assertEqual(planner.last_decision_source, source)

    def test_empty_character_name_waits_instead_of_clicking_confirm(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("请输入名字", NormalizedBox(0.43, 0.30, 0.58, 0.36), 0.99),
            TextRegion("0/7", NormalizedBox(0.58, 0.46, 0.62, 0.52), 0.99),
            TextRegion("确定", NormalizedBox(0.52, 0.66, 0.66, 0.76), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None
        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertIsNone(outcome.action)
        self.assertEqual(planner.last_decision_source, "character_name_entry_wait")

    def test_recorded_joystick_tutorial_drags_upward_once(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("滑动摇杆可以移动", NormalizedBox(0.08, 0.48, 0.30, 0.56), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.kind, GuiActionKind.DRAG)
        self.assertEqual(outcome.action.target_label, "left movement joystick")
        self.assertEqual(outcome.action.pointer_offset_y, -0.12)
        self.assertEqual(planner.last_decision_source, "ocr_onboarding_joystick_forward_fast")

    def test_recorded_invasion_task_navigates_before_enemies_appear(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("主线", NormalizedBox(0.04, 0.16, 0.11, 0.21), 0.99),
            TextRegion("入侵袭击", NormalizedBox(0.05, 0.22, 0.18, 0.27), 0.99),
            TextRegion("击败这些不速之客 0/3", NormalizedBox(0.05, 0.28, 0.27, 0.33), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "入侵袭击")
        self.assertEqual(planner.last_decision_source, "ocr_invasion_task_navigate_fast")

    def test_recorded_invasion_encounter_uses_group_attack_while_enemy_visible(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("入侵袭击", NormalizedBox(0.05, 0.22, 0.18, 0.27), 0.99),
            TextRegion("恶灵", NormalizedBox(0.46, 0.22, 0.55, 0.27), 0.99),
            TextRegion("群攻", NormalizedBox(0.72, 0.88, 0.79, 0.93), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "群攻")
        self.assertEqual(outcome.action.pointer_offset_y, -0.06)
        self.assertEqual(planner.last_decision_source, "ocr_invasion_group_attack_fast")

    def test_recorded_invasion_uses_hotspot_when_skill_caption_is_not_read(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            group_attack_hotspot=(0.770, 0.890),
        )
        snapshot = _onboarding_snapshot(
            TextRegion(
                "击败这些不速客 0/3",
                NormalizedBox(0.04, 0.27, 0.20, 0.32),
                0.99,
            ),
            TextRegion("黑衣人", NormalizedBox(0.32, 0.53, 0.40, 0.60), 0.99),
        )

        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]

        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_group_attack")
        self.assertAlmostEqual(outcome.action.target_box.center.x, 0.770, places=3)
        self.assertAlmostEqual(outcome.action.target_box.center.y, 0.890, places=3)
        self.assertEqual(planner.last_decision_source, "ocr_invasion_group_attack_fast")

    def test_demonized_spirit_fight_uses_the_blue_pet_group_attack(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            pet_group_attack_hotspot=(0.950, 0.500),
        )
        snapshot = _onboarding_snapshot(
            TextRegion("魔化精怪", NormalizedBox(0.04, 0.22, 0.18, 0.27), 0.99),
            TextRegion("制服魔化妖灵 0/4", NormalizedBox(0.04, 0.27, 0.23, 0.32), 0.99),
            TextRegion("魔化猪猪", NormalizedBox(0.42, 0.38, 0.53, 0.44), 0.99),
        )

        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]

        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_pet_group_attack")
        self.assertAlmostEqual(outcome.action.target_box.center.x, 0.950, places=3)
        self.assertAlmostEqual(outcome.action.target_box.center.y, 0.500, places=3)
        self.assertEqual(
            planner.last_decision_source, "ocr_demonized_spirit_group_attack_fast"
        )

    def test_peach_tree_fight_rotates_all_three_recorded_group_attacks(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            pet_group_attack_hotspot=(0.950, 0.500),
            secondary_group_attack_hotspot=(0.790, 0.745),
            group_attack_hotspot=(0.770, 0.890),
        )
        snapshot = _onboarding_snapshot(
            TextRegion("暴虐精怪", NormalizedBox(0.04, 0.22, 0.18, 0.27), 0.99),
            TextRegion("制服桃木精 0/3", NormalizedBox(0.04, 0.27, 0.22, 0.32), 0.99),
            TextRegion("桃木精", NormalizedBox(0.42, 0.30, 0.50, 0.36), 0.99),
        )

        labels = []
        for _ in range(3):
            outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
            assert outcome is not None and outcome.action is not None
            labels.append(outcome.action.target_label)
        self.assertEqual(
            labels,
            ["ui_pet_group_attack", "ui_secondary_group_attack", "ui_group_attack"],
        )

    def test_red_dust_auto_is_suppressed_after_the_persisted_once_flag(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            auto_combat_hotspot=(0.585, 0.803),
        )
        snapshot = _onboarding_snapshot(
            TextRegion("主线", NormalizedBox(0.04, 0.16, 0.11, 0.21), 0.99),
            TextRegion("红尘入世", NormalizedBox(0.04, 0.22, 0.18, 0.27), 0.99),
            TextRegion("与师姐一起下山", NormalizedBox(0.04, 0.27, 0.24, 0.32), 0.99),
        )

        outcome = planner._ocr_fast_path(  # type: ignore[attr-defined]
            snapshot, session_context="auto_combat_enabled=false"
        )
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_auto_combat")
        self.assertEqual(planner.last_decision_source, "ocr_red_dust_auto_once_fast")

        suppressed = planner._ocr_fast_path(  # type: ignore[attr-defined]
            snapshot, session_context="auto_combat_enabled=true"
        )
        # The generic task rule may navigate, but the one-shot Auto rule must
        # never emit after the persisted flag is true.
        if suppressed is not None and suppressed.action is not None:
            self.assertNotEqual(suppressed.action.target_label, "ui_auto_combat")

    def test_auto_navigation_waits_without_reclicking_the_task_tracker(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("主线", NormalizedBox(0.04, 0.16, 0.11, 0.21), 0.99),
            TextRegion("入侵袭击", NormalizedBox(0.05, 0.22, 0.18, 0.27), 0.99),
            TextRegion("自动寻路中", NormalizedBox(0.42, 0.66, 0.58, 0.71), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None
        self.assertEqual(outcome.kind, DecisionKind.WAIT)
        self.assertIsNone(outcome.action)
        self.assertEqual(planner.last_decision_source, "ocr_auto_navigation_wait")

    def test_recorded_cutscene_skips_only_the_upper_right_control(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("跳过", NormalizedBox(0.93, 0.075, 0.98, 0.12), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "跳过")
        self.assertEqual(planner.last_decision_source, "ocr_cutscene_skip_fast")

    def test_review_story_anchor_advances_at_the_calibrated_lower_right(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            dialogue_hotspot=(0.96, 0.915),
        )
        snapshot = _onboarding_snapshot(
            TextRegion("回顾剧情", NormalizedBox(0.02, 0.43, 0.05, 0.60), 0.99),
            TextRegion("师姐", NormalizedBox(0.11, 0.80, 0.16, 0.84), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_dialogue_advance")
        self.assertAlmostEqual(outcome.action.target_box.center.x, 0.96, places=3)
        self.assertAlmostEqual(outcome.action.target_box.center.y, 0.915, places=3)
        self.assertEqual(planner.last_decision_source, "ocr_dialogue_click_fast")

    def test_dialogue_main_quest_uses_the_tracker_after_navigation_ends(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("主线", NormalizedBox(0.04, 0.16, 0.11, 0.21), 0.99),
            TextRegion("师姐相救", NormalizedBox(0.05, 0.22, 0.18, 0.27), 0.99),
            TextRegion("与师姐对话", NormalizedBox(0.05, 0.28, 0.20, 0.33), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "与师姐对话")
        self.assertEqual(planner.last_decision_source, "ocr_task_panel_fast")

    def test_little_dragon_rescue_choice_opens_the_healing_interaction(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("拯救小龙", NormalizedBox(0.69, 0.68, 0.90, 0.80), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "拯救小龙")
        self.assertEqual(planner.last_decision_source, "ocr_rescue_little_dragon_choice_fast")

    def test_little_dragon_heal_requires_both_visual_anchors(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
            little_dragon_heal_hotspot=(0.568, 0.588),
        )
        snapshot = _onboarding_snapshot(
            TextRegion("拯救重伤的小青龙", NormalizedBox(0.38, 0.92, 0.62, 0.98), 0.99),
            TextRegion("传功疗伤", NormalizedBox(0.77, 0.76, 0.87, 0.82), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "ui_little_dragon_heal")
        self.assertAlmostEqual(outcome.action.target_box.center.x, 0.568, places=3)
        self.assertAlmostEqual(outcome.action.target_box.center.y, 0.588, places=3)
        self.assertEqual(planner.last_decision_source, "ocr_little_dragon_heal_fast")

        missing_anchor = _onboarding_snapshot(
            TextRegion("拯救重伤的小青龙", NormalizedBox(0.38, 0.92, 0.62, 0.98), 0.99),
        )
        self.assertIsNone(planner._ocr_fast_path(missing_anchor))  # type: ignore[attr-defined]

    def test_contract_story_continue_uses_its_lower_screen_control(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("点击任意处继续", NormalizedBox(0.40, 0.84, 0.61, 0.91), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "点击任意处继续")
        self.assertEqual(planner.last_decision_source, "ocr_narrative_continue_fast")

    def test_refreshed_main_quest_uses_the_tracker_after_combat(self) -> None:
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            compact_output=True,
            prefer_ocr_task_panel=True,
            strategy_registry=MUMU_REGISTRY,
        )
        snapshot = _onboarding_snapshot(
            TextRegion("主线", NormalizedBox(0.04, 0.16, 0.11, 0.21), 0.99),
            TextRegion("危机四伏", NormalizedBox(0.05, 0.22, 0.18, 0.27), 0.99),
            TextRegion("速回桃源居", NormalizedBox(0.05, 0.28, 0.20, 0.33), 0.99),
        )
        outcome = planner._ocr_fast_path(snapshot)  # type: ignore[attr-defined]
        assert outcome is not None and outcome.action is not None
        self.assertEqual(outcome.action.target_label, "速回桃源居")
        self.assertEqual(planner.last_decision_source, "ocr_task_panel_fast")

    def test_legacy_none_registry_keeps_the_compat_default(self) -> None:
        # Documented transitional default: a planner built without a registry
        # (all pre-existing unit tests) keeps every known source available.
        planner = GroundedVlmPlanner(
            _Client([]),
            max_temporal_frames=1,
            max_target_crops=0,
            prefer_ocr_task_panel=True,
        )
        self.assertTrue(planner.strategy_allows_fast_paths)


if __name__ == "__main__":
    unittest.main()
