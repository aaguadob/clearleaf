"""clearleaf — a permissively-licensed, clean-room PDF engine.

Public API mirrors PyMuPDF's shape (see the project brief for the full
target surface). Phases 1-2 provide file structure / object graph access
and text extraction:

    import clearleaf as pdf

    doc = pdf.open("file.pdf")
    page = doc[0]
    print(page.get_text())
    doc.close()
"""

from .errors import LIMITS, LimitExceeded, PDFError
from ._merge import merge
from ._objects import PDFName, PDFReference, PDFStream
from .document import Page, Pixmap, PDFDocument

__version__ = "0.1.0.dev0"

__all__ = [
    "open",
    "merge",
    "PDFError",
    "LimitExceeded",
    "LIMITS",
    "PDFDocument",
    "Page",
    "Pixmap",
    "PDFName",
    "PDFReference",
    "PDFStream",
]


def open(path) -> PDFDocument:
    """Open a PDF file (path or raw bytes) and return a PDFDocument."""
    return PDFDocument.open(path)
