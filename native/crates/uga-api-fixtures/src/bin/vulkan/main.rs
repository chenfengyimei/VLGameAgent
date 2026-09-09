//! Vulkan-rendering capture fixture: a stable-titled window clearing through
//! a deterministic color cycle on a FIFO swapchain driven by a raw
//! `vulkan-1.dll` loader, exercising the Vulkan capture path.

mod vk;

use std::time::Instant;

use uga_api_fixtures::common::{FixtureWindow, cycle_color, parse_duration_seconds, run_loop};
use vk::{
    VK_ACCESS_MEMORY_READ_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, VK_COMMAND_BUFFER_LEVEL_PRIMARY,
    VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
    VK_ERROR_OUT_OF_DATE_KHR, VK_FENCE_CREATE_SIGNALED_BIT, VK_FORMAT_B8G8R8A8_SRGB,
    VK_FORMAT_B8G8R8A8_UNORM, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
    VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_UNDEFINED,
    VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
    VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PRESENT_MODE_FIFO_KHR,
    VK_QUEUE_GRAPHICS_BIT, VK_SHARING_MODE_CONCURRENT, VK_SHARING_MODE_EXCLUSIVE,
    VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
    VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
    VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
    VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
    VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, VK_STRUCTURE_TYPE_SUBMIT_INFO,
    VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR,
    VK_SUBOPTIMAL_KHR, VK_SUCCESS, VK_TIMEOUT, VK_TRUE, Vk, VkBool32, VkClearColorValue,
    VkColorSpaceKHR, VkCommandBuffer, VkCommandBufferAllocateInfo, VkCommandBufferBeginInfo,
    VkCommandPool, VkCommandPoolCreateInfo, VkCompositeAlphaFlagBitsKHR, VkDevice,
    VkDeviceCreateInfo, VkDeviceQueueCreateInfo, VkEntry, VkExtent2D, VkFence, VkFenceCreateFlags,
    VkFenceCreateInfo, VkFormat, VkImage, VkImageMemoryBarrier, VkImageSubresourceRange,
    VkInstance, VkPhysicalDevice, VkPresentInfoKHR, VkPresentModeKHR, VkQueue,
    VkQueueFamilyProperties, VkSemaphore, VkSemaphoreCreateInfo, VkSharingMode, VkSubmitInfo,
    VkSurfaceCapabilitiesKHR, VkSurfaceFormatKHR, VkSurfaceKHR, VkSwapchainCreateInfoKHR,
    VkSwapchainKHR, VkWin32SurfaceCreateInfoKHR, c_name, check_result,
};
use windows::Win32::Foundation::RECT;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::GetClientRect;
use windows::core::{Error, HRESULT, Result};

/// Occlusion can stall present retirement indefinitely, so every wait is
/// bounded: the loop keeps pumping messages and honoring its duration limit
/// instead of blocking the process forever.
const ACQUIRE_TIMEOUT_NS: u64 = 250 * 1_000_000;
const FENCE_WAIT_SLICE_NS: u64 = 250 * 1_000_000;
const FENCE_WAIT_SLICES: u32 = 8;

/// One frame is in flight at a time: the previous submit must be
/// fence-complete before the shared command buffer is reset.
struct VulkanRenderer {
    vk: Vk,
    instance: VkInstance,
    surface: VkSurfaceKHR,
    physical: VkPhysicalDevice,
    device: VkDevice,
    graphics_queue: VkQueue,
    present_queue: VkQueue,
    graphics_family: u32,
    present_family: u32,
    command_pool: VkCommandPool,
    command_buffer: VkCommandBuffer,
    frame_fence: VkFence,
    acquire_fence: VkFence,
    /// One present-wait semaphore per swapchain image; index-matched.
    render_finished: Vec<VkSemaphore>,
    /// Semaphores from retired swapchains: never reused, destroyed at
    /// teardown (the presentation engine may still hold pending waits).
    retired_semaphores: Vec<VkSemaphore>,
    swapchain: VkSwapchainKHR,
    images: Vec<VkImage>,
    target_size: (i32, i32),
    recreate_pending: bool,
}

