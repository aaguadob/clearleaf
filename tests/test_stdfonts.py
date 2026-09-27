from clearleaf._stdfonts import substitute_font


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
