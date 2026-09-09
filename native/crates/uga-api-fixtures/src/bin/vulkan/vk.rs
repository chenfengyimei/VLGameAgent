//! Raw Vulkan 1.0 ABI bindings for the capture fixture.
//!
//! Everything below mirrors the Khronos `vulkan_core.h` layout and values
//! (verified against the 1.1.130 SDK header): field order, enum numbers, and
//! bit masks are load-bearing, so keep this file aligned with the header
//! instead of reshaping names. Only the entry points a clear-cycle renderer
//! needs are bound, resolved through `vkGetInstanceProcAddr` on a live
//! instance (the loader dispatches device-level core functions too).
//!
//! The module deliberately avoids a Vulkan wrapper crate: the fixture has to
//! build with the pinned, offline, no-new-dependencies toolchain policy.

// Vulkan ABI names intentionally repeat the `Vk` module prefix so every
// declaration can be diffed line-by-line against vulkan_core.h.
#![allow(clippy::module_name_repetitions)]

use core::ffi::{c_char, c_void};

use windows::Win32::Foundation::HMODULE;
use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};
use windows::core::{Error, HRESULT, Result, s, w};

// ---------------------------------------------------------------------------
// Handle types: opaque pointers, cheap to copy, defaulting to null.
// ---------------------------------------------------------------------------

macro_rules! vk_handle {
    ($($name:ident),* $(,)?) => {
        $(
            #[repr(transparent)]
            #[derive(Clone, Copy, Default, PartialEq, Eq)]
            pub struct $name(*mut c_void);
        )*
    };
}

vk_handle!(
    VkInstance,
    VkPhysicalDevice,
    VkDevice,
    VkQueue,
    VkCommandPool,
    VkCommandBuffer,
    VkSemaphore,
    VkFence,
    VkSurfaceKHR,
    VkSwapchainKHR,
    VkImage,
);

// ---------------------------------------------------------------------------
// Scalar aliases (C enum and flag types).
// ---------------------------------------------------------------------------

pub type VkBool32 = u32;
pub type VkStructureType = i32;
pub type VkResult = i32;
pub type VkFormat = i32;
pub type VkColorSpaceKHR = i32;
pub type VkImageLayout = i32;
pub type VkPresentModeKHR = i32;
pub type VkSharingMode = i32;
pub type VkCommandBufferLevel = i32;
pub type VkSurfaceTransformFlagBitsKHR = i32;
pub type VkCompositeAlphaFlagBitsKHR = i32;

pub type VkInstanceCreateFlags = u32;
pub type VkDeviceQueueCreateFlags = u32;
pub type VkDeviceCreateFlags = u32;
pub type VkQueueFlags = u32;
pub type VkImageUsageFlags = u32;
pub type VkImageAspectFlags = u32;
pub type VkPipelineStageFlags = u32;
pub type VkAccessFlags = u32;
pub type VkCommandPoolCreateFlags = u32;
pub type VkFenceCreateFlags = u32;
pub type VkSemaphoreCreateFlags = u32;
pub type VkCommandBufferUsageFlags = u32;
pub type VkCommandBufferResetFlags = u32;
pub type VkDependencyFlags = u32;
pub type VkSwapchainCreateFlagsKHR = u32;
pub type VkSurfaceTransformFlagsKHR = u32;
pub type VkCompositeAlphaFlagsKHR = u32;

// ---------------------------------------------------------------------------
// Constants (values from vulkan_core.h; see file docs).
// ---------------------------------------------------------------------------

pub const VK_TRUE: VkBool32 = 1;
pub const VK_API_VERSION_1_0: u32 = 0x0040_0000; // VK_MAKE_VERSION(1, 0, 0)

// VkStructureType
pub const VK_STRUCTURE_TYPE_APPLICATION_INFO: VkStructureType = 0;
pub const VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO: VkStructureType = 1;
pub const VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO: VkStructureType = 2;
pub const VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO: VkStructureType = 3;
pub const VK_STRUCTURE_TYPE_SUBMIT_INFO: VkStructureType = 4;
pub const VK_STRUCTURE_TYPE_FENCE_CREATE_INFO: VkStructureType = 8;
pub const VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO: VkStructureType = 9;
pub const VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO: VkStructureType = 39;
pub const VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO: VkStructureType = 40;
pub const VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO: VkStructureType = 42;
pub const VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER: VkStructureType = 45;
pub const VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR: VkStructureType = 1_000_001_000;
pub const VK_STRUCTURE_TYPE_PRESENT_INFO_KHR: VkStructureType = 1_000_001_001;
pub const VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR: VkStructureType = 1_000_009_000;

