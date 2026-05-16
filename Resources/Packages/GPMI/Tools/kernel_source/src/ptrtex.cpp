#include "ptrtex.hpp"
#include <array>
#include <fstream>
#include <string>
#include <cstring>

namespace ptr
{
namespace
{
constexpr std::array<char, 8> MAGIC = {'P','T','R','T','E','X','0','1'};

struct DiskHeader
{
    char magic[8];
    uint32_t width;
    uint32_t height;
    uint32_t format;
    uint32_t row_pitch;
    uint32_t data_size;
};

bool is_bgra_format(reshade::api::format fmt)
{
    using reshade::api::format;
    return fmt == format::b8g8r8a8_unorm || fmt == format::b8g8r8a8_unorm_srgb;
}

void swizzle_rgba_to_bgra(const PtrTex &src, std::vector<uint8_t> &scratch)
{
    scratch.resize(static_cast<size_t>(src.height) * src.width * 4);
    for (uint32_t y = 0; y < src.height; ++y)
    {
        const uint8_t *in = src.pixels.data() + static_cast<size_t>(y) * src.row_pitch;
        uint8_t *out = scratch.data() + static_cast<size_t>(y) * src.width * 4;
        for (uint32_t x = 0; x < src.width; ++x)
        {
            out[x * 4 + 0] = in[x * 4 + 2];
            out[x * 4 + 1] = in[x * 4 + 1];
            out[x * 4 + 2] = in[x * 4 + 0];
            out[x * 4 + 3] = in[x * 4 + 3];
        }
    }
}

void swizzle_bgra_to_rgba(const PtrTex &src, std::vector<uint8_t> &scratch)
{
    // Same byte swap as rgba->bgra.
    swizzle_rgba_to_bgra(src, scratch);
}
}

bool load_ptrtex(const std::filesystem::path &path, PtrTex &out, std::string &error)
{
    std::ifstream f(path, std::ios::binary);
    if (!f)
    {
        error = "cannot open ptrtex: " + path.string();
        return false;
    }

    DiskHeader h{};
    f.read(reinterpret_cast<char *>(&h), sizeof(h));
    if (!f || std::memcmp(h.magic, MAGIC.data(), MAGIC.size()) != 0)
    {
        error = "bad ptrtex header: " + path.string();
        return false;
    }
    if (h.width == 0 || h.height == 0 || h.row_pitch < h.width * 4)
    {
        error = "bad ptrtex dimensions: " + path.string();
        return false;
    }
    if (h.format != static_cast<uint32_t>(PtrTexFormat::rgba8) && h.format != static_cast<uint32_t>(PtrTexFormat::bgra8))
    {
        error = "unsupported ptrtex format: " + path.string();
        return false;
    }

    PtrTex tex;
    tex.width = h.width;
    tex.height = h.height;
    tex.format = static_cast<PtrTexFormat>(h.format);
    tex.row_pitch = h.row_pitch;
    tex.pixels.resize(h.data_size);
    f.read(reinterpret_cast<char *>(tex.pixels.data()), tex.pixels.size());
    if (!f)
    {
        error = "truncated ptrtex: " + path.string();
        return false;
    }

    out = std::move(tex);
    return true;
}

bool save_ptrtex(const std::filesystem::path &path, const PtrTex &tex, std::string &error)
{
    if (tex.width == 0 || tex.height == 0 || tex.pixels.empty())
    {
        error = "empty texture";
        return false;
    }
    std::filesystem::create_directories(path.parent_path());
    std::ofstream f(path, std::ios::binary);
    if (!f)
    {
        error = "cannot write ptrtex: " + path.string();
        return false;
    }
    DiskHeader h{};
    std::memcpy(h.magic, MAGIC.data(), MAGIC.size());
    h.width = tex.width;
    h.height = tex.height;
    h.format = static_cast<uint32_t>(tex.format);
    h.row_pitch = tex.row_pitch;
    h.data_size = static_cast<uint32_t>(tex.pixels.size());
    f.write(reinterpret_cast<const char *>(&h), sizeof(h));
    f.write(reinterpret_cast<const char *>(tex.pixels.data()), tex.pixels.size());
    if (!f)
    {
        error = "write failed: " + path.string();
        return false;
    }
    return true;
}

bool make_subresource_for_format(const PtrTex &src,
                                 reshade::api::format dest_format,
                                 std::vector<uint8_t> &scratch,
                                 reshade::api::subresource_data &out)
{
    using reshade::api::format;
    const bool dest_bgra = is_bgra_format(dest_format);
    const bool src_bgra = src.format == PtrTexFormat::bgra8;

    if (dest_bgra == src_bgra && src.row_pitch == src.width * 4)
    {
        out.data = const_cast<uint8_t *>(src.pixels.data());
        out.row_pitch = src.row_pitch;
        out.slice_pitch = src.row_pitch * src.height;
        return true;
    }

    if (dest_bgra != src_bgra)
    {
        if (src_bgra)
            swizzle_bgra_to_rgba(src, scratch);
        else
            swizzle_rgba_to_bgra(src, scratch);
    }
    else
    {
        scratch.resize(static_cast<size_t>(src.width) * src.height * 4);
        for (uint32_t y = 0; y < src.height; ++y)
            std::memcpy(scratch.data() + static_cast<size_t>(y) * src.width * 4,
                        src.pixels.data() + static_cast<size_t>(y) * src.row_pitch,
                        src.width * 4);
    }

    out.data = scratch.data();
    out.row_pitch = src.width * 4;
    out.slice_pitch = src.width * src.height * 4;
    return true;
}
}
