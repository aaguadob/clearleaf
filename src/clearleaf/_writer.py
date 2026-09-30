"""Minimal PDF serializer/writer.

Supports exactly what `_merge.py` needs: writing a flat set of indirect
objects (dicts/arrays/names/refs/streams) out as a classic (non-compressed)
PDF file with a plain xref table. Not a general editing API — see the
project brief's "non-goal for v1: writing/editing PDFs"; this is scoped
narrowly to support structural page concatenation (`merge()`).
"""

from __future__ import annotations

import os
from typing import Optional, Union

from ._objects import PDFName, PDFReference, PDFStream

_UNSET = object()

_NAME_SAFE_PUNCTUATION = frozenset("!\"$&'*+,-.:;=?@^_`|~")


def _escape_name(name: str) -> bytes:
    out = bytearray(b"/")
    for byte in name.encode("utf-8"):
        ch = chr(byte)
        if byte < 0x21 or byte > 0x7E or (not ch.isalnum() and ch not in _NAME_SAFE_PUNCTUATION):
            out += f"#{byte:02X}".encode("ascii")
        else:
            out.append(byte)
    return bytes(out)


def _format_number(n: Union[int, float]) -> bytes:
    if isinstance(n, int):
        return str(n).encode("ascii")
    if n == int(n) and abs(n) < 1e15:
        return str(int(n)).encode("ascii")
    s = f"{n:.6f}".rstrip("0").rstrip(".")
    return s.encode("ascii")


def serialize_value(obj) -> bytes:
    """Serialize a parsed PDF value (as produced by `_objects.py`) back to
    PDF syntax bytes. `PDFStream` is not accepted here — streams are only
    ever written as top-level indirect objects (`PDFWriter.write`), never
    nested inside another value, matching the PDF spec.
    """
    if obj is None:
        return b"null"
    if isinstance(obj, bool):
        return b"true" if obj else b"false"
    if isinstance(obj, PDFReference):
        return f"{obj.num} {obj.gen} R".encode("ascii")
    if isinstance(obj, (int, float)):
        return _format_number(obj)
    if isinstance(obj, (bytes, bytearray)):
        return b"<" + bytes(obj).hex().encode("ascii") + b">"
    if isinstance(obj, str):
        # PDFName (a str subclass) and any plain str we author ourselves
        # (e.g. "Catalog" for a /Type value) both serialize as Names --
        # PDF strings are represented as `bytes` in this object model, so
        # a bare `str` here is never meant to become a PDF (...) string.
        return _escape_name(obj)
    if isinstance(obj, dict):
        parts = [b"<<"]
        for k, v in obj.items():
            parts.append(_escape_name(str(k)))
            parts.append(b" ")
            parts.append(serialize_value(v))
            parts.append(b" ")
        parts.append(b">>")
        return b"".join(parts)
    if isinstance(obj, list):
        return b"[" + b" ".join(serialize_value(v) for v in obj) + b"]"
    raise TypeError(f"cannot serialize object of type {type(obj)!r}")


class PDFWriter:
    """Accumulates indirect objects and serializes them as a classic PDF."""

    def __init__(self):
        self._objects: list = []

    def reserve(self) -> int:
        """Allocate an object number without a value yet -- lets a value
        that needs a forward reference to another not-yet-built object
        (e.g. a page's /Parent pointing at a /Pages node built afterwards)
        get one immediately."""
        self._objects.append(_UNSET)
        return len(self._objects)

    def set(self, num: int, value) -> None:
        self._objects[num - 1] = value

    def add(self, value) -> PDFReference:
        num = self.reserve()
        self.set(num, value)
        return PDFReference(num, 0)

    def write(self, path: Union[str, "os.PathLike[str]"], root: PDFReference) -> None:
        with open(path, "wb") as f:  # noqa: PTH123 - simple binary write
            f.write(self.to_bytes(root))

    def to_bytes(self, root: PDFReference, trailer_extra: Optional[dict] = None) -> bytes:
        buf = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for i, obj in enumerate(self._objects, start=1):
            if obj is _UNSET:
                raise ValueError(f"object {i} was reserved but never set")
            offsets[i] = len(buf)
            buf += f"{i} 0 obj\n".encode("ascii")
            if isinstance(obj, PDFStream):
                stream_dict = {k: v for k, v in obj.dict.items() if k != "Length"}
                stream_dict["Length"] = len(obj.raw_data)
                buf += serialize_value(stream_dict)
                buf += b"\nstream\n"
                buf += obj.raw_data
                buf += b"\nendstream\n"
            else:
                buf += serialize_value(obj)
                buf += b"\n"
            buf += b"endobj\n"

        xref_offset = len(buf)
        size = len(self._objects) + 1
        buf += b"xref\n"
        buf += f"0 {size}\n".encode("ascii")
        buf += b"0000000000 65535 f \n"
        for i in range(1, size):
            buf += f"{offsets[i]:010d} 00000 n \n".encode("ascii")
        buf += b"trailer\n"
        buf += serialize_value({"Size": size, "Root": root, **(trailer_extra or {})})
        buf += b"\nstartxref\n"
        buf += f"{xref_offset}\n".encode("ascii")
        buf += b"%%EOF\n"
        return bytes(buf)
