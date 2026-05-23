import logging
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import core.event_manager as Events
import core.config_manager as Config

from core.package_manager import PackageMetadata
from core.packages.migoto_package import MigotoManagerConfig
from core.packages.model_importers.model_importer import ModelImporterConfig, ModelImporterPackage
from core.gpmi.mods import (
    GPMI_VERSION,
    META_FILE,
    RUNTIME_MANIFEST_FILE,
    build_live_portrait_manifest,
    ensure_game_profile,
    ensure_package_profile,
    game_profile_dir,
)

log = logging.getLogger(__name__)

LIVE_BRIDGE_PATCH_NAME = 'GPMILiveBridge.pck'
LEGACY_LIVE_BRIDGE_PATCH_NAME = 'patch_88.pck'


@dataclass
class GPMIConfig(ModelImporterConfig):
    """Godot Portrait Model Importer settings.

    GPMI keeps the XXMI launcher shell, but the runtime path is different:
    the Portrait Manager writes a live portrait manifest consumed by the in-game
    GPMI bridge. The original game executable is not modified.
    """
    game_exe_names: List[str] = field(default_factory=lambda: [])
    game_folder_names: List[str] = field(default_factory=lambda: [])
    game_folder_children: List[str] = field(default_factory=lambda: [])
    importer_folder: str = 'Resources/Packages/GPMI/'
    use_launch_options: bool = False
    launch_options: str = ''
    overwrite_ini: bool = False
    process_start_method: str = 'Native'
    process_timeout: int = 30

    # Generic Godot target/runtime settings used by the GPMI portrait manager.
    custom_game_exe_name: str = ''
    min_width: int = 32
    min_height: int = 32
    ptr_bgra_import: bool = False

    # Compatibility fields: the original XXMI settings windows still instantiate
    # some generic Unity/UE widgets depending on the selected importer. Keep these
    # fields on GPMI so old config files and hidden UI code cannot crash startup
    # with "GPMIConfig object has no attribute ...". They are intentionally inert
    # for GPMI unless a future GPMI UI explicitly uses them.
    process_exe_names: List[str] = field(default_factory=lambda: [])
    unlock_fps: bool = False
    unlock_fps_value: int = 120
    enable_hdr: bool = False
    force_max_lod_bias: bool = False
    disable_wounded_fx: bool = False
    apply_perf_tweaks: bool = False
    mesh_lod_distance_scale: float = 1.0
    mesh_lod_distance_offset: int = 0
    texture_streaming_boost: int = 0
    texture_streaming_min_boost: int = 0
    texture_streaming_use_all_mips: bool = False
    texture_streaming_pool_size: int = 0
    texture_streaming_limit_to_vram: bool = False
    texture_streaming_fixed_pool_size: bool = False
    engine_ini: Dict[str, Dict[str, Union[str, int, float, bool]]] = field(default_factory=lambda: {})
    perf_tweaks: Dict[str, Dict[str, Dict[str, Union[str, int, float, bool]]]] = field(default_factory=lambda: {})

    d3dx_ini: Dict[str, Dict[str, Dict[str, Union[str, int, float, Dict[str, Union[str, int, float]]]]]] = field(default_factory=lambda: {})


@dataclass
class GPMIPackageConfig:
    Importer: GPMIConfig = field(default_factory=lambda: GPMIConfig())
    Migoto: MigotoManagerConfig = field(default_factory=lambda: MigotoManagerConfig())


