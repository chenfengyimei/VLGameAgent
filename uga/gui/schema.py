from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


class GuiActionKind(StrEnum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE = "type"
    KEY = "key"
    HOTKEY = "hotkey"
    WAIT = "wait"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class GuiTask(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.gui_task"

    task_id: str
    instruction: str
    success_condition: str
    max_actions: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if (
            not self.task_id.strip()
            or not self.instruction.strip()
            or not self.success_condition.strip()
        ):
            raise ContractViolation("GUI task has blank required fields")
        if self.max_actions < 1:
            raise ContractViolation("GUI task max actions must be positive")


@dataclass(frozen=True, slots=True)
class GuiAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.gui_action"

    action_id: str
    kind: GuiActionKind
    lifetime: ActionLifetime
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    text: str | None = None
    key_codes: tuple[int, ...] = ()
    scroll_delta: int = 0
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.action_id.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("GUI action requires id and bounded confidence")
        self.lifetime.validate()
        point_kinds = {
            GuiActionKind.CLICK,
            GuiActionKind.DOUBLE_CLICK,
            GuiActionKind.RIGHT_CLICK,
            GuiActionKind.DRAG,
            GuiActionKind.SCROLL,
        }
        if self.kind in point_kinds and (
            self.x is None or self.y is None or not (0 <= self.x <= 1 and 0 <= self.y <= 1)
        ):
            raise ContractViolation("GUI pointer coordinates must be normalized in [0, 1]")
        if self.kind == GuiActionKind.DRAG and (
            self.end_x is None
            or self.end_y is None
            or not 0 <= self.end_x <= 1
            or not 0 <= self.end_y <= 1
        ):
            raise ContractViolation("GUI drag endpoint must be normalized in [0, 1]")
        if self.kind == GuiActionKind.TYPE and (self.text is None or not self.text):
            raise ContractViolation("GUI type action requires text")
        if self.kind in (GuiActionKind.KEY, GuiActionKind.HOTKEY) and not self.key_codes:
            raise ContractViolation("GUI key action requires virtual-key codes")
        if self.kind == GuiActionKind.SCROLL and self.scroll_delta == 0:
            raise ContractViolation("GUI scroll action requires a non-zero delta")


@dataclass(frozen=True, slots=True)
class GuiResult:
    task_id: str
    actions: tuple[GuiAction, ...]
    done: bool
    explanation: str
