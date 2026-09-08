//! Monotonic nanosecond time contract for UGA.

/// Timestamp on the single UGA monotonic timeline.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
#[repr(transparent)]
pub struct UgaTimeNs(pub i64);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ClockError {
    PlatformCallFailed,
    InvalidFrequency,
    Overflow,
}

pub trait Clock {
    /// Read the current monotonic timestamp.
    ///
    /// # Errors
    ///
    /// Returns an error when the platform clock fails or conversion overflows.
    fn now(&self) -> Result<UgaTimeNs, ClockError>;
}

#[cfg(windows)]
mod platform {
    use super::{Clock, ClockError, UgaTimeNs};

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn QueryPerformanceCounter(value: *mut i64) -> i32;
        fn QueryPerformanceFrequency(value: *mut i64) -> i32;
    }

    #[derive(Clone, Copy, Debug)]
    pub struct QpcClock {
        frequency: i64,
    }

    impl QpcClock {
        /// Initialize the clock and cache the QPC frequency.
        ///
        /// # Errors
        ///
        /// Returns an error when QPC is unavailable or reports an invalid frequency.
        pub fn new() -> Result<Self, ClockError> {
            let mut frequency = 0_i64;
            // SAFETY: `frequency` is a valid, writable i64 for the duration of the call.
            if unsafe { QueryPerformanceFrequency(&raw mut frequency) } == 0 {
                return Err(ClockError::PlatformCallFailed);
            }
            if frequency <= 0 {
                return Err(ClockError::InvalidFrequency);
            }
            Ok(Self { frequency })
        }

        #[must_use]
        pub const fn frequency(&self) -> i64 {
            self.frequency
        }

        /// Convert a raw QPC count to the UGA nanosecond timeline.
        ///
        /// # Errors
        ///
        /// Returns an error when the conversion overflows.
        pub fn ticks_to_ns(&self, counter: i64) -> Result<UgaTimeNs, ClockError> {
            let nanos = i128::from(counter)
                .checked_mul(1_000_000_000)
                .and_then(|value| value.checked_div(i128::from(self.frequency)))
                .ok_or(ClockError::Overflow)?;
            let nanos = i64::try_from(nanos).map_err(|_| ClockError::Overflow)?;
            Ok(UgaTimeNs(nanos))
        }
    }

    impl Clock for QpcClock {
        fn now(&self) -> Result<UgaTimeNs, ClockError> {
            let mut counter = 0_i64;
            // SAFETY: `counter` is a valid, writable i64 for the duration of the call.
            if unsafe { QueryPerformanceCounter(&raw mut counter) } == 0 {
                return Err(ClockError::PlatformCallFailed);
            }
            self.ticks_to_ns(counter)
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn qpc_does_not_regress() {
            let clock = QpcClock::new().expect("QPC must be available on supported Windows");
            let first = clock.now().expect("first timestamp");
            let second = clock.now().expect("second timestamp");
            assert!(second >= first);
        }
    }
}

#[cfg(windows)]
pub use platform::QpcClock;
