from tests.test_truetype import _build_format4_cmap_subtable, build_minimal_ttf

from clearleaf._fonts import SimpleFont, Type0Font, load_font, style_flags
from clearleaf._objects import PDFName, PDFStream


class FakeDoc:
    """A stand-in for PDFDocument that never needs indirect-reference
    resolution — the font dicts built in these tests are already fully
    literal, so resolve() is just the identity function."""

    def resolve(self, obj):
        return obj


def test_simple_font_widths_and_default_win_ansi_decode():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Helvetica"),
        "FirstChar": 65,
        "Widths": [700, 700, 700],  # A, B, C
    }
    font = SimpleFont(doc, font_dict)
    assert font.width_of(65) == 700
    assert font.width_of(66) == 700
    assert font.width_of(99) == 500  # no /Widths entry -> fallback guess

    decoded = list(font.decode(b"AB"))
    assert [d[2] for d in decoded] == ["A", "B"]
    assert [d[3] for d in decoded] == [700, 700]


def test_simple_font_differences_override_base_encoding():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Helvetica"),
        "Encoding": {
            "BaseEncoding": PDFName("WinAnsiEncoding"),
            "Differences": [65, PDFName("bullet")],
        },
    }
    font = SimpleFont(doc, font_dict)
    decoded = list(font.decode(b"AB"))
    assert decoded[0][2] == "•"  # remapped
    assert decoded[1][2] == "B"  # untouched, falls through to WinAnsi


def test_simple_font_missing_width_fallback():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Custom"),
        "FontDescriptor": {"MissingWidth": 333},
    }
    font = SimpleFont(doc, font_dict)
    assert font.width_of(65) == 333


def test_simple_font_prefers_to_unicode_over_encoding_table():
    doc = FakeDoc()
    to_unicode_data = (
        b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        b"1 beginbfchar\n<41> <2603>\nendbfchar\n"
    )
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Custom"),
        "Encoding": PDFName("WinAnsiEncoding"),
        "ToUnicode": PDFStream({}, to_unicode_data),
    }
    font = SimpleFont(doc, font_dict)
    # ToUnicode's CMap here uses 1-byte codes (codespace <00> <FF>), but
    # SimpleFont always looks codes up one byte at a time regardless.
    decoded = list(font.decode(b"A"))
    assert decoded[0][2] == "☃"  # snowman, not "A"


def test_type0_font_identity_h_with_widths():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type0"),
        "BaseFont": PDFName("TestCID"),
        "Encoding": PDFName("Identity-H"),
        "DescendantFonts": [
            {
                "Subtype": PDFName("CIDFontType2"),
                "DW": 1000,
                "W": [1, [500, 600], 5, 10, 250],
            }
        ],
    }
    font = Type0Font(doc, font_dict)
    assert font.width_of(1) == 500
    assert font.width_of(2) == 600
    assert font.width_of(7) == 250  # covered by the 5..10 range form
    assert font.width_of(999) == 1000  # falls back to /DW

    decoded = list(font.decode(bytes([0x00, 0x01, 0x00, 0x02])))
    assert [d[0] for d in decoded] == [1, 2]
    assert [d[1] for d in decoded] == [2, 2]
    assert [d[3] for d in decoded] == [500, 600]


def test_type0_font_to_unicode():
    doc = FakeDoc()
    to_unicode_data = (
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"1 beginbfchar\n<0001> <0048>\nendbfchar\n"
    )
    font_dict = {
        "Subtype": PDFName("Type0"),
        "BaseFont": PDFName("TestCID"),
        "Encoding": PDFName("Identity-H"),
        "DescendantFonts": [{"Subtype": PDFName("CIDFontType2")}],
        "ToUnicode": PDFStream({}, to_unicode_data),
    }
    font = Type0Font(doc, font_dict)
    decoded = list(font.decode(bytes([0x00, 0x01])))
    assert decoded[0][2] == "H"


def test_load_font_dispatches_on_subtype():
    doc = FakeDoc()
    assert isinstance(load_font(doc, {"Subtype": PDFName("Type1")}), SimpleFont)
    assert isinstance(
        load_font(doc, {"Subtype": PDFName("Type0"), "DescendantFonts": []}),
        Type0Font,
    )


def test_style_flags_bold_italic_from_descriptor():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Arial-BoldItalic"),
        "FontDescriptor": {"Flags": 0, "StemV": 200},
    }
    font = SimpleFont(doc, font_dict)
    flags = style_flags(font)
    assert flags & 2  # italic (from name heuristic)
    assert flags & 16  # bold (from StemV heuristic)


