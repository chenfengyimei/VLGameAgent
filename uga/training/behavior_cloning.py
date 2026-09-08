from __future__ import annotations

import math
from dataclasses import dataclass

from uga.core.errors import ContractViolation
from uga.policy.action_chunk import KNOWN_ACTION_BUTTON_MASK
from uga.policy.fast_policy import DecoderCheckpoint


@dataclass(frozen=True, slots=True)
class MotorTrainingSample:
    features: tuple[float, ...]
    move_x: float
    move_y: float
    look_x: float
    look_y: float
    buttons: int
    episode_id: str | None = None
    observation_id: str | None = None
    action_id: str | None = None

    def __post_init__(self) -> None:
        if not self.features or any(not math.isfinite(value) for value in self.features):
            raise ContractViolation("motor sample requires finite features")
        if any(
            not -1 <= axis <= 1 for axis in (self.move_x, self.move_y, self.look_x, self.look_y)
        ):
            raise ContractViolation("motor target axes must be in [-1, 1]")
        if not 0 <= self.buttons <= 0xFFFF or self.buttons & ~KNOWN_ACTION_BUTTON_MASK:
            raise ContractViolation("motor sample contains undefined canonical button bits")
        identifiers = (self.episode_id, self.observation_id, self.action_id)
        if any(value is not None and not value.strip() for value in identifiers):
            raise ContractViolation("motor sample provenance identifiers cannot be blank")
        if any(value is None for value in identifiers) and any(
            value is not None for value in identifiers
        ):
            raise ContractViolation("motor sample provenance must be complete when present")

    @property
    def has_provenance(self) -> bool:
        return self.episode_id is not None


@dataclass(frozen=True, slots=True)
class TrainingMetrics:
    epochs: int
    samples: int
    move_mse: float
    camera_huber: float
    button_accuracy: float


class BehaviorCloningTrainer:
    """Deterministic policy-head trainer used for feasibility and contract tests."""

    def train(
        self,
        samples: tuple[MotorTrainingSample, ...],
        *,
        policy_version: str,
        epochs: int = 100,
        learning_rate: float = 0.05,
    ) -> tuple[DecoderCheckpoint, TrainingMetrics]:
        if not samples or epochs < 1 or learning_rate <= 0:
            raise ContractViolation("invalid behavior-cloning training request")
        input_dim = len(samples[0].features)
        if any(len(sample.features) != input_dim for sample in samples):
            raise ContractViolation("training feature dimensions are inconsistent")
        weights = [[0.0] * input_dim for _ in range(4)]
        biases = [0.0] * 4
        for _ in range(epochs):
            for sample in samples:
                targets = (sample.move_x, sample.move_y, sample.look_x, sample.look_y)
                for axis in range(4):
                    prediction = (
                        sum(
                            weight * value
                            for weight, value in zip(weights[axis], sample.features, strict=True)
                        )
                        + biases[axis]
                    )
                    error = prediction - targets[axis]
                    for index, value in enumerate(sample.features):
                        weights[axis][index] -= learning_rate * error * value
                    biases[axis] -= learning_rate * error
        button_counts: dict[int, int] = {}
        for sample in samples:
            button_counts[sample.buttons] = button_counts.get(sample.buttons, 0) + 1
        button_mask = max(button_counts, key=button_counts.get)  # type: ignore[arg-type]
        predictions = [
            self._predict(tuple(tuple(row) for row in weights), tuple(biases), sample)
            for sample in samples
        ]
        move_errors = [
            (prediction[0] - sample.move_x) ** 2 + (prediction[1] - sample.move_y) ** 2
            for prediction, sample in zip(predictions, samples, strict=True)
        ]
        camera_errors = [
            self._huber(prediction[2] - sample.look_x) + self._huber(prediction[3] - sample.look_y)
            for prediction, sample in zip(predictions, samples, strict=True)
        ]
        accuracy = sum(sample.buttons == button_mask for sample in samples) / len(samples)
        move_mse = sum(move_errors) / (2 * len(samples))
        camera_huber = sum(camera_errors) / (2 * len(samples))
        checkpoint = DecoderCheckpoint(
            policy_version,
            input_dim,
            tuple(tuple(row) for row in weights),
            tuple(biases),  # type: ignore[arg-type]
            button_mask,
            max(0.0, min(1.0, 1.0 - move_mse - camera_huber)),
        )
        return checkpoint, TrainingMetrics(epochs, len(samples), move_mse, camera_huber, accuracy)

    @staticmethod
    def _predict(
        weights: tuple[tuple[float, ...], ...],
        biases: tuple[float, ...],
        sample: MotorTrainingSample,
    ) -> tuple[float, ...]:
        return tuple(
            sum(weight * value for weight, value in zip(row, sample.features, strict=True)) + bias
            for row, bias in zip(weights, biases, strict=True)
        )

    @staticmethod
    def _huber(error: float, delta: float = 0.1) -> float:
        absolute = abs(error)
        return 0.5 * error * error if absolute <= delta else delta * (absolute - 0.5 * delta)
