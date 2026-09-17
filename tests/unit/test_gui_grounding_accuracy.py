from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_grounded_vlm import _Client, _reply, _snapshot
from uga.agent.closed_loop import ActionValidator
from uga.environment.profile import PerceptionProfile
from uga.perception.schema import DecisionKind, NormalizedBox, TextRegion
from uga.policy.grounded_vlm import GroundedOutcomeVerifier, GroundedVlmPlanner


class GuiGroundingAccuracyTests(unittest.TestCase):
    def test_same_text_far_away_cannot_teleport_model_target(self) -> None:
        data = json.loads(_reply())
        planner = GroundedVlmPlanner(_Client([]))
        proposed = planner._parse(json.dumps(data), _snapshot())
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(data["action"]["target_label"], NormalizedBox(0.7, 0.7, 0.9, 0.9), 0.99),
            ),
        )
        self.assertEqual(planner._snap_model_action_to_ocr(proposed, current), proposed)

    def test_local_target_wins_over_remote_higher_confidence_duplicate(self) -> None:
        planner = GroundedVlmPlanner(_Client([]))
        proposed = planner._parse(_reply(), _snapshot())
        label = proposed.action.target_label
        local = TextRegion(label, NormalizedBox(0.22, 0.32, 0.38, 0.48), 0.91)
        distant = TextRegion(label, NormalizedBox(0.7, 0.7, 0.9, 0.9), 0.999)
        current = replace(_snapshot(), visible_text=(distant, local))
        actual = planner._snap_model_action_to_ocr(proposed, current)
        self.assertEqual(actual.action.target_box, local.box)
        self.assertEqual(actual.action.confidence, 0.91)

    def test_two_buttons_in_a_loose_model_box_abstain_instead_of_guessing(self) -> None:
        planner = GroundedVlmPlanner(_Client([]))
        proposed = planner._parse(_reply(), _snapshot())
        current = replace(
            _snapshot(),
            visible_text=(
                TextRegion(
                    proposed.action.target_label, NormalizedBox(0.21, 0.32, 0.27, 0.47), 0.98
                ),
                TextRegion(
                    proposed.action.target_label, NormalizedBox(0.32, 0.32, 0.39, 0.47), 0.99
                ),
            ),
        )
        actual = planner._snap_model_action_to_ocr(proposed, current)
        self.assertEqual(actual.kind, DecisionKind.ABSTAIN)

    def test_changed_icon_cannot_skip_freshness_due_to_high_confidence(self) -> None:
        action = outcome(1, label="gear icon")
        before = snapshot(1, 1)
        after = snapshot(2, 2)
        # Non-overlapping OCR elsewhere, the condition used by visual_only.
        text = (TextRegion("Inventory", NormalizedBox(0.01, 0.01, 0.04, 0.04), 0.99),)
        before = replace(before, visible_text=text)
        after = replace(after, visible_text=text)
        valid, reason = ActionValidator(PerceptionProfile()).validate(
            action, before, after, frame(1), frame(255), "open settings"
        )
        self.assertFalse(valid, reason)

    def test_verifier_rejects_duplicate_and_blank_verdicts(self) -> None:
        for reply in (
            '{"approved":false,"approved":true,"confidence":0.99,"reason":"ok"}',
            json.dumps({"approved": True, "confidence": 0.99, "reason": ""}),
            json.dumps({"approved": True, "confidence": 0.99, "reason": "x" * 241}),
        ):
            verifier = GroundedOutcomeVerifier(_Client([reply]))
            self.assertFalse(verifier(outcome(1), snapshot(1, 1), frame(1), "open settings"))

    def test_verifier_receives_operation_not_just_a_target_name(self) -> None:
        client = _Client([json.dumps({"approved": True, "confidence": 0.99, "reason": "visible"})])
        verifier = GroundedOutcomeVerifier(client)
        self.assertTrue(verifier(outcome(1), snapshot(1, 1), frame(1), "open settings"))
        prompt = client.calls[0]["instruction"]
        self.assertIn('"kind":"click"', prompt)
        self.assertIn('"key":null', prompt)
        self.assertIn("independent", prompt)
