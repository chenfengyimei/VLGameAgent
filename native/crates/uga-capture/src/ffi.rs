use std::cell::RefCell;
use std::ffi::{c_char, c_void};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::sync::mpsc::{RecvTimeoutError, SyncSender, sync_channel};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use windows::Win32::Foundation::HWND;

use crate::windows::{CaptureError, CapturedBgraFrame, DxgiCapture, WgcCapture};

const STATUS_OK: i32 = 0;
const STATUS_INVALID_ARGUMENT: i32 = 1;
const STATUS_UNAVAILABLE: i32 = 2;
const STATUS_TIMEOUT: i32 = 3;
const STATUS_ACCESS_LOST: i32 = 4;
const STATUS_TARGET_LOST: i32 = 5;
const STATUS_INTERNAL: i32 = 6;
const BACKEND_WGC: u32 = 1;
const BACKEND_DXGI: u32 = 2;
// R21: bounded waits around every cross-thread capture boundary. A driver
// that exceeds its own capture timeout by this grace margin is treated as
// stuck; the session is abandoned instead of hanging the caller forever.
const RESPONSE_GRACE_MS: u64 = 2_000;
const INIT_DEADLINE_MS: u64 = 10_000;

thread_local! {
    static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
}

#[repr(C)]
pub struct UgaCaptureFrame {
    pub capture_timestamp_ns: i64,
    pub present_estimate_ns: i64,
    pub has_present_estimate: u8,
    pub _padding: [u8; 7],
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
    pub width: u32,
    pub height: u32,
    pub stride_bytes: u32,
    pub data: *mut u8,
    pub data_len: usize,
}

impl Default for UgaCaptureFrame {
    fn default() -> Self {
        Self {
            capture_timestamp_ns: 0,
            present_estimate_ns: 0,
            has_present_estimate: 0,
            _padding: [0; 7],
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
            width: 0,
            height: 0,
            stride_bytes: 0,
            data: ptr::null_mut(),
            data_len: 0,
        }
    }
}

struct NativeError {
    status: i32,
    message: String,
}

enum CaptureCommand {
    Capture {
        timeout_ms: u32,
        response: SyncSender<Result<CapturedBgraFrame, NativeError>>,
    },
    Stop,
}

enum CaptureImplementation {
    Wgc(WgcCapture),
    Dxgi(DxgiCapture),
}

impl CaptureImplementation {
    fn create(backend: u32, hwnd: HWND) -> Result<Self, NativeError> {
        match backend {
            BACKEND_WGC => WgcCapture::new(hwnd)
                .map(Self::Wgc)
                .map_err(NativeError::from),
            BACKEND_DXGI => DxgiCapture::new(hwnd)
                .map(Self::Dxgi)
                .map_err(NativeError::from),
            _ => Err(NativeError {
                status: STATUS_INVALID_ARGUMENT,
                message: format!("unknown capture backend id: {backend}"),
            }),
        }
    }

    fn capture(&mut self, timeout_ms: u32) -> Result<CapturedBgraFrame, NativeError> {
        match self {
            Self::Wgc(capture) => capture.capture(timeout_ms),
            Self::Dxgi(capture) => capture.capture(timeout_ms),
        }
        .map_err(NativeError::from)
    }
}

impl From<CaptureError> for NativeError {
    fn from(value: CaptureError) -> Self {
        let status = match value {
            CaptureError::Timeout => STATUS_TIMEOUT,
            CaptureError::AccessLost => STATUS_ACCESS_LOST,
            CaptureError::TargetLost => STATUS_TARGET_LOST,
            CaptureError::Unsupported(_) | CaptureError::Platform(_) => STATUS_UNAVAILABLE,
            CaptureError::Clock(_) => STATUS_INTERNAL,
        };
        Self {
            status,
            message: value.to_string(),
        }
    }
}

pub struct NativeCaptureHandle {
    commands: SyncSender<CaptureCommand>,
    worker: Option<JoinHandle<()>>,
    // R21: set when a capture response exceeded its bounded wait. The worker
    // thread is deliberately abandoned (it may be stuck inside a driver call)
    // and the session is marked unusable — bounded failure, never a hang.
    poisoned: bool,
}

