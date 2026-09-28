"""Shadings (ISO 32000-1 section 8.7.4.5): smooth colour gradients.

All seven shading types:

1  function-based: colour = f(x, y) over a rectangular domain
2  axial:          colour = f(t), t from the projection onto a line
3  radial:         colour = f(t), t from a family of circles blending
                   between two (the largest matching circle wins)
4, 5             free-form / lattice Gouraud-shaded triangle meshes
6, 7             Coons / tensor-product patch meshes (subdivided into
                   Gouraud triangles)

A Shading answers colour queries in *shading space*; `ShadingRaster`
turns it into device-pixel colours for a given shading-to-device matrix.
Evaluating a PDF function per pixel is slow in pure Python, so 1-D
shadings go through a lookup table and function-based ones through a
sampled grid; meshes are rasterised triangle by triangle into a sparse
per-pixel colour buffer.
"""

from __future__ import annotations

import math
from typing import Optional

from . import _matrix as mx
from ._colorspace import IndexedColorSpace, load_colorspace
from ._function import load_function
from ._objects import PDFStream

LUT_SIZE = 512
GRID_SIZE = 256  # function-based shadings: samples per axis
MAX_PATCH_STEPS = 24


def _floats(doc, value, default=None):
    value = doc.resolve(value)
    if not isinstance(value, list):
        return default
    try:
        return [float(doc.resolve(v)) for v in value]
    except (TypeError, ValueError):
        return default


