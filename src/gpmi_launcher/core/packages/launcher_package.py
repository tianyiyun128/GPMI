import sys
import logging
import subprocess
import time

import winshell
import pythoncom
import winreg

from dataclasses import dataclass, field
from pathlib import Path

import core.path_manager as Paths
import core.event_manager as Events
import core.config_manager as Config

from core.locale_manager import L
from core.package_manager import Package, PackageMetadata

from core.utils.proxy import ProxyConfig
from core.utils.process_tracker import wait_for_process, WaitResult

log = logging.getLogger(__name__)


@dataclass
class LauncherManagerConfig:
    auto_update: bool = False
    pre_release: bool = False
    update_channel: str = 'Disabled'
    auto_close: bool = False
    start_timeout: int = 30
    gui_theme: str = 'Default'
    theme_mode: str = 'System'
    active_importer: str = 'GPMI'
    enabled_importers: list = field(default_factory=lambda: ['GPMI'])
    log_level: str = 'DEBUG'
    config_version: str = ''
    theme_dev_mode: bool = False
    github_token: str = ''
    verify_ssl: bool = True
    proxy: ProxyConfig = field(default_factory=lambda: ProxyConfig())
    credits_shown: bool = False
    locale: str = ''


@dataclass
class LauncherManagerEvents:

    @dataclass
    class Update:
        pass

    @dataclass
    class CreateShortcut:
        pass


class LauncherPackage(Package):
    def __init__(self):
        super().__init__(PackageMetadata(
            package_name='Launcher',
            auto_load=True,
            github_repo_owner='tianyiyun128',
            github_repo_name='GPMI',
            asset_version_pattern=r'v?(\d+\.\d+\.\d+).*',
            asset_name_format='GPMI*.msi',
            signature_pattern=None,
            signature_public_key=None,
            exit_after_update=True,
        ))
        self.subscribe(Events.LauncherManager.CreateShortcut, lambda event: self.create_shortcut())

        self.upgrade_installation()

    def get_installed_version(self):
        if '__compiled__' in globals() or getattr(sys, 'frozen', False):
            return self.get_file_version(sys.executable, max_parts=3)
        else:
            return '0.0.0'

    def get_last_installed_version(self):
        return self.get_installed_version()

    def update_available(self):
        return super().update_available()


    def detect_latest_version(self):
        return super().detect_latest_version()

    def download_latest_version(self):
        return super().download_latest_version()

    def install_latest_version(self, clean):
        Events.Fire(Events.PackageManager.InitializeInstallation())

        if self.downloaded_asset_path is None or not self.downloaded_asset_path.is_file():
            raise FileNotFoundError('Downloaded GPMI installer was not found.')

        cmd = f'msiexec /i "{self.downloaded_asset_path}" /qr /norestart APPDIR="{Paths.App.Root}" CREATE_SHORTCUTS=""'
        log.debug(f'Calling `{cmd}`...')
        subprocess.Popen(cmd, shell=True)

        installer_process_name = 'msiexec.exe'

        Events.Fire(Events.Application.StatusUpdate(status=L('status_waiting_installer', 'Waiting for installer to start...')))

        result, pid = wait_for_process(installer_process_name, with_window=False, timeout=15)
        if result == WaitResult.Timeout:
            raise ValueError(L('error_launcher_installer_start_failed', """
                Failed to start {asset_name}!
                
                Was it blocked by Antivirus software or security settings?
            """).format(asset_name=self.downloaded_asset_path.name))

        time.sleep(1)

    def detect_update_channel(self):
        try:
            launcher_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\SpectrumQT\\XXMI Launcher', 0, winreg.KEY_READ)
        except FileNotFoundError:
            return 'ZIP'

        try:
            (path_value, regtype) = winreg.QueryValueEx(launcher_key, 'Path')
            if regtype != winreg.REG_SZ:
                return 'ZIP'
        except FileNotFoundError:
            return 'ZIP'

        if Path(path_value) != Paths.App.Root:
            return 'ZIP'

        return 'MSI'

    def update(self, clean=False):
        super().update(clean=clean)

    def upgrade_installation(self):
        # Grab old version info from config
        old_version = Config.Launcher.config_version

        # Grab new version info from exe
        new_version = self.get_installed_version()

        # Exit early if no version upgrade required
        if old_version == new_version:
            return

        # Upgrade existing config to the latest version
        Config.Config.upgrade(old_version, new_version)

        # Exit early if old version is empty (aka fresh installation)
        if not old_version:
            return

    def create_shortcut(self):
        pythoncom.CoInitialize()

        with winshell.shortcut(str(Path(winshell.desktop()) / f'GPMI.lnk')) as link:
            link.path = str(Path(sys.executable))
            link.description = L('launcher_shortcut_description', 'Shortcut to GPMI')
            link.working_directory = str(Paths.App.Resources / 'Bin')
            link.icon_location = (str(Path(sys.executable)), 0)

        with winshell.shortcut(str(Paths.App.Root / f'GPMI.lnk')) as link:
            link.path = str(Path(sys.executable))
            link.description = L('launcher_shortcut_description', 'Shortcut to GPMI')
            link.working_directory = str(Paths.App.Resources / 'Bin')
            link.icon_location = (str(Path(sys.executable)), 0)

    def uninstall(self):
        log.debug(f'Uninstalling package {self.metadata.package_name}...')

        shortcut_path = Path(winshell.desktop()) / f'GPMI.lnk'
        if shortcut_path.is_file():
            log.debug(f'Removing {shortcut_path}...')
            shortcut_path.unlink()

        shortcut_path = Paths.App.Root / f'GPMI.lnk'
        if shortcut_path.is_file():
            log.debug(f'Removing {shortcut_path}...')
            shortcut_path.unlink()
