"""clearleaf — a permissively-licensed, clean-room PDF engine.

The public API follows PyMuPDF's names:

    import clearleaf as pdf

    doc = pdf.open("file.pdf")
    page = doc[0]
    print(page.get_text())
    page.add_highlight_annot([(72, 100, 300, 114)])
    doc.save("file-edited.pdf")

``clearleaf edit file.pdf`` opens the editor in a browser.
"""

from .errors import LIMITS, LimitExceeded, PDFError
from ._merge import merge
from ._objects import PDFName, PDFReference, PDFStream
from .document import Page, Pixmap, PDFDocument
from ._edit import get_text_length

__version__ = "0.1.0"

__all__ = [
    "open",
    "merge",
    "PDFError",
    "LimitExceeded",
    "LIMITS",
    "PDFDocument",
    "Page",
    "Pixmap",
    "get_text_length",
    "PDFName",
    "PDFReference",
    "PDFStream",
]


def open(path) -> PDFDocument:
    """Open a PDF file (path or raw bytes) and return a PDFDocument."""
    return PDFDocument.open(path)
