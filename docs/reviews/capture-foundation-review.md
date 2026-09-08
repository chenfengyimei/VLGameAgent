# Capture Foundation implementation review

Date: 2026-09-09

| Issue | Status | Evidence / remaining gate |
|---|---|---|
| UGA-001 | implemented | package, config, CI, CLI, Python/Rust layouts |
| UGA-002 | implemented | `ARCHITECTURE.md` invariants |
| UGA-003 | implemented | v1.1 schema envelopes and contract tests |
| UGA-004 | implemented | Python perf-counter clock, Rust QPC, regression tests |
| UGA-005 | implemented | async event bus and lifecycle smoke path |
| UGA-006 | implemented | composite identity and HWND generation tracker |
| UGA-007 | implemented | ctypes Win32 discovery; headless-host smoke tested |
| UGA-008 | implemented | explicit spaces, DPI, letterbox and negative-monitor tests |
| UGA-009 | implemented | backend lifecycle/capability/health/frame contracts |
| UGA-010 | implemented | native WGC frame pool, resize rebuild, CPU readback, C ABI, live fixture capture |
| UGA-011 | implemented | native DXGI duplication, crop/readback, access-loss handling, live fixture capture |
| UGA-012 | implemented | ranked registry, start fallback, runtime failover |
| UGA-013 | implemented | bounded ring plus single-slot latest-state transport |

## Review outcome

Capture Foundation passes architecture review and Safe Control Foundation may
begin. Both native providers compile behind a panic-contained C ABI, run on a
dedicated native worker thread, and captured an owned Win32 fixture through the
Python runtime. Contract, failover, and latest-state tests remain green.

The full environmental qualification matrix remains a release gate rather than
a contract blocker: 30-minute soak, real DX11/DX12/Vulkan/OpenGL games,
exclusive fullscreen, HDR, multi-monitor, minimize/alt-tab, and device-loss
recovery must be recorded on suitable hardware before M1 is declared release-
qualified.
