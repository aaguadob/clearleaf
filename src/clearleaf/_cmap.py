"""CMap parsing (ISO 32000-1:2008 section 9.7.5) for two distinct uses:

  - ToUnicode CMaps: map a font's character codes to Unicode text, via
    bfchar/bfrange operators.
  - CID CMaps (a font's /Encoding when it isn't a predefined name like
    Identity-H): map character codes to CIDs, via cidchar/cidrange.

Both share the same PostScript-derived CMap syntax, so one parser + one
lookup structure serves both (the values stored are just str for the
ToUnicode case, int for the CID case).
"""

from __future__ import annotations

from typing import Optional, Union

from ._lexer import Lexer


class CMap:
    def __init__(self, default_length: int = 1):
        self.codespace_ranges: list[tuple[int, int, int]] = []  # (lo, hi, nbytes)
        self.single_map: dict[int, Union[str, int]] = {}
        self.ranges: list[tuple[int, int, Union[str, int]]] = []
        self.default_length = default_length
        self._identity = False

    @classmethod
    def identity(cls, byte_length: int = 2) -> "CMap":
        cm = cls(default_length=byte_length)
        cm.codespace_ranges.append((0, (1 << (8 * byte_length)) - 1, byte_length))
        cm._identity = True
        return cm

    def lookup(self, code: int) -> Optional[Union[str, int]]:
        if self._identity:
            return code
        if code in self.single_map:
            return self.single_map[code]
        for lo, hi, base in self.ranges:
            if lo <= code <= hi:
                offset = code - lo
                if isinstance(base, int):
                    return base + offset
                if isinstance(base, str) and base:
                    chars = list(base)
                    chars[-1] = chr(ord(chars[-1]) + offset)
                    return "".join(chars)
                return None
        return None

    def split_codes(self, data: bytes):
        """Yield (code, nbytes) pairs, splitting ``data`` per the declared
        codespace ranges (falling back to a fixed default_length if none
        were declared, e.g. for a freshly-built identity CMap with no
        explicit ranges but its own synthesized codespace)."""
        if not self.codespace_ranges:
            length = self.default_length
            for i in range(0, len(data) - len(data) % length, length):
                yield int.from_bytes(data[i : i + length], "big"), length
            return

        lengths = sorted({length for _lo, _hi, length in self.codespace_ranges})
        i, n = 0, len(data)
        while i < n:
            matched = False
            for length in lengths:
                if i + length > n:
                    continue
                val = int.from_bytes(data[i : i + length], "big")
                for lo, hi, l in self.codespace_ranges:
                    if l == length and lo <= val <= hi:
                        yield val, length
                        i += length
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                length = lengths[0]
                if i + length > n:
                    break
                yield int.from_bytes(data[i : i + length], "big"), length
                i += length


def parse_cmap_stream(data: bytes, default_length: int = 1) -> CMap:
    cmap = CMap(default_length=default_length)
    lx = Lexer(data)
    while True:
        tok = lx.next_token()
        if tok.kind == "eof":
            break
        if tok.kind != "keyword":
            continue
        if tok.value == "begincodespacerange":
            _read_codespacerange(lx, cmap)
        elif tok.value == "beginbfchar":
            _read_char(lx, cmap, "endbfchar", _decode_unicode_dst)
        elif tok.value == "beginbfrange":
            _read_range(lx, cmap, "endbfrange", _decode_unicode_dst)
        elif tok.value == "begincidchar":
            _read_char(lx, cmap, "endcidchar", _decode_cid_dst)
        elif tok.value == "begincidrange":
            _read_range(lx, cmap, "endcidrange", _decode_cid_dst)
    return cmap


def _decode_unicode_dst(tok) -> Optional[str]:
    if tok.kind != "hexstring":
        return None
    try:
        return tok.value.decode("utf-16-be")
    except UnicodeDecodeError:
        return None


def _decode_cid_dst(tok) -> Optional[int]:
    if tok.kind != "num":
        return None
    return int(tok.value)


def _read_codespacerange(lx: Lexer, cmap: CMap) -> None:
    while True:
        tok = lx.next_token()
        if tok.kind == "eof" or (tok.kind == "keyword" and tok.value == "endcodespacerange"):
            return
        if tok.kind != "hexstring":
            continue
        lo = tok.value
        hi_tok = lx.next_token()
        if hi_tok.kind != "hexstring":
            continue
        length = len(lo)
        cmap.codespace_ranges.append(
            (int.from_bytes(lo, "big"), int.from_bytes(hi_tok.value, "big"), length)
        )


def _read_char(lx: Lexer, cmap: CMap, end_kw: str, decode_dst) -> None:
    while True:
        tok = lx.next_token()
        if tok.kind == "eof" or (tok.kind == "keyword" and tok.value == end_kw):
            return
        if tok.kind != "hexstring":
            continue
        src = int.from_bytes(tok.value, "big")
        dst_tok = lx.next_token()
        dst = decode_dst(dst_tok)
        if dst is not None:
            cmap.single_map[src] = dst


def _read_range(lx: Lexer, cmap: CMap, end_kw: str, decode_dst) -> None:
    while True:
        tok = lx.next_token()
        if tok.kind == "eof" or (tok.kind == "keyword" and tok.value == end_kw):
            return
        if tok.kind != "hexstring":
            continue
        lo_tok = tok
        hi_tok = lx.next_token()
        if hi_tok.kind != "hexstring":
            continue
        dst_tok = lx.next_token()
        lo = int.from_bytes(lo_tok.value, "big")
        hi = int.from_bytes(hi_tok.value, "big")
        if dst_tok.kind == "array_start":
            items = []
            while True:
                t = lx.next_token()
                if t.kind == "eof" or t.kind == "array_end":
                    break
                items.append(decode_dst(t))
            for i, code in enumerate(range(lo, hi + 1)):
                if i < len(items) and items[i] is not None:
                    cmap.single_map[code] = items[i]
        else:
            base = decode_dst(dst_tok)
            if base is not None:
                cmap.ranges.append((lo, hi, base))
