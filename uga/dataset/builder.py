from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.dataset.manifest import (
    DatasetCategory,
    DatasetEpisode,
    DatasetLicense,
    DatasetManifest,
    episode_content_digest,
)
from uga.dataset.processor import DatasetProcessor, DatasetSplit, EpisodeQualification
from uga.dataset.validator import DatasetValidator, QualityStatus
from uga.recording.replay import ReplayEngine


def build_dataset_manifest(
    inventory_path: str | Path,
    dataset_root: str | Path,
    *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> DatasetManifest:
    """Build a verified manifest from an explicit, reviewable YAML inventory."""
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError("dataset inventory requires PyYAML") from exc
    inventory: Any = yaml.safe_load(
        read_text_limited(
            inventory_path,
            limits.max_config_bytes,
            "dataset inventory",
        )
    )
    if not isinstance(inventory, dict):
        raise ContractViolation("dataset inventory root must be an object")
    raw_licenses = inventory.get("licenses")
    raw_episodes = inventory.get("episodes")
    if not isinstance(raw_licenses, list) or not isinstance(raw_episodes, list):
        raise ContractViolation("dataset inventory requires license and Episode lists")
    if len(raw_licenses) > limits.max_dataset_licenses:
        raise ContractViolation("dataset inventory exceeds the license resource limit")
    if len(raw_episodes) > limits.max_dataset_episodes:
        raise ContractViolation("dataset inventory exceeds the Episode resource limit")
    try:
        licenses = tuple(
            DatasetLicense(
                str(item["id"]),
                str(item["source"]),
                str(item["dataset_license"]),
                _strict_bool(item["distribution_allowed"], "distribution_allowed"),
                _strict_bool(item["commercial_allowed"], "commercial_allowed"),
                str(item["review_date"]),
            )
            for item in raw_licenses
            if isinstance(item, dict)
        )
        if len(licenses) != len(raw_licenses):
            raise ContractViolation("dataset license inventory entry must be an object")
        root = Path(dataset_root).resolve()
        episodes = tuple(_build_episode(root, item, limits=limits) for item in raw_episodes)
        return DatasetManifest(
            str(inventory["dataset_id"]),
            str(inventory["dataset_version"]),
            str(inventory["source_revision"]),
            tuple(str(item) for item in inventory.get("locked_test_games", ())),
            licenses,
            episodes,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractViolation(f"dataset inventory is incomplete: {exc}") from exc


def _build_episode(
    root: Path,
    item: object,
    *,
    limits: ArtifactResourceLimits,
) -> DatasetEpisode:
    if not isinstance(item, dict):
        raise ContractViolation("dataset Episode inventory entry must be an object")
    relative_path = str(item["path"])
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents or not candidate.is_dir():
        raise ContractViolation(f"dataset Episode path is invalid: {relative_path}")
    replay = ReplayEngine(candidate, limits=limits)
    quality = DatasetValidator(limits=limits).validate(candidate)
    if quality.status != QualityStatus.ACCEPTED:
        raise ContractViolation(
            f"dataset Episode requires accepted quality: {quality.episode_id} ({quality.status})"
        )
    processed = DatasetProcessor(limits=limits).process(candidate)
    if processed.qualification != EpisodeQualification.QUALIFIED:
        raise ContractViolation(
            f"dataset Episode lacks verified executed training labels: {quality.episode_id}"
        )
    checksum = candidate / "checksum.json"
    start = int(replay.metadata["start_monotonic_ns"])
    end = int(replay.metadata["end_monotonic_ns"])
    return DatasetEpisode(
        str(replay.metadata["episode_id"]),
        str(item["session_id"]),
        str(item["player_id"]),
        str(replay.metadata["game_id"]),
        candidate.relative_to(root).as_posix(),
        DatasetSplit(str(item["split"])),
        DatasetCategory(str(item["category"])),
        end - start,
        quality.status,
        quality.quality_score,
        str(item["license_id"]),
        sha256_file_limited(
            checksum,
            limits.max_checksum_bytes,
            "Episode checksum manifest",
        ),
        _strict_bool(item.get("instruction_labeled", False), "instruction_labeled"),
        _strict_bool(item.get("reasoning_labeled", False), "reasoning_labeled"),
        True,
        processed.qualified_duration_ns,
        episode_content_digest(candidate, limits=limits),
    )


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ContractViolation(f"dataset inventory {field} must be a boolean")
    return value
