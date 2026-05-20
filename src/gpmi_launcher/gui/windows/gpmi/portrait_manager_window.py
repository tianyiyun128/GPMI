from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

import core.config_manager as Config
from core.locale_manager import L

from core.gpmi.mods import (
    META_FILE,
    REQUIRED_SLOTS,
    RUNTIME_MANIFEST_FILE,
    RUNTIME_MODS_DIR,
    USER_MODS_DIR,
    build_live_portrait_manifest,
    clear_selected_outfit,
    ensure_game_profile,
    game_profile_dir,
    import_game_portrait_mods,
    import_user_mod,
    load_mod_meta,
    scan_user_mods,
    select_imported_outfit,
    save_mod_meta,
    summarize_live_portrait_manifest,
)


class PortraitManagerWindow(ctk.CTkToplevel):
    IMPORT_PAGE_PREVIEW_FRACTION = 262 / 940
    PREVIEW_IMAGE_DEFAULT_WIDTH = 230
    PREVIEW_IMAGE_DEFAULT_HEIGHT = 320

    def __init__(self, master, overlay: bool = False):
        super().__init__(master)
        self.title(str(L('portrait_manager_window_title', 'GPMI Portrait Manager')))
        self.geometry('1120x720')
        self.minsize(980, 620)
        self.overrideredirect(True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._drag_start_x = 0
        self._drag_start_y = 0

        self.game_exe_path = self._configured_game_exe_path()
        self.profile_dir = game_profile_dir(self.game_exe_path) if self.game_exe_path else None
        if self.profile_dir is not None:
            ensure_game_profile(self.profile_dir)

        self.scanned_mods: list[dict] = []
        self.selected_mod_index: int | None = None
        self.selected_character_id: str | None = None
        self.selected_outfit_id: str | None = None
        self.current_page: str = 'import'
        self.character_rows: list[str] = []
        self.outfit_rows: list[tuple[str, dict]] = []
        self.import_preview_image: ctk.CTkImage | None = None
        self.outfit_preview_image: ctk.CTkImage | None = None
        self.import_preview_path: Path | None = None
        self.outfit_preview_path: Path | None = None
        self.import_preview_size: tuple[int, int] | None = None
        self.outfit_preview_size: tuple[int, int] | None = None
        self.sync_failures: list[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_summary()
        self._build_main()
        self._build_status()
        self.set_overlay_mode(overlay)
        self.refresh_all(L('portrait_manager_status_ready', 'Ready.'))

    def close(self):
        self.destroy()

    def set_overlay_mode(self, enabled: bool):
        self.attributes('-alpha', 0.72 if enabled else 1.0)
        self.attributes('-topmost', bool(enabled))

    def _start_window_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _handle_window_drag(self, event):
        self.geometry(f'+{event.x_root - self._drag_start_x}+{event.y_root - self._drag_start_y}')

    def _configured_game_exe_path(self) -> Path | None:
        configured = str(getattr(Config.Importers.GPMI.Importer, 'game_folder', '') or '').strip().strip('"')
        if not configured:
            return None
        path = Path(configured)
        if path.suffix.lower() != '.exe' or not path.is_file():
            return None
        return path.resolve()

    def _profile_required(self) -> Path | None:
        if self.profile_dir is None:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_select_game_exe',
                'Select the exact game .exe in launcher settings first.'
            ))
            return None
        ensure_game_profile(self.profile_dir)
        return self.profile_dir

    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=10)
        header.grid(row=0, column=0, sticky='ew', padx=14, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)
        header.bind('<ButtonPress-1>', self._start_window_drag)
        header.bind('<B1-Motion>', self._handle_window_drag)

        game_text = str(self.game_exe_path) if self.game_exe_path else L(
            'portrait_manager_no_game_exe',
            'No game .exe selected'
        )
        profile_text = str(self.profile_dir) if self.profile_dir else L('portrait_manager_unavailable', 'Unavailable')
        self.game_label = ctk.CTkLabel(
            header,
            text=L('portrait_manager_game_label', 'Game: {game_path}').format(game_path=game_text),
            anchor='w',
        )
        self.game_label.grid(row=0, column=0, sticky='ew', padx=14, pady=(12, 2))
        self.game_label.bind('<ButtonPress-1>', self._start_window_drag)
        self.game_label.bind('<B1-Motion>', self._handle_window_drag)
        self.profile_label = ctk.CTkLabel(
            header,
            text=L('portrait_manager_profile_label', 'Profile: {profile_path}').format(profile_path=profile_text),
            anchor='w',
        )
        self.profile_label.grid(row=1, column=0, sticky='ew', padx=14, pady=(2, 12))
        self.profile_label.bind('<ButtonPress-1>', self._start_window_drag)
        self.profile_label.bind('<B1-Motion>', self._handle_window_drag)

        buttons = ctk.CTkFrame(header, fg_color='transparent')
        buttons.grid(row=0, column=1, rowspan=2, sticky='e', padx=12, pady=10)
        ctk.CTkButton(
            buttons,
            text=L('portrait_manager_open_mods_folder_button', 'Open Mods Folder'),
            width=132,
            command=self.open_mods,
        ).pack(pady=3)
        ctk.CTkButton(
            buttons,
            text=L('portrait_manager_open_gpmi_folder_button', 'Open GPMI Folder'),
            width=132,
            command=self.open_profile,
        ).pack(pady=3)

    def _build_summary(self):
        self.summary_label = None

    def _build_main(self):
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=2, column=0, sticky='nsew', padx=14, pady=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        nav = ctk.CTkFrame(body, corner_radius=10)
        nav.grid(row=0, column=0, sticky='ns', padx=(0, 10), pady=0)
        self.import_page_button = ctk.CTkButton(
            nav,
            text=L('portrait_manager_import_page_button', 'Import Mods'),
            width=108,
            command=lambda: self.show_page('import'),
        )
        self.import_page_button.pack(padx=10, pady=(12, 6))
        self.outfits_page_button = ctk.CTkButton(
            nav,
            text=L('portrait_manager_select_page_button', 'Select Mods'),
            width=108,
            command=lambda: self.show_page('outfits'),
        )
        self.outfits_page_button.pack(padx=10, pady=6)

        self.pages_frame = ctk.CTkFrame(body, fg_color='transparent')
        self.pages_frame.grid(row=0, column=1, sticky='nsew')
        self.pages_frame.grid_columnconfigure(0, weight=1)
        self.pages_frame.grid_rowconfigure(0, weight=1)

        self.import_page = ctk.CTkFrame(self.pages_frame, fg_color='transparent')
        self.outfits_page = ctk.CTkFrame(self.pages_frame, fg_color='transparent')
        self.import_page.grid(row=0, column=0, sticky='nsew')
        self.outfits_page.grid(row=0, column=0, sticky='nsew')

        self._build_import_page(self.import_page)
        self._build_outfits_page(self.outfits_page)
        self.show_page('import')

    def _build_import_page(self, page):
        preview_fraction = self.IMPORT_PAGE_PREVIEW_FRACTION
        list_fraction = 1 - preview_fraction

        self.mods_panel = ctk.CTkFrame(page, corner_radius=10)
        self.mods_panel.place(relx=0, rely=0, relwidth=list_fraction, relheight=1)
        self.mods_panel.grid_columnconfigure(0, weight=1)
        self.mods_panel.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            self.mods_panel,
            text=L('portrait_manager_import_page_title', '1. Import Mods'),
            font=ctk.CTkFont(size=18, weight='bold'),
        ).grid(
            row=0, column=0, sticky='w', padx=12, pady=(12, 4)
        )
        self.import_help_label = ctk.CTkLabel(
            self.mods_panel,
            text=L(
                'portrait_manager_import_help',
                'Expected: GPMI/Mods/<character>/<mod>/Unit and Unit_H, one image in each folder.'
            ),
            anchor='w',
        )
        self.import_help_label.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 8))

        self.summary_label = ctk.CTkLabel(self.mods_panel, text='', anchor='w')
        self.summary_label.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 8))
        self.mods_panel.bind('<Configure>', self._handle_import_panel_resize)

        ctk.CTkButton(
            self.mods_panel,
            text=L('portrait_manager_auto_import_game_button', 'Auto Import Game Battle Portrait Mods'),
            command=self.auto_import_game_portrait_mods,
        ).grid(row=3, column=0, sticky='ew', padx=12, pady=(0, 8))

        mod_buttons = ctk.CTkFrame(self.mods_panel, fg_color='transparent')
        mod_buttons.grid(row=5, column=0, sticky='ew', padx=10, pady=(8, 10))
        mod_buttons.grid_columnconfigure(0, weight=1)
        mod_buttons.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            mod_buttons,
            text=L('portrait_manager_scan_mods_button', 'Scan Mods'),
            command=self.scan_mods,
        ).grid(row=0, column=0, sticky='ew', padx=3)
        ctk.CTkButton(
            mod_buttons,
            text=L('portrait_manager_open_mod_folder_button', 'Open Mod Folder'),
            command=self.open_selected_mod_folder,
        ).grid(
            row=0, column=1, sticky='ew', padx=3
        )

        self.mods_list = ctk.CTkScrollableFrame(self.mods_panel, corner_radius=8)
        self.mods_list.grid(row=4, column=0, sticky='nsew', padx=10, pady=0)
        self.mods_list.grid_columnconfigure(0, weight=1)

        self.import_preview_panel = ctk.CTkFrame(page, corner_radius=10)
        self.import_preview_panel.place(
            relx=list_fraction,
            rely=0,
            relwidth=preview_fraction,
            relheight=1,
        )
        self._build_preview_panel(
            self.import_preview_panel,
            title=L('portrait_manager_preview_title', 'Mod Preview'),
            title_attr='import_preview_title',
            image_attr='import_preview_label',
        )

    def _build_outfits_page(self, page):
        preview_fraction = self.IMPORT_PAGE_PREVIEW_FRACTION
        list_fraction = 1 - preview_fraction

        self.outfits_panel = ctk.CTkFrame(page, corner_radius=10)
        self.outfits_panel.place(relx=0, rely=0, relwidth=list_fraction, relheight=1)
        self.outfits_panel.grid_columnconfigure(0, weight=1)
        self.outfits_panel.grid_columnconfigure(1, weight=1)
        self.outfits_panel.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.outfits_panel,
            text=L('portrait_manager_select_page_title', '2. Choose Active Mod'),
            font=ctk.CTkFont(size=18, weight='bold'),
        ).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=12, pady=(12, 4)
        )
        ctk.CTkLabel(
            self.outfits_panel,
            text=L(
                'portrait_manager_select_help',
                'Pick a character, then click a portrait to use it immediately.'
            ),
            anchor='w',
        ).grid(row=1, column=0, columnspan=2, sticky='ew', padx=12, pady=(0, 8))

        self.characters_list = ctk.CTkScrollableFrame(self.outfits_panel, corner_radius=8)
        self.characters_list.grid(row=2, column=0, sticky='nsew', padx=(10, 5), pady=0)
        self.characters_list.grid_columnconfigure(0, weight=1)
        self.outfits_list = ctk.CTkScrollableFrame(self.outfits_panel, corner_radius=8)
        self.outfits_list.grid(row=2, column=1, sticky='nsew', padx=(5, 10), pady=0)
        self.outfits_list.grid_columnconfigure(0, weight=1)

        self.outfit_preview_panel = ctk.CTkFrame(page, corner_radius=10)
        self.outfit_preview_panel.place(
            relx=list_fraction,
            rely=0,
            relwidth=preview_fraction,
            relheight=1,
        )
        self._build_preview_panel(
            self.outfit_preview_panel,
            title=L('portrait_manager_preview_title', 'Mod Preview'),
            title_attr='outfit_preview_title',
            image_attr='outfit_preview_label',
        )

    def _build_preview_panel(self, panel, title: str, title_attr: str, image_attr: str):
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(panel, text=title, font=ctk.CTkFont(size=18, weight='bold')).grid(
            row=0, column=0, sticky='w', padx=12, pady=(12, 4)
        )
        title_label = ctk.CTkLabel(
            panel,
            text=L('portrait_manager_preview_select_item', 'Select an item to preview Unit_H.'),
            anchor='w',
            wraplength=320,
        )
        title_label.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 8))
        image_frame = ctk.CTkFrame(panel, fg_color='transparent')
        image_frame.grid(row=2, column=0, sticky='nsew', padx=12, pady=(0, 12))
        image_label = ctk.CTkLabel(
            image_frame,
            text=L('portrait_manager_no_preview', 'No preview'),
            anchor='center',
        )
        image_label.place(relx=0.5, rely=0.5, anchor='center')
        if image_attr == 'import_preview_label':
            image_frame.bind('<Configure>', lambda _event: self._resize_preview('import'))
            self.import_preview_frame = image_frame
        elif image_attr == 'outfit_preview_label':
            image_frame.bind('<Configure>', lambda _event: self._resize_preview('outfit'))
            self.outfit_preview_frame = image_frame
        setattr(self, title_attr, title_label)
        setattr(self, image_attr, image_label)

    def show_page(self, page_name: str):
        self.current_page = page_name
        if page_name == 'outfits':
            self.outfits_page.tkraise()
        else:
            self.import_page.tkraise()
        self.import_page_button.configure(fg_color='#1f6aa5' if page_name == 'import' else 'transparent')
        self.outfits_page_button.configure(fg_color='#1f6aa5' if page_name == 'outfits' else 'transparent')

    def _handle_import_panel_resize(self, event):
        wraplength = max(160, event.width - 28)
        self.import_help_label.configure(wraplength=wraplength)
        if self.summary_label is not None:
            self.summary_label.configure(wraplength=wraplength)

    def _build_status(self):
        status_frame = ctk.CTkFrame(self, fg_color='transparent')
        status_frame.grid(row=3, column=0, sticky='ew', padx=14, pady=(8, 14))
        status_frame.grid_columnconfigure(0, weight=1)
        self.status_box = ctk.CTkTextbox(status_frame, height=110, font=ctk.CTkFont(family='Consolas', size=12))
        self.status_box.grid(row=0, column=0, sticky='ew', padx=(0, 10), pady=0)
        ctk.CTkButton(
            status_frame,
            text=L('portrait_manager_close_button', 'Close'),
            width=112,
            command=self.close,
        ).grid(row=0, column=1, sticky='sew', pady=0)

    def open_profile(self):
        profile = self._profile_required()
        if profile:
            self._open_folder(profile)

    def open_mods(self):
        profile = self._profile_required()
        if profile:
            self._open_folder(profile / USER_MODS_DIR)

    def _open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            subprocess.Popen(['explorer.exe', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])

    def refresh_all(self, message: str | None = None):
        self._scan_mods_from_disk()
        self._render_mods()
        self.refresh_outfits()
        self.refresh_status(message)

    def _scan_mods_from_disk(self):
        profile = self.profile_dir
        if profile is None:
            self.scanned_mods = []
            self.selected_mod_index = None
            return
        ensure_game_profile(profile)
        self.scanned_mods = scan_user_mods(profile)
        self._sync_ready_mods()
        if self.selected_mod_index is not None and self.selected_mod_index >= len(self.scanned_mods):
            self.selected_mod_index = None

    def _sync_ready_mods(self):
        profile = self.profile_dir
        self.sync_failures = []
        if profile is None:
            return
        ready_source_paths = {item.get('source_path') for item in self.scanned_mods if item.get('ready')}
        imported_count = 0
        meta_changed = False
        for item in self.scanned_mods:
            if not item.get('ready'):
                continue
            try:
                import_user_mod(profile, item['character_id'], item['source_outfit_name'])
                imported_count += 1
            except Exception as e:
                self.sync_failures.append(f'{item["character_id"]}/{item["source_outfit_name"]}: {e}')
        meta = load_mod_meta(profile)
        selected_outfits = meta.setdefault('selected_outfits', {})
        selected_sources = meta.setdefault('selected_sources', {})
        for cid in list(meta.get('characters', {}).keys()):
            character = meta.get('characters', {}).get(cid, {})
            outfits = character.get('outfits', {})
            for outfit_id in list(outfits.keys()):
                if outfits[outfit_id].get('source_path') not in ready_source_paths:
                    outfits.pop(outfit_id, None)
                    meta_changed = True
            if not outfits:
                meta.get('characters', {}).pop(cid, None)
                selected_outfits.pop(cid, None)
                selected_sources.pop(cid, None)
                meta_changed = True
                continue
            selected_id = selected_outfits.get(cid)
            if selected_id in outfits:
                source_path = outfits[selected_id].get('source_path')
                if source_path and selected_sources.get(cid) != source_path:
                    selected_sources[cid] = source_path
                    meta_changed = True
                continue
            if cid in selected_outfits and selected_id is None:
                if cid in selected_sources:
                    selected_sources.pop(cid, None)
                    meta_changed = True
                continue
            saved_source_path = selected_sources.get(cid)
            restored_id = None
            if saved_source_path:
                for outfit_id, outfit in sorted(outfits.items()):
                    if outfit.get('source_path') == saved_source_path:
                        restored_id = outfit_id
                        break
            if restored_id:
                selected_outfits[cid] = restored_id
                meta_changed = True
            else:
                if cid in selected_outfits:
                    selected_outfits.pop(cid, None)
                    meta_changed = True
                if cid in selected_sources:
                    selected_sources.pop(cid, None)
                    meta_changed = True
        save_mod_meta(profile, meta)
        if imported_count or meta_changed:
            self._write_runtime(profile)

    def scan_mods(self):
        self._scan_mods_from_disk()
        self._render_mods()
        self.refresh_outfits()
        ready_count, broken_count = self._mod_status_counts()
        lines = [
            L(
                'portrait_manager_scan_result',
                'Scanned {total_count} mod folder(s): {ready_count} ready / {broken_count} broken.'
            ).format(
                total_count=len(self.scanned_mods),
                ready_count=ready_count,
                broken_count=broken_count,
            )
        ]
        if self.sync_failures:
            lines.append('')
            lines.append(L('portrait_manager_sync_failures', 'Sync failures:'))
            lines.extend(self.sync_failures[:12])
        self.refresh_status('\n'.join(lines))

    def auto_import_game_portrait_mods(self):
        profile = self._profile_required()
        if not profile:
            return
        if self.game_exe_path is None:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_select_game_exe',
                'Select the exact game .exe in launcher settings first.'
            ))
            return
        try:
            result = import_game_portrait_mods(self.game_exe_path, profile)
            if result.get('missing_dirs'):
                messagebox.showerror(
                    'GPMI',
                    L(
                        'portrait_manager_error_game_portrait_folders_missing',
                        'Game portrait folders were not found:\n{folders}'
                    ).format(folders='\n'.join(result['missing_dirs']))
                )
                return
            self._scan_mods_from_disk()
            self._render_mods()
            self.refresh_outfits()
            ready_count = sum(1 for item in self.scanned_mods if item.get('ready'))
            lines = [
                L(
                    'portrait_manager_auto_import_result',
                    'Auto Import Game Battle Portrait Mods: copied {copied_count} portrait pair(s).'
                ).format(copied_count=len(result['copied'])),
                L(
                    'portrait_manager_target_mods_folder',
                    'Target Mods folder: {folder_path}'
                ).format(folder_path=result['target_mods_dir']),
                L(
                    'portrait_manager_mods_ready_line',
                    'Mods: {ready_count}/{total_count} ready.'
                ).format(ready_count=ready_count, total_count=len(self.scanned_mods)),
            ]
            if result.get('skipped_invalid'):
                lines.append('')
                lines.append(L(
                    'portrait_manager_skipped_invalid_names',
                    'Skipped invalid names (expected character_h_*):'
                ))
                lines.extend(f'  {name}' for name in result['skipped_invalid'][:12])
                if len(result['skipped_invalid']) > 12:
                    lines.append(L(
                        'portrait_manager_more_items',
                        '  ... {more_count} more'
                    ).format(more_count=len(result['skipped_invalid']) - 12))
            if result.get('skipped_unpaired'):
                lines.append('')
                lines.append(L('portrait_manager_skipped_unpaired_files', 'Skipped unpaired files:'))
                lines.extend(f'  {name}' for name in result['skipped_unpaired'][:12])
                if len(result['skipped_unpaired']) > 12:
                    lines.append(L(
                        'portrait_manager_more_items',
                        '  ... {more_count} more'
                    ).format(more_count=len(result['skipped_unpaired']) - 12))
            self.refresh_status('\n'.join(lines))
        except Exception as e:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_auto_import_failed',
                'Failed to auto import game portrait mods:\n{error}'
            ).format(error=e))

    def _clear_frame(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _resolve_profile_path(self, rel_path: str | None) -> Path | None:
        if not rel_path or self.profile_dir is None:
            return None
        return self.profile_dir / rel_path

    def _preview_target_size(self, label) -> tuple[int, int]:
        width = label.winfo_width() - 8
        height = label.winfo_height() - 8
        if width <= 1:
            width = self.PREVIEW_IMAGE_DEFAULT_WIDTH
        if height <= 1:
            height = self.PREVIEW_IMAGE_DEFAULT_HEIGHT
        return width, height

    def _preview_frame_for_attr(self, image_attr: str):
        if image_attr == 'import_preview_image':
            return getattr(self, 'import_preview_frame', self.import_preview_label)
        if image_attr == 'outfit_preview_image':
            return getattr(self, 'outfit_preview_frame', self.outfit_preview_label)
        return None

    def _fit_preview_image(self, path: Path, target_size: tuple[int, int]) -> ctk.CTkImage:
        with Image.open(path) as source:
            image = source.copy()
        target_width, target_height = target_size
        scale = min(target_width / image.width, target_height / image.height)
        new_size = (
            max(1, int(image.width * scale)),
            max(1, int(image.height * scale)),
        )
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        widget_scale = ctk.ScalingTracker.get_widget_scaling(self)
        logical_size = (
            max(1, int(new_size[0] / widget_scale)),
            max(1, int(new_size[1] / widget_scale)),
        )
        return ctk.CTkImage(light_image=image, dark_image=image, size=logical_size)

    def _preview_placeholder_image(self) -> ctk.CTkImage:
        image = Image.new('RGBA', (1, 1), (0, 0, 0, 0))
        return ctk.CTkImage(light_image=image, dark_image=image, size=(1, 1))

    def _set_preview_text(self, label, image_attr: str, text: str):
        preview = self._preview_placeholder_image()
        setattr(self, image_attr, preview)
        label.configure(image=preview, text=text)

    def _set_preview(self, label, title_label, image_attr: str, title: str, path: Path | None):
        title_label.configure(text=title)
        path_attr = image_attr.replace('_image', '_path')
        size_attr = image_attr.replace('_image', '_size')
        setattr(self, path_attr, path)
        setattr(self, size_attr, None)
        if path is None or not path.is_file():
            try:
                self._set_preview_text(
                    label,
                    image_attr,
                    L('portrait_manager_no_unit_h_preview', 'No Unit_H preview available.'),
                )
            except Exception:
                pass
            return
        try:
            area = self._preview_frame_for_attr(image_attr) or label
            size = self._preview_target_size(area)
            preview = self._fit_preview_image(path, size)
            setattr(self, image_attr, preview)
            label.configure(image=preview, text='')
        except Exception as e:
            try:
                self._set_preview_text(
                    label,
                    image_attr,
                    L('portrait_manager_preview_failed', 'Preview failed:\n{error}').format(error=e),
                )
            except Exception:
                pass
            return
        setattr(self, size_attr, size)

    def _resize_preview(self, kind: str):
        if kind == 'import':
            label = getattr(self, 'import_preview_label', None)
            area = getattr(self, 'import_preview_frame', label)
            image_attr = 'import_preview_image'
            path = self.import_preview_path
        else:
            label = getattr(self, 'outfit_preview_label', None)
            area = getattr(self, 'outfit_preview_frame', label)
            image_attr = 'outfit_preview_image'
            path = self.outfit_preview_path
        if label is None or area is None or path is None or not path.is_file():
            return
        size_attr = image_attr.replace('_image', '_size')
        size = self._preview_target_size(area)
        if getattr(self, size_attr, None) == size:
            return
        try:
            preview = self._fit_preview_image(path, size)
            setattr(self, image_attr, preview)
            label.configure(image=preview, text='')
        except Exception:
            return
        setattr(self, size_attr, size)

    def _clear_import_preview(self):
        self._set_preview(
            self.import_preview_label,
            self.import_preview_title,
            'import_preview_image',
            L('portrait_manager_select_mod_preview', 'Select a mod to preview Unit_H.'),
            None,
        )

    def _clear_outfit_preview(self):
        self._set_preview(
            self.outfit_preview_label,
            self.outfit_preview_title,
            'outfit_preview_image',
            L('portrait_manager_select_mod_preview', 'Select a mod to preview Unit_H.'),
            None,
        )

    def _mod_preview_path(self, item: dict) -> Path | None:
        slot_files = item.get('slot_files', {})
        return self._resolve_profile_path(slot_files.get('Unit_H') or slot_files.get('Unit'))

    def _outfit_preview_path(self, outfit: dict) -> Path | None:
        source_files = outfit.get('source_files', {})
        files = outfit.get('files', {})
        rel_path = source_files.get('Unit_H') or files.get('Unit_H') or source_files.get('Unit') or files.get('Unit')
        return self._resolve_profile_path(rel_path)

    def _update_import_preview(self, item: dict | None = None):
        if item is None:
            if self.selected_mod_index is None or self.selected_mod_index >= len(self.scanned_mods):
                self._clear_import_preview()
                return
            item = self.scanned_mods[self.selected_mod_index]
        title = f'{item["character_id"]} / {item["source_outfit_name"]}'
        self._set_preview(
            self.import_preview_label,
            self.import_preview_title,
            'import_preview_image',
            title,
            self._mod_preview_path(item),
        )

    def _update_outfit_preview(self, outfit_id: str | None = None, outfit: dict | None = None):
        if outfit is None:
            if not self.selected_outfit_id:
                self._clear_outfit_preview()
                return
            meta = load_mod_meta(self.profile_dir) if self.profile_dir is not None else {}
            outfit = (
                meta.get('characters', {})
                .get(self.selected_character_id or '', {})
                .get('outfits', {})
                .get(outfit_id or self.selected_outfit_id)
            )
        if not outfit:
            self._clear_outfit_preview()
            return
        title = f'{outfit.get("character_id", self.selected_character_id)} / {outfit.get("source_name", outfit_id or self.selected_outfit_id)}'
        self._set_preview(
            self.outfit_preview_label,
            self.outfit_preview_title,
            'outfit_preview_image',
            title,
            self._outfit_preview_path(outfit),
        )

    def _row_button(
        self,
        parent,
        text: str,
        command,
        selected: bool = False,
        bad: bool = False,
        imported: bool = False,
        status: str | None = None,
    ):
        if selected:
            fg = '#1f6aa5'
            hover = '#2b78bd'
        elif status == 'ready':
            fg = '#23633f'
            hover = '#2d7a4d'
        elif status == 'broken':
            fg = '#8a2f2f'
            hover = '#9b3a3a'
        elif imported:
            fg = '#23633f'
            hover = '#2d7a4d'
        elif bad:
            fg = '#7a3e1d'
            hover = '#8c4a24'
        else:
            fg = 'transparent'
            hover = '#343638'
        button = ctk.CTkButton(
            parent,
            text=text,
            command=command,
            anchor='w',
            height=34,
            fg_color=fg,
            hover_color=hover,
            border_width=0 if selected else 1,
        )
        button.grid(sticky='ew', padx=4, pady=3)
        return button

    def _mod_status_counts(self) -> tuple[int, int]:
        ready_count = sum(1 for item in self.scanned_mods if item.get('ready'))
        return ready_count, len(self.scanned_mods) - ready_count

    def _render_mods(self):
        self._clear_frame(self.mods_list)
        if self.profile_dir is None:
            ctk.CTkLabel(
                self.mods_list,
                text=L('portrait_manager_no_game_exe_selected', 'No game .exe selected.'),
                anchor='w',
            ).grid(sticky='ew', padx=8, pady=8)
            self._clear_import_preview()
            return
        if not self.scanned_mods:
            ctk.CTkLabel(
                self.mods_list,
                text=L('portrait_manager_no_mod_folders_found', 'No mod folders found.'),
                anchor='w',
            ).grid(sticky='ew', padx=8, pady=8)
            self._clear_import_preview()
            return
        for idx, item in enumerate(self.scanned_mods):
            ready = bool(item.get('ready'))
            prefix = L('portrait_manager_ready_badge', 'READY') if ready else L('portrait_manager_broken_badge', 'BROKEN')
            label = f'{prefix}  {item["character_id"]} / {item["source_outfit_name"]}'
            if item.get('issues'):
                label += f'  - {item["issues"][0]}'
            self._row_button(
                self.mods_list,
                label,
                command=lambda i=idx: self.select_mod(i),
                selected=(idx == self.selected_mod_index),
                bad=not ready,
                status='ready' if ready else 'broken',
            )
        self._update_import_preview()

    def select_mod(self, idx: int):
        self.selected_mod_index = idx
        self._render_mods()
        item = self.scanned_mods[idx]
        lines = [
            L(
                'portrait_manager_selected_mod_line',
                'Selected mod: {character_id} / {mod_name}'
            ).format(character_id=item['character_id'], mod_name=item['source_outfit_name']),
            L('portrait_manager_folder_line', 'Folder: {folder_path}').format(folder_path=item['source_path']),
            L(
                'portrait_manager_status_line',
                'Status: {status}'
            ).format(status=L('portrait_manager_ready_badge', 'READY') if item.get('ready') else L('portrait_manager_broken_badge', 'BROKEN')),
        ]
        for slot in REQUIRED_SLOTS:
            lines.append(f'{slot}: {item.get("slot_files", {}).get(slot, "<missing>")}')
        if item.get('issues'):
            lines.append(L('portrait_manager_issues_line', 'Issues: {issues}').format(issues='; '.join(item['issues'])))
        self._update_import_preview(item)
        self.refresh_status('\n'.join(lines))

    def open_selected_mod_folder(self):
        profile = self._profile_required()
        if not profile:
            return
        if self.selected_mod_index is None:
            messagebox.showinfo('GPMI', L('portrait_manager_info_select_one_mod', 'Select one mod first.'))
            return
        item = self.scanned_mods[self.selected_mod_index]
        source_path = item.get('source_path')
        if not source_path:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_selected_mod_no_folder',
                'Selected mod has no source folder.'
            ))
            return
        self._open_folder(profile / source_path)

    def refresh_outfits(self):
        profile = self.profile_dir
        self.character_rows = []
        self.outfit_rows = []
        if profile is None:
            self._render_characters({})
            self._render_outfits({})
            return
        meta = load_mod_meta(profile)
        characters = meta.get('characters', {})
        self.character_rows = sorted(characters.keys())
        if self.selected_character_id not in characters:
            selected = meta.get('selected_outfits', {})
            selected_characters = [cid for cid in self.character_rows if cid in selected]
            self.selected_character_id = (
                selected_characters[0]
                if selected_characters
                else self.character_rows[0] if self.character_rows else None
            )
        self._render_characters(meta)
        self._render_outfits(meta)

    def _render_characters(self, meta: dict):
        self._clear_frame(self.characters_list)
        characters = meta.get('characters', {})
        selected = meta.get('selected_outfits', {})
        if not characters:
            ctk.CTkLabel(
                self.characters_list,
                text=L('portrait_manager_no_imported_mods', 'No imported mods yet.'),
                anchor='w',
            ).grid(sticky='ew', padx=8, pady=8)
            return
        for cid in self.character_rows:
            outfits = characters.get(cid, {}).get('outfits', {})
            selected_id = selected.get(cid)
            selected_outfit = outfits.get(selected_id or '')
            suffix = f'  {selected_outfit.get("source_name", selected_id)}' if selected_outfit else ''
            label = f'{cid}  ({len(outfits)}){suffix}'
            self._row_button(
                self.characters_list,
                label,
                command=lambda value=cid: self.select_character(value),
                selected=(cid == self.selected_character_id),
            )

    def _render_outfits(self, meta: dict):
        self._clear_frame(self.outfits_list)
        self.outfit_rows = []
        if not self.selected_character_id:
            ctk.CTkLabel(
                self.outfits_list,
                text=L('portrait_manager_select_character_prompt', 'Select a character.'),
                anchor='w',
            ).grid(sticky='ew', padx=8, pady=8)
            self._clear_outfit_preview()
            return
        profile = self.profile_dir
        if profile is None:
            return
        char = meta.get('characters', {}).get(self.selected_character_id, {})
        outfits = char.get('outfits', {})
        selected_id = meta.get('selected_outfits', {}).get(self.selected_character_id, '')
        if selected_id in outfits:
            self.selected_outfit_id = selected_id
        elif not selected_id or self.selected_outfit_id not in outfits:
            self.selected_outfit_id = None
        self._row_button(
            self.outfits_list,
            L('portrait_manager_disable_mod_option', 'Do Not Load Mod'),
            command=self.disable_selected_character,
            selected=not selected_id,
        )
        for number, outfit_id in enumerate(sorted(outfits.keys()), start=1):
            outfit = outfits[outfit_id]
            label = f'{number}. {outfit.get("source_name", outfit_id)}'
            self.outfit_rows.append((outfit_id, outfit))
            self._row_button(
                self.outfits_list,
                label,
                command=lambda value=outfit_id: self.select_outfit(value),
                selected=(outfit_id == selected_id),
            )
        if not outfits:
            self._clear_outfit_preview()
        elif self.selected_outfit_id:
            self._update_outfit_preview()
        else:
            self._clear_outfit_preview()

    def select_character(self, cid: str):
        self.selected_character_id = cid
        self.selected_outfit_id = None
        self.refresh_outfits()
        self.refresh_status(L('portrait_manager_selected_character', 'Selected character: {character_id}').format(
            character_id=cid
        ))

    def select_outfit(self, outfit_id: str):
        self.selected_outfit_id = outfit_id
        self.use_selected_outfit()

    def use_selected_outfit(self):
        profile = self._profile_required()
        if not profile:
            return
        if not self.selected_character_id or not self.selected_outfit_id:
            messagebox.showinfo('GPMI', L(
                'portrait_manager_info_select_character_and_mod',
                'Select a character and a mod first.'
            ))
            return
        try:
            select_imported_outfit(profile, self.selected_character_id, self.selected_outfit_id)
            result = self._write_runtime(profile)
            self.refresh_outfits()
            self.refresh_status(
                L(
                    'portrait_manager_selected_mod_line',
                    'Selected mod: {character_id} / {mod_name}'
                ).format(character_id=self.selected_character_id, mod_name=self.selected_outfit_id)
            )
        except Exception as e:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_select_mod_failed',
                'Failed to select mod:\n{error}'
            ).format(error=e))

    def disable_selected_character(self):
        profile = self._profile_required()
        if not profile:
            return
        if not self.selected_character_id:
            messagebox.showinfo('GPMI', L('portrait_manager_info_select_character', 'Select a character first.'))
            return
        try:
            clear_selected_outfit(profile, self.selected_character_id)
            result = self._write_runtime(profile)
            disabled = self.selected_character_id
            self.selected_outfit_id = None
            self.refresh_outfits()
            self.refresh_status(L(
                'portrait_manager_mod_disabled',
                'Mod disabled for {character_id}.'
            ).format(character_id=disabled))
        except Exception as e:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_disable_mod_failed',
                'Failed to disable mod:\n{error}'
            ).format(error=e))

    def apply_runtime_rules(self):
        profile = self._profile_required()
        if not profile:
            return
        try:
            result = self._write_runtime(profile)
            self.refresh_outfits()
            self.refresh_status(L(
                'portrait_manager_applied_selection',
                'Applied selection to {manifest_file}.'
            ).format(manifest_file=RUNTIME_MANIFEST_FILE))
        except Exception as e:
            messagebox.showerror('GPMI', L(
                'portrait_manager_error_apply_selection_failed',
                'Failed to apply selections:\n{error}'
            ).format(error=e))

    def _write_runtime(self, profile: Path) -> dict:
        return build_live_portrait_manifest(profile)

    def refresh_status(self, message: str | None = None):
        profile = self.profile_dir
        if profile is None:
            self.summary_label.configure(text=L('portrait_manager_no_game_exe_selected', 'No game .exe selected.'))
            self._set_status(message or L(
                'portrait_manager_error_select_game_exe',
                'Select the exact game .exe in launcher settings first.'
            ))
            return

        summary = summarize_live_portrait_manifest(profile)
        runtime = summary.get('runtime') or {}
        ready_mods, broken_mods = self._mod_status_counts()
        self.summary_label.configure(
            text=L(
                'portrait_manager_summary_mod_counts',
                'Mods {ready_count} ready / {broken_count} broken'
            ).format(ready_count=ready_mods, broken_count=broken_mods)
        )

        lines = []
        if message:
            lines.append(message)
            lines.append('')
        lines.extend([
            L('portrait_manager_mods_folder_line', 'Mods folder: {folder_path}').format(folder_path=profile / USER_MODS_DIR),
            L('portrait_manager_source_images_line', 'Source images: {folder_path}').format(folder_path=profile / RUNTIME_MODS_DIR),
            L('portrait_manager_live_manifest_line', 'Live manifest: {file_path}').format(file_path=profile / RUNTIME_MANIFEST_FILE),
            L('portrait_manager_meta_line', 'Meta: {file_path}').format(file_path=profile / META_FILE),
        ])
        generated = runtime.get('generated') or {}
        issues = generated.get('issues') or {}
        if issues:
            lines.append('')
            lines.append(L('portrait_manager_runtime_issues', 'Runtime issues:'))
            for cid, values in issues.items():
                lines.append(f'  {cid}: {"; ".join(values)}')
        self._set_status('\n'.join(lines))

    def _set_status(self, text: str):
        self.status_box.configure(state='normal')
        self.status_box.delete('1.0', 'end')
        self.status_box.insert('end', text)
        self.status_box.configure(state='disabled')
