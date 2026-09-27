"""Font loading for text extraction (ISO 32000-1:2008 section 9.6-9.8).

Two concrete kinds:
  - SimpleFont: Type1/TrueType/MMType1 — single-byte codes, /Widths array,
    an /Encoding (base table + /Differences).
  - Type0Font: composite (CID-keyed) fonts — codes split per an /Encoding
    CMap (usually the predefined Identity-H/V, occasionally an embedded
    CID CMap stream), CID widths from /W on the descendant CIDFont.

Both prefer an embedded /ToUnicode CMap for code-to-text mapping when
present, since it's the most reliable source regardless of font kind.

Non-embedded standard-14 fonts (or common aliases like Arial/Times New
Roman/Courier New) get real Adobe Core 14 AFM widths via `_afm.py`, looked
up by glyph name — so only *genuinely* non-standard, non-embedded fonts
without a /Widths array fall back to a flat guessed width.

Remaining known gaps (documented rather than silently wrong): symbolic
simple fonts without ToUnicode and without an embedded TrueType font
program (or whose embedded program's cmap subtable this parser doesn't
recognize) fall back to a Latin-1-ish guess; Type0 fonts without ToUnicode
and without a usable CID->Unicode table (e.g. genuine Adobe-Japan1/GB1/...
character collections) decode to empty text.
"""

from __future__ import annotations

from typing import Optional

from ._afm import STANDARD_FONT_WIDTHS
from ._cmap import CMap, parse_cmap_stream
from ._encodings import (
    BASE_ENCODINGS,
    STANDARD_ENCODING,
    UNICODE_TO_GLYPH_NAME,
    WIN_ANSI_ENCODING,
    glyph_name_to_unicode,
)
from ._objects import PDFStream
from ._symbolenc import SYMBOL_ENCODING, ZAPF_DINGBATS_ENCODING
from ._truetype import TrueTypeFont

_DEFAULT_SIMPLE_WIDTH = 500.0  # last-resort guess absent /Widths + MissingWidth

# Common non-standard-14 font names PDF producers use, expecting a viewer
# substitution — mapped to the standard-14 name whose AFM metrics apply.
_STANDARD_FONT_ALIASES = {
    "Arial": "Helvetica", "ArialMT": "Helvetica",
    "Arial-Bold": "Helvetica-Bold", "Arial,Bold": "Helvetica-Bold",
    "Arial-BoldMT": "Helvetica-Bold",
    "Arial-Italic": "Helvetica-Oblique", "Arial,Italic": "Helvetica-Oblique",
    "Arial-ItalicMT": "Helvetica-Oblique",
    "Arial-BoldItalic": "Helvetica-BoldOblique",
    "Arial,BoldItalic": "Helvetica-BoldOblique",
    "Arial-BoldItalicMT": "Helvetica-BoldOblique",
    "TimesNewRoman": "Times-Roman", "TimesNewRomanPSMT": "Times-Roman",
    "TimesNewRoman,Bold": "Times-Bold", "TimesNewRomanPS-BoldMT": "Times-Bold",
    "TimesNewRoman,Italic": "Times-Italic", "TimesNewRomanPS-ItalicMT": "Times-Italic",
    "TimesNewRoman,BoldItalic": "Times-BoldItalic",
    "TimesNewRomanPS-BoldItalicMT": "Times-BoldItalic",
    "CourierNew": "Courier", "CourierNewPSMT": "Courier",
    "CourierNew,Bold": "Courier-Bold", "CourierNewPS-BoldMT": "Courier-Bold",
    "CourierNew,Italic": "Courier-Oblique", "CourierNewPS-ItalicMT": "Courier-Oblique",
    "CourierNew,BoldItalic": "Courier-BoldOblique",
    "CourierNewPS-BoldItalicMT": "Courier-BoldOblique",
}


def _normalize_base_font(name: str) -> str:
    """Strip a subset tag prefix like "ABCDEF+Helvetica" (six uppercase
    letters + "+", ISO 32000-1 9.6.4) down to the plain font name."""
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


