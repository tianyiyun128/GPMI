#include "config.hpp"
#include "log.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <regex>
#include <sstream>
#include <utility>

namespace ptr
{
ConfigStore &config_store()
{
    static ConfigStore store;
    return store;
}

static std::string trim(std::string s)
{
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = s.find_last_not_of(" \t\r\n");
    return s.substr(first, last - first + 1);
}

static bool to_bool(const std::string &s, bool fallback)
{
    std::string v = s;
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    if (v == "true" || v == "1" || v == "yes" || v == "on") return true;
    if (v == "false" || v == "0" || v == "no" || v == "off") return false;
    return fallback;
}

static std::string read_all(const std::filesystem::path &path)
{
    std::ifstream f(path, std::ios::binary);
    if (!f) return {};
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static uint64_t json_uint64_field(const std::string &json, const char *name, uint64_t fallback)
{
    const std::regex re(std::string("\\\"") + name + "\\\"\\s*:\\s*([0-9]+)");
    std::smatch m;
    if (std::regex_search(json, m, re))
        return std::stoull(m[1].str());
    return fallback;
}

bool ConfigStore::load(const std::filesystem::path &base_dir)
{
    Config fresh;
    fresh.base_dir = base_dir;
    config_ = std::move(fresh);

    load_ini(base_dir / "ptr_config.ini");
    log().open(config_.base_dir / config_.log_file);
    load_manifest_summary(config_.base_dir / config_.manifest_path);

    log().info("config loaded, mode=" + config_.mode +
               ", manifest=" + config_.manifest_path.string() +
               ", revision=" + std::to_string(config_.manifest_revision) +
               ", live_rules=" + std::to_string(config_.manifest_rules) +
               ", base=" + config_.base_dir.string());
    return true;
}

void ConfigStore::load_ini(const std::filesystem::path &path)
{
    std::ifstream f(path);
    if (!f)
        return;

    std::string section;
    std::string line;
    while (std::getline(f, line))
    {
        line = trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#') continue;
        if (line.front() == '[' && line.back() == ']')
        {
            section = trim(line.substr(1, line.size() - 2));
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        const auto key = trim(line.substr(0, eq));
        const auto value = trim(line.substr(eq + 1));
        if (section == "core")
        {
            if (key == "enabled") config_.enabled = to_bool(value, config_.enabled);
            else if (key == "mode") config_.mode = value;
            else if (key == "profile_dir")
            {
                auto profile = path.parent_path() / value;
                std::error_code ec;
                profile = std::filesystem::weakly_canonical(profile, ec);
                if (!ec) config_.base_dir = profile;
                else config_.base_dir = path.parent_path() / value;
            }
            else if (key == "min_width") config_.min_width = static_cast<uint32_t>(std::stoul(value));
            else if (key == "min_height") config_.min_height = static_cast<uint32_t>(std::stoul(value));
            else if (key == "manifest") config_.manifest_path = value;
            else if (key == "log_file") config_.log_file = value;
        }
    }
}

void ConfigStore::load_manifest_summary(const std::filesystem::path &path)
{
    const std::string json = read_all(path);
    if (json.empty())
    {
        log().warn("live manifest not found or empty: " + path.string());
        return;
    }

    config_.manifest_revision = json_uint64_field(json, "revision", 0);
    const auto rules_pos = json.find("\"rules\"");
    if (rules_pos == std::string::npos)
        return;
    const auto array_start = json.find('[', rules_pos);
    const auto array_end = json.find(']', array_start);
    if (array_start == std::string::npos || array_end == std::string::npos)
        return;

    uint32_t count = 0;
    for (size_t pos = array_start; pos < array_end; ++pos)
    {
        if (json[pos] == '{')
            ++count;
    }
    config_.manifest_rules = count;
}
}
