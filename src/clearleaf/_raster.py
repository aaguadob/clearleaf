"""Software rasterizer: scanline polygon fill with anti-aliasing, a simple
stroker, and clip masks — no rendering library involved, per the project's
from-scratch-over-dependency policy.

Anti-aliasing strategy: exact (analytic) coverage in X per sub-scanline,
supersampled in Y. For each of `ss` sub-scanlines per pixel row, compute
the real-valued x-intervals where the polygon is "inside" (per the nonzero
or even-odd rule), then add each interval's exact fractional overlap with
every pixel column it touches. Averaging over `ss` sub-scanlines gives
smooth AA without a full 2D supersampling pass. This is a standard
technique (e.g. FreeType's rasterizer is a more elaborate analytic version
of the same idea) — implemented independently from the PDF spec's geometry
rules, not from any specific rasterizer's source.

Coordinate convention: device-space pixels, X right, Y *down* (row 0 is the
top of the canvas) — the painter is responsible for flipping PDF's
Y-goes-up page space into this before calling anything here.
"""

from __future__ import annotations

import math
from typing import Optional

from . import _matrix as mx

Point = tuple  # (x, y)
Subpath = list  # list[Point]


class Canvas:
    def __init__(self, width: int, height: int, background=(255, 255, 255)):
        self.width = width
        self.height = height
        row = bytes(background) * width
        self.pixels = bytearray(row * height)

    def blend_row(self, y: int, coverage, rgb, clip_row: Optional[bytearray] = None) -> None:
        if y < 0 or y >= self.height:
            return
        r, g, b = rgb
        base = y * self.width * 3
        px = self.pixels
        w = self.width
        for x in range(w):
            a = coverage[x]
            if a <= 0.0:
                continue
            if clip_row is not None:
                a *= clip_row[x] / 255.0
                if a <= 0.0:
                    continue
            if a > 1.0:
                a = 1.0
            i = base + x * 3
            if a >= 0.999:
                px[i] = r
                px[i + 1] = g
                px[i + 2] = b
            else:
                inv = 1.0 - a
                px[i] = int(r * a + px[i] * inv)
                px[i + 1] = int(g * a + px[i + 1] * inv)
                px[i + 2] = int(b * a + px[i + 2] * inv)

    def to_ppm_bytes(self) -> bytes:
        header = f"P6\n{self.width} {self.height}\n255\n".encode("ascii")
        return header + bytes(self.pixels)


# -- edge list / scanline coverage -----------------------------------------


def _build_edges(subpaths):
    edges = []  # (ylo, yhi, x_at_ylo, dxdy, winding)
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for path in subpaths:
        n = len(path)
        if n < 2:
            continue
        for i in range(n):
            x0, y0 = path[i]
            x1, y1 = path[(i + 1) % n]
            minx = min(minx, x0, x1)
            maxx = max(maxx, x0, x1)
            miny = min(miny, y0, y1)
            maxy = max(maxy, y0, y1)
            if y0 == y1:
                continue
            winding = 1 if y1 > y0 else -1
            ylo, yhi = (y0, y1) if y0 < y1 else (y1, y0)
            dxdy = (x1 - x0) / (y1 - y0)
            x_at_ylo = x0 if y0 < y1 else x1
            edges.append((ylo, yhi, x_at_ylo, dxdy, winding))
    if not edges:
        return [], (0, 0, 0, 0)
    return edges, (minx, miny, maxx, maxy)


def _accumulate_span(coverage, x0: float, x1: float, weight: float, width: int) -> None:
    if x1 <= x0:
        return
    x0 = max(x0, 0.0)
    x1 = min(x1, float(width))
    if x1 <= x0:
        return
    ix0 = int(math.floor(x0))
    ix1 = int(math.floor(x1))
    if ix0 == ix1:
        if ix0 < width:
            coverage[ix0] += (x1 - x0) * weight
        return
    if ix0 < width:
        coverage[ix0] += (ix0 + 1 - x0) * weight
    for ix in range(max(ix0 + 1, 0), min(ix1, width)):
        coverage[ix] += weight
    if 0 <= ix1 < width:
        coverage[ix1] += (x1 - ix1) * weight


