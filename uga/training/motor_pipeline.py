from __future__ import annotations

import importlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.dataset.manifest import DatasetManifest
from uga.dataset.processor import DatasetProcessor, DatasetSplit
from uga.dataset.validator import QualityStatus
from uga.policy.action_chunk import ActionButton
from uga.recording.parquet_io import read_rows
from uga.training.artifact import TrainingArtifactManifest, sha256_file
from uga.training.behavior_cloning import (
    BehaviorCloningTrainer,
    MotorTrainingSample,
    TrainingMetrics,
)


@dataclass(frozen=True, slots=True)
class MotorTrainingConfig:
    stage: str
    base_model: str
    epochs: int
    learning_rate: float
    tick_rate_hz: float
    action_horizon: int

    def __post_init__(self) -> None:
        if (
            self.stage != "motor_bc"
            or not self.base_model.strip()
            or self.epochs < 1
            or self.learning_rate <= 0
            or self.tick_rate_hz <= 0
            or self.action_horizon < 1
        ):
            raise ContractViolation("motor training configuration is invalid")


@dataclass(frozen=True, slots=True)
class MotorTrainingResult:
    output_directory: Path
    checkpoint: Path
    metrics: Path
    artifact_manifest: Path


def export_motor_samples(episode_paths: tuple[str | Path, ...], output_path: str | Path) -> Path:
    """Export provenance-preserving motor samples from canonical Episode actions."""
    if not episode_paths:
        raise ContractViolation("motor sample export requires at least one Episode")
    rows: list[str] = []
    for episode_path in episode_paths:
        episode = DatasetProcessor().process(episode_path)
        if not episode.samples:
            raise ContractViolation(f"Episode has no canonical motor samples: {episode_path}")
        for aligned in episode.samples:
            try:
                observation: Any = json.loads(aligned.observation_json)
                action: Any = json.loads(aligned.action_json)
                features = observation.get("features")
                if not isinstance(features, list):
                    raise TypeError("observation features must be a list")
                sample = MotorTrainingSample(
                    tuple(float(value) for value in features),
                    float(action["move_x"]),
                    float(action["move_y"]),
                    float(action["look_x"]),
                    float(action["look_y"]),
                    _canonical_button_mask(action),
                    aligned.episode_id,
                    aligned.observation_id,
                    aligned.action_id,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ContractViolation(
                    f"Episode contains an invalid motor sample {aligned.action_id}: {exc}"
                ) from exc
            rows.append(
                json.dumps(
                    {
                        "features": sample.features,
                        "move_x": sample.move_x,
                        "move_y": sample.move_y,
                        "look_x": sample.look_x,
                        "look_y": sample.look_y,
                        "buttons": sample.buttons,
                        "episode_id": sample.episode_id,
                        "observation_id": sample.observation_id,
                        "action_id": sample.action_id,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return destination


def load_motor_training_config(path: str | Path) -> MotorTrainingConfig:
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError("motor training config requires PyYAML") from exc
    payload: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContractViolation("motor training config root must be an object")
    try:
        return MotorTrainingConfig(
            str(payload["stage"]),
            str(payload["base_model"]),
            int(payload.get("epochs", 100)),
            float(payload.get("learning_rate", 0.05)),
            float(payload["tick_rate_hz"]),
            int(payload["action_horizon"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractViolation(f"motor training config is incomplete: {exc}") from exc


def load_motor_samples(path: str | Path) -> tuple[MotorTrainingSample, ...]:
    samples: list[MotorTrainingSample] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
            if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
                raise TypeError("sample must be an object with a features list")
            samples.append(
                MotorTrainingSample(
                    tuple(float(value) for value in payload["features"]),
                    float(payload["move_x"]),
                    float(payload["move_y"]),
                    float(payload["look_x"]),
                    float(payload["look_y"]),
                    int(payload["buttons"]),
                    str(payload["episode_id"]),
                    str(payload["observation_id"]),
                    str(payload["action_id"]),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ContractViolation) as exc:
            raise ContractViolation(
                f"invalid motor sample JSONL line {line_number}: {exc}"
            ) from exc
    if not samples:
        raise ContractViolation("motor training requires at least one sample")
    return tuple(samples)


def train_motor_policy(
    *,
    samples_path: str | Path,
    dataset_manifest_path: str | Path,
    dataset_root: str | Path,
    training_config_path: str | Path,
    output_directory: str | Path,
    policy_version: str,
    source_revision: str,
    base_model_license: str,
) -> MotorTrainingResult:
    if any(not value.strip() for value in (policy_version, source_revision, base_model_license)):
        raise ContractViolation("motor training identity/license arguments cannot be blank")
    dataset = DatasetManifest.load(dataset_manifest_path)
    dataset.verify_episode_artifacts(dataset_root)
    config = load_motor_training_config(training_config_path)
    samples = load_motor_samples(samples_path)
    _verify_training_sample_provenance(samples, dataset, dataset_root)
    checkpoint, metrics = BehaviorCloningTrainer().train(
        samples,
        policy_version=policy_version,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
    )
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=False)
    checkpoint_path = checkpoint.save(output / "decoder-checkpoint.json")
    metrics_path = _write_metrics(metrics, output / "metrics.json")
    license_metadata = tuple(
        entry
        for item in dataset.licenses
        for entry in (
            (f"dataset:{item.license_id}:source", item.source),
            (f"dataset:{item.license_id}:license", item.dataset_license),
            (
                f"dataset:{item.license_id}:distribution_allowed",
                str(item.distribution_allowed).lower(),
            ),
            (
                f"dataset:{item.license_id}:commercial_allowed",
                str(item.commercial_allowed).lower(),
            ),
            (f"dataset:{item.license_id}:review_date", item.review_date),
        )
    ) + (("base_model", base_model_license),)
    artifact = TrainingArtifactManifest(
        policy_version,
        checkpoint_path.name,
        "1.1",
        str(Path(dataset_manifest_path).resolve()),
        source_revision,
        str(Path(training_config_path).resolve()),
        str(Path(samples_path).resolve()),
        config.base_model,
        sha256_file(checkpoint_path),
        sha256_file(dataset_manifest_path),
        sha256_file(training_config_path),
        sha256_file(samples_path),
        tuple((key, float(value)) for key, value in asdict(metrics).items()),
        license_metadata,
    )
    artifact_path = artifact.write(output / "training-artifact.json")
    TrainingArtifactManifest.load(artifact_path).verify(output)
    return MotorTrainingResult(output, checkpoint_path, metrics_path, artifact_path)


def _write_metrics(metrics: TrainingMetrics, path: Path) -> Path:
    path.write_text(json.dumps(asdict(metrics), indent=2) + "\n", encoding="utf-8")
    return path


def _canonical_button_mask(payload: dict[str, Any]) -> int:
    result = 0
    for name, button in (
        ("jump", ActionButton.JUMP),
        ("sprint", ActionButton.SPRINT),
        ("crouch", ActionButton.CROUCH),
        ("interact", ActionButton.INTERACT),
        ("primary", ActionButton.PRIMARY),
        ("secondary", ActionButton.SECONDARY),
        ("menu", ActionButton.MENU),
        ("confirm", ActionButton.CONFIRM),
        ("back", ActionButton.BACK),
    ):
        if bool(payload.get(name, False)):
            result |= int(button)
    return result


def _verify_training_sample_provenance(
    samples: tuple[MotorTrainingSample, ...],
    dataset: DatasetManifest,
    dataset_root: str | Path,
) -> None:
    episodes = {episode.episode_id: episode for episode in dataset.episodes}
    grouped: dict[str, list[MotorTrainingSample]] = {}
    for sample in samples:
        if not sample.has_provenance or sample.episode_id not in episodes:
            raise ContractViolation("motor sample must reference a manifest Episode")
        episode_id = sample.episode_id
        assert episode_id is not None
        episode = episodes[episode_id]
        if episode.split != DatasetSplit.TRAIN:
            raise ContractViolation("motor training samples must come from the train split")
        if episode.quality_status != QualityStatus.ACCEPTED:
            raise ContractViolation("motor training samples require accepted-quality Episodes")
        grouped.setdefault(episode_id, []).append(sample)

    root = Path(dataset_root).resolve()
    seen_provenance: set[tuple[str, str, str]] = set()
    for episode_id, episode_samples in grouped.items():
        episode = episodes[episode_id]
        episode_path = (root / episode.relative_path).resolve()
        observations: dict[str, tuple[float, ...]] = {}
        for row in read_rows(episode_path / "observations.parquet"):
            observation_id = str(row["observation_id"])
            observation_payload = json.loads(str(row["payload_json"]))
            features = observation_payload.get("features")
            if not isinstance(features, list):
                raise ContractViolation("recorded observation has no motor features")
            if observation_id in observations:
                raise ContractViolation("Episode contains duplicate observation identifiers")
            observations[observation_id] = tuple(float(value) for value in features)
        actions = {
            str(row["action_id"]): row for row in read_rows(episode_path / "actions.parquet")
        }
        for sample in episode_samples:
            assert sample.observation_id is not None and sample.action_id is not None
            if sample.observation_id not in observations:
                raise ContractViolation("motor sample references an unknown observation")
            if sample.action_id not in actions:
                raise ContractViolation("motor sample references an unknown action")
            action = actions[sample.action_id]
            linked_observation = (
                None if action["observation_id"] is None else str(action["observation_id"])
            )
            if linked_observation != sample.observation_id:
                raise ContractViolation("motor sample action/observation provenance does not match")
            provenance_key = (episode_id, sample.observation_id, sample.action_id)
            if provenance_key in seen_provenance:
                raise ContractViolation("motor sample provenance tuple is duplicated")
            seen_provenance.add(provenance_key)
            recorded_features = observations[sample.observation_id]
            if len(recorded_features) != len(sample.features) or any(
                not math.isclose(recorded, supplied, rel_tol=0.0, abs_tol=1e-9)
                for recorded, supplied in zip(recorded_features, sample.features, strict=True)
            ):
                raise ContractViolation("motor sample features do not match the observation")
            if action.get("action_layer") != "canonical":
                raise ContractViolation("motor sample must reference a canonical action")
            payload = json.loads(str(action["payload_json"]))
            axes = ("move_x", "move_y", "look_x", "look_y")
            if any(
                not math.isclose(
                    float(payload.get(name, float("nan"))),
                    getattr(sample, name),
                    abs_tol=1e-6,
                )
                for name in axes
            ):
                raise ContractViolation("motor sample axes do not match the recorded action")
            expected_buttons = _canonical_button_mask(payload)
            if expected_buttons != sample.buttons:
                raise ContractViolation("motor sample buttons do not match the recorded action")
