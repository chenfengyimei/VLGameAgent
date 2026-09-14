use uga_clock::{Clock, QpcClock};
use windows::Win32::Foundation::{HMODULE, HWND, RECT};
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_HARDWARE;
use windows::Win32::Graphics::Direct3D11::{
    D3D11_CPU_ACCESS_READ, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_MAP_READ,
    D3D11_MAPPED_SUBRESOURCE, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
};
use windows::Win32::Graphics::Dxgi::Common::DXGI_MODE_ROTATION_IDENTITY;
use windows::Win32::Graphics::Dxgi::{
    DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_NOT_FOUND, DXGI_ERROR_WAIT_TIMEOUT, DXGI_OUTDUPL_FRAME_INFO,
    IDXGIAdapter, IDXGIDevice, IDXGIOutput1, IDXGIOutputDuplication, IDXGIResource,
};
use windows::Win32::Graphics::Gdi::{MONITOR_DEFAULTTONEAREST, MonitorFromWindow};
use windows::Win32::UI::WindowsAndMessaging::IsWindow;
use windows::core::Interface;

use super::{CaptureError, CapturedBgraFrame, visible_window_rect};

pub struct DxgiCapture {
    hwnd: HWND,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    duplication: IDXGIOutputDuplication,
    output_rect: RECT,
    staging: Option<(u32, u32, ID3D11Texture2D)>,
    clock: QpcClock,
}

impl DxgiCapture {
    /// Create desktop duplication for the monitor nearest to `hwnd`.
    ///
    /// # Errors
    ///
    /// Returns an error when the target, D3D device, output, or duplication API is unavailable.
    pub fn new(hwnd: HWND) -> Result<Self, CaptureError> {
        if hwnd.0.is_null() || !unsafe { IsWindow(Some(hwnd)) }.as_bool() {
            return Err(CaptureError::TargetLost);
        }
        let (device, context) = create_device()?;
        let dxgi_device: IDXGIDevice = device.cast()?;
        let adapter = unsafe { dxgi_device.GetAdapter()? };
        let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
        let output = find_output(&adapter, monitor)?;
        let output_desc = unsafe { output.GetDesc()? };
        if output_desc.Rotation != DXGI_MODE_ROTATION_IDENTITY {
            return Err(CaptureError::Unsupported("rotated DXGI outputs"));
        }
        let output1: IDXGIOutput1 = output.cast()?;
        let duplication = unsafe { output1.DuplicateOutput(&device)? };
        Ok(Self {
            hwnd,
            device,
            context,
            duplication,
            output_rect: output_desc.DesktopCoordinates,
            staging: None,
            clock: QpcClock::new()?,
        })
    }

