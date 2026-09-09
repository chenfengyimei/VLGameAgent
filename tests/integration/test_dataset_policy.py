from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.helpers import frame, identity
from uga.control.arbiter import ActionArbiter
from uga.control.canonical import CanonicalAction
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import (
    AbsolutePointerAction,
    KeyboardAction,
    MouseButton,
    MouseButtonAction,
)
from uga.core.errors import ContractViolation
from uga.dataset.manifest import (
    DatasetCategory,
    DatasetEpisode,
    DatasetLicense,
    DatasetManifest,
)
from uga.dataset.opencua import OpenCuaExporter, load_opencua_trajectory, write_opencua_trajectory
from uga.dataset.processor import (
    DatasetProcessor,
    DatasetSplit,
    LeakageSafeSplitRegistry,
    MultiGameDataset,
)
from uga.dataset.validator import DatasetValidator, QualityStatus
from uga.dataset.viewer import write_episode_viewer
from uga.evaluation.closed_loop import ClosedLoopEpisodeResult, ClosedLoopEvaluator
from uga.evaluation.offline import OfflineEvaluator
from uga.policy.action_chunk import ActionChunk
from uga.policy.cadence import AdaptivePolicyCadence
from uga.policy.chunk_controller import ActionChunkController, CanonicalButton, expand_action_chunk
from uga.policy.fast_policy import (
    DecoderCheckpoint,
    PolicyContext,
    Qwen3VlBackbone,
    StructuredFastPolicy,
    TemporalActionDecoder,
)
from uga.policy.reasoning_gate import ReasoningGate, ReasoningReason, ReasoningSignals
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import (
    ActionProvenance,
    EpisodeMetadata,
    EpisodeResult,
    InputStateRecord,
)
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import ManualClock, UGATime
from uga.training.artifact import TrainingArtifactManifest
from uga.training.behavior_cloning import BehaviorCloningTrainer, MotorTrainingSample
from uga.training.dagger import DaggerPipeline, HumanOverride
from uga.training.datasets import InstructionSample, RecoveryDataset, RecoverySample
from uga.training.motor_pipeline import export_motor_samples, load_motor_samples, train_motor_policy
from uga.training.veomni import DistributedTrainingJob, VeOmniBackend
from uga.windows.coordinates import CoordinateSpace


def make_episode(root: Path) -> Path:
    metadata = EpisodeMetadata(
        "dataset-episode",
        "game-a",
        "1",
        (2, 2),
        "fixture",
        100,
        "move",
        EpisodeResult.IN_PROGRESS,
        "agent",
        "policy",
        False,
    )
    writer = EpisodeWriter(root, metadata)
    writer.attach_video(PyAvVideoRecorder(writer.video_path))
    writer.record_frame(frame(1, timestamp_ns=100))
    writer.record_observation("obs-1", UGATime(110), {"frame": "frame-1", "features": [1.0, -0.5]})
    lifetime = ActionLifetime(UGATime(120), UGATime(120), UGATime(200))
    canonical = CanonicalAction(
        "canonical-action-1",
        lifetime,
        move_x=-1.0,
        move_y=0.5,
        look_x=-0.2,
        look_y=0.1,
    )
    writer.record_canonical_action(
        canonical,
        ActionProvenance(
            canonical.action_id,
            "FAST_POLICY",
            "policy",
            "checkpoint",
            "obs-1",
            "move",
            "task",
            "play_3d",
            "lease",
            0.9,
            False,
            lifetime,
        ),
    )
    action = KeyboardAction("action-1", lifetime, 0x11, True)
    writer.record_action(
        action,
        ActionProvenance(
            "action-1",
            "FAST_POLICY",
            "policy",
            "checkpoint",
            "obs-1",
            "move",
            "task",
            "play_3d",
            "lease",
            0.9,
            False,
            lifetime,
        ),
    )
    return writer.finalize(EpisodeResult.SUCCESS, UGATime(1_000_000_100))


def chunk(chunk_id: str, value: float = 0.5) -> ActionChunk:
    return ActionChunk(
        chunk_id,
        "obs-1",
        UGATime(100),
        UGATime(100),
        UGATime(200_000_100),
        30.0,
        (value,) * 6,
        (value,) * 6,
        (0.1,) * 6,
        (0.2,) * 6,
        (1,) * 6,
        0.9,
        "policy-1",
    )


class FakeEncoder:
    model_version = "qwen-feature-fixture"

    def encode_visual_history(self, observations, instruction):  # type: ignore[no-untyped-def]
        del observations, instruction
        return (1.0, -0.5)


