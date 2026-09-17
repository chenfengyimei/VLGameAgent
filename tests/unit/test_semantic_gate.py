from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_grounded_vlm import _Client
from uga.agent.closed_loop import ActionValidator, ClosedLoopSupervisor, DecisionDisposition
from uga.agent.strategies import StrategyRegistry
from uga.environment.profile import PerceptionProfile
from uga.perception.schema import ActionRisk, DecisionKind
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.safety.semantic_gate import sensitive_action_reason, sensitive_page_reason
from uga.time.clock import ManualClock


class SensitiveGateTests(unittest.TestCase):
    def test_sensitive_page_overrides_model_risk_and_reserved_rule_labels(self) -> None:
        for cue in ("Confirm payment", "Privacy policy", "Password", "Confirm deletion"):
            for label in ("Confirm", "ui_back", "ui_close", "ui_promote"):
                with self.subTest(cue=cue, label=label):
                    sup = ClosedLoopSupervisor(ManualClock(100), PerceptionProfile())
                    snap = snapshot(1, 100, visible_text=(cue, "Confirm"))
                    result = sup.assess(
                        outcome(1, label=label),
                        snap,
                        snap,
                        frame(1),
                        frame(1),
                        "do everything",
                        decision_source="ocr_test",
                    )
                    self.assertEqual(result.disposition, DecisionDisposition.WAIT)
                    self.assertEqual(sup.recovery_count, 0)

    def test_safety_gate_applies_to_generic_profiles_without_game_strategy(self) -> None:
        planner = GroundedVlmPlanner(_Client([]), strategy_registry=StrategyRegistry("generic", ()))
        snap = snapshot(1, 100, visible_text=("Terms of service", "Start game"))
        result = planner.decide(snapshot=snap, frames=(frame(1),), goal="play")
        self.assertEqual(result.kind, DecisionKind.WAIT)
        self.assertEqual(planner.last_decision_source, "sensitive_page_handoff")
        self.assertFalse(hasattr(planner, "_login_agreement_clicked"))

    def test_high_confidence_matching_goal_is_not_authorization(self) -> None:
        item = outcome(1, label="delete account", risk=ActionRisk.CRITICAL)
        snap = snapshot(1, 100, visible_text=("Confirm",))
        ok, reason = ActionValidator(PerceptionProfile()).validate(
            item, snap, snap, frame(1), frame(1), "delete account", secondary_verified=True
        )
        self.assertFalse(ok)
        self.assertIn("handoff", reason)

    def test_normal_reward_and_ascii_word_boundaries(self) -> None:
        self.assertIsNone(
            sensitive_page_reason(snapshot(1, 100, visible_text=("Claim reward",)).visible_text)
        )
        self.assertIsNone(sensitive_action_reason(outcome(1, label="display").action))
        region = snapshot(1, 100, visible_text=("Settings",)).visible_text[0]
        self.assertIsNone(
            sensitive_page_reason((replace(region, text="Terms of service", confidence=0.1),))
        )
