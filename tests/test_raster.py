import math

from clearleaf._raster import (
    Canvas,
    ClipMask,
    fill_shape,
    fill_shapes_union,
    flatten_cubic,
    flatten_quadratic,
    stroke_to_shapes,
)


def pixel(canvas, x, y):
    i = (y * canvas.width + x) * 3
    return tuple(canvas.pixels[i : i + 3])


def test_fill_shape_solid_square_exact_coverage():
    canvas = Canvas(10, 10, background=(255, 255, 255))
    square = [[(2, 2), (8, 2), (8, 8), (2, 8)]]
    fill_shape(canvas, square, (0, 0, 0))
    assert pixel(canvas, 5, 5) == (0, 0, 0)  # deep interior: fully black
    assert pixel(canvas, 0, 0) == (255, 255, 255)  # outside: untouched
    assert pixel(canvas, 5, 1) == (255, 255, 255)  # above the square


def test_fill_shape_antialiases_edges():
    canvas = Canvas(10, 10, background=(255, 255, 255))
    # A square with a half-pixel-offset edge should partially blend, not
    # be a hard 0/255 step.
    square = [[(2.5, 2.5), (7.5, 2.5), (7.5, 7.5), (2.5, 7.5)]]
    fill_shape(canvas, square, (0, 0, 0))
    edge_pixel = pixel(canvas, 2, 4)  # straddles the left edge at x=2.5
    assert 0 < edge_pixel[0] < 255


def test_fill_shape_alpha_blends_with_background():
    canvas = Canvas(4, 4, background=(255, 255, 255))
    square = [[(0, 0), (4, 0), (4, 4), (0, 4)]]
    fill_shape(canvas, square, (0, 0, 0), alpha=0.5)
    r, g, b = pixel(canvas, 2, 2)
    assert 120 <= r <= 135  # ~127-128, roughly half-blended


def test_nonzero_rule_fills_overlap_of_same_direction_squares():
    canvas = Canvas(10, 10)
    # Two same-direction (CCW) overlapping squares: nonzero winding fills
    # their union solidly (no cancellation).
    a = [(1, 1), (6, 1), (6, 6), (1, 6)]
    b = [(3, 3), (8, 3), (8, 8), (3, 8)]
    fill_shape(canvas, [a, b], (0, 0, 0), evenodd=False)
    assert pixel(canvas, 4, 4) == (0, 0, 0)  # overlap region
    assert pixel(canvas, 2, 2) == (0, 0, 0)  # a-only region
    assert pixel(canvas, 7, 7) == (0, 0, 0)  # b-only region


def test_evenodd_rule_creates_hole_in_overlap():
    canvas = Canvas(10, 10)
    a = [(1, 1), (6, 1), (6, 6), (1, 6)]
    b = [(3, 3), (8, 3), (8, 8), (3, 8)]
    fill_shape(canvas, [a, b], (0, 0, 0), evenodd=True)
    assert pixel(canvas, 4, 4) == (255, 255, 255)  # overlap: punched out
    assert pixel(canvas, 2, 2) == (0, 0, 0)  # a-only: still filled


def test_glyph_style_hole_via_nonzero_opposite_winding():
    # Outer square wound CCW, inner square (the "hole", like the counter of
    # an 'O') wound CW — standard nonzero-rule glyph convention.
    canvas = Canvas(10, 10)
    outer = [(1, 1), (9, 1), (9, 9), (1, 9)]
    inner_hole = [(4, 4), (4, 6), (6, 6), (6, 4)]  # reversed winding vs outer
    fill_shape(canvas, [outer, inner_hole], (0, 0, 0), evenodd=False)
    assert pixel(canvas, 5, 5) == (255, 255, 255)  # inside the hole
    assert pixel(canvas, 2, 2) == (0, 0, 0)  # solid part of the ring


def test_fill_shapes_union_no_cancellation_between_independent_shapes():
    # Same two overlapping squares as above, but as independent shapes fed
    # to fill_shapes_union with OPPOSITE winding — must still render as a
    # solid union (no hole), unlike a naive combined nonzero fill would if
    # windings disagree.
    canvas = Canvas(10, 10)
    a = [[(1, 1), (6, 1), (6, 6), (1, 6)]]
    b = [[(3, 8), (8, 8), (8, 3), (3, 3)]]  # reversed winding vs `a`
    fill_shapes_union(canvas, [a, b], (0, 0, 0))
    assert pixel(canvas, 4, 4) == (0, 0, 0)  # overlap: must NOT be a hole


