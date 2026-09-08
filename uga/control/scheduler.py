from __future__ import annotations

import asyncio
import contextlib
import heapq
from dataclasses import dataclass
from threading import Lock

from uga.control.arbiter import ArbiterDecision
from uga.control.executor import ExecutionReason, InputExecutor
from uga.control.lease import ControlLease
from uga.control.physical import PhysicalAction
from uga.core.errors import ContractViolation
from uga.time.clock import ClockBackend
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class SchedulerStats:
    queued: int
    executed: int
    expired: int
    rejected: int
    flushed: int


@dataclass(frozen=True, slots=True)
class _ScheduledAction:
    action: PhysicalAction
    target: WindowIdentity
    lease: ControlLease


class ActionScheduler:
    """Deterministic 30 Hz queue; only arbiter-approved work may enter it."""

    DEFAULT_HZ = 30.0

    def __init__(self, clock: ClockBackend, executor: InputExecutor) -> None:
        self._clock = clock
        self._executor = executor
        self._heap: list[tuple[int, int, _ScheduledAction]] = []
        self._action_ids: set[str] = set()
        self._sequence = 0
        self._executed = 0
        self._expired = 0
        self._rejected = 0
        self._flushed = 0
        self._lock = Lock()

    def schedule(
        self,
        decision: ArbiterDecision,
        target: WindowIdentity,
        lease: ControlLease,
    ) -> int:
        proposal = decision.proposal
        if not decision.accepted:
            with self._lock:
                self._rejected += len(proposal.actions)
            return 0
        if (
            proposal.lease_id != lease.lease_id
            or proposal.lease_generation != lease.generation
            or proposal.owner != lease.owner
            or proposal.mode != lease.mode
        ):
            raise ContractViolation("scheduler lease does not match accepted proposal")

        added = 0
        with self._lock:
            for action in proposal.actions:
                if action.action_id in self._action_ids:
                    self._rejected += 1
                    continue
                self._sequence += 1
                item = _ScheduledAction(action, target, lease)
                heapq.heappush(
                    self._heap,
                    (action.lifetime.effective_from.value_ns, self._sequence, item),
                )
                self._action_ids.add(action.action_id)
                added += 1
        return added

    def tick(self) -> SchedulerStats:
        """Execute all due work once; callers drive this at 30 Hz."""
        now = self._clock.now()
        due: list[_ScheduledAction] = []
        with self._lock:
            while self._heap and self._heap[0][0] <= now.value_ns:
                _, _, item = heapq.heappop(self._heap)
                self._action_ids.discard(item.action.action_id)
                due.append(item)

        for index, item in enumerate(due):
            result = self._executor.execute(item.action, item.target, item.lease)
            with self._lock:
                if result.executed:
                    self._executed += 1
                elif result.reason == ExecutionReason.EXPIRED:
                    self._expired += 1
                else:
                    # A runtime guard failed. Drop all future input so stale work
                    # cannot resume if focus later returns.
                    unexecuted_due = len(due) - index - 1
                    self._flushed += unexecuted_due + self._clear_locked()
                    break
        return self.stats()

    async def run(self, stop: asyncio.Event, *, frequency_hz: float = DEFAULT_HZ) -> None:
        if frequency_hz <= 0:
            raise ContractViolation("scheduler frequency must be positive")
        period_s = 1.0 / frequency_hz
        while not stop.is_set():
            self.tick()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=period_s)

    def flush(self) -> int:
        with self._lock:
            count = self._clear_locked()
            self._flushed += count
            return count

    def stats(self) -> SchedulerStats:
        with self._lock:
            return SchedulerStats(
                queued=len(self._heap),
                executed=self._executed,
                expired=self._expired,
                rejected=self._rejected,
                flushed=self._flushed,
            )

    def _clear_locked(self) -> int:
        count = len(self._heap)
        self._heap.clear()
        self._action_ids.clear()
        return count
