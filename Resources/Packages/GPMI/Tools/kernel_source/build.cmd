@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo [GPMI] Building PortraitHashReplace.addon64 from:
echo [GPMI]   %CD%

set "RESHADE_INCLUDE_DIR=%CD%\..\third_party\reshade\include"
if not exist "%RESHADE_INCLUDE_DIR%\reshade.hpp" (
    echo [GPMI][ERROR] ReShade header not found:
    echo [GPMI][ERROR]   %RESHADE_INCLUDE_DIR%\reshade.hpp
    echo [GPMI][ERROR] Expected layout:
    echo [GPMI][ERROR]   Resources\Packages\GPMI\Tools\third_party\reshade\include\reshade.hpp
    exit /b 1
)

where cl.exe >nul 2>nul
if errorlevel 1 (
    call :SetupMSVC
    if errorlevel 1 exit /b 1
)

where cl.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] MSVC setup completed, but cl.exe is still not available.
    echo [GPMI][ERROR] Your Visual Studio C++ toolchain may be incomplete.
    exit /b 1
)

where cmake.exe >nul 2>nul
if errorlevel 1 (
    call :SetupCMake
    if errorlevel 1 exit /b 1
)

where cmake.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] CMake setup completed, but cmake.exe is still not available.
    exit /b 1
)

echo [GPMI] cl.exe:
where cl.exe
echo [GPMI] cmake.exe:
where cmake.exe

echo [GPMI] ReShade include:
echo [GPMI]   %RESHADE_INCLUDE_DIR%

set "BUILD_OK="
set "OUTPUT_ADDON="

if defined CMAKE_GENERATOR (
    set "BUILD_DIR=build_custom"
    echo [GPMI] Using CMAKE_GENERATOR override: %CMAKE_GENERATOR%
    call :ConfigureAndBuild "%CMAKE_GENERATOR%" "%CMAKE_GENERATOR_PLATFORM%" "%BUILD_DIR%"
    if errorlevel 1 exit /b 1
    set "BUILD_OK=1"
) else (
    where ninja.exe >nul 2>nul
    if not errorlevel 1 (
        set "BUILD_DIR=build_ninja"
        echo [GPMI] Trying CMake generator: Ninja
        call :ConfigureAndBuild "Ninja" "" "!BUILD_DIR!"
        if not errorlevel 1 set "BUILD_OK=1"
    )

    if not defined BUILD_OK (
        set "BUILD_DIR=build_vs18"
        echo [GPMI] Trying CMake generator: Visual Studio 18 2026
        call :ConfigureAndBuild "Visual Studio 18 2026" "x64" "!BUILD_DIR!"
        if not errorlevel 1 set "BUILD_OK=1"
    )

    if not defined BUILD_OK (
        set "BUILD_DIR=build_vs17"
        echo [GPMI] Trying CMake generator: Visual Studio 17 2022
        call :ConfigureAndBuild "Visual Studio 17 2022" "x64" "!BUILD_DIR!"
        if not errorlevel 1 set "BUILD_OK=1"
    )
)

if not defined BUILD_OK (
    echo [GPMI][ERROR] All CMake generator attempts failed.
    echo [GPMI][ERROR] If you know your generator, run for example:
    echo [GPMI][ERROR]   set "CMAKE_GENERATOR=Visual Studio 18 2026"
    echo [GPMI][ERROR]   build.cmd
    echo [GPMI][ERROR] Or, if Ninja is installed:
    echo [GPMI][ERROR]   set "CMAKE_GENERATOR=Ninja"
    echo [GPMI][ERROR]   build.cmd
    exit /b 1
)

call :FindOutput "%BUILD_DIR%"
if errorlevel 1 exit /b 1

