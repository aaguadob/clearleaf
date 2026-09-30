"""Build small CFF font programs for tests (Adobe TN #5176 / #5177 wire
format): name-keyed with a custom charset and encoding, or CID-keyed with
an FDArray and FDSelect."""

from __future__ import annotations

import struct

from clearleaf._cffstd import CFF_STANDARD_STRINGS

T2_OPS = {
    "hstem": [1], "vstem": [3], "vmoveto": [4], "rlineto": [5], "hlineto": [6], "vlineto": [7],
    "rrcurveto": [8], "callsubr": [10], "return": [11], "endchar": [14], "hstemhm": [18],
    "hintmask": [19], "cntrmask": [20], "rmoveto": [21], "hmoveto": [22], "vstemhm": [23],
    "rcurveline": [24], "rlinecurve": [25], "vvcurveto": [26], "hhcurveto": [27], "callgsubr": [29],
    "vhcurveto": [30], "hvcurveto": [31],
    "hflex": [12, 34], "flex": [12, 35], "hflex1": [12, 36], "flex1": [12, 37],
    "add": [12, 10], "sub": [12, 11], "div": [12, 12], "neg": [12, 14], "dup": [12, 27], "exch": [12, 28],
}


def t2_number(v: int) -> bytes:
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    return bytes([28]) + struct.pack(">h", v)


def t2(program: str) -> bytes:
    """'10 rmoveto 50 0 rlineto ...'; 'mask:XX' emits a raw hint-mask byte (hex)."""
    out = bytearray()
    for tok in program.split():
        if tok.startswith("mask:"):
            out.append(int(tok[5:], 16))
        elif tok in T2_OPS:
            out += bytes(T2_OPS[tok])
        else:
            out += t2_number(int(tok))
    return bytes(out)


def index(items) -> bytes:
    items = list(items)
    if not items:
        return b"\x00\x00"
    offsets = [1]
    for it in items:
        offsets.append(offsets[-1] + len(it))
    return struct.pack(">HB", len(items), 4) + b"".join(struct.pack(">I", o) for o in offsets) + b"".join(items)


def dict_int(v: int) -> bytes:
    return b"\x1d" + struct.pack(">i", v)  # always 5 bytes, so offsets can be patched in


def dict_op(op: int) -> bytes:
    return bytes([12, op - 1200]) if op >= 1200 else bytes([op])


def dict_bytes(entries) -> bytes:
    """entries: list of (op, [operands]); floats encoded as reals."""
    out = bytearray()
    for op, operands in entries:
        for v in operands:
            if isinstance(v, float):
                out += _real(v)
            else:
                out += dict_int(v)
        out += dict_op(op)
    return bytes(out)


def _real(v: float) -> bytes:
    text = repr(v).replace("e-", "c").replace("e", "b").replace("E", "b")
    nibbles = []
    for ch in text:
        nibbles.append({".": 0xA, "b": 0xB, "c": 0xC, "-": 0xE}.get(ch, None) if not ch.isdigit() else int(ch))
    nibbles.append(0xF)
    if len(nibbles) % 2:
        nibbles.append(0xF)
    return b"\x1e" + bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def _layout(top_entries_fn, tail_parts_fn, name=b"TestCFF", strings=()):
    """Two-pass layout: offsets in the Top DICT are fixed-width ints, so the
    Top DICT's size doesn't depend on their values."""
    header = bytes([1, 0, 4, 4])
    name_index = index([name])
    string_index = index([s.encode() for s in strings])

    def build(offsets):
        top = index([dict_bytes(top_entries_fn(offsets))])
        return header + name_index + top

    prefix_len = len(build({k: 0 for k in ("charset", "encoding", "charstrings", "private", "fdarray", "fdselect")}))
    return header, name_index, string_index, build, prefix_len


