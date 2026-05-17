#include <Windows.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
constexpr int kVariantObject = 24;
constexpr std::uint64_t kObjectIdRefCountedBit = 1ull << 63;

HMODULE g_module = nullptr;
std::ofstream g_log;

struct RuntimeFunction
{
    std::uint32_t begin = 0;
    std::uint32_t end = 0;
    std::uint32_t unwind = 0;
};

struct Section
{
    std::string name;
    std::uint32_t va = 0;
    std::uint32_t virtual_size = 0;
    std::uint32_t raw_size = 0;
    std::uint32_t characteristics = 0;
};

struct ModuleView
{
    std::uint8_t *base = nullptr;
    std::uint32_t size = 0;
    std::uint64_t image_base = 0;
    std::vector<Section> sections;
    std::vector<RuntimeFunction> functions;
};

struct LoaderConfig
{
    std::uintptr_t image_load_from_file = 0;
    std::uintptr_t image_texture_create_from_image = 0;
    std::uintptr_t variant_clear = 0;
    bool verbose = false;
};

LoaderConfig g_config;
bool g_config_loaded = false;

std::string narrow(const std::filesystem::path &path)
{
    return path.u8string();
}

std::filesystem::path module_dir()
{
    wchar_t path[MAX_PATH * 4]{};
    GetModuleFileNameW(g_module, path, static_cast<DWORD>(std::size(path)));
    return std::filesystem::path(path).parent_path();
}

std::filesystem::path game_dir()
{
    wchar_t path[MAX_PATH * 4]{};
    GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
    return std::filesystem::path(path).parent_path();
}

std::filesystem::path profile_dir()
{
    wchar_t env[MAX_PATH * 4]{};
    const DWORD len = GetEnvironmentVariableW(L"GPMI_PROFILE_DIR", env, static_cast<DWORD>(std::size(env)));
    if (len > 0 && len < std::size(env))
        return std::filesystem::path(env);
    return game_dir() / L"GPMI";
}

void open_log()
{
    if (g_log.is_open())
        return;
    auto dir = profile_dir();
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    g_log.open(dir / "GPMITextureLoader.log", std::ios::app);
}

void log_line(const std::string &line)
{
    open_log();
    if (g_log.is_open())
    {
        g_log << line << '\n';
        g_log.flush();
    }
}

std::optional<std::uintptr_t> parse_number(const std::string &value)
{
    try
    {
        size_t idx = 0;
        std::uintptr_t parsed = 0;
        if (value.rfind("0x", 0) == 0 || value.rfind("0X", 0) == 0)
            parsed = static_cast<std::uintptr_t>(std::stoull(value, &idx, 16));
        else
            parsed = static_cast<std::uintptr_t>(std::stoull(value, &idx, 10));
        if (idx == value.size())
            return parsed;
    }
    catch (...)
    {
    }
    return std::nullopt;
}

std::unordered_map<std::string, std::string> read_ini(const std::filesystem::path &path)
{
    std::unordered_map<std::string, std::string> out;
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
            out[key] = value;
    }
    return out;
}

bool load_module_view(ModuleView &view)
{
    auto *base = reinterpret_cast<std::uint8_t *>(GetModuleHandleW(nullptr));
    if (!base)
        return false;
    auto *dos = reinterpret_cast<IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return false;
    auto *nt = reinterpret_cast<IMAGE_NT_HEADERS64 *>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
        return false;

    view.base = base;
    view.size = nt->OptionalHeader.SizeOfImage;
    view.image_base = nt->OptionalHeader.ImageBase;

    auto *sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i)
    {
        Section s;
        char name[9]{};
        std::memcpy(name, sec[i].Name, 8);
        s.name = name;
        s.va = sec[i].VirtualAddress;
        s.virtual_size = sec[i].Misc.VirtualSize;
        s.raw_size = sec[i].SizeOfRawData;
        s.characteristics = sec[i].Characteristics;
        view.sections.push_back(s);
    }

    const auto &pdata = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXCEPTION];
    if (pdata.VirtualAddress && pdata.Size >= sizeof(RuntimeFunction))
    {
        auto *rf = reinterpret_cast<RuntimeFunction *>(base + pdata.VirtualAddress);
        const size_t count = pdata.Size / sizeof(RuntimeFunction);
        for (size_t i = 0; i < count; ++i)
        {
            if (rf[i].begin && rf[i].begin < rf[i].end && rf[i].end < view.size)
                view.functions.push_back(rf[i]);
        }
        std::sort(view.functions.begin(), view.functions.end(), [](const auto &a, const auto &b) {
            return a.begin < b.begin;
        });
    }
    return true;
}

const Section *find_section(const ModuleView &view, const char *name)
{
    for (const auto &s : view.sections)
    {
        if (s.name == name)
            return &s;
    }
    return nullptr;
}

std::optional<std::uint32_t> rva_of_ptr(const ModuleView &view, const void *ptr)
{
    const auto p = reinterpret_cast<std::uintptr_t>(ptr);
    const auto b = reinterpret_cast<std::uintptr_t>(view.base);
    if (p < b || p >= b + view.size)
        return std::nullopt;
    return static_cast<std::uint32_t>(p - b);
}

