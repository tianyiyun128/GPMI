#pragma once

#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>

namespace gpmi
{
struct Rule
{
    bool enabled = false;
    std::string logical_path;
    std::string cache_key;
    std::string character_id;
    std::string outfit_id;
    std::string slot;
    std::string action;
    std::filesystem::path replacement;
};

class Manifest
{
public:
    void set_path(std::filesystem::path path);
    void reload_if_needed();
    std::optional<Rule> match(const std::string &logical_path);
    std::filesystem::path path() const;

private:
    void reload_locked();

    mutable std::mutex mutex_;
    std::filesystem::path path_;
    std::filesystem::file_time_type last_write_{};
    int revision_ = -1;
    std::unordered_map<std::string, Rule> rules_;
};

Manifest &manifest();
}
