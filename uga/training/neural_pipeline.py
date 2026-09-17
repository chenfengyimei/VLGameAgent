"""Causal split verification, portable neural artifacts and explicit runtime opt-in.

Integrity is not authenticity without a trusted manifest pin. The numerical
trainer alone cannot establish Episode provenance; this orchestration does.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import read_bytes_limited, read_text_limited
from uga.core.errors import ContractViolation
from uga.dataset.manifest import DatasetManifest
from uga.dataset.processor import DatasetSplit
from uga.policy.fast_policy import StructuredFastPolicy, VisualBackbone
from uga.policy.neural_motor import MAX_CHECKPOINT_BYTES, NeuralActionDecoder, NeuralMotorCheckpoint
from uga.release.revision import validate_source_revision
from uga.training.contracts import atomic_text, identifier, strict_json
from uga.training.motor_pipeline import load_motor_samples, verify_motor_sample_provenance
from uga.training.neural_config import NeuralTrainingConfig
from uga.training.neural_motor import evaluate_head, train_neural_head, validation_reliability

DIGEST = re.compile(r"[0-9a-f]{64}")
FILE_LIMITS = {
    "model.json": MAX_CHECKPOINT_BYTES,
    "metrics.json": 1024 * 1024,
    "train.jsonl": 64 * 1024 * 1024,
    "validation.jsonl": 64 * 1024 * 1024,
    "config.json": 65536,
    "dataset-manifest.json": 16 * 1024 * 1024,
}


def digest_file(path: Path, maximum: int) -> str:
    return hashlib.sha256(read_bytes_limited(path, maximum, path.name)).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _verify_selected_sources(dataset: DatasetManifest, root: Path, selected: set[str]) -> None:
    # Training never opens held-out Test Episode contents.
    subset = replace(
        dataset, episodes=tuple(e for e in dataset.episodes if e.episode_id in selected)
    )
    if {e.episode_id for e in subset.episodes} != selected:
        raise ContractViolation("sample references an unknown manifest Episode")
    subset.verify_episode_artifacts(root)


def _validate_output(
    output: Path, dataset: DatasetManifest, root: Path, *, extra_inputs: tuple[Path, ...] = ()
) -> None:
    for episode in dataset.episodes:
        source = (root / episode.relative_path).resolve()
        if output == source or source in output.parents:
            raise ContractViolation("training output must be outside every source Episode")
    if output in extra_inputs or any(output in path.parents for path in extra_inputs):
        raise ContractViolation("training output cannot replace an input or its parent")
    if output.exists():
        raise ContractViolation("training output must not already exist")


def train_neural_motor(
    *,
    train_path: str | Path,
    validation_path: str | Path,
    dataset_manifest_path: str | Path,
    dataset_root: str | Path,
    config_path: str | Path,
    output_directory: str | Path,
    policy_version: str,
    encoder_version: str,
    encoder_license: str,
    source_revision: str,
) -> Path:
    validate_source_revision(source_revision)
    for name, value in (
        ("policy_version", policy_version),
        ("encoder_version", encoder_version),
        ("encoder_license", encoder_license),
    ):
        identifier(value, name)
    output, root = Path(output_directory).resolve(), Path(dataset_root).resolve()
    inputs = {
        "train.jsonl": Path(train_path).resolve(),
        "validation.jsonl": Path(validation_path).resolve(),
        "dataset-manifest.json": Path(dataset_manifest_path).resolve(),
        "config.json": Path(config_path).resolve(),
    }
    if inputs["train.jsonl"] == inputs["validation.jsonl"]:
        raise ContractViolation("train and validation files must be distinct")
    dataset = DatasetManifest.load(inputs["dataset-manifest.json"])
    _validate_output(output, dataset, root, extra_inputs=tuple(inputs.values()))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".uga-neural-", dir=output.parent))
    owned_output = False
    try:
        for name, source in inputs.items():
            (staging / name).write_bytes(read_bytes_limited(source, FILE_LIMITS[name], name))
        dataset = DatasetManifest.load(staging / "dataset-manifest.json")
        validate_source_revision(dataset.source_revision)
        _validate_output(output, dataset, root, extra_inputs=tuple(inputs.values()))
        config = NeuralTrainingConfig.load(staging / "config.json")
        train = load_motor_samples(staging / "train.jsonl")
        validation = load_motor_samples(staging / "validation.jsonl")
        selected = {str(s.episode_id) for s in (*train, *validation)}
        _verify_selected_sources(dataset, root, selected)
        verify_motor_sample_provenance(train, dataset, root, expected_split=DatasetSplit.TRAIN)
        verify_motor_sample_provenance(
            validation, dataset, root, expected_split=DatasetSplit.VALIDATION
        )
        model, metrics = train_neural_head(
            train,
            validation,
            policy_version=policy_version,
            encoder_version=encoder_version,
            config=config,
        )
        _verify_selected_sources(dataset, root, selected)
        model.save(staging / "model.json")
        metrics.update(
            source_revision=source_revision, dataset_source_revision=dataset.source_revision
        )
        atomic_text(staging / "metrics.json", _json(metrics))
        artifact = {
            "schema": "uga.neural_motor_artifact",
            "schema_version": "1.0",
            "policy_version": policy_version,
            "encoder_version": encoder_version,
            "encoder_license": encoder_license,
            "source_revision": source_revision,
            "dataset_source_revision": dataset.source_revision,
            "stage": "motor",
            "release_qualified": False,
            "encoder_identity_source": "operator_attested; recorded_feature_values_verified",
            "files": {
                name: digest_file(staging / name, limit) for name, limit in FILE_LIMITS.items()
            },
        }
        atomic_text(staging / "artifact.json", _json(artifact))
        verify_neural_artifact(staging / "artifact.json")
        # Exclusive reservation never replaces an existing empty directory.
        # The completion marker is moved last, after all validated components.
        output.mkdir(exist_ok=False)
        owned_output = True
        for name in FILE_LIMITS:
            (staging / name).replace(output / name)
        (staging / "artifact.json").replace(output / "artifact.json")
        return output / "artifact.json"
    except BaseException:
        if owned_output:
            shutil.rmtree(output)
        raise
    finally:
        shutil.rmtree(staging)


def _metrics_equal(stored: Any, actual: dict[str, Any]) -> bool:
    if not isinstance(stored, dict) or set(stored) != set(actual):
        return False
    exact = {"samples", "action_accuracy", "button_accuracy", "abstention_rate"}
    for key, value in actual.items():
        supplied = stored[key]
        if type(supplied) not in (int, float) or not math.isfinite(supplied):
            return False
        if key in exact:
            if supplied != value:
                return False
        elif not math.isclose(supplied, value, rel_tol=1e-9, abs_tol=1e-10):
            return False
    return True


def _verified_neural_artifact(
    path: str | Path, *, expected_sha256: str | None = None
) -> tuple[dict[str, Any], NeuralMotorCheckpoint, DatasetManifest, str]:
    artifact_path = Path(path).resolve()
    raw = read_bytes_limited(artifact_path, 65536, "neural artifact manifest")
    manifest_digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or DIGEST.fullmatch(expected_sha256) is None
        or manifest_digest != expected_sha256
    ):
        raise ContractViolation("neural artifact manifest digest mismatch")
    try:
        data = strict_json(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ContractViolation("neural artifact is not UTF-8") from exc
    keys = {
        "schema",
        "schema_version",
        "policy_version",
        "encoder_version",
        "encoder_license",
        "source_revision",
        "dataset_source_revision",
        "stage",
        "release_qualified",
        "encoder_identity_source",
        "files",
    }
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or data["schema"] != "uga.neural_motor_artifact"
        or data["schema_version"] != "1.0"
        or data["stage"] != "motor"
        or data["release_qualified"] is not False
    ):
        raise ContractViolation("unsupported neural artifact; no release qualification is implied")
    for key in ("policy_version", "encoder_version", "encoder_license", "encoder_identity_source"):
        identifier(data[key], key)
    for key in ("source_revision", "dataset_source_revision"):
        if not isinstance(data[key], str):
            raise ContractViolation("invalid neural source revision")
        validate_source_revision(data[key])
    if not isinstance(data["files"], dict) or set(data["files"]) != set(FILE_LIMITS):
        raise ContractViolation("neural artifact file inventory must match the fixed contract")
    snapshots: dict[str, bytes] = {}
    root = artifact_path.parent
    for name, limit in FILE_LIMITS.items():
        member = root / name
        if member.is_symlink() or member.resolve().parent != root:
            raise ContractViolation("neural artifact files must not escape or use symlinks")
        snapshots[name] = read_bytes_limited(member, limit, name)
        digest = data["files"][name]
        if (
            not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or hashlib.sha256(snapshots[name]).hexdigest() != digest
        ):
            raise ContractViolation(f"neural artifact file digest mismatch: {name}")
    # Parse only private snapshots of the exact bytes whose digests were checked.
    # The verified model is returned, never reread from a mutable source path.
    with tempfile.TemporaryDirectory(prefix="uga-neural-verify-") as directory:
        root = Path(directory)
        for name, content in snapshots.items():
            (root / name).write_bytes(content)
        model = NeuralMotorCheckpoint.load(root / "model.json")
        if (
            model.policy_version != data["policy_version"]
            or model.encoder_version != data["encoder_version"]
        ):
            raise ContractViolation("neural checkpoint identity differs from artifact")
        config = NeuralTrainingConfig.load(root / "config.json")
        if config.hidden_dim != model.hidden_dim:
            raise ContractViolation("neural model dimensions differ from training config")
        dataset = DatasetManifest.load(root / "dataset-manifest.json")
        if dataset.source_revision != data["dataset_source_revision"]:
            raise ContractViolation("neural artifact dataset revision mismatch")
        train = load_motor_samples(root / "train.jsonl")
        validation = load_motor_samples(root / "validation.jsonl")
        columns = tuple(zip(*(sample.features for sample in train), strict=True))
        expected_statistics = (
            tuple(statistics.fmean(c) for c in columns),
            tuple(max(1e-6, statistics.pstdev(c)) for c in columns),
            tuple(min(c) for c in columns),
            tuple(max(c) for c in columns),
        )
        for stored, expected in zip(
            (model.mean, model.scale, model.lower, model.upper), expected_statistics, strict=True
        ):
            if len(stored) != len(expected) or any(
                not math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
                for x, y in zip(stored, expected, strict=True)
            ):
                raise ContractViolation("neural normalization differs from train-only statistics")
        episode_splits = {e.episode_id: e.split for e in dataset.episodes}
        seen: set[tuple[str | None, str | None, str | None]] = set()
        for samples, split in ((train, DatasetSplit.TRAIN), (validation, DatasetSplit.VALIDATION)):
            for sample in samples:
                sample_key = (sample.episode_id, sample.observation_id, sample.action_id)
                if sample_key in seen or episode_splits.get(str(sample.episode_id)) != split:
                    raise ContractViolation(
                        "neural artifact sample leakage or duplicated provenance"
                    )
                seen.add(sample_key)
                if len(sample.features) != model.input_dim:
                    raise ContractViolation("neural artifact sample dimension mismatch")
        metrics = strict_json(
            read_text_limited(root / "metrics.json", FILE_LIMITS["metrics.json"], "metrics")
        )
        if (
            not isinstance(metrics, dict)
            or metrics.get("schema") != "uga.neural_motor_training"
            or metrics.get("stage") != "motor"
            or metrics.get("seed") != config.seed
            or metrics.get("device") != config.device
            or metrics.get("source_revision") != data["source_revision"]
            or metrics.get("dataset_source_revision") != dataset.source_revision
            or metrics.get("release_qualified") is not False
            or metrics.get("test_split_used") is not False
        ):
            raise ContractViolation("neural metrics metadata is inconsistent")
        for name, samples in (("train", train), ("validation", validation)):
            actual = asdict(evaluate_head(model, samples))
            if not _metrics_equal(metrics.get(name), actual):
                raise ContractViolation(f"neural {name} metrics do not match the exported model")
        expected_reliability = validation_reliability(
            metrics["validation"]["action_accuracy"], len(validation)
        )
        if not math.isclose(model.validation_reliability, expected_reliability, abs_tol=1e-12):
            raise ContractViolation("neural confidence does not match validation evidence")
        return data, model, dataset, manifest_digest


def verify_neural_artifact(
    path: str | Path, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    return _verified_neural_artifact(path, expected_sha256=expected_sha256)[0]


def load_neural_fast_policy(
    path: str | Path,
    backbone: VisualBackbone,
    *,
    expected_sha256: str,
    allow_development_model: bool = False,
) -> StructuredFastPolicy:
    if allow_development_model is not True:
        raise ContractViolation("neural motor head requires explicit development-model opt-in")
    # Unlike offline inspection, deployment requires an external identity pin.
    if not isinstance(expected_sha256, str) or DIGEST.fullmatch(expected_sha256) is None:
        raise ContractViolation("runtime loading requires a trusted manifest digest")
    data, model, _, _ = _verified_neural_artifact(path, expected_sha256=expected_sha256)
    if backbone.backbone_version != data["encoder_version"]:
        raise ContractViolation("runtime encoder identity does not match neural model")
    return StructuredFastPolicy(backbone, NeuralActionDecoder(model))


def evaluate_neural_artifact(
    *,
    artifact_path: str | Path,
    samples_path: str | Path,
    dataset_root: str | Path,
    split: DatasetSplit,
    output_path: str | Path,
) -> Path:
    if split not in (DatasetSplit.VALIDATION, DatasetSplit.TEST):
        raise ContractViolation("offline evaluation requires validation or test split")
    artifact_path, output = Path(artifact_path).resolve(), Path(output_path).resolve()
    artifact, model, dataset, artifact_digest = _verified_neural_artifact(artifact_path)
    folder = artifact_path.parent
    _validate_output(
        output,
        dataset,
        Path(dataset_root).resolve(),
        extra_inputs=(Path(samples_path).resolve(), artifact_path),
    )
    if output == folder or folder in output.parents:
        raise ContractViolation("evaluation must not mutate the training artifact")
    sample_bytes = read_bytes_limited(
        samples_path, FILE_LIMITS["validation.jsonl"], "evaluation samples"
    )
    with tempfile.TemporaryDirectory(prefix="uga-neural-eval-") as directory:
        sample_snapshot = Path(directory) / "samples.jsonl"
        sample_snapshot.write_bytes(sample_bytes)
        samples = load_motor_samples(sample_snapshot)
    _verify_selected_sources(
        dataset, Path(dataset_root).resolve(), {str(s.episode_id) for s in samples}
    )
    verify_motor_sample_provenance(samples, dataset, dataset_root, expected_split=split)
    report = {
        "schema": "uga.neural_motor_evaluation",
        "schema_version": "1.0",
        "artifact_sha256": artifact_digest,
        "samples_sha256": hashlib.sha256(sample_bytes).hexdigest(),
        "source_revision": artifact["source_revision"],
        "split": split.value,
        "metrics": asdict(evaluate_head(model, samples)),
        "release_qualified": False,
    }
    return atomic_text(output, _json(report))
