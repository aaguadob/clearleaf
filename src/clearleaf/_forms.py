"""Filling in forms (AcroForm, ISO 32000-1 section 12.7).

``page.widgets()`` lists the form fields on a page; set ``field_value``
and call ``update()`` to store a value. Text and choice fields get a new
appearance stream built from the value, and check boxes and radio buttons
switch appearance state, so every viewer shows the filled-in form.
"""

from __future__ import annotations

from .errors import PDFError, public
from ._annots import COMBO, PUSHBUTTON, _inherited, _numbers, decode_text_string, field_appearance
from ._edit import text_string
from ._objects import PDFStream

READ_ONLY = 1
RADIO = 1 << 15


class Widget:
    """One form field widget (the part of a field shown on a page)."""

    def __init__(self, page, ref, annot: dict):
        self.page = page
        self._ref = ref
        self.dict = annot
        self._value = _UNSET

    # -- the field ------------------------------------------------------------------

    @property
    def _field(self) -> dict:
        """The terminal field: the widget itself when the two are merged."""
        if "T" in self.dict or "FT" in self.dict:
            return self.dict
        parent = self.page._doc.resolve(self.dict.get("Parent"))
        return parent if isinstance(parent, dict) else self.dict

    def _get(self, key):
        return _inherited(self.page._doc, self.dict, key)

    @property
    def field_name(self) -> str:
        parts, node, depth = [], self._field, 0
        while isinstance(node, dict) and depth < 32:
            if "T" in node:
                parts.append(decode_text_string(self.page._doc.resolve(node["T"])))
            node = self.page._doc.resolve(node.get("Parent"))
            depth += 1
        return ".".join(reversed(parts))

    @property
    def field_flags(self) -> int:
        return int(self._get("Ff") or 0)

    @property
    def read_only(self) -> bool:
        return bool(self.field_flags & READ_ONLY)

    @property
    def field_type(self) -> str:
        kind, flags = str(self._get("FT") or ""), self.field_flags
        if kind == "Tx":
            return "text"
        if kind == "Btn":
            return "button" if flags & PUSHBUTTON else ("radiobutton" if flags & RADIO else "checkbox")
        if kind == "Ch":
            return "combobox" if flags & COMBO else "listbox"
        if kind == "Sig":
            return "signature"
        return "unknown"

    @property
    def multiline(self) -> bool:
        return self.field_type == "text" and bool(self.field_flags & (1 << 12))

    @property
    def rect(self) -> tuple:
        r = _numbers(self.page._doc, self.dict.get("Rect"), 4)
        return self.page._rect_from_pdf(r) if r else (0.0, 0.0, 0.0, 0.0)

    @property
    def xref(self) -> int:
        return getattr(self._ref, "num", 0)

    def _states(self) -> list:
        ap = self.page._doc.resolve(self.dict.get("AP"))
        normal = self.page._doc.resolve(ap.get("N")) if isinstance(ap, dict) else None
        return [str(k) for k in normal] if isinstance(normal, dict) else []

    @property
    def on_state(self):
        """The appearance state that means 'on' for a check box / radio button."""
        states = [s for s in self._states() if s != "Off"]
        return states[0] if states else ("Yes" if self.field_type == "checkbox" else None)

    @property
    def choice_values(self) -> list:
        """(export value, display text) for each option of a choice field."""
        out = []
        options = self.page._doc.resolve(self._get("Opt"))
        for opt in options if isinstance(options, list) else []:
            opt = self.page._doc.resolve(opt)
            if isinstance(opt, list) and len(opt) >= 2:
                out.append((decode_text_string(self.page._doc.resolve(opt[0])),
                            decode_text_string(self.page._doc.resolve(opt[1]))))
            else:
                text = decode_text_string(opt)
                out.append((text, text))
        return out

    # -- the value ------------------------------------------------------------------

    @property
    def field_value(self):
        if self._value is not _UNSET:
            return self._value
        doc = self.page._doc
        kind = self.field_type
        if kind == "checkbox":
            state = doc.resolve(self.dict.get("AS"))
            return state is not None and str(state) != "Off"
        value = self._get("V")
        if kind == "radiobutton":
            state = doc.resolve(self.dict.get("AS"))
            return str(state) if state is not None and str(state) != "Off" else ""
        if isinstance(value, list):
            return [decode_text_string(doc.resolve(v)) for v in value]
        if value is None:
            return ""
        return str(value) if not isinstance(value, (bytes, bytearray)) else decode_text_string(value)

    @field_value.setter
    def field_value(self, value) -> None:
        self._value = value

    @public
    def update(self) -> None:
        """Store ``field_value`` in the document and redraw the field."""
        if self._value is _UNSET:
            return
        if self.read_only:
            raise PDFError(f"field {self.field_name!r} is read-only")
        doc = self.page._doc
        kind, value, field = self.field_type, self._value, self._field
        if kind == "checkbox":
            state = self.on_state if value not in (False, None, "", "Off") else "Off"
            field["V"] = state
            for w in self._siblings():
                w["AS"] = state if state in _state_names(doc, w) else "Off"
        elif kind == "radiobutton":
            group = field
            state = self.on_state if value is True else (str(value) if value else "Off")
            group["V"] = state
            for w in self._siblings(group):
                w["AS"] = state if state in _state_names(doc, w) else "Off"
        elif kind in ("text", "combobox", "listbox"):
            if kind != "text":
                options = self.choice_values
                exports = {e for e, _ in options} | {d for _, d in options}
                values = value if isinstance(value, list) else [value]
                if options and any(v not in exports for v in values if v) and not self.field_flags & (1 << 18):
                    raise PDFError(f"{value!r} is not an option of {self.field_name!r}")
                display_to_export = {d: e for e, d in options}
                values = [display_to_export.get(v, v) for v in values]
                field["V"] = [text_string(v) for v in values] if isinstance(value, list) else text_string(values[0])
            else:
                field["V"] = text_string(str(value))
            acroform = doc.resolve(doc.root.get("AcroForm"))
            acroform = acroform if isinstance(acroform, dict) else {}
            for w in self._siblings():
                ap = field_appearance(doc, w, acroform)
                if ap is None:
                    rect = _numbers(doc, w.get("Rect"), 4) or [0, 0, 0, 0]
                    ap = PDFStream({"Type": "XObject", "Subtype": "Form",
                                    "BBox": [0, 0, abs(rect[2] - rect[0]), abs(rect[3] - rect[1])]}, b"")
                w["AP"] = {"N": doc._new_ref(ap)}
                w.pop("AS", None)
        else:
            raise PDFError(f"a {kind} field can't be filled in")
        doc.is_dirty = True
        self._value = _UNSET

    def _siblings(self, field=None) -> list:
        """Every widget of this field (or of a radio group)."""
        doc = self.page._doc
        field = field or self._field
        kids = doc.resolve(field.get("Kids"))
        if isinstance(kids, list):
            return [k for k in (doc.resolve(r) for r in kids) if isinstance(k, dict)]
        return [field]

    def __repr__(self) -> str:
        return f"Widget({self.field_type} {self.field_name!r} = {self.field_value!r})"


_UNSET = object()


def _state_names(doc, widget: dict) -> list:
    ap = doc.resolve(widget.get("AP"))
    normal = doc.resolve(ap.get("N")) if isinstance(ap, dict) else None
    return [str(k) for k in normal] if isinstance(normal, dict) else []


class PageForms:
    """Mixed into Page."""

    def widgets(self) -> list:
        out = []
        annots = self._doc.resolve(self.dict.get("Annots"))
        for ref in annots if isinstance(annots, list) else []:
            annot = self._doc.resolve(ref)
            if isinstance(annot, dict) and str(self._doc.resolve(annot.get("Subtype", ""))) == "Widget":
                out.append(Widget(self, ref, annot))
        return out
