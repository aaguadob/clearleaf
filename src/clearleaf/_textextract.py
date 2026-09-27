"""Content-stream interpretation for text extraction (ISO 32000-1:2008
chapter 9, text operators; 8.4/8.5 for the CTM stack and graphics state
needed to place text in device space).

Only the operators relevant to *finding and positioning text* are
interpreted (q/Q/cm, BT/ET, Tf/Td/TD/T*/Tm/Tc/Tw/Tz/TL/Ts/Tr, the four
show-text operators, fill-color operators for the reported span color, and
Do for recursing into Form XObjects). Painting operators (paths, images,
clipping, shading) are consumed as no-ops here — Phase 3 has its own,
fuller interpreter for actually rendering them.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import _matrix as mx
from ._content import iter_operations
from ._fonts import load_font, style_flags
from ._objects import PDFStream


@dataclass
class TextSpan:
    text: str
    font_resource_name: str
    base_font: str
    size: float
    origin: tuple
    bbox: tuple
    color: int
    flags: int
    newline: bool


class _GraphicsState:
    __slots__ = (
        "ctm",
        "fill_color",
        "font",
        "font_resource_name",
        "font_size",
        "char_space",
        "word_space",
        "h_scale",
        "leading",
        "rise",
        "render_mode",
    )

    def __init__(self):
        self.ctm = mx.IDENTITY
        self.fill_color = 0
        self.font = None
        self.font_resource_name = ""
        self.font_size = 0.0
        self.char_space = 0.0
        self.word_space = 0.0
        self.h_scale = 1.0
        self.leading = 0.0
        self.rise = 0.0
        self.render_mode = 0

    def copy(self) -> "_GraphicsState":
        g = _GraphicsState()
        for attr in self.__slots__:
            setattr(g, attr, getattr(self, attr))
        return g


def _pack_rgb(r, g, b) -> int:
    def c(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        return max(0, min(255, round(v * 255)))

    return (c(r) << 16) | (c(g) << 8) | c(b)


class TextExtractor:
    MAX_XOBJECT_DEPTH = 12

    def __init__(self, doc, resources: dict, ctm=mx.IDENTITY):
        self.doc = doc
        self.spans: list[TextSpan] = []
        self._font_cache: dict = {}
        self._gstack: list[_GraphicsState] = []
        self._resources_stack: list[dict] = [resources if isinstance(resources, dict) else {}]
        self.gs = _GraphicsState()
        self.gs.ctm = ctm
        self.tm = mx.IDENTITY
        self.tlm = mx.IDENTITY
        self._depth = 0
        self._last_origin = None
        self._last_size = None
        self._last_end = None

    def run(self, content_bytes: bytes) -> None:
        for op, operands in iter_operations(content_bytes):
            handler = _DISPATCH.get(op)
            if handler is None:
                continue
            try:
                handler(self, operands)
            except Exception:
                # A single malformed/unexpected operand shape shouldn't
                # abort extraction for the rest of a real-world PDF.
                continue

    # -- graphics state ---------------------------------------------------

    def _op_q(self, operands):
        self._gstack.append(self.gs.copy())

    def _op_Q(self, operands):
        if self._gstack:
            self.gs = self._gstack.pop()

    def _op_cm(self, operands):
        a, b, c, d, e, f = (float(x) for x in operands[-6:])
        self.gs.ctm = mx.multiply((a, b, c, d, e, f), self.gs.ctm)

    # -- text object / positioning -----------------------------------------

    def _op_BT(self, operands):
        self.tm = mx.IDENTITY
        self.tlm = mx.IDENTITY

    def _op_ET(self, operands):
        pass

    def _op_Tf(self, operands):
        name, size = operands[-2:]
        self.gs.font_resource_name = str(name)
        self.gs.font_size = float(size)
        self.gs.font = self._resolve_font(str(name))

    def _move_line(self, tx: float, ty: float):
        self.tlm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, ty), self.tlm)
        self.tm = self.tlm

    def _op_Td(self, operands):
        tx, ty = (float(x) for x in operands[-2:])
        self._move_line(tx, ty)

    def _op_TD(self, operands):
        tx, ty = (float(x) for x in operands[-2:])
        self.gs.leading = -ty
        self._move_line(tx, ty)

    def _op_Tstar(self, operands):
        self._move_line(0.0, -self.gs.leading)

    def _op_Tm(self, operands):
        a, b, c, d, e, f = (float(x) for x in operands[-6:])
        self.tlm = (a, b, c, d, e, f)
        self.tm = self.tlm

    def _op_Tc(self, operands):
        self.gs.char_space = float(operands[-1])

    def _op_Tw(self, operands):
        self.gs.word_space = float(operands[-1])

    def _op_Tz(self, operands):
        self.gs.h_scale = float(operands[-1]) / 100.0

    def _op_TL(self, operands):
        self.gs.leading = float(operands[-1])

    def _op_Ts(self, operands):
        self.gs.rise = float(operands[-1])

    def _op_Tr(self, operands):
        self.gs.render_mode = int(operands[-1])

    # -- showing text -------------------------------------------------------

    def _op_Tj(self, operands):
        self._show_text(operands[-1])

    def _op_TJ(self, operands):
        for item in operands[-1]:
            if isinstance(item, (bytes, bytearray)):
                self._show_text(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                tx = (-item / 1000.0) * self.gs.font_size * self.gs.h_scale
                self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)

    def _op_quote(self, operands):
        self._op_Tstar([])
        self._show_text(operands[-1])

    def _op_dquote(self, operands):
        aw, ac, s = operands[-3:]
        self.gs.word_space = float(aw)
        self.gs.char_space = float(ac)
        self._op_Tstar([])
        self._show_text(s)

    def _show_text(self, s):
        font = self.gs.font
        if font is None or not isinstance(s, (bytes, bytearray)) or not s:
            return
        size = self.gs.font_size
        hscale = self.gs.h_scale
        start_tm = self.tm
        parts = []
        for code, nbytes, text, width1000 in font.decode(bytes(s)):
            parts.append(text)
            w0 = width1000 / 1000.0
            extra = self.gs.word_space if (nbytes == 1 and code == 0x20) else 0.0
            tx = (w0 * size + self.gs.char_space + extra) * hscale
            self.tm = mx.multiply((1.0, 0.0, 0.0, 1.0, tx, 0.0), self.tm)

        full_text = "".join(parts)
        if not full_text:
            return

        start_device = mx.multiply(start_tm, self.gs.ctm)
        end_device = mx.multiply(self.tm, self.gs.ctm)
        origin = mx.apply(start_device, 0.0, self.gs.rise)
        end_point = mx.apply(end_device, 0.0, self.gs.rise)
        device_size = abs(size * mx.scale_factor(start_device)) or 1.0

        x0, x1 = sorted((origin[0], end_point[0]))
        y0, y1 = sorted((origin[1], end_point[1]))
        y0 -= 0.2 * device_size
        y1 += 0.8 * device_size

        newline = self._is_new_line(origin)
        if not newline and self._needs_word_space(origin, start_device, device_size, full_text):
            full_text = " " + full_text
        self.spans.append(
            TextSpan(
                text=full_text,
                font_resource_name=self.gs.font_resource_name,
                base_font=font.base_font,
                size=device_size,
                origin=origin,
                bbox=(x0, y0, x1, y1),
                color=self.gs.fill_color,
                flags=style_flags(font) | (1 if self.gs.rise > 0 else 0),
                newline=newline,
            )
        )
        self._last_origin = origin
        self._last_size = device_size
        self._last_end = end_point

    # Forward gap (in ems) between one text run's end and the next run's
    # start on the same line above which the gap is read as a word break.
    # Producers like pdfTeX never emit space glyphs -- words are separated
    # purely by TJ/Td positioning (typically 0.3-0.5 em), while kerning
    # inside a word is a few hundredths of an em.
    WORD_GAP_EM = 0.15

    def _needs_word_space(self, origin, start_device, device_size, text) -> bool:
        if self._last_end is None or not self.spans:
            return False
        if text[:1].isspace() or self.spans[-1].text[-1:].isspace():
            return False
        # Measure the gap along the text direction, so rotated text works.
        dx, dy = start_device[0], start_device[1]
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        gap = ((origin[0] - self._last_end[0]) * dx + (origin[1] - self._last_end[1]) * dy) / norm
        return gap > self.WORD_GAP_EM * device_size

    def _is_new_line(self, origin) -> bool:
        if self._last_origin is None:
            return True
        dy = abs(origin[1] - self._last_origin[1])
        threshold = max(1.0, 0.3 * (self._last_size or 1.0))
        return dy > threshold

    # -- color ----------------------------------------------------------

    def _set_color_from_operands(self, operands):
        nums = [o for o in operands if isinstance(o, (int, float)) and not isinstance(o, bool)]
        if len(nums) == 1:
            v = nums[0]
            self.gs.fill_color = _pack_rgb(v, v, v)
        elif len(nums) == 3:
            self.gs.fill_color = _pack_rgb(*nums)
        elif len(nums) == 4:
            c, m, y, k = nums
            self.gs.fill_color = _pack_rgb((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
        # else: pattern/other color space — leave the current color as-is.

    def _op_rg(self, operands):
        self.gs.fill_color = _pack_rgb(*operands[-3:])

    def _op_g(self, operands):
        v = operands[-1]
        self.gs.fill_color = _pack_rgb(v, v, v)

    def _op_k(self, operands):
        c, m, y, k = operands[-4:]
        self.gs.fill_color = _pack_rgb((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))

    def _op_sc(self, operands):
        self._set_color_from_operands(operands)

    def _op_scn(self, operands):
        self._set_color_from_operands(operands)

    # -- fonts / XObjects ----------------------------------------------------

    def _resolve_font(self, name: str):
        resources = self._resources_stack[-1]
        font_container = self.doc.resolve(resources.get("Font")) if isinstance(resources, dict) else None
        if not isinstance(font_container, dict):
            return None
        font_dict = self.doc.resolve(font_container.get(name))
        if not isinstance(font_dict, dict):
            return None
        key = id(font_dict)
        font = self._font_cache.get(key)
        if font is None:
            font = load_font(self.doc, font_dict)
            self._font_cache[key] = font
        return font

    def _op_Do(self, operands):
        if self._depth >= self.MAX_XOBJECT_DEPTH:
            return
        name = str(operands[-1])
        resources = self._resources_stack[-1]
        xobj_container = self.doc.resolve(resources.get("XObject")) if isinstance(resources, dict) else None
        if not isinstance(xobj_container, dict):
            return
        xobj = self.doc.resolve(xobj_container.get(name))
        if not isinstance(xobj, PDFStream):
            return
        if str(self.doc.resolve(xobj.dict.get("Subtype", ""))) != "Form":
            return  # Image XObjects carry no text of their own.

        raw_matrix = self.doc.resolve(xobj.dict.get("Matrix")) or [1, 0, 0, 1, 0, 0]
        form_matrix = tuple(float(self.doc.resolve(v)) for v in raw_matrix)
        form_resources = self.doc.resolve(xobj.dict.get("Resources"))
        if not isinstance(form_resources, dict):
            form_resources = resources

        saved_gs = self.gs
        self.gs = self.gs.copy()
        self.gs.ctm = mx.multiply(form_matrix, self.gs.ctm)
        self._resources_stack.append(form_resources)
        self._depth += 1
        try:
            data = xobj.get_data(resolver=self.doc.resolve)
            self.run(data)
        finally:
            self._depth -= 1
            self._resources_stack.pop()
            self.gs = saved_gs


_DISPATCH = {
    "q": TextExtractor._op_q,
    "Q": TextExtractor._op_Q,
    "cm": TextExtractor._op_cm,
    "BT": TextExtractor._op_BT,
    "ET": TextExtractor._op_ET,
    "Tf": TextExtractor._op_Tf,
    "Td": TextExtractor._op_Td,
    "TD": TextExtractor._op_TD,
    "T*": TextExtractor._op_Tstar,
    "Tm": TextExtractor._op_Tm,
    "Tc": TextExtractor._op_Tc,
    "Tw": TextExtractor._op_Tw,
    "Tz": TextExtractor._op_Tz,
    "TL": TextExtractor._op_TL,
    "Ts": TextExtractor._op_Ts,
    "Tr": TextExtractor._op_Tr,
    "Tj": TextExtractor._op_Tj,
    "TJ": TextExtractor._op_TJ,
    "'": TextExtractor._op_quote,
    '"': TextExtractor._op_dquote,
    "rg": TextExtractor._op_rg,
    "g": TextExtractor._op_g,
    "k": TextExtractor._op_k,
    "sc": TextExtractor._op_sc,
    "scn": TextExtractor._op_scn,
    "Do": TextExtractor._op_Do,
}


def spans_to_plain_text(spans: list[TextSpan]) -> str:
    out = []
    for i, span in enumerate(spans):
        if i > 0 and span.newline:
            out.append("\n")
        out.append(span.text)
    return "".join(out)


def spans_to_dict(spans: list[TextSpan], width: float, height: float) -> dict:
    lines: list[list[TextSpan]] = []
    current: list[TextSpan] = []
    for span in spans:
        if span.newline and current:
            lines.append(current)
            current = []
        current.append(span)
    if current:
        lines.append(current)

    def span_dict(s: TextSpan) -> dict:
        return {
            "text": s.text,
            "font": s.base_font,
            "size": s.size,
            "flags": s.flags,
            "color": s.color,
            "origin": list(s.origin),
            "bbox": list(s.bbox),
        }

    def line_dict(line_spans: list[TextSpan]) -> dict:
        x0 = min(s.bbox[0] for s in line_spans)
        y0 = min(s.bbox[1] for s in line_spans)
        x1 = max(s.bbox[2] for s in line_spans)
        y1 = max(s.bbox[3] for s in line_spans)
        return {
            "spans": [span_dict(s) for s in line_spans],
            "wmode": 0,
            "dir": [1.0, 0.0],
            "bbox": [x0, y0, x1, y1],
        }

    line_dicts = [line_dict(l) for l in lines]
    blocks = [_block_dict(group) for group in _group_lines_into_blocks(line_dicts)]
    return {"width": width, "height": height, "blocks": blocks}


def _group_lines_into_blocks(line_dicts: list[dict], gap_factor: float = 1.8):
    """Split lines into blocks on a simple paragraph-gap heuristic: a new
    block starts when the vertical gap to the previous line exceeds
    ``gap_factor`` times the taller of the two lines' heights. This is a
    y-position heuristic, not real layout analysis (column detection,
    reading-order inference, etc.) — good enough to separate visually
    distinct paragraphs/headings, not guaranteed to match any particular
    "ground truth" block segmentation.
    """
    if not line_dicts:
        return []
    groups: list[list[dict]] = [[line_dicts[0]]]
    prev_center = _line_center_y(line_dicts[0])
    prev_height = _line_height(line_dicts[0])
    for line in line_dicts[1:]:
        center = _line_center_y(line)
        height = _line_height(line)
        gap = abs(center - prev_center)
        threshold = gap_factor * max(prev_height, height, 1.0)
        if gap > threshold:
            groups.append([])
        groups[-1].append(line)
        prev_center, prev_height = center, height
    return groups


def _line_center_y(line: dict) -> float:
    y0, y1 = line["bbox"][1], line["bbox"][3]
    return (y0 + y1) / 2.0


def _line_height(line: dict) -> float:
    return line["bbox"][3] - line["bbox"][1]


def _block_dict(line_dicts: list[dict]) -> dict:
    return {
        "type": 0,
        "bbox": [
            min(l["bbox"][0] for l in line_dicts),
            min(l["bbox"][1] for l in line_dicts),
            max(l["bbox"][2] for l in line_dicts),
            max(l["bbox"][3] for l in line_dicts),
        ],
        "lines": line_dicts,
    }
