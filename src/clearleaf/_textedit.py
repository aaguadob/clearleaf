"""Editing text where it is: delete letters from a line, type new ones.

``page.text_lines()`` lists the page's lines of text with the position of
every character; ``page.edit_text_line(rect, text)`` changes one of them.

The edit is made in the content stream itself, not by covering the old
line and drawing a new one. Characters that stay keep their exact glyphs
and positions (kerning, justification); only what comes after an edit
point moves, by the width of what was added or removed. Typed characters
use the line's own font when it has them (a subset font only has the
characters the document already uses); others are set in the closest
standard font (Helvetica, Times or Courier, with the same weight and
slant). Text drawn inside a form XObject is replaced as a whole instead,
in the closest standard font.
"""

from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher

from . import _matrix as mx
from .errors import PDFError, public
from ._content import iter_operations
from ._edit import encode_winansi, flate_stream, font_dict
from ._fonts import SimpleFont
from ._objects import PDFName
from ._textextract import _DISPATCH, TextExtractor
from ._writer import serialize_value

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_STRIPPED_PROPS = ("ActualText", "Alt", "E")
_SHOW_OPS = ("Tj", "TJ", "'", '"')


class _Glyph:
    __slots__ = ("op", "data", "text", "font", "size", "h", "tc", "tw", "x", "width", "adv", "trm", "rise",
                 "color", "editable", "hidden", "along", "perp", "u", "scale", "psize", "angle")


class _Walker(TextExtractor):
    """Records every glyph of a page with the operator that draws it and its
    position within that operator (in the operator's text space)."""

    def __init__(self, doc, resources):
        super().__init__(doc, resources)
        self.glyphs: list = []
        self.op_info: dict = {}  # show operator index -> its state at the start
        self.used: dict = {}  # id(font) -> {character: code bytes} seen on the page
        self._op_index = -1
        self._x = 0.0
        self._bdc_stack: list = []

    def process(self, data: bytes) -> list:
        ops = list(iter_operations(data))
        for i, (op, operands) in enumerate(ops):
            self._op_index = i
            if op in ("BDC", "BMC"):
                self._bdc_stack.append(i if op == "BDC" else None)
            elif op == "EMC" and self._bdc_stack:
                self._bdc_stack.pop()
            handler = _WALK_DISPATCH.get(op)
            if handler is None:
                continue
            try:
                handler(self, operands)
            except Exception:
                continue
        return ops

    def _begin(self):
        self._x = 0.0
        if self._depth == 0:
            gs = self.gs
            self.op_info[self._op_index] = {
                "fname": gs.font_resource_name, "size": gs.font_size, "h": gs.h_scale, "font": gs.font,
                "tc": gs.char_space, "tw": gs.word_space, "end": 0.0,
                "bdc": [i for i in self._bdc_stack if i is not None]}

    def _end(self):
        if self._depth == 0 and self._op_index in self.op_info:
            self.op_info[self._op_index]["end"] = self._x

    def _op_Tj(self, operands):
        self._begin()
        self._show_text(operands[-1])
        self._end()

    def _op_TJ(self, operands):
        self._begin()
        for item in operands[-1]:
            if isinstance(item, (bytes, bytearray)):
                self._show_text(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                tx = (-item / 1000.0) * self.gs.font_size * self.gs.h_scale
                self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)
                self._x += tx
        self._end()

    def _op_quote(self, operands):
        self._op_Tstar([])
        self._begin()
        self._show_text(operands[-1])
        self._end()

    def _op_dquote(self, operands):
        aw, ac, s = operands[-3:]
        self.gs.word_space = float(aw)
        self.gs.char_space = float(ac)
        self._op_Tstar([])
        self._begin()
        self._show_text(s)
        self._end()

    def _show_text(self, s):
        font = self.gs.font
        if font is not None and isinstance(s, (bytes, bytearray)) and s:
            self._record(font, bytes(s))
        super()._show_text(s)

    def _record(self, font, s: bytes) -> None:
        gs = self.gs
        size, h, tc, tw, rise = gs.font_size, gs.h_scale, gs.char_space, gs.word_space, gs.rise
        tm, x, pos = self.tm, self._x, 0
        used = self.used.setdefault(id(font), {})
        for code, nbytes, text, width1000 in font.decode(s):
            w0 = width1000 / 1000.0
            tx = (w0 * size + tc + (tw if nbytes == 1 and code == 0x20 else 0.0)) * h
            trm = mx.multiply(tm, gs.ctm)
            g = _Glyph()
            g.op = self._op_index if self._depth == 0 else None
            g.data = s[pos:pos + nbytes]
            g.text = text or ""
            g.font, g.size, g.h, g.tc, g.tw, g.rise = font, size, h, tc, tw, rise
            g.x, g.width, g.adv, g.trm = x, w0 * size * h, tx, trm
            g.color = gs.fill_color
            g.editable = self._depth == 0
            g.hidden = gs.render_mode in (3, 7)
            self.glyphs.append(g)
            if len(g.text) == 1 and g.text not in used:
                used[g.text] = g.data
            pos += nbytes
            x += tx
            tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
        self._x = x


