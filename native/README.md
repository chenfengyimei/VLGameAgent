# UGA native boundary

These crates freeze low-level contracts without exposing Win32 details to the
Python runtime. `uga-clock` provides a direct QueryPerformanceCounter clock;
`uga-window` owns FFI-friendly identity/geometry; `uga-capture` implements WGC
and DXGI/D3D11 providers behind a versioned C ABI.

The exported capture handle owns a dedicated worker thread. WGC/COM lifecycle
therefore remains thread-correct even when Python invokes the ABI from changing
executor threads. Returned CPU buffers must be released exactly once through
`uga_capture_frame_release`.
