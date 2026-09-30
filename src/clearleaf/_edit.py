"""Editing: page operations, new content, and saving.

The API follows PyMuPDF's names. Coordinates are in points, with the origin
at the top-left of the page's visible area (its CropBox) and y pointing
down, on the unrotated page -- the same space ``get_text("dict")`` reports.

Saving writes a new file from scratch: only what the edited document still
uses is copied. Deleted pages, removed annotations and redacted content are
therefore gone from the output, not merely hidden.
"""

from __future__ import annotations

import os
import struct
import zlib
from collections import deque
from typing import Iterable, Optional, Union

from .errors import PDFError, public
from ._objects import PDFReference, PDFStream
from ._writer import PDFWriter

# PyMuPDF's short names for the standard 14 fonts
STANDARD_FONTS = {
    "helv": "Helvetica", "heit": "Helvetica-Oblique", "hebo": "Helvetica-Bold", "hebi": "Helvetica-BoldOblique",
    "tiro": "Times-Roman", "tiit": "Times-Italic", "tibo": "Times-Bold", "tibi": "Times-BoldItalic",
    "cour": "Courier", "coit": "Courier-Oblique", "cobo": "Courier-Bold", "cobi": "Courier-BoldOblique",
}
_PAGE_KEYS_DROPPED = {"Parent", "Thumb", "B", "PieceInfo", "Resources", "MediaBox", "CropBox", "Rotate"}


def font_dict(fontname: str) -> dict:
    base = STANDARD_FONTS.get(fontname.lower(), fontname)
    if base not in STANDARD_FONTS.values():
        raise PDFError(f"unknown font {fontname!r}: use one of {', '.join(STANDARD_FONTS)}")
    return {"Type": "Font", "Subtype": "Type1", "BaseFont": base, "Encoding": "WinAnsiEncoding"}


def encode_winansi(text: str) -> bytes:
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = text[exc.start]
        raise PDFError(f"character {bad!r} can't be written with the built-in fonts "
                       "(they cover Western European text)") from None


def get_text_length(text: str, fontname: str = "helv", fontsize: float = 11) -> float:
    """Width of ``text`` in points, set in one of the standard fonts."""
    from ._afm import STANDARD_FONT_WIDTHS
    from ._encodings import UNICODE_TO_GLYPH_NAME

    widths = STANDARD_FONT_WIDTHS[font_dict(fontname)["BaseFont"]]
    encode_winansi(text)  # same characters as insert_text accepts
    total = 0
    for ch in text:
        name = "space" if ch == " " else UNICODE_TO_GLYPH_NAME.get(ch, ch)
        total += widths.get(name, 500)
    return total * fontsize / 1000.0


def text_string(text: str) -> bytes:
    """A PDF text string: PDFDocEncoding when it fits, else UTF-16BE."""
    try:
        data = text.encode("latin-1")
        if all(0x20 <= b < 0x7F or b in (0x0A, 0x0D, 0x09) for b in data):
            return data
    except UnicodeEncodeError:
        pass
    return b"\xfe\xff" + text.encode("utf-16-be")


def color_ops(color, stroke: bool = False) -> bytes:
    if color is None:
        return b""
    if isinstance(color, (int, float)):
        color = (color,)
    op = {1: b"G" if stroke else b"g", 3: b"RG" if stroke else b"rg", 4: b"K" if stroke else b"k"}.get(len(color))
    if op is None:
        raise PDFError(f"a color has 1, 3 or 4 components, got {color!r}")
    return b" ".join(b"%.4g" % float(c) for c in color) + b" " + op


def flate_stream(data: bytes, extra: Optional[dict] = None) -> PDFStream:
    d = dict(extra or {})
    d["Filter"] = "FlateDecode"
    return PDFStream(d, zlib.compress(data))


# -- saving -------------------------------------------------------------------------


