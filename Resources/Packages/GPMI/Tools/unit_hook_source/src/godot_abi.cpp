#include "godot_abi.hpp"

#include "log.hpp"
#include "win_memory.hpp"

#include <Windows.h>

#include <array>
#include <cctype>
#include <cstring>
#include <fstream>
#include <sstream>
#include <unordered_map>
#include <vector>

namespace gpmi
{
namespace
{
std::uintptr_t g_texture_loader = 0;

std::unordered_map<std::string, std::string> read_ini(const std::filesystem::path &path)
{
    std::unordered_map<std::string, std::string> values;
    std::ifstream file(path);
    std::string line;
    while (std::getline(file, line))
    {
        const auto comment = line.find_first_of(";#");
        if (comment != std::string::npos)
            line.resize(comment);
        const auto eq = line.find('=');
        if (eq == std::string::npos)
            continue;
        auto key = line.substr(0, eq);
        auto value = line.substr(eq + 1);
        auto trim = [](std::string &s) {
            while (!s.empty() && std::isspace(static_cast<unsigned char>(s.front())))
                s.erase(s.begin());
            while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back())))
                s.pop_back();
        };
        trim(key);
        trim(value);
        if (!key.empty())
            values[key] = value;
    }
    return values;
}

std::string read_utf32_string(const char32_t *data, size_t max_chars)
{
    std::string out;
    for (size_t i = 0; i < max_chars; ++i)
    {
        const char32_t ch = data[i];
        if (ch == 0)
            break;
        if (ch > 0x7f)
            return {};
        out.push_back(static_cast<char>(ch));
    }
    return out;
}

bool is_printable_ascii(const std::string &value)
{
    if (value.empty() || value.size() > 128)
        return false;
    for (char ch : value)
    {
        if (static_cast<unsigned char>(ch) < 0x20 || static_cast<unsigned char>(ch) > 0x7e)
            return false;
    }
    return true;
}

std::optional<std::string> scan_ascii_near(const void *base, size_t bytes)
{
    std::vector<unsigned char> buffer(bytes);
    if (!safe_copy(buffer.data(), base, buffer.size()))
        return std::nullopt;
    for (size_t i = 0; i + 4 < buffer.size(); ++i)
    {
        if (std::memcmp(buffer.data() + i, "unit", 4) == 0)
            return std::string("unit");
    }
    return std::nullopt;
}

std::optional<std::string> scan_utf32_near(const void *base, size_t bytes)
{
    std::vector<unsigned char> buffer(bytes);
    if (!safe_copy(buffer.data(), base, buffer.size()))
        return std::nullopt;
    const unsigned char needle[] = {'u', 0, 0, 0, 'n', 0, 0, 0, 'i', 0, 0, 0, 't', 0, 0, 0};
    for (size_t i = 0; i + sizeof(needle) <= buffer.size(); ++i)
    {
        if (std::memcmp(buffer.data() + i, needle, sizeof(needle)) == 0)
            return std::string("unit");
    }
    return std::nullopt;
}

std::optional<std::string> try_string_name_to_ascii(const void *string_name)
{
    if (!string_name)
        return std::nullopt;

    void *data = nullptr;
    if (safe_read_ptr(string_name, &data) && data)
    {
        if (auto value = scan_ascii_near(data, 0x200))
            return value;
        if (auto value = scan_utf32_near(data, 0x400))
            return value;
    }

    if (auto value = scan_ascii_near(string_name, 0x40))
        return value;
    if (auto value = scan_utf32_near(string_name, 0x80))
        return value;
    return std::nullopt;
}

std::optional<std::string> try_variant_string(const void *variant)
{
    if (!variant)
        return std::nullopt;

    std::array<unsigned char, 64> bytes{};
    if (!safe_copy(bytes.data(), variant, bytes.size()))
        return std::nullopt;

    // Godot 4 Variant stores the type tag near the front. String is normally 4,
    // StringName is normally 21. This decoder is deliberately defensive because
    // the exact private layout is not ABI-stable.
    const int type_tag = *reinterpret_cast<const int *>(bytes.data());
    if (type_tag != 4 && type_tag != 21)
        return std::nullopt;

    for (size_t off = 8; off + sizeof(void *) <= bytes.size(); off += sizeof(void *))
    {
        void *candidate = *reinterpret_cast<void **>(bytes.data() + off);
        if (!candidate)
            continue;
        if (auto ascii = scan_ascii_near(candidate, 0x100); ascii && is_printable_ascii(*ascii))
            return ascii;
        if (auto ascii = scan_utf32_near(candidate, 0x200); ascii && is_printable_ascii(*ascii))
            return ascii;
    }
    return std::nullopt;
}

