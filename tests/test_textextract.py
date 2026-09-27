import os

import clearleaf as pdf
from clearleaf._textextract import TextExtractor, spans_to_dict, spans_to_plain_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def path(name):
    return os.path.join(FIXTURES, name)


def test_multiline_plain_text_joins_lines_with_newlines():
    doc = pdf.open(path("text_multiline.pdf"))
    assert doc[0].get_text() == "Line one\nLine two\nLine three"
    doc.close()


def test_differences_encoding_resolves_via_agl():
    doc = pdf.open(path("text_differences.pdf"))
    assert doc[0].get_text() == "•"
    doc.close()


def test_cid_identity_font_uses_embedded_tounicode():
    doc = pdf.open(path("text_cid_identity.pdf"))
    assert doc[0].get_text() == "HELLO"
    doc.close()


def test_form_xobject_text_is_recursed_into():
    doc = pdf.open(path("text_form_xobject.pdf"))
    assert doc[0].get_text() == "In form"
    doc.close()


def test_inline_image_does_not_break_surrounding_text():
    doc = pdf.open(path("text_inline_image.pdf"))
    assert doc[0].get_text() == "Before\nAfter"
    doc.close()


def test_get_text_dict_shape_and_values():
    doc = pdf.open(path("classic_simple.pdf"))
    result = doc[0].get_text("dict")
    assert result["width"] == 612.0
    assert result["height"] == 792.0
    assert len(result["blocks"]) == 1
    block = result["blocks"][0]
    assert block["type"] == 0
    assert len(block["lines"]) == 1
    span = block["lines"][0]["spans"][0]
    assert span["text"] == "Hello clearleaf"
    assert span["font"] == "Helvetica"
    assert span["size"] == 12.0
    assert span["origin"] == [72.0, 712.0]
    doc.close()


def test_get_text_unsupported_option_raises():
    doc = pdf.open(path("classic_simple.pdf"))
    try:
        doc[0].get_text("bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass
    doc.close()


_HELVETICA = {
    "Font": {
        "F1": {
            "Subtype": "Type1",
            "BaseFont": "Helvetica",
            "Encoding": "WinAnsiEncoding",
        }
    }
}


class _FakeDoc:
    def resolve(self, obj):
        return obj


def _extract(content: bytes):
    extractor = TextExtractor(_FakeDoc(), _HELVETICA)
    extractor.run(content)
    return extractor


def test_tj_word_gap_inserts_space():
    # pdfTeX-style output: no space glyphs, words separated purely by TJ
    # positioning (-447/1000 em here). Readers expect a space.
    extractor = _extract(b"BT /F1 12 Tf 0 0 Td [(Lorem)-447(ipsum)-446(dolor)] TJ ET")
    assert spans_to_plain_text(extractor.spans) == "Lorem ipsum dolor"
    # The spans themselves stay at their real positions, left to right.
    xs = [s.origin[0] for s in extractor.spans]
    assert xs == sorted(xs)


def test_tj_kerning_does_not_insert_space():
    # Small adjustments inside a word are kerning, not word breaks.
    extractor = _extract(b"BT /F1 12 Tf 0 0 Td [(non)27(um)28(y)-28(d)] TJ ET")
    assert spans_to_plain_text(extractor.spans) == "nonumyd"


def test_gap_does_not_double_an_existing_space():
    extractor = _extract(b"BT /F1 12 Tf 0 0 Td [(Lorem )-447(ipsum)] TJ ET")
    assert spans_to_plain_text(extractor.spans) == "Lorem ipsum"


def test_words_placed_by_td_get_a_space():
    extractor = _extract(b"BT /F1 12 Tf 0 0 Td (Hello) Tj 40 0 Td (world) Tj ET")
    assert spans_to_plain_text(extractor.spans) == "Hello world"


def test_adjacent_runs_without_gap_are_joined():
    # "Hello" in 12pt Helvetica advances 27.34pt; next run starts right there.
    extractor = _extract(b"BT /F1 12 Tf 0 0 Td (Hel) Tj (lo) Tj ET")
    assert spans_to_plain_text(extractor.spans) == "Hello"


def test_fill_color_tracked_on_spans():
    content = b"1 0 0 rg BT /F1 12 Tf 0 0 Td (Red) Tj ET"
    resources = {
        "Font": {"F1": {"Subtype": "Type1", "BaseFont": "Helvetica"}}
    }

    class FakeDoc:
        def resolve(self, obj):
            return obj

    extractor = TextExtractor(FakeDoc(), resources)
    extractor.run(content)
    assert extractor.spans[0].color == 0xFF0000


def test_q_Q_restores_graphics_state():
    content = b"q 2 0 0 2 0 0 cm Q BT /F1 12 Tf 0 0 Td (X) Tj ET"
    resources = {
        "Font": {"F1": {"Subtype": "Type1", "BaseFont": "Helvetica"}}
    }

    class FakeDoc:
        def resolve(self, obj):
            return obj

    extractor = TextExtractor(FakeDoc(), resources)
    extractor.run(content)
    # After Q, CTM should be back to identity, so origin == raw Td position.
    assert extractor.spans[0].origin == (0.0, 0.0)


def test_spans_to_dict_splits_blocks_on_large_paragraph_gap():
    # Two lines 14 units apart (same paragraph, small gap relative to a
    # 12pt line), then a third line 200 units further down (clearly a new
    # paragraph/heading) — should yield two blocks, not one.
    content = (
        b"BT /F1 12 Tf 14 TL 0 500 Td (Para one line one) Tj T* "
        b"(Para one line two) Tj ET "
        b"BT /F1 12 Tf 0 300 Td (Second block) Tj ET"
    )
    resources = {"Font": {"F1": {"Subtype": "Type1", "BaseFont": "Helvetica"}}}

    class FakeDoc:
        def resolve(self, obj):
            return obj

    extractor = TextExtractor(FakeDoc(), resources)
    extractor.run(content)
    result = spans_to_dict(extractor.spans, width=200, height=600)
    assert len(result["blocks"]) == 2
    assert len(result["blocks"][0]["lines"]) == 2
    assert len(result["blocks"][1]["lines"]) == 1


def test_spans_to_dict_groups_multiple_lines():
    content = b"BT /F1 12 Tf 14 TL 0 100 Td (One) Tj T* (Two) Tj ET"
    resources = {
        "Font": {"F1": {"Subtype": "Type1", "BaseFont": "Helvetica"}}
    }

    class FakeDoc:
        def resolve(self, obj):
            return obj

    extractor = TextExtractor(FakeDoc(), resources)
    extractor.run(content)
    result = spans_to_dict(extractor.spans, width=200, height=200)
    assert len(result["blocks"][0]["lines"]) == 2