class _Copier:
    """Copies objects from one or more source documents into a writer,
    breadth-first (no recursion along reference chains such as long
    outline lists). References to pages that aren't kept become null;
    references to removed annotations/fields are dropped from arrays."""

    def __init__(self, writer: PDFWriter, page_refs: dict, dropped: set):
        self.writer = writer
        self.page_refs = page_refs  # id(page dict) -> new reference
        self.dropped = dropped  # ids of annotation/field dicts to leave out
        self.memo: dict = {}
        self.queue: deque = deque()

    def _target(self, doc, ref):
        return doc.get_object(ref.num, ref.gen)

    def ref(self, doc, ref: PDFReference):
        target = self._target(doc, ref)
        if isinstance(target, dict) and target.get("Type") in ("Page", "Pages"):
            return self.page_refs.get(id(target))  # None: a page that isn't kept
        if id(target) in self.dropped or target is None:
            return None
        key = (id(doc), ref.num, ref.gen)
        new = self.memo.get(key)
        if new is None:
            new = PDFReference(self.writer.reserve(), 0)
            self.memo[key] = new
            self.queue.append((doc, target, new.num))
        return new

    def value(self, doc, obj):
        if isinstance(obj, PDFReference):
            return self.ref(doc, obj)
        if isinstance(obj, dict):
            return {k: self.value(doc, v) for k, v in obj.items()}
        if isinstance(obj, list):
            out = []
            for v in obj:
                if isinstance(v, PDFReference) and id(self._target(doc, v)) in self.dropped:
                    continue
                out.append(self.value(doc, v))
            return out
        if isinstance(obj, PDFStream):
            return PDFStream(self.value(doc, obj.dict), obj.raw_data)
        return obj

    def drain(self) -> None:
        while self.queue:
            doc, target, num = self.queue.popleft()
            self.writer.set(num, self.value(doc, target))


def _field_widgets(doc, field, depth=0) -> list:
    """The widget annotation dicts of a form field (the field itself when
    field and widget are merged)."""
    kids = doc.resolve(field.get("Kids")) if isinstance(field, dict) else None
    if not isinstance(kids, list) or depth > 32:
        return [field]
    out = []
    for kid in kids:
        kid = doc.resolve(kid)
        if isinstance(kid, dict):
            out += _field_widgets(doc, kid, depth + 1)
    return out


def write_document(doc) -> bytes:
    doc._require_access()
    writer = PDFWriter()
    pages_ref = PDFReference(writer.reserve(), 0)
    pages = doc._pages_list()
    page_refs = {id(p.dict): PDFReference(writer.reserve(), 0) for p in pages}

    kept_annots = set()
    for p in pages:
        annots = p._doc.resolve(p.dict.get("Annots"))
        for a in annots if isinstance(annots, list) else []:
            kept_annots.add(id(p._doc.resolve(a)))
    dropped = set()
    root = doc.root
    acroform = doc.resolve(root.get("AcroForm"))
    fields = doc.resolve(acroform.get("Fields")) if isinstance(acroform, dict) else None
    for field in fields if isinstance(fields, list) else []:
        field = doc.resolve(field)
        widgets = _field_widgets(doc, field)
        for w in widgets:
            if id(w) not in kept_annots:
                dropped.add(id(w))
        if widgets and all(id(w) in dropped for w in widgets):
            dropped.add(id(field))

    copier = _Copier(writer, page_refs, dropped)
    for p in pages:
        src = p._doc
        d = {k: copier.value(src, v) for k, v in p.dict.items() if k not in _PAGE_KEYS_DROPPED}
        d["Type"] = "Page"
        d["Parent"] = pages_ref
        d["MediaBox"] = list(p.mediabox)
        if p.cropbox != p.mediabox:
            d["CropBox"] = list(p.cropbox)
        if p.rotation:
            d["Rotate"] = p.rotation
        d["Resources"] = copier.value(src, p.resources)
        writer.set(page_refs[id(p.dict)].num, d)
    writer.set(pages_ref.num, {"Type": "Pages", "Kids": [page_refs[id(p.dict)] for p in pages], "Count": len(pages)})

    catalog = {k: copier.value(doc, v) for k, v in root.items() if k != "Pages"}
    catalog["Type"] = "Catalog"
    catalog["Pages"] = pages_ref
    catalog_ref = writer.add(catalog)
    info = copier.value(doc, doc.trailer.get("Info")) if doc.trailer.get("Info") is not None else None
    copier.drain()
    extra = {}
    if isinstance(info, (dict, PDFReference)):
        extra["Info"] = info if isinstance(info, PDFReference) else writer.add(info)
    return writer.to_bytes(catalog_ref, extra)


