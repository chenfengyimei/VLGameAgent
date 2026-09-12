from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.observation.schema import Observation
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class ModeEvidence:
    mode: ControlMode
    confidence: float
    source: str
    observed_at: UGATime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0 or not self.source.strip():
            raise ContractViolation("mode evidence requires a source and bounded confidence")


@dataclass(frozen=True, slots=True)
class ModeTransition:
    previous: ControlMode
    current: ControlMode
    confirmed_at: UGATime
    evidence: ModeEvidence


@runtime_checkable
class ModeClassifier(Protocol):
    def classify(self, observation: Observation) -> ModeEvidence: ...


class RuleModeClassifier:
    """Cheap first-pass rules; a VLM classifier can be composed as fallback."""

    def __init__(self, mode_hints: tuple[tuple[str, str], ...] = ()) -> None:
        parsed: list[tuple[str, ControlMode]] = []
        for token, mode in mode_hints:
            try:
                parsed.append((token.casefold(), ControlMode(mode)))
            except ValueError as exc:
                raise ContractViolation(f"unsupported mode hint target: {mode}") from exc
        self._mode_hints = tuple(parsed)

    def classify(self, observation: Observation) -> ModeEvidence:
        text = " ".join(observation.visible_text).casefold()
        hinted = next((mode for token, mode in self._mode_hints if token in text), None)
        if hinted is not None:
            mode, confidence = hinted, 0.96
        elif any(token in text for token in ("loading", "please wait", "载入", "加载中")):
            mode, confidence = ControlMode.LOADING, 0.95
        elif any(token in text for token in ("resume", "settings", "quit game", "继续游戏")):
            mode, confidence = ControlMode.GUI, 0.9
        elif any(token in text for token in ("you died", "respawn", "死亡", "重生")):
            mode, confidence = ControlMode.DEAD, 0.95
        elif observation.current_mode != ControlMode.UNKNOWN:
            mode, confidence = observation.current_mode, 0.7
        else:
            mode, confidence = ControlMode.UNKNOWN, 0.2
        return ModeEvidence(mode, confidence, "rules", observation.created_at)


class ModeRouter:
    """Confidence, confirmation-count, and minimum-hold hysteresis."""

    def __init__(
        self,
        initial_mode: ControlMode = ControlMode.UNKNOWN,
        *,
        confirmation_frames: int = 3,
        minimum_hold_ns: int = 500_000_000,
        transition_confidence: float = 0.8,
        started_at: UGATime | None = None,
    ) -> None:
        if confirmation_frames < 1 or minimum_hold_ns < 0:
            raise ContractViolation("invalid mode hysteresis configuration")
        if not 0.0 <= transition_confidence <= 1.0:
            raise ContractViolation("mode transition confidence must be in [0, 1]")
        self._current = initial_mode
        self._confirmed_at = started_at or UGATime(0)
        self._confirmation_frames = confirmation_frames
        self._minimum_hold_ns = minimum_hold_ns
        self._transition_confidence = transition_confidence
        self._candidate: ControlMode | None = None
        self._candidate_count = 0

    @property
    def current(self) -> ControlMode:
        return self._current

    def consider(self, evidence: ModeEvidence) -> ModeTransition | None:
        if evidence.observed_at < self._confirmed_at:
            raise ContractViolation("mode evidence cannot predate the current mode")
        if evidence.mode == self._current:
            self._candidate = None
            self._candidate_count = 0
            return None
        if evidence.confidence < self._transition_confidence:
            self._candidate = None
            self._candidate_count = 0
            return None
        if evidence.mode == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = evidence.mode
            self._candidate_count = 1
        held_ns = evidence.observed_at.value_ns - self._confirmed_at.value_ns
        if self._candidate_count < self._confirmation_frames or held_ns < self._minimum_hold_ns:
            return None
        previous = self._current
        self._current = evidence.mode
        self._confirmed_at = evidence.observed_at
        self._candidate = None
        self._candidate_count = 0
        return ModeTransition(previous, self._current, self._confirmed_at, evidence)
