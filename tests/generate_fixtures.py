"""Hand-crafts minimal PDF byte streams as test fixtures.

Run directly (`python tests/generate_fixtures.py tests/fixtures`) to
regenerate the files under tests/fixtures/. Fixtures are built at the byte
level (no PDF library involved) so offsets/structure are fully under our
control, and the files exercise: classic xref tables, cross-reference
streams, object streams (compressed objects), FlateDecode, LZWDecode, and
an inherited-attribute page tree.
"""

import os
import zlib


class PdfBuilder:
    def __init__(self):
        self.buf = bytearray()
        self.offsets = {}  # obj_num -> offset
        self.buf += b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"

    def tell(self):
        return len(self.buf)

    def add_object(self, num, body: bytes):
        self.offsets[num] = self.tell()
        self.buf += f"{num} 0 obj\n".encode("ascii")
        self.buf += body
        if not body.endswith(b"\n"):
            self.buf += b"\n"
        self.buf += b"endobj\n"

    def add_stream_object(self, num, dict_extra: dict, data: bytes):
        parts = ["<<"]
        for k, v in dict_extra.items():
            parts.append(f"/{k} {v}")
        parts.append(f"/Length {len(data)}")
        parts.append(">>")
        header = " ".join(parts).encode("ascii")
        body = header + b"\nstream\n" + data + b"\nendstream\n"
        self.offsets[num] = self.tell()
        self.buf += f"{num} 0 obj\n".encode("ascii")
        self.buf += body
        self.buf += b"endobj\n"

    def write_classic_xref_and_trailer(self, size, root_num, extra_trailer=""):
        xref_offset = self.tell()
        self.buf += b"xref\n"
        self.buf += f"0 {size}\n".encode("ascii")
        self.buf += b"0000000000 65535 f \n"
        for n in range(1, size):
            off = self.offsets.get(n)
            if off is None:
                self.buf += b"0000000000 00000 f \n"
            else:
                self.buf += f"{off:010d} 00000 n \n".encode("ascii")
        self.buf += b"trailer\n"
        self.buf += f"<< /Size {size} /Root {root_num} 0 R {extra_trailer}>>\n".encode(
            "ascii"
        )
        self.buf += b"startxref\n"
        self.buf += f"{xref_offset}\n".encode("ascii")
        self.buf += b"%%EOF\n"


def build_classic_simple(path):
    b = PdfBuilder()
    content = b"q 1 0 0 1 0 0 cm BT /F1 12 Tf 72 712 Td (Hello clearleaf) Tj ET Q"
    compressed = zlib.compress(content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> "
        b"/Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, compressed)
    b.add_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))
    return content