std::optional<std::uint32_t> find_bytes_rva(const ModuleView &view, const std::string &needle)
{
    if (needle.empty())
        return std::nullopt;
    const auto *begin = reinterpret_cast<const char *>(view.base);
    const auto *end = begin + view.size;
    const auto it = std::search(begin, end, needle.begin(), needle.end());
    if (it == end)
        return std::nullopt;
    return static_cast<std::uint32_t>(it - begin);
}

std::optional<RuntimeFunction> function_for_rva(const ModuleView &view, std::uint32_t rva)
{
    auto it = std::upper_bound(view.functions.begin(), view.functions.end(), rva, [](std::uint32_t value, const RuntimeFunction &fn) {
        return value < fn.begin;
    });
    if (it == view.functions.begin())
        return std::nullopt;
    --it;
    if (it->begin <= rva && rva < it->end)
        return *it;
    return std::nullopt;
}

std::vector<std::uint32_t> find_rel32_xrefs(const ModuleView &view, std::uint32_t target_rva)
{
    std::vector<std::uint32_t> refs;
    const Section *text = find_section(view, ".text");
    if (!text)
        return refs;
    const auto text_size = text->virtual_size ? text->virtual_size : text->raw_size;
    const auto *bytes = view.base + text->va;
    for (std::uint32_t i = 0; i + 4 <= text_size; ++i)
    {
        std::int32_t disp = 0;
        std::memcpy(&disp, bytes + i, sizeof(disp));
        const auto src_rva = text->va + i;
        if (src_rva + 4 + disp == target_rva)
            refs.push_back(src_rva);
    }
    return refs;
}

std::optional<std::uintptr_t> locate_function_by_string(const ModuleView &view, const std::vector<std::string> &needles)
{
    std::unordered_map<std::uint32_t, int> scores;
    for (const auto &needle : needles)
    {
        auto str_rva = find_bytes_rva(view, needle);
        if (!str_rva)
            continue;
        for (auto ref_rva : find_rel32_xrefs(view, *str_rva))
        {
            auto fn = function_for_rva(view, ref_rva);
            if (fn)
                scores[fn->begin] += 1;
        }
    }
    if (scores.empty())
        return std::nullopt;
    auto best = std::max_element(scores.begin(), scores.end(), [](const auto &a, const auto &b) {
        return a.second < b.second;
    });
    return reinterpret_cast<std::uintptr_t>(view.base) + best->first;
}

void apply_config_value(const std::unordered_map<std::string, std::string> &ini,
                        const char *abs_key,
                        const char *rva_key,
                        std::uintptr_t &target,
                        std::uintptr_t module_base)
{
    if (auto it = ini.find(abs_key); it != ini.end())
    {
        if (auto parsed = parse_number(it->second))
            target = *parsed;
    }
    if (auto it = ini.find(rva_key); it != ini.end())
    {
        if (auto parsed = parse_number(it->second))
            target = module_base + *parsed;
    }
}

void load_config_once()
{
    if (g_config_loaded)
        return;
    g_config_loaded = true;

    ModuleView view;
    if (!load_module_view(view))
    {
        log_line("[GPMITextureLoader][ERROR] failed to parse main module");
        return;
    }
    const auto main_base = reinterpret_cast<std::uintptr_t>(view.base);

    for (const auto &path : {profile_dir() / "GPMITextureLoader.ini", module_dir() / "GPMITextureLoader.ini"})
    {
        if (!std::filesystem::is_regular_file(path))
            continue;
        const auto ini = read_ini(path);
        apply_config_value(ini, "image_load_from_file_abs", "image_load_from_file_rva", g_config.image_load_from_file, main_base);
        apply_config_value(ini, "image_texture_create_from_image_abs", "image_texture_create_from_image_rva", g_config.image_texture_create_from_image, main_base);
        apply_config_value(ini, "variant_clear_abs", "variant_clear_rva", g_config.variant_clear, main_base);
        if (auto it = ini.find("verbose"); it != ini.end())
            g_config.verbose = (it->second == "1" || it->second == "true" || it->second == "yes");
        log_line("[GPMITextureLoader] loaded config: " + narrow(path));
    }

    if (!g_config.image_load_from_file)
    {
        if (auto found = locate_function_by_string(view, {"Failed to load image. Error %d", "Loaded resource as image file"}))
        {
            g_config.image_load_from_file = *found;
            std::ostringstream out;
            out << "[GPMITextureLoader] auto-located Image::load_from_file rva=0x" << std::hex << (*found - main_base);
            log_line(out.str());
        }
    }

    if (!g_config.image_texture_create_from_image)
    {
        if (auto found = locate_function_by_string(view, {"Invalid image: null", "Invalid image: image is empty"}))
        {
            g_config.image_texture_create_from_image = *found;
            std::ostringstream out;
            out << "[GPMITextureLoader] auto-located ImageTexture::create_from_image rva=0x" << std::hex << (*found - main_base);
            log_line(out.str());
        }
    }
}

