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
        self._last_pts: int | None = None

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
            # yuv420p chroma subsampling requires even dimensions, but windowed
            # captures (e.g. WGC borders around an emulator client) produce
            # arbitrary sizes; crop at most one row/column to stay encodable.
            encode_width = frame.width & ~1
            encode_height = frame.height & ~1
            if encode_width < 2 or encode_height < 2:
                raise ContractViolation("video frame is smaller than the 2x2 encode minimum")
            self._container = self._av.open(str(self._path), mode="w")
            self._stream = self._container.add_stream(self._codec, rate=self._fps)
            self._stream.width = encode_width
            self._stream.height = encode_height
            self._stream.pix_fmt = "yuv420p"
            self._stream.time_base = Fraction(1, 1_000_000)
            self._stream.codec_context.time_base = Fraction(1, 1_000_000)
        assert self._stream is not None
        encode_width = frame.width & ~1
        encode_height = frame.height & ~1
        pixel_format = "bgra" if frame.pixel_format == PixelFormat.BGRA8 else "rgba"
        video_frame = self._av.VideoFrame(encode_width, encode_height, pixel_format)
        plane = video_frame.planes[0]
        packed = bytearray(plane.buffer_size)
        row_bytes = encode_width * 4
        for row in range(encode_height):
            source_start = row * frame.stride_bytes
            target_start = row * plane.line_size
            packed[target_start : target_start + row_bytes] = source[
                source_start : source_start + row_bytes
            ]
        plane.update(packed)
        if encode_width != self._stream.width or encode_height != self._stream.height:
            # The window can resize mid-run (emulator sidebars, user layout
            # changes); an MP4 segment keeps one geometry, so resample the
            # frame into the registered size instead of killing a long
            # autonomous session over a cosmetic layout change.
            video_frame = video_frame.reformat(
                width=self._stream.width,
                height=self._stream.height,
                format=pixel_format,
            )
        timestamp_ns = frame.capture_timestamp.value_ns
        if self._last_timestamp_ns is not None and timestamp_ns < self._last_timestamp_ns:
            raise ContractViolation("video frame timestamps cannot regress")
        if self._start_timestamp_ns is None:
            self._start_timestamp_ns = timestamp_ns
        self._last_timestamp_ns = timestamp_ns
        # FPS is an encoder hint, not a replacement for capture timestamps.
        # Coarse 1/fps PTS used to stretch a 60 Hz capture recorded at 15 fps.
        pts = (timestamp_ns - self._start_timestamp_ns) // 1_000
        if self._last_pts is not None:
            pts = max(pts, self._last_pts + 1)
        self._last_pts = pts
        video_frame.pts = pts
        video_frame.time_base = Fraction(1, 1_000_000)
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
        self._start_timestamp_ns = None
        self._last_timestamp_ns = None
        self._last_pts = None