_WALK_DISPATCH = {**_DISPATCH, "Tj": _Walker._op_Tj, "TJ": _Walker._op_TJ, "'": _Walker._op_quote,
                  '"': _Walker._op_dquote}


# -- lines ----------------------------------------------------------------------------


def _place(g: _Glyph) -> None:
    a, b = g.trm[0], g.trm[1]
    s = math.hypot(a, b) or 1.0
    g.u = (a / s, b / s)
    g.scale = s
    ox, oy = mx.apply(g.trm, 0.0, g.rise)
    g.along = ox * g.u[0] + oy * g.u[1]
    g.perp = -ox * g.u[1] + oy * g.u[0]
    g.psize = abs(g.size * mx.scale_factor(g.trm)) or 1.0
    g.angle = round(math.degrees(math.atan2(g.u[1], g.u[0]))) % 360


class _Line:
    """Glyphs on one baseline, in reading order, with the text they spell.
    ``tokens`` pairs every character of ``text`` with its glyph (or None for
    a space the PDF leaves as a gap)."""

    def __init__(self, glyphs: list):
        self.glyphs = glyphs
        tokens = []  # (glyph or None, start along, end along)
        prev = None
        for g in glyphs:
            if prev is not None:
                end = prev.along + prev.width * prev.scale
                gap = g.along - end
                if gap > 0.15 * max(g.psize, prev.psize) and not prev.text.isspace() and not g.text.isspace():
                    tokens.append((None, end, g.along))
            tokens.append((g, g.along, g.along + g.width * g.scale))
            prev = g
        self.tokens = tokens
        self.chars = []  # (token index, character index in the token)
        text = []
        for t, (g, _, _) in enumerate(tokens):
            s = " " if g is None else g.text
            for k, ch in enumerate(s):
                text.append(ch)
                self.chars.append((t, k))
        self.text = "".join(text)
        first = glyphs[0]
        self.u, self.angle = first.u, first.angle
        self.perp = sum(g.perp for g in glyphs) / len(glyphs)
        self.size = max(g.psize for g in glyphs)
        # right-to-left text is stored in drawing order, not reading order
        self.rtl = any(unicodedata.bidirectional(c) in ("R", "AL") for c in self.text)
        self.editable = all(g.editable for g in glyphs) and not self.rtl
        self.hidden = all(g.hidden for g in glyphs)

    def point(self, along: float, perp: float) -> tuple:
        u = self.u
        return (along * u[0] - perp * u[1], along * u[1] + perp * u[0])

    def bbox(self) -> tuple:
        pts = []
        for g in self.glyphs:
            for a in (g.along, g.along + g.width * g.scale):
                for p in (g.perp - 0.2 * g.psize, g.perp + 0.8 * g.psize):
                    pts.append(self.point(a, p))
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def carets(self) -> list:
        """Position along the baseline before each character, and after the last."""
        out = []
        for g, start, end in self.tokens:
            n = 1 if g is None else len(g.text)
            for k in range(n):
                out.append(start + (end - start) * k / n)
        out.append(self.tokens[-1][2] if self.tokens else 0.0)
        return out