def _standard_widths_for(base_font: str) -> Optional[dict]:
    name = _normalize_base_font(base_font)
    if name in STANDARD_FONT_WIDTHS:
        return STANDARD_FONT_WIDTHS[name]
    alias = _STANDARD_FONT_ALIASES.get(name)
    if alias is not None:
        return STANDARD_FONT_WIDTHS.get(alias)
    return None


_ZAPF_DINGBATS_NAMES = {"zapfdingbats", "zapf-dingbats", "dingbats"}
_SYMBOL_NAMES = {"symbol", "symbolmt"}


def _builtin_encoding_for(base_font: str) -> Optional[dict]:
    """Symbol and ZapfDingbats aren't Latin text fonts — they don't use
    Standard/WinAnsi/MacRomanEncoding at all, only their own fixed,
    font-specific built-in encoding (ISO 32000-1 9.6.6.2), which a
    /Differences array (if present) then patches on top of.

    Matched by exact normalized name (not substring) — a font merely
    *named* something like "CustomSymbolFont" is not the Symbol font and
    must not get its Greek/math glyph mapping.
    """
    name = _normalize_base_font(base_font).lower()
    if name in _ZAPF_DINGBATS_NAMES:
        return ZAPF_DINGBATS_ENCODING
    if name in _SYMBOL_NAMES:
        return SYMBOL_ENCODING
    return None


def _load_embedded_truetype_from(doc, descriptor: dict) -> Optional[TrueTypeFont]:
    """Load a /FontFile2 (TrueType) or sfnt-wrapped /FontFile3 (OpenType)
    program from a FontDescriptor, for both symbolic-encoding resolution
    (Phase 2) and glyph-outline rendering (Phase 3). Bare Type1/CFF
    programs (FontFile, or FontFile3 without an sfnt wrapper) aren't
    parsed — out of scope, see the module docstring."""
    if not isinstance(descriptor, dict):
        return None
    for key in ("FontFile2", "FontFile3"):
        ff = doc.resolve(descriptor.get(key))
        if not isinstance(ff, PDFStream):
            continue
        try:
            data = ff.get_data(resolver=doc.resolve)
        except Exception:
            continue
        if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
            continue  # bare Type1/CFF program, or something unrecognized
        try:
            return TrueTypeFont(data)
        except Exception:
            return None
    return None


class _BaseFont:
    def __init__(self, doc, font_dict: dict):
        self.doc = doc
        self.dict = font_dict
        self.subtype = str(doc.resolve(font_dict.get("Subtype", "")) or "")
        self.base_font = str(doc.resolve(font_dict.get("BaseFont", "")) or "")
        self.to_unicode = self._load_to_unicode()
        self.descriptor: dict = {}

    def _load_to_unicode(self) -> Optional[CMap]:
        tu = self.doc.resolve(self.dict.get("ToUnicode"))
        if isinstance(tu, PDFStream):
            try:
                data = tu.get_data(resolver=self.doc.resolve)
                return parse_cmap_stream(data, default_length=2)
            except Exception:
                return None
        return None

    def decode(self, data: bytes):
        raise NotImplementedError


