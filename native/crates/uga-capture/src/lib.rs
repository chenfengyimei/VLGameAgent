//! Capture metadata contracts shared by future WGC and DXGI providers.

use uga_clock::UgaTimeNs;
use uga_window::{RectI32, WindowIdentityCore};

#[cfg(windows)]
pub mod windows;

#[cfg(windows)]
mod ffi;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum PixelFormat {
    Bgra8 = 1,
    Rgba8 = 2,
    Nv12 = 3,
    R10G10B10A2 = 4,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum BufferKind {
    CpuBytes = 1,
    Native = 2,
    GpuTexture = 3,
    SharedMemory = 4,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct FrameMetadata {
    pub frame_sequence: u64,
    pub capture_timestamp: UgaTimeNs,
    pub present_estimate: UgaTimeNs,
    pub has_present_estimate: bool,
    pub window: WindowIdentityCore,
    pub width: u32,
    pub height: u32,
    pub stride_bytes: u32,
    pub pixel_format: PixelFormat,
    pub physical_rect: RectI32,
    pub client_rect: RectI32,
    pub buffer_kind: BufferKind,
    pub buffer_size_bytes: u64,
}

impl FrameMetadata {
    #[must_use]
    pub const fn is_valid(&self) -> bool {
        self.frame_sequence != 0
            && self.capture_timestamp.0 >= 0
            && self.window.is_valid()
            && self.width != 0
            && self.height != 0
            && self.stride_bytes >= self.width
            && self.physical_rect.is_valid()
            && self.client_rect.is_valid()
    }
}
