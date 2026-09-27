import pytest

from clearleaf._objects import PDFName, PDFReference, PDFStream
from clearleaf._parser import ObjectParser


def parse(data: bytes):
    return ObjectParser(data).parse_object()


def test_parse_scalars():
    assert parse(b"42") == 42
    assert parse(b"-3.5") == -3.5
    assert parse(b"true") is True
    assert parse(b"false") is False
    assert parse(b"null") is None
    assert parse(b"(hi)") == b"hi"
    assert parse(b"<48656C6C6F>") == b"Hello"
    assert parse(b"/Name") == PDFName("Name")


def test_parse_array():
    assert parse(b"[1 2.5 /A (b) [3 4] << /K 1 >>]") == [
        1,
        2.5,
        PDFName("A"),
        b"b",
        [3, 4],
        {"K": 1},
    ]


def test_parse_dict():
    d = parse(b"<< /Type /Catalog /Count 3 /Nested << /A true >> >>")
    assert d == {"Type": PDFName("Catalog"), "Count": 3, "Nested": {"A": True}}


def test_parse_indirect_reference():
    assert parse(b"12 0 R") == PDFReference(12, 0)


def test_number_not_confused_with_reference():
    # "5 6" alone (no trailing R) should just be the number 5, leaving
    # "6" as the next token, not consumed into a reference.
    parser = ObjectParser(b"5 6")
    assert parser.parse_object() == 5
    assert parser.parse_object() == 6


def test_reference_inside_array_and_dict():
    arr = parse(b"[1 0 R 2 0 R 3]")
    assert arr == [PDFReference(1, 0), PDFReference(2, 0), 3]
    d = parse(b"<< /Parent 7 0 R /Count 2 >>")
    assert d == {"Parent": PDFReference(7, 0), "Count": 2}


def test_parse_indirect_object_with_stream_and_direct_length():
    data = b"5 0 obj\n<< /Length 11 >>\nstream\nhello world\nendstream\nendobj\n"
    num, gen, obj = ObjectParser(data).parse_indirect_object()
    assert (num, gen) == (5, 0)
    assert isinstance(obj, PDFStream)
    assert obj.dict == {"Length": 11}
    assert obj.raw_data == b"hello world"


def test_stream_length_scan_fallback_when_length_is_indirect_and_unresolved():
    data = (
        b"5 0 obj\n<< /Length 6 0 R >>\nstream\nhello world\nendstream\nendobj\n"
    )
    num, gen, obj = ObjectParser(data).parse_indirect_object()
    assert obj.raw_data == b"hello world"


def test_stream_length_scan_fallback_when_direct_length_is_wrong():
    # A wrong /Length (common in the wild) must not corrupt the data — the
    # parser should fall back to scanning for "endstream".
    data = b"5 0 obj\n<< /Length 999 >>\nstream\nhello world\nendstream\nendobj\n"
    num, gen, obj = ObjectParser(data).parse_indirect_object()
    assert obj.raw_data == b"hello world"


def test_parse_indirect_object_requires_correct_header():
    with pytest.raises(ValueError):
        ObjectParser(b"not an object").parse_indirect_object()
