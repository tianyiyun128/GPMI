@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=0.0.0"
if /I "%VERSION:~0,1%"=="v" set "VERSION=%VERSION:~1%"

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
set "MSI_OUT=%CD%\dist\GPMI-%VERSION%.msi"
set "WXS=%CD%\build\msi\GPMI.wxs"
set "RESOURCE_BUNDLE_ARG="
set "TKHTML_EXTRAS_PATH="

echo [GPMI] Building launcher and packaging MSI from:
echo [GPMI]   %CD%

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
    exit /b 1
)

call :EnsureWixExtension WixToolset.UI.wixext
if errorlevel 1 exit /b 1

call :EnsureWixExtension WixToolset.Util.wixext
if errorlevel 1 exit /b 1

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
    echo [GPMI][ERROR] Embedded resource bundle was not generated:
    echo [GPMI][ERROR]   src\gpmi_launcher\core\resources_bundle.py
    exit /b 1
)

echo [GPMI] Cleaning previous Nuitka output...
call :CleanDir "build\app.build"
if errorlevel 1 exit /b 1
call :CleanDir "build\app.dist"
if errorlevel 1 exit /b 1
call :CleanDir "build\app.onefile-build"
if errorlevel 1 exit /b 1

echo [GPMI] Running Nuitka...
python -m nuitka ^
  --mode=standalone ^
  --mingw64 ^
  --enable-plugin=tk-inter ^
  --windows-console-mode=disable ^
  --assume-yes-for-downloads ^
  --output-dir=build ^
  --output-filename="GPMI.exe" ^
  --product-name="GPMI Launcher" ^
  --file-description="GPMI Launcher" ^
  --company-name="GPMI" ^
  --file-version=%VERSION4% ^
  --product-version=%VERSION4% ^
  --include-package=customtkinter ^
  --include-package-data=customtkinter ^
  --include-package=tkinterweb ^
  --include-package=tkinterweb_tkhtml ^
  --include-package=tkinterweb_tkhtml_extras ^
  --include-data-dir="%TKHTML_EXTRAS_PATH%\tkhtml=tkinterweb_tkhtml_extras\tkhtml" ^
  %RESOURCE_BUNDLE_ARG% ^
  src\gpmi_launcher\app.py
if errorlevel 1 exit /b 1

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
    echo [GPMI][ERROR] Could not find newly built launcher output containing GPMI.exe under:
    echo [GPMI][ERROR]   "%BUILD_DIR%"
    exit /b 1
)

echo [GPMI] Using newly built launcher output:
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

:EnsureWixExtension
set "WIX_EXTENSION=%~1"
wix extension list 2>nul | findstr /I /C:"%WIX_EXTENSION%" >nul
if not errorlevel 1 exit /b 0

echo [GPMI] WiX extension was not found: %WIX_EXTENSION%
echo [GPMI] Installing it now...
wix extension add %WIX_EXTENSION%
if errorlevel 1 (
    echo [GPMI][ERROR] Failed to install WiX extension: %WIX_EXTENSION%
    echo [GPMI][ERROR] Run this manually, then retry:
    echo [GPMI][ERROR]   wix extension add %WIX_EXTENSION%
    exit /b 1
)

wix extension list 2>nul | findstr /I /C:"%WIX_EXTENSION%" >nul
if errorlevel 1 (
    echo [GPMI][ERROR] WiX extension is still not available after installation: %WIX_EXTENSION%
    exit /b 1
)
exit /b 0

:BadVersion
echo [GPMI][ERROR] Version must use semantic version format, for example:
echo [GPMI][ERROR]   package-gpmi-msi.cmd 0.1.0
exit /b 1