def _row_coverage(edges, y: int, width: int, evenodd: bool, ss: int):
    """Coverage array (list[float], len==width) for pixel row ``y``."""
    coverage = [0.0] * width
    weight = 1.0 / ss
    for s in range(ss):
        sy = y + (s + 0.5) / ss
        crossings = []
        for ylo, yhi, x_at_ylo, dxdy, winding in edges:
            if ylo <= sy < yhi:
                crossings.append((x_at_ylo + (sy - ylo) * dxdy, winding))
        if not crossings:
            continue
        crossings.sort(key=lambda c: c[0])
        wind = 0
        count = 0
        for i in range(len(crossings) - 1):
            x_i, w_i = crossings[i]
            wind += w_i
            count += 1
            inside = (count % 2 == 1) if evenodd else (wind != 0)
            if inside:
                _accumulate_span(coverage, x_i, crossings[i + 1][0], weight, width)
    return coverage


def shape_coverage_rows(subpaths, width: int, height: int, evenodd: bool = False, ss: int = 4):
    """Return {row_y: coverage_list} for the rows the shape actually touches."""
    edges, (minx, miny, maxy_x, maxy) = _build_edges(subpaths)
    if not edges:
        return {}
    y0 = max(0, int(math.floor(miny)))
    y1 = min(height, int(math.ceil(maxy)))
    rows = {}
    for y in range(y0, y1):
        cov = _row_coverage(edges, y, width, evenodd, ss)
        if any(cov):
            rows[y] = cov
    return rows


def fill_shape(canvas: Canvas, subpaths, rgb, alpha: float = 1.0, evenodd: bool = False,
               clip: Optional["ClipMask"] = None, ss: int = 4) -> None:
    """Fill one shape (subpaths combined via the winding rule — e.g. a
    glyph's outer contour plus inner "hole" contours) directly onto the
    canvas."""
    rows = shape_coverage_rows(subpaths, canvas.width, canvas.height, evenodd, ss)
    for y, cov in rows.items():
        if alpha != 1.0:
            cov = [c * alpha for c in cov]
        clip_row = clip.row(y) if clip is not None else None
        canvas.blend_row(y, cov, rgb, clip_row)


def fill_shapes_union(canvas: Canvas, shapes, rgb, alpha: float = 1.0,
                       clip: Optional["ClipMask"] = None, ss: int = 4) -> None:
    """Fill the union of independent shapes (e.g. a stroke's per-segment
    quads and per-joint discs) as one blend pass, combining via max — so
    overlapping pieces don't cancel out the way a naive combined nonzero-
    winding fill would if their edge directions disagree."""
    combined: dict[int, list] = {}
    for subpaths in shapes:
        rows = shape_coverage_rows(subpaths, canvas.width, canvas.height, False, ss)
        for y, cov in rows.items():
            if y not in combined:
                combined[y] = cov
            else:
                dst = combined[y]
                for x in range(len(dst)):
                    if cov[x] > dst[x]:
                        dst[x] = cov[x]
    for y, cov in combined.items():
        if alpha != 1.0:
            cov = [c * alpha for c in cov]
        clip_row = clip.row(y) if clip is not None else None
        canvas.blend_row(y, cov, rgb, clip_row)


# -- clipping ---------------------------------------------------------------


def _ZERO_ROW(width: int) -> bytearray:
    return bytearray(width)


