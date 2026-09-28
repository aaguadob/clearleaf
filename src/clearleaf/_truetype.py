"""TrueType/OpenType (sfnt) font-file parsing — Microsoft/Adobe "OpenType"
spec, tables `head`/`maxp`/`loca`/`glyf`/`hmtx`/`hhea`/`cmap`.

Two independent uses:
  - `cmap` gives a code-to-glyph-id table, which is exactly what's needed to
    resolve a *symbolic* simple font's character codes to Unicode when
    there's no /ToUnicode CMap (see `_fonts.py`): PDF viewers cross the
    font's own (3,0) Microsoft-Symbol or (1,0) Macintosh subtable (code ->
    glyph id) with its (3,1) Microsoft-Unicode subtable (glyph id <-
    unicode) to recover a code -> Unicode mapping. This is a standard,
    widely-documented technique (not derived from any single implementation
    — it follows directly from what the two cmap subtables mean).
  - `glyf`/`loca` give vector glyph outlines (contours of on/off-curve
    points describing quadratic Bezier segments), which Phase 3's
    rasterizer scan-converts into pixels.

CFF-flavored OpenType (`OTTO`) and bare Type1/CFF fonts are out of scope
here — glyph_outline() returns None for glyphs it can't find, callers
handle that as "no outline available" rather than erroring.
"""

from __future__ import annotations

import struct
from typing import Optional


