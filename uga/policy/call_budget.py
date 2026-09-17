"""One monotonic deadline for a decision, its retries and its verifier.

Synchronous I/O cannot be forcibly killed in Python. Timed-out workers are
retired, never reused, and their late results cannot become executable input.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import math
import threading
import time
from collections.abc import Callable, Iterator
from typing import TypeVar

from uga.core.deadline import remaining_timeout
from uga.core.errors import ContractViolation
from uga.policy.vision_transport import ProviderError, ProviderErrorKind

T = TypeVar("T")


class CallBudget:
    def __init__(
        self, timeout_s: float, *, max_requests: int = 4,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ContractViolation("decision deadline must be finite and positive")
        if type(max_requests) is not int or max_requests < 1:
            raise ContractViolation("decision request cap must be a positive integer")
        self.deadline = time.monotonic() + timeout_s
        self._cancelled = threading.Event()
        self._external_cancelled = cancelled
        self._requests = 0
        self._max_requests = max_requests
        self._lock = threading.Lock()

    def remaining(self) -> float:
        seconds = self.deadline - time.monotonic()
        if self._cancelled.is_set() or seconds <= 0:
            raise ProviderError(
                ProviderErrorKind.TIMEOUT,
                "decision deadline expired or request cancelled; worker retired",
                fatal=True,
            )
        return seconds

    def request(self) -> None:
        self.remaining()
        with self._lock:
            if self._requests >= self._max_requests:
                raise ProviderError(
                    ProviderErrorKind.INVALID_REQUEST,
                    "decision HTTP request budget exhausted", fatal=True,
                )
            self._requests += 1

    def cancel(self) -> None:
        self._cancelled.set()


_current: contextvars.ContextVar[CallBudget | None] = contextvars.ContextVar(
    "uga_decision_budget", default=None
)


@contextlib.contextmanager
def decision_budget(timeout_s: float = 60.0) -> Iterator[CallBudget]:
    """Nested fallback/repair/verifier calls inherit, never reset, the deadline."""
    budget = _current.get() or CallBudget(remaining_timeout(timeout_s))
    token = _current.set(budget)
    try:
        budget.remaining()
        yield budget
        budget.remaining()
    finally:
        _current.reset(token)


def checkpoint() -> None:
    remaining_timeout(60.0)
    budget = _current.get()
    if budget is not None:
        budget.remaining()


def request_timeout(default_s: float) -> float:
    default_s = remaining_timeout(default_s)
    budget = _current.get()
    if budget is None:
        return default_s
    budget.request()
    return min(default_s, budget.remaining())


class DeadlineWorker:
    """At most one active thread per worker, zero detached-thread retries.

    Unlike asyncio.to_thread this worker is not joined by asyncio.run's default
    executor shutdown. A permanently stuck provider cannot trap the input
    shutdown path. Cancellation/timeout makes this worker unusable thereafter.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._busy = False
        self._retired = False

    async def call(self, operation: Callable[[], T], budget: CallBudget) -> T:
        budget.remaining()
        with self._lock:
            if self._busy or self._retired:
                raise ProviderError(
                    ProviderErrorKind.TIMEOUT, "decision worker busy or retired", fatal=True
                )
            self._busy = True
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        context = contextvars.copy_context()
        completed = threading.Event()

        def deliver(result: T | None, failure: BaseException | None) -> None:
            if future.done():
                return
            if failure is not None:
                future.set_exception(failure)
            else:
                # None is a legitimate result for a side-effect-only operation.
                future.set_result(result)  # type: ignore[arg-type]

        def invoke() -> None:
            token = _current.set(budget)
            result: T | None = None
            failure: BaseException | None = None
            try:
                budget.remaining()
                result = operation()
                budget.remaining()
            except BaseException as exc:
                failure = exc
            finally:
                _current.reset(token)
                completed.set()
                with self._lock:
                    self._busy = False
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(deliver, result, failure)

        thread = threading.Thread(
            target=lambda: context.run(invoke), name=self._name, daemon=True
        )
        try:
            thread.start()
            while not completed.is_set():
                if budget._external_cancelled is not None and budget._external_cancelled():
                    raise asyncio.CancelledError("run stopped during blocking operation")
                await asyncio.wait({future}, timeout=min(0.02, budget.remaining()))
            budget.remaining()
            # A result already completed when stop arrived is delivered so the
            # run-stamp guard can audit and discard it, never execute it.
            return await future
        except BaseException:
            if not future.done():
                budget.cancel()
                future.cancel()
                with self._lock:
                    self._retired = True
            raise
