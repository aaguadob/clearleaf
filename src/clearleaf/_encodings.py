"""Simple-font text encodings (ISO 32000-1:2008 Annex D) and the Adobe
Glyph List for resolving glyph names (e.g. in /Differences) to Unicode.

WinAnsiEncoding and MacRomanEncoding are, for practically all code points
that matter in real-world PDFs, the same byte-to-character assignments as
Python's built-in `cp1252` and `mac_roman` codecs — so those are used
directly instead of hand-transcribing Annex D's tables. StandardEncoding
is derived from its glyph-name table (`_stdenc.py`) through the Adobe
Glyph List. The full AGL (bundled as `_data/glyphlist.txt.gz`, BSD-3,
see `_data/ADOBE-LICENSE.txt`) resolves names, following the AGL
specification's rules for suffixes (`a.sc`), ligatures (`f_f_i`) and
`uniXXXX` / `uXXXX[XX]` names.
"""

from __future__ import annotations

import gzip
import os
from typing import Optional

from ._stdenc import STANDARD_ENCODING_NAMES


def _codec_table(codec: str) -> dict:
    table = {}
    for code in range(256):
        try:
            ch = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            continue
        if ch:
            table[code] = ch
    return table


WIN_ANSI_ENCODING = _codec_table("cp1252")
# Annex D, note 6: WinAnsiEncoding's unused codes above 040 map to the bullet.
WIN_ANSI_ENCODING.update(
    {code: "\u2022" for code in (0x7F, 0x81, 0x8D, 0x8F, 0x90, 0x9D)}
)
MAC_ROMAN_ENCODING = _codec_table("mac_roman")
# Filled in below from StandardEncoding's glyph-name table once the glyph
# list is loaded (it isn't a stock Python codec).
STANDARD_ENCODING: dict = {}
PDF_DOC_ENCODING = _codec_table("cp1252")

BASE_ENCODINGS = {
    "WinAnsiEncoding": WIN_ANSI_ENCODING,
    "MacRomanEncoding": MAC_ROMAN_ENCODING,
    "StandardEncoding": STANDARD_ENCODING,
    "PDFDocEncoding": PDF_DOC_ENCODING,
    "MacExpertEncoding": STANDARD_ENCODING,  # rare; no good stock mapping
}

# A small subset of the Adobe Glyph List: common named glyphs that show up
# in /Differences arrays. Anything not listed falls back to the "uniXXXX"/
# "uXXXX" naming convention (handled in glyph_name_to_unicode), which covers
# most subsetted-font glyph names seen in practice.
_AGL_SUBSET = {
    "space": " ",
    "exclam": "!",
    "quotedbl": '"',
    "numbersign": "#",
    "dollar": "$",
    "percent": "%",
    "ampersand": "&",
    "quotesingle": "'",
    "parenleft": "(",
    "parenright": ")",
    "asterisk": "*",
    "plus": "+",
    "comma": ",",
    "hyphen": "-",
    "period": ".",
    "slash": "/",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "colon": ":",
    "semicolon": ";",
    "less": "<",
    "equal": "=",
    "greater": ">",
    "question": "?",
    "at": "@",
    "bracketleft": "[",
    "backslash": "\\",
    "bracketright": "]",
    "asciicircum": "^",
    "underscore": "_",
    "grave": "`",
    "braceleft": "{",
    "bar": "|",
    "braceright": "}",
    "asciitilde": "~",
    "quoteleft": "‘",
    "quoteright": "’",
    "quotedblleft": "“",
    "quotedblright": "”",
    "bullet": "•",
    "endash": "–",
    "emdash": "—",
    "ellipsis": "…",
    "trademark": "™",
    "dagger": "†",
    "daggerdbl": "‡",
    "section": "§",
    "paragraph": "¶",
    "copyright": "©",
    "registered": "®",
    "degree": "°",
    "plusminus": "±",
    "fi": "ﬁ",
    "fl": "ﬂ",
    "florin": "ƒ",
    "nbspace": " ",
}
for _code in range(0x41, 0x5B):
    _AGL_SUBSET[chr(_code)] = chr(_code)
