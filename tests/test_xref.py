import os

import pytest

from clearleaf._objects import PDFReference
from clearleaf._xref import find_startxref, load_xref_chain, parse_xref_section

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def test_find_startxref():
    buf = b"garbage\nstartxref\n1234\n%%EOF"
    assert find_startxref(buf) == 1234


def test_find_startxref_missing_raises():
    with pytest.raises(ValueError):
        find_startxref(b"no marker here")


def test_parse_classic_xref_section_from_fixture():
    buf = _read("classic_simple.pdf")
    start = find_startxref(buf)
    entries, trailer, prev, xrefstm = parse_xref_section(buf, start)
    assert prev is None
    assert xrefstm is None
    assert trailer["Root"] == PDFReference(1, 0)
    assert set(entries.keys()) == {1, 2, 3, 4, 5}
    for num, (kind, _off, _gen) in entries.items():
        assert kind == "n"


def test_parse_xref_stream_section_from_fixture():
    buf = _read("xref_stream.pdf")
    start = find_startxref(buf)
    entries, trailer, prev, xrefstm = parse_xref_section(buf, start)
    assert prev is None
    assert trailer["Root"] == PDFReference(1, 0)
    # Objects 1 and 5 were packed into the object stream (object 6).
    assert entries[1] == ("c", 6, 0)
    assert entries[5] == ("c", 6, 1)
    assert entries[2][0] == "n"
    assert entries[6][0] == "n"


def test_prev_chain_incremental_update_newest_wins():
    base = bytearray()
    base += b"%PDF-1.4\n"
    off1 = len(base)
    base += b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    off2 = len(base)
    base += b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    off3 = len(base)
    base += b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n"
    xref1_offset = len(base)
    base += b"xref\n0 4\n"
    base += b"0000000000 65535 f \n"
    base += f"{off1:010d} 00000 n \n".encode()
    base += f"{off2:010d} 00000 n \n".encode()
    base += f"{off3:010d} 00000 n \n".encode()
    base += b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n"
    base += f"{xref1_offset}\n".encode()
    base += b"%%EOF\n"

    update = bytearray(base)
    off3b = len(update)
    update += (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>\nendobj\n"
    )
    xref2_offset = len(update)
    update += b"xref\n"
    update += b"3 1\n"
    update += f"{off3b:010d} 00000 n \n".encode()
    update += b"trailer\n"
    update += f"<< /Size 4 /Root 1 0 R /Prev {xref1_offset} >>\n".encode()
    update += b"startxref\n"
    update += f"{xref2_offset}\n".encode()
    update += b"%%EOF\n"

    entries, trailer = load_xref_chain(bytes(update), xref2_offset)
    assert entries[1] == ("n", off1, 0)
    assert entries[2] == ("n", off2, 0)
    assert entries[3] == ("n", off3b, 0)  # the update's revision wins
    assert trailer["Root"] == PDFReference(1, 0)


def test_load_xref_chain_from_fixtures_matches_direct_section():
    for name in ["classic_simple.pdf", "xref_stream.pdf"]:
        buf = _read(name)
        start = find_startxref(buf)
        chained_entries, chained_trailer = load_xref_chain(buf, start)
        direct_entries, direct_trailer, _prev, _xrefstm = parse_xref_section(buf, start)
        assert chained_entries == direct_entries
        assert chained_trailer["Root"] == direct_trailer["Root"]
