#include "config.hpp"
#include "hash.hpp"
#include "log.hpp"
#include "ptrtex.hpp"

#include <reshade.hpp>
#include <Windows.h>

#include <array>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>
#include <cstring>

namespace ptr
{
namespace
{
struct ResourceRecord
{
    reshade::api::resource_desc desc{};
    uint64_t last_hash = 0;
};

struct MapRecord
{
    reshade::api::subresource_data data{};
    uint32_t subresource = 0;
};

std::mutex g_mutex;
std::unordered_map<uint64_t, ResourceRecord> g_resources;
std::unordered_map<uint64_t, MapRecord> g_maps;
std::filesystem::path g_base_dir;
bool g_loaded = false;
bool g_inside_replacement_upload = false;
uint64_t g_replace_count = 0;
uint64_t g_dump_count = 0;
uint64_t g_seen_count = 0;

thread_local PtrTex g_tls_tex;
thread_local std::vector<uint8_t> g_tls_scratch;
thread_local reshade::api::subresource_data g_tls_subresource{};

uint64_t handle_of(reshade::api::resource r)
{
    return static_cast<uint64_t>(r.handle);
}

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

bool should_consider(const reshade::api::resource_desc &desc)
{
    using reshade::api::resource_type;
    const auto &cfg = config_store().current();
    return cfg.enabled &&
           desc.type == resource_type::texture_2d &&
           desc.texture.width >= cfg.min_width &&
           desc.texture.height >= cfg.min_height &&
           desc.texture.samples <= 1 &&
           is_supported_color_format(desc.texture.format);
}

std::filesystem::path dump_path_for(uint64_t hash)
{
    return config_store().current().base_dir / "dumps" / (hash_to_hex(hash) + ".ptrtex");
}

bool copy_upload_to_ptrtex(const reshade::api::resource_desc &desc,
                           const reshade::api::subresource_data &data,
                           PtrTex &out)
{
    const uint32_t bpp = bytes_per_pixel(desc.texture.format);
    if (bpp != 4 || data.data == nullptr)
        return false;
    const uint32_t row_bytes = desc.texture.width * 4;
    if (data.row_pitch < row_bytes)
        return false;

    out.width = desc.texture.width;
    out.height = desc.texture.height;
    out.format = (desc.texture.format == reshade::api::format::b8g8r8a8_unorm ||
                  desc.texture.format == reshade::api::format::b8g8r8a8_unorm_srgb)
                     ? PtrTexFormat::bgra8
                     : PtrTexFormat::rgba8;
    out.row_pitch = row_bytes;
    out.pixels.resize(static_cast<size_t>(out.height) * row_bytes);

    const auto *src = static_cast<const uint8_t *>(data.data);
    for (uint32_t y = 0; y < out.height; ++y)
        std::memcpy(out.pixels.data() + static_cast<size_t>(y) * row_bytes,
                    src + static_cast<size_t>(y) * data.row_pitch,
                    row_bytes);
    return true;
}

void dump_unknown_if_needed(uint64_t hash,
                            const reshade::api::resource_desc &desc,
                            const reshade::api::subresource_data &data)
{
    const auto &cfg = config_store().current();
    if (!cfg.dump_unknown || hash == 0 || config_store().find(hash) != nullptr)
        return;

    const auto path = dump_path_for(hash);
    if (std::filesystem::exists(path))
        return;

    PtrTex tex;
    if (!copy_upload_to_ptrtex(desc, data, tex))
        return;

    std::string err;
    if (save_ptrtex(path, tex, err))
    {
        ++g_dump_count;
        log().info("dumped unknown texture " + hash_to_hex(hash) + " -> " + path.string());
    }
    else
    {
        log().warn("dump failed: " + err);
    }
}

bool load_rule_texture(const Rule &rule, PtrTex &out)
{
    if (rule.cached_texture.has_value())
    {
        out = *rule.cached_texture;
        return true;
    }

    const auto path = config_store().current().base_dir / rule.replacement;
    PtrTex tex;
    std::string err;
    if (!load_ptrtex(path, tex, err))
    {
        log().warn("replacement load failed: " + err);
        return false;
    }

    rule.cached_texture = tex;
    out = std::move(tex);
    return true;
}

bool replacement_matches_target(const PtrTex &tex, const reshade::api::resource_desc &desc)
{
    return tex.width == desc.texture.width && tex.height == desc.texture.height;
}

bool build_replacement_upload(const Rule &rule,
                              const reshade::api::resource_desc &desc,
                              PtrTex &texture_cache,
                              std::vector<uint8_t> &scratch,
                              reshade::api::subresource_data &upload)
{
    if (!load_rule_texture(rule, texture_cache))
        return false;
    if (!replacement_matches_target(texture_cache, desc))
    {
        log().warn("replacement size mismatch for " + hash_to_hex(rule.hash) +
                   ": replacement=" + std::to_string(texture_cache.width) + "x" + std::to_string(texture_cache.height) +
                   ", original=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height));
        return false;
    }
    return make_subresource_for_format(texture_cache, desc.texture.format, scratch, upload);
}

bool try_prepare_initial_replacement(const reshade::api::resource_desc &desc,
                                     reshade::api::subresource_data *initial_data)
{
    if (initial_data == nullptr || initial_data[0].data == nullptr || !should_consider(desc))
        return false;

    const uint64_t hash = hash_texture_upload(desc, initial_data[0], 0, nullptr);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    if (rule == nullptr)
    {
        dump_unknown_if_needed(hash, desc, initial_data[0]);
        return false;
    }

    if (!build_replacement_upload(*rule, desc, g_tls_tex, g_tls_scratch, g_tls_subresource))
        return false;

    initial_data[0] = g_tls_subresource;
    ++g_replace_count;
    log().info("initial_data replaced: " + hash_to_hex(hash) + " -> " + rule->replacement.string());
    return true;
}

bool try_replace_update(reshade::api::device *device,
                        const reshade::api::resource_desc &desc,
                        const reshade::api::subresource_data &data,
                        reshade::api::resource resource,
                        uint32_t subresource,
                        const reshade::api::subresource_box *box)
{
    if (g_inside_replacement_upload || data.data == nullptr || !should_consider(desc))
        return false;

    const uint64_t hash = hash_texture_upload(desc, data, subresource, box);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    if (rule == nullptr)
    {
        dump_unknown_if_needed(hash, desc, data);
        return false;
    }

    PtrTex tex;
    std::vector<uint8_t> scratch;
    reshade::api::subresource_data upload{};
    if (!build_replacement_upload(*rule, desc, tex, scratch, upload))
        return false;

    g_inside_replacement_upload = true;
    device->update_texture_region(upload, resource, subresource, box);
    g_inside_replacement_upload = false;

    ++g_replace_count;
    log().info("update_texture_region replaced: " + hash_to_hex(hash) + " -> " + rule->replacement.string());
    return true;
}

void try_replace_mapped_data(const reshade::api::resource_desc &desc,
                             const reshade::api::subresource_data &mapped,
                             uint32_t subresource)
{
    if (mapped.data == nullptr || !should_consider(desc))
        return;

    const uint64_t hash = hash_texture_upload(desc, mapped, subresource, nullptr);
    if (hash == 0)
        return;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    if (rule == nullptr)
    {
        dump_unknown_if_needed(hash, desc, mapped);
        return;
    }

    PtrTex tex;
    std::vector<uint8_t> scratch;
    reshade::api::subresource_data upload{};
    if (!build_replacement_upload(*rule, desc, tex, scratch, upload))
        return;

    const uint32_t row_bytes = desc.texture.width * bytes_per_pixel(desc.texture.format);
    auto *dst = static_cast<uint8_t *>(mapped.data);
    const auto *src = static_cast<const uint8_t *>(upload.data);
    for (uint32_t y = 0; y < desc.texture.height; ++y)
        std::memcpy(dst + static_cast<size_t>(y) * mapped.row_pitch,
                    src + static_cast<size_t>(y) * upload.row_pitch,
                    row_bytes);

    ++g_replace_count;
    log().info("mapped texture replaced: " + hash_to_hex(hash) + " -> " + rule->replacement.string());
}
}
}

static void on_init_device(reshade::api::device *device)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    if (!ptr::g_loaded)
    {
        ptr::g_base_dir = ptr::detect_base_dir();
        ptr::config_store().load(ptr::g_base_dir);
        ptr::g_loaded = true;
    }
}

