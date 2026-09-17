"""End-to-end recording/replay checks for causal action labels (no OS input)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import identity
from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_dataset_policy import make_episode, make_outcome_episode
from tests.integration.test_review_followup import make_loop
from uga.control.execution_receipt import ExecutionPrimitiveStatus, ExecutionReceipt
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.core.errors import ContractViolation
from uga.core.events import EventType
from uga.dataset.processor import DatasetProcessor, EpisodeQualification
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.replay import ReplayEngine
from uga.recording.schema import ActionProvenance, EpisodeMetadata, EpisodeResult
from uga.time.clock import ManualClock, UGATime


def writer_at(root: Path) -> EpisodeWriter:
    return EpisodeWriter(
        root,
        EpisodeMetadata(
            "causal-test",
            "fixture-game",
            "1",
            (2, 2),
            "fake",
            0,
            "open settings",
            EpisodeResult.IN_PROGRESS,
            "test",
            "test",
            False,
        ),
        require_video=False,
    )


class CausalReceiptTests(unittest.TestCase):
    def test_effect_frame_is_never_the_policy_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = make_episode(Path(temporary))
            processed = DatasetProcessor().process(path)
            sample = processed.samples[0]
            self.assertEqual(json.loads(sample.observation_json)["features"], [1.0, -0.5])
            self.assertLessEqual(sample.observation_timestamp_ns, sample.action_timestamp_ns)
            receipts = ReplayEngine(path).execution_receipts
            self.assertEqual(receipts[0]["pre_action_observation_id"], "obs-execution-1")
            self.assertEqual(receipts[0]["effect_observation_id"], "obs-effect-1")
            self.assertNotEqual(sample.observation_id, receipts[0]["effect_observation_id"])

    def test_post_action_only_receipts_do_not_qualify(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = make_outcome_episode(
                Path(temporary),
                "legacy-causal",
                (ExecutionPrimitiveStatus.EXECUTED,),
                causal=False,
            )
            processed = DatasetProcessor().process(path)
            self.assertEqual(processed.samples, ())
            self.assertEqual(processed.qualification, EpisodeQualification.UNQUALIFIED)
            self.assertIn("missing_pre_action_observation", dict(processed.exclusion_counts))

    def test_writer_rejects_future_pre_action_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = writer_at(Path(temporary))
            try:
                writer.record_observation("future", UGATime(200), {"features": [9.0]})
                lifetime = ActionLifetime(UGATime(100), UGATime(100), UGATime(500))
                action = KeyboardAction("key", lifetime, 0x11, True)
                writer.record_action(
                    action,
                    ActionProvenance(
                        "key",
                        "GUI_AGENT",
                        "test",
                        None,
                        None,
                        None,
                        None,
                        "GUI",
                        "lease",
                        1.0,
                        False,
                        lifetime,
                        "proposal",
                    ),
                )
                receipt = ExecutionReceipt(
                    "key",
                    "proposal",
                    "KeyboardAction",
                    ExecutionPrimitiveStatus.EXECUTED,
                    UGATime(150),
                    identity(),
                    "lease",
                    1,
                    pre_action_observation_id="future",
                    pre_action_capture_ns=200,
                )
                with self.assertRaisesRegex(ContractViolation, "before execution"):
                    writer.record_execution_receipts((receipt,))
            finally:
                writer.abort()

    def test_partial_gui_proposal_has_no_positive_primitive_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = writer_at(Path(temporary))
            writer.record_observation("pre", UGATime(100), {"features": [1.0]})
            lifetime = ActionLifetime(UGATime(110), UGATime(110), UGATime(200))
            for index, status in enumerate(
                (
                    ExecutionPrimitiveStatus.EXECUTED,
                    ExecutionPrimitiveStatus.REJECTED,
                    ExecutionPrimitiveStatus.FLUSHED,
                )
            ):
                name = f"key-{index}"
                action = KeyboardAction(name, lifetime, 0x11, index != 2)
                writer.record_action(
                    action,
                    ActionProvenance(
                        name,
                        "GUI_AGENT",
                        "test",
                        None,
                        "pre",
                        None,
                        None,
                        "GUI",
                        "lease",
                        1.0,
                        False,
                        lifetime,
                        "click-proposal",
                    ),
                )
                writer.record_execution_receipts(
                    (
                        ExecutionReceipt(
                            name,
                            "click-proposal",
                            "KeyboardAction",
                            status,
                            UGATime(120),
                            identity(),
                            "lease",
                            1,
                            pre_action_observation_id="pre",
                            pre_action_capture_ns=100,
                        ),
                    )
                )
            path = writer.finalize(EpisodeResult.FAILURE, UGATime(200))
            processed = DatasetProcessor().process(path)
            self.assertEqual(processed.samples, ())
            self.assertEqual(dict(processed.exclusion_counts)["not_fully_executed"], 3)


class FinalReceiptPumpTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_step_receipts_are_persisted_without_another_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock(100)
            writer = writer_at(Path(temporary))
            loop, backend, _, scheduler, _ = make_loop(
                GroundedClickPlanner(),
                clock,
                recorder=writer,
            )
            stop = asyncio.Event()
            loop._events.subscribe(
                lambda event: stop.set() if event.event_type == EventType.ACTION_ACCEPTED else None
            )
            await loop.run(stop)
            self.assertEqual(len(backend.actions), 3)
            self.assertEqual(scheduler.drain_receipts(), ())
            path = writer.finalize(EpisodeResult.ABORTED, clock.now())
            replay = ReplayEngine(path)
            self.assertEqual(len(replay.execution_receipts), 3)
            self.assertTrue(all(r["pre_action_observation_id"] for r in replay.execution_receipts))
