"""Annotation appearances, regenerated form fields, /ActualText and clip
paths -- what a viewer shows is what get_text() and get_pixmap() see.
Expected outputs were checked against PyMuPDF on the same files."""

import clearleaf as pdf

HELV = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def build(content: bytes, annots=(), catalog_extra=b"", page_extra=b"", extra_objects=()) -> bytes:
    """A one-page PDF. ``annots``: (annotation dict, appearance content or
    None) pairs; objects are numbered 1 catalog, 2 pages, 3 page, 4
    contents, 5 Helvetica, then two per annotation, then extra_objects."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        None,
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        HELV,
    ]
    refs = []
    for annot, appearance in annots:
        num = len(objects) + 1
        refs.append(b"%d 0 R" % num)
        if appearance is not None:
            annot = annot.replace(b">>", b" /AP << /N %d 0 R >> >>" % (num + 1), 1) if b"/AP" not in annot else annot
        objects.append(annot)
        ap = appearance or b""
        objects.append(b"<< /Type /XObject /Subtype /Form /BBox [0 0 200 20] "
                       b"/Resources << /Font << /F1 5 0 R >> >> /Length %d >>\nstream\n" % len(ap) + ap + b"\nendstream")
    objects.extend(extra_objects)
    objects[2] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                  b"/Resources << /Font << /F1 5 0 R >> >> /Annots [" + b" ".join(refs) + b"] " + page_extra + b">>")
    out, offsets = b"%PDF-1.7\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


PAGE_TEXT = b"BT /F1 12 Tf 72 700 Td (PageText) Tj ET"
AP_TEXT = lambda s: b"BT /F1 12 Tf 2 5 Td (" + s + b") Tj ET"


def text(data: bytes) -> str:
    return pdf.open(data)[0].get_text()


def test_annotation_appearance_text_follows_page_text():
    for subtype in (b"FreeText", b"Stamp", b"Square"):
        annot = b"<< /Type /Annot /Subtype /" + subtype + b" /Rect [72 600 272 620] /F 4 >>"
        assert text(build(PAGE_TEXT, [(annot, AP_TEXT(b"InAnnot"))])) == "PageText\nInAnnot"


def test_hidden_and_popup_annotations_are_not_shown():
    hidden = b"<< /Type /Annot /Subtype /FreeText /Rect [72 600 272 620] /F 2 >>"
    popup = b"<< /Type /Annot /Subtype /Popup /Rect [72 600 272 620] /F 0 >>"
    assert text(build(PAGE_TEXT, [(hidden, AP_TEXT(b"Hidden")), (popup, AP_TEXT(b"Popup"))])) == "PageText"


def test_appearance_is_placed_in_the_annotation_rect():
    annot = b"<< /Type /Annot /Subtype /Square /Rect [100 100 300 120] /F 4 >>"
    red_box = b"1 0 0 rg 0 0 200 20 re f"
    pix = pdf.open(build(b"", [(annot, red_box)]))[0].get_pixmap(dpi=72)
    at = lambda x, y: tuple(pix.samples[(y * pix.width + x) * 3 : (y * pix.width + x) * 3 + 3])
    assert at(200, 792 - 110) == (255, 0, 0)
    assert at(200, 792 - 130) == (255, 255, 255)


NEED_APPEARANCES = b"/AcroForm << /Fields [] /NeedAppearances true /DR << /Font << /Helv 5 0 R >> >> >> "


def widget(fields: bytes) -> bytes:
    return b"<< /Type /Annot /Subtype /Widget /Rect [72 600 272 620] /F 4 " + fields + b">>"


def test_need_appearances_rebuilds_fields_from_their_values():
    cases = {
        b"/FT /Tx /T (a) /V (Typed value) /DA (/Helv 12 Tf 0 g)": "Typed value",
        b"/FT /Tx /T (a) /V <FEFF00C9006C00E8006E0065> /DA (/Helv 10 Tf 0 g)": "Élène",
        b"/FT /Tx /T (a) /V () /DA (/Helv 12 Tf 0 g)": "",
        b"/FT /Ch /Ff 131072 /T (d) /V (Choice) /Opt [(Choice) (Other)] /DA (/Helv 12 Tf 0 g)": "Choice",
        b"/FT /Btn /Ff 65536 /T (b) /MK << /CA (Caption) >> /DA (/Helv 12 Tf 0 g)": "Caption",
        b"/FT /Btn /Ff 65536 /T (b)": "",  # push button without a caption
        b"/FT /Btn /T (c) /V /Off /AS /Off /AP << /N << /Off 99 0 R >> >>": "",  # unchecked box
    }
    for fields, expected in cases.items():
        data = build(PAGE_TEXT, [(widget(fields), AP_TEXT(b"StaleAppearance"))], catalog_extra=NEED_APPEARANCES)
        assert text(data) == ("PageText\n" + expected if expected else "PageText"), fields


def test_multiline_and_list_box_fields():
    multi = widget(b"/FT /Tx /Ff 4096 /T (a) /V (Line one\\rLine two) /DA (/Helv 10 Tf 0 g)").replace(
        b"[72 600 272 620]", b"[72 560 272 620]")
    assert text(build(PAGE_TEXT, [(multi, None)], catalog_extra=NEED_APPEARANCES)) == "PageText\nLine one\nLine two"
    listbox = widget(b"/FT /Ch /T (d) /V (B) /Opt [(A) (B) (C)] /DA (/Helv 10 Tf 0 g)").replace(
        b"[72 600 272 620]", b"[72 560 272 620]")
    assert text(build(PAGE_TEXT, [(listbox, None)], catalog_extra=NEED_APPEARANCES)) == "PageText\nA\nB\nC"


def test_without_need_appearances_the_stored_appearance_is_used():
    field = widget(b"/FT /Tx /T (a) /V (Value) /DA (/Helv 12 Tf 0 g)")
    assert text(build(PAGE_TEXT, [(field, AP_TEXT(b"Stored"))])) == "PageText\nStored"
    # ...unless there is none, then the value is shown
    assert text(build(PAGE_TEXT, [(field, None)])) == "PageText\nValue"


def test_inline_actual_text_replaces_what_is_drawn():
    content = b"BT /F1 12 Tf 72 700 Td /Span << /ActualText (fi) >> BDC (X) Tj EMC (nd) Tj ET"
    assert text(build(content)) == "find"


def _tagged(content: bytes, actual_text: bytes) -> bytes:
    """A page whose MCID 0 belongs to a Span with /ActualText (found via the
    page's /StructParents entry in the /ParentTree, as in real files)."""
    image = (b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 "
             b"/ColorSpace /DeviceGray /Length 1 >>\nstream\n\x00\nendstream")
    data = build(content, catalog_extra=b"/StructTreeRoot 7 0 R /MarkInfo << /Marked true >> ",
                 page_extra=b"/StructParents 0",
                 extra_objects=[image,
                                b"<< /Type /StructTreeRoot /K 8 0 R /ParentTree << /Nums [0 [8 0 R]] >> >>",
                                b"<< /Type /StructElem /S /Span /P 7 0 R /Pg 3 0 R /K 0 /ActualText " + actual_text + b" >>"])
    return data.replace(b"/Resources << /Font << /F1 5 0 R >> >>",
                        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >>")


def test_structure_tree_actual_text_replaces_marked_content():
    content = b"/P << /MCID 0 >> BDC BT /F1 12 Tf 72 700 Td (Z) Tj ET EMC BT /F1 12 Tf 84 700 Td (Item) Tj ET"
    assert text(_tagged(content, b"<FEFF2022>")) == "\u2022 Item"


def test_structure_tree_actual_text_for_an_image():
    # A bullet drawn as a picture still reads as a bullet.
    content = b"/P << /MCID 0 >> BDC q 9 0 0 12 72 698 cm /Im1 Do Q EMC BT /F1 12 Tf 84 700 Td (Item) Tj ET"
    got = text(_tagged(content, b"<FEFF2022>"))
    assert got.split() == ["\u2022", "Item"]


def test_text_outside_the_clip_path_is_dropped():
    content = (b"q 0 0 3 6 re W n BT /F1 12 Tf 4 1 Td (Invisible) Tj ET Q "
               b"BT /F1 12 Tf 72 700 Td (Visible) Tj ET")
    assert text(build(content)) == "Visible"
    # glyph by glyph: "P" (10-18) and "a" (18-24.7) touch the clip, the rest don't
    content = b"q 0 0 20 20 re W n BT /F1 12 Tf 10 5 Td (Partly) Tj ET Q"
    assert text(build(content)) == "Pa"