if not exist "..\..\Runtime" mkdir "..\..\Runtime"
call :CopyWithRetry "%OUTPUT_ADDON%" "..\..\Runtime\PortraitHashReplace.addon64"
if errorlevel 1 exit /b 1
if not exist "..\..\Runtime\Addons" mkdir "..\..\Runtime\Addons"
call :CopyWithRetry "%OUTPUT_ADDON%" "..\..\Runtime\Addons\PortraitHashReplace.addon64"
if errorlevel 1 exit /b 1

echo [GPMI] Done.
echo [GPMI] Output:
echo [GPMI]   %OUTPUT_ADDON%
echo [GPMI] Copied to:
echo [GPMI]   %CD%\..\..\Runtime\PortraitHashReplace.addon64
echo [GPMI]   %CD%\..\..\Runtime\Addons\PortraitHashReplace.addon64
exit /b 0

:ConfigureAndBuild
set "GENERATOR=%~1"
set "PLATFORM=%~2"
set "TRY_BUILD_DIR=%~3"

if "%GENERATOR%"=="" exit /b 1
if "%TRY_BUILD_DIR%"=="" set "TRY_BUILD_DIR=build"

set "CMAKE_CONFIG_ARGS=-S . -B "%TRY_BUILD_DIR%" -G "%GENERATOR%" -DRESHADE_INCLUDE_DIR="%RESHADE_INCLUDE_DIR%""
if not "%PLATFORM%"=="" set "CMAKE_CONFIG_ARGS=%CMAKE_CONFIG_ARGS% -A %PLATFORM%"
if /I "%GENERATOR%"=="Ninja" set "CMAKE_CONFIG_ARGS=%CMAKE_CONFIG_ARGS% -DCMAKE_BUILD_TYPE=Release"

cmake %CMAKE_CONFIG_ARGS%
if errorlevel 1 (
    echo [GPMI][WARN] Configure failed with generator: %GENERATOR%
    exit /b 1
)

cmake --build "%TRY_BUILD_DIR%" --config Release
if errorlevel 1 (
    call :FindOutput "%TRY_BUILD_DIR%" >nul 2>nul
    if not errorlevel 1 (
        echo [GPMI][WARN] Build command returned failure, but output was produced. Continuing with generated add-on.
        set "BUILD_DIR=%TRY_BUILD_DIR%"
        exit /b 0
    )
    echo [GPMI][WARN] Build failed with generator: %GENERATOR%
    exit /b 1
)

set "BUILD_DIR=%TRY_BUILD_DIR%"
exit /b 0

:FindOutput
set "SEARCH_BUILD_DIR=%~1"
set "OUTPUT_ADDON="

if exist "%SEARCH_BUILD_DIR%\Release\PortraitHashReplace.addon64" set "OUTPUT_ADDON=%CD%\%SEARCH_BUILD_DIR%\Release\PortraitHashReplace.addon64"
if not defined OUTPUT_ADDON if exist "%SEARCH_BUILD_DIR%\PortraitHashReplace.addon64" set "OUTPUT_ADDON=%CD%\%SEARCH_BUILD_DIR%\PortraitHashReplace.addon64"

if not defined OUTPUT_ADDON (
    for /f "usebackq tokens=*" %%F in (`dir /s /b "%SEARCH_BUILD_DIR%\PortraitHashReplace.addon64" 2^>nul`) do (
        if not defined OUTPUT_ADDON set "OUTPUT_ADDON=%%F"
    )
)

if not defined OUTPUT_ADDON (
    echo [GPMI][ERROR] Build completed but output was not found under:
    echo [GPMI][ERROR]   %CD%\%SEARCH_BUILD_DIR%
    exit /b 1
)
exit /b 0

:CopyWithRetry
set "COPY_SRC=%~1"
set "COPY_DST=%~2"
for /L %%R in (1,1,8) do (
    copy /Y "%COPY_SRC%" "%COPY_DST%" >nul 2>nul
    if not errorlevel 1 exit /b 0
    echo [GPMI][WARN] Copy failed, retry %%R/8:
    echo [GPMI][WARN]   %COPY_DST%
    ping -n 2 127.0.0.1 >nul
)
echo [GPMI][ERROR] Could not copy output. Close the game/launcher if it is using:
echo [GPMI][ERROR]   %COPY_DST%
exit /b 1

