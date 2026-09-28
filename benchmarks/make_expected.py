"""Build the verified expected texts in corpus/expected/ -- used instead of
PyMuPDF's text for the few files where PyMuPDF's output is demonstrably
wrong. Each expectation is derived WITHOUT clearleaf, from an independent
source; corpus/EXPECTED.md documents the evidence per file.

    pip install pypdf           # dev-only, BSD-licensed
    python benchmarks/make_expected.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
FILES = HERE / "corpus" / "files"
OUT = HERE / "corpus" / "expected"
TEXT_PAGES = 10  # as in run_fidelity.py


def pypdf_text(file_id: str) -> str:
    """pypdf's extraction: the ToUnicode mappings concatenated in content
    order -- for the habibi files that's the text the producer encoded."""
    import pypdf

    reader = pypdf.PdfReader(FILES / f"{file_id}.pdf")
    return "\n".join(page.extract_text() for page in reader.pages[:TEXT_PAGES])


LABEL = re.compile(r"^a\d+ \[x([0-9A-F]{4})\]$")


def zapf_dingbats_text(file_id: str) -> str:
    """PyMuPDF's text, with each dingbat replaced by the character the
    document itself names for it: every glyph line is followed by a label
    line 'a<n> [x<hex>]' giving its Unicode value. PyMuPDF prints the raw
    font codes (control characters, '!', '"', ...) instead."""
    import pymupdf

    doc = pymupdf.open(FILES / f"{file_id}.pdf")
    pages = []
    for i in range(min(TEXT_PAGES, len(doc))):
        lines = doc[i].get_text().split("\n")
        for k in range(1, len(lines)):
            match = LABEL.match(lines[k].strip())
            if match:
                lines[k - 1] = chr(int(match.group(1), 16))
        pages.append("\n".join(lines))
    return "\n".join(pages)


EXPECTED = {
    "pypdf-habibi": pypdf_text,
    "pypdf-habibi-oneline-cmap": pypdf_text,
    "pypdf-habibi-rotated": pypdf_text,
    "pdfjs-ZapfDingbats": zapf_dingbats_text,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for file_id, derive in EXPECTED.items():
        text = derive(file_id)
        (OUT / f"{file_id}.txt").write_text(text, encoding="utf-8")
        print(f"{file_id}: {len(text.split())} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
