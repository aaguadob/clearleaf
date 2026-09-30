"""Blanks on a form that has no form fields: runs of underscores or dots,
lines to write on, empty boxes and check boxes drawn on the page.

``page.find_blanks()`` returns where they are and where text typed into
them should sit (its baseline and a font size that fits), so an editor can
treat them like form fields.
"""

from __future__ import annotations

import re

from . import _matrix as mx
from .errors import public
from ._content import iter_operations
from ._objects import PDFStream

_LEADER = re.compile(r"_{3,}|\.{4,}|…{2,}")
_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}


class _Paths:
    """Axis-aligned rectangles and horizontal lines the page paints."""

    MAX_DEPTH = 6

    def __init__(self, doc):
        self.doc = doc
        self.rules: list = []  # (x0, x1, y) in PDF space
        self.boxes: list = []  # (x0, y0, x1, y1) in PDF space

    def run(self, data: bytes, resources, ctm=mx.IDENTITY, depth: int = 0) -> None:
        stack, subpaths, current = [], [], None
        rects = []
        for op, operands in iter_operations(data):
            try:
                if op == "q":
                    stack.append(ctm)
                elif op == "Q" and stack:
                    ctm = stack.pop()
                elif op == "cm":
                    ctm = mx.multiply(tuple(float(v) for v in operands[-6:]), ctm)
                elif op == "m":
                    current = [mx.apply(ctm, float(operands[-2]), float(operands[-1]))]
                    subpaths.append(current)
                elif op == "l" and current is not None:
                    current.append(mx.apply(ctm, float(operands[-2]), float(operands[-1])))
                elif op in ("c", "v", "y"):
                    current = None  # curves: not a blank
                    subpaths.append(None)
                elif op == "re":
                    x, y, w, h = (float(v) for v in operands[-4:])
                    pts = [mx.apply(ctm, px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
                    rects.append(pts)
                elif op in _PAINT or op == "n":
                    if op != "n":
                        self._collect(subpaths, rects)
                    subpaths, rects, current = [], [], None
                elif op == "Do" and depth < self.MAX_DEPTH:
                    self._form(str(operands[-1]), resources, ctm, depth)
            except (TypeError, ValueError, IndexError):
                continue

    def _form(self, name, resources, ctm, depth):
        xobjects = self.doc.resolve(resources.get("XObject")) if isinstance(resources, dict) else None
        xobj = self.doc.resolve(xobjects.get(name)) if isinstance(xobjects, dict) else None
        if not isinstance(xobj, PDFStream) or str(self.doc.resolve(xobj.dict.get("Subtype"))) != "Form":
            return
        matrix = self.doc.resolve(xobj.dict.get("Matrix")) or [1, 0, 0, 1, 0, 0]
        form_ctm = mx.multiply(tuple(float(self.doc.resolve(v)) for v in matrix), ctm)
        res = self.doc.resolve(xobj.dict.get("Resources"))
        try:
            self.run(xobj.get_data(resolver=self.doc.resolve), res if isinstance(res, dict) else resources,
                     form_ctm, depth + 1)
        except Exception:
            pass

    def _collect(self, subpaths, rects):
        for pts in rects:
            self._shape(pts)
        for pts in subpaths:
            if not pts or len(pts) < 2:
                continue
            if len(pts) == 2:
                (x0, y0), (x1, y1) = pts
                if abs(y1 - y0) < 0.5:
                    self.rules.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            elif len(pts) in (4, 5):
                self._shape(pts[:4])

    def _shape(self, pts):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        # only rectangles that stay rectangles on the page
        if len({round(x, 1) for x in xs}) > 2 or len({round(y, 1) for y in ys}) > 2:
            return
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if y1 - y0 <= 2.0:
            self.rules.append((x0, x1, (y0 + y1) / 2))
        else:
            self.boxes.append((x0, y0, x1, y1))


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


class PageBlanks:
    @public
    def find_blanks(self) -> list:
        """Places on the page meant to be written in that aren't form fields:
        ``rect`` (page coordinates), ``baseline`` and ``size`` for the text
        typed there, and ``kind``: "line", "box" or "check"."""
        lines = self.text_lines()
        width, height = self.rect[2], self.rect[3]
        fields = [w.rect for w in self.widgets()]
        blanks = []

        def text_in(region, skip=None):
            return [l for l in lines if l is not skip and not l["hidden"] and _overlap(l["bbox"], region) > 0.3 * min(
                _area(l["bbox"]), _area(region))]

        # underscores and dot leaders in the text
        for l in lines:
            if l["angle"] != 0 or "carets" not in l:
                continue
            for m in _LEADER.finditer(l["text"]):
                x0, x1 = l["carets"][m.start()], l["carets"][m.end()]
                size = min(l["size"], 12)
                region = (x0, l["baseline"] - size * 1.05, x1, l["baseline"] + size * 0.15)
                if x1 - x0 > 8 and not text_in(region, skip=l):
                    blanks.append({"kind": "line", "rect": region, "baseline": l["baseline"], "size": size})

        paths = _Paths(self._doc)
        try:
            paths.run(self.get_contents_raw(), self.resources)
        except Exception:
            return []

        def label_size(y):
            near = [l["size"] for l in lines if abs(l.get("baseline", -99) - y) < 8]
            return min(max(near), 12) if near else 10

        def labelled(x0, x1, y):
            """A line to write on has its label before it on the same row
            ("Email: ____") or a short caption right under it ("Signature")."""
            for l in lines:
                b = l["bbox"]
                if y - 16 <= l.get("baseline", -99) <= y + 3 and x0 - 60 <= b[2] <= x0 + 4:
                    return True
                if y < b[1] < y + 12 and abs(b[0] - x0) < 12 and b[2] - b[0] < 0.6 * (x1 - x0):
                    # not the rule above a footnote, which has text running on past it
                    return not any(y < m["bbox"][1] < y + 14 and m["bbox"][2] > x1 + 10 for m in lines)
            return False

        for x0, x1, y in paths.rules:
            x0, y, x1, _ = self._rect_from_pdf((x0, y, x1, y))
            length = x1 - x0
            if length < 30 or length > 0.85 * width:
                continue
            size = label_size(y - 2)
            region = (x0, y - size * 1.4, x1, y)
            if not text_in(region) and labelled(x0, x1, y):
                blanks.append({"kind": "line", "rect": region, "baseline": y - 2.5, "size": size})

        for box in paths.boxes:
            x0, y0, x1, y1 = self._rect_from_pdf(box)
            w, h = x1 - x0, y1 - y0
            if 5 <= w <= 16 and 5 <= h <= 16 and abs(w - h) <= 2:
                if not text_in((x0, y0, x1, y1)):
                    size = h * 0.8
                    blanks.append({"kind": "check", "rect": (x0, y0, x1, y1), "baseline": y1 - (h - size * 0.72) / 2,
                                   "size": round(size, 2)})
                continue
            if w < 24 or h < 9 or h > 60 or w > 0.95 * width:
                continue
            inside = text_in((x0, y0, x1, y1))
            if inside:  # a label at the top of the box: the blank is what's left below it
                y0 = max(l["bbox"][3] for l in inside) + 1
                h = y1 - y0
                if h < 9 or any(l["bbox"][1] > y0 - 1 for l in inside):
                    continue
            size = min(11, round(h * 0.6, 1))
            blanks.append({"kind": "box", "rect": (x0, y0, x1, y1), "baseline": y1 - (h - size * 0.72) / 2,
                           "size": size})

        out = []
        for b in sorted(blanks, key=lambda b: (b["kind"] != "check", _area(b["rect"]))):
            r = b["rect"]
            if any(_overlap(r, f) > 0.3 * _area(r) for f in fields):
                continue  # a real form field is there
            if any(_overlap(r, o["rect"]) > 0.5 * min(_area(r), _area(o["rect"])) for o in out):
                continue
            if r[0] < 0 or r[1] < 0 or r[2] > width or r[3] > height:
                continue
            b["rect"] = tuple(round(v, 2) for v in r)
            b["baseline"] = round(b["baseline"], 2)
            out.append(b)
        out.sort(key=lambda b: (b["rect"][1], b["rect"][0]))
        return out
