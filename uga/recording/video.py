from __future__ import annotations

import importlib
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from uga.capture.frame import BufferKind, Frame, PixelFormat
from uga.core.errors import BackendUnavailableError, ContractViolation


@runtime_checkable
class VideoSink(Protocol):
    def append(self, frame: Frame) -> None: ...

    def close(self) -> None: ...


class PyAvVideoRecorder:
    """MP4 recorder that preserves the raw-source cadence supplied by capture."""

    def __init__(self, path: Path, *, fps: int = 30, codec: str = "libx264") -> None:
        if fps <= 0:
            raise ContractViolation("video FPS must be positive")
        try:
            av = importlib.import_module("av")
        except ImportError as exc:
            raise BackendUnavailableError(
                "MP4 recording requires the declared av dependency"
            ) from exc
        self._av = av
        self._path = path
        self._fps = fps
        self._codec = codec
        self._container: Any | None = None
        self._stream: Any | None = None
        self._start_timestamp_ns: int | None = None
        self._last_timestamp_ns: int | None = None

    def append(self, frame: Frame) -> None:
        frame.validate()
        handle = frame.buffer_handle
        if handle.kind != BufferKind.CPU_BYTES:
            raise ContractViolation("video recorder currently requires a CPU byte frame")
        if frame.pixel_format not in (PixelFormat.BGRA8, PixelFormat.RGBA8):
            raise ContractViolation("video recorder supports BGRA8 and RGBA8 frames")
        source = handle.readonly_view()
        row_bytes = frame.width * 4
        if frame.stride_bytes < row_bytes or source.nbytes < frame.stride_bytes * frame.height:
            raise ContractViolation("video frame buffer is smaller than its declared stride")
        if self._container is None:
            self._container = self._av.open(str(self._path), mode="w")
            self._stream = self._container.add_stream(self._codec, rate=self._fps)
            self._stream.width = frame.width
            self._stream.height = frame.height
            self._stream.pix_fmt = "yuv420p"
        assert self._stream is not None
        if frame.width != self._stream.width or frame.height != self._stream.height:
            raise ContractViolation("video resolution changed within one MP4 segment")
        pixel_format = "bgra" if frame.pixel_format == PixelFormat.BGRA8 else "rgba"
        video_frame = self._av.VideoFrame(frame.width, frame.height, pixel_format)
        plane = video_frame.planes[0]
        packed = bytearray(plane.buffer_size)
        for row in range(frame.height):
            source_start = row * frame.stride_bytes
            target_start = row * plane.line_size
            packed[target_start : target_start + row_bytes] = source[
                source_start : source_start + row_bytes
            ]
        plane.update(packed)
        timestamp_ns = frame.capture_timestamp.value_ns
        if self._last_timestamp_ns is not None and timestamp_ns < self._last_timestamp_ns:
            raise ContractViolation("video frame timestamps cannot regress")
        if self._start_timestamp_ns is None:
            self._start_timestamp_ns = timestamp_ns
        self._last_timestamp_ns = timestamp_ns
        video_frame.pts = timestamp_ns - self._start_timestamp_ns
        video_frame.time_base = Fraction(1, 1_000_000_000)
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)

    def close(self) -> None:
        if self._container is None:
            return
        assert self._stream is not None
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()
        self._container = None
        self._stream = None