// VkResult
pub const VK_SUCCESS: VkResult = 0;
pub const VK_TIMEOUT: VkResult = 2;
pub const VK_SUBOPTIMAL_KHR: VkResult = 1_000_001_003;
pub const VK_ERROR_OUT_OF_DATE_KHR: VkResult = -1_000_001_004;

// VkImageLayout
pub const VK_IMAGE_LAYOUT_UNDEFINED: VkImageLayout = 0;
pub const VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL: VkImageLayout = 7;
pub const VK_IMAGE_LAYOUT_PRESENT_SRC_KHR: VkImageLayout = 1_000_001_002;

// Flags
pub const VK_QUEUE_GRAPHICS_BIT: VkQueueFlags = 0x0000_0001;
pub const VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT: VkImageUsageFlags = 0x0000_0010;
pub const VK_IMAGE_ASPECT_COLOR_BIT: VkImageAspectFlags = 0x0000_0001;
pub const VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT: VkPipelineStageFlags = 0x0000_0001;
pub const VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT: VkPipelineStageFlags = 0x0000_0400;
pub const VK_PIPELINE_STAGE_TRANSFER_BIT: VkPipelineStageFlags = 0x0000_1000;
pub const VK_ACCESS_TRANSFER_WRITE_BIT: VkAccessFlags = 0x0000_1000;
pub const VK_ACCESS_MEMORY_READ_BIT: VkAccessFlags = 0x0000_8000;
pub const VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT: VkCommandPoolCreateFlags = 0x0000_0002;
pub const VK_FENCE_CREATE_SIGNALED_BIT: VkFenceCreateFlags = 0x0000_0001;
pub const VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR: u32 = 0x0000_0001;

// Simple enums
pub const VK_FORMAT_B8G8R8A8_UNORM: VkFormat = 44;
pub const VK_FORMAT_B8G8R8A8_SRGB: VkFormat = 50;
pub const VK_PRESENT_MODE_FIFO_KHR: VkPresentModeKHR = 2;
pub const VK_SHARING_MODE_EXCLUSIVE: VkSharingMode = 0;
pub const VK_SHARING_MODE_CONCURRENT: VkSharingMode = 1;
pub const VK_COMMAND_BUFFER_LEVEL_PRIMARY: VkCommandBufferLevel = 0;

