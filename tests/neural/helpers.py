"""Synthetic receipt-backed fixtures; not claims of actual OS input or gameplay."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tests.helpers import frame, identity
from tests.neural.test_training import samples
from uga.control.canonical import CanonicalAction
from uga.control.execution_receipt import ExecutionPrimitiveStatus, ExecutionReceipt
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.dataset.manifest import (
    DatasetCategory,
    DatasetEpisode,
    DatasetLicense,
    DatasetManifest,
    episode_content_digest,
)
from uga.dataset.processor import DatasetProcessor, DatasetSplit
from uga.dataset.validator import DatasetValidator, QualityStatus
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import ActionProvenance, EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import UGATime
from uga.training.artifact import sha256_file
from uga.training.motor_pipeline import export_motor_samples
from uga.training.neural_config import NeuralTrainingConfig


def corpus(root: Path, revision: str = "a" * 40) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    episodes, paths = [], {}
    for split_number, split in enumerate(DatasetSplit):
        episode_id = split.value
        writer = EpisodeWriter(
            root,
            EpisodeMetadata(
                episode_id,
                f"synthetic-game-{episode_id}",
                "1",
                (2, 2),
                "fixture",
                100,
                "synthetic neural pipeline contract",
                EpisodeResult.IN_PROGRESS,
                "test",
                "test",
                False,
            ),
        )
        writer.attach_video(PyAvVideoRecorder(writer.video_path))
        for index, sample in enumerate(samples(episode_id)):
            t = 100 + index * 100_000_000
            writer.record_frame(frame(20 + split_number * 70 + index * 5, timestamp_ns=t))
            obs = f"o{index}"
            writer.record_observation(obs, UGATime(t + 1), {"features": list(sample.features)})
            lifetime = ActionLifetime(UGATime(t + 2), UGATime(t + 2), UGATime(t + 100))
            canonical = CanonicalAction(
                sample.action_id,
                lifetime,
                move_x=sample.move_x,
                move_y=sample.move_y,
                jump=bool(sample.buttons),
            )
            proposal = f"proposal:{index}"
            provenance = ActionProvenance(
                sample.action_id,
                "FAST_POLICY",
                "test",
                "test",
                obs,
                "move",
                "task",
                "play_3d",
                "lease",
                0.9,
                False,
                lifetime,
                proposal,
            )
            writer.record_canonical_action(canonical, provenance)
            child = KeyboardAction(f"physical{index}", lifetime, 0x11, True)
            writer.record_action(
                child,
                ActionProvenance(
                    child.action_id,
                    "FAST_POLICY",
                    "test",
                    "test",
                    obs,
                    "move",
                    "task",
                    "play_3d",
                    "lease",
                    0.9,
                    False,
                    lifetime,
                    proposal,
                    sample.action_id,
                ),
            )
            writer.record_execution_receipts(
                (
                    ExecutionReceipt(
                        child.action_id,
                        proposal,
                        type(child).__name__,
                        ExecutionPrimitiveStatus.EXECUTED,
                        UGATime(t + 3),
                        identity(),
                        "lease",
                        1,
                        pre_action_observation_id=obs,
                        pre_action_capture_ns=t + 1,
                    ),
                )
            )
        path = writer.finalize(EpisodeResult.SUCCESS, UGATime(400_000_100))
        quality = DatasetValidator().validate(path)
        assert quality.status == QualityStatus.ACCEPTED, quality
        processed = DatasetProcessor().process(path)
        episodes.append(
            DatasetEpisode(
                episode_id,
                f"session-{episode_id}",
                f"player-{episode_id}",
                f"synthetic-game-{episode_id}",
                episode_id,
                split,
                DatasetCategory.EXPLORATION_NAVIGATION,
                400_000_000,
                quality.status,
                quality.quality_score,
                "synthetic",
                sha256_file(path / "checksum.json"),
                execution_receipts_qualified=True,
                qualified_duration_ns=processed.qualified_duration_ns,
                content_digest=episode_content_digest(path),
            )
        )
        paths[split.value] = export_motor_samples((path,), root / f"{episode_id}.jsonl")
    manifest = DatasetManifest(
        "synthetic-neural-contract",
        "1",
        revision,
        ("synthetic-game-test",),
        (DatasetLicense("synthetic", "unit-test-fixture", "MIT", True, True, "2026-09-17"),),
        tuple(episodes),
    )
    paths["manifest"] = manifest.write(root / "dataset-manifest.json")
    cfg = NeuralTrainingConfig(hidden_dim=8, epochs=2, batch_size=4)
    paths["config"] = root / "config.json"
    paths["config"].write_text(json.dumps(asdict(cfg)), encoding="utf-8")
    paths["root"] = root
    return paths