impl VulkanRenderer {
    fn new(window: &FixtureWindow) -> Result<Self> {
        let entry = VkEntry::load()?;
        let instance_extensions = [
            c_name(b"VK_KHR_surface\0"),
            c_name(b"VK_KHR_win32_surface\0"),
        ];
        let instance = entry.create_instance(&instance_extensions)?;
        // The dispatch table outlives `entry`: the loader library is held
        // for the process lifetime by design (see `VkEntry::load`).
        let vk = unsafe { Vk::load(&entry, instance)? };
        let surface = create_surface(&vk, instance, window)?;
        let (physical, graphics_family, present_family) = pick_device(&vk, instance, surface)?;
        let device = create_device(&vk, physical, graphics_family, present_family)?;
        let mut graphics_queue = VkQueue::default();
        let mut present_queue = VkQueue::default();
        unsafe {
            (vk.get_device_queue)(device, graphics_family, 0, &raw mut graphics_queue);
            (vk.get_device_queue)(device, present_family, 0, &raw mut present_queue);
        }
        let pool_info = VkCommandPoolCreateInfo {
            s_type: VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
            flags: VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
            queue_family_index: graphics_family,
            ..VkCommandPoolCreateInfo::default()
        };
        let mut command_pool = VkCommandPool::default();
        check_result("create-command-pool", unsafe {
            (vk.create_command_pool)(
                device,
                &raw const pool_info,
                std::ptr::null(),
                &raw mut command_pool,
            )
        })?;
        let alloc_info = VkCommandBufferAllocateInfo {
            s_type: VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
            command_pool,
            level: VK_COMMAND_BUFFER_LEVEL_PRIMARY,
            command_buffer_count: 1,
            ..VkCommandBufferAllocateInfo::default()
        };
        let mut command_buffer = VkCommandBuffer::default();
        check_result("allocate-command-buffer", unsafe {
            (vk.allocate_command_buffers)(device, &raw const alloc_info, &raw mut command_buffer)
        })?;
        let frame_fence = create_fence(&vk, device, VK_FENCE_CREATE_SIGNALED_BIT)?;
        let acquire_fence = create_fence(&vk, device, 0)?;
        let mut renderer = Self {
            vk,
            instance,
            surface,
            physical,
            device,
            graphics_queue,
            present_queue,
            graphics_family,
            present_family,
            command_pool,
            command_buffer,
            frame_fence,
            acquire_fence,
            render_finished: Vec::new(),
            retired_semaphores: Vec::new(),
            swapchain: VkSwapchainKHR::default(),
            images: Vec::new(),
            target_size: (0, 0),
            recreate_pending: false,
        };
        renderer.recreate_swapchain(window)?;
        Ok(renderer)
    }

