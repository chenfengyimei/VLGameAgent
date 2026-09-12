from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from tests.helpers import frame
from uga.capture.frame import BufferHandle
from uga.capture.ring_buffer import SequencedFrame
from uga.control.lease import ControlMode
from uga.perception.builder import PerceptionBuilder, perceptual_signature
from uga.perception.schema import NormalizedBox, TextRegion
from uga.perception.text import NullTextProvider, RapidOcrProvider


class _TextProvider:
    @property
    def available(self) -> bool:
        return True

    def recognize(self, source):  # type: ignore[no-untyped-def]
        del source
        return (TextRegion("设置", NormalizedBox(0.1, 0.2, 0.3, 0.4), 0.9),)


class PerceptionTests(unittest.TestCase):
    def test_null_provider_is_an_explicit_no_text_fallback(self) -> None:
        provider = NullTextProvider()

        self.assertFalse(provider.available)
        self.assertEqual(provider.recognize(frame(1)), ())

    def test_rapidocr_output_is_normalized_to_frame_coordinates(self) -> None:
        result = SimpleNamespace(
            boxes=(((10, 20), (50, 20), (50, 40), (10, 40)),),
            txts=("网络和互联网",),
            scores=(0.98,),
        )
        received: list[bytes] = []

        def engine(payload: bytes) -> object:
            received.append(payload)
            return result

        provider = RapidOcrProvider(engine=engine)

        regions = provider.recognize(_large_frame())

        self.assertTrue(received[0].startswith(b"\x89PNG"))
        self.assertEqual(regions[0].text, "网络和互联网")
        self.assertEqual(regions[0].box, NormalizedBox(0.1, 0.2, 0.5, 0.4))

    def test_signature_ignores_small_pixel_changes_outside_sample_points(self) -> None:
        source = _large_frame()
        changed = bytearray(source.buffer_handle.readonly_view())
        changed[4:8] = b"\xff\xff\xff\xff"
        other = replace(
            source,
            buffer_handle=BufferHandle(
                source.buffer_handle.handle_id,
                source.buffer_handle.kind,
                len(changed),
                bytes(changed),
            ),
        )

        first = perceptual_signature(source, (), ControlMode.GUI, 1)
        second = perceptual_signature(other, (), ControlMode.GUI, 1)

        self.assertEqual(first, second)

    def test_builder_fuses_ocr_into_snapshot(self) -> None:
        snapshot = PerceptionBuilder(_TextProvider()).build(
            SequencedFrame(4, frame(100)),
            ControlMode.GUI,
            geometry_generation=2,
            task_generation=3,
        )

        self.assertEqual(snapshot.frame_sequence, 4)
        self.assertEqual(snapshot.text, ("设置",))
        self.assertAlmostEqual(snapshot.confidence, 0.9)


def _large_frame():  # type: ignore[no-untyped-def]
    source = frame(1)
    payload = bytes([1]) * (100 * 100 * 4)
    return replace(
        source,
        width=100,
        height=100,
        stride_bytes=400,
        buffer_handle=BufferHandle(
            source.buffer_handle.handle_id,
            source.buffer_handle.kind,
            len(payload),
            payload,
        ),
    )


if __name__ == "__main__":
    unittest.main()
