from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tkinter import filedialog, simpledialog, messagebox

import customtkinter as ctk

import core.config_manager as Config
import core.event_manager as Events
import core.path_manager as Paths

from core.gpmi.profile import (
    add_or_update_rule,
    ensure_profile,
    import_replacement_texture,
    load_hash_db,
    normalize_hash,
    remove_rules,
    save_hash_db,
    write_runtime_ini,
)
from core.gpmi.ptrtex import ptrtex_info, ptrtex_to_image, ptrtex_to_png


class PortraitManagerWindow(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title('GPMI Portrait Manager')
        self.geometry('1080x680')
        self.minsize(980, 600)
        self.transient(master)

        self.importer_path = Config.Importers.GPMI.Importer.importer_path
        ensure_profile(self.importer_path)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(self, corner_radius=16)
        self.header.grid(row=0, column=0, sticky='ew', padx=16, pady=(16, 8))
        self.header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.header, text='GPMI', font=ctk.CTkFont(size=28, weight='bold')).grid(row=0, column=0, rowspan=2, padx=18, pady=14)
        self.path_label = ctk.CTkLabel(self.header, text=str(self.importer_path), anchor='w')
        self.path_label.grid(row=0, column=1, sticky='ew', padx=10, pady=(16, 2))
        ctk.CTkLabel(self.header, text='Hash-based Godot portrait replacement profile', anchor='w').grid(row=1, column=1, sticky='ew', padx=10, pady=(0, 14))
        ctk.CTkButton(self.header, text='Open Profile', width=120, command=self.open_profile).grid(row=2, column=2, padx=(8, 16), pady=(14, 2))
        ctk.CTkButton(self.header, text='Open Dumps', width=120, command=self.open_dumps).grid(row=2, column=2, padx=(8, 16), pady=(2, 14))

        self.tabs = ctk.CTkTabview(self, corner_radius=16)
        self.tabs.grid(row=1, column=0, sticky='nsew', padx=16, pady=(8, 16))
        self.rules_tab = self.tabs.add('Rules')
        self.import_tab = self.tabs.add('Import Texture')
        self.dumps_tab = self.tabs.add('Dumps')
        self.runtime_tab = self.tabs.add('Runtime')

        self._build_rules_tab()
        self._build_import_tab()
        self._build_dumps_tab()
        self._build_runtime_tab()
        self.refresh_all()

    def _build_rules_tab(self):
        self.rules_tab.grid_columnconfigure(0, weight=1)
        self.rules_tab.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(self.rules_tab)
        toolbar.grid(row=0, column=0, sticky='ew', padx=12, pady=12)
        ctk.CTkButton(toolbar, text='Refresh', command=self.refresh_rules).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Toggle Selected', command=self.toggle_selected_rule).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Delete Selected', fg_color='#8a2f2f', hover_color='#a03a3a', command=self.delete_selected_rule).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Save Runtime INI', command=self.save_runtime).pack(side='right', padx=6, pady=8)
        self.rules_box = ctk.CTkTextbox(self.rules_tab, font=ctk.CTkFont(family='Consolas', size=14))
        self.rules_box.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 12))

    def _build_import_tab(self):
        self.import_tab.grid_columnconfigure(1, weight=1)
        self.import_tab.grid_rowconfigure(4, weight=1)
        for i, label in enumerate(['Texture hash', 'Mod name', 'Note']):
            ctk.CTkLabel(self.import_tab, text=label, anchor='e').grid(row=i, column=0, padx=(24, 12), pady=12, sticky='e')
        self.hash_entry = ctk.CTkEntry(self.import_tab, placeholder_text='0x1234567890abcdef')
        self.hash_entry.grid(row=0, column=1, sticky='ew', padx=(0, 24), pady=12)
        self.mod_entry = ctk.CTkEntry(self.import_tab, placeholder_text='Default')
        self.mod_entry.grid(row=1, column=1, sticky='ew', padx=(0, 24), pady=12)
        self.note_entry = ctk.CTkEntry(self.import_tab, placeholder_text='例如：jean_default / Unit_H/...')
        self.note_entry.grid(row=2, column=1, sticky='ew', padx=(0, 24), pady=12)
        ctk.CTkButton(self.import_tab, text='Choose PNG and Add Rule', height=44, command=self.import_png_rule).grid(row=3, column=1, sticky='e', padx=(0, 24), pady=12)
        self.import_status = ctk.CTkTextbox(self.import_tab, height=240)
        self.import_status.grid(row=4, column=0, columnspan=2, sticky='nsew', padx=24, pady=(12, 24))

    def _build_dumps_tab(self):
        self.dumps_tab.grid_columnconfigure(0, weight=3)
        self.dumps_tab.grid_columnconfigure(1, weight=2)
        self.dumps_tab.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(self.dumps_tab)
        toolbar.grid(row=0, column=0, columnspan=2, sticky='ew', padx=12, pady=12)
        ctk.CTkButton(toolbar, text='Refresh Dumps', command=self.refresh_dumps).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Preview Selected', command=self.preview_selected_dump).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Export Selected PNG', command=self.export_selected_dump_png).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Export All PNG', command=self.export_all_dump_pngs).pack(side='left', padx=6, pady=8)
        ctk.CTkButton(toolbar, text='Create Rule From Dump', command=self.create_rule_from_dump).pack(side='left', padx=6, pady=8)
        self.dumps_box = ctk.CTkTextbox(self.dumps_tab, font=ctk.CTkFont(family='Consolas', size=14))
        self.dumps_box.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 12))
        preview_frame = ctk.CTkFrame(self.dumps_tab)
        preview_frame.grid(row=1, column=1, sticky='nsew', padx=(0, 12), pady=(0, 12))
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)
        self.dump_preview_info = ctk.CTkLabel(preview_frame, text='Select a dump and click Preview Selected.', anchor='w')
        self.dump_preview_info.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 8))
        self.dump_preview_label = ctk.CTkLabel(preview_frame, text='No preview', width=360, height=360)
        self.dump_preview_label.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 12))
        self.dump_preview_image = None

    def _build_runtime_tab(self):
        self.runtime_tab.grid_columnconfigure(1, weight=1)
        self.runtime_tab.grid_rowconfigure(5, weight=1)
        ctk.CTkLabel(self.runtime_tab, text='Godot EXE name').grid(row=0, column=0, padx=(24, 12), pady=12, sticky='e')
        self.exe_entry = ctk.CTkEntry(self.runtime_tab, placeholder_text='Leave empty to auto-select the only .exe')
        self.exe_entry.grid(row=0, column=1, sticky='ew', padx=(0, 24), pady=12, columnspan=2)
        self.exe_entry.insert(0, Config.Importers.GPMI.Importer.custom_game_exe_name)

        ctk.CTkLabel(self.runtime_tab, text='ReShade64.dll').grid(row=1, column=0, padx=(24, 12), pady=12, sticky='e')
        self.reshade_entry = ctk.CTkEntry(self.runtime_tab)
        self.reshade_entry.grid(row=1, column=1, sticky='ew', padx=(0, 12), pady=12)
        self.reshade_entry.insert(0, Config.Importers.GPMI.Importer.reshade_dll_path or str(self.importer_path / 'Core/GPMI/ReShade64.dll'))
        ctk.CTkButton(self.runtime_tab, text='Browse', command=lambda: self.browse_runtime(self.reshade_entry, 'ReShade64.dll')).grid(row=1, column=2, padx=(0, 24), pady=12)

        ctk.CTkLabel(self.runtime_tab, text='PortraitHashReplace.addon64').grid(row=2, column=0, padx=(24, 12), pady=12, sticky='e')
        self.addon_entry = ctk.CTkEntry(self.runtime_tab)
        self.addon_entry.grid(row=2, column=1, sticky='ew', padx=(0, 12), pady=12)
        self.addon_entry.insert(0, Config.Importers.GPMI.Importer.addon_dll_path or str(self.importer_path / 'Core/GPMI/PortraitHashReplace.addon64'))
        ctk.CTkButton(self.runtime_tab, text='Browse', command=lambda: self.browse_runtime(self.addon_entry, 'PortraitHashReplace.addon64')).grid(row=2, column=2, padx=(0, 24), pady=12)

        self.dump_var = ctk.BooleanVar(value=Config.Importers.GPMI.Importer.dump_unknown)
        ctk.CTkCheckBox(self.runtime_tab, text='Dump unknown textures', variable=self.dump_var).grid(row=3, column=1, sticky='w', padx=(0, 24), pady=8)
        ctk.CTkButton(self.runtime_tab, text='Save Runtime Settings', command=self.save_runtime_settings).grid(row=4, column=1, sticky='e', padx=(0, 24), pady=16)

    def open_profile(self):
        self._open_folder(self.importer_path)

    def open_dumps(self):
        self._open_folder(self.importer_path / 'Dumps')

    def _open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            subprocess.Popen(['explorer.exe', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])

    def refresh_all(self):
        self.refresh_rules()
        self.refresh_dumps()

    def refresh_rules(self):
        db = load_hash_db(self.importer_path / 'hash_db.json')
        self.rules_box.configure(state='normal')
        self.rules_box.delete('1.0', 'end')
        lines = [f'enabled={db.enabled} dump_unknown={db.dump_unknown} min={db.min_width}x{db.min_height}', '']
        for idx, rule in enumerate(db.rules, 1):
            status = 'ON ' if rule.enabled else 'OFF'
            lines.append(f'{idx:03d} [{status}] {rule.hash} -> {rule.replacement}  # {rule.note}')
        if len(lines) == 2:
            lines.append('No rules yet. Import a PNG or create a rule from a dump.')
        self.rules_box.insert('end', '\n'.join(lines))
        self.rules_box.configure(state='normal')

    def _selected_rule_index(self):
        try:
            line_no = int(self.rules_box.index('insert').split('.')[0])
        except Exception:
            return None
        line = self.rules_box.get(f'{line_no}.0', f'{line_no}.end')
        if not line[:3].isdigit():
            return None
        return int(line[:3]) - 1

    def toggle_selected_rule(self):
        idx = self._selected_rule_index()
        if idx is None:
            messagebox.showinfo('GPMI', 'Select a rule line first.')
            return
        db = load_hash_db(self.importer_path / 'hash_db.json')
        if idx >= len(db.rules):
            return
        db.rules[idx].enabled = not db.rules[idx].enabled
        save_hash_db(self.importer_path / 'hash_db.json', db)
        write_runtime_ini(self.importer_path, db)
        self.refresh_rules()

    def delete_selected_rule(self):
        idx = self._selected_rule_index()
        if idx is None:
            messagebox.showinfo('GPMI', 'Select a rule line first.')
            return
        db = load_hash_db(self.importer_path / 'hash_db.json')
        if idx >= len(db.rules):
            return
        rule = db.rules[idx]
        if messagebox.askyesno('GPMI', f'Delete rule {rule.hash}?'):
            remove_rules(self.importer_path, [rule.hash])
            self.refresh_rules()

    def import_png_rule(self):
        try:
            hash_text = normalize_hash(self.hash_entry.get())
        except Exception as e:
            messagebox.showerror('GPMI', f'Invalid hash: {e}')
            return
        src = filedialog.askopenfilename(title='Choose replacement PNG', filetypes=[('PNG images', '*.png'), ('All files', '*.*')])
        if not src:
            return
        mod_name = self.mod_entry.get().strip() or 'Default'
        try:
            dst, width, height, fmt = import_replacement_texture(self.importer_path, Path(src), mod_name, Config.Importers.GPMI.Importer.ptr_bgra_import)
            rule = add_or_update_rule(self.importer_path, hash_text, dst, self.note_entry.get().strip(), True)
            self.import_status.insert('end', f'Imported {src}\n -> {dst}\n {width}x{height} {fmt}\n rule: {rule.hash}\n\n')
            self.refresh_rules()
        except Exception as e:
            messagebox.showerror('GPMI', str(e))

    def refresh_dumps(self):
        dumps = sorted((self.importer_path / 'Dumps').glob('*.ptrtex'))
        self.dumps_box.configure(state='normal')
        self.dumps_box.delete('1.0', 'end')
        if not dumps:
            self.dumps_box.insert('end', 'No dumps yet. Start the game with Dump unknown textures enabled, then return here.\n')
            return
        for idx, dump in enumerate(dumps, 1):
            try:
                info = ptrtex_info(dump)
                self.dumps_box.insert('end', f'{idx:03d} {dump.stem}  {info["width"]}x{info["height"]} {info["format"]}  {dump}\n')
            except Exception as e:
                self.dumps_box.insert('end', f'{idx:03d} {dump.stem}  unreadable: {e}\n')

    def _selected_dump_hash(self):
        try:
            line_no = int(self.dumps_box.index('insert').split('.')[0])
        except Exception:
            return None
        line = self.dumps_box.get(f'{line_no}.0', f'{line_no}.end')
        if not line[:3].isdigit():
            return None
        parts = line.split()
        return parts[1] if len(parts) > 1 else None

    def _selected_dump_path(self):
        hash_text = self._selected_dump_hash()
        if hash_text is None:
            return None
        path = self.importer_path / 'Dumps' / f'{hash_text}.ptrtex'
        return path if path.is_file() else None

    def preview_selected_dump(self):
        path = self._selected_dump_path()
        if path is None:
            messagebox.showinfo('GPMI', 'Select a dump line first.')
            return
        try:
            image = ptrtex_to_image(path)
            info = ptrtex_info(path)
            preview = image.copy()
            preview.thumbnail((420, 420))
            self.dump_preview_image = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
            self.dump_preview_label.configure(image=self.dump_preview_image, text='')
            self.dump_preview_info.configure(
                text=f'{path.stem}  {info["width"]}x{info["height"]} {info["format"]}'
            )
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to preview dump: {e}')

    def export_selected_dump_png(self):
        path = self._selected_dump_path()
        if path is None:
            messagebox.showinfo('GPMI', 'Select a dump line first.')
            return
        dst = filedialog.asksaveasfilename(
            title='Export dump as PNG',
            initialfile=f'{path.stem}.png',
            defaultextension='.png',
            filetypes=[('PNG images', '*.png'), ('All files', '*.*')],
        )
        if not dst:
            return
        try:
            width, height, fmt = ptrtex_to_png(path, Path(dst))
            messagebox.showinfo('GPMI', f'Exported {width}x{height} {fmt} PNG.')
        except Exception as e:
            messagebox.showerror('GPMI', f'Failed to export PNG: {e}')

    def export_all_dump_pngs(self):
        dumps = sorted((self.importer_path / 'Dumps').glob('*.ptrtex'))
        if not dumps:
            messagebox.showinfo('GPMI', 'No dumps to export.')
            return
        out_dir = self.importer_path / 'Dumps' / 'png'
        exported = 0
        failures = []
        for dump in dumps:
            try:
                ptrtex_to_png(dump, out_dir / f'{dump.stem}.png')
                exported += 1
            except Exception as e:
                failures.append(f'{dump.name}: {e}')
        if failures:
            messagebox.showwarning('GPMI', f'Exported {exported} PNG files.\n\nFailed:\n' + '\n'.join(failures[:8]))
        else:
            messagebox.showinfo('GPMI', f'Exported {exported} PNG files to:\n{out_dir}')

    def create_rule_from_dump(self):
        hash_text = self._selected_dump_hash()
        if hash_text is None:
            hash_text = simpledialog.askstring('GPMI', 'Texture hash from dump:')
        if not hash_text:
            return
        self.hash_entry.delete(0, 'end')
        self.hash_entry.insert(0, hash_text)
        self.tabs.set('Import Texture')

    def save_runtime(self):
        db = load_hash_db(self.importer_path / 'hash_db.json')
        write_runtime_ini(self.importer_path, db)
        messagebox.showinfo('GPMI', 'Runtime INI saved.')

    def browse_runtime(self, entry, title):
        path = filedialog.askopenfilename(title=title, filetypes=[('DLL/addon files', '*.dll *.addon64'), ('All files', '*.*')])
        if path:
            entry.delete(0, 'end')
            entry.insert(0, path)

    def save_runtime_settings(self):
        Config.Importers.GPMI.Importer.custom_game_exe_name = self.exe_entry.get().strip()
        Config.Importers.GPMI.Importer.reshade_dll_path = self.reshade_entry.get().strip()
        Config.Importers.GPMI.Importer.addon_dll_path = self.addon_entry.get().strip()
        Config.Importers.GPMI.Importer.dump_unknown = bool(self.dump_var.get())
        Config.Config.save()
        db = load_hash_db(self.importer_path / 'hash_db.json')
        db.dump_unknown = Config.Importers.GPMI.Importer.dump_unknown
        save_hash_db(self.importer_path / 'hash_db.json', db)
        write_runtime_ini(self.importer_path, db)
        messagebox.showinfo('GPMI', 'Runtime settings saved.')