class Shading:
    def __init__(self, doc, obj, resources=None):
        d = obj.dict if isinstance(obj, PDFStream) else obj
        self.doc = doc
        self.type = int(doc.resolve(d.get("ShadingType", 0)) or 0)
        self.cs = load_colorspace(doc, d.get("ColorSpace"), resources)
        func = d.get("Function")
        self.function = load_function(doc, func) if func is not None else None
        bg = _floats(doc, d.get("Background"))
        self.background = self.cs.to_rgb(bg) if bg else None
        bbox = _floats(doc, d.get("BBox"))
        self.bbox = bbox if bbox and len(bbox) == 4 else None
        self.coords = _floats(doc, d.get("Coords"), [])
        self.domain = _floats(doc, d.get("Domain"))
        extend = doc.resolve(d.get("Extend"))
        self.extend = (
            [bool(doc.resolve(e)) for e in extend][:2]
            if isinstance(extend, list)
            else [False, False]
        )
        while len(self.extend) < 2:
            self.extend.append(False)
        self.matrix = tuple(_floats(doc, d.get("Matrix"), [1, 0, 0, 1, 0, 0]))
        self._lut = None
        # meshes: Gouraud triangles (p0, p1, p2, c0, c1, c2) and patches
        # (4x4 control points, 4 corner colours); colours as component lists
        self.triangles: list = []
        self.patches: list = []
        if self.type in (4, 5, 6, 7) and isinstance(obj, PDFStream):
            _MeshReader(doc, obj, self).read()

    # -- colours --------------------------------------------------------------

    def color_of(self, components) -> tuple:
        """RGB for a colour given as shading 'values' (function inputs when
        there's a function, colour components otherwise)."""
        if self.function is not None:
            try:
                components = self.function.eval(list(components))
            except Exception:
                return (128, 128, 128)
        if isinstance(self.cs, IndexedColorSpace):
            components = [round(components[0])]
        return self.cs.to_rgb(components)

    def _t_range(self):
        return (
            (self.domain[0], self.domain[1])
            if self.domain and len(self.domain) >= 2
            else (0.0, 1.0)
        )

    def lut(self) -> list:
        """RGB for LUT_SIZE evenly spaced parameters across the t domain."""
        if self._lut is None:
            t0, t1 = self._t_range()
            self._lut = [
                self.color_of([t0 + (t1 - t0) * i / (LUT_SIZE - 1)])
                for i in range(LUT_SIZE)
            ]
        return self._lut

    # -- parameter for axial / radial -------------------------------------------

    def parameter(self, x: float, y: float) -> Optional[float]:
        """s in [0, 1] (after Extend) for shading-space point (x, y), or None."""
        c = self.coords
        if self.type == 2 and len(c) >= 4:
            dx, dy = c[2] - c[0], c[3] - c[1]
            denom = dx * dx + dy * dy
            s = 0.0 if denom == 0 else ((x - c[0]) * dx + (y - c[1]) * dy) / denom
            return self._extend(s)
        if self.type == 3 and len(c) >= 6:
            return self._radial(x, y)
        return None

    def _extend(self, s: float) -> Optional[float]:
        if s < 0.0:
            return 0.0 if self.extend[0] else None
        if s > 1.0:
            return 1.0 if self.extend[1] else None
        return s

    def _radial(self, x: float, y: float) -> Optional[float]:
        x0, y0, r0, x1, y1, r1 = self.coords[:6]
        cdx, cdy, dr = x1 - x0, y1 - y0, r1 - r0
        pdx, pdy = x - x0, y - y0
        a = cdx * cdx + cdy * cdy - dr * dr
        b = pdx * cdx + pdy * cdy + r0 * dr
        c = pdx * pdx + pdy * pdy - r0 * r0
        if abs(a) < 1e-9:
            if abs(b) < 1e-12:
                return None
            candidates = [c / (2.0 * b)]
        else:
            disc = b * b - a * c
            if disc < 0:
                return None
            root = math.sqrt(disc)
            candidates = sorted(((b + root) / a, (b - root) / a), reverse=True)
        for s in candidates:  # circles with larger s are drawn later, so they win
            if r0 + s * dr < 0:
                continue  # negative radius: not part of the shading
            if (
                0.0 <= s <= 1.0
                or (s > 1.0 and self.extend[1])
                or (s < 0.0 and self.extend[0])
            ):
                return s  # (colour_at_parameter clamps the extended ranges)
        return None

    def color_at_parameter(self, s: float) -> tuple:
        s = 0.0 if s < 0.0 else 1.0 if s > 1.0 else s
        return self.lut()[int(s * (LUT_SIZE - 1) + 0.5)]

    # -- function-based -----------------------------------------------------------

    def function_grid(self):
        dom = (
            self.domain
            if self.domain and len(self.domain) >= 4
            else [0.0, 1.0, 0.0, 1.0]
        )
        x0, x1, y0, y1 = dom[:4]
        grid = []
        for j in range(GRID_SIZE):
            y = y0 + (y1 - y0) * (j + 0.5) / GRID_SIZE
            row = []
            for i in range(GRID_SIZE):
                xv = x0 + (x1 - x0) * (i + 0.5) / GRID_SIZE
                row.append(self.color_of([xv, y]))
            grid.append(row)
        return (x0, x1, y0, y1), grid


