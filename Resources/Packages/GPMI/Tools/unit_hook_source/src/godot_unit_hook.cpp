#include "godot_unit_hook.hpp"

#include "godot_abi.hpp"
#include "log.hpp"
#include "manifest.hpp"
#include "win_memory.hpp"

#include <mutex>

namespace gpmi
{
namespace
{
// MSVC x64 member function returning Godot Variant by value is expected to use
// a hidden return pointer first, then this, then normal arguments.
using ObjectCallpFn = void(__fastcall *)(void *return_variant,
                                         void *self,
                                         const void *method,
                                         const void **args,
                                         int arg_count,
                                         void *call_error);

Hook g_hook;
ObjectCallpFn g_original = nullptr;
thread_local bool g_inside_hook = false;
std::mutex g_install_mutex;

void __fastcall object_callp_detour(void *return_variant,
                                    void *self,
                                    const void *method,
                                    const void **args,
                                    int arg_count,
                                    void *call_error)
{
    std::optional<UnitCall> unit_call;
    if (!g_inside_hook)
    {
        g_inside_hook = true;
        unit_call = try_decode_unit_call(self, method, args, arg_count);
        g_inside_hook = false;
    }

    g_original(return_variant, self, method, args, arg_count, call_error);

    if (!unit_call || !unit_call->valid)
        return;

    auto rule = manifest().match(unit_call->logical_path);
    if (!rule && unit_call->action != "default")
    {
        const std::string fallback_key = std::string(unit_call->high_resolution ? "Unit_H/" : "Unit/") +
                                         unit_call->type + "_default";
        rule = manifest().match(fallback_key);
    }

    if (!rule)
    {
        log().info("unit call skipped: no rule for " + unit_call->logical_path);
        return;
    }

    if (!std::filesystem::is_regular_file(rule->replacement))
    {
        log().warn("unit call matched but replacement file is missing: " + rule->replacement.string());
        return;
    }

    log().info("unit call matched: " + unit_call->logical_path +
               " -> " + rule->replacement.string());
    if (replace_return_with_texture(return_variant, rule->replacement))
        log().info("unit return replaced: " + unit_call->logical_path);
    else
        log().warn("unit return replacement failed: " + unit_call->logical_path);
}
}

bool install_unit_hook(const std::filesystem::path &profile_dir,
                       const std::filesystem::path &dll_dir)
{
    std::lock_guard lock(g_install_mutex);
    if (g_hook.trampoline)
        return true;

    manifest().set_path(profile_dir / "live_portraits.json");
    manifest().reload_if_needed();

    GodotAbiConfig abi = load_abi_config(profile_dir, dll_dir);
    set_texture_loader(abi.texture_loader);

    void *target = reinterpret_cast<void *>(abi.object_callp);
    if (!target)
    {
        auto range = main_module_range();
        if (!range)
        {
            log().error("failed to read main module range");
            return false;
        }
        target = scan_pattern(*range, abi.object_callp_pattern);
    }

    if (!target)
    {
        log().error("Object::callp target not found. Add object_callp_rva or object_callp_abs to GPMIUnitHook.ini.");
        return false;
    }

    g_hook.target = target;
    g_hook.detour = reinterpret_cast<void *>(&object_callp_detour);
    g_hook.patch_size = abi.object_callp_patch_size;
    if (!install_hook(g_hook))
    {
        log().error("failed to install Object::callp hook");
        return false;
    }

    g_original = reinterpret_cast<ObjectCallpFn>(g_hook.trampoline);
    log().info("Object::callp hook installed at " + std::to_string(reinterpret_cast<std::uintptr_t>(target)));
    if (!abi.texture_loader)
        log().warn("texture loader thunk is not configured; hook will log matches but cannot replace return values");
    return true;
}

void remove_unit_hook()
{
    std::lock_guard lock(g_install_mutex);
    if (g_hook.trampoline)
    {
        remove_hook(g_hook);
        g_original = nullptr;
        log().info("Object::callp hook removed");
    }
}
}
