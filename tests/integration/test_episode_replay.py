from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.helpers import frame
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS
from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetProcessor
from uga.recording.episode_writer import EpisodeWriter, RecorderChannel
from uga.recording.replay import ReplayEngine
from uga.recording.schema import (
    ActionProvenance,
    EpisodeMetadata,
    EpisodeResult,
    InputStateRecord,
)
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import UGATime


class EpisodeReplayTests(unittest.TestCase):
    def test_abort_removes_private_staging_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EpisodeWriter(
                root,
                EpisodeMetadata(
                    episode_id="aborted-episode",
                    game_id="fixture-game",
                    game_version="1.0",
                    window_size=(2, 2),
                    capture_backend="fixture",
                    start_monotonic_ns=100,
                    task="abort setup",
                    result=EpisodeResult.IN_PROGRESS,
                    agent_version="test-agent",
                    policy_version="test-policy",
                    human_controlled=False,
                ),
                require_video=False,
            )
            staging = writer.staging_path

            writer.abort()

            self.assertFalse(staging.exists())
            with self.assertRaisesRegex(ContractViolation, "already finalized"):
                writer.finalize(EpisodeResult.ABORTED)

    def test_writer_rejects_buffer_growth_during_recording(self) -> None:
        metadata = EpisodeMetadata(
            episode_id="bounded-episode",
            game_id="fixture-game",
            game_version="1.0",
            window_size=(2, 2),
            capture_backend="fixture",
            start_monotonic_ns=100,
            task="bounded recording",
            result=EpisodeResult.IN_PROGRESS,
            agent_version="test-agent",
            policy_version="test-policy",
            human_controlled=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            writer = EpisodeWriter(
                temporary,
                metadata,
                require_video=False,
                limits=replace(DEFAULT_ARTIFACT_LIMITS, max_episode_buffer_bytes=1),
            )
            with self.assertRaisesRegex(ContractViolation, "buffer resource limit"):
                writer.record_event("event", UGATime(100), {"payload": "too large"})

            finalize_writer = EpisodeWriter(
                temporary,
                replace(metadata, episode_id="bounded-finalize"),
                require_video=False,
                limits=replace(DEFAULT_ARTIFACT_LIMITS, max_episode_bytes=1),
            )
            with self.assertRaisesRegex(ContractViolation, "total byte resource limit"):
                finalize_writer.finalize(EpisodeResult.SUCCESS, UGATime(100))

    def _record_episode(self, root: Path) -> Path:
        metadata = EpisodeMetadata(
            episode_id="episode-00001",
            game_id="fixture-game",
            game_version="1.0",
            window_size=(2, 2),
            capture_backend="fixture",
            start_monotonic_ns=100,
            task="press forward",
            result=EpisodeResult.IN_PROGRESS,
            agent_version="test-agent",
            policy_version="test-policy",
            human_controlled=False,
        )
        writer = EpisodeWriter(root, metadata)
        writer.attach_video(PyAvVideoRecorder(writer.video_path, fps=30))
        writer.record_frame(frame(1, timestamp_ns=100))
        # Adjacent source timestamps may quantize to the same codec tick; the
        # recorder must still emit strictly monotonic PTS values.
        writer.record_frame(frame(2, timestamp_ns=101))
        writer.record_event("event-1", UGATime(105), {"name": "capture_started"})
        writer.record_observation("observation-1", UGATime(110), {"frame_id": "frame-1"})
        writer.record_task("task-1", UGATime(115), {"goal": "press forward"})
        writer.record_planner("plan-1", UGATime(116), {"skill": "move_forward"})
        writer.set_metrics({"capture_fps": 30.0, "expired_actions": 0})
        lifetime = ActionLifetime(UGATime(120), UGATime(121), UGATime(200))
        action = KeyboardAction("action-1", lifetime, 0x11, True)
        provenance = ActionProvenance(
            action_id=action.action_id,
            action_source="FAST_POLICY",
            policy_version="test-policy",
            model_checkpoint="checkpoint-1",
            observation_id="observation-1",
            skill_id="move-forward",
            task_node_id="task-1",
            mode="PLAY_3D",
            lease_id="lease-1",
            confidence=0.95,
            human_override=False,
            lifetime=lifetime,
        )
        writer.record_action(action, provenance)
        human_lifetime = ActionLifetime(UGATime(140), UGATime(140), UGATime(210))
        human_action = KeyboardAction("human-action-1", human_lifetime, 0x11, False)
        human_provenance = ActionProvenance(
            action_id=human_action.action_id,
            action_source="HUMAN",
            policy_version=None,
            model_checkpoint=None,
            observation_id="observation-1",
            skill_id=None,
            task_node_id="task-1",
            mode="PLAY_3D",
            lease_id="manual-lease-1",
            confidence=1.0,
            human_override=True,
            lifetime=human_lifetime,
        )
        input_state = InputStateRecord(UGATime(140), human_action, (), ())
        writer.record_action(human_action, human_provenance, input_state=input_state)
        writer.record_annotation("annotation-1", UGATime(130), {"quality": "ok"})
        return writer.finalize(EpisodeResult.SUCCESS, UGATime(40_000_000))

    def test_episode_round_trip_has_video_provenance_and_stable_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = self._record_episode(Path(temporary))
            self.assertTrue((episode / "video.mp4").is_file())
            self.assertTrue((episode / "run.json").is_file())
            self.assertIn("move_forward", (episode / "planner.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads((episode / "metrics.json").read_text(encoding="utf-8"))["capture_fps"],
                30.0,
            )
            self.assertGreater((episode / "video.mp4").stat().st_size, 0)
            av = importlib.import_module("av")
            with av.open(str(episode / "video.mp4")) as container:
                self.assertEqual(sum(1 for _ in container.decode(video=0)), 2)
            replay = ReplayEngine(episode)
            validation = replay.validation()
            self.assertEqual(validation.action_count, 2)
            self.assertEqual(validation.observation_count, 1)
            self.assertTrue(replay.actions_for_observation("observation-1"))
            self.assertIsNotNone(replay.provenance_for_action("action-1"))
            first_digest = validation.digest
            self.assertEqual(ReplayEngine(episode).validation().digest, first_digest)
            elapsed = [event.elapsed_ns for event in replay.events_until(1_000)]
            self.assertEqual(elapsed, sorted(elapsed))

    def test_checksum_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = self._record_episode(Path(temporary))
            events = episode / "events.jsonl"
            events.write_text("corrupted\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "checksum mismatch"):
                ReplayEngine(episode)

    def test_checksum_manifest_cannot_omit_episode_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = self._record_episode(Path(temporary))
            checksum_path = episode / "checksum.json"
            checksum = json.loads(checksum_path.read_text(encoding="utf-8"))
            del checksum["files"]["events.jsonl"]
            checksum_path.write_text(json.dumps(checksum), encoding="utf-8")

            with self.assertRaisesRegex(ContractViolation, "file set mismatch"):
                ReplayEngine(episode)

    def test_nested_checksum_named_file_is_not_exempt_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = self._record_episode(Path(temporary))
            nested = episode / "nested" / "checksum.json"
            nested.parent.mkdir()
            nested.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ContractViolation, "file set mismatch"):
                ReplayEngine(episode)

    def test_replay_rejects_episode_and_parquet_resource_overruns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = self._record_episode(Path(temporary))
            file_limits = replace(DEFAULT_ARTIFACT_LIMITS, max_episode_files=1)
            with self.assertRaisesRegex(ContractViolation, "file-count"):
                ReplayEngine(episode, limits=file_limits)

            row_limits = replace(DEFAULT_ARTIFACT_LIMITS, max_parquet_rows=1)
            with self.assertRaisesRegex(ContractViolation, "row limit"):
                ReplayEngine(episode, limits=row_limits)
            with self.assertRaisesRegex(ContractViolation, "row limit"):
                DatasetProcessor(limits=row_limits).process(episode)

    def test_recorder_channel_preserves_submission_order(self) -> None:
        observed: list[int] = []
        channel = RecorderChannel(capacity=1)
        for value in range(10):
            channel.submit(lambda value=value: observed.append(value))
        channel.close()
        self.assertEqual(observed, list(range(10)))


if __name__ == "__main__":
    unittest.main()