def test_clip_mask_restricts_fill():
    canvas = Canvas(10, 10)
    clip = ClipMask.full(10, 10).intersect_shape([[(0, 0), (5, 0), (5, 10), (0, 10)]])
    square = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    fill_shape(canvas, square, (0, 0, 0), clip=clip)
    assert pixel(canvas, 2, 5) == (0, 0, 0)  # inside clip region
    assert pixel(canvas, 8, 5) == (255, 255, 255)  # outside clip region


def test_clip_mask_row_outside_shape_bbox_is_fully_clipped_not_unclipped():
    # Regression: a clip shape that doesn't span the full canvas height
    # leaves some rows entirely absent from the sparse coverage dict —
    # those rows must read as fully clipped (0), not "no restriction"
    # (which is what a bare `None` from ClipMask.full() means).
    canvas = Canvas(10, 10)
    clip = ClipMask.full(10, 10).intersect_shape([[(2, 2), (6, 2), (6, 6), (2, 6)]])
    square = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    fill_shape(canvas, square, (0, 0, 0), clip=clip)
    assert pixel(canvas, 4, 4) == (0, 0, 0)  # inside the clip shape
    assert pixel(canvas, 4, 8) == (255, 255, 255)  # below the clip shape's row range
    assert pixel(canvas, 4, 0) == (255, 255, 255)  # above the clip shape's row range


def test_clip_mask_intersection_narrows_further():
    canvas = Canvas(10, 10)
    clip = ClipMask.full(10, 10)
    clip = clip.intersect_shape([[(0, 0), (7, 0), (7, 10), (0, 10)]])
    clip = clip.intersect_shape([[(3, 0), (10, 0), (10, 10), (3, 10)]])
    square = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    fill_shape(canvas, square, (0, 0, 0), clip=clip)
    assert pixel(canvas, 5, 5) == (0, 0, 0)  # in both clip shapes (3..7)
    assert pixel(canvas, 1, 5) == (255, 255, 255)  # only in first clip shape
    assert pixel(canvas, 9, 5) == (255, 255, 255)  # only in second clip shape


def test_flatten_cubic_endpoints():
    pts = flatten_cubic((0, 0), (0, 10), (10, 10), (10, 0))
    assert pts[0] == (0, 0)
    assert pts[-1] == (10, 0)
    assert len(pts) > 2  # actually subdivided, not just endpoints


def test_flatten_straight_cubic_stays_nearly_collinear():
    pts = flatten_cubic((0, 0), (5, 0), (10, 0), (15, 0))
    assert all(abs(y) < 0.01 for _x, y in pts)


def test_flatten_quadratic_endpoints():
    pts = flatten_quadratic((0, 0), (5, 10), (10, 0))
    assert pts[0] == (0, 0)
    assert pts[-1] == (10, 0)
    assert any(y > 1 for _x, y in pts)  # bulges upward through the control point


def test_stroke_to_shapes_open_path_produces_segment_and_cap_shapes():
    shapes = stroke_to_shapes([[(0, 0), (10, 0)]], [False], line_width=2.0)
    canvas = Canvas(14, 4)
    # Shift into the small canvas.
    shapes = [[[(x + 2, y + 2) for x, y in sub] for sub in shape] for shape in shapes]
    fill_shapes_union(canvas, shapes, (0, 0, 0))
    assert pixel(canvas, 6, 2) == (0, 0, 0)  # middle of the stroked line
    assert pixel(canvas, 6, 0) == (255, 255, 255)  # above the stroke