class GPMIPackage(ModelImporterPackage):
    def __init__(self):
        super().__init__(PackageMetadata(
            package_name='GPMI',
            auto_load=False,
            installation_path='GPMI/',
            requirements=[],
            # XXMI's Package base class always initializes Security(public_key=...).
            # GPMI is a local/offline package, but this field still must contain a
            # syntactically valid DER public key string or launcher startup fails.
            signature_public_key='MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAEYac352uRGKZh6LOwK0fVDW/TpyECEfnRtUp+bP2PJPP63SWOkJ3a/d9pAnPfYezRVJ1hWjZtpRTT8HEAN/b4mWpJvqO43SAEV/1Q6vz9Rk/VvRV3jZ6B/tmqVnIeHKEb',
            exit_after_update=False,
        ))
        self.use_hook = False

    def detect_latest_version(self):
        self.cfg.latest_version = self.get_installed_version()
        self.cfg.latest_release_notes = 'Local GPMI package bundled with this launcher build.'
        self.download_url = ''
        self.signature = ''
        self.manifest_url = None

    def update_available(self):
        return False

    def get_installed_version(self):
        try:
            ensure_package_profile(Config.Importers.GPMI.Importer.importer_path)
            return GPMI_VERSION
        except Exception:
            log.exception('Failed to initialize GPMI profile')
            return ''

    def install_latest_version(self, clean):
        Events.Fire(Events.PackageManager.InitializeInstallation())
        ensure_package_profile(Config.Active.Importer.importer_path)

    def validate_package_files(self):
        ensure_package_profile(Config.Active.Importer.importer_path)

    def create_shortcut(self):
        # Generic Godot targets do not have a stable executable name, so GPMI avoids
        # deploying an automatic quick-start shortcut until the user explicitly builds one.
        Config.Active.Importer.shortcut_deployed = True

    def optimize_mods(self, event):
        ensure_package_profile(Config.Active.Importer.importer_path)
        if not event.silent:
            Events.Fire(Events.Application.ShowInfo(
                modal=True,
                title='GPMI',
                message='GPMI imports portrait images through the Portrait Manager; there are no 3DMigoto INI files to optimize.'
            ))

    def _configured_game_exe_path(self) -> Optional[Path]:
        """Return the exact game executable selected by the user.

        GPMI is intentionally single-target: the launcher stores and starts one
        explicit Godot game .exe. Folder auto-detection/scanning from the original
        XXMI importers is disabled so sibling tools/editors never trigger the old
        "multiple .exe files" path.
        """
        cfg = Config.Active.Importer
        configured = str(getattr(cfg, 'game_folder', '') or '').strip().strip('"')
        if not configured:
            return None
        return Path(configured)

    def validate_game_path(self, game_folder) -> Path:
        configured_path = Path(str(game_folder or '').strip().strip('"'))
        if not str(configured_path):
            raise ValueError('Game executable is not configured. Select the exact Godot game .exe first.')
        if configured_path.suffix.lower() != '.exe':
            raise ValueError(
                'GPMI requires an exact game executable path, not a folder. '
                'Click Browse and select the Godot game .exe.'
            )
        if not configured_path.is_absolute():
            raise ValueError(f'Game executable path must be absolute: {configured_path}')
        if not configured_path.is_file():
            raise ValueError(f'Game executable not found: {configured_path}')
        return configured_path.parent

    def validate_game_exe_path(self, game_path: Path) -> Path:
        explicit_exe = self._configured_game_exe_path()
        if explicit_exe is None:
            raise ValueError('Game executable is not configured. Select the exact Godot game .exe first.')
        if not explicit_exe.is_absolute():
            explicit_exe = game_path / explicit_exe
        if explicit_exe.suffix.lower() != '.exe':
            raise ValueError(f'Configured game path is not an .exe: {explicit_exe}')
        if not explicit_exe.is_file():
            raise ValueError(f'Selected game executable not found: {explicit_exe}')
        return explicit_exe

    def detect_game_paths(self, supress_errors=False):
        # A generic Godot game cannot be reliably auto-detected. Use Settings > General > Game Executable.
        if supress_errors:
            return []
        raise UserWarning('Automatic game detection is not supported for generic GPMI targets.')

    def get_game_paths(self):
        game_path = self.validate_game_path(Config.Active.Importer.game_folder)
        game_exe_path = self.validate_game_exe_path(game_path)
        # Keep runtime config normalized for later reads in the same session.
        Config.Active.Importer.custom_game_exe_name = game_exe_path.name
        return game_path, game_exe_path

    def _ensure_live_bridge_patch(self, game_exe_path: Path) -> None:
        """Check for the Godot-side live bridge patch used by portrait refresh."""
        game_dir = game_exe_path.resolve().parent
        mod_dir = game_dir / 'MOD'
        active_bridge = mod_dir / LIVE_BRIDGE_PATCH_NAME
        legacy_bridge = mod_dir / LEGACY_LIVE_BRIDGE_PATCH_NAME
        disabled_dir = game_dir / 'HAModManagerData' / 'MOD_disabled'
        disabled_bridge = disabled_dir / LIVE_BRIDGE_PATCH_NAME
        disabled_legacy_bridge = disabled_dir / LEGACY_LIVE_BRIDGE_PATCH_NAME
        bundled_bridge = Config.Active.Importer.importer_path / 'Runtime' / LIVE_BRIDGE_PATCH_NAME

        if active_bridge.is_file():
            log.info('GPMI live bridge patch is active: %s', active_bridge)
            return

        if legacy_bridge.is_file():
            log.info('Legacy GPMI live bridge patch is active: %s', legacy_bridge)
            return

        if bundled_bridge.is_file():
            try:
                mod_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundled_bridge, active_bridge)
                log.info('Installed GPMI live bridge patch: %s', active_bridge)
                return
            except Exception as exc:
                raise ValueError(f'Failed to install {LIVE_BRIDGE_PATCH_NAME} into {mod_dir}: {exc}') from exc

        if disabled_bridge.is_file() or disabled_legacy_bridge.is_file():
            log.warning('GPMI live bridge patch exists but is disabled under %s', disabled_dir)
            return

        log.warning(
            'GPMI live bridge patch is missing. Portrait selections will write %s, '
            'but the game will not refresh portraits until %s is installed under %s.',
            RUNTIME_MANIFEST_FILE,
            LIVE_BRIDGE_PATCH_NAME,
            mod_dir,
        )

    def _prepare_runtime_profile(self):
        importer_path = Config.Active.Importer.importer_path
        ensure_package_profile(importer_path)
        game_path = self.validate_game_path(Config.Active.Importer.game_folder)
        game_exe_path = self.validate_game_exe_path(game_path)
        profile_dir = game_profile_dir(game_exe_path)
        runtime_manifest = profile_dir / RUNTIME_MANIFEST_FILE
        meta_path = profile_dir / META_FILE
        ensure_game_profile(profile_dir)
        self._ensure_live_bridge_patch(game_exe_path)

        needs_runtime_rebuild = not runtime_manifest.is_file()
        if runtime_manifest.is_file() and meta_path.is_file() and meta_path.stat().st_mtime > runtime_manifest.stat().st_mtime:
            needs_runtime_rebuild = True

        if needs_runtime_rebuild:
            try:
                build_live_portrait_manifest(profile_dir)
            except Exception:
                log.exception('Failed to build initial GPMI live portrait manifest')

    def get_start_cmd(self, game_path: Path) -> Tuple[Path, List[str], Optional[str]]:
        game_exe_path = self.validate_game_exe_path(game_path)
        start_args: List[str] = []
        if Config.Active.Importer.use_launch_options and Config.Active.Importer.launch_options.strip():
            # Windows command lines often contain quoted paths/values. shlex with
            # posix=False preserves Windows quoting better than a raw split().
            start_args = shlex.split(Config.Active.Importer.launch_options, posix=False)
        return game_exe_path, start_args, str(game_exe_path.parent)

    def start_game(self, event):
        self.validate_package_files()
        self._prepare_runtime_profile()

        game_path, game_exe_path = self.get_game_paths()
        start_exe_path, start_args, work_dir = self.get_start_cmd(game_path)

        from core.utils.process_tracker import get_hwnds_for_pid

        process = subprocess.Popen([str(start_exe_path), *start_args], cwd=work_dir)
        pid = process.pid
        Config.Active.Importer.runtime_game_pid = pid

        Events.Fire(Events.Application.WaitForProcess(process_name=game_exe_path.name))
        deadline = time.time() + max(1, int(Config.Active.Importer.process_timeout))
        while time.time() < deadline:
            if process.poll() is not None:
                raise ValueError(f'{game_exe_path.name} exited before its window appeared.')
            if get_hwnds_for_pid(pid=pid, check_visibility=True):
                time.sleep(0.5)
                return
            time.sleep(0.1)
        raise ValueError(f'Failed to detect game window for {game_exe_path.name}.')
