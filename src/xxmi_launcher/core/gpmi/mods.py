from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.gpmi.profile import infer_rule_identity, normalize_hash, sanitize_identifier
from core.gpmi.ptrtex import png_to_ptrtex

REQUIRED_SLOTS = ("Unit", "Unit_H")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
USER_MODS_DIR = "Mods"
RUNTIME_MODS_DIR = "RuntimeMods"
META_FILE = "mod_meta.json"
HASH_DB_FILE = "hash_db.json"


def character_id(value: str) -> str:
    return sanitize_identifier(value, "unknown").lower()


def source_outfit_name(value: str) -> str:
    return sanitize_identifier(value, "outfit").lower()


def game_profile_dir(game_exe_path: Path) -> Path:
    return Path(game_exe_path).resolve().parent / "GPMI"


def ensure_game_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    for rel in [USER_MODS_DIR, RUNTIME_MODS_DIR, "Dumps"]:
        (profile_dir / rel).mkdir(parents=True, exist_ok=True)
    if not (profile_dir / META_FILE).exists():
        save_mod_meta(profile_dir, default_meta())
    write_runtime_ini(profile_dir)


def write_runtime_ini(
    profile_dir: Path,
    dump_unknown: bool = True,
    min_width: int = 32,
    min_height: int = 32,
    mirror_dirs: Optional[List[Path]] = None,
) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_abs = profile_dir.resolve()
    common = (
        "[core]\n"
        f"enabled=true\n"
        f"dump_unknown={'true' if dump_unknown else 'false'}\n"
        f"min_width={int(min_width)}\n"
        f"min_height={int(min_height)}\n"
        f"profile_dir={profile_abs}\n"
        f"hash_db={HASH_DB_FILE}\n"
        f"log_file=PortraitHashReplace.log\n"
    )
    (profile_dir / "ptr_config.ini").write_text(common, encoding="utf-8")
    for mirror_dir in mirror_dirs or []:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        (mirror_dir / "ptr_config.ini").write_text(common, encoding="utf-8")


def default_meta() -> dict:
    return {
        "version": 1,
        "selected_outfits": {},
        "characters": {},
    }


def load_mod_meta(profile_dir: Path) -> dict:
    path = profile_dir / META_FILE
    if not path.is_file():
        return default_meta()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_meta()
    if not isinstance(data, dict):
        return default_meta()
    data.setdefault("version", 1)
    data.setdefault("selected_outfits", {})
    data.setdefault("characters", {})
    return data


