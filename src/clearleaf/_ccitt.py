"""CCITTFaxDecode (ISO 32000-1 section 7.4.6): ITU-T T.4 (Group 3, one- and
two-dimensional) and T.6 (Group 4) fax decoding.

Written from the ITU-T recommendations: modified Huffman run lengths
(T.4 tables 2 and 3, plus the shared extended make-up codes), and the
two-dimensional pass / horizontal / vertical modes that code each line
relative to the line above it (T.4 section 4.2, T.6 section 2.2).

Output is packed 1 bit per pixel rows, black = 0 unless BlackIs1 is true
-- the sample format the filter is defined to produce.
"""

from __future__ import annotations


class CCITTError(ValueError):
    pass


# (run length, code) -- T.4 table 2 (terminating) and table 3 (make-up).
_WHITE_CODES = """
0 00110101 1 000111 2 0111 3 1000 4 1011 5 1100 6 1110 7 1111 8 10011 9 10100
10 00111 11 01000 12 001000 13 000011 14 110100 15 110101 16 101010 17 101011
18 0100111 19 0001100 20 0001000 21 0010111 22 0000011 23 0000100 24 0101000
25 0101011 26 0010011 27 0100100 28 0011000 29 00000010 30 00000011 31 00011010
32 00011011 33 00010010 34 00010011 35 00010100 36 00010101 37 00010110
38 00010111 39 00101000 40 00101001 41 00101010 42 00101011 43 00101100
44 00101101 45 00000100 46 00000101 47 00001010 48 00001011 49 01010010
50 01010011 51 01010100 52 01010101 53 00100100 54 00100101 55 01011000
56 01011001 57 01011010 58 01011011 59 01001010 60 01001011 61 00110010
62 00110011 63 00110100
64 11011 128 10010 192 010111 256 0110111 320 00110110 384 00110111
448 01100100 512 01100101 576 01101000 640 01100111 704 011001100 768 011001101
832 011010010 896 011010011 960 011010100 1024 011010101 1088 011010110
1152 011010111 1216 011011000 1280 011011001 1344 011011010 1408 011011011
1472 010011000 1536 010011001 1600 010011010 1664 011000 1728 010011011
"""

_BLACK_CODES = """
0 0000110111 1 010 2 11 3 10 4 011 5 0011 6 0010 7 00011 8 000101 9 000100
10 0000100 11 0000101 12 0000111 13 00000100 14 00000111 15 000011000
16 0000010111 17 0000011000 18 0000001000 19 00001100111 20 00001101000
21 00001101100 22 00000110111 23 00000101000 24 00000010111 25 00000011000
26 000011001010 27 000011001011 28 000011001100 29 000011001101 30 000001101000
31 000001101001 32 000001101010 33 000001101011 34 000011010010 35 000011010011
36 000011010100 37 000011010101 38 000011010110 39 000011010111 40 000001101100
41 000001101101 42 000011011010 43 000011011011 44 000001010100 45 000001010101
46 000001010110 47 000001010111 48 000001100100 49 000001100101 50 000001010010
51 000001010011 52 000000100100 53 000000110111 54 000000111000 55 000000100111
56 000000101000 57 000001011000 58 000001011001 59 000000101011 60 000000101100
61 000001011010 62 000001100110 63 000001100111
64 0000001111 128 000011001000 192 000011001001 256 000001011011 320 000000110011
384 000000110100 448 000000110101 512 0000001101100 576 0000001101101
640 0000001001010 704 0000001001011 768 0000001001100 832 0000001001101
896 0000001110010 960 0000001110011 1024 0000001110100 1088 0000001110101
1152 0000001110110 1216 0000001110111 1280 0000001010010 1344 0000001010011
1408 0000001010100 1472 0000001010101 1536 0000001011010 1600 0000001011011
1664 0000001100100 1728 0000001100101
"""

