import os

import clearleaf as pdf
from clearleaf.document import PDFDocument

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def path(name):
    return os.path.join(FIXTURES, name)


def test_open_classic_simple():
    doc = pdf.open(path("classic_simple.pdf"))
    assert len(doc) == 1
    page = doc[0]
    assert page.mediabox == (0.0, 0.0, 612.0, 792.0)
    assert page.rotation == 0
    assert page.get_contents_raw() == (
        b"q 1 0 0 1 0 0 cm BT /F1 12 Tf 72 712 Td (Hello clearleaf) Tj ET Q"
    )
    doc.close()


def test_open_accepts_bytes():
    with open(path("classic_simple.pdf"), "rb") as f:
        data = f.read()
    doc = pdf.open(data)
    assert len(doc) == 1


def test_two_page_inherited_attributes():
    doc = pdf.open(path("two_page_inherited.pdf"))
    assert len(doc) == 2
    page0, page1 = doc[0], doc[1]
    # page0 has no own MediaBox -> inherits from the Pages node.
    assert page0.mediabox == (0.0, 0.0, 300.0, 400.0)
    assert page0.rotation == 0
    # page1 overrides MediaBox and sets its own Rotate.
    assert page1.mediabox == (0.0, 0.0, 100.0, 200.0)
    assert page1.rotation == 90
    doc.close()


def test_xref_stream_and_object_streams_resolve_correctly():
    doc = pdf.open(path("xref_stream.pdf"))
    assert len(doc) == 1
    assert doc.root["Type"] == "Catalog"
    page = doc[0]
    assert page.mediabox == (0.0, 0.0, 612.0, 792.0)
    assert page.get_contents_raw() == b"q Q"
    # Font (object 5) lives inside the object stream too.
    resources = doc.resolve(page.dict.get("Resources"))
    font = doc.resolve(doc.resolve(resources["Font"])["F1"])
    assert font["BaseFont"] == "Helvetica"
    doc.close()


def test_lzw_content_stream_decodes():
    doc = pdf.open(path("lzw_content.pdf"))
    page = doc[0]
    assert page.get_contents_raw() == (
        b"q 1 0 0 1 0 0 cm BT /F1 10 Tf 10 10 Td (LZW test) Tj ET Q"
    )
    doc.close()


def test_object_cache_returns_same_instance():
    doc = pdf.open(path("classic_simple.pdf"))
    a = doc.get_object(3)
    b = doc.get_object(3)
    assert a is b
    doc.close()


def test_get_object_missing_returns_none():
    doc = pdf.open(path("classic_simple.pdf"))
    assert doc.get_object(9999) is None
    doc.close()


def test_get_images_on_page_with_no_images_is_empty():
    doc = pdf.open(path("classic_simple.pdf"))
    page = doc[0]
    assert page.get_images() == []
    doc.close()


def test_context_manager_closes():
    with pdf.open(path("classic_simple.pdf")) as doc:
        assert len(doc) == 1
    assert doc._cache == {}


def test_pdfdocument_open_is_same_as_module_open():
    doc = PDFDocument.open(path("classic_simple.pdf"))
    assert isinstance(doc, PDFDocument)
    doc.close()


def test_cropbox_defaults_to_mediabox():
    doc = pdf.open(path("classic_simple.pdf"))
    assert doc[0].cropbox == doc[0].mediabox
    doc.close()


def test_cropbox_is_read_and_clipped_to_mediabox():
    doc = pdf.open(path("cropbox.pdf"))
    assert doc[0].mediabox == (0.0, 0.0, 400.0, 400.0)  # inherited
    assert doc[0].cropbox == (100.0, 100.0, 300.0, 250.0)
    assert doc[1].cropbox == (0.0, 0.0, 400.0, 400.0)  # overhang clipped
    doc.close()


def test_pixmap_covers_only_the_cropbox():
    doc = pdf.open(path("cropbox.pdf"))
    pix = doc[0].get_pixmap(dpi=72)
    assert (pix.width, pix.height) == (200, 150)

    def px(x, y):
        i = (y * pix.width + x) * 3
        return tuple(pix.samples[i : i + 3])

    assert px(10, 140) == (255, 0, 0)  # red square, crop's bottom-left
    assert px(190, 10) == (255, 255, 255)  # empty top-right
    doc.close()


def test_text_dict_page_size_is_the_cropbox():
    doc = pdf.open(path("cropbox.pdf"))
    d = doc[0].get_text("dict")
    assert (d["width"], d["height"]) == (200.0, 150.0)
    doc.close()


def test_get_text_ignores_text_outside_the_cropbox():
    import zlib

    from .generate_fixtures import PdfBuilder

    b = PdfBuilder()
    content = b"BT /F1 12 Tf 150 150 Td (inside) Tj ET BT /F1 12 Tf 10 10 Td (outside) Tj ET"
    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /CropBox [100 100 300 300] "
                    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, zlib.compress(content))
    b.add_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    doc = pdf.open(bytes(b.buf))
    assert doc[0].get_text() == "inside"
    assert [s["text"] for blk in doc[0].get_text("dict")["blocks"] for ln in blk["lines"] for s in ln["spans"]] == ["inside"]
