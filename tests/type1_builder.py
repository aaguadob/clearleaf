"""Build small Type 1 font programs for tests, in the same wire format as
real embedded fonts: clear-text header, eexec-encrypted private part,
individually encrypted charstrings (Adobe Type 1 Font Format, ch. 6-7)."""

from __future__ import annotations

from clearleaf._type1 import CHARSTRING_KEY, EEXEC_KEY, encrypt

OPS = {
    "hstem": [1], "vstem": [3], "vmoveto": [4], "rlineto": [5], "hlineto": [6], "vlineto": [7],
    "rrcurveto": [8], "closepath": [9], "callsubr": [10], "return": [11], "hsbw": [13],
    "endchar": [14], "rmoveto": [21], "hmoveto": [22], "vhcurveto": [30], "hvcurveto": [31],
    "dotsection": [12, 0], "seac": [12, 6], "sbw": [12, 7], "div": [12, 12],
    "callothersubr": [12, 16], "pop": [12, 17], "setcurrentpoint": [12, 33],
}


def encode_number(v: int) -> bytes:
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    return bytes([255]) + int(v).to_bytes(4, "big", signed=True)


def charstring(program: str) -> bytes:
    """'50 600 hsbw 0 0 rmoveto ...' -> plaintext charstring bytes."""
    out = bytearray()
    for tok in program.split():
        out += bytes(OPS[tok]) if tok in OPS else encode_number(int(tok))
    return bytes(out)


def _encrypted_cs(program: str, len_iv: int = 4) -> bytes:
    return encrypt(b"\x00" * len_iv + charstring(program), CHARSTRING_KEY)


def build_type1(glyphs: dict, subrs: dict | None = None, encoding: dict | None = None,
                font_matrix=(0.001, 0, 0, 0.001, 0, 0), hex_eexec: bool = False) -> bytes:
    """glyphs: name -> charstring program text; subrs: index -> program text;
    encoding: code -> glyph name (None = StandardEncoding)."""
    fm = " ".join(str(v) for v in font_matrix)
    if encoding is None:
        enc = b"/Encoding StandardEncoding def\n"
    else:
        enc = b"/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
        enc += b"".join(f"dup {c} /{n} put\n".encode() for c, n in sorted(encoding.items()))
        enc += b"readonly def\n"
    clear = (b"%!PS-AdobeFont-1.0: TestFont 001.000\n11 dict begin\n/FontName /TestFont def\n"
             b"/PaintType 0 def\n/FontType 1 def\n" + f"/FontMatrix [{fm}] readonly def\n".encode()
             + enc + b"currentdict end\ncurrentfile eexec\n")

    subrs = subrs or {}
    private = bytearray(b"dup /Private 8 dict dup begin\n/RD{string currentfile exch readstring pop}executeonly def\n"
                        b"/ND{noaccess def}executeonly def\n/NP{noaccess put}executeonly def\n/lenIV 4 def\n")
    private += f"/Subrs {max(subrs) + 1 if subrs else 0} array\n".encode()
    for i, prog in sorted(subrs.items()):
        blob = _encrypted_cs(prog)
        private += f"dup {i} {len(blob)} RD ".encode() + blob + b" NP\n"
    private += b"ND\n2 index /CharStrings " + str(len(glyphs) + 1).encode() + b" dict dup begin\n"
    for name, prog in {".notdef": "0 250 hsbw endchar", **glyphs}.items():
        blob = _encrypted_cs(prog)
        private += f"/{name} {len(blob)} RD ".encode() + blob + b" ND\n"
    private += b"end\nend\nreadonly put\nnoaccess put\ndup /FontName get exch definefont pop\nmark currentfile closefile\n"

    cipher = encrypt(b"\x9a\x3c\x51\x07" + bytes(private), EEXEC_KEY)
    if hex_eexec:
        hexed = cipher.hex()
        cipher = "\n".join(hexed[i : i + 64] for i in range(0, len(hexed), 64)).encode()
    trailer = b"\n" + (b"0" * 64 + b"\n") * 8 + b"cleartomark\n"
    return clear + cipher + trailer