# -- document operations --------------------------------------------------------------


class DocumentEditing:
    """Mixed into PDFDocument."""

    def _new_ref(self, value) -> PDFReference:
        """Add a new indirect object to the document (in memory)."""
        num = max([0, *self._xref, *self._cache]) + 1
        self._cache[num] = value
        self._xref[num] = ("new", 0, 0)
        self.is_dirty = True
        return PDFReference(num, 0)

    def _renumber(self) -> None:
        for i, p in enumerate(self._pages_list()):
            p.number = i
        self.is_dirty = True

    @public
    def tobytes(self) -> bytes:
        """The edited document as PDF bytes."""
        return write_document(self)

    @public
    def save(self, path: Union[str, "os.PathLike[str]"]) -> None:
        data = write_document(self)
        with open(path, "wb") as f:  # noqa: PTH123
            f.write(data)
        self.is_dirty = False

    def _index(self, pno: int) -> int:
        n = len(self)
        i = pno + n if pno < 0 else pno
        if not 0 <= i < n:
            raise IndexError(f"page {pno} out of range (document has {n} pages)")
        return i

    def delete_page(self, pno: int = -1) -> None:
        del self._pages_list()[self._index(pno)]
        self._renumber()

    def delete_pages(self, pages: Iterable[int]) -> None:
        for i in sorted({self._index(p) for p in pages}, reverse=True):
            del self._pages_list()[i]
        self._renumber()

    def select(self, pages: Iterable[int]) -> None:
        """Keep only these pages, in this order (repeats allowed)."""
        current = self._pages_list()
        chosen = [current[self._index(p)] for p in pages]
        from .document import Page

        seen, result = set(), []
        for p in chosen:
            if id(p.dict) in seen:  # a repeated page needs its own page object
                p = Page(p._doc, dict(p.dict), dict(p._inherited), 0)
            seen.add(id(p.dict))
            result.append(p)
        current[:] = result
        self._renumber()

    def move_page(self, pno: int, to: int = -1) -> None:
        """Move page ``pno`` in front of page ``to`` (-1: to the end)."""
        pages = self._pages_list()
        i = self._index(pno)
        target = None if to == -1 or to >= len(pages) else pages[self._index(to)]
        page = pages.pop(i)
        pages.insert(len(pages) if target is None else pages.index(target), page)
        self._renumber()

    def new_page(self, pno: int = -1, width: float = 595, height: float = 842):
        from .document import Page

        pages = self._pages_list()
        page = Page(self, {"Type": "Page", "MediaBox": [0, 0, width, height], "Resources": {}}, {}, 0)
        pages.insert(len(pages) if pno == -1 else self._index(pno), page)
        self._renumber()
        return page

    @public
    def insert_pdf(self, src, from_page: int = 0, to_page: int = -1, start_at: int = -1) -> None:
        """Insert pages ``from_page``..``to_page`` (inclusive) of ``src`` (a
        PDFDocument, a path or bytes) before page ``start_at`` (-1: at the end)."""
        from .document import Page, PDFDocument

        if not isinstance(src, PDFDocument):
            src = PDFDocument.open(src)
        src._require_access()
        n = len(src)
        last = n - 1 if to_page == -1 else to_page
        step = 1 if last >= from_page else -1
        new = [src[i] for i in range(from_page, last + step, step)]
        # own page dicts, so a page inserted twice is two pages
        new = [Page(p._doc, dict(p.dict), dict(p._inherited), 0) for p in new]
        for p in new:
            p._own_resources()  # materialise inherited resources
        pages = self._pages_list()
        at = len(pages) if start_at == -1 else self._index(start_at)
        pages[at:at] = new
        self._renumber()


# -- page operations ------------------------------------------------------------------


