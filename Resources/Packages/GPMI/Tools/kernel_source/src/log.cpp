#include "log.hpp"
#include <chrono>
#include <iomanip>
#include <sstream>
#include <Windows.h>

namespace ptr
{
Logger &log()
{
    static Logger instance;
    return instance;
}

void Logger::open(const std::filesystem::path &path)
{
    std::scoped_lock lock(mutex_);
    std::filesystem::create_directories(path.parent_path());
    file_.open(path, std::ios::out | std::ios::app);
}

void Logger::info(const std::string &message) { write("INFO", message); }
void Logger::warn(const std::string &message) { write("WARN", message); }
void Logger::error(const std::string &message) { write("ERROR", message); }

void Logger::write(const char *level, const std::string &message)
{
    std::ostringstream line;
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    localtime_s(&tm, &time);
    line << std::put_time(&tm, "%Y-%m-%d %H:%M:%S") << " [" << level << "] " << message << "\n";

    OutputDebugStringA(line.str().c_str());

    std::scoped_lock lock(mutex_);
    if (file_.is_open())
    {
        file_ << line.str();
        file_.flush();
    }
}
}
