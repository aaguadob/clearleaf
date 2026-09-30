"""JBIG2Decode (ISO 32000-1 section 7.4.7): ITU-T T.88 bi-level decoding.

Written from the T.88 recommendation. Supported:

- the MQ arithmetic decoder (Annex E) and the arithmetic integer decoding
  procedures IAx / IAID (Annex A)
- generic regions (6.2): templates 0-3 with adaptive-template pixels and
  typical prediction (TPGDON), and MMR-coded regions (via _ccitt.py)
- symbol dictionaries (6.5) and text regions (6.4), arithmetic-coded,
  without refinement
- page information, region composition with all five combination
  operators, and the JBIG2Globals stream PDF uses for shared segments

Not (yet) supported, and skipped segment by segment: Huffman-coded symbol
dictionaries / text regions, refinement, halftone and pattern regions.

Output follows the PDF filter's convention: 1 bits are black.
"""

from __future__ import annotations

from .errors import PDFError

import struct

# Annex E, Table E.1: (Qe, NMPS, NLPS, SWITCH)
_QE = [
    (0x5601, 1, 1, 1), (0x3401, 2, 6, 0), (0x1801, 3, 9, 0), (0x0AC1, 4, 12, 0),
    (0x0521, 5, 29, 0), (0x0221, 38, 33, 0), (0x5601, 7, 6, 1), (0x5401, 8, 14, 0),
    (0x4801, 9, 14, 0), (0x3801, 10, 14, 0), (0x3001, 11, 17, 0), (0x2401, 12, 18, 0),
    (0x1C01, 13, 20, 0), (0x1601, 29, 21, 0), (0x5601, 15, 14, 1), (0x5401, 16, 14, 0),
    (0x5101, 17, 15, 0), (0x4801, 18, 16, 0), (0x3801, 19, 17, 0), (0x3401, 20, 18, 0),
    (0x3001, 21, 19, 0), (0x2801, 22, 19, 0), (0x2401, 23, 20, 0), (0x2201, 24, 21, 0),
    (0x1C01, 25, 22, 0), (0x1801, 26, 23, 0), (0x1601, 27, 24, 0), (0x1401, 28, 25, 0),
    (0x1201, 29, 26, 0), (0x1101, 30, 27, 0), (0x0AC1, 31, 28, 0), (0x09C1, 32, 29, 0),
    (0x08A1, 33, 30, 0), (0x0521, 34, 31, 0), (0x0441, 35, 32, 0), (0x02A1, 36, 33, 0),
    (0x0221, 37, 34, 0), (0x0141, 38, 35, 0), (0x0111, 39, 36, 0), (0x0085, 40, 37, 0),
    (0x0049, 41, 38, 0), (0x0025, 42, 39, 0), (0x0015, 43, 40, 0), (0x0009, 44, 41, 0),
    (0x0005, 45, 42, 0), (0x0001, 45, 43, 0), (0x5601, 46, 46, 0),
]


class JBIG2Error(PDFError):
    pass


class _Contexts:
    """Adaptive probability states: an index into _QE and the MPS, per context."""

    __slots__ = ("index", "mps")

    def __init__(self, size: int):
        self.index = bytearray(size)
        self.mps = bytearray(size)


