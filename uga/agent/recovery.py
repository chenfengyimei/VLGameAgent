from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uga.core.errors import ContractViolation


class ReflectionTrigger(StrEnum):
    NO_PROGRESS = "no_progress"
    SKILL_FAILED = "skill_failed"
    LOW_CONFIDENCE = "low_confidence"
    DEATH = "death"
    UNEXPECTED_GUI = "unexpected_gui"
    TARGET_LOST = "target_lost"
    REPEATED_LOOP = "repeated_loop"
    WRONG_LOCATION = "wrong_location"


class FailureKind(StrEnum):
    PERCEPTION = "perception"
    NAVIGATION = "navigation"
    CONTROL = "control"
    GUI = "gui"
    SKILL = "skill"
    PLANNING = "planning"
    UNKNOWN = "unknown"


class RecoveryStrategy(StrEnum):
    RETRY = "retry"
    BACKTRACK = "backtrack"
    REORIENT_CAMERA = "reorient_camera"
    REOPEN_UI = "reopen_ui"
    ALTERNATIVE_SKILL = "alternative_skill"
    ASK_SLOW_BRAIN = "ask_slow_brain"
    HUMAN_INTERVENTION = "human_intervention"


@dataclass(frozen=True, slots=True)
class FailureReport:
    trigger: ReflectionTrigger
    kind: FailureKind
    detail: str
    attempts: int
    confidence: float

    def __post_init__(self) -> None:
        if not self.detail.strip() or self.attempts < 0 or not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("invalid failure report")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    strategy: RecoveryStrategy
    reason: str
    requires_new_plan: bool


class ReflectionEngine:
    """Event-triggered reflection; ordinary successful steps never invoke it."""

    def reflect(self, report: FailureReport) -> RecoveryDecision:
        if report.attempts >= 3 or report.confidence < 0.2:
            return RecoveryDecision(
                RecoveryStrategy.HUMAN_INTERVENTION,
                "retry or confidence safety limit reached",
                False,
            )
        strategies = {
            FailureKind.PERCEPTION: RecoveryStrategy.REORIENT_CAMERA,
            FailureKind.NAVIGATION: RecoveryStrategy.BACKTRACK,
            FailureKind.CONTROL: RecoveryStrategy.RETRY,
            FailureKind.GUI: RecoveryStrategy.REOPEN_UI,
            FailureKind.SKILL: RecoveryStrategy.ALTERNATIVE_SKILL,
            FailureKind.PLANNING: RecoveryStrategy.ASK_SLOW_BRAIN,
            FailureKind.UNKNOWN: RecoveryStrategy.ASK_SLOW_BRAIN,
        }
        strategy = strategies[report.kind]
        return RecoveryDecision(
            strategy,
            f"{report.trigger.value}: {report.detail}",
            strategy in (RecoveryStrategy.ALTERNATIVE_SKILL, RecoveryStrategy.ASK_SLOW_BRAIN),
        )
