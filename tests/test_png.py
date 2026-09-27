import struct
import zlib

import pytest

from clearleaf._png import encode_png


def _parse_chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        assert crc == (zlib.crc32(tag + payload) & 0xFFFFFFFF)
        chunks.append((tag, payload))
        pos += 12 + length
    return chunks


def _decode_raw_scanlines(idat: bytes, width: int, height: int, channels: int) -> bytes:
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray()
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        assert filter_type == 0  # encoder always emits "None"
        pos += 1
        out += raw[pos : pos + stride]
        pos += stride
    return bytes(out)


def test_png_signature_and_chunk_order():
    pixels = bytes([255, 0, 0] * 4)  # 2x2 red
    png = encode_png(pixels, 2, 2, channels=3)
    chunks = _parse_chunks(png)
    tags = [tag for tag, _ in chunks]
    assert tags == [b"IHDR", b"IDAT", b"IEND"]


def test_png_ihdr_fields():
    pixels = bytes(3 * 4 * 5)
    png = encode_png(pixels, 4, 5, channels=3)
    chunks = _parse_chunks(png)
    ihdr = dict(chunks)[b"IHDR"]
    width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    assert (width, height) == (4, 5)
    assert bit_depth == 8
    assert color_type == 2  # RGB
    assert (comp, filt, interlace) == (0, 0, 0)


def test_png_roundtrip_rgb_pixel_data():
    width, height = 3, 2
    pixels = bytes(
        [
            255, 0, 0,    0, 255, 0,   0, 0, 255,
            10, 20, 30,   40, 50, 60,  70, 80, 90,
        ]
    )
    png = encode_png(pixels, width, height, channels=3)
    chunks = _parse_chunks(png)
    idat = dict(chunks)[b"IDAT"]
    decoded = _decode_raw_scanlines(idat, width, height, 3)
    assert decoded == pixels


def test_png_roundtrip_grayscale():
    width, height = 4, 1
    pixels = bytes([0, 85, 170, 255])
    png = encode_png(pixels, width, height, channels=1)
    chunks = _parse_chunks(png)
    ihdr = dict(chunks)[b"IHDR"]
    color_type = struct.unpack(">IIBBBBB", ihdr)[3]
    assert color_type == 0  # grayscale
    idat = dict(chunks)[b"IDAT"]
    assert _decode_raw_scanlines(idat, width, height, 1) == pixels


def test_png_roundtrip_rgba():
    width, height = 2, 1
    pixels = bytes([255, 0, 0, 128, 0, 255, 0, 64])
    png = encode_png(pixels, width, height, channels=4)
    chunks = _parse_chunks(png)
    ihdr = dict(chunks)[b"IHDR"]
    color_type = struct.unpack(">IIBBBBB", ihdr)[3]
    assert color_type == 6  # RGBA
    idat = dict(chunks)[b"IDAT"]
    assert _decode_raw_scanlines(idat, width, height, 4) == pixels


def test_png_rejects_mismatched_buffer_length():
    with pytest.raises(ValueError):
        encode_png(bytes(10), width=4, height=4, channels=3)


def test_png_rejects_unsupported_channel_count():
    with pytest.raises(ValueError):
        encode_png(bytes(9), width=3, height=1, channels=5)
