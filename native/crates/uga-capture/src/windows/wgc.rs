use std::marker::PhantomData;
use std::rc::Rc;
use std::sync::mpsc::{Receiver, SyncSender, sync_channel};
use std::time::Duration;

use uga_clock::{Clock, QpcClock, UgaTimeNs};
use windows::Foundation::TypedEventHandler;
use windows::Graphics::Capture::{
    Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession,
};
use windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
use windows::Graphics::DirectX::DirectXPixelFormat;
use windows::Win32::Foundation::{HWND, RECT, RPC_E_CHANGED_MODE};
use windows::Win32::Graphics::Direct3D11::{
    D3D11_CPU_ACCESS_READ, D3D11_MAP_READ, D3D11_MAPPED_SUBRESOURCE, D3D11_TEXTURE2D_DESC,
    D3D11_USAGE_STAGING, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
};
use windows::Win32::Graphics::Dxgi::IDXGIDevice;
use windows::Win32::System::WinRT::Direct3D11::{
    CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess,
};
use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;
use windows::Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize, RoUninitialize};
use windows::Win32::UI::WindowsAndMessaging::{GetWindowRect, IsWindow};
use windows::core::{IInspectable, Interface, factory};

use super::dxgi::create_device;
use super::{CaptureError, CapturedBgraFrame};

pub struct WgcCapture {
    hwnd: HWND,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    winrt_device: IDirect3DDevice,
    frame_pool: Direct3D11CaptureFramePool,
    session: GraphicsCaptureSession,
    frame_arrived_token: i64,
    arrivals: Receiver<()>,
    staging: Option<(u32, u32, ID3D11Texture2D)>,
    clock: QpcClock,
    ro_initialized: bool,
    _thread_bound: PhantomData<Rc<()>>,
}

impl WgcCapture {
    /// Create a borderless Windows Graphics Capture session for a window.
    ///
    /// # Errors
    ///
    /// Returns an error when `WinRT`, D3D11, the target item, or capture session is unavailable.
    pub fn new(hwnd: HWND) -> Result<Self, CaptureError> {
        if hwnd.0.is_null() || !unsafe { IsWindow(Some(hwnd)) }.as_bool() {
            return Err(CaptureError::TargetLost);
        }
        let ro_initialized = match unsafe { RoInitialize(RO_INIT_MULTITHREADED) } {
            Ok(()) => true,
            Err(error) if error.code() == RPC_E_CHANGED_MODE => false,
            Err(error) => return Err(CaptureError::Platform(error)),
        };
        let result = Self::create_initialized(hwnd, ro_initialized);
        if result.is_err() && ro_initialized {
            unsafe { RoUninitialize() };
        }
        result
    }