class SimpleFont(_BaseFont):
    def __init__(self, doc, font_dict: dict):
        super().__init__(doc, font_dict)
        self.descriptor = doc.resolve(font_dict.get("FontDescriptor")) or {}
        first_char = doc.resolve(font_dict.get("FirstChar"))
        widths_arr = doc.resolve(font_dict.get("Widths"))
        self.widths: dict[int, float] = {}
        if isinstance(widths_arr, list) and isinstance(first_char, int):
            for i, w in enumerate(widths_arr):
                w = doc.resolve(w)
                if isinstance(w, (int, float)):
                    self.widths[first_char + i] = float(w)
        missing = 0.0
        if isinstance(self.descriptor, dict):
            mw = doc.resolve(self.descriptor.get("MissingWidth", 0))
            if isinstance(mw, (int, float)):
                missing = float(mw)
        self.missing_width = missing

        flags = 0
        if isinstance(self.descriptor, dict):
            f = doc.resolve(self.descriptor.get("Flags", 0))
            if isinstance(f, (int, float)):
                flags = int(f)
        self.symbolic = bool(flags & 4) and not bool(flags & 32)
        self.code_to_unicode = self._build_encoding()
        self._standard_widths = _standard_widths_for(self.base_font)
        self.embedded_truetype = _load_embedded_truetype_from(doc, self.descriptor)

    def _build_encoding(self) -> dict:
        enc = self.doc.resolve(self.dict.get("Encoding"))
        base_table = None
        differences = None
        if isinstance(enc, str):
            base_table = BASE_ENCODINGS.get(str(enc))
        elif isinstance(enc, dict):
            base_name = self.doc.resolve(enc.get("BaseEncoding"))
            if base_name:
                base_table = BASE_ENCODINGS.get(str(base_name))
            differences = self.doc.resolve(enc.get("Differences"))
        if base_table is None:
            base_table = _builtin_encoding_for(self.base_font)
        if base_table is None:
            base_table = STANDARD_ENCODING if self.symbolic else WIN_ANSI_ENCODING
        table = dict(base_table)
        if isinstance(differences, list):
            current = 0
            for item in differences:
                item = self.doc.resolve(item)
                if isinstance(item, (int, float)):
                    current = int(item)
                elif isinstance(item, str):
                    uni = glyph_name_to_unicode(str(item))
                    if uni is not None:
                        table[current] = uni
                    current += 1
        return table

    def width_of(self, code: int) -> float:
        if code in self.widths:
            return self.widths[code]
        if self._standard_widths is not None:
            ch = self.code_to_unicode.get(code)
            name = UNICODE_TO_GLYPH_NAME.get(ch) if ch else None
            if name is not None and name in self._standard_widths:
                return self._standard_widths[name]
        return self.missing_width or _DEFAULT_SIMPLE_WIDTH

    def decode(self, data: bytes):
        """Yield (code, nbytes, text, width_1000ths_em) per byte."""
        for code in data:
            text = None
            if self.to_unicode is not None:
                text = self.to_unicode.lookup(code)
            if text is None and self.symbolic and self.embedded_truetype is not None:
                text = self.embedded_truetype.resolve_symbolic_code_to_unicode(code)
            if text is None:
                text = self.code_to_unicode.get(code)
            if text is None:
                text = chr(code) if 32 <= code <= 255 else ""
            yield (code, 1, text, self.width_of(code))


