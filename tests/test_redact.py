"""Redaction removes the content from the file, not just from view."""

import zlib

import pytest

import clearleaf as pdf

from .pdfbuild import CATALOG, HELV, pages, pdf as build, stream
from .test_edit import everything, reopen


def page_with(content: bytes, **kw):
    return pdf.open(pages([content]))


def words(page):
    return page.get_text().split()


def span_box(page, text):
    for b in page.get_text("dict")["blocks"]:
        for line in b["lines"]:
            for s in line["spans"]:
                if text in s["text"]:
                    return s["bbox"]
    raise AssertionError(text)


def test_text_under_the_mark_is_gone_and_the_rest_stays_in_place():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Name: John Smith, born 1980) Tj ET")
    page = doc[0]
    before = span_box(page, "Name")
    # "John Smith" starts after "Name: " (Helvetica widths)
    start = 20 + pdf.get_text_length("Name: ", "helv", 12)
    end = start + pdf.get_text_length("John Smith", "helv", 12)
    page.add_redact_annot((start - 0.5, 40, end + 0.5, 56))
    assert page.apply_redactions() == 1
    out = reopen(doc)
    assert out[0].get_text().split() == ["Name:", ",", "born", "1980"] or "John" not in out[0].get_text()
    assert "John" not in out[0].get_text() and "Smith" not in out[0].get_text()
    assert "born 1980" in out[0].get_text()
    data = everything(doc.tobytes())
    assert b"John" not in data and b"Smith" not in data
    # "born" keeps its position
    x_comma = 20 + pdf.get_text_length("Name: John Smith", "helv", 12)
    assert span_box(out[0], "born")[0] == pytest.approx(x_comma, abs=0.5)  # ", born 1980" hasn't moved
    assert span_box(out[0], "Name")[0] == pytest.approx(before[0])
    # the black box is drawn
    pix = out[0].get_pixmap(dpi=72)
    i = (48 * 200 + int(start) + 5) * 3
    assert tuple(pix.samples[i:i + 3]) == (0, 0, 0)


@pytest.mark.parametrize("show", [
    b"[(Top) -250 (secret) -250 (end)] TJ",
    b"(ignored) Tj 0 -20 Td (secret) '",
    b"(ignored) Tj 2 1 (secret) \"",
])
def test_every_text_operator(show):
    doc = page_with(b"BT /F1 12 Tf 20 150 Td 14 TL " + show + b" ET")
    box = span_box(doc[0], "secret")
    doc[0].add_redact_annot((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1), fill=None)
    doc[0].apply_redactions()
    out = reopen(doc)
    assert "secret" not in out[0].get_text()
    assert b"secret" not in everything(doc.tobytes())
    assert out[0].get_text().strip() != ""


def test_text_inside_a_form_xobject_on_this_page_only():
    form = stream(b"BT /F1 12 Tf 0 0 Td (in the form) Tj ET",
                  b"/Type /XObject /Subtype /Form /BBox [0 0 200 50] /Resources " + HELV)
    objs = [CATALOG, b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /XObject << /Fm 5 0 R >> >> >>",
            stream(b"q 1 0 0 1 20 100 cm /Fm Do Q"), form,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 7 0 R /Resources << /XObject << /Fm 5 0 R >> >> >>",
            stream(b"q 1 0 0 1 20 100 cm /Fm Do Q")]
    doc = pdf.open(build(objs))
    box = span_box(doc[0], "form")
    doc[0].add_redact_annot((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1))
    doc[0].apply_redactions()
    out = reopen(doc)
    assert "form" not in out[0].get_text()
    assert "in the form" in out[1].get_text()  # the other page still uses the original


def test_image_pixels_are_overwritten():
    w = h = 20
    red = zlib.compress(b"\xff\x00\x00" * (w * h))
    objs = [CATALOG, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /XObject << /Im 5 0 R >> >> >>",
            stream(b"q 200 0 0 200 0 0 cm /Im Do Q"),
            stream(red, b"/Type /XObject /Subtype /Image /Width 20 /Height 20 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode")]
    doc = pdf.open(build(objs))
    doc[0].add_redact_annot((0, 0, 100, 100), fill=(0, 0, 1))  # top-left quarter
    doc[0].apply_redactions()
    out = reopen(doc)
    img = [o for o in (out.get_object(n) for n in list(out._xref)) if hasattr(o, "dict") and o.dict.get("Subtype") == "Image"]
    assert len(img) == 1
    samples = img[0].get_data(resolver=out.resolve)
    at = lambda x, y: samples[(y * w + x) * 3:(y * w + x) * 3 + 3]
    assert at(2, 2) == b"\x00\x00\xff" and at(15, 15) == b"\xff\x00\x00" and at(15, 2) == b"\xff\x00\x00"


def test_inline_image_touching_the_mark_is_removed():
    doc = page_with(b"q 50 0 0 50 10 10 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\x00\x00\x00 EI Q "
                    b"BT /F1 12 Tf 100 150 Td (kept) Tj ET")
    doc[0].add_redact_annot((0, 150, 70, 200))  # page coords: bottom-left area
    doc[0].apply_redactions()
    data = everything(doc.tobytes())
    assert b"BI" not in data.split(b"kept")[0][-200:] or b"ID \x00\x00\x00\x00" not in data
    assert "kept" in reopen(doc)[0].get_text()


def test_actual_text_over_removed_glyphs_is_stripped():
    doc = page_with(b"/Span << /ActualText (hidden secret) >> BDC BT /F1 12 Tf 20 150 Td (xxxxx) Tj ET EMC "
                    b"BT /F1 12 Tf 20 100 Td (visible) Tj ET")
    box = span_box(doc[0], "hidden secret")
    doc[0].add_redact_annot((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1))
    doc[0].apply_redactions()
    assert b"secret" not in everything(doc.tobytes())
    assert "visible" in reopen(doc)[0].get_text()


def test_overlapping_annotations_go_and_marks_are_removed():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (text) Tj ET")
    page = doc[0]
    page.add_text_annot((30, 45), "note about the secret")
    page.add_text_annot((150, 150), "far away")
    page.add_redact_annot((20, 40, 80, 60))
    page.apply_redactions()
    assert [a.info["content"] for a in reopen(doc)[0].annots()] == ["far away"]
    assert b"about the secret" not in everything(doc.tobytes())


def test_replace_text():
    doc = page_with(b"BT /F1 12 Tf 1 0 0 rg 20 150 Td (Total: 100 EUR) Tj ET")
    page = doc[0]
    box = span_box(page, "Total")
    page.replace_text(box, "Total: 250 EUR")
    out = reopen(doc)
    assert out[0].get_text().strip() == "Total: 250 EUR"
    span = out[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    assert span["origin"] == pytest.approx([20, 50], abs=0.01) and span["size"] == 12 and span["color"] == 0xFF0000
    assert b"100 EUR" not in everything(doc.tobytes())


def test_no_marks_changes_nothing():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (text) Tj ET")
    assert doc[0].apply_redactions() == 0
