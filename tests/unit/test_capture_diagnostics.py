from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.capture.diagnostics import CaptureDiagnosticsAccumulator, summarize_capture
from uga.capture.frame import BufferHandle, BufferKind


class CaptureDiagnosticsTests(unittest.TestCase):
    def test_summary_reports_latency_gaps_regressions_and_resize(self) -> None:
        first = frame(1, timestamp_ns=100)
        second = frame(2, timestamp_ns=200)
        third = frame(3, timestamp_ns=150)
        resized_payload = bytes([3]) * 32
        third = replace(
            third,
            width=4,
            stride_bytes=16,
            buffer_handle=BufferHandle(
                "resized-buffer",
                BufferKind.CPU_BYTES,
                len(resized_payload),
                resized_payload,
            ),
        )
        report = summarize_capture(
            "fixture",
            (first, second, third),
            (1.0, 2.0, 3.0),
            elapsed_seconds=0.1,
        )
        self.assertEqual(report.timestamp_regressions, 1)
        self.assertEqual(report.resolution_changes, 1)
        self.assertEqual(report.latency_ms_p99, 3.0)

        accumulator = CaptureDiagnosticsAccumulator("fixture")
        accumulator.add(first, 1.0)
        accumulator.add(second, 2.0)
        accumulator.add(third, 3.0)
        streamed = accumulator.summarize(elapsed_seconds=0.1)
        self.assertEqual(streamed, report)


if __name__ == "__main__":
    unittest.main()