    fn render(&mut self, window: &FixtureWindow, color: (f32, f32, f32)) -> Result<()> {
        if self.recreate_pending || client_size(window) != self.target_size {
            self.recreate_swapchain(window)?;
        }
        // The previous frame's submit must be complete before the shared
        // command buffer is reset for this frame.
        if !self.wait_fence(self.frame_fence, "wait-frame-fence")? {
            eprintln!("vulkan: frame fence stalled; quitting");
            uga_api_fixtures::common::request_quit();
            return Ok(());
        }
        let mut index: u32 = 0;
        let code = unsafe {
            (self.vk.acquire_next_image_khr)(
                self.device,
                self.swapchain,
                ACQUIRE_TIMEOUT_NS,
                VkSemaphore::default(),
                self.acquire_fence,
                &raw mut index,
            )
        };
        if code == VK_TIMEOUT {
            // No free image yet (for example an occluded window whose
            // presents are not retiring). The fence is replaced instead of
            // relying on restore-on-failure semantics, and the frame is
            // skipped so the loop keeps pumping messages.
            self.recreate_acquire_fence()?;
            return Ok(());
        }
        if code == VK_ERROR_OUT_OF_DATE_KHR {
            // Destroying the acquire fence is legal whatever state the
            // loader left it in, so replace it together with the swapchain.
            self.recreate_acquire_fence()?;
            self.recreate_swapchain(window)?;
            return Ok(());
        }
        if code == VK_SUBOPTIMAL_KHR {
            self.recreate_pending = true;
        }
        check_result("acquire", code)?;
        // Retired only now: a frame is guaranteed to submit after this point.
        check_result("reset-frame-fence", unsafe {
            (self.vk.reset_fences)(self.device, 1, &raw const self.frame_fence)
        })?;
        // The image cannot be touched until its previous presentation has
        // retired; the acquire fence reports exactly that.
        if !self.wait_fence(self.acquire_fence, "wait-acquire-fence")? {
            eprintln!("vulkan: acquire fence stalled; quitting");
            uga_api_fixtures::common::request_quit();
            return Ok(());
        }
        check_result("reset-acquire-fence", unsafe {
            (self.vk.reset_fences)(self.device, 1, &raw const self.acquire_fence)
        })?;
        self.record_frame(index, color)?;
        let image_index = usize::try_from(index).unwrap_or(usize::MAX);
        let Some(render_finished) = self.render_finished.get(image_index) else {
            return Ok(());
        };
        let submit_info = VkSubmitInfo {
            s_type: VK_STRUCTURE_TYPE_SUBMIT_INFO,
            command_buffer_count: 1,
            p_command_buffers: &raw const self.command_buffer,
            signal_semaphore_count: 1,
            p_signal_semaphores: render_finished,
            ..VkSubmitInfo::default()
        };
        check_result("submit", unsafe {
            (self.vk.queue_submit)(
                self.graphics_queue,
                1,
                &raw const submit_info,
                self.frame_fence,
            )
        })?;
        let present_info = VkPresentInfoKHR {
            s_type: VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
            wait_semaphore_count: 1,
            p_wait_semaphores: render_finished,
            swapchain_count: 1,
            p_swapchains: &raw const self.swapchain,
            p_image_indices: &raw const index,
            ..VkPresentInfoKHR::default()
        };
        let code =
            unsafe { (self.vk.queue_present_khr)(self.present_queue, &raw const present_info) };
        if code == VK_ERROR_OUT_OF_DATE_KHR || code == VK_SUBOPTIMAL_KHR {
            self.recreate_pending = true;
        }
        check_result("present", code)
    }

    fn record_frame(&mut self, index: u32, color: (f32, f32, f32)) -> Result<()> {
        let Some(image) = self
            .images
            .get(usize::try_from(index).unwrap_or(usize::MAX))
        else {
            return Ok(());
        };
        let to_clear = VkImageMemoryBarrier {
            s_type: VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            src_access_mask: 0,
            dst_access_mask: VK_ACCESS_TRANSFER_WRITE_BIT,
            old_layout: VK_IMAGE_LAYOUT_UNDEFINED,
            new_layout: VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            src_queue_family_index: 0,
            dst_queue_family_index: 0,
            image: *image,
            subresource_range: full_color_range(),
            ..VkImageMemoryBarrier::default()
        };
        let to_present = VkImageMemoryBarrier {
            src_access_mask: VK_ACCESS_TRANSFER_WRITE_BIT,
            dst_access_mask: VK_ACCESS_MEMORY_READ_BIT,
            old_layout: VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            new_layout: VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
            ..to_clear
        };
        let clear = VkClearColorValue {
            float32: [color.0, color.1, color.2, 1.0],
        };
        check_result("reset-command-buffer", unsafe {
            (self.vk.reset_command_buffer)(self.command_buffer, 0)
        })?;
        let begin_info = VkCommandBufferBeginInfo {
            s_type: VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            ..VkCommandBufferBeginInfo::default()
        };
        check_result("begin-command-buffer", unsafe {
            (self.vk.begin_command_buffer)(self.command_buffer, &raw const begin_info)
        })?;
        unsafe {
            (self.vk.cmd_pipeline_barrier)(
                self.command_buffer,
                VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                VK_PIPELINE_STAGE_TRANSFER_BIT,
                0,
                0,
                std::ptr::null(),
                0,
                std::ptr::null(),
                1,
                &raw const to_clear,
            );
            (self.vk.cmd_clear_color_image)(
                self.command_buffer,
                *image,
                VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                &raw const clear,
                1,
                &full_color_range(),
            );
            (self.vk.cmd_pipeline_barrier)(
                self.command_buffer,
                VK_PIPELINE_STAGE_TRANSFER_BIT,
                VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                0,
                0,
                std::ptr::null(),
                0,
                std::ptr::null(),
                1,
                &raw const to_present,
            );
        }
        check_result("end-command-buffer", unsafe {
            (self.vk.end_command_buffer)(self.command_buffer)
        })
    }

