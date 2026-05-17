#include "config.hpp"
#include "hash.hpp"
#include "log.hpp"
#include "ptrtex.hpp"

#include <reshade.hpp>
#include <Windows.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace ptr
{
namespace
{
struct ResourceRecord
{
    reshade::api::resource_desc desc{};
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

uint64_t g_resource_log_count = 0;
uint64_t g_upload_log_count = 0;
uint64_t g_view_log_count = 0;
uint64_t g_descriptor_log_count = 0;
uint64_t g_draw_log_count = 0;
uint64_t g_render_pass_log_count = 0;
uint64_t g_lifecycle_log_count = 0;
uint64_t g_copy_log_count = 0;
uint64_t g_present_log_count = 0;

constexpr uint64_t k_max_resource_logs = 600;
constexpr uint64_t k_max_upload_logs = 800;
constexpr uint64_t k_max_view_logs = 400;
constexpr uint64_t k_max_descriptor_logs = 400;
constexpr uint64_t k_max_draw_logs = 800;
constexpr uint64_t k_max_render_pass_logs = 400;
constexpr uint64_t k_max_lifecycle_logs = 300;
constexpr uint64_t k_max_copy_logs = 400;
constexpr uint64_t k_max_present_logs = 160;

thread_local PtrTex g_tls_tex;
thread_local std::vector<uint8_t> g_tls_scratch;
thread_local reshade::api::subresource_data g_tls_upload{};

uint64_t handle_of(reshade::api::resource value) { return static_cast<uint64_t>(value.handle); }
uint64_t handle_of(reshade::api::resource_view value) { return static_cast<uint64_t>(value.handle); }
uint64_t handle_of(reshade::api::descriptor_table value) { return static_cast<uint64_t>(value.handle); }
uint64_t handle_of(reshade::api::command_list *value) { return reinterpret_cast<uint64_t>(value); }
uint64_t handle_of(reshade::api::command_queue *value) { return reinterpret_cast<uint64_t>(value); }
uint64_t handle_of(reshade::api::swapchain *value) { return reinterpret_cast<uint64_t>(value); }
uint64_t handle_of(reshade::api::effect_runtime *value) { return reinterpret_cast<uint64_t>(value); }

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

bool is_texture_2d(const reshade::api::resource_desc &desc)
{
    return desc.type == reshade::api::resource_type::texture_2d;
}

std::string desc_text(const reshade::api::resource_desc &desc)
{
    if (!is_texture_2d(desc))
        return "type=" + std::to_string(static_cast<uint32_t>(desc.type));

    return "target=" + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
           ", format=" + std::to_string(static_cast<uint32_t>(desc.texture.format)) +
           ", samples=" + std::to_string(desc.texture.samples) +
           ", levels=" + std::to_string(desc.texture.levels) +
           ", type=" + std::to_string(static_cast<uint32_t>(desc.type));
}

uint32_t box_width(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->right > box->left)
        return box->right - box->left;
    return is_texture_2d(desc) ? desc.texture.width : 0;
}

uint32_t box_height(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->bottom > box->top)
        return box->bottom - box->top;
    return is_texture_2d(desc) ? desc.texture.height : 0;
}

bool matches_rule_size(const reshade::api::resource_desc &desc)
{
    if (!is_texture_2d(desc))
        return false;

    for (const auto &entry : config_store().current().rules)
    {
        const Rule &rule = entry.second;
        if (rule.width != 0 && rule.height != 0 &&
            rule.width == desc.texture.width && rule.height == desc.texture.height)
            return true;
    }
    return false;
}

std::string target_suffix(const reshade::api::resource_desc &desc)
{
    if (!matches_rule_size(desc))
        return {};

    uint32_t count = 0;
    std::string labels;
    for (const auto &entry : config_store().current().rules)
    {
        const Rule &rule = entry.second;
        if (rule.width != desc.texture.width || rule.height != desc.texture.height)
            continue;

        ++count;
        if (labels.size() < 180)
        {
            if (!labels.empty())
                labels += "|";
            labels += rule.slot.empty() ? "slot?" : rule.slot;
            if (!rule.hash_variant.empty())
                labels += ":" + rule.hash_variant;
        }
    }

    return ", TARGET size_rules=" + std::to_string(count) +
           (labels.empty() ? std::string() : ", labels=" + labels);
}

void log_lifecycle(const std::string &message)
{
    if (!g_loaded || g_lifecycle_log_count >= k_max_lifecycle_logs)
        return;
    ++g_lifecycle_log_count;
    log().info(message);
}

void log_upload_candidate(const char *stage,
                          const reshade::api::resource_desc &desc,
                          const reshade::api::subresource_box *box,
                          bool accepted,
                          const std::string &reason)
{
    if (!g_loaded || g_upload_log_count >= k_max_upload_logs)
        return;

    ++g_upload_log_count;
    log().info(std::string("upload candidate ") + stage + ": " + desc_text(desc) +
               ", upload=" + std::to_string(box_width(desc, box)) + "x" + std::to_string(box_height(desc, box)) +
               ", accepted=" + (accepted ? "yes" : "no") +
               ", reason=" + reason + target_suffix(desc));
}

bool load_rule_texture(const Rule &rule, PtrTex &out)
{
    if (rule.cached_texture.has_value())
    {
        out = *rule.cached_texture;
        return true;
    }

    PtrTex tex;
    std::string err;
    const auto path = config_store().current().base_dir / rule.replacement;
    if (!load_ptrtex(path, tex, err))
    {
        log().warn("replacement load failed: " + err);
        return false;
    }

    rule.cached_texture = tex;
    out = std::move(tex);
    return true;
}

bool build_replacement_upload(const Rule &rule,
                              const reshade::api::resource_desc &desc,
                              PtrTex &texture_cache,
                              std::vector<uint8_t> &scratch,
                              reshade::api::subresource_data &upload)
{
    if (!load_rule_texture(rule, texture_cache))
        return false;

    if (!is_texture_2d(desc) || texture_cache.width != desc.texture.width || texture_cache.height != desc.texture.height)
    {
        log().warn("replacement size mismatch for " + hash_to_hex(rule.hash) +
                   ": replacement=" + std::to_string(texture_cache.width) + "x" + std::to_string(texture_cache.height) +
                   ", original=" + desc_text(desc));
        return false;
    }

    return make_subresource_for_format(texture_cache, desc.texture.format, scratch, upload);
}

bool should_hash_upload(const reshade::api::resource_desc &desc, std::string *reason)
{
    const auto &cfg = config_store().current();
    if (!cfg.enabled)
    {
        if (reason != nullptr) *reason = "disabled";
        return false;
    }
    if (!is_texture_2d(desc))
    {
        if (reason != nullptr) *reason = "not texture_2d";
        return false;
    }
    if (desc.texture.width < cfg.min_width || desc.texture.height < cfg.min_height)
    {
        if (reason != nullptr) *reason = "below minimum";
        return false;
    }
    if (desc.texture.samples > 1)
    {
        if (reason != nullptr) *reason = "multisampled";
        return false;
    }
    if (!is_supported_color_format(desc.texture.format))
    {
        if (reason != nullptr) *reason = "unsupported format";
        return false;
    }
    if (reason != nullptr) *reason = "accepted";
    return true;
}

const Rule *find_rule_for_upload(const char *stage,
                                 const reshade::api::resource_desc &desc,
                                 const reshade::api::subresource_data &data,
                                 uint32_t subresource,
                                 const reshade::api::subresource_box *box)
{
    std::string reason;
    const bool accepted = should_hash_upload(desc, &reason);
    log_upload_candidate(stage, desc, box, accepted, reason);
    if (!accepted || data.data == nullptr)
        return nullptr;

    const uint64_t hash = hash_texture_upload(desc, data, subresource, box);
    if (hash == 0)
        return nullptr;

    const Rule *rule = config_store().find(hash);
    if (g_upload_log_count < k_max_upload_logs)
    {
        ++g_upload_log_count;
        log().info(std::string("seen texture ") + stage + ": hash=" + hash_to_hex(hash) +
                   ", rule=" + (rule != nullptr ? "yes" : "no") +
                   ", " + desc_text(desc));
    }
    return rule;
}

bool try_prepare_initial_replacement(const reshade::api::resource_desc &desc,
                                     reshade::api::subresource_data *initial_data)
{
    if (initial_data == nullptr || initial_data[0].data == nullptr)
    {
        std::string reason;
        const bool accepted = should_hash_upload(desc, &reason);
        log_upload_candidate("initial_data", desc, nullptr, accepted, reason + ", initial_data=no");
        return false;
    }

    const Rule *rule = find_rule_for_upload("initial_data", desc, initial_data[0], 0, nullptr);
    if (rule == nullptr)
        return false;

    if (!build_replacement_upload(*rule, desc, g_tls_tex, g_tls_scratch, g_tls_upload))
        return false;

    initial_data[0] = g_tls_upload;
    log().info("initial_data replaced: " + hash_to_hex(rule->hash) + " -> " + rule->replacement.string());
    return true;
}

bool try_replace_existing_upload(reshade::api::device *device,
                                 const reshade::api::resource_desc &desc,
                                 const reshade::api::subresource_data &data,
                                 reshade::api::resource resource,
                                 uint32_t subresource,
                                 const reshade::api::subresource_box *box,
                                 const char *stage)
{
    if (device == nullptr || g_inside_replacement_upload || data.data == nullptr || handle_of(resource) == 0)
        return false;

    const Rule *rule = find_rule_for_upload(stage, desc, data, subresource, box);
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

    log().info(std::string(stage) + " replaced: " + hash_to_hex(rule->hash) + " -> " + rule->replacement.string());
    return true;
}
}
}

