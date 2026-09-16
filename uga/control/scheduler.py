from __future__ import annotations

import asyncio
import contextlib
import heapq
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from uga.control.arbiter import ArbiterDecision
from uga.control.execution_receipt import ExecutionPrimitiveStatus, ExecutionReceipt
from uga.control.executor import ExecutionReason, InputExecutor
from uga.control.lease import ControlLease
from uga.control.lease_manager import ControlLeaseManager
from uga.control.physical import PhysicalAction
from uga.core.errors import ContractViolation
from uga.time.clock import ClockBackend, UGATime
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class SchedulerStats:
    queued: int
    scheduled: int
    executed: int
    expired: int
    rejected: int
    flushed: int


@dataclass(frozen=True, slots=True)
class _ScheduledAction:
    action: PhysicalAction
    target: WindowIdentity
    lease: ControlLease
    proposal_id: str


class ActionScheduler:
    """Deterministic 30 Hz queue; only arbiter-approved work may enter it.

    Every primitive that enters the heap leaves exactly one terminal
    ExecutionReceipt (EXECUTED / REJECTED / EXPIRED / FLUSHED) in a bounded
    ring, so downstream consumers can distinguish "the arbiter accepted it"
    from "the OS actually received it".  Proposals rejected by the arbiter or
    dropped as duplicate ids never enter the heap and produce no receipts —
    they are only counted in ``stats().rejected``.
    """

    DEFAULT_HZ = 30.0
    _RECEIPT_RING = 1024

    def __init__(
        self,
        clock: ClockBackend,
        executor: InputExecutor,
        leases: ControlLeaseManager,
    ) -> None:
        self._clock = clock
        self._executor = executor
        self._heap: list[tuple[int, int, _ScheduledAction]] = []
        self._action_ids: set[str] = set()
        self._sequence = 0
        self._scheduled = 0
        self._executed = 0
        self._expired = 0
        self._rejected = 0
        self._flushed = 0
        self._receipts: deque[ExecutionReceipt] = deque(maxlen=self._RECEIPT_RING)
        self._lock = Lock()
        leases.register_authority_loss_handler(self.neutralize)

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
                item = _ScheduledAction(action, target, lease, proposal.proposal_id)
                heapq.heappush(
                    self._heap,
                    (action.lifetime.effective_from.value_ns, self._sequence, item),
                )
                self._action_ids.add(action.action_id)
                added += 1
            self._scheduled += added
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
            try:
                result = self._executor.execute(item.action, item.target, item.lease)
            except BaseException as exc:
                with self._lock:
                    self._rejected += 1
                    self._flushed += len(due) - index - 1 + len(self._clear_locked(now))
                    self._publish_receipt_locked(
                        item,
                        ExecutionPrimitiveStatus.REJECTED,
                        now,
                        failure_reason=f"executor exception: {exc}",
                    )
                    self._publish_flushed_receipts_locked(due[index + 1 :], now)
                raise
            with self._lock:
                if result.executed:
                    self._executed += 1
                    self._publish_receipt_locked(
                        item, ExecutionPrimitiveStatus.EXECUTED, result.executed_at
                    )
                elif result.reason == ExecutionReason.EXPIRED:
                    self._expired += 1
                    self._flushed += len(due) - index - 1 + len(self._clear_locked(now))
                    self._publish_receipt_locked(
                        item,
                        ExecutionPrimitiveStatus.EXPIRED,
                        result.executed_at,
                        failure_reason="action lifetime expired before execution",
                    )
                    self._publish_flushed_receipts_locked(due[index + 1 :], now)
                    break
                else:
                    # A runtime guard failed. Drop all future input so stale work
                    # cannot resume if focus later returns.
                    unexecuted_due = len(due) - index - 1
                    self._rejected += 1
                    self._flushed += unexecuted_due + len(self._clear_locked(now))
                    self._publish_receipt_locked(
                        item,
                        ExecutionPrimitiveStatus.REJECTED,
                        result.executed_at,
                        failure_reason=str(result.reason),
                    )
                    self._publish_flushed_receipts_locked(due[index + 1 :], now)
                    break
        return self.stats()

    async def run(
        self,
        stop: asyncio.Event,
        *,
        frequency_hz: float = DEFAULT_HZ,
        heartbeat: Callable[[], object] | None = None,
    ) -> None:
        if frequency_hz <= 0:
            raise ContractViolation("scheduler frequency must be positive")
        period_s = 1.0 / frequency_hz
        try:
            while not stop.is_set():
                self.tick()
                if heartbeat is not None:
                    # Heartbeats ride the control plane: they prove the
                    # scheduler kept draining, not that a blind timer fired.
                    heartbeat()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=period_s)
        finally:
            self.neutralize()

    def flush(self) -> int:
        with self._lock:
            cleared = self._clear_locked(self._clock.now())
            self._flushed += len(cleared)
            return len(cleared)

    def neutralize(self) -> int:
        """Drop queued work and actively release every stateful input backend."""
        count = self.flush()
        self._executor.release_all()
        return count

    def drain_receipts(self) -> tuple[ExecutionReceipt, ...]:
        """Pop every terminal receipt published since the last drain."""
        with self._lock:
            receipts = tuple(self._receipts)
            self._receipts.clear()
            return receipts

    def stats(self) -> SchedulerStats:
        with self._lock:
            return SchedulerStats(
                queued=len(self._heap),
                scheduled=self._scheduled,
                executed=self._executed,
                expired=self._expired,
                rejected=self._rejected,
                flushed=self._flushed,
            )

    def _publish_receipt_locked(
        self,
        item: _ScheduledAction,
        status: ExecutionPrimitiveStatus,
        at: UGATime,
        *,
        failure_reason: str | None = None,
    ) -> None:
        self._receipts.append(
            ExecutionReceipt(
                action_id=item.action.action_id,
                proposal_id=item.proposal_id,
                primitive=type(item.action).__name__,
                status=status,
                at=at,
                target=item.target,
                lease_id=item.lease.lease_id,
                lease_generation=item.lease.generation,
                failure_reason=failure_reason,
            )
        )

    def _publish_flushed_receipts_locked(
        self, items: list[_ScheduledAction], at: UGATime
    ) -> None:
        for item in items:
            self._publish_receipt_locked(item, ExecutionPrimitiveStatus.FLUSHED, at)

    def _clear_locked(self, at: UGATime) -> list[_ScheduledAction]:
        cleared = [entry[2] for entry in self._heap]
        self._heap.clear()
        self._action_ids.clear()
        self._publish_flushed_receipts_locked(cleared, at)
        return cleared
