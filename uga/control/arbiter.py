from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uga.control.lease_manager import ControlLeaseManager
from uga.control.proposal import ActionProposal
from uga.time.clock import ClockBackend, UGATime


class ArbiterReason(StrEnum):
    ACCEPTED = "accepted"
    PROPOSAL_EXPIRED = "proposal_expired"
    LEASE_INVALID = "lease_invalid"
    LEASE_MISMATCH = "lease_mismatch"
    PROPOSAL_OUTLIVES_LEASE = "proposal_outlives_lease"


@dataclass(frozen=True, slots=True)
class ArbiterDecision:
    accepted: bool
    reason: ArbiterReason
    decided_at: UGATime
    proposal: ActionProposal


class ActionArbiter:
    """The only gate allowed to authorize proposals for scheduling."""

    def __init__(self, clock: ClockBackend, leases: ControlLeaseManager) -> None:
        self._clock = clock
        self._leases = leases

    def decide(self, proposal: ActionProposal) -> ArbiterDecision:
        proposal.validate()
        now = self._clock.now()
        if proposal.lifetime.is_expired(now):
            return ArbiterDecision(False, ArbiterReason.PROPOSAL_EXPIRED, now, proposal)
        lease = self._leases.current(now)
        if lease is None or not self._leases.validate(lease, now):
            return ArbiterDecision(False, ArbiterReason.LEASE_INVALID, now, proposal)
        if (
            proposal.lease_id != lease.lease_id
            or proposal.lease_generation != lease.generation
            or proposal.owner != lease.owner
            or proposal.mode != lease.mode
        ):
            return ArbiterDecision(False, ArbiterReason.LEASE_MISMATCH, now, proposal)
        if proposal.lifetime.expires_at > lease.expires_at:
            return ArbiterDecision(False, ArbiterReason.PROPOSAL_OUTLIVES_LEASE, now, proposal)
        return ArbiterDecision(True, ArbiterReason.ACCEPTED, now, proposal)