static void on_init_device(reshade::api::device *device)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_loaded)
        return;

    ptr::g_base_dir = ptr::detect_base_dir();
    ptr::config_store().load(ptr::g_base_dir);
    ptr::g_loaded = true;

    const auto &cfg = ptr::config_store().current();
    ptr::log().info("runtime filters: min_width=" + std::to_string(cfg.min_width) +
                    ", min_height=" + std::to_string(cfg.min_height) +
                    ", hash_db=" + cfg.hash_db_path.string());
    if (device != nullptr)
    {
        ptr::log().info("device api: " + ptr::api_name(device->get_api()) +
                        ", native=" + std::to_string(device->get_native()));
        ptr::log().info("device caps: bind_rt=" + std::to_string(device->check_capability(reshade::api::device_caps::bind_render_targets_and_depth_stencil)) +
                        ", sampler_with_resource_view=" + std::to_string(device->check_capability(reshade::api::device_caps::sampler_with_resource_view)) +
                        ", draw_indirect=" + std::to_string(device->check_capability(reshade::api::device_caps::draw_or_dispatch_indirect)) +
                        ", copy_buffer_to_texture=" + std::to_string(device->check_capability(reshade::api::device_caps::copy_buffer_to_texture)));
    }
    ptr::log().info("draw diagnostics: command stream coverage probe enabled");
}

