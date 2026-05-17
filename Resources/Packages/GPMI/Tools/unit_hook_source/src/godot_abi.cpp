#include "godot_abi.hpp"

#include "log.hpp"
#include "win_memory.hpp"

#include <Windows.h>

#include <array>
#include <cctype>
#include <cstring>
#include <fstream>
#include <iomanip>
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

bool parse_bool_value(const std::string &value)
{
    std::string lowered;
    lowered.reserve(value.size());
    for (char ch : value)
        lowered.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    return lowered == "1" || lowered == "true" || lowered == "yes" || lowered == "on";
}

std::optional<std::string> scan_ascii_token_near(const void *base, size_t bytes, const std::string &needle)
{
    if (needle.empty())
        return std::nullopt;
    std::vector<unsigned char> buffer(bytes);
    if (!safe_copy(buffer.data(), base, buffer.size()))
        return std::nullopt;
    for (size_t i = 0; i + needle.size() <= buffer.size(); ++i)
    {
        if (std::memcmp(buffer.data() + i, needle.data(), needle.size()) == 0)
            return needle;
    }
    return std::nullopt;
}

std::optional<std::string> scan_utf32_token_near(const void *base, size_t bytes, const std::string &needle)
{
    if (needle.empty())
        return std::nullopt;
    std::vector<unsigned char> buffer(bytes);
    if (!safe_copy(buffer.data(), base, buffer.size()))
        return std::nullopt;
    std::vector<unsigned char> utf32;
    utf32.reserve(needle.size() * 4);
    for (char ch : needle)
    {
        utf32.push_back(static_cast<unsigned char>(ch));
        utf32.push_back(0);
        utf32.push_back(0);
        utf32.push_back(0);
    }
    for (size_t i = 0; i + utf32.size() <= buffer.size(); ++i)
    {
        if (std::memcmp(buffer.data() + i, utf32.data(), utf32.size()) == 0)
            return needle;
    }
    return std::nullopt;
}

std::optional<std::string> read_ascii_c_string(const void *address, size_t max_chars)
{
    std::vector<char> buffer(max_chars + 1);
    if (!safe_copy(buffer.data(), address, max_chars))
        return std::nullopt;
    buffer[max_chars] = 0;
    size_t len = 0;
    while (len < max_chars && buffer[len])
        ++len;
    if (len == 0 || len == max_chars)
        return std::nullopt;
    std::string value(buffer.data(), len);
    if (!is_printable_ascii(value))
        return std::nullopt;
    return value;
}

std::optional<std::string> read_utf32_c_string(const void *address, size_t max_chars)
{
    std::vector<char32_t> buffer(max_chars + 1);
    if (!safe_copy(buffer.data(), address, max_chars * sizeof(char32_t)))
        return std::nullopt;
    buffer[max_chars] = 0;
    std::string value = read_utf32_string(buffer.data(), max_chars);
    if (!is_printable_ascii(value))
        return std::nullopt;
    return value;
}

std::optional<std::string> scan_printable_string_near(const void *base, size_t bytes)
{
    std::vector<unsigned char> buffer(bytes);
    if (!safe_copy(buffer.data(), base, buffer.size()))
        return std::nullopt;

    for (size_t i = 0; i + 2 < buffer.size(); ++i)
    {
        if (!std::isprint(buffer[i]))
            continue;
        size_t j = i;
        while (j < buffer.size() && std::isprint(buffer[j]))
            ++j;
        const size_t len = j - i;
        if (len >= 2 && len <= 64)
        {
            std::string value(reinterpret_cast<const char *>(buffer.data() + i), len);
            if (is_printable_ascii(value))
                return value;
        }
        i = j;
    }
    return std::nullopt;
}

std::optional<std::string> scan_string_name_for_token(const void *string_name, const std::string &token)
{
    if (!string_name)
        return std::nullopt;

    void *data = nullptr;
    if (safe_read_ptr(string_name, &data) && data)
    {
        if (auto value = scan_ascii_token_near(data, 0x200, token))
            return value;
        if (auto value = scan_utf32_token_near(data, 0x400, token))
            return value;
    }

    if (auto value = scan_ascii_token_near(string_name, 0x40, token))
        return value;
    if (auto value = scan_utf32_token_near(string_name, 0x80, token))
        return value;
    return std::nullopt;
}

std::optional<std::string> try_string_name_to_ascii(const void *string_name)
{
    if (!string_name)
        return std::nullopt;

    void *data = nullptr;
    if (!safe_read_ptr(string_name, &data) || !data)
        return std::nullopt;

    // Godot 4.3 StringName is a single _Data*.
    // _Data layout begins with:
    //   SafeRefCount refcount; SafeNumeric<uint32_t> static_count;
    //   const char *cname; String name;
    // On Windows x64 this places cname at +8 and String's CowData pointer at +16.
    void *cname = nullptr;
    if (safe_read_ptr(static_cast<const unsigned char *>(data) + 8, &cname) && cname)
    {
        if (auto value = read_ascii_c_string(cname, 128))
            return value;
    }

    void *string_ptr = nullptr;
    if (safe_read_ptr(static_cast<const unsigned char *>(data) + 16, &string_ptr) && string_ptr)
    {
        if (auto value = read_utf32_c_string(string_ptr, 128))
            return value;
    }

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
        if (auto ascii = read_utf32_c_string(candidate, 128))
            return ascii;
        if (auto ascii = read_ascii_c_string(candidate, 128))
            return ascii;
        if (auto ascii = scan_printable_string_near(candidate, 0x200))
            return ascii;
    }
    return std::nullopt;
}

