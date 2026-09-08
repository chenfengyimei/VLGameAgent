from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from uga.control.lease import ControlMode, ControlOwner
from uga.control.lifetime import ActionLifetime
from uga.control.physical import PhysicalAction
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


@dataclass(frozen=True, slots=True)
class ActionProposal(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.action_proposal"

    proposal_id: str
    producer: str
    owner: ControlOwner
    mode: ControlMode
    lease_id: str
    lease_generation: int
    lifetime: ActionLifetime
    actions: tuple[PhysicalAction, ...]
    observation_id: str | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.proposal_id.strip() or not self.producer.strip() or not self.lease_id.strip():
            raise ContractViolation("proposal, producer, and lease ids are required")
        if self.lease_generation < 1 or not self.actions:
            raise ContractViolation("proposal requires a generation and at least one action")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("proposal confidence must be in [0, 1]")
        self.lifetime.validate()
        for action in self.actions:
            action.validate()
            if action.lifetime.created_at < self.lifetime.created_at:
                raise ContractViolation("action cannot predate its proposal")
            if action.lifetime.expires_at > self.lifetime.expires_at:
                raise ContractViolation("action cannot outlive its proposal")