static void on_init_command_list(reshade::api::command_list *cmd_list)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("command list init: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                       ", native=" + (cmd_list != nullptr ? std::to_string(cmd_list->get_native()) : "0"));
}

static void on_destroy_command_list(reshade::api::command_list *cmd_list)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("command list destroy: cmd=" + std::to_string(ptr::handle_of(cmd_list)));
}

static void on_init_command_queue(reshade::api::command_queue *queue)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("command queue init: queue=" + std::to_string(ptr::handle_of(queue)) +
                       ", native=" + (queue != nullptr ? std::to_string(queue->get_native()) : "0"));
}

static void on_destroy_command_queue(reshade::api::command_queue *queue)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("command queue destroy: queue=" + std::to_string(ptr::handle_of(queue)));
}

static void on_init_swapchain(reshade::api::swapchain *swapchain, bool resize)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("swapchain init: swapchain=" + std::to_string(ptr::handle_of(swapchain)) +
                       ", resize=" + std::to_string(resize) +
                       ", native=" + (swapchain != nullptr ? std::to_string(swapchain->get_native()) : "0"));
}

static void on_init_effect_runtime(reshade::api::effect_runtime *runtime)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("effect runtime init: runtime=" + std::to_string(ptr::handle_of(runtime)));
}

static bool on_create_resource(reshade::api::device *device,
                               reshade::api::resource_desc &desc,
                               reshade::api::subresource_data *initial_data,
                               reshade::api::resource_usage initial_state)
{
    UNREFERENCED_PARAMETER(device);
    UNREFERENCED_PARAMETER(initial_state);
    std::scoped_lock lock(ptr::g_mutex);

    if (ptr::g_resource_log_count < ptr::k_max_resource_logs)
    {
        ++ptr::g_resource_log_count;
        ptr::log().info("resource create heartbeat: " + ptr::desc_text(desc) +
                        ", initial_data=" + std::string(initial_data != nullptr && initial_data[0].data != nullptr ? "yes" : "no") +
                        ptr::target_suffix(desc));
    }
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

    if (ptr::g_resource_log_count < ptr::k_max_resource_logs)
    {
        ++ptr::g_resource_log_count;
        ptr::log().info("resource init heartbeat: res=" + std::to_string(ptr::handle_of(resource)) +
                        ", " + ptr::desc_text(desc) +
                        ", initial_data=" + std::string(initial_data != nullptr && initial_data[0].data != nullptr ? "yes" : "no") +
                        ptr::target_suffix(desc));
    }

    if (ptr::is_texture_2d(desc))
        ptr::g_resources[ptr::handle_of(resource)] = ptr::ResourceRecord{desc};
}

