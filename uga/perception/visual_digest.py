"""Bounded multichannel pixel evidence shared by freshness and effect checks."""
from __future__ import annotations

import math

from uga.capture.frame import BufferKind, Frame
from uga.core.errors import ContractViolation
from uga.perception.schema import NormalizedBox


def region_digest(frame: Frame, box: NormalizedBox) -> bytes:
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("target verification requires a CPU-addressable frame")
    payload = frame.buffer_handle.readonly_view()
    left = max(0, int(box.left * frame.width))
    right = min(frame.width, max(left + 1, int(box.right * frame.width)))
    top = max(0, int(box.top * frame.height))
    bottom = min(frame.height, max(top + 1, int(box.bottom * frame.height)))
    # F07: the old [::16] byte stride sampled one colour channel of every
    # fourth pixel, so a pure red or green state change stayed invisible to
    # every freshness and effect comparison.  Two bytes per sampled pixel —
    # luminance plus a channel XOR — always cover every colour channel while
    # ignoring alpha and stride padding, and the deterministic spatial grid
    # keeps the sampling bounded on huge regions.
    width = right - left
    step = max(1, math.isqrt(max(1, width * (bottom - top)) // 4096))
    stride = frame.stride_bytes
    digest = bytearray()
    for row in range(top, bottom, step):
        base = row * stride + left * 4
        for offset in range(0, width * 4, step * 4):
            blue = payload[base + offset]
            green = payload[base + offset + 1]
            red = payload[base + offset + 2]
            digest.append((29 * blue + 150 * green + 77 * red) >> 8)
            digest.append(blue ^ green ^ red)
    return bytes(digest)



def digest_difference(before: bytes, after: bytes) -> float:
    if not before or len(before) != len(after):
        return 1.0
    return sum(a != b for a, b in zip(before, after, strict=True)) / len(before)
