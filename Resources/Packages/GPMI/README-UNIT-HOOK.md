# GPMI Unit Hook Runtime Design

This document records the only accepted runtime design for portrait replacement in GPMI.

## Goal

GPMI must replace the return value of the game function:

```text
ImageLoader.unit(type, action, high_resolution)
```

Only the two portrait groups below are in scope:

```text
Unit
Unit_H
```

The runtime must not rely on ReShade, GPU texture hashes, path shadowing, generated MOD files, or pre-populating `ImageLoader.image_cache`.

## Required behavior

The native runtime must run inside the game process and intercept calls to `ImageLoader.unit(type, action, high_resolution)`.

For every call:

1. Capture the original arguments:
   - `type`
   - `action`
   - `high_resolution`
2. Call the original `ImageLoader.unit(...)` implementation.
3. Build the logical key from the actual call:
   - `Unit/<type>_<action>` when `high_resolution == false`
   - `Unit_H/<type>_<action>` when `high_resolution == true`
4. Read GPMI's live manifest:
   - `<game exe folder>/GPMI/live_portraits.json`
5. If the manifest has an enabled rule matching that logical key, the native runtime must read the replacement image file itself.
6. Convert the replacement image into a Godot texture object compatible with the original return type.
7. Replace the return value of `ImageLoader.unit(...)` with that texture object.
8. If no matching replacement exists, return the original function result unchanged.

## Why this must be done at `unit()` return

`ImageLoader.unit()` contains game-specific fallback behavior. It can remap actions and can fall back to default portraits. Replacing files on disk or pre-filling the image cache does not reliably preserve the game's real call semantics, especially on the first read or when the cache is empty.

Therefore, the replacement decision must happen after the original `unit()` function has received the real arguments and produced its original return value.

## Manifest contract

The launcher owns user workflow and writes:

```text
<game exe folder>/GPMI/live_portraits.json
```

The native runtime reads this manifest at runtime. The expected rule fields are:

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

`slot` must be either `Unit` or `Unit_H`. Other image groups are out of scope.

## Runtime log requirements

The native runtime must write a log file next to the manifest:

```text
<game exe folder>/GPMI/GPMIUnitHook.log
```

The log must include:

- runtime load/unload
- manifest path
- manifest revision changes
- every matched `unit()` call
- every replacement image read
- every replaced return value
- skipped calls and the reason
- image decode failures
- texture creation failures

## Explicitly rejected approaches

The following approaches are not valid for GPMI:

- ReShade add-ons
- ReShade DLL loading
- GPU hash replacement
- writing generated files into `MOD/Unit` or `MOD/Unit_H`
- changing ImageLoader search paths
- pre-populating `ImageLoader.image_cache`
- adding `.gd` files to this repository
- relying on the cache layer instead of the `unit()` call return value

## Launcher responsibility

The launcher should only:

1. let the user import/select portrait images;
2. write `mod_meta.json`;
3. write `live_portraits.json`;
4. start the game with the native runtime available.

The launcher must not implement replacement by generating Godot resource override files.

## Native runtime responsibility

The native runtime must:

1. enter the Godot game process by the chosen native loading path;
2. locate the `ImageLoader` autoload object;
3. intercept `ImageLoader.unit(type, action, high_resolution)`;
4. call the original function;
5. conditionally replace only the return value;
6. return a valid Godot texture object to the caller.
