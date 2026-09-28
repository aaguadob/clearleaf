"""Embedded CFF font programs (/FontFile3 Type1C and CIDFontType0C).

Implemented from Adobe Technical Note #5176 (The Compact Font Format
Specification) and #5177 (The Type 2 Charstring Format): header, INDEX
and DICT structures, charset, encoding and FDSelect tables, and a Type 2
charstring interpreter. Both name-keyed fonts (glyphs looked up by glyph
name, as in Type 1) and CID-keyed fonts (glyphs looked up by CID, used by
Type0/CIDFontType0 fonts) are supported.

Outlines use the same path representation as `_type1.py`, in character
space; ``font_matrix`` maps them to text space. Hints are ignored.
"""

from __future__ import annotations

import math
import struct
from typing import Optional

from ._cffstd import CFF_STANDARD_STRINGS
from ._stdenc import STANDARD_ENCODING_NAMES

MAX_SUBR_DEPTH = 10
N_STANDARD = len(CFF_STANDARD_STRINGS)

# Top/Private DICT operators used here (escaped operators are 1200 + b1).
OP_CHARSET, OP_ENCODING, OP_CHARSTRINGS, OP_PRIVATE = 15, 16, 17, 18
OP_SUBRS, OP_DEFAULT_WIDTH, OP_NOMINAL_WIDTH = 19, 20, 21
OP_FONT_MATRIX, OP_ROS, OP_FD_ARRAY, OP_FD_SELECT = 1207, 1230, 1236, 1237


def read_index(data: bytes, pos: int) -> tuple[list[bytes], int]:
    """An INDEX: count (Card16), offSize, count+1 offsets (1-based), data."""
    count = struct.unpack_from(">H", data, pos)[0]
    if count == 0:
        return [], pos + 2
    off_size = data[pos + 2]
    offsets_at = pos + 3
    offsets = [int.from_bytes(data[offsets_at + i * off_size : offsets_at + (i + 1) * off_size], "big")
               for i in range(count + 1)]
    base = offsets_at + (count + 1) * off_size - 1
    items = [data[base + offsets[i] : base + offsets[i + 1]] for i in range(count)]
    return items, base + offsets[-1]


def _real(data: bytes, i: int) -> tuple[float, int]:
    nibble_chars = "0123456789.EE?-"
    text = ""
    while True:
        byte = data[i]
        i += 1
        for nib in (byte >> 4, byte & 0xF):
            if nib == 0xF:
                return (float(text) if text not in ("", "-", ".") else 0.0), i
            text += "E-" if nib == 0xC else nibble_chars[nib]


def parse_dict(data: bytes) -> dict[int, list]:
    """A DICT: operands (integers/reals) followed by an operator."""
    result: dict[int, list] = {}
    operands: list = []
    i, n = 0, len(data)
    while i < n:
        b0 = data[i]
        i += 1
        if b0 <= 21:
            op = b0
            if b0 == 12:
                op = 1200 + data[i]
                i += 1
            result[op] = operands
            operands = []
        elif b0 == 28:
            operands.append(struct.unpack_from(">h", data, i)[0])
            i += 2
        elif b0 == 29:
            operands.append(struct.unpack_from(">i", data, i)[0])
            i += 4
        elif b0 == 30:
            value, i = _real(data, i)
            operands.append(value)
        elif 32 <= b0 <= 246:
            operands.append(b0 - 139)
        elif 247 <= b0 <= 250:
            operands.append((b0 - 247) * 256 + data[i] + 108)
            i += 1
        elif 251 <= b0 <= 254:
            operands.append(-(b0 - 251) * 256 - data[i] - 108)
            i += 1
    return result


def subr_bias(count: int) -> int:
    return 107 if count < 1240 else (1131 if count < 33900 else 32768)


