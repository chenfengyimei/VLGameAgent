from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from uga.capture.base import CaptureCapability
from uga.capture.frame import BufferHandle, PixelFormat
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class NativeCapturedFrame:
    frame_id: str
    capture_timestamp: UGATime
    present_estimate: UGATime | None
    width: int
    height: int
    stride_bytes: int
    pixel_format: PixelFormat
    physical_rect: Rect
    client_rect: Rect
    buffer_handle: BufferHandle


class NativeCaptureDriver(Protocol):
    """Narrow boundary implemented by a future PyO3/C-ABI native provider."""

    def probe(self, target: WindowIdentity) -> tuple[bool, str, CaptureCapability]: ...

    def start(self, target: WindowIdentity) -> None: ...

    def capture(self) -> NativeCapturedFrame: ...

    def stop(self) -> None: ...
