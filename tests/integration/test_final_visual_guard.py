from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers import frame
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_review_followup import make_loop
from tests.unit import test_closed_loop_supervisor as supervisor_fixtures
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition
from uga.core.events import EventType
from uga.environment.profile import PerceptionProfile
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import NormalizedBox, TextRegion
from uga.time.clock import ManualClock, UGATime


class FinalGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_overlay_during_verifier_is_reobserved_before_input(self) -> None:
        clock = ManualClock(100)
        loop, backend, supervisor, _, frames = make_loop(GroundedClickPlanner(), clock)
        original = supervisor.assess

        class Credentials:
            available = True

            def recognize(self, source):  # type: ignore[no-untyped-def]
                return (TextRegion("password", NormalizedBox(0.1, 0.1, 0.9, 0.2), 0.99),)

        def verify(*args, **kwargs):  # type: ignore[no-untyped-def]
            result = original(*args, **kwargs)
            loop._perception_builder = PerceptionBuilder(Credentials())
            frames.publish(frame(2, timestamp_ns=100))
            return result

        with patch.object(supervisor, "assess", side_effect=verify):
            result = await loop.step()
        self.assertEqual(backend.actions, [])
        self.assertEqual(result.supervision.disposition, DecisionDisposition.WAIT)

    async def test_page_changes_after_acceptance_never_reach_input(self) -> None:
        clock = ManualClock(100)
        loop, backend, _, _, frames = make_loop(GroundedClickPlanner(), clock)
        changed = replace(
            supervisor_fixtures.RegionDigestTests._bgra_frame(2, 255, 0, 255),
            capture_timestamp=clock.now(),
        )
        loop._events.subscribe(
            lambda e: frames.publish(changed) if e.event_type == EventType.ACTION_ACCEPTED else None
        )
        result = await loop.step()
        self.assertIsNotNone(result.gui_submission)
        self.assertEqual(backend.actions, [])


class EffectEvidenceTests(unittest.TestCase):
    def test_context_transition_is_not_credited_as_effect_of_old_action(self) -> None:
        clock = ManualClock(0)
        supervisor = ClosedLoopSupervisor(clock, PerceptionProfile(action_effect_timeout_ms=1000))
        supervisor.start_action(outcome(1), snapshot(1, 0), frame(1))
        clock.set(300_000_000)
        other = replace(snapshot(2, clock.now().value_ns), task_generation=2)
        result = supervisor.observe(other, frame(2, timestamp_ns=clock.now().value_ns))
        self.assertFalse(result.pending)
        self.assertIsNone(result.effect_observed)
        self.assertEqual(supervisor.diagnostics()["verified_effect_actions"], 0)

    def test_frozen_frame_cannot_extend_absolute_effect_deadline(self) -> None:
        clock = ManualClock(0)
        supervisor = ClosedLoopSupervisor(clock, PerceptionProfile(action_effect_timeout_ms=1000))
        initial = snapshot(1, 0)
        supervisor.start_action(outcome(1), initial, frame(1))
        clock.set(2_000_000_000)
        result = supervisor.observe(initial, frame(1))
        self.assertFalse(result.pending)
        self.assertFalse(result.effect_observed)

    def test_deadline_does_not_waive_pixel_stability_window(self) -> None:
        clock = ManualClock(0)
        supervisor = ClosedLoopSupervisor(clock, PerceptionProfile(action_effect_timeout_ms=1000))
        before = replace(
            supervisor_fixtures.RegionDigestTests._bgra_frame(1, 0, 0, 0),
            capture_timestamp=UGATime(0),
        )
        supervisor.start_action(outcome(1), snapshot(1, 0), before)
        for number, timestamp in ((2, 900_000_000), (3, 1_000_000_000)):
            clock.set(timestamp)
            changed = replace(
                supervisor_fixtures.RegionDigestTests._bgra_frame(number, 255, 255, 255),
                capture_timestamp=clock.now(),
            )
            result = supervisor.observe(snapshot(number, timestamp), changed)
        self.assertFalse(result.pending)
        self.assertFalse(result.effect_observed)
