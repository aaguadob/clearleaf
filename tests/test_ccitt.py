"""CCITTFaxDecode: the code tables, hand-made streams, and a cross-check
against libtiff (through Pillow, if installed) on generated bitmaps in
every coding the filter supports: Group 4, Group 3 1-D and 2-D, with and
without byte-aligned EOLs."""

import io
import random

import pytest

import clearleaf as pdf
from clearleaf._ccitt import BLACK, WHITE, ccitt_decode


@pytest.mark.parametrize("table", [WHITE, BLACK], ids=["white", "black"])
def test_code_tables_are_prefix_free_and_complete(table):
    codes = [format(value, f"0{length}b") for length, value in table]
    assert len(codes) == len(set(codes)) == 64 + 27 + 13
    for a in codes:
        for b in codes:
            assert a == b or not b.startswith(a)
    # Kraft sum: the codes plus the EOL prefix (000000000000...) fill the code space.
    assert sum(2.0 ** -len(c) for c in codes) + 2.0 ** -11 * 1 <= 1.0


def _bits(text: str) -> bytes:
    text = text.replace(" ", "")
    text += "0" * (-len(text) % 8)
    return int(text, 2).to_bytes(len(text) // 8, "big")


def test_one_dimensional_runs():
    # 8 columns: 3 white, 2 black, 3 white  (white 3 = 1000, black 2 = 11, white 3 = 1000)
    data = _bits("1000 11 1000")
    assert ccitt_decode(data, k=0, columns=8, rows=1) == bytes([0b11100111])
    assert ccitt_decode(data, k=0, columns=8, rows=1, black_is_1=True) == bytes([0b00011000])


def test_group4_vertical_and_horizontal_modes():
    # Line 1 (reference: all white): horizontal mode, white 3 / black 2, then
    # V0 for the final change (b1 = 8) -> 3 white, 2 black, 3 white.
    # Line 2: V0, V0, V0 -> identical to line 1.  Then EOFB.
    data = _bits("001 1000 11" + "1" + "1 1 1" + "000000000001 000000000001")
    out = ccitt_decode(data, k=-1, columns=8)
    assert out == bytes([0b11100111, 0b11100111])


def test_missing_rows_are_padded_white():
    data = _bits("1000 11 1000")
    assert ccitt_decode(data, k=0, columns=8, rows=3) == bytes([0b11100111, 0xFF, 0xFF])


def _libtiff_strip(img, compression, t4options=None):
    from PIL import Image, TiffImagePlugin

    info = TiffImagePlugin.ImageFileDirectory_v2()
    if t4options is not None:
        info[292] = t4options
    buf = io.BytesIO()
    img.save(buf, "TIFF", compression=compression, tiffinfo=info)
    raw = buf.getvalue()
    tags = Image.open(io.BytesIO(raw)).tag_v2
    return b"".join(raw[o : o + n] for o, n in zip(tags[273], tags[279]))


CODINGS = [  # (Pillow compression, T4Options, /K)
    ("group4", None, -1),
    ("group3", 0, 0),   # 1-D, EOL before each line
    ("group3", 4, 0),   # 1-D, EOLs byte-aligned
    ("group3", 1, 1),   # 2-D (mixed, tag bit after each EOL)
    ("group3", 5, 1),   # 2-D, EOLs byte-aligned
]


@pytest.mark.parametrize("compression,t4options,k", CODINGS)
def test_matches_libtiff(compression, t4options, k):
    Image = pytest.importorskip("PIL.Image")
    from PIL import features

    if not features.check("libtiff"):
        pytest.skip("Pillow built without libtiff")
    rng = random.Random(7)
    for trial in range(12):
        w, h = rng.choice([1, 7, 16, 33, 257, 1728]), rng.choice([1, 5, 24])
        img = Image.new("1", (w, h), 1)
        px = img.load()
        period = rng.randint(1, 40)
        for y in range(h):
            for x in range(w):
                on = (rng.random() < 0.5) if trial % 3 == 0 else ((x // period + y // 3) % 2 == 0)
                px[x, y] = 0 if on else 1
        data = _libtiff_strip(img, compression, t4options)
        # Pillow writes mode "1" as min-is-black, so fax "black" = pixel value 1.
        got = ccitt_decode(data, k=k, columns=w, rows=h, black_is_1=True)
        assert got == img.tobytes(), (compression, t4options, w, h, trial)


def test_ccitt_image_in_a_pdf_renders_and_extracts():
    Image = pytest.importorskip("PIL.Image")
    from PIL import features

    if not features.check("libtiff"):
        pytest.skip("Pillow built without libtiff")
    img = Image.new("1", (16, 16), 1)
    for x in range(4, 12):
        for y in range(4, 12):
            img.putpixel((x, y), 0)
    fax = _libtiff_strip(img, "group4")
    content = b"q 16 0 0 16 0 0 cm /Im1 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 16 16] /Contents 4 0 R "
        b"/Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 16 /Height 16 /BitsPerComponent 1 "
        b"/ColorSpace /DeviceGray /Filter /CCITTFaxDecode "
        b"/DecodeParms << /K -1 /Columns 16 /Rows 16 /BlackIs1 true >> /Length %d >>\nstream\n" % len(fax)
        + fax + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.7\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)

    doc = pdf.open(out)
    pix = doc[0].get_pixmap(dpi=72)
    at = lambda x, y: pix.samples[(y * pix.width + x) * 3]
    # Pillow's min-is-black data with /BlackIs1 true decodes back to the
    # original samples: a black square on white.
    assert at(8, 8) == 0 and at(1, 1) == 255
    image = doc.extract_image(doc[0].get_images()[0][0])
    assert image["ext"] == "png" and image["width"] == 16
