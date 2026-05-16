#pragma once
#include <cstdint>
#include <filesystem>
#include <vector>
#include <reshade.hpp>

namespace ptr
{
enum class PtrTexFormat : uint32_t
{
    rgba8 = 1,
    bgra8 = 2,
};

struct PtrTex
{
    uint32_t width = 0;
    uint32_t height = 0;
    PtrTexFormat format = PtrTexFormat::rgba8;
    uint32_t row_pitch = 0;
    std::vector<uint8_t> pixels;
};

bool load_ptrtex(const std::filesystem::path &path, PtrTex &out, std::string &error);
bool save_ptrtex(const std::filesystem::path &path, const PtrTex &tex, std::string &error);

bool make_subresource_for_format(const PtrTex &src,
                                 reshade::api::format dest_format,
                                 std::vector<uint8_t> &scratch,
                                 reshade::api::subresource_data &out);
}
