from __future__ import annotations

import hashlib
import re
import uuid

from uga.capture.frame import BufferKind, Frame, PixelFormat
from uga.capture.ring_buffer import SequencedFrame
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.perception.schema import PerceptionSnapshot, TextRegion
from uga.perception.text import TextObservationProvider

_TEXT_NORMALIZER = re.compile(r"\W+", re.UNICODE)


def normalize_visible_text(value: str) -> str:
    return _TEXT_NORMALIZER.sub("", value.casefold())


def perceptual_signature(
    frame: Frame,
    text: tuple[TextRegion, ...],
    mode: ControlMode,
    task_generation: int,
) -> str:
    """Stable coarse image hash fused with semantic OCR/mode/task state."""
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("perceptual signature requires a CPU-addressable frame")
    if frame.pixel_format not in (PixelFormat.BGRA8, PixelFormat.RGBA8):
        raise ContractViolation("perceptual signature requires a four-channel frame")
    payload = frame.buffer_handle.readonly_view()
    samples: list[int] = []
    for grid_y in range(16):
        y = min((grid_y * frame.height + frame.height // 2) // 16, frame.height - 1)
        for grid_x in range(16):
            x = min((grid_x * frame.width + frame.width // 2) // 16, frame.width - 1)
            offset = y * frame.stride_bytes + x * 4
            blue, green, red = payload[offset : offset + 3]
            samples.append((int(red) * 77 + int(green) * 150 + int(blue) * 29) >> 8)
    average = sum(samples) / len(samples)
    bits = bytearray(32)
    for index, value in enumerate(samples):
        if value >= average:
            bits[index // 8] |= 1 << (index % 8)
    semantic = "\n".join(
        (
            bits.hex(),
            mode.value,
            str(task_generation),
            *(normalize_visible_text(region.text) for region in text),
        )
    )
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()


class PerceptionBuilder:
    def __init__(self, text_provider: TextObservationProvider) -> None:
        self._text_provider = text_provider

    @property
    def text_provider_available(self) -> bool:
        return self._text_provider.available

    def build(
        self,
        item: SequencedFrame,
        mode: ControlMode,
        *,
        geometry_generation: int,
        task_generation: int,
        goal_facts: tuple[tuple[str, str], ...] = (),
    ) -> PerceptionSnapshot:
        regions = self._text_provider.recognize(item.frame)
        confidence = (
            sum(region.confidence for region in regions) / len(regions) if regions else 0.0
        )
        return PerceptionSnapshot(
            uuid.uuid4().hex,
            item.frame.frame_id,
            item.sequence,
            item.frame.capture_timestamp,
            item.frame.window_identity,
            geometry_generation,
            task_generation,
            mode,
            regions,
            (),
            goal_facts,
            perceptual_signature(item.frame, regions, mode, task_generation),
            confidence,
        )