def build_cff(glyphs: dict, subrs=(), gsubrs=(), encoding: dict | None = None,
              font_matrix=None, nominal_width=0, default_width=0) -> bytes:
    """Name-keyed CFF. glyphs: name -> Type 2 program ('.notdef' added);
    encoding: code -> glyph name (custom format 0), None for Standard."""
    names = [".notdef"] + [n for n in glyphs if n != ".notdef"]
    programs = [glyphs.get(".notdef", "endchar")] + [glyphs[n] for n in names[1:]]
    std = {s: i for i, s in enumerate(CFF_STANDARD_STRINGS)}
    custom = [n for n in names[1:] if n not in std]
    sid = {n: std[n] if n in std else len(CFF_STANDARD_STRINGS) + custom.index(n) for n in names}

    def top_entries(off):
        e = [(15, [off["charset"]]), (17, [off["charstrings"]]), (18, [off["private_size"], off["private"]])]
        if encoding is not None:
            e.append((16, [off["encoding"]]))
        if font_matrix:
            e.append((1207, [float(v) for v in font_matrix]))
        return e

    header, name_index, string_index, build_prefix, _ = _layout(lambda o: top_entries({**o, "private_size": 0}), None, strings=custom)
    gsubr_index = index([t2(p) for p in gsubrs])
    charset = b"\x00" + b"".join(struct.pack(">H", sid[n]) for n in names[1:])
    enc = b""
    if encoding is not None:
        gid_of = {n: i for i, n in enumerate(names)}
        by_gid = sorted(encoding.items(), key=lambda kv: gid_of[kv[1]])
        enc = bytes([0, len(by_gid)]) + bytes(c for c, _ in by_gid)
    charstrings = index([t2(p) for p in programs])
    local = index([t2(p) for p in subrs]) if subrs else b""

    private_entries = lambda subrs_off: [(20, [default_width]), (21, [nominal_width])] + ([(19, [subrs_off])] if subrs else [])
    private_size = len(dict_bytes(private_entries(0)))

    offsets = {"charset": 0, "encoding": 0, "charstrings": 0, "private": 0, "private_size": private_size}
    for _ in range(2):  # second pass with real offsets (sizes don't change)
        prefix = header + name_index + index([dict_bytes(top_entries(offsets))]) + string_index + gsubr_index
        pos = len(prefix)
        offsets["charset"] = pos
        pos += len(charset)
        offsets["encoding"] = pos
        pos += len(enc)
        offsets["charstrings"] = pos
        pos += len(charstrings)
        offsets["private"] = pos
    private = dict_bytes(private_entries(private_size))
    return prefix + charset + enc + charstrings + private + local


def build_cid_cff(glyphs_by_cid: dict, fd_of_cid: dict, fd_subrs: list) -> bytes:
    """CID-keyed CFF. glyphs_by_cid: cid -> program (gid order = sorted cids,
    after .notdef); fd_of_cid: cid -> font DICT index; fd_subrs: per-FD list
    of local subr programs."""
    cids = sorted(glyphs_by_cid)
    programs = ["endchar"] + [glyphs_by_cid[c] for c in cids]
    n_glyphs = len(programs)
    # charset format 2: one range per run of consecutive CIDs
    runs = []
    for c in cids:
        if runs and c == runs[-1][0] + runs[-1][1] + 1:
            runs[-1][1] += 1
        else:
            runs.append([c, 0])
    charset = b"\x02" + b"".join(struct.pack(">HH", first, left) for first, left in runs)
    fds = [0] + [fd_of_cid[c] for c in cids]
    # FDSelect format 3: ranges of equal fd
    ranges = []
    for gid, fd in enumerate(fds):
        if not ranges or ranges[-1][1] != fd:
            ranges.append((gid, fd))
    fdselect = b"\x03" + struct.pack(">H", len(ranges)) + b"".join(struct.pack(">HB", g, f) for g, f in ranges) + struct.pack(">H", n_glyphs)
    charstrings = index([t2(p) for p in programs])
    locals_ = [index([t2(p) for p in subrs]) for subrs in fd_subrs]
    private_for = lambda has_subrs, subrs_off: [(20, [0]), (21, [0])] + ([(19, [subrs_off])] if has_subrs else [])
    private_sizes = [len(dict_bytes(private_for(bool(s), 0))) for s in fd_subrs]

    def top_entries(off):
        return [(1230, [len(CFF_STANDARD_STRINGS), len(CFF_STANDARD_STRINGS) + 1, 0]),
                (15, [off["charset"]]), (17, [off["charstrings"]]),
                (1236, [off["fdarray"]]), (1237, [off["fdselect"]])]

    header = bytes([1, 0, 4, 4])
    name_index = index([b"TestCID"])
    string_index = index([b"Adobe", b"Identity"])
    gsubr_index = index([])
    offsets = {"charset": 0, "charstrings": 0, "fdarray": 0, "fdselect": 0}
    private_offsets = [0] * len(fd_subrs)
    for _ in range(2):
        fd_dicts = index([dict_bytes([(18, [private_sizes[i], private_offsets[i]])]) for i in range(len(fd_subrs))])
        prefix = header + name_index + index([dict_bytes(top_entries(offsets))]) + string_index + gsubr_index
        pos = len(prefix)
        offsets["charset"] = pos
        pos += len(charset)
        offsets["fdselect"] = pos
        pos += len(fdselect)
        offsets["charstrings"] = pos
        pos += len(charstrings)
        offsets["fdarray"] = pos
        pos += len(fd_dicts)
        blobs = []
        for i, subrs in enumerate(fd_subrs):
            private_offsets[i] = pos
            priv = dict_bytes(private_for(bool(subrs), private_sizes[i]))
            blobs.append(priv + (locals_[i] if subrs else b""))
            pos += len(blobs[-1])
    return prefix + charset + fdselect + charstrings + fd_dicts + b"".join(blobs)
