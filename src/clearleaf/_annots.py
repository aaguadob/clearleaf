"""Annotation appearance streams (ISO 32000-1 section 12.5.5).

A viewer draws each visible annotation by painting its normal appearance
(/AP /N, a Form XObject -- or, for annotations with states such as check
boxes, the entry of /AP /N selected by /AS) into the annotation's /Rect.
Both the renderer and text extraction use the same list, so a filled-in
form field's value shows up in get_text() as it does on screen.

When the form sets /NeedAppearances true, the file's appearance streams
are stale placeholders and the viewer must build them from the field
values (section 12.7.3.3, variable text): done here for text fields
(single/multi-line, password, left/centre/right quadding), combo and list
boxes, push-button captions and check boxes / radio buttons.
"""

from __future__ import annotations

import re

from . import _matrix as mx
from ._objects import PDFStream

HIDDEN, NO_VIEW = 1 << 1, 1 << 5  # annotation flags (Table 165)


def _numbers(doc, value, count: int):
    value = doc.resolve(value)
    if not isinstance(value, list) or len(value) != count:
        return None
    try:
        return [float(doc.resolve(v)) for v in value]
    except (TypeError, ValueError):
        return None


def _inherited(doc, field: dict, key: str):
    """A field attribute, following /Parent (section 12.7.3.1)."""
    seen = 0
    while isinstance(field, dict) and seen < 32:
        if key in field:
            return doc.resolve(field[key])
        field = doc.resolve(field.get("Parent"))
        seen += 1
    return None


def decode_text_string(value) -> str:
    """A PDF text string (section 7.9.2.2): UTF-16BE or UTF-8 with a byte
    order mark, else PDFDocEncoding (approximated by Latin-1)."""
    if not isinstance(value, (bytes, bytearray)):
        return str(value) if value is not None else ""
    data = bytes(value)
    if data[:2] == b"\xfe\xff":
        return data[2:].decode("utf-16-be", "replace")
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    return data.decode("latin-1")


_DA_FONT = re.compile(rb"/([^\s/<>\[\]()]+)\s+([-+\d.]+)\s+Tf")
# field flags (Tables 226, 228, 230)
MULTILINE, PASSWORD, PUSHBUTTON, COMBO = 1 << 12, 1 << 13, 1 << 16, 1 << 17


def _field_lines(doc, annot: dict, kind: str, flags: int):
    """The lines of text a regenerated appearance shows, and whether they
    start at the top of the box (True) or are centred vertically."""
    if kind == "Btn":
        if flags & PUSHBUTTON:  # the caption
            mk = doc.resolve(annot.get("MK"))
            caption = decode_text_string(doc.resolve(mk.get("CA"))) if isinstance(mk, dict) else ""
            return [caption], False
        return [], False  # check boxes / radios: see _check_appearance
    value = _inherited(doc, annot, "V")
    if kind == "Ch" and not flags & COMBO:  # list box: every option, top down
        options = doc.resolve(_inherited(doc, annot, "Opt"))
        lines = []
        for opt in options if isinstance(options, list) else []:
            opt = doc.resolve(opt)
            if isinstance(opt, list) and opt:  # [export value, display text]
                opt = doc.resolve(opt[-1])
            lines.append(decode_text_string(opt))
        return lines, True
    if isinstance(value, list):  # multi-select: first selection
        value = doc.resolve(value[0]) if value else None
    text = decode_text_string(value)
    if kind == "Ch":  # show an option's display text, not its export value
        options = doc.resolve(_inherited(doc, annot, "Opt"))
        for opt in options if isinstance(options, list) else []:
            opt = doc.resolve(opt)
            if isinstance(opt, list) and len(opt) >= 2 and decode_text_string(doc.resolve(opt[0])) == text:
                text = decode_text_string(doc.resolve(opt[1]))
                break
    if kind == "Tx" and flags & PASSWORD:
        text = "*" * len(text)  # shall not be displayed in readable form
    if kind == "Tx" and flags & MULTILINE:
        return text.splitlines(), True
    return [" ".join(text.splitlines())], False


