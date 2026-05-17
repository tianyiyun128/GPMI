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
uint64_t g_seen_count = 0;
uint64_t g_trace_count = 0;
uint64_t g_candidate_trace_count = 0;
constexpr uint64_t k_max_trace_logs = 1200;
constexpr uint64_t k_max_candidate_trace_logs = 400;

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

bool should_consider_with_reason(const reshade::api::resource_desc &desc, std::string *reason)
{
    using reshade::api::resource_type;
    const auto &cfg = config_store().current();
    if (!cfg.enabled)
    {
        if (reason != nullptr) *reason = "disabled by config";
        return false;
    }
    if (desc.type != resource_type::texture_2d)
    {
        if (reason != nullptr) *reason = "not texture_2d, type=" + std::to_string(static_cast<uint32_t>(desc.type));
        return false;
    }
    if (desc.texture.width < cfg.min_width || desc.texture.height < cfg.min_height)
    {
        if (reason != nullptr)
            *reason = "below minimum, target=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
                      ", minimum=" + std::to_string(cfg.min_width) + "x" + std::to_string(cfg.min_height);
        return false;
    }
    if (desc.texture.samples > 1)
    {
        if (reason != nullptr) *reason = "multisampled, samples=" + std::to_string(desc.texture.samples);
        return false;
    }
    if (!is_supported_color_format(desc.texture.format))
    {
        if (reason != nullptr) *reason = "unsupported format=" + std::to_string(static_cast<uint32_t>(desc.texture.format));
        return false;
    }
    if (reason != nullptr) *reason = "accepted";
    return true;
}

bool should_consider(const reshade::api::resource_desc &desc)
{
    return should_consider_with_reason(desc, nullptr);
}

std::string texture_desc_text(const reshade::api::resource_desc &desc,
                              const reshade::api::subresource_box *box)
{
    return "target=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
           ", upload=" + std::to_string(region_width(desc, box)) + "x" + std::to_string(region_height(desc, box)) +
           ", format=" + std::to_string(static_cast<uint32_t>(desc.texture.format)) +
           ", samples=" + std::to_string(desc.texture.samples) +
           ", type=" + std::to_string(static_cast<uint32_t>(desc.type));
}

void trace_candidate(const char *stage,
                     const reshade::api::resource_desc &desc,
                     const reshade::api::subresource_box *box,
                     bool accepted,
                     const std::string &reason,
                     const std::string &detail = {})
{
    if (!g_loaded || g_candidate_trace_count >= k_max_candidate_trace_logs)
        return;

    ++g_candidate_trace_count;
    log().info(std::string("texture candidate ") + stage +
               ": " + texture_desc_text(desc, box) +
               ", accepted=" + (accepted ? "yes" : "no") +
               ", reason=" + reason +
               (detail.empty() ? std::string() : ", " + detail));
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

uint32_t region_width(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->right > box->left)
        return box->right - box->left;
    return desc.texture.width;
}

uint32_t region_height(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->bottom > box->top)
        return box->bottom - box->top;
    return desc.texture.height;
}

void trace_seen_upload(const char *stage,
                       uint64_t hash,
                       const reshade::api::resource_desc &desc,
                       uint32_t subresource,
                       const reshade::api::subresource_box *box,
                       const Rule *rule)
{
    if (g_trace_count >= k_max_trace_logs)
        return;

    ++g_trace_count;
    const uint32_t upload_width = region_width(desc, box);
    const uint32_t upload_height = region_height(desc, box);

    log().info(std::string("seen texture ") + stage +
               ": hash=" + hash_to_hex(hash) +
               ", target=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
               ", upload=" + std::to_string(upload_width) + "x" + std::to_string(upload_height) +
               ", format=" + std::to_string(static_cast<uint32_t>(desc.texture.format)) +
               ", subresource=" + std::to_string(subresource) +
               ", rule=" + (rule != nullptr ? "yes" : "no"));
}

void trace_copy_event(const char *stage,
                      const reshade::api::resource_desc &desc,
                      uint32_t subresource,
                      const reshade::api::subresource_box *box,
                      const std::string &detail)
{
    if (g_trace_count >= k_max_trace_logs)
        return;

    ++g_trace_count;
    log().info(std::string("texture upload ") + stage +
               ": target=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
               ", upload=" + std::to_string(region_width(desc, box)) + "x" + std::to_string(region_height(desc, box)) +
               ", format=" + std::to_string(static_cast<uint32_t>(desc.texture.format)) +
               ", subresource=" + std::to_string(subresource) +
               ", " + detail);
}

