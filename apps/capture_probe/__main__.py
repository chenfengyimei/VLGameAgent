from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict
from pathlib import Path

from uga.capture.diagnostics import CaptureDiagnosticsAccumulator
from uga.capture.dxgi import DXGIDuplicationBackend
from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.windows_graphics_capture import WindowsGraphicsCaptureBackend
from uga.core.errors import ContractViolation
from uga.time.clock import PerfCounterClock
from uga.windows.backend import Win32WindowBackend


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture frames from one exact Windows title")
    parser.add_argument("--title", required=True, help="full regular expression for window title")
    parser.add_argument(
        "--backend",
        choices=("auto", "windows_graphics_capture", "dxgi_duplication", "gdi_fallback"),
        default="auto",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--frames", type=int, default=120)
    group.add_argument("--duration-seconds", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--target-fps", type=float, default=60.0)
    args = parser.parse_args()
    if args.frames is not None and args.frames < 1:
        raise ContractViolation("capture probe frame count must be positive")
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        raise ContractViolation("capture probe duration must be positive")
    if not 0 < args.target_fps <= 240:
        raise ContractViolation("capture probe target FPS must be in (0, 240]")

    title_pattern = re.compile(args.title)
    windows = Win32WindowBackend()
    matches = tuple(
        snapshot
        for snapshot in windows.discover()
        if title_pattern.fullmatch(snapshot.title) is not None
    )
    if len(matches) != 1:
        raise ContractViolation(
            f"capture probe requires exactly one title match; found {len(matches)}"
        )
    target = matches[0]
    preference = (
        (args.backend,)
        if args.backend != "auto"
        else ("windows_graphics_capture", "dxgi_duplication", "gdi_fallback")
    )
    registry = CaptureBackendRegistry(preference)
    registry.register(WindowsGraphicsCaptureBackend(windows=windows))
    registry.register(DXGIDuplicationBackend(windows=windows))
    registry.register(GDIFallbackCaptureBackend(windows))
    candidates = registry.candidates(target.identity)
    backend = registry.start_best(target.identity)
    clock = PerfCounterClock()
    diagnostics = CaptureDiagnosticsAccumulator(backend.backend_id)
    started = clock.now()
    target_interval_ns = round(1_000_000_000 / args.target_fps)
    frame_count = 0
    try:
        while True:
            before = clock.now()
            captured = backend.capture()
            after = clock.now()
            diagnostics.add(captured, (after.value_ns - before.value_ns) / 1_000_000)
            frame_count += 1
            elapsed = (after.value_ns - started.value_ns) / 1_000_000_000
            if args.duration_seconds is not None:
                if elapsed >= args.duration_seconds:
                    break
            elif frame_count >= args.frames:
                break
            remaining_ns = target_interval_ns - (after.value_ns - before.value_ns)
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)
    finally:
        backend.stop()
    ended = clock.now()
    report = {
        "schema": "uga.capture_diagnostics",
        "schema_version": "1.1",
        "target": {
            "hwnd": target.identity.hwnd,
            "pid": target.identity.pid,
            "title": target.title,
            "dpi": target.dpi,
        },
        "candidates": [
            {
                "backend_id": item.backend.backend_id,
                "available": item.probe.available,
                "score": item.probe.score,
                "reason": item.probe.reason,
            }
            for item in candidates
        ],
        "diagnostics": asdict(
            diagnostics.summarize(
                elapsed_seconds=(ended.value_ns - started.value_ns) / 1_000_000_000,
            )
        ),
    }
    document = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(document + "\n", encoding="utf-8")
        print(args.output.resolve())
    else:
        print(document)


if __name__ == "__main__":
    main()
