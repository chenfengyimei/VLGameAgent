from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.dataset.processor import DatasetSplit, LeakageSafeSplitRegistry
from uga.dataset.validator import QualityStatus
from uga.recording.replay import ReplayEngine

_SHA256 = re.compile(r"[0-9a-f]{64}")


class DatasetCategory(StrEnum):
    EXPLORATION_NAVIGATION = "exploration_navigation"
    COMBAT = "combat"
    INTERACTION = "interaction"
    GUI = "gui"
    RECOVERY = "recovery"
    MIXED_LONG_TASK = "mixed_long_task"


@dataclass(frozen=True, slots=True)
class DatasetLicense:
    license_id: str
    source: str
    dataset_license: str
    distribution_allowed: bool
    commercial_allowed: bool
    review_date: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.license_id, self.source, self.dataset_license, self.review_date)
        ):
            raise ContractViolation("dataset license record is incomplete")
        if type(self.distribution_allowed) is not bool or type(self.commercial_allowed) is not bool:
            raise ContractViolation("dataset license permissions must be booleans")
        try:
            date.fromisoformat(self.review_date)
        except ValueError as exc:
            raise ContractViolation("dataset license review date must be ISO-8601") from exc


@dataclass(frozen=True, slots=True)
class DatasetEpisode:
    episode_id: str
    session_id: str
    player_id: str
    game_id: str
    relative_path: str
    split: DatasetSplit
    category: DatasetCategory
    duration_ns: int
    quality_status: QualityStatus
    quality_score: float
    license_id: str
    checksum_digest: str
    instruction_labeled: bool = False
    reasoning_labeled: bool = False

    def __post_init__(self) -> None:
        identifiers = (
            self.episode_id,
            self.session_id,
            self.player_id,
            self.game_id,
            self.relative_path,
            self.license_id,
        )
        path = PurePosixPath(self.relative_path)
        if any(not value.strip() for value in identifiers):
            raise ContractViolation("dataset episode identifiers cannot be blank")
        if path.is_absolute() or ".." in path.parts or "\\" in self.relative_path:
            raise ContractViolation("dataset episode path must be safe relative POSIX path")
        if self.duration_ns <= 0:
            raise ContractViolation("dataset episode duration must be positive")
        if not math.isfinite(self.quality_score) or not 0 <= self.quality_score <= 100:
            raise ContractViolation("dataset quality score must be in [0, 100]")
        if _SHA256.fullmatch(self.checksum_digest) is None:
            raise ContractViolation("dataset episode checksum digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class DatasetManifest(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.dataset_manifest"

    dataset_id: str
    dataset_version: str
    source_revision: str
    locked_test_games: tuple[str, ...]
    licenses: tuple[DatasetLicense, ...]
    episodes: tuple[DatasetEpisode, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if any(
            not value.strip()
            for value in (self.dataset_id, self.dataset_version, self.source_revision)
        ):
            raise ContractViolation("dataset manifest identity is incomplete")
        if len(set(self.locked_test_games)) != len(self.locked_test_games):
            raise ContractViolation("locked test games contain duplicates")
        licenses = {item.license_id: item for item in self.licenses}
        if len(licenses) != len(self.licenses):
            raise ContractViolation("dataset license ids must be unique")
        if not licenses:
            raise ContractViolation("dataset manifest requires license records")
        if len(self.licenses) > DEFAULT_ARTIFACT_LIMITS.max_dataset_licenses:
            raise ContractViolation("dataset manifest exceeds the license resource limit")
        if len(self.episodes) > DEFAULT_ARTIFACT_LIMITS.max_dataset_episodes:
            raise ContractViolation("dataset manifest exceeds the Episode resource limit")
        registry = LeakageSafeSplitRegistry()
        episode_ids: set[str] = set()
        for episode in self.episodes:
            if episode.episode_id in episode_ids:
                raise ContractViolation(f"duplicate dataset episode: {episode.episode_id}")
            episode_ids.add(episode.episode_id)
            if episode.license_id not in licenses:
                raise ContractViolation(
                    f"episode {episode.episode_id} references an unknown dataset license"
                )
            if episode.quality_status == QualityStatus.REJECTED:
                raise ContractViolation(
                    f"rejected episode cannot enter dataset: {episode.episode_id}"
                )
            if episode.game_id in self.locked_test_games and episode.split != DatasetSplit.TEST:
                raise ContractViolation("locked test game appears outside the test split")
            if episode.split == DatasetSplit.TEST and episode.game_id not in self.locked_test_games:
                raise ContractViolation("test episode game was not locked before dataset creation")
            registry.assign(
                episode_id=episode.episode_id,
                session_id=episode.session_id,
                player_id=episode.player_id,
                game_id=episode.game_id,
                split=episode.split,
            )

    def hours(self, split: DatasetSplit | None = None) -> float:
        duration = sum(
            episode.duration_ns
            for episode in self.episodes
            if split is None or episode.split == split
        )
        return duration / 3_600_000_000_000

    def category_distribution(self) -> tuple[tuple[DatasetCategory, float], ...]:
        total = sum(episode.duration_ns for episode in self.episodes)
        if total == 0:
            return tuple((category, 0.0) for category in DatasetCategory)
        return tuple(
            (
                category,
                sum(
                    episode.duration_ns for episode in self.episodes if episode.category == category
                )
                / total,
            )
            for category in DatasetCategory
        )

    def verify_episode_artifacts(
        self,
        root: str | Path,
        *,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        base = Path(root).resolve()
        total_bytes = 0
        for episode in self.episodes:
            episode_path = (base / episode.relative_path).resolve()
            checksum_path = episode_path / "checksum.json"
            if base not in checksum_path.parents or not checksum_path.is_file():
                raise ContractViolation(
                    f"dataset episode artifact is missing: {episode.episode_id}"
                )
            for candidate in episode_path.rglob("*"):
                if not candidate.is_file():
                    continue
                try:
                    total_bytes += candidate.stat().st_size
                except OSError as exc:
                    raise ContractViolation(
                        f"cannot inspect dataset artifact: {candidate}"
                    ) from exc
                if total_bytes > limits.max_dataset_bytes:
                    raise ContractViolation("dataset exceeds the total byte resource limit")
            digest = sha256_file_limited(
                checksum_path,
                limits.max_checksum_bytes,
                "Episode checksum manifest",
            )
            if digest != episode.checksum_digest:
                raise ContractViolation(f"dataset episode digest mismatch: {episode.episode_id}")
            ReplayEngine(checksum_path.parent, limits=limits)

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        payload = {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "source_revision": self.source_revision,
                "locked_test_games": self.locked_test_games,
                "licenses": [
                    {
                        "license_id": item.license_id,
                        "source": item.source,
                        "dataset_license": item.dataset_license,
                        "distribution_allowed": item.distribution_allowed,
                        "commercial_allowed": item.commercial_allowed,
                        "review_date": item.review_date,
                    }
                    for item in self.licenses
                ],
                "episodes": [self._episode_payload(item) for item in self.episodes],
            },
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> DatasetManifest:
        payload: Any = json.loads(
            read_text_limited(
                path,
                limits.max_document_bytes,
                "Dataset Manifest",
            )
        )
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != cls.SCHEMA_NAME
            or payload.get("schema_version") != cls.SCHEMA_VERSION
            or not isinstance(payload.get("data"), dict)
        ):
            raise ContractViolation("unsupported dataset manifest envelope")
        data: dict[str, Any] = payload["data"]
        raw_licenses = data.get("licenses")
        raw_episodes = data.get("episodes")
        if not isinstance(raw_licenses, list) or not isinstance(raw_episodes, list):
            raise ContractViolation("dataset manifest lists are invalid")
        if len(raw_licenses) > limits.max_dataset_licenses:
            raise ContractViolation("dataset manifest exceeds the license resource limit")
        if len(raw_episodes) > limits.max_dataset_episodes:
            raise ContractViolation("dataset manifest exceeds the Episode resource limit")
        licenses = tuple(DatasetLicense(**item) for item in raw_licenses if isinstance(item, dict))
        episodes = tuple(cls._episode_from_payload(item) for item in raw_episodes)
        if len(licenses) != len(raw_licenses):
            raise ContractViolation("dataset license entry must be an object")
        return cls(
            str(data["dataset_id"]),
            str(data["dataset_version"]),
            str(data["source_revision"]),
            tuple(str(item) for item in data.get("locked_test_games", ())),
            licenses,
            episodes,
        )

    @staticmethod
    def _episode_payload(item: DatasetEpisode) -> dict[str, object]:
        return {
            "episode_id": item.episode_id,
            "session_id": item.session_id,
            "player_id": item.player_id,
            "game_id": item.game_id,
            "relative_path": item.relative_path,
            "split": item.split.value,
            "category": item.category.value,
            "duration_ns": item.duration_ns,
            "quality_status": item.quality_status.value,
            "quality_score": item.quality_score,
            "license_id": item.license_id,
            "checksum_digest": item.checksum_digest,
            "instruction_labeled": item.instruction_labeled,
            "reasoning_labeled": item.reasoning_labeled,
        }

    @staticmethod
    def _episode_from_payload(item: object) -> DatasetEpisode:
        if not isinstance(item, dict):
            raise ContractViolation("dataset episode entry must be an object")
        return DatasetEpisode(
            str(item["episode_id"]),
            str(item["session_id"]),
            str(item["player_id"]),
            str(item["game_id"]),
            str(item["relative_path"]),
            DatasetSplit(str(item["split"])),
            DatasetCategory(str(item["category"])),
            int(item["duration_ns"]),
            QualityStatus(str(item["quality_status"])),
            float(item["quality_score"]),
            str(item["license_id"]),
            str(item["checksum_digest"]),
            _strict_bool(item.get("instruction_labeled", False), "instruction_labeled"),
            _strict_bool(item.get("reasoning_labeled", False), "reasoning_labeled"),
        )


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ContractViolation(f"dataset {field} must be a boolean")
    return value
