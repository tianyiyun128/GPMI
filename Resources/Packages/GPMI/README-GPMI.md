# GPMI - Godot Portrait Model Importer

This folder is the local runtime profile for the XXMI-based GPMI launcher fork.

## Runtime layout

- `Core/GPMI/ReShade64.dll` - injected runtime DLL used by the launcher shell.
- `Core/GPMI/PortraitHashReplace.addon64` - native add-on for diagnostics/native experiments.
- `Mods/<character>/<outfit>/Unit|Unit_H/<image>` - user supplied portrait images.
- `mod_meta.json` - imported outfit metadata and selected outfit ids.
- `live_portraits.json` - live selection manifest for the in-game GPMI bridge.

## Flow

The launcher records portrait image paths, assigns stable outfit ids, and writes `live_portraits.json` whenever the active selection changes. The in-game GPMI bridge watches that manifest revision, reloads the selected Unit/Unit_H images from their original mod folders, clears the relevant `ImageLoader.image_cache` entries, and refreshes visible portrait nodes without restarting the game.

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

The bundled ReShade runtime currently supports add-on API 18, so the vendored headers under `Tools/third_party/reshade/include` are pinned to `RESHADE_API_VERSION 18`.
