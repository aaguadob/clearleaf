import os

import clearleaf as pdf

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def path(name):
    return os.path.join(FIXTURES, name)


def test_merge_concatenates_pages_in_order(tmp_path):
    out = tmp_path / "merged.pdf"
    pdf.merge([path("classic_simple.pdf"), path("two_page_inherited.pdf")], out)

    doc = pdf.open(str(out))
    try:
        assert len(doc) == 3
        assert doc[0].get_contents_raw() == (
            b"q 1 0 0 1 0 0 cm BT /F1 12 Tf 72 712 Td (Hello clearleaf) Tj ET Q"
        )
        assert doc[0].mediabox == (0.0, 0.0, 612.0, 792.0)
        # inherited MediaBox/Rotate from two_page_inherited.pdf's Pages node
        # must have been flattened onto the copied pages.
        assert doc[1].mediabox == (0.0, 0.0, 300.0, 400.0)
        assert doc[1].rotation == 0
        assert doc[2].mediabox == (0.0, 0.0, 100.0, 200.0)
        assert doc[2].rotation == 90
    finally:
        doc.close()


def test_merge_accepts_already_open_documents(tmp_path):
    out = tmp_path / "merged.pdf"
    a = pdf.open(path("classic_simple.pdf"))
    b = pdf.open(path("classic_simple.pdf"))
    try:
        pdf.merge([a, b], out)
        merged = pdf.open(str(out))
        try:
            assert len(merged) == 2
        finally:
            merged.close()
    finally:
        a.close()
        b.close()


def test_merge_preserves_font_resource_across_pages(tmp_path):
    out = tmp_path / "merged.pdf"
    pdf.merge([path("two_page_inherited.pdf")], out)

    doc = pdf.open(str(out))
    try:
        assert len(doc) == 2
        for page in doc:
            resources = doc.resolve(page.dict.get("Resources"))
            font_dict = doc.resolve(resources["Font"])
            font = doc.resolve(font_dict["F1"])
            assert font["BaseFont"] == "Helvetica"
    finally:
        doc.close()


def test_merge_single_input_roundtrips_text(tmp_path):
    out = tmp_path / "merged.pdf"
    pdf.merge([path("classic_simple.pdf")], out)
    doc = pdf.open(str(out))
    try:
        assert doc[0].get_text() == "Hello clearleaf"
    finally:
        doc.close()


def test_merge_selects_specific_pages_in_given_order(tmp_path):
    out = tmp_path / "merged.pdf"
    # two_page_inherited.pdf: page 0 is 300x400/rot0, page 1 is 100x200/rot90.
    pdf.merge([(path("two_page_inherited.pdf"), [1, 0])], out)

    doc = pdf.open(str(out))
    try:
        assert len(doc) == 2
        assert doc[0].mediabox == (0.0, 0.0, 100.0, 200.0)
        assert doc[0].rotation == 90
        assert doc[1].mediabox == (0.0, 0.0, 300.0, 400.0)
        assert doc[1].rotation == 0
    finally:
        doc.close()


def test_merge_drops_a_single_page(tmp_path):
    out = tmp_path / "merged.pdf"
    pdf.merge([(path("two_page_inherited.pdf"), [0])], out)

    doc = pdf.open(str(out))
    try:
        assert len(doc) == 1
        assert doc[0].mediabox == (0.0, 0.0, 300.0, 400.0)
    finally:
        doc.close()