std::string hex_bytes(const unsigned char *data, size_t size)
{
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (size_t i = 0; i < size; ++i)
    {
        if (i)
            out << ' ';
        out << std::setw(2) << static_cast<unsigned int>(data[i]);
    }
    return out.str();
}

std::string ptr_to_hex(const void *ptr)
{
    std::ostringstream out;
    out << "0x" << std::hex << reinterpret_cast<std::uintptr_t>(ptr);
    return out.str();
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
        "41 57 41 56 41 55 41 54 55 57 56 53 48 81 EC 88 00 00 00";

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
        const auto probe_only = values.find("probe_only");
        if (probe_only != values.end())
            cfg.probe_only = parse_bool_value(probe_only->second);
        const auto verbose_calls = values.find("verbose_calls");
        if (verbose_calls != values.end())
            cfg.verbose_calls = parse_bool_value(verbose_calls->second);
        log().info("loaded ABI config: " + path.string());
    }
    return cfg;
}

std::optional<std::string> try_decode_method_name(const void *method)
{
    return try_string_name_to_ascii(method);
}

std::string describe_string_name_for_log(const void *string_name)
{
    if (!string_name)
        return "<null>";

    std::array<unsigned char, 64> bytes{};
    std::ostringstream out;
    out << ptr_to_hex(string_name);
    if (!safe_copy(bytes.data(), string_name, bytes.size()))
        return out.str() + " <unreadable>";

    out << " bytes=" << hex_bytes(bytes.data(), 32);
    if (auto decoded = try_decode_method_name(string_name))
        out << " decoded=\"" << *decoded << "\"";

    void *data = nullptr;
    if (safe_read_ptr(string_name, &data) && data)
    {
        std::array<unsigned char, 64> data_bytes{};
        if (safe_copy(data_bytes.data(), data, data_bytes.size()))
            out << " data=" << ptr_to_hex(data) << " data_bytes=" << hex_bytes(data_bytes.data(), 40);
        void *cname = nullptr;
        if (safe_read_ptr(static_cast<const unsigned char *>(data) + 8, &cname) && cname)
        {
            out << " cname=" << ptr_to_hex(cname);
            if (auto value = read_ascii_c_string(cname, 128))
                out << " cname_value=\"" << *value << "\"";
        }
        void *name_ptr = nullptr;
        if (safe_read_ptr(static_cast<const unsigned char *>(data) + 16, &name_ptr) && name_ptr)
        {
            out << " name_ptr=" << ptr_to_hex(name_ptr);
            if (auto value = read_utf32_c_string(name_ptr, 128))
                out << " name_value=\"" << *value << "\"";
        }
    }

    for (size_t off = 0; off + sizeof(void *) <= bytes.size(); off += sizeof(void *))
    {
        void *candidate = *reinterpret_cast<void **>(bytes.data() + off);
        if (!candidate)
            continue;
        std::string found;
        if (auto value = read_utf32_c_string(candidate, 96))
            found = *value;
        else if (auto value = read_ascii_c_string(candidate, 96))
            found = *value;
        else if (auto value = scan_printable_string_near(candidate, 0x100))
            found = *value;

        if (!found.empty())
            out << " ptr+" << off << "=" << ptr_to_hex(candidate) << " -> \"" << found << "\"";
    }
    return out.str();
}

std::string describe_variant_for_log(const void *variant)
{
    if (!variant)
        return "<null>";

    std::array<unsigned char, 80> bytes{};
    if (!safe_copy(bytes.data(), variant, bytes.size()))
        return ptr_to_hex(variant) + " <unreadable>";

    std::ostringstream out;
    const int type_i32 = *reinterpret_cast<const int *>(bytes.data());
    const std::uint64_t type_u64 = *reinterpret_cast<const std::uint64_t *>(bytes.data());
    out << ptr_to_hex(variant)
        << " type_i32=" << type_i32
        << " type_u64=" << type_u64
        << " bytes=" << hex_bytes(bytes.data(), 48);

    if (auto inline_utf32 = read_utf32_c_string(static_cast<const unsigned char *>(variant) + 8, 96))
        out << " inline_utf32=\"" << *inline_utf32 << "\"";
    if (auto inline_ascii = read_ascii_c_string(static_cast<const unsigned char *>(variant) + 8, 96))
        out << " inline_ascii=\"" << *inline_ascii << "\"";

    for (size_t off = 0; off + sizeof(void *) <= bytes.size(); off += sizeof(void *))
    {
        void *candidate = *reinterpret_cast<void **>(bytes.data() + off);
        if (!candidate)
            continue;
        std::string found;
        if (auto value = read_utf32_c_string(candidate, 96))
            found = *value;
        else if (auto value = read_ascii_c_string(candidate, 96))
            found = *value;
        else if (auto value = scan_printable_string_near(candidate, 0x100))
            found = *value;

        if (!found.empty())
            out << " ptr+" << off << "=" << ptr_to_hex(candidate) << " -> \"" << found << "\"";
    }

    if (auto decoded = try_variant_string(variant))
        out << " decoded_string=\"" << *decoded << "\"";
    if (auto decoded_bool = try_variant_bool(variant))
        out << " decoded_bool=" << (*decoded_bool ? "true" : "false");
    return out.str();
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