class Type0Font(_BaseFont):
    def __init__(self, doc, font_dict: dict):
        super().__init__(doc, font_dict)
        descendants = doc.resolve(font_dict.get("DescendantFonts"))
        self.descendant: dict = {}
        if isinstance(descendants, list) and descendants:
            self.descendant = doc.resolve(descendants[0]) or {}
        self.descriptor = doc.resolve(self.descendant.get("FontDescriptor")) or {}
        dw = doc.resolve(self.descendant.get("DW", 1000))
        self.default_width = float(dw) if isinstance(dw, (int, float)) else 1000.0
        self.cid_widths = self._load_cid_widths()
        self.encoding_cmap = self._load_encoding_cmap()
        self.embedded_truetype = _load_embedded_truetype_from(doc, self.descriptor)
        self.cid_to_gid_map = self._load_cid_to_gid_map()

    def _load_cid_to_gid_map(self) -> Optional[bytes]:
        m = self.doc.resolve(self.descendant.get("CIDToGIDMap"))
        if isinstance(m, PDFStream):
            try:
                return m.get_data(resolver=self.doc.resolve)
            except Exception:
                return None
        return None  # missing or /Identity: CID == GID

    def glyph_id_for_cid(self, cid: int) -> int:
        """CID -> glyph index in `self.embedded_truetype` (ISO 32000-1
        9.7.4.2). Only meaningful when rendering with this font's *own*
        embedded program — a substitute font's glyph indices have no
        relationship to this CIDFont's CIDs."""
        if self.cid_to_gid_map is None:
            return cid  # /Identity (default)
        offset = cid * 2
        if offset + 2 > len(self.cid_to_gid_map):
            return 0
        return (self.cid_to_gid_map[offset] << 8) | self.cid_to_gid_map[offset + 1]

    def _load_encoding_cmap(self) -> CMap:
        enc = self.doc.resolve(self.dict.get("Encoding"))
        if isinstance(enc, str) and str(enc) in ("Identity-H", "Identity-V"):
            return CMap.identity(byte_length=2)
        if isinstance(enc, PDFStream):
            try:
                data = enc.get_data(resolver=self.doc.resolve)
                return parse_cmap_stream(data, default_length=2)
            except Exception:
                return CMap.identity(byte_length=2)
        # Unrecognized predefined CMap name (e.g. "UniGB-UCS2-H") without an
        # embedded stream: fall back to 2-byte identity splitting so
        # decoding doesn't crash, even though CID values will be wrong.
        return CMap.identity(byte_length=2)

    def _load_cid_widths(self) -> dict:
        widths: dict[int, float] = {}
        w = self.doc.resolve(self.descendant.get("W"))
        if not isinstance(w, list):
            return widths
        i, n = 0, len(w)
        while i < n:
            first = self.doc.resolve(w[i])
            i += 1
            if i >= n or not isinstance(first, (int, float)):
                break
            nxt = self.doc.resolve(w[i])
            if isinstance(nxt, list):
                for j, width in enumerate(nxt):
                    width = self.doc.resolve(width)
                    if isinstance(width, (int, float)):
                        widths[int(first) + j] = float(width)
                i += 1
            else:
                i += 1
                if i >= n:
                    break
                width = self.doc.resolve(w[i])
                i += 1
                if isinstance(nxt, (int, float)) and isinstance(width, (int, float)):
                    for cid in range(int(first), int(nxt) + 1):
                        widths[cid] = float(width)
        return widths

    def width_of(self, cid: int) -> float:
        return self.cid_widths.get(cid, self.default_width)

    def decode(self, data: bytes):
        for code, nbytes in self.encoding_cmap.split_codes(data):
            looked_up = self.encoding_cmap.lookup(code)
            cid = looked_up if isinstance(looked_up, int) else code
            text = self.to_unicode.lookup(code) if self.to_unicode is not None else None
            if text is None:
                # No general CID->Unicode table for non-Identity character
                # collections (Adobe-Japan1 etc.) without ToUnicode — Phase 2
                # scope stops short of shipping those mapping tables.
                text = ""
            yield (code, nbytes, text, self.width_of(cid))


def load_font(doc, font_dict: dict):
    subtype = str(doc.resolve(font_dict.get("Subtype", "")) or "")
    if subtype == "Type0":
        return Type0Font(doc, font_dict)
    return SimpleFont(doc, font_dict)


def style_flags(font) -> int:
    """Best-effort PyMuPDF-shaped span style bits: italic=2, serif=4,
    monospaced=8, bold=16 (bit 1/superscript is set by the caller from
    text-rise state, not from font properties). This is a heuristic
    derived from public FontDescriptor data (/Flags, /StemV, /BaseFont) —
    not verified against a live PyMuPDF install, so treat exact bit parity
    as approximate rather than guaranteed.
    """
    descriptor = font.descriptor if isinstance(font.descriptor, dict) else {}
    pdf_flags_raw = font.doc.resolve(descriptor.get("Flags", 0)) if descriptor else 0
    try:
        pdf_flags = int(pdf_flags_raw) if pdf_flags_raw is not None else 0
    except (TypeError, ValueError):
        pdf_flags = 0

    name = (font.base_font or "").lower()
    italic = bool(pdf_flags & (1 << 6)) or "italic" in name or "oblique" in name
    serif = bool(pdf_flags & (1 << 1))
    fixed = bool(pdf_flags & 1)
    bold = bool(pdf_flags & (1 << 18)) or "bold" in name
    if descriptor:
        stemv = font.doc.resolve(descriptor.get("StemV"))
        if isinstance(stemv, (int, float)) and stemv >= 140:
            bold = True

    result = 0
    if italic:
        result |= 2
    if serif:
        result |= 4
    if fixed:
        result |= 8
    if bold:
        result |= 16
    return result
