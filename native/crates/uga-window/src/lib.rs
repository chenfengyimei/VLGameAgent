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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rectangles_allow_negative_monitor_origins_but_not_empty_extents() {
        assert!(
            RectI32 {
                left: -1920,
                top: -200,
                right: 0,
                bottom: 880,
            }
            .is_valid()
        );
        assert!(
            !RectI32 {
                left: 10,
                top: 20,
                right: 10,
                bottom: 30,
            }
            .is_valid()
        );
    }

    #[test]
    fn window_identity_requires_live_handle_process_and_generation() {
        let mut identity = WindowIdentityCore {
            hwnd: 1,
            pid: 2,
            process_start_time_100ns: 3,
            window_generation: 1,
            executable_path_sha256: [0; 32],
        };
        assert!(identity.is_valid());
        identity.window_generation = 0;
        assert!(!identity.is_valid());
    }
}