class PageEditing:
    """Mixed into Page."""

    @property
    def rect(self) -> tuple:
        x0, y0, x1, y1 = self.cropbox
        return (0.0, 0.0, x1 - x0, y1 - y0)

    def set_rotation(self, rotation: int) -> None:
        if rotation % 90:
            raise PDFError("rotation must be a multiple of 90")
        self.dict["Rotate"] = rotation % 360
        self._doc.is_dirty = True

    # coordinates: page space (top-left origin) <-> PDF user space
    def _to_pdf(self, x: float, y: float) -> tuple:
        cx0, _, _, cy1 = self.cropbox
        return (cx0 + x, cy1 - y)

    def _rect_to_pdf(self, rect) -> list:
        x0, y0, x1, y1 = (float(v) for v in rect)
        a, b = self._to_pdf(min(x0, x1), max(y0, y1)), self._to_pdf(max(x0, x1), min(y0, y1))
        return [a[0], a[1], b[0], b[1]]

    def _from_pdf(self, x: float, y: float) -> tuple:
        cx0, _, _, cy1 = self.cropbox
        return (x - cx0, cy1 - y)

    def _rect_from_pdf(self, rect) -> tuple:
        x0, y0, x1, y1 = rect
        a, b = self._from_pdf(min(x0, x1), max(y0, y1)), self._from_pdf(max(x0, x1), min(y0, y1))
        return (a[0], a[1], b[0], b[1])

    def _own_resources(self) -> dict:
        """The page's resources as a dictionary of its own (inherited or
        shared resources are copied one level deep), ready to extend."""
        res = dict(self.resources)
        for key, value in list(res.items()):
            value = self._doc.resolve(value)
            res[key] = dict(value) if isinstance(value, dict) else value
        self.dict["Resources"] = res
        return res

    def _add_resource(self, category: str, prefix: str, obj) -> str:
        res = self._own_resources()
        group = res.get(category)
        if not isinstance(group, dict):
            group = res[category] = {}
        i = 1
        while f"{prefix}{i}" in group:
            i += 1
        name = f"{prefix}{i}"
        group[name] = obj
        return name

    def _add_content(self, ops: bytes) -> None:
        """Append drawing operations (PDF user space) to the page, isolated
        from whatever graphics state the existing content leaves behind."""
        doc = self._doc
        contents = doc.resolve(self.dict.get("Contents"))
        if contents is None:
            existing = []
        elif isinstance(contents, list):
            existing = list(self.dict["Contents"]) if isinstance(self.dict["Contents"], list) else list(contents)
        else:
            existing = [self.dict["Contents"]]
        if existing and not getattr(self, "_wrapped", False):
            existing = [doc._new_ref(flate_stream(b"q\n")), *existing, doc._new_ref(flate_stream(b"\nQ\n"))]
            self._wrapped = True
        existing.append(doc._new_ref(flate_stream(b"\nq\n" + ops + b"\nQ\n")))
        self.dict["Contents"] = existing
        doc.is_dirty = True

    @public
    def insert_text(self, point, text: str, fontsize: float = 11, fontname: str = "helv",
                    color=(0, 0, 0), lineheight: float = 1.2) -> int:
        """Write ``text`` with its first baseline starting at ``point``;
        ``\\n`` starts a new line. Returns the number of lines."""
        font = font_dict(fontname)
        lines = str(text).split("\n")
        encoded = [encode_winansi(line) for line in lines]
        name = self._add_resource("Font", "CLF", self._doc._new_ref(font))
        x, y = self._to_pdf(*point)
        ops = [b"BT /%s %.4g Tf %s" % (name.encode(), fontsize, color_ops(color))]
        for i, data in enumerate(encoded):
            ops.append(b"1 0 0 1 %.3f %.3f Tm <%s> Tj" % (x, y - i * fontsize * lineheight, data.hex().encode()))
        ops.append(b"ET")
        self._add_content(b"\n".join(ops))
        return len(lines)

    @public
    def insert_image(self, rect, filename=None, stream: Optional[bytes] = None, keep_proportion: bool = True) -> None:
        """Draw a PNG or JPEG image into ``rect``."""
        if stream is None:
            if filename is None:
                raise PDFError("insert_image needs filename or stream")
            with open(filename, "rb") as f:  # noqa: PTH123
                stream = f.read()
        image, width, height = image_xobject(bytes(stream), self._doc)
        name = self._add_resource("XObject", "CLI", self._doc._new_ref(image))
        x0, y0, x1, y1 = self._rect_to_pdf(rect)
        w, h = x1 - x0, y1 - y0
        if keep_proportion and width and height:
            scale = min(w / width, h / height)
            nw, nh = width * scale, height * scale
            x0, y0, w, h = x0 + (w - nw) / 2, y0 + (h - nh) / 2, nw, nh
        self._add_content(b"%.3f 0 0 %.3f %.3f %.3f cm /%s Do" % (w, h, x0, y0, name.encode()))


