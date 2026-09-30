"""Minimal PDF serializer/writer.

Supports exactly what `_merge.py` needs: writing a flat set of indirect
objects (dicts/arrays/names/refs/streams) out as a classic (non-compressed)
PDF file with a plain xref table. Not a general editing API — see the
project brief's "non-goal for v1: writing/editing PDFs"; this is scoped
narrowly to support structural page concatenation (`merge()`).
"""

from __future__ import annotations

import os
import zlib
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


_LITERAL_ESCAPES = {0x5C: b"\\\\", 0x28: b"\\(", 0x29: b"\\)", 0x0A: b"\\n", 0x0D: b"\\r", 0x09: b"\\t"}


def _string(data: bytes) -> bytes:
    """A literal string when that is shorter (mostly printable text),
    otherwise a hex string."""
    printable = sum(1 for b in data if 0x20 <= b < 0x7F)
    if len(data) and printable < 0.8 * len(data):
        return b"<" + data.hex().encode("ascii") + b">"
    out = bytearray(b"(")
    for b in data:
        if b in _LITERAL_ESCAPES:
            out += _LITERAL_ESCAPES[b]
        elif 0x20 <= b < 0x7F:
            out.append(b)
        else:
            out += b"\\%03o" % b
    return bytes(out + b")")


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
        return _string(bytes(obj))
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

    def to_bytes(self, root: PDFReference, trailer_extra: Optional[dict] = None, compress: bool = False) -> bytes:
        """The PDF file. With ``compress``, objects other than streams are
        packed into compressed object streams and the cross-reference
        table is itself a stream (PDF 1.5), which is typically several
        times smaller."""
        for i, obj in enumerate(self._objects, start=1):
            if obj is _UNSET:
                raise ValueError(f"object {i} was reserved but never set")
        buf = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        entries = {}  # num -> (1, offset) or (2, object stream num, index)
        n = len(self._objects)
        packable = [i for i, o in enumerate(self._objects, 1) if not isinstance(o, PDFStream)] if compress else []
        packed = set(packable)
        for i, obj in enumerate(self._objects, start=1):
            if i in packed:
                continue
            entries[i] = (1, len(buf))
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

        if not compress:
            xref_offset = len(buf)
            size = n + 1
            buf += b"xref\n"
            buf += f"0 {size}\n".encode("ascii")
            buf += b"0000000000 65535 f \n"
            for i in range(1, size):
                buf += f"{entries[i][1]:010d} 00000 n \n".encode("ascii")
            buf += b"trailer\n"
            buf += serialize_value({"Size": size, "Root": root, **(trailer_extra or {})})
            buf += b"\nstartxref\n"
            buf += f"{xref_offset}\n".encode("ascii")
            buf += b"%%EOF\n"
            return bytes(buf)

        next_num = n + 1
        for chunk_start in range(0, len(packable), 200):
            chunk = packable[chunk_start:chunk_start + 200]
            header, body = [], bytearray()
            for index, num in enumerate(chunk):
                header.append(b"%d %d" % (num, len(body)))
                body += serialize_value(self._objects[num - 1]) + b"\n"
                entries[num] = (2, next_num, index)
            head = b" ".join(header) + b"\n"
            data = zlib.compress(head + bytes(body))
            entries[next_num] = (1, len(buf))
            buf += b"%d 0 obj\n" % next_num + serialize_value(
                {"Type": "ObjStm", "N": len(chunk), "First": len(head), "Filter": "FlateDecode", "Length": len(data)})
            buf += b"\nstream\n" + data + b"\nendstream\nendobj\n"
            next_num += 1
        xref_num = next_num
        xref_offset = len(buf)
        entries[xref_num] = (1, xref_offset)
        size = xref_num + 1
        rows = bytearray(b"\x00\x00\x00\x00\x00\xff\xff")
        for i in range(1, size):
            kind, a, *rest = entries[i]
            rows += bytes([kind]) + a.to_bytes(4, "big") + (rest[0] if rest else 0).to_bytes(2, "big")
        data = zlib.compress(bytes(rows))
        buf += b"%d 0 obj\n" % xref_num + serialize_value(
            {"Type": "XRef", "Size": size, "W": [1, 4, 2], "Root": root, **(trailer_extra or {}),
             "Filter": "FlateDecode", "Length": len(data)})
        buf += b"\nstream\n" + data + b"\nendstream\nendobj\n"
        buf += b"startxref\n%d\n%%%%EOF\n" % xref_offset
        return bytes(buf)
