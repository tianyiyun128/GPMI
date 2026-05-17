#pragma once

#include <Windows.h>

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace gpmi
{
struct ModuleRange
{
    std::uint8_t *base = nullptr;
    size_t size = 0;
};

struct Hook
{
    void *target = nullptr;
    void *detour = nullptr;
    void *trampoline = nullptr;
    size_t patch_size = 16;
    std::vector<std::uint8_t> original;
};

std::optional<ModuleRange> main_module_range();
std::optional<std::uintptr_t> parse_number(const std::string &text);
std::uint8_t *scan_pattern(ModuleRange range, const std::string &pattern);
bool install_hook(Hook &hook);
bool remove_hook(Hook &hook);
bool safe_copy(void *dst, const void *src, size_t size);
bool safe_read_ptr(const void *address, void **out);
std::string last_error_message(const char *action);
}
