"""Pixel-only perception contracts used by the closed-loop agent."""

from uga.perception.schema import (
    ActionRisk,
    DecisionKind,
    GoalStatus,
    GroundedAction,
    NormalizedBox,
    PerceptionSnapshot,
    PlannerOutcome,
    TextRegion,
    UiElement,
    WaitReason,
)
from uga.perception.text import NullTextProvider, RapidOcrProvider, TextObservationProvider

__all__ = [
    "ActionRisk",
    "DecisionKind",
    "GoalStatus",
    "GroundedAction",
    "NormalizedBox",
    "NullTextProvider",
    "PerceptionSnapshot",
    "PlannerOutcome",
    "RapidOcrProvider",
    "TextRegion",
    "TextObservationProvider",
    "UiElement",
    "WaitReason",
]
