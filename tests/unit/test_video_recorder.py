from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import av

from tests.helpers import identity
from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.core.errors import ContractViolation
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect


def _frame(width: int, height: int) -> Frame:
    row_bytes = width * 4
    payload = bytes([200]) * (row_bytes * height)
    return Frame(
        frame_id=f"frame-{width}x{height}",
        capture_timestamp=UGATime(100),
        present_estimate=None,
        window_identity=identity(),
        width=width,
        height=height,
        stride_bytes=row_bytes,
        pixel_format=PixelFormat.BGRA8,
        physical_rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        source_backend="fixture",
        buffer_handle=BufferHandle(
            handle_id="buffer-1",
            kind=BufferKind.CPU_BYTES,
            size_bytes=len(payload),
            payload=payload,
        ),
    )


class PyAvVideoRecorderTests(unittest.TestCase):
    def test_odd_dimensions_are_cropped_to_even_encode_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "video.mp4"
            recorder = PyAvVideoRecorder(path, fps=30)
            try:
                recorder.append(_frame(3, 3))
                recorder.append(_frame(3, 3))
                recorder.close()
            finally:
                recorder.close()

            container = av.open(str(path))
            try:
                stream = container.streams.video[0]
                self.assertEqual(
                    (stream.codec_context.width, stream.codec_context.height), (2, 2)
                )
            finally:
                container.close()

    def test_frames_below_encode_minimum_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "video.mp4"
            recorder = PyAvVideoRecorder(path, fps=30)
            with self.assertRaisesRegex(ContractViolation, "2x2 encode minimum"):
                recorder.append(_frame(1, 1))


if __name__ == "__main__":
    unittest.main()
