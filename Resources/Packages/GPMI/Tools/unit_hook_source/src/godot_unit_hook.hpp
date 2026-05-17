#pragma once

#include <filesystem>

namespace gpmi
{
bool install_unit_hook(const std::filesystem::path &profile_dir,
                       const std::filesystem::path &dll_dir);
void remove_unit_hook();
}
