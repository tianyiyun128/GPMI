#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace ptr
{
struct Config
{
    bool enabled = true;
    std::string mode = "godot_live_bridge";
    uint32_t min_width = 200;
    uint32_t min_height = 200;
    std::filesystem::path base_dir;
    std::filesystem::path manifest_path = "live_portraits.json";
    std::filesystem::path log_file = "PortraitHashReplace.log";
    uint64_t manifest_revision = 0;
    uint32_t manifest_rules = 0;
};

class ConfigStore
{
public:
    bool load(const std::filesystem::path &base_dir);
    const Config &current() const { return config_; }

private:
    void load_ini(const std::filesystem::path &path);
    void load_manifest_summary(const std::filesystem::path &path);
    Config config_;
};

ConfigStore &config_store();
}