static void on_destroy_resource(reshade::api::device *device, reshade::api::resource resource)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    ptr::g_resources.erase(ptr::handle_of(resource));
    ptr::g_maps.erase(ptr::handle_of(resource));
}

static void on_init_resource_view(reshade::api::device *device,
                                  reshade::api::resource resource,
                                  reshade::api::resource_usage usage_type,
                                  const reshade::api::resource_view_desc &desc,
                                  reshade::api::resource_view view)
{
    UNREFERENCED_PARAMETER(desc);
    std::scoped_lock lock(ptr::g_mutex);
    if (device == nullptr || ptr::g_view_log_count >= ptr::k_max_view_logs)
        return;

    const auto resource_desc = device->get_resource_desc(resource);
    ++ptr::g_view_log_count;
    ptr::log().info("resource view heartbeat: view=" + std::to_string(ptr::handle_of(view)) +
                    ", res=" + std::to_string(ptr::handle_of(resource)) +
                    ", usage=" + std::to_string(static_cast<uint32_t>(usage_type)) +
                    ", " + ptr::desc_text(resource_desc) + ptr::target_suffix(resource_desc));
}

static void on_destroy_resource_view(reshade::api::device *device, reshade::api::resource_view view)
{
    UNREFERENCED_PARAMETER(device);
    UNREFERENCED_PARAMETER(view);
}

static bool on_update_texture_region(reshade::api::device *device,
                                     const reshade::api::subresource_data &data,
                                     reshade::api::resource resource,
                                     uint32_t subresource,
                                     const reshade::api::subresource_box *box)
{
    std::scoped_lock lock(ptr::g_mutex);
    auto it = ptr::g_resources.find(ptr::handle_of(resource));
    if (it == ptr::g_resources.end())
        return false;
    return ptr::try_replace_existing_upload(device, it->second.desc, data, resource, subresource, box, "update_texture_region");
}