class CFFFont:
    def __init__(self, data: bytes):
        self.data = data
        pos = data[2]  # hdrSize
        names, pos = read_index(data, pos)
        top_dicts, pos = read_index(data, pos)
        self.strings, pos = read_index(data, pos)
        self.global_subrs, pos = read_index(data, pos)
        if not top_dicts:
            raise ValueError("CFF without a Top DICT")
        top = parse_dict(top_dicts[0])
        self.font_name = names[0].decode("latin-1") if names else ""
        fm = top.get(OP_FONT_MATRIX)
        self.font_matrix = tuple(float(v) for v in fm) if fm and len(fm) == 6 else (0.001, 0.0, 0.0, 0.001, 0.0, 0.0)
        self.is_cid = OP_ROS in top

        self.charstrings, _ = read_index(data, top[OP_CHARSTRINGS][0])
        n_glyphs = len(self.charstrings)
        self.charset = self._read_charset(top.get(OP_CHARSET, [0])[0], n_glyphs)

        if self.is_cid:
            fd_dicts, _ = read_index(data, top[OP_FD_ARRAY][0])
            self._fd_privates = [self._read_private(parse_dict(fd)) for fd in fd_dicts]
            self._fd_select = self._read_fd_select(top[OP_FD_SELECT][0], n_glyphs) if OP_FD_SELECT in top else None
            self.cid_to_gid = {cid: gid for gid, cid in enumerate(self.charset)}
            self.name_to_gid: dict[str, int] = {}
            self.builtin_encoding: dict[int, str] = {}
        else:
            self._private = self._read_private(top)
            self.glyph_names = [self._string(sid) for sid in self.charset]
            self.name_to_gid = {name: gid for gid, name in enumerate(self.glyph_names)}
            self.builtin_encoding = self._read_encoding(top.get(OP_ENCODING, [0])[0])
        self._cache: dict[int, tuple] = {}

    # -- tables ------------------------------------------------------------

    def _string(self, sid: int) -> str:
        if sid < N_STANDARD:
            return CFF_STANDARD_STRINGS[sid]
        i = sid - N_STANDARD
        return self.strings[i].decode("latin-1") if i < len(self.strings) else f"sid{sid}"

    def _read_charset(self, offset: int, n_glyphs: int) -> list[int]:
        """gid -> SID (name-keyed) or CID (CID-keyed); gid 0 is .notdef."""
        if offset in (0, 1, 2):  # predefined: ISOAdobe / Expert / ExpertSubset
            return list(range(n_glyphs))
        d = self.data
        fmt = d[offset]
        out = [0]
        pos = offset + 1
        if fmt == 0:
            out += list(struct.unpack_from(f">{n_glyphs - 1}H", d, pos))
        else:
            left_size = 1 if fmt == 1 else 2
            while len(out) < n_glyphs:
                first = struct.unpack_from(">H", d, pos)[0]
                n_left = int.from_bytes(d[pos + 2 : pos + 2 + left_size], "big")
                pos += 2 + left_size
                out += list(range(first, first + n_left + 1))
        return out[:n_glyphs]

    def _read_encoding(self, offset: int) -> dict[int, str]:
        """code -> glyph name."""
        if offset == 0:
            return {c: n for c, n in STANDARD_ENCODING_NAMES.items() if n in self.name_to_gid}
        if offset == 1:
            return {}  # Expert encoding: effectively never used in PDFs
        d = self.data
        fmt = d[offset]
        pos = offset + 1
        codes: dict[int, str] = {}
        gid = 1
        if fmt & 0x7F == 0:
            n = d[pos]
            for i in range(n):
                if gid + i < len(self.glyph_names):
                    codes[d[pos + 1 + i]] = self.glyph_names[gid + i]
            pos += 1 + n
        else:
            n_ranges = d[pos]
            pos += 1
            for _ in range(n_ranges):
                first, n_left = d[pos], d[pos + 1]
                pos += 2
                for code in range(first, first + n_left + 1):
                    if gid < len(self.glyph_names):
                        codes[code] = self.glyph_names[gid]
                    gid += 1
        if fmt & 0x80:  # supplements: (code, SID) pairs
            n_sups = d[pos]
            for i in range(n_sups):
                code = d[pos + 1 + 3 * i]
                sid = struct.unpack_from(">H", d, pos + 2 + 3 * i)[0]
                codes[code] = self._string(sid)
        return codes

    def _read_fd_select(self, offset: int, n_glyphs: int) -> list[int]:
        d = self.data
        fmt = d[offset]
        if fmt == 0:
            return list(d[offset + 1 : offset + 1 + n_glyphs])
        n_ranges = struct.unpack_from(">H", d, offset + 1)[0]
        out = [0] * n_glyphs
        pos = offset + 3
        for r in range(n_ranges):
            first, fd = struct.unpack_from(">HB", d, pos)
            end = struct.unpack_from(">H", d, pos + 3)[0]  # next range's first, or the sentinel
            for gid in range(first, min(end, n_glyphs)):
                out[gid] = fd
            pos += 3
        return out

    def _read_private(self, dict_: dict) -> tuple[list[bytes], float, float]:
        """(local subrs, nominalWidthX, defaultWidthX)."""
        if OP_PRIVATE not in dict_:
            return [], 0.0, 0.0
        size, offset = dict_[OP_PRIVATE][:2]
        private = parse_dict(self.data[offset : offset + size])
        subrs: list[bytes] = []
        if OP_SUBRS in private:
            subrs, _ = read_index(self.data, offset + private[OP_SUBRS][0])
        return subrs, float(private.get(OP_NOMINAL_WIDTH, [0])[0]), float(private.get(OP_DEFAULT_WIDTH, [0])[0])

    # -- glyphs ------------------------------------------------------------

    def has_glyph(self, name: str) -> bool:
        return name in self.name_to_gid

    def gid_for_cid(self, cid: int) -> Optional[int]:
        return self.cid_to_gid.get(cid) if self.is_cid else (cid if cid < len(self.charstrings) else None)

    def glyph_outline(self, name: str) -> list:
        gid = self.name_to_gid.get(name)
        return self.glyph_outline_gid(gid)[0] if gid is not None else []

    def glyph_advance(self, name: str) -> Optional[float]:
        gid = self.name_to_gid.get(name)
        return self.glyph_outline_gid(gid)[1] if gid is not None else None

    def glyph_outline_cid(self, cid: int) -> list:
        gid = self.gid_for_cid(cid)
        return self.glyph_outline_gid(gid)[0] if gid is not None else []

    def glyph_outline_gid(self, gid: int, depth: int = 0) -> tuple:
        """(path, advance width) for a glyph id."""
        if gid in self._cache:
            return self._cache[gid]
        if gid is None or not 0 <= gid < len(self.charstrings) or depth > 2:
            return [], None
        if self.is_cid:
            fd = self._fd_select[gid] if self._fd_select and gid < len(self._fd_select) else 0
            local, nominal, default = self._fd_privates[fd] if fd < len(self._fd_privates) else ([], 0.0, 0.0)
        else:
            local, nominal, default = self._private
        interp = _Type2(self, local, nominal, default)
        try:
            interp.execute(self.charstrings[gid])
        except _EndChar:
            pass
        except (IndexError, KeyError, ZeroDivisionError, RecursionError, ValueError, struct.error):
            pass  # malformed charstring: keep what was drawn so far
        interp.close()
        path = interp.path
        if interp.seac is not None:
            path = self._compose_seac(interp.seac, depth)
        result = (path, interp.width)
        self._cache[gid] = result
        return result

    def _compose_seac(self, seac, depth: int) -> list:
        adx, ady, bchar, achar = seac
        path = []
        for code, dx, dy in ((bchar, 0.0, 0.0), (achar, adx, ady)):
            name = STANDARD_ENCODING_NAMES.get(int(code))
            gid = self.name_to_gid.get(name) if name else None
            for sp in (self.glyph_outline_gid(gid, depth + 1)[0] if gid is not None else []):
                path.append([_shift(seg, dx, dy) for seg in sp])
        return path


