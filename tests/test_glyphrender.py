import os

from clearleaf._glyphrender import contour_to_polygon, glyph_outline_to_device_polygons
from clearleaf._raster import Canvas, fill_shape
from clearleaf._stdfonts import substitute_font

FONTDATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "clearleaf", "_fontdata",
)


def identity_transform(x, y):
    return (x, y)


def test_contour_to_polygon_all_on_curve_is_passthrough_lines():
    points = [(0, 0, True), (10, 0, True), (10, 10, True), (0, 10, True)]
    poly = contour_to_polygon(points, identity_transform)
    assert poly[0] == (0, 0)
    assert (10, 0) in poly
    assert (10, 10) in poly
    assert (0, 10) in poly


def test_contour_to_polygon_quadratic_bulges_through_control_point():
    # on, off (control), on: a simple upward-bulging quadratic arc. The
    # contour is implicitly closed back to the start, so poly[-1] duplicates
    # poly[0] (a valid closed-polygon representation) rather than being the
    # curve's other endpoint — check the curve passed through (10, 0)
    # instead of asserting it's the literal last point.
    points = [(0, 0, True), (5, 10, False), (10, 0, True)]
    poly = contour_to_polygon(points, identity_transform)
    assert poly[0] == (0, 0)
    assert poly[0] == poly[-1]  # closed loop
    assert any(abs(x - 10) < 1e-6 and abs(y) < 1e-6 for x, y in poly)
    assert any(y > 1.0 for _x, y in poly)  # actually bulges, not a straight line


def test_contour_to_polygon_implied_on_curve_midpoint():
    # Two consecutive off-curve points: TrueType implies an on-curve point
    # at their midpoint, splitting into two quadratic segments.
    points = [(0, 0, True), (5, 10, False), (10, 10, False), (15, 0, True)]
    poly = contour_to_polygon(points, identity_transform)
    assert poly[0] == (0, 0)
    assert poly[0] == poly[-1]  # closed loop
    assert any(abs(x - 15) < 1e-6 and abs(y) < 1e-6 for x, y in poly)
    assert any(y > 5.0 for _x, y in poly)


def test_contour_to_polygon_all_off_curve_synthesizes_start():
    # A stylized "circle" with 4 off-curve-only points is valid TrueType.
    points = [(10, 0, False), (0, 10, False), (-10, 0, False), (0, -10, False)]
    poly = contour_to_polygon(points, identity_transform)
    assert len(poly) > 4  # actually flattened into curve segments, not just 4 corners


def test_real_liberation_sans_glyph_A_renders_two_contours_with_a_hole():
    font = substitute_font("Helvetica", bold=False, italic=False)
    gid = None
    for platform_id, encoding_id, table in font.cmap_subtables:
        if (platform_id, encoding_id) == (3, 1):
            gid = table.get(ord("A"))
            break
    assert gid is not None

    scale = 200.0 / font.units_per_em

    def transform(x, y):
        return (x * scale + 10, 220 - y * scale)  # flip Y into raster space

    polygons = glyph_outline_to_device_polygons(font, gid, transform)
    assert len(polygons) == 2  # outer contour + the triangular counter

    canvas = Canvas(240, 240)
    fill_shape(canvas, polygons, (0, 0, 0), evenodd=False)

    def pixel(x, y):
        i = (y * canvas.width + x) * 3
        return tuple(canvas.pixels[i : i + 3])

    # Bottom-left leg of the 'A' should be painted...
    assert pixel(20, 210) == (0, 0, 0)
    # ...but the counter (the hole inside the crossbar) should not be.
    assert pixel(76, 140) == (255, 255, 255)
    # And well outside the glyph entirely should be untouched.
    assert pixel(5, 5) == (255, 255, 255)
