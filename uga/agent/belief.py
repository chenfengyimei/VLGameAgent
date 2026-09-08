from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class BeliefState(VersionedMixin):
    """Agent inference about the visible world, never privileged game state."""

    SCHEMA_NAME: ClassVar[str] = "uga.belief_state"

    game_id: str
    mode: ControlMode
    current_goal: str
    current_subgoal: str | None
    player_state: tuple[tuple[str, str], ...]
    target_state: tuple[tuple[str, str], ...]
    nearby_entities: tuple[str, ...]
    navigation_state: tuple[tuple[str, str], ...]
    combat_state: tuple[tuple[str, str], ...]
    gui_state: tuple[tuple[str, str], ...]
    progress_state: tuple[tuple[str, str], ...]
    last_success: str | None
    last_failure: str | None
    confidence: float
    updated_at: UGATime

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.game_id.strip() or not self.current_goal.strip():
            raise ContractViolation("belief state requires game id and current goal")
        if self.current_subgoal is not None and not self.current_subgoal.strip():
            raise ContractViolation("belief subgoal cannot be blank")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("belief confidence must be in [0, 1]")
        for section in (
            self.player_state,
            self.target_state,
            self.navigation_state,
            self.combat_state,
            self.gui_state,
            self.progress_state,
        ):
            keys = [key for key, _ in section]
            if any(not key.strip() for key in keys) or len(keys) != len(set(keys)):
                raise ContractViolation("belief section keys must be unique and non-empty")
        self.updated_at.validate()
