//! Shared scaffolding for the developer-owned graphics-API fixture windows.
//!
//! Every fixture opens a stable-titled window and clears it through a
//! deterministic color cycle so capture analysis can distinguish a live
//! fixture from a stale frame.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::VK_ESCAPE;
use windows::Win32::UI::WindowsAndMessaging::{
    CS_OWNDC, CreateWindowExW, DefWindowProcW, DispatchMessageW, GetSystemMetrics, MSG, PM_REMOVE,
    PeekMessageW, RegisterClassW, SM_CXSCREEN, SM_CYSCREEN, TranslateMessage, WINDOW_EX_STYLE,
    WM_DESTROY, WM_KEYDOWN, WNDCLASSW, WS_OVERLAPPEDWINDOW, WS_VISIBLE,
};
use windows::core::w;

/// Rendering stays responsive rather than unbounded; the clear color changes
/// twice per cycle second so soak analysis can verify liveness.
pub const FRAME_BUDGET: Duration = Duration::from_millis(16);
pub const COLOR_SEQUENCE: [(f32, f32, f32); 4] = [
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0),
];
pub const COLOR_PHASE_MS: u32 = 500;

static QUIT_REQUESTED: AtomicBool = AtomicBool::new(false);

/// Deterministic full-intensity RGB cycling; capture soaks assert that the
/// observed colors follow this sequence.
#[must_use]
pub fn cycle_color(elapsed: Duration) -> (f32, f32, f32) {
    let phase = elapsed.as_millis() / u128::from(COLOR_PHASE_MS);
    let index = (phase % COLOR_SEQUENCE.len() as u128) as usize;
    COLOR_SEQUENCE[index]
}

/// Parses `--duration-seconds <f64>`; `None` means run until the window is
/// closed or `Escape` is pressed.
#[must_use]
pub fn parse_duration_seconds(args: &[String]) -> Option<Duration> {
    let mut duration = None;
    let mut index = 1;
    while index < args.len() {
        if args[index] == "--duration-seconds" {
            if let Some(raw) = args.get(index + 1) {
                if let Ok(seconds) = raw.parse::<f64>() {
                    if seconds > 0.0 {
                        duration = Some(Duration::from_secs_f64(seconds));
                    }
                }
            }
        }
        index += 1;
    }
    duration
}

pub struct FixtureWindow {
    pub handle: HWND,
}

impl FixtureWindow {
    /// Creates the visible fixture window with `CS_OWNDC` so the OpenGL
    /// fixture can own its device context.
    ///
    /// # Errors
    ///
    /// Returns an error when the Win32 window cannot be created.
    pub fn create(title: &str) -> windows::core::Result<Self> {
        let wide_title: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            // None resolves to the running executable's own module.
            let module = GetModuleHandleW(None)?;
            let instance = HINSTANCE(module.0);
            let class = WNDCLASSW {
                style: CS_OWNDC,
                lpfnWndProc: Some(fixture_window_proc),
                hInstance: instance,
                lpszClassName: w!("UGA_API_FIXTURE_WINDOW"),
                ..Default::default()
            };
            // A second fixture in the same process would find the class
            // already registered with identical parameters, which is fine.
            let _ = RegisterClassW(&raw const class);
            let width = (GetSystemMetrics(SM_CXSCREEN) / 4).max(480);
            let height = (GetSystemMetrics(SM_CYSCREEN) / 4).max(360);
            let handle = CreateWindowExW(
                WINDOW_EX_STYLE(0),
                w!("UGA_API_FIXTURE_WINDOW"),
                windows::core::PCWSTR(wide_title.as_ptr()),
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                40,
                40,
                width,
                height,
                None,
                None,
                Some(instance),
                None,
            )?;
            Ok(Self { handle })
        }
    }

    #[must_use]
    pub fn quit_requested(&self) -> bool {
        QUIT_REQUESTED.load(Ordering::SeqCst)
    }
}

/// Asks the running fixture loop to stop; renderers use this on device loss.
pub fn request_quit() {
    QUIT_REQUESTED.store(true, Ordering::SeqCst);
}

/// Pumps all pending thread messages, honoring `Escape` and destruction.
fn pump_messages() {
    let mut message = MSG::default();
    unsafe {
        while PeekMessageW(&raw mut message, None, 0, 0, PM_REMOVE).as_bool() {
            if message.message == WM_KEYDOWN && message.wParam == WPARAM(VK_ESCAPE.0 as usize) {
                QUIT_REQUESTED.store(true, Ordering::SeqCst);
            }
            if message.message == WM_DESTROY {
                QUIT_REQUESTED.store(true, Ordering::SeqCst);
            }
            let _ = TranslateMessage(&raw const message);
            let _ = DispatchMessageW(&raw const message);
        }
    }
}

/// Runs until `limit` elapses, the window closes, or `Escape` is pressed,
/// while `render_frame` presents one frame per pass.
pub fn run_loop(window: &FixtureWindow, render_frame: &mut dyn FnMut(), limit: Option<Duration>) {
    let start = std::time::Instant::now();
    loop {
        pump_messages();
        if window.quit_requested() {
            break;
        }
        if let Some(limit) = limit {
            if start.elapsed() >= limit {
                break;
            }
        }
        render_frame();
        std::thread::sleep(FRAME_BUDGET);
    }
}

unsafe extern "system" fn fixture_window_proc(
    handle: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_DESTROY {
        QUIT_REQUESTED.store(true, Ordering::SeqCst);
    }
    unsafe { DefWindowProcW(handle, message, wparam, lparam) }
}
