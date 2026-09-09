from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from uga.benchmark.io import load_benchmark_runs
from uga.benchmark.schema import BenchmarkRun, load_benchmark_tasks
from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    read_text_limited,
)
from uga.core.errors import ContractViolation
from uga.dataset.validator import DatasetValidator
from uga.policy.fast_policy import DecoderCheckpoint
from uga.training.artifact import TrainingArtifactManifest, sha256_file
from uga.training.behavior_cloning import BehaviorCloningTrainer, MotorTrainingSample
from uga.training.motor_pipeline import load_motor_samples


class ArtifactResourceLimitTests(unittest.TestCase):
    def test_limits_and_text_reader_fail_closed(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "must be positive"):
            replace(DEFAULT_ARTIFACT_LIMITS, max_document_bytes=0)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_text("12345", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "resource limit"):
                read_text_limited(path, 4, "test document")

    def test_motor_sample_and_training_work_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            samples = Path(temporary) / "samples.jsonl"
            samples.write_text(
                json.dumps(
                    {
                        "features": [0.0, 1.0],
                        "move_x": 0.0,
                        "move_y": 0.0,
                        "look_x": 0.0,
                        "look_y": 0.0,
                        "buttons": 0,
                        "episode_id": "episode",
                        "observation_id": "observation",
                        "action_id": "action",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_feature_dimensions=1)
            with self.assertRaisesRegex(ContractViolation, "feature dimension"):
                load_motor_samples(samples, limits=limits)
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_training_work=4)
            with self.assertRaisesRegex(ContractViolation, "work exceeds"):
                load_motor_samples(samples, limits=limits, training_epochs=1)

        sample = MotorTrainingSample((1.0,), 0.0, 0.0, 0.0, 0.0, 0)
        limits = replace(DEFAULT_ARTIFACT_LIMITS, max_training_work=1)
        with self.assertRaisesRegex(ContractViolation, "work exceeds"):
            BehaviorCloningTrainer().train(
                (sample,), policy_version="fixture", epochs=1, limits=limits
            )

    def test_checkpoint_dimension_is_rejected_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "policy_version": "fixture",
                        "input_dim": 2,
                        "axis_weights": [[0.0, 0.0]] * 4,
                        "axis_bias": [0.0] * 4,
                        "button_mask": 0,
                        "confidence": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_feature_dimensions=1)
            with self.assertRaisesRegex(ContractViolation, "resource contract"):
                DecoderCheckpoint.load(checkpoint, limits=limits)

    def test_benchmark_config_and_latency_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "benchmark.yaml"
            config.write_text(
                """tasks:
  - id: task
    game: fixture
    instruction: move
    timeout_seconds: 10
    repeat: 2
    success:
      evaluator: fixture
""",
                encoding="utf-8",
            )
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_benchmark_runs=1)
            with self.assertRaisesRegex(ContractViolation, "run resource limit"):
                load_benchmark_tasks(config, limits=limits)

            run = BenchmarkRun(
                "task",
                "fixture",
                0,
                True,
                1.0,
                0,
                0,
                0,
                False,
                0,
                0,
                0,
                1,
                0.0,
                (1.0, 2.0),
                (1.0,),
                (1.0,),
                (1.0,),
            )
            runs = root / "runs.jsonl"
            runs.write_text(json.dumps(asdict(run)) + "\n", encoding="utf-8")
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_latency_samples_per_group=1)
            with self.assertRaisesRegex(ContractViolation, "latency samples"):
                load_benchmark_runs(runs, limits=limits)
            limits = replace(
                DEFAULT_ARTIFACT_LIMITS,
                max_latency_samples_per_group=10,
                max_latency_samples_total=4,
            )
            with self.assertRaisesRegex(ContractViolation, "total latency"):
                load_benchmark_runs(runs, limits=limits)

    def test_training_artifact_and_video_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.json"
            dataset = root / "dataset.json"
            config = root / "config.yaml"
            samples = root / "samples.jsonl"
            model.write_bytes(b"xx")
            for path in (dataset, config, samples):
                path.write_text("x", encoding="utf-8")
            artifact = TrainingArtifactManifest(
                "fixture",
                model.name,
                "1.1",
                str(dataset),
                "revision",
                str(config),
                str(samples),
                "fixture",
                sha256_file(model),
                sha256_file(dataset),
                sha256_file(config),
                sha256_file(samples),
                (("loss", 0.0),),
                (("base_model", "fixture"),),
            )
            limits = replace(DEFAULT_ARTIFACT_LIMITS, max_artifact_model_bytes=1)
            with self.assertRaisesRegex(ContractViolation, "training model exceeds"):
                artifact.verify(root, limits=limits)

            video = root / "video.mp4"
            video.write_bytes(b"x")
            findings = []
            validator = DatasetValidator(
                limits=replace(DEFAULT_ARTIFACT_LIMITS, max_video_frames=1)
            )
            validator._check_video(video, 2, findings)
            self.assertEqual(findings[0].code, "video_resource_limit")


if __name__ == "__main__":
    unittest.main()
