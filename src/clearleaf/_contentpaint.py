"""Full content-stream interpreter for rendering (Phase 3): paths,
fills/strokes, clipping, text (via glyph outlines), Form XObjects, and
Image XObjects — built on `_raster.py`'s rasterizer.

Simplifications, all deliberate and documented rather than silent:
  - Line width is scaled by the CTM's approximate uniform scale factor
    (`_matrix.scale_factor`), not sheared/scaled per-axis.
  - Shadings (the `sh` operator and shading patterns) and tiling patterns
    paint per pixel (`_shading.py`, `_pattern.py`).
  - DCTDecode/JPXDecode images that don't have
    a decoder available render as a flat placeholder box rather than
    nothing (see `_jpeg.py` for the one of these that *is* implemented).
  - Embedded TrueType (/FontFile2, sfnt-wrapped /FontFile3 with `glyf`
    outlines), Type 1 (/FontFile) and bare CFF (/FontFile3 Type1C and
    CIDFontType0C) programs render with real outlines. CFF-flavoured
    OpenType (an sfnt with a 'CFF ' table instead of 'glyf') does not yet,
    and paints nothing for those glyphs (advance widths stay correct).
  - Soft masks and blend modes aren't implemented; ExtGState is only
    consulted for constant alpha (/ca, /CA).
"""

from __future__ import annotations

import math
import sys
from array import array
from itertools import accumulate

from . import _matrix as mx
from ._colorspace import (
    DEVICE_CMYK,
    DEVICE_GRAY,
    DEVICE_RGB,
    IndexedColorSpace,
    PatternColorSpace,
    load_colorspace,
)
from ._content import iter_operations
from ._fonts import SimpleFont, Type0Font, load_font, style_flags
from ._cff import CFFFont
from ._type1 import Type1Font
from ._glyphrender import glyph_outline_to_device_polygons, path_to_device_polygons
from ._jpeg import decode_jpeg
from ._objects import PDFStream
from ._raster import (
    CAP_BUTT,
    CAP_ROUND,
    CAP_SQUARE,
    JOIN_BEVEL,
    JOIN_MITER,
    JOIN_ROUND,
    ClipMask,
    fill_shape,
    fill_shapes_union,
    flatten_cubic,
    shape_coverage_rows,
    stroke_to_shapes,
    union_coverage_rows,
)
from ._blend import blend_mode_from, composite
from ._function import load_function
from ._raster import Canvas
from ._stdfonts import substitute_font

_INLINE_IMAGE_KEY_ALIASES = {
    "W": "Width",
    "H": "Height",
    "BPC": "BitsPerComponent",
    "CS": "ColorSpace",
    "F": "Filter",
    "DP": "DecodeParms",
    "D": "Decode",
    "IM": "ImageMask",
    "I": "Interpolate",
    "L": "Length",
}

_IMAGE_CODEC_FILTERS = {"DCTDecode", "DCT", "JPXDecode"}


class _PaintGState:
    __slots__ = (
        "ctm",
        "fill_cs",
        "fill_color",
        "stroke_cs",
        "stroke_color",
        "line_width",
        "line_cap",
        "line_join",
        "miter_limit",
        "clip",
        "fill_alpha",
        "stroke_alpha",
        "font",
        "font_resource_name",
        "font_size",
        "char_space",
        "word_space",
        "h_scale",
        "leading",
        "rise",
        "render_mode",
        "fill_pattern",
        "stroke_pattern",
        "blend_mode",
        "soft_mask",
    )

    def __init__(self):
        self.ctm = mx.IDENTITY
        self.fill_cs = DEVICE_GRAY
        self.fill_color = [0.0]
        self.stroke_cs = DEVICE_GRAY
        self.stroke_color = [0.0]
        self.line_width = 1.0
        self.line_cap = CAP_BUTT
        self.line_join = JOIN_MITER
        self.miter_limit = 10.0
        self.clip = None
        self.fill_alpha = 1.0
        self.stroke_alpha = 1.0
        self.font = None
        self.font_resource_name = ""
        self.font_size = 0.0
        self.char_space = 0.0
        self.word_space = 0.0
        self.h_scale = 1.0
        self.leading = 0.0
        self.rise = 0.0
        self.render_mode = 0
        # (pattern object, components) once a pattern colour is selected
        self.fill_pattern = None
        self.stroke_pattern = None
        self.blend_mode = "Normal"
        self.soft_mask = None  # ClipMask-shaped per-pixel alpha from /SMask

    def copy(self) -> "_PaintGState":
        g = _PaintGState()
        for attr in self.__slots__:
            setattr(g, attr, getattr(self, attr))
        return g


