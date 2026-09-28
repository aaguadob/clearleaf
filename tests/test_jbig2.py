"""JBIG2Decode: the MQ arithmetic decoder against the T.88 test sequence,
the bitmap operations, and two real streams from the pdf.js test suite
(test/pdfs/jbig2_symbol_offset.pdf and jbig2_file_header.pdf, Apache-2.0,
Mozilla Foundation). Their expected bitmaps match PyMuPDF's rendering of
the same files (rendered-page agreement 0.998 and 1.000)."""

import base64

from clearleaf._jbig2 import Bitmap, IntDecoder, MQDecoder, _Contexts, jbig2_decode, parse_segments


def test_mq_decoder_matches_the_t88_test_sequence():
    # ITU-T T.88 Annex H.2: 32 bytes coded in a single context
    plain = bytes.fromhex("00 02 00 51 00 00 00 C0 03 52 87 2A AA AA AA AA 82 C0 20 00 FC D7 9E F6 BF 7F ED 90 4F 46 A3 BF")
    coded = bytes.fromhex("84 C7 3B FC E1 A1 43 04 02 20 00 00 41 0D BB 86 F4 31 7F FF 88 FF 37 47 1A DB 6A DF FF AC")
    mq, ctx = MQDecoder(coded), _Contexts(1)
    out = bytearray()
    for _ in plain:
        byte = 0
        for _ in range(8):
            byte = (byte << 1) | mq.decode(ctx, 0)
        out.append(byte)
    assert bytes(out) == plain


def test_bitmap_combination_operators():
    for op, expected in [(0, [1, 1, 1, 0]), (1, [1, 0, 0, 0]), (2, [0, 1, 1, 0]), (3, [1, 0, 0, 1]), (4, [1, 0, 1, 0])]:
        page = Bitmap(4, 1)
        page.rows[0][:] = bytes([1, 1, 0, 0])
        glyph = Bitmap(4, 1)
        glyph.rows[0][:] = bytes([1, 0, 1, 0])
        page.compose(glyph, 0, 0, op)
        assert list(page.rows[0]) == expected, op
    page = Bitmap(3, 2)
    glyph = Bitmap(2, 2, fill=1)
    page.compose(glyph, 2, 1, 0)  # clipped at the page edge
    assert [list(r) for r in page.rows] == [[0, 0, 0], [0, 0, 1]]
    assert Bitmap(10, 1, fill=1).packed() == bytes([0xFF, 0xC0])


def _rows(width, height, packed):
    row_bytes = (width + 7) // 8
    return ["".join("#" if (packed[y * row_bytes + x // 8] >> (7 - x % 8)) & 1 else "." for x in range(width))
            for y in range(height)]


# symbol dictionary (generic template 2) + text region, arithmetic coded
SYMBOL_TEXT = base64.b64decode(
    "AAAAADAAAQAAABMAAACEAAAADgAAAAAAAAAAAQAAAAAAAQABAQAAAGQIAAL/AAAACAAAAAg6SDeK"
    "rLQGMOCR6nvlZB3+bb/wRY292yCRzm1wdObzkU9uL2i74C9s62uF4ijSVFMSHpHRzkpEIYE80oud"
    "Q/8q0BizMcnB809fuzdfYDei74q5U9w0G/+sAAAAAgcgAQEAAAAmAAAAhAAAAA4AAAAAAAAAAAAA"
    "BAAAAAno0J752vCHcGHFOfq//6w="
)
SYMBOL_TEXT_BITMAP = [
    "....########..#########......#########.......######.#......#####...............###########..###########....####...####...###########",
    "........#.......#......#.........#..........#......##.....#.....#..............#....#....#....#.......#......#.....#.....#....#....#",
    "........#.......#.......#........#.........#........#....#.......#.............#....#....#....#.......#.......#...#......#....#....#",
    "........#.......#.......#........#.........#.............#........#............#....#....#....#...............#...#......#....#....#",
    "........#.......#.......#........#........#.......................#............#....#....#....#....#...........#.#.......#....#....#",
    "........#.......#......#.........#........#.......................#.................#.........#....#............#.............#.....",
    "........#.......#######..........#........#......................#..................#.........######............#.............#.....",
    "........#.......#......##........#........#....................##...................#.........#....#...........#.#............#.....",
    "##......#.......#........#.......#........#.....######.......##.....................#.........#....#..........#...#...........#.....",
    "#.......#.......#........#.......#........#.........#.......#.......................#.........#........#......#...#...........#.....",
    "#.......#.......#........#.......#........##........#......#........................#.........#........#.....#.....#..........#.....",
    "#.......#.......#........#.......#.........#........#.....#.........................#.........#........#....#.......#.........#.....",
    ".#.....#........#.......#........#..........#......##....#........#.................#.........#........#....#.......#.........#.....",
    "..#####.......##########.....#########.......######......##########...............#####.....############...####....####.....#####...",
]


def test_symbol_dictionary_and_text_region():
    kinds = [seg.type for seg in parse_segments(SYMBOL_TEXT)]
    assert kinds == [48, 0, 7]  # page info, symbol dictionary, immediate lossless text region
    width, height, packed = jbig2_decode(SYMBOL_TEXT)
    assert (width, height) == (132, 14)
    assert _rows(width, height, packed) == SYMBOL_TEXT_BITMAP  # reads "JBIG2 TEXT"


# a stream that (against the PDF rules) starts with a JBIG2 file header
FILE_HEADER = base64.b64decode(
    "l0pCMg0KGgoBAAAAAQAAAAAwAAEAAAATAAAAgAAAAGAAAAAAAAAAAAEAAAAAAAEmAAEAAAA+AAAA"
    "gAAAAGAAAAAAAAAAAAAAA//9/wL+/v6rXkW0TNlUBB4Pkx6D/3//f/9//xBrmWWEiKVVmfsr34sf"
    "/6wAAAACMQABAAAAAAAAAAMzAAEAAAAA"
)


def test_stream_with_a_file_header():
    width, height, packed = jbig2_decode(FILE_HEADER)
    rows = _rows(width, height, packed)
    assert (width, height) == (128, 96)
    # two black blocks at the top, one on the left, two at the bottom: an "E"-like shape
    assert rows[20][12:53] == "#" * 41 and rows[20][76:117] == "#" * 41
    assert rows[45][12:53] == "#" * 41 and rows[45][76:117] == "." * 41
    assert rows[5] == "." * 128 and rows[90] == "." * 128


def test_integer_decoder_out_of_band():
    # an all-0xFF stream decodes; the procedure never runs away
    mq = MQDecoder(bytes([0xFF]) * 8)
    value = IntDecoder().decode(mq)
    assert value is None or isinstance(value, int)
