from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class ActionLifetime(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.action_lifetime"

    created_at: UGATime
    effective_from: UGATime
    expires_at: UGATime

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self.created_at.validate()
        self.effective_from.validate()
        self.expires_at.validate()
        if self.created_at > self.effective_from:
            raise ContractViolation("action cannot become effective before it is created")
        if self.effective_from >= self.expires_at:
            raise ContractViolation("action expiration must be after its effective time")

    def is_effective(self, now: UGATime) -> bool:
        return self.effective_from <= now <= self.expires_at

    def is_expired(self, now: UGATime) -> bool:
        return now > self.expires_at
