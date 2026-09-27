"""Recursive-descent parser that builds PDF objects out of lexer tokens.

Grammar reference: ISO 32000-1:2008 section 7.3. The one bit of lookahead
this needs beyond a plain recursive descent is distinguishing a bare integer
from the start of ``num gen R`` (indirect reference) or ``num gen obj``
(indirect object definition) — both require peeking two tokens ahead and,
on mismatch, pushing them back.
"""

from __future__ import annotations

from typing import Callable, Optional

from ._lexer import Lexer, Token
from ._objects import PDFName, PDFReference, PDFStream


class ObjectParser:
    def __init__(
        self,
        buf: bytes,
        pos: int = 0,
        resolve_length: Optional[Callable[[PDFReference], object]] = None,
    ):
        self.lexer = Lexer(buf, pos)
        self.resolve_length = resolve_length

    def tell(self) -> int:
        return self.lexer.tell()

    def seek(self, pos: int) -> None:
        self.lexer.seek(pos)

    # -- top-level entry points -------------------------------------------------

    def parse_object(self):
        tok = self.lexer.next_token()
        return self._parse_from_token(tok)

    def parse_indirect_object(self):
        """Parse ``N G obj ... endobj`` and return ``(num, gen, obj)``."""
        tok1 = self.lexer.next_token()
        tok2 = self.lexer.next_token()
        tok3 = self.lexer.next_token()
        if not (
            tok1.kind == "num"
            and isinstance(tok1.value, int)
            and tok2.kind == "num"
            and isinstance(tok2.value, int)
            and tok3.kind == "keyword"
            and tok3.value == "obj"
        ):
            raise ValueError(
                f"expected 'N G obj' at offset {self.lexer.pos}, got "
                f"{tok1!r} {tok2!r} {tok3!r}"
            )
        num, gen = tok1.value, tok2.value
        obj = self.parse_object()
        tok = self.lexer.next_token()
        if not (tok.kind == "keyword" and tok.value == "endobj"):
            self.lexer.push_back(tok)
        return num, gen, obj

    # -- internals ----------------------------------------------------------

    def _parse_from_token(self, tok: Token):
        kind, value = tok
        if kind == "num":
            return self._maybe_reference(value)
        if kind == "name":
            return PDFName(value)
        if kind in ("string", "hexstring"):
            return value
        if kind == "array_start":
            return self._parse_array()
        if kind == "dict_start":
            return self._parse_dict_or_stream()
        if kind == "keyword":
            if value == "true":
                return True
            if value == "false":
                return False
            if value == "null":
                return None
            raise ValueError(f"unexpected keyword {value!r} at offset {self.lexer.pos}")
        if kind == "eof":
            raise ValueError("unexpected EOF while parsing object")
        raise ValueError(f"unexpected token {tok!r}")

    def _maybe_reference(self, first_num):
        if not isinstance(first_num, int):
            return first_num
        tok2 = self.lexer.next_token()
        if tok2.kind == "num" and isinstance(tok2.value, int):
            tok3 = self.lexer.next_token()
            if tok3.kind == "keyword" and tok3.value == "R":
                return PDFReference(first_num, tok2.value)
            self.lexer.push_back(tok3)
            self.lexer.push_back(tok2)
            return first_num
        self.lexer.push_back(tok2)
        return first_num

    def _parse_array(self):
        items = []
        while True:
            tok = self.lexer.next_token()
            if tok.kind == "array_end":
                break
            if tok.kind == "eof":
                raise ValueError("unexpected EOF in array")
            items.append(self._parse_from_token(tok))
        return items

    def _parse_dict_or_stream(self):
        d = {}
        while True:
            tok = self.lexer.next_token()
            if tok.kind == "dict_end":
                break
            if tok.kind == "eof":
                raise ValueError("unexpected EOF in dictionary")
            if tok.kind != "name":
                raise ValueError(f"expected name key in dict, got {tok!r}")
            key = tok.value
            val = self.parse_object()
            d[key] = val

        tok = self.lexer.next_token()
        if tok.kind == "keyword" and tok.value == "stream":
            raw = self._read_stream_data(d)
            return PDFStream(d, raw)
        self.lexer.push_back(tok)
        return d

    def _read_stream_data(self, stream_dict: dict) -> bytes:
        buf = self.lexer.buf
        pos = self.lexer.pos
        n = len(buf)
        if pos < n and buf[pos] == 0x0D:
            pos += 1
            if pos < n and buf[pos] == 0x0A:
                pos += 1
        elif pos < n and buf[pos] == 0x0A:
            pos += 1

        length = stream_dict.get("Length")
        data = None
        if isinstance(length, int) and not isinstance(length, bool):
            end = pos + length
            if 0 <= length and end <= n and self._endstream_follows(buf, end):
                data = buf[pos:end]
        elif isinstance(length, PDFReference) and self.resolve_length is not None:
            resolved = self.resolve_length(length)
            if isinstance(resolved, int) and not isinstance(resolved, bool):
                end = pos + resolved
                if 0 <= resolved and end <= n and self._endstream_follows(buf, end):
                    data = buf[pos:end]

        if data is None:
            end = self._scan_for_endstream(buf, pos)
            data = buf[pos:end]
            self.lexer.seek(end)
        else:
            self.lexer.seek(pos + len(data))

        tok = self.lexer.next_token()
        if not (tok.kind == "keyword" and tok.value == "endstream"):
            self.lexer.push_back(tok)
        return data

    @staticmethod
    def _endstream_follows(buf: bytes, end: int) -> bool:
        n = len(buf)
        if buf[end : end + 9] == b"endstream":
            return True
        j = end
        if j < n and buf[j] == 0x0D:
            j += 1
            if j < n and buf[j] == 0x0A:
                j += 1
        elif j < n and buf[j] == 0x0A:
            j += 1
        return buf[j : j + 9] == b"endstream"

    @staticmethod
    def _scan_for_endstream(buf: bytes, start: int) -> int:
        idx = buf.find(b"endstream", start)
        if idx == -1:
            return len(buf)
        end = idx
        if end > start and buf[end - 1] == 0x0A:
            end -= 1
            if end > start and buf[end - 1] == 0x0D:
                end -= 1
        elif end > start and buf[end - 1] == 0x0D:
            end -= 1
        return end
