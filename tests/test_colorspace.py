from clearleaf._colorspace import (
    DEVICE_CMYK,
    DEVICE_GRAY,
    DEVICE_RGB,
    IndexedColorSpace,
    LabColorSpace,
    PatternColorSpace,
    load_colorspace,
)
from clearleaf._objects import PDFName, PDFStream


class FakeDoc:
    def resolve(self, obj):
        return obj


def test_device_gray_to_rgb():
    assert DEVICE_GRAY.to_rgb([0.0]) == (0, 0, 0)
    assert DEVICE_GRAY.to_rgb([1.0]) == (255, 255, 255)
    assert DEVICE_GRAY.to_rgb([0.5]) == (128, 128, 128)


def test_device_rgb_to_rgb():
    assert DEVICE_RGB.to_rgb([1.0, 0.0, 0.0]) == (255, 0, 0)


def test_device_cmyk_to_rgb():
    assert DEVICE_CMYK.to_rgb([0, 0, 0, 0]) == (255, 255, 255)
    assert DEVICE_CMYK.to_rgb([0, 0, 0, 1]) == (0, 0, 0)
    assert DEVICE_CMYK.to_rgb([1, 0, 0, 0]) == (0, 255, 255)  # pure cyan


def test_values_clamped_out_of_range():
    assert DEVICE_GRAY.to_rgb([-1.0]) == (0, 0, 0)
    assert DEVICE_GRAY.to_rgb([2.0]) == (255, 255, 255)


def test_load_colorspace_device_names():
    doc = FakeDoc()
    assert load_colorspace(doc, PDFName("DeviceGray")) is DEVICE_GRAY
    assert load_colorspace(doc, PDFName("DeviceRGB")) is DEVICE_RGB
    assert load_colorspace(doc, PDFName("DeviceCMYK")) is DEVICE_CMYK


def test_load_colorspace_resource_indirection():
    doc = FakeDoc()
    resources = {"ColorSpace": {"CS0": PDFName("DeviceRGB")}}
    cs = load_colorspace(doc, PDFName("CS0"), resources)
    assert cs is DEVICE_RGB


def test_indexed_colorspace():
    base = DEVICE_RGB
    table = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])  # red, green, blue
    cs = IndexedColorSpace(base, hival=2, table=table)
    assert cs.to_rgb([0]) == (255, 0, 0)
    assert cs.to_rgb([1]) == (0, 255, 0)
    assert cs.to_rgb([2]) == (0, 0, 255)
    assert cs.to_rgb([99]) == (0, 0, 255)  # clamped to hival


def test_load_colorspace_indexed_array():
    doc = FakeDoc()
    table = bytes([10, 20, 30])
    arr = [PDFName("Indexed"), PDFName("DeviceRGB"), 0, table]
    cs = load_colorspace(doc, arr)
    assert cs.to_rgb([0]) == (10, 20, 30)


def test_separation_colorspace_via_load_colorspace():
    doc = FakeDoc()
    tint_fn = {"FunctionType": 2, "Domain": [0, 1], "C0": [1, 1, 1], "C1": [1, 0, 0], "N": 1}
    arr = [PDFName("Separation"), PDFName("Spot"), PDFName("DeviceRGB"), tint_fn]
    cs = load_colorspace(doc, arr)
    assert cs.to_rgb([0.0]) == (255, 255, 255)
    assert cs.to_rgb([1.0]) == (255, 0, 0)


def test_devicen_colorspace_component_count():
    doc = FakeDoc()
    tint_fn = {
        "FunctionType": 4,
        "Domain": [0, 1, 0, 1],
        "Range": [0, 1, 0, 1, 0, 1],
        "Range_unused": None,
    }
    # Simple PS function ignoring inputs precisely isn't needed here — just
    # check DeviceN reports the right input arity via `names`.
    arr = [
        PDFName("DeviceN"),
        [PDFName("Spot1"), PDFName("Spot2")],
        PDFName("DeviceGray"),
        {"FunctionType": 2, "Domain": [0, 1], "C0": [0], "C1": [1], "N": 1},
    ]
    cs = load_colorspace(doc, arr)
    assert cs.num_components == 2


def test_iccbased_falls_back_to_component_count():
    doc = FakeDoc()
    stream = PDFStream({"N": 4}, b"")
    arr = [PDFName("ICCBased"), stream]
    cs = load_colorspace(doc, arr)
    assert cs is DEVICE_CMYK


def test_iccbased_prefers_alternate():
    doc = FakeDoc()
    stream = PDFStream({"N": 3, "Alternate": PDFName("DeviceGray")}, b"")
    arr = [PDFName("ICCBased"), stream]
    cs = load_colorspace(doc, arr)
    assert cs is DEVICE_GRAY


def test_lab_colorspace_black_and_white():
    doc = FakeDoc()
    cs = LabColorSpace(doc, {})
    assert cs.to_rgb([0.0, 0.0, 0.0]) == (0, 0, 0)
    r, g, b = cs.to_rgb([100.0, 0.0, 0.0])
    assert (r, g, b) == (255, 255, 255)


def test_pattern_colorspace_is_flat_placeholder():
    cs = PatternColorSpace()
    assert cs.to_rgb([0]) == (128, 128, 128)


def test_unrecognized_name_falls_back_to_gray():
    doc = FakeDoc()
    assert load_colorspace(doc, PDFName("SomeWeirdSpace")) is DEVICE_GRAY
