import webbrowser

import core.event_manager as Events
import core.config_manager as Config

from core.locale_manager import L

from gui.events import Stage
from gui.classes.containers import UIFrame
from gui.classes.widgets import UIText, UIImageButton
from gui.windows.main.launcher_frame.top_bar import TopBarFrame
from gui.windows.main.launcher_frame.bottom_bar import BottomBarFrame
from gui.windows.settings.settings_frame import SettingsFrame


class LauncherFrame(UIFrame):
    def __init__(self, master, minimal=False):
        super().__init__(master, width=master.cfg.width, height=master.cfg.height, fg_color='transparent')

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.canvas.grid(row=0, column=0)

        if minimal:
            self.set_background_image('background-image-gpmi.webp', width=master.cfg.width, height=master.cfg.height)
            return

        # Background
        self.update_background(Config.Launcher.active_importer)

        # Top Panel
        self.put(TopBarFrame(self, self.canvas))

        # Bottom Panel
        self.put(BottomBarFrame(self, self.canvas, width=master.cfg.width, height=master.cfg.height)).grid(row=0, column=0, sticky='swe')

        # Action Panel
        self.put(PortraitManagerMainButton(self))
        self.put(StartButton(self, None))

        # Package versions
        self.put(LauncherVersionText(self))

        # Settings Frame
        self.put(SettingsFrame(self, self.canvas))

        # Donate Frame
        self.subscribe(Events.Application.OpenDonationCenter, self.handle_open_donation_center)

        # from gui.windows.main.message_frame.message_frame import MessageFrame
        # message_frame = self.put(MessageFrame(
        #     self, self.canvas, title='Performance Notification', message=text,
        #     checkbox_options=checkbox_options,
        #     confirm_text='Disable Selected', cancel_text='Ignore'))
        # message_frame.show()

        # Application Events
        self.subscribe(
            Events.Application.Ready,
            lambda event: Events.Fire(Events.GUI.LauncherFrame.StageUpdate(Stage.Ready)))
        self.subscribe(
            Events.PackageManager.StartDownload,
            lambda event: Events.Fire(Events.GUI.LauncherFrame.StageUpdate(Stage.Download)))
        self.subscribe(
            Events.Application.Busy,
            lambda event: Events.Fire(Events.GUI.LauncherFrame.StageUpdate(Stage.Busy)))
        self.subscribe(
            Events.Application.LoadImporter,
            lambda event: self.update_background(event.importer_id))

    def update_background(self, importer_id):
        self.set_background_image(f'background-image-{importer_id.lower()}.webp',
                                  width=self.master.cfg.width,
                                  height=self.master.cfg.height)

    def handle_open_donation_center(self, event: Events.Application.OpenDonationCenter):
        from gui.windows.main.donate_frame.donate_frame import DonateFrame
        donate_frame = self.put(DonateFrame(self, self.canvas))
        donate_frame.set_content(mode=event.mode, model_importer=event.model_importer, num_sessions=event.launch_count)
        donate_frame.show()


class SelectGameText(UIText):
    def __init__(self, master):
        super().__init__(x=32,
                         y=505,
                         text=L('launcher_select_games_text', 'Select Games To Mod:'),
                         font=('SimHei', 24, 'bold'),
                         fill='white',
                         activefill='white',
                         anchor='nw',
                         master=master)
        self.subscribe(Events.Application.LoadImporter, self.handle_load_importer)
        self.subscribe(Events.GUI.LauncherFrame.StageUpdate, self.handle_stage_update)
        # self.subscribe_show(
        #     Events.GUI.LauncherFrame.StageUpdate,
        #     lambda event: event.stage == Stage.Ready)

    def handle_load_importer(self, event):
        self.show(self.stage == Stage.Ready and event.importer_id == 'XXMI')

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer == 'XXMI')