:SetupCMake
echo [GPMI] cmake.exe not found in PATH. Trying Visual Studio bundled CMake...

if defined CMAKE_EXE (
    if exist "%CMAKE_EXE%" (
        echo [GPMI] Using CMAKE_EXE override:
        echo [GPMI]   %CMAKE_EXE%
        set "PATH=%~dp0;%PATH%"
        for %%D in ("%CMAKE_EXE%") do set "PATH=%%~dpD;%PATH%"
        exit /b 0
    ) else (
        echo [GPMI][WARN] CMAKE_EXE is set but file does not exist:
        echo [GPMI][WARN]   %CMAKE_EXE%
    )
)

if defined VSINSTALL (
    if exist "%VSINSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" (
        set "PATH=%VSINSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%PATH%"
        exit /b 0
    )
)

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do set "CMAKE_VSINSTALL=%%i"
    if defined CMAKE_VSINSTALL (
        if exist "%CMAKE_VSINSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" (
            echo [GPMI] Using Visual Studio bundled CMake:
            echo [GPMI]   %CMAKE_VSINSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin
            set "PATH=%CMAKE_VSINSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%PATH%"
            exit /b 0
        )
    )
)

for %%P in (
    "%ProgramFiles%\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
) do (
    if exist "%%~P\cmake.exe" (
        echo [GPMI] Using CMake:
        echo [GPMI]   %%~P
        set "PATH=%%~P;%PATH%"
        exit /b 0
    )
)

echo [GPMI][ERROR] cmake.exe not found.
echo [GPMI][ERROR] Install CMake, or install the C++ CMake tools for Windows component in Visual Studio Installer.
exit /b 1

:SetupMSVC
echo [GPMI] MSVC environment not detected. Trying to locate vcvars64.bat...

rem 1) Manual override. Example:
rem    set VCVARS64=C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat
if defined VCVARS64 (
    if exist "%VCVARS64%" (
        echo [GPMI] Using VCVARS64 override:
        echo [GPMI]   %VCVARS64%
        call "%VCVARS64%"
        if errorlevel 1 exit /b 1
        exit /b 0
    ) else (
        echo [GPMI][WARN] VCVARS64 is set but file does not exist:
        echo [GPMI][WARN]   %VCVARS64%
    )
)

rem 2) Prefer vswhere when available.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
    if defined VSINSTALL (
        if exist "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat" (
            echo [GPMI] Using Visual Studio found by vswhere:
            echo [GPMI]   %VSINSTALL%
            call "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
            if errorlevel 1 exit /b 1
            exit /b 0
        )
    )
) else (
    echo [GPMI][WARN] vswhere.exe not found, falling back to common install paths.
)

rem 3) Common install paths, including VS 18 Insiders.
for %%P in (
    "%ProgramFiles%\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
) do (
    if exist "%%~P" (
        echo [GPMI] Using vcvars64.bat:
        echo [GPMI]   %%~P
        call "%%~P"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

echo [GPMI][ERROR] Could not find vcvars64.bat.
echo [GPMI][ERROR]
echo [GPMI][ERROR] Since your VS path is unusual, run:
echo [GPMI][ERROR]   set "VCVARS64=C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat"
echo [GPMI][ERROR]   build.cmd
echo [GPMI][ERROR]
echo [GPMI][ERROR] If the file does not exist, open Visual Studio Installer and install:
echo [GPMI][ERROR]   Desktop development with C++
echo [GPMI][ERROR]   MSVC x64/x86 build tools
echo [GPMI][ERROR]   Windows 10/11 SDK
echo [GPMI][ERROR]   C++ CMake tools for Windows
exit /b 1