std::optional<bool> try_variant_bool(const void *variant)
{
    if (!variant)
        return std::nullopt;
    std::array<unsigned char, 32> bytes{};
    if (!safe_copy(bytes.data(), variant, bytes.size()))
        return std::nullopt;
    const int type_tag = *reinterpret_cast<const int *>(bytes.data());
    if (type_tag != 1)
        return std::nullopt;
    return bytes[8] != 0;
}

bool call_texture_loader_seh(std::uintptr_t loader_address, void *return_variant, const wchar_t *path)
{
    using LoaderFn = bool(__fastcall *)(void *, const wchar_t *);
    const auto loader = reinterpret_cast<LoaderFn>(loader_address);
    __try
    {
        return loader(return_variant, path);
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}
}

GodotAbiConfig load_abi_config(const std::filesystem::path &profile_dir,
                               const std::filesystem::path &dll_dir)
{
    GodotAbiConfig cfg;
    cfg.object_callp_pattern =
        "48 89 5C 24 ?? 48 89 6C 24 ?? 48 89 74 24 ?? 57 48 83 EC ?? 49 8B";

    for (const auto &path : {profile_dir / "GPMIUnitHook.ini", dll_dir / "GPMIUnitHook.ini"})
    {
        if (!std::filesystem::is_regular_file(path))
            continue;
        const auto values = read_ini(path);
        auto set_number = [&](const char *key, std::uintptr_t &target) {
            const auto it = values.find(key);
            if (it != values.end())
            {
                if (auto parsed = parse_number(it->second))
                    target = *parsed;
            }
        };
        set_number("object_callp_abs", cfg.object_callp);
        set_number("texture_loader_abs", cfg.texture_loader);
        const auto rva = values.find("object_callp_rva");
        if (rva != values.end())
        {
            if (auto parsed = parse_number(rva->second))
            {
                if (auto range = main_module_range())
                    cfg.object_callp = reinterpret_cast<std::uintptr_t>(range->base) + *parsed;
            }
        }
        const auto loader_rva = values.find("texture_loader_rva");
        if (loader_rva != values.end())
        {
            if (auto parsed = parse_number(loader_rva->second))
            {
                if (auto range = main_module_range())
                    cfg.texture_loader = reinterpret_cast<std::uintptr_t>(range->base) + *parsed;
            }
        }
        const auto pattern = values.find("object_callp_pattern");
        if (pattern != values.end() && !pattern->second.empty())
            cfg.object_callp_pattern = pattern->second;
        const auto patch_size = values.find("object_callp_patch_size");
        if (patch_size != values.end())
        {
            if (auto parsed = parse_number(patch_size->second))
                cfg.object_callp_patch_size = static_cast<size_t>(*parsed);
        }
        log().info("loaded ABI config: " + path.string());
    }
    return cfg;
}

std::optional<UnitCall> try_decode_unit_call(void *, const void *method, const void **args, int arg_count)
{
    if (arg_count < 2 || args == nullptr)
        return std::nullopt;

    auto method_name = try_string_name_to_ascii(method);
    if (!method_name || *method_name != "unit")
        return std::nullopt;

    UnitCall call;
    call.method_name = *method_name;
    if (auto type = try_variant_string(args[0]))
        call.type = *type;
    if (auto action = try_variant_string(args[1]))
        call.action = *action;
    if (arg_count >= 3)
    {
        if (auto high = try_variant_bool(args[2]))
            call.high_resolution = *high;
    }

    if (call.type.empty() || call.action.empty())
        return std::nullopt;

    call.logical_path = std::string(call.high_resolution ? "Unit_H/" : "Unit/") +
                        call.type + "_" + call.action;
    call.valid = true;
    return call;
}

void set_texture_loader(std::uintptr_t address)
{
    g_texture_loader = address;
}

bool replace_return_with_texture(void *return_variant, const std::filesystem::path &replacement)
{
    if (!g_texture_loader)
    {
        log().warn("matched unit call but texture_loader_rva/abs is not configured; cannot replace return yet");
        return false;
    }

    const bool ok = call_texture_loader_seh(g_texture_loader, return_variant, replacement.c_str());
    if (!ok)
        log().error("texture loader thunk crashed while loading: " + replacement.string());
    return ok;
}
}
