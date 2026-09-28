"""Glyph-name and character-collection mappings used by text extraction."""

from clearleaf._encodings import (
    STANDARD_ENCODING,
    UNICODE_TO_GLYPH_NAME,
    glyph_name_to_unicode,
)
from clearleaf._textextract import TextExtractor, spans_to_plain_text


class FakeDoc:
    def resolve(self, obj):
        return obj


def extract(content: bytes, fonts: dict) -> str:
    ex = TextExtractor(FakeDoc(), {"Font": fonts})
    ex.run(content)
    return spans_to_plain_text(ex.spans)


def test_full_adobe_glyph_list_and_naming_convention():
    # full AGL names beyond the old curated subset
    assert glyph_name_to_unicode("whitesquare") == "□"
    assert glyph_name_to_unicode("whiteuppointingtriangle") == "△"
    assert glyph_name_to_unicode("alefhebrew") == "א"
    # 'square'/'triangle' are not AGL names (MuPDF maps them from its own list)
    assert glyph_name_to_unicode("square") is None
    assert glyph_name_to_unicode("f_f_i") == "ffi"  # ligature components
    assert glyph_name_to_unicode("a.sc") == "a"  # suffix stripped
    assert glyph_name_to_unicode("uni00410042") == "AB"  # uni sequences
    assert glyph_name_to_unicode("u1F600") == "\U0001f600"
    assert glyph_name_to_unicode("notaglyphname") is None


def test_reverse_map_keeps_canonical_names():
    assert UNICODE_TO_GLYPH_NAME[" "] == "space"  # not 'spacehackarabic'
    assert UNICODE_TO_GLYPH_NAME["A"] == "A"
    assert UNICODE_TO_GLYPH_NAME["—"] == "emdash"


def test_standard_encoding_is_the_real_table():
    assert STANDARD_ENCODING[0xD0] == "—"  # emdash (Latin-1 would give Ð)
    assert STANDARD_ENCODING[0x27] == "’"  # quoteright
    assert STANDARD_ENCODING[0xAE] == "ﬁ"  # fi


def test_type1_font_without_encoding_uses_standard_encoding():
    fonts = {"F1": {"Type": "Font", "Subtype": "Type1", "BaseFont": "Helvetica"}}
    assert extract(b"BT /F1 12 Tf (a\\320b) Tj ET", fonts) == "a—b"


def test_type0_identity_with_adobe_collection_and_no_tounicode():
    fonts = {
        "F1": {
            "Type": "Font",
            "Subtype": "Type0",
            "BaseFont": "X",
            "Encoding": "Identity-H",
            "DescendantFonts": [
                {
                    "Type": "Font",
                    "Subtype": "CIDFontType2",
                    "BaseFont": "X",
                    "CIDSystemInfo": {
                        "Registry": b"Adobe",
                        "Ordering": b"Japan1",
                        "Supplement": 2,
                    },
                }
            ],
        }
    }
    # CIDs 53 'T', 843/845 'あい', 3284/3722 '日本' in Adobe-Japan1
    assert extract(b"BT /F1 12 Tf <0035034B034D0CD40E8A> Tj ET", fonts) == "Tあい日本"


def test_type0_predefined_unicode_cmap_codes_are_unicode():
    fonts = {
        "F1": {
            "Type": "Font",
            "Subtype": "Type0",
            "BaseFont": "X",
            "Encoding": "UniJIS-UCS2-H",
            "DescendantFonts": [
                {
                    "Type": "Font",
                    "Subtype": "CIDFontType0",
                    "BaseFont": "X",
                    "CIDSystemInfo": {
                        "Registry": b"Adobe",
                        "Ordering": b"Japan1",
                        "Supplement": 2,
                    },
                }
            ],
        }
    }
    assert extract(b"BT /F1 12 Tf <65E5672C8A9E> Tj ET", fonts) == "日本語"
