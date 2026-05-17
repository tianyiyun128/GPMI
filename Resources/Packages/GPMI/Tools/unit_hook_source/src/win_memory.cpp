#include "win_memory.hpp"

#include <Psapi.h>

#include <cstring>
#include <sstream>

#pragma comment(lib, "Psapi.lib")

namespace gpmi
{
std::optional<ModuleRange> main_module_range()
{
    HMODULE module = GetModuleHandleW(nullptr);
    if (!module)
        return std::nullopt;

    MODULEINFO info{};
    if (!GetModuleInformation(GetCurrentProcess(), module, &info, sizeof(info)))
        return std::nullopt;

    return ModuleRange{
        static_cast<std::uint8_t *>(info.lpBaseOfDll),
        static_cast<size_t>(info.SizeOfImage),
    };
}

std::optional<std::uintptr_t> parse_number(const std::string &text)
{
    if (text.empty())
        return std::nullopt;
    try
    {
        size_t consumed = 0;
        const auto value = std::stoull(text, &consumed, 0);
        if (consumed != text.size())
            return std::nullopt;
        return static_cast<std::uintptr_t>(value);
    }
    catch (...)
    {
        return std::nullopt;
    }
}

std::uint8_t *scan_pattern(ModuleRange range, const std::string &pattern)
{
    std::vector<int> bytes;
    std::istringstream in(pattern);
    std::string token;
    while (in >> token)
    {
        if (token == "?" || token == "??")
        {
            bytes.push_back(-1);
            continue;
        }
        bytes.push_back(static_cast<int>(std::stoul(token, nullptr, 16) & 0xff));
    }

    if (bytes.empty() || bytes.size() > range.size)
        return nullptr;

    for (size_t offset = 0; offset <= range.size - bytes.size(); ++offset)
    {
        bool ok = true;
        for (size_t i = 0; i < bytes.size(); ++i)
        {
            if (bytes[i] >= 0 && range.base[offset + i] != static_cast<std::uint8_t>(bytes[i]))
            {
                ok = false;
                break;
            }
        }
        if (ok)
            return range.base + offset;
    }
    return nullptr;
}

bool safe_copy(void *dst, const void *src, size_t size)
{
    __try
    {
        std::memcpy(dst, src, size);
        return true;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return false;
    }
}

bool safe_read_ptr(const void *address, void **out)
{
    return safe_copy(out, address, sizeof(void *));
}

bool install_hook(Hook &hook)
{
    if (!hook.target || !hook.detour || hook.patch_size < 12)
        return false;

    auto *target = static_cast<std::uint8_t *>(hook.target);
    hook.original.resize(hook.patch_size);
    if (!safe_copy(hook.original.data(), target, hook.patch_size))
        return false;

    const size_t trampoline_size = hook.patch_size + 12;
    auto *trampoline = static_cast<std::uint8_t *>(
        VirtualAlloc(nullptr, trampoline_size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    if (!trampoline)
        return false;

    std::memcpy(trampoline, hook.original.data(), hook.patch_size);
    auto *cursor = trampoline + hook.patch_size;
    cursor[0] = 0x48;
    cursor[1] = 0xB8;
    *reinterpret_cast<void **>(cursor + 2) = target + hook.patch_size;
    cursor[10] = 0xFF;
    cursor[11] = 0xE0;

    DWORD old_protect = 0;
    if (!VirtualProtect(target, hook.patch_size, PAGE_EXECUTE_READWRITE, &old_protect))
    {
        VirtualFree(trampoline, 0, MEM_RELEASE);
        return false;
    }

    std::memset(target, 0x90, hook.patch_size);
    target[0] = 0x48;
    target[1] = 0xB8;
    *reinterpret_cast<void **>(target + 2) = hook.detour;
    target[10] = 0xFF;
    target[11] = 0xE0;

    FlushInstructionCache(GetCurrentProcess(), target, hook.patch_size);
    VirtualProtect(target, hook.patch_size, old_protect, &old_protect);
    hook.trampoline = trampoline;
    return true;
}

bool remove_hook(Hook &hook)
{
    if (!hook.target || hook.original.empty())
        return false;
    auto *target = static_cast<std::uint8_t *>(hook.target);
    DWORD old_protect = 0;
    if (!VirtualProtect(target, hook.original.size(), PAGE_EXECUTE_READWRITE, &old_protect))
        return false;
    std::memcpy(target, hook.original.data(), hook.original.size());
    FlushInstructionCache(GetCurrentProcess(), target, hook.original.size());
    VirtualProtect(target, hook.original.size(), old_protect, &old_protect);
    if (hook.trampoline)
        VirtualFree(hook.trampoline, 0, MEM_RELEASE);
    hook.trampoline = nullptr;
    return true;
}

std::string last_error_message(const char *action)
{
    const DWORD code = GetLastError();
    char *buffer = nullptr;
    FormatMessageA(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                   nullptr, code, 0, reinterpret_cast<char *>(&buffer), 0, nullptr);
    std::string message = action;
    message += " failed with Windows error ";
    message += std::to_string(code);
    if (buffer)
    {
        message += ": ";
        message += buffer;
        LocalFree(buffer);
    }
    return message;
}
}
