"""Substitute glyph outlines for non-embedded fonts.

When a PDF references a font it doesn't embed (the overwhelmingly common
case for the standard-14 fonts — Helvetica/Times/Courier — and also seen
for arbitrary system font names like "Arial" or "Calibri" that a producer
assumed the viewer has), there's no font *program* in the file to get
outlines from at all. Real viewers substitute a locally-available font;
this ships Liberation Sans/Serif/Mono (SIL OFL 1.1, metrically compatible
with Helvetica/Times/Courier — see `_fontdata/LIBERATION-LICENSE.txt`) so
non-embedded text still renders with real glyph shapes instead of boxes.

Family selection for names outside the standard 14 is a keyword heuristic
(e.g. "Calibri" -> sans, "Georgia" -> serif) — reasonable, not exact, since
we're substituting a different typeface's actual letterforms either way.
Symbol/ZapfDingbats and dingbat-style fonts have no bundled substitute
(their glyphs aren't textual Latin shapes a generic substitute could
stand in for) and resolve to no outline.
"""

from __future__ import annotations

import os
from typing import Optional

from ._truetype import TrueTypeFont

_FONTDATA_DIR = os.path.join(os.path.dirname(__file__), "_fontdata")

_FAMILY_BY_KEYWORD = [
    ("courier", "LiberationMono"),
    ("mono", "LiberationMono"),
    ("consol", "LiberationMono"),
    ("times", "LiberationSerif"),
    ("serif", "LiberationSerif"),
    ("georgia", "LiberationSerif"),
    ("garamond", "LiberationSerif"),
    ("cambria", "LiberationSerif"),
    ("minion", "LiberationSerif"),
    ("helvetica", "LiberationSans"),
    ("arial", "LiberationSans"),
    ("verdana", "LiberationSans"),
    ("tahoma", "LiberationSans"),
    ("calibri", "LiberationSans"),
    ("segoe", "LiberationSans"),
]

_cache: dict = {}


def _family_for(base_font: str) -> Optional[str]:
    name = base_font.lower()
    if "symbol" in name or "dingbat" in name or "wingding" in name:
        return None
    for keyword, family in _FAMILY_BY_KEYWORD:
        if keyword in name:
            return family
    return "LiberationSans"  # generic fallback: better than no outline at all


def substitute_font(base_font: str, bold: bool, italic: bool) -> Optional[TrueTypeFont]:
    family = _family_for(base_font or "")
    if family is None:
        return None
    variant = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
    filename = f"{family}-{variant}.ttf"

    if filename in _cache:
        return _cache[filename]
    path = os.path.join(_FONTDATA_DIR, filename)
    try:
        with open(path, "rb") as f:
            data = f.read()
        font = TrueTypeFont(data)
    except OSError:
        font = None
    _cache[filename] = font
    return font