// ---------------------------------------------------------------------------
// Structures (field order and types mirror vulkan_core.h exactly).
// ---------------------------------------------------------------------------

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkExtent2D {
    pub width: u32,
    pub height: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkExtent3D {
    pub width: u32,
    pub height: u32,
    pub depth: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkImageSubresourceRange {
    pub aspect_mask: VkImageAspectFlags,
    pub base_mip_level: u32,
    pub level_count: u32,
    pub base_array_layer: u32,
    pub layer_count: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkApplicationInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub p_application_name: *const c_char,
    pub application_version: u32,
    pub p_engine_name: *const c_char,
    pub engine_version: u32,
    pub api_version: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkInstanceCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkInstanceCreateFlags,
    pub p_application_info: *const VkApplicationInfo,
    pub enabled_layer_count: u32,
    pub pp_enabled_layer_names: *const *const c_char,
    pub enabled_extension_count: u32,
    pub pp_enabled_extension_names: *const *const c_char,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkWin32SurfaceCreateInfoKHR {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: u32,
    pub hinstance: *mut c_void,
    pub hwnd: *mut c_void,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkQueueFamilyProperties {
    pub queue_flags: VkQueueFlags,
    pub queue_count: u32,
    pub timestamp_valid_bits: u32,
    pub min_image_transfer_granularity: VkExtent3D,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkSurfaceCapabilitiesKHR {
    pub min_image_count: u32,
    pub max_image_count: u32,
    pub current_extent: VkExtent2D,
    pub min_image_extent: VkExtent2D,
    pub max_image_extent: VkExtent2D,
    pub max_image_array_layers: u32,
    pub supported_transforms: VkSurfaceTransformFlagsKHR,
    pub current_transform: VkSurfaceTransformFlagBitsKHR,
    pub supported_composite_alpha: VkCompositeAlphaFlagsKHR,
    pub supported_usage_flags: VkImageUsageFlags,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkSurfaceFormatKHR {
    pub format: VkFormat,
    pub color_space: VkColorSpaceKHR,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkDeviceQueueCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkDeviceQueueCreateFlags,
    pub queue_family_index: u32,
    pub queue_count: u32,
    pub p_queue_priorities: *const f32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkDeviceCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkDeviceCreateFlags,
    pub queue_create_info_count: u32,
    pub p_queue_create_infos: *const VkDeviceQueueCreateInfo,
    pub enabled_layer_count: u32,
    pub pp_enabled_layer_names: *const *const c_char,
    pub enabled_extension_count: u32,
    pub pp_enabled_extension_names: *const *const c_char,
    pub p_enabled_features: *const c_void,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkCommandPoolCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkCommandPoolCreateFlags,
    pub queue_family_index: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkCommandBufferAllocateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub command_pool: VkCommandPool,
    pub level: VkCommandBufferLevel,
    pub command_buffer_count: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkCommandBufferBeginInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkCommandBufferUsageFlags,
    pub p_inheritance_info: *const c_void,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkSemaphoreCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkSemaphoreCreateFlags,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkFenceCreateInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkFenceCreateFlags,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkImageMemoryBarrier {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub src_access_mask: VkAccessFlags,
    pub dst_access_mask: VkAccessFlags,
    pub old_layout: VkImageLayout,
    pub new_layout: VkImageLayout,
    pub src_queue_family_index: u32,
    pub dst_queue_family_index: u32,
    pub image: VkImage,
    pub subresource_range: VkImageSubresourceRange,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkSubmitInfo {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub wait_semaphore_count: u32,
    pub p_wait_semaphores: *const VkSemaphore,
    pub p_wait_dst_stage_mask: *const VkPipelineStageFlags,
    pub command_buffer_count: u32,
    pub p_command_buffers: *const VkCommandBuffer,
    pub signal_semaphore_count: u32,
    pub p_signal_semaphores: *const VkSemaphore,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkSwapchainCreateInfoKHR {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub flags: VkSwapchainCreateFlagsKHR,
    pub surface: VkSurfaceKHR,
    pub min_image_count: u32,
    pub image_format: VkFormat,
    pub image_color_space: VkColorSpaceKHR,
    pub image_extent: VkExtent2D,
    pub image_array_layers: u32,
    pub image_usage: VkImageUsageFlags,
    pub image_sharing_mode: VkSharingMode,
    pub queue_family_index_count: u32,
    pub p_queue_family_indices: *const u32,
    pub pre_transform: VkSurfaceTransformFlagBitsKHR,
    pub composite_alpha: VkCompositeAlphaFlagBitsKHR,
    pub present_mode: VkPresentModeKHR,
    pub clipped: VkBool32,
    pub old_swapchain: VkSwapchainKHR,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct VkPresentInfoKHR {
    pub s_type: VkStructureType,
    pub p_next: *const c_void,
    pub wait_semaphore_count: u32,
    pub p_wait_semaphores: *const VkSemaphore,
    pub swapchain_count: u32,
    pub p_swapchains: *const VkSwapchainKHR,
    pub p_image_indices: *const u32,
    pub p_results: *mut VkResult,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub union VkClearColorValue {
    pub float32: [f32; 4],
    pub int32: [i32; 4],
    pub uint32: [u32; 4],
}

// ---------------------------------------------------------------------------
// Function pointer types.
// ---------------------------------------------------------------------------

pub type PfnVkGetInstanceProcAddr =
    unsafe extern "system" fn(VkInstance, *const c_char) -> *mut c_void;
pub type PfnVkCreateInstance = unsafe extern "system" fn(
    *const VkInstanceCreateInfo,
    *const c_void,
    *mut VkInstance,
) -> VkResult;
pub type PfnVkDestroyInstance = unsafe extern "system" fn(VkInstance, *const c_void);
pub type PfnVkEnumeratePhysicalDevices =
    unsafe extern "system" fn(VkInstance, *mut u32, *mut VkPhysicalDevice) -> VkResult;
pub type PfnVkGetPhysicalDeviceQueueFamilyProperties =
    unsafe extern "system" fn(VkPhysicalDevice, *mut u32, *mut VkQueueFamilyProperties);
pub type PfnVkCreateWin32SurfaceKHR = unsafe extern "system" fn(
    VkInstance,
    *const VkWin32SurfaceCreateInfoKHR,
    *const c_void,
    *mut VkSurfaceKHR,
) -> VkResult;
pub type PfnVkDestroySurfaceKHR =
    unsafe extern "system" fn(VkInstance, VkSurfaceKHR, *const c_void);
pub type PfnVkGetPhysicalDeviceSurfaceSupportKHR =
    unsafe extern "system" fn(VkPhysicalDevice, u32, VkSurfaceKHR, *mut VkBool32) -> VkResult;
pub type PfnVkGetPhysicalDeviceSurfaceCapabilitiesKHR = unsafe extern "system" fn(
    VkPhysicalDevice,
    VkSurfaceKHR,
    *mut VkSurfaceCapabilitiesKHR,
) -> VkResult;
pub type PfnVkGetPhysicalDeviceSurfaceFormatsKHR = unsafe extern "system" fn(
    VkPhysicalDevice,
    VkSurfaceKHR,
    *mut u32,
    *mut VkSurfaceFormatKHR,
) -> VkResult;
pub type PfnVkGetPhysicalDeviceSurfacePresentModesKHR = unsafe extern "system" fn(
    VkPhysicalDevice,
    VkSurfaceKHR,
    *mut u32,
    *mut VkPresentModeKHR,
) -> VkResult;
pub type PfnVkCreateDevice = unsafe extern "system" fn(
    VkPhysicalDevice,
    *const VkDeviceCreateInfo,
    *const c_void,
    *mut VkDevice,
) -> VkResult;
pub type PfnVkDestroyDevice = unsafe extern "system" fn(VkDevice, *const c_void);
pub type PfnVkGetDeviceQueue = unsafe extern "system" fn(VkDevice, u32, u32, *mut VkQueue);
pub type PfnVkCreateCommandPool = unsafe extern "system" fn(
    VkDevice,
    *const VkCommandPoolCreateInfo,
    *const c_void,
    *mut VkCommandPool,
) -> VkResult;
pub type PfnVkDestroyCommandPool =
    unsafe extern "system" fn(VkDevice, VkCommandPool, *const c_void);
pub type PfnVkAllocateCommandBuffers = unsafe extern "system" fn(
    VkDevice,
    *const VkCommandBufferAllocateInfo,
    *mut VkCommandBuffer,
) -> VkResult;
pub type PfnVkCreateSemaphore = unsafe extern "system" fn(
    VkDevice,
    *const VkSemaphoreCreateInfo,
    *const c_void,
    *mut VkSemaphore,
) -> VkResult;
pub type PfnVkDestroySemaphore = unsafe extern "system" fn(VkDevice, VkSemaphore, *const c_void);
pub type PfnVkCreateFence = unsafe extern "system" fn(
    VkDevice,
    *const VkFenceCreateInfo,
    *const c_void,
    *mut VkFence,
) -> VkResult;
pub type PfnVkDestroyFence = unsafe extern "system" fn(VkDevice, VkFence, *const c_void);
pub type PfnVkWaitForFences =
    unsafe extern "system" fn(VkDevice, u32, *const VkFence, VkBool32, u64) -> VkResult;
pub type PfnVkResetFences = unsafe extern "system" fn(VkDevice, u32, *const VkFence) -> VkResult;
pub type PfnVkDeviceWaitIdle = unsafe extern "system" fn(VkDevice) -> VkResult;
pub type PfnVkCreateSwapchainKHR = unsafe extern "system" fn(
    VkDevice,
    *const VkSwapchainCreateInfoKHR,
    *const c_void,
    *mut VkSwapchainKHR,
) -> VkResult;
pub type PfnVkDestroySwapchainKHR =
    unsafe extern "system" fn(VkDevice, VkSwapchainKHR, *const c_void);
pub type PfnVkGetSwapchainImagesKHR =
    unsafe extern "system" fn(VkDevice, VkSwapchainKHR, *mut u32, *mut VkImage) -> VkResult;
pub type PfnVkAcquireNextImageKHR = unsafe extern "system" fn(
    VkDevice,
    VkSwapchainKHR,
    u64,
    VkSemaphore,
    VkFence,
    *mut u32,
) -> VkResult;
pub type PfnVkQueuePresentKHR =
    unsafe extern "system" fn(VkQueue, *const VkPresentInfoKHR) -> VkResult;
pub type PfnVkBeginCommandBuffer =
    unsafe extern "system" fn(VkCommandBuffer, *const VkCommandBufferBeginInfo) -> VkResult;
pub type PfnVkEndCommandBuffer = unsafe extern "system" fn(VkCommandBuffer) -> VkResult;
pub type PfnVkResetCommandBuffer =
    unsafe extern "system" fn(VkCommandBuffer, VkCommandBufferResetFlags) -> VkResult;
pub type PfnVkCmdPipelineBarrier = unsafe extern "system" fn(
    VkCommandBuffer,
    VkPipelineStageFlags,
    VkPipelineStageFlags,
    VkDependencyFlags,
    u32,
    *const c_void,
    u32,
    *const c_void,
    u32,
    *const VkImageMemoryBarrier,
);
pub type PfnVkCmdClearColorImage = unsafe extern "system" fn(
    VkCommandBuffer,
    VkImage,
    VkImageLayout,
    *const VkClearColorValue,
    u32,
    *const VkImageSubresourceRange,
);
pub type PfnVkQueueSubmit =
    unsafe extern "system" fn(VkQueue, u32, *const VkSubmitInfo, VkFence) -> VkResult;

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

/// `*const c_char` from a NUL-terminated ASCII byte string like `b"name\0"`.
#[must_use]
pub fn c_name(name: &'static [u8]) -> *const c_char {
    name.as_ptr().cast()
}

/// Maps a negative `VkResult` to an error after logging the stage and code.
pub fn check_result(stage: &str, code: VkResult) -> Result<()> {
    if code >= VK_SUCCESS {
        Ok(())
    } else {
        eprintln!("vulkan {stage}: VkResult {code}");
        Err(Error::from_hresult(HRESULT(-1)))
    }
}

/// Resolves `name` (appending the NUL) to a function pointer of type `$ty`.
macro_rules! vkproc {
    ($entry:expr, $instance:expr, $name:literal, $ty:ty) => {{
        let address = unsafe {
            ($entry.get_instance_proc_addr)($instance, c_name(concat!($name, "\0").as_bytes()))
        };
        if address.is_null() {
            return Err(Error::from_hresult(HRESULT(-1)));
        }
        let func: $ty = unsafe { std::mem::transmute_copy(&address) };
        func
    }};
}

// ---------------------------------------------------------------------------
// Loader entry point and instance dispatch table.
// ---------------------------------------------------------------------------

/// The process-global `vulkan-1.dll` loader state.
pub struct VkEntry {
    _library: HMODULE,
    get_instance_proc_addr: PfnVkGetInstanceProcAddr,
}

impl VkEntry {
    /// Loads `vulkan-1.dll` and resolves `vkGetInstanceProcAddr`.
    ///
    /// The library is intentionally never freed: the fixture holds it for the
    /// process lifetime, matching implicit import-library linking.
    ///
    /// # Errors
    ///
    /// Fails when `vulkan-1.dll` is missing or does not export
    /// `vkGetInstanceProcAddr`.
    pub fn load() -> Result<Self> {
        unsafe {
            let library = LoadLibraryW(w!("vulkan-1.dll"))?;
            let Some(proc) = GetProcAddress(library, s!("vkGetInstanceProcAddr")) else {
                return Err(Error::from_hresult(HRESULT(-1)));
            };
            let get_instance_proc_addr: PfnVkGetInstanceProcAddr = std::mem::transmute_copy(&proc);
            Ok(Self {
                _library: library,
                get_instance_proc_addr,
            })
        }
    }

    /// Creates the fixture instance enabling the given extensions.
    ///
    /// # Errors
    ///
    /// Fails when instance creation is rejected (no ICD, unsupported
    /// extension).
    pub fn create_instance(&self, extensions: &[*const c_char]) -> Result<VkInstance> {
        let app_info = VkApplicationInfo {
            s_type: VK_STRUCTURE_TYPE_APPLICATION_INFO,
            p_application_name: c_name(b"UGA API Fixture Vulkan\0"),
            application_version: 1,
            p_engine_name: c_name(b"uga-api-fixtures\0"),
            engine_version: 1,
            api_version: VK_API_VERSION_1_0,
            ..VkApplicationInfo::default()
        };
        let info = VkInstanceCreateInfo {
            s_type: VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
            p_application_info: &raw const app_info,
            enabled_extension_count: u32::try_from(extensions.len()).unwrap_or(0),
            pp_enabled_extension_names: extensions.as_ptr(),
            ..VkInstanceCreateInfo::default()
        };
        let create = vkproc!(
            self,
            VkInstance::default(),
            "vkCreateInstance",
            PfnVkCreateInstance
        );
        let mut instance = VkInstance::default();
        let code = unsafe { create(&raw const info, std::ptr::null(), &raw mut instance) };
        check_result("create-instance", code)?;
        Ok(instance)
    }
}

/// Every Vulkan entry point the fixture calls, resolved once per instance.
pub struct Vk {
    pub destroy_instance: PfnVkDestroyInstance,
    pub enumerate_physical_devices: PfnVkEnumeratePhysicalDevices,
    pub get_physical_device_queue_family_properties: PfnVkGetPhysicalDeviceQueueFamilyProperties,
    pub create_win32_surface_khr: PfnVkCreateWin32SurfaceKHR,
    pub destroy_surface_khr: PfnVkDestroySurfaceKHR,
    pub get_physical_device_surface_support_khr: PfnVkGetPhysicalDeviceSurfaceSupportKHR,
    pub get_physical_device_surface_capabilities_khr: PfnVkGetPhysicalDeviceSurfaceCapabilitiesKHR,
    pub get_physical_device_surface_formats_khr: PfnVkGetPhysicalDeviceSurfaceFormatsKHR,
    pub get_physical_device_surface_present_modes_khr: PfnVkGetPhysicalDeviceSurfacePresentModesKHR,
    pub create_device: PfnVkCreateDevice,
    pub destroy_device: PfnVkDestroyDevice,
    pub get_device_queue: PfnVkGetDeviceQueue,
    pub create_command_pool: PfnVkCreateCommandPool,
    pub destroy_command_pool: PfnVkDestroyCommandPool,
    pub allocate_command_buffers: PfnVkAllocateCommandBuffers,
    pub create_semaphore: PfnVkCreateSemaphore,
    pub destroy_semaphore: PfnVkDestroySemaphore,
    pub create_fence: PfnVkCreateFence,
    pub destroy_fence: PfnVkDestroyFence,
    pub wait_for_fences: PfnVkWaitForFences,
    pub reset_fences: PfnVkResetFences,
    pub device_wait_idle: PfnVkDeviceWaitIdle,
    pub create_swapchain_khr: PfnVkCreateSwapchainKHR,
    pub destroy_swapchain_khr: PfnVkDestroySwapchainKHR,
    pub get_swapchain_images_khr: PfnVkGetSwapchainImagesKHR,
    pub acquire_next_image_khr: PfnVkAcquireNextImageKHR,
    pub queue_present_khr: PfnVkQueuePresentKHR,
    pub begin_command_buffer: PfnVkBeginCommandBuffer,
    pub end_command_buffer: PfnVkEndCommandBuffer,
    pub reset_command_buffer: PfnVkResetCommandBuffer,
    pub cmd_pipeline_barrier: PfnVkCmdPipelineBarrier,
    pub cmd_clear_color_image: PfnVkCmdClearColorImage,
    pub queue_submit: PfnVkQueueSubmit,
}

impl Vk {
    /// Resolves the dispatch table through `vkGetInstanceProcAddr`.
    ///
    /// # Safety
    ///
    /// `instance` must be a live instance created by the same `VkEntry`
    /// loader, and every call made through the returned table must happen
    /// before the instance is destroyed.
    // The entry-point table is a flat resolution list; splitting it would
    // obscure the one-name-per-proc mapping.
    #[allow(clippy::too_many_lines)]
    pub unsafe fn load(entry: &VkEntry, instance: VkInstance) -> Result<Self> {
        Ok(Self {
            destroy_instance: vkproc!(entry, instance, "vkDestroyInstance", PfnVkDestroyInstance),
            enumerate_physical_devices: vkproc!(
                entry,
                instance,
                "vkEnumeratePhysicalDevices",
                PfnVkEnumeratePhysicalDevices
            ),
            get_physical_device_queue_family_properties: vkproc!(
                entry,
                instance,
                "vkGetPhysicalDeviceQueueFamilyProperties",
                PfnVkGetPhysicalDeviceQueueFamilyProperties
            ),
            create_win32_surface_khr: vkproc!(
                entry,
                instance,
                "vkCreateWin32SurfaceKHR",
                PfnVkCreateWin32SurfaceKHR
            ),
            destroy_surface_khr: vkproc!(
                entry,
                instance,
                "vkDestroySurfaceKHR",
                PfnVkDestroySurfaceKHR
            ),
            get_physical_device_surface_support_khr: vkproc!(
                entry,
                instance,
                "vkGetPhysicalDeviceSurfaceSupportKHR",
                PfnVkGetPhysicalDeviceSurfaceSupportKHR
            ),
            get_physical_device_surface_capabilities_khr: vkproc!(
                entry,
                instance,
                "vkGetPhysicalDeviceSurfaceCapabilitiesKHR",
                PfnVkGetPhysicalDeviceSurfaceCapabilitiesKHR
            ),
            get_physical_device_surface_formats_khr: vkproc!(
                entry,
                instance,
                "vkGetPhysicalDeviceSurfaceFormatsKHR",
                PfnVkGetPhysicalDeviceSurfaceFormatsKHR
            ),
            get_physical_device_surface_present_modes_khr: vkproc!(
                entry,
                instance,
                "vkGetPhysicalDeviceSurfacePresentModesKHR",
                PfnVkGetPhysicalDeviceSurfacePresentModesKHR
            ),
            create_device: vkproc!(entry, instance, "vkCreateDevice", PfnVkCreateDevice),
            destroy_device: vkproc!(entry, instance, "vkDestroyDevice", PfnVkDestroyDevice),
            get_device_queue: vkproc!(entry, instance, "vkGetDeviceQueue", PfnVkGetDeviceQueue),
            create_command_pool: vkproc!(
                entry,
                instance,
                "vkCreateCommandPool",
                PfnVkCreateCommandPool
            ),
            destroy_command_pool: vkproc!(
                entry,
                instance,
                "vkDestroyCommandPool",
                PfnVkDestroyCommandPool
            ),
            allocate_command_buffers: vkproc!(
                entry,
                instance,
                "vkAllocateCommandBuffers",
                PfnVkAllocateCommandBuffers
            ),
            create_semaphore: vkproc!(entry, instance, "vkCreateSemaphore", PfnVkCreateSemaphore),
            destroy_semaphore: vkproc!(
                entry,
                instance,
                "vkDestroySemaphore",
                PfnVkDestroySemaphore
            ),
            create_fence: vkproc!(entry, instance, "vkCreateFence", PfnVkCreateFence),
            destroy_fence: vkproc!(entry, instance, "vkDestroyFence", PfnVkDestroyFence),
            wait_for_fences: vkproc!(entry, instance, "vkWaitForFences", PfnVkWaitForFences),
            reset_fences: vkproc!(entry, instance, "vkResetFences", PfnVkResetFences),
            device_wait_idle: vkproc!(entry, instance, "vkDeviceWaitIdle", PfnVkDeviceWaitIdle),
            create_swapchain_khr: vkproc!(
                entry,
                instance,
                "vkCreateSwapchainKHR",
                PfnVkCreateSwapchainKHR
            ),
            destroy_swapchain_khr: vkproc!(
                entry,
                instance,
                "vkDestroySwapchainKHR",
                PfnVkDestroySwapchainKHR
            ),
            get_swapchain_images_khr: vkproc!(
                entry,
                instance,
                "vkGetSwapchainImagesKHR",
                PfnVkGetSwapchainImagesKHR
            ),
            acquire_next_image_khr: vkproc!(
                entry,
                instance,
                "vkAcquireNextImageKHR",
                PfnVkAcquireNextImageKHR
            ),
            queue_present_khr: vkproc!(entry, instance, "vkQueuePresentKHR", PfnVkQueuePresentKHR),
            begin_command_buffer: vkproc!(
                entry,
                instance,
                "vkBeginCommandBuffer",
                PfnVkBeginCommandBuffer
            ),
            end_command_buffer: vkproc!(
                entry,
                instance,
                "vkEndCommandBuffer",
                PfnVkEndCommandBuffer
            ),
            reset_command_buffer: vkproc!(
                entry,
                instance,
                "vkResetCommandBuffer",
                PfnVkResetCommandBuffer
            ),
            cmd_pipeline_barrier: vkproc!(
                entry,
                instance,
                "vkCmdPipelineBarrier",
                PfnVkCmdPipelineBarrier
            ),
            cmd_clear_color_image: vkproc!(
                entry,
                instance,
                "vkCmdClearColorImage",
                PfnVkCmdClearColorImage
            ),
            queue_submit: vkproc!(entry, instance, "vkQueueSubmit", PfnVkQueueSubmit),
        })
    }
}
