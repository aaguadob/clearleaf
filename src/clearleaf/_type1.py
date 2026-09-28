"""Embedded Type 1 font programs (/FontFile): parsing and glyph outlines.

Implemented from Adobe's "Adobe Type 1 Font Format" specification (the
"black book"): a clear-text PostScript header (FontMatrix, Encoding),
followed by an eexec-encrypted private part holding /Subrs and
/CharStrings. Each charstring is itself encrypted and, once decrypted, is
a small stack language of path operators (chapter 6), plus the Flex and
hint-replacement conventions built on callothersubr (chapter 8) and the
seac accented-character operator.

Hints (hstem/vstem/dotsection) only matter for grid-fitting and are
ignored. Outlines are returned in character space (font units); callers
map them to text space with ``font_matrix``, typically [0.001 0 0 0.001 0 0].
"""

from __future__ import annotations

import re
from typing import Optional

from ._stdenc import STANDARD_ENCODING_NAMES

EEXEC_KEY = 55665
CHARSTRING_KEY = 4330
MAX_SUBR_DEPTH = 10

# A path is a list of subpaths; each subpath is a list of segments:
# ("M", x, y) first, then ("L", x, y) or ("C", x1, y1, x2, y2, x3, y3).
Path = list


def decrypt(data: bytes, key: int, skip: int) -> bytes:
    """Type 1 decryption (spec 7.2): r starts at ``key``; each cipher byte c
    gives plain = c ^ (r >> 8), then r = (c + r) * 52845 + 22719 mod 2^16.
    The first ``skip`` plaintext bytes are random padding."""
    r = key
    out = bytearray(len(data))
    for i, c in enumerate(data):
        out[i] = c ^ (r >> 8)
        r = ((c + r) * 52845 + 22719) & 0xFFFF
    return bytes(out[skip:])


def encrypt(plain: bytes, key: int) -> bytes:
    """Inverse of `decrypt` (with no padding) -- used by the test suite to
    build fonts; the padding bytes are the caller's to prepend."""
    r = key
    out = bytearray(len(plain))
    for i, p in enumerate(plain):
        c = p ^ (r >> 8)
        out[i] = c
        r = ((c + r) * 52845 + 22719) & 0xFFFF
    return bytes(out)


_HEX = set(b"0123456789abcdefABCDEF")


