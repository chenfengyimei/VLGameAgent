//! DX12-rendering capture fixture: a stable-titled window clearing through a
//! deterministic color cycle with a `FLIP_SEQUENTIAL` swap chain, a direct
//! command queue, and fence-synchronized frame pacing.

use std::mem::ManuallyDrop;
use std::time::Instant;

use uga_api_fixtures::common::{FixtureWindow, cycle_color, parse_duration_seconds, run_loop};
use windows::Win32::Foundation::{HANDLE, RECT};
use windows::Win32::Graphics::Direct3D::D3D_FEATURE_LEVEL_11_0;
use windows::Win32::Graphics::Direct3D12::{
    D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_COMMAND_QUEUE_DESC, D3D12_COMMAND_QUEUE_FLAGS,
    D3D12_CPU_DESCRIPTOR_HANDLE, D3D12_DESCRIPTOR_HEAP_DESC, D3D12_DESCRIPTOR_HEAP_FLAG_NONE,
    D3D12_DESCRIPTOR_HEAP_TYPE_RTV, D3D12_FENCE_FLAG_NONE, D3D12_RESOURCE_BARRIER,
    D3D12_RESOURCE_BARRIER_FLAG_NONE, D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
    D3D12_RESOURCE_STATE_PRESENT, D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATES,
    D3D12_RESOURCE_TRANSITION_BARRIER, D3D12CreateDevice, ID3D12CommandAllocator,
    ID3D12CommandList, ID3D12CommandQueue, ID3D12DescriptorHeap, ID3D12Device, ID3D12Fence,
    ID3D12GraphicsCommandList, ID3D12Resource,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_ALPHA_MODE_IGNORE, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, DXGI_PRESENT, DXGI_SCALING_STRETCH, DXGI_SWAP_CHAIN_DESC1,
    DXGI_SWAP_CHAIN_FLAG, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, DXGI_USAGE_RENDER_TARGET_OUTPUT,
    IDXGIFactory2, IDXGISwapChain,
};
use windows::Win32::System::Threading::{
    CREATE_EVENT, CreateEventExW, EVENT_ALL_ACCESS, WaitForSingleObject,
};
use windows::Win32::UI::WindowsAndMessaging::GetClientRect;
use windows::core::{Interface, Result};

const FRAME_TIMEOUT_MS: u32 = 0xFFFF_FFFF;
const BUFFER_COUNT: u32 = 2;

struct Dx12Renderer {
    device: ID3D12Device,
    queue: ID3D12CommandQueue,
    swap: IDXGISwapChain,
    heap: ID3D12DescriptorHeap,
    handle_step: usize,
    allocator: ID3D12CommandAllocator,
    command_list: ID3D12GraphicsCommandList,
    fence: ID3D12Fence,
    fence_event: HANDLE,
    fence_value: u64,
    frame_index: u32,
    target_size: (i32, i32),
}

