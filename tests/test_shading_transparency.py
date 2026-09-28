"""Shadings, patterns, blend modes and soft masks, checked pixel by pixel
against values worked out from the formulas in ISO 32000-1 (sections
8.7.3-8.7.4 and 11.3-11.6)."""

import zlib

import pytest

import clearleaf as pdf
from clearleaf._blend import blend


def build(content: bytes, resources: bytes = b"", objects=(), size=(100, 100)) -> bytes:
    """One page; extra ``objects`` are numbered from 5."""
    body = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R /Resources << %s >> >>"
        % (size[0], size[1], resources),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        *objects,
    ]
    out, offsets = b"%PDF-1.7\n", []
    for i, obj in enumerate(body, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(body) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(body) + 1, xref)
    return out


def stream(d: bytes, data: bytes) -> bytes:
    return b"<< " + d + b" /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def render(data: bytes):
    pix = pdf.open(data)[0].get_pixmap(dpi=72)

    def at(x, y_from_bottom):  # PDF coordinates on a 72 dpi page
        y = pix.height - 1 - int(y_from_bottom)
        i = (y * pix.width + int(x)) * 3
        return tuple(pix.samples[i : i + 3])

    return at


def close(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


RED_TO_BLUE = b"<< /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >>"


def test_axial_shading_interpolates_along_its_axis():
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>",
                      [b"<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] /Function " + RED_TO_BLUE + b" >>"]))
    assert close(at(1, 50), (255, 0, 0), 8)
    assert close(at(50, 50), (128, 0, 128), 8)
    assert close(at(98, 50), (0, 0, 255), 8)


def test_axial_shading_extend():
    coords = b"/Coords [40 0 60 0]"
    base = b"<< /ShadingType 2 /ColorSpace /DeviceRGB " + coords + b" /Function " + RED_TO_BLUE
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>", [base + b" >>"]))
    assert at(10, 50) == (255, 255, 255)  # no Extend: nothing beyond the ends
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>", [base + b" /Extend [true true] >>"]))
    assert close(at(10, 50), (255, 0, 0)) and close(at(90, 50), (0, 0, 255))


def test_radial_shading():
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>",
                      [b"<< /ShadingType 3 /ColorSpace /DeviceRGB /Coords [50 50 0 50 50 40] /Function "
                       + RED_TO_BLUE + b" >>"]))
    assert close(at(50, 50), (255, 0, 0), 12)          # centre: t = 0
    assert close(at(70, 50), (128, 0, 128), 12)        # half way out
    assert at(95, 95) == (255, 255, 255)               # outside the outer circle


def test_function_based_shading():
    fn = b"<< /FunctionType 4 /Domain [0 1 0 1] /Range [0 1 0 1 0 1] /Length 12 >>\nstream\n{ pop 0 0 }\nendstream"
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>",
                      [b"<< /ShadingType 1 /ColorSpace /DeviceRGB /Matrix [100 0 0 100 0 0] /Function 6 0 R >>", fn]))
    # colour = (x, 0, 0) over the unit square scaled to the page
    assert close(at(25, 50), (64, 0, 0), 8) and close(at(75, 50), (191, 0, 0), 8)


def _packed(values, bits):
    out, acc, n = bytearray(), 0, 0
    for v, b in values:
        acc = (acc << b) | v
        n += b
        while n >= 8:
            n -= 8
            out.append((acc >> n) & 0xFF)
    if n:
        out.append((acc << (8 - n)) & 0xFF)
    return bytes(out)


def test_free_form_triangle_mesh_is_gouraud_shaded():
    # one triangle: red at (0,0), green at (100,0), blue at (0,100); 8-bit everything
    verts = [(0, 0, 0, (255, 0, 0)), (0, 255, 0, (0, 255, 0)), (0, 0, 255, (0, 0, 255))]
    data = _packed([(v, 8) for f, x, y, c in verts for v in (f, x, y, *c)], 8)
    shading = stream(b"/ShadingType 4 /ColorSpace /DeviceRGB /BitsPerCoordinate 8 /BitsPerComponent 8 "
                     b"/BitsPerFlag 8 /Decode [0 100 0 100 0 1 0 1 0 1]", data)
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>", [shading]))
    assert close(at(2, 2), (250, 3, 3), 10)
    assert close(at(33, 33), (85, 85, 85), 10)   # the centroid mixes all three
    assert at(80, 80) == (255, 255, 255)          # outside the triangle


def test_coons_patch_mesh_fills_its_boundary():
    # a patch whose boundary is the square (10,10)-(90,90), straight edges
    pts = [(10, 10), (10, 37), (10, 63), (10, 90), (37, 90), (63, 90), (90, 90), (90, 63), (90, 37),
           (90, 10), (63, 10), (37, 10)]
    colors = [(255, 0, 0), (255, 0, 0), (0, 0, 255), (0, 0, 255)]  # c00 c03 c33 c30
    values = [(0, 8)] + [(v, 8) for p in pts for v in p] + [(v, 8) for c in colors for v in c]
    shading = stream(b"/ShadingType 6 /ColorSpace /DeviceRGB /BitsPerCoordinate 8 /BitsPerComponent 8 "
                     b"/BitsPerFlag 8 /Decode [0 255 0 255 0 1 0 1 0 1]", _packed(values, 8))
    at = render(build(b"/Sh1 sh", b"/Shading << /Sh1 5 0 R >>", [shading]))
    assert close(at(15, 50), (240, 0, 15), 20)     # near u = 0: red
    assert close(at(85, 50), (15, 0, 240), 20)     # near u = 1: blue
    assert at(5, 50) == (255, 255, 255)            # outside the patch