def _eexec_ciphertext(data: bytes) -> bytes:
    idx = data.find(b"eexec")
    if idx < 0:
        raise ValueError("no eexec section")
    pos = idx + len(b"eexec")
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    body = data[pos:]
    # Binary or hexadecimal form (spec 7.2): hex if the first 4 bytes are hex digits.
    if len(body) >= 4 and all(b in _HEX for b in body[:4]):
        digits = bytes(b for b in body if b in _HEX)
        return bytes.fromhex(digits[: len(digits) // 2 * 2].decode("ascii"))
    return body


_NUM = rb"-?\d+(?:\.\d+)?"


class Type1Font:
    def __init__(self, data: bytes):
        clear_end = data.find(b"eexec")
        clear = data[:clear_end] if clear_end >= 0 else data
        self.font_matrix = self._parse_font_matrix(clear)
        self.builtin_encoding = self._parse_encoding(clear)

        private = decrypt(_eexec_ciphertext(data), EEXEC_KEY, 4)
        m = re.search(rb"/lenIV\s+(-?\d+)", private)
        self.len_iv = int(m.group(1)) if m else 4
        self.subrs = self._parse_subrs(private)
        self.charstrings = self._parse_charstrings(private)
        self._cache: dict[str, tuple] = {}

    # -- header ----------------------------------------------------------

    @staticmethod
    def _parse_font_matrix(clear: bytes) -> tuple:
        m = re.search(rb"/FontMatrix\s*[\[{]([^\]}]*)[\]}]", clear)
        if m:
            nums = [float(x) for x in re.findall(_NUM, m.group(1))]
            if len(nums) == 6:
                return tuple(nums)
        return (0.001, 0.0, 0.0, 0.001, 0.0, 0.0)

    @staticmethod
    def _parse_encoding(clear: bytes) -> dict[int, str]:
        if re.search(rb"/Encoding\s+StandardEncoding\s+def", clear):
            return dict(STANDARD_ENCODING_NAMES)
        return {int(code): name.decode("latin-1")
                for code, name in re.findall(rb"dup\s+(\d+)\s*/([^\s/\[\]{}()<>%]+)\s+put", clear)}

    # -- private dictionary ----------------------------------------------

    def _decrypt_charstring(self, blob: bytes) -> bytes:
        if self.len_iv < 0:
            return blob
        return decrypt(blob, CHARSTRING_KEY, self.len_iv)

    def _parse_subrs(self, private: bytes) -> dict[int, bytes]:
        subrs: dict[int, bytes] = {}
        m = re.search(rb"/Subrs\s+\d+\s+array", private)
        if not m:
            return subrs
        pos = m.end()
        # "dup <index> <nbytes> RD <nbytes of binary> NP" -- RD/NP may also be
        # spelled -| and |, and exactly one space separates RD from the data.
        entry = re.compile(rb"dup\s+(\d+)\s+(\d+)\s+\S+ ")
        stop = private.find(b"/CharStrings", pos)
        while True:
            m = entry.search(private, pos)
            if not m or (0 <= stop < m.start()):
                break
            n = int(m.group(2))
            subrs[int(m.group(1))] = self._decrypt_charstring(private[m.end() : m.end() + n])
            pos = m.end() + n
        return subrs

    def _parse_charstrings(self, private: bytes) -> dict[str, bytes]:
        charstrings: dict[str, bytes] = {}
        m = re.search(rb"/CharStrings\s+\d+\s+dict\s+dup\s+begin", private)
        if not m:
            m = re.search(rb"/CharStrings.*?begin", private, re.S)
            if not m:
                return charstrings
        pos = m.end()
        # "/<name> <nbytes> RD <nbytes of binary> ND"
        entry = re.compile(rb"/([^\s/\[\]{}()<>%]+)\s+(\d+)\s+\S+ ")
        end_marker = re.compile(rb"\s*end\b")
        while True:
            if end_marker.match(private, pos):
                break
            m = entry.search(private, pos)
            if not m:
                break
            n = int(m.group(2))
            charstrings[m.group(1).decode("latin-1")] = self._decrypt_charstring(private[m.end() : m.end() + n])
            # skip the ND / |- terminator
            pos = m.end() + n
            t = re.match(rb"\s*\S+", private[pos:])
            pos += t.end() if t else 0
        return charstrings

    # -- glyphs ------------------------------------------------------------

    def has_glyph(self, name: str) -> bool:
        return name in self.charstrings

    def glyph_outline(self, name: str) -> Path:
        """The glyph's outline in character space, or [] if unknown."""
        return self._run(name)[0]

    def glyph_advance(self, name: str) -> Optional[float]:
        """Advance width from hsbw/sbw, in character space."""
        return self._run(name)[1] if name in self.charstrings else None

    def _run(self, name: str, depth: int = 0) -> tuple:
        if name in self._cache:
            return self._cache[name]
        cs = self.charstrings.get(name)
        if cs is None or depth > 2:
            return [], None
        interp = _Interpreter(self)
        try:
            interp.execute(cs)
        except _EndChar:
            pass
        except (IndexError, KeyError, ZeroDivisionError, RecursionError, ValueError):
            pass  # malformed charstring: keep what was drawn so far
        interp.finish()
        path = interp.path
        if interp.seac is not None:
            path = self._compose_seac(interp, depth)
        result = (path, interp.advance)
        self._cache[name] = result
        return result

    def _compose_seac(self, interp: "_Interpreter", depth: int) -> Path:
        asb, adx, ady, bchar, achar = interp.seac
        base = STANDARD_ENCODING_NAMES.get(int(bchar))
        accent = STANDARD_ENCODING_NAMES.get(int(achar))
        path = [list(sp) for sp in self._run(base, depth + 1)[0]] if base else []
        if accent:
            # The accent's origin sits at (adx, ady) relative to the base
            # character's origin; its own charstring starts at its left
            # sidebearing asb, while the composite's hsbw put the base's
            # sidebearing at sbx -- hence the shift adx - asb + sbx.
            dx = adx - asb + interp.sbx
            for sp in self._run(accent, depth + 1)[0]:
                path.append([_shift(seg, dx, ady) for seg in sp])
        return path


def _shift(seg: tuple, dx: float, dy: float) -> tuple:
    op, *c = seg
    return (op, *[v + (dx if i % 2 == 0 else dy) for i, v in enumerate(c)])


class _EndChar(Exception):
    pass


class _Interpreter:
    """Type 1 charstring interpreter (spec chapter 6)."""

    def __init__(self, font: Type1Font):
        self.font = font
        self.stack: list[float] = []
        self.ps_stack: list[float] = []  # PostScript operand stack for callothersubr/pop
        self.x = self.y = 0.0
        self.sbx = 0.0
        self.advance: Optional[float] = None
        self.path: Path = []
        self.current: Optional[list] = None
        self.flex: Optional[list] = None
        self.seac = None

    # path construction
    def _moveto(self, x, y):
        if self.flex is not None:
            self.flex.append((x, y))  # Flex (spec 8.3): moves record control points
            self.x, self.y = x, y
            return
        self._close()
        self.x, self.y = x, y
        self.current = [("M", x, y)]

    def _lineto(self, x, y):
        if self.current is None:
            self.current = [("M", self.x, self.y)]
        self.current.append(("L", x, y))
        self.x, self.y = x, y

    def _curveto(self, x1, y1, x2, y2, x3, y3):
        if self.current is None:
            self.current = [("M", self.x, self.y)]
        self.current.append(("C", x1, y1, x2, y2, x3, y3))
        self.x, self.y = x3, y3

    def _close(self):
        if self.current is not None and len(self.current) > 1:
            self.path.append(self.current)
        self.current = None

    def finish(self):
        self._close()

    def execute(self, cs: bytes, depth: int = 0) -> None:
        if depth > MAX_SUBR_DEPTH:
            raise RecursionError("subr nesting too deep")
        s = self.stack
        i, n = 0, len(cs)
        while i < n:
            v = cs[i]
            i += 1
            if v >= 32:
                if v <= 246:
                    s.append(v - 139)
                elif v <= 250:
                    s.append((v - 247) * 256 + cs[i] + 108)
                    i += 1
                elif v <= 254:
                    s.append(-(v - 251) * 256 - cs[i] - 108)
                    i += 1
                else:
                    s.append(int.from_bytes(cs[i : i + 4], "big", signed=True))
                    i += 4
                continue
            if v == 12:
                op = 1200 + cs[i]
                i += 1
            else:
                op = v
            if op == 11:  # return
                return
            self._op(op, depth)

    def _op(self, op: int, depth: int) -> None:
        s = self.stack
        if op == 13:  # hsbw: sbx wx
            self.sbx, self.advance = s[-2], s[-1]
            self.x, self.y = self.sbx, 0.0
        elif op == 1207:  # sbw: sbx sby wx wy
            self.sbx, sby, self.advance = s[-4], s[-3], s[-2]
            self.x, self.y = self.sbx, sby
        elif op == 21:  # rmoveto
            self._moveto(self.x + s[-2], self.y + s[-1])
        elif op == 22:  # hmoveto
            self._moveto(self.x + s[-1], self.y)
        elif op == 4:  # vmoveto
            self._moveto(self.x, self.y + s[-1])
        elif op == 5:  # rlineto
            self._lineto(self.x + s[-2], self.y + s[-1])
        elif op == 6:  # hlineto
            self._lineto(self.x + s[-1], self.y)
        elif op == 7:  # vlineto
            self._lineto(self.x, self.y + s[-1])
        elif op == 8:  # rrcurveto dx1 dy1 dx2 dy2 dx3 dy3
            dx1, dy1, dx2, dy2, dx3, dy3 = s[-6:]
            x1, y1 = self.x + dx1, self.y + dy1
            x2, y2 = x1 + dx2, y1 + dy2
            self._curveto(x1, y1, x2, y2, x2 + dx3, y2 + dy3)
        elif op == 30:  # vhcurveto dy1 dx2 dy2 dx3
            dy1, dx2, dy2, dx3 = s[-4:]
            x1, y1 = self.x, self.y + dy1
            x2, y2 = x1 + dx2, y1 + dy2
            self._curveto(x1, y1, x2, y2, x2 + dx3, y2)
        elif op == 31:  # hvcurveto dx1 dx2 dy2 dy3
            dx1, dx2, dy2, dy3 = s[-4:]
            x1, y1 = self.x + dx1, self.y
            x2, y2 = x1 + dx2, y1 + dy2
            self._curveto(x1, y1, x2, y2, x2, y2 + dy3)
        elif op == 9:  # closepath
            self._close()
        elif op == 10:  # callsubr
            index = int(s.pop())
            self.execute(self.font.subrs[index], depth + 1)
            return  # the subr has managed the stack itself
        elif op == 14:  # endchar
            self._close()
            raise _EndChar
        elif op == 1206:  # seac asb adx ady bchar achar
            self.seac = tuple(s[-5:])
            s.clear()
            raise _EndChar
        elif op == 1212:  # div
            b, a = s.pop(), s.pop()
            s.append(a / b)
            return
        elif op == 1216:  # callothersubr: arg1 .. argn n othersubr#
            other = int(s.pop())
            count = int(s.pop())
            args = [s.pop() for _ in range(count)][::-1]
            self._othersubr(other, args)
            return
        elif op == 1217:  # pop: move a result from the PostScript stack
            s.append(self.ps_stack.pop() if self.ps_stack else 0)
            return
        elif op == 1233:  # setcurrentpoint
            self.x, self.y = s[-2], s[-1]
        # hstem (1), vstem (3), dotsection (1200), vstem3/hstem3 (1201/1202):
        # hints only -- nothing to draw.
        s.clear()

    def _othersubr(self, other: int, args: list) -> None:
        if other == 1:  # start Flex
            self.flex = []
        elif other == 2:  # Flex point marker (the preceding rmoveto recorded it)
            pass
        elif other == 0 and self.flex is not None:  # end Flex: flexheight x y
            pts = self.flex
            self.flex = None
            # 7 points: a reference point, then the two curves' control
            # points and end points (spec 8.3, figure 8b).
            if len(pts) >= 7:
                c = pts[-6:]
                self._curveto(*c[0], *c[1], *c[2])
                self._curveto(*c[3], *c[4], *c[5])
            end_x, end_y = (args[1], args[2]) if len(args) >= 3 else (self.x, self.y)
            # Subrs[0] then does "pop pop setcurrentpoint": pops must yield x, then y.
            self.ps_stack.extend([end_y, end_x])
        else:
            # Hint replacement (3: "subr# 1 3 callothersubr pop callsubr") and
            # any other OtherSubrs: return the arguments unchanged, so the
            # following pops stay balanced -- for 3 that calls the hint subr
            # itself, which only holds hints.
            self.ps_stack.extend(reversed(args))