class ClipMask:
    """A per-pixel coverage mask (0-255) restricting where fills apply.
    Immutable once built — intersecting creates a new mask, so graphics
    states (q/Q) can cheaply share a reference instead of copying."""

    __slots__ = ("width", "height", "rows")

    def __init__(self, width: int, height: int, rows: Optional[dict] = None):
        self.width = width
        self.height = height
        self.rows = rows  # {y: bytearray(width)} sparse; missing row == fully clipped (0)

    @classmethod
    def full(cls, width: int, height: int) -> "ClipMask":
        return cls(width, height, rows=None)  # None rows == unclipped everywhere

    def row(self, y: int):
        if self.rows is None:
            return None  # caller treats None as "no restriction" (unclipped)
        # self.rows is a sparse dict of only the rows the clip shape(s)
        # actually touched — a row present but absent from it has zero
        # coverage (fully clipped), which is NOT the same as "unclipped".
        row = self.rows.get(y)
        return row if row is not None else _ZERO_ROW(self.width)

    def intersect_shape(self, subpaths, evenodd: bool = False, ss: int = 4) -> "ClipMask":
        new_rows_cov = shape_coverage_rows(subpaths, self.width, self.height, evenodd, ss)
        result: dict[int, bytearray] = {}
        if self.rows is None:
            for y, cov in new_rows_cov.items():
                result[y] = bytearray(min(255, max(0, round(c * 255))) for c in cov)
        else:
            for y, existing in self.rows.items():
                new_cov = new_rows_cov.get(y)
                if new_cov is None:
                    continue
                merged = bytearray(self.width)
                for x in range(self.width):
                    # existing[x] is a 0-255 byte fraction, new_cov[x] a 0-1
                    # float fraction — combined fraction is their product,
                    # which is already in 0-255 scale (no extra /255 needed).
                    merged[x] = min(255, max(0, round(existing[x] * new_cov[x])))
                if any(merged):
                    result[y] = merged
        return ClipMask(self.width, self.height, result)


# -- path flattening (beziers -> line segments) ------------------------


def flatten_cubic(p0, p1, p2, p3, tolerance: float = 0.3):
    """Flatten a cubic Bezier into line-segment points via fixed-depth
    recursive subdivision (de Casteljau), stopping when the control points
    are within ``tolerance`` of the chord — plenty for screen-resolution
    rendering without needing an adaptive-flatness closed form."""
    points = [p0]
    _subdivide_cubic(p0, p1, p2, p3, tolerance, 0, points)
    points.append(p3)
    return points


def _subdivide_cubic(p0, p1, p2, p3, tol, depth, out):
    if depth >= 16 or _cubic_is_flat(p0, p1, p2, p3, tol):
        out.append(p3)
        return
    p01 = _mid(p0, p1)
    p12 = _mid(p1, p2)
    p23 = _mid(p2, p3)
    p012 = _mid(p01, p12)
    p123 = _mid(p12, p23)
    p0123 = _mid(p012, p123)
    _subdivide_cubic(p0, p01, p012, p0123, tol, depth + 1, out)
    _subdivide_cubic(p0123, p123, p23, p3, tol, depth + 1, out)


def _mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _cubic_is_flat(p0, p1, p2, p3, tol) -> bool:
    d1 = _point_line_distance(p1, p0, p3)
    d2 = _point_line_distance(p2, p0, p3)
    return d1 <= tol and d2 <= tol


def _point_line_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def flatten_quadratic(p0, p1, p2, tolerance: float = 0.3):
    """Flatten via elevation to an equivalent cubic (exact conversion),
    reusing the cubic flattener rather than a second recursive routine."""
    c1 = (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1]))
    c2 = (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1]))
    return flatten_cubic(p0, c1, c2, p2, tolerance)


# -- stroking -----------------------------------------------------------

# PDF /LineCap and /LineJoin values (ISO 32000-1:2008 8.4.3.3/8.4.3.4).
CAP_BUTT, CAP_ROUND, CAP_SQUARE = "butt", "round", "square"
JOIN_MITER, JOIN_ROUND, JOIN_BEVEL = "miter", "round", "bevel"


