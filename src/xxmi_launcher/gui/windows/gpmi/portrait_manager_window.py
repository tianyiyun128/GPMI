from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

import core.config_manager as Config

from core.gpmi.mods import (
    META_FILE,
    REQUIRED_SLOTS,
    RUNTIME_HASH_DB_FILE,
    RUNTIME_MODS_DIR,
    USER_MODS_DIR,
    build_live_portrait_manifest,
    clear_selected_outfit,
    ensure_game_profile,
    game_profile_dir,
    import_all_ready_mods,
    import_user_mod,
    load_mod_meta,
    scan_user_mods,
    select_imported_outfit,
    summarize_live_portrait_manifest,
    validate_imported_outfit,
    write_runtime_ini,
)


class PortraitManagerWindow(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title('GPMI Portrait Manager')
        self.geometry('1120x720')
        self.minsize(980, 620)
        self.transient(master)

        self.game_exe_path = self._configured_game_exe_path()
        self.profile_dir = game_profile_dir(self.game_exe_path) if self.game_exe_path else None
        if self.profile_dir is not None:
            ensure_game_profile(self.profile_dir)

        self.scanned_mods: list[dict] = []
        self.selected_mod_index: int | None = None
        self.selected_character_id: str | None = None
        self.selected_outfit_id: str | None = None
        self.character_rows: list[str] = []
        self.outfit_rows: list[tuple[str, dict]] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_summary()
        self._build_main()
        self._build_status()
        self.refresh_all('Ready.')

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
            messagebox.showerror('GPMI', 'Select the exact game .exe in launcher settings first.')
            return None
        ensure_game_profile(self.profile_dir)
        return self.profile_dir

    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=10)
        header.grid(row=0, column=0, sticky='ew', padx=14, pady=(14, 8))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text='Portrait Manager', font=ctk.CTkFont(size=24, weight='bold')).grid(
            row=0, column=0, rowspan=2, sticky='w', padx=14, pady=12
        )
        game_text = str(self.game_exe_path) if self.game_exe_path else 'No game .exe selected'
        profile_text = str(self.profile_dir) if self.profile_dir else 'Unavailable'
        self.game_label = ctk.CTkLabel(header, text=f'Game: {game_text}', anchor='w')
        self.game_label.grid(row=0, column=1, sticky='ew', padx=10, pady=(12, 2))
        self.profile_label = ctk.CTkLabel(header, text=f'Profile: {profile_text}', anchor='w')
        self.profile_label.grid(row=1, column=1, sticky='ew', padx=10, pady=(2, 12))

        buttons = ctk.CTkFrame(header, fg_color='transparent')
        buttons.grid(row=0, column=2, rowspan=2, sticky='e', padx=12, pady=10)
        ctk.CTkButton(buttons, text='Open Mods Folder', width=150, command=self.open_mods).pack(pady=3)
        ctk.CTkButton(buttons, text='Open GPMI Folder', width=150, command=self.open_profile).pack(pady=3)
        ctk.CTkButton(buttons, text='Refresh', width=150, command=self.refresh_all).pack(pady=3)

    def _build_summary(self):
        self.summary_label = ctk.CTkLabel(self, text='', anchor='w')
        self.summary_label.grid(row=1, column=0, sticky='ew', padx=18, pady=(0, 8))

    def _build_main(self):
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=2, column=0, sticky='nsew', padx=14, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.mods_panel = ctk.CTkFrame(body, corner_radius=10)
        self.mods_panel.grid(row=0, column=0, sticky='nsew', padx=(0, 7), pady=0)
        self.mods_panel.grid_columnconfigure(0, weight=1)
        self.mods_panel.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self.mods_panel, text='1. Import Mods', font=ctk.CTkFont(size=18, weight='bold')).grid(
            row=0, column=0, sticky='w', padx=12, pady=(12, 4)
        )
        ctk.CTkLabel(
            self.mods_panel,
            text='Expected: GPMI/Mods/<character>/<outfit>/Unit and Unit_H, one image in each folder.',
            anchor='w',
            wraplength=500,
        ).grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 8))

        mod_buttons = ctk.CTkFrame(self.mods_panel, fg_color='transparent')
        mod_buttons.grid(row=3, column=0, sticky='ew', padx=10, pady=(8, 10))
        ctk.CTkButton(mod_buttons, text='Scan Mods', command=self.scan_mods).pack(side='left', padx=3)
        ctk.CTkButton(mod_buttons, text='Import Selected', command=self.import_selected_mod).pack(side='left', padx=3)
        ctk.CTkButton(mod_buttons, text='Import All Ready', command=self.import_all_mods).pack(side='left', padx=3)

        self.mods_list = ctk.CTkScrollableFrame(self.mods_panel, corner_radius=8)
        self.mods_list.grid(row=2, column=0, sticky='nsew', padx=10, pady=0)
        self.mods_list.grid_columnconfigure(0, weight=1)

        self.outfits_panel = ctk.CTkFrame(body, corner_radius=10)
        self.outfits_panel.grid(row=0, column=1, sticky='nsew', padx=(7, 0), pady=0)
        self.outfits_panel.grid_columnconfigure(0, weight=1)
        self.outfits_panel.grid_columnconfigure(1, weight=1)
        self.outfits_panel.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self.outfits_panel, text='2. Choose Active Outfit', font=ctk.CTkFont(size=18, weight='bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=12, pady=(12, 4)
        )
        ctk.CTkLabel(
            self.outfits_panel,
            text='Pick a character, pick an imported outfit, then apply the live selection.',
            anchor='w',
        ).grid(row=1, column=0, columnspan=2, sticky='ew', padx=12, pady=(0, 8))

        self.characters_list = ctk.CTkScrollableFrame(self.outfits_panel, corner_radius=8)
        self.characters_list.grid(row=2, column=0, sticky='nsew', padx=(10, 5), pady=0)
        self.characters_list.grid_columnconfigure(0, weight=1)
        self.outfits_list = ctk.CTkScrollableFrame(self.outfits_panel, corner_radius=8)
        self.outfits_list.grid(row=2, column=1, sticky='nsew', padx=(5, 10), pady=0)
        self.outfits_list.grid_columnconfigure(0, weight=1)

        outfit_buttons = ctk.CTkFrame(self.outfits_panel, fg_color='transparent')
        outfit_buttons.grid(row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=(8, 10))
        ctk.CTkButton(outfit_buttons, text='Use Selected Outfit', command=self.use_selected_outfit).pack(side='left', padx=3)
        ctk.CTkButton(outfit_buttons, text='Disable Character', command=self.disable_selected_character).pack(side='left', padx=3)
        ctk.CTkButton(outfit_buttons, text='Apply Live Selection', command=self.apply_runtime_rules).pack(side='right', padx=3)

    def _build_status(self):
        self.status_box = ctk.CTkTextbox(self, height=110, font=ctk.CTkFont(family='Consolas', size=12))
        self.status_box.grid(row=3, column=0, sticky='ew', padx=14, pady=(8, 14))

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
        if self.selected_mod_index is not None and self.selected_mod_index >= len(self.scanned_mods):
            self.selected_mod_index = None

    def scan_mods(self):
        self._scan_mods_from_disk()
        self._render_mods()
        ready_count = sum(1 for item in self.scanned_mods if item.get('ready'))
        self.refresh_status(f'Scanned {len(self.scanned_mods)} mod folder(s), {ready_count} ready.')

    def _clear_frame(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _row_button(self, parent, text: str, command, selected: bool = False, bad: bool = False):
        fg = '#1f6aa5' if selected else ('#7a3e1d' if bad else 'transparent')
        hover = '#2b78bd' if selected else '#343638'
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

    def _render_mods(self):
        self._clear_frame(self.mods_list)
        if self.profile_dir is None:
            ctk.CTkLabel(self.mods_list, text='No game .exe selected.', anchor='w').grid(sticky='ew', padx=8, pady=8)
            return
        if not self.scanned_mods:
            ctk.CTkLabel(self.mods_list, text='No mod folders found.', anchor='w').grid(sticky='ew', padx=8, pady=8)
            return
        for idx, item in enumerate(self.scanned_mods):
            ready = bool(item.get('ready'))
            prefix = 'READY' if ready else 'FIX'
            label = f'{prefix}  {item["character_id"]} / {item["source_outfit_name"]}'
            if item.get('issues'):
                label += f'  - {item["issues"][0]}'
            self._row_button(
                self.mods_list,
                label,
                command=lambda i=idx: self.select_mod(i),
                selected=(idx == self.selected_mod_index),
                bad=not ready,
            )

    def select_mod(self, idx: int):
        self.selected_mod_index = idx
        self._render_mods()
        item = self.scanned_mods[idx]
        lines = [
            f'Selected mod: {item["character_id"]} / {item["source_outfit_name"]}',
            f'Folder: {item["source_path"]}',
        ]
        for slot in REQUIRED_SLOTS:
            lines.append(f'{slot}: {item.get("slot_files", {}).get(slot, "<missing>")}')
        if item.get('issues'):
            lines.append('Issues: ' + '; '.join(item['issues']))
        self.refresh_status('\n'.join(lines))

    def import_selected_mod(self):
        profile = self._profile_required()
        if not profile:
            return
        if self.selected_mod_index is None:
            ready_indexes = [idx for idx, item in enumerate(self.scanned_mods) if item.get('ready')]
            if len(ready_indexes) == 1:
                self.selected_mod_index = ready_indexes[0]
            else:
                messagebox.showinfo('GPMI', 'Select one READY mod first.')
                return
        item = self.scanned_mods[self.selected_mod_index]
        if not item.get('ready'):
            messagebox.showerror('GPMI', 'Fix this mod before importing:\n' + '; '.join(item.get('issues', [])))
            return
        try:
            outfit = import_user_mod(profile, item['character_id'], item['source_outfit_name'])
            select_imported_outfit(profile, outfit['character_id'], outfit['id'])
            result = self._write_runtime(profile)
            self.selected_character_id = outfit['character_id']
            self.selected_outfit_id = outfit['id']
            self._scan_mods_from_disk()
            self._render_mods()
            self.refresh_outfits()
            self.refresh_status(
                f'Imported and selected {outfit["character_id"]} / {outfit["source_name"]} -> {outfit["id"]}.\n'
                f'Live portrait slots: {result.get("active_slots", 0)}.'
            )
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to import mod:\n{e}')

    def import_all_mods(self):
        profile = self._profile_required()
        if not profile:
            return
        try:
            imported, failures = import_all_ready_mods(profile)
            result = self._write_runtime(profile)
            self._scan_mods_from_disk()
            self._render_mods()
            self.refresh_outfits()
            lines = [
                f'Imported {len(imported)} mod(s).',
                f'Live portrait slots: {result.get("active_slots", 0)}.',
            ]
            if failures:
                lines.append('')
                lines.append('Skipped:')
                lines.extend(failures[:12])
                if len(failures) > 12:
                    lines.append(f'... {len(failures) - 12} more')
            self.refresh_status('\n'.join(lines))
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to import mods:\n{e}')

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
            self.selected_character_id = self.character_rows[0] if self.character_rows else None
        self._render_characters(meta)
        self._render_outfits(meta)

    def _render_characters(self, meta: dict):
        self._clear_frame(self.characters_list)
        characters = meta.get('characters', {})
        selected = meta.get('selected_outfits', {})
        if not characters:
            ctk.CTkLabel(self.characters_list, text='No imported outfits yet.', anchor='w').grid(sticky='ew', padx=8, pady=8)
            return
        for cid in self.character_rows:
            outfits = characters.get(cid, {}).get('outfits', {})
            selected_id = selected.get(cid, 'disabled')
            label = f'{cid}  ({len(outfits)})  active={selected_id}'
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
            ctk.CTkLabel(self.outfits_list, text='Select a character.', anchor='w').grid(sticky='ew', padx=8, pady=8)
            return
        profile = self.profile_dir
        if profile is None:
            return
        char = meta.get('characters', {}).get(self.selected_character_id, {})
        outfits = char.get('outfits', {})
        selected_id = meta.get('selected_outfits', {}).get(self.selected_character_id, '')
        if self.selected_outfit_id not in outfits:
            self.selected_outfit_id = selected_id if selected_id in outfits else (next(iter(sorted(outfits)), None))
        for outfit_id in sorted(outfits.keys()):
            outfit = outfits[outfit_id]
            ready, _issues = validate_imported_outfit(profile, outfit)
            mark = 'ACTIVE' if outfit_id == selected_id else 'READY' if ready else 'FIX'
            label = f'{mark}  {outfit.get("source_name", outfit_id)}  [{outfit_id}]'
            self.outfit_rows.append((outfit_id, outfit))
            self._row_button(
                self.outfits_list,
                label,
                command=lambda value=outfit_id: self.select_outfit(value),
                selected=(outfit_id == self.selected_outfit_id),
                bad=not ready,
            )
        if not outfits:
            ctk.CTkLabel(self.outfits_list, text='No outfits for this character.', anchor='w').grid(sticky='ew', padx=8, pady=8)

    def select_character(self, cid: str):
        self.selected_character_id = cid
        self.selected_outfit_id = None
        self.refresh_outfits()
        self.refresh_status(f'Selected character: {cid}')

    def select_outfit(self, outfit_id: str):
        self.selected_outfit_id = outfit_id
        self.refresh_outfits()
        self.refresh_status(f'Selected outfit: {self.selected_character_id} / {outfit_id}')

    def use_selected_outfit(self):
        profile = self._profile_required()
        if not profile:
            return
        if not self.selected_character_id or not self.selected_outfit_id:
            messagebox.showinfo('GPMI', 'Select a character and an outfit first.')
            return
        try:
            select_imported_outfit(profile, self.selected_character_id, self.selected_outfit_id)
            result = self._write_runtime(profile)
            self.refresh_outfits()
            self.refresh_status(
                f'Active outfit: {self.selected_character_id} / {self.selected_outfit_id}\n'
                f'Live portrait slots: {result.get("active_slots", 0)}.'
            )
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to use selected outfit:\n{e}')

    def disable_selected_character(self):
        profile = self._profile_required()
        if not profile:
            return
        if not self.selected_character_id:
            messagebox.showinfo('GPMI', 'Select a character first.')
            return
        try:
            clear_selected_outfit(profile, self.selected_character_id)
            result = self._write_runtime(profile)
            disabled = self.selected_character_id
            self.refresh_outfits()
            self.refresh_status(f'Disabled {disabled}.\nLive portrait slots: {result.get("active_slots", 0)}.')
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to disable character:\n{e}')

    def apply_runtime_rules(self):
        profile = self._profile_required()
        if not profile:
            return
        try:
            result = self._write_runtime(profile)
            self.refresh_outfits()
            self.refresh_status(
                f'Applied live selection to {RUNTIME_HASH_DB_FILE}.\n'
                f'Live portrait slots: {result.get("active_slots", 0)}.'
            )
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to apply selections:\n{e}')

    def _write_runtime(self, profile: Path) -> dict:
        result = build_live_portrait_manifest(profile)
        core_dir = Config.Importers.GPMI.Importer.importer_path / 'Core/GPMI'
        write_runtime_ini(
            profile,
            min_width=int(Config.Importers.GPMI.Importer.min_width),
            min_height=int(Config.Importers.GPMI.Importer.min_height),
            mirror_dirs=[core_dir],
        )
        return result

    def refresh_status(self, message: str | None = None):
        profile = self.profile_dir
        if profile is None:
            self.summary_label.configure(text='No game .exe selected.')
            self._set_status(message or 'Select the exact game .exe in launcher settings first.')
            return

        summary = summarize_live_portrait_manifest(profile)
        runtime = summary.get('runtime') or {}
        meta = load_mod_meta(profile)
        imported_outfits = sum(len(c.get('outfits', {})) for c in meta.get('characters', {}).values())
        ready_mods = sum(1 for item in self.scanned_mods if item.get('ready'))
        self.summary_label.configure(
            text=(
                f'Live: {runtime.get("active_slots", 0)} slots / {runtime.get("active_characters", 0)} characters   '
                f'Mods: {ready_mods}/{len(self.scanned_mods)} ready   '
                f'Imported: {imported_outfits} outfits   '
                f'Revision: {runtime.get("revision", 0)}'
            )
        )

        lines = []
        if message:
            lines.append(message)
            lines.append('')
        lines.extend([
            f'Mods folder: {profile / USER_MODS_DIR}',
            f'Source images: {profile / RUNTIME_MODS_DIR}',
            f'Live manifest: {profile / RUNTIME_HASH_DB_FILE}',
            f'Meta: {profile / META_FILE}',
        ])
        generated = runtime.get('generated') or {}
        issues = generated.get('issues') or {}
        if issues:
            lines.append('')
            lines.append('Runtime issues:')
            for cid, values in issues.items():
                lines.append(f'  {cid}: {"; ".join(values)}')
        self._set_status('\n'.join(lines))

    def _set_status(self, text: str):
        self.status_box.configure(state='normal')
        self.status_box.delete('1.0', 'end')
        self.status_box.insert('end', text)
        self.status_box.configure(state='disabled')