    fn create_initialized(hwnd: HWND, ro_initialized: bool) -> Result<Self, CaptureError> {
        let (device, context) = create_device()?;
        let dxgi_device: IDXGIDevice = device.cast()?;
        let inspectable = unsafe { CreateDirect3D11DeviceFromDXGIDevice(&dxgi_device)? };
        let winrt_device: IDirect3DDevice = inspectable.cast()?;
        let interop: IGraphicsCaptureItemInterop =
            factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()?;
        let item: GraphicsCaptureItem = unsafe { interop.CreateForWindow(hwnd)? };
        let size = item.Size()?;
        if size.Width <= 0 || size.Height <= 0 {
            return Err(CaptureError::TargetLost);
        }
        let frame_pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            &winrt_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            size,
        )?;
        let session = frame_pool.CreateCaptureSession(&item)?;
        let (arrivals_tx, arrivals) = sync_channel(1);
        let frame_arrived_token = install_frame_handler(&frame_pool, arrivals_tx)?;
        session.StartCapture()?;
        Ok(Self {
            hwnd,
            device,
            context,
            winrt_device,
            frame_pool,
            session,
            frame_arrived_token,
            arrivals,
            staging: None,
            clock: QpcClock::new()?,
            ro_initialized,
            _thread_bound: PhantomData,
        })
    }

    /// Wait for and copy the newest available frame into tightly packed BGRA bytes.
    ///
    /// # Errors
    ///
    /// Returns timeout, target-lost, unsupported-state, or platform errors.
    pub fn capture(&mut self, timeout_ms: u32) -> Result<CapturedBgraFrame, CaptureError> {
        if !unsafe { IsWindow(Some(self.hwnd)) }.as_bool() {
            return Err(CaptureError::TargetLost);
        }
        self.arrivals
            .recv_timeout(Duration::from_millis(u64::from(timeout_ms)))
            .map_err(|_| CaptureError::Timeout)?;
        // Drain coalesced notifications and then pull all queued frames, retaining the newest.
        while self.arrivals.try_recv().is_ok() {}
        let mut newest = self.frame_pool.TryGetNextFrame()?;
        while let Ok(next) = self.frame_pool.TryGetNextFrame() {
            let _ = newest.Close();
            newest = next;
        }
        let result = self.copy_frame(&newest);
        let closed = newest.Close();
        match (result, closed) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(CaptureError::Platform(error)),
            (Ok(frame), Ok(())) => Ok(frame),
        }
    }

    fn copy_frame(
        &mut self,
        frame: &windows::Graphics::Capture::Direct3D11CaptureFrame,
    ) -> Result<CapturedBgraFrame, CaptureError> {
        let content_size = frame.ContentSize()?;
        let width = u32::try_from(content_size.Width)
            .map_err(|_| CaptureError::Unsupported("invalid WGC frame width"))?;
        let height = u32::try_from(content_size.Height)
            .map_err(|_| CaptureError::Unsupported("invalid WGC frame height"))?;
        if width == 0 || height == 0 {
            return Err(CaptureError::TargetLost);
        }
        let surface = frame.Surface()?;
        let access: IDirect3DDxgiInterfaceAccess = surface.cast()?;
        let texture: ID3D11Texture2D = unsafe { access.GetInterface()? };
        let mut source_desc = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&raw mut source_desc) };
        if width > source_desc.Width || height > source_desc.Height {
            return Err(CaptureError::Unsupported(
                "WGC content exceeds its frame surface",
            ));
        }
        let staging = self.staging_texture(source_desc)?;
        unsafe { self.context.CopyResource(&staging, &texture) };
        let bytes = map_tightly_packed(&self.context, &staging, width, height)?;

        let mut physical_rect = RECT::default();
        unsafe { GetWindowRect(self.hwnd, &raw mut physical_rect)? };
        let system_time = frame.SystemRelativeTime()?;
        let present_estimate = i128::from(system_time.Duration)
            .checked_mul(100)
            .and_then(|value| i64::try_from(value).ok())
            .filter(|value| *value >= 0)
            .map(UgaTimeNs);
        if source_desc.Width != width || source_desc.Height != height {
            self.frame_pool.Recreate(
                &self.winrt_device,
                DirectXPixelFormat::B8G8R8A8UIntNormalized,
                2,
                content_size,
            )?;
            self.staging = None;
        }
        Ok(CapturedBgraFrame {
            captured_at: self.clock.now()?,
            present_estimate,
            physical_rect,
            width,
            height,
            stride_bytes: width * 4,
            bytes,
        })
    }

    fn staging_texture(
        &mut self,
        source_desc: D3D11_TEXTURE2D_DESC,
    ) -> Result<ID3D11Texture2D, CaptureError> {
        let recreate = self.staging.as_ref().is_none_or(|(width, height, _)| {
            *width != source_desc.Width || *height != source_desc.Height
        });
        if recreate {
            let staging_desc = D3D11_TEXTURE2D_DESC {
                Usage: D3D11_USAGE_STAGING,
                BindFlags: 0,
                CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
                MiscFlags: 0,
                ..source_desc
            };
            let mut texture = None;
            unsafe {
                self.device.CreateTexture2D(
                    &raw const staging_desc,
                    None,
                    Some(&raw mut texture),
                )?;
            }
            self.staging = Some((
                source_desc.Width,
                source_desc.Height,
                texture.ok_or(CaptureError::Unsupported("staging texture was not created"))?,
            ));
        }
        Ok(self
            .staging
            .as_ref()
            .expect("staging initialized")
            .2
            .clone())
    }
}

impl Drop for WgcCapture {
    fn drop(&mut self) {
        let _ = self.frame_pool.RemoveFrameArrived(self.frame_arrived_token);
        let _ = self.session.Close();
        let _ = self.frame_pool.Close();
        if self.ro_initialized {
            unsafe { RoUninitialize() };
        }
    }
}

fn install_frame_handler(
    frame_pool: &Direct3D11CaptureFramePool,
    arrivals: SyncSender<()>,
) -> Result<i64, CaptureError> {
    let handler = TypedEventHandler::<Direct3D11CaptureFramePool, IInspectable>::new(
        move |_sender, _args| {
            let _ = arrivals.try_send(());
            Ok(())
        },
    );
    Ok(frame_pool.FrameArrived(&handler)?)
}

fn map_tightly_packed(
    context: &ID3D11DeviceContext,
    staging: &ID3D11Texture2D,
    width: u32,
    height: u32,
) -> Result<Vec<u8>, CaptureError> {
    let row_bytes = usize::try_from(width)
        .ok()
        .and_then(|value| value.checked_mul(4))
        .ok_or(CaptureError::Unsupported("frame row size overflow"))?;
    let total_bytes = row_bytes
        .checked_mul(height as usize)
        .ok_or(CaptureError::Unsupported("frame size overflow"))?;
    let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
    unsafe {
        context.Map(staging, 0, D3D11_MAP_READ, 0, Some(&raw mut mapped))?;
    }
    let copied = if mapped.pData.is_null() || mapped.RowPitch < width * 4 {
        Err(CaptureError::Unsupported("invalid mapped WGC texture"))
    } else {
        let mut output = Vec::with_capacity(total_bytes);
        for row in 0..height {
            let offset = row as usize * mapped.RowPitch as usize;
            // SAFETY: D3D11 Map exposes `height` rows with at least `width * 4` bytes each.
            let source = unsafe {
                core::slice::from_raw_parts((mapped.pData as *const u8).add(offset), row_bytes)
            };
            output.extend_from_slice(source);
        }
        Ok(output)
    };
    unsafe { context.Unmap(staging, 0) };
    copied
}
