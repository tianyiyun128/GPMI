import webbrowser

import core.event_manager as Events
import core.path_manager as Paths
import core.config_manager as Config
from core.locale_manager import L

from gui.events import Stage
from gui.classes.containers import UIFrame
from gui.classes.widgets import UIText, UIImageButton


class TopBarFrame(UIFrame):
    def __init__(self, master, canvas, **kwargs):
        super().__init__(master=master, canvas=canvas, **kwargs)

        self.set_background_image(image_path='background-image.png', width=1280, height=80, opacity=0.65)

        self._offset_x = 0
        self._offset_y = 0
        self.background_image.bind('<Button-1>', self._handle_button_press)
        self.background_image.bind('<B1-Motion>', self._handle_mouse_move)

        # GPMI is a single-target launcher now: no importer/game selector buttons.
        self.put(GitHubButton(self))
        self.put(DonateButton(self))

        self.put(SettingsButton(self))
        self.put(MinimizeButton(self))
        self.put(CloseButton(self))

        self.put(UnsafeModeText(self))

        self.subscribe(Events.Application.ToggleImporter, self.handle_toggle_importer)
        self.handle_toggle_importer(event=None)

    def _handle_button_press(self, event):
        self._offset_x = event.x
        self._offset_y = event.y

    def _handle_mouse_move(self, event):
        Events.Fire(Events.Application.MoveWindow(offset_x=self._offset_x, offset_y=self._offset_y))

    def handle_toggle_importer(self, event):
        if event is not None:
            try:
                index = Config.Launcher.enabled_importers.index(event.importer_id)
                del Config.Launcher.enabled_importers[index]
                Events.Fire(Events.GUI.LauncherFrame.ToggleImporter(importer_id=event.importer_id, index=0, show=False))

                for idx, importer_id in enumerate(Config.Launcher.enabled_importers):
                    if idx < index:
                        continue
                    Events.Fire(Events.GUI.LauncherFrame.ToggleImporter(importer_id=importer_id, index=idx, show=True))

            except ValueError:
                Config.Launcher.enabled_importers.append(event.importer_id)
                idx = len(Config.Launcher.enabled_importers) - 1
                Events.Fire(Events.GUI.LauncherFrame.ToggleImporter(importer_id=event.importer_id, index=idx, show=True))

        # No XXMI management page in the GPMI-only UI.


# region Web Resource Buttons

class WebResourceButton(UIImageButton):
    def __init__(self, **kwargs):
        kwargs.update(
            y=40,
            width=42,
            height=42,
            bg_width=54,
            bg_height=54,
            button_normal_opacity=0.8,
            button_hover_opacity=1,
            button_selected_opacity=1,
            bg_image_path='button-resource-background.png',
            bg_normal_opacity=0,
            bg_hover_opacity=0.2,
            bg_selected_opacity=0.35)
        super().__init__(**kwargs)

    
class GitHubButton(WebResourceButton):
    def __init__(self, master):
        super().__init__(
            x=930,
            button_image_path='button-resource-github.png',
            command=lambda: webbrowser.open('https://github.com/tianyiyun128-blip/GPMI'),
            master=master)
        self.set_tooltip(L('top_bar_github_button_tooltip', 'Project GitHub'), delay=0.01)


class DonateButton(WebResourceButton):
    def __init__(self, master):
        super().__init__(
            x=1000,
            button_image_path='button-resource-donate.png',
            command=lambda: None,
            master=master)
        self.set_disabled(True)
        self.set_tooltip(L('top_bar_donate_button_tooltip_disabled', 'Sponsor button is temporarily disabled.'), delay=0.01)



# endregion


# region Control Buttons

class ControlButton(UIImageButton):
    def __init__(self, **kwargs):
        kwargs.update(
            y=40,
            width=32,
            height=32,
            bg_width=48,
            bg_height=48,
            button_normal_opacity=0.8,
            button_hover_opacity=1,
            button_selected_opacity=1,
            bg_image_path='button-system-background.png',
            bg_normal_opacity=0,
            bg_hover_opacity=0.2,
            bg_selected_opacity=0.3)
        super().__init__(**kwargs)


class SettingsButton(ControlButton):
    def __init__(self, master):
        super().__init__(
            x=1120,
            width=36,
            height=36,
            button_disabled_opacity=0.25,
            bg_disabled_opacity=0,
            button_image_path='button-system-settings.png',
            command=lambda: Events.Fire((Events.Application.OpenSettings())),
            master=master)
        self.stage = None
        self.set_tooltip(L('top_bar_settings_button_tooltip', 'Open Settings'), delay=0.1)
        self.subscribe(Events.Application.LoadImporter, self.handle_load_importer)
        self.subscribe(Events.GUI.LauncherFrame.StageUpdate, self.handle_stage_update)

    def handle_load_importer(self, event):
        self.set_disabled(self.stage != Stage.Ready or event.importer_id == 'XXMI')

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.set_disabled(self.stage != Stage.Ready or Config.Launcher.active_importer == 'XXMI')


class MinimizeButton(ControlButton):
    def __init__(self, master):
        super().__init__(
            x=1180,
            button_image_path='button-system-minimize.png',
            command=lambda: Events.Fire((Events.Application.Minimize())),
            master=master)
        self.set_tooltip(L('top_bar_minimize_button_tooltip', 'Minimize'), delay=0.1)


class CloseButton(ControlButton):
    def __init__(self, master):
        super().__init__(
            x=1240,
            button_image_path='button-system-close.png',
            command=lambda: Events.Fire((Events.Application.Close())),
            master=master)
        self.set_tooltip(L('top_bar_close_button_tooltip', 'Close'), delay=0.1)

# endregion


class UnsafeModeText(UIText):
    def __init__(self, master):
        super().__init__(x=640,
                         y=25,
                         text=L('top_bar_unsafe_mode_text', 'Unsafe Mode'),
                         font=('Asap', 20),
                         fill='#ff2929',
                         activefill='#ff4040',
                         anchor='n',
                         master=master)
        self.subscribe_show(
            Events.GUI.LauncherFrame.StageUpdate,
            lambda event: event.stage == Stage.Ready)
        self.subscribe(
            Events.Application.ConfigUpdate,
            self.handle_config_update)
        self.set_tooltip(L('top_bar_unsafe_mode_text_tooltip', """
            Usage of 3-rd party 3dmigoto DLLs is allowed.
            Make sure to use ones only from a trusted source!
        """))

    def handle_config_update(self, event=None):
        self.enabled = Config.Launcher.active_importer != 'XXMI' and Config.Active.Migoto.unsafe_mode
        self.show()
