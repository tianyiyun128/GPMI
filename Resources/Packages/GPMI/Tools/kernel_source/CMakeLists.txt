cmake_minimum_required(VERSION 3.21)
project(PortraitHashReplace LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(RESHADE_INCLUDE_DIR "${CMAKE_CURRENT_LIST_DIR}/../third_party/reshade/include" CACHE PATH "Path to ReShade include directory")

add_library(PortraitHashReplace SHARED
    src/addon.cpp
    src/config.cpp
    src/log.cpp
)

target_include_directories(PortraitHashReplace PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${RESHADE_INCLUDE_DIR}
)

target_compile_definitions(PortraitHashReplace PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
    RESHADE_ADDON
)

target_compile_options(PortraitHashReplace PRIVATE /W4 /permissive-)

set_target_properties(PortraitHashReplace PROPERTIES
    OUTPUT_NAME "PortraitHashReplace"
    SUFFIX ".addon64"
)

# ReShade add-ons are normal DLLs with a .addon64 extension.
