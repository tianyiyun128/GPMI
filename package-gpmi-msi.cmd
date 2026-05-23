@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=0.0.0"
if /I "%VERSION:~0,1%"=="v" set "VERSION=%VERSION:~1%"

for /f "tokens=1-4 delims=." %%a in ("%VERSION%") do (
    if "%%a"=="" goto :BadVersion
    if "%%b"=="" goto :BadVersion
    if "%%c"=="" goto :BadVersion
)

set "BUILD_DIR=%CD%\build"
set "STAGE_DIR=%CD%\dist\GPMI"
set "BIN_DIR=%STAGE_DIR%\Resources\Bin"
set "MSI_OUT=%CD%\dist\GPMI-%VERSION%.msi"
set "WXS=%CD%\build\msi\GPMI.wxs"

echo [GPMI] Packaging MSI from existing launcher output.
echo [GPMI] Embedded resources will be regenerated first.
echo [GPMI] Compiler and Nuitka build will not be run.

where python.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] python.exe not found in PATH.
    exit /b 1
)

call build.bat
if errorlevel 1 exit /b 1

where wix.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] WiX Toolset was not found.
    echo [GPMI][ERROR] Install WiX v4+ first, for example:
    echo [GPMI][ERROR]   dotnet tool install --global wix
    exit /b 1
)

wix extension list 2>nul | findstr /I /C:"WixToolset.UI.wixext" >nul
if errorlevel 1 (
    echo [GPMI] WiX UI extension was not found. Installing it now...
    wix extension add WixToolset.UI.wixext
    if errorlevel 1 (
        echo [GPMI][ERROR] Failed to install WiX UI extension.
        echo [GPMI][ERROR] Run this manually, then retry:
        echo [GPMI][ERROR]   wix extension add WixToolset.UI.wixext
        exit /b 1
    )
)

wix extension list 2>nul | findstr /I /C:"WixToolset.UI.wixext" >nul
if errorlevel 1 (
    echo [GPMI][ERROR] WiX UI extension is still not available after installation.
    exit /b 1
)

set "NUITKA_DIST="
for %%D in ("%BUILD_DIR%\app.dist" "%BUILD_DIR%\GPMI.dist" "%BUILD_DIR%\GPMI Launcher.dist") do (
    if exist "%%~D\GPMI.exe" set "NUITKA_DIST=%%~D"
)
if not defined NUITKA_DIST (
    for /d %%D in ("%BUILD_DIR%\*.dist") do (
        if exist "%%~D\GPMI.exe" set "NUITKA_DIST=%%~D"
    )
)
if not defined NUITKA_DIST (
    echo [GPMI][ERROR] Could not find existing launcher output containing GPMI.exe under:
    echo [GPMI][ERROR]   "%BUILD_DIR%"
    echo [GPMI][ERROR]
    echo [GPMI][ERROR] Expected one of these layouts:
    echo [GPMI][ERROR]   build\app.dist\GPMI.exe
    echo [GPMI][ERROR]   build\GPMI.dist\GPMI.exe
    echo [GPMI][ERROR]   build\GPMI Launcher.dist\GPMI.exe
    echo [GPMI][ERROR]   build\*.dist\GPMI.exe
    exit /b 1
)

echo [GPMI] Using launcher output:
echo [GPMI]   "%NUITKA_DIST%"

if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"
mkdir "%BIN_DIR%"

robocopy "%NUITKA_DIST%" "%BIN_DIR%" /E /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 exit /b %ERRORLEVEL%

python scripts\package_msi.py --version "%VERSION%" --stage-dir "%STAGE_DIR%" --output "%MSI_OUT%" --wxs "%WXS%"
if errorlevel 1 exit /b 1

echo.
echo [GPMI] MSI package:
echo [GPMI]   "%MSI_OUT%"
exit /b 0

:BadVersion
echo [GPMI][ERROR] Version must use semantic version format, for example:
echo [GPMI][ERROR]   package-gpmi-msi.cmd 0.1.0
exit /b 1
