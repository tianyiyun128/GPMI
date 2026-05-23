from __future__ import annotations

import argparse
from collections import defaultdict
import subprocess
import uuid
from pathlib import Path
from xml.sax.saxutils import escape


UPGRADE_CODE = "2FE50C15-4B01-4C73-9E87-6F4A1F921A80"
INSTALL_DIR_REGISTRY_KEY = "Software\\GPMI"
INSTALL_DIR_REGISTRY_VALUE = "InstallDir"
SHORTCUTS_COMPONENT_GUID = "F86D7E2F-2B4A-46C3-A46E-5B40D2D74A1C"
INSTALL_DIR_REGISTRY_COMPONENT_GUID = "5C5AFC80-770E-4B35-8D7E-B44C6188B741"


def wix_id(prefix: str, index: int) -> str:
    return f"{prefix}{index:05d}"


def write_wxs(stage_dir: Path, wxs_path: Path, version: str) -> None:
    files = [path for path in sorted(stage_dir.rglob("*")) if path.is_file()]
    directories: dict[Path, str] = {stage_dir: "APPDIR"}
    child_dirs: dict[Path, list[Path]] = defaultdict(list)
    child_files: dict[Path, list[Path]] = defaultdict(list)

    launcher_path = stage_dir / "Resources" / "Bin" / "GPMI.exe"
    launcher_file_id: str | None = None
    launcher_working_dir_id: str | None = None

    for directory in sorted({parent for path in files for parent in [path.parent, *path.parent.parents]}, key=lambda p: len(p.parts)):
        if directory == stage_dir or stage_dir not in directory.parents:
            continue
        directories[directory] = wix_id("Dir", len(directories))
        child_dirs[directory.parent].append(directory)

    for path in files:
        child_files[path.parent].append(path)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">',
        f'  <Package Name="GPMI" Manufacturer="GPMI" Version="{escape(version)}" UpgradeCode="{UPGRADE_CODE}" Scope="perUser">',
        '    <MajorUpgrade DowngradeErrorMessage="A newer version of GPMI is already installed." />',
        '    <MediaTemplate EmbedCab="yes" />',
        '    <Property Id="WIXUI_INSTALLDIR" Value="APPDIR" />',
        '    <Property Id="APPDIR">',
        f'      <RegistrySearch Id="SearchInstallDir" Root="HKCU" Key="{INSTALL_DIR_REGISTRY_KEY}" Name="{INSTALL_DIR_REGISTRY_VALUE}" Type="raw" />',
        '    </Property>',
        f'    <WixVariable Id="WixUILicenseRtf" Value="{escape(str((wxs_path.parent / "GPMI-License.rtf").resolve()))}" />',
        '    <ui:WixUI Id="WixUI_InstallDir" />',
        '    <StandardDirectory Id="ProgramMenuFolder">',
        '      <Directory Id="GPMIProgramMenuFolder" Name="GPMI" />',
        '    </StandardDirectory>',
        '    <StandardDirectory Id="LocalAppDataFolder">',
        '      <Directory Id="APPDIR" Name="GPMI">',
    ]

    component_refs: list[str] = []
    component_index = 0

    def emit_directory(directory: Path, indent_level: int) -> None:
        nonlocal component_index, launcher_file_id, launcher_working_dir_id
        indent = "  " * indent_level

        for child in sorted(child_dirs[directory], key=lambda p: p.name.lower()):
            lines.append(f'{indent}<Directory Id="{directories[child]}" Name="{escape(child.name)}">')
            emit_directory(child, indent_level + 1)
            lines.append(f'{indent}</Directory>')

        for path in sorted(child_files[directory], key=lambda p: p.name.lower()):
            component_index += 1
            component_id = wix_id("Cmp", component_index)
            file_id = wix_id("File", component_index)
            component_refs.append(component_id)
            if path == launcher_path:
                launcher_file_id = file_id
                launcher_working_dir_id = directories[path.parent]
            lines.append(f'{indent}<Component Id="{component_id}" Guid="{uuid.uuid5(uuid.NAMESPACE_URL, path.relative_to(stage_dir).as_posix())}">')
            lines.append(f'{indent}  <File Id="{file_id}" Source="{escape(str(path))}" KeyPath="yes" />')
            lines.append(f'{indent}</Component>')

    emit_directory(stage_dir, 4)

    if launcher_file_id and launcher_working_dir_id:
        component_refs.append("CmpShortcuts")
        lines.extend([
            f'        <Component Id="CmpShortcuts" Guid="{SHORTCUTS_COMPONENT_GUID}">',
            f'          <Shortcut Id="AppInstallDirShortcut" Name="GPMI" Description="Launch GPMI" Target="[#{launcher_file_id}]" WorkingDirectory="{launcher_working_dir_id}" />',
            f'          <Shortcut Id="AppStartMenuShortcut" Directory="GPMIProgramMenuFolder" Name="GPMI" Description="Launch GPMI" Target="[#{launcher_file_id}]" WorkingDirectory="{launcher_working_dir_id}" />',
            '          <Shortcut Id="UninstallInstallDirShortcut" Name="Uninstall GPMI" Description="Uninstall GPMI" Target="[SystemFolder]msiexec.exe" Arguments="/x [ProductCode]" />',
            '          <Shortcut Id="UninstallStartMenuShortcut" Directory="GPMIProgramMenuFolder" Name="Uninstall GPMI" Description="Uninstall GPMI" Target="[SystemFolder]msiexec.exe" Arguments="/x [ProductCode]" />',
            '          <RemoveFolder Id="RemoveGPMIProgramMenuFolder" Directory="GPMIProgramMenuFolder" On="uninstall" />',
            f'          <RegistryValue Root="HKCU" Key="{INSTALL_DIR_REGISTRY_KEY}" Name="Shortcuts" Value="1" Type="integer" KeyPath="yes" />',
            '        </Component>',
        ])

    component_refs.append("CmpInstallDirRegistry")
    lines.extend([
        f'        <Component Id="CmpInstallDirRegistry" Guid="{INSTALL_DIR_REGISTRY_COMPONENT_GUID}">',
        f'          <RegistryValue Root="HKCU" Key="{INSTALL_DIR_REGISTRY_KEY}" Name="{INSTALL_DIR_REGISTRY_VALUE}" Value="[APPDIR]" Type="string" KeyPath="yes" />',
        '        </Component>',
    ])

    lines.extend([
        '      </Directory>',
        '    </StandardDirectory>',
        '    <Feature Id="MainFeature" Title="GPMI" Level="1">',
    ])
    for component_id in component_refs:
        lines.append(f'      <ComponentRef Id="{component_id}" />')
    lines.extend([
        '    </Feature>',
        '  </Package>',
        '</Wix>',
    ])

    wxs_path.parent.mkdir(parents=True, exist_ok=True)
    license_path = wxs_path.parent / "GPMI-License.rtf"
    license_path.write_text(
        r"{\rtf1\ansi\deff0{\fonttbl{\f0 Segoe UI;}}\f0\fs20 GPMI Launcher\par\par This installer will install GPMI Launcher on this computer.\par}",
        encoding="ascii",
    )
    wxs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--stage-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wxs", required=True, type=Path)
    args = parser.parse_args()

    write_wxs(args.stage_dir.resolve(), args.wxs.resolve(), args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["wix", "build", str(args.wxs), "-ext", "WixToolset.UI.wixext", "-o", str(args.output)], check=True)
    print(f"[OK] Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