class _Bits:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def read(self, n: int) -> int:
        value = 0
        for _ in range(n):
            byte = self.pos >> 3
            if byte >= len(self.data):
                raise EOFError
            value = (value << 1) | ((self.data[byte] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def align(self):
        self.pos = (self.pos + 7) & ~7


class _MeshReader:
    """Decode mesh shading streams (types 4-7) into Gouraud triangles."""

    def __init__(self, doc, stream: PDFStream, shading: Shading):
        d = stream.dict
        self.shading = shading
        self.bits = _Bits(stream.get_data(resolver=doc.resolve))
        self.bpc = int(doc.resolve(d.get("BitsPerCoordinate", 16)) or 16)
        self.bpcomp = int(doc.resolve(d.get("BitsPerComponent", 16)) or 16)
        self.bpf = int(doc.resolve(d.get("BitsPerFlag", 8)) or 8)
        self.per_row = int(doc.resolve(d.get("VerticesPerRow", 2)) or 2)
        self.ncomp = 1 if shading.function is not None else shading.cs.num_components
        decode = _floats(doc, d.get("Decode"), [])
        self.decode = decode

    def _value(self, nbits: int, lo: float, hi: float) -> float:
        raw = self.bits.read(nbits)
        top = (1 << nbits) - 1 if nbits < 64 else 1
        return lo + (hi - lo) * raw / top if top else lo

    def _point(self):
        dec = self.decode
        x = (
            self._value(self.bpc, dec[0], dec[1])
            if len(dec) >= 4
            else self.bits.read(self.bpc)
        )
        y = (
            self._value(self.bpc, dec[2], dec[3])
            if len(dec) >= 4
            else self.bits.read(self.bpc)
        )
        return (x, y)

    def _color(self):
        dec = self.decode
        out = []
        for i in range(self.ncomp):
            lo, hi = (
                (dec[4 + 2 * i], dec[5 + 2 * i])
                if len(dec) >= 6 + 2 * i
                else (0.0, 1.0)
            )
            out.append(self._value(self.bpcomp, lo, hi))
        return out

    def read(self):
        kind = self.shading.type
        try:
            if kind == 4:
                self._free_form()
            elif kind == 5:
                self._lattice()
            else:
                self._patches(tensor=(kind == 7))
        except (EOFError, IndexError, ValueError):
            pass  # a truncated stream keeps what was complete

    def _vertex(self):
        return (self._point(), self._color())

    def _free_form(self):
        """Each vertex carries a flag: 0 starts a new triangle (the next two
        vertices complete it, their flags ignored); 1 and 2 add a triangle
        sharing the previous one's edge (vb, vc) or (va, vc)."""
        tri = self.shading.triangles
        va = vb = vc = None
        while True:
            flag = self.bits.read(self.bpf)
            v = self._vertex()
            if flag == 0 or va is None:
                self.bits.read(self.bpf)
                v2 = self._vertex()
                self.bits.read(self.bpf)
                v3 = self._vertex()
                va, vb, vc = v, v2, v3
            elif flag == 1:
                va, vb, vc = vb, vc, v
            else:
                va, vb, vc = va, vc, v
            tri.append((va[0], vb[0], vc[0], va[1], vb[1], vc[1]))

    def _lattice(self):
        rows = []
        n = max(2, self.per_row)
        while True:
            row = []
            for _ in range(n):
                row.append((self._point(), self._color()))
            rows.append(row)
            if len(rows) >= 2:
                top, bottom = rows[-2], rows[-1]
                for i in range(n - 1):
                    p, q, r, s = top[i], top[i + 1], bottom[i], bottom[i + 1]
                    self.shading.triangles.append((p[0], q[0], r[0], p[1], q[1], r[1]))
                    self.shading.triangles.append((q[0], s[0], r[0], q[1], s[1], r[1]))

    def _patches(self, tensor: bool):
        prev_pts = prev_cols = None
        count = 16 if tensor else 12
        while True:
            flag = self.bits.read(self.bpf)
            if flag == 0 or prev_pts is None:
                pts = [self._point() for _ in range(count)]
                cols = [self._color() for _ in range(4)]
            else:
                edge = {1: (3, 4, 5, 6), 2: (6, 7, 8, 9), 3: (9, 10, 11, 0)}.get(
                    flag, (3, 4, 5, 6)
                )
                cedge = {1: (1, 2), 2: (2, 3), 3: (3, 0)}.get(flag, (1, 2))
                pts = [prev_pts[i] for i in edge] + [
                    self._point() for _ in range(count - 4)
                ]
                cols = [prev_cols[i] for i in cedge] + [self._color() for _ in range(2)]
            self._emit_patch(pts, cols, tensor)
            prev_pts, prev_cols = pts, cols

    def _emit_patch(self, P, C, tensor: bool):
        # control points p[i][j]: i along u (left->right), j along v (bottom->top)
        p = [[None] * 4 for _ in range(4)]
        order = [
            (0, 0),
            (0, 1),
            (0, 2),
            (0, 3),
            (1, 3),
            (2, 3),
            (3, 3),
            (3, 2),
            (3, 1),
            (3, 0),
            (2, 0),
            (1, 0),
        ]
        for (i, j), pt in zip(order, P):
            p[i][j] = pt
        if tensor:
            p[1][1], p[1][2], p[2][2], p[2][1] = P[12], P[13], P[14], P[15]
        else:  # Coons: the implicit interior points (section 8.7.4.5.7)

            def combo(*terms):
                return tuple(
                    sum(w * p[i][j][k] for w, i, j in terms) / 9.0 for k in (0, 1)
                )

            p[1][1] = combo(
                (-4, 0, 0),
                (6, 0, 1),
                (6, 1, 0),
                (-2, 0, 3),
                (-2, 3, 0),
                (3, 3, 1),
                (3, 1, 3),
                (-1, 3, 3),
            )
            p[1][2] = combo(
                (-4, 0, 3),
                (6, 0, 2),
                (6, 1, 3),
                (-2, 0, 0),
                (-2, 3, 3),
                (3, 3, 2),
                (3, 1, 0),
                (-1, 3, 0),
            )
            p[2][1] = combo(
                (-4, 3, 0),
                (6, 3, 1),
                (6, 2, 0),
                (-2, 3, 3),
                (-2, 0, 0),
                (3, 0, 1),
                (3, 2, 3),
                (-1, 0, 3),
            )
            p[2][2] = combo(
                (-4, 3, 3),
                (6, 3, 2),
                (6, 2, 3),
                (-2, 3, 0),
                (-2, 0, 3),
                (3, 0, 2),
                (3, 2, 0),
                (-1, 0, 0),
            )
        # tessellated later, once the device resolution is known
        self.shading.patches.append((p, C))


def _bernstein(t: float):
    s = 1.0 - t
    return (s * s * s, 3 * t * s * s, 3 * t * t * s, t * t * t)


def _tessellate(p, colors, to_device):
    """Gouraud triangles approximating one patch: the bicubic surface
    evaluated on a grid fine enough for about 3 device pixels per step."""
    c00, c03, c33, c30 = colors
    dev = [mx.apply(to_device, *p[i][j]) for i in range(4) for j in range(4)]
    extent = max(
        max(q[0] for q in dev) - min(q[0] for q in dev),
        max(q[1] for q in dev) - min(q[1] for q in dev),
    )
    steps = max(2, min(MAX_PATCH_STEPS, int(extent / 3) + 1))
    grid = []
    for a in range(steps + 1):
        u = a / steps
        bu = _bernstein(u)
        row = []
        for b in range(steps + 1):
            v = b / steps
            bv = _bernstein(v)
            x = y = 0.0
            for i in range(4):
                for j in range(4):
                    w = bu[i] * bv[j]
                    x += w * p[i][j][0]
                    y += w * p[i][j][1]
            col = [
                (1 - u) * (1 - v) * c00[k]
                + (1 - u) * v * c03[k]
                + u * v * c33[k]
                + u * (1 - v) * c30[k]
                for k in range(len(c00))
            ]
            row.append(((x, y), col))
        grid.append(row)
    out = []
    for a in range(steps):
        for b in range(steps):
            q00, q01, q10, q11 = (
                grid[a][b],
                grid[a][b + 1],
                grid[a + 1][b],
                grid[a + 1][b + 1],
            )
            out.append((q00[0], q10[0], q11[0], q00[1], q10[1], q11[1]))
            out.append((q00[0], q11[0], q01[0], q00[1], q11[1], q01[1]))
    return out


class ShadingRaster:
    """Device-pixel colours of a shading under a shading-to-device matrix."""

    def __init__(
        self,
        shading: Shading,
        to_device,
        width: int,
        height: int,
        use_background: bool = False,
    ):
        self.shading = shading
        self.to_device = to_device
        self.inverse = mx.invert(to_device)
        self.use_background = use_background
        self.width, self.height = width, height
        self._mesh = None
        self._grid = None
        if shading.type in (4, 5, 6, 7):
            self._mesh = self._rasterize_mesh()
        elif shading.type == 1:
            self._grid = shading.function_grid()
            self._fn_inverse = mx.invert(mx.multiply(shading.matrix, to_device))

    def _inside_bbox(self, sx: float, sy: float) -> bool:
        b = self.shading.bbox
        return b is None or (
            min(b[0], b[2]) <= sx <= max(b[0], b[2])
            and min(b[1], b[3]) <= sy <= max(b[1], b[3])
        )

    def color(self, px: int, py: int):
        """RGB at device pixel (px, py), or None where the shading paints nothing."""
        sh = self.shading
        if self._mesh is not None:
            rgb = self._mesh.get(py * self.width + px)
        elif self.inverse is None:
            return None
        else:
            sx, sy = mx.apply(self.inverse, px + 0.5, py + 0.5)
            if not self._inside_bbox(sx, sy):
                return None
            if sh.type == 1:
                rgb = self._function_color(px + 0.5, py + 0.5)
            else:
                s = sh.parameter(sx, sy)
                rgb = None if s is None else sh.color_at_parameter(s)
        if rgb is None and self.use_background:
            return sh.background
        return rgb

    def _function_color(self, dx: float, dy: float):
        if self._fn_inverse is None:
            return None
        (x0, x1, y0, y1), grid = self._grid
        u, v = mx.apply(self._fn_inverse, dx, dy)
        if not (min(x0, x1) <= u <= max(x0, x1) and min(y0, y1) <= v <= max(y0, y1)):
            return None
        i = int((u - x0) / (x1 - x0) * GRID_SIZE) if x1 != x0 else 0
        j = int((v - y0) / (y1 - y0) * GRID_SIZE) if y1 != y0 else 0
        return grid[min(GRID_SIZE - 1, max(0, j))][min(GRID_SIZE - 1, max(0, i))]

    def _rasterize_mesh(self) -> dict:
        out: dict = {}
        sh = self.shading
        m = self.to_device
        w, h = self.width, self.height
        triangles = list(sh.triangles)
        for control, colors in sh.patches:
            triangles.extend(_tessellate(control, colors, m))
        for p0, p1, p2, c0, c1, c2 in triangles:
            (x0, y0), (x1, y1), (x2, y2) = (
                mx.apply(m, *p0),
                mx.apply(m, *p1),
                mx.apply(m, *p2),
            )
            det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(det) < 1e-12:
                continue
            bx0 = max(0, int(math.floor(min(x0, x1, x2))))
            bx1 = min(w - 1, int(math.ceil(max(x0, x1, x2))))
            by0 = max(0, int(math.floor(min(y0, y1, y2))))
            by1 = min(h - 1, int(math.ceil(max(y0, y1, y2))))
            if bx0 > bx1 or by0 > by1:
                continue
            rgb0, rgb1, rgb2 = sh.color_of(c0), sh.color_of(c1), sh.color_of(c2)
            single = sh.function is not None
            for py in range(by0, by1 + 1):
                cy = py + 0.5
                for px in range(bx0, bx1 + 1):
                    cx = px + 0.5
                    l0 = ((y1 - y2) * (cx - x2) + (x2 - x1) * (cy - y2)) / det
                    l1 = ((y2 - y0) * (cx - x2) + (x0 - x2) * (cy - y2)) / det
                    l2 = 1.0 - l0 - l1
                    if l0 < -1e-6 or l1 < -1e-6 or l2 < -1e-6:
                        continue
                    if single:  # interpolate the parameter, then look it up
                        t = l0 * c0[0] + l1 * c1[0] + l2 * c2[0]
                        rgb = sh.color_of([t])
                    else:
                        rgb = (
                            round(l0 * rgb0[0] + l1 * rgb1[0] + l2 * rgb2[0]),
                            round(l0 * rgb0[1] + l1 * rgb1[1] + l2 * rgb2[1]),
                            round(l0 * rgb0[2] + l1 * rgb1[2] + l2 * rgb2[2]),
                        )
                    out[py * w + px] = rgb
        return out


def load_shading(doc, obj, resources=None) -> Optional[Shading]:
    obj = doc.resolve(obj)
    if not isinstance(obj, (dict, PDFStream)):
        return None
    try:
        return Shading(doc, obj, resources)
    except Exception:
        return None
