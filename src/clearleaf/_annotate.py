"""Creating, listing and deleting annotations (ISO 32000-1 section 12.5).

Every annotation clearleaf creates carries its own appearance stream, so
any viewer draws it the same way. Positions are page coordinates (see
_edit.py); colors are RGB tuples of 0..1 floats.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from .errors import PDFError, public
from ._annots import _numbers, decode_text_string
from ._edit import color_ops, encode_winansi, font_dict, get_text_length, text_string
from ._objects import PDFReference, PDFStream

_K = 0.5523  # control-point distance for a quarter circle drawn as a Bezier curve
MARKUP_TYPES = ("Highlight", "Underline", "StrikeOut", "Squiggly")


def _now() -> bytes:
    return time.strftime("D:%Y%m%d%H%M%S", time.localtime()).encode()


class Annot:
    """An annotation on a page."""

    def __init__(self, page, ref, annot: dict):
        self.page = page
        self._ref = ref
        self.dict = annot

    @property
    def type(self) -> str:
        return str(self.page._doc.resolve(self.dict.get("Subtype", "")))

    @property
    def xref(self) -> int:
        return self._ref.num if isinstance(self._ref, PDFReference) else 0

    @property
    def id(self) -> str:
        return decode_text_string(self.page._doc.resolve(self.dict.get("NM")))

    @property
    def rect(self) -> tuple:
        r = _numbers(self.page._doc, self.dict.get("Rect"), 4)
        return self.page._rect_from_pdf(r) if r else (0.0, 0.0, 0.0, 0.0)

    @property
    def info(self) -> dict:
        r = self.page._doc.resolve
        return {"content": decode_text_string(r(self.dict.get("Contents"))),
                "title": decode_text_string(r(self.dict.get("T"))),
                "id": self.id}

    @property
    def colors(self) -> dict:
        r = self.page._doc.resolve
        c = r(self.dict.get("C"))
        ic = r(self.dict.get("IC"))
        return {"stroke": tuple(float(r(v)) for v in c) if isinstance(c, list) else None,
                "fill": tuple(float(r(v)) for v in ic) if isinstance(ic, list) else None}

    def __repr__(self) -> str:
        return f"Annot({self.type!r}, rect={tuple(round(v, 1) for v in self.rect)})"


def _appearance(bbox, content: bytes, resources: Optional[dict] = None) -> PDFStream:
    from ._edit import flate_stream

    d = {"Type": "XObject", "Subtype": "Form", "BBox": [float(v) for v in bbox]}
    if resources:
        d["Resources"] = resources
    return flate_stream(content, d)


def _grow(rect, by: float) -> list:
    x0, y0, x1, y1 = rect
    return [x0 - by, y0 - by, x1 + by, y1 + by]


class PageAnnotations:
    """Mixed into Page."""

    def _annots_list(self) -> list:
        annots = self._doc.resolve(self.dict.get("Annots"))
        annots = list(annots) if isinstance(annots, list) else []
        self.dict["Annots"] = annots
        return annots

    def annots(self, types=None) -> list:
        out = []
        annots = self._doc.resolve(self.dict.get("Annots"))
        for ref in annots if isinstance(annots, list) else []:
            annot = self._doc.resolve(ref)
            if not isinstance(annot, dict):
                continue
            a = Annot(self, ref, annot)
            if a.type in ("Popup", "Widget") or (types is not None and a.type not in types):
                continue
            out.append(a)
        return out

    def delete_annot(self, annot: Annot) -> None:
        annots = self._annots_list()
        popup = self._doc.resolve(annot.dict.get("Popup"))
        annots[:] = [a for a in annots if self._doc.resolve(a) is not annot.dict and self._doc.resolve(a) is not popup]
        self._doc.is_dirty = True

    def _add_annot(self, subtype: str, rect_pdf, appearance: PDFStream, color=None, content: str = "",
                   opacity: float = 1.0, **extra) -> Annot:
        doc = self._doc
        annot = {"Type": "Annot", "Subtype": subtype, "Rect": [round(float(v), 3) for v in rect_pdf],
                 "F": 4, "NM": text_string(str(uuid.uuid4())), "M": _now(),
                 "AP": {"N": doc._new_ref(appearance)}}
        if color is not None:
            annot["C"] = [float(c) for c in color]
        if content:
            annot["Contents"] = text_string(content)
        if opacity != 1.0:
            annot["CA"] = float(opacity)
        annot.update(extra)
        ref = doc._new_ref(annot)
        self._annots_list().append(ref)
        doc.is_dirty = True
        return Annot(self, ref, annot)

    # -- text markup ----------------------------------------------------------------

    def _markup(self, subtype: str, rects, color, opacity: float, content: str) -> Annot:
        if not rects:
            raise PDFError("text markup needs at least one rectangle")
        if isinstance(rects[0], (int, float)):
            rects = [rects]
        quads, ops = [], []
        for r in rects:
            x0, y0, x1, y1 = self._rect_to_pdf(r)
            quads += [x0, y1, x1, y1, x0, y0, x1, y0]  # upper-left, upper-right, lower-left, lower-right
            h = y1 - y0
            if subtype == "Highlight":
                ops.append(b"%.3f %.3f %.3f %.3f re f" % (x0, y0, x1 - x0, h))
            elif subtype == "Underline":
                ops.append(b"%.3f w %.3f %.3f m %.3f %.3f l S" % (max(0.5, h / 14), x0, y0 + h * 0.07, x1, y0 + h * 0.07))
            elif subtype == "StrikeOut":
                ops.append(b"%.3f w %.3f %.3f m %.3f %.3f l S" % (max(0.5, h / 14), x0, y0 + h * 0.45, x1, y0 + h * 0.45))
            else:  # Squiggly
                step, amp, x = max(1.5, h / 8), max(0.8, h / 16), x0
                path = [b"%.3f w %.3f %.3f m" % (max(0.5, h / 20), x0, y0 + amp)]
                up = False
                while x < x1:
                    x = min(x1, x + step)
                    path.append(b"%.3f %.3f l" % (x, y0 + (2 * amp if up else 0)))
                    up = not up
                ops.append(b" ".join(path) + b" S")
        xs, ys = quads[0::2], quads[1::2]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        resources = None
        fill = color_ops(color) + b" " + color_ops(color, stroke=True)
        if subtype == "Highlight":
            resources = {"ExtGState": {"H": {"Type": "ExtGState", "BM": "Multiply", "ca": float(opacity), "CA": float(opacity)}}}
            fill = b"/H gs " + fill
        ap = _appearance(bbox, b"q " + fill + b"\n" + b"\n".join(ops) + b"\nQ", resources)
        return self._add_annot(subtype, bbox, ap, color, content, 1.0 if subtype == "Highlight" else opacity,
                               QuadPoints=[round(v, 3) for v in quads])

    @public
    def add_highlight_annot(self, rects, color=(1, 0.9, 0), opacity: float = 1.0, content: str = "") -> Annot:
        return self._markup("Highlight", rects, color, opacity, content)

    @public
    def add_underline_annot(self, rects, color=(0, 0, 1), opacity: float = 1.0, content: str = "") -> Annot:
        return self._markup("Underline", rects, color, opacity, content)

    @public
    def add_strikeout_annot(self, rects, color=(1, 0, 0), opacity: float = 1.0, content: str = "") -> Annot:
        return self._markup("StrikeOut", rects, color, opacity, content)

    @public
    def add_squiggly_annot(self, rects, color=(1, 0, 0), opacity: float = 1.0, content: str = "") -> Annot:
        return self._markup("Squiggly", rects, color, opacity, content)

    # -- notes and text ------------------------------------------------------------

    @public
    def add_text_annot(self, point, text: str, color=(1, 0.85, 0.2), icon: str = "Note") -> Annot:
        """A sticky note: an icon at ``point`` that opens to show ``text``."""
        x, y = self._to_pdf(*point)
        x0, y0, x1, y1 = x, y - 20, x + 20, y
        ap = (b"q 0.6 w %s 0.25 0.25 0.25 RG %.3f %.3f 19 19 re B "
              b"0.25 0.25 0.25 RG %.3f %.3f m %.3f %.3f l %.3f %.3f m %.3f %.3f l %.3f %.3f m %.3f %.3f l S Q" % (
                  color_ops(color), x0 + 0.5, y0 + 0.5,
                  x0 + 4, y0 + 14, x0 + 16, y0 + 14, x0 + 4, y0 + 10, x0 + 16, y0 + 10, x0 + 4, y0 + 6, x0 + 12, y0 + 6))
        return self._add_annot("Text", [x0, y0, x1, y1], _appearance([x0, y0, x1, y1], ap), color, text,
                               Name=icon, Open=False)

    @public
    def add_freetext_annot(self, rect, text: str, fontsize: float = 12, fontname: str = "helv",
                           text_color=(0, 0, 0), fill_color=None, border_color=None, border_width: float = 1,
                           align: int = 0) -> Annot:
        """Text in a box, wrapped to its width. ``align``: 0 left, 1 centre, 2 right."""
        font = font_dict(fontname)
        x0, y0, x1, y1 = self._rect_to_pdf(rect)
        pad = 2 + (border_width if border_color is not None else 0)
        lines = wrap_text(text, fontname, fontsize, max(1.0, x1 - x0 - 2 * pad))
        ops = [b"q"]
        if fill_color is not None:
            ops.append(b"%s %.3f %.3f %.3f %.3f re f" % (color_ops(fill_color), x0, y0, x1 - x0, y1 - y0))
        if border_color is not None and border_width > 0:
            half = border_width / 2
            ops.append(b"%.3f w %s %.3f %.3f %.3f %.3f re S" % (
                border_width, color_ops(border_color, stroke=True), x0 + half, y0 + half, x1 - x0 - border_width,
                y1 - y0 - border_width))
        ops.append(b"%.3f %.3f %.3f %.3f re W n BT /Helv %.3f Tf %s" % (x0, y0, x1 - x0, y1 - y0, fontsize,
                                                                    color_ops(text_color)))
        y = y1 - pad - fontsize * 0.9
        for line in lines:
            width = get_text_length(line, fontname, fontsize)
            x = x0 + pad if align == 0 else (x0 + (x1 - x0 - width) / 2 if align == 1 else x1 - pad - width)
            ops.append(b"1 0 0 1 %.3f %.3f Tm <%s> Tj" % (x, y, encode_winansi(line).hex().encode()))
            y -= fontsize * 1.2
        ops.append(b"ET Q")
        da = b"/Helv %g Tf %s" % (fontsize, color_ops(text_color))
        ap = _appearance([x0, y0, x1, y1], b"\n".join(ops), {"Font": {"Helv": font}})
        extra = {"DA": da, "Q": int(align)}
        if border_color is None:
            extra["BS"] = {"W": 0}
        else:
            extra["BS"] = {"W": float(border_width)}
        if fill_color is not None:
            extra["IC"] = [float(c) for c in fill_color]
        return self._add_annot("FreeText", [x0, y0, x1, y1], ap, border_color, text, **extra)

    # -- drawing -------------------------------------------------------------------

    @public
    def add_ink_annot(self, strokes, color=(0, 0, 1), width: float = 2, opacity: float = 1.0) -> Annot:
        """Freehand drawing: ``strokes`` is a list of point lists."""
        ink, ops, xs, ys = [], [], [], []
        for stroke in strokes:
            pts = [self._to_pdf(float(x), float(y)) for x, y in stroke]
            if not pts:
                continue
            if len(pts) == 1:
                pts = pts * 2
            ink.append([round(v, 3) for p in pts for v in p])
            ops.append(b"%.3f %.3f m " % pts[0] + b" ".join(b"%.3f %.3f l" % p for p in pts[1:]))
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        if not ink:
            raise PDFError("an ink annotation needs at least one point")
        bbox = _grow([min(xs), min(ys), max(xs), max(ys)], width)
        ap = _appearance(bbox, b"q %s1 J 1 j %.3f w %s\n%s S Q" % (_gs(opacity), width, color_ops(color, stroke=True), b"\n".join(ops)),
                         _opacity(opacity))
        return self._add_annot("Ink", bbox, ap, color, "", opacity, InkList=ink, BS={"W": float(width)})

    def _shape(self, subtype: str, rect, color, fill, width: float, opacity: float, path) -> Annot:
        x0, y0, x1, y1 = self._rect_to_pdf(rect)
        h = width / 2
        ix0, iy0, ix1, iy1 = x0 + h, y0 + h, x1 - h, y1 - h
        paint = b"B" if fill is not None and color is not None else (b"f" if fill is not None else b"S")
        body = path(ix0, iy0, ix1, iy1)
        ap = _appearance([x0, y0, x1, y1], b"q %s%.3f w %s %s %s %s Q" % (
            _gs(opacity), width, color_ops(color, stroke=True), color_ops(fill), body, paint), _opacity(opacity))
        extra = {"BS": {"W": float(width)}}
        if fill is not None:
            extra["IC"] = [float(c) for c in fill]
        return self._add_annot(subtype, [x0, y0, x1, y1], ap, color, "", opacity, **extra)

    @public
    def add_rect_annot(self, rect, color=(1, 0, 0), fill=None, width: float = 1.5, opacity: float = 1.0) -> Annot:
        return self._shape("Square", rect, color, fill, width, opacity,
                           lambda a, b, c, d: b"%.3f %.3f %.3f %.3f re" % (a, b, c - a, d - b))

    @public
    def add_circle_annot(self, rect, color=(1, 0, 0), fill=None, width: float = 1.5, opacity: float = 1.0) -> Annot:
        def ellipse(x0, y0, x1, y1):
            cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
            kx, ky = rx * _K, ry * _K
            return (b"%.3f %.3f m " % (cx + rx, cy)
                    + b"%.3f %.3f %.3f %.3f %.3f %.3f c " % (cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry)
                    + b"%.3f %.3f %.3f %.3f %.3f %.3f c " % (cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy)
                    + b"%.3f %.3f %.3f %.3f %.3f %.3f c " % (cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry)
                    + b"%.3f %.3f %.3f %.3f %.3f %.3f c h" % (cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy))

        return self._shape("Circle", rect, color, fill, width, opacity, ellipse)

    @public
    def add_line_annot(self, p1, p2, color=(1, 0, 0), width: float = 1.5, opacity: float = 1.0) -> Annot:
        a, b = self._to_pdf(*p1), self._to_pdf(*p2)
        bbox = _grow([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])], width)
        ap = _appearance(bbox, b"q %s1 J %.3f w %s %.3f %.3f m %.3f %.3f l S Q" % (
            _gs(opacity), width, color_ops(color, stroke=True), a[0], a[1], b[0], b[1]), _opacity(opacity))
        return self._add_annot("Line", bbox, ap, color, "", opacity, L=[a[0], a[1], b[0], b[1]], BS={"W": float(width)})


def _gs(opacity: float) -> bytes:
    return b"/O gs " if opacity < 1.0 else b""


def _opacity(opacity: float):
    if opacity >= 1.0:
        return None
    return {"ExtGState": {"O": {"Type": "ExtGState", "ca": float(opacity), "CA": float(opacity)}}}


def wrap_text(text: str, fontname: str, fontsize: float, width: float) -> list:
    """Split ``text`` into lines no wider than ``width`` (breaking at spaces,
    or inside a word longer than a line)."""
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = word if not line else line + " " + word
            if get_text_length(candidate, fontname, fontsize) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
            while get_text_length(line, fontname, fontsize) > width and len(line) > 1:
                cut = len(line) - 1
                while cut > 1 and get_text_length(line[:cut], fontname, fontsize) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines
