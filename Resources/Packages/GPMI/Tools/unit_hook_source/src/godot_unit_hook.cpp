#include "godot_unit_hook.hpp"

#include "godot_abi.hpp"
#include "log.hpp"
#include "manifest.hpp"
#include "win_memory.hpp"

#include <atomic>
#include <array>
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
bool g_probe_only = false;
bool g_verbose_calls = false;
std::atomic<std::uint64_t> g_call_count = 0;
std::atomic<std::uint64_t> g_decoded_method_count = 0;
std::atomic<std::uint64_t> g_unit_method_count = 0;
std::atomic<std::uint64_t> g_unit_decoded_count = 0;
std::atomic<std::uint64_t> g_unit_decode_failure_count = 0;
std::atomic<std::uint64_t> g_sret_arg_sample_count = 0;
std::atomic<std::uint64_t> g_alt_arg_sample_count = 0;
std::array<std::atomic<std::uint64_t>, 8> g_sret_arg_hist{};
std::array<std::atomic<std::uint64_t>, 8> g_alt_arg_hist{};
thread_local bool g_inside_hook = false;
std::mutex g_install_mutex;

void log_argument_sample(const char *label,
                         const void *method,
                         const void **args,
                         int arg_count,
                         std::atomic<std::uint64_t> &counter)
{
    if (arg_count < 2 || arg_count > 4)
        return;
    const auto sample = ++counter;
    if (sample > 16 && (sample % 200) != 0)
        return;

    std::string method_name = "<decode failed>";
    if (auto decoded = try_decode_method_name(method))
        method_name = *decoded;

    log().info(std::string(label) + " candidate call: arg_count=" + std::to_string(arg_count) +
               ", method_guess=" + method_name +
               ", method_raw=" + describe_string_name_for_log(method));
    if (args && arg_count > 0)
        log().info(std::string(label) + " arg[0]=" + describe_variant_for_log(args[0]));
    if (args && arg_count > 1)
        log().info(std::string(label) + " arg[1]=" + describe_variant_for_log(args[1]));
    if (args && arg_count > 2)
        log().info(std::string(label) + " arg[2]=" + describe_variant_for_log(args[2]));
}

void __fastcall object_callp_detour(void *return_variant,
                                    void *self,
                                    const void *method,
                                    const void **args,
                                    int arg_count,
                                    void *call_error)
{
    std::optional<UnitCall> unit_call;
    const auto call_index = ++g_call_count;
    if (arg_count >= 0 && arg_count < static_cast<int>(g_sret_arg_hist.size()))
        ++g_sret_arg_hist[static_cast<size_t>(arg_count)];
    const int alt_arg_count = static_cast<int>(reinterpret_cast<std::uintptr_t>(args));
    if (alt_arg_count >= 0 && alt_arg_count < static_cast<int>(g_alt_arg_hist.size()))
        ++g_alt_arg_hist[static_cast<size_t>(alt_arg_count)];

    if (!g_inside_hook)
    {
        g_inside_hook = true;
        if (g_verbose_calls)
        {
            if (auto method_name = try_decode_method_name(method))
            {
                const auto decoded = ++g_decoded_method_count;
                if (*method_name == "unit" || decoded <= 80 || (decoded % 500) == 0)
                    log().info("callp method observed: " + *method_name);
                if (*method_name == "unit")
                    ++g_unit_method_count;
            }
            else if (call_index <= 80 || (call_index % 1000) == 0)
            {
                log().info("callp observed but method decode failed, calls=" + std::to_string(call_index));
            }
        }
        if (g_verbose_calls)
        {
            log_argument_sample("sret", method, args, arg_count, g_sret_arg_sample_count);

            const void *alt_method = self;
            const void **alt_args = reinterpret_cast<const void **>(const_cast<void *>(method));
            log_argument_sample("nosret", alt_method, alt_args, alt_arg_count, g_alt_arg_sample_count);
        }
        unit_call = try_decode_unit_call(self, method, args, arg_count);
        if (!unit_call && g_verbose_calls)
        {
            if (auto method_name = try_decode_method_name(method); method_name && *method_name == "unit")
            {
                const auto failures = ++g_unit_decode_failure_count;
                if (failures <= 12 || (failures % 100) == 0)
                {
                    log().warn("unit call argument decode failed: arg_count=" + std::to_string(arg_count));
                    if (args && arg_count > 0)
                        log().warn("  arg[0]=" + describe_variant_for_log(args[0]));
                    if (args && arg_count > 1)
                        log().warn("  arg[1]=" + describe_variant_for_log(args[1]));
                    if (args && arg_count > 2)
                        log().warn("  arg[2]=" + describe_variant_for_log(args[2]));
                }
            }
        }
        g_inside_hook = false;
    }

    g_original(return_variant, self, method, args, arg_count, call_error);

    if (!unit_call || !unit_call->valid)
        return;

    ++g_unit_decoded_count;
    log().info("unit call observed: " + unit_call->logical_path);
    if (g_probe_only)
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
    g_probe_only = abi.probe_only;
    g_verbose_calls = abi.verbose_calls;

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
    if (abi.probe_only)
        log().info("probe_only enabled; unit calls will be logged without return replacement");
    if (abi.verbose_calls)
        log().info("verbose_calls enabled; Object::callp method names will be sampled");
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
        log().info("Object::callp stats: calls=" + std::to_string(g_call_count.load()) +
                   ", decoded_methods=" + std::to_string(g_decoded_method_count.load()) +
                   ", unit_methods=" + std::to_string(g_unit_method_count.load()) +
                   ", unit_decoded=" + std::to_string(g_unit_decoded_count.load()) +
                   ", unit_decode_failures=" + std::to_string(g_unit_decode_failure_count.load()));
        std::string sret_hist = "Object::callp sret arg_count histogram:";
        std::string alt_hist = "Object::callp nosret arg_count histogram:";
        for (size_t i = 0; i < g_sret_arg_hist.size(); ++i)
        {
            sret_hist += " " + std::to_string(i) + "=" + std::to_string(g_sret_arg_hist[i].load());
            alt_hist += " " + std::to_string(i) + "=" + std::to_string(g_alt_arg_hist[i].load());
        }
        log().info(sret_hist);
        log().info(alt_hist);
        log().info("Object::callp hook removed");
    }
}
}
