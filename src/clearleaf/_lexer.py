"""Tokenizer for PDF syntax (ISO 32000-1:2008 section 7.2).

Splits a byte buffer into a stream of tokens without interpreting their
grammar (that's the parser's job — see ``_parser.py``). Kept independent of
object semantics so it can be reused for content-stream tokenizing later
(Phase 2), which shares the same low-level lexical rules.
"""

from __future__ import annotations

import re

from typing import NamedTuple, Optional

WHITESPACE = b"\x00\t\n\x0c\r "
_WHITESPACE_RUN = re.compile(rb"[\x00\t\n\x0c\r ]+")
DELIMITERS = b"()<>[]{}/%"


class Token(NamedTuple):
    kind: str  # "num" | "name" | "string" | "hexstring" | "keyword" |
    # "array_start" | "array_end" | "dict_start" | "dict_end" | "eof"
    value: object


def _is_whitespace(b: int) -> bool:
    return b in WHITESPACE


def _is_delimiter(b: int) -> bool:
    return b in DELIMITERS


def _is_regular(b: int) -> bool:
    return not _is_whitespace(b) and not _is_delimiter(b)


class Lexer:
    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos
        self._pushback: list[Token] = []

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        self.pos = pos
        self._pushback.clear()

    def push_back(self, token: Token) -> None:
        self._pushback.append(token)

    def _skip_whitespace_and_comments(self) -> None:
        buf, n = self.buf, len(self.buf)
        while self.pos < n:
            b = buf[self.pos]
            if _is_whitespace(b):
                self.pos = _WHITESPACE_RUN.match(buf, self.pos).end()
            elif b == 0x25:  # '%'
                while self.pos < n and buf[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def next_token(self) -> Token:
        if self._pushback:
            return self._pushback.pop()

        self._skip_whitespace_and_comments()
        buf, n = self.buf, len(self.buf)
        if self.pos >= n:
            return Token("eof", None)

        b = buf[self.pos]

        if b == 0x2F:  # '/'
            return self._read_name()
        if b == 0x28:  # '('
            return self._read_literal_string()
        if b == 0x3C:  # '<'
            if self.pos + 1 < n and buf[self.pos + 1] == 0x3C:
                self.pos += 2
                return Token("dict_start", None)
            return self._read_hex_string()
        if b == 0x3E:  # '>'
            if self.pos + 1 < n and buf[self.pos + 1] == 0x3E:
                self.pos += 2
                return Token("dict_end", None)
            raise ValueError(f"stray '>' at offset {self.pos}")
        if b == 0x5B:  # '['
            self.pos += 1
            return Token("array_start", None)
        if b == 0x5D:  # ']'
            self.pos += 1
            return Token("array_end", None)
        if b == 0x7B:  # '{' — PostScript-calculator syntax, rare; treat as keyword
            self.pos += 1
            return Token("keyword", "{")
        if b == 0x7D:
            self.pos += 1
            return Token("keyword", "}")
        if b in b"+-.0123456789":
            tok = self._try_read_number()
            if tok is not None:
                return tok
        return self._read_keyword()

    def _read_name(self) -> Token:
        buf, n = self.buf, len(self.buf)
        self.pos += 1  # skip '/'
        start = self.pos
        raw = bytearray()
        while self.pos < n and _is_regular(buf[self.pos]):
            b = buf[self.pos]
            if b == 0x23 and self.pos + 2 < n:  # '#'
                h = buf[self.pos + 1 : self.pos + 3]
                try:
                    raw.append(int(h, 16))
                    self.pos += 3
                    continue
                except ValueError:
                    pass
            raw.append(b)
            self.pos += 1
        _ = start
        return Token("name", raw.decode("latin-1"))

    def _read_literal_string(self) -> Token:
        buf, n = self.buf, len(self.buf)
        self.pos += 1  # skip '('
        depth = 1
        out = bytearray()
        while self.pos < n:
            c = buf[self.pos]
            if c == 0x5C:  # backslash
                self.pos += 1
                if self.pos >= n:
                    break
                e = buf[self.pos]
                if e == 0x6E:  # n
                    out.append(0x0A)
                    self.pos += 1
                elif e == 0x72:  # r
                    out.append(0x0D)
                    self.pos += 1
                elif e == 0x74:  # t
                    out.append(0x09)
                    self.pos += 1
                elif e == 0x62:  # b
                    out.append(0x08)
                    self.pos += 1
                elif e == 0x66:  # f
                    out.append(0x0C)
                    self.pos += 1
                elif e in (0x28, 0x29, 0x5C):  # ( ) \
                    out.append(e)
                    self.pos += 1
                elif e in (0x0D, 0x0A):  # line continuation
                    self.pos += 1
                    if e == 0x0D and self.pos < n and buf[self.pos] == 0x0A:
                        self.pos += 1
                elif 0x30 <= e <= 0x37:  # octal escape, up to 3 digits
                    digits = bytearray([e])
                    self.pos += 1
                    for _ in range(2):
                        if self.pos < n and 0x30 <= buf[self.pos] <= 0x37:
                            digits.append(buf[self.pos])
                            self.pos += 1
                        else:
                            break
                    out.append(int(digits, 8) & 0xFF)
                else:
                    out.append(e)
                    self.pos += 1
            elif c == 0x28:  # (
                depth += 1
                out.append(c)
                self.pos += 1
            elif c == 0x29:  # )
                depth -= 1
                self.pos += 1
                if depth == 0:
                    break
                out.append(c)
            elif c == 0x0D:
                # Bare CR or CRLF within a string is normalized to LF.
                out.append(0x0A)
                self.pos += 1
                if self.pos < n and buf[self.pos] == 0x0A:
                    self.pos += 1
            else:
                out.append(c)
                self.pos += 1
        return Token("string", bytes(out))

    def _read_hex_string(self) -> Token:
        buf, n = self.buf, len(self.buf)
        self.pos += 1  # skip '<'
        digits = bytearray()
        while self.pos < n and buf[self.pos] != 0x3E:
            b = buf[self.pos]
            if not _is_whitespace(b):
                digits.append(b)
            self.pos += 1
        if self.pos < n:
            self.pos += 1  # skip '>'
        if len(digits) % 2:
            digits.append(0x30)
        try:
            data = bytes.fromhex(digits.decode("ascii"))
        except ValueError as exc:
            raise ValueError(f"malformed hex string at offset {self.pos}") from exc
        return Token("hexstring", data)

    def _try_read_number(self) -> Optional[Token]:
        buf, n = self.buf, len(self.buf)
        start = self.pos
        i = self.pos
        if buf[i] in b"+-":
            i += 1
        saw_digit = False
        saw_dot = False
        while i < n:
            b = buf[i]
            if 0x30 <= b <= 0x39:
                saw_digit = True
                i += 1
            elif b == 0x2E and not saw_dot:
                saw_dot = True
                i += 1
            else:
                break
        if not saw_digit and not saw_dot:
            return None
        if i < n and _is_regular(buf[i]):
            # e.g. a bare '-' or something butted up against letters;
            # not a valid number token, fall through to keyword reading.
            if not saw_digit:
                return None
        text = buf[start:i].decode("ascii")
        self.pos = i
        if saw_dot:
            try:
                return Token("num", float(text))
            except ValueError:
                return Token("num", 0.0)
        return Token("num", int(text))

    def _read_keyword(self) -> Token:
        buf, n = self.buf, len(self.buf)
        start = self.pos
        while self.pos < n and _is_regular(buf[self.pos]):
            self.pos += 1
        if self.pos == start:
            # Unrecognized delimiter byte on its own; consume one byte so
            # callers make forward progress instead of looping forever.
            self.pos += 1
            return Token("keyword", buf[start:self.pos].decode("latin-1"))
        return Token("keyword", buf[start:self.pos].decode("latin-1"))