class MQDecoder:
    """The MQ arithmetic decoder (T.88 Annex E, software conventions)."""

    def __init__(self, data: bytes, start: int = 0, end=None):
        self.data = data
        self.end = len(data) if end is None else end
        self.bp = start
        self.c = self._byte(self.bp) << 16
        self._byte_in()
        self.c = (self.c << 7) & 0xFFFFFFFF
        self.ct -= 7
        self.a = 0x8000

    def _byte(self, pos: int) -> int:
        return self.data[pos] if pos < self.end else 0xFF

    def _byte_in(self) -> None:
        if self._byte(self.bp) == 0xFF:
            nxt = self._byte(self.bp + 1)
            if nxt > 0x8F:
                self.c += 0xFF00
                self.ct = 8
            else:
                self.bp += 1
                self.c += nxt << 9
                self.ct = 7
        else:
            self.bp += 1
            self.c += self._byte(self.bp) << 8
            self.ct = 8
        self.c &= 0xFFFFFFFF

    def decode(self, ctx: _Contexts, cx: int) -> int:
        i = ctx.index[cx]
        qe, nmps, nlps, switch = _QE[i]
        self.a -= qe
        if (self.c >> 16) < qe:  # LPS exchange
            if self.a < qe:
                self.a = qe
                d = ctx.mps[cx]
                ctx.index[cx] = nmps
            else:
                self.a = qe
                d = 1 - ctx.mps[cx]
                if switch:
                    ctx.mps[cx] = 1 - ctx.mps[cx]
                ctx.index[cx] = nlps
            self._renorm()
        else:
            self.c -= qe << 16
            if self.a & 0x8000 == 0:  # MPS exchange
                if self.a < qe:
                    d = 1 - ctx.mps[cx]
                    if switch:
                        ctx.mps[cx] = 1 - ctx.mps[cx]
                    ctx.index[cx] = nlps
                else:
                    d = ctx.mps[cx]
                    ctx.index[cx] = nmps
                self._renorm()
            else:
                d = ctx.mps[cx]
        return d

    def _renorm(self) -> None:
        while True:
            if self.ct == 0:
                self._byte_in()
            self.a = (self.a << 1) & 0xFFFF
            self.c = (self.c << 1) & 0xFFFFFFFF
            self.ct -= 1
            if self.a & 0x8000:
                return


OOB = None


class IntDecoder:
    """Arithmetic integer decoding procedure (Annex A.2): one per IAx."""

    def __init__(self):
        self.ctx = _Contexts(512)

    def decode(self, mq: MQDecoder):
        prev = 1

        def bit():
            nonlocal prev
            d = mq.decode(self.ctx, prev)
            prev = ((prev << 1) | d) if prev < 256 else ((((prev << 1) | d) & 511) | 256)
            return d

        sign = bit()
        if not bit():
            nbits, offset = 2, 0
        elif not bit():
            nbits, offset = 4, 4
        elif not bit():
            nbits, offset = 6, 20
        elif not bit():
            nbits, offset = 8, 84
        elif not bit():
            nbits, offset = 12, 340
        else:
            nbits, offset = 32, 4436
        value = 0
        for _ in range(nbits):
            value = (value << 1) | bit()
        value += offset
        if sign:
            return OOB if value == 0 else -value
        return value


class IDDecoder:
    """IAID (Annex A.3): symbol ids of a fixed code length."""

    def __init__(self, code_len: int):
        self.len = code_len
        self.ctx = _Contexts(1 << (code_len + 1))

    def decode(self, mq: MQDecoder) -> int:
        prev = 1
        for _ in range(self.len):
            prev = (prev << 1) | mq.decode(self.ctx, prev)
        return prev - (1 << self.len)


# -- bitmaps ---------------------------------------------------------------


class Bitmap:
    __slots__ = ("width", "height", "rows")

    def __init__(self, width: int, height: int, fill: int = 0):
        self.width, self.height = width, height
        self.rows = [bytearray([fill]) * width for _ in range(height)]

    def get(self, x: int, y: int) -> int:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.rows[y][x]
        return 0

    def compose(self, other: "Bitmap", x: int, y: int, op: int) -> None:
        """Place ``other`` at (x, y): 0 OR, 1 AND, 2 XOR, 3 XNOR, 4 REPLACE.
        Rows hold one 0/1 byte per pixel, so whole-row slices combine as
        integers (a byte-wise OR/AND/XOR of 0/1 values stays 0/1)."""
        sx0 = max(0, -x)
        sx1 = min(other.width, self.width - x)
        if sx1 <= sx0:
            return
        n = sx1 - sx0
        tx0 = x + sx0
        for sy in range(max(0, -y), min(other.height, self.height - y)):
            dst = self.rows[y + sy]
            src = other.rows[sy][sx0:sx1]
            if op == 4:
                dst[tx0 : tx0 + n] = src
                continue
            a = int.from_bytes(dst[tx0 : tx0 + n], "big")
            b = int.from_bytes(src, "big")
            if op == 0:
                v = a | b
            elif op == 1:
                v = a & b
            elif op == 2:
                v = a ^ b
            else:
                v = a ^ b ^ int.from_bytes(b"\x01" * n, "big")
            dst[tx0 : tx0 + n] = v.to_bytes(n, "big")

    def packed(self) -> bytes:
        """Rows packed 8 pixels per byte, MSB first."""
        out = bytearray()
        nbytes = (self.width + 7) // 8
        pad = nbytes * 8 - self.width
        for row in self.rows:
            bits = int(row.translate(_TO_ASCII_BITS), 2) << pad if self.width else 0
            out += bits.to_bytes(nbytes, "big")
        return bytes(out)