    fn recreate_swapchain(&mut self, window: &FixtureWindow) -> Result<()> {
        let (width, height) = client_size(window);
        let mut caps = VkSurfaceCapabilitiesKHR::default();
        check_result("surface-capabilities", unsafe {
            (self.vk.get_physical_device_surface_capabilities_khr)(
                self.physical,
                self.surface,
                &raw mut caps,
            )
        })?;
        let extent = choose_extent(&caps, width, height);
        if extent.width == 0 || extent.height == 0 {
            // Minimized or hidden: keep presenting on the current swapchain
            // and retry the resize on a later frame.
            self.recreate_pending = true;
            return Ok(());
        }
        let (format, color_space) = choose_format(&self.vk, self.physical, self.surface)?;
        let present_mode = choose_present_mode(&self.vk, self.physical, self.surface)?;
        let mut image_count = caps.min_image_count.max(2);
        if caps.max_image_count > 0 {
            image_count = image_count.min(caps.max_image_count);
        }
        let composite_alpha_mask =
            if caps.supported_composite_alpha & VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR != 0 {
                VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR
            } else {
                // Lowest supported bit; the mask is non-empty for any surface.
                caps.supported_composite_alpha & !(caps.supported_composite_alpha - 1)
            };
        let composite_alpha: VkCompositeAlphaFlagBitsKHR =
            i32::try_from(composite_alpha_mask).unwrap_or(1);
        // A distinct present family requires concurrent sharing so the
        // graphics queue may record clears while the present queue presents.
        let (sharing_mode, families): (VkSharingMode, Vec<u32>) =
            if self.graphics_family == self.present_family {
                (VK_SHARING_MODE_EXCLUSIVE, Vec::new())
            } else {
                (
                    VK_SHARING_MODE_CONCURRENT,
                    vec![self.graphics_family, self.present_family],
                )
            };
        unsafe {
            let _ = (self.vk.device_wait_idle)(self.device);
            // Old present-wait semaphores may still be pending inside the
            // presentation engine; retire rather than destroy mid-run.
            self.retired_semaphores.append(&mut self.render_finished);
            if self.swapchain != VkSwapchainKHR::default() {
                (self.vk.destroy_swapchain_khr)(self.device, self.swapchain, std::ptr::null());
            }
        }
        let info = VkSwapchainCreateInfoKHR {
            s_type: VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
            surface: self.surface,
            min_image_count: image_count,
            image_format: format,
            image_color_space: color_space,
            image_extent: extent,
            image_array_layers: 1,
            image_usage: VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
            image_sharing_mode: sharing_mode,
            queue_family_index_count: u32::try_from(families.len()).unwrap_or(0),
            p_queue_family_indices: families.as_ptr(),
            pre_transform: caps.current_transform,
            composite_alpha,
            present_mode,
            clipped: VK_TRUE,
            ..VkSwapchainCreateInfoKHR::default()
        };
        let mut swapchain = VkSwapchainKHR::default();
        check_result("create-swapchain", unsafe {
            (self.vk.create_swapchain_khr)(
                self.device,
                &raw const info,
                std::ptr::null(),
                &raw mut swapchain,
            )
        })?;
        self.swapchain = swapchain;
        self.adopt_swapchain_images()?;
        self.target_size = (width, height);
        self.recreate_pending = false;
        Ok(())
    }

