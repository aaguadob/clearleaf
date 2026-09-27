"""2D affine transforms (ISO 32000-1:2008 section 8.3.4).

A PDF matrix is 6 numbers ``[a b c d e f]`` representing:

    | a b 0 |
    | c d 0 |
    | e f 1 |

applied to a row vector ``[x y 1]`` on the left: ``x' = a*x + c*y + e``,
``y' = b*x + d*y + f``. Used for the CTM stack (q/Q/cm) and the text
matrices (Tm/Td/TD/T*).
"""

from __future__ import annotations

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(m1, m2):
    """Return ``m1 * m2`` (apply m1 first, then m2 — matches PDF's `cm`
    semantics where the new matrix is prepended: `m' = m1 x m2`)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m, x: float, y: float):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def invert(m):
    """Inverse of an affine matrix, or None if singular (degenerate CTM —
    e.g. a zero-area image transform)."""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    ie = -(e * ia + f * ic)
    if_ = -(e * ib + f * id_)
    return (ia, ib, ic, id_, ie, if_)


def scale_factor(m) -> float:
    """Approximate uniform scale of a matrix (geometric mean of the two
    basis-vector lengths) — used to turn text-space font size into an
    approximate device-space size for bbox/size reporting."""
    a, b, c, d, _e, _f = m
    sx = (a * a + b * b) ** 0.5
    sy = (c * c + d * d) ** 0.5
    return (sx * sy) ** 0.5
