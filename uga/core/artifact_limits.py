from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class ArtifactResourceLimits:
    max_document_bytes: int = 16 * 1024 * 1024
    max_config_bytes: int = 1024 * 1024
    max_checksum_bytes: int = 1024 * 1024
    max_jsonl_bytes: int = 512 * 1024 * 1024
    max_jsonl_line_bytes: int = 2 * 1024 * 1024
    max_benchmark_jsonl_bytes: int = 256 * 1024 * 1024
    max_benchmark_line_bytes: int = 1024 * 1024
    max_episode_files: int = 32
    max_episode_bytes: int = 32 * 1024 * 1024 * 1024
    max_episode_buffer_bytes: int = 512 * 1024 * 1024
    max_dataset_bytes: int = 128 * 1024 * 1024 * 1024
    max_parquet_file_bytes: int = 2 * 1024 * 1024 * 1024
    max_parquet_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_parquet_rows: int = 1_000_000
    max_parquet_columns: int = 64
    max_dataset_episodes: int = 10_000
    max_dataset_licenses: int = 1_000
    max_training_samples: int = 1_000_000
    max_feature_dimensions: int = 8_192
    max_training_epochs: int = 10_000
    max_training_work: int = 500_000_000
    max_artifact_model_bytes: int = 8 * 1024 * 1024 * 1024
    max_artifact_metrics: int = 10_000
    max_artifact_licenses: int = 10_000
    max_benchmark_tasks: int = 10_000
    max_benchmark_repeat: int = 1_000
    max_benchmark_runs: int = 100_000
    max_latency_samples_per_group: int = 20_000
    max_latency_samples_total: int = 2_000_000
    max_video_bytes: int = 32 * 1024 * 1024 * 1024
    max_video_dimension: int = 8_192
    max_video_pixels: int = 33_554_432
    max_video_frames: int = 1_000_000
    # D11: recorder channel dual caps — entries AND pending bytes are bounded
    # so a slow consumer surfaces backpressure instead of unbounded growth.
    max_recorder_queue_entries: int = 1024
    max_recorder_queue_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(getattr(self, field.name) < 1 for field in fields(self)):
            raise ContractViolation("artifact resource limits must be positive")


DEFAULT_ARTIFACT_LIMITS = ArtifactResourceLimits()


def parse_json_text(text: str) -> Any:
    """Parse untrusted JSON text; nesting bombs fail closed instead of crashing.

    Deeply nested documents raise RecursionError in the JSON parser; that is a
    malformed-payload condition, not a crash the tooling should surface.
    """
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise ContractViolation("JSON payload nesting exceeds the parser limit") from exc


def ensure_file_size(path: str | Path, maximum: int, label: str) -> int:
    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise ContractViolation(f"cannot inspect {label}: {candidate}") from exc
    if size > maximum:
        raise ContractViolation(f"{label} exceeds the {maximum}-byte resource limit")
    return size


def read_bytes_limited(path: str | Path, maximum: int, label: str) -> bytes:
    candidate = Path(path)
    try:
        with candidate.open("rb") as stream:
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise ContractViolation(f"cannot read {label}: {candidate}") from exc
    if len(payload) > maximum:
        raise ContractViolation(f"{label} exceeds the {maximum}-byte resource limit")
    return payload


def read_text_limited(path: str | Path, maximum: int, label: str) -> str:
    payload = read_bytes_limited(path, maximum, label)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractViolation(f"{label} is not valid UTF-8") from exc


def iter_text_lines_limited(
    path: str | Path,
    *,
    maximum_bytes: int,
    maximum_line_bytes: int,
    label: str,
) -> Iterator[tuple[int, str]]:
    candidate = Path(path)
    total = 0
    try:
        with candidate.open("rb") as stream:
            line_number = 0
            while True:
                raw_line = stream.readline(maximum_line_bytes + 1)
                if not raw_line:
                    break
                line_number += 1
                total += len(raw_line)
                if total > maximum_bytes:
                    raise ContractViolation(
                        f"{label} exceeds the {maximum_bytes}-byte resource limit"
                    )
                if len(raw_line) > maximum_line_bytes:
                    raise ContractViolation(
                        f"{label} line {line_number} exceeds the line resource limit"
                    )
                try:
                    yield line_number, raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ContractViolation(
                        f"{label} line {line_number} is not valid UTF-8"
                    ) from exc
    except OSError as exc:
        raise ContractViolation(f"cannot read {label}: {candidate}") from exc


def sha256_file_limited(path: str | Path, maximum: int, label: str) -> str:
    candidate = Path(path)
    digest = hashlib.sha256()
    total = 0
    try:
        with candidate.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(block)
                if total > maximum:
                    raise ContractViolation(f"{label} exceeds the {maximum}-byte resource limit")
                digest.update(block)
    except OSError as exc:
        raise ContractViolation(f"cannot hash {label}: {candidate}") from exc
    return digest.hexdigest()
