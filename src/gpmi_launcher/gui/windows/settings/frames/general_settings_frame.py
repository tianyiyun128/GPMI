import webbrowser
from pathlib import Path

from customtkinter import filedialog, ThemeManager

import core.event_manager as Events
import core.config_manager as Config
import core.path_manager as Paths
import gui.vars as Vars

from core.locale_manager import L, Locale
from gui.classes.containers import UIFrame, UIScrollableFrame
from gui.classes.widgets import UILabel, UIButton, UIEntry, UICheckbox, UIOptionMenu


class GeneralSettingsFrame(UIScrollableFrame):
    """GPMI-only settings page.

    The original XXMI settings page mixed many game-specific Unity/UE tweaks.
    GPMI only needs language, the exact Godot game executable, and optional
    command-line arguments.
    """

    def __init__(self, master, fix_grid=False):
        super().__init__(master, height=410, corner_radius=0, border_width=0, hide_scrollbar=True, fix_grid=fix_grid)
        self._scrollbar_hidden_color = master._fg_color

        self.grid_columnconfigure((0, 2, 3), weight=1)
        self.grid_columnconfigure(1, weight=100)
        self.grid_rowconfigure((0, 1, 2), weight=1)
        self.grid_rowconfigure(3, weight=100)

        self.put(LanguageLabel(self)).grid(row=0, column=0, padx=(20, 10), pady=(0, 30), sticky='w')
        self.put(LanguageOptionMenu(self)).grid(row=0, column=1, padx=(0, 10), pady=(0, 30), sticky='w', columnspan=3)

        self.put(GameFolderLabel(self)).grid(row=1, column=0, padx=(20, 10), pady=(0, 30), sticky='w')
        self.put(GameFolderFrame(self)).grid(row=1, column=1, padx=(0, 65), pady=(0, 30), sticky='new', columnspan=3)
        self.put(DetectGameFolderButton(self)).grid(row=1, column=1, padx=(0, 20), pady=(0, 30), sticky='e', columnspan=3)

        self.put(LaunchOptionsLabel(self)).grid(row=2, column=0, padx=(20, 10), pady=(0, 30), sticky='w')
        self.put(LaunchOptionsFrame(self)).grid(row=2, column=1, padx=(0, 20), pady=(0, 30), sticky='ew', columnspan=3)


class LanguageLabel(UILabel):
    def __init__(self, master):
        super().__init__(
            text=L('launcher_settings_language_label', 'Language:'),
            font=('SimSun', 14, 'bold'),
            fg_color='transparent',
            master=master)


class LanguageOptionMenu(UIOptionMenu):
    def __init__(self, master):
        super().__init__(
            width=120,
            height=36,
            font=('SimSun', 14),
            dropdown_font=('SimSun', 14),
            values={l.name: l.display_name for l in Locale.get_indexed_locales()},
            variable=Vars.Launcher.locale,
            command=self.handle_language_change,
            master=master)

    def handle_language_change(self, value):
        Events.Fire(Events.Application.LoadLocale(locale_name=Vars.Launcher.locale.get(), skip_reload=False))
        Events.Fire(Events.Application.CloseSettings(save=True))
        Events.Fire(Events.GUI.ReloadGUI())
        Events.Fire(Events.Application.Busy())
        Events.Fire(Events.Application.OpenSettings())
        Events.Fire(Events.Application.Ready())


class GameFolderLabel(UILabel):
    def __init__(self, master):
        super().__init__(
            text=L('general_settings_game_executable_label', 'Game Executable:'),
            font=('SimSun', 14, 'bold'),
            fg_color='transparent',
            master=master)


class GameFolderFrame(UIFrame):
    def __init__(self, master):
        super().__init__(
            border_color=ThemeManager.theme['CTkEntry'].get('border_color', None),
            border_width=ThemeManager.theme['CTkEntry'].get('border_width', None),
            fg_color=ThemeManager.theme['CTkEntry'].get('fg_color', None),
            master=master)
        self.grid_columnconfigure(0, weight=100)
        game_folder_error = master.put(GameFolderErrorLabel(master))
        self.put(GameFolderEntry(self, game_folder_error)).grid(row=0, column=0, padx=(4, 0), pady=(2, 0), sticky='new')
        self.put(ChangeGameFolderButton(self)).grid(row=0, column=1, padx=(0, 4), pady=(2, 2), sticky='ne')


