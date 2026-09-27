"""Cross-checks clearleaf's LZWDecode against real LZW bitstreams produced by
libtiff (via Pillow), an independent, permissively-licensed implementation.

PDF's LZWDecode filter is bit-for-bit the same scheme TIFF uses for its LZW
compression (9-bit codes growing to 12, CLEAR=256/EOD=257, EarlyChange=1 by
default) — see ISO 32000-1:2008 7.4.4.2. This caught a real bug during
development: a naive symmetric ">" check for when to widen the code length
works for the *encoder* but is off by one entry for the *decoder*, because
the decoder's table construction inherently trails the encoder's by one
entry (it can't materialize the Nth entry until it has read the code that
reveals it). The fix needed ">=" on the decode side specifically — these
tests pin that against ground truth so it can't regress silently.
"""

import random

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from clearleaf._filters import lzw_decode  # noqa: E402


def _tiff_lzw_strips(pixel_bytes: bytes, width: int, height: int):
    import io

    img = Image.new("L", (width, height))
    img.putdata(list(pixel_bytes))
    buf = io.BytesIO()
    img.save(buf, format="TIFF", compression="tiff_lzw")
    raw = buf.getvalue()

    img2 = Image.open(io.BytesIO(raw))
    offsets = img2.tag_v2.get(273)
    counts = img2.tag_v2.get(279)
    return [raw[o : o + c] for o, c in zip(offsets, counts)]


def _decode_all(strips) -> bytes:
    return b"".join(lzw_decode(s) for s in strips)


def test_lzw_decode_matches_libtiff_short_stream():
    original = bytes((i * 37) % 256 for i in range(600))
    strips = _tiff_lzw_strips(original, width=600, height=1)
    assert _decode_all(strips) == original


def test_lzw_decode_matches_libtiff_across_multiple_width_transitions():
    # Enough near-incompressible data to cross the 511/1023/2047-entry
    # code-width boundaries (and likely trigger table-full CLEAR resets).
    rng = random.Random(42)
    original = bytes(rng.randrange(256) for _ in range(90_000))
    strips = _tiff_lzw_strips(original, width=300, height=300)
    assert _decode_all(strips) == original


def test_lzw_decode_matches_libtiff_highly_repetitive_stream():
    # Highly compressible data exercises long table entries and lots of
    # multi-byte matches, a different code path than near-random data.
    original = (b"ABCABCABC" * 50 + b"XYZ" * 30) * 4
    strips = _tiff_lzw_strips(original, width=len(original), height=1)
    assert _decode_all(strips) == original
