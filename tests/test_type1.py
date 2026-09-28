"""Type 1 font programs: parsing, decryption and the charstring interpreter,
on fonts built with known glyph shapes."""

import pytest

from clearleaf._type1 import CHARSTRING_KEY, EEXEC_KEY, Type1Font, decrypt, encrypt

from .type1_builder import build_type1

SQUARE = "50 600 hsbw 0 0 rmoveto 500 0 rlineto 0 500 rlineto -500 0 rlineto closepath endchar"


def test_encrypt_decrypt_round_trip():
    plain = b"\x00\x01hello charstring\xff" * 3
    for key in (EEXEC_KEY, CHARSTRING_KEY):
        assert decrypt(b"abcd"[:0] + encrypt(b"PADD" + plain, key), key, 4) == plain


@pytest.mark.parametrize("hex_eexec", [False, True])
def test_square_glyph_outline_and_advance(hex_eexec):
    font = Type1Font(build_type1({"square": SQUARE}, hex_eexec=hex_eexec))
    assert font.glyph_outline("square") == [
        [("M", 50, 0), ("L", 550, 0), ("L", 550, 500), ("L", 50, 500)]
    ]
    assert font.glyph_advance("square") == 600
    assert font.glyph_outline("missing") == []


def test_header_font_matrix_and_encodings():
    custom = Type1Font(build_type1({"square": SQUARE}, encoding={65: "square", 66: "ff"},
                                   font_matrix=(0.002, 0, 0, 0.002, 0, 0)))
    assert custom.font_matrix == (0.002, 0.0, 0.0, 0.002, 0.0, 0.0)
    assert custom.builtin_encoding == {65: "square", 66: "ff"}
    standard = Type1Font(build_type1({"A": SQUARE}))
    assert standard.builtin_encoding[65] == "A" and standard.builtin_encoding[39] == "quoteright"


def test_curve_operators():
    prog = ("0 1000 hsbw 100 100 rmoveto "
            "10 20 30 40 50 60 rrcurveto "  # relative control points
            "100 50 50 100 hvcurveto "      # horizontal start, vertical end
            "100 50 50 100 vhcurveto "      # vertical start, horizontal end
            "closepath endchar")
    (sub,) = Type1Font(build_type1({"g": prog})).glyph_outline("g")
    assert sub[0] == ("M", 100, 100)
    assert sub[1] == ("C", 110, 120, 140, 160, 190, 220)
    assert sub[2] == ("C", 290, 220, 340, 270, 340, 370)
    assert sub[3] == ("C", 340, 470, 390, 520, 490, 520)


def test_h_v_moves_and_lines_and_multiple_subpaths():
    prog = ("0 1000 hsbw 100 hmoveto 50 vmoveto 200 hlineto 200 vlineto closepath "
            "300 hmoveto 10 0 rlineto 0 10 rlineto closepath endchar")
    a, b = Type1Font(build_type1({"g": prog})).glyph_outline("g")
    assert a == [("M", 100, 50), ("L", 300, 50), ("L", 300, 250)]
    assert b[0] == ("M", 600, 250) and b[-1] == ("L", 610, 260)


def test_callsubr_and_div():
    subrs = {5: "500 0 rlineto return"}
    prog = "0 1000 hsbw 0 0 rmoveto 5 callsubr 0 1000 4 div rlineto closepath endchar"
    (sub,) = Type1Font(build_type1({"g": prog}, subrs=subrs)).glyph_outline("g")
    assert sub == [("M", 0, 0), ("L", 500, 0), ("L", 500, 250.0)]


FLEX_SUBRS = {
    0: "3 0 callothersubr pop pop setcurrentpoint return",
    1: "0 1 callothersubr return",
    2: "0 2 callothersubr return",
    4: "0 10 hstem return",
}


def test_flex_produces_two_curves_and_ends_at_last_point():
    # Reference point, then 6 points: curve 1 = p1 p2 p3, curve 2 = p4 p5 p6.
    moves = [(100, 0), (10, 5), (20, 5), (20, 0), (20, -5), (20, -5), (10, 0)]
    prog = "0 1000 hsbw 0 0 rmoveto 1 callsubr "
    prog += " ".join(f"{dx} {dy} rmoveto 2 callsubr" for dx, dy in moves)
    prog += " 50 200 0 0 callsubr 0 100 rlineto closepath endchar"
    (sub,) = Type1Font(build_type1({"g": prog}, subrs=FLEX_SUBRS)).glyph_outline("g")
    assert sub[0] == ("M", 0, 0)
    assert sub[1] == ("C", 110, 5, 130, 10, 150, 10)
    assert sub[2] == ("C", 170, 5, 190, 0, 200, 0)
    assert sub[3] == ("L", 200, 100)  # continues from the flex end point


def test_hint_replacement_is_harmless():
    prog = "0 1000 hsbw 0 0 rmoveto 4 1 3 callothersubr pop callsubr 100 0 rlineto 0 100 rlineto closepath endchar"
    (sub,) = Type1Font(build_type1({"g": prog}, subrs=FLEX_SUBRS)).glyph_outline("g")
    assert sub == [("M", 0, 0), ("L", 100, 0), ("L", 100, 100)]


def test_seac_composes_base_and_shifted_accent():
    glyphs = {
        "A": "20 600 hsbw 0 0 rmoveto 500 0 rlineto 0 600 rlineto closepath endchar",
        "acute": "30 300 hsbw 0 0 rmoveto 100 0 rlineto 0 100 rlineto closepath endchar",
        # asb=30 adx=200 ady=650, base 'A' (65), accent 'acute' (194 in StandardEncoding)
        "Aacute": "20 600 hsbw 30 200 650 65 194 seac",
    }
    font = Type1Font(build_type1(glyphs))
    base, accent = font.glyph_outline("Aacute")
    assert base == font.glyph_outline("A")[0]
    # accent drawn from its own sidebearing (30,0), shifted by adx - asb + sbx = 200 - 30 + 20
    assert accent == [("M", 220, 650), ("L", 320, 650), ("L", 320, 750)]
    assert font.glyph_advance("Aacute") == 600


def test_malformed_charstring_keeps_partial_outline():
    prog = "0 1000 hsbw 0 0 rmoveto 100 0 rlineto 0 100 rlineto 99 callsubr"  # subr 99 doesn't exist
    (sub,) = Type1Font(build_type1({"g": prog})).glyph_outline("g")
    assert sub == [("M", 0, 0), ("L", 100, 0), ("L", 100, 100)]
