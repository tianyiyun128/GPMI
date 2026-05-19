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
    call :SetupMSVC
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
if exist "build\app.build" rmdir /s /q "build\app.build"
if exist "build\app.dist" rmdir /s /q "build\app.dist"
if exist "build\app.onefile-build" rmdir /s /q "build\app.onefile-build"

echo [GPMI] Running Nuitka...
python -m nuitka ^
  --mode=standalone ^
  --msvc=latest ^
  --enable-plugin=tk-inter ^
  --windows-console-mode=disable ^
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

:SetupMSVC
echo [GPMI] MSVC environment not detected. Trying to locate vcvars64.bat...

if defined VCVARS64 (
    if exist "%VCVARS64%" (
        echo [GPMI] Using VCVARS64 from environment:
        echo [GPMI]   %VCVARS64%
        call "%VCVARS64%"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

set "VCVARS64_FOUND="
set "VSINSTALL="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
    if defined VSINSTALL if exist "!VSINSTALL!\VC\Auxiliary\Build\vcvars64.bat" (
        set "VCVARS64_FOUND=!VSINSTALL!\VC\Auxiliary\Build\vcvars64.bat"
    )
)

if not defined VCVARS64_FOUND (
    for %%P in (
        "%ProgramFiles%\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\Community\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\Professional\VC\Auxiliary\Build\vcvars64.bat"
        "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
    ) do (
        if exist "%%~P" (
            set "VCVARS64_FOUND=%%~P"
            goto :FoundVCVars64
        )
    )
)

:FoundVCVars64
if not defined VCVARS64_FOUND (
    echo [GPMI][ERROR] Could not find vcvars64.bat.
    echo [GPMI][ERROR] Checked vswhere.exe and common Visual Studio Build Tools paths.
    echo [GPMI][ERROR] You can set it manually once with:
    echo [GPMI][ERROR]   setx VCVARS64 "C:\Path\To\VC\Auxiliary\Build\vcvars64.bat"
    exit /b 1
)

echo [GPMI] Found vcvars64.bat:
echo [GPMI]   %VCVARS64_FOUND%

set "VCVARS64=%VCVARS64_FOUND%"
setx VCVARS64 "%VCVARS64_FOUND%" >nul 2>nul
if errorlevel 1 (
    echo [GPMI][WARN] Failed to persist VCVARS64 with setx. Continuing for this session.
) else (
    echo [GPMI] Saved VCVARS64 as a user environment variable.
)

call "%VCVARS64_FOUND%"
if errorlevel 1 exit /b 1
exit /b 0
