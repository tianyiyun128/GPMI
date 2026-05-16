import logging
import os
import subprocess
import time
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import core.event_manager as Events
import core.path_manager as Paths
import core.config_manager as Config

from core.locale_manager import L
from core.package_manager import PackageMetadata
from core.packages.migoto_package import MigotoManagerConfig
from core.packages.model_importers.model_importer import ModelImporterConfig, ModelImporterPackage
from core.gpmi.profile import GPMI_VERSION, ensure_profile, load_hash_db, write_runtime_ini

log = logging.getLogger(__name__)


@dataclass
class GPMIConfig(ModelImporterConfig):
    """Godot Portrait Model Importer settings.

    GPMI keeps the XXMI launcher shell, but the runtime path is different:
    a ReShade add-on hashes uploaded textures and replaces matching uploads from a
    local hash_db.json profile. The original game executable is not modified.
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
    reshade_dll_path: str = ''
    addon_dll_path: str = ''
    dump_unknown: bool = True
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
            requirements=['XXMI'],
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
            ensure_profile(Config.Importers.GPMI.Importer.importer_path)
            return GPMI_VERSION
        except Exception:
            log.exception('Failed to initialize GPMI profile')
            return ''

    def install_latest_version(self, clean):
        Events.Fire(Events.PackageManager.InitializeInstallation())
        ensure_profile(Config.Active.Importer.importer_path)

    def validate_package_files(self):
        ensure_profile(Config.Active.Importer.importer_path)
        Paths.verify_path(Config.Active.Importer.importer_path / 'Mods')

    def create_shortcut(self):
        # Generic Godot targets do not have a stable executable name, so GPMI avoids
        # deploying an automatic quick-start shortcut until the user explicitly builds one.
        Config.Active.Importer.shortcut_deployed = True

    def optimize_mods(self, event):
        ensure_profile(Config.Active.Importer.importer_path)
        if not event.silent:
            Events.Fire(Events.Application.ShowInfo(
                modal=True,
                title='GPMI',
                message='GPMI uses hash_db.json and PTRTEX files; there are no 3DMigoto INI files to optimize.'
            ))

    def validate_game_path(self, game_folder) -> Path:
        game_path = Path(game_folder)
        if not str(game_folder):
            raise ValueError('Game folder is not configured.')
        if not game_path.is_absolute() or not game_path.is_dir():
            raise ValueError(f'Game folder not found: {game_path}')
        return game_path

    def _candidate_exe_names(self) -> List[str]:
        cfg = Config.Active.Importer
        names = []
        if cfg.custom_game_exe_name:
            names.append(cfg.custom_game_exe_name)
        names.extend(cfg.game_exe_names)
        return [name for name in names if name]

    def validate_game_exe_path(self, game_path: Path) -> Path:
        for exe_name in self._candidate_exe_names():
            exe_path = game_path / exe_name
            if exe_path.is_file():
                return exe_path
        candidates = [p for p in game_path.glob('*.exe') if 'crash' not in p.name.lower() and 'unins' not in p.name.lower()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ', '.join(p.name for p in candidates[:10])
            raise ValueError(
                'Multiple .exe files were found. Set custom_game_exe_name in GPMI settings/config. '\
                f'Candidates: {names}'
            )
        raise ValueError('No .exe file was found in the configured Godot game folder.')

    def detect_game_paths(self, supress_errors=False):
        # A generic Godot game cannot be reliably auto-detected. Use Settings > General > Game Folder.
        if supress_errors:
            return []
        raise UserWarning('Automatic game detection is not supported for generic GPMI targets.')

    def get_game_paths(self):
        game_path = self.validate_game_path(Config.Active.Importer.game_folder)
        game_exe_path = self.validate_game_exe_path(game_path)
        return game_path, game_exe_path

    def _resolve_runtime_dll(self, configured_path: str, default_rel: str, label: str) -> Path:
        if configured_path:
            path = Path(configured_path)
            if not path.is_absolute():
                path = Paths.App.Root / path
        else:
            path = Config.Active.Importer.importer_path / default_rel
        if not path.is_file():
            raise ValueError(
                f'{label} not found: {path}\n\n'
                'Open the GPMI Portrait Manager and copy/build the required runtime DLLs into Core/GPMI.'
            )
        return path

    def _prepare_runtime_profile(self):
        importer_path = Config.Active.Importer.importer_path
        ensure_profile(importer_path)
        db = load_hash_db(importer_path / 'hash_db.json')
        db.dump_unknown = Config.Active.Importer.dump_unknown
        db.min_width = Config.Active.Importer.min_width
        db.min_height = Config.Active.Importer.min_height
        write_runtime_ini(importer_path, db)

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

        reshade_dll = self._resolve_runtime_dll(
            Config.Active.Importer.reshade_dll_path,
            'Core/GPMI/ReShade64.dll',
            'ReShade64.dll'
        )
        addon_dll = self._resolve_runtime_dll(
            Config.Active.Importer.addon_dll_path,
            'Core/GPMI/PortraitHashReplace.addon64',
            'PortraitHashReplace.addon64'
        )

        # ReShade looks for add-ons next to its own base path. The launcher keeps the
        # add-on in Core/GPMI and also mirrors it into Addons for common ReShade layouts.
        addon_mirror = Config.Active.Importer.importer_path / 'Core/GPMI/Addons' / addon_dll.name
        if addon_mirror.resolve() != addon_dll.resolve():
            addon_mirror.parent.mkdir(parents=True, exist_ok=True)
            try:
                import shutil
                shutil.copy2(addon_dll, addon_mirror)
            except Exception:
                log.exception('Failed to mirror GPMI add-on')

        try:
            xxmi_package = self.manager.get_package('XXMI')
            injector_lib = xxmi_package.package_path / '3dmloader.dll'
        except Exception as e:
            raise ValueError('XXMI runtime injector package is not installed. Install/repair XXMI first.') from e

        from core.utils.dll_injector import DllInjector
        from core.utils.process_tracker import wait_for_process, WaitResult

        Events.Fire(Events.Application.Inject(library_name=reshade_dll.name, process_name=game_exe_path.name))
        injector = DllInjector(injector_lib, load_inject=True)
        try:
            injector.open_process(
                start_method=Config.Active.Importer.process_start_method,
                exe_path=str(start_exe_path),
                work_dir=work_dir,
                start_args=start_args,
                process_flags=subprocess.NORMAL_PRIORITY_CLASS,
                process_name=game_exe_path.name,
                dll_paths=[reshade_dll],
                cmd=None,
                inject_timeout=Config.Active.Importer.process_timeout,
            )
            Events.Fire(Events.Application.WaitForProcess(process_name=game_exe_path.name))
            result, _pid = wait_for_process(game_exe_path.name, with_window=True, timeout=Config.Active.Importer.process_timeout, check_visibility=True)
            if result == WaitResult.Timeout:
                raise ValueError(f'Failed to detect game window for {game_exe_path.name}.')
            time.sleep(0.5)
        finally:
            try:
                injector.unload()
            except Exception:
                log.exception('Failed to unload injector')