class GameTileButton(UIImageButton):
    def __init__(self, master, pos_id, importer_id):
        super().__init__(
            x=125+pos_id*206,
            y=600,
            button_image_path='game-tile-background.png',
            width=184,
            height=102,
            button_normal_opacity=0.35,
            button_hover_opacity=0.65,
            button_selected_opacity=1,
            button_normal_brightness=1,
            button_selected_brightness=1,
            bg_image_path=f'game-tile-{importer_id.lower()}.png',
            bg_width=180,
            bg_height=100,
            bg_normal_opacity=0.75,
            bg_hover_opacity=0.75,
            bg_selected_opacity=1,
            bg_normal_brightness=0.6,
            bg_selected_brightness=1,
            command=lambda: Events.Fire(Events.Application.ToggleImporter(importer_id=importer_id)),
            master=master)

        self.eye_button_image = self.put(UIImageButton(
            x=self._x + 72, y=self._y - 36,
            button_image_path='eye-show.png',
            width=28,
            height=28,
            button_normal_opacity=0,
            button_hover_opacity=1,
            button_selected_opacity=0,
            bg_image_path=f'eye-hide.png',
            bg_width=28,
            bg_height=28,
            bg_normal_opacity=0,
            bg_hover_opacity=0,
            bg_selected_opacity=1,
            master=master))

        self.eye_button_image.bind("<ButtonPress-1>", self._handle_button_press)
        self.eye_button_image.bind("<ButtonRelease-1>", self._handle_button_release)
        self.eye_button_image.bind("<Enter>", self._handle_enter)
        self.eye_button_image.bind("<Leave>", self._handle_leave)

        self.importer_id = importer_id

        self.subscribe(Events.Application.LoadImporter, self.handle_load_importer)
        self.subscribe(Events.GUI.LauncherFrame.StageUpdate, self.handle_stage_update)
        self.subscribe(Events.GUI.LauncherFrame.ToggleImporter, self.handle_toggle_importer)

        try:
            idx = Config.Launcher.enabled_importers.index(importer_id)
            self.set_selected(True)
        except ValueError:
            self.set_selected(False)

    def handle_load_importer(self, event):
        self.show(self.stage == Stage.Ready and event.importer_id == 'XXMI')

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer == 'XXMI')

    def handle_toggle_importer(self, event):
        if event.importer_id != self.importer_id:
            return
        self.set_selected(event.show)

    def _handle_enter(self, event):
        super()._handle_enter(event)
        Events.Fire(Events.GUI.LauncherFrame.HoverImporter(importer_id=self.importer_id, hover=True))
        self.eye_button_image._handle_enter(event)
        if self.selected:
            self.eye_button_image.set_selected(self.selected)

    def _handle_leave(self, event):
        super()._handle_leave(event)
        Events.Fire(Events.GUI.LauncherFrame.HoverImporter(importer_id=self.importer_id, hover=False))
        self.eye_button_image._handle_leave(event)
        self.eye_button_image.set_selected(False)

    def set_selected(self, selected: bool = False):
        super().set_selected(selected)
        if self.hovered:
            self.eye_button_image.set_selected(selected)


class MainActionButton(UIImageButton):
    def __init__(self, **kwargs):
        self.command = kwargs['command']
        defaults = {}
        defaults.update(
            y=640,
            height=64,
            button_normal_opacity=0.95,
            button_hover_opacity=1,
            button_normal_brightness=0.95,
            button_hover_brightness=1,
            button_selected_brightness=0.8,
            bg_normal_opacity=0.95,
            bg_hover_opacity=1,
            bg_normal_brightness=0.95,
            bg_hover_brightness=1,
            bg_selected_brightness=0.8,
        )
        defaults.update(kwargs)
        super().__init__(**defaults)
        self.stage = None
        self.subscribe(Events.Application.LoadImporter, self.handle_load_importer)
        self.subscribe(Events.GUI.LauncherFrame.StageUpdate, self.handle_stage_update)

    def handle_load_importer(self, event):
        self.show(self.stage == Stage.Ready and event.importer_id != 'XXMI')

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer != 'XXMI')


