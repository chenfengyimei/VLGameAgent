mod dxgi;
mod wgc;

pub use dxgi::DxgiCapture;
pub use wgc::WgcCapture;

use uga_clock::UgaTimeNs;

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
