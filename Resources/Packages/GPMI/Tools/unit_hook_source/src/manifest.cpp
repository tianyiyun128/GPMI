#include "manifest.hpp"

#include "log.hpp"

#include <fstream>
#include <regex>
#include <sstream>

namespace gpmi
{
namespace
{
Manifest g_manifest;

std::string read_file(const std::filesystem::path &path)
{
    std::ifstream file(path, std::ios::binary);
    std::ostringstream out;
    out << file.rdbuf();
    return out.str();
}

std::string unescape_json_string(std::string value)
{
    std::string out;
    out.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] != '\\' || i + 1 >= value.size())
        {
            out.push_back(value[i]);
            continue;
        }
        const char esc = value[++i];
        switch (esc)
        {
        case '\\': out.push_back('\\'); break;
        case '"': out.push_back('"'); break;
        case '/': out.push_back('/'); break;
        case 'b': out.push_back('\b'); break;
        case 'f': out.push_back('\f'); break;
        case 'n': out.push_back('\n'); break;
        case 'r': out.push_back('\r'); break;
        case 't': out.push_back('\t'); break;
        default: out.push_back(esc); break;
        }
    }
    return out;
}

std::string json_string_field(const std::string &object, const char *name)
{
    const std::regex re(std::string("\"") + name + "\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\"");
    std::smatch match;
    if (!std::regex_search(object, match, re))
        return {};
    return unescape_json_string(match[1].str());
}

bool json_bool_field(const std::string &object, const char *name)
{
    const std::regex re(std::string("\"") + name + "\"\\s*:\\s*true");
    return std::regex_search(object, re);
}

int json_int_field(const std::string &text, const char *name, int fallback)
{
    const std::regex re(std::string("\"") + name + "\"\\s*:\\s*(-?\\d+)");
    std::smatch match;
    if (!std::regex_search(text, match, re))
        return fallback;
    try
    {
        return std::stoi(match[1].str());
    }
    catch (...)
    {
        return fallback;
    }
}
}

Manifest &manifest()
{
    return g_manifest;
}

void Manifest::set_path(std::filesystem::path path)
{
    std::lock_guard lock(mutex_);
    path_ = std::move(path);
    last_write_ = {};
    revision_ = -1;
    rules_.clear();
}

std::filesystem::path Manifest::path() const
{
    std::lock_guard lock(mutex_);
    return path_;
}

void Manifest::reload_if_needed()
{
    std::lock_guard lock(mutex_);
    if (path_.empty() || !std::filesystem::is_regular_file(path_))
        return;

    const auto write_time = std::filesystem::last_write_time(path_);
    if (write_time == last_write_)
        return;

    last_write_ = write_time;
    reload_locked();
}

std::optional<Rule> Manifest::match(const std::string &logical_path)
{
    reload_if_needed();
    std::lock_guard lock(mutex_);
    const auto it = rules_.find(logical_path);
    if (it == rules_.end() || !it->second.enabled)
        return std::nullopt;
    return it->second;
}

void Manifest::reload_locked()
{
    rules_.clear();
    std::string text;
    try
    {
        text = read_file(path_);
    }
    catch (const std::exception &e)
    {
        log().error("manifest read failed: " + std::string(e.what()));
        return;
    }

    revision_ = json_int_field(text, "revision", revision_);

    const std::regex object_re("\\{[^\\{\\}]*\"replacement\"\\s*:[^\\{\\}]*\\}");
    int loaded = 0;
    for (auto it = std::sregex_iterator(text.begin(), text.end(), object_re); it != std::sregex_iterator(); ++it)
    {
        const std::string object = it->str();
        Rule rule;
        rule.enabled = json_bool_field(object, "enabled");
        rule.logical_path = json_string_field(object, "logical_path");
        rule.cache_key = json_string_field(object, "cache_key");
        rule.character_id = json_string_field(object, "character_id");
        rule.outfit_id = json_string_field(object, "outfit_id");
        rule.slot = json_string_field(object, "slot");
        rule.action = json_string_field(object, "action");
        rule.replacement = widen(json_string_field(object, "replacement"));

        if (rule.logical_path.empty())
            rule.logical_path = rule.cache_key;
        if (rule.logical_path.empty() || rule.replacement.empty())
            continue;

        rules_[rule.logical_path] = std::move(rule);
        ++loaded;
    }

    log().info("manifest loaded: revision=" + std::to_string(revision_) +
               ", rules=" + std::to_string(loaded) +
               ", path=" + path_.string());
}
}
