cmake_minimum_required(VERSION 3.21)
project(GPMIUnitHook LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(GPMIUnitHook SHARED
    src/config.cpp
    src/image_decode.cpp
    src/logger.cpp
    src/runtime.cpp
)

target_include_directories(GPMIUnitHook PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
)

target_compile_definitions(GPMIUnitHook PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
)

target_compile_options(GPMIUnitHook PRIVATE /W4 /permissive-)

target_link_libraries(GPMIUnitHook PRIVATE windowscodecs ole32)

set_target_properties(GPMIUnitHook PROPERTIES
    OUTPUT_NAME "GPMIUnitHook"
    SUFFIX ".dll"
)
