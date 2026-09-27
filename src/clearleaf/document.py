"""Document-level API: opens a PDF, resolves indirect objects on demand,
and walks the page tree.

File structure and the object graph are Phase 1; content-stream
interpretation (`get_text`, `get_pixmap`, `get_images`/`extract_image`) is
Phase 2/3/4, all implemented — see `_textextract.py`, `_contentpaint.py`,
and `_images.py` respectively.
"""

from __future__ import annotations

import os
from typing import Optional, Union

from ._lexer import Lexer
from ._objects import PDFReference, PDFStream
from ._parser import ObjectParser
from ._xref import find_startxref, load_xref_chain

_INHERITABLE_PAGE_ATTRS = ("Resources", "MediaBox", "CropBox", "Rotate")
_DEFAULT_MEDIABOX = (0.0, 0.0, 612.0, 792.0)  # US Letter, per spec 7.7.3.3


class PDFDocument:
    def __init__(self, buf: bytes):
        self._buf = bytes(buf)
        start = find_startxref(self._buf)
        self._xref, self._trailer = load_xref_chain(self._buf, start)
        self._cache: dict[int, object] = {}
        self._objstm_cache: dict[int, tuple] = {}
        self._pages: Optional[list] = None

    @classmethod
    def open(cls, path: Union[str, "os.PathLike[str]", bytes, bytearray]) -> "PDFDocument":
        if isinstance(path, (bytes, bytearray)):
            return cls(bytes(path))
        with open(path, "rb") as f:  # noqa: PTH123 - simple binary read
            return cls(f.read())

    # -- object graph ---------------------------------------------------

    def get_object(self, num: int, gen: int = 0):
        if num in self._cache:
            return self._cache[num]
        entry = self._xref.get(num)
        if entry is None:
            return None
        if entry[0] == "n":
            _, offset, _gen = entry
            parser = ObjectParser(self._buf, offset, resolve_length=self._resolve_length)
            _n2, _g2, obj = parser.parse_indirect_object()
        else:
            _, stream_num, index = entry
            obj = self._get_from_object_stream(stream_num, index)
        self._cache[num] = obj
        return obj

    def resolve(self, obj):
        while isinstance(obj, PDFReference):
            obj = self.get_object(obj.num, obj.gen)
        return obj

    def _resolve_length(self, ref: PDFReference):
        return self.get_object(ref.num, ref.gen)

    def _get_from_object_stream(self, stream_num: int, index: int):
        cached = self._objstm_cache.get(stream_num)
        if cached is None:
            stream_obj = self.get_object(stream_num)
            if not isinstance(stream_obj, PDFStream):
                raise ValueError(f"object {stream_num} is not an object stream")
            data = stream_obj.get_data(resolver=self.resolve)
            count = int(self.resolve(stream_obj.dict.get("N")))
            first = int(self.resolve(stream_obj.dict.get("First")))
            header = Lexer(data, 0)
            pairs = []
            for _ in range(count):
                t_num = header.next_token()
                t_off = header.next_token()
                pairs.append((t_num.value, t_off.value))
            cached = (data, first, pairs)
            self._objstm_cache[stream_num] = cached
        data, first, pairs = cached
        _objnum, rel_offset = pairs[index]
        parser = ObjectParser(data, first + rel_offset)
        return parser.parse_object()

    # -- trailer / catalog ------------------------------------------------

    @property
    def trailer(self) -> dict:
        return self._trailer

    @property
    def root(self) -> dict:
        catalog = self.resolve(self._trailer.get("Root"))
        if not isinstance(catalog, dict):
            raise ValueError("document trailer has no valid /Root catalog")
        return catalog

    # -- pages ------------------------------------------------------------

    def _collect_pages(self) -> list:
        pages_root = self.root.get("Pages")
        result: list = []
        seen: set = set()

        def walk(node_ref, inherited: dict):
            node = self.resolve(node_ref)
            if not isinstance(node, dict):
                return
            marker = id(node)
            if marker in seen:
                return
            seen.add(marker)
            merged = dict(inherited)
            for key in _INHERITABLE_PAGE_ATTRS:
                if key in node:
                    merged[key] = node[key]
            if "Kids" in node and node.get("Type") != "Page":
                for kid in self.resolve(node.get("Kids")) or []:
                    walk(kid, merged)
            else:
                result.append(Page(self, node, merged, len(result)))

        walk(pages_root, {})
        return result

    def _pages_list(self) -> list:
        if self._pages is None:
            self._pages = self._collect_pages()
        return self._pages

    def __len__(self) -> int:
        return len(self._pages_list())

    def __getitem__(self, index: int) -> "Page":
        return self._pages_list()[index]

    def __iter__(self):
        return iter(self._pages_list())

    def extract_image(self, xref: int) -> dict:
        from ._images import extract_image

        return extract_image(self, xref)

    def close(self) -> None:
        self._cache.clear()
        self._objstm_cache.clear()

    def __enter__(self) -> "PDFDocument":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class Page:
    def __init__(self, doc: PDFDocument, obj_dict: dict, inherited: dict, index: int):
        self._doc = doc
        self.dict = obj_dict
        self._inherited = inherited
        self.number = index

    @property
    def mediabox(self) -> tuple:
        mb = self.dict.get("MediaBox", self._inherited.get("MediaBox"))
        mb = self._doc.resolve(mb)
        if not isinstance(mb, list) or len(mb) != 4:
            return _DEFAULT_MEDIABOX
        return tuple(float(self._doc.resolve(x)) for x in mb)

    @property
    def cropbox(self) -> tuple:
        """The visible region (spec 14.11.2): /CropBox clipped to the
        MediaBox, defaulting to the MediaBox. This is what viewers display
        and what `get_pixmap()`/`get_text("dict")` cover."""
        mx0, my0, mx1, my1 = self.mediabox
        cb = self._doc.resolve(self.dict.get("CropBox", self._inherited.get("CropBox")))
        if not isinstance(cb, list) or len(cb) != 4:
            return self.mediabox
        x0, y0, x1, y1 = (float(self._doc.resolve(v)) for v in cb)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        clipped = (max(x0, min(mx0, mx1)), max(y0, min(my0, my1)), min(x1, max(mx0, mx1)), min(y1, max(my0, my1)))
        if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
            return self.mediabox  # degenerate/non-overlapping crop: ignore it
        return clipped

    @property
    def rotation(self) -> int:
        r = self.dict.get("Rotate", self._inherited.get("Rotate", 0))
        r = self._doc.resolve(r)
        return int(r or 0) % 360

    @property
    def resources(self) -> dict:
        r = self.dict.get("Resources", self._inherited.get("Resources"))
        r = self._doc.resolve(r)
        return r if isinstance(r, dict) else {}

    def get_contents_raw(self) -> bytes:
        """Decoded content-stream bytes, concatenated (filters applied,
        operators not interpreted). `get_text()` runs the Phase 2
        interpreter over exactly this; `get_pixmap()` will do the same for
        rendering once Phase 3 lands.
        """
        contents = self._doc.resolve(self.dict.get("Contents"))
        if contents is None:
            return b""
        streams = contents if isinstance(contents, list) else [contents]
        parts = []
        for s in streams:
            s = self._doc.resolve(s)
            if isinstance(s, PDFStream):
                parts.append(s.get_data(resolver=self._doc.resolve))
        return b"\n".join(parts)

    def get_text(self, option: str = "text"):
        from ._textextract import TextExtractor, spans_to_dict, spans_to_plain_text

        extractor = TextExtractor(self._doc, self.resources)
        extractor.run(self.get_contents_raw())
        if option in ("text", "plain", ""):
            return spans_to_plain_text(extractor.spans)
        if option == "dict":
            x0, y0, x1, y1 = self.cropbox
            return spans_to_dict(extractor.spans, width=x1 - x0, height=y1 - y0)
        raise ValueError(f"unsupported get_text() option: {option!r}")

    def get_pixmap(self, dpi: int = 72) -> "Pixmap":
        from . import _matrix as mx
        from ._contentpaint import ContentPainter
        from ._raster import Canvas

        x0, y0, x1, y1 = self.cropbox
        scale = dpi / 72.0
        pw = max(1, round((x1 - x0) * scale))
        ph = max(1, round((y1 - y0) * scale))

        # PDF user space (origin bottom-left, Y up) -> unrotated device
        # pixels (origin top-left, Y down), then a further rotation for
        # /Rotate, matching how a viewer displays the page.
        flip = (scale, 0.0, 0.0, -scale, -x0 * scale, y1 * scale)
        rotation = self.rotation
        if rotation == 90:
            rot = (0.0, 1.0, -1.0, 0.0, float(ph), 0.0)
            width, height = ph, pw
        elif rotation == 180:
            rot = (-1.0, 0.0, 0.0, -1.0, float(pw), float(ph))
            width, height = pw, ph
        elif rotation == 270:
            rot = (0.0, -1.0, 1.0, 0.0, 0.0, float(pw))
            width, height = ph, pw
        else:
            rot = mx.IDENTITY
            width, height = pw, ph

        ctm = mx.multiply(flip, rot)
        canvas = Canvas(width, height)
        painter = ContentPainter(self._doc, canvas, self.resources, ctm)
        painter.run(self.get_contents_raw())
        return Pixmap(canvas.pixels, width, height)

    def get_images(self, full: bool = False) -> list:
        from ._images import get_page_images

        return get_page_images(self._doc, self.resources, full)

    def __repr__(self) -> str:
        return f"Page({self.number})"


class Pixmap:
    """An RGB raster produced by `Page.get_pixmap()`. `samples` is raw,
    row-major, top-to-bottom RGB24 bytes — `width*height*3` long."""

    def __init__(self, samples: bytes, width: int, height: int):
        self.samples = bytes(samples)
        self.width = width
        self.height = height
        self.n = 3  # channels; matches PyMuPDF's Pixmap.n for a no-alpha RGB pixmap

    def save(self, path) -> None:
        from ._png import encode_png

        png = encode_png(self.samples, self.width, self.height, channels=3)
        with open(path, "wb") as f:
            f.write(png)

    def tobytes(self, output: str = "png") -> bytes:
        if output != "png":
            raise ValueError(f"unsupported Pixmap.tobytes() output format: {output!r}")
        from ._png import encode_png

        return encode_png(self.samples, self.width, self.height, channels=3)

    def __repr__(self) -> str:
        return f"Pixmap({self.width}x{self.height})"