static bool on_create_resource(reshade::api::device *device,
                               reshade::api::resource_desc &desc,
                               reshade::api::subresource_data *initial_data,
                               reshade::api::resource_usage initial_state)
{
    UNREFERENCED_PARAMETER(device);
    UNREFERENCED_PARAMETER(initial_state);
    std::scoped_lock lock(ptr::g_mutex);
    return ptr::try_prepare_initial_replacement(desc, initial_data);
}

static void on_init_resource(reshade::api::device *device,
                             const reshade::api::resource_desc &desc,
                             const reshade::api::subresource_data *initial_data,
                             reshade::api::resource_usage initial_state,
                             reshade::api::resource resource)
{
    UNREFERENCED_PARAMETER(device);
    UNREFERENCED_PARAMETER(initial_state);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::should_consider(desc))
    {
        ptr::g_resources[ptr::handle_of(resource)] = ptr::ResourceRecord{desc, 0};
        if (initial_data != nullptr && initial_data[0].data != nullptr)
        {
            const uint64_t h = ptr::hash_texture_upload(desc, initial_data[0], 0, nullptr);
            ptr::g_resources[ptr::handle_of(resource)].last_hash = h;
        }
    }
}

static void on_destroy_resource(reshade::api::device *device, reshade::api::resource resource)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    ptr::g_resources.erase(ptr::handle_of(resource));
    ptr::g_maps.erase(ptr::handle_of(resource));
}

