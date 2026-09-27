"""Content-stream tokenizing (ISO 32000-1:2008 section 8.2 / 7.8.2): groups
the operand/operator stream into ``(operator, operands)`` instructions,
reusing the same lexical rules as object parsing (`_lexer.Lexer`) since
content streams use the same number/string/name/array/dict syntax — just
without indirect references or `obj`/`endobj` wrappers, and with one extra
construct object parsing doesn't have: inline images (`BI ... ID <raw
bytes> EI`).
"""

from __future__ import annotations

from ._lexer import Lexer
from ._objects import PDFName

_WS = b" \t\r\n\x0c\x00"


def iter_operations(data: bytes):
    """Yield ``(operator, operands)`` tuples. Inline images are yielded as
    ``("INLINE_IMAGE", (image_dict, raw_data))``.
    """
    lexer = Lexer(data)
    operands: list = []
    while True:
        tok = lexer.next_token()
        if tok.kind == "eof":
            return
        if tok.kind == "num":
            operands.append(tok.value)
        elif tok.kind == "name":
            operands.append(PDFName(tok.value))
        elif tok.kind in ("string", "hexstring"):
            operands.append(tok.value)
        elif tok.kind == "array_start":
            operands.append(_parse_array(lexer))
        elif tok.kind == "dict_start":
            operands.append(_parse_dict(lexer))
        elif tok.kind == "keyword":
            if tok.value == "true":
                operands.append(True)
            elif tok.value == "false":
                operands.append(False)
            elif tok.value == "null":
                operands.append(None)
            elif tok.value == "BI":
                image_dict, raw = _read_inline_image(lexer)
                yield ("INLINE_IMAGE", (image_dict, raw))
                operands = []
            else:
                yield (tok.value, operands)
                operands = []
        # array_end/dict_end with no matching start: malformed input,
        # drop silently and keep scanning rather than aborting extraction.


def _parse_operand(lexer: Lexer, tok):
    if tok.kind == "num":
        return tok.value
    if tok.kind == "name":
        return PDFName(tok.value)
    if tok.kind in ("string", "hexstring"):
        return tok.value
    if tok.kind == "array_start":
        return _parse_array(lexer)
    if tok.kind == "dict_start":
        return _parse_dict(lexer)
    if tok.kind == "keyword":
        if tok.value == "true":
            return True
        if tok.value == "false":
            return False
        if tok.value == "null":
            return None
    raise ValueError(f"unexpected operand token {tok!r}")


def _parse_array(lexer: Lexer) -> list:
    items = []
    while True:
        tok = lexer.next_token()
        if tok.kind == "array_end":
            return items
        if tok.kind == "eof":
            raise ValueError("unexpected EOF in content-stream array")
        items.append(_parse_operand(lexer, tok))


def _parse_dict(lexer: Lexer) -> dict:
    d = {}
    while True:
        tok = lexer.next_token()
        if tok.kind == "dict_end":
            return d
        if tok.kind == "eof":
            raise ValueError("unexpected EOF in content-stream dict")
        if tok.kind != "name":
            raise ValueError(f"expected name key, got {tok!r}")
        key = tok.value
        vtok = lexer.next_token()
        d[key] = _parse_operand(lexer, vtok)


def _read_inline_image(lexer: Lexer):
    d = {}
    while True:
        tok = lexer.next_token()
        if tok.kind == "keyword" and tok.value == "ID":
            break
        if tok.kind == "eof":
            raise ValueError("unexpected EOF in inline image dict")
        if tok.kind != "name":
            raise ValueError(f"expected name key in inline image dict, got {tok!r}")
        key = tok.value
        vtok = lexer.next_token()
        d[key] = _parse_operand(lexer, vtok)

    buf = lexer.buf
    pos = lexer.pos
    if pos < len(buf) and buf[pos] in _WS:
        pos += 1  # exactly one whitespace byte separates ID from the data

    length = d.get("L") if isinstance(d.get("L"), int) else d.get("Length")
    if isinstance(length, int):
        end = pos + length
    else:
        end = _scan_for_ei(buf, pos)
    data = buf[pos:end]
    lexer.seek(end)

    tok = lexer.next_token()
    if not (tok.kind == "keyword" and tok.value == "EI"):
        lexer.push_back(tok)
    return d, data


def _scan_for_ei(buf: bytes, start: int) -> int:
    n = len(buf)
    i = start
    while True:
        idx = buf.find(b"EI", i)
        if idx == -1:
            return n
        before_ok = idx == 0 or buf[idx - 1] in _WS
        after_ok = idx + 2 >= n or buf[idx + 2] in _WS
        if before_ok and after_ok:
            end = idx
            if end > start and buf[end - 1] in _WS:
                end -= 1
            return end
        i = idx + 2
