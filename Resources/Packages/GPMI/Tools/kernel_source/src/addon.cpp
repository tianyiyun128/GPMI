#include "config.hpp"
#include "hash.hpp"
#include "log.hpp"
#include "ptrtex.hpp"

#include <reshade.hpp>
#include <Windows.h>

#include <array>
#include <algorithm>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

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

struct ViewRecord
{
    reshade::api::resource resource{};
    reshade::api::resource_desc desc{};
    reshade::api::resource_usage usage{};
};

struct CommandState
{
    std::vector<uint64_t> target_views;
    reshade::api::resource render_target{};
    reshade::api::resource_desc render_target_desc{};
    bool has_render_target = false;
};

std::mutex g_mutex;
std::unordered_map<uint64_t, ResourceRecord> g_resources;
std::unordered_map<uint64_t, MapRecord> g_maps;
std::unordered_map<uint64_t, ViewRecord> g_views;
std::unordered_map<uint64_t, std::vector<uint64_t>> g_descriptor_table_targets;
std::unordered_map<uint64_t, CommandState> g_command_states;
std::filesystem::path g_base_dir;
bool g_loaded = false;
bool g_inside_replacement_upload = false;
uint64_t g_replace_count = 0;
uint64_t g_seen_count = 0;
uint64_t g_trace_count = 0;
uint64_t g_candidate_trace_count = 0;
uint64_t g_target_trace_count = 0;
uint64_t g_draw_trace_count = 0;
constexpr uint64_t k_max_trace_logs = 1200;
constexpr uint64_t k_max_candidate_trace_logs = 400;
constexpr uint64_t k_max_target_trace_logs = 1000;
constexpr uint64_t k_max_draw_trace_logs = 600;

thread_local PtrTex g_tls_tex;
thread_local std::vector<uint8_t> g_tls_scratch;
thread_local reshade::api::subresource_data g_tls_subresource{};

uint32_t region_width(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box);
uint32_t region_height(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box);

uint64_t handle_of(reshade::api::resource r)
{
    return static_cast<uint64_t>(r.handle);
}

uint64_t handle_of(reshade::api::resource_view v)
{
    return static_cast<uint64_t>(v.handle);
}

uint64_t handle_of(reshade::api::descriptor_table table)
{
    return static_cast<uint64_t>(table.handle);
}

uint64_t handle_of(reshade::api::command_list *cmd_list)
{
    return reinterpret_cast<uint64_t>(cmd_list);
}

bool is_texture_2d(const reshade::api::resource_desc &desc)
{
    return desc.type == reshade::api::resource_type::texture_2d;
}

bool rule_has_dimensions(const Rule &rule)
{
    return rule.width != 0 && rule.height != 0;
}

bool dimensions_match_rule(const reshade::api::resource_desc &desc, const Rule &rule)
{
    return is_texture_2d(desc) && rule_has_dimensions(rule) &&
           desc.texture.width == rule.width && desc.texture.height == rule.height;
}

bool format_matches_rule(const reshade::api::resource_desc &desc, const Rule &rule)
{
    return rule.gpu_format == 0 || static_cast<uint32_t>(desc.texture.format) == rule.gpu_format;
}

bool has_target_sized_rule(const reshade::api::resource_desc &desc)
{
    if (!is_texture_2d(desc))
        return false;
    for (const auto &entry : config_store().current().rules)
    {
        if (dimensions_match_rule(desc, entry.second))
            return true;
    }
    return false;
}

std::string target_rule_summary(const reshade::api::resource_desc &desc)
{
    uint32_t size_matches = 0;
    uint32_t format_matches = 0;
    std::vector<std::string> labels;
    for (const auto &entry : config_store().current().rules)
    {
        const Rule &rule = entry.second;
        if (!dimensions_match_rule(desc, rule))
            continue;
        ++size_matches;
        if (format_matches_rule(desc, rule))
            ++format_matches;
        if (labels.size() < 5)
        {
            std::string label = rule.slot.empty() ? std::string("slot?") : rule.slot;
            if (!rule.hash_variant.empty())
                label += ":" + rule.hash_variant;
            if (!rule.character_id.empty())
                label += ":" + rule.character_id;
            labels.push_back(label);
        }
    }

    std::string result = "size_rules=" + std::to_string(size_matches) +
                         ", format_rules=" + std::to_string(format_matches);
    if (!labels.empty())
    {
        result += ", labels=";
        for (size_t i = 0; i < labels.size(); ++i)
        {
            if (i != 0)
                result += "|";
            result += labels[i];
        }
    }
    return result;
}

