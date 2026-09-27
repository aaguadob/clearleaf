"""Color space resolution to RGB (ISO 32000-1:2008 section 8.6).

Covers DeviceGray/RGB/CMYK, CalGray/CalRGB (approximated as their Device
equivalents — gamma/matrix/white-point correction not applied), Lab (a real
CIE Lab -> linear-sRGB conversion), ICCBased (approximated via its /N
component count or /Alternate, not the actual ICC profile), Indexed, and
Separation/DeviceN (via `_function.py`'s tint-transform evaluation).

Pattern color spaces are a known gap: painting a pattern-filled area
requires either re-executing a tiling pattern's own content stream per
tile or evaluating a shading's gradient function per pixel — both
substantial features on their own. For now, pattern fills render as a flat
mid-gray placeholder rather than not rendering at all.
"""

from __future__ import annotations

from ._function import load_function
from ._objects import PDFStream


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


class _DeviceGray:
    num_components = 1

    def default_components(self):
        return [0.0]

    def to_rgb(self, c):
        v = round(_clamp01(c[0]) * 255)
        return (v, v, v)


class _DeviceRGB:
    num_components = 3

    def default_components(self):
        return [0.0, 0.0, 0.0]

    def to_rgb(self, c):
        r, g, b = (_clamp01(x) for x in c[:3])
        return (round(r * 255), round(g * 255), round(b * 255))


class _DeviceCMYK:
    num_components = 4

    def default_components(self):
        return [0.0, 0.0, 0.0, 1.0]

    def to_rgb(self, c):
        cc, mm, yy, kk = (_clamp01(x) for x in c[:4])
        r, g, b = (1 - cc) * (1 - kk), (1 - mm) * (1 - kk), (1 - yy) * (1 - kk)
        return (round(r * 255), round(g * 255), round(b * 255))


DEVICE_GRAY = _DeviceGray()
DEVICE_RGB = _DeviceRGB()
DEVICE_CMYK = _DeviceCMYK()


class LabColorSpace:
    num_components = 3

    def __init__(self, doc, params: dict):
        wp = doc.resolve(params.get("WhitePoint")) if isinstance(params, dict) else None
        self.white = [float(doc.resolve(x)) for x in wp] if wp else [0.9505, 1.0, 1.089]

    def default_components(self):
        return [0.0, 0.0, 0.0]

    def to_rgb(self, c):
        L, a, b = c[0], c[1], c[2]
        fy = (L + 16) / 116.0
        fx = fy + a / 500.0
        fz = fy - b / 200.0

        def finv(t):
            return t ** 3 if t ** 3 > 0.008856 else (t - 16.0 / 116.0) / 7.787

        xw, yw, zw = self.white
        x, y, z = xw * finv(fx), yw * finv(fy), zw * finv(fz)

        r = 3.2406 * x - 1.5372 * y - 0.4986 * z
        g = -0.9689 * x + 1.8758 * y + 0.0415 * z
        bl = 0.0557 * x - 0.2040 * y + 1.0570 * z

        def gamma(u):
            u = _clamp01(u)
            return 1.055 * (u ** (1 / 2.4)) - 0.055 if u > 0.0031308 else 12.92 * u

        r, g, bl = gamma(r), gamma(g), gamma(bl)
        return (round(_clamp01(r) * 255), round(_clamp01(g) * 255), round(_clamp01(bl) * 255))


class IndexedColorSpace:
    num_components = 1

    def __init__(self, base, hival: int, table: bytes):
        self.base = base
        self.hival = hival
        self.table = table

    def default_components(self):
        return [0]

    def to_rgb(self, c):
        idx = max(0, min(self.hival, int(round(c[0]))))
        n = self.base.num_components
        offset = idx * n
        comps = [
            (self.table[offset + i] / 255.0) if offset + i < len(self.table) else 0.0
            for i in range(n)
        ]
        return self.base.to_rgb(comps)


class SeparationColorSpace:
    def __init__(self, n_in: int, alternate, func):
        self.num_components = n_in
        self.alternate = alternate
        self.func = func

    def default_components(self):
        return [1.0] * self.num_components

    def to_rgb(self, c):
        try:
            alt_comps = self.func.eval(list(c[: self.num_components]))
        except Exception:
            gray = 1.0 - (sum(c) / max(len(c), 1))
            alt_comps = [gray] * self.alternate.num_components
        return self.alternate.to_rgb(alt_comps)


class PatternColorSpace:
    """See module docstring: patterns aren't rendered — a flat placeholder
    color stands in for whatever the pattern would have painted."""

    num_components = 1

    def __init__(self, base=None, placeholder=(128, 128, 128)):
        self.base = base
        self.placeholder = placeholder

    def default_components(self):
        return [0.0]

    def to_rgb(self, c):
        return self.placeholder


def load_colorspace(doc, obj, resources=None):
    obj = doc.resolve(obj)

    if isinstance(obj, str):
        name = str(obj)
        if name in ("DeviceGray", "G", "CalGray"):
            return DEVICE_GRAY
        if name in ("DeviceRGB", "RGB"):
            return DEVICE_RGB
        if name in ("DeviceCMYK", "CMYK"):
            return DEVICE_CMYK
        if name == "Pattern":
            return PatternColorSpace()
        if isinstance(resources, dict):
            cs_dict = doc.resolve(resources.get("ColorSpace"))
            if isinstance(cs_dict, dict) and name in cs_dict:
                return load_colorspace(doc, cs_dict[name], resources)
        return DEVICE_GRAY  # unrecognized name: safe fallback

    if isinstance(obj, list) and obj:
        family = str(doc.resolve(obj[0]))
        if family == "CalGray":
            return DEVICE_GRAY
        if family == "CalRGB":
            return DEVICE_RGB
        if family == "Lab":
            params = doc.resolve(obj[1]) if len(obj) > 1 else {}
            return LabColorSpace(doc, params if isinstance(params, dict) else {})
        if family == "ICCBased":
            stream = doc.resolve(obj[1]) if len(obj) > 1 else None
            n, alternate = 3, None
            if isinstance(stream, PDFStream):
                n = int(doc.resolve(stream.dict.get("N", 3)) or 3)
                alt = stream.dict.get("Alternate")
                if alt is not None:
                    alternate = load_colorspace(doc, alt, resources)
            if alternate is not None:
                return alternate
            return {1: DEVICE_GRAY, 3: DEVICE_RGB, 4: DEVICE_CMYK}.get(n, DEVICE_RGB)
        if family == "Indexed" and len(obj) >= 4:
            base = load_colorspace(doc, obj[1], resources)
            hival = int(doc.resolve(obj[2]))
            lookup = doc.resolve(obj[3])
            if isinstance(lookup, PDFStream):
                table = lookup.get_data(resolver=doc.resolve)
            elif isinstance(lookup, (bytes, bytearray)):
                table = bytes(lookup)
            else:
                table = b""
            return IndexedColorSpace(base, hival, table)
        if family in ("Separation", "DeviceN") and len(obj) >= 4:
            names = doc.resolve(obj[1])
            n_in = 1 if family == "Separation" else len(names)
            alternate = load_colorspace(doc, obj[2], resources)
            func = load_function(doc, obj[3])
            return SeparationColorSpace(n_in, alternate, func)
        if family == "Pattern":
            base = load_colorspace(doc, obj[1], resources) if len(obj) > 1 else None
            return PatternColorSpace(base)

    return DEVICE_GRAY
