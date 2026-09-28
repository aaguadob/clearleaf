"""Cross-reference table / stream parsing (ISO 32000-1:2008 section 7.5.4,
7.5.8) and the /Prev chain that stitches together incrementally-updated
files.

An xref entry is represented as a tuple:
  - ("n", offset, gen)        -- object stored directly at a byte offset
  - ("c", stream_obj_num, i)  -- object is the i-th object packed into the
                                  object stream numbered stream_obj_num
Free entries (type 0 in classic tables, type 0 in xref streams) are simply
omitted.
"""

from __future__ import annotations

import re
from typing import Optional

from ._lexer import Lexer
from ._objects import PDFStream
from ._parser import ObjectParser

_WS = b" \t\r\n\x0c\x00"


def find_startxref(buf: bytes) -> int:
    idx = buf.rfind(b"startxref")
    if idx == -1:
        raise ValueError("no 'startxref' keyword found")
    i = idx + len(b"startxref")
    n = len(buf)
    while i < n and buf[i] in _WS:
        i += 1
    start = i
    while i < n and 0x30 <= buf[i] <= 0x39:
        i += 1
    if start == i:
        raise ValueError("malformed 'startxref' value")
    return int(buf[start:i])


def parse_xref_section(buf: bytes, offset: int, resolve_length=None):
    """Parse one xref section (classic table or stream) at ``offset``.

    Returns ``(entries, trailer, prev_offset, xrefstm_offset)``.
    """
    pos = offset
    n = len(buf)
    while pos < n and buf[pos] in _WS:
        pos += 1
    if buf[pos : pos + 4] == b"xref" and (
        pos + 4 >= n or buf[pos + 4] in _WS or buf[pos + 4] in b"([<%/"
    ):
        return _parse_classic_xref(buf, pos + 4)
    return _parse_xref_stream(buf, offset, resolve_length)


def _parse_classic_xref(buf: bytes, pos: int):
    entries: dict[int, tuple] = {}
    lexer = Lexer(buf, pos)
    while True:
        tok = lexer.next_token()
        if tok.kind == "keyword" and tok.value == "trailer":
            break
        if tok.kind == "eof":
            raise ValueError("unexpected EOF in xref table")
        if tok.kind != "num" or not isinstance(tok.value, int):
            raise ValueError(f"malformed xref subsection header at {lexer.pos}")
        start = tok.value
        count_tok = lexer.next_token()
        if count_tok.kind != "num" or not isinstance(count_tok.value, int):
            raise ValueError("malformed xref subsection header (count)")
        count = count_tok.value
        for i in range(count):
            off_tok = lexer.next_token()
            gen_tok = lexer.next_token()
            type_tok = lexer.next_token()
            if (
                off_tok.kind != "num"
                or gen_tok.kind != "num"
                or type_tok.kind != "keyword"
            ):
                raise ValueError(f"malformed xref entry near offset {lexer.pos}")
            if type_tok.value == "n":
                entries[start + i] = ("n", off_tok.value, gen_tok.value)

    parser = ObjectParser(buf, lexer.pos)
    trailer = parser.parse_object()
    if not isinstance(trailer, dict):
        raise ValueError("expected trailer dictionary after 'trailer' keyword")
    prev = trailer.get("Prev")
    xrefstm = trailer.get("XRefStm")
    return entries, trailer, prev, xrefstm


