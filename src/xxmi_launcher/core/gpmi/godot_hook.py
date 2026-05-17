from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)

GODOT_HOOK_DIR = 'GodotHook'
GODOT_HOOK_SCRIPT = 'Install_88.gd'
GODOT_HOOK_DLL = 'GPMIGodotHook.dll'


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def install_godot_hook_assets(game_exe_path: Path, importer_path: Path, profile_dir: Path) -> Dict[str, object]:
    """Install the Godot-side bridge files next to the target game.

    The supported in-game hook loads `Install_*.gd` files from the game's MOD
    folder. The launcher mirrors the same files into the GPMI profile for
    inspection/debugging, but the effective installation target is
    `<game exe folder>/MOD/Install_88.gd`.
    """
    game_dir = Path(game_exe_path).resolve().parent
    importer_path = Path(importer_path).resolve()
    profile_dir = Path(profile_dir).resolve()

    source_dir = importer_path / 'Core' / GODOT_HOOK_DIR
    game_mod_dir = game_dir / 'MOD'
    profile_hook_dir = profile_dir / GODOT_HOOK_DIR

    installed: List[str] = []
    missing: List[str] = []

    files = [GODOT_HOOK_SCRIPT]
    for name in files:
        src = source_dir / name
        copied_any = False
        for target_dir in (game_mod_dir, profile_hook_dir):
            if _copy_if_exists(src, target_dir / name):
                installed.append(str((target_dir / name).resolve()))
                copied_any = True
        if not copied_any:
            missing.append(str(src))

    # Optional native DLL companion. It is not injected by the launcher. If built,
    # it is mirrored beside the profile hook so native Godot/mod-loader setups can
    # load it explicitly without ReShade.
    dll_src = importer_path / 'Core' / 'GPMI' / GODOT_HOOK_DLL
    if dll_src.is_file():
        for target_dir in (profile_hook_dir, game_mod_dir):
            if _copy_if_exists(dll_src, target_dir / GODOT_HOOK_DLL):
                installed.append(str((target_dir / GODOT_HOOK_DLL).resolve()))

    status = {
        'version': 1,
        'hook': 'godot_live_bridge',
        'game_exe': str(Path(game_exe_path).resolve()),
        'profile_dir': str(profile_dir),
        'game_mod_dir': str(game_mod_dir),
        'source_dir': str(source_dir),
        'installed': installed,
        'missing': missing,
    }
    profile_hook_dir.mkdir(parents=True, exist_ok=True)
    (profile_hook_dir / 'install_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')

    if missing:
        log.warning('GPMI Godot hook assets missing: %s', missing)
    else:
        log.info('Installed GPMI Godot hook assets: %s', installed)
    return status
