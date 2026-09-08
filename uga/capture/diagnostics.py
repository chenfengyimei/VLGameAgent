from __future__ import annotations

import math
from dataclasses import dataclass

from uga.capture.frame import Frame
from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class CaptureDiagnostics:
    backend_id: str
    frame_count: int
    elapsed_seconds: float
    effective_fps: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    maximum_source_gap_ms: float
    timestamp_regressions: int
    resolution_changes: int


class CaptureDiagnosticsAccumulator:
    """Streaming diagnostics that never retains frame pixel buffers."""

    def __init__(self, backend_id: str) -> None:
        if not backend_id.strip():
            raise ContractViolation("capture diagnostics backend cannot be blank")
        self._backend_id = backend_id
        self._frame_count = 0
        self._latencies_ms: list[float] = []
        self._previous_timestamp_ns: int | None = None
        self._previous_size: tuple[int, int] | None = None
        self._maximum_source_gap_ns = 0
        self._timestamp_regressions = 0
        self._resolution_changes = 0

    def add(self, frame: Frame, capture_latency_ms: float) -> None:
        if capture_latency_ms < 0 or not math.isfinite(capture_latency_ms):
            raise ContractViolation("capture latency sample is invalid")
        frame.validate()
        timestamp_ns = frame.capture_timestamp.value_ns
        size = (frame.width, frame.height)
        if self._previous_timestamp_ns is not None:
            gap_ns = timestamp_ns - self._previous_timestamp_ns
            if gap_ns < 0:
                self._timestamp_regressions += 1
            else:
                self._maximum_source_gap_ns = max(self._maximum_source_gap_ns, gap_ns)
        if self._previous_size is not None and size != self._previous_size:
            self._resolution_changes += 1
        self._previous_timestamp_ns = timestamp_ns
        self._previous_size = size
        self._latencies_ms.append(capture_latency_ms)
        self._frame_count += 1

    def summarize(self, *, elapsed_seconds: float) -> CaptureDiagnostics:
        if self._frame_count < 1 or elapsed_seconds <= 0 or not math.isfinite(elapsed_seconds):
            raise ContractViolation("capture diagnostics samples are invalid")
        ordered = sorted(self._latencies_ms)

        def percentile(fraction: float) -> float:
            return ordered[math.ceil(fraction * len(ordered)) - 1]

        return CaptureDiagnostics(
            self._backend_id,
            self._frame_count,
            elapsed_seconds,
            self._frame_count / elapsed_seconds,
            percentile(0.50),
            percentile(0.95),
            percentile(0.99),
            self._maximum_source_gap_ns / 1_000_000,
            self._timestamp_regressions,
            self._resolution_changes,
        )


def summarize_capture(
    backend_id: str,
    frames: tuple[Frame, ...],
    capture_latencies_ms: tuple[float, ...],
    *,
    elapsed_seconds: float,
) -> CaptureDiagnostics:
    if (
        not backend_id.strip()
        or not frames
        or len(frames) != len(capture_latencies_ms)
        or elapsed_seconds <= 0
        or not math.isfinite(elapsed_seconds)
        or any(value < 0 or not math.isfinite(value) for value in capture_latencies_ms)
    ):
        raise ContractViolation("capture diagnostics samples are invalid")
    accumulator = CaptureDiagnosticsAccumulator(backend_id)
    for frame, latency in zip(frames, capture_latencies_ms, strict=True):
        accumulator.add(frame, latency)
    return accumulator.summarize(elapsed_seconds=elapsed_seconds)
