from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from uga.perception.builder import normalize_visible_text
from uga.perception.schema import PerceptionSnapshot


@dataclass(frozen=True, slots=True)
class SemanticState:
    visual_signature: str
    semantic_signature: str
    goal_signature: str
    loop_signature: str


class ProgressTracker:
    """Builds stable semantic state without treating animation alone as progress."""

    @staticmethod
    def state(snapshot: PerceptionSnapshot) -> SemanticState:
        semantic = "\n".join(
            (
                snapshot.mode.value,
                str(snapshot.task_generation),
                *(normalize_visible_text(value) for value in snapshot.text),
                *(f"{key}={value}" for key, value in snapshot.goal_facts),
                *(
                    f"{normalize_visible_text(item.label)}:{item.enabled}:{item.selected}"
                    for item in snapshot.ui_elements
                ),
            )
        )
        goal = "\n".join(f"{key}={value}" for key, value in snapshot.goal_facts)
        semantic_signature = hashlib.sha256(semantic.encode("utf-8")).hexdigest()
        goal_signature = hashlib.sha256(goal.encode("utf-8")).hexdigest()
        loop_material = "\n".join(
            (snapshot.state_signature, semantic_signature, goal_signature)
        )
        return SemanticState(
            snapshot.state_signature,
            semantic_signature,
            goal_signature,
            hashlib.sha256(loop_material.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def progressed(
        before: SemanticState,
        after: SemanticState,
        *,
        target_effect_observed: bool,
    ) -> bool:
        return (
            target_effect_observed
            or before.semantic_signature != after.semantic_signature
            or before.goal_signature != after.goal_signature
        )


class LoopKind(StrEnum):
    REPEATED_INEFFECTIVE_ACTION = "repeated_ineffective_action"
    STATE_ACTION_CYCLE = "state_action_cycle"


@dataclass(frozen=True, slots=True)
class LoopRecord:
    state_signature: str
    action_key: str
    effect_observed: bool
    next_state_signature: str
    semantic_progress: bool


@dataclass(frozen=True, slots=True)
class LoopFinding:
    kind: LoopKind
    cycle_length: int
    detail: str


class LoopDetector:
    """Detects ineffective repetition and exact 2-6 state/action rings."""

    def __init__(self, *, capacity: int = 32) -> None:
        if capacity < 12:
            raise ValueError("loop detector capacity must be at least 12")
        self._records: deque[LoopRecord] = deque(maxlen=capacity)

    @property
    def records(self) -> tuple[LoopRecord, ...]:
        return tuple(self._records)

    def record(self, item: LoopRecord) -> LoopFinding | None:
        self._records.append(item)
        recent = tuple(self._records)
        if len(recent) >= 3:
            last = recent[-3:]
            if (
                all(not value.effect_observed for value in last)
                and len({(value.state_signature, value.action_key) for value in last}) == 1
            ):
                return LoopFinding(
                    LoopKind.REPEATED_INEFFECTIVE_ACTION,
                    1,
                    "same state repeated the same ineffective action three times",
                )
        for length in range(2, 7):
            if len(recent) < length * 2:
                continue
            first = recent[-length * 2 : -length]
            second = recent[-length:]
            first_tokens = tuple((value.state_signature, value.action_key) for value in first)
            second_tokens = tuple((value.state_signature, value.action_key) for value in second)
            if first_tokens == second_tokens:
                return LoopFinding(
                    LoopKind.STATE_ACTION_CYCLE,
                    length,
                    f"state/action ring of length {length} repeated twice",
                )
        return None
