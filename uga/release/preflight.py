from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.dataset.manifest import DatasetManifest
from uga.dataset.processor import DatasetSplit
from uga.release.manifest import GateStatus
from uga.release.qualification import QualificationLedger
from uga.training.artifact import TrainingArtifactManifest


@dataclass(frozen=True, slots=True)
class HostQualificationProbe:
    source_revision: str | None
    gpu_devices: tuple[str, ...]
    worktree_clean: bool = True


@dataclass(frozen=True, slots=True)
class QualificationPreflight:
    schema: str
    schema_version: str
    ledger_releasable: bool
    worktree_clean: bool
    source_revision: str | None
    source_revision_matches_ledger: bool
    ledger_sha256: str
    license_files: tuple[str, ...]
    gpu_devices: tuple[str, ...]
    training_artifact_id: str | None
    training_artifact_path: str | None
    training_artifact_sha256: str | None
    dataset_id: str | None
    dataset_manifest_path: str | None
    dataset_manifest_sha256: str | None
    dataset_root: str | None
    dataset_hours: float
    train_games: tuple[str, ...]
    locked_test_games: tuple[str, ...]
    gate_statuses: tuple[tuple[str, str], ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema != "uga.qualification_preflight" or self.schema_version != "1.1":
            raise ContractViolation("unsupported qualification preflight envelope")
        if type(self.ledger_releasable) is not bool or type(self.worktree_clean) is not bool:
            raise ContractViolation("qualification preflight booleans are invalid")
        if type(self.source_revision_matches_ledger) is not bool:
            raise ContractViolation("qualification revision binding is invalid")
        digests = (
            self.ledger_sha256,
            self.training_artifact_sha256,
            self.dataset_manifest_sha256,
        )
        if _SHA256.fullmatch(self.ledger_sha256) is None or any(
            value is not None and _SHA256.fullmatch(value) is None for value in digests[1:]
        ):
            raise ContractViolation("qualification preflight digest is invalid")
        if not math.isfinite(self.dataset_hours) or self.dataset_hours < 0:
            raise ContractViolation("qualification dataset hours are invalid")
        if (self.training_artifact_path is None) != (self.training_artifact_sha256 is None):
            raise ContractViolation("qualification training artifact binding is incomplete")
        dataset_fields = (
            self.dataset_manifest_path,
            self.dataset_manifest_sha256,
            self.dataset_root,
        )
        if any(value is None for value in dataset_fields) != all(
            value is None for value in dataset_fields
        ):
            raise ContractViolation("qualification dataset binding is incomplete")

    def write(self, path: str | Path) -> Path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path) -> QualificationPreflight:
        try:
            payload: Any = json.loads(
                read_text_limited(
                    path,
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "qualification preflight",
                )
            )
            if not isinstance(payload, dict):
                raise ContractViolation("qualification preflight must be an object")
            return cls(
                _string(payload, "schema"),
                _string(payload, "schema_version"),
                _boolean(payload, "ledger_releasable"),
                _boolean(payload, "worktree_clean"),
                _optional_string(payload, "source_revision"),
                _boolean(payload, "source_revision_matches_ledger"),
                _string(payload, "ledger_sha256"),
                _string_tuple(payload, "license_files"),
                _string_tuple(payload, "gpu_devices"),
                _optional_string(payload, "training_artifact_id"),
                _optional_string(payload, "training_artifact_path"),
                _optional_string(payload, "training_artifact_sha256"),
                _optional_string(payload, "dataset_id"),
                _optional_string(payload, "dataset_manifest_path"),
                _optional_string(payload, "dataset_manifest_sha256"),
                _optional_string(payload, "dataset_root"),
                _number(payload, "dataset_hours"),
                _string_tuple(payload, "train_games"),
                _string_tuple(payload, "locked_test_games"),
                _pair_tuple(payload, "gate_statuses"),
                _string_tuple(payload, "blockers"),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ContractViolation(f"invalid qualification preflight: {exc}") from exc


_SHA256 = re.compile(r"[0-9a-f]{64}")


def probe_host(project_root: str | Path) -> HostQualificationProbe:
    root = Path(project_root).resolve()
    revision = _run_line(("git", "rev-parse", "HEAD"), root)
    gpu_output = _run_lines(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader",
        ),
        root,
    )
    return HostQualificationProbe(revision, gpu_output, _git_worktree_clean(root))


