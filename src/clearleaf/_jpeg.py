"""Baseline sequential and progressive JPEG decoder (ISO/IEC 10918-1) for
PDF's DCTDecode filter — Huffman entropy decoding, dequantization, IDCT,
chroma upsampling, and YCbCr/YCCK -> RGB/CMYK color conversion, all from
scratch.

Both baseline (SOF0/SOF1) and progressive (SOF2) DCT with Huffman coding
are supported, covering the overwhelming majority of real-world JPEGs.
Arithmetic coding, lossless, and hierarchical JPEG are out of scope and
raise `JPEGError`; callers (`_contentpaint.py`) catch that and fall back
to a placeholder box rather than crashing the whole render.

Progressive decoding stores every block's DCT coefficients for the whole
image (refined incrementally, scan by scan — DC first/refinement passes,
AC first/refinement passes with spectral selection and successive
approximation, per spec Annex G) and only runs IDCT once at the end,
unlike baseline's decode-block-then-immediately-IDCT. Both interleaved and
non-interleaved (single-component) scans are handled — progressive JPEGs
routinely use non-interleaved scans for the AC passes, so that case had to
be supported for progressive to work at all, which incidentally makes
baseline non-interleaved scans work correctly too (previously a
documented gap).
"""

from __future__ import annotations

import math
import struct

ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

_COS = [[math.cos((2 * x + 1) * u * math.pi / 16) for x in range(8)] for u in range(8)]
_C = [1.0 / math.sqrt(2.0)] + [1.0] * 7


class JPEGError(Exception):
    pass


class _Component:
    __slots__ = (
        "id", "h", "v", "tq", "td", "ta", "dc_pred", "plane_w", "plane_h", "samples",
        "blocks_w", "blocks_h", "coeffs",
    )


def decode_jpeg(data: bytes):
    """Decode a baseline JPEG. Returns (pixels: bytes, width, height,
    num_components) — pixels are interleaved samples per pixel
    (grayscale/RGB/direct-convention CMYK; 1/3/4 components respectively),
    row-major top-to-bottom."""
    return _Decoder(data).decode()


def _build_huffman(counts: list, symbols: list) -> dict:
    table = {}
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[k]
            code += 1
            k += 1
        code <<= 1
    return table


def _clamp255(v: float) -> int:
    v = int(round(v))
    return 0 if v < 0 else 255 if v > 255 else v


def _ycbcr_to_rgb(y: int, cb: int, cr: int):
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    return (_clamp255(r), _clamp255(g), _clamp255(b))


def _idct_1d(s):
    out = [0.0] * 8
    for x in range(8):
        total = 0.0
        for u in range(8):
            total += _C[u] * s[u] * _COS[u][x]
        out[x] = 0.5 * total
    return out


def _idct_8x8(coeffs: list) -> list:
    """`coeffs` is 64 dequantized values, natural order (index = v*8+u,
    u=horizontal frequency, v=vertical), per the standard JPEG convention.
    Returns 64 level-shifted, clamped 8-bit samples, row-major (y*8+x)."""
    intermediate = [_idct_1d(coeffs[v * 8 : v * 8 + 8]) for v in range(8)]
    out = [0] * 64
    for x in range(8):
        col = [intermediate[v][x] for v in range(8)]
        col_out = _idct_1d(col)
        for y in range(8):
            out[y * 8 + x] = _clamp255(col_out[y] + 128.0)
    return out


class _BitReader:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos
        self.bitbuf = 0
        self.bitcount = 0

    def _next_byte(self):
        data = self.data
        if self.pos >= len(data):
            return 0
        b = data[self.pos]
        self.pos += 1
        if b == 0xFF:
            if self.pos < len(data):
                nxt = data[self.pos]
                if nxt == 0x00:
                    self.pos += 1  # byte-stuffed literal 0xFF
                elif 0xD0 <= nxt <= 0xD7:
                    self.pos -= 1
                    return None  # restart marker: caller must resync
                else:
                    self.pos -= 1
                    return None  # some other marker: end of entropy data
        return b

    def read_bit(self):
        if self.bitcount == 0:
            b = self._next_byte()
            if b is None:
                return None
            self.bitbuf = b
            self.bitcount = 8
        self.bitcount -= 1
        return (self.bitbuf >> self.bitcount) & 1

    def read_bits(self, n: int):
        v = 0
        for _ in range(n):
            b = self.read_bit()
            if b is None:
                return None
            v = (v << 1) | b
        return v

    def resync_after_restart(self) -> None:
        self.bitcount = 0
        data = self.data
        while self.pos + 1 < len(data):
            if data[self.pos] == 0xFF and 0xD0 <= data[self.pos + 1] <= 0xD7:
                self.pos += 2
                return
            self.pos += 1


