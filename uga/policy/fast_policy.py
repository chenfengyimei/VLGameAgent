from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from uga.core.errors import ContractViolation
from uga.observation.buffer import TemporalObservation
from uga.policy.action_chunk import KNOWN_ACTION_BUTTON_MASK, ActionChunk
from uga.policy.cadence import AdaptivePolicyCadence
from uga.time.clock import ClockBackend, PerfCounterClock, UGATime


@dataclass(frozen=True, slots=True)
class PolicyContext:
    observation_id: str
    generated_at: UGATime
    temporal_context: tuple[TemporalObservation, ...]
    instruction: str | None


@dataclass(frozen=True, slots=True)
class FastPolicyOutput:
    chunk: ActionChunk
    need_reasoning: bool
    reasoning_reason: str | None
    observation_hz: float | None = None
    next_observation_hz: float | None = None


@runtime_checkable
class FastPolicy(Protocol):
    @property
    def policy_version(self) -> str: ...

    def infer(self, context: PolicyContext) -> FastPolicyOutput: ...


@runtime_checkable
class VisualBackbone(Protocol):
    @property
    def backbone_version(self) -> str: ...

    def encode(self, context: PolicyContext) -> tuple[float, ...]: ...


@runtime_checkable
class QwenFeatureEncoder(Protocol):
    @property
    def model_version(self) -> str: ...

    def encode_visual_history(
        self, observations: tuple[TemporalObservation, ...], instruction: str | None
    ) -> tuple[float, ...]: ...


class Qwen3VlBackbone:
    """Injected feature adapter; visual layers can remain frozen during motor BC."""

    def __init__(self, encoder: QwenFeatureEncoder) -> None:
        self._encoder = encoder

    @property
    def backbone_version(self) -> str:
        return self._encoder.model_version

    def encode(self, context: PolicyContext) -> tuple[float, ...]:
        features = self._encoder.encode_visual_history(
            context.temporal_context, context.instruction
        )
        if not features or any(not math.isfinite(value) for value in features):
            raise ContractViolation("Qwen backbone returned invalid features")
        return features


@dataclass(frozen=True, slots=True)
class DecoderCheckpoint:
    policy_version: str
    input_dim: int
    axis_weights: tuple[tuple[float, ...], ...]
    axis_bias: tuple[float, float, float, float]
    button_mask: int
    confidence: float

    def __post_init__(self) -> None:
        if self.input_dim < 1 or len(self.axis_weights) != 4:
            raise ContractViolation("decoder checkpoint shape is invalid")
        if any(len(weights) != self.input_dim for weights in self.axis_weights):
            raise ContractViolation("decoder checkpoint weights do not match input dimension")
        if (
            not 0 <= self.button_mask <= 0xFFFF
            or self.button_mask & ~KNOWN_ACTION_BUTTON_MASK
            or not 0 <= self.confidence <= 1
        ):
            raise ContractViolation("decoder checkpoint output metadata is invalid")

    def save(self, path: str | Path) -> Path:
        payload = {
            "schema_version": "1.1",
            "policy_version": self.policy_version,
            "input_dim": self.input_dim,
            "axis_weights": self.axis_weights,
            "axis_bias": self.axis_bias,
            "button_mask": self.button_mask,
            "confidence": self.confidence,
        }
        destination = Path(path)
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path) -> DecoderCheckpoint:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != "1.1":
            raise ContractViolation("unsupported decoder checkpoint schema")
        return cls(
            str(payload["policy_version"]),
            int(payload["input_dim"]),
            tuple(tuple(float(value) for value in row) for row in payload["axis_weights"]),
            tuple(float(value) for value in payload["axis_bias"]),  # type: ignore[arg-type]
            int(payload["button_mask"]),
            float(payload["confidence"]),
        )


class TemporalActionDecoder:
    def __init__(
        self,
        checkpoint: DecoderCheckpoint,
        *,
        horizon: int = 6,
        tick_rate_hz: float = 30.0,
        max_horizon: int = 12,
    ) -> None:
        if horizon < 1 or horizon > max_horizon or tick_rate_hz <= 0:
            raise ContractViolation("decoder horizon and tick rate must be positive")
        self._checkpoint = checkpoint
        self._horizon = horizon
        self._tick_rate_hz = tick_rate_hz
        self._max_horizon = max_horizon

    @property
    def policy_version(self) -> str:
        return self._checkpoint.policy_version

    def decode(
        self,
        features: tuple[float, ...],
        context: PolicyContext,
        *,
        horizon: int | None = None,
    ) -> ActionChunk:
        if len(features) != self._checkpoint.input_dim:
            raise ContractViolation("feature vector does not match decoder input dimension")
        selected_horizon = self._horizon if horizon is None else horizon
        if not 1 <= selected_horizon <= self._max_horizon:
            raise ContractViolation("requested decoder horizon exceeds configured bounds")
        axes = tuple(
            max(
                -1.0,
                min(
                    1.0,
                    sum(weight * value for weight, value in zip(weights, features, strict=True))
                    + bias,
                ),
            )
            for weights, bias in zip(
                self._checkpoint.axis_weights, self._checkpoint.axis_bias, strict=True
            )
        )
        duration_ns = round(selected_horizon * 1_000_000_000 / self._tick_rate_hz)
        expires = UGATime(context.generated_at.value_ns + duration_ns)
        repeated = tuple((value,) * selected_horizon for value in axes)
        return ActionChunk(
            uuid.uuid4().hex,
            context.observation_id,
            context.generated_at,
            context.generated_at,
            expires,
            self._tick_rate_hz,
            repeated[0],
            repeated[1],
            repeated[2],
            repeated[3],
            (self._checkpoint.button_mask,) * selected_horizon,
            self._checkpoint.confidence,
            self._checkpoint.policy_version,
        )


class StructuredFastPolicy:
    def __init__(
        self,
        backbone: VisualBackbone,
        decoder: TemporalActionDecoder,
        *,
        cadence: AdaptivePolicyCadence | None = None,
        clock: ClockBackend | None = None,
    ) -> None:
        self._backbone = backbone
        self._decoder = decoder
        self._cadence = cadence
        self._clock = clock or PerfCounterClock()

    @property
    def policy_version(self) -> str:
        return self._decoder.policy_version

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
        started = self._clock.now()
        active_cadence = None if self._cadence is None else self._cadence.decision
        features = self._backbone.encode(context)
        chunk = self._decoder.decode(
            features,
            context,
            horizon=None if active_cadence is None else active_cadence.action_horizon,
        )
        ended = self._clock.now()
        next_cadence = (
            None
            if self._cadence is None
            else self._cadence.observe_inference((ended.value_ns - started.value_ns) / 1_000_000)
        )
        need_reasoning = chunk.confidence < 0.5
        return FastPolicyOutput(
            chunk,
            need_reasoning,
            "low_confidence" if need_reasoning else None,
            None if active_cadence is None else active_cadence.observation_hz,
            None if next_cadence is None else next_cadence.observation_hz,
        )
