from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REQUIRED_SLOTS = ("Unit", "Unit_H")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
GAME_PORTRAIT_NAME_PATTERN = re.compile(r"^(.+)_h_(.+)$")
GPMI_VERSION = "0.5.0"
USER_MODS_DIR = "Mods"
RUNTIME_MODS_DIR = USER_MODS_DIR
META_FILE = "mod_meta.json"
RUNTIME_MANIFEST_FILE = "live_portraits.json"


def sanitize_identifier(value: str, fallback: str = "default") -> str:
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip()
    safe = "_".join(safe.split())
    return safe or fallback


def character_id(value: str) -> str:
    return sanitize_identifier(value, "unknown").lower()


def source_outfit_name(value: str) -> str:
    return sanitize_identifier(value, "outfit").lower()


def parse_game_portrait_name(file_name: str) -> Tuple[str, str] | None:
    stem = Path(file_name).stem
    match = GAME_PORTRAIT_NAME_PATTERN.match(stem)
    if match is None:
        return None
    raw_character, raw_outfit = match.groups()
    if not raw_character.strip() or not raw_outfit.strip():
        return None
    return character_id(raw_character), source_outfit_name(raw_outfit)


def game_profile_dir(game_exe_path: Path) -> Path:
    return Path(game_exe_path).resolve().parent / "GPMI"


def _portable_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def ensure_game_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / USER_MODS_DIR).mkdir(parents=True, exist_ok=True)
    if not (profile_dir / META_FILE).exists():
        save_mod_meta(profile_dir, default_meta())
    if not (profile_dir / RUNTIME_MANIFEST_FILE).exists():
        save_runtime_manifest(profile_dir, default_runtime_manifest(profile_dir))


def _game_portrait_files(slot_dir: Path) -> Dict[str, Path]:
    if not slot_dir.is_dir():
        return {}
    return {
        path.name: path
        for path in slot_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    }

def _replace_slot_image(slot_dir: Path, src: Path) -> None:
    slot_dir.mkdir(parents=True, exist_ok=True)
    for existing in slot_dir.iterdir():
        if existing.is_file() and existing.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES and existing.name != src.name:
            existing.unlink()
    shutil.copyfile(src, slot_dir / src.name)


def import_game_portrait_mods(game_exe_path: Path, profile_dir: Path) -> dict:
    """Copy game-deployed Unit/Unit_H portrait pairs into the user Mods layout."""
    ensure_game_profile(profile_dir)
    game_dir = Path(game_exe_path).resolve().parent
    source_dirs = {
        "Unit": game_dir / "MOD" / "Unit",
        "Unit_H": game_dir / "MOD" / "Unit_H",
    }

    missing_dirs = [str(path) for path in source_dirs.values() if not path.is_dir()]
    if missing_dirs:
        return {
            "copied": [],
            "skipped_invalid": [],
            "skipped_unpaired": [],
            "missing_dirs": missing_dirs,
            "target_mods_dir": str(profile_dir / USER_MODS_DIR),
        }

    slot_files = {slot: _game_portrait_files(path) for slot, path in source_dirs.items()}
    paired_names = sorted(set(slot_files["Unit"]) & set(slot_files["Unit_H"]), key=str.lower)
    unpaired_names = sorted(set(slot_files["Unit"]) ^ set(slot_files["Unit_H"]), key=str.lower)

    copied: List[dict] = []
    skipped_invalid: List[str] = []
    target_mods_dir = profile_dir / USER_MODS_DIR

    for file_name in paired_names:
        parsed = parse_game_portrait_name(file_name)
        if parsed is None:
            skipped_invalid.append(file_name)
            continue
        cid, outfit_name = parsed
        outfit_dir = target_mods_dir / cid / outfit_name
        _replace_slot_image(outfit_dir / "Unit", slot_files["Unit"][file_name])
        _replace_slot_image(outfit_dir / "Unit_H", slot_files["Unit_H"][file_name])
        copied.append({
            "character_id": cid,
            "outfit_name": outfit_name,
            "file_name": file_name,
            "target_path": str(outfit_dir),
        })

    return {
        "copied": copied,
        "skipped_invalid": skipped_invalid,
        "skipped_unpaired": unpaired_names,
        "missing_dirs": [],
        "target_mods_dir": str(target_mods_dir),
    }


def default_meta() -> dict:
    return {
        "version": 3,
        "mode": "godot_live_bridge",
        "selected_outfits": {},
        "selected_sources": {},
        "characters": {},
    }


