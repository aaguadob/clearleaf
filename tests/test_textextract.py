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


def test_tj_array_kerning_adjustment_repositions_but_does_not_insert_text():
    content = b"BT /F1 12 Tf 0 0 Td [(AB) -1000 (CD)] TJ ET"
    resources = {
        "Font": {
            "F1": {
                "Subtype": "Type1",
                "BaseFont": "Helvetica",
                "Encoding": "WinAnsiEncoding",
            }
        }
    }

    class FakeDoc:
        def resolve(self, obj):
            return obj

    extractor = TextExtractor(FakeDoc(), resources)
    extractor.run(content)
    text = spans_to_plain_text(extractor.spans)
    # Two Tj-equivalent runs on the same line: no synthetic space injected
    # for a pure positioning adjustment (TJ numbers are kerning, not word
    # separators) — see _textextract.py module docstring for the rationale.
    assert text == "ABCD"
    assert len(extractor.spans) == 2
    # The second span should start to the right of the first (kerning
    # pulled it left of a full two-char advance, but still forward of it).
    assert extractor.spans[1].origin[0] > extractor.spans[0].origin[0]


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
