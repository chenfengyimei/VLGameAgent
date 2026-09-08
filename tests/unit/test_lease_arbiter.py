from __future__ import annotations

import unittest

from uga.control.arbiter import ActionArbiter, ArbiterReason
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.control.proposal import ActionProposal
from uga.core.errors import LeaseDeniedError
from uga.time.clock import ManualClock, UGATime


def proposal_for(lease, start: int, end: int) -> ActionProposal:  # type: ignore[no-untyped-def]
    lifetime = ActionLifetime(UGATime(start), UGATime(start), UGATime(end))
    return ActionProposal(
        "proposal-1",
        "test",
        lease.owner,
        lease.mode,
        lease.lease_id,
        lease.generation,
        lifetime,
        (KeyboardAction("key-1", lifetime, 0x11, True),),
    )


class LeaseArbiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(100)
        self.leases = ControlLeaseManager(self.clock)
        self.arbiter = ActionArbiter(self.clock, self.leases)

    def test_higher_priority_owner_preempts_lower_priority(self) -> None:
        fast = self.leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            100,
            confidence=0.9,
            reason="play",
        )
        manual = self.leases.grant(
            ControlOwner.MANUAL,
            ControlMode.PLAY_3D,
            100,
            confidence=1.0,
            reason="takeover",
        )
        self.assertFalse(self.leases.validate(fast))
        self.assertTrue(self.leases.validate(manual))

    def test_lower_priority_cannot_preempt(self) -> None:
        self.leases.grant(
            ControlOwner.MANUAL,
            ControlMode.PLAY_3D,
            100,
            confidence=1.0,
            reason="takeover",
        )
        with self.assertRaises(LeaseDeniedError):
            self.leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                100,
                confidence=0.9,
                reason="play",
            )

    def test_arbiter_accepts_only_exact_live_lease(self) -> None:
        lease = self.leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            100,
            confidence=0.9,
            reason="play",
        )
        self.assertEqual(
            self.arbiter.decide(proposal_for(lease, 100, 150)).reason, ArbiterReason.ACCEPTED
        )
        self.leases.revoke(lease.lease_id)
        self.assertEqual(
            self.arbiter.decide(proposal_for(lease, 100, 150)).reason,
            ArbiterReason.LEASE_INVALID,
        )

    def test_expired_proposal_is_never_backfilled(self) -> None:
        lease = self.leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            100,
            confidence=0.9,
            reason="play",
        )
        proposal = proposal_for(lease, 100, 110)
        self.clock.set(111)
        self.assertEqual(self.arbiter.decide(proposal).reason, ArbiterReason.PROPOSAL_EXPIRED)


if __name__ == "__main__":
    unittest.main()
