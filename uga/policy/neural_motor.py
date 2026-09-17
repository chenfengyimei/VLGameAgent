"""Numeric-only motor MLP inference; no torch, pickle or input backend.

The model maps recorded features to actions, not pixels or language. Multi-tick
chunks repeat one prediction, rather than claiming learned temporal planning.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import read_text_limited
from uga.core.errors import ContractViolation
from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.policy.fast_policy import PolicyContext
from uga.time.clock import UGATime
from uga.training.contracts import atomic_text, finite_number, identifier, integer, strict_json

BUTTON_FLAGS = tuple(int(button) for button in ActionButton)
OUTPUT_DIM = 4 + len(BUTTON_FLAGS)
MAX_PARAMETERS = 250_000
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


def vector(value: Any, size: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ContractViolation(f"{name} has invalid shape")
    return tuple(finite_number(item, name, maximum=1e6) for item in value)


def matrix(value: Any, rows: int, columns: int, name: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, (list, tuple)) or len(value) != rows:
        raise ContractViolation(f"{name} has invalid shape")
    return tuple(vector(row, columns, name) for row in value)


@dataclass(frozen=True, slots=True)
class NeuralPrediction:
    axes: tuple[float, float, float, float]
    buttons: int
    confidence: float
    out_of_distribution: bool


@dataclass(frozen=True, slots=True)
class NeuralMotorCheckpoint:
    policy_version: str
    encoder_version: str
    input_dim: int
    hidden_dim: int
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    hidden_weight: tuple[tuple[float, ...], ...]
    hidden_bias: tuple[float, ...]
    output_weight: tuple[tuple[float, ...], ...]
    output_bias: tuple[float, ...]
    validation_reliability: float = 0.0

    def __post_init__(self) -> None:
        identifier(self.policy_version, "policy_version")
        identifier(self.encoder_version, "encoder_version")
        integer(self.input_dim, "input_dim", 1, 8192)
        integer(self.hidden_dim, "hidden_dim", 1, 256)
        if self.parameter_count > MAX_PARAMETERS:
            raise ContractViolation("neural checkpoint parameter budget exceeded")
        for name in ("mean", "scale", "lower", "upper"):
            object.__setattr__(self, name, vector(getattr(self, name), self.input_dim, name))
        object.__setattr__(
            self,
            "hidden_weight",
            matrix(self.hidden_weight, self.hidden_dim, self.input_dim, "hidden_weight"),
        )
        object.__setattr__(
            self, "hidden_bias", vector(self.hidden_bias, self.hidden_dim, "hidden_bias")
        )
        object.__setattr__(
            self,
            "output_weight",
            matrix(self.output_weight, OUTPUT_DIM, self.hidden_dim, "output_weight"),
        )
        object.__setattr__(self, "output_bias", vector(self.output_bias, OUTPUT_DIM, "output_bias"))
        if any(value < 1e-6 for value in self.scale):
            raise ContractViolation("normalization scale must be at least 1e-6")
        if any(
            not lo <= avg <= hi
            for lo, avg, hi in zip(self.lower, self.mean, self.upper, strict=True)
        ):
            raise ContractViolation("normalization mean must lie within training support")
        if not 0 <= finite_number(self.validation_reliability, "reliability") <= 1:
            raise ContractViolation("validation reliability must be in [0, 1]")

    @property
    def parameter_count(self) -> int:
        return self.hidden_dim * (self.input_dim + 1) + OUTPUT_DIM * (self.hidden_dim + 1)

    def raw_predict(self, features: tuple[float, ...]) -> tuple[tuple[float, ...], bool]:
        values = vector(features, self.input_dim, "features")
        outside = any(
            not lo - max(1e-6, 0.05 * (hi - lo)) <= value <= hi + max(1e-6, 0.05 * (hi - lo))
            for value, lo, hi in zip(values, self.lower, self.upper, strict=True)
        )
        normalized = tuple(
            max(-8.0, min(8.0, (x - m) / s))
            for x, m, s in zip(values, self.mean, self.scale, strict=True)
        )
        hidden = tuple(
            math.tanh(math.fsum(w * x for w, x in zip(row, normalized, strict=True)) + b)
            for row, b in zip(self.hidden_weight, self.hidden_bias, strict=True)
        )
        logits = tuple(
            math.fsum(w * x for w, x in zip(row, hidden, strict=True)) + b
            for row, b in zip(self.output_weight, self.output_bias, strict=True)
        )
        return logits, outside

    def predict(self, features: tuple[float, ...]) -> NeuralPrediction:
        logits, outside = self.raw_predict(features)
        if outside:
            return NeuralPrediction((0.0, 0.0, 0.0, 0.0), 0, 0.0, True)
        axes = tuple(math.tanh(value) for value in logits[:4])
        probabilities = tuple(
            1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value)))) for value in logits[4:]
        )
        mask = sum(flag for flag, p in zip(BUTTON_FLAGS, probabilities, strict=True) if p > 0.5)
        # Aggregate validation lower bound times decision margin; not a
        # calibrated per-frame safety or goal-completion probability.
        confidence = self.validation_reliability * min(abs(p - 0.5) * 2 for p in probabilities)
        return NeuralPrediction((axes[0], axes[1], axes[2], axes[3]), mask, confidence, False)

    def save(self, path: str | Path) -> Path:
        text = (
            json.dumps(
                {
                    "schema": "uga.neural_motor_checkpoint",
                    "schema_version": "1.0",
                    "data": asdict(self),
                },
                allow_nan=False,
                sort_keys=True,
            )
            + "\n"
        )
        if len(text.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ContractViolation("neural checkpoint byte budget exceeded")
        return atomic_text(Path(path), text)

    @classmethod
    def load(cls, path: str | Path) -> NeuralMotorCheckpoint:
        payload = strict_json(read_text_limited(path, MAX_CHECKPOINT_BYTES, "neural checkpoint"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "schema_version", "data"}
            or payload["schema"] != "uga.neural_motor_checkpoint"
            or payload["schema_version"] != "1.0"
            or not isinstance(payload["data"], dict)
        ):
            raise ContractViolation("unsupported neural checkpoint envelope")
        try:
            return cls(**payload["data"])
        except (TypeError, KeyError) as exc:
            raise ContractViolation("invalid neural checkpoint fields") from exc


class NeuralActionDecoder:
    def __init__(
        self,
        checkpoint: NeuralMotorCheckpoint,
        *,
        horizon: int = 1,
        tick_rate_hz: float = 30.0,
        max_horizon: int = 12,
    ) -> None:
        integer(max_horizon, "max_horizon", 1, 12)
        integer(horizon, "horizon", 1, max_horizon)
        rate = finite_number(tick_rate_hz, "tick_rate_hz")
        if not 1 <= rate <= 240:
            raise ContractViolation("tick rate must be in [1, 240]")
        self.checkpoint = checkpoint
        self._horizon, self._max_horizon, self._tick_rate = horizon, max_horizon, rate

    @property
    def policy_version(self) -> str:
        return self.checkpoint.policy_version

    def decode(
        self, features: tuple[float, ...], context: PolicyContext, *, horizon: int | None = None
    ) -> ActionChunk:
        count = self._horizon if horizon is None else horizon
        integer(count, "horizon", 1, self._max_horizon)
        prediction = self.checkpoint.predict(features)
        axes = tuple((value,) * count for value in prediction.axes)
        return ActionChunk(
            uuid.uuid4().hex,
            context.observation_id,
            context.generated_at,
            context.generated_at,
            UGATime(context.generated_at.value_ns + round(count * 1e9 / self._tick_rate)),
            self._tick_rate,
            axes[0],
            axes[1],
            axes[2],
            axes[3],
            (prediction.buttons,) * count,
            prediction.confidence,
            self.policy_version,
        )