for _code in range(0x61, 0x7B):
    _AGL_SUBSET[chr(_code)] = chr(_code)

_AGL_LATIN1_EXTRA = {
    "Agrave": 0xC0,
    "Aacute": 0xC1,
    "Acircumflex": 0xC2,
    "Atilde": 0xC3,
    "Adieresis": 0xC4,
    "Aring": 0xC5,
    "AE": 0xC6,
    "Ccedilla": 0xC7,
    "Egrave": 0xC8,
    "Eacute": 0xC9,
    "Ecircumflex": 0xCA,
    "Edieresis": 0xCB,
    "Igrave": 0xCC,
    "Iacute": 0xCD,
    "Icircumflex": 0xCE,
    "Idieresis": 0xCF,
    "Eth": 0xD0,
    "Ntilde": 0xD1,
    "Ograve": 0xD2,
    "Oacute": 0xD3,
    "Ocircumflex": 0xD4,
    "Otilde": 0xD5,
    "Odieresis": 0xD6,
    "Oslash": 0xD8,
    "Ugrave": 0xD9,
    "Uacute": 0xDA,
    "Ucircumflex": 0xDB,
    "Udieresis": 0xDC,
    "Yacute": 0xDD,
    "Thorn": 0xDE,
    "germandbls": 0xDF,
    "agrave": 0xE0,
    "aacute": 0xE1,
    "acircumflex": 0xE2,
    "atilde": 0xE3,
    "adieresis": 0xE4,
    "aring": 0xE5,
    "ae": 0xE6,
    "ccedilla": 0xE7,
    "egrave": 0xE8,
    "eacute": 0xE9,
    "ecircumflex": 0xEA,
    "edieresis": 0xEB,
    "igrave": 0xEC,
    "iacute": 0xED,
    "icircumflex": 0xEE,
    "idieresis": 0xEF,
    "eth": 0xF0,
    "ntilde": 0xF1,
    "ograve": 0xF2,
    "oacute": 0xF3,
    "ocircumflex": 0xF4,
    "otilde": 0xF5,
    "odieresis": 0xF6,
    "oslash": 0xF8,
    "ugrave": 0xF9,
    "uacute": 0xFA,
    "ucircumflex": 0xFB,
    "udieresis": 0xFC,
    "yacute": 0xFD,
    "thorn": 0xFE,
    "ydieresis": 0xFF,
    "exclamdown": 0xA1,
    "cent": 0xA2,
    "sterling": 0xA3,
    "currency": 0xA4,
    "yen": 0xA5,
    "brokenbar": 0xA6,
    "dieresis": 0xA8,
    "ordfeminine": 0xAA,
    "guillemotleft": 0xAB,
    "logicalnot": 0xAC,
    "macron": 0xAF,
    "acute": 0xB4,
    "mu": 0xB5,
    "periodcentered": 0xB7,
    "cedilla": 0xB8,
    "onesuperior": 0xB9,
    "ordmasculine": 0xBA,
    "guillemotright": 0xBB,
    "onequarter": 0xBC,
    "onehalf": 0xBD,
    "threequarters": 0xBE,
    "questiondown": 0xBF,
    "twosuperior": 0xB2,
    "threesuperior": 0xB3,
    "multiply": 0xD7,
    "divide": 0xF7,
}
for _name, _code in _AGL_LATIN1_EXTRA.items():
    _AGL_SUBSET[_name] = chr(_code)