_TO_ASCII_BITS = bytes([0x30, 0x31] + [0x31] * 254)  # 0 -> "0", anything else -> "1"


# Generic region templates (6.2.5.3): the context bits, least significant
# first, as (dx, dy) offsets from the pixel being decoded; "A1".."A4"
# stand for the adaptive-template pixels. (Reference form of _WINDOWS.)
_TEMPLATES = {
    0: [(-1, 0), (-2, 0), (-3, 0), (-4, 0), "A1", (2, -1), (1, -1), (0, -1), (-1, -1), (-2, -1),
        "A2", "A3", (1, -2), (0, -2), (-1, -2), "A4"],
    1: [(-1, 0), (-2, 0), (-3, 0), "A1", (2, -1), (1, -1), (0, -1), (-1, -1), (-2, -1),
        (2, -2), (1, -2), (0, -2), (-1, -2)],
    2: [(-1, 0), (-2, 0), "A1", (1, -1), (0, -1), (-1, -1), (-2, -1), (1, -2), (0, -2), (-1, -2)],
    3: [(-1, 0), (-2, 0), (-3, 0), (-4, 0), "A1", (1, -1), (0, -1), (-1, -1), (-2, -1), (-3, -1)],
}
_TPGDON_CONTEXT = {0: 0x9B25, 1: 0x0795, 2: 0x00E5, 3: 0x0195}


# Per template: the fixed pixels as rolling windows over the rows y, y-1
# and y-2 -- (row offset, number of bits, rightmost dx, shift into the
# context) -- plus each AT pixel's context bit. This is the bit order of
# _TEMPLATES, just computed incrementally along the row.
_WINDOWS = {
    0: ([(0, 4, -1, 0), (-1, 5, 2, 5), (-2, 3, 1, 12)], [4, 10, 11, 15]),
    1: ([(0, 3, -1, 0), (-1, 5, 2, 4), (-2, 4, 2, 9)], [3]),
    2: ([(0, 2, -1, 0), (-1, 4, 1, 3), (-2, 3, 1, 7)], [2]),
    3: ([(0, 4, -1, 0), (-1, 5, 1, 5)], [4]),
}
PAD = 8  # zero pixels either side of a padded row, so lookups need no bounds checks


