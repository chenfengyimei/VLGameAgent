from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntFlag
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


class ActionButton(IntFlag):
    JUMP = 1 << 0
    SPRINT = 1 << 1
    CROUCH = 1 << 2
    INTERACT = 1 << 3
    PRIMARY = 1 << 4
    SECONDARY = 1 << 5
    MENU = 1 << 6
    CONFIRM = 1 << 7
    BACK = 1 << 8


KNOWN_ACTION_BUTTON_MASK = int(
    ActionButton.JUMP
    | ActionButton.SPRINT
    | ActionButton.CROUCH
    | ActionButton.INTERACT
    | ActionButton.PRIMARY
    | ActionButton.SECONDARY
    | ActionButton.MENU
    | ActionButton.CONFIRM
    | ActionButton.BACK
)


@dataclass(frozen=True, slots=True)
class ActionChunk(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.action_chunk"

    chunk_id: str
    observation_id: str
    generated_at: UGATime
    effective_from: UGATime
    expires_at: UGATime
    tick_rate_hz: float
    move_x: tuple[float, ...]
    move_y: tuple[float, ...]
    look_x: tuple[float, ...]
    look_y: tuple[float, ...]
    buttons: tuple[int, ...]
    confidence: float
    policy_version: str
    pointer_x: float | None = None
    pointer_y: float | None = None
    pointer_drag: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if (
            not self.chunk_id.strip()
            or not self.observation_id.strip()
            or not self.policy_version.strip()
        ):
            raise ContractViolation("action chunk identifiers cannot be blank")
        if not (self.generated_at <= self.effective_from <= self.expires_at):
            raise ContractViolation("action chunk lifetime is invalid")
        if (
            not math.isfinite(self.tick_rate_hz)
            or self.tick_rate_hz <= 0
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ContractViolation("action chunk rate/confidence is invalid")
        lengths = {
            len(self.move_x),
            len(self.move_y),
            len(self.look_x),
            len(self.look_y),
            len(self.buttons),
        }
        if len(lengths) != 1 or not self.move_x:
            raise ContractViolation("action chunk arrays must have one shared non-zero length")
        axes = (*self.move_x, *self.move_y, *self.look_x, *self.look_y)
        if any(not -1.0 <= axis <= 1.0 for axis in axes):
            raise ContractViolation("action chunk axes must be in [-1, 1]")
        if any(
            not 0 <= mask <= 0xFFFF or mask & ~KNOWN_ACTION_BUTTON_MASK for mask in self.buttons
        ):
            raise ContractViolation("action chunk contains undefined canonical button bits")
        if (self.pointer_x is None) != (self.pointer_y is None):
            raise ContractViolation("action chunk pointer coordinates must be set together")
        if self.pointer_x is not None and (
            not math.isfinite(self.pointer_x) or self.pointer_x < 0.0
        ):
            raise ContractViolation("action chunk pointer coordinates must be non-negative")
        if self.pointer_drag:
            if any(
                not math.isfinite(px) or not math.isfinite(py) or px < 0.0 or py < 0.0
                for px, py in self.pointer_drag
            ):
                raise ContractViolation(
                    "action chunk drag path points must be non-negative finite pixels"
                )
            if self.pointer_x is not None:
                raise ContractViolation(
                    "action chunk drag path excludes a static pointer position"
                )

    @property
    def horizon(self) -> int:
        return len(self.move_x)