def build_two_page_inherited(path):
    b = PdfBuilder()
    content1 = zlib.compress(b"q Q")
    content2 = zlib.compress(b"q Q")

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(
        2,
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 "
        b"/MediaBox [0 0 300 400] /Resources << /Font << /F1 7 0 R >> >> >>",
    )
    b.add_object(3, b"<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>")
    b.add_object(
        4,
        b"<< /Type /Page /Parent 2 0 R /Contents 6 0 R "
        b"/MediaBox [0 0 100 200] /Rotate 90 >>",
    )
    b.add_stream_object(5, {"Filter": "/FlateDecode"}, content1)
    b.add_stream_object(6, {"Filter": "/FlateDecode"}, content2)
    b.add_object(7, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    b.write_classic_xref_and_trailer(size=8, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def _xref_stream_entry(f0, f1, f2):
    return bytes([f0]) + f1.to_bytes(4, "big") + bytes([f2])


def build_xref_stream(path):
    """Same logical document as build_classic_simple, but: objects 1 and 5
    are packed into an object stream (type-2 xref entries), and the xref
    itself is a cross-reference stream (type-1 xref entries + a free entry
    for object 0), instead of a classic xref table.
    """
    b = PdfBuilder()
    content = zlib.compress(b"q Q")

    obj1_body = b"<< /Type /Catalog /Pages 2 0 R >>"
    obj5_body = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    header = f"1 0 5 {len(obj1_body) + 1}".encode("ascii")
    objstm_data = header + b"\n" + obj1_body + b" " + obj5_body
    objstm_compressed = zlib.compress(objstm_data)
    first = len(header) + 1

    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, content)
    b.add_stream_object(
        6,
        {"Type": "/ObjStm", "N": 2, "First": first, "Filter": "/FlateDecode"},
        objstm_compressed,
    )

    entries = {
        0: (0, 0, 0),
        1: (2, 6, 0),  # compressed, in objstm 6, index 0
        2: (1, b.offsets[2], 0),
        3: (1, b.offsets[3], 0),
        4: (1, b.offsets[4], 0),
        5: (2, 6, 1),  # compressed, in objstm 6, index 1
        6: (1, b.offsets[6], 0),
        7: (1, 0, 0),
    }
    xref_offset = b.tell()
    entries[7] = (1, xref_offset, 0)

    raw = bytearray()
    for n in range(8):
        raw += _xref_stream_entry(*entries[n])
    compressed_xref = zlib.compress(bytes(raw))

    xref_dict = (
        "<< /Type /XRef /Size 8 /Root 1 0 R /W [1 4 1] "
        f"/Filter /FlateDecode /Length {len(compressed_xref)} >>"
    )
    b.buf += b"7 0 obj\n"
    b.buf += xref_dict.encode("ascii")
    b.buf += b"\nstream\n" + compressed_xref + b"\nendstream\nendobj\n"
    b.buf += b"startxref\n"
    b.buf += f"{xref_offset}\n".encode("ascii")
    b.buf += b"%%EOF\n"

    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def _lzw_encode(data: bytes, early_change=1) -> bytes:
    """Independent reference LZW encoder (spec-driven, not derived from any
    third-party codebase) used only to produce test input for
    clearleaf's LZWDecode."""
    CLEAR, EOD = 256, 257
    table = {bytes([i]): i for i in range(256)}
    next_code = 258
    code_width = 9
    out_bits = []

    def emit(code, width):
        out_bits.append((code, width))

    emit(CLEAR, code_width)
    w = b""
    for byte in data:
        c = bytes([byte])
        wc = w + c
        if wc in table:
            w = wc
        else:
            emit(table[w], code_width)
            table[wc] = next_code
            next_code += 1
            w = c
            # Encoder (unlike the decoder) has no table-construction lag, so
            # it uses a plain ">" here — confirmed against real libtiff
            # output, which diverges from this encoder if ">=" is used.
            if next_code + early_change > (1 << code_width) and code_width < 12:
                code_width += 1
    if w:
        emit(table[w], code_width)
    emit(EOD, code_width)

    bitstream, nbits = 0, 0
    out = bytearray()
    for code, width in out_bits:
        bitstream = (bitstream << width) | code
        nbits += width
        while nbits >= 8:
            nbits -= 8
            out.append((bitstream >> nbits) & 0xFF)
    if nbits:
        out.append((bitstream << (8 - nbits)) & 0xFF)
    return bytes(out)


def build_lzw_content(path):
    b = PdfBuilder()
    content = b"q 1 0 0 1 0 0 cm BT /F1 10 Tf 10 10 Td (LZW test) Tj ET Q"
    encoded = _lzw_encode(content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/LZWDecode"}, encoded)
    b.write_classic_xref_and_trailer(size=5, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))
    return content


def build_text_multiline(path):
    """Three lines of plain WinAnsi-encoded text via Td/T*, to test plain
    -text newline joining."""
    b = PdfBuilder()
    content = (
        b"BT /F1 12 Tf 14 TL 72 700 Td "
        b"(Line one) Tj T* "
        b"(Line two) Tj T* "
        b"(Line three) Tj "
        b"ET"
    )
    compressed = zlib.compress(content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, compressed)
    b.add_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>")
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_text_differences(path):
    """/Differences remaps byte 0x41 ('A') to /bullet, proving Differences
    + AGL glyph-name resolution (not just the base WinAnsi table)."""
    b = PdfBuilder()
    content = b"BT /F1 12 Tf 72 700 Td (A) Tj ET"
    compressed = zlib.compress(content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, compressed)
    b.add_object(
        5,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding << /BaseEncoding /WinAnsiEncoding "
        b"/Differences [65 /bullet] >> >>",
    )
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_text_cid_identity(path):
    """Type0/Identity-H composite font with an embedded ToUnicode CMap
    (bfchar) mapping 5 two-byte codes to the letters of "HELLO"."""
    b = PdfBuilder()
    codes = bytes([0, 1, 0, 2, 0, 3, 0, 4, 0, 5])  # 2-byte codes 1..5
    content = b"BT /F1 12 Tf 72 700 Td <" + codes.hex().encode("ascii") + b"> Tj ET"
    compressed = zlib.compress(content)

    tounicode = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\nbegincmap\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"5 beginbfchar\n"
        b"<0001> <0048>\n<0002> <0045>\n<0003> <004C>\n"
        b"<0004> <004C>\n<0005> <004F>\n"
        b"endbfchar\nendcmap\nend\nend"
    )

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, compressed)
    b.add_object(
        5,
        b"<< /Type /Font /Subtype /Type0 /BaseFont /TestCID "
        b"/Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
    )
    b.add_object(
        6,
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestCID "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
        b"/Supplement 0 >> /DW 1000 >>",
    )
    b.add_stream_object(7, {}, tounicode)
    b.write_classic_xref_and_trailer(size=8, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_text_form_xobject(path):
    """Page content invokes a Form XObject (via `cm`+`Do`) that itself
    shows text, to test recursive interpretation and CTM composition."""
    b = PdfBuilder()
    page_content = b"q 1 0 0 1 10 20 cm /Fm1 Do Q"
    form_content = b"BT /F1 12 Tf 0 0 Td (In form) Tj ET"
    page_compressed = zlib.compress(page_content)
    form_compressed = zlib.compress(form_content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_compressed)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject",
            "Subtype": "/Form",
            "BBox": "[0 0 200 100]",
            "Resources": "<< /Font << /F1 6 0 R >> >>",
            "Filter": "/FlateDecode",
        },
        form_compressed,
    )
    b.add_object(6, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>")
    b.write_classic_xref_and_trailer(size=7, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_text_inline_image(path):
    """An inline image (BI/ID/EI) sits between two text-showing operators,
    to test that it's correctly skipped without corrupting the token
    stream around it."""
    b = PdfBuilder()
    image_data = bytes(range(12))  # 4x1 px, 3 components/px, 1 byte each
    content = (
        b"BT /F1 12 Tf 72 700 Td (Before) Tj ET "
        b"q 10 0 0 10 0 0 cm "
        b"BI /W 4 /H 1 /BPC 8 /CS /RGB ID " + image_data + b" EI Q "
        b"BT /F1 12 Tf 72 680 Td (After) Tj ET"
    )
    compressed = zlib.compress(content)

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, compressed)
    b.add_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>")
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_image_flate_rgb(path):
    """A page painting a single 2x2 FlateDecode DeviceRGB image XObject —
    red/green/blue/white pixels, so extraction can be checked pixel-exact."""
    b = PdfBuilder()
    page_content = zlib.compress(b"q 100 0 0 100 0 0 cm /Im1 Do Q")
    image_data = zlib.compress(bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]))

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_content)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject", "Subtype": "/Image",
            "Width": 2, "Height": 2, "ColorSpace": "/DeviceRGB",
            "BitsPerComponent": 8, "Filter": "/FlateDecode",
        },
        image_data,
    )
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_image_mask(path):
    """An 8x1 stencil mask (/ImageMask true) painted with the current fill
    color — alternating on/off bits, 1 byte covers all 8 columns."""
    b = PdfBuilder()
    page_content = zlib.compress(b"q 1 0 0 rg 100 0 0 100 0 0 cm /Im1 Do Q")
    image_data = bytes([0b10101010])

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_content)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject", "Subtype": "/Image",
            "Width": 8, "Height": 1, "ImageMask": "true",
        },
        image_data,
    )
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_image_unsupported_codec(path):
    """A CCITTFaxDecode image — no decoder exists, so `extract_image()`
    should raise cleanly rather than misinterpret the (fake) fax data."""
    b = PdfBuilder()
    page_content = zlib.compress(b"q 100 0 0 100 0 0 cm /Im1 Do Q")
    image_data = bytes([0x00, 0xFF, 0x00, 0xFF])  # not real fax data; filter is never decoded

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_content)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject", "Subtype": "/Image",
            "Width": 8, "Height": 1, "BitsPerComponent": 1,
            "ColorSpace": "/DeviceGray", "Filter": "/CCITTFaxDecode",
        },
        image_data,
    )
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_image_in_form(path):
    """The page's own /Resources has no /XObject at all — the only image
    lives inside a Form XObject's own /Resources, reachable only when
    `get_images(full=True)` recurses into forms."""
    b = PdfBuilder()
    page_content = zlib.compress(b"q /Fm1 Do Q")
    form_content = zlib.compress(b"q 50 0 0 50 0 0 cm /Im1 Do Q")
    image_data = zlib.compress(bytes([128, 64, 32]))

    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_content)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject", "Subtype": "/Form",
            "BBox": "[0 0 50 50]",
            "Resources": "<< /XObject << /Im1 6 0 R >> >>",
            "Filter": "/FlateDecode",
        },
        form_content,
    )
    b.add_stream_object(
        6,
        {
            "Type": "/XObject", "Subtype": "/Image",
            "Width": 1, "Height": 1, "ColorSpace": "/DeviceRGB",
            "BitsPerComponent": 8, "Filter": "/FlateDecode",
        },
        image_data,
    )
    b.write_classic_xref_and_trailer(size=7, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


def build_cropbox(path):
    """Page 1: 400x400 MediaBox cropped to [100 100 300 250], with a red
    50x50 square in the crop's bottom-left corner. Page 2: a CropBox that
    overhangs the MediaBox on every side (must be clipped to it)."""
    b = PdfBuilder()
    content = zlib.compress(b"1 0 0 rg 100 100 50 50 re f")
    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 400 400] >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /CropBox [100 100 300 250] /Contents 5 0 R >>",
    )
    b.add_object(
        4,
        b"<< /Type /Page /Parent 2 0 R /CropBox [-50 -50 500 500] /Contents 5 0 R >>",
    )
    b.add_stream_object(5, {"Filter": "/FlateDecode"}, content)
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    with open(path, "wb") as f:
        f.write(bytes(b.buf))


