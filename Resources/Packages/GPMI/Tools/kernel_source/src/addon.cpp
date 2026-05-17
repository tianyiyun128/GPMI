#include "config.hpp"
#include "log.hpp"

#include <reshade.hpp>
#include <Windows.h>

#include <filesystem>
#include <string>

namespace ptr
{
namespace
{
std::filesystem::path g_base_dir;
bool g_loaded = false;

std::filesystem::path detect_base_dir()
{
    char path[MAX_PATH * 4]{};
    size_t path_size = sizeof(path);
    reshade::get_reshade_base_path(path, &path_size);
    if (path[0] != '\0')
        return std::filesystem::path(path);

    wchar_t module_path[MAX_PATH * 4]{};
    GetModuleFileNameW(nullptr, module_path, static_cast<DWORD>(sizeof(module_path) / sizeof(module_path[0])));
    return std::filesystem::path(module_path).parent_path();
}

std::string api_name(reshade::api::device_api api)
{
    switch (api)
    {
    case reshade::api::device_api::d3d9: return "d3d9";
    case reshade::api::device_api::d3d10: return "d3d10";
    case reshade::api::device_api::d3d11: return "d3d11";
    case reshade::api::device_api::d3d12: return "d3d12";
    case reshade::api::device_api::opengl: return "opengl";
    case reshade::api::device_api::vulkan: return "vulkan";
    default: return "unknown(" + std::to_string(static_cast<uint32_t>(api)) + ")";
    }
}
}
}

static void on_init_device(reshade::api::device *device)
{
    if (ptr::g_loaded)
        return;

    ptr::g_base_dir = ptr::detect_base_dir();
    ptr::config_store().load(ptr::g_base_dir);
    ptr::g_loaded = true;

    const auto &cfg = ptr::config_store().current();
    ptr::log().info("GPMI live bridge mode active, manifest=" + cfg.manifest_path.string() +
                    ", revision=" + std::to_string(cfg.manifest_revision) +
                    ", live_rules=" + std::to_string(cfg.manifest_rules));
    if (device != nullptr)
    {
        ptr::log().info("device api: " + ptr::api_name(device->get_api()) +
                        ", native=" + std::to_string(device->get_native()));
    }
}

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID)
{
    switch (fdwReason)
    {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hinstDLL);
        if (!reshade::register_addon(hinstDLL))
            return FALSE;
        reshade::register_event<reshade::addon_event::init_device>(&on_init_device);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_addon(hinstDLL);
        break;
    }
    return TRUE;
}
