from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import identity
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_causal_receipts import writer_at
from tests.integration.test_dataset_policy import make_episode
from tests.integration.test_review_followup import make_loop
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.dataset.gui_export import export_gui_samples
from uga.dataset.processor import DatasetProcessor, EpisodeQualification
from uga.dataset.validator import DatasetValidator
from uga.gui.schema import GuiAction, GuiActionKind
from uga.recording.replay import ReplayEngine
from uga.recording.schema import EpisodeResult
from uga.time.clock import ManualClock, UGATime
from uga.windows.coordinates import CoordinateTransform, Rect


class GuiTrainingExportTests(unittest.TestCase):
    def _episode(self, root: Path, kind: GuiActionKind, *, reject: bool = False) -> Path:
        clock = ManualClock(100)
        writer = writer_at(root)
        loop, _, _, scheduler, _ = make_loop(GroundedClickPlanner(), clock, recorder=writer)
        writer.record_observation("pre", clock.now(), {"features": [1.0], "image": "before"})
        lease = loop._leases.grant(
            ControlOwner.GUI_AGENT, ControlMode.GUI, 2_000_000_000, confidence=1.0, reason="test"
        )
        action = GuiAction(
            "logical", kind, ActionLifetime(clock.now(), clock.now(), UGATime(1_000_000_100)),
            x=0.3, y=0.4, end_x=0.6, end_y=0.8, text="hello",
            key_codes=(17, 65), scroll_delta=-120,
        )
        rect = Rect(0, 0, 200, 200)
        submission = loop._gui_controller.submit(
            action, CoordinateTransform(rect, rect, rect, rect, 1.0), identity(), lease,
            observation_id="pre", policy_version="test", pre_action_observation_id="pre",
            pre_action_capture_ns=100, execution_guard=lambda: not reject,
        )
        for timestamp in sorted({
            a.lifetime.effective_from.value_ns for a in submission.physical_actions
        }):
            clock.set(timestamp)
            scheduler.tick()
            writer.record_execution_receipts(scheduler.drain_receipts())
        return writer.finalize(EpisodeResult.SUCCESS, UGATime(2_000_000_100))

    def test_all_gui_kinds_round_trip_once_without_physical_duplicate_targets(self) -> None:
        for kind in GuiActionKind:
            if kind in (GuiActionKind.WAIT, GuiActionKind.DONE):
                continue
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                path = self._episode(Path(tmp), kind)
                processed = DatasetProcessor().process(path)
                self.assertEqual(len(processed.samples), 1)
                sample = processed.samples[0]
                self.assertEqual(sample.action_type, "GuiAction")
                self.assertEqual(json.loads(sample.action_json)["kind"], kind.value)
                self.assertEqual(processed.qualification, EpisodeQualification.QUALIFIED)
                self.assertEqual(processed.qualified_duration_ns,
                                 700_000_000 if kind == GuiActionKind.LONG_CLICK else 0)
                output = export_gui_samples((path,), Path(tmp) / "gui.jsonl")
                row = json.loads(output.read_text())
                self.assertEqual(row["action"]["kind"], kind.value)
                self.assertEqual(row["observation"]["image"], "before")
                self.assertLessEqual(row["capture_timestamp_ns"], row["executed_at_ns"])
                findings = []
                DatasetValidator._check_execution_receipts(ReplayEngine(path), findings)
                self.assertEqual(findings, [])

    def test_rejected_gui_action_has_no_training_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._episode(Path(tmp), GuiActionKind.CLICK, reject=True)
            self.assertEqual(DatasetProcessor().process(path).samples, ())
            output = Path(tmp) / "gui.jsonl"
            output.write_text("previous-good-export")
            with self.assertRaisesRegex(ContractViolation, "no executed"):
                export_gui_samples((path,), output)
            self.assertEqual(output.read_text(), "previous-good-export")

    def test_mixed_logical_actions_are_selected_without_canonical_short_circuit(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            gui = ReplayEngine(self._episode(Path(tmp) / "gui", GuiActionKind.CLICK))
            motor = ReplayEngine(make_episode(Path(tmp) / "motor"))
            from uga.recording.parquet_io import read_rows

            tables = {
                name + ".parquet": getattr(gui, name) + getattr(motor, name)
                for name in ("actions", "provenance", "execution_receipts", "observations")
            }

            def mixed_rows(path, **kwargs):
                return tables.get(Path(path).name, read_rows(path, **kwargs))

            # Validation of each source was done above; only the synthetic
            # combined tables lack a shared on-disk timeline. Preserve indexes.
            with (
                patch("uga.recording.replay.read_rows", side_effect=mixed_rows),
                patch.object(ReplayEngine, "_validate_and_materialize", return_value=[]),
            ):
                processed = DatasetProcessor().process(gui.path)
            self.assertEqual({s.action_type for s in processed.samples},
                             {"GuiAction", "CanonicalAction"})
            self.assertEqual(len(processed.samples), 2)

    def test_action_ttl_never_counts_as_observed_active_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            processed = DatasetProcessor().process(make_episode(Path(tmp)))
            self.assertTrue(processed.samples)
            self.assertEqual(processed.active_execution_duration_ns, 0)
            self.assertEqual(processed.qualified_duration_ns, 10)

    def test_same_context_identifier_with_conflicting_capture_times_is_rejected(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = self._episode(Path(tmp), GuiActionKind.CLICK)
            replay = ReplayEngine(path)
            replay.execution_receipts[-1]["pre_action_capture_ns"] = 99
            with patch("uga.dataset.processor.ReplayEngine", return_value=replay):
                self.assertEqual(DatasetProcessor().process(path).samples, ())