class ContentPainter:
    MAX_XOBJECT_DEPTH = 12

    def __init__(self, doc, canvas, resources: dict, ctm):
        self.doc = doc
        self.canvas = canvas
        self._font_cache: dict = {}
        self._gstack: list = []
        self._resources_stack = [resources if isinstance(resources, dict) else {}]
        self.gs = _PaintGState()
        self.gs.ctm = ctm
        self.gs.clip = ClipMask.full(canvas.width, canvas.height)
        self.tm = mx.IDENTITY
        self.tlm = mx.IDENTITY
        self._depth = 0
        # Patterns live in the default space of the content stream using
        # them: the page's initial CTM, or a form XObject's own space.
        self._pattern_space = ctm

        self.path_subpaths: list = []
        self.path_closed: list = []
        self.current_point = None
        self.subpath_start = None
        self._pending_clip = None

    def run(self, content_bytes: bytes) -> None:
        for op, operands in iter_operations(content_bytes):
            if op == "INLINE_IMAGE":
                try:
                    self._paint_inline_image(operands)
                except Exception:
                    pass
                continue
            handler = _DISPATCH.get(op)
            if handler is None:
                continue
            try:
                handler(self, operands)
            except Exception:
                continue

    # -- graphics state ---------------------------------------------------

    def _op_q(self, operands):
        self._gstack.append(self.gs.copy())

    def _op_Q(self, operands):
        if self._gstack:
            self.gs = self._gstack.pop()

    def _op_cm(self, operands):
        a, b, c, d, e, f = (float(x) for x in operands[-6:])
        self.gs.ctm = mx.multiply((a, b, c, d, e, f), self.gs.ctm)

    def _op_w(self, operands):
        self.gs.line_width = float(operands[-1])

    def _op_J(self, operands):
        self.gs.line_cap = {0: CAP_BUTT, 1: CAP_ROUND, 2: CAP_SQUARE}.get(
            int(operands[-1]), CAP_BUTT
        )

    def _op_j(self, operands):
        self.gs.line_join = {0: JOIN_MITER, 1: JOIN_ROUND, 2: JOIN_BEVEL}.get(
            int(operands[-1]), JOIN_MITER
        )

    def _op_M(self, operands):
        self.gs.miter_limit = float(operands[-1])

    def _op_gs(self, operands):
        name = str(operands[-1])
        resources = self._resources_stack[-1]
        extg = (
            self.doc.resolve(resources.get("ExtGState"))
            if isinstance(resources, dict)
            else None
        )
        d = self.doc.resolve(extg.get(name)) if isinstance(extg, dict) else None
        if not isinstance(d, dict):
            return
        ca = self.doc.resolve(d.get("ca"))
        if isinstance(ca, (int, float)):
            self.gs.fill_alpha = float(ca)
        cap = self.doc.resolve(d.get("CA"))
        if isinstance(cap, (int, float)):
            self.gs.stroke_alpha = float(cap)
        if "BM" in d:
            self.gs.blend_mode = blend_mode_from(self.doc.resolve(d.get("BM")))
        if "SMask" in d:
            smask = self.doc.resolve(d.get("SMask"))
            self.gs.soft_mask = self._render_soft_mask(smask) if isinstance(smask, dict) else None

    def _render_soft_mask(self, smask: dict):
        """The soft mask's per-pixel values (section 11.6.5.2): its group
        form rendered offscreen with the current CTM, then its luminosity
        (over the /BC backdrop) or its coverage taken as alpha."""
        doc = self.doc
        group = doc.resolve(smask.get("G"))
        if not isinstance(group, PDFStream):
            return None
        kind = str(doc.resolve(smask.get("S", "Luminosity")))
        w, h = self.canvas.width, self.canvas.height
        group_dict = doc.resolve(group.dict.get("Group"))
        cs = DEVICE_GRAY
        if isinstance(group_dict, dict) and group_dict.get("CS") is not None:
            cs = load_colorspace(doc, group_dict.get("CS"), self._resources_stack[-1])
        bc = [float(doc.resolve(v)) for v in (doc.resolve(smask.get("BC")) or [])]
        backdrop = cs.to_rgb(bc) if bc and len(bc) == cs.num_components else (0, 0, 0)

        def render(background):
            canvas = Canvas(w, h, background=background)
            sub = ContentPainter(doc, canvas, self._resources_stack[-1], self.gs.ctm)
            sub._depth = self._depth + 1
            sub._pattern_space = self._pattern_space
            try:
                sub._paint_form(group)
            except Exception:
                pass
            return canvas.pixels

        transfer = doc.resolve(smask.get("TR"))
        fn = None
        if transfer is not None and str(transfer) != "Identity":
            try:
                fn = load_function(doc, transfer)
            except Exception:
                fn = None
        rows = {}
        if kind == "Alpha":
            white, black = render((255, 255, 255)), render((0, 0, 0))
            values = [255 - (white[i] - black[i]) for i in range(0, len(white), 3)]
        else:
            pixels = render(backdrop)
            values = [(30 * pixels[i] + 59 * pixels[i + 1] + 11 * pixels[i + 2]) // 100
                      for i in range(0, len(pixels), 3)]
        if fn is not None:
            table = []
            for v in range(256):
                try:
                    table.append(max(0, min(255, round(fn.eval([v / 255.0])[0] * 255))))
                except Exception:
                    table.append(v)
            values = [table[v] for v in values]
        for y in range(h):
            row = bytearray(max(0, min(255, v)) for v in values[y * w : (y + 1) * w])
            if any(row):
                rows[y] = row
        return ClipMask(w, h, rows)

    # -- path construction ------------------------------------------------

    def _tp(self, x: float, y: float):
        return mx.apply(self.gs.ctm, x, y)

    def _op_m(self, operands):
        x, y = (float(v) for v in operands[-2:])
        pt = self._tp(x, y)
        self.path_subpaths.append([pt])
        self.path_closed.append(False)
        self.current_point = pt
        self.subpath_start = pt

    def _ensure_subpath(self):
        if not self.path_subpaths:
            pt = self._tp(0.0, 0.0)
            self.path_subpaths.append([pt])
            self.path_closed.append(False)
            self.current_point = pt
            self.subpath_start = pt

    def _op_l(self, operands):
        self._ensure_subpath()
        x, y = (float(v) for v in operands[-2:])
        pt = self._tp(x, y)
        self.path_subpaths[-1].append(pt)
        self.current_point = pt

    def _op_c(self, operands):
        self._ensure_subpath()
        x1, y1, x2, y2, x3, y3 = (float(v) for v in operands[-6:])
        p0 = self.current_point
        p1, p2, p3 = self._tp(x1, y1), self._tp(x2, y2), self._tp(x3, y3)
        self.path_subpaths[-1].extend(flatten_cubic(p0, p1, p2, p3)[1:])
        self.current_point = p3

    def _op_v(self, operands):
        self._ensure_subpath()
        x2, y2, x3, y3 = (float(v) for v in operands[-4:])
        p0 = self.current_point
        p2, p3 = self._tp(x2, y2), self._tp(x3, y3)
        self.path_subpaths[-1].extend(flatten_cubic(p0, p0, p2, p3)[1:])
        self.current_point = p3

    def _op_y(self, operands):
        self._ensure_subpath()
        x1, y1, x3, y3 = (float(v) for v in operands[-4:])
        p0 = self.current_point
        p1, p3 = self._tp(x1, y1), self._tp(x3, y3)
        self.path_subpaths[-1].extend(flatten_cubic(p0, p1, p3, p3)[1:])
        self.current_point = p3

    def _op_h(self, operands):
        if self.path_closed:
            self.path_closed[-1] = True
            self.current_point = self.subpath_start

    def _op_re(self, operands):
        x, y, w, h = (float(v) for v in operands[-4:])
        p0, p1 = self._tp(x, y), self._tp(x + w, y)
        p2, p3 = self._tp(x + w, y + h), self._tp(x, y + h)
        self.path_subpaths.append([p0, p1, p2, p3])
        self.path_closed.append(True)
        self.current_point = p0
        self.subpath_start = p0

    # -- path painting ------------------------------------------------------

    def _reset_path(self):
        self.path_subpaths = []
        self.path_closed = []
        self.current_point = None
        self.subpath_start = None

    def _device_line_width(self) -> float:
        return max(self.gs.line_width * mx.scale_factor(self.gs.ctm), 1.0)

    def _user_line_width_for_stroke(self) -> float:
        """User-space line width for `stroke_to_shapes`'s anisotropic path
        (paired with `_ctm_linear()`), boosted just enough that its
        approximate device-space width doesn't fall under ~1px — same
        "always visible" guarantee as `_device_line_width()`, just applied
        before the per-segment anisotropic transform instead of after a
        single uniform one."""
        scale = mx.scale_factor(self.gs.ctm)
        min_user_width = (1.0 / scale) if scale > 0 else self.gs.line_width
        return max(self.gs.line_width, min_user_width)

    def _ctm_linear(self):
        a, b, c, d, _e, _f = self.gs.ctm
        return (a, b, c, d)

    def _resolve_fill_rgb(self):
        return self.gs.fill_cs.to_rgb(self.gs.fill_color)

    def _resolve_stroke_rgb(self):
        return self.gs.stroke_cs.to_rgb(self.gs.stroke_color)

    # -- pattern / shading paint ---------------------------------------------

    def _paint_for(self, stroke: bool):
        """The pattern paint (see _pattern.py) when the current fill or stroke
        colour is a pattern, else None."""
        cs = self.gs.stroke_cs if stroke else self.gs.fill_cs
        selected = self.gs.stroke_pattern if stroke else self.gs.fill_pattern
        if not isinstance(cs, PatternColorSpace) or selected is None:
            return None
        from ._pattern import pattern_paint

        pattern, components = selected
        # a tiling pattern's tile is rendered once per use context, not per glyph
        key = (id(pattern), tuple(components), self._pattern_space, id(cs))
        cache = self.__dict__.setdefault("_pattern_cache", {})
        if key not in cache:
            cache[key] = pattern_paint(
                self, pattern, components, self._pattern_space, cs
            )
        return cache[key]

    def _paint_clip(self):
        """What limits painting right now: the clip times the soft mask.
        Also hands the blend mode to the canvas for the paint that follows."""
        self.canvas.blend_mode = self.gs.blend_mode
        mask = self.gs.soft_mask
        if mask is None:
            return self.gs.clip
        key = (id(self.gs.clip), id(mask))
        cached = getattr(self, "_masked_clip", None)
        if cached is None or cached[0] != key:
            self._masked_clip = (key, self.gs.clip.multiply(mask), self.gs.clip, mask)
        return self._masked_clip[1]

    def _paint_rows(self, rows: dict, alpha: float, paint) -> None:
        """Blend per-pixel colours from ``paint`` through coverage ``rows``
        ({y: [coverage per x]}) and the current clip."""
        px = self.canvas.pixels
        width = self.canvas.width
        clip = self._paint_clip()
        mode = self.gs.blend_mode
        sample = paint.sample
        for y, cov in rows.items():
            clip_row = clip.row(y) if clip is not None else None
            base = y * width * 3
            for x in range(width):
                a = cov[x]
                if a <= 0.0:
                    continue
                if clip_row is not None:
                    a *= clip_row[x] / 255.0
                    if a <= 0.0:
                        continue
                hit = sample(x, y)
                if hit is None:
                    continue
                rgb, pa = hit
                a = min(a, 1.0) * alpha * pa
                i = base + 3 * x
                if mode != "Normal":
                    composite(px, i, rgb, a, mode)
                elif a >= 0.999:
                    px[i], px[i + 1], px[i + 2] = rgb
                elif a > 0.0:
                    inv = 1.0 - a
                    px[i] = int(rgb[0] * a + px[i] * inv)
                    px[i + 1] = int(rgb[1] * a + px[i + 1] * inv)
                    px[i + 2] = int(rgb[2] * a + px[i + 2] * inv)

    def _fill_polygons(self, subpaths, evenodd: bool = False) -> None:
        paint = self._paint_for(False)
        if paint is None:
            fill_shape(
                self.canvas,
                subpaths,
                self._resolve_fill_rgb(),
                alpha=self.gs.fill_alpha,
                evenodd=evenodd,
                clip=self._paint_clip(),
            )
        else:
            rows = shape_coverage_rows(
                subpaths, self.canvas.width, self.canvas.height, evenodd
            )
            self._paint_rows(rows, self.gs.fill_alpha, paint)

    def _stroke_shapes(self, shapes) -> None:
        paint = self._paint_for(True)
        if paint is None:
            fill_shapes_union(
                self.canvas,
                shapes,
                self._resolve_stroke_rgb(),
                alpha=self.gs.stroke_alpha,
                clip=self._paint_clip(),
            )
        else:
            rows = union_coverage_rows(shapes, self.canvas.width, self.canvas.height)
            self._paint_rows(rows, self.gs.stroke_alpha, paint)

    def _op_sh(self, operands):
        """Paint a shading over the whole clip region."""
        from ._pattern import PatternPaint
        from ._shading import ShadingRaster, load_shading

        resources = self._resources_stack[-1]
        shadings = (
            self.doc.resolve(resources.get("Shading"))
            if isinstance(resources, dict)
            else None
        )
        if not isinstance(shadings, dict):
            return
        shading = load_shading(self.doc, shadings.get(str(operands[-1])), resources)
        if shading is None:
            return
        w, h = self.canvas.width, self.canvas.height
        raster = ShadingRaster(shading, self.gs.ctm, w, h)

        def sample(x, y):
            rgb = raster.color(x, y)
            return None if rgb is None else (rgb, 1.0)

        clip_rows = self.gs.clip.rows if self.gs.clip is not None else None
        full = [1.0] * w
        ys = range(h) if clip_rows is None else sorted(clip_rows)
        self._paint_rows(
            {y: full for y in ys}, self.gs.fill_alpha, PatternPaint(sample)
        )

    def _end_path(self, fill_rule, do_stroke: bool, close_all: bool = False):
        subpaths = self.path_subpaths
        closed = [True] * len(self.path_closed) if close_all else list(self.path_closed)
        if fill_rule is not None and subpaths:
            self._fill_polygons(subpaths, evenodd=(fill_rule == "evenodd"))
        if do_stroke and subpaths:
            shapes = stroke_to_shapes(
                subpaths,
                closed,
                self._user_line_width_for_stroke(),
                cap_style=self.gs.line_cap,
                join_style=self.gs.line_join,
                miter_limit=self.gs.miter_limit,
                ctm_linear=self._ctm_linear(),
            )
            self._stroke_shapes(shapes)
        if self._pending_clip is not None and subpaths:
            self.gs.clip = self.gs.clip.intersect_shape(
                subpaths, evenodd=(self._pending_clip == "evenodd")
            )
        self._pending_clip = None
        self._reset_path()

    def _op_S(self, operands):
        self._end_path(None, True)

    def _op_s(self, operands):
        self._end_path(None, True, close_all=True)

    def _op_f(self, operands):
        self._end_path("nonzero", False)

    def _op_fstar(self, operands):
        self._end_path("evenodd", False)

    def _op_B(self, operands):
        self._end_path("nonzero", True)

    def _op_Bstar(self, operands):
        self._end_path("evenodd", True)

    def _op_b(self, operands):
        self._end_path("nonzero", True, close_all=True)

    def _op_bstar(self, operands):
        self._end_path("evenodd", True, close_all=True)

    def _op_n(self, operands):
        self._end_path(None, False)

    def _op_W(self, operands):
        self._pending_clip = "nonzero"

    def _op_Wstar(self, operands):
        self._pending_clip = "evenodd"

    # -- color --------------------------------------------------------------

    def _op_g(self, operands):
        self.gs.fill_cs, self.gs.fill_color = DEVICE_GRAY, [float(operands[-1])]

    def _op_G(self, operands):
        self.gs.stroke_cs, self.gs.stroke_color = DEVICE_GRAY, [float(operands[-1])]

    def _op_rg(self, operands):
        self.gs.fill_cs = DEVICE_RGB
        self.gs.fill_color = [float(v) for v in operands[-3:]]

    def _op_RG(self, operands):
        self.gs.stroke_cs = DEVICE_RGB
        self.gs.stroke_color = [float(v) for v in operands[-3:]]

    def _op_k(self, operands):
        self.gs.fill_cs = DEVICE_CMYK
        self.gs.fill_color = [float(v) for v in operands[-4:]]

    def _op_K(self, operands):
        self.gs.stroke_cs = DEVICE_CMYK
        self.gs.stroke_color = [float(v) for v in operands[-4:]]

    def _op_cs(self, operands):
        cs = load_colorspace(self.doc, operands[-1], self._resources_stack[-1])
        self.gs.fill_cs, self.gs.fill_color = cs, cs.default_components()

    def _op_CS(self, operands):
        cs = load_colorspace(self.doc, operands[-1], self._resources_stack[-1])
        self.gs.stroke_cs, self.gs.stroke_color = cs, cs.default_components()

    @staticmethod
    def _numeric_operands(operands):
        return [
            o
            for o in operands
            if isinstance(o, (int, float)) and not isinstance(o, bool)
        ]

    def _pattern_operand(self, operands, cs):
        """(pattern object, components) for `/P1 scn` / `c1 c2 /P1 scn`."""
        if (
            not isinstance(cs, PatternColorSpace)
            or not operands
            or not isinstance(operands[-1], str)
        ):
            return None
        resources = self._resources_stack[-1]
        patterns = (
            self.doc.resolve(resources.get("Pattern"))
            if isinstance(resources, dict)
            else None
        )
        pattern = (
            self.doc.resolve(patterns.get(str(operands[-1])))
            if isinstance(patterns, dict)
            else None
        )
        if pattern is None:
            return None
        return (pattern, self._numeric_operands(operands))

    def _op_sc(self, operands):
        pattern = self._pattern_operand(operands, self.gs.fill_cs)
        if pattern is not None:
            self.gs.fill_pattern = pattern
            return
        nums = self._numeric_operands(operands)
        if nums:
            self.gs.fill_color = nums

    def _op_SC(self, operands):
        pattern = self._pattern_operand(operands, self.gs.stroke_cs)
        if pattern is not None:
            self.gs.stroke_pattern = pattern
            return
        nums = self._numeric_operands(operands)
        if nums:
            self.gs.stroke_color = nums

    # -- text -----------------------------------------------------------

    def _op_BT(self, operands):
        self.tm = mx.IDENTITY
        self.tlm = mx.IDENTITY

    def _op_ET(self, operands):
        pass

    def _op_Tf(self, operands):
        name, size = operands[-2:]
        self.gs.font_resource_name = str(name)
        self.gs.font_size = float(size)
        self.gs.font = self._resolve_font(str(name))

    def _move_line(self, tx: float, ty: float):
        self.tlm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, ty), self.tlm)
        self.tm = self.tlm

    def _op_Td(self, operands):
        tx, ty = (float(v) for v in operands[-2:])
        self._move_line(tx, ty)

    def _op_TD(self, operands):
        tx, ty = (float(v) for v in operands[-2:])
        self.gs.leading = -ty
        self._move_line(tx, ty)

    def _op_Tstar(self, operands):
        self._move_line(0.0, -self.gs.leading)

    def _op_Tm(self, operands):
        a, b, c, d, e, f = (float(v) for v in operands[-6:])
        self.tlm = (a, b, c, d, e, f)
        self.tm = self.tlm

    def _op_Tc(self, operands):
        self.gs.char_space = float(operands[-1])

    def _op_Tw(self, operands):
        self.gs.word_space = float(operands[-1])

    def _op_Tz(self, operands):
        self.gs.h_scale = float(operands[-1]) / 100.0

    def _op_TL(self, operands):
        self.gs.leading = float(operands[-1])

    def _op_Ts(self, operands):
        self.gs.rise = float(operands[-1])

    def _op_Tr(self, operands):
        self.gs.render_mode = int(operands[-1])

    def _op_Tj(self, operands):
        self._show_text(operands[-1])

    def _op_TJ(self, operands):
        for item in operands[-1]:
            if isinstance(item, (bytes, bytearray)):
                self._show_text(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                tx = (-item / 1000.0) * self.gs.font_size * self.gs.h_scale
                self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)

    def _op_quote(self, operands):
        self._op_Tstar([])
        self._show_text(operands[-1])

    def _op_dquote(self, operands):
        aw, ac, s = operands[-3:]
        self.gs.word_space, self.gs.char_space = float(aw), float(ac)
        self._op_Tstar([])
        self._show_text(s)

    def _resolve_font(self, name: str):
        resources = self._resources_stack[-1]
        font_container = (
            self.doc.resolve(resources.get("Font"))
            if isinstance(resources, dict)
            else None
        )
        if not isinstance(font_container, dict):
            return None
        font_dict = self.doc.resolve(font_container.get(name))
        if not isinstance(font_dict, dict):
            return None
        key = id(font_dict)
        font = self._font_cache.get(key)
        if font is None:
            font = load_font(self.doc, font_dict)
            self._font_cache[key] = font
        return font

    @staticmethod
    def _renderer_for(font):
        """(glyph source or None, uses_own_embedded_program: bool). The source
        is a TrueTypeFont (glyph ids) or a Type1Font/CFFFont (glyph names, or
        CIDs for CID-keyed CFF)."""
        program = getattr(font, "embedded_postscript", None)
        if program is not None:
            return program, True
        ttf = getattr(font, "embedded_truetype", None)
        if ttf is not None:
            return ttf, True
        flags = style_flags(font)
        substitute = substitute_font(
            font.base_font, bold=bool(flags & 16), italic=bool(flags & 2)
        )
        # An installed Type 1 standard font has the standard glyph names, so
        # it's addressed by name like an embedded program.
        return substitute, isinstance(substitute, (Type1Font, CFFFont))

    @staticmethod
    def _glyph_id_for(font, ttf, uses_own_program: bool, code: int, text: str):
        if ttf is None:
            return None
        if uses_own_program and isinstance(font, Type0Font):
            looked_up = font.encoding_cmap.lookup(code)
            cid = looked_up if isinstance(looked_up, int) else code
            return font.glyph_id_for_cid(cid)
        if uses_own_program and isinstance(font, SimpleFont) and font.symbolic:
            for platform_id, encoding_id, table in ttf.cmap_subtables:
                if (platform_id, encoding_id) == (3, 0):
                    for candidate in (0xF000 + code, code):
                        gid = table.get(candidate)
                        if gid:
                            return gid
                elif (platform_id, encoding_id) == (1, 0):
                    gid = table.get(code)
                    if gid:
                        return gid
        if text:
            cp = ord(text[0])
            for platform_id, encoding_id, table in ttf.cmap_subtables:
                if (platform_id, encoding_id) in ((3, 1), (0, 3), (0, 4), (0, 6)):
                    gid = table.get(cp)
                    if gid:
                        return gid
            for _p, _e, table in ttf.cmap_subtables:
                gid = table.get(cp)
                if gid:
                    return gid
        if getattr(ttf, "system_family", None) in ("Symbol", "ZapfDingbats"):
            # installed symbol fonts: their Mac cmap is keyed by the font's own codes
            for platform_id, encoding_id, table in ttf.cmap_subtables:
                if (platform_id, encoding_id) == (1, 0) and table.get(code):
                    return table[code]
        return None

    def _glyph_polygons(
        self, font, source, uses_own: bool, code: int, text: str, trm
    ) -> list:
        """Device-space polygons for one glyph, from a TrueType program (by
        glyph id) or a Type 1/CFF program (by glyph name, or by CID for a
        composite font), through the program's FontMatrix."""
        if isinstance(source, (Type1Font, CFFFont)):
            if isinstance(font, Type0Font) and isinstance(source, CFFFont):
                looked_up = font.encoding_cmap.lookup(code)
                path = source.glyph_outline_cid(
                    looked_up if isinstance(looked_up, int) else code
                )
            elif isinstance(font, SimpleFont):
                name = font.glyph_name_for(code, text)
                path = source.glyph_outline(name) if name else []
            else:
                path = []
            if not path:
                return []
            a, b, c, d, e, f = source.font_matrix

            def transform(gx, gy, _trm=trm):
                return mx.apply(_trm, a * gx + c * gy + e, b * gx + d * gy + f)

            return path_to_device_polygons(path, transform)

        gid = self._glyph_id_for(font, source, uses_own, code, text)
        if gid is None:
            return []
        units = source.units_per_em or 1000

        def transform(gx, gy, _trm=trm, _units=units):
            return mx.apply(_trm, gx / _units, gy / _units)

        return glyph_outline_to_device_polygons(source, gid, transform)

    def _show_text(self, s):
        font = self.gs.font
        if font is None or not isinstance(s, (bytes, bytearray)) or not s:
            return
        size = self.gs.font_size
        hscale = self.gs.h_scale
        mode = self.gs.render_mode
        do_fill = mode in (0, 2, 4, 6)
        do_stroke = mode in (1, 2, 5, 6)
        source, uses_own = (None, False)
        if do_fill or do_stroke:
            source, uses_own = self._renderer_for(font)
        for code, nbytes, text, width1000 in font.decode(bytes(s)):
            if (do_fill or do_stroke) and source is not None:
                glyph_matrix = (size * hscale, 0.0, 0.0, size, 0.0, self.gs.rise)
                trm = mx.multiply(glyph_matrix, mx.multiply(self.tm, self.gs.ctm))
                polygons = self._glyph_polygons(font, source, uses_own, code, text, trm)
                if polygons and do_fill:
                    self._fill_polygons(polygons)
                if polygons and do_stroke:
                    shapes = stroke_to_shapes(
                        polygons,
                        [True] * len(polygons),
                        self._device_line_width(),
                        join_style=self.gs.line_join,
                        miter_limit=self.gs.miter_limit,
                    )
                    self._stroke_shapes(shapes)

            w0 = width1000 / 1000.0
            extra = self.gs.word_space if (nbytes == 1 and code == 0x20) else 0.0
            tx = (w0 * size + self.gs.char_space + extra) * hscale
            self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)

    # -- XObjects: Form + Image -----------------------------------------

    def _op_Do(self, operands):
        name = str(operands[-1])
        resources = self._resources_stack[-1]
        xobj_container = (
            self.doc.resolve(resources.get("XObject"))
            if isinstance(resources, dict)
            else None
        )
        if not isinstance(xobj_container, dict):
            return
        xobj = self.doc.resolve(xobj_container.get(name))
        if not isinstance(xobj, PDFStream):
            return
        subtype = str(self.doc.resolve(xobj.dict.get("Subtype", "")))
        if subtype == "Form":
            self._paint_form(xobj)
        elif subtype == "Image":
            self._paint_image(xobj)

    def paint_form(self, xobj: PDFStream, placement) -> None:
        """Paint a Form XObject with ``placement`` (e.g. an annotation's
        appearance matrix) applied before the form's own /Matrix."""
        saved_ctm = self.gs.ctm
        self.gs.ctm = mx.multiply(placement, saved_ctm)
        try:
            self._paint_form(xobj)
        finally:
            self.gs.ctm = saved_ctm

    def _paint_form(self, xobj: PDFStream):
        if self._depth >= self.MAX_XOBJECT_DEPTH:
            return
        raw_matrix = self.doc.resolve(xobj.dict.get("Matrix")) or [1, 0, 0, 1, 0, 0]
        form_matrix = tuple(float(self.doc.resolve(v)) for v in raw_matrix)
        form_resources = self.doc.resolve(xobj.dict.get("Resources"))
        if not isinstance(form_resources, dict):
            form_resources = self._resources_stack[-1]

        saved_gs = self.gs
        self.gs = self.gs.copy()
        self.gs.ctm = mx.multiply(form_matrix, self.gs.ctm)

        bbox = self.doc.resolve(xobj.dict.get("BBox"))
        if isinstance(bbox, list) and len(bbox) == 4:
            x0, y0, x1, y1 = (float(self.doc.resolve(v)) for v in bbox)
            corners = [
                self._tp(x0, y0),
                self._tp(x1, y0),
                self._tp(x1, y1),
                self._tp(x0, y1),
            ]
            self.gs.clip = self.gs.clip.intersect_shape([corners])

        self._resources_stack.append(form_resources)
        self._depth += 1
        saved_pattern_space = self._pattern_space
        self._pattern_space = (
            self.gs.ctm
        )  # patterns used inside live in the form's space
        try:
            data = xobj.get_data(resolver=self.doc.resolve)
            self.run(data)
        finally:
            self._depth -= 1
            self._resources_stack.pop()
            self.gs = saved_gs
            self._pattern_space = saved_pattern_space

    _image_alpha = None  # (u, v) -> 0..1 from the image's /SMask or /Mask while it's painted
    _image_dict = None  # the image being painted

    def _paint_image(self, xobj: PDFStream):
        self._image_alpha = None if self.gs.fill_alpha <= 0 else _image_alpha_function(self.doc, xobj.dict)
        self._image_dict = xobj.dict
        try:
            self._paint_image_inner(xobj)
        finally:
            self._image_alpha = None
            self._image_dict = None

    def _paint_image_inner(self, xobj: PDFStream):
        d = xobj.dict
        width = int(self.doc.resolve(d.get("Width", 0)) or 0)
        height = int(self.doc.resolve(d.get("Height", 0)) or 0)
        if width <= 0 or height <= 0:
            return
        is_mask = bool(self.doc.resolve(d.get("ImageMask", False)))

        filt = self.doc.resolve(d.get("Filter"))
        filter_names = (
            [str(f) for f in filt]
            if isinstance(filt, list)
            else ([str(filt)] if filt else [])
        )
        codec_filters = [f for f in filter_names if f in _IMAGE_CODEC_FILTERS]

        if codec_filters and codec_filters[-1] in ("DCTDecode", "DCT"):
            self._paint_jpeg_image(xobj, width, height)
            return
        if codec_filters:
            self._paint_image_placeholder()
            return

        try:
            data = xobj.get_data(resolver=self.doc.resolve)
        except Exception:
            return
        self._paint_image_data(xobj, data, width, height, is_mask)

    def _paint_image_data(self, xobj, data, width, height, is_mask):
        d = xobj.dict
        if is_mask:
            self._paint_image_mask(data, width, height, d)
            return

        cs = load_colorspace(self.doc, d.get("ColorSpace"), self._resources_stack[-1])
        bpc = int(self.doc.resolve(d.get("BitsPerComponent", 8)) or 8)
        self._paint_sampled_image(data, width, height, cs, bpc)

    def _paint_jpeg_image(self, xobj: PDFStream, width: int, height: int):
        try:
            raw = xobj.raw_data
            filt = self.doc.resolve(xobj.dict.get("Filter"))
            names = [str(f) for f in filt] if isinstance(filt, list) else [str(filt)]
            idx = (
                names.index("DCTDecode") if "DCTDecode" in names else names.index("DCT")
            )
            for name in names[:idx]:
                from ._filters import decode_stream

                raw = decode_stream({"Filter": name}, raw, resolver=self.doc.resolve)
            pixels, jw, jh, ncomp = decode_jpeg(raw)
        except Exception:
            self._paint_image_placeholder()
            return

        if ncomp == 1:

            def rgb_lookup(ix, iy):
                v = pixels[(iy * jw + ix)]
                return (v, v, v)

        elif ncomp == 3:

            def rgb_lookup(ix, iy):
                o = (iy * jw + ix) * 3
                return (pixels[o], pixels[o + 1], pixels[o + 2])

        elif ncomp == 4:
            # decode_jpeg already normalizes to direct-convention CMYK
            # (0=no ink), un-inverting Adobe's stored convention itself —
            # no further inversion here.
            def rgb_lookup(ix, iy):
                o = (iy * jw + ix) * 4
                c, m, y, k = (pixels[o + i] / 255.0 for i in range(4))
                return DEVICE_CMYK.to_rgb([c, m, y, k])

        else:
            self._paint_image_placeholder()
            return
        self._blit_unit_square(jw, jh, rgb_lookup)

    def _paint_image_placeholder(self):
        square = [self._tp(0, 0), self._tp(1, 0), self._tp(1, 1), self._tp(0, 1)]
        fill_shape(
            self.canvas,
            [square],
            (200, 200, 200),
            alpha=self.gs.fill_alpha,
            clip=self._paint_clip(),
        )

    def _paint_image_mask(self, data: bytes, width: int, height: int, d: dict):
        decode = self.doc.resolve(d.get("Decode"))
        invert = (
            isinstance(decode, list)
            and len(decode) >= 2
            and float(self.doc.resolve(decode[0])) == 1.0
        )
        row_bytes = (width + 7) // 8
        rgb = self._resolve_fill_rgb()

        def rgb_lookup(ix, iy):
            bit = self._mask_bit(data, row_bytes, ix, iy)
            painted = (bit == 0) != invert
            return rgb if painted else None

        self._blit_unit_square(width, height, rgb_lookup)

    @staticmethod
    def _mask_bit(data: bytes, row_bytes: int, x: int, y: int) -> int:
        offset = y * row_bytes + (x // 8)
        if offset >= len(data):
            return 0
        return (data[offset] >> (7 - (x % 8))) & 1

    def _paint_sampled_image(self, data: bytes, width: int, height: int, cs, bpc: int):
        n = cs.num_components
        row_bytes = (width * n * bpc + 7) // 8
        max_val = float((1 << bpc) - 1)
        is_indexed = isinstance(cs, IndexedColorSpace)
        # /Decode maps each sample linearly onto [Dmin, Dmax] (section 8.9.5.2)
        raw_decode = self.doc.resolve((self._image_dict or {}).get("Decode"))
        decode = None
        if isinstance(raw_decode, list) and len(raw_decode) >= 2 * n:
            decode = [float(self.doc.resolve(v)) for v in raw_decode[: 2 * n]]
        inverted = decode is not None and all(decode[2 * i] == 1 and decode[2 * i + 1] == 0 for i in range(n))

        if (cs is DEVICE_GRAY or cs is DEVICE_RGB) and bpc in (1, 8) and (decode is None or inverted):
            def channel_rows(iy):
                row = data[iy * row_bytes : (iy + 1) * row_bytes]
                if bpc == 1:
                    row = b"".join(_EXPAND_BITS[b] for b in row)[:width]
                if inverted:
                    row = row.translate(_INVERT)
                if n == 1:
                    return [row]
                return [row[0::3], row[1::3], row[2::3]]

            if self._blit_area_average(width, height, channel_rows):
                return

        def rgb_lookup(ix, iy):
            bit_offset = iy * row_bytes * 8 + ix * n * bpc
            comps = []
            for c in range(n):
                v = self._read_bits(data, bit_offset + c * bpc, bpc)
                if is_indexed:
                    comps.append(float(v) if decode is None else decode[2 * c] + v * (decode[2 * c + 1] - decode[2 * c]) / max_val)
                elif decode is not None:
                    comps.append(decode[2 * c] + (v / max_val) * (decode[2 * c + 1] - decode[2 * c]))
                else:
                    comps.append(v / max_val if max_val > 0 else 0.0)
            return cs.to_rgb(comps)

        self._blit_unit_square(width, height, rgb_lookup)

    AREA_AVERAGE_FROM = 4.0  # samples per device pixel beyond which images are box-filtered

    def _blit_area_average(self, img_width: int, img_height: int, channel_rows) -> bool:
        """Exact box-filter downscaling for an axis-aligned image shrunk a
        lot: each device pixel gets the mean of the samples it covers.
        ``channel_rows(iy)`` gives the image row as one 0-255 byte string
        per channel (1 = grey, 3 = RGB). Returns False when it doesn't apply
        (rotated/skewed, not shrunk enough, or per-sample alpha)."""
        ctm = _grid_fit(self.gs.ctm)
        a, b, c, d, e, f = ctm
        if abs(b) > 1e-9 or abs(c) > 1e-9 or self._image_alpha is not None:
            return False
        if max(img_width / abs(a), img_height / abs(d)) < self.AREA_AVERAGE_FROM:
            return False
        dx0, dx1 = sorted((e, e + a))
        dy0, dy1 = sorted((f, f + d))
        cw, ch = self.canvas.width, self.canvas.height
        px0, px1 = max(0, int(dx0)), min(cw, int(dx1))
        py0, py1 = max(0, int(dy0)), min(ch, int(dy1))
        if px0 >= px1 or py0 >= py1:
            return True

        def span(p, origin, size, count):
            """Sample index range covered by device pixel p along one axis."""
            u0, u1 = (p - origin) / size, (p + 1 - origin) / size
            lo, hi = sorted((u0, u1))
            i0 = max(0, min(count - 1, int(math.floor(lo * count + 1e-9))))
            i1 = max(i0 + 1, min(count, int(math.ceil(hi * count - 1e-9))))
            return i0, i1

        # image row iy is at unit-square v = 1 - (iy + 0.5) / h, i.e. top first
        col_spans = [span(p, e, a, img_width) for p in range(px0, px1)]
        # Rows are summed as big integers with one 16-bit lane per sample,
        # so a device row's vertical sum is a handful of C-level additions.
        row_ints: dict = {}

        def packed_row(iy):
            hit = row_ints.get(iy)
            if hit is None:
                hit = row_ints[iy] = [int.from_bytes(_widen(ch_row), "big") for ch_row in channel_rows(iy)]
            return hit

        alpha = self.gs.fill_alpha
        clip = self._paint_clip()
        max_rows = 255  # lanes of 16 bits hold 255 * 255 sums safely
        for py in range(py0, py1):
            v0, v1 = (py - f) / d, (py + 1 - f) / d
            r0, r1 = sorted((1.0 - v0, 1.0 - v1))
            iy0 = max(0, min(img_height - 1, int(math.floor(r0 * img_height + 1e-9))))
            iy1 = max(iy0 + 1, min(img_height, int(math.ceil(r1 * img_height - 1e-9))))
            iy1 = min(iy1, iy0 + max_rows)
            nrows = iy1 - iy0
            totals = None
            for iy in range(iy0, iy1):
                ints = packed_row(iy)
                totals = list(ints) if totals is None else [t + i for t, i in zip(totals, ints)]
            sums = [_lane_prefix(t.to_bytes(img_width * 2, "big")) for t in totals]
            clip_row = clip.row(py) if clip is not None else None
            for k, (i0, i1) in enumerate(col_spans):
                x = px0 + k
                cov = alpha
                if clip_row is not None:
                    cov *= clip_row[x] / 255.0
                    if cov <= 0.0:
                        continue
                count = (i1 - i0) * nrows
                vals = [round((pre[i1] - pre[i0]) / count) for pre in sums]
                rgb = (vals[0], vals[0], vals[0]) if len(vals) == 1 else tuple(vals)
                self._blend_pixel(x, py, rgb, cov)
        return True

    @staticmethod
    def _read_bits(data: bytes, bit_offset: int, nbits: int) -> int:
        val = 0
        byte_idx, bit_in_byte, bits_read = bit_offset // 8, bit_offset % 8, 0
        while bits_read < nbits:
            if byte_idx >= len(data):
                break
            byte = data[byte_idx]
            avail = 8 - bit_in_byte
            take = min(avail, nbits - bits_read)
            chunk = (byte >> (avail - take)) & ((1 << take) - 1)
            val = (val << take) | chunk
            bits_read += take
            bit_in_byte += take
            if bit_in_byte >= 8:
                bit_in_byte, byte_idx = 0, byte_idx + 1
        return val

    MAX_IMAGE_SUBSAMPLES = 4  # per axis, per device pixel

    def _blit_unit_square(self, img_width: int, img_height: int, rgb_lookup) -> None:
        """Map image samples onto the PDF unit square via the CTM, inverse-
        mapping device pixels back into image space (correct under
        rotation/skew). A device pixel that covers several image samples
        (a downscaled image) averages an n x n grid of them, and pixels on
        the image's edge are blended by how much of them it covers.
        Axis-aligned images are first snapped to whole device pixels, so
        abutting images don't leave hairline seams."""
        ctm = _grid_fit(self.gs.ctm)
        corners = [
            mx.apply(ctm, 0, 0),
            mx.apply(ctm, 1, 0),
            mx.apply(ctm, 1, 1),
            mx.apply(ctm, 0, 1),
        ]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        x0 = max(0, int(math.floor(min(xs))))
        x1 = min(self.canvas.width, int(math.ceil(max(xs))))
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.canvas.height, int(math.ceil(max(ys))))
        if x0 >= x1 or y0 >= y1:
            return
        inv = mx.invert(ctm)
        if inv is None:
            return
        # image samples per device pixel along each image axis
        len_u = math.hypot(ctm[0], ctm[1]) or 1e-9
        len_v = math.hypot(ctm[2], ctm[3]) or 1e-9
        n = max(
            1,
            min(
                self.MAX_IMAGE_SUBSAMPLES,
                math.ceil(max(img_width / len_u, img_height / len_v) - 1e-6),
            ),
        )
        offsets = [(i + 0.5) / n for i in range(n)]
        cache: dict = {}

        def sample(ix, iy):
            key = iy * img_width + ix
            rgb = cache.get(key, 0)
            if rgb == 0:
                rgb = cache[key] = rgb_lookup(ix, iy)
            return rgb

        alpha = self.gs.fill_alpha
        clip = self._paint_clip()
        total = n * n
        alpha_fn = self._image_alpha
        for py in range(y0, y1):
            clip_row = clip.row(py) if clip is not None else None
            for px in range(x0, x1):
                if clip_row is not None and clip_row[px] == 0:
                    continue
                r = g = b = 0.0
                weight = 0.0
                for oy in offsets:
                    for ox in offsets:
                        ux, uy = mx.apply(inv, px + ox, py + oy)
                        if not (0.0 <= ux < 1.0 and 0.0 <= uy < 1.0):
                            continue
                        rgb = sample(
                            min(img_width - 1, int(ux * img_width)),
                            min(img_height - 1, int((1.0 - uy) * img_height)),
                        )
                        if rgb is None:
                            continue
                        w = alpha_fn(ux, 1.0 - uy) if alpha_fn is not None else 1.0
                        r += rgb[0] * w
                        g += rgb[1] * w
                        b += rgb[2] * w
                        weight += w
                if weight <= 0.0:
                    continue
                a = alpha * weight / total
                if clip_row is not None:
                    a *= clip_row[px] / 255.0
                if a <= 0.0:
                    continue
                self._blend_pixel(
                    px, py, (round(r / weight), round(g / weight), round(b / weight)), a
                )

    def _blend_pixel(self, x: int, y: int, rgb, a: float) -> None:
        composite(self.canvas.pixels, (y * self.canvas.width + x) * 3, rgb, a, self.gs.blend_mode)

    def _paint_inline_image(self, operands):
        img_dict, raw = operands
        full_dict = {
            _INLINE_IMAGE_KEY_ALIASES.get(str(k), str(k)): v
            for k, v in img_dict.items()
        }
        self._paint_image(PDFStream(full_dict, raw))


