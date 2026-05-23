@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo [GPMI] Reusing existing launcher output.
echo [GPMI] No compiler or Nuitka build will be run.

set "BUILD_DIR=%CD%\build"
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

echo [GPMI] Found launcher output:
echo [GPMI]   "%NUITKA_DIST%"
echo [GPMI] Done.
exit /b 0