def default_runtime_manifest(profile_dir: Path) -> dict:
    return {
        "version": 1,
        "mode": "godot_live_bridge",
        "enabled": True,
        "revision": 0,
        "updated_at": int(time.time()),
        "profile_dir": _portable_path(profile_dir),
        "source_mods_dir": _portable_path(profile_dir / USER_MODS_DIR),
        "runtime_images_dir": _portable_path(profile_dir / USER_MODS_DIR),
        "selected_outfits": {},
        "selected_sources": {},
        "rules": [],
        "gpmi_generated": {
            "active_characters": 0,
            "active_slots": 0,
            "issues": {},
        },
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
    data.setdefault("version", 3)
    data.setdefault("mode", "godot_live_bridge")
    data.setdefault("selected_outfits", {})
    data.setdefault("selected_sources", {})
    data.setdefault("characters", {})
    return data


def save_mod_meta(profile_dir: Path, meta: dict) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runtime_manifest(profile_dir: Path) -> dict:
    path = profile_dir / RUNTIME_MANIFEST_FILE
    if not path.is_file():
        return default_runtime_manifest(profile_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_runtime_manifest(profile_dir)
    if not isinstance(data, dict):
        return default_runtime_manifest(profile_dir)
    return data


def save_runtime_manifest(profile_dir: Path, data: dict) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / RUNTIME_MANIFEST_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _allocate_outfit_id(character_meta: dict, source_path: str) -> str:
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
    outfit_id = _allocate_outfit_id(char_meta, source_path)
    files: Dict[str, str] = {}
    absolute_files: Dict[str, str] = {}
    source_files: Dict[str, str] = {}
    for slot, src in slot_sources.items():
        files[slot] = _relative(profile_dir, src)
        absolute_files[slot] = _portable_path(src)
        source_files[slot] = _relative(profile_dir, src)

    outfit_meta = {
        "id": outfit_id,
        "character_id": cid,
        "source_name": source_name,
        "source_path": source_path,
        "runtime_path": source_path,
        "files": files,
        "absolute_files": absolute_files,
        "source_files": source_files,
        "imported_at": int(time.time()),
    }
    char_meta.setdefault("outfits", {})[outfit_id] = outfit_meta
    selected_outfits = meta.setdefault("selected_outfits", {})
    selected_sources = meta.setdefault("selected_sources", {})
    if selected_outfits.get(cid) == outfit_id:
        selected_sources[cid] = source_path
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


def _outfit_slot_rel(outfit: dict, slot: str) -> str:
    source_rel = outfit.get("source_files", {}).get(slot, "")
    if source_rel:
        return source_rel
    return outfit.get("files", {}).get(slot, "")


def _outfit_file_exists(profile_dir: Path, outfit: dict, slot: str) -> bool:
    rel = _outfit_slot_rel(outfit, slot)
    if not rel:
        return False
    path = profile_dir / rel
    return path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES


def validate_imported_outfit(profile_dir: Path, outfit: dict) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for slot in REQUIRED_SLOTS:
        if not _outfit_file_exists(profile_dir, outfit, slot):
            issues.append(f"missing runtime {slot} image")
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
    source_path = outfit.get("source_path")
    if source_path:
        meta.setdefault("selected_sources", {})[cid] = source_path
    save_mod_meta(profile_dir, meta)
    return outfit


def clear_selected_outfit(profile_dir: Path, cid: str) -> None:
    meta = load_mod_meta(profile_dir)
    cid = character_id(cid)
    meta.setdefault("selected_outfits", {})[cid] = None
    meta.setdefault("selected_sources", {}).pop(cid, None)
    save_mod_meta(profile_dir, meta)


def _selected_outfit(meta: dict, cid: str) -> Optional[dict]:
    outfit_id = meta.get("selected_outfits", {}).get(cid)
    if not outfit_id:
        return None
    return meta.get("characters", {}).get(cid, {}).get("outfits", {}).get(outfit_id)


def _next_revision(profile_dir: Path) -> int:
    current = load_runtime_manifest(profile_dir)
    try:
        return int(current.get("revision", 0)) + 1
    except Exception:
        return int(time.time())


def _portrait_type_for_character(cid: str) -> str:
    cid = character_id(cid)
    return cid if cid.endswith("_h") else f"{cid}_h"


def _rule_for_slot(profile_dir: Path, outfit: dict, slot: str) -> dict:
    cid = character_id(str(outfit.get("character_id", "")))
    rel = _outfit_slot_rel(outfit, slot)
    abs_path = _portable_path(profile_dir / rel)
    portrait_type = _portrait_type_for_character(cid)
    logical_path = f"{slot}/{portrait_type}_default"
    return {
        "enabled": True,
        "character_id": cid,
        "portrait_type": portrait_type,
        "outfit_id": str(outfit.get("id", "")),
        "source_name": str(outfit.get("source_name", "")),
        "slot": slot,
        "action": "default",
        "cache_key": logical_path,
        "logical_path": logical_path,
        "replacement": abs_path,
        "replacement_rel": rel,
    }


def build_live_portrait_manifest(profile_dir: Path) -> dict:
    """Write the live portrait manifest consumed by the Godot live bridge.

    The target game calls ImageLoader.unit(type, action, high_resolution).
    For the current target, both Unit and Unit_H slots use the h-suffixed
    portrait type in the logical key, e.g. Unit/jean_h_default and
    Unit_H/jean_h_default.
    """
    ensure_game_profile(profile_dir)
    meta = load_mod_meta(profile_dir)
    rules: List[dict] = []
    issues: Dict[str, List[str]] = {}

    for cid, outfit_id in sorted(meta.get("selected_outfits", {}).items()):
        if not outfit_id:
            continue
        outfit = _selected_outfit(meta, cid)
        if outfit is None:
            issues.setdefault(cid, []).append("selected outfit metadata missing")
            continue
        ready, outfit_issues = validate_imported_outfit(profile_dir, outfit)
        if not ready:
            issues.setdefault(cid, []).extend(outfit_issues)
            continue
        for slot in REQUIRED_SLOTS:
            rules.append(_rule_for_slot(profile_dir, outfit, slot))

    generated = {
        "updated_at": int(time.time()),
        "runtime_manifest": RUNTIME_MANIFEST_FILE,
        "selected_outfits": meta.get("selected_outfits", {}),
        "selected_sources": meta.get("selected_sources", {}),
        "active_characters": len({rule["character_id"] for rule in rules}),
        "active_slots": len(rules),
        "enabled_rules": len(rules),
        "issues": {key: sorted(set(value)) for key, value in issues.items()},
    }

    data = {
        "version": 1,
        "mode": "godot_live_bridge",
        "enabled": True,
        "revision": _next_revision(profile_dir),
        "updated_at": generated["updated_at"],
        "profile_dir": _portable_path(profile_dir),
        "source_mods_dir": _portable_path(profile_dir / USER_MODS_DIR),
        "runtime_images_dir": _portable_path(profile_dir / USER_MODS_DIR),
        "selected_outfits": meta.get("selected_outfits", {}),
        "selected_sources": meta.get("selected_sources", {}),
        "rules": rules,
        "gpmi_generated": generated,
    }
    save_runtime_manifest(profile_dir, data)
    return generated


def _summarize_manifest(path: Path) -> dict:
    if not path.is_file():
        return {"exists": False, "rules": 0, "enabled": 0, "characters": 0, "active_slots": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": True, "rules": 0, "enabled": 0, "characters": 0, "active_slots": 0, "error": "invalid json"}
    rules = data.get("rules", []) if isinstance(data, dict) else []
    characters = set()
    enabled = 0
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        cid = character_id(str(rule.get("character_id", ""))) if rule.get("character_id") else ""
        if cid:
            characters.add(cid)
        if rule.get("enabled"):
            enabled += 1
    generated = data.get("gpmi_generated", {}) if isinstance(data, dict) else {}
    return {
        "exists": True,
        "rules": len(rules) if isinstance(rules, list) else 0,
        "enabled": enabled,
        "characters": len(characters),
        "active_slots": generated.get("active_slots", enabled),
        "active_characters": generated.get("active_characters", len(characters)),
        "revision": data.get("revision", 0) if isinstance(data, dict) else 0,
        "generated": generated,
    }


def summarize_live_portrait_manifest(profile_dir: Path) -> dict:
    runtime = _summarize_manifest(profile_dir / RUNTIME_MANIFEST_FILE)
    source = {
        "exists": False,
        "rules": 0,
        "enabled": 0,
        "characters": 0,
    }
    return {
        "source": source,
        "runtime": runtime,
        "exists": runtime.get("exists", False),
        "rules": runtime.get("rules", 0),
        "enabled": runtime.get("enabled", 0),
        "characters": runtime.get("characters", 0),
        "active_slots": runtime.get("active_slots", 0),
        "active_characters": runtime.get("active_characters", 0),
        "revision": runtime.get("revision", 0),
        "generated": runtime.get("generated", {}),
    }
