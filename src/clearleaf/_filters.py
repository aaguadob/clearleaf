"""Stream filters (ISO 32000-1:2008 section 7.4).

Implements FlateDecode, LZWDecode, ASCII85Decode, ASCIIHexDecode and
RunLengthDecode, plus the PNG/TIFF predictors used with Flate and LZW for
row-oriented image data, and CCITTFaxDecode (_ccitt.py). The other
image-codec filters (DCTDecode/JBIG2Decode/JPXDecode) are passed through
undecoded — turning their bytes
into pixels is real image decoding, deferred to the rendering phase; Phase 1
only needs to hand back the still-encoded bytes (e.g. raw JPEG data for
`get_images()`).
"""

from __future__ import annotations

import struct
import zlib
from typing import Callable, Optional

from ._objects import PDFReference

FILTER_ALIASES = {
    "Fl": "FlateDecode",
    "AHx": "ASCIIHexDecode",
    "A85": "ASCII85Decode",
    "LZW": "LZWDecode",
    "RL": "RunLengthDecode",
    "CCF": "CCITTFaxDecode",
    "DCT": "DCTDecode",
}

PASSTHROUGH_FILTERS = {"DCTDecode", "JBIG2Decode", "JPXDecode"}


def _make_resolver(resolver: Optional[Callable]):
    def res(x):
        if resolver is not None:
            while isinstance(x, PDFReference):
                x = resolver(x)
        return x

    return res


def decode_stream(stream_dict: dict, raw_data: bytes, resolver=None) -> bytes:
    res = _make_resolver(resolver)

    filt = res(stream_dict.get("Filter"))
    if filt is None:
        return raw_data
    filters = [res(f) for f in filt] if isinstance(filt, list) else [filt]

    parms = res(stream_dict.get("DecodeParms"))
    if parms is None:
        parms = res(stream_dict.get("DP"))
    parms_list = [res(p) for p in parms] if isinstance(parms, list) else [parms]
    while len(parms_list) < len(filters):
        parms_list.append(None)

    data = raw_data
    for f, p in zip(filters, parms_list):
        name = FILTER_ALIASES.get(str(f), str(f))
        if name == "FlateDecode":
            data = flate_decode(data)
            data = _apply_predictor(data, p, res)
        elif name == "ASCII85Decode":
            data = ascii85_decode(data)
        elif name == "ASCIIHexDecode":
            data = asciihex_decode(data)
        elif name == "LZWDecode":
            early = 1
            if isinstance(p, dict):
                v = res(p.get("EarlyChange", 1))
                early = 1 if v is None else v
            data = lzw_decode(data, early_change=early)
            data = _apply_predictor(data, p, res)
        elif name == "RunLengthDecode":
            data = runlength_decode(data)
        elif name == "CCITTFaxDecode":
            from ._ccitt import decode_with_params

            data = decode_with_params(data, {str(k): res(v) for k, v in p.items()} if isinstance(p, dict) else None)
        elif name in PASSTHROUGH_FILTERS:
            pass
        elif name == "Crypt":
            pass  # decryption is applied when the object is loaded (_security.py)
        else:
            raise ValueError(f"unsupported filter {name!r}")
    return data


# -- FlateDecode --------------------------------------------------------------


def flate_decode(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        # Some producers write truncated/corrupt trailing bytes; recover
        # whatever the decompressor managed to produce rather than erroring.
        d = zlib.decompressobj()
        out = d.decompress(data)
        out += d.flush() if not d.eof else b""
        return out


# -- Predictors -----------------------------------------------------------


def _apply_predictor(data: bytes, parms, res) -> bytes:
    if not isinstance(parms, dict):
        return data
    predictor = res(parms.get("Predictor", 1))
    predictor = 1 if predictor is None else predictor
    if predictor == 1:
        return data
    colors = res(parms.get("Colors", 1)) or 1
    bpc = res(parms.get("BitsPerComponent", 8)) or 8
    columns = res(parms.get("Columns", 1)) or 1
    if predictor == 2:
        return _tiff_predictor_decode(data, colors, bpc, columns)
    if predictor >= 10:
        return _png_predictor_decode(data, colors, bpc, columns)
    raise ValueError(f"unsupported predictor {predictor}")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_predictor_decode(data: bytes, colors: int, bpc: int, columns: int) -> bytes:
    bpp = max(1, (colors * bpc + 7) // 8)
    row_bytes = (colors * bpc * columns + 7) // 8
    out = bytearray()
    prev = bytearray(row_bytes)
    pos, n = 0, len(data)
    while pos < n:
        filter_type = data[pos]
        pos += 1
        chunk = data[pos : pos + row_bytes]
        pos += row_bytes
        row = bytearray(chunk)
        if len(row) < row_bytes:
            row.extend(b"\x00" * (row_bytes - len(row)))
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:
            for i in range(len(row)):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) // 2)) & 0xFF
        elif filter_type == 4:
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(left, prev[i], upleft)) & 0xFF
        else:
            raise ValueError(f"unknown PNG predictor filter type {filter_type}")
        out += row
        prev = row
    return bytes(out)


