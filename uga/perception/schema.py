from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.gui.schema import GuiActionKind
from uga.time.clock import UGATime
from uga.windows.coordinates import Point
from uga.windows.window_identity import WindowIdentity


class DecisionKind(StrEnum):
    ACT = "act"
    WAIT = "wait"
    DONE = "done"
    ABSTAIN = "abstain"
    RECOVER = "recover"


class GoalStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class WaitReason(StrEnum):
    LOADING = "loading"
    ANIMATION = "animation"
    RATE_LIMIT = "rate_limit"
    NO_SAFE_ACTION = "no_safe_action"


class ActionRisk(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class NormalizedBox:
    """Half-open target box in model-normalized client coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            raise ContractViolation("normalized box coordinates must be finite and in [0, 1]")
        if self.right <= self.left or self.bottom <= self.top:
            raise ContractViolation("normalized box must have positive width and height")

    @property
    def center(self) -> Point:
        return Point((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def contains(self, point: Point) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def intersection_ratio(self, other: NormalizedBox) -> float:
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return 0.0
        intersection = (right - left) * (bottom - top)
        own_area = (self.right - self.left) * (self.bottom - self.top)
        return intersection / own_area


@dataclass(frozen=True, slots=True)
class TextRegion:
    text: str
    box: NormalizedBox
    confidence: float

    def __post_init__(self) -> None:
        if not self.text.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("text region requires text and bounded confidence")


@dataclass(frozen=True, slots=True)
class UiElement:
    label: str
    box: NormalizedBox
    confidence: float
    enabled: bool = True
    selected: bool = False

    def __post_init__(self) -> None:
        if not self.label.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("UI element requires a label and bounded confidence")
        if type(self.enabled) is not bool or type(self.selected) is not bool:
            raise ContractViolation("UI element state flags must be booleans")


@dataclass(frozen=True, slots=True)
class PerceptionSnapshot(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.perception_snapshot"

    snapshot_id: str
    frame_id: str
    frame_sequence: int
    captured_at: UGATime
    window_identity: WindowIdentity
    geometry_generation: int
    task_generation: int
    mode: ControlMode
    visible_text: tuple[TextRegion, ...]
    ui_elements: tuple[UiElement, ...]
    goal_facts: tuple[tuple[str, str], ...]
    state_signature: str
    confidence: float

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        required = (self.snapshot_id, self.frame_id, self.state_signature)
        if any(not item.strip() for item in required):
            raise ContractViolation("perception snapshot identifiers cannot be blank")
        if self.frame_sequence < 1 or self.geometry_generation < 0 or self.task_generation < 0:
            raise ContractViolation("perception generations and sequence are invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("perception confidence must be in [0, 1]")
        keys = [key for key, _ in self.goal_facts]
        if any(not key.strip() for key in keys) or len(keys) != len(set(keys)):
            raise ContractViolation("goal fact keys must be unique and non-empty")

    @property
    def text(self) -> tuple[str, ...]:
        return tuple(region.text for region in self.visible_text)


@dataclass(frozen=True, slots=True)
class GroundedAction:
    kind: GuiActionKind
    target_label: str
    target_box: NormalizedBox | None
    expected_effect: str
    confidence: float
    risk: ActionRisk = ActionRisk.NORMAL
    key: str | None = None

    def __post_init__(self) -> None:
        if not self.target_label.strip() or not self.expected_effect.strip():
            raise ContractViolation("grounded action requires target and expected effect")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("grounded action confidence must be in [0, 1]")
        pointer_kinds = {
            GuiActionKind.CLICK,
            GuiActionKind.DOUBLE_CLICK,
            GuiActionKind.RIGHT_CLICK,
            GuiActionKind.DRAG,
            GuiActionKind.SCROLL,
        }
        if self.kind in pointer_kinds and self.target_box is None:
            raise ContractViolation("grounded pointer action requires a target box")
        if self.kind in {GuiActionKind.WAIT, GuiActionKind.DONE}:
            raise ContractViolation("wait/done are decisions, not grounded actions")
        if self.kind in {GuiActionKind.KEY, GuiActionKind.HOTKEY} and not self.key:
            raise ContractViolation("grounded key action requires a key name")


@dataclass(frozen=True, slots=True)
class PlannerOutcome(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.planner_outcome"

    decision_id: str
    request_frame_id: str
    request_frame_sequence: int
    window_generation: int
    geometry_generation: int
    task_generation: int
    kind: DecisionKind
    scene_summary: str
    visible_text: tuple[str, ...]
    goal_status: GoalStatus
    confidence: float
    action: GroundedAction | None = None
    wait_reason: WaitReason | None = None
    explanation: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.decision_id.strip() or not self.request_frame_id.strip():
            raise ContractViolation("planner outcome identifiers cannot be blank")
        if self.request_frame_sequence < 1:
            raise ContractViolation("planner outcome frame sequence must be positive")
        if min(self.window_generation, self.geometry_generation, self.task_generation) < 0:
            raise ContractViolation("planner outcome generations cannot be negative")
        if not self.scene_summary.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("planner outcome requires a scene and bounded confidence")
        if self.kind == DecisionKind.ACT and self.action is None:
            raise ContractViolation("ACT outcome requires exactly one grounded action")
        if self.kind != DecisionKind.ACT and self.action is not None:
            raise ContractViolation("only ACT outcomes may carry a grounded action")
        if self.kind == DecisionKind.WAIT and self.wait_reason is None:
            raise ContractViolation("WAIT outcome requires a reason")
        if self.kind != DecisionKind.WAIT and self.wait_reason is not None:
            raise ContractViolation("only WAIT outcomes may carry a wait reason")
        if self.kind == DecisionKind.DONE and self.goal_status != GoalStatus.SUCCEEDED:
            raise ContractViolation("DONE outcome must report a succeeded goal")

