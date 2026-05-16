from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional

import core.path_manager as Paths

GPMI_VERSION = "0.2.0"


@dataclass
class PortraitRule:
    enabled: bool = True
    hash: str = ""
    replacement: str = ""
    note: str = ""


@dataclass
class HashDb:
    enabled: bool = True
    dump_unknown: bool = True
    min_width: int = 32
    min_height: int = 32
    rules: List[PortraitRule] = field(default_factory=list)


def normalize_hash(hash_text: str) -> str:
    value = hash_text.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    value = "".join(ch for ch in value if ch in "0123456789abcdef")
    if not value:
        raise ValueError("hash is empty")
    if len(value) > 16:
        raise ValueError("hash is longer than 64 bits")
    return "0x" + value.rjust(16, "0")


def _rule_from_dict(data: dict) -> PortraitRule:
    rule = PortraitRule(
        enabled=bool(data.get("enabled", True)),
        hash=str(data.get("hash", "")),
        replacement=str(data.get("replacement", "")),
        note=str(data.get("note", "")),
    )
    if rule.hash:
        rule.hash = normalize_hash(rule.hash)
    return rule


def load_hash_db(path: Path) -> HashDb:
    if not path.is_file():
        return HashDb()
    data = json.loads(path.read_text(encoding="utf-8"))
    return HashDb(
        enabled=bool(data.get("enabled", True)),
        dump_unknown=bool(data.get("dump_unknown", True)),
        min_width=int(data.get("min_width", 32)),
        min_height=int(data.get("min_height", 32)),
        rules=[_rule_from_dict(item) for item in data.get("rules", [])],
    )


def save_hash_db(path: Path, db: HashDb) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(db)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_profile(importer_path: Path) -> None:
    """Create the runtime profile layout used by the ReShade add-on.

    Layout:
      Core/GPMI/PortraitHashReplace.addon64  - compiled add-on, user-built or copied here
      Core/GPMI/ReShade64.dll                - optional ReShade DLL for direct injection
      Core/GPMI/ptr_config.ini               - runtime add-on config
      Mods/<mod>/textures/*.ptrtex           - replacement textures
      Dumps/*.ptrtex                         - textures dumped by the add-on
      hash_db.json                           - enabled replacement rules
    """
    for rel in [
        "Core/GPMI",
        "Core/GPMI/Addons",
        "Mods/Default/textures",
        "Dumps",
        "Tools",
    ]:
        Paths.verify_path(importer_path / rel)
    db_path = importer_path / "hash_db.json"
    if not db_path.exists():
        save_hash_db(db_path, HashDb())
    write_runtime_ini(importer_path)


def write_runtime_ini(importer_path: Path, db: Optional[HashDb] = None) -> None:
    if db is None:
        db = load_hash_db(importer_path / "hash_db.json")
    common = (
        "[core]\n"
        f"enabled={'true' if db.enabled else 'false'}\n"
        f"dump_unknown={'true' if db.dump_unknown else 'false'}\n"
        f"min_width={int(db.min_width)}\n"
        f"min_height={int(db.min_height)}\n"
    )
    root_ini = common + "profile_dir=.\nhash_db=hash_db.json\nlog_file=PortraitHashReplace.log\n"
    core_ini = common + "profile_dir=../..\nhash_db=hash_db.json\nlog_file=PortraitHashReplace.log\n"
    (importer_path / "ptr_config.ini").write_text(root_ini, encoding="utf-8")
    core_path = importer_path / "Core/GPMI/ptr_config.ini"
    core_path.parent.mkdir(parents=True, exist_ok=True)
    core_path.write_text(core_ini, encoding="utf-8")


def import_replacement_texture(importer_path: Path, src_png: Path, mod_name: str, bgra: bool = False) -> tuple[Path, int, int, str]:
    from core.gpmi.ptrtex import png_to_ptrtex

    safe_mod_name = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in mod_name.strip()) or "Default"
    dst = importer_path / "Mods" / safe_mod_name / "textures" / (src_png.stem + ".ptrtex")
    width, height, fmt = png_to_ptrtex(src_png, dst, bgra=bgra)
    return dst, width, height, fmt


def relative_to_profile(importer_path: Path, path: Path) -> str:
    try:
        return path.relative_to(importer_path).as_posix()
    except ValueError:
        return path.as_posix()


def add_or_update_rule(importer_path: Path, hash_text: str, replacement: Path, note: str = "", enabled: bool = True) -> PortraitRule:
    db_path = importer_path / "hash_db.json"
    db = load_hash_db(db_path)
    norm_hash = normalize_hash(hash_text)
    rel = relative_to_profile(importer_path, replacement)
    for rule in db.rules:
        if normalize_hash(rule.hash) == norm_hash:
            rule.enabled = enabled
            rule.replacement = rel
            rule.note = note
            save_hash_db(db_path, db)
            write_runtime_ini(importer_path, db)
            return rule
    rule = PortraitRule(enabled=enabled, hash=norm_hash, replacement=rel, note=note)
    db.rules.append(rule)
    save_hash_db(db_path, db)
    write_runtime_ini(importer_path, db)
    return rule


def remove_rules(importer_path: Path, hashes: Iterable[str]) -> int:
    normalized = {normalize_hash(h) for h in hashes}
    db_path = importer_path / "hash_db.json"
    db = load_hash_db(db_path)
    before = len(db.rules)
    db.rules = [r for r in db.rules if normalize_hash(r.hash) not in normalized]
    save_hash_db(db_path, db)
    write_runtime_ini(importer_path, db)
    return before - len(db.rules)


def copy_kernel_sources(src_root: Path, importer_path: Path) -> None:
    dst = importer_path / "Tools" / "kernel_source"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src_root, dst)
