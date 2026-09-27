from clearleaf._function import eval_ps, load_function, parse_ps_program
from clearleaf._objects import PDFStream


class FakeDoc:
    def resolve(self, obj):
        return obj


def test_exponential_function_linear():
    d = {"FunctionType": 2, "Domain": [0, 1], "C0": [0.0], "C1": [1.0], "N": 1}
    fn = load_function(FakeDoc(), d)
    assert fn.eval([0.0]) == [0.0]
    assert fn.eval([1.0]) == [1.0]
    assert abs(fn.eval([0.5])[0] - 0.5) < 1e-9


def test_exponential_function_multi_output():
    d = {"FunctionType": 2, "Domain": [0, 1], "C0": [0.0, 1.0], "C1": [1.0, 0.0], "N": 1}
    fn = load_function(FakeDoc(), d)
    r0, r1 = fn.eval([0.25])
    assert abs(r0 - 0.25) < 1e-9
    assert abs(r1 - 0.75) < 1e-9


def test_exponential_function_clips_to_domain():
    d = {"FunctionType": 2, "Domain": [0, 1], "C0": [0.0], "C1": [1.0], "N": 1}
    fn = load_function(FakeDoc(), d)
    assert fn.eval([-5.0]) == [0.0]
    assert fn.eval([5.0]) == [1.0]


def test_stitching_function_routes_to_correct_subfunction():
    f0 = {"FunctionType": 2, "Domain": [0, 1], "C0": [0.0], "C1": [1.0], "N": 1}
    f1 = {"FunctionType": 2, "Domain": [0, 1], "C0": [1.0], "C1": [2.0], "N": 1}
    d = {
        "FunctionType": 3,
        "Domain": [0, 1],
        "Functions": [f0, f1],
        "Bounds": [0.5],
        "Encode": [0, 1, 0, 1],
    }
    fn = load_function(FakeDoc(), d)
    assert abs(fn.eval([0.25])[0] - 0.5) < 1e-9  # halfway through f0's [0,1]
    assert abs(fn.eval([0.75])[0] - 1.5) < 1e-9  # halfway through f1's [1,2]


def test_sampled_function_1d_identity_lookup():
    # 4 samples, 8 bits each, values 0,85,170,255 -> should roughly identity-map [0,1]
    data = bytes([0, 85, 170, 255])
    stream = PDFStream(
        {
            "FunctionType": 0,
            "Domain": [0, 1],
            "Range": [0, 1],
            "Size": [4],
            "BitsPerSample": 8,
        },
        data,
    )
    fn = load_function(FakeDoc(), stream)
    assert abs(fn.eval([0.0])[0] - 0.0) < 1e-6
    assert abs(fn.eval([1.0])[0] - 1.0) < 1e-6
    mid = fn.eval([0.5])[0]
    assert 0.3 < mid < 0.7


def test_sampled_function_2d_interpolation():
    # 2x2 grid, 1 output, 8 bits: corners (0,0)=0 (1,0)=255 (0,1)=0 (1,1)=255
    # i.e. output depends only on the first input, linearly.
    data = bytes([0, 255, 0, 255])
    stream = PDFStream(
        {
            "FunctionType": 0,
            "Domain": [0, 1, 0, 1],
            "Range": [0, 1],
            "Size": [2, 2],
            "BitsPerSample": 8,
        },
        data,
    )
    fn = load_function(FakeDoc(), stream)
    assert abs(fn.eval([0.0, 0.5])[0] - 0.0) < 1e-6
    assert abs(fn.eval([1.0, 0.5])[0] - 1.0) < 1e-6
    assert abs(fn.eval([0.5, 0.0])[0] - 0.5) < 1e-6


def test_postscript_arithmetic():
    program = parse_ps_program(b"{ 2 3 add }")
    stack = []
    eval_ps(program, stack)
    assert stack == [5.0]


def test_postscript_ifelse():
    program = parse_ps_program(b"{ dup 0.5 gt { pop 1 } { pop 0 } ifelse }")
    stack = [0.8]
    eval_ps(program, stack)
    assert stack == [1.0]
    stack = [0.2]
    eval_ps(program, stack)
    assert stack == [0.0]


def test_postscript_function_cmyk_style_transform():
    # A typical Separation tint transform: single tint -> CMYK, here just
    # "tint tint tint 0" (C=M=Y=tint, K=0) to exercise dup/stack shape.
    data = b"{ dup dup 0 }"
    stream = PDFStream({"FunctionType": 4, "Domain": [0, 1], "Range": [0, 1, 0, 1, 0, 1, 0, 1]}, data)
    fn = load_function(FakeDoc(), stream)
    c, m, y, k = fn.eval([0.6])
    assert abs(c - 0.6) < 1e-9
    assert abs(m - 0.6) < 1e-9
    assert abs(y - 0.6) < 1e-9
    assert k == 0.0


def test_postscript_roll_and_index():
    stack = [1.0, 2.0, 3.0]
    eval_ps(parse_ps_program(b"{ 3 1 roll }"), stack)
    assert stack == [3.0, 1.0, 2.0]

    stack = [1.0, 2.0, 3.0]
    eval_ps(parse_ps_program(b"{ 1 index }"), stack)
    assert stack == [1.0, 2.0, 3.0, 2.0]


def test_array_of_functions():
    f0 = {"FunctionType": 2, "Domain": [0, 1], "C0": [0.0], "C1": [1.0], "N": 1}
    f1 = {"FunctionType": 2, "Domain": [0, 1], "C0": [1.0], "C1": [0.0], "N": 1}
    fn = load_function(FakeDoc(), [f0, f1])
    r0, r1 = fn.eval([0.25])
    assert abs(r0 - 0.25) < 1e-9
    assert abs(r1 - 0.75) < 1e-9
