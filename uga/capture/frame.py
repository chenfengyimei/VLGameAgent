from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity


class PixelFormat(StrEnum):
    BGRA8 = "bgra8"
    RGBA8 = "rgba8"
    RGB8 = "rgb8"
    NV12 = "nv12"
    R10G10B10A2 = "r10g10b10a2"


class BufferKind(StrEnum):
    CPU_BYTES = "cpu_bytes"
    NATIVE = "native"
    GPU_TEXTURE = "gpu_texture"
    SHARED_MEMORY = "shared_memory"


@dataclass(frozen=True, slots=True)
class BufferHandle:
    """Opaque frame storage reference; payload is intentionally not serialized."""

    handle_id: str
    kind: BufferKind
    size_bytes: int
    payload: Any = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.handle_id.strip():
            raise ContractViolation("buffer handle id cannot be empty")
        if self.size_bytes < 0:
            raise ContractViolation("buffer size cannot be negative")

    def readonly_view(self) -> memoryview:
        if self.kind != BufferKind.CPU_BYTES:
            raise ContractViolation(f"buffer {self.handle_id!r} is not CPU-addressable")
        try:
            view = memoryview(self.payload)
        except TypeError as error:
            raise ContractViolation(
                "CPU buffer payload does not implement the buffer protocol"
            ) from error
        if view.nbytes != self.size_bytes:
            raise ContractViolation("CPU buffer size does not match its descriptor")
        return view.toreadonly()

    def descriptor(self) -> dict[str, object]:
        return {
            "handle_id": self.handle_id,
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class Frame(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.frame"

    frame_id: str
    capture_timestamp: UGATime
    present_estimate: UGATime | None
    window_identity: WindowIdentity
    width: int
    height: int
    stride_bytes: int
    pixel_format: PixelFormat
    physical_rect: Rect
    client_rect: Rect
    source_backend: str
    buffer_handle: BufferHandle

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.frame_id.strip():
            raise ContractViolation("frame id cannot be empty")
        self.capture_timestamp.validate()
        if self.present_estimate is not None:
            self.present_estimate.validate()
        self.window_identity.validate()
        if self.width < 1 or self.height < 1:
            raise ContractViolation("frame dimensions must be positive")
        bytes_per_pixel = {
            PixelFormat.BGRA8: 4,
            PixelFormat.RGBA8: 4,
            PixelFormat.RGB8: 3,
            PixelFormat.NV12: 1,
            PixelFormat.R10G10B10A2: 4,
        }[self.pixel_format]
        if self.stride_bytes < self.width * bytes_per_pixel:
            raise ContractViolation("frame stride is too small")
        minimum_buffer_bytes = self.stride_bytes * self.height
        if (
            self.buffer_handle.kind in {BufferKind.CPU_BYTES, BufferKind.SHARED_MEMORY}
            and self.buffer_handle.size_bytes < minimum_buffer_bytes
        ):
            raise ContractViolation("frame buffer cannot cover its declared rows")
        if not self.source_backend.strip():
            raise ContractViolation("source backend cannot be empty")

    def to_envelope(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": {
                "frame_id": self.frame_id,
                "capture_timestamp_ns": self.capture_timestamp.value_ns,
                "present_estimate_ns": (
                    None if self.present_estimate is None else self.present_estimate.value_ns
                ),
                "hwnd": self.window_identity.hwnd,
                "process_id": self.window_identity.pid,
                "window_generation": self.window_identity.window_generation,
                "width": self.width,
                "height": self.height,
                "stride_bytes": self.stride_bytes,
                "pixel_format": self.pixel_format.value,
                "physical_rect": {
                    "left": self.physical_rect.left,
                    "top": self.physical_rect.top,
                    "right": self.physical_rect.right,
                    "bottom": self.physical_rect.bottom,
                },
                "client_rect": {
                    "left": self.client_rect.left,
                    "top": self.client_rect.top,
                    "right": self.client_rect.right,
                    "bottom": self.client_rect.bottom,
                },
                "source_backend": self.source_backend,
                "buffer_handle": self.buffer_handle.descriptor(),
            },
        }
