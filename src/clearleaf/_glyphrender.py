"""Bridges glyph outlines -- TrueType on/off-curve contour points
(`_truetype.py`) and Type 1/CFF cubic paths (`_type1.py`) -- to flattened
device-space polygons `_raster.py` can fill.

A TrueType simple-glyph contour is a *quadratic* Bezier spline encoded
implicitly: on-curve points are spline vertices, off-curve points are
control points, and two consecutive off-curve points imply an on-curve
point at their midpoint (so a contour can be all off-curve points, in the
rare case of e.g. a perfect circle approximated by four such implied
points). This is a standard, well-documented property of the format
(OpenType spec, `glyf` table) — not derived from any particular renderer's
implementation.
"""

from __future__ import annotations

from ._raster import flatten_cubic, flatten_quadratic


def contour_to_polygon(points: list, transform_fn, tolerance: float = 0.3) -> list:
    """``points``: (x, y, on_curve) in font units, describing one closed
    contour (implicitly closed back to the first point). Returns a flat
    list of device-space (x, y) points."""
    n = len(points)
    if n == 0:
        return []

    start_idx = next((i for i, (_x, _y, on) in enumerate(points) if on), None)
    if start_idx is None:
        fx, fy, _ = points[0]
        lx, ly, _ = points[-1]
        points = [((fx + lx) / 2.0, (fy + ly) / 2.0, True)] + points
        start_idx = 0

    ordered = points[start_idx:] + points[:start_idx]
    ordered.append(ordered[0])

    cur = (ordered[0][0], ordered[0][1])
    poly = [transform_fn(*cur)]
    i = 1
    while i < len(ordered):
        x, y, on = ordered[i]
        if on:
            poly.append(transform_fn(x, y))
            cur = (x, y)
            i += 1
            continue

        if i + 1 < len(ordered):
            nx, ny, next_on = ordered[i + 1]
        else:
            nx, ny, next_on = ordered[0][0], ordered[0][1], True

        if next_on:
            end = (nx, ny)
            consumed = 2
        else:
            end = ((x + nx) / 2.0, (y + ny) / 2.0)
            consumed = 1

        p0 = transform_fn(*cur)
        p1 = transform_fn(x, y)
        p2 = transform_fn(*end)
        flat = flatten_quadratic(p0, p1, p2, tolerance)
        poly.extend(flat[1:])  # p0 already the last point in `poly`
        cur = end
        i += consumed
    return poly


def glyph_outline_to_device_polygons(font, glyph_id: int, transform_fn) -> list:
    """Return device-space subpaths for ``glyph_id``, ready for
    `_raster.fill_shape` (nonzero winding — TrueType's own fill rule)."""
    contours = font.glyph_outline(glyph_id)
    if not contours:
        return []
    polygons = []
    for contour in contours:
        poly = contour_to_polygon(contour, transform_fn)
        if len(poly) >= 3:
            polygons.append(poly)
    return polygons


def path_to_device_polygons(path, transform_fn, tolerance: float = 0.3) -> list:
    """Cubic-Bezier glyph paths (Type 1 / CFF charstring outlines, see
    `_type1.py`) to device-space subpaths for `_raster.fill_shape`.
    Each subpath is [("M", x, y), ("L", x, y) | ("C", x1, y1, x2, y2, x3, y3), ...]
    in glyph space and is implicitly closed; Type 1 outlines use the
    nonzero winding rule, like TrueType."""
    polygons = []
    for subpath in path:
        poly = []
        for seg in subpath:
            if seg[0] in ("M", "L"):
                poly.append(transform_fn(seg[1], seg[2]))
            else:
                p0 = poly[-1] if poly else transform_fn(0.0, 0.0)
                p1 = transform_fn(seg[1], seg[2])
                p2 = transform_fn(seg[3], seg[4])
                p3 = transform_fn(seg[5], seg[6])
                poly.extend(flatten_cubic(p0, p1, p2, p3, tolerance)[1:])
        if len(poly) >= 3:
            polygons.append(poly)
    return polygons