static bool on_update_texture_region_command(reshade::api::command_list *cmd_list,
                                             const reshade::api::subresource_data &data,
                                             reshade::api::resource dest,
                                             uint32_t dest_subresource,
                                             const reshade::api::subresource_box *dest_box)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (cmd_list == nullptr)
        return false;
    reshade::api::device *device = cmd_list->get_device();
    if (device == nullptr)
        return false;
    const auto desc = device->get_resource_desc(dest);
    return ptr::try_replace_existing_upload(device, desc, data, dest, dest_subresource, dest_box, "command_update_texture_region");
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
    std::scoped_lock lock(ptr::g_mutex);
    if (device == nullptr)
        return;
    auto map_it = ptr::g_maps.find(ptr::handle_of(resource));
    if (map_it == ptr::g_maps.end() || map_it->second.subresource != subresource)
        return;
    const auto desc = device->get_resource_desc(resource);
    ptr::try_replace_existing_upload(device, desc, map_it->second.data, resource, subresource, nullptr, "mapped_texture");
    ptr::g_maps.erase(map_it);
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
    UNREFERENCED_PARAMETER(source);
    UNREFERENCED_PARAMETER(source_offset);
    UNREFERENCED_PARAMETER(row_length);
    UNREFERENCED_PARAMETER(slice_height);
    UNREFERENCED_PARAMETER(dest_box);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_copy_log_count < ptr::k_max_copy_logs)
    {
        ++ptr::g_copy_log_count;
        ptr::log().info("copy buffer to texture heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", dest=" + std::to_string(ptr::handle_of(dest)) +
                        ", subresource=" + std::to_string(dest_subresource));
    }
    return false;
}

static bool on_update_descriptor_tables(reshade::api::device *device,
                                        uint32_t count,
                                        const reshade::api::descriptor_table_update *updates)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_descriptor_log_count < ptr::k_max_descriptor_logs)
    {
        ++ptr::g_descriptor_log_count;
        std::string msg = "descriptor update heartbeat: count=" + std::to_string(count);
        if (updates != nullptr && count > 0)
        {
            msg += ", first_table=" + std::to_string(ptr::handle_of(updates[0].table)) +
                   ", first_binding=" + std::to_string(updates[0].binding) +
                   ", first_type=" + std::to_string(static_cast<uint32_t>(updates[0].type)) +
                   ", first_count=" + std::to_string(updates[0].count);
        }
        ptr::log().info(msg);
    }
    return false;
}

static void on_push_descriptors(reshade::api::command_list *cmd_list,
                                reshade::api::shader_stage stages,
                                reshade::api::pipeline_layout layout,
                                uint32_t layout_param,
                                const reshade::api::descriptor_table_update &update)
{
    UNREFERENCED_PARAMETER(stages);
    UNREFERENCED_PARAMETER(layout);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_descriptor_log_count < ptr::k_max_descriptor_logs)
    {
        ++ptr::g_descriptor_log_count;
        ptr::log().info("push descriptors heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", layout_param=" + std::to_string(layout_param) +
                        ", binding=" + std::to_string(update.binding) +
                        ", type=" + std::to_string(static_cast<uint32_t>(update.type)) +
                        ", count=" + std::to_string(update.count));
    }
}

static void on_bind_descriptor_tables(reshade::api::command_list *cmd_list,
                                      reshade::api::shader_stage stages,
                                      reshade::api::pipeline_layout layout,
                                      uint32_t first,
                                      uint32_t count,
                                      const reshade::api::descriptor_table *tables)
{
    UNREFERENCED_PARAMETER(stages);
    UNREFERENCED_PARAMETER(layout);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_descriptor_log_count < ptr::k_max_descriptor_logs)
    {
        ++ptr::g_descriptor_log_count;
        ptr::log().info("descriptor table bind heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", first=" + std::to_string(first) +
                        ", count=" + std::to_string(count) +
                        ", first_table=" + (tables != nullptr && count > 0 ? std::to_string(ptr::handle_of(tables[0])) : "0"));
    }
}

static bool on_begin_render_pass(reshade::api::command_list *cmd_list,
                                 uint32_t count,
                                 const reshade::api::render_pass_render_target_desc *rts,
                                 const reshade::api::render_pass_depth_stencil_desc *ds,
                                 reshade::api::render_pass_flags flags)
{
    UNREFERENCED_PARAMETER(rts);
    UNREFERENCED_PARAMETER(ds);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_render_pass_log_count < ptr::k_max_render_pass_logs)
    {
        ++ptr::g_render_pass_log_count;
        ptr::log().info("begin render pass heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", count=" + std::to_string(count) +
                        ", flags=" + std::to_string(static_cast<uint32_t>(flags)));
    }
    return false;
}