def _parse_xref_stream(buf: bytes, offset: int, resolve_length):
    parser = ObjectParser(buf, offset, resolve_length=resolve_length)
    _num, _gen, obj = parser.parse_indirect_object()
    if not isinstance(obj, PDFStream):
        raise ValueError("expected a cross-reference stream object")
    d = obj.dict
    if str(d.get("Type", "")) != "XRef":
        raise ValueError("cross-reference stream missing /Type /XRef")
    w = d.get("W")
    if not (isinstance(w, list) and len(w) == 3):
        raise ValueError("cross-reference stream missing /W")
    w0, w1, w2 = (int(x) for x in w)

    size = d.get("Size")
    index = d.get("Index") or [0, size]
    data = obj.get_data()

    entries: dict[int, tuple] = {}
    entry_size = w0 + w1 + w2
    di = 0
    it = iter(index)
    for start, count in zip(it, it):
        for i in range(count):
            if di + entry_size > len(data):
                break
            chunk = data[di : di + entry_size]
            di += entry_size
            f0 = int.from_bytes(chunk[:w0], "big") if w0 > 0 else 1
            f1 = int.from_bytes(chunk[w0 : w0 + w1], "big") if w1 > 0 else 0
            f2 = (
                int.from_bytes(chunk[w0 + w1 : w0 + w1 + w2], "big") if w2 > 0 else 0
            )
            objnum = start + i
            if f0 == 1:
                entries[objnum] = ("n", f1, f2)
            elif f0 == 2:
                entries[objnum] = ("c", f1, f2)
            # f0 == 0: free entry, omitted

    prev = d.get("Prev")
    return entries, d, prev, None


def load_xref_chain(buf: bytes, start_offset: int):
    """Walk the /Prev (and hybrid-reference /XRefStm) chain starting at the
    offset named by ``startxref``. The newest section is visited first, so
    entries and trailer keys from it win over anything an older section
    defines for the same object number / key.
    """
    entries: dict[int, tuple] = {}
    trailer: dict = {}
    visited: set[int] = set()
    offset: Optional[int] = start_offset

    while offset is not None:
        if offset in visited:
            break
        visited.add(offset)
        sec_entries, sec_trailer, prev, xrefstm = parse_xref_section(buf, offset)
        for k, v in sec_entries.items():
            entries.setdefault(k, v)
        for k, v in sec_trailer.items():
            if k not in trailer:
                trailer[k] = v

        if xrefstm is not None and xrefstm not in visited:
            visited.add(xrefstm)
            xs_entries, _xs_trailer, _p, _x = parse_xref_section(buf, xrefstm)
            for k, v in xs_entries.items():
                entries.setdefault(k, v)

        offset = prev

    return entries, trailer


# -- recovery ----------------------------------------------------------------

_OBJ_HEADER = re.compile(rb"(?<![0-9])(\d{1,10})[ \t\r\n\x0c\x00]+(\d{1,5})[ \t\r\n\x0c\x00]+obj(?![A-Za-z0-9])")
_STREAM_KW = re.compile(rb"(?<![A-Za-z])stream(?:\r\n|\n|\r)")


def reconstruct_xref(buf: bytes):
    """Rebuild the object index of a damaged file by scanning it for
    ``N G obj`` headers -- what readers do when the xref data is missing or
    points to the wrong places.

    Stream bodies are skipped, so data inside a stream that happens to look
    like an object header is not mistaken for one. Later definitions win,
    matching incremental-update semantics. Returns ``(entries, trailers)``
    where ``trailers`` are the file's ``trailer`` dictionaries in file
    order (possibly empty; callers fall back to finding the catalog).
    """
    entries: dict[int, tuple] = {}
    pos = 0
    n = len(buf)
    while True:
        m = _OBJ_HEADER.search(buf, pos)
        if not m:
            break
        num, gen = int(m.group(1)), int(m.group(2))
        entries[num] = ("n", m.start(), gen)
        pos = m.end()
        end_obj = buf.find(b"endobj", pos)
        s = _STREAM_KW.search(buf, pos, end_obj if end_obj >= 0 else n)
        if s:
            end_stream = buf.find(b"endstream", s.end())
            pos = end_stream + len(b"endstream") if end_stream >= 0 else n
    trailers = []
    for t in re.finditer(rb"trailer", buf):
        try:
            parser = ObjectParser(buf, t.end())
            d = parser.parse_object()
        except Exception:
            continue
        if isinstance(d, dict):
            trailers.append(d)
    return entries, trailers
