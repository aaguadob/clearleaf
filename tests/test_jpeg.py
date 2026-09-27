"""Cross-checks clearleaf's from-scratch JPEG decoder against libjpeg (via
Pillow, an independent, permissively-licensed implementation) — the same
strategy used for the LZW decoder cross-checks, which caught a real bug
there. Also covers a couple of pure-unit-test invariants (Huffman table
construction, IDCT of an all-zero / DC-only block).
"""

import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from clearleaf._jpeg import JPEGError, _build_huffman, _idct_8x8, decode_jpeg  # noqa: E402


def _encode(mode, width, height, pixel_fn, **save_kwargs):
    img = Image.new(mode, (width, height))
    img.putdata([pixel_fn(x, y) for y in range(height) for x in range(width)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", **save_kwargs)
    return buf.getvalue()


def _max_diff(a, b, n):
    return max(abs(a[i] - b[i]) for i in range(n))


def test_grayscale_matches_libjpeg_exactly():
    data = _encode("L", 16, 16, lambda x, y: ((x * 16 + y) * 255) % 256, quality=90)
    pixels, w, h, n = decode_jpeg(data)
    assert (w, h, n) == (16, 16, 1)
    ref = list(Image.open(io.BytesIO(data)).convert("L").getdata())
    assert _max_diff(pixels, ref, w * h) == 0


def test_rgb_no_subsampling_matches_libjpeg_almost_exactly():
    # 4:4:4 (no chroma subsampling) isolates the DCT/Huffman/dequant/color
    # pipeline from our chroma-upsampling simplification (nearest-neighbor
    # vs libjpeg's fancier default) — should be near bit-exact.
    data = _encode(
        "RGB", 32, 24,
        lambda x, y: ((x * 8) % 256, (y * 10) % 256, ((x + y) * 5) % 256),
        quality=85, subsampling=0,
    )
    pixels, w, h, n = decode_jpeg(data)
    assert (w, h, n) == (32, 24, 3)
    ref_img = Image.open(io.BytesIO(data)).convert("RGB")
    ref = [c for px in ref_img.getdata() for c in px]
    assert _max_diff(pixels, ref, w * h * 3) <= 3


def test_rgb_with_chroma_subsampling_close_to_libjpeg():
    # Default 4:2:0 subsampling: our nearest-neighbor upsampling deviates
    # more from libjpeg's fancy upsampling, especially on sharp synthetic
    # gradients — bounded but not tiny. Documented simplification, not a
    # correctness bug (see test above for the isolated pipeline check).
    data = _encode(
        "RGB", 32, 24,
        lambda x, y: ((x * 8) % 256, (y * 10) % 256, ((x + y) * 5) % 256),
        quality=85,
    )
    pixels, w, h, n = decode_jpeg(data)
    ref_img = Image.open(io.BytesIO(data)).convert("RGB")
    ref = [c for px in ref_img.getdata() for c in px]
    avg = sum(abs(pixels[i] - ref[i]) for i in range(w * h * 3)) / (w * h * 3)
    assert avg < 5.0


def test_cmyk_adobe_inversion_matches_libjpeg():
    data = _encode(
        "CMYK", 16, 16,
        lambda x, y: ((x * 16) % 256, (y * 16) % 256, ((x + y) * 8) % 256, 40),
        quality=90,
    )
    assert b"Adobe" in data[:200]  # sanity: PIL did write the marker we rely on
    pixels, w, h, n = decode_jpeg(data)
    assert (w, h, n) == (16, 16, 4)
    ref = list(Image.open(io.BytesIO(data)).getdata())
    ref_flat = [c for px in ref for c in px]
    assert _max_diff(pixels, ref_flat, w * h * 4) <= 2


def test_smooth_photo_like_gradient_rgb():
    # A smoother image (no hard per-pixel edges) is more representative of
    # real photos and should upsample close to identically regardless of
    # filter choice.
    def px(x, y):
        return (
            int(128 + 100 * ((x / 31) - 0.5)),
            int(128 + 100 * ((y / 23) - 0.5)),
            128,
        )

    data = _encode("RGB", 32, 24, px, quality=90)
    pixels, w, h, n = decode_jpeg(data)
    ref_img = Image.open(io.BytesIO(data)).convert("RGB")
    ref = [c for p in ref_img.getdata() for c in p]
    avg = sum(abs(pixels[i] - ref[i]) for i in range(w * h * 3)) / (w * h * 3)
    assert avg < 2.0


def test_progressive_no_subsampling_matches_libjpeg_almost_exactly():
    data = _encode(
        "RGB", 32, 24,
        lambda x, y: ((x * 8) % 256, (y * 10) % 256, ((x + y) * 5) % 256),
        quality=85, subsampling=0, progressive=True,
    )
    pixels, w, h, n = decode_jpeg(data)
    assert (w, h, n) == (32, 24, 3)
    ref_img = Image.open(io.BytesIO(data)).convert("RGB")
    ref = [c for px in ref_img.getdata() for c in px]
    assert _max_diff(pixels, ref, w * h * 3) <= 3


def test_progressive_grayscale_matches_libjpeg_almost_exactly():
    data = _encode("L", 16, 16, lambda x, y: ((x * 16 + y) * 255) % 256, quality=90, progressive=True)
    pixels, w, h, n = decode_jpeg(data)
    assert (w, h, n) == (16, 16, 1)
    ref = list(Image.open(io.BytesIO(data)).convert("L").getdata())
    assert _max_diff(pixels, ref, w * h) <= 2


def test_arithmetic_coding_raises_jpeg_error():
    data = _encode("RGB", 16, 16, lambda x, y: (x * 16, y * 16, 0))
    # SOF1 (extended sequential) is a supported baseline-family marker; corrupt
    # it to SOF9 (arithmetic-coded extended sequential) to hit the reject path.
    corrupted = data.replace(b"\xff\xc0", b"\xff\xc9", 1)
    with pytest.raises(JPEGError):
        decode_jpeg(corrupted)


def test_not_a_jpeg_raises():
    with pytest.raises(Exception):
        decode_jpeg(b"not a jpeg at all")


def test_build_huffman_produces_canonical_codes():
    # 3 symbols, all length 2: canonical codes are 00, 01, 10.
    counts = [0, 3] + [0] * 14
    symbols = [5, 6, 7]
    table = _build_huffman(counts, symbols)
    assert table[(2, 0b00)] == 5
    assert table[(2, 0b01)] == 6
    assert table[(2, 0b10)] == 7


def test_idct_dc_only_block_is_uniform():
    coeffs = [0] * 64
    coeffs[0] = 8  # DC-only, dequantized value 8 -> uniform level shift
    block = _idct_8x8(coeffs)
    # DC-only block should produce a (near-)constant output block.
    assert max(block) - min(block) <= 1


def test_idct_zero_block_is_mid_gray():
    block = _idct_8x8([0] * 64)
    assert all(v == 128 for v in block)