class UpdateButton(MainActionButton):
    def __init__(self, master):
        super().__init__(
            x=800,
            width=64,
            button_image_path='button-update.png',
            command=lambda: Events.Fire(Events.Application.Update(force=True)),
            master=master)
        self.subscribe(Events.PackageManager.VersionNotification, self.handle_version_notification)

    def handle_version_notification(self, event):
        pending_update_message = []

        for package_name, package in event.package_states.items():
            if package_name == Config.Launcher.active_importer and not package.installed_version:
                pending_update_message = []
                break
            if package.latest_version != '' and (package.installed_version != package.latest_version):
                pending_update_message.append(
                    f'* {package_name}: {package.installed_version or 'N/A'} → {package.latest_version}')

        if len(pending_update_message) > 0:
            self.enabled = True
            self.set_tooltip(L('launcher_update_button_tooltip', """
                ## Update packages to latest versions:
                {pending_update_message}
                
                <font color="#3366ff">*Hover over versions in the bottom-left corner to view update descriptions.*</font>
            """).format(
                pending_update_message='\n'.join(pending_update_message)
            ), delay=0)
            self.show(self.stage == Stage.Ready and Config.Launcher.active_importer != 'XXMI')
        else:
            self.enabled = False
            self.hide()


class PortraitManagerMainButton(MainActionButton):
    def __init__(self, master):
        super().__init__(
            x=800,
            width=32,
            height=32,
            button_image_path='button-tools.png',
            button_x_offset=12,
            bg_image_path='button-start-background.png',
            bg_width=280,
            bg_height=64,
            text=L('launcher_portrait_manager_button', 'Portrait Manager'),
            text_x_offset=8,
            text_y_offset=-1,
            font=('SimSun', 18, 'bold'),
            command=lambda: Events.Fire(Events.Application.OpenPortraitManager()),
            auto_offset='center',
            auto_offset_pad=7,
            master=master)

class StartButton(MainActionButton):
    def __init__(self, master, tools_button=None):
        super().__init__(
            x=1130,
            width=32,
            height=32,
            button_image_path='button-start.png',
            button_x_offset=17,
            bg_image_path='button-start-background.png',
            bg_width=260,
            bg_height=64,
            text=L('launcher_start_button', 'Start'),
            text_x_offset=17,
            text_y_offset=-1,
            font=('SimSun', 23, 'bold'),
            command=lambda: Events.Fire(Events.Application.Launch()),
            auto_offset='center',
            auto_offset_pad=7,
            master=master)
        self.tools_button = tools_button
        self.stage = None
        self.subscribe(
            Events.PackageManager.VersionNotification,
            self.handle_version_notification)

    def handle_version_notification(self, event):
        package_state = event.package_states.get(Config.Launcher.active_importer, None)
        if package_state is None:
            return
        installed = package_state.installed_version != ''
        self.set_enabled(installed)
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer != 'XXMI')

    def _handle_enter(self, event):
        if self.tools_button is not None:
            self.tools_button._handle_enter(None, True)
        super()._handle_enter(self)

    def _handle_leave(self, event):
        if self.tools_button is not None:
            self.tools_button._handle_leave(None, True)
        super()._handle_leave(self)

    def _handle_button_press(self, event):
        if self.tools_button is not None:
            self.tools_button.set_selected(True)
        super()._handle_button_press(self)

    def _handle_button_release(self, event):
        if self.tools_button is not None:
            self.tools_button.selected = False
            self.tools_button._handle_leave(None, True)
        super()._handle_button_release(self)


class InstallButton(MainActionButton):
    def __init__(self, master, tools_button=None):
        super().__init__(
            x=1023,
            width=32,
            height=32,
            # button_image_path='button-start.png',
            # button_x_offset=-14,
            bg_image_path='button-start-background.png',
            bg_width=340,
            bg_height=64,
            text=L('launcher_install_button', 'Install'),
            text_x_offset=18,
            text_y_offset=-1,
            font=('SimSun', 23, 'bold'),
            command=lambda: Events.Fire(Events.ModelImporter.Install()),
            master=master)
        self.tools_button = tools_button
        self.subscribe(
            Events.PackageManager.VersionNotification,
            self.handle_version_notification)

    def handle_version_notification(self, event):
        package_state = event.package_states.get(Config.Launcher.active_importer, None)
        if package_state is None:
            return
        not_installed = package_state.installed_version == ''
        self.set_enabled(not_installed)
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer != 'XXMI')

    def _handle_enter(self, event):
        if self.tools_button is not None:
            self.tools_button._handle_enter(None, True)
        super()._handle_enter(self)

    def _handle_leave(self, event):
        if self.tools_button is not None:
            self.tools_button._handle_leave(None, True)
        super()._handle_leave(self)

    def _handle_button_press(self, event):
        if self.tools_button is not None:
            self.tools_button.set_selected(True)
        super()._handle_button_press(self)

    def _handle_button_release(self, event):
        if self.tools_button is not None:
            self.tools_button.selected = False
            self.tools_button._handle_leave(None, True)
        super()._handle_button_release(self)