if __name__ == "__main__":
    import sys

    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    build_classic_simple(os.path.join(out_dir, "classic_simple.pdf"))
    build_two_page_inherited(os.path.join(out_dir, "two_page_inherited.pdf"))
    build_xref_stream(os.path.join(out_dir, "xref_stream.pdf"))
    build_lzw_content(os.path.join(out_dir, "lzw_content.pdf"))
    build_text_multiline(os.path.join(out_dir, "text_multiline.pdf"))
    build_text_differences(os.path.join(out_dir, "text_differences.pdf"))
    build_text_cid_identity(os.path.join(out_dir, "text_cid_identity.pdf"))
    build_text_form_xobject(os.path.join(out_dir, "text_form_xobject.pdf"))
    build_text_inline_image(os.path.join(out_dir, "text_inline_image.pdf"))
    build_image_flate_rgb(os.path.join(out_dir, "image_flate_rgb.pdf"))
    build_image_mask(os.path.join(out_dir, "image_mask.pdf"))
    build_image_unsupported_codec(os.path.join(out_dir, "image_unsupported_codec.pdf"))
    build_image_in_form(os.path.join(out_dir, "image_in_form.pdf"))
    build_cropbox(os.path.join(out_dir, "cropbox.pdf"))
    print("wrote fixtures to", out_dir)
