from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class QualificationPreflight:
    schema: str
    schema_version: str
    ledger_releasable: bool
    source_revision: str | None
    source_revision_matches_ledger: bool
    license_files: tuple[str, ...]
    gpu_devices: tuple[str, ...]
    training_artifact_id: str | None
    dataset_id: str | None
    dataset_hours: float
    train_games: tuple[str, ...]
    locked_test_games: tuple[str, ...]
    gate_statuses: tuple[tuple[str, str], ...]
    blockers: tuple[str, ...]

    def write(self, path: str | Path) -> Path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return destination


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
    return HostQualificationProbe(revision, gpu_output)


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
    if dataset_manifest_path is not None:
        dataset = DatasetManifest.load(dataset_manifest_path)
        if dataset_root is None:
            raise ContractViolation("dataset root is required with a Dataset Manifest")
        dataset.verify_episode_artifacts(dataset_root)
    elif dataset_root is not None:
        raise ContractViolation("Dataset Manifest is required with a dataset root")

    statuses = tuple((record.gate_id, record.status.value) for record in ledger.records)
    blockers: list[str] = []
    if detected.source_revision is None:
        blockers.append("source tree has no traceable Git revision")
    elif ledger.source_revision != detected.source_revision:
        blockers.append("qualification ledger source revision does not match Git HEAD")
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
        detected.source_revision,
        detected.source_revision is not None and ledger.source_revision == detected.source_revision,
        licenses,
        detected.gpu_devices,
        None if artifact is None else artifact.artifact_id,
        None if dataset is None else dataset.dataset_id,
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
