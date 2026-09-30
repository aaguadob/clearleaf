"""Redaction: remove content under rectangles, for real.

``page.add_redact_annot(rect)`` marks an area; ``page.apply_redactions()``
then rewrites the page so that what was there no longer exists in the file:

- text: every glyph whose box lies mostly inside a rectangle (or whose
  centre does) is removed from the text operators; the glyphs around it
  keep their positions. Marked-content /ActualText, /Alt and /E that
  covered removed glyphs are deleted too.
- images: the covered pixels are overwritten with the fill color and the
  image is re-encoded (Flate); an image that can't be decoded (e.g. JPX)
  is removed entirely. Inline images that touch a rectangle are removed.
- form XObjects are redacted the same way (a redacted copy replaces them on
  this page; other pages using the same form are untouched).
- annotations and form fields overlapping a rectangle are removed.

Vector graphics (lines, fills) are not removed; they are covered by the
fill box when one is drawn. The document outline, metadata and the
structure tree's alternate text are not searched.
"""

from __future__ import annotations

import zlib

from . import _matrix as mx
from .errors import public
from ._annots import _numbers
from ._content import iter_operations
from ._edit import color_ops, flate_stream
from ._objects import PDFName, PDFStream
from ._textextract import _DISPATCH, TextExtractor
from ._writer import serialize_value

_STRIPPED_PROPS = ("ActualText", "Alt", "E")


def _overlap(box, rect) -> float:
    ix0, iy0 = max(box[0], rect[0]), max(box[1], rect[1])
    ix1, iy1 = min(box[2], rect[2]), min(box[3], rect[3])
    return max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)


def _box(points) -> tuple:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _covered(box, rects) -> bool:
    area = (box[2] - box[0]) * (box[3] - box[1])
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for r in rects:
        if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
            return True
        if area > 0 and _overlap(box, r) >= 0.3 * area:
            return True
    return False


def _touches(box, rects) -> bool:
    return any(_overlap(box, r) > 0 for r in rects)


