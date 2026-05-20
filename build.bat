@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo [GPMI] Building launcher from:
echo [GPMI]   %CD%

set "GPMI_VERSION=0.0.0.0"
set "RESOURCE_BUNDLE_ARG="
set "TKHTML_EXTRAS_PATH="

if not "%~1"=="" set "GPMI_VERSION=%~1"

where cl.exe >nul 2>nul
if errorlevel 1 (
    call scripts\setup_msvc.cmd
    if errorlevel 1 exit /b 1
)

where cl.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] MSVC setup completed, but cl.exe is still not available.
    echo [GPMI][ERROR] If Visual Studio Build Tools is installed, run this manually:
    echo [GPMI][ERROR]   call "%%VCVARS64%%"
    exit /b 1
)

where link.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] cl.exe is available, but link.exe is not available.
    echo [GPMI][ERROR] Your MSVC environment is incomplete.
    exit /b 1
)

where python.exe >nul 2>nul
if errorlevel 1 (
    echo [GPMI][ERROR] python.exe not found in PATH.
    exit /b 1
)

echo [GPMI] Generating embedded resource bundle...
python scripts\build_resources_bundle.py
if errorlevel 1 exit /b 1

for /f "usebackq tokens=*" %%i in (`python -c "import importlib.util, pathlib; spec = importlib.util.find_spec('tkinterweb_tkhtml_extras'); print(pathlib.Path(spec.origin).parent if spec else '')"`) do set "TKHTML_EXTRAS_PATH=%%i"
if not exist "%TKHTML_EXTRAS_PATH%\tkhtml" (
    echo [GPMI][ERROR] tkinterweb_tkhtml_extras tkhtml runtime was not found.
    echo [GPMI][ERROR] Install dependencies first:
    echo [GPMI][ERROR]   python -m pip install -r requirements.txt
    exit /b 1
)

if exist "src\gpmi_launcher\core\resources_bundle.py" (
    echo [GPMI] Found embedded resource bundle. It will be included.
    set "RESOURCE_BUNDLE_ARG=--include-module=core.resources_bundle"
) else (
    echo [GPMI][WARN] Embedded resource bundle not found:
    echo [GPMI][WARN]   src\gpmi_launcher\core\resources_bundle.py
    echo [GPMI][WARN] Build will continue without embedded Locale/Themes bundle.
)

echo [GPMI] Cleaning previous Nuitka output...
@REM call :CleanDir "build\app.build"
@REM if errorlevel 1 exit /b 1
@REM call :CleanDir "build\app.dist"
@REM if errorlevel 1 exit /b 1
@REM call :CleanDir "build\app.onefile-build"
@REM if errorlevel 1 exit /b 1

echo [GPMI] Running Nuitka...
python -m nuitka ^
  --mode=standalone ^
  --msvc=latest ^
  --enable-plugin=tk-inter ^
  --windows-console-mode=disable ^
  --assume-yes-for-downloads ^
  --output-dir=build ^
  --output-filename="GPMI.exe" ^
  --product-name="GPMI Launcher" ^
  --file-description="GPMI Launcher" ^
  --company-name="GPMI" ^
  --file-version=%GPMI_VERSION% ^
  --product-version=%GPMI_VERSION% ^
  --include-package=customtkinter ^
  --include-package-data=customtkinter ^
  --include-package=tkinterweb ^
  --include-package=tkinterweb_tkhtml ^
  --include-package=tkinterweb_tkhtml_extras ^
  --include-data-dir="%TKHTML_EXTRAS_PATH%\tkhtml=tkinterweb_tkhtml_extras\tkhtml" ^
  %RESOURCE_BUNDLE_ARG% ^
  src\gpmi_launcher\app.py

if errorlevel 1 exit /b 1

echo [GPMI] Done.
echo [GPMI] Nuitka output is under:
echo [GPMI]   %CD%\build
exit /b 0

:CleanDir
set "CLEAN_TARGET=%~1"
if not exist "%CLEAN_TARGET%" exit /b 0
rmdir /s /q "%CLEAN_TARGET%" >nul 2>nul
if not exist "%CLEAN_TARGET%" exit /b 0
echo [GPMI][ERROR] Failed to remove "%CLEAN_TARGET%".
echo [GPMI][ERROR] Close any running GPMI.exe from the build folder, close Explorer windows opened inside build\app.dist, then run packaging again.
echo [GPMI][ERROR] To force-close the old build manually:
echo [GPMI][ERROR]   taskkill /IM GPMI.exe /F
exit /b 1
