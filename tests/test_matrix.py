import math

from clearleaf import _matrix as mx


def test_identity_multiply_is_identity():
    m = (2.0, 0.0, 0.0, 3.0, 5.0, 7.0)
    assert mx.multiply(mx.IDENTITY, m) == m
    assert mx.multiply(m, mx.IDENTITY) == m


def test_multiply_composes_translation_then_translation():
    t1 = (1.0, 0.0, 0.0, 1.0, 10.0, 0.0)
    t2 = (1.0, 0.0, 0.0, 1.0, 0.0, 5.0)
    combined = mx.multiply(t1, t2)
    assert mx.apply(combined, 0.0, 0.0) == (10.0, 5.0)


def test_multiply_composes_scale_and_translation():
    scale = (2.0, 0.0, 0.0, 2.0, 0.0, 0.0)
    translate = (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)
    # Apply scale first, then translate: point (3,4) -> scaled (6,8) -> +100
    combined = mx.multiply(scale, translate)
    assert mx.apply(combined, 3.0, 4.0) == (106.0, 108.0)


def test_apply_identity():
    assert mx.apply(mx.IDENTITY, 3.0, 4.0) == (3.0, 4.0)


def test_scale_factor_identity_is_one():
    assert math.isclose(mx.scale_factor(mx.IDENTITY), 1.0)


def test_scale_factor_uniform_scale():
    m = (2.0, 0.0, 0.0, 2.0, 0.0, 0.0)
    assert math.isclose(mx.scale_factor(m), 2.0)


def test_invert_identity():
    assert mx.invert(mx.IDENTITY) == mx.IDENTITY


def test_invert_roundtrips_arbitrary_matrix():
    m = (2.0, 0.5, -0.3, 1.5, 10.0, -7.0)
    inv = mx.invert(m)
    x, y = mx.apply(mx.multiply(m, inv), 3.0, 4.0)
    assert math.isclose(x, 3.0, abs_tol=1e-9)
    assert math.isclose(y, 4.0, abs_tol=1e-9)


def test_invert_singular_returns_none():
    singular = (1.0, 2.0, 2.0, 4.0, 0.0, 0.0)  # rows are linearly dependent
    assert mx.invert(singular) is None