def field_appearance(doc, annot: dict, acroform: dict):
    """A regenerated appearance stream for a text field, choice field or
    push button, built from its value (or caption); None if nothing shows.
    Check boxes and radio buttons that are on get their check glyph."""
    kind = str(_inherited(doc, annot, "FT") or "")
    rect = _numbers(doc, annot.get("Rect"), 4)
    if kind not in ("Tx", "Ch", "Btn") or rect is None:
        return None
    flags = int(_inherited(doc, annot, "Ff") or 0)
    width, height = abs(rect[2] - rect[0]), abs(rect[3] - rect[1])
    resources = _inherited(doc, annot, "DR") or doc.resolve(acroform.get("DR"))
    da = _inherited(doc, annot, "DA") or doc.resolve(acroform.get("DA")) or b""
    da = bytes(da) if isinstance(da, (bytes, bytearray)) else b""

    if kind == "Btn" and not flags & PUSHBUTTON:
        return _check_appearance(doc, annot, da, width, height)
    lines, from_top = _field_lines(doc, annot, kind, flags)
    if not any(line.strip() for line in lines):
        return None

    match = _DA_FONT.search(da)
    size = float(match.group(2)) if match else 0.0
    if size <= 0:  # auto size: fit the height, as viewers do
        size = max(4.0, min(12.0, (height - 4) * 0.75))
    quadding = int(_inherited(doc, annot, "Q") or doc.resolve(acroform.get("Q")) or 0)
    if kind == "Btn":
        quadding = 1  # captions are centred
    font_name = match.group(1).decode("latin-1") if match else "Helv"
    fonts = doc.resolve(resources.get("Font")) if isinstance(resources, dict) else None
    font_dict = doc.resolve(fonts.get(font_name)) if isinstance(fonts, dict) else None
    font_dict = _winansi_font(doc, font_dict)
    resources = {"Font": {font_name: font_dict}}
    if not match:
        da = b"/Helv %g Tf " % size + da

    leading = size * 1.15
    y = height - 2 - size * 0.9 if from_top else (height - size * 0.72) / 2
    ops = [b"/Tx BMC q BT", da]
    for i, line in enumerate(lines):
        encoded = line.encode("cp1252", "replace")
        text_width = _string_width(doc, font_dict, encoded, size)
        x = 2.0
        if quadding == 1:
            x = (width - text_width) / 2
        elif quadding == 2:
            x = width - 2 - text_width
        ops.append(b"1 0 0 1 %.2f %.2f Tm <%s> Tj" % (x, y - i * leading, encoded.hex().encode()))
    ops.append(b"ET Q EMC")
    return _form(b"\n".join(ops), width, height, resources)


_ZAPF = {"Type": "Font", "Subtype": "Type1", "BaseFont": "ZapfDingbats"}


def _check_appearance(doc, annot: dict, da: bytes, width: float, height: float):
    state = doc.resolve(annot.get("AS"))
    if state is None or str(state) == "Off":
        return None
    mk = doc.resolve(annot.get("MK"))
    glyph = doc.resolve(mk.get("CA")) if isinstance(mk, dict) else None
    glyph = bytes(glyph)[:1] if isinstance(glyph, (bytes, bytearray)) and glyph else b"4"  # a20, the check mark
    size = min(width, height) * 0.8
    color = re.sub(_DA_FONT, b"", da)
    content = b"q BT %s /ZaDb %.2f Tf %.2f %.2f Td <%s> Tj ET Q" % (
        color, size, (width - size * 0.75) / 2, (height - size * 0.7) / 2, glyph.hex().encode())
    return _form(content, width, height, {"Font": {"ZaDb": _ZAPF}})


def _form(content: bytes, width: float, height: float, resources) -> PDFStream:
    stream = PDFStream({"Type": "XObject", "Subtype": "Form", "BBox": [0, 0, width, height]}, content)
    if isinstance(resources, dict):
        stream.dict["Resources"] = resources
    return stream


