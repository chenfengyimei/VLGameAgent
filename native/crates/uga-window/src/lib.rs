//! FFI-friendly window identity and geometry contracts.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct RectI32 {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl RectI32 {
    #[must_use]
    pub const fn is_valid(self) -> bool {
        self.right > self.left && self.bottom > self.top
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct WindowIdentityCore {
    pub hwnd: isize,
    pub pid: u32,
    pub process_start_time_100ns: u64,
    pub window_generation: u64,
    pub executable_path_sha256: [u8; 32],
}

impl WindowIdentityCore {
    #[must_use]
    pub const fn is_valid(&self) -> bool {
        self.hwnd != 0 && self.pid != 0 && self.window_generation != 0
    }
}
