"""Blend modes (ISO 32000-1 section 11.3.5): how a painted colour combines
with the backdrop already on the page before alpha compositing.

The result of painting colour Cs with alpha a over backdrop Cb is
(1 - a) * Cb + a * B(Cb, Cs), with B the mode's blend function -- the
separable modes work channel by channel, the non-separable ones (Hue,
Saturation, Color, Luminosity) on the colour as a whole via its
luminosity and saturation (section 11.3.5.3).
"""

from __future__ import annotations

import math

NORMAL = "Normal"


def _soft_light(cb: float, cs: float) -> float:
    if cs <= 0.5:
        return cb - (1 - 2 * cs) * cb * (1 - cb)
    d = ((16 * cb - 12) * cb + 4) * cb if cb <= 0.25 else math.sqrt(cb)
    return cb + (2 * cs - 1) * (d - cb)


def _hard_light(cb: float, cs: float) -> float:
    return cb * 2 * cs if cs <= 0.5 else 1 - (1 - cb) * (1 - (2 * cs - 1))  # multiply / screen


SEPARABLE = {
    "Multiply": lambda cb, cs: cb * cs,
    "Screen": lambda cb, cs: cb + cs - cb * cs,
    "Overlay": lambda cb, cs: _hard_light(cs, cb),
    "Darken": min,
    "Lighten": max,
    "ColorDodge": lambda cb, cs: 0.0 if cb == 0 else (1.0 if cs >= 1 else min(1.0, cb / (1 - cs))),
    "ColorBurn": lambda cb, cs: 1.0 if cb >= 1 else (0.0 if cs <= 0 else 1 - min(1.0, (1 - cb) / cs)),
    "HardLight": _hard_light,
    "SoftLight": _soft_light,
    "Difference": lambda cb, cs: abs(cb - cs),
    "Exclusion": lambda cb, cs: cb + cs - 2 * cb * cs,
}


def _lum(c):
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def _clip_color(c):
    lum = _lum(c)
    n, x = min(c), max(c)
    if n < 0:
        c = [lum + (v - lum) * lum / (lum - n) if lum != n else lum for v in c]
    if x > 1:
        c = [lum + (v - lum) * (1 - lum) / (x - lum) if x != lum else lum for v in c]
    return c


def _set_lum(c, lum):
    d = lum - _lum(c)
    return _clip_color([v + d for v in c])


def _sat(c):
    return max(c) - min(c)


def _set_sat(c, s):
    order = sorted(range(3), key=lambda i: c[i])
    lo, mid, hi = order
    out = [0.0, 0.0, 0.0]
    if c[hi] > c[lo]:
        out[mid] = (c[mid] - c[lo]) * s / (c[hi] - c[lo])
        out[hi] = s
    return out


NON_SEPARABLE = {
    "Hue": lambda cb, cs: _set_lum(_set_sat(cs, _sat(cb)), _lum(cb)),
    "Saturation": lambda cb, cs: _set_lum(_set_sat(cb, _sat(cs)), _lum(cb)),
    "Color": lambda cb, cs: _set_lum(cs, _lum(cb)),
    "Luminosity": lambda cb, cs: _set_lum(cb, _lum(cs)),
}

SUPPORTED = {NORMAL, "Compatible", *SEPARABLE, *NON_SEPARABLE}


def blend_mode_from(value) -> str:
    """/BM is a name or an array of names (use the first one supported)."""
    names = value if isinstance(value, list) else [value]
    for name in names:
        name = str(name)
        if name in SUPPORTED:
            return NORMAL if name == "Compatible" else name
    return NORMAL


def blend(mode: str, backdrop, source) -> tuple:
    """B(Cb, Cs) for 0-255 RGB triples, as a 0-255 RGB triple."""
    cb = [v / 255.0 for v in backdrop]
    cs = [v / 255.0 for v in source]
    fn = SEPARABLE.get(mode)
    if fn is not None:
        out = [fn(b, s) for b, s in zip(cb, cs)]
    else:
        fn = NON_SEPARABLE.get(mode)
        out = fn(cb, cs) if fn is not None else cs
    return tuple(max(0, min(255, round(v * 255))) for v in out)


def composite(pixels, i: int, rgb, a: float, mode: str) -> None:
    """Paint ``rgb`` with alpha ``a`` over pixels[i:i+3] using ``mode``."""
    if mode != NORMAL:
        rgb = blend(mode, (pixels[i], pixels[i + 1], pixels[i + 2]), rgb)
    if a >= 0.999:
        pixels[i], pixels[i + 1], pixels[i + 2] = rgb
    else:
        inv = 1.0 - a
        pixels[i] = int(rgb[0] * a + pixels[i] * inv)
        pixels[i + 1] = int(rgb[1] * a + pixels[i + 1] * inv)
        pixels[i + 2] = int(rgb[2] * a + pixels[i + 2] * inv)
