#include "log.hpp"

#include <Windows.h>

#include <chrono>
#include <iomanip>
#include <sstream>

namespace gpmi
{
namespace
{
Logger g_logger;

std::string timestamp()
{
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    localtime_s(&tm, &time);
    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%d %H:%M:%S");
    return out.str();
}
}

Logger &log()
{
    return g_logger;
}

void Logger::open(const std::filesystem::path &path)
{
    std::lock_guard lock(mutex_);
    std::filesystem::create_directories(path.parent_path());
    file_.open(path, std::ios::out | std::ios::app);
}

void Logger::info(const std::string &message)
{
    write("INFO", message);
}

void Logger::warn(const std::string &message)
{
    write("WARN", message);
}

void Logger::error(const std::string &message)
{
    write("ERROR", message);
}

void Logger::write(const char *level, const std::string &message)
{
    std::lock_guard lock(mutex_);
    std::ostringstream line;
    line << timestamp() << " [" << level << "] " << message << "\n";
    OutputDebugStringA(line.str().c_str());
    if (file_.is_open())
    {
        file_ << line.str();
        file_.flush();
    }
}

std::string narrow(const std::wstring &value)
{
    if (value.empty())
        return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}

std::wstring widen(const std::string &value)
{
    if (value.empty())
        return {};
    const int size = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0);
    std::wstring result(size, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), result.data(), size);
    return result;
}
}
