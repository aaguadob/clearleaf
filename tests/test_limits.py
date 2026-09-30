"""Hostile and malformed input: every failure is a PDFError, and nothing
hangs or exhausts memory."""

import time
import zlib

import pytest

import clearleaf as pdf
from clearleaf import LIMITS, LimitExceeded, PDFError

from .pdfbuild import CATALOG, one_page, pdf as build, stream


@pytest.fixture
def small_limit():
    old = LIMITS["max_stream_bytes"]
    LIMITS["max_stream_bytes"] = 1 << 20
    yield
    LIMITS["max_stream_bytes"] = old


def bomb(size: int) -> bytes:
    c = zlib.compressobj(9)
    return b"".join(c.compress(b"\0" * (1 << 20)) for _ in range(size >> 20)) + c.flush()


def test_decompression_bomb_stops_at_the_limit(small_limit):
    data = one_page(b"")
    doc = pdf.open(build([CATALOG, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                          b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
                          stream(bomb(64 << 20), b"/Filter /FlateDecode")]))
    t = time.time()
    with pytest.raises(LimitExceeded, match="max_stream_bytes"):
        doc[0].get_text()
    with pytest.raises(LimitExceeded):
        doc[0].get_pixmap()
    assert time.time() - t < 5
    assert data  # the helper itself works


def test_other_expanding_filters_are_limited(small_limit):
    from clearleaf._filters import decode_stream

    runs = bytes([129, 0]) * 20000  # RunLength: 128 bytes per 2 input bytes = 2.5 MB
    with pytest.raises(LimitExceeded):
        decode_stream({"Filter": "RunLengthDecode"}, runs)


def test_a_stream_under_the_limit_decodes():
    assert pdf.open(one_page(b"BT /F1 12 Tf 10 100 Td (hello) Tj ET"))[0].get_text().strip() == "hello"


@pytest.mark.parametrize("content", [b"[" * 100000 + b"]" * 100000, b"<< /A " * 50000 + b">>" * 50000])
def test_deep_nesting_in_content_is_a_pdf_error(content):
    with pytest.raises(PDFError, match="nest"):
        pdf.open(one_page(content))[0].get_text()


def test_deep_nesting_in_an_object_fails_cleanly():
    # the page object can't be parsed: either a PDFError, or a document
    # without that page -- never a RecursionError
    data = one_page(b"", page_extra=b"/X " + b"[" * 100000 + b"]" * 100000)
    try:
        doc = pdf.open(data)
        [p.get_text() for p in doc]
    except PDFError:
        pass


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", b"%PDF-1.7\n" + b"\xff" * 1000])
def test_garbage_is_a_pdf_error(data):
    with pytest.raises(PDFError):
        doc = pdf.open(data)
        [p.get_text() for p in doc]


def test_pdf_error_is_a_value_error_and_keeps_the_cause():
    with pytest.raises(ValueError) as info:
        pdf.open(b"not a pdf")
    assert isinstance(info.value, PDFError)
