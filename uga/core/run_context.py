from __future__ import annotations

import uuid
from dataclasses import dataclass
from threading import Lock

from uga.safety.shutdown import SafetyShutdown


@dataclass(frozen=True, slots=True)
class RunStamp:
    """Generation binding issued with every planning or verification request."""

    run_id: str
    generation: int


class RunContext:
    """Latched run lifecycle shared by the composition root and the agent loop.

    Safety ownership stays with :class:`SafetyShutdown` (input de-authorization,
    lease revoke, queue flush, input release). This context adds the run
    identity and a monotonic generation so requests stamped before a stop or a
    supervisor rebuild lose execution eligibility instead of being refreshed: a
    late model result must never regain authority by adopting the current
    generation, and a cancelled run cannot be un-cancelled by the loop.
    """

    def __init__(self, shutdown: SafetyShutdown | None = None) -> None:
        self._shutdown = shutdown
        self._run_id = uuid.uuid4().hex
        self._generation = 1
        self._cancelled = False
        self._lock = Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def stamp(self) -> RunStamp:
        with self._lock:
            return RunStamp(self._run_id, self._generation)

    def is_live(self, stamp: RunStamp) -> bool:
        with self._lock:
            if (
                self._cancelled
                or stamp.run_id != self._run_id
                or stamp.generation != self._generation
            ):
                return False
        shutdown = self._shutdown
        return shutdown is None or shutdown.tripped is None

    def should_stop(self) -> bool:
        if self.cancelled:
            return True
        shutdown = self._shutdown
        return shutdown is not None and shutdown.tripped is not None

    def advance_generation(self) -> int:
        """Invalidate every outstanding stamp (used on supervisor rebuilds)."""
        with self._lock:
            self._generation += 1
            return self._generation

    def cancel(self) -> None:
        """Latch the stop; all outstanding stamps die with the old generation."""
        with self._lock:
            self._cancelled = True
            self._generation += 1
