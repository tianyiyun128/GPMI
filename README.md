# GPMI - Godot Portrait Model Importer

GPMI is a single-game Godot portrait replacement launcher derived from XXMI Launcher. It keeps the launcher shell architecture, but removes the old multi-game model-importer workflow.

## Current scope

- Start one explicitly selected Godot game `.exe`.
- Inject `Resources/Packages/GPMI/Core/GPMI/ReShade64.dll`.
- Load `PortraitHashReplace.addon64` as a ReShade add-on.
- Hash uploaded textures and replace matches from `Resources/Packages/GPMI/hash_db.json`.
- Manage dumps, `.ptrtex` replacements, and rules through the GPMI Portrait Manager.

## Important path behavior

GPMI does **not** accept a game folder. On first GUI launch it asks for the exact Godot game executable. The saved config intentionally stores the full `.exe` path in `Importers.GPMI.Importer.game_folder` for compatibility with the old settings field name.

Example:

```json
"game_folder": "C:\\Games\\ExampleGodotGame\\game.exe",
"custom_game_exe_name": "game.exe"
```

The launcher starts that exact file and never scans sibling `.exe` files in the directory.

## Runtime files

Expected runtime layout:

```text
Resources/Packages/GPMI/Core/GPMI/ReShade64.dll
Resources/Packages/GPMI/Core/GPMI/PortraitHashReplace.addon64
Resources/Packages/GPMI/hash_db.json
Resources/Packages/GPMI/Mods/
Resources/Packages/GPMI/dumps/
```

The current GPMI start path uses the local Win32 ReShade injection helper and no longer requires `Resources/Packages/XXMI`.

## Run

```powershell
python src\xxmi_launcher\app.py
```

Quick start with an explicit executable:

```powershell
python src\xxmi_launcher\app.py "C:\Games\ExampleGodotGame\game.exe"
```

## Preflight

```powershell
python Resources\Packages\GPMI\Tools\gpmi_preflight.py
```

## License

This project is based on XXMI Launcher and keeps the original GPLv3 license and copyright notices.
