from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    parse_json_text,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.dataset.manifest import DatasetManifest
from uga.release.revision import validate_source_revision
from uga.training.artifact import TrainingArtifactManifest

MODEL_STAGES = ("motor", "instruction", "recovery", "reasoning_gate", "dagger")
_OPERATORS = {
    ">=": lambda observed, threshold: observed >= threshold,
    "<=": lambda observed, threshold: observed <= threshold,
    ">": lambda observed, threshold: observed > threshold,
    "<": lambda observed, threshold: observed < threshold,
}


@dataclass(frozen=True, slots=True)
class VerifiedModelQualification:
    dataset_manifest_path: Path
    artifacts: tuple[TrainingArtifactManifest, ...]


def build_model_stage_report(
    *,
    stage: str,
    dataset_manifest_path: str | Path,
    artifact_manifest_path: str | Path,
    offline_metrics_path: str | Path,
    closed_loop_metrics_path: str | Path,
    trainer_backend: str,
    training_run_id: str,
    output_path: str | Path,
    source_revision: str,
    gpu_devices: tuple[str, ...] | None = None,
) -> Path:
    validate_source_revision(source_revision)
    if stage not in MODEL_STAGES:
        raise ContractViolation(f"unsupported model qualification stage: {stage}")
    if not trainer_backend.strip() or not training_run_id.strip():
        raise ContractViolation("model stage requires trainer backend and run id")
    devices = _probe_gpu_devices() if gpu_devices is None else gpu_devices
    if not devices or any(not item.strip() for item in devices):
        raise ContractViolation("model stage qualification requires detected GPU devices")

    destination = Path(output_path).resolve()
    root = destination.parent
    dataset_path = Path(dataset_manifest_path).resolve()
    artifact_path = Path(artifact_manifest_path).resolve()
    offline_path = Path(offline_metrics_path).resolve()
    closed_path = Path(closed_loop_metrics_path).resolve()
    references = {
        "dataset_manifest": dataset_path,
        "artifact_manifest": artifact_path,
        "offline_metrics": offline_path,
        "closed_loop_metrics": closed_path,
    }
    relative = {name: _relative(path, root, name) for name, path in references.items()}
    digests = {name: _digest(path, name) for name, path in references.items()}

    dataset = DatasetManifest.load(dataset_path)
    if dataset.source_revision != source_revision:
        raise ContractViolation("model stage Dataset Manifest revision does not match source")
    artifact = TrainingArtifactManifest.load(artifact_path)
    artifact.verify(artifact_path.parent)
    if artifact.source_revision != source_revision:
        raise ContractViolation("model stage training artifact revision does not match source")
    if artifact.dataset_manifest_sha256 != digests["dataset_manifest"]:
        raise ContractViolation("model stage artifact does not bind the supplied Dataset Manifest")
    _validate_metrics_report(
        offline_path,
        expected_schema="uga.offline_metrics",
        count_field="samples",
        expected_stage=stage,
        expected_revision=source_revision,
        dataset_digest=digests["dataset_manifest"],
        artifact_digest=digests["artifact_manifest"],
    )
    _validate_metrics_report(
        closed_path,
        expected_schema="uga.closed_loop_metrics",
        count_field="episodes",
        expected_stage=stage,
        expected_revision=source_revision,
        dataset_digest=digests["dataset_manifest"],
        artifact_digest=digests["artifact_manifest"],
        require_games=True,
    )
    payload = {
        "schema": "uga.model_stage_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "stage": stage,
        "passed": True,
        "trainer_backend": trainer_backend,
        "training_run_id": training_run_id,
        "gpu_devices": list(devices),
        **{
            name: relative[name]
            for name in (
                "dataset_manifest",
                "artifact_manifest",
                "offline_metrics",
                "closed_loop_metrics",
            )
        },
        **{f"{name}_sha256": digest for name, digest in digests.items()},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def build_model_qualification_report(
    *,
    dataset_manifest_path: str | Path,
    stage_report_paths: tuple[str | Path, ...],
    output_path: str | Path,
    source_revision: str,
) -> Path:
    validate_source_revision(source_revision)
    destination = Path(output_path).resolve()
    root = destination.parent
    dataset_path = Path(dataset_manifest_path).resolve()
    dataset_relative = _relative(dataset_path, root, "dataset_manifest")
    dataset_digest = _digest(dataset_path, "Dataset Manifest")
    dataset = DatasetManifest.load(dataset_path)
    if dataset.source_revision != source_revision:
        raise ContractViolation(
            "model qualification Dataset Manifest revision does not match source"
        )

    reports: dict[str, tuple[Path, dict[str, Any]]] = {}
    for supplied in stage_report_paths:
        stage_path = Path(supplied).resolve()
        _relative(stage_path, root, "stage_report")
        stage_payload = validate_model_stage_report(
            stage_path,
            expected_revision=source_revision,
            expected_dataset_digest=dataset_digest,
        )
        stage = str(stage_payload["stage"])
        if stage in reports:
            raise ContractViolation(f"duplicate model qualification stage: {stage}")
        reports[stage] = (stage_path, stage_payload)
    if set(reports) != set(MODEL_STAGES):
        raise ContractViolation("model qualification requires exactly all five training stages")

    devices = sorted(
        {
            device
            for _, payload in reports.values()
            for device in payload["gpu_devices"]
        }
    )
    stages = []
    for stage in MODEL_STAGES:
        stage_path, stage_payload = reports[stage]
        stages.append(
            {
                "stage": stage,
                "passed": True,
                "report": _relative(stage_path, root, "stage_report"),
                "report_sha256": _digest(stage_path, "model stage report"),
                "artifact_sha256": stage_payload["artifact_manifest_sha256"],
                "offline_metrics_sha256": stage_payload["offline_metrics_sha256"],
                "closed_loop_metrics_sha256": stage_payload["closed_loop_metrics_sha256"],
            }
        )
    payload = {
        "schema": "uga.model_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "passed": True,
        "gpu_devices": devices,
        "dataset_manifest": dataset_relative,
        "dataset_manifest_sha256": dataset_digest,
        "stages": stages,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def validate_model_qualification_report(
    path: str | Path,
    *,
    expected_revision: str,
) -> dict[str, Any]:
    report_path = Path(path).resolve()
    payload = _read_object(report_path, "model qualification report")
    if (
        payload.get("schema") != "uga.model_qualification"
        or payload.get("schema_version") != "1.1"
        or payload.get("source_revision") != expected_revision
        or payload.get("passed") is not True
    ):
        raise ContractViolation("model qualification report envelope is invalid")
    devices = payload.get("gpu_devices")
    stages = payload.get("stages")
    if (
        not isinstance(devices, list)
        or not devices
        or any(not isinstance(item, str) or not item.strip() for item in devices)
        or not isinstance(stages, list)
        or len(stages) != len(MODEL_STAGES)
    ):
        raise ContractViolation("model qualification hardware or stage list is invalid")
    dataset_path = _bound_reference(
        report_path,
        payload,
        "dataset_manifest",
        "Dataset Manifest",
    )
    dataset_digest = str(payload["dataset_manifest_sha256"])
    dataset = DatasetManifest.load(dataset_path)
    if dataset.source_revision != expected_revision:
        raise ContractViolation("model qualification Dataset Manifest revision is invalid")

    seen: set[str] = set()
    reported_devices: set[str] = set()
    for item in stages:
        if not isinstance(item, dict) or item.get("passed") is not True:
            raise ContractViolation("model qualification stage entry is invalid")
        stage = item.get("stage")
        if not isinstance(stage, str) or stage not in MODEL_STAGES or stage in seen:
            raise ContractViolation("model qualification stages are invalid")
        seen.add(stage)
        stage_path = _bound_reference(report_path, item, "report", "model stage report")
        stage_payload = validate_model_stage_report(
            stage_path,
            expected_revision=expected_revision,
            expected_dataset_digest=dataset_digest,
        )
        if stage_payload.get("stage") != stage:
            raise ContractViolation("model qualification stage report identity differs")
        for aggregate_field, stage_field in (
            ("artifact_sha256", "artifact_manifest_sha256"),
            ("offline_metrics_sha256", "offline_metrics_sha256"),
            ("closed_loop_metrics_sha256", "closed_loop_metrics_sha256"),
        ):
            if item.get(aggregate_field) != stage_payload.get(stage_field):
                raise ContractViolation("model qualification stage digest differs")
        reported_devices.update(str(device) for device in stage_payload["gpu_devices"])
    if seen != set(MODEL_STAGES) or set(devices) != reported_devices:
        raise ContractViolation("model qualification aggregate does not match stage reports")
    return payload


def load_model_qualification_inputs(
    path: str | Path,
    *,
    expected_revision: str,
) -> VerifiedModelQualification:
    report_path = Path(path).resolve()
    payload = validate_model_qualification_report(
        report_path,
        expected_revision=expected_revision,
    )
    dataset_path = _bound_reference(
        report_path,
        payload,
        "dataset_manifest",
        "Dataset Manifest",
    )
    artifacts: list[TrainingArtifactManifest] = []
    for stage in payload["stages"]:
        stage_path = _bound_reference(report_path, stage, "report", "model stage report")
        stage_payload = _read_object(stage_path, "model stage report")
        artifact_path = _bound_reference(
            stage_path,
            stage_payload,
            "artifact_manifest",
            "training artifact manifest",
        )
        artifacts.append(TrainingArtifactManifest.load(artifact_path))
    return VerifiedModelQualification(dataset_path, tuple(artifacts))


def validate_model_stage_report(
    path: str | Path,
    *,
    expected_revision: str,
    expected_dataset_digest: str,
) -> dict[str, Any]:
    report_path = Path(path).resolve()
    payload = _read_object(report_path, "model stage report")
    stage = payload.get("stage")
    devices = payload.get("gpu_devices")
    if (
        payload.get("schema") != "uga.model_stage_qualification"
        or payload.get("schema_version") != "1.1"
        or payload.get("source_revision") != expected_revision
        or payload.get("passed") is not True
        or not isinstance(stage, str)
        or stage not in MODEL_STAGES
        or not isinstance(payload.get("trainer_backend"), str)
        or not payload["trainer_backend"].strip()
        or not isinstance(payload.get("training_run_id"), str)
        or not payload["training_run_id"].strip()
        or not isinstance(devices, list)
        or not devices
        or any(not isinstance(item, str) or not item.strip() for item in devices)
    ):
        raise ContractViolation("model stage qualification envelope is invalid")
    dataset_path = _bound_reference(report_path, payload, "dataset_manifest", "Dataset Manifest")
    if payload.get("dataset_manifest_sha256") != expected_dataset_digest:
        raise ContractViolation("model stage Dataset Manifest digest differs")
    dataset = DatasetManifest.load(dataset_path)
    if dataset.source_revision != expected_revision:
        raise ContractViolation("model stage Dataset Manifest revision differs")
    artifact_path = _bound_reference(
        report_path, payload, "artifact_manifest", "training artifact manifest"
    )
    artifact = TrainingArtifactManifest.load(artifact_path)
    artifact.verify(artifact_path.parent)
    if (
        artifact.source_revision != expected_revision
        or artifact.dataset_manifest_sha256 != expected_dataset_digest
    ):
        raise ContractViolation("model stage training artifact binding differs")
    artifact_digest = str(payload["artifact_manifest_sha256"])
    offline_path = _bound_reference(report_path, payload, "offline_metrics", "offline metrics")
    closed_path = _bound_reference(
        report_path, payload, "closed_loop_metrics", "closed-loop metrics"
    )
    _validate_metrics_report(
        offline_path,
        expected_schema="uga.offline_metrics",
        count_field="samples",
        expected_stage=stage,
        expected_revision=expected_revision,
        dataset_digest=expected_dataset_digest,
        artifact_digest=artifact_digest,
    )
    _validate_metrics_report(
        closed_path,
        expected_schema="uga.closed_loop_metrics",
        count_field="episodes",
        expected_stage=stage,
        expected_revision=expected_revision,
        dataset_digest=expected_dataset_digest,
        artifact_digest=artifact_digest,
        require_games=True,
    )
    return payload


def _validate_metrics_report(
    path: Path,
    *,
    expected_schema: str,
    count_field: str,
    expected_stage: str,
    expected_revision: str,
    dataset_digest: str,
    artifact_digest: str,
    require_games: bool = False,
) -> None:
    payload = _read_object(path, expected_schema)
    metrics = payload.get("metrics")
    checks = payload.get("checks")
    count = payload.get(count_field)
    if (
        payload.get("schema") != expected_schema
        or payload.get("schema_version") != "1.1"
        or payload.get("source_revision") != expected_revision
        or payload.get("stage") != expected_stage
        or payload.get("dataset_manifest_sha256") != dataset_digest
        or payload.get("artifact_manifest_sha256") != artifact_digest
        or payload.get("passed") is not True
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
        or not isinstance(metrics, dict)
        or not metrics
        or not isinstance(checks, list)
        or not checks
    ):
        raise ContractViolation(f"{expected_schema} report is incomplete")
    numeric_metrics = {
        name: float(value)
        for name, value in metrics.items()
        if isinstance(name, str)
        and name.strip()
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    if len(numeric_metrics) != len(metrics):
        raise ContractViolation(f"{expected_schema} metrics are invalid")
    names: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ContractViolation(f"{expected_schema} threshold check is invalid")
        name = check.get("metric")
        operator = check.get("operator")
        threshold = check.get("threshold")
        if (
            not isinstance(name, str)
            or name in names
            or name not in numeric_metrics
            or operator not in _OPERATORS
            or not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(float(threshold))
            or not _OPERATORS[str(operator)](numeric_metrics[name], float(threshold))
        ):
            raise ContractViolation(f"{expected_schema} threshold check did not pass")
        names.add(name)
    if require_games:
        games = payload.get("games")
        if (
            not isinstance(games, list)
            or not games
            or len(set(item for item in games if isinstance(item, str) and item.strip()))
            != len(games)
        ):
            raise ContractViolation("closed-loop metrics require distinct games")


def _bound_reference(report_path: Path, payload: dict[str, Any], field: str, label: str) -> Path:
    relative = payload.get(field)
    expected_digest = payload.get(f"{field}_sha256")
    if not isinstance(relative, str) or not isinstance(expected_digest, str):
        raise ContractViolation(f"{label} binding is incomplete")
    candidate = (report_path.parent / relative).resolve()
    if report_path.parent not in candidate.parents or not candidate.is_file():
        raise ContractViolation(f"{label} path is unsafe or missing")
    if _digest(candidate, label) != expected_digest:
        raise ContractViolation(f"{label} digest mismatch")
    return candidate


def _relative(path: Path, root: Path, label: str) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ContractViolation(f"{label} must be beneath the report evidence directory") from exc
    if not path.is_file():
        raise ContractViolation(f"{label} is missing: {path}")
    return relative


def _digest(path: Path, label: str) -> str:
    return sha256_file_limited(
        path,
        DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
        label,
    )


def _read_object(path: Path, label: str) -> dict[str, Any]:
    payload: Any = parse_json_text(
        read_text_limited(path, DEFAULT_ARTIFACT_LIMITS.max_document_bytes, label)
    )
    if not isinstance(payload, dict):
        raise ContractViolation(f"{label} must be an object")
    return payload


def _probe_gpu_devices() -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            (
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ),
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