def stroke_to_shapes(
    subpaths,
    closed_flags,
    line_width: float,
    cap_style: str = CAP_ROUND,
    join_style: str = JOIN_ROUND,
    miter_limit: float = 10.0,
    disc_sides: int = 10,
    ctm_linear=None,
):
    """Convert stroked subpaths into a list of independent fillable shapes
    (one quad per segment, one join/cap shape per vertex) suitable for
    `fill_shapes_union`. Supports all three PDF line caps (butt/round/
    square) and joins (miter/round/bevel), miter falling back to bevel
    past `miter_limit` per spec.

    ``ctm_linear`` is optional: when given (the CTM's linear 2x2 part,
    ``(a, b, c, d)``, no translation), segment width is computed correctly
    per-segment for an anisotropically-scaled CTM (e.g. `2 0 0 1 0 0 cm`
    before stroking should produce a visibly elliptical pen — twice as
    wide horizontally as vertically) instead of one uniform width for the
    whole path; ``line_width`` is then interpreted as the *user-space*
    width. When omitted (the default), ``line_width`` is used directly as
    an already-device-scaled uniform half-width, unchanged from before —
    existing callers that don't pass it see no behavior change. Join/cap
    shapes (discs, miter/bevel wedges, square-cap quads) still use a
    uniform radius even when `ctm_linear` is given — a smaller, still-
    documented approximation, since full elliptical joins/caps are a lot
    more geometry for a rarely-visible detail.
    """
    half = max(line_width, 0.1) / 2.0
    if ctm_linear is not None:
        # Approximate device-space radius for caps/joins (see docstring —
        # those stay circular/uniform even under anisotropic scaling).
        half_caps_joins = half * mx.scale_factor((*ctm_linear, 0.0, 0.0))
    else:
        half_caps_joins = half

    shapes = []
    for path, closed in zip(subpaths, closed_flags):
        pts = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
        if len(pts) < 2:
            if pts and half_caps_joins > 0 and cap_style == CAP_ROUND:
                shapes.append(_disc(pts[0], half_caps_joins, disc_sides))
            continue
        n = len(pts)
        segment_count = n if closed else n - 1
        for i in range(segment_count):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            quad = _segment_quad(p0, p1, half, ctm_linear)
            if quad:
                shapes.append(quad)

        joint_range = range(n) if closed else range(1, n - 1)
        for i in joint_range:
            shapes.extend(
                _join_shapes(
                    pts[i - 1], pts[i], pts[(i + 1) % n], half_caps_joins, join_style, miter_limit
                )
            )
        if not closed:
            shapes.extend(_cap_shapes(pts[1], pts[0], half_caps_joins, cap_style, disc_sides))
            shapes.extend(_cap_shapes(pts[-2], pts[-1], half_caps_joins, cap_style, disc_sides))
    return shapes


def _segment_quad(p0, p1, half, ctm_linear=None):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return []  # degenerate (e.g. a closed path's start==end wraparound): no shape to add
    if ctm_linear is None:
        nx, ny = -dy / length * half, dx / length * half
    else:
        nx, ny = _anisotropic_offset((dx / length, dy / length), half, ctm_linear)
    return [
        [(p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny),
         (p1[0] - nx, p1[1] - ny), (p0[0] - nx, p0[1] - ny)]
    ]


def _anisotropic_offset(unit_dir_dev, half_width_user: float, ctm_linear):
    """Device-space perpendicular offset vector (length = half_width_user
    as scaled by the CTM in the direction perpendicular to this specific
    segment) for a segment whose *device-space* unit direction is
    `unit_dir_dev`, given the CTM's linear part `(a, b, c, d)` (no
    translation — this transforms directions/vectors, not points).

    Derivation: find the user-space direction that the CTM maps to this
    device-space direction (inverse-transform it), take its perpendicular
    in *user space* (where the nominal pen really is circular), scale by
    the true user-space half-width, then forward-transform that offset
    *vector* back through the CTM. This reproduces the elliptical pen an
    anisotropic CTM (e.g. non-uniform `cm` scale) actually produces,
    instead of one uniform width for the whole path.
    """
    inv = mx.invert((*ctm_linear, 0.0, 0.0))
    if inv is None:
        # Singular CTM (zero-area transform) — nothing sensible to draw at
        # any width; fall back to the plain device-space perpendicular so
        # callers still get *some* shape rather than a crash.
        return (-unit_dir_dev[1] * half_width_user, unit_dir_dev[0] * half_width_user)
    ia, ib, ic, id_ = inv[0], inv[1], inv[2], inv[3]
    udx = ia * unit_dir_dev[0] + ic * unit_dir_dev[1]
    udy = ib * unit_dir_dev[0] + id_ * unit_dir_dev[1]
    ulen = math.hypot(udx, udy)
    if ulen == 0:
        return (-unit_dir_dev[1] * half_width_user, unit_dir_dev[0] * half_width_user)
    pnx, pny = -udy / ulen * half_width_user, udx / ulen * half_width_user
    a, b, c, d = ctm_linear
    return (a * pnx + c * pny, b * pnx + d * pny)


