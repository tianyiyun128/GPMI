from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import core.path_manager as Paths

GPMI_VERSION = "0.3.0"


@dataclass
class PortraitRule:
    enabled: bool = True
    hash: str = ""
    replacement: str = ""
    note: str = ""
    character_id: str = ""
    outfit_id: str = "default"
    slot: str = "Unit"


@dataclass
class CharacterOutfit:
    id: str = "default"
    name: str = "Default"
    rules: List[PortraitRule] = field(default_factory=list)


@dataclass
class CharacterProfile:
    id: str = ""
    name: str = ""
    selected_outfit_id: str = "default"
    outfits: List[CharacterOutfit] = field(default_factory=list)


@dataclass
class HashDb:
    enabled: bool = True
    dump_unknown: bool = True
    min_width: int = 32
    min_height: int = 32
    rules: List[PortraitRule] = field(default_factory=list)
    characters: List[CharacterProfile] = field(default_factory=list)


def sanitize_identifier(value: str, fallback: str = "default") -> str:
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip()
    safe = "_".join(safe.split())
    return safe or fallback


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


def infer_rule_identity(note: str, replacement: str = "") -> Tuple[str, str, str]:
    """Infer character/outfit/slot from legacy notes such as `Unit/jean/default`.

    Numeric suffix variants (`mona_2`, `klee_2`) are treated as outfits of the
    base character so only one outfit is active for that character at a time.
    """
    source = (note or "").replace("\\", "/").strip("/")
    parts = [part for part in source.split("/") if part]
    slot = parts[0] if len(parts) >= 1 else "Unit"
    raw_character = parts[1] if len(parts) >= 2 else "Unknown"
    outfit = parts[2] if len(parts) >= 3 else "default"

    if raw_character == "Unknown" and replacement:
        stem = Path(replacement).stem
        # Common legacy replacements look like `jean_default` or `mona_2_default`.
        if stem.endswith("_default"):
            stem = stem[:-8]
        raw_character = stem or raw_character

    match = re.match(r"^(.+)_([0-9]+)$", raw_character)
    if match:
        raw_character = match.group(1)
        outfit = match.group(2)

    return (
        sanitize_identifier(raw_character, "Unknown"),
        sanitize_identifier(outfit, "default"),
        sanitize_identifier(slot, "Unit"),
    )


def _rule_from_dict(data: dict) -> PortraitRule:
    character_id = str(data.get("character_id", ""))
    outfit_id = str(data.get("outfit_id", ""))
    slot = str(data.get("slot", ""))
    note = str(data.get("note", ""))
    replacement = str(data.get("replacement", ""))
    if not character_id or not outfit_id or not slot:
        inferred_character, inferred_outfit, inferred_slot = infer_rule_identity(note, replacement)
        character_id = character_id or inferred_character
        outfit_id = outfit_id or inferred_outfit
        slot = slot or inferred_slot

    rule = PortraitRule(
        enabled=bool(data.get("enabled", True)),
        hash=str(data.get("hash", "")),
        replacement=replacement,
        note=note,
        character_id=sanitize_identifier(character_id, "Unknown"),
        outfit_id=sanitize_identifier(outfit_id, "default"),
        slot=sanitize_identifier(slot, "Unit"),
    )
    if rule.hash:
        rule.hash = normalize_hash(rule.hash)
    return rule


def _outfit_from_dict(data: dict) -> CharacterOutfit:
    outfit = CharacterOutfit(
        id=sanitize_identifier(str(data.get("id", "default")), "default"),
        name=str(data.get("name", "") or data.get("id", "Default")),
        rules=[_rule_from_dict(item) for item in data.get("rules", [])],
    )
    for rule in outfit.rules:
        rule.outfit_id = outfit.id
    return outfit


def _character_from_dict(data: dict) -> CharacterProfile:
    character = CharacterProfile(
        id=sanitize_identifier(str(data.get("id", "")), "Unknown"),
        name=str(data.get("name", "") or data.get("id", "")),
        selected_outfit_id=sanitize_identifier(str(data.get("selected_outfit_id", "default")), "default"),
        outfits=[_outfit_from_dict(item) for item in data.get("outfits", [])],
    )
    for outfit in character.outfits:
        for rule in outfit.rules:
            rule.character_id = character.id
            rule.outfit_id = outfit.id
    if not character.outfits:
        character.outfits.append(CharacterOutfit(id="default", name="Default"))
    if character.selected_outfit_id not in {outfit.id for outfit in character.outfits}:
        character.selected_outfit_id = character.outfits[0].id
    return character


def _sort_outfits(outfits: List[CharacterOutfit]) -> List[CharacterOutfit]:
    def key(outfit: CharacterOutfit):
        if outfit.id == "default":
            return (0, "")
        if outfit.id.isdigit():
            return (1, int(outfit.id))
        return (2, outfit.id.lower())

    return sorted(outfits, key=key)


