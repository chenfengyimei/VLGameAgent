from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


class ControlMode(StrEnum):
    PLAY_3D = "play_3d"
    PLAY_2D = "play_2d"
    GUI = "gui"
    DIALOGUE = "dialogue"
    CUTSCENE = "cutscene"
    LOADING = "loading"
    PAUSED = "paused"
    DEAD = "dead"
    UNKNOWN = "unknown"


class ControlOwner(IntEnum):
    BACKGROUND = 10
    FAST_POLICY = 20
    GUI_AGENT = 30
    RECOVERY = 40
    MANUAL = 50
    EMERGENCY = 60


@dataclass(frozen=True, slots=True)
class ControlLease(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.control_lease"

    lease_id: str
    owner: ControlOwner
    mode: ControlMode
    generation: int
    issued_at: UGATime
    expires_at: UGATime
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.lease_id.strip() or self.generation < 1:
            raise ContractViolation("lease id and positive generation are required")
        if self.issued_at >= self.expires_at:
            raise ContractViolation("lease expiration must follow issue time")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("lease confidence must be in [0, 1]")
        if not self.reason.strip():
            raise ContractViolation("lease reason cannot be empty")

    def is_expired(self, now: UGATime) -> bool:
        return now > self.expires_at