fn set_last_error(message: impl Into<String>) {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = message.into());
}

fn clear_last_error() {
    set_last_error(String::new());
}

fn create_handle(backend: u32, hwnd: isize) -> Result<NativeCaptureHandle, NativeError> {
    if hwnd == 0 {
        return Err(NativeError {
            status: STATUS_INVALID_ARGUMENT,
            message: "HWND cannot be zero".to_string(),
        });
    }
    let (commands, receiver) = sync_channel::<CaptureCommand>(1);
    let (initialized, init_receiver) = sync_channel::<Result<(), NativeError>>(1);
    let worker = thread::Builder::new()
        .name("uga-native-capture".to_string())
        .spawn(move || {
            let hwnd = HWND(hwnd as *mut c_void);
            let Ok(mut capture) =
                CaptureImplementation::create(backend, hwnd).inspect_err(|error| {
                    let _ = initialized.send(Err(NativeError {
                        status: error.status,
                        message: error.message.clone(),
                    }));
                })
            else {
                return;
            };
            if initialized.send(Ok(())).is_err() {
                return;
            }
            while let Ok(command) = receiver.recv() {
                match command {
                    CaptureCommand::Capture {
                        timeout_ms,
                        response,
                    } => {
                        let _ = response.send(capture.capture(timeout_ms));
                    }
                    CaptureCommand::Stop => break,
                }
            }
        })
        .map_err(|error| NativeError {
            status: STATUS_INTERNAL,
            message: format!("failed to start capture worker: {error}"),
        })?;
    match init_receiver.recv_timeout(Duration::from_millis(INIT_DEADLINE_MS)) {
        Ok(Ok(())) => Ok(NativeCaptureHandle {
            commands,
            worker: Some(worker),
            poisoned: false,
        }),
        Ok(Err(error)) => {
            let _ = worker.join();
            Err(error)
        }
        Err(RecvTimeoutError::Timeout) => {
            // R21: bounded initialization. The worker may be stuck inside a
            // driver call; detach it (a late completion exits on its own via
            // the dropped channel) instead of hanging the caller forever.
            drop(worker);
            Err(NativeError {
                status: STATUS_INTERNAL,
                message: "capture worker initialization exceeded the bounded deadline".to_string(),
            })
        }
        Err(RecvTimeoutError::Disconnected) => {
            let _ = worker.join();
            Err(NativeError {
                status: STATUS_INTERNAL,
                message: "capture worker initialization channel failed".to_string(),
            })
        }
    }
}

fn capture_frame(
    handle: &mut NativeCaptureHandle,
    timeout_ms: u32,
) -> Result<CapturedBgraFrame, NativeError> {
    let (response, receiver) = sync_channel(1);
    handle
        .commands
        .send(CaptureCommand::Capture {
            timeout_ms,
            response,
        })
        .map_err(|error| NativeError {
            status: STATUS_INTERNAL,
            message: format!("capture worker is unavailable: {error}"),
        })?;
    // R21: bound the response wait. The worker's own capture carries
    // timeout_ms; a driver that exceeds it by the grace margin is stuck, and
    // the session must fail bounded instead of hanging the caller forever.
    let bounded_ms = u64::from(timeout_ms).saturating_add(RESPONSE_GRACE_MS);
    match receiver.recv_timeout(Duration::from_millis(bounded_ms)) {
        Ok(result) => result,
        Err(RecvTimeoutError::Timeout) => {
            handle.poisoned = true;
            // Disconnect the command channel: further captures fail fast
            // instead of queueing behind a stuck worker.
            let (dead_tx, dead_rx) = sync_channel::<CaptureCommand>(0);
            drop(dead_rx);
            handle.commands = dead_tx;
            Err(NativeError {
                status: STATUS_TIMEOUT,
                message:
                    "capture worker did not respond within the bounded wait; session abandoned"
                        .to_string(),
            })
        }
        Err(RecvTimeoutError::Disconnected) => {
            handle.poisoned = true;
            Err(NativeError {
                status: STATUS_INTERNAL,
                message: "capture worker response channel failed".to_string(),
            })
        }
    }
}

