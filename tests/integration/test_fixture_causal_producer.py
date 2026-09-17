"""Real fixture producer contracts without physical input or a GPU."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import frame, identity
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_causal_receipts import writer_at
from tests.integration.test_review_followup import make_loop
from uga.control.arbiter import ActionArbiter
from uga.control.lease import ControlMode, ControlOwner
from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetProcessor
from uga.environment.fixture_world import FixtureScenario
from uga.recording.replay import ReplayEngine
from uga.recording.schema import EpisodeResult
from uga.release.fixture_qualification import (
    _build_fixture_cycle,
    _fixture_action_groups,
    _submit_fixture_group,
)
from uga.time.clock import ManualClock, UGATime
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import Rect


class FixtureProducerTests(unittest.TestCase):
    def test_real_group_producer_records_pre_images_not_future_observation_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            clock = ManualClock(0)
            loop, _, _, scheduler, _ = make_loop(GroundedClickPlanner(), clock)
            rect = Rect(0, 0, 800, 600)
            target = WindowSnapshot(identity(), "fixture", rect, rect, 96, True, True)
            actions, _ = _build_fixture_cycle(
                clock.now(), target, 0, 0, FixtureScenario.EXPLORATION
            )
            groups = _fixture_action_groups(actions, 0)
            lease = loop._leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                10_000_000_000,
                confidence=1.0,
                reason="dry fixture",
            )
            arbiter = ActionArbiter(clock, loop._leases)
            for index, instant in enumerate(sorted({a.lifetime.effective_from for a in actions})):
                clock.set(instant.value_ns)
                captured = frame(index + 1, timestamp_ns=instant.value_ns)
                writer.record_frame(captured)
                for group in groups:
                    if group.effective_ns == instant.value_ns:
                        _submit_fixture_group(
                            group,
                            captured,
                            lease,
                            FixtureScenario.EXPLORATION,
                            writer,
                            scheduler,
                            arbiter,
                            clock.now(),
                        )
                scheduler.tick()
                writer.record_execution_receipts(scheduler.drain_receipts())
            path = writer.finalize(EpisodeResult.SUCCESS, UGATime(6_000_000_000))
            replay = ReplayEngine(path)
            processed = DatasetProcessor().process(path)
            motor = [s for s in processed.samples if s.action_layer == "canonical"]
            self.assertEqual(len(motor), 3)
            movement = next(s for s in motor if "-movement-" in s.action_id)
            look = next(s for s in motor if "-look-" in s.action_id)
            interact = next(s for s in motor if "-interact-" in s.action_id)
            self.assertEqual(json.loads(look.action_json)["look_x"], 0.12)
            self.assertTrue(json.loads(interact.action_json)["interact"])
            self.assertEqual(json.loads(look.action_json)["move_x"], 0)
            self.assertEqual(json.loads(interact.action_json)["move_x"], 0)
            target_action = json.loads(movement.action_json)
            self.assertFalse(target_action["interact"])
            self.assertEqual(target_action["look_x"], 0)
            children = [
                r for r in replay.provenance if r.get("parent_action_id") == movement.action_id
            ]
            self.assertTrue(children)
            self.assertTrue(all("-d-" in r["action_id"] for r in children))
            self.assertEqual(len(replay.execution_receipts), len(actions))
            self.assertTrue(all(r["pre_action_observation_id"] for r in replay.execution_receipts))
            self.assertGreater(processed.qualified_duration_ns, 0)
            self.assertLess(processed.qualified_duration_ns, 4_000_000_000)

    def test_stale_fixture_group_is_refused_before_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            clock = ManualClock(2_000_000_000)
            loop, backend, _, scheduler, _ = make_loop(GroundedClickPlanner(), clock)
            rect = Rect(0, 0, 800, 600)
            target = WindowSnapshot(identity(), "fixture", rect, rect, 96, True, True)
            actions, _ = _build_fixture_cycle(
                clock.now(), target, 0, clock.now().value_ns, FixtureScenario.EXPLORATION
            )
            lease = loop._leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                10_000_000_000,
                confidence=1.0,
                reason="dry fixture",
            )
            with self.assertRaisesRegex(ContractViolation, "fresh pre-action"):
                _submit_fixture_group(
                    _fixture_action_groups(actions, 0)[0],
                    frame(1, timestamp_ns=0),
                    lease,
                    FixtureScenario.EXPLORATION,
                    writer,
                    scheduler,
                    ActionArbiter(clock, loop._leases),
                    clock.now(),
                )
            self.assertEqual(scheduler.stats().scheduled, 0)
            self.assertEqual(backend.actions, [])
            writer.abort()
