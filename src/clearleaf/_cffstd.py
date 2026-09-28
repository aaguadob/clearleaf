"""The 391 CFF standard strings (Adobe Technical Note #5176, Appendix A).

String IDs (SIDs) below 391 in a CFF font refer to this table; higher SIDs
index the font's own String INDEX. SIDs 1-228 are the ISOAdobe character
set (also the order of CFF's predefined charset 0).
"""

_UPPER = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
_LOWER = [chr(c) for c in range(ord("a"), ord("z") + 1)]
_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

CFF_STANDARD_STRINGS: tuple = tuple([
    ".notdef", "space", "exclam", "quotedbl", "numbersign", "dollar", "percent", "ampersand",
    "quoteright", "parenleft", "parenright", "asterisk", "plus", "comma", "hyphen", "period", "slash",
    *_DIGITS,
    "colon", "semicolon", "less", "equal", "greater", "question", "at",
    *_UPPER,
    "bracketleft", "backslash", "bracketright", "asciicircum", "underscore", "quoteleft",
    *_LOWER,
    "braceleft", "bar", "braceright", "asciitilde", "exclamdown", "cent", "sterling", "fraction",
    "yen", "florin", "section", "currency", "quotesingle", "quotedblleft", "guillemotleft",
    "guilsinglleft", "guilsinglright", "fi", "fl", "endash", "dagger", "daggerdbl", "periodcentered",
    "paragraph", "bullet", "quotesinglbase", "quotedblbase", "quotedblright", "guillemotright",
    "ellipsis", "perthousand", "questiondown", "grave", "acute", "circumflex", "tilde", "macron",
    "breve", "dotaccent", "dieresis", "ring", "cedilla", "hungarumlaut", "ogonek", "caron", "emdash",
    "AE", "ordfeminine", "Lslash", "Oslash", "OE", "ordmasculine", "ae", "dotlessi", "lslash",
    "oslash", "oe", "germandbls", "onesuperior", "logicalnot", "mu", "trademark", "Eth", "onehalf",
    "plusminus", "Thorn", "onequarter", "divide", "brokenbar", "degree", "thorn", "threequarters",
    "twosuperior", "registered", "minus", "eth", "multiply", "threesuperior", "copyright",
    "Aacute", "Acircumflex", "Adieresis", "Agrave", "Aring", "Atilde", "Ccedilla", "Eacute",
    "Ecircumflex", "Edieresis", "Egrave", "Iacute", "Icircumflex", "Idieresis", "Igrave", "Ntilde",
    "Oacute", "Ocircumflex", "Odieresis", "Ograve", "Otilde", "Scaron", "Uacute", "Ucircumflex",
    "Udieresis", "Ugrave", "Yacute", "Ydieresis", "Zcaron", "aacute", "acircumflex", "adieresis",
    "agrave", "aring", "atilde", "ccedilla", "eacute", "ecircumflex", "edieresis", "egrave",
    "iacute", "icircumflex", "idieresis", "igrave", "ntilde", "oacute", "ocircumflex", "odieresis",
    "ograve", "otilde", "scaron", "uacute", "ucircumflex", "udieresis", "ugrave", "yacute",
    "ydieresis", "zcaron",
    # 229-378: expert character set
    "exclamsmall", "Hungarumlautsmall", "dollaroldstyle", "dollarsuperior", "ampersandsmall",
    "Acutesmall", "parenleftsuperior", "parenrightsuperior", "twodotenleader", "onedotenleader",
    *[d + "oldstyle" for d in _DIGITS],
    "commasuperior", "threequartersemdash", "periodsuperior", "questionsmall", "asuperior",
    "bsuperior", "centsuperior", "dsuperior", "esuperior", "isuperior", "lsuperior", "msuperior",
    "nsuperior", "osuperior", "rsuperior", "ssuperior", "tsuperior", "ff", "ffi", "ffl",
    "parenleftinferior", "parenrightinferior", "Circumflexsmall", "hyphensuperior", "Gravesmall",
    *[c + "small" for c in _UPPER],
    "colonmonetary", "onefitted", "rupiah", "Tildesmall", "exclamdownsmall", "centoldstyle",
    "Lslashsmall", "Scaronsmall", "Zcaronsmall", "Dieresissmall", "Brevesmall", "Caronsmall",
    "Dotaccentsmall", "Macronsmall", "figuredash", "hypheninferior", "Ogoneksmall", "Ringsmall",
    "Cedillasmall", "questiondownsmall", "oneeighth", "threeeighths", "fiveeighths", "seveneighths",
    "onethird", "twothirds", "zerosuperior", "foursuperior", "fivesuperior", "sixsuperior",
    "sevensuperior", "eightsuperior", "ninesuperior",
    *[d + "inferior" for d in _DIGITS],
    "centinferior", "dollarinferior", "periodinferior", "commainferior", "Agravesmall",
    "Aacutesmall", "Acircumflexsmall", "Atildesmall", "Adieresissmall", "Aringsmall", "AEsmall",
    "Ccedillasmall", "Egravesmall", "Eacutesmall", "Ecircumflexsmall", "Edieresissmall",
    "Igravesmall", "Iacutesmall", "Icircumflexsmall", "Idieresissmall", "Ethsmall", "Ntildesmall",
    "Ogravesmall", "Oacutesmall", "Ocircumflexsmall", "Otildesmall", "Odieresissmall", "OEsmall",
    "Oslashsmall", "Ugravesmall", "Uacutesmall", "Ucircumflexsmall", "Udieresissmall",
    "Yacutesmall", "Thornsmall", "Ydieresissmall",
    # 379-390
    "001.000", "001.001", "001.002", "001.003", "Black", "Bold", "Book", "Light", "Medium",
    "Regular", "Roman", "Semibold",
])
