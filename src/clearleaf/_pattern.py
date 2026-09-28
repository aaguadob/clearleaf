"""Patterns (ISO 32000-1 section 8.7.3): filling and stroking with a
repeated tile or a shading instead of a flat colour.

A pattern lives in *pattern space*, mapped by its /Matrix into the default
coordinate space of the content stream it's used in (the page, or the
form XObject that uses it) -- not the CTM at the moment of the fill.

- Shading patterns (PatternType 2) wrap a shading (_shading.py).
- Tiling patterns (PatternType 1) repeat a small content stream every
  XStep / YStep. The tile is rendered once, at device resolution, into an
  offscreen canvas -- twice, over white and over black, which recovers
  both its colours and its coverage (alpha) without an alpha channel in
  the rasteriser -- and each device pixel then looks up its position in
  the tile. Uncoloured tiles (PaintType 2) take their colour from the
  components given with the pattern.
"""

from __future__ import annotations

import math
from typing import Optional

from . import _matrix as mx
from ._objects import PDFStream
from ._shading import ShadingRaster, load_shading

MAX_TILE_PIXELS = 1 << 20


def _floats(doc, value, default):
    value = doc.resolve(value)
    if not isinstance(value, list):
        return default
    try:
        return [float(doc.resolve(v)) for v in value]
    except (TypeError, ValueError):
        return default


class PatternPaint:
    """Per-pixel colour source for one use of a pattern: ``sample(x, y)``
    returns ``(rgb, alpha)`` or None where the pattern leaves the pixel
    untouched."""

    def __init__(self, sample):
        self.sample = sample


def pattern_paint(
    painter, pattern, components, base_ctm, colorspace=None
) -> Optional[PatternPaint]:
    doc = painter.doc
    d = pattern.dict if isinstance(pattern, PDFStream) else pattern
    if not isinstance(d, dict):
        return None
    matrix = tuple(_floats(doc, d.get("Matrix"), [1, 0, 0, 1, 0, 0]))
    to_device = mx.multiply(matrix, base_ctm)
    kind = int(doc.resolve(d.get("PatternType", 1)) or 1)
    width, height = painter.canvas.width, painter.canvas.height
    if kind == 2:
        shading = load_shading(doc, d.get("Shading"), painter._resources_stack[-1])
        if shading is None:
            return None
        raster = ShadingRaster(shading, to_device, width, height, use_background=True)

        def sample(x, y):
            rgb = raster.color(x, y)
            return None if rgb is None else (rgb, 1.0)

        return PatternPaint(sample)
    if isinstance(pattern, PDFStream):
        return _tiling_paint(painter, pattern, components, to_device, colorspace)
    return None


def _tiling_paint(
    painter, pattern: PDFStream, components, to_device, colorspace
) -> Optional[PatternPaint]:
    doc = painter.doc
    d = pattern.dict
    bbox = _floats(doc, d.get("BBox"), None)
    if not bbox or len(bbox) != 4:
        return None
    bx0, by0 = min(bbox[0], bbox[2]), min(bbox[1], bbox[3])
    bx1, by1 = max(bbox[0], bbox[2]), max(bbox[1], bbox[3])
    xstep = float(doc.resolve(d.get("XStep", bx1 - bx0)) or (bx1 - bx0))
    ystep = float(doc.resolve(d.get("YStep", by1 - by0)) or (by1 - by0))
    xstep, ystep = abs(xstep), abs(ystep)
    if xstep <= 0 or ystep <= 0:
        return None
    inverse = mx.invert(to_device)
    if inverse is None:
        return None

    # Tile resolution: device pixels per pattern unit along each axis.
    sx = math.hypot(to_device[0], to_device[1])
    sy = math.hypot(to_device[2], to_device[3])
    tw = max(1, int(math.ceil(xstep * sx)))
    th = max(1, int(math.ceil(ystep * sy)))
    if tw * th > MAX_TILE_PIXELS:
        scale = math.sqrt(MAX_TILE_PIXELS / (tw * th))
        tw, th = max(1, int(tw * scale)), max(1, int(th * scale))
    # pattern space (one cell starting at the BBox origin) -> tile pixels, Y down
    tile_ctm = (
        tw / xstep,
        0.0,
        0.0,
        -th / ystep,
        -bx0 * tw / xstep,
        (by0 + ystep) * th / ystep,
    )

    uncolored = int(doc.resolve(d.get("PaintType", 1)) or 1) == 2
    over_white = _render_tile(painter, pattern, tile_ctm, tw, th, (255, 255, 255), bbox)
    over_black = _render_tile(painter, pattern, tile_ctm, tw, th, (0, 0, 0), bbox)
    color = None
    if uncolored:
        base = getattr(colorspace, "base", None)
        if base is not None and components:
            try:
                color = base.to_rgb(list(components))
            except Exception:
                color = None
        color = color or (0, 0, 0)

    alpha_cache: dict = {}

    def texel(i: int):
        hit = alpha_cache.get(i)
        if hit is None:
            o = 3 * i
            w = over_white[o : o + 3]
            k = over_black[o : o + 3]
            a = 1.0 - sum(w[c] - k[c] for c in range(3)) / (3 * 255.0)
            if a <= 0.004:
                hit = (None, 0.0)
            elif uncolored:
                hit = (color, a)
            else:
                hit = (tuple(min(255, round(k[c] / a)) for c in range(3)), a)
            alpha_cache[i] = hit
        return hit

    def sample(x, y):
        u, v = mx.apply(inverse, x + 0.5, y + 0.5)
        fu = ((u - bx0) % xstep) / xstep
        fv = ((v - by0) % ystep) / ystep
        tx = min(tw - 1, int(fu * tw))
        ty = min(th - 1, int((1.0 - fv) * th))
        rgb, a = texel(ty * tw + tx)
        return None if rgb is None else (rgb, a)

    return PatternPaint(sample)


def _render_tile(
    painter, pattern: PDFStream, tile_ctm, tw: int, th: int, background, bbox
) -> bytearray:
    from ._contentpaint import ContentPainter
    from ._raster import Canvas

    doc = painter.doc
    canvas = Canvas(tw, th, background=background)
    resources = doc.resolve(pattern.dict.get("Resources"))
    if not isinstance(resources, dict):
        resources = painter._resources_stack[-1]
    sub = ContentPainter(doc, canvas, resources, tile_ctm)
    sub._depth = painter._depth + 1
    if sub._depth > painter.MAX_XOBJECT_DEPTH:
        return canvas.pixels
    # the tile's content is clipped to its BBox
    x0, y0, x1, y1 = bbox
    corners = [
        mx.apply(tile_ctm, x0, y0),
        mx.apply(tile_ctm, x1, y0),
        mx.apply(tile_ctm, x1, y1),
        mx.apply(tile_ctm, x0, y1),
    ]
    sub.gs.clip = sub.gs.clip.intersect_shape([corners])
    try:
        sub.run(pattern.get_data(resolver=doc.resolve))
    except Exception:
        pass
    return canvas.pixels