def _grid_fit(ctm):
    """For an axis-aligned image, stretch the unit square's device image to
    the nearest whole-pixel edges (at least one pixel wide)."""
    a, b, c, d, e, f = ctm
    if abs(b) > 1e-6 or abs(c) > 1e-6:
        return ctm

    def snap(start, length):
        lo, hi = sorted((start, start + length))
        lo2, hi2 = round(lo), round(hi)
        if hi2 <= lo2:
            hi2 = lo2 + 1
        return (lo2, hi2 - lo2) if length >= 0 else (hi2, lo2 - hi2)

    e2, a2 = snap(e, a)
    f2, d2 = snap(f, d)
    return (float(a2), 0.0, 0.0, float(d2), float(e2), float(f2))


_DISPATCH = {
    "sh": ContentPainter._op_sh,
    "q": ContentPainter._op_q,
    "Q": ContentPainter._op_Q,
    "cm": ContentPainter._op_cm,
    "w": ContentPainter._op_w,
    "J": ContentPainter._op_J,
    "j": ContentPainter._op_j,
    "M": ContentPainter._op_M,
    "gs": ContentPainter._op_gs,
    "m": ContentPainter._op_m,
    "l": ContentPainter._op_l,
    "c": ContentPainter._op_c,
    "v": ContentPainter._op_v,
    "y": ContentPainter._op_y,
    "h": ContentPainter._op_h,
    "re": ContentPainter._op_re,
    "S": ContentPainter._op_S,
    "s": ContentPainter._op_s,
    "f": ContentPainter._op_f,
    "F": ContentPainter._op_f,
    "f*": ContentPainter._op_fstar,
    "B": ContentPainter._op_B,
    "B*": ContentPainter._op_Bstar,
    "b": ContentPainter._op_b,
    "b*": ContentPainter._op_bstar,
    "n": ContentPainter._op_n,
    "W": ContentPainter._op_W,
    "W*": ContentPainter._op_Wstar,
    "g": ContentPainter._op_g,
    "G": ContentPainter._op_G,
    "rg": ContentPainter._op_rg,
    "RG": ContentPainter._op_RG,
    "k": ContentPainter._op_k,
    "K": ContentPainter._op_K,
    "cs": ContentPainter._op_cs,
    "CS": ContentPainter._op_CS,
    "sc": ContentPainter._op_sc,
    "scn": ContentPainter._op_sc,
    "SC": ContentPainter._op_SC,
    "SCN": ContentPainter._op_SC,
    "BT": ContentPainter._op_BT,
    "ET": ContentPainter._op_ET,
    "Tf": ContentPainter._op_Tf,
    "Td": ContentPainter._op_Td,
    "TD": ContentPainter._op_TD,
    "T*": ContentPainter._op_Tstar,
    "Tm": ContentPainter._op_Tm,
    "Tc": ContentPainter._op_Tc,
    "Tw": ContentPainter._op_Tw,
    "Tz": ContentPainter._op_Tz,
    "TL": ContentPainter._op_TL,
    "Ts": ContentPainter._op_Ts,
    "Tr": ContentPainter._op_Tr,
    "Tj": ContentPainter._op_Tj,
    "TJ": ContentPainter._op_TJ,
    "'": ContentPainter._op_quote,
    '"': ContentPainter._op_dquote,
    "Do": ContentPainter._op_Do,
}


def _image_alpha_function(doc, d: dict):
    """(u, v) -> alpha (u right, v down, both 0..1) from an image's /SMask
    (a greyscale soft mask image) or stencil /Mask, or None."""
    for key in ("SMask", "Mask"):
        mask = doc.resolve(d.get(key))
        if not isinstance(mask, PDFStream):
            continue
        md = mask.dict
        mw = int(doc.resolve(md.get("Width", 0)) or 0)
        mh = int(doc.resolve(md.get("Height", 0)) or 0)
        if mw <= 0 or mh <= 0:
            continue
        stencil = key == "Mask"
        bpc = 1 if stencil else int(doc.resolve(md.get("BitsPerComponent", 8)) or 8)
        filters = doc.resolve(md.get("Filter"))
        names = [str(f) for f in filters] if isinstance(filters, list) else ([str(filters)] if filters else [])
        try:
            if names and names[-1] in ("DCTDecode", "DCT"):
                pixels, jw, jh, ncomp = decode_jpeg(mask.raw_data if len(names) == 1 else
                                                    mask.get_data(resolver=doc.resolve))
                values = [pixels[i * ncomp] / 255.0 for i in range(jw * jh)]
                mw, mh = jw, jh
            else:
                data = mask.get_data(resolver=doc.resolve)
                row_bits = ((mw * bpc + 7) // 8) * 8
                top = float((1 << bpc) - 1)
                values = []
                for y in range(mh):
                    for x in range(mw):
                        v = ContentPainter._read_bits(data, y * row_bits + x * bpc, bpc) / top
                        values.append(v)
        except Exception:
            continue
        decode = [float(doc.resolve(v)) for v in (doc.resolve(md.get("Decode")) or [])]
        invert = len(decode) >= 2 and decode[0] > decode[1]
        if stencil:  # sample 1 masks the image out (unless /Decode [1 0])
            values = [v if invert else 1.0 - v for v in values]
        elif invert:
            values = [1.0 - v for v in values]
        if all(v >= 1.0 for v in values):
            return None  # fully opaque: nothing to do

        def alpha(u, v, _values=values, _w=mw, _h=mh):
            return _values[min(_h - 1, int(v * _h)) * _w + min(_w - 1, int(u * _w))]

        return alpha
    return None


_EXPAND_BITS = [bytes(0 if (byte >> (7 - i)) & 1 == 0 else 255 for i in range(8)) for byte in range(256)]
_INVERT = bytes(255 - i for i in range(256))


def _widen(row: bytes) -> bytes:
    """Each byte as a big-endian 16-bit lane (so rows can be summed as ints)."""
    out = bytearray(2 * len(row))
    out[1::2] = row
    return bytes(out)


def _lane_prefix(raw: bytes) -> list:
    """Prefix sums over big-endian 16-bit lanes."""
    values = array("H", raw)
    if sys.byteorder == "little":
        values.byteswap()
    return [0, *accumulate(values)]
