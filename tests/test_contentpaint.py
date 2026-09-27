import zlib

from clearleaf._contentpaint import ContentPainter
from clearleaf._objects import PDFName, PDFStream
from clearleaf._raster import Canvas


class FakeDoc:
    def resolve(self, obj):
        return obj


def pixel(canvas, x, y):
    i = (y * canvas.width + x) * 3
    return tuple(canvas.pixels[i : i + 3])


def paint(content: bytes, width=100, height=100, resources=None, flip_y=True):
    canvas = Canvas(width, height)
    # Simple top-left-origin CTM (no PDF Y-flip) unless flip_y requested,
    # to keep most path-geometry tests' coordinates intuitive; flip_y=True
    # uses a real PDF-style flip (origin bottom-left, Y up) for tests that
    # care about matching real page rendering.
    ctm = (1.0, 0.0, 0.0, -1.0, 0.0, float(height)) if flip_y else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    painter = ContentPainter(FakeDoc(), canvas, resources or {}, ctm)
    painter.run(content)
    return canvas


def test_rectangle_fill():
    canvas = paint(b"1 0 0 rg 10 10 30 30 re f", flip_y=False)
    assert pixel(canvas, 20, 20) == (255, 0, 0)
    assert pixel(canvas, 50, 50) == (255, 255, 255)


def test_rectangle_stroke():
    canvas = paint(b"0 0 1 RG 2 w 10 10 30 30 re S", flip_y=False)
    assert pixel(canvas, 10, 20) == (0, 0, 255)  # left edge
    assert pixel(canvas, 25, 25) == (255, 255, 255)  # interior: not filled


def test_fill_and_stroke_together():
    canvas = paint(b"0 1 0 rg 1 0 0 RG 2 w 10 10 30 30 re B", flip_y=False)
    assert pixel(canvas, 25, 25) == (0, 255, 0)  # interior fill
    assert pixel(canvas, 10, 25) == (255, 0, 0)  # edge stroke


def test_line_stroke_with_width():
    canvas = paint(b"0 0 0 RG 6 w 10 50 m 90 50 l S", flip_y=False)
    assert pixel(canvas, 50, 50) == (0, 0, 0)
    assert pixel(canvas, 50, 10) == (255, 255, 255)


def test_bezier_curve_fill():
    # A curve that bulges through y~80 near x=50; check it actually curves
    # (a pixel well above the straight chord gets painted).
    content = b"0 0 0 rg 10 20 m 10 80 90 80 90 20 c 90 20 l 10 20 l f"
    canvas = paint(content, flip_y=False)
    assert pixel(canvas, 50, 60) == (0, 0, 0)


def test_nonzero_vs_evenodd_fill_rule():
    # Two same-direction overlapping rectangles as one path (via two `re`
    # ops before a single fill) — nonzero fills solid, evenodd punches a
    # hole in the overlap. (`re` always winds counter-clockwise per spec,
    # so both rects share direction here.)
    content = b"0 0 0 rg 10 10 40 40 re 25 25 40 40 re {op}"
    canvas_nz = paint(content.replace(b"{op}", b"f"), flip_y=False)
    canvas_eo = paint(content.replace(b"{op}", b"f*"), flip_y=False)
    assert pixel(canvas_nz, 35, 35) == (0, 0, 0)  # overlap: solid under nonzero
    assert pixel(canvas_eo, 35, 35) == (255, 255, 255)  # overlap: hole under evenodd


def test_clip_restricts_subsequent_fill():
    content = b"10 10 30 30 re W n 0 0 0 rg 0 0 100 100 re f"
    canvas = paint(content, flip_y=False)
    assert pixel(canvas, 20, 20) == (0, 0, 0)  # inside clip
    assert pixel(canvas, 60, 60) == (255, 255, 255)  # outside clip


