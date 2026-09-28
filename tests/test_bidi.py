"""Right-to-left text comes out in logical (reading) order, decided from
glyph positions -- whether the PDF stores glyphs in visual order (left to
right on the page) or in logical order drawn right to left."""

from clearleaf._objects import PDFStream
from clearleaf._textextract import TextExtractor, spans_to_plain_text

# Codes 1-9 -> letters: 1-3 Arabic (alef, beh, teh), 4 fatha (combining mark),
# 5-6 Hebrew (alef, bet), 7-9 Latin (a, b, c), 10 space.
LETTERS = {1: "ا", 2: "ب", 3: "ت", 4: "َ", 5: "א", 6: "ב",
           7: "a", 8: "b", 9: "c", 10: " "}
TOUNICODE = (b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
             + f"{len(LETTERS)} beginbfchar ".encode()
             + b"".join(f"<{c:04X}> <{ord(t):04X}> ".encode() for c, t in LETTERS.items())
             + b"endbfchar")


class FakeDoc:
    def resolve(self, obj):
        return obj


FONTS = {"F1": {
    "Type": "Font", "Subtype": "Type0", "BaseFont": "X", "Encoding": "Identity-H",
    "ToUnicode": PDFStream({}, TOUNICODE),
    "DescendantFonts": [{"Type": "Font", "Subtype": "CIDFontType2", "BaseFont": "X", "DW": 500,
                         "W": [4, [0]]}],  # the fatha has zero width, like a real mark
}}


def run(glyph_positions) -> str:
    """glyph_positions: [(code, x)] in content-stream order, each shown at (x, 100)."""
    ops = b"BT /F1 20 Tf "
    for code, x in glyph_positions:
        ops += f"1 0 0 1 {x} 100 Tm <{code:04X}> Tj ".encode()
    ops += b"ET"
    ex = TextExtractor(FakeDoc(), {"Font": FONTS})
    ex.run(ops)
    return spans_to_plain_text(ex.spans)


def test_arabic_stored_in_visual_order():
    # On the page, left to right: teh beh alef  ==  reads alef beh teh
    assert run([(3, 100), (2, 110), (1, 120)]) == "ابت"


def test_arabic_stored_in_logical_order_drawn_right_to_left():
    assert run([(1, 120), (2, 110), (3, 100)]) == "ابت"


def test_mixed_rtl_line_keeps_embedded_latin_word_left_to_right():
    # Page, left to right: "teh beh" "abc" "beh alef" -> reads: alef beh, abc, beh teh
    visual = [(3, 100), (2, 110), (10, 120), (7, 130), (8, 140), (9, 150), (10, 160), (2, 170), (1, 180)]
    assert run(visual) == "اب abc بت"


def test_ltr_line_with_embedded_hebrew_word():
    # Page: "abc" then Hebrew shown visually as bet alef -> reads: abc, alef bet
    visual = [(7, 100), (8, 110), (9, 120), (10, 130), (6, 140), (5, 150), (10, 160), (7, 170)]
    assert run(visual) == "abc אב a"


def test_combining_mark_stays_with_its_letter():
    # beh + fatha (zero-width, drawn at the same x) then alef to its left
    assert run([(2, 110), (4, 110), (1, 100)]) == "بَا"


def test_latin_only_lines_are_untouched():
    assert run([(7, 100), (8, 110), (9, 120)]) == "abc"
