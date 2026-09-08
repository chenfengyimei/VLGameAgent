from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from uga.core.errors import ContractViolation


class ReasoningReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_SCENE = "unknown_scene"
    NO_PROGRESS = "no_progress"
    TARGET_LOST = "target_lost"
    MODE_UNCERTAINTY = "mode_uncertainty"


@dataclass(frozen=True, slots=True)
class ReasoningSignals:
    confidence: float
    unknown_scene: bool = False
    no_progress: bool = False
    target_lost: bool = False
    mode_uncertainty: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ContractViolation("reasoning confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ReasoningDecision:
    need_reasoning: bool
    reasons: tuple[ReasoningReason, ...]
    cancel_future_chunk: bool


class Flushable(Protocol):
    def flush(self) -> int: ...


class ReasoningGate:
    def __init__(self, confidence_threshold: float = 0.5) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ContractViolation("reasoning threshold must be in [0, 1]")
        self._threshold = confidence_threshold

    def evaluate(self, signals: ReasoningSignals) -> ReasoningDecision:
        reasons: list[ReasoningReason] = []
        if signals.confidence < self._threshold:
            reasons.append(ReasoningReason.LOW_CONFIDENCE)
        for active, reason in (
            (signals.unknown_scene, ReasoningReason.UNKNOWN_SCENE),
            (signals.no_progress, ReasoningReason.NO_PROGRESS),
            (signals.target_lost, ReasoningReason.TARGET_LOST),
            (signals.mode_uncertainty, ReasoningReason.MODE_UNCERTAINTY),
        ):
            if active:
                reasons.append(reason)
        return ReasoningDecision(bool(reasons), tuple(reasons), bool(reasons))

    @staticmethod
    def apply(decision: ReasoningDecision, scheduler: Flushable) -> int:
        return scheduler.flush() if decision.cancel_future_chunk else 0
