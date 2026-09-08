from __future__ import annotations

from uga.control.lifetime import ActionLifetime
from uga.time.clock import UGATime


def should_drop(lifetime: ActionLifetime, now: UGATime) -> bool:
    """Expired work is always dropped and never compensated later."""
    return lifetime.is_expired(now)
