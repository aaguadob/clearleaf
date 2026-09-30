"""Filling in forms: text, check box, radio group, combo box; values and
appearances read back by clearleaf and (when installed) PyMuPDF."""

import pytest

import clearleaf as pdf
from clearleaf import PDFError

from .pdfbuild import CATALOG, HELV, pdf as build, stream
from .test_edit import everything, reopen


def check_ap(on: bytes) -> bytes:
    return b"<< /N << /%s 20 0 R /Off 21 0 R >> >>" % on


def form() -> pdf.PDFDocument:
    # 1 catalog, 2 pages, 3 page, 4 content, 5 name (text), 6 agree (check box),
    # 7 color (radio group) with kids 8, 9; 10 size (combo); 11 locked (read-only text)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R 6 0 R 7 0 R 10 0 R 11 0 R] "
        b"/DR << /Font << /Helv << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /DA (/Helv 0 Tf 0 g) >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 4 0 R /Resources " + HELV
        + b" /Annots [5 0 R 6 0 R 8 0 R 9 0 R 10 0 R 11 0 R] >>",
        stream(b""),
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /Rect [20 250 200 270] /DA (/Helv 12 Tf 0 g) /P 3 0 R >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Btn /T (agree) /Rect [20 220 34 234] /AS /Off /AP "
        + check_ap(b"Yes") + b" >>",
        b"<< /FT /Btn /Ff 49152 /T (color) /Kids [8 0 R 9 0 R] >>",
        b"<< /Type /Annot /Subtype /Widget /Parent 7 0 R /Rect [20 190 34 204] /AS /Off /AP " + check_ap(b"red") + b" >>",
        b"<< /Type /Annot /Subtype /Widget /Parent 7 0 R /Rect [40 190 54 204] /AS /Off /AP " + check_ap(b"blue") + b" >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Ch /Ff 131072 /T (size) /Opt [[(s) (Small)] [(l) (Large)]] "
        b"/Rect [20 150 120 170] /DA (/Helv 10 Tf 0 g) >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /Ff 1 /T (locked) /V (fixed) /Rect [20 120 120 140] /DA (/Helv 10 Tf 0 g) >>",
    ]
    while len(objs) < 19:
        objs.append(b"null")
    objs += [stream(b"0 0 1 rg 2 2 10 10 re f", b"/Type /XObject /Subtype /Form /BBox [0 0 14 14]"),
             stream(b"", b"/Type /XObject /Subtype /Form /BBox [0 0 14 14]")]
    return pdf.open(build(objs))


def by_name(page):
    return {w.field_name: w for w in page.widgets()}


def test_widgets_are_listed_with_types_and_values():
    w = by_name(form()[0])
    assert {n: (x.field_type, x.field_value) for n, x in w.items()} == {
        "name": ("text", ""), "agree": ("checkbox", False), "color": ("radiobutton", ""),
        "size": ("combobox", ""), "locked": ("text", "fixed")}
    assert w["size"].choice_values == [("s", "Small"), ("l", "Large")]
    assert w["name"].rect == pytest.approx((20, 30, 200, 50))
    assert w["locked"].read_only


def fill(doc):
    page = doc[0]
    radios = [x for x in page.widgets() if x.field_type == "radiobutton"]
    for x in page.widgets():
        if x.field_name == "name":
            x.field_value = "Ana Pérez"
        elif x.field_name == "agree":
            x.field_value = True
        elif x.field_name == "size":
            x.field_value = "Large"
        else:
            continue
        x.update()
    radios[1].field_value = True
    radios[1].update()
    return doc


def test_fill_and_read_back():
    out = reopen(fill(form()))
    w = by_name(out[0])
    assert w["name"].field_value == "Ana Pérez"
    assert w["agree"].field_value is True
    assert w["size"].field_value == "l"
    assert [x.field_value for x in out[0].widgets() if x.field_type == "radiobutton"] == ["", "blue"]
    # the value is drawn (appearance regenerated), so it's in the page text too
    assert "Ana Pérez" in out[0].get_text() and "Large" in out[0].get_text()


def test_pymupdf_sees_the_values():
    pymupdf = pytest.importorskip("pymupdf")
    ref = pymupdf.open(stream=fill(form()).tobytes(), filetype="pdf")
    values = {w.field_name: w.field_value for w in ref[0].widgets()}
    assert values["name"] == "Ana Pérez"
    assert values["agree"] in ("Yes", True)
    assert values["color"] == "blue"
    assert values["size"] in ("l", "Large")


def test_unchecking_and_clearing():
    doc = fill(form())
    w = by_name(doc[0])
    w["agree"].field_value = False
    w["agree"].update()
    w["name"].field_value = ""
    w["name"].update()
    out = reopen(doc)
    assert by_name(out[0])["agree"].field_value is False
    assert by_name(out[0])["name"].field_value == ""


def test_invalid_updates():
    w = by_name(form()[0])
    w["locked"].field_value = "changed"
    with pytest.raises(PDFError, match="read-only"):
        w["locked"].update()
    w["size"].field_value = "Huge"
    with pytest.raises(PDFError, match="not an option"):
        w["size"].update()


def test_deleting_the_page_drops_its_fields():
    doc = fill(form())
    doc.new_page()
    doc.delete_page(0)
    data = everything(doc.tobytes())
    assert b"/T (agree)" in everything(fill(form()).tobytes())  # the check below can see fields
    assert b"Ana" not in data and b"/T (name)" not in data
    reopen(doc)
