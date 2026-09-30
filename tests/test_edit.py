"""Editing: page operations, new content, saving. Every saved file is read
back by clearleaf and, when installed, by PyMuPDF as an independent reader."""

import zlib

import pytest

import clearleaf as pdf
from clearleaf import PDFError

from .pdfbuild import CATALOG, flate, pages, pdf as build, stream


def text_page(label: str) -> bytes:
    return b"BT /F1 12 Tf 20 150 Td (%s) Tj ET" % label.encode()


def five_pages():
    return pdf.open(pages([text_page(f"page {i}") for i in range(5)]))


def texts(doc):
    return [p.get_text().strip() for p in doc]


def reopen(doc):
    """Save, then read back with clearleaf and (when available) PyMuPDF."""
    data = doc.tobytes()
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        ref = pymupdf.open(stream=data, filetype="pdf")
        assert ref.page_count == len(doc)
        assert not ref.is_repaired, "PyMuPDF had to repair the file"
        assert [p.get_text().strip() for p in ref] == texts(pdf.open(data))
    return pdf.open(data)


def test_save_unchanged_round_trips():
    assert texts(reopen(five_pages())) == [f"page {i}" for i in range(5)]


def test_delete_move_select():
    doc = five_pages()
    doc.delete_page(1)
    assert texts(doc) == ["page 0", "page 2", "page 3", "page 4"]
    doc.move_page(3, 0)  # page 4 to the front
    assert texts(doc) == ["page 4", "page 0", "page 2", "page 3"]
    doc.move_page(0)  # back to the end
    assert texts(doc) == ["page 0", "page 2", "page 3", "page 4"]
    doc.delete_pages([0, -1])
    assert texts(reopen(doc)) == ["page 2", "page 3"]
    doc = five_pages()
    doc.select([4, 0, 0])
    out = reopen(doc)
    assert texts(out) == ["page 4", "page 0", "page 0"] and [p.number for p in doc] == [0, 1, 2]


def everything(data: bytes) -> bytes:
    """Every object in the file, serialised, with streams decoded (object
    streams included): what a search of the file's content can find."""
    from clearleaf._objects import PDFStream
    from clearleaf._writer import serialize_value

    doc = pdf.open(data)
    out = [data]
    for num in list(doc._xref):
        obj = doc.get_object(num)
        if isinstance(obj, PDFStream):
            out += [serialize_value(obj.dict), obj.get_data(resolver=doc.resolve)]
        elif obj is not None:
            out.append(serialize_value(obj))
    return b"\n".join(out)


def test_deleted_page_content_is_not_in_the_file():
    doc = pdf.open(pages([b"BT /F1 12 Tf 20 150 Td (keep) Tj ET", b"BT /F1 12 Tf 20 150 Td (SECRET) Tj ET"]))
    doc.delete_page(1)
    assert b"keep" in everything(doc.tobytes())
    assert b"SECRET" not in everything(doc.tobytes())