def decode_generic(mq: MQDecoder, ctx: _Contexts, width: int, height: int, template: int,
                   at: list, tpgdon: bool = False) -> Bitmap:
    """Generic region decoding procedure, arithmetic case (6.2.5.7)."""
    windows, at_bits = _WINDOWS[template]
    (_, n0, _, s0), (_, n1, r1, s1) = windows[0], windows[1]
    n2, r2, s2 = (windows[2][1], windows[2][2], windows[2][3]) if len(windows) > 2 else (0, 0, 0)
    m0, m1, m2 = (1 << n0) - 1, (1 << n1) - 1, (1 << n2) - 1
    # AT pixels: (dx, dy, context bit value); dy is -2, -1 or 0 (then dx < 0)
    ats = [(dx, dy, 1 << bit) for (dx, dy), bit in zip(at, at_bits)]
    bm = Bitmap(width, height)
    rows = bm.rows
    zero_row = bytearray(width + 2 * PAD)
    padded = [zero_row, zero_row]  # rows y-2, y-1, padded
    ltp = 0
    sltp_cx = _TPGDON_CONTEXT[template]
    decode = mq.decode
    index, mps = ctx.index, ctx.mps
    for y in range(height):
        if tpgdon:
            ltp ^= decode(ctx, sltp_cx)
        if tpgdon and ltp:  # typical row: a copy of the one above
            if y > 0:
                rows[y][:] = rows[y - 1]
        else:
            row = rows[y]
            up2, up1 = padded
            cur = bytearray(width + 2 * PAD)  # the row being decoded, padded
            # windows at x = 0 cover x + right - n + 1 .. x + right
            w0 = 0
            w1 = 0
            for i in range(n1):
                w1 = (w1 << 1) | up1[PAD + r1 - n1 + 1 + i]
            w2 = 0
            for i in range(n2):
                w2 = (w2 << 1) | up2[PAD + r2 - n2 + 1 + i]
            at_src = [((up2 if dy == -2 else up1 if dy == -1 else cur), PAD + dx, value) for dx, dy, value in ats]
            # unrolled: up to four AT pixels (unused ones read a zero row)
            at_src += [(zero_row, PAD, 0)] * (4 - len(at_src))
            (sa, oa, va), (sb, ob, vb), (sc, oc, vc), (sd, od, vd) = at_src
            i1, i2 = PAD + r1 + 1, PAD + r2 + 1
            # The MQ decoder's DECODE procedure, inlined for speed (same
            # steps as MQDecoder.decode / _renorm / _byte_in).
            a, c, ct = mq.a, mq.c, mq.ct
            for x in range(width):
                cx = (w0 << s0) | (w1 << s1) | (w2 << s2)
                if sa[x + oa]:
                    cx |= va
                if sb[x + ob]:
                    cx |= vb
                if sc[x + oc]:
                    cx |= vc
                if sd[x + od]:
                    cx |= vd
                qe, nmps, nlps, switch = _QE[index[cx]]
                a -= qe
                if (c >> 16) < qe:
                    if a < qe:
                        a = qe
                        v = mps[cx]
                        index[cx] = nmps
                    else:
                        a = qe
                        v = 1 - mps[cx]
                        if switch:
                            mps[cx] = v
                        index[cx] = nlps
                    renorm = True
                else:
                    c -= qe << 16
                    if a & 0x8000:
                        v = mps[cx]
                        renorm = False
                    else:
                        if a < qe:
                            v = 1 - mps[cx]
                            if switch:
                                mps[cx] = v
                            index[cx] = nlps
                        else:
                            v = mps[cx]
                            index[cx] = nmps
                        renorm = True
                if renorm:
                    while True:
                        if ct == 0:
                            mq.c, mq.ct = c, ct
                            mq._byte_in()
                            c, ct = mq.c, mq.ct
                        a = (a << 1) & 0xFFFF
                        c = (c << 1) & 0xFFFFFFFF
                        ct -= 1
                        if a & 0x8000:
                            break
                row[x] = v
                cur[PAD + x] = v
                w0 = ((w0 << 1) | v) & m0
                w1 = ((w1 << 1) | up1[i1 + x]) & m1
                w2 = ((w2 << 1) | up2[i2 + x]) & m2
            mq.a, mq.c, mq.ct = a, c, ct
        padded = [padded[1], bytearray(PAD) + rows[y] + bytearray(PAD)]
    return bm


def decode_generic_mmr(data: bytes, width: int, height: int) -> Bitmap:
    from ._ccitt import ccitt_decode

    packed = ccitt_decode(data, k=-1, columns=width, rows=height, black_is_1=True)
    bm = Bitmap(width, height)
    row_bytes = (width + 7) // 8
    for y in range(height):
        row = bm.rows[y]
        for x in range(width):
            byte = packed[y * row_bytes + (x >> 3)] if y * row_bytes + (x >> 3) < len(packed) else 0
            row[x] = (byte >> (7 - (x & 7))) & 1
    return bm


# -- segments ------------------------------------------------------------------


class _Segment:
    __slots__ = ("number", "type", "refs", "page", "data")

    def __init__(self, number, kind, refs, page, data):
        self.number, self.type, self.refs, self.page, self.data = number, kind, refs, page, data


FILE_ID = b"\x97JB2\r\n\x1a\n"


def _segment_header(data: bytes, pos: int):
    """One segment header (7.2.2-7.2.7): (number, type, refs, page, length, next pos)."""
    number = struct.unpack(">I", data[pos : pos + 4])[0]
    flags = data[pos + 4]
    kind = flags & 0x3F
    pos += 5
    count = data[pos] >> 5
    if count == 7:  # long form: 29-bit count, then retention bits
        count = struct.unpack(">I", data[pos : pos + 4])[0] & 0x1FFFFFFF
        pos += 4 + (count + 8) // 8
    else:
        pos += 1
    ref_size = 1 if number <= 256 else (2 if number <= 65536 else 4)
    refs = [int.from_bytes(data[pos + i * ref_size : pos + (i + 1) * ref_size], "big") for i in range(count)]
    pos += count * ref_size
    if flags & 0x40:
        page = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
    else:
        page = data[pos]
        pos += 1
    length = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 4
    if length == 0xFFFFFFFF:
        raise JBIG2Error("segments of unknown length are not supported")
    return number, kind, refs, page, length, pos