def get_character(db: HashDb, character_id: str) -> Optional[CharacterProfile]:
    safe_character = sanitize_identifier(character_id, "")
    for character in db.characters:
        if character.id == safe_character:
            return character
    return None


def get_outfit(character: CharacterProfile, outfit_id: str) -> Optional[CharacterOutfit]:
    safe_outfit = sanitize_identifier(outfit_id, "default")
    for outfit in character.outfits:
        if outfit.id == safe_outfit:
            return outfit
    return None


def ensure_character(db: HashDb, character_id: str, name: str = "") -> CharacterProfile:
    safe_character = sanitize_identifier(character_id, "Unknown")
    character = get_character(db, safe_character)
    if character is not None:
        if name and not character.name:
            character.name = name
        return character
    character = CharacterProfile(id=safe_character, name=name or safe_character, selected_outfit_id="default")
    db.characters.append(character)
    db.characters.sort(key=lambda item: item.name.lower())
    return character


def ensure_outfit(character: CharacterProfile, outfit_id: str, name: str = "") -> CharacterOutfit:
    safe_outfit = sanitize_identifier(outfit_id, "default")
    outfit = get_outfit(character, safe_outfit)
    if outfit is not None:
        if name and not outfit.name:
            outfit.name = name
        return outfit
    outfit = CharacterOutfit(id=safe_outfit, name=name or ("Default" if safe_outfit == "default" else safe_outfit))
    character.outfits.append(outfit)
    character.outfits = _sort_outfits(character.outfits)
    if not character.selected_outfit_id:
        character.selected_outfit_id = outfit.id
    return outfit


def ensure_character_library(db: HashDb) -> HashDb:
    if db.characters or not db.rules:
        return db
    legacy_rules = list(db.rules)
    db.characters = []
    for rule in legacy_rules:
        character_id = rule.character_id
        outfit_id = rule.outfit_id
        slot = rule.slot
        if not character_id or not outfit_id or not slot:
            character_id, outfit_id, slot = infer_rule_identity(rule.note, rule.replacement)
        character = ensure_character(db, character_id, character_id)
        outfit_name = "Default" if outfit_id == "default" else outfit_id
        outfit = ensure_outfit(character, outfit_id, outfit_name)
        rule.character_id = character.id
        rule.outfit_id = outfit.id
        rule.slot = slot
        outfit.rules.append(rule)
    for character in db.characters:
        if any(outfit.id == "default" for outfit in character.outfits):
            character.selected_outfit_id = "default"
        elif character.outfits:
            character.selected_outfit_id = character.outfits[0].id
    return db


def _runtime_rule(character: CharacterProfile, outfit: CharacterOutfit, rule: PortraitRule) -> PortraitRule:
    return PortraitRule(
        enabled=rule.enabled,
        hash=normalize_hash(rule.hash),
        replacement=rule.replacement,
        note=rule.note,
        character_id=character.id,
        outfit_id=outfit.id,
        slot=rule.slot,
    )


def rebuild_runtime_rules(db: HashDb) -> List[PortraitRule]:
    ensure_character_library(db)
    if not db.characters:
        db.rules = [_rule_from_dict(asdict(rule)) for rule in db.rules]
        return db.rules

    runtime_rules: List[PortraitRule] = []
    for character in db.characters:
        outfit = get_outfit(character, character.selected_outfit_id)
        if outfit is None and character.outfits:
            outfit = character.outfits[0]
            character.selected_outfit_id = outfit.id
        if outfit is None:
            continue
        for rule in outfit.rules:
            runtime_rules.append(_runtime_rule(character, outfit, rule))
    db.rules = runtime_rules
    return runtime_rules


def load_hash_db(path: Path) -> HashDb:
    if not path.is_file():
        return HashDb()
    data = json.loads(path.read_text(encoding="utf-8"))
    db = HashDb(
        enabled=bool(data.get("enabled", True)),
        dump_unknown=bool(data.get("dump_unknown", True)),
        min_width=int(data.get("min_width", 32)),
        min_height=int(data.get("min_height", 32)),
        rules=[_rule_from_dict(item) for item in data.get("rules", [])],
        characters=[_character_from_dict(item) for item in data.get("characters", [])],
    )
    ensure_character_library(db)
    rebuild_runtime_rules(db)
    return db


