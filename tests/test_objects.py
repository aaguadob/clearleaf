from clearleaf._objects import PDFName, PDFReference, PDFStream, resolve


def test_pdfname_behaves_like_str():
    n = PDFName("Type")
    assert n == "Type"
    assert {n: 1}.get("Type") == 1
    assert repr(n) == "/Type"


def test_pdfreference_equality_and_hash():
    a = PDFReference(3, 0)
    b = PDFReference(3, 0)
    c = PDFReference(3, 1)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    assert repr(a) == "3 0 R"


def test_resolve_helper():
    ref = PDFReference(1, 0)
    assert resolve(ref, lambda r: "resolved") == "resolved"
    assert resolve(42, lambda r: "resolved") == 42


def test_pdfstream_get_data_uses_filters():
    import zlib

    payload = zlib.compress(b"hello world")
    s = PDFStream({"Filter": PDFName("FlateDecode")}, payload)
    assert s.get_data() == b"hello world"
