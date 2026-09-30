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

from .errors import PDFError, public
from ._edit import DocumentEditing, PageEditing
from ._annotate import PageAnnotations
from ._forms import PageForms
from ._redact import PageRedaction
from ._lexer import Lexer
from ._annots import annotation_appearances
from ._objects import PDFReference, PDFStream
from ._parser import ObjectParser
from ._structure import page_actual_text
from ._security import EncryptionError, StandardSecurityHandler
from ._xref import find_startxref, load_xref_chain, reconstruct_xref

_MISSING = object()
_INHERITABLE_PAGE_ATTRS = ("Resources", "MediaBox", "CropBox", "Rotate")
_DEFAULT_MEDIABOX = (0.0, 0.0, 612.0, 792.0)  # US Letter, per spec 7.7.3.3


class PDFDocument(DocumentEditing):
    @public
    def __init__(self, buf: bytes):
        self._buf = bytes(buf)
        self._cache: dict[int, object] = {}
        self._objstm_cache: dict[int, tuple] = {}
        self._pages: Optional[list] = None
        self._scanned: Optional[dict] = None
        self._security: Optional[StandardSecurityHandler] = None
        self._encrypt_num: Optional[int] = None
        #: True when the document has been edited since it was opened/saved.
        self.is_dirty = False
        #: True when the file's own xref data was unusable and the object
        #: index had to be rebuilt by scanning the file (see `_repair`).
        self.repaired = False
        try:
            start = find_startxref(self._buf)
            self._xref, self._trailer = load_xref_chain(self._buf, start)
            if "Root" not in self._trailer:
                raise ValueError("trailer has no /Root")
        except Exception:
            self._repair()
        self._setup_encryption()

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
            obj = self._parse_at(num, entry[1])
            if obj is not _MISSING:
                obj = self._decrypt_object(obj, num, entry[2])
            if obj is _MISSING:
                # The xref entry points at the wrong place (or at a different
                # object): use the object's actual location in the file.
                scanned = self._scan_index().get(num)
                obj = self._parse_at(num, scanned[1]) if scanned and scanned[0] == "n" else None
                if obj is _MISSING:
                    obj = None
                elif obj is not None:
                    obj = self._decrypt_object(obj, num, scanned[2])
        else:
            _, stream_num, index = entry
            obj = self._get_from_object_stream(stream_num, index)
        self._cache[num] = obj
        return obj

    # -- encryption -------------------------------------------------------

    def _setup_encryption(self) -> None:
        ref = self._trailer.get("Encrypt")
        if ref is None:
            return
        if isinstance(ref, PDFReference):
            self._encrypt_num = ref.num
        encrypt = self.resolve(ref)  # the Encrypt dictionary is never itself encrypted
        ids = self.resolve(self._trailer.get("ID")) or [b""]
        file_id = bytes(self.resolve(ids[0])) if ids and isinstance(self.resolve(ids[0]), (bytes, bytearray)) else b""
        try:
            self._security = StandardSecurityHandler(encrypt, file_id, self.resolve)
        except (EncryptionError, TypeError, ValueError):
            self._security = None
            self._unsupported_encryption = True
            return
        self.authenticate("")  # many files have an empty user password

    @property
    def is_encrypted(self) -> bool:
        return "Encrypt" in self._trailer

    @property
    def needs_pass(self) -> bool:
        """True while the document's content can't be read without a password."""
        if not self.is_encrypted:
            return False
        return self._security is None or self._security.key is None

    @public
    def authenticate(self, password) -> int:
        """Unlock an encrypted document (same convention as PyMuPDF): returns
        0 if ``password`` is wrong, otherwise 2 (user password), 4 (owner
        password) or 6 (both)."""
        if self._security is None:
            return 0
        result = self._security.authenticate(password)
        if result:
            self._cache.clear()
            self._objstm_cache.clear()
            self._pages = None
        return result

    def _require_access(self) -> None:
        if self.needs_pass:
            if getattr(self, "_unsupported_encryption", False):
                raise ValueError("document uses an unsupported security handler")
            raise ValueError("document is encrypted: call authenticate(password) first")

    def _decrypt_object(self, obj, num: int, gen: int):
        if self._security is None or self._security.key is None or num == self._encrypt_num:
            return obj
        sec = self._security

        def walk(value):
            if isinstance(value, (bytes, bytearray)):
                return sec.decrypt_string(bytes(value), num, gen)
            if isinstance(value, list):
                return [walk(v) for v in value]
            if isinstance(value, dict):
                return {k: walk(v) for k, v in value.items()}
            return value

        if isinstance(obj, PDFStream):
            decrypted_dict = walk(obj.dict)
            return PDFStream(decrypted_dict, sec.decrypt_stream(obj.raw_data, num, gen, decrypted_dict))
        return walk(obj)

    def _parse_at(self, num: int, offset: int):
        """Parse the indirect object at ``offset``, or _MISSING if what's
        there isn't a well-formed object numbered ``num``."""
        try:
            parser = ObjectParser(self._buf, offset, resolve_length=self._resolve_length)
            found, _gen, obj = parser.parse_indirect_object()
        except Exception:
            return _MISSING
        return obj if found == num else _MISSING

    def _scan_index(self) -> dict:
        if self._scanned is None:
            self._scanned, self._scanned_trailers = reconstruct_xref(self._buf)
        return self._scanned

    def _repair(self) -> None:
        """Rebuild the object index and trailer from a scan of the file."""
        self.repaired = True
        self._xref = dict(self._scan_index())
        trailer: dict = {}
        for t in self._scanned_trailers:  # later trailers are newer
            trailer.update(t)
        self._trailer = trailer
        # Objects packed in object streams aren't visible to the scan; index
        # any the scan didn't find directly.
        for num in list(self._xref):
            obj = self.get_object(num)
            if isinstance(obj, PDFStream) and str(obj.dict.get("Type")) == "ObjStm":
                try:
                    data = obj.get_data(resolver=self.resolve)
                    count = int(self.resolve(obj.dict.get("N")))
                    header = Lexer(data, 0)
                    for index in range(count):
                        inner = header.next_token().value
                        header.next_token()
                        self._xref.setdefault(int(inner), ("c", num, index))
                except Exception:
                    continue
        if "Root" not in self._trailer:
            # No usable trailer (e.g. an xref-stream file with a broken
            # stream, or none at all): take the root from an xref stream's
            # dictionary, else the last /Catalog object in the file.
            for num in sorted(self._xref, key=lambda k: self._xref[k][1] if self._xref[k][0] == "n" else -1):
                obj = self.get_object(num)
                d = obj.dict if isinstance(obj, PDFStream) else obj
                if isinstance(d, dict) and str(d.get("Type")) == "XRef" and "Root" in d:
                    self._trailer.update({k: v for k, v in d.items() if k in ("Root", "Info", "Encrypt", "ID")})
            if "Root" not in self._trailer:
                for num, entry in self._xref.items():
                    obj = self.get_object(num)
                    if isinstance(obj, dict) and str(obj.get("Type")) == "Catalog":
                        self._trailer["Root"] = PDFReference(num, entry[2] if entry[0] == "n" else 0)
        if "Root" not in self._trailer:
            raise PDFError("not a PDF, or too damaged to read: no objects found" if not self._xref
                           else "no document catalog (/Root) found")

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
        if not result and self.repaired:
            # The page tree itself is gone (unresolvable /Pages in a damaged
            # file): fall back to the page objects that survive, in file order.
            # Inheritable attributes from the lost tree can't be recovered.
            for num, entry in sorted(self._xref.items(), key=lambda kv: kv[1][1] if kv[1][0] == "n" else 0):
                obj = self.get_object(num)
                if isinstance(obj, dict) and str(obj.get("Type")) == "Page":
                    result.append(Page(self, obj, {}, len(result)))
        return result

    @public
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

    @public
    def extract_image(self, xref: int) -> dict:
        self._require_access()
        from ._images import extract_image

        return extract_image(self, xref)

    def close(self) -> None:
        self._cache.clear()
        self._objstm_cache.clear()

    def __enter__(self) -> "PDFDocument":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class Page(PageEditing, PageAnnotations, PageForms, PageRedaction):
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

    @public
    def get_contents_raw(self) -> bytes:
        """Decoded content-stream bytes, concatenated (filters applied,
        operators not interpreted). `get_text()` runs the Phase 2
        interpreter over exactly this; `get_pixmap()` will do the same for
        rendering once Phase 3 lands.
        """
        self._doc._require_access()
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

    @public
    def get_text(self, option: str = "text"):
        from ._textextract import TextExtractor, spans_to_dict, spans_to_plain_text

        extractor = TextExtractor(self._doc, self.resources, actual_text=page_actual_text(self._doc, self.dict))
        extractor.run(self.get_contents_raw())
        for form, placement in annotation_appearances(self._doc, self.dict):
            extractor.run_form(form, placement)
        # Like PyMuPDF, only text on the visible page counts: drop runs that
        # lie entirely outside the CropBox.
        x0, y0, x1, y1 = self.cropbox
        spans = [s for s in extractor.spans
                 if s.bbox[2] >= x0 and s.bbox[0] <= x1 and s.bbox[3] >= y0 and s.bbox[1] <= y1]
        if option in ("text", "plain", ""):
            return spans_to_plain_text(spans)
        if option == "dict":
            return spans_to_dict(spans, width=x1 - x0, height=y1 - y0, top_left=(x0, y1))
        raise ValueError(f"unsupported get_text() option: {option!r}")

    @public
    def get_pixmap(self, dpi: int = 72, annots: bool = True, widgets: bool = True) -> "Pixmap":
        """Render the page. ``annots=False`` leaves out annotations and
        ``widgets=False`` just the form fields (as in PyMuPDF)."""
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
        for form, placement in annotation_appearances(self._doc, self.dict, widgets=widgets) if annots else []:
            painter.paint_form(form, placement)
        return Pixmap(canvas.pixels, width, height)

    @public
    def get_images(self, full: bool = False) -> list:
        self._doc._require_access()
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