def test_rotation_and_inherited_attributes_survive():
    # MediaBox and Rotate inherited from the page tree
    data = build([CATALOG, b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 400] /Rotate 90 >>",
                  b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>", stream(b"")])
    doc = pdf.open(data)
    out = reopen(doc)
    assert out[0].mediabox == (0, 0, 300, 400) and out[0].rotation == 90
    doc[0].set_rotation(-90)
    assert reopen(doc)[0].rotation == 270
    with pytest.raises(PDFError):
        doc[0].set_rotation(45)


def test_insert_pdf_and_new_page():
    doc = five_pages()
    other = pdf.open(pages([text_page("other A"), text_page("other B")]))
    doc.insert_pdf(other, start_at=1)
    doc.insert_pdf(other.tobytes(), from_page=1, to_page=1)
    doc.new_page(0, width=200, height=100)
    out = reopen(doc)
    assert texts(out) == ["", "page 0", "other A", "other B", "page 1", "page 2", "page 3", "page 4", "other B"]
    assert out[0].mediabox == (0, 0, 200, 100)


def test_outline_links_to_a_deleted_page_become_null():
    # a long outline chain (deeper than the recursion limit if copied recursively)
    n = 3000
    objs = [b"<< /Type /Catalog /Pages 2 0 R /Outlines 5 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>", stream(b""),
            b"<< /Type /Outlines /First 6 0 R /Last %d 0 R /Count %d >>" % (5 + n, n)]
    for i in range(n):
        nxt = b"/Next %d 0 R" % (7 + i) if i < n - 1 else b""
        objs.append(b"<< /Title (item %d) /Parent 5 0 R /Dest [3 0 R /Fit] %s >>" % (i, nxt))
    doc = pdf.open(build(objs))
    doc.new_page()
    doc.delete_page(0)
    assert b"/Dest [null /Fit]" in everything(doc.tobytes())
    out = reopen(doc)
    titles = [o for o in (out.get_object(num) for num in list(out._xref)) if isinstance(o, dict) and "Title" in o]
    assert len(titles) == n
    first = out.resolve(out.resolve(out.root["Outlines"])["First"])
    assert first["Title"] == b"item 0" and first["Dest"][0] is None


def test_insert_text_and_measure():
    doc = five_pages()
    page = doc[0]
    assert page.rect == (0, 0, 200, 200)
    assert page.insert_text((20, 100), "Hello, café\nsecond line", fontsize=14, color=(1, 0, 0)) == 2
    out = reopen(doc)
    assert out[0].get_text().split("\n")[:3] == ["page 0", "Hello, café", "second line"]
    spans = [s for b in out[0].get_text("dict")["blocks"] for l in b["lines"] for s in l["spans"]]
    hello = next(s for s in spans if s["text"].startswith("Hello"))
    assert abs(hello["origin"][0] - 20) < 0.5 and abs(hello["origin"][1] - 100) < 0.5
    assert hello["size"] == pytest.approx(14) and hello["color"] == 0xFF0000
    assert pdf.get_text_length("Hello", "helv", 10) == pytest.approx(22.78, abs=0.01)
    with pytest.raises(PDFError, match="built-in fonts"):
        page.insert_text((10, 10), "Привет")


def _png(width, height, ctype, rows):
    import struct

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    raw = b"".join(b"\0" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("ctype,pixel", [(2, b"\xff\x00\x00"), (6, b"\xff\x00\x00\xff"), (0, b"\x40")])
def test_insert_png(ctype, pixel):
    doc = pdf.open(pages([b""]))
    png = _png(4, 4, ctype, [pixel * 4] * 4)
    doc[0].insert_image((50, 50, 150, 150), stream=png)
    out = reopen(doc)
    pix = out[0].get_pixmap(dpi=72)
    r, g, b = pix.samples[(100 * 200 + 100) * 3:(100 * 200 + 100) * 3 + 3]
    assert (r, g, b) == ((255, 0, 0) if ctype != 0 else (0x40, 0x40, 0x40))
    corner = pix.samples[(10 * 200 + 10) * 3:(10 * 200 + 10) * 3 + 3]
    assert tuple(corner) == (255, 255, 255)
    assert len(out[0].get_images()) == 1


def test_insert_jpeg_keeps_proportion():
    import io

    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (0, 0, 255)).save(buf, "JPEG")
    doc = pdf.open(pages([b""]))
    doc[0].insert_image((0, 0, 200, 200), stream=buf.getvalue())
    out = reopen(doc)
    assert len(out[0].get_images()) == 1
    pix = out[0].get_pixmap(dpi=72)
    at = lambda x, y: tuple(pix.samples[(y * 200 + x) * 3:(y * 200 + x) * 3 + 3])
    assert at(100, 100)[2] > 200 and at(100, 100)[0] < 60  # blue in the middle
    assert at(100, 20) == (255, 255, 255)  # 2:1 image in a square: bands above and below


def test_bad_images_are_pdf_errors():
    doc = pdf.open(pages([b""]))
    with pytest.raises(PDFError):
        doc[0].insert_image((0, 0, 10, 10), stream=b"GIF89a....")


def test_encrypted_document_saves_decrypted():
    from pathlib import Path

    doc = pdf.open(Path(__file__).parent / "fixtures" / "encrypted" / "aes_128.pdf")
    with pytest.raises(ValueError, match="encrypted"):
        doc.tobytes()
    assert doc.authenticate("user")
    before = texts(doc)
    out = reopen(doc)
    assert not out.is_encrypted and texts(out) == before


def test_is_dirty():
    doc = five_pages()
    assert not doc.is_dirty
    doc.delete_page(0)
    assert doc.is_dirty
