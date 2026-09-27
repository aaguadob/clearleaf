import struct
import zlib

import pytest

from clearleaf._filters import (
    ascii85_decode,
    asciihex_decode,
    decode_stream,
    flate_decode,
    lzw_decode,
    runlength_decode,
)
from clearleaf._objects import PDFName, PDFReference


def test_flate_decode_roundtrip():
    original = b"the quick brown fox jumps over the lazy dog" * 10
    assert flate_decode(zlib.compress(original)) == original


def test_ascii85_decode_known_vectors():
    # "Man " -> classic example from the Adobe spec. PDF's ASCII85Decode
    # filter only ever terminates streams with a trailing "~>" — the
    # leading "<~" is a convention from other contexts, not part of the
    # PDF filter, so it's not (and shouldn't be) stripped here.
    assert ascii85_decode(b"9jqo^") == b"Man "
    assert ascii85_decode(b"9jqo^~>") == b"Man "


def test_ascii85_decode_z_shorthand():
    assert ascii85_decode(b"z") == b"\x00\x00\x00\x00"


def test_ascii85_decode_roundtrip_arbitrary_length():
    import base64

    for n in range(0, 20):
        data = bytes(range(n))
        encoded = base64.a85encode(data)
        assert ascii85_decode(encoded) == data


def test_asciihex_decode():
    assert asciihex_decode(b"48656C6C6F>") == b"Hello"
    assert asciihex_decode(b"48 65 6C 6C 6F >") == b"Hello"
    assert asciihex_decode(b"4") == b"\x40"  # odd length padded with 0


def test_runlength_decode():
    # Literal run: length byte 2 => copy next 3 bytes ("abc").
    # Repeat run: length byte 253 => repeat next byte (257-253=4 times).
    # Length byte 128 => EOD.
    data = bytes([2]) + b"abc" + bytes([253]) + b"x" + bytes([128])
    assert runlength_decode(data) == b"abc" + b"x" * 4


def test_lzw_decode_roundtrip_via_reference_encoder():
    from tests.generate_fixtures import _lzw_encode

    original = b"TOBEORNOTTOBEORTOBEORNOT" * 5
    assert lzw_decode(_lzw_encode(original)) == original


def test_lzw_decode_handles_clear_code_reset():
    from tests.generate_fixtures import _lzw_encode

    # Force enough distinct data that a naive implementation without a
    # working CLEAR handler would diverge.
    original = bytes((i * 37) % 256 for i in range(600))
    assert lzw_decode(_lzw_encode(original)) == original


def test_png_predictor_sub_filter():
    # Predictor 12 (PNG "Up" per-scanline default via /Predictor >=10 dispatch),
    # 1 color, 8 bpc, 4 columns. Build 2 rows manually with filter type 1 (Sub).
    row0 = bytes([1]) + bytes([10, 1, 1, 1])  # Sub: cumulative -> 10 11 12 13
    row1 = bytes([1]) + bytes([1, 1, 1, 1])  # Sub again -> 1 2 3 4
    payload = row0 + row1
    stream_dict = {
        "Filter": PDFName("FlateDecode"),
        "DecodeParms": {"Predictor": 12, "Colors": 1, "BitsPerComponent": 8, "Columns": 4},
    }
    raw = zlib.compress(payload)
    out = decode_stream(stream_dict, raw)
    assert out == bytes([10, 11, 12, 13]) + bytes([1, 2, 3, 4])


def test_tiff_predictor_8bpc():
    # 2 colors, 8bpc, 3 columns -> 6 samples/row, horizontal differencing.
    row = bytes([10, 20, 1, 1, 1, 1])  # deltas after first pixel
    stream_dict = {
        "Filter": PDFName("FlateDecode"),
        "DecodeParms": {"Predictor": 2, "Colors": 2, "BitsPerComponent": 8, "Columns": 3},
    }
    raw = zlib.compress(row)
    out = decode_stream(stream_dict, raw)
    assert out == bytes([10, 20, 11, 21, 12, 22])


def test_tiff_predictor_16bpc():
    samples = [1000, 2000, 100, 100]
    row = struct.pack(">4H", *samples)
    stream_dict = {
        "Filter": PDFName("FlateDecode"),
        "DecodeParms": {"Predictor": 2, "Colors": 2, "BitsPerComponent": 16, "Columns": 2},
    }
    raw = zlib.compress(row)
    out = decode_stream(stream_dict, raw)
    assert struct.unpack(">4H", out) == (1000, 2000, 1100, 2100)


def test_multiple_filters_applied_in_order():
    original = b"chained filters"
    encoded = zlib.compress(original)
    hexed = encoded.hex().encode("ascii").upper() + b">"
    stream_dict = {"Filter": [PDFName("ASCIIHexDecode"), PDFName("FlateDecode")]}
    assert decode_stream(stream_dict, hexed) == original


def test_dct_decode_is_passthrough():
    fake_jpeg_bytes = b"\xff\xd8\xff\xe0not really a jpeg\xff\xd9"
    stream_dict = {"Filter": PDFName("DCTDecode")}
    assert decode_stream(stream_dict, fake_jpeg_bytes) == fake_jpeg_bytes


def test_indirect_filter_and_parms_are_resolved():
    original = b"resolved via indirect refs"
    encoded = zlib.compress(original)
    resolver_table = {
        (9, 0): PDFName("FlateDecode"),
        (10, 0): {},
    }
    stream_dict = {"Filter": PDFReference(9, 0), "DecodeParms": PDFReference(10, 0)}

    def resolver(ref):
        return resolver_table[(ref.num, ref.gen)]

    assert decode_stream(stream_dict, encoded, resolver=resolver) == original


def test_unsupported_filter_raises():
    with pytest.raises(ValueError):
        decode_stream({"Filter": PDFName("BogusDecode")}, b"data")