class _Redactor(TextExtractor):
    """Walks one content stream like the text extractor and records what
    lies under the rectangles; ``rewrite()`` returns the stream without it."""

    def __init__(self, doc, resources: dict, ctm, rects, fill, depth: int = 0):
        super().__init__(doc, resources, ctm=ctm)
        self.rects = rects
        self.fill = fill
        self._depth = depth
        self.resources = resources  # replaced by a copy once something is added
        self._own = False
        self._op_index = -1
        self._item = None
        self.removed: dict = {}  # (op index, TJ item or None) -> (glyph slices, removed flags, advances)
        self.renamed: dict = {}  # op index -> new XObject name
        self.replaced_names: set = set()  # XObject names whose uses were renamed or dropped
        self.dropped: set = set()  # op indices to leave out
        self.touched_bdc: set = set()
        self._bdc_stack: list = []
        self.changed = False

    # -- walking ---------------------------------------------------------------------

    def process(self, data: bytes) -> list:
        ops = list(iter_operations(data))
        for i, (op, operands) in enumerate(ops):
            self._op_index = i
            if op == "INLINE_IMAGE":
                self._inline_image(i)
                continue
            if op in ("BDC", "BMC"):
                self._bdc_stack.append(i if op == "BDC" else None)
            elif op == "EMC" and self._bdc_stack:
                self._bdc_stack.pop()
            handler = _REDACT_DISPATCH.get(op)
            if handler is None:
                continue
            self._item = None
            try:
                handler(self, operands)
            except Exception:
                continue
        return ops

    def _op_TJ(self, operands):
        for i, item in enumerate(operands[-1]):
            self._item = i
            if isinstance(item, (bytes, bytearray)):
                self._show_text(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                tx = (-item / 1000.0) * self.gs.font_size * self.gs.h_scale
                self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)
        self._item = None

    def _show_text(self, s):
        font = self.gs.font
        if font is not None and isinstance(s, (bytes, bytearray)) and s:
            self._record_glyphs(font, bytes(s))
        super()._show_text(s)

    def _record_glyphs(self, font, s: bytes) -> None:
        size, h, rise = self.gs.font_size, self.gs.h_scale, self.gs.rise
        tm = self.tm
        slices, flags, advances = [], [], []
        pos = 0
        any_removed = False
        for code, nbytes, _text, width1000 in font.decode(s):
            w0 = width1000 / 1000.0
            extra = self.gs.word_space if (nbytes == 1 and code == 0x20) else 0.0
            tx = (w0 * size + self.gs.char_space + extra) * h
            trm = mx.multiply(tm, self.gs.ctm)
            glyph_w = w0 * size * h
            corners = [mx.apply(trm, x, rise + y * size) for x in (0.0, glyph_w) for y in (-0.2, 0.8)]
            covered = _covered(_box(corners), self.rects)
            slices.append(s[pos:pos + nbytes])
            flags.append(covered)
            advances.append(tx)
            any_removed = any_removed or covered
            pos += nbytes
            tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
        if any_removed:
            self.removed[(self._op_index, self._item)] = (slices, flags, advances, size * h)
            self.touched_bdc.update(i for i in self._bdc_stack if i is not None)
            self.changed = True

    def _inline_image(self, index: int) -> None:
        corners = [mx.apply(self.gs.ctm, x, y) for x in (0, 1) for y in (0, 1)]
        if _touches(_box(corners), self.rects):
            self.dropped.add(index)
            self.changed = True

    def _op_Do(self, operands):
        name = str(operands[-1])
        container = self.doc.resolve(self._resources_stack[-1].get("XObject")) if isinstance(
            self._resources_stack[-1], dict) else None
        xobj = self.doc.resolve(container.get(name)) if isinstance(container, dict) else None
        if not isinstance(xobj, PDFStream):
            return
        subtype = str(self.doc.resolve(xobj.dict.get("Subtype", "")))
        if subtype == "Image":
            corners = [mx.apply(self.gs.ctm, x, y) for x in (0, 1) for y in (0, 1)]
            if not _touches(_box(corners), self.rects):
                return
            self.replaced_names.add(name)
            replacement = blank_image(self.doc, xobj, self._resources_stack[-1], self.gs.ctm, self.rects, self.fill)
            if replacement is None:
                self.dropped.add(self._op_index)
            else:
                self.renamed[self._op_index] = self._add_xobject("CLR", self.doc._new_ref(replacement))
            self.changed = True
        elif subtype == "Form" and self._depth < self.MAX_XOBJECT_DEPTH:
            new_form = redact_form(self.doc, xobj, self._resources_stack[-1], self.gs.ctm, self.rects, self.fill,
                                   self._depth + 1)
            if new_form is not None:
                self.replaced_names.add(name)
                self.renamed[self._op_index] = self._add_xobject("CLR", self.doc._new_ref(new_form))
                self.changed = True

    def _own_xobjects(self) -> dict:
        """This stream's /XObject resources as a dictionary it may change."""
        if not self._own:
            res = dict(self.resources)
            xo = self.doc.resolve(res.get("XObject"))
            res["XObject"] = dict(xo) if isinstance(xo, dict) else {}
            self.resources = res
            self._resources_stack[0] = res
            self._own = True
        return self.resources["XObject"]

    def _add_xobject(self, prefix: str, ref) -> str:
        group = self._own_xobjects()
        i = 1
        while f"{prefix}{i}" in group:
            i += 1
        group[f"{prefix}{i}"] = ref
        return f"{prefix}{i}"

    # -- rewriting -------------------------------------------------------------------

    def rewrite(self, ops: list) -> bytes:
        self._prune(ops)
        out = []
        for i, (op, operands) in enumerate(ops):
            if i in self.dropped:
                continue
            if op == "INLINE_IMAGE":
                image_dict, raw = operands
                out.append(b"BI " + b" ".join(serialize_value(PDFName(k)) + b" " + serialize_value(v)
                                              for k, v in image_dict.items()) + b" ID " + raw + b"\nEI")
                continue
            if i in self.renamed:
                operands = [PDFName(self.renamed[i])]
            elif op == "BDC" and i in self.touched_bdc and operands and isinstance(operands[-1], dict):
                operands = [*operands[:-1], {k: v for k, v in operands[-1].items() if k not in _STRIPPED_PROPS}]
            elif op in ("Tj", "'", '"') and (i, None) in self.removed:
                array = self._tj_array(self.removed[(i, None)])
                prefix = b""
                if op == "'":
                    prefix = b"T* "
                elif op == '"':
                    prefix = serialize_value(operands[-3]) + b" Tw " + serialize_value(operands[-2]) + b" Tc T* "
                out.append(prefix + serialize_value(array) + b" TJ")
                continue
            elif op == "TJ" and operands and isinstance(operands[-1], list):
                items = []
                for j, item in enumerate(operands[-1]):
                    if (i, j) in self.removed:
                        items += self._tj_array(self.removed[(i, j)])
                    else:
                        items.append(item)
                operands = [items]
            out.append(b" ".join(serialize_value(o) for o in operands) + (b" " if operands else b"") + op.encode("latin-1"))
        return b"\n".join(out)

    def _prune(self, ops: list) -> None:
        """Take replaced XObjects out of the resources when nothing uses
        them any more, so the unredacted originals aren't saved."""
        used = {str(operands[-1]) for i, (op, operands) in enumerate(ops)
                if op == "Do" and operands and i not in self.renamed and i not in self.dropped}
        unused = self.replaced_names - used
        if not unused:
            return
        group = self._own_xobjects()
        for name in unused:
            group.pop(name, None)

    @staticmethod
    def _tj_array(record) -> list:
        """The kept glyphs, with each removed glyph replaced by the move it
        would have made."""
        slices, flags, advances, scale = record
        items, run, gap = [], b"", 0.0
        for data, removed, tx in zip(slices, flags, advances):
            if removed:
                if run:
                    items.append(run)
                    run = b""
                gap += tx
            else:
                if gap:
                    items.append(round(-gap * 1000.0 / scale, 3) if scale else 0)
                    gap = 0.0
                run += data
        if run:
            items.append(run)
        if gap:
            items.append(round(-gap * 1000.0 / scale, 3) if scale else 0)
        return items