class _Decoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.qtables: dict = {}
        self.htables_dc: dict = {}
        self.htables_ac: dict = {}
        self.components: list = []
        self.width = 0
        self.height = 0
        self.restart_interval = 0
        self.adobe_transform = None
        self.progressive = False
        self.eob_run = 0

    def _u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def _u16(self) -> int:
        v = struct.unpack(">H", self.data[self.pos : self.pos + 2])[0]
        self.pos += 2
        return v

    def decode(self):
        if self._u16() != 0xFFD8:
            raise JPEGError("not a JPEG (missing SOI)")
        while True:
            marker = self._u16()
            if marker == 0xFFD9:
                break
            if 0xFFD0 <= marker <= 0xFFD7:
                continue
            length = self._u16()
            seg_end = self.pos + length - 2
            if marker == 0xFFDB:
                self._parse_dqt(seg_end)
            elif marker == 0xFFC4:
                self._parse_dht(seg_end)
            elif marker in (0xFFC0, 0xFFC1):
                self._parse_sof(seg_end)
            elif marker == 0xFFC2:
                self.progressive = True
                self._parse_sof(seg_end)
            elif marker in (
                0xFFC3, 0xFFC5, 0xFFC6, 0xFFC7,
                0xFFC9, 0xFFCA, 0xFFCB, 0xFFCD, 0xFFCE, 0xFFCF,
            ):
                raise JPEGError("arithmetic/lossless/hierarchical JPEG not supported")
            elif marker == 0xFFDD:
                self.restart_interval = self._u16()
            elif marker == 0xFFEE:
                self._parse_app14(seg_end)
            elif marker == 0xFFDA:
                self._parse_sos_and_scan()
                continue  # self.pos already advanced past the scan data
            self.pos = seg_end
        return self._finish()

    def _parse_dqt(self, seg_end: int) -> None:
        while self.pos < seg_end:
            pq_tq = self._u8()
            pq, tq = pq_tq >> 4, pq_tq & 0xF
            table = [0] * 64
            for i in range(64):
                table[ZIGZAG[i]] = self._u16() if pq else self._u8()
            self.qtables[tq] = table

    def _parse_dht(self, seg_end: int) -> None:
        while self.pos < seg_end:
            tc_th = self._u8()
            tc, th = tc_th >> 4, tc_th & 0xF
            counts = [self._u8() for _ in range(16)]
            symbols = [self._u8() for _ in range(sum(counts))]
            table = _build_huffman(counts, symbols)
            (self.htables_dc if tc == 0 else self.htables_ac)[th] = table

    def _parse_sof(self, seg_end: int) -> None:
        _precision = self._u8()
        self.height = self._u16()
        self.width = self._u16()
        nf = self._u8()
        self.components = []
        for _ in range(nf):
            cid = self._u8()
            hv = self._u8()
            tq = self._u8()
            c = _Component()
            c.id, c.h, c.v, c.tq = cid, hv >> 4, hv & 0xF, tq
            c.td = c.ta = c.dc_pred = 0
            c.coeffs = None
            c.samples = None
            self.components.append(c)

    def _parse_app14(self, seg_end: int) -> None:
        if self.data[self.pos : self.pos + 5] == b"Adobe":
            transform_pos = self.pos + 5 + 6
            if transform_pos < seg_end:
                self.adobe_transform = self.data[transform_pos]
        self.pos = seg_end

    def _parse_sos_and_scan(self) -> None:
        ns = self._u8()
        scan_components = []
        for _ in range(ns):
            cs = self._u8()
            tables = self._u8()
            comp = next(c for c in self.components if c.id == cs)
            comp.td, comp.ta = tables >> 4, tables & 0xF
            scan_components.append(comp)
        ss = self._u8()
        se = self._u8()
        ah_al = self._u8()
        ah, al = ah_al >> 4, ah_al & 0xF
        if self.progressive:
            self._decode_progressive_scan(scan_components, ss, se, ah, al)
        else:
            self._decode_scan(scan_components)

    def _mcu_dimensions(self):
        hmax = max(c.h for c in self.components)
        vmax = max(c.v for c in self.components)
        mcu_w, mcu_h = 8 * hmax, 8 * vmax
        mcus_x = (self.width + mcu_w - 1) // mcu_w
        mcus_y = (self.height + mcu_h - 1) // mcu_h
        return hmax, vmax, mcus_x, mcus_y

    def _scan_mcu_groups(self, scan_components: list, hmax: int, vmax: int, mcus_x: int, mcus_y: int):
        """Yield one list of (component, block_x, block_y) per "MCU" —
        for an interleaved (multi-component) scan that's a real MCU
        covering each component's sampling-factor block grid; for a
        non-interleaved (single-component) scan, the spec defines an MCU
        as exactly one block, iterated in that component's own (generally
        larger, since it's not padded to other components' sampling)
        block grid — ISO/IEC 10918-1 A.2.2/A.2.4."""
        if len(scan_components) > 1:
            for my in range(mcus_y):
                for mx in range(mcus_x):
                    blocks = []
                    for c in scan_components:
                        for by in range(c.v):
                            for bx in range(c.h):
                                blocks.append((c, mx * c.h + bx, my * c.v + by))
                    yield blocks
        else:
            c = scan_components[0]
            comp_blocks_x = math.ceil(math.ceil(self.width * c.h / hmax) / 8)
            comp_blocks_y = math.ceil(math.ceil(self.height * c.v / vmax) / 8)
            for by in range(comp_blocks_y):
                for bx in range(comp_blocks_x):
                    yield [(c, bx, by)]

    def _ensure_component_planes(self, mcus_x: int, mcus_y: int) -> None:
        for c in self.components:
            if c.samples is None:
                c.plane_w = mcus_x * c.h * 8
                c.plane_h = mcus_y * c.v * 8
                c.samples = bytearray(c.plane_w * c.plane_h)

    def _decode_scan(self, scan_components: list) -> None:
        hmax, vmax, mcus_x, mcus_y = self._mcu_dimensions()
        self._ensure_component_planes(mcus_x, mcus_y)
        for c in scan_components:
            c.dc_pred = 0

        br = _BitReader(self.data, self.pos)
        groups = list(self._scan_mcu_groups(scan_components, hmax, vmax, mcus_x, mcus_y))
        total = len(groups)
        count = 0
        for blocks in groups:
            for c, bx, by in blocks:
                block = self._decode_block(br, c)
                self._idct_and_store(block, c, bx, by)
            count += 1
            if self.restart_interval and count % self.restart_interval == 0 and count < total:
                br.resync_after_restart()
                for c in scan_components:
                    c.dc_pred = 0
        self.pos = br.pos

    # -- progressive scans (ISO/IEC 10918-1 Annex G) -----------------------

    def _decode_progressive_scan(self, scan_components: list, ss: int, se: int, ah: int, al: int) -> None:
        hmax, vmax, mcus_x, mcus_y = self._mcu_dimensions()
        for c in self.components:
            if c.coeffs is None:
                c.blocks_w = mcus_x * c.h
                c.blocks_h = mcus_y * c.v
                c.coeffs = [[0] * 64 for _ in range(c.blocks_w * c.blocks_h)]
        for c in scan_components:
            c.dc_pred = 0
        self.eob_run = 0

        br = _BitReader(self.data, self.pos)
        groups = list(self._scan_mcu_groups(scan_components, hmax, vmax, mcus_x, mcus_y))
        total = len(groups)
        count = 0
        for blocks in groups:
            for c, bx, by in blocks:
                if bx >= c.blocks_w or by >= c.blocks_h:
                    continue  # non-interleaved block grid can exceed the MCU-padded allocation at the edges
                coeffs = c.coeffs[by * c.blocks_w + bx]
                if ss == 0:
                    self._decode_progressive_dc(br, c, coeffs, ah, al)
                else:
                    if ah == 0:
                        self._decode_progressive_ac_first(br, c, coeffs, ss, se, al)
                    else:
                        self._decode_progressive_ac_refine(br, c, coeffs, ss, se, al)
            count += 1
            if self.restart_interval and count % self.restart_interval == 0 and count < total:
                br.resync_after_restart()
                for c in scan_components:
                    c.dc_pred = 0
                self.eob_run = 0
        self.pos = br.pos

    def _decode_progressive_dc(self, br: _BitReader, c: "_Component", coeffs: list, ah: int, al: int) -> None:
        if ah == 0:
            s = self._decode_huff(br, self.htables_dc.get(c.td, {})) or 0
            c.dc_pred += self._receive_extend(br, s)
            coeffs[0] = c.dc_pred << al
        else:
            bit = br.read_bit()
            if bit:
                coeffs[0] |= (1 << al)

    def _decode_progressive_ac_first(
        self, br: _BitReader, c: "_Component", coeffs: list, ss: int, se: int, al: int
    ) -> None:
        if self.eob_run > 0:
            self.eob_run -= 1
            return
        ac_table = self.htables_ac.get(c.ta, {})
        k = ss
        while k <= se:
            rs = self._decode_huff(br, ac_table)
            if rs is None:
                return
            r, size = rs >> 4, rs & 0xF
            if size == 0:
                if r < 15:
                    self.eob_run = (1 << r) - 1
                    if r:
                        self.eob_run += br.read_bits(r) or 0
                    return
                k += 16  # ZRL: run of 16 zero coefficients
                continue
            k += r
            if k > se:
                return
            coeffs[ZIGZAG[k]] = self._receive_extend(br, size) << al
            k += 1

    def _decode_progressive_ac_refine(
        self, br: _BitReader, c: "_Component", coeffs: list, ss: int, se: int, al: int
    ) -> None:
        # Successive-approximation AC refinement: existing nonzero
        # coefficients each get one correction bit (nudging their
        # magnitude by +-p1); newly-nonzero coefficients (found by a
        # Huffman-coded run of zero-history positions) get a full new
        # +-p1 value. An EOB run here means "the rest of this block (and
        # possibly following blocks) gets only correction bits, no new
        # coefficients" — still has to walk every remaining position to
        # apply those correction bits.
        p1 = 1 << al
        neg_p1 = -p1
        ac_table = self.htables_ac.get(c.ta, {})
        k = ss

        if self.eob_run == 0:
            while k <= se:
                rs = self._decode_huff(br, ac_table)
                if rs is None:
                    return
                r, size = rs >> 4, rs & 0xF
                new_val = 0
                if size == 0:
                    if r < 15:
                        self.eob_run = 1 << r
                        if r:
                            self.eob_run += br.read_bits(r) or 0
                        break
                    # r == 15: ZRL — skip 16 zero-history positions (still
                    # correcting any already-nonzero ones along the way).
                else:
                    bit = br.read_bit()
                    new_val = p1 if bit else neg_p1

                while k <= se:
                    z = ZIGZAG[k]
                    if coeffs[z] != 0:
                        bit = br.read_bit()
                        if bit and (coeffs[z] & p1) == 0:
                            coeffs[z] += p1 if coeffs[z] > 0 else neg_p1
                    else:
                        if r == 0:
                            if size != 0:
                                coeffs[z] = new_val
                            k += 1
                            break
                        r -= 1
                    k += 1

        if self.eob_run > 0:
            while k <= se:
                z = ZIGZAG[k]
                if coeffs[z] != 0:
                    bit = br.read_bit()
                    if bit and (coeffs[z] & p1) == 0:
                        coeffs[z] += p1 if coeffs[z] > 0 else neg_p1
                k += 1
            self.eob_run -= 1

    def _finish_progressive_idct(self) -> None:
        for c in self.components:
            if c.coeffs is None:
                continue
            c.plane_w = c.blocks_w * 8
            c.plane_h = c.blocks_h * 8
            c.samples = bytearray(c.plane_w * c.plane_h)
            for by in range(c.blocks_h):
                for bx in range(c.blocks_w):
                    coeffs = c.coeffs[by * c.blocks_w + bx]
                    self._idct_and_store(coeffs, c, bx, by)

    def _decode_huff(self, br: _BitReader, table: dict):
        code = 0
        for length in range(1, 17):
            bit = br.read_bit()
            if bit is None:
                return None
            code = (code << 1) | bit
            sym = table.get((length, code))
            if sym is not None:
                return sym
        raise JPEGError("bad Huffman code")

    @staticmethod
    def _receive_extend(br: _BitReader, s: int) -> int:
        if s == 0:
            return 0
        v = br.read_bits(s)
        if v is None:
            return 0
        if v < (1 << (s - 1)):
            v = v - (1 << s) + 1
        return v

    def _decode_block(self, br: _BitReader, c: "_Component") -> list:
        dc_table = self.htables_dc.get(c.td, {})
        ac_table = self.htables_ac.get(c.ta, {})
        coeffs = [0] * 64
        s = self._decode_huff(br, dc_table) or 0
        c.dc_pred += self._receive_extend(br, s)
        coeffs[0] = c.dc_pred
        k = 1
        while k < 64:
            rs = self._decode_huff(br, ac_table)
            if rs is None:
                break
            r, sz = rs >> 4, rs & 0xF
            if sz == 0:
                if r == 15:
                    k += 16
                    continue
                break  # EOB
            k += r
            if k >= 64:
                break
            coeffs[ZIGZAG[k]] = self._receive_extend(br, sz)
            k += 1
        return coeffs

    def _idct_and_store(self, coeffs, c: "_Component", block_x: int, block_y: int) -> None:
        q = self.qtables.get(c.tq)
        dequant = [coeffs[i] * q[i] for i in range(64)] if q else coeffs
        block = _idct_8x8(dequant)
        ox, oy = block_x * 8, block_y * 8
        for yy in range(8):
            row_off = (oy + yy) * c.plane_w + ox
            if 0 <= row_off <= len(c.samples) - 8:
                c.samples[row_off : row_off + 8] = bytes(block[yy * 8 : yy * 8 + 8])

    def _finish(self):
        if not self.components:
            raise JPEGError("no SOF marker found")
        if self.progressive:
            self._finish_progressive_idct()
        ncomp = len(self.components)
        hmax = max(c.h for c in self.components)
        vmax = max(c.v for c in self.components)
        width, height = self.width, self.height

        def sample(c, x, y):
            sx = min(x * c.h // hmax, c.plane_w - 1)
            sy = min(y * c.v // vmax, c.plane_h - 1)
            return c.samples[sy * c.plane_w + sx]

        out = bytearray(width * height * ncomp)

        if ncomp == 1:
            c = self.components[0]
            idx = 0
            for y in range(height):
                for x in range(width):
                    out[idx] = sample(c, x, y)
                    idx += 1
            return bytes(out), width, height, 1

        if ncomp == 3:
            transform = 1 if self.adobe_transform is None else self.adobe_transform
            c0, c1, c2 = self.components
            idx = 0
            for y in range(height):
                for x in range(width):
                    v0, v1, v2 = sample(c0, x, y), sample(c1, x, y), sample(c2, x, y)
                    if transform == 0:
                        r, g, b = v0, v1, v2
                    else:
                        r, g, b = _ycbcr_to_rgb(v0, v1, v2)
                    out[idx], out[idx + 1], out[idx + 2] = r, g, b
                    idx += 3
            return bytes(out), width, height, 3

        if ncomp == 4:
            transform = 0 if self.adobe_transform is None else self.adobe_transform
            invert_stored = self.adobe_transform is not None
            c0, c1, c2, c3 = self.components
            idx = 0
            for y in range(height):
                for x in range(width):
                    v0, v1, v2, v3 = (
                        sample(c0, x, y), sample(c1, x, y), sample(c2, x, y), sample(c3, x, y)
                    )
                    if transform == 2:
                        r, g, b = _ycbcr_to_rgb(v0, v1, v2)
                        cc, mm, yy = 255 - r, 255 - g, 255 - b
                        kk = v3
                    else:
                        cc, mm, yy, kk = v0, v1, v2, v3
                        if invert_stored:
                            cc, mm, yy, kk = 255 - cc, 255 - mm, 255 - yy, 255 - kk
                    out[idx], out[idx + 1], out[idx + 2], out[idx + 3] = cc, mm, yy, kk
                    idx += 4
            return bytes(out), width, height, 4

        raise JPEGError(f"unsupported component count {ncomp}")