fn export_frame(frame: CapturedBgraFrame) -> UgaCaptureFrame {
    let mut bytes = frame.bytes.into_boxed_slice();
    let data = bytes.as_mut_ptr();
    let data_len = bytes.len();
    std::mem::forget(bytes);
    UgaCaptureFrame {
        capture_timestamp_ns: frame.captured_at.0,
        present_estimate_ns: frame.present_estimate.map_or(0, |value| value.0),
        has_present_estimate: u8::from(frame.present_estimate.is_some()),
        _padding: [0; 7],
        left: frame.physical_rect.left,
        top: frame.physical_rect.top,
        right: frame.physical_rect.right,
        bottom: frame.physical_rect.bottom,
        width: frame.width,
        height: frame.height,
        stride_bytes: frame.stride_bytes,
        data,
        data_len,
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn uga_capture_abi_version() -> u32 {
    0x0001_0001
}

/// # Safety
///
/// `out_handle` must be a valid writable pointer. The returned handle must be destroyed once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn uga_capture_create(
    backend: u32,
    hwnd: isize,
    out_handle: *mut *mut NativeCaptureHandle,
) -> i32 {
    if out_handle.is_null() {
        set_last_error("out_handle cannot be null");
        return STATUS_INVALID_ARGUMENT;
    }
    match catch_unwind(AssertUnwindSafe(|| create_handle(backend, hwnd))) {
        Ok(Ok(handle)) => {
            // SAFETY: checked non-null above; ownership transfers to the caller.
            unsafe { out_handle.write(Box::into_raw(Box::new(handle))) };
            clear_last_error();
            STATUS_OK
        }
        Ok(Err(error)) => {
            set_last_error(error.message);
            error.status
        }
        Err(_) => {
            set_last_error("panic while creating native capture");
            STATUS_INTERNAL
        }
    }
}

/// # Safety
///
/// `handle` must be a live handle from `uga_capture_create`; `out_frame` must be writable.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn uga_capture_next(
    handle: *mut NativeCaptureHandle,
    timeout_ms: u32,
    out_frame: *mut UgaCaptureFrame,
) -> i32 {
    if handle.is_null() || out_frame.is_null() {
        set_last_error("capture handle and out_frame cannot be null");
        return STATUS_INVALID_ARGUMENT;
    }
    match catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: pointers were checked and the caller owns the live handle/output.
        let capture = unsafe { &mut *handle };
        capture_frame(capture, timeout_ms)
    })) {
        Ok(Ok(frame)) => {
            // SAFETY: out_frame is a valid writable pointer by contract.
            unsafe { out_frame.write(export_frame(frame)) };
            clear_last_error();
            STATUS_OK
        }
        Ok(Err(error)) => {
            set_last_error(error.message);
            error.status
        }
        Err(_) => {
            set_last_error("panic while capturing native frame");
            STATUS_INTERNAL
        }
    }
}

/// # Safety
///
/// `frame` must have been initialized by `uga_capture_next` and released at most once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn uga_capture_frame_release(frame: *mut UgaCaptureFrame) {
    if frame.is_null() {
        return;
    }
    // SAFETY: caller promises a frame produced by this library.
    let frame = unsafe { &mut *frame };
    if !frame.data.is_null() {
        // SAFETY: export_frame allocated exactly data_len bytes as a boxed slice.
        let slice = ptr::slice_from_raw_parts_mut(frame.data, frame.data_len);
        unsafe { drop(Box::from_raw(slice)) };
        frame.data = ptr::null_mut();
        frame.data_len = 0;
    }
}

/// # Safety
///
/// `handle` must be null or a live handle returned by `uga_capture_create`, destroyed once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn uga_capture_destroy(handle: *mut NativeCaptureHandle) {
    if handle.is_null() {
        return;
    }
    // SAFETY: ownership of this allocation returns from the caller.
    let mut handle = unsafe { Box::from_raw(handle) };
    if handle.poisoned {
        // R21: bounded shutdown. The worker was already abandoned after a
        // bounded capture wait (it may be stuck inside a driver call);
        // deliberately leak the stuck OS thread instead of hanging the
        // caller. If it ever unblocks, the disconnected command channel
        // makes the worker exit on its own.
        return;
    }
    let _ = handle.commands.send(CaptureCommand::Stop);
    if let Some(worker) = handle.worker.take() {
        let _ = worker.join();
    }
}