bool try_prepare_initial_replacement(const reshade::api::resource_desc &desc,
                                     reshade::api::subresource_data *initial_data)
{
    std::string reason;
    const bool accepted = should_consider_with_reason(desc, &reason);
    trace_candidate("create-resource", desc, nullptr, accepted, reason,
                    initial_data != nullptr && initial_data[0].data != nullptr ? "initial_data=yes" : "initial_data=no");
    if (initial_data == nullptr || initial_data[0].data == nullptr || !accepted)
        return false;

    const uint64_t hash = hash_texture_upload(desc, initial_data[0], 0, nullptr);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    trace_seen_upload("initial", hash, desc, 0, nullptr, rule);
    if (rule == nullptr)
        return false;

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
    if (g_inside_replacement_upload || data.data == nullptr)
        return false;

    std::string reason;
    const bool accepted = should_consider_with_reason(desc, &reason);
    trace_candidate("update", desc, box, accepted, reason, "subresource=" + std::to_string(subresource));
    if (!accepted)
        return false;

    const uint64_t hash = hash_texture_upload(desc, data, subresource, box);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    trace_seen_upload("update", hash, desc, subresource, box, rule);
    if (rule == nullptr)
        return false;

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
    if (mapped.data == nullptr)
        return;

    std::string reason;
    const bool accepted = should_consider_with_reason(desc, &reason);
    trace_candidate("mapped", desc, nullptr, accepted, reason, "subresource=" + std::to_string(subresource));
    if (!accepted)
        return;

    const uint64_t hash = hash_texture_upload(desc, mapped, subresource, nullptr);
    if (hash == 0)
        return;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    trace_seen_upload("mapped", hash, desc, subresource, nullptr, rule);
    if (rule == nullptr)
        return;

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

bool try_replace_update_command(reshade::api::command_list *cmd_list,
                                const reshade::api::subresource_data &data,
                                reshade::api::resource dest,
                                uint32_t dest_subresource,
                                const reshade::api::subresource_box *dest_box)
{
    if (g_inside_replacement_upload || data.data == nullptr || cmd_list == nullptr)
        return false;

    reshade::api::device *device = cmd_list->get_device();
    if (device == nullptr)
        return false;

    const auto desc = device->get_resource_desc(dest);
    std::string reason;
    const bool accepted = should_consider_with_reason(desc, &reason);
    trace_candidate("command-update", desc, dest_box, accepted, reason, "subresource=" + std::to_string(dest_subresource));
    if (!accepted)
        return false;

    const uint64_t hash = hash_texture_upload(desc, data, dest_subresource, dest_box);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    trace_seen_upload("command-update", hash, desc, dest_subresource, dest_box, rule);
    if (rule == nullptr)
        return false;

    PtrTex tex;
    std::vector<uint8_t> scratch;
    reshade::api::subresource_data upload{};
    if (!build_replacement_upload(*rule, desc, tex, scratch, upload))
        return false;

    g_inside_replacement_upload = true;
    cmd_list->update_texture_region(upload, dest, dest_subresource, dest_box);
    g_inside_replacement_upload = false;

    ++g_replace_count;
    log().info("command update_texture_region replaced: " + hash_to_hex(hash) + " -> " + rule->replacement.string());
    return true;
}

bool try_replace_copy_buffer_to_texture(reshade::api::command_list *cmd_list,
                                        reshade::api::resource source,
                                        uint64_t source_offset,
                                        uint32_t row_length,
                                        uint32_t slice_height,
                                        reshade::api::resource dest,
                                        uint32_t dest_subresource,
                                        const reshade::api::subresource_box *dest_box)
{
    UNREFERENCED_PARAMETER(slice_height);

    if (g_inside_replacement_upload || cmd_list == nullptr)
        return false;

    reshade::api::device *device = cmd_list->get_device();
    if (device == nullptr)
        return false;

    const auto desc = device->get_resource_desc(dest);
    std::string reason;
    const bool accepted = should_consider_with_reason(desc, &reason);
    trace_candidate("copy-buffer-to-texture", desc, dest_box, accepted, reason,
                    "subresource=" + std::to_string(dest_subresource) + ", row_length=" + std::to_string(row_length));
    if (!accepted)
        return false;

    const uint32_t bpp = bytes_per_pixel(desc.texture.format);
    const uint32_t width = region_width(desc, dest_box);
    const uint32_t height = region_height(desc, dest_box);
    if (bpp == 0 || width == 0 || height == 0)
        return false;

    const uint32_t row_pitch = (row_length != 0 ? row_length : width) * bpp;
    const uint32_t row_bytes = width * bpp;
    if (row_pitch < row_bytes)
        return false;

    const uint64_t required_size = static_cast<uint64_t>(row_pitch) * (height - 1) + row_bytes;
    void *mapped = nullptr;
    if (!device->map_buffer_region(source, source_offset, required_size, reshade::api::map_access::read_only, &mapped) || mapped == nullptr)
    {
        trace_copy_event("copy-buffer-to-texture", desc, dest_subresource, dest_box,
                         "source map failed, offset=" + std::to_string(source_offset) +
                         ", row_length=" + std::to_string(row_length));
        return false;
    }

    reshade::api::subresource_data data{};
    data.data = mapped;
    data.row_pitch = row_pitch;
    data.slice_pitch = row_pitch * height;

    const uint64_t hash = hash_texture_upload(desc, data, dest_subresource, dest_box);
    device->unmap_buffer_region(source);
    if (hash == 0)
        return false;

    ++g_seen_count;
    const Rule *rule = config_store().find(hash);
    trace_seen_upload("copy-buffer-to-texture", hash, desc, dest_subresource, dest_box, rule);
    if (rule == nullptr)
        return false;

    PtrTex tex;
    std::vector<uint8_t> scratch;
    reshade::api::subresource_data upload{};
    if (!build_replacement_upload(*rule, desc, tex, scratch, upload))
        return false;

    g_inside_replacement_upload = true;
    cmd_list->update_texture_region(upload, dest, dest_subresource, dest_box);
    g_inside_replacement_upload = false;

    ++g_replace_count;
    log().info("copy_buffer_to_texture replaced: " + hash_to_hex(hash) + " -> " + rule->replacement.string());
    return true;
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
        const auto &cfg = ptr::config_store().current();
        ptr::log().info("runtime filters: min_width=" + std::to_string(cfg.min_width) +
                        ", min_height=" + std::to_string(cfg.min_height) +
                        ", hash_db=" + cfg.hash_db_path.string());
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
    std::string reason;
    const bool accepted = ptr::should_consider_with_reason(desc, &reason);
    ptr::trace_candidate("init-resource", desc, nullptr, accepted, reason,
                         initial_data != nullptr && initial_data[0].data != nullptr ? "initial_data=yes" : "initial_data=no");
    if (accepted)
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
    {
        if (device != nullptr)
        {
            const auto desc = device->get_resource_desc(resource);
            std::string reason;
            const bool accepted = ptr::should_consider_with_reason(desc, &reason);
            ptr::trace_candidate("update-untracked", desc, box, accepted, reason,
                                 "resource was not accepted during init-resource, subresource=" + std::to_string(subresource));
        }
        return false;
    }
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
    std::scoped_lock lock(ptr::g_mutex);
    return ptr::try_replace_update_command(cmd_list, data, dest, dest_subresource, dest_box);
}

static bool on_copy_buffer_to_texture(reshade::api::command_list *cmd_list,
                                      reshade::api::resource source,
                                      uint64_t source_offset,
                                      uint32_t row_length,
                                      uint32_t slice_height,
                                      reshade::api::resource dest,
                                      uint32_t dest_subresource,
                                      const reshade::api::subresource_box *dest_box)
{
    std::scoped_lock lock(ptr::g_mutex);
    return ptr::try_replace_copy_buffer_to_texture(cmd_list, source, source_offset, row_length, slice_height, dest, dest_subresource, dest_box);
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
        reshade::register_event<reshade::addon_event::copy_buffer_to_texture>(&on_copy_buffer_to_texture);
        reshade::register_event<reshade::addon_event::map_texture_region>(&on_map_texture_region);
        reshade::register_event<reshade::addon_event::unmap_texture_region>(&on_unmap_texture_region);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_addon(hinstDLL);
        break;
    }
    return TRUE;
}