def test_clip_is_restored_by_q_Q():
    content = b"q 10 10 30 30 re W n Q 0 0 0 rg 0 0 100 100 re f"
    canvas = paint(content, flip_y=False)
    # Clip set inside q/Q shouldn't survive the Q, so the big fill covers everything.
    assert pixel(canvas, 60, 60) == (0, 0, 0)


def test_cm_affects_subsequent_path_construction():
    content = b"1 0 0 1 20 20 cm 0 0 0 rg 0 0 10 10 re f"
    canvas = paint(content, flip_y=False)
    assert pixel(canvas, 25, 25) == (0, 0, 0)  # translated by cm
    assert pixel(canvas, 5, 5) == (255, 255, 255)


def test_q_Q_restores_ctm():
    content = b"q 1 0 0 1 50 50 cm Q 0 0 0 rg 0 0 10 10 re f"
    canvas = paint(content, flip_y=False)
    assert pixel(canvas, 5, 5) == (0, 0, 0)  # back at origin, not translated


def test_cmyk_color():
    canvas = paint(b"1 0 0 0 k 0 0 50 50 re f", flip_y=False)  # pure cyan
    assert pixel(canvas, 10, 10) == (0, 255, 255)


def test_gray_color():
    canvas = paint(b"0.5 g 0 0 50 50 re f", flip_y=False)
    assert pixel(canvas, 10, 10) == (128, 128, 128)


def test_constant_alpha_via_extgstate():
    resources = {"ExtGState": {"GS1": {"ca": 0.5}}}
    content = b"/GS1 gs 0 0 0 rg 0 0 100 100 re f"
    canvas = paint(content, resources=resources, flip_y=False)
    r, g, b = pixel(canvas, 10, 10)
    assert 120 <= r <= 135  # ~half-blended black onto white


def test_text_rendering_paints_glyph_pixels():
    resources = {"Font": {"F1": {"Subtype": PDFName("Type1"), "BaseFont": PDFName("Helvetica")}}}
    content = b"BT /F1 40 Tf 0 0 0 rg 10 30 Td (I) Tj ET"
    canvas = paint(content, width=100, height=100, resources=resources, flip_y=False)
    # The glyph "I" should paint *something* black in its expected column band.
    painted = any(
        pixel(canvas, x, y) == (0, 0, 0)
        for y in range(20, 70)
        for x in range(8, 30)
    )
    assert painted


def test_text_render_mode_3_is_invisible_but_still_advances():
    resources = {"Font": {"F1": {"Subtype": PDFName("Type1"), "BaseFont": PDFName("Helvetica")}}}
    content = b"BT /F1 40 Tf 3 Tr 0 0 0 rg 10 30 Td (W) Tj ET"
    canvas = paint(content, width=100, height=100, resources=resources, flip_y=False)
    assert all(
        pixel(canvas, x, y) == (255, 255, 255)
        for y in range(20, 70)
        for x in range(8, 40)
    )


def test_raw_rgb_image_paints_correct_colors():
    img_data = bytes([255, 0, 0, 0, 255, 0])  # 2x1 image: red, green
    stream = PDFStream(
        {
            "Subtype": PDFName("Image"),
            "Width": 2, "Height": 1, "ColorSpace": PDFName("DeviceRGB"),
            "BitsPerComponent": 8,
        },
        img_data,
    )
    resources = {"XObject": {"Im1": stream}}
    content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    canvas = paint(content, resources=resources, flip_y=False)
    assert pixel(canvas, 25, 50) == (255, 0, 0)
    assert pixel(canvas, 75, 50) == (0, 255, 0)


