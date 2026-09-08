from __future__ import annotations

from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity


def identity(generation: int = 1) -> WindowIdentity:
    return WindowIdentity(
        hwnd=100,
        pid=200,
        executable_path_hash="a" * 64,
        process_start_time_100ns=300,
        window_generation=generation,
    )


def frame(number: int, timestamp_ns: int | None = None, backend: str = "fake") -> Frame:
    payload = bytes([number % 256]) * 16
    return Frame(
        frame_id=f"frame-{number}",
        capture_timestamp=UGATime(number if timestamp_ns is None else timestamp_ns),
        present_estimate=None,
        window_identity=identity(),
        width=2,
        height=2,
        stride_bytes=8,
        pixel_format=PixelFormat.BGRA8,
        physical_rect=Rect(-100, 20, -98, 22),
        client_rect=Rect(0, 0, 2, 2),
        source_backend=backend,
        buffer_handle=BufferHandle(
            handle_id=f"buffer-{number}",
            kind=BufferKind.CPU_BYTES,
            size_bytes=len(payload),
            payload=payload,
        ),
    )