# Extended make-up codes, shared by both colours (T.4 table 3, bottom).
_EXTENDED_CODES = """
1792 00000001000 1856 00000001100 1920 00000001101 1984 000000010010
2048 000000010011 2112 000000010100 2176 000000010101 2240 000000010110
2304 000000010111 2368 000000011100 2432 000000011101 2496 000000011110
2560 000000011111
"""


def _table(*texts) -> dict:
    """{(bit length, code value): run length}."""
    out = {}
    for text in texts:
        tokens = text.split()
        for run, code in zip(tokens[0::2], tokens[1::2]):
            out[(len(code), int(code, 2))] = int(run)
    return out


WHITE = _table(_WHITE_CODES, _EXTENDED_CODES)
BLACK = _table(_BLACK_CODES, _EXTENDED_CODES)

# Two-dimensional mode codes (T.4 table 4): name -> (length, value).
PASS, HORIZONTAL, EXTENSION = "P", "H", "X"
_MODES = {
    (1, 0b1): 0,           # V0
    (3, 0b011): 1,         # VR1
    (3, 0b010): -1,        # VL1
    (6, 0b000011): 2,      # VR2
    (6, 0b000010): -2,     # VL2
    (7, 0b0000011): 3,     # VR3
    (7, 0b0000010): -3,    # VL3
    (4, 0b0001): PASS,
    (3, 0b001): HORIZONTAL,
    (7, 0b0000001): EXTENSION,
}


class _Bits:
    __slots__ = ("data", "pos", "end")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0  # in bits
        self.end = len(data) * 8

    def peek(self, n: int) -> int:
        """The next n bits as an integer (zeros past the end)."""
        byte, shift = divmod(self.pos, 8)
        nbytes = (shift + n + 7) // 8
        chunk = self.data[byte : byte + nbytes].ljust(nbytes, b"\x00")
        return (int.from_bytes(chunk, "big") >> (nbytes * 8 - shift - n)) & ((1 << n) - 1)

    def skip(self, n: int) -> None:
        self.pos += n

    def at_end(self) -> bool:
        return self.pos >= self.end

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7


def _read_code(bits: _Bits, table: dict, max_len: int = 13):
    window = bits.peek(max_len)
    for length in range(1, max_len + 1):
        value = table.get((length, window >> (max_len - length)))
        if value is not None:
            bits.skip(length)
            return value
    raise CCITTError("invalid code")


def _read_run(bits: _Bits, table: dict) -> int:
    """A run length: any make-up codes followed by one terminating code."""
    total = 0
    while True:
        run = _read_code(bits, table)
        total += run
        if run < 64:
            return total
        if bits.at_end():
            raise CCITTError("truncated run")


def _eol_ahead(bits: _Bits) -> bool:
    """At least 11 zero bits followed by a one: an EOL (with fill)."""
    zeros = 0
    pos = bits.pos
    while pos < bits.end:
        byte = bits.data[pos >> 3]
        if (byte >> (7 - (pos & 7))) & 1:
            return zeros >= 11
        zeros += 1
        pos += 1
    return False


def _skip_eol(bits: _Bits) -> None:
    while not bits.at_end() and bits.peek(1) == 0:
        bits.skip(1)
    bits.skip(1)


def _decode_1d(bits: _Bits, columns: int) -> list:
    changes = []
    a0 = 0
    white = True
    while a0 < columns:
        run = _read_run(bits, WHITE if white else BLACK)
        a0 = min(columns, a0 + run)
        changes.append(a0)
        white = not white
    return changes


