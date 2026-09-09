//! DX11-rendering capture fixture: a stable-titled window clearing through a
//! deterministic color cycle with a `FLIP_SEQUENTIAL` swap chain, and correct
//! back-buffer handling across window resizes.

use std::time::Instant;

use uga_api_fixtures::common::{FixtureWindow, cycle_color, parse_duration_seconds, run_loop};
use windows::Win32::Foundation::{HMODULE, RECT};
use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0};
use windows::Win32::Graphics::Direct3D11::{
    D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11CreateDeviceAndSwapChain, ID3D11Device,
    ID3D11DeviceContext, ID3D11RenderTargetView, ID3D11Resource, ID3D11Texture2D,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_MODE_DESC, DXGI_RATIONAL, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    DXGI_PRESENT, DXGI_SWAP_CHAIN_DESC, DXGI_SWAP_CHAIN_FLAG, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
    DXGI_USAGE_RENDER_TARGET_OUTPUT, IDXGISwapChain,
};
use windows::Win32::UI::WindowsAndMessaging::GetClientRect;
use windows::core::{Interface, Result};

struct Dx11Renderer {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    swap_chain: IDXGISwapChain,
    render_target: Option<ID3D11RenderTargetView>,
    target_size: (i32, i32),
}

impl Dx11Renderer {
    fn new(window: &FixtureWindow) -> Result<Self> {
        let (width, height) = client_size(window);
        let swap_chain_desc = DXGI_SWAP_CHAIN_DESC {
            BufferDesc: DXGI_MODE_DESC {
                Width: u32::try_from(width).unwrap_or(480),
                Height: u32::try_from(height).unwrap_or(360),
                RefreshRate: DXGI_RATIONAL {
                    Numerator: 60,
                    Denominator: 1,
                },
                Format: DXGI_FORMAT_B8G8R8A8_UNORM,
                ..Default::default()
            },
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
            BufferCount: 2,
            OutputWindow: window.handle,
            Windowed: windows::core::BOOL(1),
            SwapEffect: DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
            Flags: 0,
        };
        let mut swap_chain: Option<IDXGISwapChain> = None;
        let mut device: Option<ID3D11Device> = None;
        let mut context: Option<ID3D11DeviceContext> = None;
        unsafe {
            D3D11CreateDeviceAndSwapChain(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0]),
                7,
                Some(&raw const swap_chain_desc),
                Some(&raw mut swap_chain),
                Some(&raw mut device),
                None,
                Some(&raw mut context),
            )?;
        }
        let swap_chain = swap_chain.expect("D3D11 returned success without a swap chain");
        let device = device.expect("D3D11 returned success without a device");
        let context = context.expect("D3D11 returned success without a context");
        let mut renderer = Self {
            device,
            context,
            swap_chain,
            render_target: None,
            target_size: (width, height),
        };
        renderer.recreate_render_target()?;
        Ok(renderer)
    }

    fn recreate_render_target(&mut self) -> Result<()> {
        unsafe {
            let back_buffer: ID3D11Texture2D = self.swap_chain.GetBuffer(0)?;
            let back_resource: ID3D11Resource = back_buffer.cast()?;
            let mut render_target: Option<ID3D11RenderTargetView> = None;
            self.device.CreateRenderTargetView(
                &back_resource,
                None,
                Some(&raw mut render_target),
            )?;
            self.render_target = render_target;
        }
        Ok(())
    }

    fn sync_to_client_size(&mut self, window: &FixtureWindow) -> Result<()> {
        let size = client_size(window);
        if size == self.target_size {
            return Ok(());
        }
        unsafe {
            self.render_target = None;
            self.swap_chain.ResizeBuffers(
                0,
                0,
                0,
                DXGI_FORMAT_B8G8R8A8_UNORM,
                DXGI_SWAP_CHAIN_FLAG(0),
            )?;
        }
        self.target_size = size;
        self.recreate_render_target()
    }

    fn render(&self, color: (f32, f32, f32)) -> Result<()> {
        if let Some(render_target) = &self.render_target {
            unsafe {
                self.context
                    .ClearRenderTargetView(render_target, &[color.0, color.1, color.2, 1.0]);
            }
        }
        unsafe { self.swap_chain.Present(1, DXGI_PRESENT(0)).ok() }
    }
}

fn client_size(window: &FixtureWindow) -> (i32, i32) {
    let mut rect = RECT::default();
    unsafe {
        let _ = GetClientRect(window.handle, &raw mut rect);
    }
    (
        (rect.right - rect.left).max(8),
        (rect.bottom - rect.top).max(8),
    )
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let window = FixtureWindow::create("UGA API Fixture DX11")?;
    let mut renderer = Dx11Renderer::new(&window)?;
    let start = Instant::now();
    let limit = parse_duration_seconds(&args);
    run_loop(
        &window,
        &mut || {
            if renderer.sync_to_client_size(&window).is_err() {
                return;
            }
            let color = cycle_color(start.elapsed());
            if renderer.render(color).is_err() {
                // Device removal ends the fixture; the soak treats a vanished
                // window as a target-lost condition rather than looping.
                uga_api_fixtures::common::request_quit();
            }
        },
        limit,
    );
    Ok(())
}
