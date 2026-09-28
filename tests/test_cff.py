"""CFF font programs: structures and the Type 2 charstring interpreter, on
fonts built with known glyph shapes. (All 58 CFF programs in the benchmark
corpus were also cross-checked glyph-by-glyph against fontTools during
development: 1,781/1,781 outlines identical.)"""

from clearleaf._cff import CFFFont, parse_dict, read_index, subr_bias

from .cff_builder import build_cff, build_cid_cff, dict_bytes, index

SQUARE = "100 50 rmoveto 500 0 rlineto 0 500 rlineto -500 0 rlineto endchar"


def test_index_and_dict_primitives():
    items, end = read_index(index([b"ab", b"", b"xyz"]), 0)
    assert items == [b"ab", b"", b"xyz"] and end == len(index([b"ab", b"", b"xyz"]))
    assert read_index(b"\x00\x00", 0) == ([], 2)
    d = parse_dict(dict_bytes([(17, [1234]), (1207, [0.001, 0.0, 0.0, 0.001, 0.0, 0.0]), (18, [40, 500])]))
    assert d[17] == [1234] and d[18] == [40, 500]
    assert d[1207] == [0.001, 0.0, 0.0, 0.001, 0.0, 0.0]
    assert (subr_bias(10), subr_bias(2000), subr_bias(40000)) == (107, 1131, 32768)


def test_name_keyed_glyph_charset_and_default_encoding():
    font = CFFFont(build_cff({"A": SQUARE, "custom.glyph": SQUARE}))
    assert not font.is_cid
    assert font.glyph_names == [".notdef", "A", "custom.glyph"]
    assert font.glyph_outline("A") == [[("M", 100, 50), ("L", 600, 50), ("L", 600, 550), ("L", 100, 550)]]
    assert font.builtin_encoding == {65: "A"}  # StandardEncoding, restricted to glyphs present
    assert font.font_matrix == (0.001, 0.0, 0.0, 0.001, 0.0, 0.0)


def test_custom_encoding_keeps_code_zero():
    font = CFFFont(build_cff({"minus": SQUARE, "Gamma": SQUARE}, encoding={0: "minus", 7: "Gamma"}))
    assert font.builtin_encoding == {0: "minus", 7: "Gamma"}


def test_widths_nominal_default_and_first_operator_parity():
    glyphs = {
        "a": "20 100 50 rmoveto 10 0 rlineto 0 10 rlineto endchar",      # width arg before rmoveto (3 args)
        "b": "100 50 rmoveto 10 0 rlineto 0 10 rlineto endchar",         # no width -> default
        "c": "30 0 10 20 10 hstem 100 hmoveto 10 0 rlineto 0 10 rlineto endchar",  # odd stem args -> width
    }
    font = CFFFont(build_cff(glyphs, nominal_width=500, default_width=333))
    assert font.glyph_advance("a") == 520
    assert font.glyph_advance("b") == 333
    assert font.glyph_advance("c") == 530
    assert font.glyph_outline("a")[0][0] == ("M", 100, 50)
    assert font.glyph_outline("c")[0][0] == ("M", 100, 0)


def test_curve_operator_families():
    prog = ("0 0 rmoveto "
            "10 20 30 40 50 60 rrcurveto "      # absolute end (90, 120)
            "5 10 20 30 40 hhcurveto "          # 5 args: dy1=5, then (10,5) (20,30) (40,0)
            "7 10 20 30 40 vvcurveto "          # 5 args: dx1=7, then (7,10) (20,30) (0,40)
            "10 20 30 40 hvcurveto "            # (10,0) (20,30) (0,40)
            "10 20 30 40 5 vhcurveto "          # (0,10) (20,30) (40,5): final extra arg
            "endchar")
    (sub,) = CFFFont(build_cff({"g": prog})).glyph_outline("g")
    assert sub[1] == ("C", 10, 20, 40, 60, 90, 120)
    assert sub[2] == ("C", 100, 125, 120, 155, 160, 155)
    assert sub[3] == ("C", 167, 165, 187, 195, 187, 235)
    assert sub[4] == ("C", 197, 235, 217, 265, 217, 305)
    assert sub[5] == ("C", 217, 315, 237, 345, 277, 350)
    # without the optional leading arg, hhcurveto starts flat
    (flat,) = CFFFont(build_cff({"g": "0 0 rmoveto 10 20 30 40 hhcurveto endchar"})).glyph_outline("g")
    assert flat[1] == ("C", 10, 0, 30, 30, 70, 30)