def test_stroke_to_shapes_closed_path_has_no_cap_gaps():
    square_path = [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    shapes = stroke_to_shapes(square_path, [True], line_width=2.0)
    canvas = Canvas(14, 14)
    shapes = [[[(x + 2, y + 2) for x, y in sub] for sub in shape] for shape in shapes]
    fill_shapes_union(canvas, shapes, (0, 0, 0))
    # All four edge midpoints of the stroked square outline should be
    # painted, including the corner-to-corner joins.
    assert pixel(canvas, 7, 2) == (0, 0, 0)
    assert pixel(canvas, 2, 7) == (0, 0, 0)
    assert pixel(canvas, 12, 7) == (0, 0, 0)
    assert pixel(canvas, 7, 12) == (0, 0, 0)


def _validate_shape_format(shapes):
    """Every element of `shapes` must be a "shape" (a list of subpaths,
    each subpath a list of (x, y) point tuples) — the exact structural
    contract `fill_shapes_union` depends on. A shape that's accidentally a
    bare subpath (missing one level of nesting) blows up when
    `shape_coverage_rows` tries to unpack a coordinate as if it were a
    point, which is exactly the class of bug this guards against."""
    for shape in shapes:
        assert isinstance(shape, list) and shape, "shape must be a non-empty list of subpaths"
        for subpath in shape:
            assert isinstance(subpath, list) and len(subpath) >= 2, "subpath must be a point list"
            for point in subpath:
                assert isinstance(point, tuple) and len(point) == 2
                assert isinstance(point[0], (int, float)) and isinstance(point[1], (int, float))


def _shift(shapes, dx, dy):
    return [[[(x + dx, y + dy) for x, y in sub] for sub in shape] for shape in shapes]


def test_stroke_to_shapes_all_join_and_cap_styles_produce_well_formed_shapes():
    # Regression: miter/bevel joins and square caps once returned shapes
    # missing a level of list nesting, which raised deep inside
    # fill_shapes_union — silently swallowed by the painter's top-level
    # exception handler, so a stroke would just vanish with no error.
    right_angle = [[(0, 0), (10, 0), (10, 10)]]
    for join in ("miter", "round", "bevel"):
        for cap in ("butt", "round", "square"):
            for closed in (False, True):
                shapes = stroke_to_shapes(
                    right_angle, [closed], line_width=2.0, cap_style=cap, join_style=join
                )
                _validate_shape_format(shapes)


def test_butt_cap_does_not_extend_past_endpoint():
    # Endpoint at x=10, shifted to x=15: pixel column 14 (spanning x in
    # [14,15)) is the last one fully inside the stroke; column 15 itself
    # (spanning [15,16)) is right at the cut-off edge, so it's outside.
    shapes = stroke_to_shapes([[(0, 0), (10, 0)]], [False], line_width=4.0, cap_style="butt")
    canvas = Canvas(20, 10)
    fill_shapes_union(canvas, _shift(shapes, 5, 5), (0, 0, 0))
    assert pixel(canvas, 14, 5) == (0, 0, 0)  # just inside the endpoint: painted
    assert pixel(canvas, 18, 5) == (255, 255, 255)  # well past it: not painted


def test_square_cap_extends_past_endpoint_by_half_width():
    shapes = stroke_to_shapes([[(0, 0), (10, 0)]], [False], line_width=4.0, cap_style="square")
    canvas = Canvas(20, 10)
    fill_shapes_union(canvas, _shift(shapes, 5, 5), (0, 0, 0))
    assert pixel(canvas, 16, 5) == (0, 0, 0)  # 1px past the endpoint: still painted (half-width=2)
    assert pixel(canvas, 18, 5) == (255, 255, 255)  # past the extension: not painted


def test_miter_join_reaches_sharper_apex_than_bevel():
    # Isolate just the join geometry (not full segment quads, whose far
    # endpoints would otherwise dominate any "distance from corner"
    # measurement) via the internal per-joint helper directly.
    from clearleaf._raster import _join_shapes

    prev_pt, cur_pt, next_pt = (0.0, 10.0), (0.0, 0.0), (10.0, 0.0)
    half = 3.0
    miter_shapes = _join_shapes(prev_pt, cur_pt, next_pt, half, "miter", miter_limit=10.0)
    bevel_shapes = _join_shapes(prev_pt, cur_pt, next_pt, half, "bevel", miter_limit=10.0)

    def max_dist_from_corner(shapes):
        pts = [pt for shape in shapes for sub in shape for pt in sub]
        return max(((x - cur_pt[0]) ** 2 + (y - cur_pt[1]) ** 2) ** 0.5 for x, y in pts)

    assert max_dist_from_corner(miter_shapes) > max_dist_from_corner(bevel_shapes)


def test_miter_join_falls_back_to_bevel_past_miter_limit():
    # A very sharp (near-180-degree-reversal) corner has an enormous
    # miter length — must fall back to a bounded bevel shape instead of
    # an unbounded spike.
    sharp = [[(10, 0.2), (0, 0), (10, -0.2)]]
    shapes = stroke_to_shapes(sharp, [False], line_width=2.0, join_style="miter", miter_limit=4.0)
    _validate_shape_format(shapes)
    pts = [pt for shape in shapes for sub in shape for pt in sub]
    max_dist = max((x ** 2 + y ** 2) ** 0.5 for x, y in pts)
    assert max_dist < 20.0  # bounded, not the unbounded near-parallel miter spike


def test_degenerate_zero_length_segment_returns_no_shape_not_malformed_data():
    # Regression: a closed path whose start point duplicates its end point
    # (only detectable at the wraparound, since adjacent-point dedup can't
    # see it) used to return a bare point `[p0]` instead of an empty shape
    # list — the same "missing a nesting level" bug class as the
    # miter/bevel/square-cap one, just pre-existing and untriggered before
    # non-round joins/caps started exercising unusual paths.
    square_with_dup_close = [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]]
    shapes = stroke_to_shapes(square_with_dup_close, [True], line_width=2.0, join_style="miter")
    _validate_shape_format(shapes)