def save_hash_db(path: Path, db: HashDb) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_character_library(db)
    rebuild_runtime_rules(db)
    payload = asdict(db)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_profile(importer_path: Path) -> None:
    """Create the runtime profile layout used by the ReShade add-on.

    Layout:
      Core/GPMI/PortraitHashReplace.addon64  - compiled add-on, user-built or copied here
      Core/GPMI/ReShade64.dll                - optional ReShade DLL for direct injection
      Core/GPMI/ptr_config.ini               - runtime add-on config
      Mods/<character>/<outfit>/textures/*.ptrtex - selectable replacement textures
      Dumps/*.ptrtex                         - textures dumped by the add-on
      hash_db.json                           - enabled replacement rules plus character outfit library
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
    else:
        # Opportunistically migrate legacy flat profiles without changing runtime semantics.
        save_hash_db(db_path, load_hash_db(db_path))
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

    safe_mod_name = sanitize_identifier(mod_name.strip(), "Default")
    dst = importer_path / "Mods" / safe_mod_name / "textures" / (src_png.stem + ".ptrtex")
    width, height, fmt = png_to_ptrtex(src_png, dst, bgra=bgra)
    return dst, width, height, fmt


def import_outfit_replacement_texture(
    importer_path: Path,
    src_png: Path,
    character_id: str,
    outfit_id: str,
    slot: str = "Unit",
    bgra: bool = False,
) -> tuple[Path, int, int, str]:
    from core.gpmi.ptrtex import png_to_ptrtex

    character = sanitize_identifier(character_id, "Unknown")
    outfit = sanitize_identifier(outfit_id, "default")
    slot_name = sanitize_identifier(slot, "Unit")
    dst = importer_path / "Mods" / character / outfit / "textures" / f"{slot_name}_{src_png.stem}.ptrtex"
    width, height, fmt = png_to_ptrtex(src_png, dst, bgra=bgra)
    return dst, width, height, fmt


def relative_to_profile(importer_path: Path, path: Path) -> str:
    try:
        return path.relative_to(importer_path).as_posix()
    except ValueError:
        return path.as_posix()


def select_outfit(importer_path: Path, character_id: str, outfit_id: str) -> HashDb:
    db_path = importer_path / "hash_db.json"
    db = load_hash_db(db_path)
    character = get_character(db, character_id)
    if character is None:
        raise ValueError(f"unknown character: {character_id}")
    outfit = get_outfit(character, outfit_id)
    if outfit is None:
        raise ValueError(f"unknown outfit for {character.id}: {outfit_id}")
    character.selected_outfit_id = outfit.id
    save_hash_db(db_path, db)
    write_runtime_ini(importer_path, db)
    return db


def add_or_update_rule(
    importer_path: Path,
    hash_text: str,
    replacement: Path,
    note: str = "",
    enabled: bool = True,
    character_id: str = "",
    outfit_id: str = "default",
    slot: str = "Unit",
    select_after_update: bool = True,
) -> PortraitRule:
    db_path = importer_path / "hash_db.json"
    db = load_hash_db(db_path)
    norm_hash = normalize_hash(hash_text)
    rel = relative_to_profile(importer_path, replacement)
    inferred_character, inferred_outfit, inferred_slot = infer_rule_identity(note, rel)
    character_id = sanitize_identifier(character_id or inferred_character, "Unknown")
    outfit_id = sanitize_identifier(outfit_id or inferred_outfit, "default")
    slot = sanitize_identifier(slot or inferred_slot, "Unit")
    rule_note = note or f"{slot}/{character_id}/{outfit_id}"

    character = ensure_character(db, character_id, character_id)
    outfit = ensure_outfit(character, outfit_id, "Default" if outfit_id == "default" else outfit_id)
    if select_after_update:
        character.selected_outfit_id = outfit.id

    for rule in outfit.rules:
        if normalize_hash(rule.hash) == norm_hash and rule.slot == slot:
            rule.enabled = enabled
            rule.replacement = rel
            rule.note = rule_note
            rule.character_id = character.id
            rule.outfit_id = outfit.id
            rule.slot = slot
            save_hash_db(db_path, db)
            write_runtime_ini(importer_path, db)
            return rule

    rule = PortraitRule(
        enabled=enabled,
        hash=norm_hash,
        replacement=rel,
        note=rule_note,
        character_id=character.id,
        outfit_id=outfit.id,
        slot=slot,
    )
    outfit.rules.append(rule)
    save_hash_db(db_path, db)
    write_runtime_ini(importer_path, db)
    return rule


def remove_rules(
    importer_path: Path,
    hashes: Iterable[str],
    character_id: str = "",
    outfit_id: str = "",
) -> int:
    normalized = {normalize_hash(h) for h in hashes}
    db_path = importer_path / "hash_db.json"
    db = load_hash_db(db_path)
    before = 0
    after = 0

    if db.characters:
        for character in db.characters:
            if character_id and character.id != sanitize_identifier(character_id, ""):
                continue
            for outfit in character.outfits:
                if outfit_id and outfit.id != sanitize_identifier(outfit_id, "default"):
                    continue
                before += len(outfit.rules)
                outfit.rules = [r for r in outfit.rules if normalize_hash(r.hash) not in normalized]
                after += len(outfit.rules)
    else:
        before = len(db.rules)
        db.rules = [r for r in db.rules if normalize_hash(r.hash) not in normalized]
        after = len(db.rules)

    save_hash_db(db_path, db)
    write_runtime_ini(importer_path, db)
    return before - after


def copy_kernel_sources(src_root: Path, importer_path: Path) -> None:
    dst = importer_path / "Tools" / "kernel_source"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src_root, dst)
