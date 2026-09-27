"""Tests for the TrueType/OpenType (sfnt) parser, against a minimal,
hand-built font file — no real font file is needed or bundled; we construct
just enough of the sfnt binary format (table directory + head/maxp/hhea/
hmtx/loca/glyf/cmap) to exercise the parser end to end.
"""

import struct

from clearleaf._truetype import TrueTypeFont

UNITS_PER_EM = 1000


def _pad4(data: bytes) -> bytes:
    while len(data) % 4:
        data += b"\x00"
    return data


def _build_head(units_per_em=UNITS_PER_EM, index_to_loc_long=False):
    data = bytearray(54)
    struct.pack_into(">H", data, 18, units_per_em)
    struct.pack_into(">h", data, 50, 1 if index_to_loc_long else 0)
    return bytes(data)


def _build_maxp(num_glyphs):
    data = bytearray(6)
    struct.pack_into(">I", data, 0, 0x00010000)
    struct.pack_into(">H", data, 4, num_glyphs)
    return bytes(data)


def _build_hhea(num_h_metrics):
    data = bytearray(36)
    struct.pack_into(">H", data, 34, num_h_metrics)
    return bytes(data)


def _build_hmtx(widths):
    out = bytearray()
    for w in widths:
        out += struct.pack(">Hh", w, 0)
    return bytes(out)


def _build_format4_cmap_subtable(mappings: dict, platform_id: int, encoding_id: int):
    # One real (contiguous single-code) segment per mapping entry, plus the
    # mandatory 0xFFFF terminator segment. Keeps this simple by only
    # supporting single-code segments (fine for these tests).
    segs = sorted(mappings.items())
    seg_count = len(segs) + 1
    end_codes = [code for code, _gid in segs] + [0xFFFF]
    start_codes = [code for code, _gid in segs] + [0xFFFF]
    id_deltas = [(gid - code) & 0xFFFF for code, gid in segs] + [1]
    id_range_offsets = [0] * seg_count

    header = struct.pack(">HHHHHHH", 4, 0, 0, seg_count * 2, 0, 0, 0)
    body = b""
    body += b"".join(struct.pack(">H", c) for c in end_codes)
    body += struct.pack(">H", 0)  # reservedPad
    body += b"".join(struct.pack(">H", c) for c in start_codes)
    body += b"".join(struct.pack(">h", d if d < 0x8000 else d - 0x10000) for d in id_deltas)
    body += b"".join(struct.pack(">H", r) for r in id_range_offsets)
    subtable = header + body
    length_fixed = subtable[:2] + struct.pack(">H", len(subtable)) + subtable[4:]
    return (platform_id, encoding_id, length_fixed)


def _build_cmap(subtables: list):
    header = struct.pack(">HH", 0, len(subtables))
    records = b""
    body = b""
    offset = 4 + 8 * len(subtables)
    for platform_id, encoding_id, data in subtables:
        records += struct.pack(">HHI", platform_id, encoding_id, offset)
        body += data
        offset += len(data)
    return header + records + body


def _build_simple_glyph(points_with_flags, end_pts):
    num_contours = len(end_pts)
    header = struct.pack(">h", num_contours) + b"\x00" * 8  # bbox, unused by parser
    body = b"".join(struct.pack(">H", e) for e in end_pts)
    body += struct.pack(">H", 0)  # instructionLength

    flags_bytes = b""
    xs_bytes = b""
    ys_bytes = b""
    prev_x = 0
    prev_y = 0
    for x, y, on_curve in points_with_flags:
        flag = 0x01 if on_curve else 0x00
        dx = x - prev_x
        dy = y - prev_y
        prev_x, prev_y = x, y
        if -255 <= dx <= 255:
            flag |= 0x02
            if dx >= 0:
                flag |= 0x10
            xs_bytes += struct.pack(">B", abs(dx))
        else:
            xs_bytes += struct.pack(">h", dx)
        if -255 <= dy <= 255:
            flag |= 0x04
            if dy >= 0:
                flag |= 0x20
            ys_bytes += struct.pack(">B", abs(dy))
        else:
            ys_bytes += struct.pack(">h", dy)
        flags_bytes += struct.pack(">B", flag)

    return header + body + flags_bytes + xs_bytes + ys_bytes


