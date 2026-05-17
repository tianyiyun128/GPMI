#pragma once

#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

namespace gpmi
{
class Logger
{
public:
    void open(const std::filesystem::path &path);
    void info(const std::string &message);
    void warn(const std::string &message);
    void error(const std::string &message);

private:
    void write(const char *level, const std::string &message);

    std::mutex mutex_;
    std::ofstream file_;
};

Logger &log();
std::string narrow(const std::wstring &value);
std::wstring widen(const std::string &value);
}
