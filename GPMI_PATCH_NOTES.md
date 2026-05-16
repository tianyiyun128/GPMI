# GPMI integration patch

This fork keeps the XXMI Launcher UI and adds a new importer named **GPMI** for Godot portrait replacement.

## What was added

- `GPMI` model importer registration in the launcher package system.
- Top-bar importer button and toolbox entry.
- Internal `GPMI Portrait Manager` window.
- Local runtime profile under `Resources/Packages/GPMI`.
- Hash database management via `hash_db.json`.
- PNG → PTRTEX import using Pillow.
- Dump browser for unknown texture uploads.
- Runtime settings for Godot exe name, ReShade DLL path and add-on path.
- ReShade add-on kernel source under `Resources/Packages/GPMI/Tools/kernel_source`.

## Runtime model

1. Launcher starts the original Godot `.exe`.
2. Launcher injects `ReShade64.dll` using XXMI's existing injector package.
3. ReShade loads `PortraitHashReplace.addon64`.
4. The add-on hashes 2D texture uploads.
5. If a hash matches `hash_db.json`, the upload data is replaced with the selected PTRTEX texture.

The original executable is not patched.

## Required external runtime files

The source tree intentionally does not include third-party binary DLLs. Put these files here after building/installing them:

- `Resources/Packages/GPMI/Core/GPMI/ReShade64.dll`
- `Resources/Packages/GPMI/Core/GPMI/PortraitHashReplace.addon64`

Build the add-on from `Resources/Packages/GPMI/Tools/kernel_source`. The CMake project expects ReShade add-on headers.

## Notes

- This is an MVP integration with the real XXMI UI shell. It cannot be guaranteed to work on every Godot renderer path until tested against the target packaged exe.
- The hash replacement approach intentionally avoids relying on the original GDScript `config.user_path + "/MOD/"` file reads.
- GPLv3 redistribution is compatible with using the XXMI Launcher source under its existing license terms, but keep upstream notices intact.
