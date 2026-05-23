import logging
import json

from pathlib import Path, PurePosixPath
from dataclasses import dataclass, field, fields
from typing import Union, Dict, Any, Optional, List

from dacite import from_dict

import core.path_manager as Paths

from core.locale_manager import L
from core.embedded_resources import EmbeddedResourcePath, EmbeddedResources
from core import package_manager
from core.packages import launcher_package
from core.packages.model_importers import gpmi_package

log = logging.getLogger(__name__)


@dataclass
class ImportersConfig:
    GPMI: gpmi_package.GPMIPackageConfig = field(default_factory=lambda: gpmi_package.GPMIPackageConfig())


@dataclass
class AppConfig:
    # Config fields
    Launcher: launcher_package.LauncherManagerConfig = field(
        default_factory=lambda: launcher_package.LauncherManagerConfig()
    )
    Packages: package_manager.PackageManagerConfig = field(
        default_factory=lambda: package_manager.PackageManagerConfig()
    )
    Importers: ImportersConfig = field(
        default_factory=lambda: ImportersConfig()
    )
    # State fields
    active_theme: Optional[str] = field(init=False, default=None)

    def __post_init__(self):
        self.active_theme = 'Default'

    @property
    def config_path(self):
        return Paths.App.Root / 'GPMI Config.json'

    @property
    def legacy_config_path(self):
        return Paths.App.Root / 'XXMI Launcher Config.json'

    @property
    def Active(self) -> gpmi_package.GPMIPackageConfig:
        global Active
        return Active

    def as_dict(self, obj: Any) -> Dict[str, Any]:
        result = {}

        if hasattr(obj, '__dataclass_fields__'):
            # Process dataclass object
            for obj_field in fields(obj):
                # Fields with 'init=False' contain app state data that isn't supposed to be saved
                if not obj_field.init:
                    continue
                # Recursively process nested dataclass
                value = getattr(obj, obj_field.name)

                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result[obj_field.name] = self.as_dict(value)
                else:
                    result[obj_field.name] = value

        elif isinstance(obj, dict):
            # Process dict object
            for obj_field, value in obj.items():
                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result[obj_field] = self.as_dict(value)
                else:
                    result[obj_field] = value

        elif isinstance(obj, list | tuple):
            # Process list or tuple object
            result = []
            for value in obj:
                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result.append(self.as_dict(value))
                else:
                    result.append(value)

        return result

    def as_json(self):
        cfg = self.as_dict(self)
        return json.dumps(cfg, indent=4)

    def from_json(self, config_path: Path):
        cfg = self.as_dict(self)
        if config_path.is_file():
            cfg.update(json.loads(Paths.App.read_text(config_path)))
        # Legacy XXMI/GPMI builds stored local signing state here. GPMI no longer
        # generates per-machine keys, so ignore the old node when loading config.
        cfg.pop('Security', None)
        for key, value in from_dict(data_class=AppConfig, data=cfg).__dict__.items():
            if hasattr(self, key):
                setattr(self, key, value)
        if self.Launcher.gui_theme:
            self.active_theme = self.Launcher.gui_theme

    def load(self, cfg_path=None):
        try:
            if cfg_path is None and not self.config_path.is_file() and self.legacy_config_path.is_file():
                cfg_path = self.legacy_config_path
            Config.from_json(cfg_path or self.config_path)
        except Exception as e:
            log.exception(e)
            raise e
        finally:
            global Launcher
            Launcher = self.Launcher
            global Packages
            Packages = self.Packages
            global Importers
            Importers = self.Importers

    def save(self):
        Paths.App.write_file(self.config_path, Config.as_json())

    def run_patch_195(self):
        pass

    def run_patch_201(self):
        pass

    def run_patch_216(self):
        pass

    def run_patch_219(self):
        try:
            importer = self.Importers.__dict__.get('GPMI')
            if importer is not None:
                importer.Migoto.enforce_rendering = True
        except Exception:
            pass

    def upgrade(self, old_version, new_version):
        # Save config to file and exit early if old version is empty (aka fresh installation)
        if not old_version:
            log.debug(f'Saving new config...')
            self.Launcher.config_version = new_version
            self.save()
            return

        # Apply patches
        patches = {
            '1.9.5': self.run_patch_195,
            '2.0.1': self.run_patch_201,
            '2.1.6': self.run_patch_216,
            '2.1.9': self.run_patch_219,
        }
        applied_patches = []
        for patch_version, patch_func in patches.items():
            if old_version < patch_version:
                log.debug(f'Upgrading launcher config from {old_version} to {patch_version}...')
                patch_func()
                applied_patches.append(patch_version)

        # Save patched config to file
        if len(applied_patches) > 0:
            log.debug(f'Saving patched config...')
            self.Launcher.config_version = new_version
            self.save()

Config: AppConfig = AppConfig()

# Config aliases, intended to shorten dot names
Launcher: launcher_package.LauncherManagerConfig
Packages: package_manager.PackageManagerConfig
Importers: ImportersConfig
Active: gpmi_package.GPMIPackageConfig


def get_resource_path(element, filename: Union[str, Path], extensions: Optional[Union[str, List[str]]] = None):
    filename = PurePosixPath(str(filename).replace('\\', '/'))
    search_extensions = [filename.suffix]
    if extensions is not None:
        search_extensions += [ext for ext in list(extensions) if ext != filename.suffix]
    class_path = PurePosixPath(str(element.get_resource_path()).replace('\\', '/')) / filename
    theme_names = [Config.active_theme]
    if Config.active_theme != 'Default':
        theme_names.append('Default')

    for theme_name in theme_names:
        for extension in search_extensions:
            resource_path = PurePosixPath('Themes') / theme_name / class_path.with_suffix(extension)
            if EmbeddedResources.is_file(resource_path):
                return EmbeddedResourcePath(resource_path)

    fallback_resource_path = PurePosixPath('Themes') / 'Default' / class_path
    raise FileNotFoundError(L('error_theme_resource_not_found', """
        Embedded resource not found:
        
        {resource_path}
        
        Hint: You can also use other extensions: {extensions}
    """).format(
        resource_path=fallback_resource_path,
        extensions=", ".join(extensions or []))
    )