def _winansi_font(doc, font_dict):
    """The font the regenerated text is set in. Field values are written
    in WinAnsiEncoding, so a simple font without an /Encoding of its own
    gets that one; no font at all falls back to Helvetica."""
    if not isinstance(font_dict, dict):
        return {"Type": "Font", "Subtype": "Type1", "BaseFont": "Helvetica", "Encoding": "WinAnsiEncoding"}
    if "Encoding" not in font_dict and str(doc.resolve(font_dict.get("Subtype", ""))) in ("Type1", "TrueType"):
        return {**font_dict, "Encoding": "WinAnsiEncoding"}
    return font_dict


def _string_width(doc, font_dict, encoded: bytes, size: float) -> float:
    from ._fonts import load_font

    try:
        return sum(width for *_, width in load_font(doc, font_dict).decode(encoded)) * size / 1000.0
    except Exception:
        return 0.5 * size * len(encoded)


def appearance_matrix(bbox, form_matrix, rect) -> tuple:
    """Matrix A of section 12.5.5: maps the appearance's BBox, transformed
    by its Matrix, onto the annotation's Rect. The form is then painted
    with Matrix x A as its transformation."""
    corners = [mx.apply(form_matrix, x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
    bx0 = min(p[0] for p in corners)
    by0 = min(p[1] for p in corners)
    bx1 = max(p[0] for p in corners)
    by1 = max(p[1] for p in corners)
    rx0, rx1 = sorted((rect[0], rect[2]))
    ry0, ry1 = sorted((rect[1], rect[3]))
    sx = (rx1 - rx0) / (bx1 - bx0) if bx1 > bx0 else 1.0
    sy = (ry1 - ry0) / (by1 - by0) if by1 > by0 else 1.0
    return (sx, 0.0, 0.0, sy, rx0 - bx0 * sx, ry0 - by0 * sy)


def annotation_appearances(doc, page_dict: dict) -> list:
    """(appearance Form XObject, matrix A) for every annotation a viewer
    shows on the page, in /Annots order."""
    out = []
    annots = doc.resolve(page_dict.get("Annots"))
    if not isinstance(annots, list):
        return out
    acroform = doc.resolve(doc.root.get("AcroForm"))
    acroform = acroform if isinstance(acroform, dict) else {}
    need_appearances = doc.resolve(acroform.get("NeedAppearances")) is True
    for ref in annots:
        annot = doc.resolve(ref)
        if not isinstance(annot, dict):
            continue
        try:
            flags = int(doc.resolve(annot.get("F", 0)) or 0)
        except (TypeError, ValueError):
            flags = 0
        if flags & (HIDDEN | NO_VIEW):
            continue
        if str(doc.resolve(annot.get("Subtype", ""))) == "Popup":
            continue  # popups are only shown when the user opens them
        ap = doc.resolve(annot.get("AP"))
        normal = doc.resolve(ap.get("N")) if isinstance(ap, dict) else None
        if isinstance(normal, dict):  # appearance states: pick /AS
            state = doc.resolve(annot.get("AS"))
            normal = doc.resolve(normal.get(str(state))) if state is not None else None
        if str(doc.resolve(annot.get("Subtype", ""))) == "Widget" and (need_appearances or normal is None):
            # The viewer builds the appearance from the field's value; the
            # stored one (if any) is only a placeholder.
            normal = field_appearance(doc, annot, acroform)
        if not isinstance(normal, PDFStream):
            continue
        rect = _numbers(doc, annot.get("Rect"), 4)
        bbox = _numbers(doc, normal.dict.get("BBox"), 4)
        if rect is None or bbox is None:
            continue
        form_matrix = _numbers(doc, normal.dict.get("Matrix"), 6) or [1, 0, 0, 1, 0, 0]
        out.append((normal, appearance_matrix(bbox, tuple(form_matrix), rect)))
    return out
