"""Postconditions must use corresponding pixels, locations and fresh observations."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.capture.frame import BufferHandle
from uga.environment.profile import PerceptionProfile
from uga.perception.schema import GuiEffect, NormalizedBox, TextRegion, UiElement
from uga.time.clock import ManualClock
from uga.windows.coordinates import Rect


def pixels(number: int, timestamp: int, value: int):  # type: ignore[no-untyped-def]
    original = frame(number, timestamp)
    payload = bytes((value, value, value, 255)) * (32 * 32)
    return replace(original, width=32, height=32, stride_bytes=128,
                   client_rect=Rect(0, 0, 32, 32), physical_rect=Rect(0, 0, 32, 32),
                   buffer_handle=replace(original.buffer_handle,
                                         size_bytes=len(payload), payload=payload))


class GuiEffectEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(0)
        self.supervisor = ClosedLoopSupervisor(
            self.clock, PerceptionProfile(action_effect_timeout_ms=2000)
        )

    def start(self, spec: GuiEffect, *, before=None):  # type: ignore[no-untyped-def]
        original = outcome(1)
        proposal = replace(original, action=replace(original.action, effect=spec))
        self.supervisor.start_action(
            proposal, before or snapshot(1, 0, visible_text=('settings',)), pixels(1, 0, 0)
        )

    def test_ocr_dropout_on_unchanged_pixels_is_not_disappearance(self) -> None:
        self.start(GuiEffect('text_disappears', 'settings'))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('notification',)),
                pixels(number, timestamp, 0),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_ocr_relabel_on_unchanged_pixels_is_not_new_text(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Sound',)), pixels(number, timestamp, 0)
            )
            self.assertIsNot(result.effect_observed, True)

    def test_unrelated_same_named_control_cannot_satisfy_target_state_change(self) -> None:
        distant = UiElement('settings', NormalizedBox(0.01, 0.01, 0.04, 0.04), .99)
        before = replace(snapshot(1, 0, visible_text=('settings',)), ui_elements=(distant,))
        self.start(GuiEffect('target_changes'), before=before)
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            current = replace(snapshot(number, timestamp, visible_text=('settings',)),
                              ui_elements=(replace(distant, selected=True),))
            self.assertIsNot(self.supervisor.observe(
                current, pixels(number, timestamp, 0)
            ).effect_observed, True)

    def test_concrete_text_effect_is_not_starved_by_unrelated_pixel_stabilizer(self) -> None:
        self.start(GuiEffect('text_appears', 'Ready'), before=snapshot(1, 0))
        for number, timestamp, colour in ((2, 300_000_000, 80), (3, 800_000_000, 160)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Ready',)),
                pixels(number, timestamp, colour),
            )
        self.assertTrue(result.effect_observed)

    def test_new_text_must_stay_at_the_same_location_for_confirmation(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        first_box = NormalizedBox(.1, .1, .3, .2)
        second_box = NormalizedBox(.7, .7, .9, .8)
        for number, timestamp, box in ((2, 300_000_000, first_box),
                                       (3, 800_000_000, second_box),
                                       (4, 1_100_000_000, second_box)):
            current = replace(snapshot(number, timestamp), visible_text=(
                TextRegion('Sound', box, .99),
            ))
            result = self.supervisor.observe(current, pixels(number, timestamp, 100))
            if number < 4:
                self.assertIsNot(result.effect_observed, True)
        self.assertTrue(result.effect_observed)

    def test_ocr_scene_relabel_without_pixel_change_is_not_transition(self) -> None:
        self.start(GuiEffect('scene_changes'),
                   before=snapshot(1, 0, visible_text=('settings', 'volume')))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('inventory', 'items')),
                pixels(number, timestamp, 0),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_sensitive_page_invalidates_pending_trace_with_feedback(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        result = self.supervisor.observe(
            snapshot(2, 300_000_000, visible_text=('password',)), pixels(2, 300_000_000, 100)
        )
        self.assertFalse(result.pending)
        self.assertIsNone(result.effect_observed)
        history = json.loads(self.supervisor.planner_feedback())['recent_actions']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['status'], 'invalidated')

    def test_mutable_source_buffer_is_snapshotted_before_next_capture(self) -> None:
        before = pixels(1, 0, 0)
        shared = bytearray(before.buffer_handle.payload)
        before = replace(before, buffer_handle=BufferHandle(
            before.buffer_handle.handle_id, before.buffer_handle.kind, len(shared), shared
        ))
        proposal = outcome(1)
        proposal = replace(proposal, action=replace(
            proposal.action, effect=GuiEffect('text_appears', 'Sound')
        ))
        self.supervisor.start_action(proposal, snapshot(1, 0, visible_text=('settings',)), before)
        shared[:] = bytes((100, 100, 100, 255)) * (32 * 32)
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Sound',)),
                pixels(number, timestamp, 100),
            )
        self.assertTrue(result.effect_observed)

    def test_numeric_counter_changes_are_not_a_scene_transition(self) -> None:
        self.start(GuiEffect('scene_changes'),
                   before=snapshot(1, 0, visible_text=('health100', 'coins10')))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('health99', 'coins20')),
                pixels(number, timestamp, 100),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_low_confidence_existing_text_is_not_new_when_ocr_improves(self) -> None:
        before = replace(snapshot(1, 0), visible_text=(
            TextRegion('Sound', NormalizedBox(.1, .1, .9, .2), .4),
        ))
        self.start(GuiEffect('text_appears', 'Sound'), before=before)
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Sound',)),
                pixels(number, timestamp, 100),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_stable_target_change_can_confirm_with_unchanged_ocr(self) -> None:
        self.start(GuiEffect('target_changes'))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('settings',)),
                pixels(number, timestamp, 100),
            )
        self.assertTrue(result.effect_observed)

    def test_continuing_target_animation_is_not_a_stable_target_change(self) -> None:
        self.start(GuiEffect('target_changes'))
        for number, timestamp, value in ((2, 300_000_000, 80), (3, 800_000_000, 160),
                                         (4, 2_100_000_000, 200)):
            self.clock.set(timestamp)
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('settings',)),
                pixels(number, timestamp, value),
            )
            self.assertIsNot(result.effect_observed, True)
        self.assertFalse(result.pending)

    def test_mismatched_snapshot_and_pixel_frame_cannot_confirm(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Sound',)),
                pixels(number + 10, timestamp, 100),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_postcondition_arriving_after_absolute_deadline_cannot_succeed(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        first = self.supervisor.observe(
            snapshot(2, 300_000_000, visible_text=('Sound',)), pixels(2, 300_000_000, 100)
        )
        self.assertTrue(first.pending)
        self.clock.set(2_100_000_000)
        result = self.supervisor.observe(
            snapshot(3, 2_100_000_000, visible_text=('Sound',)), pixels(3, 2_100_000_000, 100)
        )
        self.assertFalse(result.pending)
        self.assertFalse(result.effect_observed)

    def test_delayed_ocr_does_not_turn_early_animation_frames_into_settled_evidence(self) -> None:
        self.start(GuiEffect('text_appears', 'Sound'))
        self.clock.set(900_000_000)
        for number, timestamp in ((2, 10_000_000), (3, 150_000_000)):
            result = self.supervisor.observe(
                snapshot(number, timestamp, visible_text=('Sound',)),
                pixels(number, timestamp, 100),
            )
            self.assertIsNot(result.effect_observed, True)

    def test_oversized_baseline_does_not_copy_or_claim_effect(self) -> None:
        from uga.agent import gui_effects

        with patch.object(gui_effects, '_MAX_BASELINE_BYTES', 16):
            self.start(GuiEffect('text_appears', 'Sound'))
        self.clock.set(2_100_000_000)
        result = self.supervisor.observe(
            snapshot(2, 2_100_000_000, visible_text=('Sound',)), pixels(2, 2_100_000_000, 100)
        )
        self.assertFalse(result.pending)
        self.assertFalse(result.effect_observed)

    def test_low_confidence_after_text_is_not_disappearance_proof(self) -> None:
        self.start(GuiEffect('text_disappears', 'settings'))
        for number, timestamp in ((2, 300_000_000), (3, 800_000_000)):
            current = replace(snapshot(number, timestamp), visible_text=(
                TextRegion('settings', NormalizedBox(.1, .1, .9, .2), .3),
                TextRegion('notification', NormalizedBox(.1, .7, .9, .8), .99),
            ))
            self.assertIsNot(self.supervisor.observe(
                current, pixels(number, timestamp, 100)
            ).effect_observed, True)