def _shift(seg: tuple, dx: float, dy: float) -> tuple:
    op, *c = seg
    return (op, *[v + (dx if i % 2 == 0 else dy) for i, v in enumerate(c)])


class _EndChar(Exception):
    pass


_STEM_OPS = {1, 3, 18, 23}  # hstem, vstem, hstemhm, vstemhm
_WIDTH_PARITY = {1: 0, 3: 0, 18: 0, 23: 0, 19: 0, 20: 0, 21: 2, 22: 1, 4: 1}


class _Type2:
    """Type 2 charstring interpreter (Technical Note #5177)."""

    def __init__(self, font: CFFFont, local_subrs: list, nominal_width: float, default_width: float):
        self.font = font
        self.local_subrs = local_subrs
        self.local_bias = subr_bias(len(local_subrs))
        self.global_bias = subr_bias(len(font.global_subrs))
        self.nominal_width = nominal_width
        self.width: Optional[float] = None
        self.default_width = default_width
        self.stack: list[float] = []
        self.transient: dict[int, float] = {}
        self.n_stems = 0
        self.x = self.y = 0.0
        self.path: list = []
        self.current: Optional[list] = None
        self.seac = None

    # path construction
    def moveto(self, x, y):
        self.close()
        self.x, self.y = x, y
        self.current = [("M", x, y)]

    def lineto(self, x, y):
        if self.current is None:
            self.current = [("M", self.x, self.y)]
        self.current.append(("L", x, y))
        self.x, self.y = x, y

    def curveto(self, dx1, dy1, dx2, dy2, dx3, dy3):
        """Relative control points, as Type 2 always gives them."""
        if self.current is None:
            self.current = [("M", self.x, self.y)]
        x1, y1 = self.x + dx1, self.y + dy1
        x2, y2 = x1 + dx2, y1 + dy2
        x3, y3 = x2 + dx3, y2 + dy3
        self.current.append(("C", x1, y1, x2, y2, x3, y3))
        self.x, self.y = x3, y3

    def close(self):
        if self.current is not None and len(self.current) > 1:
            self.path.append(self.current)
        self.current = None

    def _take_width(self, op: int, args: list) -> list:
        """The first stack-clearing operator may carry an extra leading
        argument: the glyph's width, as a delta from nominalWidthX."""
        if self.width is not None:
            return args
        if op == 14:
            has_width = len(args) in (1, 5)
        else:
            has_width = len(args) % 2 == 1 if _WIDTH_PARITY[op] == 0 else len(args) > _WIDTH_PARITY[op]
        if has_width:
            self.width = self.nominal_width + args[0]
            return args[1:]
        self.width = self.default_width
        return args

    def execute(self, cs: bytes, depth: int = 0) -> None:
        if depth > MAX_SUBR_DEPTH:
            raise RecursionError("subr nesting too deep")
        s = self.stack
        i, n = 0, len(cs)
        while i < n:
            v = cs[i]
            i += 1
            if v >= 32 or v == 28:
                if v == 28:
                    s.append(struct.unpack_from(">h", cs, i)[0])
                    i += 2
                elif v <= 246:
                    s.append(v - 139)
                elif v <= 250:
                    s.append((v - 247) * 256 + cs[i] + 108)
                    i += 1
                elif v <= 254:
                    s.append(-(v - 251) * 256 - cs[i] - 108)
                    i += 1
                else:
                    s.append(struct.unpack_from(">i", cs, i)[0] / 65536.0)
                    i += 4
                continue
            if v == 12:
                self._escape(cs[i])
                i += 1
                continue
            if v in (10, 29):  # callsubr / callgsubr
                index = int(s.pop())
                if v == 10:
                    self.execute(self.local_subrs[index + self.local_bias], depth + 1)
                else:
                    self.execute(self.font.global_subrs[index + self.global_bias], depth + 1)
                continue
            if v == 11:  # return
                return
            if v in (19, 20):  # hintmask / cntrmask: implicit vstem args, then mask bytes
                args = self._take_width(v, s[:])
                self.n_stems += len(args) // 2
                s.clear()
                i += (self.n_stems + 7) // 8
                continue
            self._op(v, s[:])
            s.clear()

    def _op(self, op: int, args: list) -> None:
        if op in _WIDTH_PARITY or op == 14:
            args = self._take_width(op, args)
        if op in _STEM_OPS:
            self.n_stems += len(args) // 2
        elif op == 21:  # rmoveto
            self.moveto(self.x + args[-2], self.y + args[-1])
        elif op == 22:  # hmoveto
            self.moveto(self.x + args[-1], self.y)
        elif op == 4:  # vmoveto
            self.moveto(self.x, self.y + args[-1])
        elif op == 5:  # rlineto
            for k in range(0, len(args) - 1, 2):
                self.lineto(self.x + args[k], self.y + args[k + 1])
        elif op in (6, 7):  # hlineto / vlineto: alternating
            horizontal = op == 6
            for a in args:
                self.lineto(self.x + a, self.y) if horizontal else self.lineto(self.x, self.y + a)
                horizontal = not horizontal
        elif op == 8:  # rrcurveto
            for k in range(0, len(args) - 5, 6):
                self.curveto(*args[k : k + 6])
        elif op == 24:  # rcurveline: curves, then a line
            k = 0
            while len(args) - k >= 8:
                self.curveto(*args[k : k + 6])
                k += 6
            self.lineto(self.x + args[k], self.y + args[k + 1])
        elif op == 25:  # rlinecurve: lines, then a curve
            k = 0
            while len(args) - k >= 8:
                self.lineto(self.x + args[k], self.y + args[k + 1])
                k += 2
            self.curveto(*args[k : k + 6])
        elif op == 26:  # vvcurveto: [dx1] {dya dxb dyb dyc}+
            k, dx1 = (1, args[0]) if len(args) % 4 == 1 else (0, 0.0)
            while k + 4 <= len(args):
                self.curveto(dx1, args[k], args[k + 1], args[k + 2], 0.0, args[k + 3])
                dx1, k = 0.0, k + 4
        elif op == 27:  # hhcurveto: [dy1] {dxa dxb dyb dxc}+
            k, dy1 = (1, args[0]) if len(args) % 4 == 1 else (0, 0.0)
            while k + 4 <= len(args):
                self.curveto(args[k], dy1, args[k + 1], args[k + 2], args[k + 3], 0.0)
                dy1, k = 0.0, k + 4
        elif op in (30, 31):  # vhcurveto / hvcurveto: alternating, optional final arg
            horizontal = op == 31
            k = 0
            while k + 4 <= len(args):
                last = len(args) - k == 5
                a, b, c, d = args[k : k + 4]
                f = args[k + 4] if last else 0.0
                if horizontal:
                    self.curveto(a, 0.0, b, c, f, d)
                else:
                    self.curveto(0.0, a, b, c, d, f)
                k += 5 if last else 4
                horizontal = not horizontal
        elif op == 14:  # endchar (4 args = seac-style accented character)
            self.close()
            if len(args) >= 4:
                self.seac = tuple(args[-4:])
            raise _EndChar

    def _escape(self, b: int) -> None:
        s = self.stack
        if b in (34, 35, 36, 37):  # hflex, flex, hflex1, flex1
            args = s[:]
            s.clear()
            if b == 35:  # flex: two full curves + flex depth
                self.curveto(*args[0:6])
                self.curveto(*args[6:12])
            elif b == 34:  # hflex: dx1 dx2 dy2 dx3 dx4 dx5 dx6
                dx1, dx2, dy2, dx3, dx4, dx5, dx6 = args[:7]
                self.curveto(dx1, 0.0, dx2, dy2, dx3, 0.0)
                self.curveto(dx4, 0.0, dx5, -dy2, dx6, 0.0)
            elif b == 36:  # hflex1: dx1 dy1 dx2 dy2 dx3 dx4 dx5 dy5 dx6
                dx1, dy1, dx2, dy2, dx3, dx4, dx5, dy5, dx6 = args[:9]
                self.curveto(dx1, dy1, dx2, dy2, dx3, 0.0)
                self.curveto(dx4, 0.0, dx5, dy5, dx6, -(dy1 + dy2 + dy5))
            else:  # flex1: five points, the sixth's larger axis given by d6
                d = args[:10]
                sum_dx = sum(d[0::2])
                sum_dy = sum(d[1::2])
                d6 = args[10]
                last = (d6, -sum_dy) if abs(sum_dx) > abs(sum_dy) else (-sum_dx, d6)
                self.curveto(*d[0:6])
                self.curveto(d[6], d[7], d[8], d[9], *last)
            return
        # Arithmetic and storage operators (rare in practice) -- these leave
        # their results on the stack instead of clearing it.
        if b == 9:
            s.append(abs(s.pop()))
        elif b == 10:
            s.append(s.pop() + s.pop())
        elif b == 11:
            y, x = s.pop(), s.pop()
            s.append(x - y)
        elif b == 12:
            y, x = s.pop(), s.pop()
            s.append(x / y)
        elif b == 14:
            s.append(-s.pop())
        elif b == 24:
            s.append(s.pop() * s.pop())
        elif b == 26:
            s.append(math.sqrt(s.pop()))
        elif b == 18:
            s.pop()
        elif b == 27:
            s.append(s[-1])
        elif b == 28:
            s[-1], s[-2] = s[-2], s[-1]
        elif b == 29:
            k = int(s.pop())
            s.append(s[-1 - max(k, 0)])
        elif b == 30:
            j, n = int(s.pop()), int(s.pop())
            if n > 0:
                seg = s[-n:]
                j %= n
                s[-n:] = seg[-j:] + seg[:-j] if j else seg
        elif b == 20:  # put
            idx, val = int(s.pop()), s.pop()
            self.transient[idx] = val
        elif b == 21:  # get
            s.append(self.transient.get(int(s.pop()), 0.0))
        elif b == 15:  # eq
            s.append(1.0 if s.pop() == s.pop() else 0.0)
        elif b == 22:  # ifelse
            v2, v1, s2, s1 = s.pop(), s.pop(), s.pop(), s.pop()
            s.append(s1 if v1 <= v2 else s2)
        elif b in (3, 4):  # and / or
            y, x = s.pop(), s.pop()
            s.append(1.0 if ((x and y) if b == 3 else (x or y)) else 0.0)
        elif b == 5:  # not
            s.append(0.0 if s.pop() else 1.0)
        else:  # dotsection (0) and reserved: ignore
            s.clear()