    /// Fetches the new swapchain's images and builds the index-matched
    /// present semaphores.
    fn adopt_swapchain_images(&mut self) -> Result<()> {
        let mut count: u32 = 0;
        check_result("swapchain-image-count", unsafe {
            (self.vk.get_swapchain_images_khr)(
                self.device,
                self.swapchain,
                &raw mut count,
                std::ptr::null_mut(),
            )
        })?;
        self.images = vec![VkImage::default(); usize::try_from(count).unwrap_or(0)];
        check_result("get-swapchain-images", unsafe {
            (self.vk.get_swapchain_images_khr)(
                self.device,
                self.swapchain,
                &raw mut count,
                self.images.as_mut_ptr(),
            )
        })?;
        self.render_finished = Vec::with_capacity(self.images.len());
        for _ in &self.images {
            self.render_finished
                .push(create_semaphore(&self.vk, self.device)?);
        }
        Ok(())
    }

    fn recreate_acquire_fence(&mut self) -> Result<()> {
        unsafe {
            (self.vk.destroy_fence)(self.device, self.acquire_fence, std::ptr::null());
        }
        self.acquire_fence = create_fence(&self.vk, self.device, 0)?;
        Ok(())
    }

    /// Waits for `fence` in bounded slices so stalls stay visible and the
    /// run loop stays responsive.
    ///
    /// # Errors
    ///
    /// Propagates unexpected Vulkan failures; `Ok(false)` reports that the
    /// fence did not signal within the slice budget.
    fn wait_fence(&self, fence: VkFence, stage: &str) -> Result<bool> {
        for _ in 0..FENCE_WAIT_SLICES {
            let code = unsafe {
                (self.vk.wait_for_fences)(
                    self.device,
                    1,
                    &raw const fence,
                    VK_TRUE,
                    FENCE_WAIT_SLICE_NS,
                )
            };
            match code {
                VK_TIMEOUT => {}
                VK_SUCCESS => return Ok(true),
                code => {
                    check_result(stage, code)?;
                    return Ok(false);
                }
            }
        }
        Ok(false)
    }
}

impl Drop for VulkanRenderer {
    fn drop(&mut self) {
        unsafe {
            let _ = (self.vk.device_wait_idle)(self.device);
            for semaphore in self
                .render_finished
                .iter()
                .chain(self.retired_semaphores.iter())
            {
                (self.vk.destroy_semaphore)(self.device, *semaphore, std::ptr::null());
            }
            if self.swapchain != VkSwapchainKHR::default() {
                (self.vk.destroy_swapchain_khr)(self.device, self.swapchain, std::ptr::null());
            }
            (self.vk.destroy_fence)(self.device, self.acquire_fence, std::ptr::null());
            (self.vk.destroy_fence)(self.device, self.frame_fence, std::ptr::null());
            (self.vk.destroy_command_pool)(self.device, self.command_pool, std::ptr::null());
            (self.vk.destroy_device)(self.device, std::ptr::null());
            (self.vk.destroy_surface_khr)(self.instance, self.surface, std::ptr::null());
            (self.vk.destroy_instance)(self.instance, std::ptr::null());
        }
    }
}

fn create_surface(vk: &Vk, instance: VkInstance, window: &FixtureWindow) -> Result<VkSurfaceKHR> {
    let hinstance = unsafe { GetModuleHandleW(None)? };
    let info = VkWin32SurfaceCreateInfoKHR {
        s_type: VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR,
        hinstance: hinstance.0,
        hwnd: window.handle.0,
        ..VkWin32SurfaceCreateInfoKHR::default()
    };
    let mut surface = VkSurfaceKHR::default();
    check_result("create-win32-surface", unsafe {
        (vk.create_win32_surface_khr)(
            instance,
            &raw const info,
            std::ptr::null(),
            &raw mut surface,
        )
    })?;
    Ok(surface)
}

