@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo [GPMI] Building embedded resource bundle.
echo [GPMI] No compiler or Nuitka build will be run.

where python.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] python.exe not found in PATH.
    exit /b 1
)

python scripts\build_resources_bundle.py
if errorlevel 1 exit /b 1

if not exist "src\gpmi_launcher\core\resources_bundle.py" (
    echo [GPMI][ERROR] Embedded resource bundle was not generated:
    echo [GPMI][ERROR]   src\gpmi_launcher\core\resources_bundle.py
    exit /b 1
)

echo [GPMI] Embedded resource bundle generated:
echo [GPMI]   src\gpmi_launcher\core\resources_bundle.py
echo [GPMI] Done.
exit /b 0
