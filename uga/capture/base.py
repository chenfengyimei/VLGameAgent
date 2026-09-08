from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from uga.capture.frame import Frame, PixelFormat
from uga.core.errors import BackendStateError, ContractViolation
from uga.windows.window_identity import WindowIdentity


class WindowMode(StrEnum):
    WINDOWED = "windowed"
    BORDERLESS = "borderless"
    EXCLUSIVE_FULLSCREEN = "exclusive_fullscreen"


class GraphicsAPI(StrEnum):
    UNKNOWN = "unknown"
    DX11 = "dx11"
    DX12 = "dx12"
    VULKAN = "vulkan"
    OPENGL = "opengl"


@dataclass(frozen=True, slots=True)
class CaptureCapability:
    window_modes: frozenset[WindowMode]
    graphics_apis: frozenset[GraphicsAPI]
    pixel_formats: frozenset[PixelFormat]
    supports_multi_monitor: bool
    supports_hdr: bool
    supports_resize: bool
    supports_minimized: bool


@dataclass(frozen=True, slots=True)
class CaptureProbe:
    available: bool
    score: int
    reason: str
    capability: CaptureCapability

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ContractViolation("capture probe score must be in [0, 100]")
        if not self.reason.strip():
            raise ContractViolation("capture probe reason cannot be empty")


@dataclass(frozen=True, slots=True)
class CaptureHealth:
    healthy: bool
    consecutive_failures: int
    detail: str


class CaptureBackend(ABC):
    """Stable lifecycle contract implemented by all capture technologies."""

    backend_id: str

    def __init__(self) -> None:
        self._target: WindowIdentity | None = None
        self._consecutive_failures = 0
        self._last_error = "not started"

    @abstractmethod
    def probe(self, target: WindowIdentity) -> CaptureProbe:
        raise NotImplementedError

    def start(self, target: WindowIdentity) -> None:
        if self._target is not None:
            raise BackendStateError(f"{self.backend_id} is already started")
        target.validate()
        self._start(target)
        self._target = target
        self._consecutive_failures = 0
        self._last_error = "ok"

    def capture(self) -> Frame:
        if self._target is None:
            raise BackendStateError(f"{self.backend_id} is not started")
        try:
            frame = self._capture()
            if frame.source_backend != self.backend_id:
                raise ContractViolation("backend returned a frame with the wrong source_backend")
            if frame.window_identity != self._target:
                raise ContractViolation(
                    "backend returned a frame for a different window generation"
                )
            self._consecutive_failures = 0
            self._last_error = "ok"
            return frame
        except Exception as error:
            self._consecutive_failures += 1
            self._last_error = str(error)
            raise

    def stop(self) -> None:
        if self._target is None:
            return
        try:
            self._stop()
        finally:
            self._target = None

    def health(self) -> CaptureHealth:
        return CaptureHealth(
            self._target is not None and self._consecutive_failures == 0,
            self._consecutive_failures,
            self._last_error,
        )

    @abstractmethod
    def _start(self, target: WindowIdentity) -> None:
        raise NotImplementedError

    @abstractmethod
    def _capture(self) -> Frame:
        raise NotImplementedError

    @abstractmethod
    def _stop(self) -> None:
        raise NotImplementedError
