"""Minimal PNG encoder (ISO/IEC 15948 / RFC 2083) for `Pixmap.save()`.

Only what's needed to write an 8-bit RGB (or RGBA) raster out: signature +
IHDR + one IDAT (zlib/DEFLATE via the standard library's `zlib`, which is
compression plumbing, not an image/PDF library — using it is the same kind
of "standard codec" the project brief explicitly allows, same as FlateDecode
in `_filters.py`) + IEND. No filtering (per-scanline filter byte is always
0/None) — correct, just not maximally compressed; fine for a rendering
output path where correctness matters more than file size.
"""

from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(pixels: bytes, width: int, height: int, channels: int = 3) -> bytes:
    if channels not in (1, 2, 3, 4):
        raise ValueError(f"unsupported channel count {channels}")
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    bytes_per_pixel = channels
    stride = width * bytes_per_pixel
    if len(pixels) != stride * height:
        raise ValueError(
            f"pixel buffer length {len(pixels)} != width*height*channels ({stride * height})"
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw += pixels[y * stride : (y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=6)

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