def test_shading_pattern_fills_a_path():
    at = render(build(b"/Pattern cs /P1 scn 0 0 50 100 re f",
                      b"/Pattern << /P1 5 0 R >>",
                      [b"<< /PatternType 2 /Shading << /ShadingType 2 /ColorSpace /DeviceRGB "
                       b"/Coords [0 0 100 0] /Function " + RED_TO_BLUE + b" >> >>"]))
    assert close(at(25, 50), (191, 0, 64), 10)
    assert at(75, 50) == (255, 255, 255)  # outside the filled path


def test_colored_tiling_pattern_repeats():
    tile = stream(b"/PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 /Resources << >>",
                  b"1 0 0 rg 0 0 10 10 re f 0 0 1 rg 10 10 10 10 re f")
    at = render(build(b"/Pattern cs /P1 scn 0 0 100 100 re f", b"/Pattern << /P1 5 0 R >>", [tile]))
    for ox in (0, 20, 40):
        assert close(at(ox + 5, 5), (255, 0, 0)) and close(at(ox + 15, 15), (0, 0, 255))
        assert at(ox + 15, 5) == (255, 255, 255)  # transparent part of the tile


def test_uncolored_tiling_pattern_takes_its_colour_from_scn():
    tile = stream(b"/PatternType 1 /PaintType 2 /TilingType 1 /BBox [0 0 10 10] /XStep 20 /YStep 20 /Resources << >>",
                  b"0 0 10 10 re f")
    at = render(build(b"[/Pattern /DeviceRGB] cs 0 1 0 /P1 scn 0 0 100 100 re f",
                      b"/Pattern << /P1 5 0 R >>", [tile]))
    assert close(at(5, 5), (0, 255, 0)) and at(15, 5) == (255, 255, 255)


@pytest.mark.parametrize("mode,backdrop,source,expected", [
    ("Multiply", (255, 255, 0), (255, 0, 0), (255, 0, 0)),
    ("Screen", (255, 0, 0), (0, 0, 255), (255, 0, 255)),
    ("Darken", (200, 100, 50), (100, 200, 50), (100, 100, 50)),
    ("Lighten", (200, 100, 50), (100, 200, 50), (200, 200, 50)),
    ("Difference", (255, 255, 255), (255, 0, 0), (0, 255, 255)),
    ("Exclusion", (255, 255, 255), (255, 0, 0), (0, 255, 255)),
    ("Luminosity", (255, 0, 0), (0, 0, 0), (0, 0, 0)),
    # SetLum(red, 0.502) = (1.202, .202, .202), ClipColor -> (1, .289, .289)
    ("Color", (128, 128, 128), (255, 0, 0), (255, 74, 74)),
])
def test_blend_functions(mode, backdrop, source, expected):
    assert close(blend(mode, backdrop, source), expected, 2)


def test_blend_mode_from_extgstate():
    at = render(build(b"1 1 0 rg 0 0 100 100 re f /GS1 gs 1 0 0 rg 0 0 50 100 re f 0 0 1 rg 50 0 50 100 re f",
                      b"/ExtGState << /GS1 << /BM /Multiply >> >>"))
    assert at(25, 50) == (255, 0, 0)   # red x yellow
    assert at(75, 50) == (0, 0, 0)     # blue x yellow


def test_luminosity_soft_mask_from_extgstate():
    mask_form = stream(b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Group << /S /Transparency /CS /DeviceGray >>",
                       b"0.5 g 0 0 50 100 re f 1 g 50 0 50 100 re f")
    at = render(build(b"/SM gs 0 0 1 rg 0 0 100 100 re f",
                      b"/ExtGState << /SM << /SMask << /S /Luminosity /G 5 0 R /BC [0] >> >> >>", [mask_form]))
    assert close(at(25, 50), (128, 128, 255), 3)   # half-strength blue
    assert at(75, 50) == (0, 0, 255)


def test_image_soft_mask():
    # a 2x1 black image whose /SMask makes the left pixel transparent
    smask = stream(b"/Type /XObject /Subtype /Image /Width 2 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8",
                   bytes([0, 255]))
    image = stream(b"/Type /XObject /Subtype /Image /Width 2 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 "
                   b"/SMask 6 0 R", bytes([0, 0]))
    at = render(build(b"q 100 0 0 100 0 0 cm /Im1 Do Q", b"/XObject << /Im1 5 0 R >>", [image, smask]))
    assert at(25, 50) == (255, 255, 255) and at(75, 50) == (0, 0, 0)


def test_stencil_mask_on_an_image():
    mask = stream(b"/Type /XObject /Subtype /Image /Width 2 /Height 1 /ImageMask true", bytes([0b10000000]))
    image = stream(b"/Type /XObject /Subtype /Image /Width 2 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 "
                   b"/Mask 6 0 R", bytes([0, 0]))
    at = render(build(b"q 100 0 0 100 0 0 cm /Im1 Do Q", b"/XObject << /Im1 5 0 R >>", [image, mask]))
    assert at(25, 50) == (255, 255, 255) and at(75, 50) == (0, 0, 0)  # sample 1 = masked out
