"""Causal logical GUI labels referencing immutable source Episode/video data.

The loader owns image tensor extraction. Post-action screens never replace the
pre-action observation. Metadata-only fixtures explicitly have no source video.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    parse_json_text,
)
from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetProcessor
from uga.recording.replay import ReplayEngine


def export_gui_samples(
    episodes: Sequence[str | Path], output: str | Path, *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> Path:
    if not episodes or len(episodes) > limits.max_dataset_episodes:
        raise ContractViolation("GUI export requires a bounded, non-empty Episode list")
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gui-export-", dir=destination.parent)
    count = size = 0
    seen: set[tuple[str, str]] = set()
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for episode_path in episodes:
                replay = ReplayEngine(episode_path, limits=limits)
                processed = DatasetProcessor(limits=limits).process(episode_path)
                for sample in processed.samples:
                    if sample.action_type != "GuiAction":
                        continue
                    key = (sample.episode_id, sample.action_id)
                    if key in seen:
                        raise ContractViolation("GUI export contains a duplicate logical action")
                    seen.add(key)
                    row = {
                        "schema": "uga.gui_training_sample", "schema_version": "1.0",
                        "episode_id": sample.episode_id, "game_id": sample.game_id,
                        "task": replay.metadata["task"], "action_id": sample.action_id,
                        "action": parse_json_text(sample.action_json),
                        "observation_id": sample.observation_id,
                        "observation": parse_json_text(sample.observation_json),
                        "capture_timestamp_ns": sample.observation_timestamp_ns,
                        "executed_at_ns": sample.action_timestamp_ns,
                        "source_video": (
                            "video.mp4" if (replay.path / "video.mp4").is_file() else None
                        ),
                        "source_episode_start_ns": replay.metadata["start_monotonic_ns"],
                        "inference_observation_id": sample.inference_observation_id,
                        "execution_status": sample.execution_status,
                        "provenance": replay.provenance_for_action(sample.action_id),
                    }
                    encoded = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                    line_bytes = len(encoded.encode("utf-8"))
                    count += 1
                    size += line_bytes
                    if (count > limits.max_training_samples or size > limits.max_jsonl_bytes
                            or line_bytes > limits.max_jsonl_line_bytes):
                        raise ContractViolation("GUI export exceeds its resource limit")
                    stream.write(encoded)
            if not count:
                raise ContractViolation("no executed, causal logical GUI samples to export")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
