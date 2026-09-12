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
        let minimum_stride = match self.pixel_format {
            PixelFormat::Nv12 => self.width,
            PixelFormat::Bgra8 | PixelFormat::Rgba8 | PixelFormat::R10G10B10A2 => {
                self.width.saturating_mul(4)
            }
        };
        let minimum_buffer_size = (self.stride_bytes as u64).saturating_mul(self.height as u64);
        let buffer_size_is_valid = match self.buffer_kind {
            BufferKind::CpuBytes | BufferKind::SharedMemory => {
                self.buffer_size_bytes >= minimum_buffer_size
            }
            BufferKind::Native | BufferKind::GpuTexture => true,
        };
        self.frame_sequence != 0
            && self.capture_timestamp.0 >= 0
            && (!self.has_present_estimate || self.present_estimate.0 >= 0)
            && self.window.is_valid()
            && self.width != 0
            && self.height != 0
            && self.stride_bytes >= minimum_stride
            && buffer_size_is_valid
            && self.physical_rect.is_valid()
            && self.client_rect.is_valid()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metadata() -> FrameMetadata {
        FrameMetadata {
            frame_sequence: 1,
            capture_timestamp: UgaTimeNs(10),
            present_estimate: UgaTimeNs(9),
            has_present_estimate: true,
            window: WindowIdentityCore {
                hwnd: 1,
                pid: 2,
                process_start_time_100ns: 3,
                window_generation: 1,
                executable_path_sha256: [1; 32],
            },
            width: 16,
            height: 8,
            stride_bytes: 64,
            pixel_format: PixelFormat::Bgra8,
            physical_rect: RectI32 {
                left: -10,
                top: 0,
                right: 6,
                bottom: 8,
            },
            client_rect: RectI32 {
                left: 0,
                top: 0,
                right: 16,
                bottom: 8,
            },
            buffer_kind: BufferKind::CpuBytes,
            buffer_size_bytes: 512,
        }
    }

    #[test]
    fn packed_frame_requires_bytes_per_pixel_stride() {
        let mut value = metadata();
        value.stride_bytes = value.width;
        assert!(!value.is_valid());
        value.pixel_format = PixelFormat::Nv12;
        assert!(value.is_valid());
    }

    #[test]
    fn frame_buffer_must_cover_every_declared_row() {
        let mut value = metadata();
        value.buffer_size_bytes -= 1;
        assert!(!value.is_valid());
    }

    #[test]
    fn present_timestamp_is_validated_only_when_present() {
        let mut value = metadata();
        value.present_estimate = UgaTimeNs(-1);
        assert!(!value.is_valid());
        value.has_present_estimate = false;
        assert!(value.is_valid());
    }
}
