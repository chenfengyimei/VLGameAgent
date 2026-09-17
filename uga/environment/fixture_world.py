from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from uga.core.errors import ContractViolation


class FixtureMode(StrEnum):
    PLAY_3D = "PLAY_3D"
    GUI = "GUI"
    COMPLETE = "COMPLETE"


class FixtureScenario(StrEnum):
    EXPLORATION = "exploration"
    REALTIME_CONTROL = "realtime_control"
    GUI_NAVIGATION = "gui_navigation"
    HELDOUT_DIAGONAL = "heldout_diagonal"


def fixture_policy_features(
    *,
    player_x: float,
    player_y: float,
    target_x: float,
    target_y: float,
    width: int,
    height: int,
    success: bool,
    scenario: FixtureScenario,
) -> tuple[float, ...]:
    scenario_features = tuple(float(scenario == item) for item in FixtureScenario)
    return (
        (target_x - player_x) / max(width, 1),
        (target_y - player_y) / max(height, 1),
        float(success),
        1.0,
        *scenario_features,
    )


@dataclass(frozen=True, slots=True)
class FixtureSnapshot:
    player_x: float
    player_y: float
    target_x: float
    target_y: float
    heading_radians: float
    mode: FixtureMode
    success: bool
    elapsed_ns: int

    @property
    def distance_to_target(self) -> float:
        return math.hypot(self.target_x - self.player_x, self.target_y - self.player_y)


class FixtureWorld:
    """Deterministic, developer-owned target for supervised Windows I/O tests."""

    def __init__(
        self,
        *,
        width: int = 800,
        height: int = 600,
        scenario: FixtureScenario = FixtureScenario.EXPLORATION,
    ) -> None:
        if width < 320 or height < 240:
            raise ContractViolation("fixture world must be at least 320x240")
        self.width = width
        self.height = height
        self.scenario = scenario
        self.speed_pixels_per_second = (
            280.0 if scenario == FixtureScenario.HELDOUT_DIAGONAL else 220.0
        )
        self._pressed: set[str] = set()
        self.reset()

    def reset(self) -> None:
        if self.scenario == FixtureScenario.EXPLORATION:
            positions = (90.0, self.height / 2, self.width - 100.0, self.height / 2)
        elif self.scenario == FixtureScenario.REALTIME_CONTROL:
            positions = (self.width - 90.0, self.height / 2, 100.0, self.height / 2)
        elif self.scenario == FixtureScenario.GUI_NAVIGATION:
            positions = (self.width / 2, 90.0, self.width / 2, self.height - 100.0)
        else:
            positions = (90.0, 90.0, 500.0, 500.0)
        self._player_x, self._player_y, self._target_x, self._target_y = positions
        self._heading = 0.0
        self._mode = FixtureMode.PLAY_3D
        self._success = False
        self._elapsed_ns = 0
        self._pressed.clear()

    @property
    def game_id(self) -> str:
        return f"uga-fixture-{self.scenario.value.replace('_', '-')}"

    @property
    def window_title(self) -> str:
        if self.scenario == FixtureScenario.EXPLORATION:
            return "UGA Fixture World"
        return f"UGA Fixture World [{self.scenario.value}]"

    @property
    def movement_keys(self) -> tuple[str, ...]:
        return {
            FixtureScenario.EXPLORATION: ("d",),
            FixtureScenario.REALTIME_CONTROL: ("a",),
            FixtureScenario.GUI_NAVIGATION: ("s",),
            FixtureScenario.HELDOUT_DIAGONAL: ("d", "s"),
        }[self.scenario]

    @property
    def canonical_movement(self) -> tuple[float, float]:
        horizontal = float(("d" in self.movement_keys) - ("a" in self.movement_keys))
        # Canonical +Y is forward/W; screen pixel +Y instead points down.
        vertical = float(("w" in self.movement_keys) - ("s" in self.movement_keys))
        magnitude = max(1.0, math.hypot(horizontal, vertical))
        return horizontal / magnitude, vertical / magnitude

    @property
    def snapshot(self) -> FixtureSnapshot:
        return FixtureSnapshot(
            self._player_x,
            self._player_y,
            self._target_x,
            self._target_y,
            self._heading,
            self._mode,
            self._success,
            self._elapsed_ns,
        )

    def set_key(self, key: str, is_down: bool) -> None:
        normalized = key.casefold()
        was_down = normalized in self._pressed
        if is_down:
            self._pressed.add(normalized)
        else:
            self._pressed.discard(normalized)
        if not is_down or was_down:
            return
        if normalized == "escape" and not self._success:
            self._mode = FixtureMode.PLAY_3D if self._mode == FixtureMode.GUI else FixtureMode.GUI
        elif normalized == "r":
            self.reset()
        elif (
            normalized in {"e", "f"}
            and self._mode == FixtureMode.PLAY_3D
            and self.snapshot.distance_to_target <= 55
        ):
            self._success = True
            self._mode = FixtureMode.COMPLETE

    def add_mouse_delta(self, delta_x: float) -> None:
        if not math.isfinite(delta_x):
            raise ContractViolation("fixture mouse delta must be finite")
        if self._mode == FixtureMode.PLAY_3D:
            self._heading = (self._heading + delta_x * 0.004) % (2 * math.pi)

    def click(self, x: float, y: float) -> None:
        if self._mode != FixtureMode.GUI:
            return
        if self.width / 2 - 100 <= x <= self.width / 2 + 100:
            if self.height / 2 - 45 <= y <= self.height / 2 + 5:
                self._mode = FixtureMode.PLAY_3D
            elif self.height / 2 + 20 <= y <= self.height / 2 + 70:
                self.reset()

    def tick(self, elapsed_ns: int) -> FixtureSnapshot:
        if elapsed_ns < 0:
            raise ContractViolation("fixture elapsed time cannot be negative")
        elapsed_ns = min(elapsed_ns, 250_000_000)
        self._elapsed_ns += elapsed_ns
        if self._mode != FixtureMode.PLAY_3D:
            return self.snapshot
        horizontal = float(("d" in self._pressed) - ("a" in self._pressed))
        vertical = float(("s" in self._pressed) - ("w" in self._pressed))
        magnitude = math.hypot(horizontal, vertical)
        if magnitude:
            seconds = elapsed_ns / 1_000_000_000
            distance = self.speed_pixels_per_second * seconds
            self._player_x += horizontal / magnitude * distance
            self._player_y += vertical / magnitude * distance
            self._player_x = min(max(25.0, self._player_x), self.width - 25.0)
            self._player_y = min(max(60.0, self._player_y), self.height - 25.0)
        return self.snapshot