struct FakeGodotString
{
    void *string_object = nullptr;

    explicit FakeGodotString(const wchar_t *path)
    {
        if (!path)
            return;
        std::u32string text;
        for (const wchar_t *p = path; *p; ++p)
            text.push_back(static_cast<char32_t>(*p));

        const size_t chars = text.size();
        const size_t data_offset = 16;
        const size_t total = data_offset + (chars + 1) * sizeof(char32_t);
        auto *block = static_cast<std::uint8_t *>(std::calloc(1, total));
        if (!block)
            return;

        // CowData<char32_t> header: refcount, size, then data.
        *reinterpret_cast<std::uint64_t *>(block + 0) = 0x4000000000000000ull;
        *reinterpret_cast<std::uint64_t *>(block + 8) = static_cast<std::uint64_t>(chars);
        auto *data = reinterpret_cast<char32_t *>(block + data_offset);
        for (size_t i = 0; i < chars; ++i)
            data[i] = text[i];
        data[chars] = 0;

        auto *obj = static_cast<void **>(std::calloc(1, sizeof(void *)));
        if (!obj)
            return;
        *obj = data;
        string_object = obj;
        // Intentionally leaked. Godot may copy/reference the String during image
        // loading, so the backing CowData must not disappear immediately.
    }
};

bool call_sret_1(std::uintptr_t fn_addr, void *ret, const void *arg)
{
    using Fn = void(__fastcall *)(void *, const void *);
    const auto fn = reinterpret_cast<Fn>(fn_addr);
    __try
    {
        fn(ret, arg);
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}

void call_variant_clear(void *variant)
{
    if (!g_config.variant_clear || !variant)
        return;
    using Fn = void(__fastcall *)(void *);
    const auto fn = reinterpret_cast<Fn>(g_config.variant_clear);
    __try
    {
        fn(variant);
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
    }
}

void write_object_variant(void *variant, void *object)
{
    if (!variant || !object)
        return;
    std::memset(variant, 0, 24);
    *reinterpret_cast<std::int32_t *>(static_cast<std::uint8_t *>(variant) + 0) = kVariantObject;
    const auto id = kObjectIdRefCountedBit | (reinterpret_cast<std::uint64_t>(object) & 0x7fffffffffffffffull);
    *reinterpret_cast<std::uint64_t *>(static_cast<std::uint8_t *>(variant) + 8) = id;
    *reinterpret_cast<void **>(static_cast<std::uint8_t *>(variant) + 16) = object;
}
}

extern "C" __declspec(dllexport) bool __fastcall GPMI_LoadTextureVariant(void *out_variant, const wchar_t *replacement_path)
{
    load_config_once();
    if (!out_variant || !replacement_path)
    {
        log_line("[GPMITextureLoader][ERROR] invalid arguments");
        return false;
    }
    if (!g_config.image_load_from_file || !g_config.image_texture_create_from_image)
    {
        log_line("[GPMITextureLoader][ERROR] missing Godot ABI addresses for Image::load_from_file or ImageTexture::create_from_image");
        return false;
    }

    FakeGodotString path(replacement_path);
    if (!path.string_object)
    {
        log_line("[GPMITextureLoader][ERROR] failed to build Godot String for replacement path");
        return false;
    }

    alignas(16) std::array<std::uint8_t, 32> image_ref{};
    alignas(16) std::array<std::uint8_t, 32> texture_ref{};

    if (!call_sret_1(g_config.image_load_from_file, image_ref.data(), path.string_object))
    {
        log_line("[GPMITextureLoader][ERROR] Image::load_from_file crashed");
        return false;
    }
    void *image_object = *reinterpret_cast<void **>(image_ref.data());
    if (!image_object)
    {
        log_line("[GPMITextureLoader][ERROR] Image::load_from_file returned null Ref<Image>");
        return false;
    }

    if (!call_sret_1(g_config.image_texture_create_from_image, texture_ref.data(), image_ref.data()))
    {
        log_line("[GPMITextureLoader][ERROR] ImageTexture::create_from_image crashed");
        return false;
    }
    void *texture_object = *reinterpret_cast<void **>(texture_ref.data());
    if (!texture_object)
    {
        log_line("[GPMITextureLoader][ERROR] ImageTexture::create_from_image returned null Ref<ImageTexture>");
        return false;
    }

    call_variant_clear(out_variant);
    write_object_variant(out_variant, texture_object);

    if (g_config.verbose)
        log_line("[GPMITextureLoader] replaced Variant with ImageTexture object");
    return true;
}

extern "C" __declspec(dllexport) unsigned __int64 __cdecl GPMI_TextureLoaderAbiVersion()
{
    return 1;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH)
    {
        g_module = instance;
        DisableThreadLibraryCalls(instance);
        open_log();
        log_line("[GPMITextureLoader] loaded");
    }
    else if (reason == DLL_PROCESS_DETACH)
    {
        log_line("[GPMITextureLoader] unloaded");
        if (g_log.is_open())
            g_log.close();
    }
    return TRUE;
}
