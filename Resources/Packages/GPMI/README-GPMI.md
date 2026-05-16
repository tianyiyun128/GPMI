# GPMI - Godot Portrait Model Importer

This folder is the local runtime profile for the XXMI-based GPMI launcher fork.

## Runtime layout

- `Core/GPMI/ReShade64.dll` - put a ReShade DLL with add-on support here.
- `Core/GPMI/PortraitHashReplace.addon64` - build the add-on from `Tools/kernel_source` and put the output here.
- `Core/GPMI/Addons/PortraitHashReplace.addon64` - mirrored copy loaded by ReShade's add-on search path.
- `hash_db.json` - replacement rules maintained by the launcher UI.
- `Mods/<name>/textures/*.ptrtex` - imported portrait textures.
- `Dumps/*.ptrtex` - unknown uploads dumped by the add-on.

## Flow

The launcher starts the original Godot `.exe`, injects `ReShade64.dll`, ReShade loads the portrait hash add-on, and the add-on replaces matching texture uploads by hash.

The game executable itself is not patched.

## Build the add-on from `kernel_source`

If ReShade headers have already been cloned under `Tools/third_party/reshade`, open normal cmd or PowerShell and run:

```bat
cd /d Resources\Packages\GPMI\Tools\kernel_source
build.cmd
```

PowerShell:

```powershell
cd Resources\Packages\GPMI\Tools\kernel_source
.\build.ps1
```

The script automatically finds Visual Studio Build Tools, calls `vcvars64.bat`, builds Release x64 and copies `PortraitHashReplace.addon64` to `Core/GPMI`.

The bundled ReShade runtime currently supports add-on API 18, so the vendored headers under `Tools/third_party/reshade/include` are pinned to `RESHADE_API_VERSION 18`.
