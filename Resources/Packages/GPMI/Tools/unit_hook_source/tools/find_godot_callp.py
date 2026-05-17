#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Locate Godot 4.x Object::callp in a Windows x64 Godot game executable.

The script is dependency-free. It parses PE headers, .pdata RUNTIME_FUNCTION
boundaries, and RIP-relative references into .rdata strings. It is intended for
stripped Godot release/debug-template executables where C++ symbols are not
exported.

Typical use:
    python find_godot_callp.py "C:\\path\\game.exe"

The best candidate is printed with an INI snippet:
    object_callp_rva=0x...
    object_callp_patch_size=...
"""
from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Section:
    name: str
    va: int
    virtual_size: int
    raw: int
    raw_size: int
    characteristics: int


@dataclass(frozen=True)
class RuntimeFunction:
    begin: int
    end: int
    unwind: int


@dataclass
class Candidate:
    begin: int
    end: int
    score: int
    reasons: List[str]
    refs: List[Tuple[str, int]]
    first_bytes: bytes
    patch_size: int


class PE:
    def __init__(self, path: str) -> None:
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()

        if len(self.data) < 0x100 or self.data[:2] != b"MZ":
            raise ValueError("not a PE/MZ executable")

        self.e_lfanew = self.u32(0x3C)
        if self.data[self.e_lfanew:self.e_lfanew + 4] != b"PE\0\0":
            raise ValueError("missing PE signature")

        coff = self.e_lfanew + 4
        self.machine = self.u16(coff)
        self.section_count = self.u16(coff + 2)
        self.optional_header_size = self.u16(coff + 16)
        self.optional = coff + 20
        self.magic = self.u16(self.optional)
        if self.magic != 0x20B:
            raise ValueError("expected PE32+ x64 executable")

        self.image_base = self.u64(self.optional + 24)
        self.size_of_image = self.u32(self.optional + 56)
        self.data_directory_count = self.u32(self.optional + 108)
        dd = self.optional + 112
        self.directories: List[Tuple[int, int]] = [
            (self.u32(dd + i * 8), self.u32(dd + i * 8 + 4))
            for i in range(min(self.data_directory_count, 16))
        ]

        sec_off = self.optional + self.optional_header_size
        self.sections: List[Section] = []
        for i in range(self.section_count):
            off = sec_off + i * 40
            name = self.data[off:off + 8].split(b"\0", 1)[0].decode("ascii", "replace")
            virtual_size = self.u32(off + 8)
            va = self.u32(off + 12)
            raw_size = self.u32(off + 16)
            raw = self.u32(off + 20)
            characteristics = self.u32(off + 36)
            self.sections.append(Section(name, va, virtual_size, raw, raw_size, characteristics))

    def u16(self, off: int) -> int:
        return struct.unpack_from("<H", self.data, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.data, off)[0]

    def u64(self, off: int) -> int:
        return struct.unpack_from("<Q", self.data, off)[0]

    def section(self, name: str) -> Optional[Section]:
        return next((s for s in self.sections if s.name == name), None)

    def rva_to_offset(self, rva: int) -> Optional[int]:
        # Use virtual_size for RVA membership. Some Godot exports include a
        # large embedded PCK raw payload with tiny virtual_size; using raw_size
        # here would incorrectly swallow later sections such as .pdata.
        for s in self.sections:
            span = s.virtual_size if s.virtual_size else s.raw_size
            if s.va <= rva < s.va + span:
                delta = rva - s.va
                if delta < s.raw_size:
                    return s.raw + delta
                return None
        return None

    def offset_to_rva(self, off: int) -> Optional[int]:
        matches = []
        for s in self.sections:
            if s.raw and s.raw <= off < s.raw + s.raw_size:
                matches.append((s.raw, s.va + (off - s.raw), s))
        if not matches:
            return None
        # In overlapping raw layouts, prefer the section whose raw start is
        # closest to the offset.
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]

    def bytes_at_rva(self, rva: int, size: int) -> bytes:
        off = self.rva_to_offset(rva)
        if off is None:
            return b""
        return self.data[off:off + size]

    def parse_pdata(self) -> List[RuntimeFunction]:
        if len(self.directories) <= 3:
            return []
        pdata_rva, pdata_size = self.directories[3]
        pdata_off = self.rva_to_offset(pdata_rva)
        text = self.section(".text")
        if pdata_off is None or not text:
            return []
        out: List[RuntimeFunction] = []
        end_off = min(pdata_off + pdata_size, len(self.data))
        for off in range(pdata_off, end_off - 11, 12):
            begin, end, unwind = struct.unpack_from("<III", self.data, off)
            if text.va <= begin < end <= text.va + text.virtual_size:
                out.append(RuntimeFunction(begin, end, unwind))
        out.sort(key=lambda f: f.begin)
        return out

    def find_ascii_rva(self, needle: bytes) -> Optional[int]:
        off = self.data.find(needle)
        if off < 0:
            return None
        return self.offset_to_rva(off)


def function_for_rva(functions: Sequence[RuntimeFunction], rva: int) -> Optional[RuntimeFunction]:
    begins = [f.begin for f in functions]
    idx = bisect.bisect_right(begins, rva) - 1
    if idx >= 0:
        f = functions[idx]
        if f.begin <= rva < f.end:
            return f
    return None


def find_rel32_refs(pe: PE, target_rva: int) -> List[int]:
    text = pe.section(".text")
    if not text:
        return []
    raw = pe.data[text.raw:text.raw + text.raw_size]
    refs: List[int] = []

    # Fast path with numpy if available; fallback is dependency-free.
    try:
        import numpy as np  # type: ignore

        base = text.va
        for phase in range(4):
            usable = len(raw) - ((len(raw) - phase) % 4)
            if usable <= phase:
                continue
            view = np.frombuffer(raw[phase:usable], dtype="<u4")
            positions = phase + (np.arange(len(view), dtype=np.uint64) * 4)
            want = (target_rva - (base + positions + 4)) & 0xFFFFFFFF
            hits = np.nonzero(view == want.astype(np.uint32))[0]
            refs.extend(int(base + phase + int(h) * 4) for h in hits)
        refs.sort()
        return refs
    except Exception:
        pass

    for i in range(0, len(raw) - 3):
        disp = struct.unpack_from("<i", raw, i)[0]
        if text.va + i + 4 + disp == target_rva:
            refs.append(text.va + i)
    return refs


def estimate_patch_size(code: bytes, minimum: int = 12) -> int:
    """Minimal decoder for common MSVC x64 prologue instructions."""
    i = 0
    n = len(code)
    while i < minimum and i < n:
        b = code[i]

        # push r64
        if 0x50 <= b <= 0x57:
            i += 1
            continue

        # push r8-r15
        if b == 0x41 and i + 1 < n and 0x50 <= code[i + 1] <= 0x57:
            i += 2
            continue

        # sub rsp, imm32
        if i + 6 < n and code[i:i + 3] == b"\x48\x81\xec":
            i += 7
            continue

        # sub rsp, imm8
        if i + 3 < n and code[i:i + 3] == b"\x48\x83\xec":
            i += 4
            continue

        return minimum

    return i


def build_candidates(pe: PE) -> List[Candidate]:
    functions = pe.parse_pdata()
    ref_map: Dict[Tuple[int, int], Candidate] = {}

    # Strings from Object::callp's DEBUG_ENABLED branch in Godot 4.3.
    # These make this locator reliable for debug-enabled exports.
    needles: List[Tuple[str, bytes, int]] = [
        ("object_callp: cannot free RefCounted", b"Can't free a RefCounted object.", 70),
        ("object_callp: object locked", b"Object is locked and can't be freed.", 70),
        ("object_callp: object.cpp path", b"core/object/object.cpp", 20),
        ("context: callp literal", b"callp", 5),
        ("context: Invalid call literal", b"Invalid call", 3),
        ("context: Method not found literal", b"Method not found", 3),
    ]

    for label, needle, score in needles:
        target = pe.find_ascii_rva(needle)
        if target is None:
            continue
        refs = find_rel32_refs(pe, target)
        for ref in refs:
            f = function_for_rva(functions, ref)
            if not f:
                continue
            key = (f.begin, f.end)
            if key not in ref_map:
                first = pe.bytes_at_rva(f.begin, 32)
                ref_map[key] = Candidate(
                    begin=f.begin,
                    end=f.end,
                    score=0,
                    reasons=[],
                    refs=[],
                    first_bytes=first,
                    patch_size=estimate_patch_size(first, 12),
                )
            cand = ref_map[key]
            cand.score += score
            cand.reasons.append(label)
            cand.refs.append((label, ref))

    candidates = list(ref_map.values())

    for cand in candidates:
        body = pe.bytes_at_rva(cand.begin, min(0x420, cand.end - cand.begin))
        if b"\x48\x89\xcb" in body:  # mov rbx, rcx
            cand.score += 10
            cand.reasons.append("shape: saves hidden return pointer from rcx")
        if b"\x48\x89\xd6" in body:  # mov rsi, rdx
            cand.score += 10
            cand.reasons.append("shape: saves this pointer from rdx")
        if b"\x4c\x89\xc7" in body or b"\x49\x89\xf8" in body:
            cand.score += 5
            cand.reasons.append("shape: uses r8/r9 as method/args")
        if len(cand.first_bytes) >= 19 and cand.first_bytes[:19] == bytes.fromhex(
            "41 57 41 56 41 55 41 54 55 57 56 53 48 81 ec 88 00 00 00"
        ):
            cand.score += 30
            cand.reasons.append("shape: known Godot 4.3 MSVC Object::callp prologue")

    candidates.sort(key=lambda c: (c.score, -(c.end - c.begin)), reverse=True)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", help="Godot game executable")
    parser.add_argument("--top", type=int, default=8, help="number of candidates to print")
    args = parser.parse_args()

    pe = PE(args.exe)
    candidates = build_candidates(pe)
    sha256 = hashlib.sha256(pe.data).hexdigest()

    print(f"file={os.path.abspath(args.exe)}")
    print(f"sha256={sha256}")
    print(f"image_base=0x{pe.image_base:x}")
    print("sections=" + ", ".join(f"{s.name}:rva=0x{s.va:x},size=0x{s.virtual_size:x}" for s in pe.sections))
    print()

    if not candidates:
        print("No Object::callp candidate found.")
        print("Try adding more Godot-version-specific strings or inspect .pdata manually.")
        return 2

    for idx, c in enumerate(candidates[: args.top], 1):
        print(f"[{idx}] score={c.score} rva=0x{c.begin:x} end=0x{c.end:x} size=0x{c.end - c.begin:x} patch_size={c.patch_size}")
        print("    first_bytes=" + " ".join(f"{b:02x}" for b in c.first_bytes))
        for reason in collections.Counter(c.reasons).most_common():
            print(f"    reason: {reason[0]} x{reason[1]}")
        for label, ref in c.refs[:12]:
            print(f"    ref: {label} at rva=0x{ref:x}")
        print()

    best = candidates[0]
    print("Recommended GPMIUnitHook.ini:")
    print("[GPMIUnitHook]")
    print("probe_only=1")
    print(f"object_callp_rva=0x{best.begin:x}")
    print(f"object_callp_patch_size={best.patch_size}")
    print()
    print("After probe logs real unit calls, set probe_only=0 or remove it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