def parse_segments(data: bytes) -> list:
    """Segments of a JBIG2 stream. PDF embeds bare segments (7.2, each
    header followed by its data); a stream that starts with the JBIG2 file
    header (D.4.1) is also accepted, in sequential or random-access
    organisation (all headers first, then all data)."""
    pos, random_access = 0, False
    if data[:8] == FILE_ID:
        flags = data[8]
        random_access = not flags & 1
        pos = 9 if flags & 2 else 13  # bit 1: number of pages unknown (no field)
    out = []
    if random_access:
        headers = []
        while pos + 11 <= len(data):
            header = _segment_header(data, pos)
            headers.append(header)
            pos = header[5]
            if header[1] == 51:
                break
        for number, kind, refs, page, length, _ in headers:
            out.append(_Segment(number, kind, refs, page, data[pos : pos + length]))
            pos += length
        return out
    while pos + 11 <= len(data):
        number, kind, refs, page, length, pos = _segment_header(data, pos)
        out.append(_Segment(number, kind, refs, page, data[pos : pos + length]))
        pos += length
        if kind == 51:  # end of file
            break
    return out


def _region_info(data: bytes):
    w, h, x, y = struct.unpack(">IIII", data[:16])
    return w, h, x, y, data[16] & 7


def _read_at(data: bytes, pos: int, template: int):
    count = 4 if template == 0 else 1
    at = []
    for i in range(count):
        dx, dy = struct.unpack(">bb", data[pos + 2 * i : pos + 2 * i + 2])
        at.append((dx, dy))
    return at, pos + 2 * count


def _symbol_dictionary(seg: _Segment, input_symbols: list) -> list:
    d = seg.data
    flags = struct.unpack(">H", d[:2])[0]
    if flags & 1:
        raise JBIG2Error("Huffman-coded symbol dictionaries are not supported")
    if flags & 2:
        raise JBIG2Error("refinement/aggregate symbol coding is not supported")
    template = (flags >> 10) & 3
    at, pos = _read_at(d, 2, template)
    num_exported, num_new = struct.unpack(">II", d[pos : pos + 8])
    pos += 8
    mq = MQDecoder(d, pos)
    iadh, iadw, iaex = IntDecoder(), IntDecoder(), IntDecoder()
    gb_ctx = _Contexts(1 << 16)
    new_symbols: list = []
    height = 0
    while len(new_symbols) < num_new:
        dh = iadh.decode(mq)
        if dh is OOB:
            raise JBIG2Error("bad height class")
        height += dh
        width = 0
        while True:
            dw = iadw.decode(mq)
            if dw is OOB:
                break
            width += dw
            if width <= 0 or height <= 0 or len(new_symbols) >= num_new:
                raise JBIG2Error("bad symbol size")
            new_symbols.append(decode_generic(mq, gb_ctx, width, height, template, at))
    # exported symbols: alternating runs of not-exported / exported (6.5.10)
    everything = input_symbols + new_symbols
    exported, index, flag = [], 0, 0
    while index < len(everything):
        run = iaex.decode(mq)
        if run is OOB or run < 0:
            break
        if flag:
            exported.extend(everything[index : index + run])
        index += run
        flag ^= 1
    return exported[:num_exported] if num_exported else exported


