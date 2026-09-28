"""Substitute glyph outlines for non-embedded fonts.

When a PDF references a font it doesn't embed (the overwhelmingly common
case for the standard-14 fonts — Helvetica/Times/Courier — and also seen
for arbitrary system font names like "Arial" or "Calibri" that a producer
assumed the viewer has), there's no font *program* in the file to get
outlines from at all. Real viewers substitute a locally-available font;
this ships Liberation Sans/Serif/Mono (SIL OFL 1.1, metrically compatible
with Helvetica/Times/Courier — see `_fontdata/LIBERATION-LICENSE.txt`) so
non-embedded text still renders with real glyph shapes instead of boxes.

Before that, the fonts installed on the machine are tried: a viewer
shows the standard 14 fonts with the real (or URW-cloned) Helvetica,
Times, Courier, Symbol and ZapfDingbats when the system has them -- macOS
ships them, and Linux systems with Ghostscript have the URW base 35 set.
Nothing is bundled from those (their licences don't allow it); they're
only read from where the operating system put them.

Family selection for names outside the standard 14 is a keyword heuristic
(e.g. "Calibri" -> sans, "Georgia" -> serif) — reasonable, not exact, since
we're substituting a different typeface's actual letterforms either way.
Symbol/ZapfDingbats and dingbat-style fonts have no bundled substitute
(their glyphs aren't textual Latin shapes a generic substitute could
stand in for): without an installed copy they resolve to no outline.
"""

from __future__ import annotations

import glob
import os
from typing import Optional

from ._truetype import TrueTypeFont
from ._type1 import Type1Font

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


# -- installed fonts ---------------------------------------------------------

_SYSTEM_DIRS = [
    "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", "/usr/share/ghostscript/fonts",
    "/opt/homebrew/share/fonts", "/opt/homebrew/share/ghostscript/fonts",
    "/usr/local/share/ghostscript/fonts",
]

# family -> (macOS file, {(bold, italic): macOS subfamily},
#            {(bold, italic): URW base 35 / older gsfonts Type 1 file stems})
_SYSTEM_FAMILIES = {
    "Helvetica": ("Helvetica.ttc",
                  {(0, 0): "Regular", (1, 0): "Bold", (0, 1): "Oblique", (1, 1): "Bold Oblique"},
                  {(0, 0): ("NimbusSans-Regular", "n019003l"), (1, 0): ("NimbusSans-Bold", "n019004l"),
                   (0, 1): ("NimbusSans-Italic", "n019023l"), (1, 1): ("NimbusSans-BoldItalic", "n019024l")}),
    "Times": ("Times.ttc",
              {(0, 0): "Regular", (1, 0): "Bold", (0, 1): "Italic", (1, 1): "Bold Italic"},
              {(0, 0): ("NimbusRoman-Regular", "n021003l"), (1, 0): ("NimbusRoman-Bold", "n021004l"),
               (0, 1): ("NimbusRoman-Italic", "n021023l"), (1, 1): ("NimbusRoman-BoldItalic", "n021024l")}),
    "Courier": ("Courier.ttc",
                {(0, 0): "Regular", (1, 0): "Bold", (0, 1): "Oblique", (1, 1): "Bold Oblique"},
                {(0, 0): ("NimbusMonoPS-Regular", "n022003l"), (1, 0): ("NimbusMonoPS-Bold", "n022004l"),
                 (0, 1): ("NimbusMonoPS-Italic", "n022023l"), (1, 1): ("NimbusMonoPS-BoldItalic", "n022024l")}),
    "Symbol": ("Symbol.ttf", {}, {(0, 0): ("StandardSymbolsPS", "s050000l")}),
    "ZapfDingbats": ("ZapfDingbats.ttf", {}, {(0, 0): ("D050000L", "d050000l")}),
}
_LIBERATION_TO_SYSTEM = {"LiberationSans": "Helvetica", "LiberationSerif": "Times", "LiberationMono": "Courier"}
_USE_SYSTEM_FONTS = os.environ.get("CLEARLEAF_SYSTEM_FONTS", "1") != "0"


def _find_file(names: list) -> Optional[str]:
    for directory in _SYSTEM_DIRS:
        if not os.path.isdir(directory):
            continue
        for name in names:
            direct = os.path.join(directory, name)
            if os.path.isfile(direct):
                return direct
            found = glob.glob(os.path.join(directory, "**", name), recursive=True)
            if found:
                return found[0]
    return None


def _system_family(base_font: str) -> Optional[str]:
    name = (base_font or "").lower()
    if "dingbat" in name:
        return "ZapfDingbats"
    if "symbol" in name:
        return "Symbol"
    family = _family_for(base_font)
    return _LIBERATION_TO_SYSTEM.get(family) if family else None


def _pfb_to_pfa(data: bytes) -> bytes:
    """Strip PFB segment headers (0x80 type length) to plain Type 1 bytes."""
    if data[:1] != b"\x80":
        return data
    out, pos = bytearray(), 0
    while pos + 6 <= len(data) and data[pos] == 0x80 and data[pos + 1] in (1, 2):
        length = int.from_bytes(data[pos + 2 : pos + 6], "little")
        out += data[pos + 6 : pos + 6 + length]
        pos += 6 + length
    return bytes(out)


def system_font(base_font: str, bold: bool, italic: bool):
    """An installed copy of the standard font ``base_font`` stands for:
    a TrueTypeFont (macOS) or a Type1Font (URW fonts), or None."""
    if not _USE_SYSTEM_FONTS:
        return None
    family = _system_family(base_font)
    if family is None:
        return None
    style = (int(bold), int(italic))
    key = ("system", family, style)
    if key in _cache:
        return _cache[key]
    mac_file, mac_faces, urw_stems = _SYSTEM_FAMILIES[family]
    font = None
    path = _find_file([mac_file])
    if path is not None:
        font = _load_face(path, mac_faces.get(style) if mac_faces else None)
    if font is None:
        stems = urw_stems.get(style) or urw_stems.get((0, 0))
        path = _find_file([f"{stem}{ext}" for stem in stems for ext in (".t1", ".pfb", ".pfa")])
        if path is not None:
            try:
                with open(path, "rb") as f:
                    font = Type1Font(_pfb_to_pfa(f.read()))
            except Exception:
                font = None
    if isinstance(font, TrueTypeFont):
        font.system_family = family
    _cache[key] = font
    return font


def _load_face(path: str, subfamily: Optional[str]):
    try:
        with open(path, "rb") as f:
            data = f.read()
        for index in range(TrueTypeFont.face_count(data)):
            face = TrueTypeFont(data, index)
            if subfamily is None or face.names().get(2, "").lower() == subfamily.lower():
                return face
    except Exception:
        return None
    return None


def substitute_font(base_font: str, bold: bool, italic: bool):
    installed = system_font(base_font, bold, italic)
    if installed is not None:
        return installed
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