def save_mod_meta(profile_dir: Path, meta: dict) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _relative(profile_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(profile_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _slot_images(mod_dir: Path, slot: str) -> Tuple[List[Path], List[str]]:
    slot_dir = mod_dir / slot
    if not slot_dir.is_dir():
        return [], [f"missing {slot} folder"]
    images = sorted(
        path for path in slot_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )
    if len(images) == 0:
        return images, [f"missing image in {slot}"]
    if len(images) > 1:
        return images, [f"multiple images in {slot}; keep exactly one"]
    return images, []


def scan_user_mods(profile_dir: Path) -> List[dict]:
    mods_root = profile_dir / USER_MODS_DIR
    mods_root.mkdir(parents=True, exist_ok=True)
    results: List[dict] = []
    for char_dir in sorted(path for path in mods_root.iterdir() if path.is_dir()):
        cid = character_id(char_dir.name)
        for outfit_dir in sorted(path for path in char_dir.iterdir() if path.is_dir()):
            source_name = source_outfit_name(outfit_dir.name)
            issues: List[str] = []
            if char_dir.name != char_dir.name.lower():
                issues.append("character folder must be lowercase")
            slot_files: Dict[str, str] = {}
            for slot in REQUIRED_SLOTS:
                images, slot_issues = _slot_images(outfit_dir, slot)
                issues.extend(slot_issues)
                if len(images) == 1:
                    slot_files[slot] = _relative(profile_dir, images[0])
            results.append({
                "character_id": cid,
                "source_outfit_name": source_name,
                "source_path": _relative(profile_dir, outfit_dir),
                "slot_files": slot_files,
                "ready": not issues,
                "issues": issues,
            })
    return results


def _ensure_meta_character(meta: dict, cid: str) -> dict:
    characters = meta.setdefault("characters", {})
    if cid not in characters:
        characters[cid] = {
            "id": cid,
            "outfits": {},
        }
    characters[cid].setdefault("outfits", {})
    return characters[cid]


def _allocate_outfit_id(character_meta: dict, source_name: str, source_path: str) -> str:
    outfits = character_meta.setdefault("outfits", {})
    for oid, outfit in outfits.items():
        if outfit.get("source_path") == source_path:
            return oid
    used = set(outfits.keys())
    counter = 1
    while True:
        candidate = f"outfit_{counter:03d}"
        if candidate not in used:
            return candidate
        counter += 1


def import_user_mod(profile_dir: Path, cid: str, source_name: str) -> dict:
    ensure_game_profile(profile_dir)
    cid = character_id(cid)
    source_name = source_outfit_name(source_name)
    mod_dir = profile_dir / USER_MODS_DIR / cid / source_name
    if not mod_dir.is_dir():
        raise FileNotFoundError(f"mod folder not found: {mod_dir}")

    slot_sources: Dict[str, Path] = {}
    issues: List[str] = []
    for slot in REQUIRED_SLOTS:
        images, slot_issues = _slot_images(mod_dir, slot)
        issues.extend(slot_issues)
        if len(images) == 1:
            slot_sources[slot] = images[0]
    if issues:
        raise ValueError("; ".join(issues))

    meta = load_mod_meta(profile_dir)
    char_meta = _ensure_meta_character(meta, cid)
    source_path = _relative(profile_dir, mod_dir)
    outfit_id = _allocate_outfit_id(char_meta, source_name, source_path)
    runtime_dir = profile_dir / RUNTIME_MODS_DIR / cid / outfit_id
    runtime_dir.mkdir(parents=True, exist_ok=True)

    files: Dict[str, str] = {}
    source_files: Dict[str, str] = {}
    image_info: Dict[str, dict] = {}
    for slot, src in slot_sources.items():
        dst = runtime_dir / f"{cid}_{outfit_id}_{slot}.ptrtex"
        width, height, fmt = png_to_ptrtex(src, dst)
        files[slot] = _relative(profile_dir, dst)
        source_files[slot] = _relative(profile_dir, src)
        image_info[slot] = {"width": width, "height": height, "format": fmt}

    outfit_meta = {
        "id": outfit_id,
        "character_id": cid,
        "source_name": source_name,
        "source_path": source_path,
        "runtime_path": _relative(profile_dir, runtime_dir),
        "files": files,
        "source_files": source_files,
        "image_info": image_info,
        "imported_at": int(time.time()),
    }
    char_meta.setdefault("outfits", {})[outfit_id] = outfit_meta
    meta.setdefault("selected_outfits", {}).setdefault(cid, outfit_id)
    save_mod_meta(profile_dir, meta)
    return outfit_meta


def import_all_ready_mods(profile_dir: Path) -> Tuple[List[dict], List[str]]:
    imported: List[dict] = []
    failures: List[str] = []
    for item in scan_user_mods(profile_dir):
        label = f"{item['character_id']}/{item['source_outfit_name']}"
        if not item["ready"]:
            failures.append(f"{label}: " + "; ".join(item["issues"]))
            continue
        try:
            imported.append(import_user_mod(profile_dir, item["character_id"], item["source_outfit_name"]))
        except Exception as exc:
            failures.append(f"{label}: {exc}")
    return imported, failures


def _outfit_file_exists(profile_dir: Path, outfit: dict, slot: str) -> bool:
    rel = outfit.get("files", {}).get(slot, "")
    return bool(rel) and (profile_dir / rel).is_file()


def validate_imported_outfit(profile_dir: Path, outfit: dict) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for slot in REQUIRED_SLOTS:
        if not _outfit_file_exists(profile_dir, outfit, slot):
            issues.append(f"missing runtime {slot} ptrtex")
    return not issues, issues


def select_imported_outfit(profile_dir: Path, cid: str, outfit_id: str) -> dict:
    meta = load_mod_meta(profile_dir)
    cid = character_id(cid)
    outfit_id = sanitize_identifier(outfit_id, "")
    outfit = meta.get("characters", {}).get(cid, {}).get("outfits", {}).get(outfit_id)
    if outfit is None:
        raise ValueError(f"unknown imported outfit: {cid}/{outfit_id}")
    ready, issues = validate_imported_outfit(profile_dir, outfit)
    if not ready:
        raise ValueError("outfit is incomplete: " + "; ".join(issues))
    meta.setdefault("selected_outfits", {})[cid] = outfit_id
    save_mod_meta(profile_dir, meta)
    return outfit


def clear_selected_outfit(profile_dir: Path, cid: str) -> None:
    meta = load_mod_meta(profile_dir)
    meta.setdefault("selected_outfits", {}).pop(character_id(cid), None)
    save_mod_meta(profile_dir, meta)


def _hash_rule_identity(rule: dict) -> Tuple[str, str]:
    cid = character_id(str(rule.get("character_id", ""))) if rule.get("character_id") else ""
    slot = str(rule.get("slot", ""))
    if not cid or not slot:
        inferred_cid, _outfit, inferred_slot = infer_rule_identity(str(rule.get("note", "")), str(rule.get("replacement", "")))
        cid = cid or character_id(inferred_cid)
        slot = slot or inferred_slot
    return cid, slot


def _selected_outfit(meta: dict, cid: str) -> Optional[dict]:
    outfit_id = meta.get("selected_outfits", {}).get(cid)
    if not outfit_id:
        return None
    return meta.get("characters", {}).get(cid, {}).get("outfits", {}).get(outfit_id)


def build_runtime_hash_db(profile_dir: Path) -> dict:
    """Merge externally generated hashes with the selected imported outfit metadata.

    The external tool owns the hash list in GPMI/hash_db.json. GPMI only rewrites
    each top-level rule's enabled/replacement/character_id/outfit_id/slot fields.
    A character is enabled only when both Unit and Unit_H hashes exist and the
    selected imported outfit has both runtime ptrtex files.
    """
    hash_db_path = profile_dir / HASH_DB_FILE
    if not hash_db_path.is_file():
        raise FileNotFoundError(f"hash_db.json not found: {hash_db_path}")
    data = json.loads(hash_db_path.read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("hash_db.json field 'rules' must be a list")

    meta = load_mod_meta(profile_dir)
    hash_slots: Dict[str, set] = {}
    identities: List[Tuple[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            identities.append(("", ""))
            continue
        cid, slot = _hash_rule_identity(rule)
        identities.append((cid, slot))
        if cid and slot in REQUIRED_SLOTS and rule.get("hash"):
            hash_slots.setdefault(cid, set()).add(slot)

    enabled_count = 0
    disabled_count = 0
    issues: Dict[str, List[str]] = {}

    for rule, (cid, slot) in zip(rules, identities):
        if not isinstance(rule, dict):
            continue
        if "original_replacement" not in rule:
            rule["original_replacement"] = str(rule.get("replacement", ""))
        if rule.get("hash"):
            rule["hash"] = normalize_hash(str(rule.get("hash")))

        outfit = _selected_outfit(meta, cid) if cid else None
        outfit_id = str(outfit.get("id", "")) if outfit else ""
        can_enable = False
        replacement = ""
        if cid and slot in REQUIRED_SLOTS and outfit is not None:
            character_hash_slots = hash_slots.get(cid, set())
            if not all(required in character_hash_slots for required in REQUIRED_SLOTS):
                issues.setdefault(cid, []).append("hash_db is missing Unit or Unit_H hash")
            else:
                ready, outfit_issues = validate_imported_outfit(profile_dir, outfit)
                if not ready:
                    issues.setdefault(cid, []).extend(outfit_issues)
                else:
                    replacement = outfit.get("files", {}).get(slot, "")
                    can_enable = bool(replacement)

        rule["enabled"] = bool(can_enable)
        if can_enable:
            rule["replacement"] = replacement
            rule["character_id"] = cid
            rule["outfit_id"] = outfit_id
            rule["slot"] = slot
            rule["note"] = f"{slot}/{cid}/{outfit_id}"
            enabled_count += 1
        else:
            disabled_count += 1

    data["gpmi_generated"] = {
        "updated_at": int(time.time()),
        "selected_outfits": meta.get("selected_outfits", {}),
        "enabled_rules": enabled_count,
        "disabled_rules": disabled_count,
        "issues": {key: sorted(set(value)) for key, value in issues.items()},
    }
    hash_db_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data["gpmi_generated"]


def summarize_hash_db(profile_dir: Path) -> dict:
    path = profile_dir / HASH_DB_FILE
    if not path.is_file():
        return {"exists": False, "rules": 0, "enabled": 0, "characters": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": True, "rules": 0, "enabled": 0, "characters": 0, "error": "invalid json"}
    rules = data.get("rules", []) if isinstance(data, dict) else []
    characters = set()
    enabled = 0
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        cid, _slot = _hash_rule_identity(rule)
        if cid:
            characters.add(cid)
        if rule.get("enabled"):
            enabled += 1
    return {
        "exists": True,
        "rules": len(rules) if isinstance(rules, list) else 0,
        "enabled": enabled,
        "characters": len(characters),
        "generated": data.get("gpmi_generated", {}) if isinstance(data, dict) else {},
    }
