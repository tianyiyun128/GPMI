@echo off

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
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
        set "VSINSTALL=%%i"
        if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" (
            set "VCVARS64_FOUND=%%i\VC\Auxiliary\Build\vcvars64.bat"
        )
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
