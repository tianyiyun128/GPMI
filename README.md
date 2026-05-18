# GPMI - Godot Portrait Model Importer

GPMI is a Godot portrait replacement launcher built on the XXMI Launcher shell. It is designed for the target game flow where all replaceable battle portrait assets are loaded through `ImageLoader.gd`.

## Required Runtime Rule

All portrait replacement must happen by hooking this exact game function:

```gdscript
ImageLoader.unit(type, action, high_resolution = false)
```

Every asset GPMI replaces is read through `ImageLoader.gd`'s `unit()` function. The runtime must hook that function, read its real input arguments, let the original function run, then replace the final return value according to local GPMI settings.

This is mandatory regardless of which internal route `unit()` takes. It may read from `config.user_path + "/MOD/"`, from `res://`, from a fallback path, or from cache. GPMI must not hook those lower-level routes as the primary replacement mechanism. The only accepted replacement boundary is the `ImageLoader.unit(...)` call and its final returned texture.

## Explicitly Rejected Routes

These approaches are not allowed for GPMI portrait replacement:

- ReShade runtime injection
- ReShade add-ons
- GPU texture hash replacement
- D3D12 texture upload replacement
- Win32 file path redirection
- Godot `FileAccess` or `ResourceLoader` path redirection as the main solution
- generated `MOD/Unit` or `MOD/Unit_H` override files
- pre-populating `ImageLoader.image_cache`
- replacing only by editing game scripts

Those routes either cannot see the real `unit()` semantics or cannot support reliable in-game switching.

## Correct Runtime Flow

The intended flow is:

1. The launcher starts the selected Godot game executable.
2. The launcher injects `GPMIUnitHook.dll` into the suspended Godot process.
3. The native runtime locates the `ImageLoader` autoload object.
4. The runtime hooks `ImageLoader.unit(type, action, high_resolution)`.
5. For each call, the runtime captures:
   - `type`
   - `action`
   - `high_resolution`
6. The runtime calls the original `ImageLoader.unit(...)`.
7. The runtime builds the logical portrait key from the actual call:
   - `Unit/<type>_<action>` when `high_resolution == false`
   - `Unit_H/<type>_<action>` when `high_resolution == true`
8. The runtime reads `<game exe folder>/GPMI/live_portraits.json`.
9. If a matching enabled rule exists, the runtime loads the selected replacement image itself.
10. The runtime converts that image to a valid Godot texture object.
11. The runtime replaces only the final return value of `ImageLoader.unit(...)`.
12. If no rule matches, the original return value is returned unchanged.

## Building The Native Hook

Build the native runtime from:

```bat
Resources\Packages\GPMI\Tools\unit_hook_source\build.cmd
```

The build writes:

```text
Resources/Packages/GPMI/Runtime/GPMIUnitHook.dll
```

The launcher injects this DLL when starting the selected game. The DLL writes runtime logs to:

```text
<game exe folder>/GPMI/GPMIUnitHook.log
```

If the target Godot executable needs explicit ABI addresses, copy:

```text
Resources/Packages/GPMI/Tools/unit_hook_source/GPMIUnitHook.ini.example
```

to:

```text
<game exe folder>/GPMI/GPMIUnitHook.ini
```

and fill in the required RVA values.

### Locating `Object::callp`

Use the offline locator tool to find a candidate `Object::callp` RVA for the target executable:

```bat
python Resources\Packages\GPMI\Tools\unit_hook_source\tools\find_godot_callp.py "C:\path\to\Game.exe"
```

The tool parses the Windows x64 PE file, reads `.pdata` function boundaries, scans `.rdata` string references, and prints ranked `Object::callp` candidates. No Python package installation is required.

The final block of the output is the recommended INI snippet, for example:

```ini
[GPMIUnitHook]
object_callp_rva=0x2f3c0b0
object_callp_patch_size=12
```

Copy those values into:

```text
<game exe folder>/GPMI/GPMIUnitHook.ini
```

Then start the game and inspect:

```text
<game exe folder>/GPMI/GPMIUnitHook.log
```

A valid hook should log entries such as:

```text
Object::callp hook installed
unit call observed: Unit/jean_default
unit call observed: Unit_H/jean_default
```

If the locator prints multiple candidates, test the highest-scoring candidate first. Do not continue with return replacement against an unverified RVA.