_REDACT_DISPATCH = {**_DISPATCH, "TJ": _Redactor._op_TJ, "Do": _Redactor._op_Do}


def redact_form(doc, xobj: PDFStream, resources, ctm, rects, fill, depth: int):
    """A redacted copy of a form XObject, or None if nothing in it is covered."""
    matrix = doc.resolve(xobj.dict.get("Matrix")) or [1, 0, 0, 1, 0, 0]
    form_ctm = mx.multiply(tuple(float(doc.resolve(v)) for v in matrix), ctm)
    form_resources = doc.resolve(xobj.dict.get("Resources"))
    if not isinstance(form_resources, dict):
        form_resources = resources if isinstance(resources, dict) else {}
    redactor = _Redactor(doc, form_resources, form_ctm, rects, fill, depth)
    try:
        ops = redactor.process(xobj.get_data(resolver=doc.resolve))
    except Exception:
        return None
    if not redactor.changed:
        return None
    d = {k: v for k, v in xobj.dict.items() if k not in ("Filter", "DecodeParms", "Length")}
    d["Resources"] = redactor.resources
    return flate_stream(redactor.rewrite(ops), d)


def blank_image(doc, xobj: PDFStream, resources, ctm, rects, fill):
    """The image with the pixels under ``rects`` overwritten, as a Flate
    RGB image (a stencil mask stays a mask, with those pixels unpainted);
    None if the image can't be decoded."""
    d = xobj.dict
    r = doc.resolve
    try:
        width, height = int(r(d.get("Width"))), int(r(d.get("Height")))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    try:
        inverse = mx.invert(ctm)
    except Exception:
        return None
    boxes = []
    for rect in rects:
        pts = [mx.apply(inverse, x, y) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
        u0, v0, u1, v1 = _box(pts)
        # unit square -> pixels: x = u * width, row = (1 - v) * height
        x0, x1 = max(0, int(u0 * width)), min(width, int(u1 * width + 1))
        y0, y1 = max(0, int((1 - v1) * height)), min(height, int((1 - v0) * height + 1))
        if x0 < x1 and y0 < y1:
            boxes.append((x0, y0, x1, y1))
    if bool(r(d.get("ImageMask", False))):
        try:
            data = bytearray(xobj.get_data(resolver=r))
        except Exception:
            return None
        decode = r(d.get("Decode"))
        unpainted = 0 if isinstance(decode, list) and decode and float(r(decode[0])) == 1.0 else 1
        row = (width + 7) // 8
        data = data.ljust(row * height, b"\0")
        for x0, y0, x1, y1 in boxes:
            for y in range(y0, y1):
                for x in range(x0, x1):
                    bit = 0x80 >> (x % 8)
                    if unpainted:
                        data[y * row + x // 8] |= bit
                    else:
                        data[y * row + x // 8] &= ~bit & 0xFF
        new = {k: v for k, v in d.items() if k not in ("Filter", "DecodeParms", "Length")}
        new["Filter"] = "FlateDecode"
        return PDFStream(new, zlib.compress(bytes(data)))
    rgb = image_rgb(doc, xobj, resources)
    if rgb is None:
        return None
    pixels = bytearray(rgb)
    color = bytes(int(round(max(0.0, min(1.0, float(c))) * 255)) for c in (fill or (0, 0, 0)))
    if len(color) == 1:
        color = color * 3
    for x0, y0, x1, y1 in boxes:
        run = color[:3] * (x1 - x0)
        for y in range(y0, y1):
            start = (y * width + x0) * 3
            pixels[start:start + len(run)] = run
    new = {k: v for k, v in d.items() if k not in ("Filter", "DecodeParms", "Length", "Decode", "ColorSpace",
                                                   "BitsPerComponent", "Mask")}
    new.update({"ColorSpace": "DeviceRGB", "BitsPerComponent": 8, "Filter": "FlateDecode"})
    return PDFStream(new, zlib.compress(bytes(pixels)))


def image_rgb(doc, xobj: PDFStream, resources):
    """8-bit RGB samples of an image XObject, or None."""
    from ._colorspace import DEVICE_CMYK, load_colorspace
    from ._images import _sampled_to_rgb
    from ._jpeg import decode_jpeg

    d = xobj.dict
    r = doc.resolve
    width, height = int(r(d.get("Width"))), int(r(d.get("Height")))
    filt = r(d.get("Filter"))
    names = [str(r(f)) for f in filt] if isinstance(filt, list) else ([str(filt)] if filt else [])
    try:
        if names and names[-1] in ("DCTDecode", "DCT"):
            from ._filters import decode_stream

            raw = xobj.raw_data
            for name in names[:-1]:
                raw = decode_stream({"Filter": name}, raw, resolver=r)
            pixels, jw, jh, ncomp = decode_jpeg(raw)
            if (jw, jh) != (width, height):
                return None
            if ncomp == 3:
                return bytes(pixels)
            if ncomp == 1:
                return bytes(v for v in pixels for _ in range(3))
            if ncomp == 4:
                out = bytearray()
                for o in range(0, len(pixels), 4):
                    out += bytes(DEVICE_CMYK.to_rgb([pixels[o + i] / 255.0 for i in range(4)]))
                return bytes(out)
            return None
        if names and names[-1] in ("JPXDecode",):
            return None
        data = xobj.get_data(resolver=r)
        cs = load_colorspace(doc, d.get("ColorSpace"), resources if isinstance(resources, dict) else {})
        bpc = int(r(d.get("BitsPerComponent", 8)) or 8)
        return _sampled_to_rgb(data, width, height, cs, bpc)
    except Exception:
        return None


class PageRedaction:
    """Mixed into Page."""

    @public
    def add_redact_annot(self, rect, fill=(0, 0, 0)):
        """Mark ``rect`` for redaction (shown as a red outline until
        ``apply_redactions()``); ``fill=None`` leaves the area blank."""
        from ._annotate import _appearance

        x0, y0, x1, y1 = self._rect_to_pdf(rect)
        ap = _appearance([x0, y0, x1, y1], b"q 1 0 0 RG 1 w %.3f %.3f %.3f %.3f re S Q" % (
            x0 + 0.5, y0 + 0.5, x1 - x0 - 1, y1 - y0 - 1))
        extra = {"IC": [float(c) for c in fill]} if fill is not None else {}
        return self._add_annot("Redact", [x0, y0, x1, y1], ap, (1, 0, 0), **extra)

    @public
    def apply_redactions(self) -> int:
        """Remove everything under this page's redaction marks; returns how
        many marks were applied."""
        doc = self._doc
        marks = self.annots(types=("Redact",))
        if not marks:
            return 0
        rects, fills = [], []
        for a in marks:
            r = _numbers(doc, a.dict.get("Rect"), 4)
            if r is None:
                continue
            rects.append((min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])))
            ic = doc.resolve(a.dict.get("IC"))
            fills.append(tuple(float(doc.resolve(v)) for v in ic) if isinstance(ic, list) else None)
        fill = next((f for f in fills if f is not None), (1, 1, 1))
        redactor = _Redactor(doc, self.resources, mx.IDENTITY, rects, fill)
        ops = redactor.process(self.get_contents_raw())
        content = redactor.rewrite(ops) if redactor.changed else self.get_contents_raw()
        self.dict["Resources"] = redactor.resources
        boxes = [b"%s %.3f %.3f %.3f %.3f re f" % (color_ops(f), r[0], r[1], r[2] - r[0], r[3] - r[1])
                 for r, f in zip(rects, fills) if f is not None]
        self.dict["Contents"] = doc._new_ref(flate_stream(b"q\n" + content + b"\nQ\n" + b"\n".join(boxes)))
        self._wrapped = True
        # annotations and form fields under a mark go too (their appearance
        # can carry the same content), and the marks themselves
        annots = self._annots_list()
        kept = []
        for ref in annots:
            annot = doc.resolve(ref)
            if not isinstance(annot, dict):
                continue
            r = _numbers(doc, annot.get("Rect"), 4)
            subtype = str(doc.resolve(annot.get("Subtype", "")))
            if subtype == "Redact":
                continue
            if r is not None and subtype not in ("Popup", "Link") and _touches(
                    (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])), rects):
                continue
            kept.append(ref)
        annots[:] = kept
        doc.is_dirty = True
        return len(rects)

    @public
    def replace_text(self, rect, text: str, fontsize=None, fontname: str = "helv", color=None) -> None:
        """Replace the text inside ``rect`` with ``text``: the old glyphs are
        removed and the new text is written at the first one's baseline, in
        a standard font (same size and color unless given)."""
        spans = [s for b in self.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
        x0, y0, x1, y1 = rect
        inside = [s for s in spans if s["bbox"][0] < x1 and s["bbox"][2] > x0 and s["bbox"][1] < y1 and s["bbox"][3] > y0]
        first = min(inside, key=lambda s: (round(s["origin"][1]), s["origin"][0])) if inside else None
        origin = first["origin"] if first else (x0, y1 - 2)
        size = fontsize or (first["size"] if first else 11)
        if color is None and first:
            c = first["color"]
            color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
        self.add_redact_annot(rect, fill=None)
        self.apply_redactions()
        if text:
            self.insert_text(origin, text, fontsize=size, fontname=fontname, color=color or (0, 0, 0))
