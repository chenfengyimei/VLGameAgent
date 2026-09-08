# Capture Foundation contracts

Schema version: `1.1`.

## `UGATime`

- Non-negative signed 64-bit integer nanoseconds.
- Produced only by a `ClockBackend` during live operation.
- Equal consecutive values are valid; decreasing values are rejected.

## `WindowIdentity`

- `hwnd`: positive native window handle.
- `pid`: positive process identifier.
- `executable_path_hash`: normalized-path SHA-256, never the raw path.
- `process_start_time_100ns`: Windows process creation FILETIME value used for
  identity, not online scheduling.
- `window_generation`: increments when a handle maps to a new process identity.

## `Frame`

- Contains monotonic capture and optional present-estimate timestamps.
- Binds to the full target `WindowIdentity`, not just HWND/PID.
- Describes pixel format, stride, physical/client rectangles, and source
  backend.
- Holds an opaque `BufferHandle`; serialization includes only its descriptor.

## Backpressure

`FrameRingBuffer` retains a bounded recent history for in-process consumers and
counts overwritten frames. `LatestFrameSlot` holds at most one pending frame for
latency-sensitive consumers. Recorder transport is intentionally separate and
must not reuse `LatestFrameSlot`.

## Backend lifecycle

Every backend supports `probe -> start -> capture* -> stop`. Probe results are
honest capability statements. An absent native WGC or DXGI provider reports
unavailable rather than silently falling back. `CaptureSession` records failure,
retires the failed backend, and allows recovery to the next candidate.

## Native ABI

The Rust `uga-capture` crate exports ABI version `0x00010001`. Each handle owns
a dedicated worker thread so WGC/COM objects are created, used, and destroyed on
one thread even when Python calls capture through arbitrary executor threads.
Panics are contained at every exported boundary; frame buffers have explicit
one-shot release ownership and errors are retrieved as UTF-8.