def _lines(glyphs: list) -> list:
    shown = [g for g in glyphs if g.text or g.width > 0]
    for g in shown:
        _place(g)
    by_angle: dict = {}
    for g in shown:
        by_angle.setdefault(g.angle, []).append(g)
    lines = []
    for group in by_angle.values():
        group.sort(key=lambda g: -g.perp)
        clusters, current = [], []
        for g in group:
            if current and abs(current[0].perp - g.perp) > 0.4 * min(current[0].psize, g.psize):
                clusters.append(current)
                current = []
            current.append(g)
        if current:
            clusters.append(current)
        for cluster in clusters:
            cluster.sort(key=lambda g: g.along)
            segment = []
            for g in cluster:
                if segment:
                    prev = segment[-1]
                    gap = g.along - (prev.along + prev.width * prev.scale)
                    # a wide gap with no space glyph in it: a column gutter or a table cell
                    if gap > 0.8 * max(g.psize, prev.psize):
                        lines.append(segment)
                        segment = []
                segment.append(g)
            if segment:
                lines.append(segment)
    out = [_Line(seg) for seg in lines if any(g.text.strip() for g in seg)]
    out.sort(key=lambda l: (l.angle != 0, -l.perp, l.tokens[0][1]))
    return out


# -- fonts ----------------------------------------------------------------------------


def font_style(font) -> dict:
    """Family (serif, sans or mono), weight and slant of a PDF font, from its
    name and descriptor, for choosing a standard font that looks alike."""
    name = _SUBSET.sub("", getattr(font, "base_font", "") or "")
    desc = getattr(font, "descriptor", None) or {}
    doc = getattr(font, "doc", None)

    def num(key):
        v = doc.resolve(desc.get(key)) if doc is not None and isinstance(desc, dict) else None
        return v if isinstance(v, (int, float)) else 0

    flags, lower = int(num("Flags")), name.lower()
    if flags & 1 or any(k in lower for k in ("courier", "mono", "consol", "menlo", "cmtt", "sftt", "typewriter", "code")):
        family = "mono"
    elif (flags & 2 or any(k in lower for k in (
            "times", "serif", "roman", "georgia", "garamond", "minion", "cambria", "palatino", "baskerville",
            "caslon", "bookman", "century", "cmr", "cmbx", "cmti", "cmsl", "sfrm", "sfbx", "sfti", "nimbusrom",
            "libertin", "charter", "utopia", "didot", "bodoni"))) and "sans" not in lower:
        family = "serif"
    else:
        family = "sans"
    bold = bool(flags & (1 << 18)) or num("FontWeight") >= 600 or any(
        k in lower for k in ("bold", "black", "heavy", "semibold", "demi", "medi", "cmbx", "sfbx", "-bd", "extrab"))
    italic = bool(flags & 64) or num("ItalicAngle") != 0 or any(
        k in lower for k in ("italic", "oblique", "cmti", "cmmi", "sfti", "cmsl", "-it"))
    return {"name": name, "family": family, "bold": bold, "italic": italic}


def standard_font_for(font) -> str:
    st = font_style(font)
    base = {"sans": ("helv", "hebo", "heit", "hebi"), "serif": ("tiro", "tibo", "tiit", "tibi"),
            "mono": ("cour", "cobo", "coit", "cobi")}[st["family"]]
    return base[(1 if st["bold"] else 0) + (2 if st["italic"] else 0)]


def _std_width(fontname: str, ch: str) -> float:
    """Advance width of one character in a standard font, per 1000 units."""
    from ._afm import STANDARD_FONT_WIDTHS
    from ._encodings import UNICODE_TO_GLYPH_NAME

    widths = STANDARD_FONT_WIDTHS[font_dict(fontname)["BaseFont"]]
    return widths.get("space" if ch == " " else UNICODE_TO_GLYPH_NAME.get(ch, ch), 500)


