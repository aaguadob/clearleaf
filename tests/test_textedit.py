"""Editing a line of text where it is: letters removed and typed in the
content stream, the rest of the line kept in place."""

import pytest

import clearleaf as pdf

from .pdfbuild import HELV, one_page, pages, stream
from .test_edit import reopen


def page_with(content: bytes, font=b"/Helvetica"):
    data = pages([content])
    if font != b"/Helvetica":
        data = data.replace(b"/BaseFont /Helvetica", b"/BaseFont " + font)
    return pdf.open(data)


def line(page, text):
    return next(l for l in page.text_lines() if text in l["text"])


def test_lines_report_text_position_and_style():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Hello world) Tj ET")
    (l,) = doc[0].text_lines()
    assert l["text"] == "Hello world" and l["editable"] and l["size"] == 12
    assert l["font"]["family"] == "sans" and not l["font"]["bold"]
    assert len(l["carets"]) == len("Hello world") + 1
    assert l["carets"][0] == pytest.approx(20) and l["baseline"] == pytest.approx(50)
    assert l["carets"][-1] == pytest.approx(20 + pdf.get_text_length("Hello world", "helv", 12), abs=0.01)


def test_deleting_a_word_keeps_what_comes_before_and_closes_the_gap():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Pay the full amount now) Tj ET")
    page = doc[0]
    before = line(page, "Pay")
    page.edit_text_line(before["bbox"], "Pay the amount now", old=before["text"])
    after = line(reopen(doc)[0], "Pay")
    assert after["text"] == "Pay the amount now"
    assert after["carets"][:8] == pytest.approx(before["carets"][:8], abs=0.01)
    assert after["carets"][-1] == pytest.approx(20 + pdf.get_text_length("Pay the amount now", "helv", 12), abs=0.05)


def test_typing_uses_the_line_font_and_moves_the_rest_along():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td [(Total: 1) -20 (00 EUR)] TJ ET")
    page = doc[0]
    fonts_before = set(page.resources["Font"])
    l = line(page, "Total")
    page.edit_text_line(l["bbox"], "Total: 2500 EUR", old=l["text"])
    out = reopen(doc)[0]
    assert out.get_text().strip() == "Total: 2500 EUR"
    assert set(out.resources["Font"]) == fonts_before  # no new font needed
    new = line(out, "Total")
    # "1" and the kern after it made way for "25"; the rest kept its spacing
    shift = pdf.get_text_length("25", "helv", 12) - pdf.get_text_length("1", "helv", 12) - 20 * 12 / 1000
    assert new["carets"][-1] - l["carets"][-1] == pytest.approx(shift, abs=0.05)


def test_characters_missing_from_a_subset_font_use_the_closest_standard_font():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Tim) Tj ET", font=b"/ABCDEF+Times-Bold")
    page = doc[0]
    l = line(page, "Tim")
    assert l["font"] == {"name": "Times-Bold", "family": "serif", "bold": True, "italic": False}
    page.edit_text_line(l["bbox"], "Timo", old="Tim")
    out = reopen(doc)[0]
    assert out.get_text().strip() == "Timo"
    added = [out._doc.resolve(f) for name, f in out.resources["Font"].items() if name.startswith("CLF")]
    assert [f["BaseFont"] for f in added] == ["Times-Bold"]


def test_other_lines_and_columns_do_not_move():
    doc = page_with(b"BT /F1 10 Tf 10 150 Td (Left column text) Tj 110 0 Td (Right side) Tj ET "
                    b"BT /F1 10 Tf 10 130 Td (Second line) Tj ET")
    page = doc[0]
    right, second = line(page, "Right"), line(page, "Second")
    left = line(page, "Left")
    assert left["text"] == "Left column text"  # the gutter splits the lines
    page.edit_text_line(left["bbox"], "Left text, edited", old=left["text"])
    out = reopen(doc)[0]
    assert line(out, "Right")["carets"] == pytest.approx(right["carets"], abs=0.01)
    assert line(out, "Second")["carets"] == pytest.approx(second["carets"], abs=0.01)


def test_text_after_the_edited_operator_on_the_same_line_moves_with_it():
    # two operators on one line: the second is positioned with its own Td
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Name: ) Tj 40 0 Td (Ana) Tj ET")
    page = doc[0]
    l = line(page, "Name")
    page.edit_text_line(l["bbox"], "Full name: Ana", old=l["text"])
    out = line(reopen(doc)[0], "name")
    assert out["text"] == "Full name: Ana"
    grown = pdf.get_text_length("Full n", "helv", 12) - pdf.get_text_length("N", "helv", 12)
    assert out["carets"][-1] - l["carets"][-1] == pytest.approx(grown, abs=0.05)


def test_clearing_a_line_removes_it():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (Delete me) Tj ET BT /F1 12 Tf 20 100 Td (Keep me) Tj ET")
    page = doc[0]
    l = line(page, "Delete")
    page.edit_text_line(l["bbox"], "", old=l["text"])
    assert reopen(doc)[0].get_text().split() == ["Keep", "me"]


def test_text_inside_a_form_xobject_is_replaced_whole():
    form = b"BT /F1 12 Tf 20 150 Td (Inside a form) Tj ET"
    data = one_page(b"q /X1 Do Q", extra_objects=[stream(form, b"/Type /XObject /Subtype /Form /BBox [0 0 200 200] /Resources " + HELV)])
    data = data.replace(b"/Resources " + HELV + b"  >>", b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                        b"/BaseFont /Helvetica >> >> /XObject << /X1 5 0 R >> >> >>", 1)
    doc = pdf.open(data)
    page = doc[0]
    l = line(page, "Inside")
    assert not l["editable"]
    page.edit_text_line(l["bbox"], "Inside a changed form", old=l["text"])
    assert reopen(doc)[0].get_text().strip() == "Inside a changed form"


def test_right_to_left_text_is_refused():
    doc = page_with(b"BT /F1 12 Tf 20 150 Td (abc) Tj ET")
    page = doc[0]
    l = line(page, "abc")
    import clearleaf._textedit as te

    real = te._Line.__init__

    def rtl(self, glyphs):
        real(self, glyphs)
        self.rtl, self.editable = True, False

    te._Line.__init__ = rtl
    try:
        with pytest.raises(pdf.PDFError, match="right-to-left"):
            page.edit_text_line(l["bbox"], "abd", old="abc")
    finally:
        te._Line.__init__ = real


def test_editor_session_edit_and_undo(tmp_path):
    from clearleaf.editor.session import Session

    path = tmp_path / "a.pdf"
    path.write_bytes(pages([b"BT /F1 12 Tf 20 150 Td (Draft copy) Tj ET"]))
    s = Session(path)
    l = s.text_lines(0)[0]
    s.apply({"op": "edit_text", "page": 0, "rect": l["bbox"], "old": l["text"], "text": "Final copy"})
    assert s.text_lines(0)[0]["text"] == "Final copy"
    s.undo()
    assert s.text_lines(0)[0]["text"] == "Draft copy"