class GameFolderEntry(UIEntry):
    def __init__(self, master, error_label: UILabel):
        super().__init__(
            textvariable=Vars.Active.Importer.game_folder,
            width=200,
            height=32,
            border_width=0,
            font=('SimSun', 14),
            master=master)
        self.normal_border_color = self._border_color
        self.error_label = error_label
        self.configure(validate='all', validatecommand=(master.register(self.validate_game_folder), '%P'))
        self.set_tooltip(self.get_tooltip)
        self.validate_game_folder(Vars.Active.Importer.game_folder.get())

    def validate_game_folder(self, game_folder):
        try:
            Events.Call(Events.ModelImporter.ValidateGameFolder(game_folder=game_folder.strip()))
        except Exception as e:
            self.error_label.configure(text=str(e))
            self.error_label.grid(row=0, column=1, padx=(0, 15), pady=(36, 0), sticky='nwe')
            self.master.configure(border_color='#db3434')
            return True
        self.master.configure(border_color=self.normal_border_color)
        self.error_label.grid_forget()
        return True

    def get_tooltip(self):
        return L(
            'general_settings_game_executable_tooltip',
            'Path to the exact Godot game .exe. GPMI does not accept a game folder or scan sibling executables.'
        )


class GameFolderErrorLabel(UILabel):
    def __init__(self, master):
        super().__init__(
            text=L('general_settings_game_executable_not_configured', 'Game executable is not configured.'),
            font=('SimSun', 14, 'bold'),
            text_color='#ff3636',
            fg_color='transparent',
            master=master)

    def _show(self):
        if self.winfo_manager():
            super()._show()


class ChangeGameFolderButton(UIButton):
    def __init__(self, master):
        fg_color = ThemeManager.theme['CTkEntry'].get('fg_color', None)
        super().__init__(
            text=L('settings_browse_path_button', 'Browse...'),
            command=self.change_game_folder,
            auto_width=True,
            padx=6,
            height=32,
            border_width=0,
            font=('SimSun', 14),
            fg_color=fg_color,
            hover_color=fg_color,
            text_color=['#000000', '#aaaaaa'],
            text_color_hovered=['#000000', '#ffffff'],
            master=master)

    @staticmethod
    def _initial_dir() -> str:
        current = Vars.Active.Importer.game_folder.get()
        if current:
            current_path = Path(current)
            if current_path.suffix.lower() == '.exe':
                return str(current_path.parent)
            if current_path.is_dir():
                return str(current_path)
        return str(Paths.App.Root)

    @staticmethod
    def _save_exe_path(exe_path: Path):
        exe_path = exe_path.resolve()
        Vars.Active.Importer.game_folder.set(str(exe_path))
        Vars.Active.Importer.custom_game_exe_name.set(exe_path.name)
        Config.Active.Importer.game_folder = str(exe_path)
        Config.Active.Importer.custom_game_exe_name = exe_path.name
        Config.Config.save()

    def change_game_folder(self):
        exe_path = filedialog.askopenfilename(
            initialdir=self._initial_dir(),
            title=L('general_settings_select_game_executable_title', 'Select Godot Game Executable'),
            filetypes=[
                (L('general_settings_filetype_applications', 'Applications'), '*.exe'),
                (L('general_settings_filetype_all_files', 'All files'), '*.*'),
            ],
        )
        if not exe_path:
            return
        self._save_exe_path(Path(exe_path))


class DetectGameFolderButton(UIButton):
    def __init__(self, master):
        super().__init__(
            text='⟳',
            command=self.detect_game_folder,
            width=36,
            height=36,
            font=('SimSun', 18),
            master=master)
        self.set_tooltip(L(
            'general_settings_select_game_executable_tooltip',
            'Select the exact Godot game executable. Automatic folder detection is disabled for GPMI.'
        ))

    def detect_game_folder(self):
        exe_path = filedialog.askopenfilename(
            initialdir=ChangeGameFolderButton._initial_dir(),
            title=L('general_settings_select_game_executable_title', 'Select Godot Game Executable'),
            filetypes=[
                (L('general_settings_filetype_applications', 'Applications'), '*.exe'),
                (L('general_settings_filetype_all_files', 'All files'), '*.*'),
            ],
        )
        if exe_path:
            ChangeGameFolderButton._save_exe_path(Path(exe_path))


class LaunchOptionsLabel(UILabel):
    def __init__(self, master):
        super().__init__(
            text=L('general_settings_launch_options_label', 'Launch Options:'),
            font=('SimSun', 14, 'bold'),
            fg_color='transparent',
            master=master)