class LatencyEncoder:
    model_version = "qwen-latency-fixture"

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock

    def encode_visual_history(self, observations, instruction):  # type: ignore[no-untyped-def]
        del observations, instruction
        self._clock.advance(250_000_000)
        return (1.0, -0.5)


class FakeQueue:
    def __init__(self) -> None:
        self.flushes = 0

    def flush(self) -> int:
        self.flushes += 1
        return 3


class FakeLauncher:
    def launch(self, job: DistributedTrainingJob) -> str:
        return f"run-{job.job_id}"


class ChunkEnvironment:
    def adapt_action(self, action):  # type: ignore[no-untyped-def]
        return (KeyboardAction(f"{action.action_id}:key", action.lifetime, 0x11, True),)


class ChunkScheduler:
    def schedule(self, decision, target, lease):  # type: ignore[no-untyped-def]
        del target, lease
        return len(decision.proposal.actions) if decision.accepted else 0


class DatasetPolicyTests(unittest.TestCase):
    def test_validator_processor_viewer_and_locked_game_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode_path = make_episode(root)
            report = DatasetValidator().validate(episode_path)
            self.assertEqual(report.status, QualityStatus.ACCEPTED)
            processed = DatasetProcessor().process(episode_path)
            self.assertEqual(processed.samples[0].action_delay_ns, 10)
            viewer = write_episode_viewer(processed, root / "viewer.html")
            viewer_document = viewer.read_text(encoding="utf-8")
            self.assertIn("canonical-action-1", viewer_document)
            self.assertIn('id="sample-query"', viewer_document)
            dataset = MultiGameDataset({"game-a": DatasetSplit.TRAIN, "game-d": DatasetSplit.TEST})
            self.assertEqual(dataset.add(processed), DatasetSplit.TRAIN)
            self.assertEqual(len(dataset.samples(DatasetSplit.TRAIN)), 1)
            self.assertGreater(dataset.hours(DatasetSplit.TRAIN), 0)
            splits = LeakageSafeSplitRegistry()
            splits.assign(
                episode_id="episode-1",
                session_id="session-1",
                player_id="player-1",
                game_id="game-a",
                split=DatasetSplit.TRAIN,
            )
            with self.assertRaisesRegex(ContractViolation, "leakage"):
                splits.assign(
                    episode_id="episode-2",
                    session_id="session-2",
                    player_id="player-2",
                    game_id="game-a",
                    split=DatasetSplit.TEST,
                )

    def test_validator_reports_clock_input_and_raw_capture_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = EpisodeMetadata(
                "quality-faults",
                "game-a",
                "1",
                (2, 2),
                "fixture",
                0,
                "move",
                EpisodeResult.IN_PROGRESS,
                "agent",
                None,
                True,
            )
            writer = EpisodeWriter(root, metadata, require_video=False)
            writer.record_event("later", UGATime(200), {"name": "later"})
            writer.record_event("earlier", UGATime(100), {"name": "earlier"})
            lifetimes = (
                ActionLifetime(UGATime(300), UGATime(300), UGATime(400)),
                ActionLifetime(
                    UGATime(1_000_000_000),
                    UGATime(1_000_000_000),
                    UGATime(1_000_000_100),
                ),
            )
            for action_id, lifetime in zip(("first", "second"), lifetimes, strict=True):
                action = KeyboardAction(action_id, lifetime, 0x11, True)
                writer.record_action(
                    action,
                    ActionProvenance(
                        action_id,
                        "AGENT",
                        None,
                        None,
                        None,
                        None,
                        None,
                        "PLAY_3D",
                        "lease",
                        1.0,
                        False,
                        lifetime,
                    ),
                    input_state=InputStateRecord(lifetime.created_at, action, ("W",), ()),
                )
            episode = writer.finalize(EpisodeResult.FAILURE, UGATime(1_000_000_100))
            report = DatasetValidator(max_input_gap_ns=100).validate(episode)
            codes = {finding.code for finding in report.findings}
            self.assertIn("clock_regression", codes)
            self.assertIn("input_gap", codes)
            self.assertNotIn("missing_raw_input", codes)

    def test_validator_allows_sparse_agent_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EpisodeWriter(
                root,
                EpisodeMetadata(
                    "sparse-agent-actions",
                    "game-a",
                    "1",
                    (2, 2),
                    "fixture",
                    0,
                    "wait and move",
                    EpisodeResult.IN_PROGRESS,
                    "agent",
                    "policy",
                    False,
                ),
                require_video=False,
            )
            for action_id, timestamp in (("first", 100), ("second", 1_000_000_000)):
                lifetime = ActionLifetime(
                    UGATime(timestamp), UGATime(timestamp), UGATime(timestamp + 100)
                )
                action = KeyboardAction(action_id, lifetime, 0x11, True)
                writer.record_action(
                    action,
                    ActionProvenance(
                        action_id,
                        "FAST_POLICY",
                        "policy",
                        None,
                        None,
                        None,
                        None,
                        "PLAY_3D",
                        "lease",
                        1.0,
                        False,
                        lifetime,
                    ),
                )
            episode = writer.finalize(EpisodeResult.SUCCESS, UGATime(1_000_000_100))

            report = DatasetValidator(max_input_gap_ns=100).validate(episode)

            self.assertNotIn("input_gap", {finding.code for finding in report.findings})

    def test_dataset_manifest_persists_licenses_splits_and_episode_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode_path = make_episode(root)
            report = DatasetValidator().validate(episode_path)
            checksum_digest = hashlib.sha256(
                (episode_path / "checksum.json").read_bytes()
            ).hexdigest()
            manifest = DatasetManifest(
                "dataset-v1-fixture",
                "0.1",
                "test-revision",
                ("game-d",),
                (
                    DatasetLicense(
                        "fixture-license",
                        "developer-owned fixture",
                        "test-only",
                        False,
                        False,
                        "2026-09-09",
                    ),
                ),
                (
                    DatasetEpisode(
                        "dataset-episode",
                        "session-1",
                        "player-1",
                        "game-a",
                        "dataset-episode",
                        DatasetSplit.TRAIN,
                        DatasetCategory.EXPLORATION_NAVIGATION,
                        1_000_000_000,
                        report.status,
                        report.quality_score,
                        "fixture-license",
                        checksum_digest,
                        True,
                        False,
                    ),
                ),
            )
            written = manifest.write(root / "dataset-manifest.json")
            loaded = DatasetManifest.load(written)
            loaded.verify_episode_artifacts(root)
            self.assertGreater(loaded.hours(DatasetSplit.TRAIN), 0)
            self.assertEqual(dict(loaded.category_distribution())[DatasetCategory.COMBAT], 0)
            samples = root / "motor-samples.jsonl"
            samples.write_text(
                '{"features":[-1,1],"move_x":-1,"move_y":0.5,'
                '"look_x":-0.2,"look_y":0.1,"buttons":0,'
                '"episode_id":"dataset-episode","observation_id":"obs-1",'
                '"action_id":"canonical-action-1"}\n'
                '{"features":[1,1],"move_x":-1,"move_y":0.5,'
                '"look_x":-0.2,"look_y":0.1,"buttons":0,'
                '"episode_id":"dataset-episode","observation_id":"obs-1",'
                '"action_id":"canonical-action-1"}\n',
                encoding="utf-8",
            )
            project = Path(__file__).resolve().parents[2]
            trained = train_motor_policy(
                samples_path=samples,
                dataset_manifest_path=written,
                dataset_root=root,
                training_config_path=project / "configs" / "training" / "motor_bc.yaml",
                output_directory=root / "checkpoint",
                policy_version="motor-fixture-v1",
                source_revision="test-revision",
                base_model_license="fixture-only",
            )
            self.assertTrue(trained.checkpoint.is_file())
            self.assertTrue(trained.artifact_manifest.is_file())
            TrainingArtifactManifest.load(trained.artifact_manifest).verify(
                trained.output_directory
            )

    def test_motor_sample_export_preserves_episode_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = make_episode(root)

            output = export_motor_samples((episode,), root / "motor-samples.jsonl")
            samples = load_motor_samples(output)

            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0].features, (1.0, -0.5))
            self.assertEqual(samples[0].episode_id, "dataset-episode")
            self.assertEqual(samples[0].observation_id, "obs-1")
            self.assertEqual(samples[0].action_id, "canonical-action-1")

    def test_opencua_export_import_normalizes_gui_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EpisodeWriter(
                root,
                EpisodeMetadata(
                    "gui-episode",
                    "fixture-gui",
                    "1",
                    (200, 200),
                    "fixture",
                    100,
                    "click center",
                    EpisodeResult.IN_PROGRESS,
                    "agent",
                    None,
                    False,
                ),
                require_video=False,
            )
            images = writer.staging_path / "images"
            images.mkdir()
            (images / "step.png").write_bytes(b"fixture-image")
            writer.record_observation(
                "obs-gui",
                UGATime(110),
                {
                    "image": "images/step.png",
                    "client_screen_rect": {
                        "left": 100,
                        "top": 200,
                        "right": 300,
                        "bottom": 400,
                    },
                },
            )
            pointer_lifetime = ActionLifetime(UGATime(120), UGATime(120), UGATime(200))
            click_lifetime = ActionLifetime(UGATime(121), UGATime(121), UGATime(200))
            actions = (
                AbsolutePointerAction(
                    "pointer",
                    pointer_lifetime,
                    200,
                    300,
                    CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
                ),
                MouseButtonAction("click", click_lifetime, MouseButton.LEFT, True),
            )
            for action in actions:
                writer.record_action(
                    action,
                    ActionProvenance(
                        action.action_id,
                        "GUI_AGENT",
                        None,
                        "gui-checkpoint",
                        "obs-gui",
                        "click",
                        "task-gui",
                        "GUI",
                        "lease-gui",
                        0.9,
                        False,
                        action.lifetime,
                    ),
                )
            episode = writer.finalize(EpisodeResult.SUCCESS, UGATime(300))
            trajectory = OpenCuaExporter().export(episode)
            self.assertEqual(trajectory.steps[0].ground_truth_actions[0].action_type, "moveTo")
            position = trajectory.steps[0].ground_truth_actions[0].params["position"]
            self.assertEqual(position, {"x": 0.5, "y": 0.5})
            output = write_opencua_trajectory(trajectory, root / "opencua.json")
            loaded = load_opencua_trajectory(output)
            self.assertEqual(loaded.steps[0].ground_truth_actions[1].action_type, "click")

    def test_behavior_cloning_checkpoint_and_structured_policy(self) -> None:
        samples = (
            MotorTrainingSample((-1.0, 1.0), -1.0, 0.5, -0.2, 0.1, 0),
            MotorTrainingSample((1.0, 1.0), 1.0, 0.5, 0.2, 0.1, 0),
        )
        checkpoint, metrics = BehaviorCloningTrainer().train(
            samples, policy_version="motor-v0", epochs=200
        )
        self.assertLess(metrics.move_mse, 0.01)
        with tempfile.TemporaryDirectory() as temporary:
            path = checkpoint.save(Path(temporary) / "checkpoint.json")
            loaded = DecoderCheckpoint.load(path)
        policy = StructuredFastPolicy(
            Qwen3VlBackbone(FakeEncoder()),
            TemporalActionDecoder(loaded),
        )
        output = policy.infer(PolicyContext("obs-1", UGATime(100), (), "move"))
        self.assertEqual(output.chunk.horizon, 6)
        self.assertEqual(output.chunk.tick_rate_hz, 30.0)

    def test_offline_and_closed_loop_evaluation(self) -> None:
        perfect = OfflineEvaluator().evaluate((chunk("pred"),), (chunk("target"),))
        self.assertEqual(perfect.movement_mse, 0.0)
        self.assertEqual(perfect.movement_accuracy, 1.0)
        self.assertEqual(perfect.button_f1, 1.0)
        self.assertEqual(perfect.action_chunk_accuracy, 1.0)
        classification = OfflineEvaluator.classify(
            (True, True, False, False), (True, False, True, False)
        )
        self.assertEqual(classification.precision, 0.5)
        self.assertEqual(classification.recall, 0.5)
        self.assertEqual(
            OfflineEvaluator.mode_accuracy(("PLAY_3D", "GUI"), ("PLAY_3D", "GUI")),
            1.0,
        )
        closed = ClosedLoopEvaluator().summarize(
            (
                ClosedLoopEpisodeResult("one", True, 1.0, 50.0, 0, 10, 0.1),
                ClosedLoopEpisodeResult("two", False, 0.5, 70.0, 1, 10, 0.3),
            )
        )
        self.assertEqual(closed.success_rate, 0.5)
        self.assertEqual(closed.expired_action_rate, 0.05)

    def test_reasoning_gate_cancels_future_chunk(self) -> None:
        gate = ReasoningGate(0.5)
        decision = gate.evaluate(ReasoningSignals(0.2, target_lost=True))
        self.assertTrue(decision.need_reasoning)
        self.assertIn(ReasoningReason.LOW_CONFIDENCE, decision.reasons)
        queue = FakeQueue()
        self.assertEqual(gate.apply(decision, queue), 3)
        self.assertEqual(queue.flushes, 1)

    def test_fast_policy_cadence_degrades_and_recovers_with_bounded_horizon(self) -> None:
        cadence = AdaptivePolicyCadence(recovery_samples=2)
        self.assertEqual(cadence.decision.observation_hz, 5.0)
        self.assertEqual(cadence.observe_inference(250).observation_hz, 4.0)
        self.assertEqual(cadence.observe_inference(300).observation_hz, 2.5)
        self.assertEqual(cadence.decision.action_horizon, 12)
        cadence.observe_inference(100)
        recovered = cadence.observe_inference(100)
        self.assertEqual(recovered.observation_hz, 4.0)
        self.assertLessEqual(recovered.action_horizon, 12)

        clock = ManualClock(100)
        adaptive_policy = StructuredFastPolicy(
            Qwen3VlBackbone(LatencyEncoder(clock)),
            TemporalActionDecoder(
                DecoderCheckpoint(
                    "adaptive-fixture",
                    2,
                    ((0.0, 0.0),) * 4,
                    (0.0, 0.0, 0.0, 0.0),
                    0,
                    0.9,
                )
            ),
            cadence=AdaptivePolicyCadence(),
            clock=clock,
        )
        first = adaptive_policy.infer(PolicyContext("obs-1", UGATime(100), (), None))
        self.assertEqual(first.chunk.horizon, 6)
        self.assertEqual(first.observation_hz, 5.0)
        self.assertEqual(first.next_observation_hz, 4.0)
        second = adaptive_policy.infer(PolicyContext("obs-2", UGATime(250_000_100), (), None))
        self.assertEqual(second.chunk.horizon, 8)

    def test_action_chunk_expands_to_time_bounded_canonical_ticks(self) -> None:
        source = chunk("expand", 0.25)
        source = ActionChunk(
            source.chunk_id,
            source.observation_id,
            source.generated_at,
            source.effective_from,
            source.expires_at,
            source.tick_rate_hz,
            source.move_x,
            source.move_y,
            source.look_x,
            source.look_y,
            (int(CanonicalButton.JUMP | CanonicalButton.SPRINT),) * source.horizon,
            source.confidence,
            source.policy_version,
        )
        expanded = expand_action_chunk(source)
        self.assertEqual(len(expanded), 6)
        self.assertTrue(expanded[0].jump)
        self.assertTrue(expanded[0].sprint)
        self.assertEqual(expanded[1].lifetime.effective_from.value_ns, 33_333_433)
        self.assertLessEqual(expanded[-1].lifetime.expires_at, source.expires_at)
        clock = ManualClock(100)
        leases = ControlLeaseManager(clock)
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000_000_000,
            confidence=1.0,
            reason="chunk integration",
        )
        submission = ActionChunkController(
            ChunkEnvironment(),  # type: ignore[arg-type]
            ActionArbiter(clock, leases),
            ChunkScheduler(),  # type: ignore[arg-type]
        ).submit(source, identity(), lease)
        self.assertTrue(submission.decision.accepted)
        self.assertEqual(submission.scheduled_physical_actions, 6)
        with self.assertRaisesRegex(ContractViolation, "undefined"):
            ActionChunk(
                "invalid-buttons",
                source.observation_id,
                source.generated_at,
                source.effective_from,
                source.expires_at,
                source.tick_rate_hz,
                (0.0,),
                (0.0,),
                (0.0,),
                (0.0,),
                (1 << 15,),
                source.confidence,
                source.policy_version,
            )

    def test_instruction_recovery_dagger_and_veomni_contracts(self) -> None:
        instruction = InstructionSample(
            "episode-1",
            DatasetSplit.TRAIN,
            ("obs-1",),
            "follow road",
            "reach gate",
            "chunk-1",
        )
        self.assertEqual(instruction.subgoal, "reach gate")
        recovery = RecoveryDataset()
        recovery.add(
            RecoverySample(
                "episode-1",
                DatasetSplit.TRAIN,
                ("obs-2",),
                "stuck",
                "camera",
                "chunk-2",
                True,
            )
        )
        self.assertEqual(recovery.human_correction_ratio(), 1.0)
        dagger = DaggerPipeline()
        dagger.capture_override(
            HumanOverride(
                "override-1",
                "episode-1",
                "obs-2",
                DatasetSplit.TRAIN,
                UGATime(100),
                UGATime(200),
                "wrong route",
                '{"move_x":1}',
                '{"move_x":-1}',
            )
        )
        dagger.capture_failure("episode-1", DatasetSplit.TRAIN)
        iteration = dagger.finish_iteration(1, "motor-v0", "motor-v1")
        self.assertEqual(iteration.override_count, 1)
        backend = VeOmniBackend(FakeLauncher())
        run_id = backend.submit(
            DistributedTrainingJob("job-1", "qwen", "dataset", "output", "motor_bc", 1, 1)
        )
        self.assertEqual(run_id, "run-job-1")


if __name__ == "__main__":
    unittest.main()