static bool on_update_texture_region(reshade::api::device *device,
                                     const reshade::api::subresource_data &data,
                                     reshade::api::resource resource,
                                     uint32_t subresource,
                                     const reshade::api::subresource_box *box)
{
    std::scoped_lock lock(ptr::g_mutex);
    const auto it = ptr::g_resources.find(ptr::handle_of(resource));
    if (it == ptr::g_resources.end())
        return false;
    return ptr::try_replace_update(device, it->second.desc, data, resource, subresource, box);
}

static void on_map_texture_region(reshade::api::device *device,
                                  reshade::api::resource resource,
                                  uint32_t subresource,
                                  const reshade::api::subresource_box *box,
                                  reshade::api::map_access access,
                                  reshade::api::subresource_data *data)
{
    UNREFERENCED_PARAMETER(device);
    UNREFERENCED_PARAMETER(box);
    UNREFERENCED_PARAMETER(access);
    if (data == nullptr || data->data == nullptr)
        return;
    std::scoped_lock lock(ptr::g_mutex);
    ptr::g_maps[ptr::handle_of(resource)] = ptr::MapRecord{*data, subresource};
}

static void on_unmap_texture_region(reshade::api::device *device,
                                    reshade::api::resource resource,
                                    uint32_t subresource)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    const auto res_it = ptr::g_resources.find(ptr::handle_of(resource));
    const auto map_it = ptr::g_maps.find(ptr::handle_of(resource));
    if (res_it != ptr::g_resources.end() && map_it != ptr::g_maps.end() && map_it->second.subresource == subresource)
        ptr::try_replace_mapped_data(res_it->second.desc, map_it->second.data, subresource);
    if (map_it != ptr::g_maps.end())
        ptr::g_maps.erase(map_it);
}

static bool on_update_texture_region_command(reshade::api::command_list *cmd_list,
                                             const reshade::api::subresource_data &data,
                                             reshade::api::resource dest,
                                             uint32_t dest_subresource,
                                             const reshade::api::subresource_box *dest_box)
{
    // Deferred D3D11 path. A full implementation should obtain the device from cmd_list
    // and perform the same replacement upload as on_update_texture_region. Kept as a safe no-op in MVP.
    UNREFERENCED_PARAMETER(cmd_list);
    UNREFERENCED_PARAMETER(data);
    UNREFERENCED_PARAMETER(dest);
    UNREFERENCED_PARAMETER(dest_subresource);
    UNREFERENCED_PARAMETER(dest_box);
    return false;
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
        reshade::register_event<reshade::addon_event::create_resource>(&on_create_resource);
        reshade::register_event<reshade::addon_event::init_resource>(&on_init_resource);
        reshade::register_event<reshade::addon_event::destroy_resource>(&on_destroy_resource);
        reshade::register_event<reshade::addon_event::update_texture_region>(&on_update_texture_region);
        reshade::register_event<reshade::addon_event::update_texture_region_command>(&on_update_texture_region_command);
        reshade::register_event<reshade::addon_event::map_texture_region>(&on_map_texture_region);
        reshade::register_event<reshade::addon_event::unmap_texture_region>(&on_unmap_texture_region);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_addon(hinstDLL);
        break;
    }
    return TRUE;
}
