"""The editing session behind the browser editor.

A session is the original file plus a list of operations. The current
document is the original with the operations applied; undo drops the last
operation and replays the rest from the original, so every state is
reproducible and nothing depends on in-place changes being reversible.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from ..document import PDFDocument
from ..errors import PDFError


def _rect(v) -> tuple:
    return tuple(float(x) for x in v)


def _color(v, default=(0, 0, 0)):
    return tuple(float(x) for x in v) if v is not None else default


class Session:
    def __init__(self, path: str | os.PathLike, data: bytes | None = None):
        self.path = Path(path)
        self.original = data if data is not None else self.path.read_bytes()
        self.blobs: dict = {}
        self.ops: list = []
        self.redo_stack: list = []
        self.lock = threading.RLock()
        self.generation = 0
        self.saved_at = 0  # len(ops) at the last save
        self.doc = self._open()
        self._render_cache: dict = {}

    def _open(self) -> PDFDocument:
        doc = PDFDocument.open(self.original)
        if doc.needs_pass:
            raise PDFError("this PDF is password-protected: open it in a viewer, save an unprotected copy, and edit that")
        self.generation += 1
        for p in doc:
            p._rev = 0
        return doc

    # -- operations ------------------------------------------------------------------

    def apply(self, op: dict) -> None:
        with self.lock:
            try:
                self._run(self.doc, op)
            except Exception:
                self._replay()  # a failed operation may have half-changed the document
                raise
            self.ops.append(op)
            self.redo_stack.clear()

    def undo(self) -> None:
        with self.lock:
            if not self.ops:
                return
            op = self.ops.pop()
            try:
                self._replay()
            except Exception:
                self.ops.append(op)
                self._replay()
                raise
            self.redo_stack.append(op)

    def redo(self) -> None:
        with self.lock:
            if not self.redo_stack:
                return
            op = self.redo_stack.pop()
            self._run(self.doc, op)
            self.ops.append(op)

    def _replay(self) -> None:
        self.doc = self._open()
        self._render_cache.clear()
        for op in self.ops:
            self._run(self.doc, op)

    def _touch(self, page) -> None:
        page._rev = getattr(page, "_rev", 0) + 1

    def _find_annot(self, page, annot_id: str):
        for i, a in enumerate(page.annots()):
            if (a.id or f"#{i}") == annot_id:
                return a
        raise PDFError("that annotation no longer exists")

    def _run(self, doc: PDFDocument, op: dict) -> None:
        """Apply one operation. An annotation it creates keeps the id it got
        the first time, so later operations that refer to it still find it
        when the operations are replayed (undo)."""
        page = doc[int(op["page"])] if "page" in op else None
        before = {id(a.dict) for a in page.annots()} if page is not None else set()
        self._apply(doc, op)
        if page is not None and op.get("op") not in ("delete_page", "delete_pages", "move_page"):
            new = [a for a in page.annots() if id(a.dict) not in before]
            if len(new) == 1:
                from .._edit import text_string

                if "created_id" in op:
                    new[0].dict["NM"] = text_string(op["created_id"])
                else:
                    op["created_id"] = new[0].id

    def _apply(self, doc: PDFDocument, op: dict) -> None:
        kind = op.get("op")
        page = doc[int(op["page"])] if "page" in op else None
        if page is not None:
            self._touch(page)
        if kind == "delete_page":
            if len(doc) == 1:
                raise PDFError("a PDF needs at least one page")
            doc.delete_page(int(op["page"]))
        elif kind == "delete_pages":
            pages = {int(p) for p in op["pages"]}
            if len(pages) >= len(doc):
                raise PDFError("a PDF needs at least one page")
            doc.delete_pages(sorted(pages))
        elif kind == "move_page":
            doc.move_page(int(op["from"]), int(op["to"]))
        elif kind == "rotate":
            page.set_rotation(page.rotation + int(op.get("delta", 90)))
        elif kind == "new_page":
            ref = doc[min(int(op.get("after", len(doc) - 1)), len(doc) - 1)]
            w, h = ref.rect[2], ref.rect[3]
            at = int(op.get("after", len(doc) - 1)) + 1
            doc.new_page(-1 if at >= len(doc) else at, width=w, height=h)
        elif kind == "insert_pdf":
            at = int(op.get("after", len(doc) - 1)) + 1
            doc.insert_pdf(self.blobs[op["blob"]], start_at=-1 if at >= len(doc) else at)
        elif kind in ("highlight", "underline", "strikeout"):
            rects = [_rect(r) for r in op["rects"]]
            getattr(page, f"add_{kind}_annot")(rects, color=_color(op.get("color"), None) or {
                "highlight": (1, 0.9, 0), "underline": (0, 0, 1), "strikeout": (1, 0, 0)}[kind])
        elif kind == "note":
            page.add_text_annot(_rect(op["point"]), op.get("text", ""))
        elif kind == "freetext":
            page.add_freetext_annot(_rect(op["rect"]), op.get("text", ""), fontsize=float(op.get("fontsize", 12)),
                                    text_color=_color(op.get("color")))
        elif kind == "ink":
            page.add_ink_annot([[_rect(p) for p in s] for s in op["strokes"]], color=_color(op.get("color")),
                               width=float(op.get("width", 2)))
        elif kind == "rect":
            page.add_rect_annot(_rect(op["rect"]), color=_color(op.get("color")), width=float(op.get("width", 1.5)))
        elif kind == "ellipse":
            page.add_circle_annot(_rect(op["rect"]), color=_color(op.get("color")), width=float(op.get("width", 1.5)))
        elif kind == "line":
            page.add_line_annot(_rect(op["p1"]), _rect(op["p2"]), color=_color(op.get("color")),
                                width=float(op.get("width", 1.5)))
        elif kind == "delete_annot":
            page.delete_annot(self._find_annot(page, op["id"]))
        elif kind == "text":
            page.insert_text(_rect(op["point"]), op.get("text", ""), fontsize=float(op.get("fontsize", 12)),
                             fontname=op.get("fontname", "helv"), color=_color(op.get("color")))
        elif kind == "image":
            page.insert_image(_rect(op["rect"]), stream=self.blobs[op["blob"]])
        elif kind == "field":
            for w in page.widgets():
                if w.field_name == op["name"] and (op.get("xref") in (None, w.xref) or w.field_type != "radiobutton"):
                    w.field_value = op["value"]
                    w.update()
                    break
            else:
                raise PDFError(f"no field {op['name']!r} on this page")
            for p in doc:  # a field can have widgets on several pages
                self._touch(p)
        elif kind == "redact_mark":
            page.add_redact_annot(_rect(op["rect"]))
        elif kind == "redact_apply":
            for p in doc:
                if p.annots(types=("Redact",)):
                    self._touch(p)
                    p.apply_redactions()
        elif kind == "edit_text":
            page.edit_text_line(_rect(op["rect"]), op.get("text", ""), old=op.get("old"))
        elif kind == "replace_text":
            page.replace_text(_rect(op["rect"]), op.get("text", ""))
        else:
            raise PDFError(f"unknown operation {kind!r}")

    def add_blob(self, data: bytes) -> str:
        blob_id = uuid.uuid4().hex
        self.blobs[blob_id] = data
        return blob_id

    # -- views -----------------------------------------------------------------------

    def state(self) -> dict:
        with self.lock:
            pages = []
            for i, p in enumerate(self.doc):
                x0, y0, x1, y1 = p.rect
                annots = []
                for j, a in enumerate(p.annots()):
                    annots.append({"id": a.id or f"#{j}", "type": a.type, "rect": a.rect,
                                   "content": a.info["content"]})
                fields = []
                for w in p.widgets():
                    t = w.field_type
                    if t in ("button", "signature", "unknown"):
                        continue
                    value = w.field_value
                    fields.append({"name": w.field_name, "type": t, "value": value, "rect": w.rect,
                                   "xref": w.xref, "readonly": w.read_only, "multiline": w.multiline,
                                   "on_state": w.on_state if t in ("checkbox", "radiobutton") else None,
                                   "options": [d for _, d in w.choice_values] if t in ("combobox", "listbox") else None})
                pages.append({"width": x1 - x0, "height": y1 - y0, "rotation": p.rotation,
                              "key": f"{self.generation}-{id(p)}-{getattr(p, '_rev', 0)}",
                              "annots": annots, "fields": fields})
            return {"name": self.path.name, "pages": pages, "dirty": len(self.ops) != self.saved_at,
                    "can_undo": bool(self.ops), "can_redo": bool(self.redo_stack),
                    "pending_redactions": sum(1 for pg in pages for a in pg["annots"] if a["type"] == "Redact")}

    def text_lines(self, index: int) -> list:
        """Lines of text on a page, with their box, style and character
        positions, for marking and editing text."""
        with self.lock:
            return self.doc[index].text_lines()

    def render(self, index: int, dpi: int) -> bytes:
        with self.lock:
            page = self.doc[index]
            key = (self.generation, id(page), getattr(page, "_rev", 0), dpi)
            png = self._render_cache.get(key)
            if png is None:
                # form fields are shown as inputs on top of the page instead
                png = page.get_pixmap(dpi=dpi, widgets=False).tobytes("png")
                if len(self._render_cache) > 400:
                    self._render_cache.clear()
                self._render_cache[key] = png
            return png

    def pdf_bytes(self, pages=None) -> bytes:
        with self.lock:
            if pages is None:
                return self.doc.tobytes()
            copy = PDFDocument.open(self.doc.tobytes())
            copy.select(pages)
            return copy.tobytes()

    def save(self, path: str | None = None) -> str:
        with self.lock:
            target = Path(path) if path else self.path.with_name(self.path.stem + "-edited.pdf")
            data = self.doc.tobytes()
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            self.saved_at = len(self.ops)
            return str(target)
