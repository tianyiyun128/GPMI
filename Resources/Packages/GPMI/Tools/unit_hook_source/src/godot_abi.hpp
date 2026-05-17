#pragma once

#include <filesystem>
#include <optional>
#include <string>

namespace gpmi
{
struct UnitCall
{
    bool valid = false;
    std::string method_name;
    std::string type;
    std::string action;
    bool high_resolution = false;
    std::string logical_path;
};

struct GodotAbiConfig
{
    std::uintptr_t object_callp = 0;
    std::string object_callp_pattern;
    size_t object_callp_patch_size = 16;

    // Optional function inside the target process. Its ABI is intentionally
    // simple so a small game-version-specific thunk can be added later without
    // changing this hook:
    //   bool __fastcall loader(void *out_variant, const wchar_t *path)
    std::uintptr_t texture_loader = 0;
};

GodotAbiConfig load_abi_config(const std::filesystem::path &profile_dir,
                               const std::filesystem::path &dll_dir);
std::optional<UnitCall> try_decode_unit_call(void *self, const void *method, const void **args, int arg_count);
bool replace_return_with_texture(void *return_variant, const std::filesystem::path &replacement);
void set_texture_loader(std::uintptr_t address);
}
