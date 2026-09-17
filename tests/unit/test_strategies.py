"""D12 regression tests: game-strategy isolation.

A game profile's rule inventory is explicit data scoped to its game id; a
generic profile resolves to an EMPTY registry and none of the commercial-game
fast paths fire.  The inventory grants nothing by itself: every rule-proposed
action still passes the unified action safety gate (D03).
"""

from __future__ import annotations

import unittest

from tests.helpers import identity
from tests.unit.test_grounded_vlm import _Client
from uga.agent.strategies import (
    StrategyRegistry,
    registry_for,
)
from uga.agent.strategies.mumu_xianyu import REGISTRY as MUMU_REGISTRY
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.perception.schema import (
    DecisionKind,
    NormalizedBox,
    PerceptionSnapshot,
    TextRegion,
)
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.time.clock import UGATime


class StrategyRegistryTests(unittest.TestCase):
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
            "ocr_mumu_dialog_cancel_fast",
            "ocr_dialogue_click_fast",
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
