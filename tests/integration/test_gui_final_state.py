from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers import frame
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_review_followup import make_loop
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.agent.closed_loop import ActionValidator, DecisionDisposition
from uga.environment.profile import PerceptionProfile
from uga.perception.schema import NormalizedBox, TextRegion, UiElement
from uga.time.clock import ManualClock


class FinalGuiStateTests(unittest.IsolatedAsyncioTestCase):
    async def check_change(self, *, disabled: bool):  # type: ignore[no-untyped-def]
        clock = ManualClock(100)
        loop, backend, supervisor, _, frames = make_loop(GroundedClickPlanner(), clock)
        original_assess = supervisor.assess
        original_builder = loop._perception_builder

        class ChangedState:
            def build(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                assert original_builder is not None
                result = original_builder.build(*args, **kwargs)
                if disabled:
                    return replace(result, ui_elements=(
                        UiElement('settings', NormalizedBox(0.1, 0.1, 0.9, 0.9), 0.99,
                                  enabled=False),
                    ))
                return replace(result, visible_text=(
                    TextRegion('Inventory', NormalizedBox(0.1, 0.1, 0.9, 0.9), 0.99),
                ))

        def assess(*args, **kwargs):  # type: ignore[no-untyped-def]
            result = original_assess(*args, **kwargs)
            self.assertEqual(result.disposition, DecisionDisposition.EXECUTE)
            loop._perception_builder = ChangedState()
            previous = frames.latest()
            assert previous is not None
            # The latest structured evidence changed, but sampled pixels alone
            # are identical. A previous verifier is not authority for a new state.
            frames.publish(replace(previous.frame, frame_id='frame-2'))
            return result

        with patch.object(supervisor, 'assess', side_effect=assess):
            result = await loop.step()
        return backend, supervisor, result

    async def test_disabled_control_after_supervision_never_reaches_input(self) -> None:
        backend, supervisor, result = await self.check_change(disabled=True)
        self.assertEqual(backend.actions, [])
        self.assertIsNone(result.gui_submission)
        self.assertEqual(result.supervision.disposition, DecisionDisposition.REOBSERVE)
        feedback = json.loads(supervisor.planner_feedback())['recent_actions']
        self.assertEqual(len(feedback), 1)
        self.assertIn('disabled', feedback[0]['reason'])

    async def test_conflicting_fresh_ocr_is_not_overruled_by_unchanged_pixels(self) -> None:
        backend, supervisor, result = await self.check_change(disabled=False)
        self.assertEqual(backend.actions, [])
        self.assertIsNone(result.gui_submission)
        feedback = json.loads(supervisor.planner_feedback())['recent_actions']
        self.assertEqual(len(feedback), 1)
        self.assertIn('target', feedback[0]['reason'])


class DisabledLandingPointTests(unittest.TestCase):
    def test_model_alias_cannot_bypass_disabled_state_at_actual_landing_point(self) -> None:
        proposal = outcome(1)
        perceived = replace(snapshot(1, 0, visible_text=('settings',)), ui_elements=(
            UiElement('options button', NormalizedBox(0.45, 0.45, 0.55, 0.55), 0.99,
                      enabled=False),
        ))
        allowed, reason = ActionValidator(PerceptionProfile()).validate(
            proposal, perceived, perceived, frame(1), frame(1), 'open settings',
            secondary_verified=True,
        )
        self.assertFalse(allowed)
        self.assertIn('disabled', reason)