class LaunchOptionsFrame(UIFrame):
    def __init__(self, master):
        super().__init__(fg_color='transparent', master=master)
        self.grid_columnconfigure(1, weight=100)
        self.put(LaunchOptionsCheckbox(self)).grid(row=0, column=0, padx=(0, 0), pady=(0, 0), sticky='w')
        self.put(LaunchOptionsEntryFrame(self)).grid(row=0, column=1, padx=(0, 0), pady=(0, 0), sticky='ew')
        self.grab(LaunchOptionsCheckbox).set_tooltip(self.grab(LaunchOptionsEntryFrame).grab(LaunchOptionsEntry))


class LaunchOptionsEntryFrame(UIFrame):
    def __init__(self, master):
        super().__init__(
            border_color=ThemeManager.theme['CTkEntry'].get('border_color', None),
            border_width=ThemeManager.theme['CTkEntry'].get('border_width', None),
            fg_color=ThemeManager.theme['CTkEntry'].get('fg_color', None),
            master=master)
        self.grid_columnconfigure(0, weight=100)
        self.put(LaunchOptionsEntry(self)).grid(row=0, column=0, padx=(4, 0), pady=(2, 2), sticky='ew')
        self.put(LaunchOptionsButton(self)).grid(row=0, column=1, padx=(0, 4), pady=(2, 2), sticky='e')
        self.trace_write(Vars.Active.Importer.use_launch_options, self.handle_write_use_launch_options)

    def handle_write_use_launch_options(self, var, val):
        if val:
            self.configure(
                fg_color=ThemeManager.theme['CTkEntry'].get('fg_color', None),
                border_color=ThemeManager.theme['CTkEntry'].get('border_color', None))
        else:
            self.configure(
                fg_color=ThemeManager.theme['CTkEntry'].get('fg_color_disabled', None),
                border_color=ThemeManager.theme['CTkEntry'].get('border_color_disabled', None))


class LaunchOptionsCheckbox(UICheckbox):
    def __init__(self, master):
        super().__init__(
            text='',
            font=('SimSun', 14, 'bold'),
            variable=Vars.Active.Importer.use_launch_options,
            width=36,
            master=master)


class LaunchOptionsEntry(UIEntry):
    def __init__(self, master):
        super().__init__(
            textvariable=Vars.Active.Importer.launch_options,
            width=100,
            height=32,
            border_width=0,
            font=('SimSun', 14),
            master=master)
        self.set_tooltip(self.get_tooltip)
        self.trace_write(Vars.Active.Importer.use_launch_options, self.handle_write_use_launch_options)

    def get_tooltip(self):
        return L('general_settings_launch_options_entry_tooltip_default', """
            **Enabled**: Start game exe with specified command line arguments.
            **Disabled**: Ignore specified command line arguments and start game exe normally.
        """)

    def handle_write_use_launch_options(self, var, val):
        self.configure(state='normal' if val else 'disabled')


class LaunchOptionsButton(UIButton):
    def __init__(self, master):
        fg_color = ThemeManager.theme['CTkEntry'].get('fg_color', None)
        super().__init__(
            text=L('general_settings_launch_options_about_button', 'About...'),
            command=self.open_docs,
            auto_width=True,
            padx=6,
            height=32,
            border_width=0,
            font=('SimSun', 14),
            fg_color=fg_color,
            hover_color=fg_color,
            text_color=['#000000', '#aaaaaa'],
            text_color_hovered=['#000000', '#ffffff'],
            master=master)
        self.set_tooltip(self.get_tooltip)
        self.trace_write(Vars.Active.Importer.use_launch_options, self.handle_write_use_launch_options)

    def handle_write_use_launch_options(self, var, val):
        if val:
            self.configure(
                fg_color=ThemeManager.theme['CTkEntry'].get('fg_color', None),
                hover_color=ThemeManager.theme['CTkEntry'].get('fg_color', None),
                text_color=['#000000', '#aaaaaa'])
        else:
            self.configure(
                fg_color=ThemeManager.theme['CTkEntry'].get('fg_color_disabled', None),
                hover_color=ThemeManager.theme['CTkEntry'].get('fg_color_disabled', None),
                text_color=['#000000', '#666666'])

    def open_docs(self):
        webbrowser.open('https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html')

    def get_tooltip(self):
        return L('general_settings_launch_options_about_button_tooltip', """
            Open {engine} command line arguments documentation webpage.
            Note: Game engine is customized by devs and some args may not work.
        """).format(engine='Godot')
