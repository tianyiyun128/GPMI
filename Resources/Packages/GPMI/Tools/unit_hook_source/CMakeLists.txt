cmake_minimum_required(VERSION 3.21)
project(GPMIUnitHook LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

function(configure_gpmi_dll target_name output_name)
    target_include_directories(${target_name} PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}/src
    )

    target_compile_definitions(${target_name} PRIVATE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
        UNICODE
        _UNICODE
    )

    target_compile_options(${target_name} PRIVATE /W4 /permissive- /EHsc)

    set_target_properties(${target_name} PROPERTIES
        OUTPUT_NAME "${output_name}"
    )
endfunction()

add_library(GPMIUnitHook SHARED
    src/dllmain.cpp
    src/godot_abi.cpp
    src/godot_unit_hook.cpp
    src/log.cpp
    src/manifest.cpp
    src/win_memory.cpp
)
configure_gpmi_dll(GPMIUnitHook "GPMIUnitHook")

add_library(GPMITextureLoader SHARED
    src/texture_loader_dll.cpp
)
configure_gpmi_dll(GPMITextureLoader "GPMITextureLoader")
