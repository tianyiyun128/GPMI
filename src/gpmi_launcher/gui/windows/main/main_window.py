import ctypes
import json
import logging
import shutil
import sys

from copy import deepcopy
from pathlib import Path

import pyglet

import core.path_manager as Paths
import core.event_manager as Events
import core.config_manager as Config

from core.embedded_resources import EmbeddedResources
from core.locale_manager import L

from customtkinter import set_appearance_mode, set_default_color_theme
from customtkinter.windows.widgets.theme import ThemeManager

from gui.classes.windows import UIMainWindow, limit_scaling
from gui.windows.main.message_frame.message_frame import MessageFrame
from gui.windows.main.launcher_frame.launcher_frame import LauncherFrame

log = logging.getLogger(__name__)

# from customtkinter import set_widget_scaling, set_window_scaling, deactivate_automatic_dpi_awareness
# set_widget_scaling(2)
# set_window_scaling(2)
# deactivate_automatic_dpi_awareness()

# Limit automatic scaling in a way to fit arbitrary width and height on screen
limit_scaling(1280, 720)

_EMBEDDED_FONT_HANDLES: dict[str, tuple[int, ctypes.Array]] = {}


def load_customtkinter_theme_from_data(theme_name: str, theme_data: dict):
    """
    Load a CustomTkinter theme from an in-memory dict.

    CustomTkinter 5.2.2 normally reads a JSON file and assigns ThemeManager.theme.
    Keeping this logic local lets packaged builds avoid writing the theme JSON to disk.
    """
    theme = deepcopy(theme_data)

    for key in list(theme.keys()):
        value = theme[key]
        if isinstance(value, dict) and 'macOS' in value:
            if sys.platform == 'darwin':
                theme[key] = value['macOS']
            elif sys.platform.startswith('win'):
                theme[key] = value['Windows']
            else:
                theme[key] = value['Linux']

    if 'CTkCheckbox' in theme:
        theme['CTkCheckBox'] = theme.pop('CTkCheckbox')
    if 'CTkRadiobutton' in theme:
        theme['CTkRadioButton'] = theme.pop('CTkRadiobutton')

    ThemeManager.theme = theme
    ThemeManager._currently_loaded_theme = f'embedded://Themes/{theme_name}/custom-tkinter-theme.json'


def load_embedded_theme_fonts(theme_name: str):
    """
    Load embedded TTF/OTF theme fonts into the current Windows process without writing them to disk.

    pyglet.font.add_file() requires a filesystem path, so packaged builds use the Win32
    AddFontMemResourceEx API instead. Development builds still use pyglet on external files.
    """
    if not sys.platform.startswith('win'):
        return

    font_dir = f'Themes/{theme_name}/Fonts'
    if not EmbeddedResources.is_dir(font_dir):
        return

    gdi32 = ctypes.windll.gdi32

    for resource_path in EmbeddedResources.iter_files(font_dir, suffixes=['.ttf', '.otf']):
        if resource_path in _EMBEDDED_FONT_HANDLES:
            continue

        data = EmbeddedResources.read_bytes(resource_path)
        buffer = ctypes.create_string_buffer(data)
        font_count = ctypes.c_ulong(0)
        handle = gdi32.AddFontMemResourceEx(
            ctypes.c_void_p(ctypes.addressof(buffer)),
            ctypes.c_ulong(len(data)),
            None,
            ctypes.byref(font_count),
        )

        if handle:
            _EMBEDDED_FONT_HANDLES[resource_path] = (handle, buffer)
        else:
            log.warning(f'Failed to load embedded font: {resource_path}')


