from clearleaf._encodings import (
    BASE_ENCODINGS,
    MAC_ROMAN_ENCODING,
    STANDARD_ENCODING,
    WIN_ANSI_ENCODING,
    glyph_name_to_unicode,
)


def test_win_ansi_ascii_range_matches_ascii():
    for code in range(0x20, 0x7F):
        assert WIN_ANSI_ENCODING[code] == chr(code)


def test_win_ansi_known_upper_range_glyphs():
    assert WIN_ANSI_ENCODING[0xE9] == "é"
    assert WIN_ANSI_ENCODING[0xF1] == "ñ"
    assert WIN_ANSI_ENCODING[0x80] == "€"


def test_mac_roman_ascii_range_matches_ascii():
    for code in range(0x20, 0x7F):
        assert MAC_ROMAN_ENCODING[code] == chr(code)


def test_base_encodings_registry_has_expected_names():
    for name in ["WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding", "PDFDocEncoding"]:
        assert name in BASE_ENCODINGS
        assert isinstance(BASE_ENCODINGS[name], dict)


def test_standard_encoding_ascii_range():
    for code in range(0x20, 0x7F):
        assert STANDARD_ENCODING[code] == chr(code)


def test_glyph_name_to_unicode_ascii_letters():
    assert glyph_name_to_unicode("A") == "A"
    assert glyph_name_to_unicode("z") == "z"


def test_glyph_name_to_unicode_named_punctuation():
    assert glyph_name_to_unicode("bullet") == "•"
    assert glyph_name_to_unicode("space") == " "
    assert glyph_name_to_unicode("quotedblleft") == "“"


def test_glyph_name_to_unicode_uni_convention():
    assert glyph_name_to_unicode("uni0041") == "A"
    assert glyph_name_to_unicode("uni20AC") == "€"


def test_glyph_name_to_unicode_u_convention():
    assert glyph_name_to_unicode("u0041") == "A"


def test_glyph_name_to_unicode_unknown_returns_none():
    assert glyph_name_to_unicode("g123customsubset") is None