def test_ctm_linear_none_matches_pre_anisotropic_behavior():
    # Without ctm_linear, stroke_to_shapes must behave exactly as it did
    # before anisotropic support was added: line_width is the direct
    # device-space width.
    shapes_a = stroke_to_shapes([[(0, 0), (10, 0)]], [False], line_width=4.0)
    shapes_b = stroke_to_shapes([[(0, 0), (10, 0)]], [False], line_width=4.0, ctm_linear=None)
    assert shapes_a == shapes_b


def test_anisotropic_ctm_produces_elliptical_pen_widths():
    # Under CTM (2,0,0,1) (2x horizontal, 1x vertical scale, already
    # "applied" to the segment endpoints as if by the painter), a
    # horizontal segment's stroke should come out *narrower* in device Y
    # than a vertical segment's stroke comes out in device X, because a
    # nominally-circular pen becomes a horizontal ellipse under that CTM.
    ctm_linear = (2.0, 0.0, 0.0, 1.0)
    user_half_width = 2.0

    # Horizontal segment (already in "device space" consistent with ctm_linear).
    h_shapes = stroke_to_shapes(
        [[(0.0, 0.0), (20.0, 0.0)]], [False], line_width=user_half_width * 2,
        cap_style="butt", ctm_linear=ctm_linear,
    )
    h_ys = [y for shape in h_shapes for sub in shape for _x, y in sub]
    h_half_height = (max(h_ys) - min(h_ys)) / 2.0

    # Vertical segment.
    v_shapes = stroke_to_shapes(
        [[(0.0, 0.0), (0.0, 20.0)]], [False], line_width=user_half_width * 2,
        cap_style="butt", ctm_linear=ctm_linear,
    )
    v_xs = [x for shape in v_shapes for sub in shape for x, _y in sub]
    v_half_width = (max(v_xs) - min(v_xs)) / 2.0

    # Horizontal segment's perpendicular (Y) is scaled by the CTM's Y
    # factor (1.0); vertical segment's perpendicular (X) is scaled by the
    # CTM's X factor (2.0) — so the vertical stroke should render visibly
    # wider in device space than the horizontal one renders tall.
    assert v_half_width > h_half_height
    assert math.isclose(h_half_height, user_half_width * 1.0, rel_tol=0.05)
    assert math.isclose(v_half_width, user_half_width * 2.0, rel_tol=0.05)


def test_anisotropic_offset_matches_uniform_case_for_identity_ctm():
    from clearleaf._raster import _anisotropic_offset

    nx, ny = _anisotropic_offset((1.0, 0.0), 3.0, (1.0, 0.0, 0.0, 1.0))
    assert math.isclose(nx, 0.0, abs_tol=1e-9)
    assert math.isclose(abs(ny), 3.0, abs_tol=1e-9)