class MainWindow(UIMainWindow):
    def __init__(self):
        super().__init__()

        self.hide()

        self.cfg.title = 'GPMI'
        self.cfg.width = 1280
        self.cfg.height = 720

        # Use dark mode theme colors
        set_appearance_mode('Dark')

        # Fix pyglet font load
        pyglet.options['win32_gdi_font'] = True

        self.active_theme = None
        self.launcher_frame = None
        self.message_frame = None
        self.portrait_manager_window = None

        Events.Subscribe(Events.Application.MoveWindow, lambda event: self.move(event.offset_x, event.offset_y))
        Events.Subscribe(Events.Application.ShowMessage, lambda event: self.show_messagebox(event))
        Events.Subscribe(Events.Application.ShowError, lambda event: self.show_messagebox(event))
        Events.Subscribe(Events.Application.ShowWarning, lambda event: self.show_messagebox(event))
        Events.Subscribe(Events.Application.ShowInfo, lambda event: self.show_messagebox(event))
        Events.Subscribe(Events.Application.OpenPortraitManager, lambda event: self.open_portrait_manager())


    def open_portrait_manager(self):
        from gui.windows.gpmi.portrait_manager_window import PortraitManagerWindow

        if Config.Launcher.active_importer != 'GPMI':
            Events.Fire(Events.Application.LoadImporter(importer_id='GPMI'))

        if self.portrait_manager_window is not None and self.portrait_manager_window.winfo_exists():
            self.portrait_manager_window.focus_force()
            return

        self.portrait_manager_window = PortraitManagerWindow(self)
        self.portrait_manager_window.focus_force()

    @staticmethod
    def get_embedded_theme_resource(theme: str) -> str:
        return f'Themes/{theme}/custom-tkinter-theme.json'

    @staticmethod
    def get_external_theme_json_path(theme: str) -> Path:
        return Paths.App.Themes / theme / 'custom-tkinter-theme.json'

    def read_theme_data(self, theme: str) -> tuple[dict | None, Path | None]:
        embedded_resource = self.get_embedded_theme_resource(theme)
        if EmbeddedResources.is_file(embedded_resource):
            return EmbeddedResources.read_json(embedded_resource), None

        theme_json_path = self.get_external_theme_json_path(theme)
        if theme_json_path.is_file():
            return json.loads(Paths.App.read_text(theme_json_path)), theme_json_path

        return None, theme_json_path

    def load_theme(self, theme: str):
        # Skip loading the same theme
        if self.active_theme == theme:
            return

        theme_data, theme_json_path = self.read_theme_data(theme)

        # Ensure customtkinter theme integrity
        if not self.validate_theme(theme, theme_data, theme_json_path):
            return

        # Load customtkinter theme
        try:
            if theme_data is not None and theme_json_path is None:
                load_customtkinter_theme_from_data(theme, theme_data)
                load_embedded_theme_fonts(theme)
            else:
                set_default_color_theme(str(theme_json_path))
        except Exception as e:
            log.exception(e)

        # Load custom fonts from external development/user theme folders
        if theme_json_path is not None:
            theme_path = theme_json_path.parent
            fonts_path = theme_path / 'Fonts'
            if fonts_path.is_dir():
                for font_path in fonts_path.iterdir():
                    if font_path.suffix.lower() not in ('.ttf', '.otf'):
                        continue
                    try:
                        pyglet.font.add_file(str(font_path))
                    except Exception as e:
                        log.exception(e)

            # Set icon path only for real filesystem themes. Embedded .ico resources are not written to disk.
            icon_path = theme_path / 'window-icon.ico'
            if icon_path.is_file():
                self.cfg.icon_path = icon_path

        # Set theme as active
        self.active_theme = theme
        Config.Config.active_theme = theme

    def validate_theme(self, theme: str, theme_data: dict | None, theme_json_path: Path | None):
        theme_name = theme

        # Make sure that theme exists
        if theme_data is None:
            if theme_json_path is None or not theme_json_path.parent.is_dir():
                Config.Config.active_theme = 'Default'
                Config.Launcher.gui_theme = 'Default'
                self.load_theme('Default')
                Events.Fire(Events.Application.ShowWarning(
                    message=L('message_text_theme_load_failed_no_folder', """
                        Failed to load {theme} theme:
                        
                        Theme folder does not exist!
                    """).format(theme=theme_name)
                ))
                return False

            if not theme_json_path.is_file():
                Config.Config.active_theme = 'Default'
                Config.Launcher.gui_theme = 'Default'
                self.load_theme('Default')
                Events.Fire(Events.Application.ShowWarning(
                    message=L('message_text_theme_load_failed_no_file', """
                        Failed to load {theme} theme:
                        
                        Theme file `custom-tkinter-theme.json` does not exist!
                    """).format(theme=theme_name)
                ))
                return False

        try:
            if theme_data is None:
                theme_data = json.loads(Paths.App.read_text(theme_json_path))
            theme_api_version = theme_data['Metadata']['theme_api_version']
        except Exception:
            theme_api_version = '0.0.0'

        if theme_api_version <  '1.0.1':
            default_data, default_json_path = self.read_theme_data('Default')

            if default_data is not None and default_json_path is None:
                load_customtkinter_theme_from_data('Default', default_data)
            elif default_json_path is not None:
                set_default_color_theme(str(default_json_path))

            # Embedded themes cannot be patched on disk. Fall back to Default instead.
            if theme_json_path is None:
                Config.Config.active_theme = 'Default'
                Config.Launcher.gui_theme = 'Default'
                self.load_theme('Default')
                Events.Fire(Events.Application.ShowWarning(
                    message=L('message_text_theme_load_failed_no_file', """
                        Failed to load {theme} theme:
                        
                        Theme file `custom-tkinter-theme.json` does not exist!
                    """).format(theme=theme_name)
                ))
                return False

            update_dialogue = Events.Application.ShowWarning(
                modal=True,
                title=L('message_title_theme_update_required', 'Theme Update Required'),
                confirm_text=L('message_button_theme_use_default', 'Use Default'),
                cancel_text=L('message_button_patch_theme', 'Patch Theme'),
                message=L('message_text_theme_update_required', """
                    Selected {theme} theme cannot be loaded!
                    
                    Click `Use Default` to use default theme instead (ensures proper visuals).
                    Click `Patch Theme` to replace `custom-tkinter-theme.json` with new one.
                """).format(theme=theme_name)
            )
            user_requested_default_theme = self.show_messagebox(update_dialogue)
            if user_requested_default_theme:
                Config.Config.active_theme = 'Default'
                Config.Launcher.gui_theme = 'Default'
                self.load_theme('Default')
            else:
                default_external_json_path = self.get_external_theme_json_path('Default')
                Events.Fire(Events.PathManager.VerifyFileAccess(path=theme_json_path, write=True))
                theme_json_path.unlink()
                shutil.copy2(default_external_json_path, theme_json_path)
                self.load_theme(theme_name)

        return True

    def reload_theme(self, last_mod_time=0):
        if not Config.Config.Launcher.theme_dev_mode:
            return

        if self.active_theme is None:
            return

        # Embedded packaged themes are immutable and have no filesystem mtime to watch.
        if EmbeddedResources.is_file(self.get_embedded_theme_resource(self.active_theme)):
            return

        theme_path = Paths.App.Themes / self.active_theme
        mod_time = theme_path.stat().st_mtime

        # self._verify_chain()

        if mod_time != last_mod_time:
            try:
                set_default_color_theme(str(theme_path / 'custom-tkinter-theme.json'))
            except Exception as e:
                log.exception(e)
            self._apply_theme(recursive=True)

        self.after(100, self.reload_theme, mod_time)

    def _verify_chain(self):
        self._verify_chain_recursive(self)

    def _verify_chain_recursive(self, widget):
        for child in widget.children.values():
            if not hasattr(child, 'elements'):
                continue
            if not hasattr(child.master, 'elements'):
                raise Exception(f'Object of class {child.__class__.__qualname__} master is not of UIElement base class!')
            if child not in child.master.elements.values():
                raise Exception(f'Object of class {child.__class__.__qualname__} is not listed in elements of master {child.master.__class__.__qualname__}!\n'
                                f'{child.__dict__}')
            self._verify_chain_recursive(child)

    def initialize(self):
        import gui.vars as Vars

        Vars.Settings.initialize(Config.Config, self)
        Vars.Settings.load()

        # def callback(var, new_value, old_value):
        #     print(var, new_value, old_value)
        # Vars.Settings.subscribe(Vars.Settings.Launcher.log_level, callback)
        # Vars.Settings.Launcher.log_level.set('TEST')
        # Vars.Settings.save()

        self.load_theme(Config.Config.active_theme)

        self.apply_config()

        self.center_window()

        self.launcher_frame = self.put(LauncherFrame(self))
        self.launcher_frame.grid(row=0, column=0, padx=0, pady=0, sticky='news')

        # Auto reload
        self.reload_theme()

        Events.Subscribe(Events.GUI.ToggleThemeDevMode, self.reload_theme)
        Events.Subscribe(Events.GUI.ReloadGUI, self.reload_gui)

        Events.Fire(Events.Application.Ready())
        # Events.Fire(Events.Application.Busy())
        # Events.Fire(Events.PackageManager.InitializeDownload())
        # Events.Fire(Events.PackageManager.UpdateDownloadProgress(downloaded_bytes=430000, total_bytes=1000000))
        # Events.Fire(Events.PackageManager.InitializeInstallation())

        self.show()

        Events.Subscribe(Events.Application.Minimize,
                         lambda event: self.minimize())
        Events.Subscribe(Events.Application.Close, self.handle_close)

    def reload_gui(self, event: Events.GUI.ReloadGUI):
        Events.Fire(Events.Application.StatusUpdate(status=L('status_reloading_gui', 'Reloading GUI...')))

        # Remove existing LauncherFrame widgets tree
        del self.elements[self.launcher_frame._id]
        self.launcher_frame.destroy()

        # Load theme
        if event.reload_theme:
            self.load_theme(Config.Launcher.gui_theme)

        # Create new LauncherFrame
        self.launcher_frame = self.put(LauncherFrame(self))
        self.launcher_frame.grid(row=0, column=0, padx=0, pady=0, sticky='news')

        # Trigger events listened by LauncherFrame to initialize its state
        Events.Fire(Events.Application.Ready())
        Events.Fire(Events.Application.LoadImporter(importer_id=Config.Launcher.active_importer, reload=True))
        Events.Fire(Events.Application.ConfigUpdate())
        Events.Fire(Events.PackageManager.NotifyPackageVersions(detect_installed=True))

    def handle_close(self, event):
        self.after(event.delay, self.close)

    def close(self):
        Events.Fire(Events.Application.Ready())
        super().close()

    def show_messagebox(self, event=None, **kwargs):
        if not self.exists:
            return False

        if event is not None:
            kwargs = vars(event)

        minimal_gui = False
        modal = kwargs.pop('modal', False)
        show_settings = False
        settings_frame = None

        if self.launcher_frame is None:
            # Initialize minimal GUI (launcher crashed before LauncherFrame initialization)
            minimal_gui = True
            self.load_theme('Default')
            self.apply_config()
            self.center_window()
            self.launcher_frame = self.put(LauncherFrame(self, minimal=True))
            self.launcher_frame.grid(row=0, column=0, padx=0, pady=0, sticky='news')
        else:
            # Hide SettingsFrame if it's open and remember to show it again once message is closed
            settings_frame = self.launcher_frame.grab('SettingsFrame')
            if settings_frame and not settings_frame.is_hidden:
                show_settings = True
                settings_frame.hide()

        messagebox = MessageFrame(self.launcher_frame, self.launcher_frame.canvas, **kwargs)

        self.message_frame = self.put(messagebox)
        self.message_frame.show()

        if minimal_gui:
            self.show()

        if modal:
            self.wait_window(messagebox)

        if self.message_frame is not None:
            self.message_frame = None

        if show_settings and settings_frame.is_hidden:
            settings_frame.show()

        if messagebox.radio_var is not None:
            return messagebox.response, messagebox.radio_var.get()

        if messagebox.selected_options is not None:
            return messagebox.response, messagebox.selected_options

        return messagebox.response

    def report_callback_exception(self, exc, val, tb):
        # raise exc
        if self.message_frame is not None:
            self.message_frame.close()
        self.show_messagebox(Events.Application.ShowError(
            modal=True,
            message=val,
        ))
        Events.Fire(Events.Application.Ready())
        logging.exception(val)
