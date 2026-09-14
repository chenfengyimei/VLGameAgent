mod dxgi;
mod wgc;

pub use dxgi::DxgiCapture;
pub use wgc::WgcCapture;

use uga_clock::UgaTimeNs;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::Graphics::Dwm::{DWMWA_EXTENDED_FRAME_BOUNDS, DwmGetWindowAttribute};
use windows::Win32::UI::WindowsAndMessaging::GetWindowRect;

#[derive(Debug)]
pub enum CaptureError {
    Timeout,
    AccessLost,
    TargetLost,
    Unsupported(&'static str),
    Clock(uga_clock::ClockError),
    Platform(windows::core::Error),
}

impl core::fmt::Display for CaptureError {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Timeout => formatter.write_str("capture timed out"),
            Self::AccessLost => formatter.write_str("capture access was lost"),
            Self::TargetLost => formatter.write_str("target window was lost"),
            Self::Unsupported(detail) => write!(formatter, "unsupported capture state: {detail}"),
            Self::Clock(error) => write!(formatter, "clock error: {error:?}"),
            Self::Platform(error) => write!(formatter, "Windows error: {error}"),
        }
    }
}

impl std::error::Error for CaptureError {}

impl From<windows::core::Error> for CaptureError {
    fn from(value: windows::core::Error) -> Self {
        Self::Platform(value)
    }
}

impl From<uga_clock::ClockError> for CaptureError {
    fn from(value: uga_clock::ClockError) -> Self {
        Self::Clock(value)
    }
}

#[derive(Debug)]
pub struct CapturedBgraFrame {
    pub captured_at: UgaTimeNs,
    pub present_estimate: Option<UgaTimeNs>,
    pub physical_rect: windows::Win32::Foundation::RECT,
    pub width: u32,
    pub height: u32,
    pub stride_bytes: u32,
    pub bytes: Vec<u8>,
}

/// Return the visible window bounds used by Windows Graphics Capture.
///
/// `GetWindowRect` includes invisible resize borders on modern Windows. WGC
/// omits those borders, so using the raw rectangle for a DXGI failover changes
/// both the frame geometry and the pixel origin. Prefer DWM's extended frame
/// bounds and retain `GetWindowRect` only for platforms where DWM cannot answer.
pub(crate) fn visible_window_rect(hwnd: HWND) -> Result<RECT, CaptureError> {
    let mut visible = RECT::default();
    let rect_size = u32::try_from(size_of::<RECT>())
        .map_err(|_| CaptureError::Unsupported("RECT size exceeds the Windows ABI"))?;
    let dwm_result = unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            (&raw mut visible).cast(),
            rect_size,
        )
    };
    if dwm_result.is_ok() && rect_is_valid(visible) {
        return Ok(visible);
    }

    let mut window = RECT::default();
    unsafe { GetWindowRect(hwnd, &raw mut window)? };
    if rect_is_valid(window) {
        Ok(window)
    } else {
        Err(CaptureError::TargetLost)
    }
}

const fn rect_is_valid(rect: RECT) -> bool {
    rect.right > rect.left && rect.bottom > rect.top
}

#[cfg(test)]
mod tests {
    use super::rect_is_valid;
    use windows::Win32::Foundation::RECT;

    #[test]
    fn visible_rect_must_have_positive_area() {
        assert!(rect_is_valid(RECT {
            left: 10,
            top: 20,
            right: 30,
            bottom: 40,
        }));
        assert!(!rect_is_valid(RECT {
            left: 10,
            top: 20,
            right: 10,
            bottom: 40,
        }));
    }
}