/// Picks the first device exposing both graphics and present support,
/// preferring a single queue family that serves both roles.
fn pick_device(
    vk: &Vk,
    instance: VkInstance,
    surface: VkSurfaceKHR,
) -> Result<(VkPhysicalDevice, u32, u32)> {
    let mut count: u32 = 0;
    check_result("enumerate-physical-devices", unsafe {
        (vk.enumerate_physical_devices)(instance, &raw mut count, std::ptr::null_mut())
    })?;
    let mut devices = vec![VkPhysicalDevice::default(); usize::try_from(count).unwrap_or(0)];
    check_result("enumerate-physical-devices", unsafe {
        (vk.enumerate_physical_devices)(instance, &raw mut count, devices.as_mut_ptr())
    })?;
    for device in devices {
        let mut family_count: u32 = 0;
        unsafe {
            (vk.get_physical_device_queue_family_properties)(
                device,
                &raw mut family_count,
                std::ptr::null_mut(),
            );
        }
        let mut families =
            vec![VkQueueFamilyProperties::default(); usize::try_from(family_count).unwrap_or(0)];
        unsafe {
            (vk.get_physical_device_queue_family_properties)(
                device,
                &raw mut family_count,
                families.as_mut_ptr(),
            );
        }
        let mut combined = None;
        let mut graphics = None;
        let mut present = None;
        for (index, family) in families.iter().enumerate() {
            let family_index = u32::try_from(index).unwrap_or(u32::MAX);
            let is_graphics = family.queue_flags & VK_QUEUE_GRAPHICS_BIT != 0;
            let mut supported: VkBool32 = 0;
            let code = unsafe {
                (vk.get_physical_device_surface_support_khr)(
                    device,
                    family_index,
                    surface,
                    &raw mut supported,
                )
            };
            let is_present = code == VK_SUCCESS && supported == VK_TRUE;
            if is_graphics && is_present {
                combined.get_or_insert(family_index);
            }
            if is_graphics {
                graphics.get_or_insert(family_index);
            }
            if is_present {
                present.get_or_insert(family_index);
            }
        }
        if let Some(family) = combined {
            return Ok((device, family, family));
        }
        if let (Some(graphics), Some(present)) = (graphics, present) {
            return Ok((device, graphics, present));
        }
    }
    eprintln!("vulkan pick-device: no device with graphics and present queues");
    Err(Error::from_hresult(HRESULT(-1)))
}

fn create_device(
    vk: &Vk,
    physical: VkPhysicalDevice,
    graphics_family: u32,
    present_family: u32,
) -> Result<VkDevice> {
    let priority: [f32; 1] = [1.0];
    let mut queue_infos = vec![queue_create_info(graphics_family, &priority)];
    if present_family != graphics_family {
        queue_infos.push(queue_create_info(present_family, &priority));
    }
    let extensions = [c_name(b"VK_KHR_swapchain\0")];
    let info = VkDeviceCreateInfo {
        s_type: VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        queue_create_info_count: u32::try_from(queue_infos.len()).unwrap_or(0),
        p_queue_create_infos: queue_infos.as_ptr(),
        enabled_extension_count: u32::try_from(extensions.len()).unwrap_or(0),
        pp_enabled_extension_names: extensions.as_ptr(),
        ..VkDeviceCreateInfo::default()
    };
    let mut device = VkDevice::default();
    check_result("create-device", unsafe {
        (vk.create_device)(physical, &raw const info, std::ptr::null(), &raw mut device)
    })?;
    Ok(device)
}

fn queue_create_info(family: u32, priorities: &[f32; 1]) -> VkDeviceQueueCreateInfo {
    VkDeviceQueueCreateInfo {
        s_type: VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        queue_family_index: family,
        queue_count: 1,
        p_queue_priorities: priorities.as_ptr(),
        ..VkDeviceQueueCreateInfo::default()
    }
}

fn create_fence(vk: &Vk, device: VkDevice, flags: VkFenceCreateFlags) -> Result<VkFence> {
    let info = VkFenceCreateInfo {
        s_type: VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
        flags,
        ..VkFenceCreateInfo::default()
    };
    let mut fence = VkFence::default();
    check_result("create-fence", unsafe {
        (vk.create_fence)(device, &raw const info, std::ptr::null(), &raw mut fence)
    })?;
    Ok(fence)
}

fn create_semaphore(vk: &Vk, device: VkDevice) -> Result<VkSemaphore> {
    let info = VkSemaphoreCreateInfo {
        s_type: VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
        ..VkSemaphoreCreateInfo::default()
    };
    let mut semaphore = VkSemaphore::default();
    check_result("create-semaphore", unsafe {
        (vk.create_semaphore)(
            device,
            &raw const info,
            std::ptr::null(),
            &raw mut semaphore,
        )
    })?;
    Ok(semaphore)
}

