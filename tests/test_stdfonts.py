import os

import pytest

import clearleaf._stdfonts as stdfonts
from clearleaf._stdfonts import substitute_font, system_font
from clearleaf._truetype import TrueTypeFont
from clearleaf._type1 import Type1Font


@pytest.fixture(autouse=True)
def bundled_fonts_only(request, monkeypatch):
    """These tests are about the bundled Liberation substitutes, so the
    machine's installed fonts are switched off (except where a test asks
    for them)."""
    if "installed" in request.node.name:
        return
    monkeypatch.setattr(stdfonts, "_USE_SYSTEM_FONTS", False)
    monkeypatch.setattr(stdfonts, "_cache", {})


def test_helvetica_family_maps_to_liberation_sans():
    font = substitute_font("Helvetica", bold=False, italic=False)
    assert font is not None
    assert font.num_glyphs > 0


def test_times_family_maps_to_liberation_serif():
    font = substitute_font("Times-Roman", bold=False, italic=False)
    assert font is not None


def test_courier_family_maps_to_liberation_mono():
    font = substitute_font("Courier", bold=True, italic=False)
    assert font is not None


def test_all_twelve_variants_load():
    for family_name in ["Helvetica", "Times-Roman", "Courier"]:
        for bold in (False, True):
            for italic in (False, True):
                font = substitute_font(family_name, bold=bold, italic=italic)
                assert font is not None, (family_name, bold, italic)
                assert font.num_glyphs > 0


def test_arial_alias_resolves_like_helvetica():
    font = substitute_font("ArialMT", bold=False, italic=False)
    assert font is not None


def test_symbol_and_dingbats_have_no_substitute():
    assert substitute_font("Symbol", bold=False, italic=False) is None
    assert substitute_font("ZapfDingbats", bold=False, italic=False) is None


def test_unknown_font_name_falls_back_to_generic_sans():
    font = substitute_font("SomeCompletelyUnknownFontXYZ", bold=False, italic=False)
    assert font is not None


def test_glyph_lookup_works_on_substitute_font():
    font = substitute_font("Helvetica", bold=False, italic=False)
    gid = None
    for platform_id, encoding_id, table in font.cmap_subtables:
        if (platform_id, encoding_id) == (3, 1):
            gid = table.get(ord("A"))
            break
    assert gid is not None
    outline = font.glyph_outline(gid)
    assert len(outline) >= 1
    assert font.advance_width(gid) is not None


def test_substitute_font_is_cached():
    a = substitute_font("Helvetica", bold=False, italic=False)
    b = substitute_font("Arial", bold=False, italic=False)
    assert a is b


MAC_FONTS = "/System/Library/Fonts"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(MAC_FONTS, "Helvetica.ttc")),
    reason="no installed Helvetica",
)
def test_installed_standard_fonts_are_preferred():
    stdfonts._cache.clear()
    plain = system_font("Helvetica", bold=False, italic=False)
    bold = system_font("Helvetica-Bold", bold=True, italic=False)
    assert isinstance(plain, TrueTypeFont) and plain.names().get(2) == "Regular"
    assert bold.names().get(2) == "Bold"
    assert substitute_font("Helvetica", bold=False, italic=False) is plain
    assert system_font("Times-Roman", False, False).names().get(1) == "Times"
    assert system_font("Courier", False, True).names().get(2) == "Oblique"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(MAC_FONTS, "ZapfDingbats.ttf")),
    reason="no installed ZapfDingbats",
)
def test_installed_symbol_fonts_fill_the_gap():
    stdfonts._cache.clear()
    assert system_font("ZapfDingbats", False, False).system_family == "ZapfDingbats"
    assert system_font("Symbol", False, False).system_family == "Symbol"


def test_installed_fonts_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(stdfonts, "_USE_SYSTEM_FONTS", False)
    monkeypatch.setattr(stdfonts, "_cache", {})
    assert system_font("Helvetica", False, False) is None
    assert substitute_font("ZapfDingbats", False, False) is None


def test_installed_urw_type1_font_is_found(tmp_path, monkeypatch):
    # A Linux-style font directory holding a URW base 35 Type 1 file, PFB-wrapped
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from type1_builder import build_type1

    program = build_type1(
        {"A": "0 500 hsbw 0 0 rmoveto 500 0 rlineto 0 500 rlineto closepath endchar"}
    )
    pfb = b"\x80\x01" + len(program).to_bytes(4, "little") + program + b"\x80\x03"
    (tmp_path / "NimbusSans-Regular.pfb").write_bytes(pfb)
    monkeypatch.setattr(stdfonts, "_SYSTEM_DIRS", [str(tmp_path)])
    monkeypatch.setattr(stdfonts, "_cache", {})
    font = system_font("Helvetica", bold=False, italic=False)
    assert isinstance(font, Type1Font) and font.has_glyph("A")
