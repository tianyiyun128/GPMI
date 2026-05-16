#pragma once
#include <cstdint>
#include <string>
#include <reshade.hpp>

namespace ptr
{
constexpr uint64_t FNV_OFFSET = 14695981039346656037ull;
constexpr uint64_t FNV_PRIME  = 1099511628211ull;

uint64_t fnv1a_update(uint64_t state, const void *data, size_t size);
uint64_t hash_texture_upload(const reshade::api::resource_desc &desc,
                             const reshade::api::subresource_data &data,
                             uint32_t subresource,
                             const reshade::api::subresource_box *box);
std::string hash_to_hex(uint64_t hash);
bool parse_hash_hex(const std::string &text, uint64_t &out);

uint32_t bytes_per_pixel(reshade::api::format fmt);
bool is_supported_color_format(reshade::api::format fmt);
}