fn full_color_range() -> VkImageSubresourceRange {
    VkImageSubresourceRange {
        aspect_mask: VK_IMAGE_ASPECT_COLOR_BIT,
        base_mip_level: 0,
        level_count: 1,
        base_array_layer: 0,
        layer_count: 1,
    }
}

fn choose_extent(caps: &VkSurfaceCapabilitiesKHR, width: i32, height: i32) -> VkExtent2D {
    const UNDEFINED: u32 = 0xFFFF_FFFF;
    if caps.current_extent.width != UNDEFINED && caps.current_extent.height != UNDEFINED {
        return caps.current_extent;
    }
    let width = u32::try_from(width.max(0))
        .unwrap_or(0)
        .clamp(caps.min_image_extent.width, caps.max_image_extent.width);
    let height = u32::try_from(height.max(0))
        .unwrap_or(0)
        .clamp(caps.min_image_extent.height, caps.max_image_extent.height);
    VkExtent2D { width, height }
}

fn choose_format(
    vk: &Vk,
    physical: VkPhysicalDevice,
    surface: VkSurfaceKHR,
) -> Result<(VkFormat, VkColorSpaceKHR)> {
    let mut count: u32 = 0;
    check_result("surface-formats", unsafe {
        (vk.get_physical_device_surface_formats_khr)(
            physical,
            surface,
            &raw mut count,
            std::ptr::null_mut(),
        )
    })?;
    if count == 0 {
        eprintln!("vulkan choose-format: surface reports no formats");
        return Err(Error::from_hresult(HRESULT(-1)));
    }
    let mut formats = vec![VkSurfaceFormatKHR::default(); usize::try_from(count).unwrap_or(0)];
    check_result("surface-formats", unsafe {
        (vk.get_physical_device_surface_formats_khr)(
            physical,
            surface,
            &raw mut count,
            formats.as_mut_ptr(),
        )
    })?;
    let chosen = formats
        .iter()
        .find(|format| format.format == VK_FORMAT_B8G8R8A8_UNORM)
        .or_else(|| {
            formats
                .iter()
                .find(|format| format.format == VK_FORMAT_B8G8R8A8_SRGB)
        })
        .unwrap_or(&formats[0]);
    if chosen.format < 0 {
        // The driver has no preference; pick the fixture default.
        return Ok((VK_FORMAT_B8G8R8A8_UNORM, 0));
    }
    Ok((chosen.format, chosen.color_space))
}

fn choose_present_mode(
    vk: &Vk,
    physical: VkPhysicalDevice,
    surface: VkSurfaceKHR,
) -> Result<VkPresentModeKHR> {
    let mut count: u32 = 0;
    check_result("surface-present-modes", unsafe {
        (vk.get_physical_device_surface_present_modes_khr)(
            physical,
            surface,
            &raw mut count,
            std::ptr::null_mut(),
        )
    })?;
    let mut modes = vec![0 as VkPresentModeKHR; usize::try_from(count).unwrap_or(0)];
    check_result("surface-present-modes", unsafe {
        (vk.get_physical_device_surface_present_modes_khr)(
            physical,
            surface,
            &raw mut count,
            modes.as_mut_ptr(),
        )
    })?;
    if modes.contains(&VK_PRESENT_MODE_FIFO_KHR) {
        Ok(VK_PRESENT_MODE_FIFO_KHR)
    } else {
        eprintln!("vulkan choose-present-mode: FIFO unsupported ({modes:?})");
        modes
            .first()
            .copied()
            .ok_or_else(|| Error::from_hresult(HRESULT(-1)))
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
    let window = FixtureWindow::create("UGA API Fixture Vulkan")?;
    let mut renderer = VulkanRenderer::new(&window)?;
    let start = Instant::now();
    let limit = parse_duration_seconds(&args);
    run_loop(
        &window,
        &mut || {
            let color = cycle_color(start.elapsed());
            if let Err(error) = renderer.render(&window, color) {
                eprintln!("vulkan render failed: {error}");
                uga_api_fixtures::common::request_quit();
            }
        },
        limit,
    );
    Ok(())
}
