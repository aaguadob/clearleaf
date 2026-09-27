from clearleaf._content import iter_operations
from clearleaf._objects import PDFName


def ops(data: bytes):
    return list(iter_operations(data))


def test_simple_operator_grouping():
    result = ops(b"1 0 0 1 72 712 cm")
    assert result == [("cm", [1, 0, 0, 1, 72, 712])]


def test_multiple_operations_in_sequence():
    result = ops(b"q 1 0 0 1 10 20 cm Q")
    assert result == [
        ("q", []),
        ("cm", [1, 0, 0, 1, 10, 20]),
        ("Q", []),
    ]


def test_text_showing_operators():
    result = ops(b"BT /F1 12 Tf (Hello) Tj ET")
    assert result == [
        ("BT", []),
        ("Tf", [PDFName("F1"), 12]),
        ("Tj", [b"Hello"]),
        ("ET", []),
    ]


def test_tj_array_operand():
    result = ops(b"[(A) -250 (B)] TJ")
    assert result == [("TJ", [[b"A", -250, b"B"]])]


def test_dict_operand_for_bdc():
    result = ops(b"/OC << /Foo /Bar >> BDC")
    assert result == [("BDC", [PDFName("OC"), {"Foo": PDFName("Bar")}])]


def test_boolean_and_null_operands():
    result = ops(b"true false null XX")
    assert result == [("XX", [True, False, None])]


def test_inline_image_with_explicit_length():
    data = b"BI /W 2 /H 1 /BPC 8 /L 6 ID \x01\x02\x03\x04\x05\x06 EI"
    result = ops(data)
    assert len(result) == 1
    op, (img_dict, raw) = result[0]
    assert op == "INLINE_IMAGE"
    assert img_dict["W"] == 2
    assert img_dict["H"] == 1
    assert raw == b"\x01\x02\x03\x04\x05\x06"


def test_inline_image_without_length_scans_for_ei():
    data = b"BI /W 3 /H 1 ID abc EI"
    result = ops(data)
    op, (img_dict, raw) = result[0]
    assert op == "INLINE_IMAGE"
    assert raw == b"abc"


def test_inline_image_does_not_corrupt_surrounding_operators():
    data = b"(before) Tj BI /W 1 /H 1 ID \x00 EI (after) Tj"
    result = ops(data)
    assert result[0] == ("Tj", [b"before"])
    assert result[1][0] == "INLINE_IMAGE"
    assert result[2] == ("Tj", [b"after"])


def test_comment_and_whitespace_tolerated():
    result = ops(b"1 2 % a comment\nre")
    assert result == [("re", [1, 2])]
