import logging
import os
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

    def _prepare_reshade_config(self, reshade_dll: Path):
        """Create the minimal ReShade config required for LoadLibrary injection.

        ReShade intentionally aborts initialization when it is loaded as a normal
        DLL named ReShade64.dll and no ReShade.ini exists for the target app.
        GPMI keeps ReShade outside the game folder, so we point ReShade at the
        GPMI runtime directory and keep the add-on search path local.
        """
        reshade_base = reshade_dll.parent
        (reshade_base / 'Addons').mkdir(parents=True, exist_ok=True)
        reshade_ini = reshade_base / 'ReShade.ini'
        reshade_ini.write_text(
            '[INSTALL]\n'
            'BasePath=.\n'
            'Logging=1\n'
            '\n'
            '[ADDON]\n'
            'AddonPath=Addons\n'
            '\n'
            '[GENERAL]\n'
            'PresetPath=.\\GPMI_ReShadePreset.ini\n',
            encoding='utf-8'
        )
        preset = reshade_base / 'GPMI_ReShadePreset.ini'
        if not preset.exists():
            preset.write_text('[General]\n', encoding='utf-8')
        return reshade_base

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

        reshade_base = self._prepare_reshade_config(reshade_dll)

        from core.gpmi.win_reshade_injector import start_suspended_and_inject_dll
        from core.utils.process_tracker import get_hwnds_for_pid
        import psutil

        Events.Fire(Events.Application.Inject(library_name=reshade_dll.name, process_name=game_exe_path.name))
        pid = start_suspended_and_inject_dll(
            exe_path=start_exe_path,
            args=start_args,
            work_dir=work_dir,
            dll_path=reshade_dll,
            timeout_seconds=Config.Active.Importer.process_timeout,
            env_overrides={
                'RESHADE_BASE_PATH_OVERRIDE': str(reshade_base),
                # ReShade otherwise rejects non-proxy LoadLibrary loads when no
                # per-game ReShade.ini exists next to the Godot executable.
                'RESHADE_DISABLE_LOADING_CHECK': '1',
            },
        )

        Events.Fire(Events.Application.WaitForProcess(process_name=game_exe_path.name))
        deadline = time.time() + max(1, int(Config.Active.Importer.process_timeout))
        while time.time() < deadline:
            if not psutil.pid_exists(pid):
                raise ValueError(f'{game_exe_path.name} exited before its window appeared.')
            if get_hwnds_for_pid(pid=pid, check_visibility=True):
                time.sleep(0.5)
                return
            time.sleep(0.1)
        raise ValueError(f'Failed to detect game window for {game_exe_path.name}.')