def test_line_curve_combinations_and_alternating_lines():
    prog = ("0 0 rmoveto 100 hlineto 50 100 vlineto "            # h 100, v 50, h 100
            "10 10 20 20 30 30 5 5 rcurveline "                  # curve, then line
            "5 5 1 1 2 2 3 3 rlinecurve endchar")                # line, then curve
    (sub,) = CFFFont(build_cff({"g": prog})).glyph_outline("g")
    assert sub[1:4] == [("L", 100, 0), ("L", 100, 50), ("L", 200, 50)]
    assert sub[4] == ("C", 210, 60, 230, 80, 260, 110)
    assert sub[5] == ("L", 265, 115)
    assert sub[6] == ("L", 270, 120)
    assert sub[7] == ("C", 271, 121, 273, 123, 276, 126)


def test_flex_family():
    hflex = "0 0 rmoveto 10 20 5 30 40 -20 50 hflex endchar"
    (sub,) = CFFFont(build_cff({"g": hflex})).glyph_outline("g")
    assert sub[1] == ("C", 10, 0, 30, 5, 60, 5)
    assert sub[2] == ("C", 100, 5, 80, 0, 130, 0)   # dy back down by -dy2
    flex1 = "0 0 rmoveto 10 1 10 1 10 1 10 -1 10 -1 7 flex1 endchar"
    (sub,) = CFFFont(build_cff({"g": flex1})).glyph_outline("g")
    assert sub[2][-2:] == (57, 0)  # |sum dx| > |sum dy|: last dx = 7, y returns to start


def test_subrs_gsubrs_with_bias_and_hintmask():
    subrs = ["500 0 rlineto return"]                 # local index 0 -> called as -107
    gsubrs = ["0 500 rlineto return"]                # global index 0 -> called as -107
    prog = "0 10 hstemhm 0 0 rmoveto hintmask mask:80 -107 callsubr -107 callgsubr -500 0 rlineto endchar"
    (sub,) = CFFFont(build_cff({"g": prog}, subrs=subrs, gsubrs=gsubrs)).glyph_outline("g")
    assert sub == [("M", 0, 0), ("L", 500, 0), ("L", 500, 500), ("L", 0, 500)]


def test_arithmetic_operators():
    prog = "0 0 rmoveto 100 4 div 10 20 sub rlineto endchar"   # (25, -10)
    (sub,) = CFFFont(build_cff({"g": prog})).glyph_outline("g")
    assert sub[1] == ("L", 25, -10)


def test_endchar_seac_composition():
    glyphs = {
        "A": "0 0 rmoveto 500 0 rlineto 0 600 rlineto endchar",
        "acute": "0 0 rmoveto 100 0 rlineto 0 100 rlineto endchar",
        "Aacute": "200 650 65 194 endchar",  # adx ady bchar achar
    }
    font = CFFFont(build_cff(glyphs))
    base, accent = font.glyph_outline("Aacute")
    assert base == font.glyph_outline("A")[0]
    assert accent == [("M", 200, 650), ("L", 300, 650), ("L", 300, 750)]


def test_cid_keyed_font_with_fdselect_and_per_fd_subrs():
    glyphs = {
        100: "0 0 rmoveto -107 callsubr endchar",    # FD 0: its subr draws a 10-unit line
        101: "0 0 rmoveto 5 5 rlineto 5 0 rlineto endchar",
        200: "0 0 rmoveto -107 callsubr endchar",    # FD 1: same subr index, different subr
    }
    fd_of = {100: 0, 101: 0, 200: 1}
    fd_subrs = [["10 0 rlineto 0 10 rlineto return"], ["99 0 rlineto 0 99 rlineto return"]]
    font = CFFFont(build_cid_cff(glyphs, fd_of, fd_subrs))
    assert font.is_cid
    assert font.cid_to_gid == {0: 0, 100: 1, 101: 2, 200: 3}
    assert font.glyph_outline_cid(100) == [[("M", 0, 0), ("L", 10, 0), ("L", 10, 10)]]
    assert font.glyph_outline_cid(200) == [[("M", 0, 0), ("L", 99, 0), ("L", 99, 99)]]
    assert font.glyph_outline_cid(999) == []