def _tiff_predictor_decode(data: bytes, colors: int, bpc: int, columns: int) -> bytes:
    if bpc == 8:
        row_bytes = colors * columns
        out = bytearray()
        for r in range(0, len(data), row_bytes):
            row = bytearray(data[r : r + row_bytes])
            for i in range(colors, len(row)):
                row[i] = (row[i] + row[i - colors]) & 0xFF
            out += row
        return bytes(out)
    if bpc == 16:
        row_samples = colors * columns
        row_bytes = row_samples * 2
        out = bytearray()
        for r in range(0, len(data), row_bytes):
            chunk = data[r : r + row_bytes]
            samples = list(struct.unpack(f">{row_samples}H", chunk))
            for i in range(colors, row_samples):
                samples[i] = (samples[i] + samples[i - colors]) & 0xFFFF
            out += struct.pack(f">{row_samples}H", *samples)
        return bytes(out)

    # Bit-level path for 1/2/4-bit samples.
    row_bits = colors * bpc * columns
    row_bytes = (row_bits + 7) // 8
    mask = (1 << bpc) - 1
    out = bytearray()
    for r in range(0, len(data), row_bytes):
        chunk = data[r : r + row_bytes]
        bits = int.from_bytes(chunk, "big")
        total_bits = row_bytes * 8
        count = colors * columns
        samples = [
            (bits >> (total_bits - bpc * (i + 1))) & mask for i in range(count)
        ]
        for i in range(colors, count):
            samples[i] = (samples[i] + samples[i - colors]) & mask
        outbits = 0
        for s in samples:
            outbits = (outbits << bpc) | s
        outbits <<= total_bits - bpc * count
        out += outbits.to_bytes(row_bytes, "big")
    return bytes(out)


# -- ASCII85Decode / ASCIIHexDecode -------------------------------------------

_A85_WHITESPACE = b" \t\r\n\x0c\x00"


def ascii85_decode(data: bytes) -> bytes:
    data = bytes(b for b in data if b not in _A85_WHITESPACE)
    if data.endswith(b"~>"):
        data = data[:-2]
    out = bytearray()
    group = []
    for c in data:
        if c == 0x7A:  # 'z' == four zero bytes, only valid group-aligned
            if group:
                raise ValueError("'z' shorthand inside an ASCII85 group")
            out += b"\x00\x00\x00\x00"
            continue
        group.append(c - 33)
        if len(group) == 5:
            val = 0
            for g in group:
                val = val * 85 + g
            out += val.to_bytes(4, "big")
            group = []
    if group:
        k = len(group)
        padded = group + [84] * (5 - k)
        val = 0
        for g in padded:
            val = val * 85 + g
        out += val.to_bytes(4, "big")[: k - 1]
    return bytes(out)


def asciihex_decode(data: bytes) -> bytes:
    data = bytes(b for b in data if b not in _A85_WHITESPACE)
    if data.endswith(b">"):
        data = data[:-1]
    if len(data) % 2:
        data += b"0"
    return bytes.fromhex(data.decode("ascii"))


# -- LZWDecode ------------------------------------------------------------


def lzw_decode(data: bytes, early_change: int = 1) -> bytes:
    CLEAR, EOD = 256, 257
    bitpos, nbits = 0, len(data) * 8

    def read_code(width: int):
        nonlocal bitpos
        if bitpos + width > nbits:
            return None
        val = 0
        for _ in range(width):
            byte = data[bitpos // 8]
            bit = (byte >> (7 - (bitpos % 8))) & 1
            val = (val << 1) | bit
            bitpos += 1
        return val

    def reset():
        return {i: bytes([i]) for i in range(256)}, 258, 9

    out = bytearray()
    table, next_code, code_width = reset()
    prev = None
    while True:
        code = read_code(code_width)
        if code is None or code == EOD:
            break
        if code == CLEAR:
            table, next_code, code_width = reset()
            prev = None
            continue
        if code in table:
            entry = table[code]
        elif code == next_code and prev is not None:
            entry = prev + prev[:1]
        else:
            raise ValueError(f"invalid LZW code {code}")
        out += entry
        if prev is not None:
            table[next_code] = prev + entry[:1]
            next_code += 1
            # >= (not >): the decoder's table trails the encoder's by one
            # entry (it can't materialize the Nth entry until it has read
            # the code that reveals it), so it must bump the code width one
            # code sooner than a naively-symmetric check would. Verified
            # against real libtiff-produced LZW streams (Pillow, tiff_lzw).
            if next_code + early_change >= (1 << code_width) and code_width < 12:
                code_width += 1
        prev = entry
    return bytes(out)


# -- RunLengthDecode --------------------------------------------------------


def runlength_decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        length = data[i]
        i += 1
        if length == 128:
            break
        if length < 128:
            out += data[i : i + length + 1]
            i += length + 1
        elif i < n:
            out += bytes([data[i]]) * (257 - length)
            i += 1
    return bytes(out)
