#!/usr/bin/env python3
"""PTRTEX utility for Godot Portrait Hash Replace.

Usage:
  python ptrtex_tool.py png-to-ptrtex input.png output.ptrtex [--bgra]
  python ptrtex_tool.py info file.ptrtex
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

MAGIC = b"PTRTEX01"
FMT_RGBA8 = 1
FMT_BGRA8 = 2


def write_ptrtex(path: Path, width: int, height: int, fmt: int, pixels: bytes) -> None:
    row_pitch = width * 4
    path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<8sIIIII", MAGIC, width, height, fmt, row_pitch, len(pixels))
    path.write_bytes(header + pixels)


def read_ptrtex(path: Path):
    raw = path.read_bytes()
    if len(raw) < 28:
        raise ValueError("file too small")
    magic, width, height, fmt, row_pitch, size = struct.unpack("<8sIIIII", raw[:28])
    if magic != MAGIC:
        raise ValueError("bad magic")
    pixels = raw[28:28 + size]
    if len(pixels) != size:
        raise ValueError("truncated pixel data")
    return width, height, fmt, row_pitch, pixels


def png_to_ptrtex(src: Path, dst: Path, bgra: bool) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("需要安装 Pillow: pip install Pillow") from exc

    image = Image.open(src).convert("RGBA")
    width, height = image.size
    pixels = bytearray(image.tobytes("raw", "RGBA"))
    fmt = FMT_RGBA8
    if bgra:
        for i in range(0, len(pixels), 4):
            pixels[i], pixels[i + 2] = pixels[i + 2], pixels[i]
        fmt = FMT_BGRA8
    write_ptrtex(dst, width, height, fmt, bytes(pixels))
    print(f"wrote {dst} ({width}x{height}, {'BGRA8' if bgra else 'RGBA8'})")


def cmd_info(path: Path) -> None:
    width, height, fmt, row_pitch, pixels = read_ptrtex(path)
    fmt_name = {FMT_RGBA8: "RGBA8", FMT_BGRA8: "BGRA8"}.get(fmt, f"unknown({fmt})")
    print(f"file: {path}")
    print(f"size: {width}x{height}")
    print(f"format: {fmt_name}")
    print(f"row_pitch: {row_pitch}")
    print(f"data_size: {len(pixels)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("png-to-ptrtex")
    p.add_argument("src", type=Path)
    p.add_argument("dst", type=Path)
    p.add_argument("--bgra", action="store_true")

    p = sub.add_parser("info")
    p.add_argument("path", type=Path)

    args = parser.parse_args()
    if args.cmd == "png-to-ptrtex":
        png_to_ptrtex(args.src, args.dst, args.bgra)
    elif args.cmd == "info":
        cmd_info(args.path)


if __name__ == "__main__":
    main()
