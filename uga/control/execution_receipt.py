"""Terminal execution receipts: one per physical primitive, exactly once.

Arbiter acceptance only means the primitive was *queued*.  These receipts are
the factual record of what the input executor actually did to the OS: every
primitive that enters the scheduler heap gets exactly one terminal receipt —
EXECUTED, REJECTED, EXPIRED or FLUSHED — with the attempt time, failure reason
and lease identity attached.  Effect verification and any downstream metrics
must consume this record instead of treating a queued proposal as an executed
action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uga.time.clock import UGATime
from uga.windows.window_identity import WindowIdentity


class ExecutionPrimitiveStatus(StrEnum):
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FLUSHED = "flushed"


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """One primitive's terminal outcome, published by the scheduler."""

    action_id: str
    proposal_id: str
    primitive: str
    status: ExecutionPrimitiveStatus
    at: UGATime
    target: WindowIdentity
    lease_id: str
    lease_generation: int
    failure_reason: str | None = None
    pre_action_observation_id: str | None = None
    pre_action_capture_ns: int | None = None

    @property
    def executed(self) -> bool:
        return self.status == ExecutionPrimitiveStatus.EXECUTED

    def to_envelope(self) -> dict[str, object]:
        """Versionable row: additive fields keep old episodes immutable."""
        return {
            "schema": "uga.execution-receipt/2",
            "action_id": self.action_id,
            "proposal_id": self.proposal_id,
            "primitive": self.primitive,
            "status": self.status.value,
            "at_ns": self.at.value_ns,
            "lease_id": self.lease_id,
            "lease_generation": self.lease_generation,
            "failure_reason": self.failure_reason,
            "pre_action_observation_id": self.pre_action_observation_id,
            "pre_action_capture_ns": self.pre_action_capture_ns,
        }


def aggregate_receipts(receipts: tuple[ExecutionReceipt, ...]) -> str:
    """Classify a logical action from its primitive receipts.

    ``executed`` requires every primitive to have reached the OS; a click
    whose move landed but whose button-up was refused is ``partial`` and must
    never be recorded as a completed click.  With no primitive executed the
    dominant failure status names the reason.
    """
    if not receipts:
        return "unknown"
    if all(receipt.executed for receipt in receipts):
        return "executed"
    if any(receipt.executed for receipt in receipts):
        return "partial"
    dominant = max(
        (receipt.status for receipt in receipts),
        key=lambda status: sum(
            1 for receipt in receipts if receipt.status == status
        ),
    )
    return dominant.value
