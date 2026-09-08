from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    backend_preference: tuple[str, ...]
    ring_capacity: int
    health_failure_threshold: int
    max_pending_observations: int

    def __post_init__(self) -> None:
        if not self.backend_preference:
            raise ContractViolation("capture.backend_preference cannot be empty")
        if self.ring_capacity < 1:
            raise ContractViolation("capture.ring_capacity must be positive")
        if self.health_failure_threshold < 1:
            raise ContractViolation("capture.health_failure_threshold must be positive")
        if self.max_pending_observations != 1:
            raise ContractViolation("runtime capture path must use latest-state-wins (size 1)")


@dataclass(frozen=True, slots=True)
class ControlConfig:
    scheduler_hz: float
    watchdog_timeout_ms: int
    emergency_hotkey: str

    def __post_init__(self) -> None:
        if self.scheduler_hz <= 0:
            raise ContractViolation("control.scheduler_hz must be positive")
        if self.watchdog_timeout_ms <= 0:
            raise ContractViolation("control.watchdog_timeout_ms must be positive")
        if self.emergency_hotkey.casefold() != "ctrl+shift+f12":
            raise ContractViolation("V1 emergency hotkey must be Ctrl+Shift+F12")


@dataclass(frozen=True, slots=True)
class ModeRouterConfig:
    confirmation_frames: int
    minimum_hold_ms: int
    transition_confidence: float

    def __post_init__(self) -> None:
        if self.confirmation_frames < 1 or self.minimum_hold_ms < 0:
            raise ContractViolation("invalid mode router confirmation/hold configuration")
        if not 0.0 <= self.transition_confidence <= 1.0:
            raise ContractViolation("mode router confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class PolicyCadenceConfig:
    observation_rates_hz: tuple[float, ...]
    action_horizons: tuple[int, ...]
    recovery_samples: int
    recovery_utilization: float
    max_action_horizon: int

    def __post_init__(self) -> None:
        decreasing = any(
            next_rate >= rate
            for rate, next_rate in zip(
                self.observation_rates_hz, self.observation_rates_hz[1:], strict=False
            )
        )
        if (
            not self.observation_rates_hz
            or len(self.observation_rates_hz) != len(self.action_horizons)
            or any(rate <= 0 or not math.isfinite(rate) for rate in self.observation_rates_hz)
            or decreasing
            or any(
                horizon < 1 or horizon > self.max_action_horizon for horizon in self.action_horizons
            )
            or self.recovery_samples < 1
            or not 0 < self.recovery_utilization < 1
        ):
            raise ContractViolation("invalid policy cadence configuration")


@dataclass(frozen=True, slots=True)
class DatasetQualityConfig:
    max_capture_gap_ns: int
    max_input_gap_ns: int
    max_mouse_delta: int

    def __post_init__(self) -> None:
        if self.max_capture_gap_ns <= 0 or self.max_input_gap_ns <= 0 or self.max_mouse_delta <= 0:
            raise ContractViolation("dataset quality thresholds must be positive")


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    event_queue_size: int
    capture: CaptureConfig
    control: ControlConfig
    mode_router: ModeRouterConfig
    policy_cadence: PolicyCadenceConfig
    dataset_quality: DatasetQualityConfig


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractViolation(f"{name} must be a TOML table")
    return value


def load_config(path: str | Path) -> RuntimeConfig:
    with Path(path).open("rb") as stream:
        raw = tomllib.load(stream)
    runtime = _table(raw.get("runtime", {}), "runtime")
    capture = _table(raw.get("capture", {}), "capture")
    capture_runtime = _table(capture.get("runtime", {}), "capture.runtime")
    control = _table(raw.get("control", {}), "control")
    agent = _table(raw.get("agent", {}), "agent")
    mode_router = _table(agent.get("mode_router", {}), "agent.mode_router")
    policy = _table(raw.get("policy", {}), "policy")
    cadence = _table(policy.get("cadence", {}), "policy.cadence")
    dataset = _table(raw.get("dataset", {}), "dataset")
    quality = _table(dataset.get("quality", {}), "dataset.quality")
    event_queue_size = int(runtime.get("event_queue_size", 256))
    if event_queue_size < 1:
        raise ContractViolation("runtime.event_queue_size must be positive")
    return RuntimeConfig(
        event_queue_size=event_queue_size,
        capture=CaptureConfig(
            backend_preference=tuple(str(item) for item in capture["backend_preference"]),
            ring_capacity=int(capture.get("ring_capacity", 8)),
            health_failure_threshold=int(capture.get("health_failure_threshold", 3)),
            max_pending_observations=int(capture_runtime.get("max_pending_observations", 1)),
        ),
        control=ControlConfig(
            scheduler_hz=float(control.get("scheduler_hz", 30.0)),
            watchdog_timeout_ms=int(control.get("watchdog_timeout_ms", 500)),
            emergency_hotkey=str(control.get("emergency_hotkey", "ctrl+shift+f12")),
        ),
        mode_router=ModeRouterConfig(
            confirmation_frames=int(mode_router.get("confirmation_frames", 3)),
            minimum_hold_ms=int(mode_router.get("minimum_hold_ms", 500)),
            transition_confidence=float(mode_router.get("transition_confidence", 0.8)),
        ),
        policy_cadence=PolicyCadenceConfig(
            observation_rates_hz=tuple(
                float(item) for item in cadence.get("observation_rates_hz", (5, 4, 2.5, 2))
            ),
            action_horizons=tuple(
                int(item) for item in cadence.get("action_horizons", (6, 8, 12, 12))
            ),
            recovery_samples=int(cadence.get("recovery_samples", 5)),
            recovery_utilization=float(cadence.get("recovery_utilization", 0.6)),
            max_action_horizon=int(cadence.get("max_action_horizon", 12)),
        ),
        dataset_quality=DatasetQualityConfig(
            max_capture_gap_ns=int(quality.get("max_capture_gap_ns", 500_000_000)),
            max_input_gap_ns=int(quality.get("max_input_gap_ns", 500_000_000)),
            max_mouse_delta=int(quality.get("max_mouse_delta", 5000)),
        ),
    )
