from __future__ import annotations

import ast
import py_compile
import re
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f'[FAIL] {msg}')


def ok(msg: str) -> None:
    print(f'[ OK ] {msg}')


def warn(msg: str) -> None:
    print(f'[WARN] {msg}')


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    src = root / 'src' / 'xxmi_launcher'
    errors = 0

    print(f'[GPMI] Preflight root: {root}')

    required = [
        root / 'src' / 'xxmi_launcher' / 'core' / 'application.py',
        root / 'src' / 'xxmi_launcher' / 'core' / 'config_manager.py',
        root / 'src' / 'xxmi_launcher' / 'core' / 'packages' / 'model_importers' / 'gpmi_package.py',
        root / 'src' / 'xxmi_launcher' / 'gui' / 'vars.py',
        root / 'Themes' / 'Default' / 'MainWindow' / 'LauncherFrame' / 'game-tile-gpmi.png',
        root / 'Themes' / 'Default' / 'MainWindow' / 'LauncherFrame' / 'background-image-gpmi.webp',
        root / 'Resources' / 'Packages' / 'GPMI' / 'Tools' / 'kernel_source' / 'build.cmd',
        root / 'Resources' / 'Packages' / 'GPMI' / 'Tools' / 'kernel_source' / 'src' / 'addon.cpp',
        root / 'Resources' / 'Packages' / 'GPMI' / 'Tools' / 'kernel_source' / 'src' / 'ptrtex.cpp',
    ]
    for path in required:
        if path.exists():
            ok(f'exists: {path.relative_to(root)}')
        else:
            fail(f'missing: {path.relative_to(root)}')
            errors += 1

    # Runtime DLLs are produced/copied by the user, so warn instead of fail.
    runtime = [
        root / 'Resources' / 'Packages' / 'GPMI' / 'Core' / 'GPMI' / 'ReShade64.dll',
        root / 'Resources' / 'Packages' / 'GPMI' / 'Core' / 'GPMI' / 'PortraitHashReplace.addon64',
    ]
    for path in runtime:
        if path.exists():
            ok(f'runtime exists: {path.relative_to(root)}')
        else:
            warn(f'runtime missing, add/build before launching game: {path.relative_to(root)}')

    # Python syntax check; this does not import third-party modules.
    for py_file in src.rglob('*.py'):
        try:
            py_compile.compile(str(py_file), doraise=True)
        except Exception as exc:
            fail(f'python syntax: {py_file.relative_to(root)}: {exc}')
            errors += 1
    if errors == 0:
        ok('python syntax check passed')

    # Static check that every Config/Vars.Active.Importer.xxx reference has a field/property.
    refs = set()
    for py_file in src.rglob('*.py'):
        text = py_file.read_text(encoding='utf-8', errors='ignore')
        refs |= set(re.findall(r'(?:Vars|Config)\.Active\.Importer\.([A-Za-z_][A-Za-z0-9_]*)', text))

    fields = set()
    props = set()
    for rel in [
        'core/packages/model_importers/model_importer.py',
        'core/packages/model_importers/gpmi_package.py',
    ]:
        tree = ast.parse((src / rel).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in {'ModelImporterConfig', 'GPMIConfig'}:
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        fields.add(item.target.id)
                    elif isinstance(item, ast.FunctionDef):
                        for dec in item.decorator_list:
                            if isinstance(dec, ast.Name) and dec.id == 'property':
                                props.add(item.name)
    methods = {'extra_dll_paths', 'is_xxmi_dll_in_extra_libraries', 'is_xxmi_dll_used'}
    missing = sorted(refs - fields - props - methods)
    if missing:
        fail('missing GPMIConfig compatibility fields: ' + ', '.join(missing))
        errors += 1
    else:
        ok('GPMIConfig covers all active importer settings references')

    # Static check: the active config should only expose GPMI importer in config_manager.
    cfg_text = (src / 'core' / 'config_manager.py').read_text(encoding='utf-8', errors='ignore')
    for old in ['gimi_package', 'srmi_package', 'wwmi_package', 'zzmi_package', 'himi_package', 'efmi_package']:
        if old in cfg_text:
            fail(f'old importer reference remains in config_manager.py: {old}')
            errors += 1
    if errors == 0:
        ok('GPMI-only config check passed')

    if errors:
        print(f'[GPMI] Preflight failed with {errors} error(s).')
        return 1
    print('[GPMI] Preflight passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
