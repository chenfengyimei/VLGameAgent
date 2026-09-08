from __future__ import annotations

import asyncio

from uga.capture.base import CaptureBackend
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
from uga.core.errors import BackendStateError
from uga.core.events import EventBus, EventType
from uga.windows.window_identity import WindowIdentity


class CaptureSession:
    """Coordinates selection, failover, transport, and capture telemetry."""

    def __init__(
        self,
        target: WindowIdentity,
        registry: CaptureBackendRegistry,
        frames: FrameRingBuffer,
        events: EventBus,
    ) -> None:
        self._target = target
        self._registry = registry
        self._frames = frames
        self._events = events
        self._backend: CaptureBackend | None = None
        self._failed_backends: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def backend_id(self) -> str | None:
        return None if self._backend is None else self._backend.backend_id

    async def start(self) -> str:
        async with self._lock:
            if self._backend is not None:
                raise BackendStateError("capture session is already started")
            backend = await asyncio.to_thread(
                self._registry.start_best,
                self._target,
                exclude=frozenset(self._failed_backends),
            )
            self._backend = backend
        await self._events.publish(
            EventType.CAPTURE_BACKEND_SELECTED,
            "capture.session",
            {"backend_id": backend.backend_id, "hwnd": self._target.hwnd},
        )
        return backend.backend_id

    async def capture_once(self) -> SequencedFrame:
        failure: tuple[str, Exception] | None = None
        async with self._lock:
            backend = self._backend
            if backend is None:
                raise BackendStateError("capture session is not started")
            try:
                frame = await asyncio.to_thread(backend.capture)
            except Exception as capture_error:
                failed_id = backend.backend_id
                await asyncio.to_thread(backend.stop)
                self._backend = None
                self._failed_backends.add(failed_id)
                failure = (failed_id, capture_error)
            else:
                dropped_before = self._frames.dropped
                item = self._frames.publish(frame)
        if failure is not None:
            failed_id, failure_error = failure
            await self._events.publish(
                EventType.CAPTURE_BACKEND_FAILED,
                "capture.session",
                {"backend_id": failed_id, "error": str(failure_error)},
            )
            raise failure_error
        await self._events.publish(
            EventType.FRAME_CAPTURED,
            "capture.session",
            {
                "backend_id": frame.source_backend,
                "frame_id": frame.frame_id,
                "sequence": item.sequence,
                "capture_timestamp_ns": frame.capture_timestamp.value_ns,
            },
        )
        if self._frames.dropped > dropped_before:
            await self._events.publish(
                EventType.FRAME_DROPPED,
                "capture.session",
                {"reason": "ring_capacity", "dropped_total": self._frames.dropped},
            )
        return item

    async def recover(self) -> str:
        """Select the next healthy backend after `capture_once` reports a failure."""
        return await self.start()

    async def stop(self) -> None:
        async with self._lock:
            backend = self._backend
            self._backend = None
            if backend is not None:
                await asyncio.to_thread(backend.stop)
