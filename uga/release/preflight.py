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
    dataset_manifest_path: str | Path | None = None,
    dataset_root: str | Path | None = None,
) -> QualificationPreflight:
    root = Path(project_root).resolve()
    detected = host or probe_host(root)
    licenses = tuple(sorted(path.name for path in root.glob("LICENSE*") if path.is_file()))
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
    if dataset is None:
        blockers.append("licensed Dataset Manifest was not supplied")
    else:
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
        None if dataset is None else dataset.dataset_id,
        0.0 if dataset is None else dataset.hours(),
        train_games_tuple,
        () if dataset is None else dataset.locked_test_games,
        statuses,
        tuple(blockers),
    )


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