For the current HilichurlsAmbition 1.2.3.3 test executable, the locator currently reports:

```ini
object_callp_rva=0x2f3c0b0
object_callp_patch_size=12
```

## User Mod Folder Layout

User portrait mods live next to the selected game executable:

```text
<game exe folder>/GPMI/Mods/
```

Required layout:

```text
GPMI/
  Mods/
    <character_id lowercase>/
      <outfit_name>/
        Unit/
          <one image file, name can be anything>
        Unit_H/
          <one image file, name can be anything>
```

Rules:

- `<character_id lowercase>` must match the character id passed to `ImageLoader.unit()`.
- `<outfit_name>` can be any readable folder name.
- `Unit` must contain exactly one image.
- `Unit_H` must contain exactly one image.
- Image file names are arbitrary and are not used for identity.

Supported source image formats:

```text
.png, .jpg, .jpeg, .webp, .bmp, .tga
```

## Launcher-Owned Files

The launcher owns:

```text
<game exe folder>/GPMI/mod_meta.json
<game exe folder>/GPMI/live_portraits.json
```

`mod_meta.json` records detected mods, imported metadata, and the selected mod per character. It stores both the internal outfit id and the source mod folder path so player choices survive launcher restarts and metadata refreshes.

`live_portraits.json` is the runtime manifest watched by the native hook runtime. It records enabled replacement rules and absolute replacement image paths.

Example rule:

```json
{
  "enabled": true,
  "character_id": "mona",
  "outfit_id": "outfit_001",
  "slot": "Unit",
  "action": "default",
  "cache_key": "Unit/mona_default",
  "logical_path": "Unit/mona_default",
  "replacement": "C:/Games/Example/GPMI/Mods/mona/default/Unit/image.png"
}
```

## Portrait Manager Workflow

1. Select the exact Godot game `.exe` in launcher settings.
2. Open `GPMI Portrait Manager`.
3. Open the Mods folder.
4. Put portrait mods under `GPMI/Mods/<character>/<mod>/Unit` and `Unit_H`.
5. Click `Scan Mods`.
6. Review the import page:
   - `READY` mods have exactly one valid image in both `Unit` and `Unit_H`.
   - `BROKEN` mods are shown in red and list the first detected issue.
   - Ready mods are synced into launcher metadata automatically.
7. Use the preview panel to inspect the high-resolution `Unit_H` portrait.
8. Switch to `Select Mods`.
9. Pick a character, then click a portrait entry to activate it immediately.
10. Click `Do Not Load Mod` for a character to return that character to the original game portrait.
11. Start the game.
12. While the game is running, use Portrait Manager to switch portraits; the native `ImageLoader.unit()` hook must pick up manifest changes and replace future returned textures.

The selected portrait is remembered per character. On the next launcher start, GPMI restores the previous active selection from `mod_meta.json` and rewrites the live manifest if the metadata had to be refreshed.

### Importing Existing Game MOD Portraits

For the current HilichurlsAmbition target, the game may already contain portrait mods in:

```text
<game exe folder>/MOD/Unit
<game exe folder>/MOD/Unit_H
```

Portrait Manager can import these with `Auto Import Game Battle Portrait Mods`.

Import rules:

- The same file name must exist in both `MOD/Unit` and `MOD/Unit_H`.
- File names must follow `character_h_*`, for example `amber_h_default.png`.
- The `character` part is used as the character folder under `GPMI/Mods`.
- The suffix after `character_h_` is used as the mod folder name.
- Both normal and high-resolution images are copied into the required `Unit` and `Unit_H` layout.

### Language Support

The launcher language setting controls Portrait Manager text. English and Chinese translations are provided for the Portrait Manager, GPMI settings, and Portrait Manager entry buttons.

## Implementation Notes

The original game function currently resolves portraits like this:

```gdscript
func unit(type, action, high_resolution = false):
    var directory = "Unit/"
    if high_resolution:
        directory = "Unit_H/"
    var path = directory + type + "_" + action
    var _r = Mload(path)
    if _r:
        return _r
    elif action != "default":
        path = directory + type + "_default"
        _r = Mload(path)
        if _r:
            return _r
    if high_resolution:
        return null
    return preload("res://Unit/adventurer_default.png")
```

Because fallback behavior lives inside `unit()`, GPMI must hook the function boundary. Lower-level file or resource hooks cannot safely reproduce the same behavior.