def _decode_2d(bits: _Bits, columns: int, ref: list) -> list:
    """One line coded against the reference line ``ref`` (its changing
    elements: positions where the colour flips, first flip to black)."""
    changes: list = []
    ref = ref + [columns, columns]
    a0 = -1
    white = True  # colour of the run starting at a0
    i = 0  # scan position in ref
    while a0 < columns:
        # b1: first changing element on the reference line to the right of
        # a0 with the colour opposite a0's (even indices flip to black).
        while i > 0 and ref[i - 1] > a0:
            i -= 1
        while i < len(ref) - 2 and (ref[i] <= a0 or (i % 2 == 0) != white):
            i += 1
        b1 = ref[i]
        b2 = ref[i + 1]
        mode = _read_code(bits, _MODES, 7)
        if mode == PASS:
            a0 = b2
        elif mode == HORIZONTAL:
            start = max(a0, 0)
            run1 = _read_run(bits, WHITE if white else BLACK)
            run2 = _read_run(bits, BLACK if white else WHITE)
            a1 = min(columns, start + run1)
            a2 = min(columns, a1 + run2)
            changes += [a1, a2]
            a0 = a2
        elif mode == EXTENSION:
            raise CCITTError("uncompressed mode is not supported")
        else:
            a1 = b1 + mode
            if a1 < 0 or a1 > columns or a1 < a0:
                raise CCITTError("vertical mode out of range")
            changes.append(a1)
            a0 = a1
            white = not white
    return changes


def _pack(changes: list, columns: int, black_is_1: bool) -> bytes:
    """Packed row bits from changing elements (the line starts white)."""
    row = bytearray(b"\x00" * ((columns + 7) // 8))
    # Set bits for black pixels, then invert unless BlackIs1.
    start = None
    for k, pos in enumerate(changes):
        pos = min(pos, columns)
        if k % 2 == 0:
            start = pos
        else:
            _set_bits(row, start, pos)
            start = None
    if start is not None:
        _set_bits(row, start, columns)
    if not black_is_1:
        row = bytearray(b ^ 0xFF for b in row)
        if columns % 8:
            row[-1] &= (0xFF << (8 - columns % 8)) & 0xFF
    return bytes(row)


def _set_bits(row: bytearray, lo: int, hi: int) -> None:
    for x in range(lo, hi):
        row[x >> 3] |= 0x80 >> (x & 7)


def ccitt_decode(data: bytes, k: int = 0, columns: int = 1728, rows: int = 0,
                 end_of_line: bool = False, encoded_byte_align: bool = False,
                 end_of_block: bool = True, black_is_1: bool = False) -> bytes:
    columns = max(1, int(columns))
    bits = _Bits(bytes(data))
    out = bytearray()
    ref: list = []
    n = 0
    while not rows or n < rows:
        if bits.at_end():
            break
        if k < 0 and encoded_byte_align:
            bits.align()
        one_d = k == 0
        if _eol_ahead(bits):
            _skip_eol(bits)
            if _eol_ahead(bits) or bits.at_end():
                break  # EOFB / RTC: end of the data
            if k > 0:
                one_d = bits.peek(1) == 1
                bits.skip(1)
        else:
            if k >= 0 and encoded_byte_align:
                bits.align()
            if k > 0:
                one_d = bits.peek(1) == 1
                bits.skip(1)
        try:
            changes = _decode_1d(bits, columns) if one_d else _decode_2d(bits, columns, ref)
        except CCITTError:
            break  # damaged data: keep the rows decoded so far
        out += _pack(changes, columns, black_is_1)
        ref = changes
        n += 1
    row_bytes = (columns + 7) // 8
    if rows and n < rows:  # pad missing rows with white
        out += (b"\x00" if black_is_1 else b"\xff") * (row_bytes * (rows - n))
    return bytes(out)


def decode_with_params(data: bytes, params) -> bytes:
    params = params if isinstance(params, dict) else {}

    def get(name, default):
        value = params.get(name, default)
        return default if value is None else value

    return ccitt_decode(
        data,
        k=int(get("K", 0)),
        columns=int(get("Columns", 1728)),
        rows=int(get("Rows", 0)),
        end_of_line=bool(get("EndOfLine", False)),
        encoded_byte_align=bool(get("EncodedByteAlign", False)),
        end_of_block=bool(get("EndOfBlock", True)),
        black_is_1=bool(get("BlackIs1", False)),
    )
