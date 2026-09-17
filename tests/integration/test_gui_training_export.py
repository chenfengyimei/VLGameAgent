from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_causal_receipts import writer_at
from tests.integration.test_dataset_policy import make_episode, make_outcome_episode
from tests.integration.test_review_followup import make_loop
from uga.control.execution_receipt import ExecutionPrimitiveStatus
from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS
from uga.core.errors import ContractViolation
from uga.dataset.gui import export_gui_samples
from uga.dataset.opencua import OpenCuaExporter
from uga.dataset.processor import DatasetProcessor, EpisodeQualification
from uga.recording.schema import EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import ManualClock, UGATime


class GuiExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_logical_gui_receipts_export_one_pre_action_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = writer_at(root)
            writer.attach_video(PyAvVideoRecorder(writer.video_path))
            clock = ManualClock(100)
            loop, backend, _, scheduler, _ = make_loop(
                GroundedClickPlanner(), clock, recorder=writer
            )
            await loop.step()
            loop.drain_execution_receipts()
            self.assertEqual(len(backend.actions), 3)
            self.assertEqual(scheduler.drain_receipts(), ())
            episode = writer.finalize(EpisodeResult.SUCCESS, clock.now())
            processed = DatasetProcessor().process(episode)
            self.assertEqual(len(processed.samples), 1)
            self.assertEqual(processed.samples[0].action_layer, "gui")
            self.assertEqual(processed.qualified_duration_ns, 0)
            self.assertEqual(processed.qualification, EpisodeQualification.QUALIFIED)
            output = export_gui_samples(episode, root / "export")
            rows = (output / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            row = json.loads(rows[0])
            self.assertEqual(row["capture_ns"], 100)
            self.assertEqual(row["action"]["kind"], "click")
            self.assertEqual(row["coordinate_space"], "client_normalized")
            self.assertEqual((output / row["image"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            with self.assertRaises(FileExistsError):
                export_gui_samples(episode, output)
            with self.assertRaises(ContractViolation):
                export_gui_samples(episode, episode / "mutated")
            with self.assertRaises(ContractViolation):
                export_gui_samples(
                    episode,
                    root / "small",
                    limits=replace(DEFAULT_ARTIFACT_LIMITS, max_dataset_bytes=10),
                )
            self.assertFalse((root / "small").exists())
            self.assertEqual(list(root.glob(".small-*")), [])

    async def test_missing_video_is_not_fabricated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, clock = Path(tmp), ManualClock(100)
            writer = writer_at(root)
            loop, _, _, _, _ = make_loop(GroundedClickPlanner(), clock, recorder=writer)
            await loop.step()
            loop.drain_execution_receipts()
            episode = writer.finalize(EpisodeResult.SUCCESS, clock.now())
            with self.assertRaises(ContractViolation):
                export_gui_samples(episode, root / "export")
            self.assertFalse((root / "export").exists())


class QualificationDurationTests(unittest.TestCase):
    def test_unused_lifetime_is_not_demonstrated_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            processed = DatasetProcessor().process(make_episode(Path(tmp)))
            self.assertEqual(processed.qualified_duration_ns, 10)
            self.assertEqual(processed.qualification, EpisodeQualification.QUALIFIED)

    def test_post_action_data_cannot_bypass_qualification_via_opencua(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            episode = make_outcome_episode(
                Path(tmp), "unproven", (ExecutionPrimitiveStatus.EXECUTED,), causal=False
            )
            with self.assertRaisesRegex(ContractViolation, "no OpenCUA"):
                OpenCuaExporter().export(episode)

    def test_end_cannot_precede_recorded_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            writer.record_observation("obs", UGATime(500), {})
            with self.assertRaisesRegex(ContractViolation, "end"):
                writer.finalize(EpisodeResult.SUCCESS, UGATime(400))
            writer.abort()
