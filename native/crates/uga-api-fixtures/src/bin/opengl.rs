//! OpenGL-rendering capture fixture: a stable-titled window with a legacy
//! WGL context clearing through a deterministic color cycle via
//! `opengl32.dll`, exercising the OpenGL capture path.

use std::time::Instant;

use uga_api_fixtures::common::{FixtureWindow, cycle_color, parse_duration_seconds, run_loop};
use windows::Win32::Foundation::{HMODULE, HWND};
use windows::Win32::Graphics::Gdi::{GetDC, HDC, ReleaseDC};
use windows::Win32::Graphics::OpenGL::{
    ChoosePixelFormat, DescribePixelFormat, HGLRC, PFD_DOUBLEBUFFER, PFD_DRAW_TO_WINDOW,
    PFD_SUPPORT_OPENGL, PFD_TYPE_RGBA, PIXELFORMATDESCRIPTOR, SetPixelFormat, SwapBuffers,
    wglCreateContext, wglDeleteContext, wglMakeCurrent,
};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::core::{Result, s, w};

const GL_COLOR_BUFFER_BIT: u32 = 0x0000_4000;

type GlClearColor = unsafe extern "system" fn(f32, f32, f32, f32);
type GlClear = unsafe extern "system" fn(u32);

struct GlRenderer {
    window_handle: HWND,
    window_dc: HDC,
    context: HGLRC,
    gl_clear_color: GlClearColor,
    gl_clear: GlClear,
}

impl GlRenderer {
    fn new(window: &FixtureWindow) -> Result<Self> {
        unsafe {
            let window_dc = GetDC(Some(window.handle));
            let descriptor = PIXELFORMATDESCRIPTOR {
                nSize: u16::try_from(std::mem::size_of::<PIXELFORMATDESCRIPTOR>()).unwrap_or(0),
                nVersion: 1,
                dwFlags: PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER,
                iPixelType: PFD_TYPE_RGBA,
                cColorBits: 32,
                ..Default::default()
            };
            let pixel_format = ChoosePixelFormat(window_dc, &raw const descriptor);
            if pixel_format == 0
                || SetPixelFormat(window_dc, pixel_format, &raw const descriptor).is_err()
            {
                return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                    -1,
                )));
            }
            let mut actual = PIXELFORMATDESCRIPTOR::default();
            let _ = DescribePixelFormat(
                window_dc,
                pixel_format,
                u32::try_from(std::mem::size_of::<PIXELFORMATDESCRIPTOR>()).unwrap_or(0),
                Some(&raw mut actual),
            );
            let context = wglCreateContext(window_dc)?;
            if wglMakeCurrent(window_dc, context).is_err() {
                let _ = wglDeleteContext(context);
                return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                    -1,
                )));
            }
            let module = GetModuleHandleW(w!("opengl32.dll"))?;
            let clear_color = Self::load(module, s!("glClearColor"))?;
            let clear = Self::load(module, s!("glClear"))?;
            Ok(Self {
                window_handle: window.handle,
                window_dc,
                context,
                gl_clear_color: clear_color,
                gl_clear: clear,
            })
        }
    }

    unsafe fn load<T>(module: HMODULE, name: windows::core::PCSTR) -> Result<T>
    where
        T: Copy,
    {
        unsafe {
            let address = GetProcAddress(module, name);
            if address.is_none() {
                return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                    -1,
                )));
            }
            Ok(std::mem::transmute_copy(&address))
        }
    }

    fn render(&self, color: (f32, f32, f32)) {
        unsafe {
            (self.gl_clear_color)(color.0, color.1, color.2, 1.0);
            (self.gl_clear)(GL_COLOR_BUFFER_BIT);
            let _ = SwapBuffers(self.window_dc);
        }
    }
}

impl Drop for GlRenderer {
    fn drop(&mut self) {
        unsafe {
            let _ = wglMakeCurrent(self.window_dc, HGLRC::default());
            let _ = wglDeleteContext(self.context);
            let _ = ReleaseDC(Some(self.window_handle), self.window_dc);
        }
    }
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let window = FixtureWindow::create("UGA API Fixture OpenGL")?;
    let renderer = GlRenderer::new(&window)?;
    let start = Instant::now();
    let limit = parse_duration_seconds(&args);
    run_loop(
        &window,
        &mut || {
            let color = cycle_color(start.elapsed());
            renderer.render(color);
        },
        limit,
    );
    Ok(())
}
