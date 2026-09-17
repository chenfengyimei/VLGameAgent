from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import identity
from tests.integration.test_causal_receipts import writer_at
from uga.control.execution_receipt import ExecutionPrimitiveStatus, ExecutionReceipt
from uga.dataset.processor import DatasetProcessor
from uga.environment.fixture_world import FixtureScenario, FixtureWorld
from uga.recording.fixture_evidence import FixtureEvidenceRecorder
from uga.recording.replay import ReplayEngine
from uga.recording.schema import EpisodeResult
from uga.release.fixture_qualification import _build_fixture_cycle
from uga.time.clock import UGATime
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import Rect


def target() -> WindowSnapshot:
    return WindowSnapshot(
        identity(), "fixture", Rect(0, 0, 800, 600), Rect(0, 0, 800, 600), 96, True, True
    )


class FixtureEvidenceTests(unittest.TestCase):
    def test_causal_labels_exclude_reset_menu_and_match_compiler_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            writer.record_observation("inference", UGATime(0), {"features": [-99]})
            evidence = FixtureEvidenceRecorder(writer)
            scenario = FixtureScenario.GUI_NAVIGATION
            actions, _ = _build_fixture_cycle(UGATime(100), target(), 0, 100, scenario)
            evidence.register_cycle(
                actions,
                0,
                FixtureWorld(scenario=scenario),
                lease_id="lease",
                proposal_id="proposal",
                observation_id="inference",
            )
            for i, action in enumerate(sorted(actions, key=lambda a: a.lifetime.effective_from)):
                captured = UGATime(action.lifetime.effective_from.value_ns - 1)
                observation_id = f"pre-{i}"
                writer.record_observation(observation_id, captured, {"features": [i]})
                receipt = ExecutionReceipt(
                    action.action_id,
                    "proposal",
                    type(action).__name__,
                    ExecutionPrimitiveStatus.EXECUTED,
                    action.lifetime.effective_from,
                    identity(),
                    "lease",
                    1,
                )
                evidence.record((receipt,), observation_id=observation_id, captured_at=captured)
            episode = writer.finalize(EpisodeResult.SUCCESS, UGATime(10_000_000_000))
            self.assertEqual(evidence.completed_groups, 3)
            self.assertEqual(evidence._by_child, {})
            processed = DatasetProcessor().process(episode)
            self.assertEqual(len(processed.samples), 3)
            for sample in processed.samples:
                self.assertLessEqual(sample.observation_timestamp_ns, sample.action_timestamp_ns)
                self.assertNotEqual(sample.observation_id, "inference")
            labels = {s.action_id: json.loads(s.action_json) for s in processed.samples}
            self.assertEqual(labels["fixture-0000-movement-canonical"]["move_y"], -1.0)
            self.assertEqual(labels["fixture-0000-look-canonical"]["look_x"], 0.12)
            self.assertFalse(labels["fixture-0000-movement-canonical"]["interact"])
            self.assertEqual(labels["fixture-0000-interact-canonical"]["move_y"], 0.0)
            provenance = ReplayEngine(episode).provenance_for_action("fixture-0000-reset-down")
            self.assertIsNone(provenance["parent_action_id"])
            self.assertLess(processed.qualified_duration_ns, 4_000_000_000)
            self.assertGreater(processed.qualified_duration_ns, 0)

    def test_canonical_vertical_sign(self) -> None:
        self.assertEqual(
            FixtureWorld(scenario=FixtureScenario.GUI_NAVIGATION).canonical_movement, (0.0, -1.0)
        )
        x, y = FixtureWorld(scenario=FixtureScenario.HELDOUT_DIAGONAL).canonical_movement
        self.assertGreater(x, 0)
        self.assertLess(y, 0)

    def test_reset_only_aborted_run_does_not_produce_motor_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            writer.record_observation("pre", UGATime(0), {"features": [0]})
            evidence = FixtureEvidenceRecorder(writer)
            actions, _ = _build_fixture_cycle(
                UGATime(100), target(), 0, 100, FixtureScenario.GUI_NAVIGATION
            )
            evidence.register_cycle(
                actions,
                0,
                FixtureWorld(scenario=FixtureScenario.GUI_NAVIGATION),
                lease_id="lease",
                proposal_id="proposal",
                observation_id="pre",
            )
            reset = actions[0]
            evidence.record(
                (
                    ExecutionReceipt(
                        reset.action_id,
                        "proposal",
                        type(reset).__name__,
                        ExecutionPrimitiveStatus.EXECUTED,
                        reset.lifetime.effective_from,
                        identity(),
                        "lease",
                        1,
                    ),
                ),
                observation_id="pre",
                captured_at=UGATime(0),
            )
            episode = writer.finalize(EpisodeResult.ABORTED, UGATime(10_000_000_000))
            self.assertEqual(DatasetProcessor().process(episode).samples, ())
