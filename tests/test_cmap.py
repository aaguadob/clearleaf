from clearleaf._cmap import CMap, parse_cmap_stream


def test_identity_cmap_lookup_and_split():
    cm = CMap.identity(byte_length=2)
    assert cm.lookup(0x0041) == 0x0041
    codes = list(cm.split_codes(bytes([0x00, 0x41, 0x00, 0x42])))
    assert codes == [(0x0041, 2), (0x0042, 2)]


def test_bfchar_parsing():
    data = (
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"2 beginbfchar\n<0001> <0048>\n<0002> <0065>\nendbfchar\n"
    )
    cm = parse_cmap_stream(data, default_length=2)
    assert cm.lookup(0x0001) == "H"
    assert cm.lookup(0x0002) == "e"
    assert cm.lookup(0x0003) is None


def test_bfrange_single_destination_increments_last_char():
    data = b"1 beginbfrange\n<0005> <0009> <0041>\nendbfrange\n"
    cm = parse_cmap_stream(data)
    assert [cm.lookup(c) for c in range(0x0005, 0x000A)] == list("ABCDE")


def test_bfrange_array_destination():
    data = b"1 beginbfrange\n<0001> <0003> [<0041> <0042> <0043>]\nendbfrange\n"
    cm = parse_cmap_stream(data)
    assert cm.lookup(1) == "A"
    assert cm.lookup(2) == "B"
    assert cm.lookup(3) == "C"


def test_cidchar_and_cidrange():
    data = (
        b"1 begincidchar\n<0010> 5\nendcidchar\n"
        b"1 begincidrange\n<0020> <0025> 100\nendcidrange\n"
    )
    cm = parse_cmap_stream(data)
    assert cm.lookup(0x0010) == 5
    assert cm.lookup(0x0020) == 100
    assert cm.lookup(0x0023) == 103
    assert cm.lookup(0x0026) is None


def test_split_codes_respects_codespace_range_width():
    data = b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
    cm = parse_cmap_stream(data)
    codes = list(cm.split_codes(b"\x41\x42\x43"))
    assert codes == [(0x41, 1), (0x42, 1), (0x43, 1)]


def test_split_codes_default_length_when_no_codespace_declared():
    cm = CMap(default_length=2)
    codes = list(cm.split_codes(b"\x00\x41\x00\x42"))
    assert codes == [(0x0041, 2), (0x0042, 2)]


def test_malformed_cmap_stream_does_not_raise():
    # Missing endbfchar / stray tokens: parser should tolerate, not crash.
    data = b"beginbfchar <0001>"
    cm = parse_cmap_stream(data)
    assert cm.lookup(1) is None