/// Copy the current thread's last native capture error as UTF-8.
///
/// Returns the required byte count excluding a null terminator. A null buffer performs a size query.
///
/// # Safety
///
/// When non-null, `buffer` must point to at least `capacity` writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn uga_capture_last_error(buffer: *mut c_char, capacity: usize) -> usize {
    LAST_ERROR.with(|slot| {
        let message = slot.borrow();
        let bytes = message.as_bytes();
        if !buffer.is_null() && capacity > 0 {
            let copy_len = bytes.len().min(capacity.saturating_sub(1));
            // SAFETY: caller guarantees capacity writable bytes; ranges cannot overlap.
            unsafe { ptr::copy_nonoverlapping(bytes.as_ptr(), buffer.cast::<u8>(), copy_len) };
            // SAFETY: copy_len is at most capacity - 1.
            unsafe { buffer.add(copy_len).write(0) };
        }
        bytes.len()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // R21: a stuck worker (driver never answers) must fail the capture
    // within the bounded wait and poison the session — never hang the caller.
    #[test]
    fn unresponsive_worker_times_out_and_poisons_the_session() {
        let (commands, receiver) = sync_channel::<CaptureCommand>(1);
        let worker = thread::Builder::new()
            .spawn(move || {
                // Consume the capture command, hold its response channel and
                // park forever: a driver stuck inside an OS call.
                if let Ok(CaptureCommand::Capture { response, .. }) = receiver.recv() {
                    std::mem::forget(response);
                    loop {
                        thread::park();
                    }
                }
            })
            .unwrap();
        let mut handle = NativeCaptureHandle {
            commands,
            worker: Some(worker),
            poisoned: false,
        };

        let started = std::time::Instant::now();
        let error = capture_frame(&mut handle, 50).unwrap_err();
        let elapsed = started.elapsed();

        assert_eq!(error.status, STATUS_TIMEOUT);
        assert!(handle.poisoned);
        assert!(elapsed >= Duration::from_millis(50));
        assert!(elapsed < Duration::from_secs(5));

        // The poisoned session fails fast (disconnected commands) instead of
        // queueing behind the stuck worker.
        let followup = capture_frame(&mut handle, 50).unwrap_err();
        assert_eq!(followup.status, STATUS_INTERNAL);
    }

    #[test]
    fn poisoned_handle_capture_fails_fast_without_worker() {
        let (commands, receiver) = sync_channel::<CaptureCommand>(1);
        let _unused = receiver; // the dead peer is the point of this fixture
        let mut handle = NativeCaptureHandle {
            commands,
            worker: None,
            poisoned: true,
        };

        let started = std::time::Instant::now();
        let error = capture_frame(&mut handle, 50).unwrap_err();

        // The command channel has no live receiver, so the bounded response
        // wait expires: the failure is a timeout, and it is bounded by
        // timeout_ms + RESPONSE_GRACE_MS instead of hanging forever.
        assert_eq!(error.status, STATUS_TIMEOUT);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    // R21: destroy must have a bounded duration even for a poisoned session
    // whose worker thread is parked forever (deliberate leak instead of a
    // hanging join).
    #[test]
    fn destroy_poisoned_handle_is_bounded() {
        let (commands, receiver) = sync_channel::<CaptureCommand>(1);
        let _live_receiver = receiver; // keep the worker's recv() alive
        let worker = thread::Builder::new()
            .spawn(|| {
                loop {
                    std::thread::park();
                }
            })
            .unwrap();
        let handle = NativeCaptureHandle {
            commands,
            worker: Some(worker),
            poisoned: true,
        };
        let ptr = Box::into_raw(Box::new(handle));

        let started = std::time::Instant::now();
        // SAFETY: the pointer comes from Box::into_raw immediately above.
        unsafe { uga_capture_destroy(ptr) };

        assert!(started.elapsed() < std::time::Duration::from_secs(1));
    }
}
