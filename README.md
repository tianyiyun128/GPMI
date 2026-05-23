# GPMI - Godot Portrait Model Importer

GPMI is a portrait mod manager for Godot games, built on the XXMI Launcher shell. It is not XXMI itself: the app reuses the launcher framework, settings, updater, and packaging flow, while the GPMI runtime model is specific to Godot portrait replacement.

## What It Does

- Manages battle portrait mods under a per-game `GPMI/Mods` folder.
- Imports existing game `MOD/Unit` and `MOD/Unit_H` portrait pairs into the GPMI layout.
- Lets users preview portraits, select one portrait per character, or choose not to load a mod.
- Remembers the selected portrait choices across launcher restarts.
- Writes a live manifest that a Godot-side bridge can read while the game is running.

## Runtime Model

GPMI no longer uses C++ hooks, DLL injection, ReShade add-ons, GPU hash replacement, or file redirection. The launcher starts the selected game executable normally.

The live switching path is:

1. The user selects portraits in Portrait Manager.
2. GPMI writes `<game exe folder>/GPMI/mod_meta.json`.
3. GPMI writes `<game exe folder>/GPMI/live_portraits.json`.
4. A Godot-side live bridge patch reads `live_portraits.json`.
5. The bridge updates the game's portrait cache and refreshes visible portrait nodes.

The expected bridge file is:

```text
<game exe folder>/MOD/GPMILiveBridge.pck
```

The legacy `patch_88.pck` bridge is still detected for compatibility, but it should be replaced by a clean `GPMILiveBridge.pck` in release builds.

## User Mod Layout

Portrait mods live next to the selected game executable:

```text
<game exe folder>/GPMI/Mods/
```

Required layout:

```text
GPMI/
  Mods/
    <character_id lowercase>/
      <mod_name>/
        Unit/
          <one image file>
        Unit_H/
          <one image file>
```

Rules:

- `<character_id lowercase>` must match the character id used by the game portrait loader.
- `<mod_name>` can be any readable folder name.
- `Unit` must contain exactly one valid image.
- `Unit_H` must contain exactly one valid image.
- Image file names are not used as identity.

Supported source image formats:

```text
.png, .jpg, .jpeg, .webp, .bmp, .tga
```

## Launcher-Owned Files

GPMI owns these files under the selected game executable folder:

```text
GPMI/mod_meta.json
GPMI/live_portraits.json
```

`mod_meta.json` records detected mods, imported metadata, and the selected mod per character.

`live_portraits.json` is the runtime manifest consumed by the Godot live bridge. It records enabled replacement rules and absolute replacement image paths.

## Portrait Manager Workflow

1. Select the exact Godot game `.exe` in launcher settings.
2. Open `GPMI Portrait Manager`.
3. Put portrait mods under `GPMI/Mods/<character>/<mod>/Unit` and `Unit_H`.
4. Click `Scan Mods`.
5. Check the import page:
   - `READY` mods have exactly one valid image in both `Unit` and `Unit_H`.
   - `BROKEN` mods are shown in red and list the first detected issue.
6. Use the preview panel to inspect the high-resolution `Unit_H` portrait.
7. Switch to `Select Mods`.
8. Click a portrait entry to activate it immediately.
9. Click `Do Not Load Mod` to return that character to the original game portrait.
10. Start the game normally through GPMI.

Selections are saved per character and restored on the next launcher start.

## Importing Existing Game MOD Portraits

For HilichurlsAmbition-style portrait mods, the game may already contain files in:

```text
<game exe folder>/MOD/Unit
<game exe folder>/MOD/Unit_H
```

Portrait Manager can import these with `Auto Import Game Battle Portrait Mods`.

Import rules:

- The same file name must exist in both `MOD/Unit` and `MOD/Unit_H`.
- File names must follow `character_h_*`, for example `amber_h_default.png`.
- The `character` part becomes the character folder under `GPMI/Mods`.
- The suffix after `character_h_` becomes the mod folder name.
- The normal and high-resolution images are copied into the required `Unit` and `Unit_H` layout.

## Building

Build the launcher executable:

```bat
build.bat 0.1.1.0
```

Build an MSI package:

```bat
package-gpmi-msi.cmd 0.1.1
```

The MSI build requires WiX Toolset with the UI extension installed:

```bat
dotnet tool install --global wix
wix extension add WixToolset.UI.wixext
```

No C++ compiler, hook DLL, or texture loader DLL is required by GPMI.

## Development Notes

- `src/gpmi_launcher/core/gpmi/mods.py` owns mod scanning, import metadata, selection state, and live manifest generation.
- `src/gpmi_launcher/gui/windows/gpmi/portrait_manager_window.py` owns the Portrait Manager UI.
- `src/gpmi_launcher/core/packages/model_importers/gpmi_package.py` owns GPMI package integration and normal game launch.
- Translation strings live under `Locale/Strings/EN` and `Locale/Strings/CN`.
