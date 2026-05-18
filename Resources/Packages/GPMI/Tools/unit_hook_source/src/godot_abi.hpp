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
    size_t object_callp_patch_size = 12;
    bool verbose_calls = false;

    // Resolved automatically from GPMITextureLoader.dll.
    std::uintptr_t texture_loader = 0;
};

GodotAbiConfig load_abi_config(const std::filesystem::path &profile_dir,
                               const std::filesystem::path &dll_dir);
std::optional<std::string> try_decode_method_name(const void *method);
std::optional<UnitCall> try_decode_unit_call(void *self, const void *method, const void **args, int arg_count);
std::string describe_variant_for_log(const void *variant);
std::string describe_string_name_for_log(const void *string_name);
bool replace_return_with_texture(void *return_variant, const std::filesystem::path &replacement);
void set_texture_loader(std::uintptr_t address);
}
