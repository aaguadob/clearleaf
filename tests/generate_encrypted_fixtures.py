"""Regenerate tests/fixtures/encrypted/*.pdf with an *independent* writer
(pypdf + cryptography), so the decryption tests exercise interoperability,
not just clearleaf reading its own output. Not needed to run the tests:

    python -m venv /tmp/encvenv && /tmp/encvenv/bin/pip install pypdf cryptography
    /tmp/encvenv/bin/python tests/generate_encrypted_fixtures.py tests/fixtures/encrypted
"""

import sys
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject,
)

ALGORITHMS = {"rc4_40": "RC4-40", "rc4_128": "RC4-128", "aes_128": "AES-128", "aes_256": "AES-256"}
TEXT = "Secret text 42"


def page_writer() -> PdfWriter:
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)}),
    })
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 20 100 Td ({TEXT}) Tj ET".encode())
    page[NameObject("/Contents")] = w._add_object(content)
    w.add_metadata({"/Title": "Encrypted fixture", "/Subject": "string decryption check"})
    return w


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, algorithm in ALGORITHMS.items():
        w = page_writer()
        w.encrypt(user_password="user", owner_password="owner", algorithm=algorithm)
        w.write(out / f"{name}.pdf")
    w = page_writer()  # empty user password: opens without asking
    w.encrypt(user_password="", owner_password="owner", algorithm="AES-128")
    w.write(out / "aes_128_no_user_password.pdf")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/encrypted")
