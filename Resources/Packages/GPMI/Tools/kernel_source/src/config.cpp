#include "config.hpp"
#include "hash.hpp"
#include "log.hpp"
#include <algorithm>
#include <fstream>
#include <regex>
#include <sstream>

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
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char c){ return static_cast<char>(std::tolower(c)); });
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

static std::string json_string_field(const std::string &object, const char *name, const std::string &fallback = {})
{
    const std::regex re(std::string("\\\"") + name + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    std::smatch m;
    if (std::regex_search(object, m, re))
        return m[1].str();
    return fallback;
}

static bool json_bool_field(const std::string &object, const char *name, bool fallback)
{
    const std::regex re(std::string("\\\"") + name + "\\\"\\s*:\\s*(true|false|1|0)", std::regex::icase);
    std::smatch m;
    if (std::regex_search(object, m, re))
        return to_bool(m[1].str(), fallback);
    return fallback;
}

static uint32_t json_uint_field(const std::string &object, const char *name, uint32_t fallback)
{
    const std::regex re(std::string("\\\"") + name + "\\\"\\s*:\\s*([0-9]+)");
    std::smatch m;
    if (std::regex_search(object, m, re))
        return static_cast<uint32_t>(std::stoul(m[1].str()));
    return fallback;
}

bool ConfigStore::load(const std::filesystem::path &base_dir)
{
    Config fresh;
    fresh.base_dir = base_dir;
    config_ = std::move(fresh);

    load_ini(base_dir / "ptr_config.ini");
    log().open(config_.base_dir / config_.log_file);
    load_hash_db(config_.base_dir / config_.hash_db_path);

    log().info("config loaded, rules=" + std::to_string(config_.rules.size()) + ", base=" + config_.base_dir.string());
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
            else if (key == "hash_db") config_.hash_db_path = value;
            else if (key == "log_file") config_.log_file = value;
        }
    }
}

void ConfigStore::load_hash_db(const std::filesystem::path &path)
{
    const std::string json = read_all(path);
    if (json.empty())
    {
        log().warn("hash_db not found or empty: " + path.string());
        return;
    }

    config_.enabled = json_bool_field(json, "enabled", config_.enabled);
    config_.min_width = json_uint_field(json, "min_width", config_.min_width);
    config_.min_height = json_uint_field(json, "min_height", config_.min_height);

    const auto rules_pos = json.find("\"rules\"");
    if (rules_pos == std::string::npos)
        return;
    const auto array_start = json.find('[', rules_pos);
    const auto array_end = json.find(']', array_start);
    if (array_start == std::string::npos || array_end == std::string::npos)
        return;

    const std::string array = json.substr(array_start + 1, array_end - array_start - 1);
    size_t pos = 0;
    while ((pos = array.find('{', pos)) != std::string::npos)
    {
        const auto end = array.find('}', pos);
        if (end == std::string::npos) break;
        const std::string object = array.substr(pos, end - pos + 1);
        pos = end + 1;

        Rule rule;
        rule.enabled = json_bool_field(object, "enabled", true);
        rule.note = json_string_field(object, "note");
        const std::string hash_text = json_string_field(object, "hash");
        const std::string replacement = json_string_field(object, "replacement");
        if (!rule.enabled || hash_text.empty() || replacement.empty())
            continue;
        if (!parse_hash_hex(hash_text, rule.hash))
        {
            log().warn("bad hash in rule: " + hash_text);
            continue;
        }
        rule.replacement = std::filesystem::path(replacement);
        config_.rules[rule.hash] = std::move(rule);
    }
}

const Rule *ConfigStore::find(uint64_t hash) const
{
    const auto it = config_.rules.find(hash);
    if (it == config_.rules.end() || !it->second.enabled)
        return nullptr;
    return &it->second;
}
}