class TrueTypeFont:
    def __init__(self, data: bytes, face_index: int = 0):
        self.data = data
        self.face_index = face_index
        self.tables: dict[str, tuple[int, int]] = {}
        self.units_per_em = 1000
        self.num_glyphs = 0
        self.index_to_loc_long = False
        self.loca: list[int] = []
        self.hmtx: list[int] = []  # advance width per glyph id, font units
        self.cmap_subtables: list[tuple[int, int, dict]] = []  # (platform, encoding, {code: gid})
        self._gid_to_unicode_cache: Optional[dict] = None

        self._parse_directory()
        self._parse_head()
        self._parse_maxp()
        self._parse_hhea_hmtx()
        self._parse_loca()
        self._parse_cmap()

    # -- table directory -----------------------------------------------------

    def _parse_directory(self) -> None:
        data = self.data
        offset = 0
        if data[:4] == b"ttcf":  # a collection: pick the face's table directory
            count = struct.unpack(">I", data[8:12])[0]
            index = self.face_index if 0 <= self.face_index < count else 0
            offset = struct.unpack(">I", data[12 + 4 * index : 16 + 4 * index])[0]
        num_tables = struct.unpack(">H", data[offset + 4 : offset + 6])[0]
        pos = offset + 12
        for _ in range(num_tables):
            if pos + 16 > len(data):
                break
            tag = data[pos : pos + 4].decode("latin-1")
            table_offset, length = struct.unpack(">II", data[pos + 8 : pos + 16])
            self.tables[tag] = (table_offset, length)
            pos += 16

    def _table(self, tag: str) -> Optional[bytes]:
        entry = self.tables.get(tag)
        if entry is None:
            return None
        offset, length = entry
        return self.data[offset : offset + length]

    # -- head / maxp / hhea / hmtx -------------------------------------------

    def _parse_head(self) -> None:
        head = self._table("head")
        if head is None or len(head) < 54:
            return
        self.units_per_em = struct.unpack(">H", head[18:20])[0] or 1000
        self.index_to_loc_long = struct.unpack(">h", head[50:52])[0] == 1

    def _parse_maxp(self) -> None:
        maxp = self._table("maxp")
        if maxp is None or len(maxp) < 6:
            return
        self.num_glyphs = struct.unpack(">H", maxp[4:6])[0]

    def _parse_hhea_hmtx(self) -> None:
        hhea = self._table("hhea")
        hmtx = self._table("hmtx")
        if hhea is None or hmtx is None or len(hhea) < 36:
            return
        num_h_metrics = struct.unpack(">H", hhea[34:36])[0]
        widths = []
        pos = 0
        last_width = 0
        for i in range(num_h_metrics):
            if pos + 4 > len(hmtx):
                break
            last_width = struct.unpack(">H", hmtx[pos : pos + 2])[0]
            widths.append(last_width)
            pos += 4
        remaining = max(0, self.num_glyphs - len(widths))
        widths.extend([last_width] * remaining)
        self.hmtx = widths

    def advance_width(self, glyph_id: int) -> Optional[float]:
        if 0 <= glyph_id < len(self.hmtx):
            return self.hmtx[glyph_id] * 1000.0 / self.units_per_em
        return None

    # -- loca / glyf -----------------------------------------------------

    def _parse_loca(self) -> None:
        loca = self._table("loca")
        if loca is None:
            return
        if self.index_to_loc_long:
            count = len(loca) // 4
            self.loca = [
                struct.unpack(">I", loca[i * 4 : i * 4 + 4])[0] for i in range(count)
            ]
        else:
            count = len(loca) // 2
            self.loca = [
                struct.unpack(">H", loca[i * 2 : i * 2 + 2])[0] * 2 for i in range(count)
            ]

    def glyph_outline(self, glyph_id: int, _depth: int = 0):
        """Return a list of contours (each a list of (x, y, on_curve)
        tuples in font units) for ``glyph_id``, or None if unavailable."""
        glyf = self._table("glyf")
        if glyf is None or not self.loca or _depth > 8:
            return None
        if glyph_id < 0 or glyph_id + 1 >= len(self.loca):
            return None
        start, end = self.loca[glyph_id], self.loca[glyph_id + 1]
        if end <= start or end > len(glyf):
            return []  # empty glyph (e.g. space) — valid, just no contours
        data = glyf[start:end]
        num_contours = struct.unpack(">h", data[0:2])[0]
        if num_contours >= 0:
            return self._parse_simple_glyph(data, num_contours)
        return self._parse_composite_glyph(data, _depth)

    @staticmethod
    def _parse_simple_glyph(data: bytes, num_contours: int):
        pos = 10
        end_pts = struct.unpack(f">{num_contours}H", data[pos : pos + 2 * num_contours])
        pos += 2 * num_contours
        num_points = (end_pts[-1] + 1) if end_pts else 0
        instr_len = struct.unpack(">H", data[pos : pos + 2])[0]
        pos += 2 + instr_len

        flags = []
        while len(flags) < num_points:
            flag = data[pos]
            pos += 1
            flags.append(flag)
            if flag & 0x08:  # REPEAT_FLAG
                repeat = data[pos]
                pos += 1
                flags.extend([flag] * repeat)
        flags = flags[:num_points]

        xs = []
        x = 0
        for flag in flags:
            if flag & 0x02:  # X_SHORT
                dx = data[pos]
                pos += 1
                if not (flag & 0x10):
                    dx = -dx
            elif flag & 0x10:  # X_SAME_OR_POSITIVE (long form omitted)
                dx = 0
            else:
                dx = struct.unpack(">h", data[pos : pos + 2])[0]
                pos += 2
            x += dx
            xs.append(x)

        ys = []
        y = 0
        for flag in flags:
            if flag & 0x04:  # Y_SHORT
                dy = data[pos]
                pos += 1
                if not (flag & 0x20):
                    dy = -dy
            elif flag & 0x20:  # Y_SAME_OR_POSITIVE
                dy = 0
            else:
                dy = struct.unpack(">h", data[pos : pos + 2])[0]
                pos += 2
            y += dy
            ys.append(y)

        points = [(xs[i], ys[i], bool(flags[i] & 0x01)) for i in range(num_points)]

        contours = []
        start = 0
        for end in end_pts:
            contours.append(points[start : end + 1])
            start = end + 1
        return contours

    def _parse_composite_glyph(self, data: bytes, depth: int):
        pos = 10
        contours = []
        while True:
            flags, glyph_index = struct.unpack(">HH", data[pos : pos + 4])
            pos += 4
            if flags & 0x0001:  # ARG_1_AND_2_ARE_WORDS
                arg1, arg2 = struct.unpack(">hh", data[pos : pos + 4])
                pos += 4
            else:
                arg1, arg2 = struct.unpack(">bb", data[pos : pos + 2])
                pos += 2
            a, b, c, d = 1.0, 0.0, 0.0, 1.0
            if flags & 0x0008:  # WE_HAVE_A_SCALE
                a = d = struct.unpack(">h", data[pos : pos + 2])[0] / 16384.0
                pos += 2
            elif flags & 0x0040:  # WE_HAVE_AN_X_AND_Y_SCALE
                a = struct.unpack(">h", data[pos : pos + 2])[0] / 16384.0
                d = struct.unpack(">h", data[pos + 2 : pos + 4])[0] / 16384.0
                pos += 4
            elif flags & 0x0080:  # WE_HAVE_A_TWO_BY_TWO
                a, b, c, d = (
                    v / 16384.0
                    for v in struct.unpack(">hhhh", data[pos : pos + 8])
                )
                pos += 8

            dx, dy = (arg1, arg2) if flags & 0x0002 else (0, 0)  # ARGS_ARE_XY_VALUES
            sub = self.glyph_outline(glyph_index, depth + 1) or []
            for contour in sub:
                transformed = [
                    (a * px + c * py + dx, b * px + d * py + dy, on)
                    for (px, py, on) in contour
                ]
                contours.append(transformed)

            if not (flags & 0x0020):  # MORE_COMPONENTS
                break
        return contours

    # -- cmap -----------------------------------------------------------

    def _parse_cmap(self) -> None:
        cmap = self._table("cmap")
        if cmap is None or len(cmap) < 4:
            return
        num_tables = struct.unpack(">H", cmap[2:4])[0]
        for i in range(num_tables):
            rec_off = 4 + i * 8
            if rec_off + 8 > len(cmap):
                break
            platform_id, encoding_id, offset = struct.unpack(
                ">HHI", cmap[rec_off : rec_off + 8]
            )
            table = self._parse_cmap_subtable(cmap, offset)
            if table:
                self.cmap_subtables.append((platform_id, encoding_id, table))

    def names(self) -> dict:
        """{name id: string} from the 'name' table (Windows or Mac English)."""
        table = self._table("name")
        out: dict = {}
        if table is None or len(table) < 6:
            return out
        count, string_offset = struct.unpack(">HH", table[2:6])
        for i in range(count):
            rec = table[6 + 12 * i : 18 + 12 * i]
            if len(rec) < 12:
                break
            platform, encoding, language, name_id, length, offset = struct.unpack(">HHHHHH", rec)
            raw = table[string_offset + offset : string_offset + offset + length]
            if platform == 3 and language == 0x409:
                out[name_id] = raw.decode("utf-16-be", "replace")
            elif platform == 1 and language == 0 and name_id not in out:
                out[name_id] = raw.decode("mac-roman", "replace")
        return out

    @staticmethod
    def face_count(data: bytes) -> int:
        return struct.unpack(">I", data[8:12])[0] if data[:4] == b"ttcf" else 1

    @staticmethod
    def _parse_cmap_subtable(cmap: bytes, offset: int) -> dict:
        if offset + 2 > len(cmap):
            return {}
        fmt = struct.unpack(">H", cmap[offset : offset + 2])[0]
        result: dict[int, int] = {}
        try:
            if fmt == 0:
                for code in range(256):
                    gid = cmap[offset + 6 + code]
                    if gid:
                        result[code] = gid
            elif fmt == 4:
                seg_x2 = struct.unpack(">H", cmap[offset + 6 : offset + 8])[0]
                seg_count = seg_x2 // 2
                end_base = offset + 14
                start_base = end_base + seg_x2 + 2
                delta_base = start_base + seg_x2
                range_base = delta_base + seg_x2
                for s in range(seg_count):
                    end = struct.unpack(">H", cmap[end_base + 2 * s : end_base + 2 * s + 2])[0]
                    start = struct.unpack(">H", cmap[start_base + 2 * s : start_base + 2 * s + 2])[0]
                    delta = struct.unpack(">h", cmap[delta_base + 2 * s : delta_base + 2 * s + 2])[0]
                    range_offset = struct.unpack(">H", cmap[range_base + 2 * s : range_base + 2 * s + 2])[0]
                    if start == 0xFFFF and end == 0xFFFF:
                        continue
                    for code in range(start, end + 1):
                        if range_offset == 0:
                            gid = (code + delta) & 0xFFFF
                        else:
                            glyph_addr = (
                                range_base + 2 * s + range_offset + 2 * (code - start)
                            )
                            if glyph_addr + 2 > len(cmap):
                                continue
                            gid = struct.unpack(">H", cmap[glyph_addr : glyph_addr + 2])[0]
                            if gid:
                                gid = (gid + delta) & 0xFFFF
                        if gid:
                            result[code] = gid
            elif fmt == 6:
                first, count = struct.unpack(">HH", cmap[offset + 6 : offset + 10])
                for i in range(count):
                    gid_off = offset + 10 + i * 2
                    gid = struct.unpack(">H", cmap[gid_off : gid_off + 2])[0]
                    if gid:
                        result[first + i] = gid
            elif fmt == 12:
                num_groups = struct.unpack(">I", cmap[offset + 12 : offset + 16])[0]
                for i in range(num_groups):
                    g_off = offset + 16 + i * 12
                    start_char, end_char, start_gid = struct.unpack(
                        ">III", cmap[g_off : g_off + 12]
                    )
                    for code in range(start_char, min(end_char, start_char + 65536) + 1):
                        result[code] = start_gid + (code - start_char)
        except (struct.error, IndexError):
            return result
        return result

    def resolve_symbolic_code_to_unicode(self, code: int) -> Optional[str]:
        """Cross a (3,0)/(1,0) code->glyph-id subtable with a (3,1)/(0,x)
        glyph-id<-unicode subtable to recover Unicode text for a symbolic
        simple font's character code, absent a /ToUnicode CMap."""
        symbol_table = None
        mac_table = None
        for platform_id, encoding_id, table in self.cmap_subtables:
            if platform_id == 3 and encoding_id == 0:
                symbol_table = table
            elif platform_id == 1 and encoding_id == 0:
                mac_table = table

        gid_to_unicode = self._gid_to_unicode_map()
        if not gid_to_unicode:
            return None

        if symbol_table is not None:
            for candidate in (0xF000 + code, code):
                gid = symbol_table.get(candidate)
                if gid is not None and gid in gid_to_unicode:
                    return chr(gid_to_unicode[gid])
        if mac_table is not None:
            gid = mac_table.get(code)
            if gid is not None and gid in gid_to_unicode:
                return chr(gid_to_unicode[gid])
        return None

    def _gid_to_unicode_map(self) -> dict:
        if self._gid_to_unicode_cache is not None:
            return self._gid_to_unicode_cache
        unicode_table = None
        for platform_id, encoding_id, table in self.cmap_subtables:
            if platform_id == 3 and encoding_id == 1:
                unicode_table = table
                break
            if platform_id == 0 and unicode_table is None:
                unicode_table = table
        result = {}
        if unicode_table:
            # First-seen code wins per glyph id (arbitrary but stable tie-break).
            for code, gid in unicode_table.items():
                result.setdefault(gid, code)
        self._gid_to_unicode_cache = result
        return result