def _encoder(font, used: dict) -> dict:
    """Characters this font can draw -> their code bytes: those the page
    already draws with it, and, for a simple font that isn't a subset,
    everything its encoding maps."""
    table = dict(used.get(id(font), {}))
    if isinstance(font, SimpleFont) and not _SUBSET.match(font.base_font or ""):
        for c in range(256):
            try:
                decoded = list(font.decode(bytes([c])))
            except Exception:
                continue
            if decoded and len(decoded[0][2] or "") == 1 and decoded[0][3] > 0:
                table.setdefault(decoded[0][2], bytes([c]))
    return table


def _natural(font, data: bytes, size, h, tc, tw) -> float:
    """How far the pen moves after drawing ``data`` (text space)."""
    total = 0.0
    for code, nbytes, _text, width1000 in font.decode(data):
        total += (width1000 / 1000.0 * size + tc + (tw if nbytes == 1 and code == 0x20 else 0.0)) * h
    return total


# -- the edit -------------------------------------------------------------------------


class PageTextEditing:
    def _walk(self):
        walker = _Walker(self._doc, self.resources)
        ops = walker.process(self.get_contents_raw())
        return walker, ops, _lines(walker.glyphs)

    @public
    def text_lines(self) -> list:
        """The page's lines of text, top to bottom: ``text``, ``bbox``, font
        ``size``, ``baseline`` and, for horizontal lines, ``carets`` (the x
        position before each character and after the last). ``editable`` is
        False for text inside form XObjects, which ``edit_text_line``
        replaces as a whole."""
        _, _, lines = self._walk()
        out = []
        for line in lines:
            first = line.glyphs[0]
            item = {"text": line.text, "bbox": self._rect_from_pdf(line.bbox()), "size": round(line.size, 3),
                    "angle": line.angle, "editable": line.editable, "hidden": line.hidden,
                    "color": "#%06x" % first.color, "font": font_style(first.font)}
            if line.angle == 0:
                item["carets"] = [round(self._from_pdf(a, 0)[0], 3) for a in line.carets()]
                item["baseline"] = round(self._from_pdf(0, line.perp)[1], 3)
            out.append(item)
        return out

    def _find_line(self, lines, rect, old):
        x0, y0, x1, y1 = self._rect_to_pdf(rect)
        best, best_score = None, 0.0
        for line in lines:
            b = line.bbox()
            ix = max(0.0, min(x1, b[2]) - max(x0, b[0]))
            iy = max(0.0, min(y1, b[3]) - max(y0, b[1]))
            inter = ix * iy
            union = (x1 - x0) * (y1 - y0) + (b[2] - b[0]) * (b[3] - b[1]) - inter
            score = inter / union if union > 0 else 0.0
            if old is not None and line.text == old:
                score += 1.0
            if score > best_score:
                best, best_score = line, score
        return best

    @public
    def edit_text_line(self, rect, text: str, old: str | None = None) -> None:
        """Change the line of text at ``rect`` (as reported by
        ``text_lines()``; ``old``, its current text, picks the right line
        when boxes overlap) so that it reads ``text``."""
        walker, ops, lines = self._walk()
        line = self._find_line(lines, rect, old)
        if line is None:
            raise PDFError("there's no text there to edit")
        if text == line.text:
            return
        if "\n" in text:
            raise PDFError("a line of text can't contain a line break")
        if line.rtl:
            raise PDFError("right-to-left text can't be edited in place yet")
        if not line.editable:
            return self._replace_line(line, text)
        _LineEdit(self, walker, ops, line, text).apply()

    def _replace_line(self, line, text: str) -> None:
        """Text inside a form XObject: remove the line and write the new text
        in the closest standard font."""
        if line.angle != 0:
            raise PDFError("this text is rotated and inside a graphic: clearleaf can't edit it yet")
        first = line.glyphs[0]
        fontname = standard_font_for(first.font)
        rect = self._rect_from_pdf(line.bbox())
        origin = self._from_pdf(*line.point(line.tokens[0][1], line.perp))
        c = first.color
        self.add_redact_annot(rect, fill=None)
        self.apply_redactions()
        if text.strip():
            self.insert_text(origin, text, fontsize=first.psize, fontname=fontname,
                             color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255))


