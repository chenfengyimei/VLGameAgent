"""Capture contracts, backend selection, and bounded frame transport."""

from uga.capture.base import CaptureBackend, CaptureCapability, CaptureProbe
from uga.capture.frame import BufferHandle, Frame, PixelFormat
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.ring_buffer import FrameRingBuffer, LatestFrameSlot
from uga.capture.session import CaptureSession

__all__ = [
    "BufferHandle",
    "CaptureBackend",
    "CaptureBackendRegistry",
    "CaptureCapability",
    "CaptureProbe",
    "CaptureSession",
    "Frame",
    "FrameRingBuffer",
    "LatestFrameSlot",
    "PixelFormat",
]
