@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=0.0.0"
if /I "%VERSION:~0,1%"=="v" set "VERSION=%VERSION:~1%"
set "SKIP_BUILD="
if /I "%~2"=="--skip-build" set "SKIP_BUILD=1"
if /I "%~2"=="skip-build" set "SKIP_BUILD=1"
set "VERSION4=%VERSION%"
for /f "tokens=1-4 delims=." %%a in ("%VERSION%") do (
    if "%%a"=="" goto :BadVersion
    if "%%b"=="" goto :BadVersion
    if "%%c"=="" goto :BadVersion
    if "%%d"=="" set "VERSION4=%%a.%%b.%%c.0"
)

set "BUILD_DIR=%CD%\build"
set "STAGE_DIR=%CD%\dist\GPMI"
set "BIN_DIR=%STAGE_DIR%\Resources\Bin"
set "RUNTIME_SRC=%CD%\Resources\Packages\GPMI\Runtime"
set "RUNTIME_DST=%STAGE_DIR%\Resources\Packages\GPMI\Runtime"
set "MSI_OUT=%CD%\dist\GPMI-%VERSION%.msi"
set "WXS=%CD%\build\msi\GPMI.wxs"

where python.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] python.exe not found in PATH.
    exit /b 1
)

where wix.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] WiX Toolset was not found.
    echo [GPMI][ERROR] Install WiX v4+ first, for example:
    echo [GPMI][ERROR]   dotnet tool install --global wix
    echo [GPMI][ERROR]   wix extension add WixToolset.UI.wixext
    exit /b 1
)

wix extension list 2>nul | findstr /I /C:"WixToolset.UI.wixext" >nul
if errorlevel 1 (
    echo [GPMI][ERROR] WiX UI extension was not found.
    echo [GPMI][ERROR] Install it first:
    echo [GPMI][ERROR]   wix extension add WixToolset.UI.wixext
    echo [GPMI][ERROR]
    echo [GPMI][ERROR] After installing it, reuse the existing Nuitka build with:
    echo [GPMI][ERROR]   package-gpmi-msi.cmd %VERSION% --skip-build
    exit /b 1
)

if defined SKIP_BUILD (
    echo [GPMI] Skipping Nuitka build and reusing existing build output.
) else (
    call build.bat "%VERSION4%"
    if errorlevel 1 exit /b 1
)

if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"
mkdir "%BIN_DIR%"

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
    echo [GPMI][ERROR] Could not find Nuitka output containing GPMI.exe under:
    echo [GPMI][ERROR]   "%BUILD_DIR%"
    exit /b 1
)

robocopy "%NUITKA_DIST%" "%BIN_DIR%" /E /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 exit /b %ERRORLEVEL%

if exist "%RUNTIME_SRC%" (
    mkdir "%RUNTIME_DST%"
    robocopy "%RUNTIME_SRC%" "%RUNTIME_DST%" /E /NFL /NDL /NJH /NJS /NP
    if %ERRORLEVEL% GEQ 8 exit /b %ERRORLEVEL%
)

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
