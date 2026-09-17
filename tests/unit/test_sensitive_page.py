from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_review_followup import make_loop
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from tests.unit.test_grounded_vlm import _Client
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition
from uga.environment.profile import PerceptionProfile
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import DecisionKind, NormalizedBox, TextRegion
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.safety.sensitive_page import inspect_sensitive_page
from uga.time.clock import ManualClock


class SensitivePageTests(unittest.TestCase):
    def test_generic_confirmation_needs_owner_when_page_is_sensitive(self) -> None:
        for source in ("model", "ocr_progress_control_fast", "ocr_close_glyph_fast"):
            for label in ("confirm", "ui_close", "ui_promote", "settings"):
                with self.subTest(source=source, label=label):
                    current = snapshot(1, 0, visible_text=("credit card", "Confirm"))
                    supervisor = ClosedLoopSupervisor(
                        ManualClock(0), PerceptionProfile(), close_hotspot=(0.9, 0.1)
                    )
                    decision = supervisor.assess(
                        outcome(1, label=label),
                        current,
                        current,
                        frame(1),
                        frame(1),
                        "confirm payment",
                        decision_source=source,
                    )
                    self.assertEqual(decision.disposition, DecisionDisposition.WAIT)
                    self.assertIn("owner_confirmation_required", decision.reason)

    def test_consent_is_not_a_fast_click_or_speculative_checkbox_state(self) -> None:
        client = _Client([])
        planner = GroundedVlmPlanner(client, prefer_ocr_task_panel=True)
        current = snapshot(
            1, 0, visible_text=("\u5f00\u59cb\u6e38\u620f", "\u540c\u610f\u7528\u6237\u534f\u8bae")
        )
        result = planner.decide(snapshot=current, frames=(frame(1),), goal="start game")
        self.assertEqual(result.kind, DecisionKind.WAIT)
        self.assertEqual(client.calls, [])
        self.assertFalse(hasattr(planner, "_login_agreement_clicked"))

    def test_ordinary_rewards_and_promotion_are_not_legal_or_payment_forms(self) -> None:
        current = snapshot(
            1, 0, visible_text=("\u4efb\u52a1\u5b8c\u6210", "\u9886\u53d6", "gold 500")
        )
        self.assertFalse(inspect_sensitive_page(current.visible_text).requires_owner)

    def test_split_ocr_does_not_hide_credential_prompt(self) -> None:
        current = snapshot(1, 0, visible_text=("pass", "word"))
        self.assertTrue(inspect_sensitive_page(current.visible_text).requires_owner)


class SensitiveLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_screen_never_reaches_model_even_without_fast_rules(self) -> None:
        class NeverCall(GroundedClickPlanner):
            def decide(self, **kwargs):  # type: ignore[no-untyped-def]
                raise AssertionError("sensitive screen was sent to the model")

        class SensitiveText:
            available = True

            def recognize(self, item):  # type: ignore[no-untyped-def]
                return (TextRegion("password", NormalizedBox(0.1, 0.1, 0.9, 0.2), 0.99),)

        loop, backend, _, _, _ = make_loop(NeverCall(), ManualClock(100))
        loop._perception_builder = PerceptionBuilder(SensitiveText())
        result = await loop.step()
        self.assertIsNone(result.planner_outcome)
        self.assertEqual(backend.actions, [])
        self.assertIn("owner_confirmation_required", loop._last_supervision_reason)

    async def test_sensitive_overlay_after_inference_is_not_action_authority(self) -> None:
        class OverlayText:
            available = True

            def recognize(self, item):  # type: ignore[no-untyped-def]
                return (TextRegion("credit card", NormalizedBox(0.1, 0.1, 0.9, 0.2), 0.99),)

        clock = ManualClock(100)
        loop, backend, _, _, frames = make_loop(GroundedClickPlanner(), clock)

        class OverlayPlanner(GroundedClickPlanner):
            def decide(self, **kwargs):  # type: ignore[no-untyped-def]
                proposal = super().decide(**kwargs)
                frames.publish(replace(frame(2, 100), frame_id="overlay"))
                loop._perception_builder = PerceptionBuilder(OverlayText())
                return proposal

        loop._grounded_planner = OverlayPlanner()
        result = await loop.step()
        self.assertEqual(backend.actions, [])
        self.assertEqual(result.supervision.disposition, DecisionDisposition.WAIT)
