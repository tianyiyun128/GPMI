from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, Tuple

MAGIC = b"PTRTEX01"
FMT_RGBA8 = 1
FMT_BGRA8 = 2
HEADER_SIZE = 28


def write_ptrtex(path: Path, width: int, height: int, fmt: int, pixels: bytes) -> None:
    row_pitch = width * 4
    path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<8sIIIII", MAGIC, width, height, fmt, row_pitch, len(pixels))
    path.write_bytes(header + pixels)


def read_ptrtex(path: Path) -> Tuple[int, int, int, int, bytes]:
    raw = path.read_bytes()
    if len(raw) < HEADER_SIZE:
        raise ValueError("PTRTEX file is too small")
    magic, width, height, fmt, row_pitch, size = struct.unpack("<8sIIIII", raw[:HEADER_SIZE])
    if magic != MAGIC:
        raise ValueError("Bad PTRTEX magic")
    pixels = raw[HEADER_SIZE:HEADER_SIZE + size]
    if len(pixels) != size:
        raise ValueError("PTRTEX pixel payload is truncated")
    return width, height, fmt, row_pitch, pixels


def png_to_ptrtex(src: Path, dst: Path, bgra: bool = False) -> Tuple[int, int, str]:
    from PIL import Image

    image = Image.open(src).convert("RGBA")
    width, height = image.size
    pixels = bytearray(image.tobytes("raw", "RGBA"))
    fmt = FMT_RGBA8
    fmt_name = "RGBA8"
    if bgra:
        for i in range(0, len(pixels), 4):
            pixels[i], pixels[i + 2] = pixels[i + 2], pixels[i]
        fmt = FMT_BGRA8
        fmt_name = "BGRA8"
    write_ptrtex(dst, width, height, fmt, bytes(pixels))
    return width, height, fmt_name


def ptrtex_info(path: Path) -> Dict[str, object]:
    width, height, fmt, row_pitch, pixels = read_ptrtex(path)
    return {
        "width": width,
        "height": height,
        "format": "RGBA8" if fmt == FMT_RGBA8 else "BGRA8" if fmt == FMT_BGRA8 else f"unknown({fmt})",
        "row_pitch": row_pitch,
        "data_size": len(pixels),
    }