class _LineEdit:
    def __init__(self, page, walker, ops, line, text):
        self.page, self.walker, self.ops, self.line, self.new = page, walker, ops, line, text
        self.fallback_names: dict = {}  # standard font -> resource name on this page

    def _layout(self) -> list:
        """What the edited line holds, in order: ("keep", glyph, position) and
        ("new", character, position, host glyph); positions along the
        baseline, in page units."""
        line, old, new = self.line, self.line.text, self.new
        tokens = line.tokens
        items = []
        kept_tokens = set()
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == "equal":
                for k in range(i1, i2):
                    t, pos = line.chars[k]
                    g = tokens[t][0]
                    n = 1 if g is None else len(g.text)
                    whole = all(i1 <= k - pos + m < i2 for m in range(n))
                    if whole:
                        if pos == 0:
                            items.append(("keep", t))
                            kept_tokens.add(t)
                    else:
                        items.append(("char", old[k], t))
            else:
                for ch in new[j1:j2]:
                    items.append(("char", ch, None))
        # glyphs that draw no text go with the glyph before them
        for t, (g, _, _) in enumerate(tokens):
            if g is not None and not g.text and (t == 0 or t - 1 in kept_tokens):
                kept_tokens.add(t)
                items.insert(next((n + 1 for n, it in enumerate(items) if it[0] == "keep" and it[1] == t - 1), 0),
                             ("keep", t))

        next_along = [tokens[t + 1][1] if t + 1 < len(tokens) else tokens[t][2] for t in range(len(tokens))]
        cursor = tokens[0][1] if tokens else 0.0
        out = []
        host = None
        for n, it in enumerate(items):
            if it[0] == "keep":
                t = it[1]
                g = tokens[t][0]
                if g is not None:
                    out.append(("keep", g, cursor))
                    host = g
                cursor += next_along[t] - tokens[t][1]
            else:
                ch = it[1]
                if host is None:  # nothing kept before: take the style of what comes next
                    host = next((tokens[x[1]][0] for x in items[n:] if x[0] == "keep" and tokens[x[1]][0]), None) \
                        or next(g for g in line.glyphs if g.editable)
                width = self._char_width(host, ch)
                out.append(("new", ch, cursor, host))
                cursor += width
        return out

    def _encode(self, host, ch):
        """(code bytes, standard font name or None) for drawing ``ch`` in the
        host glyph's operator."""
        table = self._tables.setdefault(id(host.font), _encoder(host.font, self.walker.used))
        if ch in table:
            return table[ch], None
        fontname = standard_font_for(host.font)
        return encode_winansi(ch), fontname

    def _char_width(self, host, ch) -> float:
        """Advance of ``ch`` drawn in the host glyph's operator, page units."""
        data, fontname = self._encode(host, ch)
        if fontname is None:
            return _natural(host.font, data, host.size, host.h, host.tc, host.tw) * host.scale
        w = _std_width(fontname, ch) / 1000.0 * host.size
        return (w + host.tc + (host.tw if data == b" " else 0.0)) * host.h * host.scale

    def apply(self) -> None:
        page, line = self.page, self.line
        self._tables: dict = {}
        layout = self._layout()
        in_line = {id(g) for g in line.glyphs}
        # every operator that draws part of the line is rebuilt; the rest stay as they are
        per_op: dict = {}
        for g in self.walker.glyphs:
            if g.op is None or g.op not in self.walker.op_info:
                continue
            if id(g) in in_line:
                per_op.setdefault(g.op, [])
        for g in self.walker.glyphs:
            if g.op in per_op and id(g) not in in_line:
                per_op[g.op].append((g.x, g.data, None))  # another line's glyph in the same operator
        for it in layout:
            if it[0] == "keep":
                g, pos = it[1], it[2]
                per_op[g.op].append((g.x + (pos - g.along) / g.scale, g.data, None))
            else:
                _, ch, pos, host = it
                data, fontname = self._encode(host, ch)
                per_op.setdefault(host.op, []).append((host.x + (pos - host.along) / host.scale, data, fontname))
        replacements = {i: self._rebuild(i, items) for i, items in per_op.items()}
        touched_bdc = {b for i in per_op for b in self.walker.op_info[i]["bdc"]}
        page.dict["Contents"] = page._doc._new_ref(flate_stream(_serialize(self.ops, replacements, touched_bdc)))
        page._doc.is_dirty = True

    def _font_name(self, fontname: str) -> str:
        name = self.fallback_names.get(fontname)
        if name is None:
            name = self.page._add_resource("Font", "CLF", self.page._doc._new_ref(font_dict(fontname)))
            self.fallback_names[fontname] = name
        return name

    def _rebuild(self, index: int, items: list) -> bytes:
        info = self.walker.op_info[index]
        op, operands = self.ops[index]
        size, h, tc, tw, font = info["size"], info["h"], info["tc"], info["tw"], info["font"]
        unit = size * h / 1000.0
        if not unit:
            raise PDFError("this text has zero size and can't be edited")
        items.sort(key=lambda it: it[0])
        out = []
        if op == "'":
            out.append(b"T*")
        elif op == '"':
            out.append(serialize_value(operands[-3]) + b" Tw " + serialize_value(operands[-2]) + b" Tc T*")
        arr: list = []
        pen, current = 0.0, None

        def flush():
            if arr:
                out.append(serialize_value(list(arr)) + b" TJ")
                arr.clear()

        def move(to):
            d = to - pen
            if abs(d) > 1e-6:
                arr.append(round(-d / unit, 3))

        for x, data, fontname in items:
            if fontname != current:
                flush()
                name = info["fname"] if fontname is None else self._font_name(fontname)
                out.append(serialize_value(PDFName(name)) + b" %s Tf" % _num(size))
                current = fontname
            move(x)
            if arr and isinstance(arr[-1], bytes):
                arr[-1] = arr[-1] + data
            else:
                arr.append(data)
            if fontname is None:
                pen = x + _natural(font, data, size, h, tc, tw)
            else:
                w = _std_width(fontname, data.decode("cp1252")) / 1000.0 * size
                pen = x + (w + tc + (tw if data == b" " else 0.0)) * h
        if current is not None:
            flush()
            out.append(serialize_value(PDFName(info["fname"])) + b" %s Tf" % _num(size))
        move(info["end"])  # the pen ends where it did, so what follows stays put
        flush()
        return b"\n".join(out)


def _num(v: float) -> bytes:
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return (s if s not in ("", "-0") else "0").encode()


def _serialize(ops: list, replacements: dict, touched_bdc: set) -> bytes:
    out = []
    for i, (op, operands) in enumerate(ops):
        if i in replacements:
            if replacements[i]:
                out.append(replacements[i])
            continue
        if op == "INLINE_IMAGE":
            image_dict, raw = operands
            out.append(b"BI " + b" ".join(serialize_value(PDFName(k)) + b" " + serialize_value(v)
                                          for k, v in image_dict.items()) + b" ID " + raw + b"\nEI")
            continue
        if op == "BDC" and i in touched_bdc and operands and isinstance(operands[-1], dict):
            operands = [*operands[:-1], {k: v for k, v in operands[-1].items() if k not in _STRIPPED_PROPS}]
        out.append(b" ".join(serialize_value(o) for o in operands) + (b" " if operands else b"") + op.encode("latin-1"))
    return b"\n".join(out)
