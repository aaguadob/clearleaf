"""Adobe StandardEncoding as code -> glyph name (ISO 32000-1, Annex D.2).

`_encodings.py` approximates StandardEncoding with Latin-1 for text
extraction; glyph lookup by *name* (Type 1 built-in encodings, and the
seac operator, whose base/accent codes are defined in StandardEncoding)
needs the real table.
"""

_ASCII = {code: chr(code) for code in list(range(0x41, 0x5B)) + list(range(0x61, 0x7B))}
_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

STANDARD_ENCODING_NAMES: dict[int, str] = {
    32: "space", 33: "exclam", 34: "quotedbl", 35: "numbersign", 36: "dollar", 37: "percent",
    38: "ampersand", 39: "quoteright", 40: "parenleft", 41: "parenright", 42: "asterisk", 43: "plus",
    44: "comma", 45: "hyphen", 46: "period", 47: "slash",
    **{48 + i: name for i, name in enumerate(_DIGITS)},
    58: "colon", 59: "semicolon", 60: "less", 61: "equal", 62: "greater", 63: "question", 64: "at",
    **_ASCII,
    91: "bracketleft", 92: "backslash", 93: "bracketright", 94: "asciicircum", 95: "underscore",
    96: "quoteleft", 123: "braceleft", 124: "bar", 125: "braceright", 126: "asciitilde",
    161: "exclamdown", 162: "cent", 163: "sterling", 164: "fraction", 165: "yen", 166: "florin",
    167: "section", 168: "currency", 169: "quotesingle", 170: "quotedblleft", 171: "guillemotleft",
    172: "guilsinglleft", 173: "guilsinglright", 174: "fi", 175: "fl",
    177: "endash", 178: "dagger", 179: "daggerdbl", 180: "periodcentered", 182: "paragraph",
    183: "bullet", 184: "quotesinglbase", 185: "quotedblbase", 186: "quotedblright",
    187: "guillemotright", 188: "ellipsis", 189: "perthousand", 191: "questiondown",
    193: "grave", 194: "acute", 195: "circumflex", 196: "tilde", 197: "macron", 198: "breve",
    199: "dotaccent", 200: "dieresis", 202: "ring", 203: "cedilla", 205: "hungarumlaut",
    206: "ogonek", 207: "caron", 208: "emdash",
    225: "AE", 227: "ordfeminine", 232: "Lslash", 233: "Oslash", 234: "OE", 235: "ordmasculine",
    241: "ae", 245: "dotlessi", 248: "lslash", 249: "oslash", 250: "oe", 251: "germandbls",
}