static bool on_end_render_pass(reshade::api::command_list *cmd_list)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_render_pass_log_count < ptr::k_max_render_pass_logs)
    {
        ++ptr::g_render_pass_log_count;
        ptr::log().info("end render pass heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)));
    }
    return false;
}

static void on_bind_render_targets_and_depth_stencil(reshade::api::command_list *cmd_list,
                                                     uint32_t count,
                                                     const reshade::api::resource_view *rtvs,
                                                     reshade::api::resource_view dsv)
{
    UNREFERENCED_PARAMETER(dsv);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_render_pass_log_count < ptr::k_max_render_pass_logs)
    {
        ++ptr::g_render_pass_log_count;
        ptr::log().info("bind render target heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", count=" + std::to_string(count) +
                        ", first_rtv=" + (rtvs != nullptr && count > 0 ? std::to_string(ptr::handle_of(rtvs[0])) : "0"));
    }
}

static bool on_draw(reshade::api::command_list *cmd_list,
                    uint32_t vertex_count,
                    uint32_t instance_count,
                    uint32_t first_vertex,
                    uint32_t first_instance)
{
    UNREFERENCED_PARAMETER(first_vertex);
    UNREFERENCED_PARAMETER(first_instance);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_draw_log_count < ptr::k_max_draw_logs)
    {
        ++ptr::g_draw_log_count;
        ptr::log().info("draw heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", vertex_count=" + std::to_string(vertex_count) +
                        ", instance_count=" + std::to_string(instance_count));
    }
    return false;
}

static bool on_draw_indexed(reshade::api::command_list *cmd_list,
                            uint32_t index_count,
                            uint32_t instance_count,
                            uint32_t first_index,
                            int32_t vertex_offset,
                            uint32_t first_instance)
{
    UNREFERENCED_PARAMETER(first_index);
    UNREFERENCED_PARAMETER(vertex_offset);
    UNREFERENCED_PARAMETER(first_instance);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_draw_log_count < ptr::k_max_draw_logs)
    {
        ++ptr::g_draw_log_count;
        ptr::log().info("draw indexed heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", index_count=" + std::to_string(index_count) +
                        ", instance_count=" + std::to_string(instance_count));
    }
    return false;
}

static bool on_dispatch(reshade::api::command_list *cmd_list,
                        uint32_t group_count_x,
                        uint32_t group_count_y,
                        uint32_t group_count_z)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_draw_log_count < ptr::k_max_draw_logs)
    {
        ++ptr::g_draw_log_count;
        ptr::log().info("dispatch heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", groups=" + std::to_string(group_count_x) + "x" +
                        std::to_string(group_count_y) + "x" + std::to_string(group_count_z));
    }
    return false;
}

static bool on_draw_or_dispatch_indirect(reshade::api::command_list *cmd_list,
                                         reshade::api::indirect_command type,
                                         reshade::api::resource buffer,
                                         uint64_t offset,
                                         uint32_t draw_count,
                                         uint32_t stride)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_draw_log_count < ptr::k_max_draw_logs)
    {
        ++ptr::g_draw_log_count;
        ptr::log().info("indirect heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", type=" + std::to_string(static_cast<uint32_t>(type)) +
                        ", buffer=" + std::to_string(ptr::handle_of(buffer)) +
                        ", offset=" + std::to_string(offset) +
                        ", draw_count=" + std::to_string(draw_count) +
                        ", stride=" + std::to_string(stride));
    }
    return false;
}

static bool on_copy_resource(reshade::api::command_list *cmd_list,
                             reshade::api::resource source,
                             reshade::api::resource dest)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_copy_log_count < ptr::k_max_copy_logs)
    {
        ++ptr::g_copy_log_count;
        ptr::log().info("copy resource heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", src=" + std::to_string(ptr::handle_of(source)) +
                        ", dst=" + std::to_string(ptr::handle_of(dest)));
    }
    return false;
}