def build_qualification_preflight(
    ledger: QualificationLedger,
    project_root: str | Path,
    *,
    host: HostQualificationProbe | None = None,
    training_artifact_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    dataset_root: str | Path | None = None,
) -> QualificationPreflight:
    root = Path(project_root).resolve()
    detected = host or probe_host(root)
    licenses = tuple(sorted(path.name for path in root.glob("LICENSE*") if path.is_file()))
    artifact: TrainingArtifactManifest | None = None
    artifact_path: Path | None = None
    if training_artifact_path is not None:
        artifact_path = Path(training_artifact_path).resolve()
        artifact = TrainingArtifactManifest.load(artifact_path)
        artifact.verify(artifact_path.parent)
        artifact_dataset_path = Path(artifact.dataset_manifest).resolve()
        if (
            dataset_manifest_path is not None
            and Path(dataset_manifest_path).resolve() != artifact_dataset_path
        ):
            raise ContractViolation("training artifact and explicit Dataset Manifest do not match")
        dataset_manifest_path = artifact_dataset_path
    dataset: DatasetManifest | None = None
    resolved_dataset_path: Path | None = None
    resolved_dataset_root: Path | None = None
    if dataset_manifest_path is not None:
        resolved_dataset_path = Path(dataset_manifest_path).resolve()
        dataset = DatasetManifest.load(resolved_dataset_path)
        if dataset_root is None:
            raise ContractViolation("dataset root is required with a Dataset Manifest")
        resolved_dataset_root = Path(dataset_root).resolve()
        dataset.verify_episode_artifacts(resolved_dataset_root)
    elif dataset_root is not None:
        raise ContractViolation("Dataset Manifest is required with a dataset root")

    statuses = tuple((record.gate_id, record.status.value) for record in ledger.records)
    blockers: list[str] = []
    if detected.source_revision is None:
        blockers.append("source tree has no traceable Git revision")
    elif ledger.source_revision != detected.source_revision:
        blockers.append("qualification ledger source revision does not match Git HEAD")
    if not detected.worktree_clean:
        blockers.append("source worktree is not clean")
    if not licenses:
        blockers.append("repository license has not been selected")
    if artifact is None:
        blockers.append("verified training artifact was not supplied")
    if dataset is None:
        blockers.append("licensed Dataset Manifest was not supplied")
    else:
        if artifact is not None:
            if artifact.source_revision != dataset.source_revision:
                blockers.append("training artifact source revision does not match Dataset Manifest")
            if artifact.source_revision != ledger.source_revision:
                blockers.append(
                    "training artifact source revision does not match qualification ledger"
                )
            blockers.extend(_license_metadata_blockers(artifact, dataset))
        if dataset.source_revision != ledger.source_revision:
            blockers.append("Dataset Manifest source revision does not match qualification ledger")
        for item in dataset.licenses:
            if not item.distribution_allowed:
                blockers.append(
                    f"dataset license does not allow release distribution: {item.license_id}"
                )
            if not item.commercial_allowed:
                blockers.append(f"dataset license does not allow commercial use: {item.license_id}")
        if dataset.hours() < 5.0:
            blockers.append("dataset contains less than the required 5 hours")
        train_games = {
            episode.game_id for episode in dataset.episodes if episode.split == DatasetSplit.TRAIN
        }
        if len(train_games) < 3 or not dataset.locked_test_games:
            blockers.append("dataset does not define Train A/B/C plus a locked test game")
    for gate_id, status in statuses:
        if status != GateStatus.PASSED.value:
            blockers.append(f"qualification gate is not passed: {gate_id} ({status})")

    train_games_tuple = (
        ()
        if dataset is None
        else tuple(
            sorted(
                {
                    episode.game_id
                    for episode in dataset.episodes
                    if episode.split == DatasetSplit.TRAIN
                }
            )
        )
    )
    return QualificationPreflight(
        "uga.qualification_preflight",
        "1.1",
        ledger.releasable,
        detected.worktree_clean,
        detected.source_revision,
        detected.source_revision is not None and ledger.source_revision == detected.source_revision,
        ledger.canonical_sha256(),
        licenses,
        detected.gpu_devices,
        None if artifact is None else artifact.artifact_id,
        None if artifact_path is None else str(artifact_path),
        (
            None
            if artifact_path is None
            else sha256_file_limited(
                artifact_path,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "training artifact manifest",
            )
        ),
        None if dataset is None else dataset.dataset_id,
        None if resolved_dataset_path is None else str(resolved_dataset_path),
        (
            None
            if resolved_dataset_path is None
            else sha256_file_limited(
                resolved_dataset_path,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "Dataset Manifest",
            )
        ),
        None if resolved_dataset_root is None else str(resolved_dataset_root),
        0.0 if dataset is None else dataset.hours(),
        train_games_tuple,
        () if dataset is None else dataset.locked_test_games,
        statuses,
        tuple(blockers),
    )


def _license_metadata_blockers(
    artifact: TrainingArtifactManifest,
    dataset: DatasetManifest,
) -> tuple[str, ...]:
    metadata = dict(artifact.license_metadata)
    blockers: list[str] = []
    for item in dataset.licenses:
        expected = {
            f"dataset:{item.license_id}:source": item.source,
            f"dataset:{item.license_id}:license": item.dataset_license,
            f"dataset:{item.license_id}:distribution_allowed": str(
                item.distribution_allowed
            ).lower(),
            f"dataset:{item.license_id}:commercial_allowed": str(item.commercial_allowed).lower(),
            f"dataset:{item.license_id}:review_date": item.review_date,
        }
        if any(metadata.get(name) != value for name, value in expected.items()):
            blockers.append(
                f"training artifact license metadata does not match dataset: {item.license_id}"
            )
    return tuple(blockers)


def _run_line(command: tuple[str, ...], cwd: Path) -> str | None:
    lines = _run_lines(command, cwd)
    return lines[0] if len(lines) == 1 else None


def _run_lines(command: tuple[str, ...], cwd: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _git_worktree_clean(cwd: Path) -> bool:
    try:
        completed = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and not completed.stdout.strip()


def _string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ContractViolation(f"qualification preflight {field} is invalid")
    return value


def _optional_string(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is not None and not isinstance(value, str):
        raise ContractViolation(f"qualification preflight {field} is invalid")
    return value


def _boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if type(value) is not bool:
        raise ContractViolation(f"qualification preflight {field} is invalid")
    return value


def _number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"qualification preflight {field} is invalid")
    return float(value)


def _string_tuple(payload: dict[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractViolation(f"qualification preflight {field} is invalid")
    return tuple(value)


def _pair_tuple(payload: dict[str, Any], field: str) -> tuple[tuple[str, str], ...]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ContractViolation(f"qualification preflight {field} is invalid")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            raise ContractViolation(f"qualification preflight {field} is invalid")
        pairs.append((item[0], item[1]))
    return tuple(pairs)
