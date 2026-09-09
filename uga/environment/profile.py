from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, ClassVar

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, read_text_limited
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.core.schema import VersionedMixin
from uga.safety.environment_policy import EnvironmentClass, EnvironmentSafetyManifest


class EnvironmentCapabilityLevel(IntEnum):
    UNKNOWN_RAW = 0
    GENERIC = 1
    AUTO_PROFILE = 2
    USER_CONFIRMED_PROFILE = 3
    DEDICATED_ADAPTER = 4
    GAME_SPECIFIC_SKILLS = 5


class BindingKind(StrEnum):
    SCAN_CODE = "scan_code"
    VIRTUAL_KEY = "virtual_key"
    MOUSE_BUTTON = "mouse_button"
    GAMEPAD_BUTTON = "gamepad_button"


@dataclass(frozen=True, slots=True)
class ControlBinding:
    action: str
    kind: BindingKind
    code: int | str
    confirmed: bool

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ContractViolation("control binding action cannot be blank")
        if isinstance(self.code, str) and not self.code.strip():
            raise ContractViolation("control binding code cannot be blank")


@dataclass(frozen=True, slots=True)
class GameCapabilities:
    realtime_3d: bool
    gui: bool
    combat: bool
    gamepad: bool


@dataclass(frozen=True, slots=True)
class GameProfile(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.game_profile"

    game_id: str
    display_name: str
    executables: tuple[str, ...]
    preferred_capture: str
    controls: tuple[ControlBinding, ...]
    camera_type: str
    camera_sensitivity: float
    capabilities: GameCapabilities
    safety: EnvironmentSafetyManifest
    capability_level: EnvironmentCapabilityLevel
    window_title_pattern: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.game_id.strip() or not self.display_name.strip():
            raise ContractViolation("game profile requires id and display name")
        if not self.executables or any(not executable.strip() for executable in self.executables):
            raise ContractViolation("game profile requires executable names")
        if not self.preferred_capture.strip() or not self.camera_type.strip():
            raise ContractViolation("game profile capture and camera type cannot be blank")
        if self.camera_sensitivity <= 0:
            raise ContractViolation("camera sensitivity must be positive")
        if self.window_title_pattern is not None:
            try:
                re.compile(self.window_title_pattern)
            except re.error as exc:
                raise ContractViolation("game profile window title pattern is invalid") from exc
        actions = [binding.action for binding in self.controls]
        if len(actions) != len(set(actions)):
            raise ContractViolation("game profile control actions must be unique")
        if self.capability_level >= EnvironmentCapabilityLevel.USER_CONFIRMED_PROFILE and any(
            not binding.confirmed for binding in self.controls
        ):
            raise ContractViolation("user-confirmed profiles cannot contain unconfirmed bindings")

    def binding(self, action: str) -> ControlBinding | None:
        return next((binding for binding in self.controls if binding.action == action), None)

    def matches_window_title(self, title: str) -> bool:
        return (
            self.window_title_pattern is None
            or re.search(self.window_title_pattern, title) is not None
        )


def load_game_profile(path: str | Path) -> GameProfile:
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError(
            "YAML game profiles require the declared PyYAML dependency"
        ) from exc
    raw = yaml.safe_load(
        read_text_limited(path, DEFAULT_ARTIFACT_LIMITS.max_config_bytes, "game profile")
    )
    if not isinstance(raw, dict):
        raise ContractViolation("game profile root must be an object")
    return game_profile_from_dict(raw)


def game_profile_from_dict(raw: dict[str, Any]) -> GameProfile:
    game = _mapping(raw.get("game"), "game")
    process = _mapping(raw.get("process"), "process")
    window = _mapping(raw.get("window", {}), "window")
    camera = _mapping(raw.get("camera", {}), "camera")
    capabilities = _mapping(raw.get("capabilities", {}), "capabilities")
    safety = _mapping(raw.get("safety"), "safety")
    controls_raw = _mapping(raw.get("controls", {}), "controls")
    bindings: list[ControlBinding] = []
    for action, value in controls_raw.items():
        item = _mapping(value, f"controls.{action}")
        code: int | str = item["code"]
        bindings.append(
            ControlBinding(
                str(action),
                BindingKind(str(item.get("kind", BindingKind.SCAN_CODE))),
                code,
                bool(item.get("confirmed", False)),
            )
        )
    environment_class = EnvironmentClass(str(safety.get("environment_class", "unknown")))
    return GameProfile(
        game_id=str(game["id"]),
        display_name=str(game["display_name"]),
        executables=tuple(str(item) for item in process["executable"]),
        preferred_capture=str(window.get("preferred_capture", "auto")),
        controls=tuple(bindings),
        camera_type=str(camera.get("type", "relative_mouse")),
        camera_sensitivity=float(camera.get("sensitivity", 1.0)),
        capabilities=GameCapabilities(
            bool(capabilities.get("realtime_3d", False)),
            bool(capabilities.get("gui", False)),
            bool(capabilities.get("combat", False)),
            bool(capabilities.get("gamepad", False)),
        ),
        safety=EnvironmentSafetyManifest(
            environment_class,
            bool(safety.get("automation_allowed", False)),
            bool(safety.get("multiplayer", False)),
            bool(safety.get("anti_cheat_present", False)),
        ),
        capability_level=EnvironmentCapabilityLevel(int(raw.get("capability_level", 1))),
        window_title_pattern=(
            None if window.get("title_pattern") is None else str(window["title_pattern"])
        ),
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractViolation(f"{name} must be an object")
    return value
