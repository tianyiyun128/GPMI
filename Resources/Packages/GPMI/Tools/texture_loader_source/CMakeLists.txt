cmake_minimum_required(VERSION 3.21)
project(GPMITextureLoader LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(GPMITextureLoader SHARED
    src/texture_loader_dll.cpp
)

target_include_directories(GPMITextureLoader PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
)

target_compile_definitions(GPMITextureLoader PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
)

target_compile_options(GPMITextureLoader PRIVATE /W4 /permissive- /EHsc)

set_target_properties(GPMITextureLoader PROPERTIES
    OUTPUT_NAME "GPMITextureLoader"
)