class ToolsButton(MainActionButton):
    def __init__(self, master):
        super().__init__(
            x=1210,
            width=37,
            button_image_path='button-tools.png',
            command=lambda: True,
            master=master)

    def _handle_enter(self, event, suppress=False):
        if not suppress:
            Events.Fire(Events.GUI.LauncherFrame.ToggleToolbox(show=True))
        super()._handle_enter(self)

    def _handle_leave(self, event, suppress=False):
        if not suppress:
            Events.Fire(Events.GUI.LauncherFrame.ToggleToolbox(hide_on_leave=True))
        super()._handle_leave(self)


class PackageVersionText(UIImageButton):
    def __init__(self, **kwargs):
        defaults = {}
        defaults.update(
            width=32,
            height=32,
            text='',
            font=('Consolas', 16),
            fill='#999999',
            activefill='white',
            anchor='nw',
            command=self.open_release_link,
        )
        defaults.update(kwargs)
        super().__init__(**defaults)
        self.stage = None
        self.package_name = ''
        self.set_tooltip(self.get_tooltip, delay=0)
        self.subscribe(Events.GUI.LauncherFrame.StageUpdate, self.handle_stage_update)

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.show(self.stage == Stage.Ready and Config.Launcher.active_importer != 'XXMI')

    def open_release_link(self):
        package = Events.Call(Events.PackageManager.GetPackage(self.package_name))
        metadata = package.metadata
        if metadata.github_repo_owner and metadata.github_repo_name:
            webbrowser.open(f'https://github.com/{metadata.github_repo_owner}/{metadata.github_repo_name}/releases')

    def get_tooltip(self):
        package = Events.Call(Events.PackageManager.GetPackage(self.package_name))
        display_name = 'GPMI' if package.metadata.package_name == 'Launcher' else package.metadata.package_name
        version = package.installed_version or package.cfg.latest_version
        release_notes = package.cfg.deployed_release_notes or package.cfg.latest_release_notes
        if release_notes:
            package_release_notes = L('gpmi_launcher_release_notes_up_to_date', """
                # What's new in {package_name} v{new_package_version}:
                {installed_release_notes}
            """)
        else:
            package_release_notes = L('gpmi_launcher_release_notes_empty', """
                No release notes are available for this build.
            """)

        actions_tooltip = L('gpmi_launcher_release_notes_open_releases', """
            <font color="#3366ff">*<u>Left-Click</u> to open {package_name} GitHub releases for full changelog.*</font>
        """)

        txt = L('gpmi_launcher_release_notes_tooltip', """
            {package_release_notes}
            
            {actions_tooltip}
        """).format(
            package_release_notes=package_release_notes,
            actions_tooltip=actions_tooltip
        )

        return txt.format(
            package_name=display_name,
            new_package_version=version,
            installed_release_notes=release_notes,
        )


class LauncherVersionText(PackageVersionText):
    def __init__(self, master):
        super().__init__(x=20,
                         y=680,
                         master=master)
        self.package_name = 'Launcher'
        self.subscribe(Events.PackageManager.VersionNotification, self.handle_version_notification)

    def handle_stage_update(self, event):
        self.stage = event.stage
        self.show(self.stage == Stage.Ready)

    def handle_version_notification(self, event):
        version = event.package_states["Launcher"].installed_version
        self.set_text(f'GPMI {version}')
