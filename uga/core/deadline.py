"""Absolute budgets for single-flight blocking work and nested model requests.

The one daemon worker is poisoned on timeout/cancellation, not retried or
forcibly killed. Callers must stop rather than accumulate abandoned workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ParamSpec, TypeVar, cast

from uga.core.errors import BackendUnavailableError, ContractViolation

P = ParamSpec("P")
T = TypeVar("T")


class DeadlineExceeded(BackendUnavailableError):
    pass


@dataclass(slots=True)
class Deadline:
    expires_at: float
    cancelled: Callable[[], bool] = lambda: False
    aborted: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def after(cls, seconds: float, cancelled: Callable[[], bool] = lambda: False) -> Deadline:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise ContractViolation("operation deadline must be finite and positive")
        return cls(time.monotonic() + seconds, cancelled)

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if self.aborted.is_set() or remaining <= 0:
            raise DeadlineExceeded("operation exceeded its total deadline or was abandoned")
        return remaining


CURRENT_DEADLINE: contextvars.ContextVar[Deadline | None] = contextvars.ContextVar(
    "uga_operation_deadline", default=None
)


def remaining_timeout(default: float) -> float:
    deadline = CURRENT_DEADLINE.get()
    return default if deadline is None else min(default, deadline.remaining())


class BoundedWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = self._poisoned = False

    async def run(
        self, deadline: Deadline, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        deadline.remaining()
        with self._lock:
            if self._poisoned or self._busy:
                raise DeadlineExceeded("blocking worker unavailable; restart the stopped run")
            self._busy = True
        done = threading.Event()
        event_loop = asyncio.get_running_loop()
        completed: asyncio.Future[None] = event_loop.create_future()
        result: list[object] = []
        failure: list[BaseException] = []
        context = contextvars.copy_context()

        def notify() -> None:
            if not completed.done():
                completed.set_result(None)

        def invoke() -> None:
            token = CURRENT_DEADLINE.set(deadline)
            try:
                deadline.remaining()
                result.append(function(*args, **kwargs))
            except BaseException as exc:
                failure.append(exc)
            finally:
                CURRENT_DEADLINE.reset(token)
                done.set()
                with contextlib.suppress(RuntimeError):
                    event_loop.call_soon_threadsafe(notify)

        worker = threading.Thread(
            target=lambda: context.run(invoke), name="uga-bounded-operation", daemon=True
        )
        try:
            worker.start()
            while not done.is_set():
                if deadline.cancelled():
                    raise asyncio.CancelledError("run stopped during blocking operation")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(completed), timeout=min(0.01, deadline.remaining())
                    )
            deadline.remaining()
            if failure:
                raise failure[0]
            return cast(T, result[0])
        except (DeadlineExceeded, asyncio.CancelledError):
            deadline.aborted.set()
            with self._lock:
                self._poisoned = True
            raise
        finally:
            with self._lock:
                self._busy = False