def test_style_flags_fixed_pitch_from_descriptor_flags():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Courier"),
        "FontDescriptor": {"Flags": 1},  # FixedPitch bit
    }
    font = SimpleFont(doc, font_dict)
    assert style_flags(font) & 8


def test_standard_14_font_uses_real_afm_widths_without_widths_array():
    doc = FakeDoc()
    font = SimpleFont(doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("Helvetica")})
    assert font.width_of(ord("W")) == 944  # real AFM width, not the flat 500 guess
    assert font.width_of(ord("i")) == 222


def test_non_standard_font_without_widths_falls_back_to_flat_guess():
    doc = FakeDoc()
    font = SimpleFont(doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("SomeCustomFont")})
    assert font.width_of(ord("W")) == 500.0


def test_common_font_aliases_resolve_to_standard_14_afm_widths():
    doc = FakeDoc()
    for alias in ("ArialMT", "Arial-BoldMT", "TimesNewRomanPSMT", "CourierNewPSMT"):
        font = SimpleFont(doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName(alias)})
        assert font.width_of(ord("W")) != 500.0, alias


def test_subset_tag_prefix_is_stripped_before_afm_lookup():
    doc = FakeDoc()
    font = SimpleFont(
        doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("ABCDEF+Helvetica")}
    )
    assert font.width_of(ord("W")) == 944


def test_symbolic_font_resolves_via_embedded_truetype_cmap():
    unicode_sub = _build_format4_cmap_subtable({0x0041: 1}, platform_id=3, encoding_id=1)
    symbol_sub = _build_format4_cmap_subtable({0xF041: 1}, platform_id=3, encoding_id=0)
    ttf_data = build_minimal_ttf(
        glyphs=[([], []), ([], [])], cmap_subtables=[unicode_sub, symbol_sub]
    )
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("TrueType"),
        "BaseFont": PDFName("CustomSymbolFont"),
        "FontDescriptor": {
            "Flags": 4,  # Symbolic
            "FontFile2": PDFStream({}, ttf_data),
        },
    }
    font = SimpleFont(doc, font_dict)
    assert font.symbolic is True
    decoded = list(font.decode(bytes([0x41])))
    assert decoded[0][2] == "A"


def test_symbolic_font_without_embedded_cmap_match_falls_back():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("TrueType"),
        "BaseFont": PDFName("CustomSymbolFont"),
        "FontDescriptor": {"Flags": 4},
    }
    font = SimpleFont(doc, font_dict)
    decoded = list(font.decode(bytes([0x41])))
    # No embedded font program at all: falls through to the StandardEncoding
    # guess, which for ASCII 'A' still happens to be "A".
    assert decoded[0][2] == "A"


def test_symbol_font_uses_builtin_greek_math_encoding():
    doc = FakeDoc()
    font = SimpleFont(doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("Symbol")})
    decoded = list(font.decode(bytes([0x41, 0x61])))  # code 'A'/'a' positions
    assert decoded[0][2] == "Α"  # Greek capital Alpha, not Latin "A"
    assert decoded[1][2] == "α"  # Greek small alpha, not Latin "a"


def test_zapfdingbats_font_uses_builtin_dingbat_encoding():
    doc = FakeDoc()
    font = SimpleFont(doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("ZapfDingbats")})
    decoded = list(font.decode(bytes([0x21])))
    assert decoded[0][2] == "✁"  # first dingbat glyph, not "!"


def test_font_merely_named_like_symbol_does_not_get_builtin_encoding():
    # Regression: a font named "CustomSymbolFont" (containing the substring
    # "symbol") must NOT be treated as the actual Symbol font.
    doc = FakeDoc()
    font = SimpleFont(
        doc, {"Subtype": PDFName("Type1"), "BaseFont": PDFName("CustomSymbolFont")}
    )
    decoded = list(font.decode(bytes([0x41])))
    assert decoded[0][2] == "A"  # ordinary Latin "A", not Greek Alpha


def test_symbol_font_differences_still_apply_on_top_of_builtin_encoding():
    doc = FakeDoc()
    font_dict = {
        "Subtype": PDFName("Type1"),
        "BaseFont": PDFName("Symbol"),
        "Encoding": {"Differences": [0x41, PDFName("bullet")]},
    }
    font = SimpleFont(doc, font_dict)
    decoded = list(font.decode(bytes([0x41, 0x61])))
    assert decoded[0][2] == "•"  # overridden by Differences
    assert decoded[1][2] == "α"  # untouched: still the builtin Symbol mapping
