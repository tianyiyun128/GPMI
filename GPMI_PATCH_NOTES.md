# GPMI Patch Notes

This fork keeps the XXMI Launcher UI shell and adds a GPMI workflow for Godot portrait replacement.

## Current Direction

GPMI no longer treats ReShade/GPU hash replacement as the runtime path.

The required runtime design is native Godot function hooking:

```gdscript
ImageLoader.unit(type, action, high_resolution = false)
```

All replaceable portrait assets must be handled through this function boundary. The runtime must read the function inputs, call the original function, then replace the final returned texture when local GPMI settings say a replacement is active.

This route is mandatory because every target portrait is loaded through `ImageLoader.gd::unit()`. The implementation must not depend on whichever internal branch `unit()` uses to get the original image.

## Launcher Responsibilities

- Select and start one explicit Godot game `.exe`.
- Manage `GPMI/Mods/<character>/<outfit>/Unit` and `Unit_H` imports.
- Assign outfit ids.
- Write `GPMI/mod_meta.json`.
- Write `GPMI/live_portraits.json`.
- Keep the launcher open while the game is running so portraits can be switched in-game.

## Native Runtime Responsibilities

- Load into the Godot game process.
- Locate the `ImageLoader` autoload object.
- Hook `ImageLoader.unit(type, action, high_resolution)`.
- Capture the actual call arguments.
- Call the original `unit()` implementation.
- Match the call against `GPMI/live_portraits.json`.
- Load the replacement image from the selected mod folder.
- Convert it to a valid Godot texture.
- Replace only the final return value.

## Rejected Legacy Work

The following older ideas are not part of the accepted design:

- ReShade injection
- ReShade add-ons
- GPU texture hash matching
- D3D12 upload replacement
- `.ptrtex` generation
- generated `RuntimeMods`
- generated `MOD/Unit` or `MOD/Unit_H` override files
- `FileAccess` or `ResourceLoader` redirection as the main mechanism
- pre-populating `ImageLoader.image_cache`

These can remain as historical context in old commits, but new implementation work must not continue down those routes.
