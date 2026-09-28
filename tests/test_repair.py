"""Damaged files: wrong startxref, wrong xref entry offsets, no xref at all.
Readers are expected to recover by scanning the file for 'N G obj'
(ISO 32000 leaves recovery to the implementation; every mainstream viewer
does this)."""

import re
import zlib

import clearleaf as pdf

from .generate_fixtures import PdfBuilder

CONTENT = b"BT /F1 12 Tf 72 712 Td (Recovered text) Tj ET"


def good_pdf(decoy_in_stream: bool = False) -> bytes:
    b = PdfBuilder()
    content = CONTENT
    if decoy_in_stream:
        # binary-ish stream data that happens to contain an 'N G obj' marker
        content += b"\n% 3 0 obj << /Type /Page >> endobj\n"
    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, zlib.compress(content))
    b.add_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    return bytes(b.buf)


def text_of(data: bytes) -> str:
    doc = pdf.open(data)
    return doc[0].get_text()


def test_intact_file_is_not_marked_repaired():
    doc = pdf.open(good_pdf())
    assert doc[0].get_text() == "Recovered text"
    assert doc.repaired is False


def test_startxref_pointing_a_few_bytes_off():
    data = good_pdf()
    m = re.search(rb"startxref\n(\d+)", data)
    wrong = str(int(m.group(1)) + 9).encode()
    data = data[: m.start(1)] + wrong + data[m.end(1):]
    doc = pdf.open(data)
    assert doc[0].get_text() == "Recovered text"
    assert doc.repaired is True


def test_startxref_missing_entirely():
    data = good_pdf()
    data = data[: data.rfind(b"startxref")]
    assert text_of(data) == "Recovered text"


def test_no_xref_table_and_no_trailer_finds_catalog():
    data = good_pdf()
    data = data[: data.rfind(b"xref")]  # drop xref, trailer, startxref
    assert text_of(data) == "Recovered text"


def test_wrong_entry_offset_falls_back_to_scan():
    data = good_pdf()
    # Point object 5 (the font) at object 1's offset: a *valid* object,
    # but the wrong one -- must be detected by its number, not trusted.
    table = re.search(rb"xref\n0 6\n((?:\d{10} \d{5} [nf] \n){6})", data)
    entries = table.group(1).split(b"\n")
    entries[5] = entries[1]
    data = data[: table.start(1)] + b"\n".join(entries) + data[table.end(1):]
    doc = pdf.open(data)
    assert doc.get_object(5)["BaseFont"] == "Helvetica"
    assert doc[0].get_text() == "Recovered text"


def test_scan_ignores_obj_markers_inside_stream_data():
    data = good_pdf(decoy_in_stream=True)
    data = data[: data.rfind(b"xref")]
    doc = pdf.open(data)
    assert doc.get_object(3)["Type"] == "Page"  # the real page, not the decoy
    assert doc[0].get_text() == "Recovered text"


def test_incremental_update_later_object_wins_in_scan():
    data = good_pdf()
    data = data[: data.rfind(b"xref")]
    # an appended newer revision of object 5
    data += b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n"
    doc = pdf.open(data)
    assert doc.get_object(5)["BaseFont"] == "Courier"


def test_lost_page_tree_falls_back_to_surviving_page_objects():
    data = good_pdf()
    data = data[: data.rfind(b"xref")]
    # Destroy the page tree: object 2 is renumbered away, so /Pages 2 0 R dangles.
    data = data.replace(b"2 0 obj\n<< /Type /Pages", b"99 0 obj\n<< /Type /Junk")
    doc = pdf.open(data)
    assert doc.repaired and len(doc) == 1
    assert doc[0].get_text() == "Recovered text"


def test_intact_empty_page_tree_is_not_second_guessed():
    b = PdfBuilder()
    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [] /Count 0 >>")
    b.add_object(3, b"<< /Type /Page /Parent 2 0 R >>")  # orphan, not in the tree
    b.write_classic_xref_and_trailer(size=4, root_num=1)
    doc = pdf.open(bytes(b.buf))
    assert not doc.repaired and len(doc) == 0