static bool on_copy_texture_region(reshade::api::command_list *cmd_list,
                                   reshade::api::resource source,
                                   uint32_t source_subresource,
                                   const reshade::api::subresource_box *source_box,
                                   reshade::api::resource dest,
                                   uint32_t dest_subresource,
                                   const reshade::api::subresource_box *dest_box,
                                   reshade::api::filter_mode filter)
{
    UNREFERENCED_PARAMETER(source_box);
    UNREFERENCED_PARAMETER(dest_box);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_copy_log_count < ptr::k_max_copy_logs)
    {
        ++ptr::g_copy_log_count;
        ptr::log().info("copy texture heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", src=" + std::to_string(ptr::handle_of(source)) +
                        ", src_sub=" + std::to_string(source_subresource) +
                        ", dst=" + std::to_string(ptr::handle_of(dest)) +
                        ", dst_sub=" + std::to_string(dest_subresource) +
                        ", filter=" + std::to_string(static_cast<uint32_t>(filter)));
    }
    return false;
}

static bool on_copy_texture_to_buffer(reshade::api::command_list *cmd_list,
                                      reshade::api::resource source,
                                      uint32_t source_subresource,
                                      const reshade::api::subresource_box *source_box,
                                      reshade::api::resource dest,
                                      uint64_t dest_offset,
                                      uint32_t row_length,
                                      uint32_t slice_height)
{
    UNREFERENCED_PARAMETER(source_box);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_copy_log_count < ptr::k_max_copy_logs)
    {
        ++ptr::g_copy_log_count;
        ptr::log().info("copy texture to buffer heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", src=" + std::to_string(ptr::handle_of(source)) +
                        ", src_sub=" + std::to_string(source_subresource) +
                        ", dst=" + std::to_string(ptr::handle_of(dest)) +
                        ", dest_offset=" + std::to_string(dest_offset) +
                        ", row_length=" + std::to_string(row_length) +
                        ", slice_height=" + std::to_string(slice_height));
    }
    return false;
}

static bool on_clear_render_target_view(reshade::api::command_list *cmd_list,
                                        reshade::api::resource_view rtv,
                                        const float color[4],
                                        uint32_t rect_count,
                                        const reshade::api::rect *rects)
{
    UNREFERENCED_PARAMETER(color);
    UNREFERENCED_PARAMETER(rects);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_copy_log_count < ptr::k_max_copy_logs)
    {
        ++ptr::g_copy_log_count;
        ptr::log().info("clear render target heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                        ", rtv=" + std::to_string(ptr::handle_of(rtv)) +
                        ", rect_count=" + std::to_string(rect_count));
    }
    return false;
}

static void on_execute_command_list(reshade::api::command_queue *queue,
                                    reshade::api::command_list *cmd_list)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("execute command list heartbeat: queue=" + std::to_string(ptr::handle_of(queue)) +
                       ", cmd=" + std::to_string(ptr::handle_of(cmd_list)));
}

static void on_execute_secondary_command_list(reshade::api::command_list *cmd_list,
                                              reshade::api::command_list *secondary_cmd_list)
{
    std::scoped_lock lock(ptr::g_mutex);
    ptr::log_lifecycle("execute secondary command list heartbeat: cmd=" + std::to_string(ptr::handle_of(cmd_list)) +
                       ", secondary=" + std::to_string(ptr::handle_of(secondary_cmd_list)));
}

static void on_present(reshade::api::command_queue *queue,
                       reshade::api::swapchain *swapchain,
                       const reshade::api::rect *source_rect,
                       const reshade::api::rect *dest_rect,
                       uint32_t dirty_rect_count,
                       const reshade::api::rect *dirty_rects)
{
    UNREFERENCED_PARAMETER(source_rect);
    UNREFERENCED_PARAMETER(dest_rect);
    UNREFERENCED_PARAMETER(dirty_rects);
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_present_log_count < ptr::k_max_present_logs)
    {
        ++ptr::g_present_log_count;
        ptr::log().info("present heartbeat: queue=" + std::to_string(ptr::handle_of(queue)) +
                        ", swapchain=" + std::to_string(ptr::handle_of(swapchain)) +
                        ", dirty_rect_count=" + std::to_string(dirty_rect_count));
    }
}