def _text_region(seg: _Segment, symbols: list) -> tuple:
    d = seg.data
    width, height, x, y, combop = _region_info(d)
    flags = struct.unpack(">H", d[17:19])[0]
    if flags & 1:
        raise JBIG2Error("Huffman-coded text regions are not supported")
    if flags & 2:
        raise JBIG2Error("refinement in text regions is not supported")
    log_strips = (flags >> 2) & 3
    refcorner = (flags >> 4) & 3
    transposed = (flags >> 6) & 1
    sbcombop = (flags >> 7) & 3
    defpixel = (flags >> 9) & 1
    dsoffset = (flags >> 10) & 0x1F
    if dsoffset >= 16:
        dsoffset -= 32
    pos = 19
    num_instances = struct.unpack(">I", d[pos : pos + 4])[0]
    pos += 4
    strips = 1 << log_strips
    code_len = max(0, (len(symbols) - 1).bit_length())
    mq = MQDecoder(d, pos)
    iadt, iafs, iads, iait = IntDecoder(), IntDecoder(), IntDecoder(), IntDecoder()
    iaid = IDDecoder(code_len)
    region = Bitmap(width, height, defpixel)
    TOPLEFT, TOPRIGHT, BOTTOMLEFT, BOTTOMRIGHT = 1, 3, 0, 2
    stript = -iadt.decode(mq) * strips
    firsts = 0
    placed = 0
    while placed < num_instances:
        stript += iadt.decode(mq) * strips
        curs = None
        while placed < num_instances:
            if curs is None:
                firsts += iafs.decode(mq)
                curs = firsts
            else:
                ids = iads.decode(mq)
                if ids is OOB:
                    break
                curs += ids + dsoffset
            curt = iait.decode(mq) if strips > 1 else 0
            t = stript + curt
            sid = iaid.decode(mq)
            if not 0 <= sid < len(symbols):
                raise JBIG2Error("symbol id out of range")
            sym = symbols[sid]
            w, h = sym.width, sym.height
            if not transposed and refcorner in (TOPRIGHT, BOTTOMRIGHT):
                curs += w - 1
            elif transposed and refcorner in (BOTTOMLEFT, BOTTOMRIGHT):
                curs += h - 1
            s = curs
            if not transposed:
                sx = s - (w - 1) if refcorner in (TOPRIGHT, BOTTOMRIGHT) else s
                ty = t if refcorner in (TOPLEFT, TOPRIGHT) else t - (h - 1)
                region.compose(sym, sx, ty, sbcombop)
            else:
                sy = s - (h - 1) if refcorner in (BOTTOMLEFT, BOTTOMRIGHT) else s
                tx = t if refcorner in (TOPLEFT, BOTTOMLEFT) else t - (w - 1)
                region.compose(sym, tx, sy, sbcombop)
            if not transposed and refcorner in (TOPLEFT, BOTTOMLEFT):
                curs += w - 1
            elif transposed and refcorner in (TOPLEFT, TOPRIGHT):
                curs += h - 1
            placed += 1
    return region, x, y, combop


def _generic_region(seg: _Segment) -> tuple:
    d = seg.data
    width, height, x, y, combop = _region_info(d)
    flags = d[17]
    mmr, template, tpgdon = flags & 1, (flags >> 1) & 3, (flags >> 3) & 1
    pos = 18
    if mmr:
        return decode_generic_mmr(d[pos:], width, height), x, y, combop
    at, pos = _read_at(d, pos, template)
    mq = MQDecoder(d, pos)
    bm = decode_generic(mq, _Contexts(1 << 16), width, height, template, at, bool(tpgdon))
    return bm, x, y, combop


def jbig2_decode(data: bytes, globals_data: bytes = b"") -> tuple:
    """(width, height, packed rows with 1 = black) of the first page."""
    segments = (parse_segments(globals_data) if globals_data else []) + parse_segments(data)
    by_number: dict = {}
    page = None
    for seg in segments:
        if seg.type == 48:  # page information
            w, h = struct.unpack(">II", seg.data[:8])
            flags = seg.data[16]
            default = (flags >> 2) & 1
            if h == 0xFFFFFFFF:
                h = 0  # striped, grown as regions arrive
            page = Bitmap(w, h, default)
            page_combop = (flags >> 3) & 3
        elif seg.type == 0:
            inputs = [s for r in seg.refs for s in by_number.get(r, [])]
            try:
                by_number[seg.number] = _symbol_dictionary(seg, inputs)
            except (JBIG2Error, IndexError, struct.error):
                by_number[seg.number] = []
        elif seg.type in (4, 6, 7, 36, 38, 39) and page is not None:
            try:
                if seg.type in (4, 6, 7):
                    symbols = [s for r in seg.refs for s in by_number.get(r, [])]
                    region, x, y, op = _text_region(seg, symbols)
                else:
                    region, x, y, op = _generic_region(seg)
            except (JBIG2Error, IndexError, struct.error):
                continue
            if y + region.height > page.height:  # striped page: extend it
                page.rows.extend(bytearray(page.width) for _ in range(y + region.height - page.height))
                page.height = y + region.height
            page.compose(region, x, y, op)
        elif seg.type == 51:
            break
    if page is None:
        raise JBIG2Error("no page information segment")
    return page.width, page.height, page.packed()