impl Dx12Renderer {
    fn new(window: &FixtureWindow) -> Result<Self> {
        let (width, height) = client_size(window);
        let mut device: Option<ID3D12Device> = None;
        unsafe {
            D3D12CreateDevice(None, D3D_FEATURE_LEVEL_11_0, &raw mut device)?;
        }
        let device = device.expect("D3D12 returned success without a device");
        let queue_desc = D3D12_COMMAND_QUEUE_DESC {
            Type: D3D12_COMMAND_LIST_TYPE_DIRECT,
            Priority: 0,
            Flags: D3D12_COMMAND_QUEUE_FLAGS::default(),
            NodeMask: 0,
        };
        let queue: ID3D12CommandQueue =
            unsafe { device.CreateCommandQueue(&raw const queue_desc)? };
        let factory: IDXGIFactory2 = unsafe { CreateDXGIFactory1()? };
        let swap_desc = DXGI_SWAP_CHAIN_DESC1 {
            Width: u32::try_from(width).unwrap_or(480),
            Height: u32::try_from(height).unwrap_or(360),
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            Stereo: windows::core::BOOL(0),
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
            BufferCount: BUFFER_COUNT,
            Scaling: DXGI_SCALING_STRETCH,
            SwapEffect: DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
            AlphaMode: DXGI_ALPHA_MODE_IGNORE,
            Flags: 0,
        };
        let swap1 = unsafe {
            factory.CreateSwapChainForHwnd(
                &queue,
                window.handle,
                &raw const swap_desc,
                None,
                None,
            )?
        };
        let swap: IDXGISwapChain = swap1.cast()?;
        let heap: ID3D12DescriptorHeap = unsafe {
            device.CreateDescriptorHeap(&D3D12_DESCRIPTOR_HEAP_DESC {
                Type: D3D12_DESCRIPTOR_HEAP_TYPE_RTV,
                NumDescriptors: BUFFER_COUNT,
                Flags: D3D12_DESCRIPTOR_HEAP_FLAG_NONE,
                NodeMask: 0,
            })?
        };
        let handle_step =
            unsafe { device.GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV) }
                as usize;
        let allocator: ID3D12CommandAllocator =
            unsafe { device.CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT)? };
        let command_list: ID3D12GraphicsCommandList = unsafe {
            device.CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, &allocator, None)?
        };
        unsafe {
            command_list.Close()?;
        }
        let fence: ID3D12Fence = unsafe { device.CreateFence(0, D3D12_FENCE_FLAG_NONE)? };
        // EVENT_ALL_ACCESS is required: a zero desired-access mask makes the
        // fence event unusable (SetEventOnCompletion fails with access denied).
        let fence_event =
            unsafe { CreateEventExW(None, None, CREATE_EVENT(0), EVENT_ALL_ACCESS.0)? };
        let mut renderer = Self {
            device,
            queue,
            swap,
            heap,
            handle_step,
            allocator,
            command_list,
            fence,
            fence_event,
            fence_value: 0,
            frame_index: 0,
            target_size: (width, height),
        };
        renderer.create_render_targets()?;
        Ok(renderer)
    }

    fn render_target_handle(&self, index: u32) -> D3D12_CPU_DESCRIPTOR_HANDLE {
        let start = unsafe { self.heap.GetCPUDescriptorHandleForHeapStart() };
        D3D12_CPU_DESCRIPTOR_HANDLE {
            ptr: start.ptr + self.handle_step * index as usize,
        }
    }

    fn create_render_targets(&mut self) -> Result<()> {
        for index in 0..BUFFER_COUNT {
            unsafe {
                let buffer: ID3D12Resource = self.swap.GetBuffer(index)?;
                self.device
                    .CreateRenderTargetView(&buffer, None, self.render_target_handle(index));
            }
        }
        Ok(())
    }

    fn sync_to_client_size(&mut self, window: &FixtureWindow) -> Result<()> {
        let size = client_size(window);
        if size == self.target_size {
            return Ok(());
        }
        // The CPU must be idle before resizing; wait for the last frame fence.
        unsafe {
            self.wait_for_fence()?;
            self.swap.ResizeBuffers(
                0,
                0,
                0,
                DXGI_FORMAT_B8G8R8A8_UNORM,
                DXGI_SWAP_CHAIN_FLAG(0),
            )?;
        }
        self.target_size = size;
        self.create_render_targets()
    }

    unsafe fn wait_for_fence(&mut self) -> Result<()> {
        self.fence_value += 1;
        unsafe {
            self.queue.Signal(&self.fence, self.fence_value)?;
            self.fence
                .SetEventOnCompletion(self.fence_value, self.fence_event)?;
            WaitForSingleObject(self.fence_event, FRAME_TIMEOUT_MS);
        }
        Ok(())
    }

    fn render(&mut self, color: (f32, f32, f32)) -> Result<()> {
        let frame_index = self.frame_index;
        let buffer: ID3D12Resource = unsafe { self.swap.GetBuffer(frame_index)? };
        let handle = self.render_target_handle(frame_index);
        let color_rgba = [color.0, color.1, color.2, 1.0];
        stage("allocator-reset", || unsafe { self.allocator.Reset() })?;
        stage("list-reset", || unsafe {
            self.command_list.Reset(&self.allocator, None)
        })?;
        stage("barriers-clear-close", || unsafe {
            self.command_list.ResourceBarrier(&[transition_barrier(
                &buffer,
                D3D12_RESOURCE_STATE_PRESENT,
                D3D12_RESOURCE_STATE_RENDER_TARGET,
            )]);
            self.command_list
                .ClearRenderTargetView(handle, &color_rgba, None);
            self.command_list.ResourceBarrier(&[transition_barrier(
                &buffer,
                D3D12_RESOURCE_STATE_RENDER_TARGET,
                D3D12_RESOURCE_STATE_PRESENT,
            )]);
            self.command_list.Close()
        })?;
        stage("execute", || unsafe {
            let command_list: ID3D12CommandList = self.command_list.cast()?;
            self.queue.ExecuteCommandLists(&[Some(command_list)]);
            Ok(())
        })?;
        stage("present", || unsafe {
            self.swap.Present(1, DXGI_PRESENT(0)).ok()
        })?;
        stage("fence", || unsafe { self.wait_for_fence() })?;
        // FLIP_SEQUENTIAL with two buffers alternates the back buffer index
        // deterministically after each Present.
        self.frame_index = (self.frame_index + 1) % BUFFER_COUNT;
        Ok(())
    }
}

fn stage<T>(name: &str, call: impl FnOnce() -> Result<T>) -> Result<T> {
    call().inspect_err(|error| eprintln!("dx12 stage {name}: {error}"))
}

fn transition_barrier(
    resource: &ID3D12Resource,
    before: D3D12_RESOURCE_STATES,
    after: D3D12_RESOURCE_STATES,
) -> D3D12_RESOURCE_BARRIER {
    let mut barrier = D3D12_RESOURCE_BARRIER {
        Type: D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
        Flags: D3D12_RESOURCE_BARRIER_FLAG_NONE,
        ..Default::default()
    };
    barrier.Anonymous.Transition = ManuallyDrop::new(D3D12_RESOURCE_TRANSITION_BARRIER {
        pResource: ManuallyDrop::new(Some(resource.clone())),
        Subresource: 0,
        StateBefore: before,
        StateAfter: after,
    });
    barrier
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
    let window = FixtureWindow::create("UGA API Fixture DX12")?;
    let mut renderer = Dx12Renderer::new(&window)?;
    let start = Instant::now();
    let limit = parse_duration_seconds(&args);
    run_loop(
        &window,
        &mut || {
            if renderer.sync_to_client_size(&window).is_err() {
                return;
            }
            let color = cycle_color(start.elapsed());
            if let Err(error) = renderer.render(color) {
                eprintln!("dx12 render failed: {error}");
                uga_api_fixtures::common::request_quit();
            }
        },
        limit,
    );
    Ok(())
}