void add_unique(std::vector<uint64_t> &values, uint64_t value)
{
    if (value == 0)
        return;
    if (std::find(values.begin(), values.end(), value) == values.end())
        values.push_back(value);
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

void trace_target(const std::string &message)
{
    if (!g_loaded || g_target_trace_count >= k_max_target_trace_logs)
        return;
    ++g_target_trace_count;
    log().info(message);
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

void remember_view_record(reshade::api::device *device,
                          reshade::api::resource_view view,
                          reshade::api::resource_usage usage = reshade::api::resource_usage::shader_resource)
{
    const uint64_t vh = handle_of(view);
    if (vh == 0 || device == nullptr)
        return;
    if (g_views.find(vh) != g_views.end())
        return;

    const reshade::api::resource resource = device->get_resource_from_view(view);
    if (handle_of(resource) == 0)
        return;

    const auto desc = device->get_resource_desc(resource);
    g_views[vh] = ViewRecord{resource, desc, usage};
}

std::vector<uint64_t> extract_target_views_from_descriptor_update(reshade::api::device *device,
                                                                  const reshade::api::descriptor_table_update &update)
{
    std::vector<uint64_t> result;
    if (update.descriptors == nullptr || update.count == 0)
        return result;

    if (update.type == reshade::api::descriptor_type::shader_resource_view ||
        update.type == reshade::api::descriptor_type::buffer_shader_resource_view ||
        update.type == reshade::api::descriptor_type::unordered_access_view ||
        update.type == reshade::api::descriptor_type::buffer_unordered_access_view)
    {
        const auto *views = static_cast<const reshade::api::resource_view *>(update.descriptors);
        for (uint32_t i = 0; i < update.count; ++i)
        {
            const reshade::api::resource_view view = views[i];
            remember_view_record(device, view);
            const uint64_t vh = handle_of(view);
            const auto view_it = g_views.find(vh);
            if (view_it != g_views.end() && has_target_sized_rule(view_it->second.desc))
                add_unique(result, vh);
        }
    }
    else if (update.type == reshade::api::descriptor_type::sampler_with_resource_view)
    {
        const auto *combined = static_cast<const reshade::api::sampler_with_resource_view *>(update.descriptors);
        for (uint32_t i = 0; i < update.count; ++i)
        {
            const reshade::api::resource_view view = combined[i].view;
            remember_view_record(device, view);
            const uint64_t vh = handle_of(view);
            const auto view_it = g_views.find(vh);
            if (view_it != g_views.end() && has_target_sized_rule(view_it->second.desc))
                add_unique(result, vh);
        }
    }

    return result;
}

std::string target_views_text(const std::vector<uint64_t> &views)
{
    std::string text;
    size_t written = 0;
    for (uint64_t vh : views)
    {
        const auto it = g_views.find(vh);
        if (it == g_views.end())
            continue;
        const auto &desc = it->second.desc;
        if (written != 0)
            text += "; ";
        text += "view=" + std::to_string(vh) +
                " res=" + std::to_string(handle_of(it->second.resource)) +
                " " + std::to_string(desc.texture.width) + "x" + std::to_string(desc.texture.height) +
                " fmt=" + std::to_string(static_cast<uint32_t>(desc.texture.format)) +
                " " + target_rule_summary(desc);
        ++written;
        if (written >= 8)
            break;
    }
    return text;
}

void remember_command_targets(reshade::api::command_list *cmd_list, const std::vector<uint64_t> &views)
{
    if (cmd_list == nullptr || views.empty())
        return;
    auto &state = g_command_states[handle_of(cmd_list)];
    for (uint64_t view : views)
        add_unique(state.target_views, view);
}

void log_draw_targets(const char *stage,
                      reshade::api::command_list *cmd_list,
                      uint32_t primary_count,
                      uint32_t instance_count)
{
    if (cmd_list == nullptr || g_draw_trace_count >= k_max_draw_trace_logs)
        return;

    const auto state_it = g_command_states.find(handle_of(cmd_list));
    if (state_it == g_command_states.end())
        return;
    const CommandState &state = state_it->second;
    if (state.target_views.empty())
        return;

    ++g_draw_trace_count;
    std::string message = std::string("draw-time target texture ") + stage +
                          ": count=" + std::to_string(primary_count) +
                          ", instances=" + std::to_string(instance_count) +
                          ", textures=[" + target_views_text(state.target_views) + "]";
    if (state.has_render_target)
    {
        message += ", rt=" + std::to_string(state.render_target_desc.texture.width) +
                   "x" + std::to_string(state.render_target_desc.texture.height) +
                   " fmt=" + std::to_string(static_cast<uint32_t>(state.render_target_desc.texture.format));
    }
    log().info(message);
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
    if (ptr::has_target_sized_rule(desc))
    {
        ptr::trace_target("target-sized resource init: res=" + std::to_string(ptr::handle_of(resource)) +
                          ", " + ptr::texture_desc_text(desc, nullptr) +
                          ", " + ptr::target_rule_summary(desc) +
                          (initial_data != nullptr && initial_data[0].data != nullptr ? ", initial_data=yes" : ", initial_data=no"));
    }
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

static void on_init_resource_view(reshade::api::device *device,
                                  reshade::api::resource resource,
                                  reshade::api::resource_usage usage_type,
                                  const reshade::api::resource_view_desc &desc,
                                  reshade::api::resource_view view)
{
    UNREFERENCED_PARAMETER(desc);
    std::scoped_lock lock(ptr::g_mutex);
    if (device == nullptr || ptr::handle_of(resource) == 0 || ptr::handle_of(view) == 0)
        return;

    const auto resource_desc = device->get_resource_desc(resource);
    ptr::g_views[ptr::handle_of(view)] = ptr::ViewRecord{resource, resource_desc, usage_type};
    if (ptr::has_target_sized_rule(resource_desc))
    {
        ptr::trace_target("target-sized resource view: view=" + std::to_string(ptr::handle_of(view)) +
                          ", res=" + std::to_string(ptr::handle_of(resource)) +
                          ", " + ptr::texture_desc_text(resource_desc, nullptr) +
                          ", usage=" + std::to_string(static_cast<uint32_t>(usage_type)) +
                          ", " + ptr::target_rule_summary(resource_desc));
    }
}

static void on_destroy_resource_view(reshade::api::device *device, reshade::api::resource_view view)
{
    UNREFERENCED_PARAMETER(device);
    std::scoped_lock lock(ptr::g_mutex);
    ptr::g_views.erase(ptr::handle_of(view));
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

static bool on_update_descriptor_tables(reshade::api::device *device,
                                        uint32_t count,
                                        const reshade::api::descriptor_table_update *updates)
{
    std::scoped_lock lock(ptr::g_mutex);
    if (device == nullptr || updates == nullptr)
        return false;

    for (uint32_t i = 0; i < count; ++i)
    {
        const auto &update = updates[i];
        const std::vector<uint64_t> targets = ptr::extract_target_views_from_descriptor_update(device, update);
        if (targets.empty())
            continue;

        const uint64_t table = ptr::handle_of(update.table);
        if (table != 0)
        {
            auto &stored = ptr::g_descriptor_table_targets[table];
            for (uint64_t view : targets)
                ptr::add_unique(stored, view);
        }

        ptr::trace_target("target-sized descriptor update: table=" + std::to_string(table) +
                          ", binding=" + std::to_string(update.binding) +
                          ", count=" + std::to_string(update.count) +
                          ", type=" + std::to_string(static_cast<uint32_t>(update.type)) +
                          ", textures=[" + ptr::target_views_text(targets) + "]");
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
    if (cmd_list == nullptr)
        return;
    reshade::api::device *device = cmd_list->get_device();
    std::vector<uint64_t> targets = ptr::extract_target_views_from_descriptor_update(device, update);
    if (targets.empty())
        return;

    ptr::remember_command_targets(cmd_list, targets);
    ptr::trace_target("target-sized push descriptors: layout_param=" + std::to_string(layout_param) +
                      ", binding=" + std::to_string(update.binding) +
                      ", count=" + std::to_string(update.count) +
                      ", type=" + std::to_string(static_cast<uint32_t>(update.type)) +
                      ", textures=[" + ptr::target_views_text(targets) + "]");
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
    if (cmd_list == nullptr || tables == nullptr)
        return;

    std::vector<uint64_t> targets;
    for (uint32_t i = 0; i < count; ++i)
    {
        const uint64_t table = ptr::handle_of(tables[i]);
        const auto it = ptr::g_descriptor_table_targets.find(table);
        if (it == ptr::g_descriptor_table_targets.end())
            continue;
        for (uint64_t view : it->second)
            ptr::add_unique(targets, view);
    }
    if (targets.empty())
        return;

    ptr::remember_command_targets(cmd_list, targets);
    ptr::trace_target("target-sized descriptor table bind: first=" + std::to_string(first) +
                      ", count=" + std::to_string(count) +
                      ", textures=[" + ptr::target_views_text(targets) + "]");
}

static void on_bind_render_targets_and_depth_stencil(reshade::api::command_list *cmd_list,
                                                     uint32_t count,
                                                     const reshade::api::resource_view *rtvs,
                                                     reshade::api::resource_view dsv)
{
    UNREFERENCED_PARAMETER(dsv);
    std::scoped_lock lock(ptr::g_mutex);
    if (cmd_list == nullptr)
        return;

    auto &state = ptr::g_command_states[ptr::handle_of(cmd_list)];
    state.has_render_target = false;
    state.render_target = {};

    if (count == 0 || rtvs == nullptr || ptr::handle_of(rtvs[0]) == 0)
        return;

    reshade::api::device *device = cmd_list->get_device();
    if (device == nullptr)
        return;

    const reshade::api::resource rt = device->get_resource_from_view(rtvs[0]);
    if (ptr::handle_of(rt) == 0)
        return;

    state.render_target = rt;
    state.render_target_desc = device->get_resource_desc(rt);
    state.has_render_target = true;

    if (ptr::has_target_sized_rule(state.render_target_desc))
    {
        ptr::trace_target("target-sized render target bind: rt=" + std::to_string(ptr::handle_of(rt)) +
                          ", " + ptr::texture_desc_text(state.render_target_desc, nullptr) +
                          ", " + ptr::target_rule_summary(state.render_target_desc));
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
    ptr::log_draw_targets("draw", cmd_list, vertex_count, instance_count);
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
    ptr::log_draw_targets("draw_indexed", cmd_list, index_count, instance_count);
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
        reshade::register_event<reshade::addon_event::init_resource_view>(&on_init_resource_view);
        reshade::register_event<reshade::addon_event::destroy_resource_view>(&on_destroy_resource_view);
        reshade::register_event<reshade::addon_event::update_texture_region>(&on_update_texture_region);
        reshade::register_event<reshade::addon_event::update_texture_region_command>(&on_update_texture_region_command);
        reshade::register_event<reshade::addon_event::copy_buffer_to_texture>(&on_copy_buffer_to_texture);
        reshade::register_event<reshade::addon_event::map_texture_region>(&on_map_texture_region);
        reshade::register_event<reshade::addon_event::unmap_texture_region>(&on_unmap_texture_region);
        reshade::register_event<reshade::addon_event::update_descriptor_tables>(&on_update_descriptor_tables);
        reshade::register_event<reshade::addon_event::push_descriptors>(&on_push_descriptors);
        reshade::register_event<reshade::addon_event::bind_descriptor_tables>(&on_bind_descriptor_tables);
        reshade::register_event<reshade::addon_event::bind_render_targets_and_depth_stencil>(&on_bind_render_targets_and_depth_stencil);
        reshade::register_event<reshade::addon_event::draw>(&on_draw);
        reshade::register_event<reshade::addon_event::draw_indexed>(&on_draw_indexed);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_addon(hinstDLL);
        break;
    }
    return TRUE;
}
