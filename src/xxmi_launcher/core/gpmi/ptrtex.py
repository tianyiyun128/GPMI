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


def ptrtex_to_image(src: Path):
    from PIL import Image

    width, height, fmt, row_pitch, pixels = read_ptrtex(src)
    row_bytes = width * 4
    if row_pitch < row_bytes:
        raise ValueError("PTRTEX row pitch is smaller than the image width")
    if fmt not in (FMT_RGBA8, FMT_BGRA8):
        raise ValueError(f"Unsupported PTRTEX format: {fmt}")

    packed = bytearray(height * row_bytes)
    for y in range(height):
        src_offset = y * row_pitch
        dst_offset = y * row_bytes
        packed[dst_offset:dst_offset + row_bytes] = pixels[src_offset:src_offset + row_bytes]

    if fmt == FMT_BGRA8:
        for i in range(0, len(packed), 4):
            packed[i], packed[i + 2] = packed[i + 2], packed[i]

    return Image.frombytes("RGBA", (width, height), bytes(packed), "raw", "RGBA")


def ptrtex_to_png(src: Path, dst: Path) -> Tuple[int, int, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    image = ptrtex_to_image(src)
    image.save(dst, "PNG")
    _, _, fmt, _, _ = read_ptrtex(src)
    fmt_name = "RGBA8" if fmt == FMT_RGBA8 else "BGRA8" if fmt == FMT_BGRA8 else f"unknown({fmt})"
    return image.width, image.height, fmt_name


def ptrtex_info(path: Path) -> Dict[str, object]:
    width, height, fmt, row_pitch, pixels = read_ptrtex(path)
    return {
        "width": width,
        "height": height,
        "format": "RGBA8" if fmt == FMT_RGBA8 else "BGRA8" if fmt == FMT_BGRA8 else f"unknown({fmt})",
        "row_pitch": row_pitch,
        "data_size": len(pixels),
    }