    /// Acquire and copy the newest desktop frame cropped to the current target rectangle.
    ///
    /// # Errors
    ///
    /// Returns timeout, access-lost, target-lost, unsupported-state, or platform errors.
    pub fn capture(&mut self, timeout_ms: u32) -> Result<CapturedBgraFrame, CaptureError> {
        if !unsafe { IsWindow(Some(self.hwnd)) }.as_bool() {
            return Err(CaptureError::TargetLost);
        }
        let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut resource: Option<IDXGIResource> = None;
        let acquired = unsafe {
            self.duplication
                .AcquireNextFrame(timeout_ms, &raw mut info, &raw mut resource)
        };
        if let Err(error) = acquired {
            return match error.code() {
                DXGI_ERROR_WAIT_TIMEOUT => Err(CaptureError::Timeout),
                DXGI_ERROR_ACCESS_LOST => Err(CaptureError::AccessLost),
                _ => Err(CaptureError::Platform(error)),
            };
        }

        let resource = resource.ok_or(CaptureError::Unsupported("DXGI returned no resource"))?;
        let result = self.copy_acquired_frame(&resource, info);
        let released = unsafe { self.duplication.ReleaseFrame() };
        match (result, released) {
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                Err(CaptureError::AccessLost)
            }
            (Ok(_), Err(error)) => Err(CaptureError::Platform(error)),
            (Ok(frame), Ok(())) => Ok(frame),
        }
    }

    fn copy_acquired_frame(
        &mut self,
        resource: &IDXGIResource,
        info: DXGI_OUTDUPL_FRAME_INFO,
    ) -> Result<CapturedBgraFrame, CaptureError> {
        let texture: ID3D11Texture2D = resource.cast()?;
        let mut source_desc = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&raw mut source_desc) };
        let staging = self.staging_texture(source_desc)?;
        unsafe { self.context.CopyResource(&staging, &texture) };

        let target_rect = visible_window_rect(self.hwnd)?;
        let crop = intersect(target_rect, self.output_rect).ok_or(CaptureError::TargetLost)?;
        let source_x = u32::try_from(crop.left - self.output_rect.left)
            .map_err(|_| CaptureError::Unsupported("negative crop origin"))?;
        let source_y = u32::try_from(crop.top - self.output_rect.top)
            .map_err(|_| CaptureError::Unsupported("negative crop origin"))?;
        let width = u32::try_from(crop.right - crop.left)
            .map_err(|_| CaptureError::Unsupported("invalid crop width"))?;
        let height = u32::try_from(crop.bottom - crop.top)
            .map_err(|_| CaptureError::Unsupported("invalid crop height"))?;

        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            self.context
                .Map(&staging, 0, D3D11_MAP_READ, 0, Some(&raw mut mapped))?;
        }
        let copied = copy_bgra_rows(mapped, source_x, source_y, width, height);
        unsafe { self.context.Unmap(&staging, 0) };
        let bytes = copied?;
        let captured_at = self.clock.now()?;
        let present_estimate = (info.LastPresentTime > 0)
            .then(|| self.clock.ticks_to_ns(info.LastPresentTime))
            .transpose()?;
        Ok(CapturedBgraFrame {
            captured_at,
            present_estimate,
            physical_rect: crop,
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

pub(super) fn create_device() -> Result<(ID3D11Device, ID3D11DeviceContext), CaptureError> {
    let mut device = None;
    let mut context = None;
    unsafe {
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None,
            D3D11_SDK_VERSION,
            Some(&raw mut device),
            None,
            Some(&raw mut context),
        )?;
    }
    Ok((
        device.ok_or(CaptureError::Unsupported("D3D11 device was not created"))?,
        context.ok_or(CaptureError::Unsupported("D3D11 context was not created"))?,
    ))
}

fn find_output(
    adapter: &IDXGIAdapter,
    monitor: windows::Win32::Graphics::Gdi::HMONITOR,
) -> Result<windows::Win32::Graphics::Dxgi::IDXGIOutput, CaptureError> {
    let mut index = 0;
    loop {
        match unsafe { adapter.EnumOutputs(index) } {
            Ok(output) => {
                if unsafe { output.GetDesc()? }.Monitor == monitor {
                    return Ok(output);
                }
                index += 1;
            }
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => {
                return Err(CaptureError::Unsupported(
                    "no DXGI output matches the target",
                ));
            }
            Err(error) => return Err(CaptureError::Platform(error)),
        }
    }
}

fn intersect(left: RECT, right: RECT) -> Option<RECT> {
    let result = RECT {
        left: left.left.max(right.left),
        top: left.top.max(right.top),
        right: left.right.min(right.right),
        bottom: left.bottom.min(right.bottom),
    };
    (result.right > result.left && result.bottom > result.top).then_some(result)
}

fn copy_bgra_rows(
    mapped: D3D11_MAPPED_SUBRESOURCE,
    source_x: u32,
    source_y: u32,
    width: u32,
    height: u32,
) -> Result<Vec<u8>, CaptureError> {
    if mapped.pData.is_null() {
        return Err(CaptureError::Unsupported("mapped texture has no data"));
    }
    let row_bytes = usize::try_from(width)
        .ok()
        .and_then(|value| value.checked_mul(4))
        .ok_or(CaptureError::Unsupported("frame row size overflow"))?;
    let total_bytes = row_bytes
        .checked_mul(height as usize)
        .ok_or(CaptureError::Unsupported("frame size overflow"))?;
    let mut output = Vec::with_capacity(total_bytes);
    for row in 0..height {
        let offset = usize::try_from(source_y + row)
            .ok()
            .and_then(|value| value.checked_mul(mapped.RowPitch as usize))
            .and_then(|value| value.checked_add(source_x as usize * 4))
            .ok_or(CaptureError::Unsupported("mapped texture offset overflow"))?;
        // SAFETY: D3D11 Map guarantees a readable allocation with RowPitch for every texture row;
        // the crop was intersected with the source texture's desktop output dimensions.
        let source = unsafe {
            core::slice::from_raw_parts((mapped.pData as *const u8).add(offset), row_bytes)
        };
        output.extend_from_slice(source);
    }
    Ok(output)
}
