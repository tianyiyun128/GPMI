#include "godot_unit_hook.hpp"
#include "log.hpp"

#include <Windows.h>

#include <filesystem>
#include <string>

namespace gpmi
{
namespace
{
HMODULE g_module = nullptr;

std::filesystem::path module_dir()
{
    wchar_t path[MAX_PATH * 4]{};
    GetModuleFileNameW(g_module, path, static_cast<DWORD>(sizeof(path) / sizeof(path[0])));
    return std::filesystem::path(path).parent_path();
}

std::filesystem::path game_dir()
{
    wchar_t path[MAX_PATH * 4]{};
    GetModuleFileNameW(nullptr, path, static_cast<DWORD>(sizeof(path) / sizeof(path[0])));
    return std::filesystem::path(path).parent_path();
}

std::filesystem::path profile_dir()
{
    wchar_t env[MAX_PATH * 4]{};
    const DWORD env_size = static_cast<DWORD>(sizeof(env) / sizeof(env[0]));
    const DWORD len = GetEnvironmentVariableW(L"GPMI_PROFILE_DIR", env, env_size);
    if (len > 0 && len < env_size)
        return std::filesystem::path(env);
    return game_dir() / "GPMI";
}

DWORD WINAPI init_thread(void *)
{
    const auto profile = profile_dir();
    const auto dll = module_dir();
    log().open(profile / "GPMIUnitHook.log");
    log().info("GPMIUnitHook loaded");
    log().info("profile_dir=" + profile.string());
    log().info("dll_dir=" + dll.string());

    if (!install_unit_hook(profile, dll))
        log().error("GPMIUnitHook initialization failed");
    return 0;
}
}
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        gpmi::g_module = instance;
        DisableThreadLibraryCalls(instance);
        HANDLE thread = CreateThread(nullptr, 0, &gpmi::init_thread, nullptr, 0, nullptr);
        if (thread)
            CloseHandle(thread);
    }
    else if (reason == DLL_PROCESS_DETACH)
    {
        gpmi::remove_unit_hook();
        gpmi::log().info("GPMIUnitHook unloaded");
    }
    return TRUE;
}
