from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


@dataclass(frozen=True, slots=True)
class CanonicalAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.canonical_action"

    action_id: str
    lifetime: ActionLifetime
    move_x: float = 0.0
    move_y: float = 0.0
    look_x: float = 0.0
    look_y: float = 0.0
    jump: bool = False
    sprint: bool = False
    crouch: bool = False
    interact: bool = False
    primary: bool = False
    secondary: bool = False
    menu: bool = False
    confirm: bool = False
    back: bool = False
    custom_slots: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("canonical action id cannot be empty")
        self.lifetime.validate()
        axes = (self.move_x, self.move_y, self.look_x, self.look_y, *self.custom_slots)
        if any(not -1.0 <= axis <= 1.0 for axis in axes):
            raise ContractViolation("canonical axes and custom slots must be in [-1, 1]")
