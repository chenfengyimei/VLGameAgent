from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, TypeAlias

from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.windows.coordinates import CoordinateSpace


class KeyEncoding(StrEnum):
    SCAN_CODE = "scan_code"
    VIRTUAL_KEY = "virtual_key"


class MouseButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    X1 = "x1"
    X2 = "x2"


@dataclass(frozen=True, slots=True)
class KeyboardAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.keyboard"

    action_id: str
    lifetime: ActionLifetime
    code: int
    is_down: bool
    encoding: KeyEncoding = KeyEncoding.SCAN_CODE
    is_extended: bool = False

    def validate(self) -> None:
        if not self.action_id.strip() or not 0 <= self.code <= 0xFFFF:
            raise ContractViolation("invalid keyboard action")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class UnicodeTextAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.unicode_text"

    action_id: str
    lifetime: ActionLifetime
    text: str

    def validate(self) -> None:
        if not self.action_id.strip() or not self.text or len(self.text) > 4096:
            raise ContractViolation("Unicode text action requires 1 to 4096 characters")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class RelativeMouseAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.mouse_relative"

    action_id: str
    lifetime: ActionLifetime
    dx: int
    dy: int

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("relative mouse action id cannot be empty")
        if not -(2**31) <= self.dx < 2**31 or not -(2**31) <= self.dy < 2**31:
            raise ContractViolation("relative mouse delta must fit int32")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class AbsolutePointerAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.pointer_absolute"

    action_id: str
    lifetime: ActionLifetime
    x: int
    y: int
    coordinate_space: CoordinateSpace

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("absolute pointer action id cannot be empty")
        if self.coordinate_space != CoordinateSpace.PHYSICAL_SCREEN_PIXEL:
            raise ContractViolation("physical pointer actions require physical-screen coordinates")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class MouseButtonAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.mouse_button"

    action_id: str
    lifetime: ActionLifetime
    button: MouseButton
    is_down: bool

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("mouse button action id cannot be empty")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class WheelAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.wheel"

    action_id: str
    lifetime: ActionLifetime
    delta: int

    def validate(self) -> None:
        if not self.action_id.strip() or self.delta == 0:
            raise ContractViolation("wheel action requires an id and non-zero delta")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class GamepadAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.gamepad"

    action_id: str
    lifetime: ActionLifetime
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    left_trigger: float = 0.0
    right_trigger: float = 0.0
    buttons: int = 0

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("gamepad action id cannot be empty")
        sticks = (self.left_x, self.left_y, self.right_x, self.right_y)
        triggers = (self.left_trigger, self.right_trigger)
        if any(not -1.0 <= axis <= 1.0 for axis in sticks):
            raise ContractViolation("gamepad stick axes must be in [-1, 1]")
        if any(not 0.0 <= axis <= 1.0 for axis in triggers):
            raise ContractViolation("gamepad triggers must be in [0, 1]")
        if not 0 <= self.buttons <= 0xFFFF:
            raise ContractViolation("gamepad button mask must fit uint16")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class RawInputAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.physical.raw_input"

    action_id: str
    lifetime: ActionLifetime
    device: str
    report: bytes

    def validate(self) -> None:
        if not self.action_id.strip() or not self.device.strip():
            raise ContractViolation("raw input action requires action and device ids")
        if not 1 <= len(self.report) <= 64:
            raise ContractViolation("raw HID report must contain 1 to 64 bytes")
        self.lifetime.validate()

    def __post_init__(self) -> None:
        self.validate()


PhysicalAction: TypeAlias = (
    KeyboardAction
    | UnicodeTextAction
    | RelativeMouseAction
    | AbsolutePointerAction
    | MouseButtonAction
    | WheelAction
    | GamepadAction
    | RawInputAction
)
