from __future__ import annotations

import math
from dataclasses import dataclass

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class CadenceDecision:
    observation_hz: float
    action_horizon: int
    degraded: bool
    reason: str


class AdaptivePolicyCadence:
    """Bounded dynamic degradation for slow Fast Policy inference."""

    def __init__(
        self,
        *,
        observation_rates_hz: tuple[float, ...] = (5.0, 4.0, 2.5, 2.0),
        action_horizons: tuple[int, ...] = (6, 8, 12, 12),
        recovery_samples: int = 5,
        recovery_utilization: float = 0.6,
        max_action_horizon: int = 12,
    ) -> None:
        decreasing = any(
            next_rate >= rate
            for rate, next_rate in zip(observation_rates_hz, observation_rates_hz[1:], strict=False)
        )
        if (
            not observation_rates_hz
            or len(observation_rates_hz) != len(action_horizons)
            or any(rate <= 0 or not math.isfinite(rate) for rate in observation_rates_hz)
            or decreasing
            or any(horizon < 1 or horizon > max_action_horizon for horizon in action_horizons)
            or recovery_samples < 1
            or not 0 < recovery_utilization < 1
        ):
            raise ContractViolation("invalid adaptive policy cadence configuration")
        self._rates = observation_rates_hz
        self._horizons = action_horizons
        self._recovery_samples = recovery_samples
        self._recovery_utilization = recovery_utilization
        self._level = 0
        self._healthy_samples = 0

    @property
    def decision(self) -> CadenceDecision:
        return CadenceDecision(
            self._rates[self._level],
            self._horizons[self._level],
            self._level > 0,
            "inference_over_budget" if self._level > 0 else "target_cadence",
        )

    def observe_inference(self, latency_ms: float) -> CadenceDecision:
        if latency_ms < 0 or not math.isfinite(latency_ms):
            raise ContractViolation("inference latency must be finite and non-negative")
        interval_ms = 1_000.0 / self._rates[self._level]
        if latency_ms > interval_ms:
            self._level = min(self._level + 1, len(self._rates) - 1)
            self._healthy_samples = 0
            return self.decision
        if self._level > 0 and latency_ms <= interval_ms * self._recovery_utilization:
            self._healthy_samples += 1
            if self._healthy_samples >= self._recovery_samples:
                self._level -= 1
                self._healthy_samples = 0
        else:
            self._healthy_samples = 0
        return self.decision