def test_image_mask_uses_current_fill_color():
    # 8x1 bits, MSB first. Per the default /Decode [0 1] for image masks,
    # sample 0 means "paint" and 1 means "leave unchanged" (ISO 32000-1
    # 8.9.6.2) — so 0111 1111 paints only the first pixel.
    mask_data = bytes([0b01111111])
    stream = PDFStream(
        {"Subtype": PDFName("Image"), "Width": 8, "Height": 1, "ImageMask": True},
        mask_data,
    )
    resources = {"XObject": {"Im1": stream}}
    content = b"0 0 1 rg q 80 0 0 80 0 0 cm /Im1 Do Q"
    canvas = paint(content, resources=resources, flip_y=False)
    assert pixel(canvas, 5, 40) == (0, 0, 255)
    assert pixel(canvas, 75, 40) == (255, 255, 255)


def test_form_xobject_is_painted_with_composed_ctm():
    form_content = b"0 0 0 rg 0 0 10 10 re f"
    form_stream = PDFStream(
        {"Type": PDFName("XObject"), "Subtype": PDFName("Form"), "BBox": [0, 0, 10, 10]},
        form_content,
    )
    resources = {"XObject": {"Fm1": form_stream}}
    content = b"1 0 0 1 30 30 cm /Fm1 Do"
    canvas = paint(content, resources=resources, flip_y=False)
    assert pixel(canvas, 35, 35) == (0, 0, 0)
    assert pixel(canvas, 5, 5) == (255, 255, 255)


def test_pdf_style_y_flip_matches_real_page_rendering():
    # With a real PDF-style flip (origin bottom-left), content drawn near
    # y=0 in PDF space should land near the BOTTOM of the raster (high
    # pixel-row index), not the top.
    canvas = paint(b"0 0 0 rg 10 10 20 20 re f", width=100, height=100, flip_y=True)
    assert pixel(canvas, 15, 85) == (0, 0, 0)  # near bottom of the raster
    assert pixel(canvas, 15, 15) == (255, 255, 255)  # near top: untouched


def test_end_to_end_get_pixmap_via_document():
    import clearleaf as pdf

    content = b"0 0 1 rg 10 10 50 50 re f"
    compressed = zlib.compress(content)
    buf = bytearray(b"%PDF-1.7\n")
    offsets = {}

    def add(num, body):
        offsets[num] = len(buf)
        buf.extend(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>")
    offsets[4] = len(buf)
    buf += (
        f"4 0 obj\n<< /Filter /FlateDecode /Length {len(compressed)} >>\nstream\n".encode()
        + compressed
        + b"\nendstream\nendobj\n"
    )
    xref_off = len(buf)
    buf += b"xref\n0 5\n0000000000 65535 f \n"
    for n in range(1, 5):
        buf += f"{offsets[n]:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_off}\n%%EOF\n".encode()

    doc = pdf.open(bytes(buf))
    pix = doc[0].get_pixmap(dpi=72)
    assert pix.width == 100 and pix.height == 100
    assert pix.n == 3
    i = (70 * pix.width + 30) * 3  # inside the filled rect (near page bottom in pixel space)
    assert pix.samples[i : i + 3] == bytes([0, 0, 255])
    doc.close()


def test_get_pixmap_rotation_swaps_dimensions():
    import clearleaf as pdf

    content = b""
    compressed = zlib.compress(content)
    buf = bytearray(b"%PDF-1.7\n")
    offsets = {}

    def add(num, body):
        offsets[num] = len(buf)
        buf.extend(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 200] /Rotate 90 /Contents 4 0 R >>")
    offsets[4] = len(buf)
    buf += (
        f"4 0 obj\n<< /Filter /FlateDecode /Length {len(compressed)} >>\nstream\n".encode()
        + compressed
        + b"\nendstream\nendobj\n"
    )
    xref_off = len(buf)
    buf += b"xref\n0 5\n0000000000 65535 f \n"
    for n in range(1, 5):
        buf += f"{offsets[n]:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_off}\n%%EOF\n".encode()

    doc = pdf.open(bytes(buf))
    pix = doc[0].get_pixmap(dpi=72)
    assert (pix.width, pix.height) == (200, 100)  # swapped for a 90-degree rotation
    doc.close()