# A handful of WinAnsi-range symbols that live above 0x7F but aren't
# Latin-1 (cp1252 diverges from Latin-1 here) — filled in directly from
# the WinAnsiEncoding (cp1252) table itself rather than hand-typed code
# points, since we already trust that table.
_AGL_CP1252_EXTRA = {
    "Scaron": 0x8A,
    "OE": 0x8C,
    "Zcaron": 0x8E,
    "scaron": 0x9A,
    "oe": 0x9C,
    "zcaron": 0x9E,
    "Ydieresis": 0x9F,
    "circumflex": 0x88,
    "tilde": 0x98,
    "quotesinglbase": 0x82,
    "quotedblbase": 0x84,
    "perthousand": 0x89,
    "Euro": 0x80,
    "minus": 0x2212,
}
for _name, _code in _AGL_CP1252_EXTRA.items():
    if _code in WIN_ANSI_ENCODING:
        _AGL_SUBSET[_name] = WIN_ANSI_ENCODING[_code]
    else:
        _AGL_SUBSET[_name] = chr(_code)

# Reverse of the above: Unicode char -> a plausible glyph name, used to look
# up Adobe Core 14 AFM widths (keyed by glyph name) starting from a decoded
# character. Where multiple glyph names map to the same character, the
# dict-literal's last write wins — fine here since these are effectively
# synonyms for width-lookup purposes.
UNICODE_TO_GLYPH_NAME = {v: k for k, v in _AGL_SUBSET.items()}


def _load_agl() -> dict:
    path = os.path.join(os.path.dirname(__file__), "_data", "glyphlist.txt.gz")
    table: dict[str, str] = {}
    try:
        with gzip.open(path, "rt", encoding="ascii") as f:
            for line in f:
                if line.startswith("#") or ";" not in line:
                    continue
                name, codes = line.strip().split(";", 1)
                table[name] = "".join(chr(int(c, 16)) for c in codes.split())
    except OSError:
        pass
    return table


AGL = _load_agl()

# Curated names stay canonical in the reverse map; the AGL adds the rest,
# preferring the shortest name per character (so ' ' stays "space", not
# "spacehackarabic").
for _name, _text in sorted(AGL.items(), key=lambda kv: (len(kv[0]), kv[0])):
    if len(_text) == 1:
        UNICODE_TO_GLYPH_NAME.setdefault(_text, _name)


# Glyph names outside the AGL that producers still use for plain shapes
# (seen in Type 3 fonts). Other readers agree on these -- pypdf and PyMuPDF
# both read "square" as U+25A1; for "triangle" the geometric shape U+25B3
# is used (as PyMuPDF does) rather than U+2206 INCREMENT (pypdf).
EXTRA_GLYPH_NAMES = {"square": "\u25a1", "triangle": "\u25b3"}


def _component_to_unicode(part: str) -> Optional[str]:
    if part in _AGL_SUBSET:
        return _AGL_SUBSET[part]
    if part in AGL:
        return AGL[part]
    if part in EXTRA_GLYPH_NAMES:
        return EXTRA_GLYPH_NAMES[part]
    if part.startswith("uni") and len(part) >= 7 and (len(part) - 3) % 4 == 0:
        try:
            chars = [int(part[i : i + 4], 16) for i in range(3, len(part), 4)]
        except ValueError:
            chars = []
        if chars and all(not 0xD800 <= c <= 0xDFFF for c in chars):
            return "".join(chr(c) for c in chars)
    if part.startswith("u") and 5 <= len(part) <= 7:
        try:
            value = int(part[1:], 16)
        except ValueError:
            return None
        if value <= 0x10FFFF and not 0xD800 <= value <= 0xDFFF:
            return chr(value)
    return None


def glyph_name_to_unicode(name: str) -> Optional[str]:
    """Adobe Glyph List specification: drop any suffix after the first
    period, split ligature names on underscores, map each component."""
    if name in _AGL_SUBSET:
        return _AGL_SUBSET[name]
    base = name.split(".", 1)[0]
    if not base:
        return None
    parts = [_component_to_unicode(p) for p in base.split("_")]
    if not parts or any(p is None for p in parts):
        return None
    return "".join(parts)


# StandardEncoding from its real Annex D glyph-name table.
STANDARD_ENCODING.clear()
STANDARD_ENCODING.update(
    {
        code: text
        for code, text in (
            (c, glyph_name_to_unicode(n)) for c, n in STANDARD_ENCODING_NAMES.items()
        )
        if text
    }
)
