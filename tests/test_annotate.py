"""Annotations: created with their own appearance, listed, deleted; read
back by clearleaf and (when installed) PyMuPDF."""

import pytest

import clearleaf as pdf
from clearleaf import PDFError

from .pdfbuild import pages
from .test_edit import everything, reopen


def blank():
    return pdf.open(pages([b"BT /F1 12 Tf 20 150 Td (Some text to mark) Tj ET"]))


def pixel(page, x, y, dpi=72):
    pix = page.get_pixmap(dpi=dpi)
    i = (int(y) * pix.width + int(x)) * 3
    return tuple(pix.samples[i:i + 3])


def pymupdf_annots(doc):
    pymupdf = pytest.importorskip("pymupdf")
    ref = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    return [(a.type[1], a.info["content"]) for a in ref[0].annots()]


def test_each_kind_is_created_drawn_and_read_back():
    doc = blank()
    p = doc[0]
    p.add_highlight_annot([(20, 36, 120, 52)], content="look")
    p.add_underline_annot((20, 36, 120, 52))
    p.add_strikeout_annot((20, 36, 120, 52))
    p.add_squiggly_annot((20, 36, 120, 52))
    p.add_text_annot((150, 10), "a sticky note")
    p.add_freetext_annot((10, 80, 190, 120), "Free text that wraps onto a second line", fontsize=10,
                         fill_color=(0.9, 0.9, 1), border_color=(0, 0, 0))
    p.add_ink_annot([[(10, 150), (50, 170), (90, 150)], [(100, 150), (100, 190)]])
    p.add_rect_annot((120, 130, 190, 190), fill=(0, 1, 0))
    p.add_circle_annot((130, 140, 180, 180), color=(0, 0, 1))
    p.add_line_annot((10, 195), (190, 195))
    types = [a.type for a in p.annots()]
    assert types == ["Highlight", "Underline", "StrikeOut", "Squiggly", "Text", "FreeText", "Ink", "Square",
                     "Circle", "Line"]
    out = reopen(doc)
    assert [a.type for a in out[0].annots()] == types
    assert out[0].annots()[0].info["content"] == "look"
    assert pymupdf_annots(doc) == [(t, c) for t, c in zip(types, ["look", "", "", "", "a sticky note",
                                                                   "Free text that wraps onto a second line",
                                                                   "", "", "", ""])]
    # drawn by clearleaf's own renderer from the appearance streams
    r, g, b = pixel(out[0], 150, 185)  # inside the green rectangle, outside the circle
    assert g > 200 and r < 60 and b < 60
    hl = pixel(out[0], 118, 40)  # highlight over white: yellow
    assert hl[0] > 230 and hl[1] > 200 and hl[2] < 60
    assert "Free text that wraps" in "".join(out[0].get_text().split("\n"))


def test_highlight_multiplies_so_text_stays_readable():
    doc = blank()
    doc[0].add_highlight_annot((15, 34, 200, 54))
    page = reopen(doc)[0]
    pix = page.get_pixmap(dpi=72)
    darkest = min(sum(pix.samples[(y * 200 + x) * 3:(y * 200 + x) * 3 + 3]) for y in range(36, 52) for x in range(20, 120))
    assert darkest < 100  # the black glyphs still show through the yellow


def test_rect_in_page_coordinates():
    doc = blank()
    a = doc[0].add_rect_annot((10, 20, 60, 80))
    assert a.rect == pytest.approx((10, 20, 60, 80))
    assert reopen(doc)[0].annots()[0].rect == pytest.approx((10, 20, 60, 80))


def test_delete_annot():
    doc = blank()
    p = doc[0]
    note = p.add_text_annot((10, 10), "gone")
    p.add_rect_annot((10, 20, 60, 80))
    p.delete_annot(note)
    out = reopen(doc)
    assert [a.type for a in out[0].annots()] == ["Square"]
    assert b"gone" not in everything(doc.tobytes())


def test_freetext_wraps_to_the_box():
    from clearleaf._annotate import wrap_text

    lines = wrap_text("the quick brown fox jumps over the lazy dog", "helv", 10, 80)
    assert len(lines) > 1 and all(pdf.get_text_length(l, "helv", 10) <= 80 for l in lines)
    assert wrap_text("a\nb", "helv", 10, 80) == ["a", "b"]
    assert all(pdf.get_text_length(l, "helv", 10) <= 30 for l in wrap_text("x" * 40, "helv", 10, 30))


def test_errors():
    p = blank()[0]
    with pytest.raises(PDFError):
        p.add_ink_annot([])
    with pytest.raises(PDFError):
        p.add_highlight_annot([])
    with pytest.raises(PDFError):
        p.add_freetext_annot((0, 0, 50, 50), "Привет")