# -- images ------------------------------------------------------------------------


def image_xobject(data: bytes, doc) -> tuple:
    """(image XObject stream, width, height) for PNG or JPEG bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return _jpeg_xobject(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_xobject(data, doc)
    raise PDFError("only PNG and JPEG images can be inserted")


def _jpeg_xobject(data: bytes) -> tuple:
    i, n = 2, len(data)
    adobe = False
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xEE and data[i + 4:i + 9] == b"Adobe":
            adobe = True
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            bpc, height, width, comps = struct.unpack(">BHHB", data[i + 4:i + 10])
            space = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(comps)
            if space is None:
                raise PDFError(f"JPEG with {comps} components isn't supported")
            d = {"Type": "XObject", "Subtype": "Image", "Width": width, "Height": height,
                 "ColorSpace": space, "BitsPerComponent": bpc, "Filter": "DCTDecode"}
            if comps == 4 and adobe:
                d["Decode"] = [1, 0, 1, 0, 1, 0, 1, 0]  # Adobe CMYK JPEGs are stored inverted
            return PDFStream(d, data), width, height
        i += 2 + length
    raise PDFError("not a readable JPEG (no frame header)")


def _png_xobject(data: bytes, doc) -> tuple:
    from ._filters import _png_predictor_decode, flate_decode

    pos, idat, plte, header = 8, [], None, None
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + length
    if header is None or not idat:
        raise PDFError("not a readable PNG")
    width, height, depth, ctype, _, _, interlace = header
    if interlace:
        raise PDFError("interlaced PNG isn't supported: save it without interlacing")
    compressed = b"".join(idat)
    colors = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    base = {"Type": "XObject", "Subtype": "Image", "Width": width, "Height": height, "BitsPerComponent": depth}
    if ctype in (0, 2, 3):  # no alpha: the PNG data is already a Flate stream with PNG predictors
        d = dict(base, Filter="FlateDecode",
                 DecodeParms={"Predictor": 15, "Colors": colors, "BitsPerComponent": depth, "Columns": width})
        if ctype == 3:
            if plte is None:
                raise PDFError("palette PNG without a palette")
            d["ColorSpace"] = ["Indexed", "DeviceRGB", len(plte) // 3 - 1, plte]
        else:
            d["ColorSpace"] = "DeviceGray" if ctype == 0 else "DeviceRGB"
        return PDFStream(d, compressed), width, height
    # gray+alpha / RGBA: split the alpha channel into a soft mask
    raw = _png_predictor_decode(flate_decode(compressed), colors, depth, width)
    sample = depth // 8
    pixel = colors * sample
    color_bytes, alpha_bytes = bytearray(), bytearray()
    for i in range(0, width * height * pixel, pixel):
        color_bytes += raw[i:i + pixel - sample]
        alpha_bytes += raw[i + pixel - sample:i + pixel]
    mask = PDFStream({**base, "ColorSpace": "DeviceGray", "Filter": "FlateDecode"}, zlib.compress(bytes(alpha_bytes)))
    d = dict(base, ColorSpace="DeviceGray" if ctype == 4 else "DeviceRGB", Filter="FlateDecode",
             SMask=doc._new_ref(mask))
    return PDFStream(d, zlib.compress(bytes(color_bytes))), width, height
