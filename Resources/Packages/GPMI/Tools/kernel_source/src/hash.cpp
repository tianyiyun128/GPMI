#include "hash.hpp"
#include <algorithm>
#include <charconv>
#include <iomanip>
#include <sstream>
#include <cstring>

namespace ptr
{
uint64_t fnv1a_update(uint64_t state, const void *data, size_t size)
{
    const auto *bytes = static_cast<const uint8_t *>(data);
    for (size_t i = 0; i < size; ++i)
    {
        state ^= bytes[i];
        state *= FNV_PRIME;
    }
    return state;
}

uint32_t bytes_per_pixel(reshade::api::format fmt)
{
    using reshade::api::format;
    switch (fmt)
    {
    case format::r8g8b8a8_unorm:
    case format::r8g8b8a8_unorm_srgb:
    case format::b8g8r8a8_unorm:
    case format::b8g8r8a8_unorm_srgb:
        return 4;
    default:
        return 0;
    }
}

bool is_supported_color_format(reshade::api::format fmt)
{
    return bytes_per_pixel(fmt) != 0;
}

static uint32_t box_width(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->right > box->left)
        return box->right - box->left;
    return desc.texture.width;
}

static uint32_t box_height(const reshade::api::resource_desc &desc, const reshade::api::subresource_box *box)
{
    if (box != nullptr && box->bottom > box->top)
        return box->bottom - box->top;
    return desc.texture.height;
}

uint64_t hash_texture_upload(const reshade::api::resource_desc &desc,
                             const reshade::api::subresource_data &data,
                             uint32_t subresource,
                             const reshade::api::subresource_box *box)
{
    const uint32_t bpp = bytes_per_pixel(desc.texture.format);
    if (data.data == nullptr || bpp == 0)
        return 0;

    const uint32_t width = box_width(desc, box);
    const uint32_t height = box_height(desc, box);
    const uint32_t row_bytes = width * bpp;
    if (width == 0 || height == 0 || data.row_pitch < row_bytes)
        return 0;

    uint64_t state = FNV_OFFSET;
    const uint64_t header[] = {
        static_cast<uint64_t>(desc.texture.width),
        static_cast<uint64_t>(desc.texture.height),
        static_cast<uint64_t>(desc.texture.format),
        static_cast<uint64_t>(subresource),
        static_cast<uint64_t>(width),
        static_cast<uint64_t>(height),
        static_cast<uint64_t>(bpp)
    };
    state = fnv1a_update(state, header, sizeof(header));

    const auto *base = static_cast<const uint8_t *>(data.data);
    for (uint32_t y = 0; y < height; ++y)
        state = fnv1a_update(state, base + static_cast<size_t>(y) * data.row_pitch, row_bytes);

    return state;
}

std::string hash_to_hex(uint64_t hash)
{
    std::ostringstream ss;
    ss << "0x" << std::hex << std::setw(16) << std::setfill('0') << std::nouppercase << hash;
    return ss.str();
}

bool parse_hash_hex(const std::string &text, uint64_t &out)
{
    std::string s = text;
    s.erase(std::remove_if(s.begin(), s.end(), ::isspace), s.end());
    if (s.rfind("0x", 0) == 0 || s.rfind("0X", 0) == 0)
        s = s.substr(2);
    if (s.empty())
        return false;
    uint64_t value = 0;
    auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), value, 16);
    if (ec != std::errc() || ptr != s.data() + s.size())
        return false;
    out = value;
    return true;
}
}
