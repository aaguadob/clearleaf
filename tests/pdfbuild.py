"""Build small PDFs for tests: numbered objects (bytes, in order from 1)
with a classic xref table and a trailer."""

from __future__ import annotations

import zlib


def pdf(objs: list, root: int = 1, trailer_extra: bytes = b"") -> bytes:
    out = b"%PDF-1.7\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offsets:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root %d 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, root, trailer_extra, xref)
    return out


def stream(data: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s >>\nstream\n" % (len(data), extra) + data + b"\nendstream"


def flate(data: bytes, extra: bytes = b"") -> bytes:
    return stream(zlib.compress(data), b"/Filter /FlateDecode " + extra)


CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
HELV = b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"


def page(contents: bytes = b"4 0 R", extra: bytes = b"", parent: int = 2) -> bytes:
    return (b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 200 200] /Contents " % parent + contents
            + b" /Resources " + HELV + b" " + extra + b" >>")


def one_page(content: bytes, page_extra: bytes = b"", extra_objects=()) -> bytes:
    """Catalog 1, Pages 2, Page 3, content 4, then extra_objects from 5."""
    return pdf([CATALOG, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page(extra=page_extra), stream(content),
                *extra_objects])


def pages(contents: list) -> bytes:
    """One page per content stream: catalog 1, Pages 2, then page/content pairs."""
    n = len(contents)
    kids = b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n))
    objs = [CATALOG, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)]
    for i, c in enumerate(contents):
        objs += [page(b"%d 0 R" % (4 + 2 * i)), stream(c)]
    return pdf(objs)
