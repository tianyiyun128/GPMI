#pragma once
#include "ptrtex.hpp"
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace ptr
{
struct Rule
{
    bool enabled = true;
    uint64_t hash = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t gpu_format = 0;
    std::string hash_variant;
    std::filesystem::path replacement;
    std::string note;
    std::string character_id;
    std::string outfit_id = "default";
    std::string slot;
    mutable std::optional<PtrTex> cached_texture;
};

struct Config
{
    bool enabled = true;
    uint32_t min_width = 32;
    uint32_t min_height = 32;
    std::filesystem::path base_dir;
    std::filesystem::path hash_db_path = "runtime_hash_db.json";
    std::filesystem::path log_file = "PortraitHashReplace.log";
    std::unordered_map<uint64_t, Rule> rules;
};

class ConfigStore
{
public:
    bool load(const std::filesystem::path &base_dir);
    const Config &current() const { return config_; }
    Config &current() { return config_; }
    const Rule *find(uint64_t hash) const;

private:
    void load_ini(const std::filesystem::path &path);
    void load_hash_db(const std::filesystem::path &path);
    Config config_;
};

ConfigStore &config_store();
}