static void on_finish_present(reshade::api::command_queue *queue,
                              reshade::api::swapchain *swapchain)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (ptr::g_present_log_count < ptr::k_max_present_logs)
    {
        ++ptr::g_present_log_count;
        ptr::log().info("finish present heartbeat: queue=" + std::to_string(ptr::handle_of(queue)) +
                        ", swapchain=" + std::to_string(ptr::handle_of(swapchain)));
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
        reshade::register_event<reshade::addon_event::init_command_list>(&on_init_command_list);
        reshade::register_event<reshade::addon_event::destroy_command_list>(&on_destroy_command_list);
        reshade::register_event<reshade::addon_event::init_command_queue>(&on_init_command_queue);
        reshade::register_event<reshade::addon_event::destroy_command_queue>(&on_destroy_command_queue);
        reshade::register_event<reshade::addon_event::init_swapchain>(&on_init_swapchain);
        reshade::register_event<reshade::addon_event::init_effect_runtime>(&on_init_effect_runtime);
        reshade::register_event<reshade::addon_event::create_resource>(&on_create_resource);
        reshade::register_event<reshade::addon_event::init_resource>(&on_init_resource);
        reshade::register_event<reshade::addon_event::destroy_resource>(&on_destroy_resource);
        reshade::register_event<reshade::addon_event::init_resource_view>(&on_init_resource_view);
        reshade::register_event<reshade::addon_event::destroy_resource_view>(&on_destroy_resource_view);
        reshade::register_event<reshade::addon_event::update_texture_region>(&on_update_texture_region);
        reshade::register_event<reshade::addon_event::update_texture_region_command>(&on_update_texture_region_command);
        reshade::register_event<reshade::addon_event::copy_buffer_to_texture>(&on_copy_buffer_to_texture);
        reshade::register_event<reshade::addon_event::copy_texture_to_buffer>(&on_copy_texture_to_buffer);
        reshade::register_event<reshade::addon_event::copy_resource>(&on_copy_resource);
        reshade::register_event<reshade::addon_event::copy_texture_region>(&on_copy_texture_region);
        reshade::register_event<reshade::addon_event::clear_render_target_view>(&on_clear_render_target_view);
        reshade::register_event<reshade::addon_event::map_texture_region>(&on_map_texture_region);
        reshade::register_event<reshade::addon_event::unmap_texture_region>(&on_unmap_texture_region);
        reshade::register_event<reshade::addon_event::update_descriptor_tables>(&on_update_descriptor_tables);
        reshade::register_event<reshade::addon_event::push_descriptors>(&on_push_descriptors);
        reshade::register_event<reshade::addon_event::bind_descriptor_tables>(&on_bind_descriptor_tables);
        reshade::register_event<reshade::addon_event::begin_render_pass>(&on_begin_render_pass);
        reshade::register_event<reshade::addon_event::end_render_pass>(&on_end_render_pass);
        reshade::register_event<reshade::addon_event::bind_render_targets_and_depth_stencil>(&on_bind_render_targets_and_depth_stencil);
        reshade::register_event<reshade::addon_event::draw>(&on_draw);
        reshade::register_event<reshade::addon_event::draw_indexed>(&on_draw_indexed);
        reshade::register_event<reshade::addon_event::dispatch>(&on_dispatch);
        reshade::register_event<reshade::addon_event::draw_or_dispatch_indirect>(&on_draw_or_dispatch_indirect);
        reshade::register_event<reshade::addon_event::execute_command_list>(&on_execute_command_list);
        reshade::register_event<reshade::addon_event::execute_secondary_command_list>(&on_execute_secondary_command_list);
        reshade::register_event<reshade::addon_event::present>(&on_present);
        reshade::register_event<reshade::addon_event::finish_present>(&on_finish_present);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_addon(hinstDLL);
        break;
    }
    return TRUE;
}