def _disc(center, radius, sides):
    cx, cy = center
    pts = [
        (cx + radius * math.cos(2 * math.pi * i / sides),
         cy + radius * math.sin(2 * math.pi * i / sides))
        for i in range(sides)
    ]
    return [pts]


def _cap_shapes(from_pt, to_pt, half: float, cap_style: str, disc_sides: int):
    """Cap applied at `to_pt`, extending outward away from `from_pt`."""
    if cap_style == CAP_ROUND:
        return [_disc(to_pt, half, disc_sides)]
    if cap_style == CAP_SQUARE:
        dx, dy = to_pt[0] - from_pt[0], to_pt[1] - from_pt[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return [_disc(to_pt, half, disc_sides)]
        ux, uy = dx / length, dy / length
        nx, ny = -uy * half, ux * half
        ext = (to_pt[0] + ux * half, to_pt[1] + uy * half)
        quad = [
            (to_pt[0] + nx, to_pt[1] + ny), (ext[0] + nx, ext[1] + ny),
            (ext[0] - nx, ext[1] - ny), (to_pt[0] - nx, to_pt[1] - ny),
        ]
        return [[quad]]
    return []  # butt: the segment quad's own flat end is already the cap


def _line_intersect(p1, d1, p2, d2):
    """Intersection of line p1+t*d1 with line p2+u*d2, or None if parallel."""
    x1, y1 = p1
    x2, y2 = p2
    dx1, dy1 = d1
    dx2, dy2 = d2
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-9:
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / denom
    return (x1 + t * dx1, y1 + t * dy1)


def _join_shapes(prev_pt, cur_pt, next_pt, half: float, join_style: str, miter_limit: float):
    if join_style == JOIN_ROUND:
        return [_disc(cur_pt, half, 10)]

    d1 = (cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
    len1 = math.hypot(*d1)
    d2 = (next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
    len2 = math.hypot(*d2)
    if len1 == 0 or len2 == 0:
        return [_disc(cur_pt, half, 10)]
    u1 = (d1[0] / len1, d1[1] / len1)
    u2 = (d2[0] / len2, d2[1] / len2)

    shapes = []
    # Compute both candidate sides (the turn's outer corner needs the real
    # join shape; the inner side's shape ends up redundant with the
    # segment quads that already cover it — harmless under the union-fill
    # max-coverage combine in fill_shapes_union, so no need to work out
    # turn direction explicitly).
    for s in (1, -1):
        n1 = (-u1[1] * half * s, u1[0] * half * s)
        n2 = (-u2[1] * half * s, u2[0] * half * s)
        p_prev_edge = (cur_pt[0] + n1[0], cur_pt[1] + n1[1])
        p_next_edge = (cur_pt[0] + n2[0], cur_pt[1] + n2[1])
        if join_style == JOIN_MITER:
            m = _line_intersect(p_prev_edge, u1, p_next_edge, u2)
            if m is not None:
                miter_len = math.hypot(m[0] - cur_pt[0], m[1] - cur_pt[1])
                if miter_len <= miter_limit * half:
                    shapes.append([[cur_pt, p_prev_edge, m, p_next_edge]])
                    continue
        shapes.append([[cur_pt, p_prev_edge, p_next_edge]])  # bevel (also the miter-limit fallback)
    return shapes