def build_minimal_ttf(glyphs: list, cmap_subtables: list, units_per_em=UNITS_PER_EM):
    """``glyphs`` is a list of (points_with_flags, end_pts) per glyph (glyph
    0 conventionally .notdef — pass an empty glyph: ([], []))."""
    glyf_chunks = [_build_simple_glyph(pts, ends) if ends else b"" for pts, ends in glyphs]
    loca = [0]
    offset = 0
    for chunk in glyf_chunks:
        offset += len(_pad4(chunk))
        loca.append(offset)
    glyf = b"".join(_pad4(c) for c in glyf_chunks)

    head = _build_head(units_per_em, index_to_loc_long=False)
    maxp = _build_maxp(len(glyphs))
    hhea = _build_hhea(len(glyphs))
    hmtx = _build_hmtx([500] * len(glyphs))
    loca_bytes = b"".join(struct.pack(">H", o // 2) for o in loca)
    cmap = _build_cmap(cmap_subtables)

    tables = {
        "head": head,
        "maxp": maxp,
        "hhea": hhea,
        "hmtx": hmtx,
        "loca": loca_bytes,
        "glyf": glyf,
        "cmap": cmap,
    }

    num_tables = len(tables)
    directory = bytearray()
    header = struct.pack(">IHHHH", 0x00010000, num_tables, 0, 0, 0)
    table_data = b""
    offset = 12 + 16 * num_tables
    for tag, data in tables.items():
        padded = _pad4(data)
        directory += tag.encode("ascii") + struct.pack(">III", 0, offset, len(data))
        table_data += padded
        offset += len(padded)

    return header + bytes(directory) + table_data


def test_head_maxp_hhea_hmtx_parsed():
    font_data = build_minimal_ttf(glyphs=[([], []), ([], [])], cmap_subtables=[])
    font = TrueTypeFont(font_data)
    assert font.units_per_em == UNITS_PER_EM
    assert font.num_glyphs == 2
    assert font.advance_width(0) == 500
    assert font.advance_width(1) == 500
    assert font.advance_width(99) is None


def test_simple_glyph_outline_parsed():
    triangle = ([(0, 0, True), (500, 0, True), (250, 500, True)], [2])
    font_data = build_minimal_ttf(glyphs=[([], []), triangle], cmap_subtables=[])
    font = TrueTypeFont(font_data)
    outline = font.glyph_outline(1)
    assert len(outline) == 1
    contour = outline[0]
    assert len(contour) == 3
    assert contour[0] == (0, 0, True)
    assert contour[1] == (500, 0, True)
    assert contour[2] == (250, 500, True)


def test_empty_glyph_outline():
    font_data = build_minimal_ttf(glyphs=[([], []), ([], [])], cmap_subtables=[])
    font = TrueTypeFont(font_data)
    assert font.glyph_outline(0) == []


def test_cmap_format4_direct_lookup():
    subtable = _build_format4_cmap_subtable({0x0041: 1}, platform_id=3, encoding_id=1)
    font_data = build_minimal_ttf(glyphs=[([], []), ([], [])], cmap_subtables=[subtable])
    font = TrueTypeFont(font_data)
    assert len(font.cmap_subtables) == 1
    platform_id, encoding_id, table = font.cmap_subtables[0]
    assert (platform_id, encoding_id) == (3, 1)
    assert table[0x0041] == 1


def test_resolve_symbolic_code_to_unicode_via_symbol_and_unicode_subtables():
    unicode_sub = _build_format4_cmap_subtable({0x0041: 1}, platform_id=3, encoding_id=1)
    symbol_sub = _build_format4_cmap_subtable({0xF041: 1}, platform_id=3, encoding_id=0)
    font_data = build_minimal_ttf(
        glyphs=[([], []), ([], [])], cmap_subtables=[unicode_sub, symbol_sub]
    )
    font = TrueTypeFont(font_data)
    assert font.resolve_symbolic_code_to_unicode(0x41) == "A"
    assert font.resolve_symbolic_code_to_unicode(0x99) is None


def test_resolve_symbolic_code_to_unicode_without_unicode_subtable_returns_none():
    symbol_sub = _build_format4_cmap_subtable({0xF041: 1}, platform_id=3, encoding_id=0)
    font_data = build_minimal_ttf(glyphs=[([], []), ([], [])], cmap_subtables=[symbol_sub])
    font = TrueTypeFont(font_data)
    assert font.resolve_symbolic_code_to_unicode(0x41) is None
