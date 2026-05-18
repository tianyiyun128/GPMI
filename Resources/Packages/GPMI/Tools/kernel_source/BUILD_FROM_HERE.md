# Build PortraitHashReplace.addon64 from this folder

Open normal **cmd** or **PowerShell**, then `cd` directly into this folder:

```bat
cd /d D:\XXMI-Launcher-GPMI\Resources\Packages\GPMI\Tools\kernel_source
build.cmd
```

PowerShell:

```powershell
cd D:\XXMI-Launcher-GPMI\Resources\Packages\GPMI\Tools\kernel_source
.\build.ps1
```

The script expects the ReShade repository here:

```text
Resources\Packages\GPMI\Tools\third_party\reshade\include\reshade.hpp
```

If it is missing, run this from `Resources\Packages\GPMI\Tools`:

```bat
git clone --depth=1 https://github.com/crosire/reshade.git third_party\reshade
```

The script looks for MSVC in this order:

1. Existing `cl.exe` in the current environment.
2. Manual `VCVARS64` override.
3. `vswhere.exe` if installed.
4. Common Visual Studio 2022 paths, including BuildTools, Community, Professional, and Enterprise.

If automatic detection still fails, locate `vcvars64.bat` manually:

```bat
dir /s /b "%ProgramFiles%\Microsoft Visual Studio\2022\*\VC\Auxiliary\Build\vcvars64.bat"
```

Then run:

```bat
set "VCVARS64=C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
build.cmd
```

Replace the path with the actual path found on your PC.

Successful output:

```text
Resources\Packages\GPMI\Tools\kernel_source\build\Release\PortraitHashReplace.addon64
Resources\Packages\GPMI\Runtime\PortraitHashReplace.addon64
```

The script configures CMake, builds Release x64, and copies the `.addon64` into the GPMI Core folder.
